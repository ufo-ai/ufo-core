"""The checked-out repositories' own `AGENTS.md` files, carried to a coding child as it works.

Claude Code loads a directory's `CLAUDE.md` the first time a file under it is read: the read names
the directory, the walk from there up to the session root names every ancestor, each file lands
once per session, and the result follows the tool result into the same turn. Nothing enumerates a
tree and nothing looks for a checkout — the file the child is working on says where the rules are.

The same here, with `/workspace` as the root and `AGENTS.md` as the name. A read, write or edit
under the workspace carries the ancestors of its directory the turn has not yet looked at, root
first so the deepest reads last and wins, fenced and budgeted, each directory once per turn.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from uuid import UUID

from ufo.sdk.manifest import HookContext, HookOutcome, HookSpec, InjectContext, PostToolUse
from ufo.sdk.o11y import log_error
from ufo.sdk.sandbox import WORKSPACE_DIR, Sandbox, workspace_path

INSTRUCTION_FILENAME = "AGENTS.md"
FILE_TOOLS = ("read", "write", "edit")
"""The tools whose input names the file the child is working on. A shell command's does not."""
INSTRUCTION_FILE_MAX_BYTES = 40_000
"""Per file — Claude Code's own threshold for a `CLAUDE.md`."""
REPO_INSTRUCTIONS_MAX_CHARS = 100_000
"""The whole block: under an eighth of the smallest window the runtime serves, so no checkout fills
a turn's window from one read. ufo's own chain (root + `extensions/web`) is 42K."""
CUT_NOTE = "\n[cut here; the file runs {total} chars]"
CACHED_TURNS_MAX = 64
BLOCK_TAG = "repo_instructions"
BLOCK_NOTE = (
    "The instruction files the repositories checked out in this workspace carry, from each "
    "repository root down to its most specific directory. Follow them as the repository's own "
    "rules."
)
ENTRY_JOIN = "\n\n"
FILE_TAG = "file"
CLOSE_ESCAPES = tuple((f"</{tag}>", f"&lt;/{tag}&gt;") for tag in (BLOCK_TAG, FILE_TAG))
"""A closing delimiter escaped inside the body, the way `wall` escapes its own: a file that writes
out this block's own closing tag stays content rather than closing the frame and continuing as the
system's own voice."""
PATH_ESCAPES = (*CLOSE_ESCAPES, ('"', "&quot;"))
"""A path is an attribute value as well as content, and a directory name may hold either — `<` and
`"` are both legal in a POSIX filename."""


@dataclass(frozen=True)
class InstructionFile:
    path: str
    text: str


@dataclass
class _TurnMemory:
    checked: set[str] = field(default_factory=set)


HELD_TURNS: OrderedDict[UUID, _TurnMemory] = OrderedDict()


def instruction_directories(file_path: str) -> tuple[str, ...]:
    """The directories whose `AGENTS.md` govern `file_path`: its own and every ancestor down to
    the workspace, root first. Empty for a path outside the workspace."""
    try:
        resolved = PurePosixPath(workspace_path(file_path))
    except ValueError:
        return ()
    base = PurePosixPath(WORKSPACE_DIR)
    return tuple(
        str(parent) for parent in reversed(resolved.parents) if parent.is_relative_to(base)
    )


def render_repo_instructions(
    files: Sequence[InstructionFile], *, cap: int = REPO_INSTRUCTIONS_MAX_CHARS
) -> str:
    """The one labelled block a turn carries, or `""` where there is nothing to carry.

    The budget is spent root first, as Codex spends its: the file that crosses it is cut there and
    says so, and the files below it are left out. Spending it deepest first and dropping whole
    files dropped ufo's 20K root `AGENTS.md` beside its 21K `extensions/web/AGENTS.md`."""
    entries: list[str] = []
    budget = cap - len(_block(""))
    for file in files:
        if not file.text.strip():
            continue
        room = budget - len(ENTRY_JOIN) if entries else budget
        entry = _entry(file, room)
        if entry is None:
            break
        entries.append(entry)
        budget = room - len(entry)
    return _block(ENTRY_JOIN.join(entries)) if entries else ""


async def sandbox_repo_instructions(sandbox: Sandbox, directories: Sequence[str]) -> str:
    """The block for `directories`, root first: the `AGENTS.md` each holds, read through the
    sandbox and rendered. A file that exists and fails to read is dropped alone."""
    candidates = [f"{directory}/{INSTRUCTION_FILENAME}" for directory in directories]
    present = await asyncio.gather(*(sandbox.file_exists(path) for path in candidates))
    paths = [path for path, held in zip(candidates, present, strict=True) if held]
    texts = await asyncio.gather(*(_read(sandbox, path) for path in paths), return_exceptions=True)
    for text in texts:
        if isinstance(text, BaseException) and not isinstance(text, Exception):
            raise text
    unread = [
        f"{path} ({type(text).__name__})"
        for path, text in zip(paths, texts, strict=True)
        if isinstance(text, Exception)
    ]
    if unread:
        log_error("coding.agents_md.file_unread", paths=", ".join(unread))
    files = [
        InstructionFile(path=path, text=text)
        for path, text in zip(paths, texts, strict=True)
        if isinstance(text, str)
    ]
    return render_repo_instructions(files)


def repo_instruction_hooks(profiles: frozenset[str]) -> tuple[HookSpec, ...]:
    """The one hook that carries the block into a turn on one of `profiles`: after a read, write
    or edit, the `AGENTS.md` of every directory above that file the turn has not yet looked at.

    The file names the directories, so nothing probes for a checkout and a turn that touches no
    file under the workspace pays nothing. Each directory is looked at once per turn, marked before
    the read so a failure is not retried on every call.

    `post_tool_use` is not a gating event, so a workspace this cannot read is swallowed with a log:
    the turn runs on without the block rather than being denied."""

    async def carry(ctx: HookContext) -> HookOutcome:
        turn = ctx.turn
        if (
            not isinstance(ctx.payload, PostToolUse)
            or turn is None
            or turn.subagent_profile not in profiles
            or ctx.sandbox is None
        ):
            return None
        file_path = ctx.payload.tool_input.model_dump().get("file_path")
        if not isinstance(file_path, str):
            return None
        memory = _memory(turn.id)
        fresh = [d for d in instruction_directories(file_path) if d not in memory.checked]
        if not fresh:
            return None
        memory.checked.update(fresh)
        try:
            block = await sandbox_repo_instructions(ctx.sandbox, fresh)
        except Exception as error:
            log_error("coding.agents_md.unread", turn_id=str(turn.id), fault=type(error).__name__)
            return None
        return InjectContext(text=block) if block else None

    return (HookSpec(event="post_tool_use", handler=carry, tools=FILE_TOOLS),)


def _memory(turn_id: UUID) -> _TurnMemory:
    memory = HELD_TURNS.get(turn_id)
    if memory is None:
        memory = _TurnMemory()
        HELD_TURNS[turn_id] = memory
        while len(HELD_TURNS) > CACHED_TURNS_MAX:
            HELD_TURNS.popitem(last=False)
    HELD_TURNS.move_to_end(turn_id)
    return memory


async def _read(sandbox: Sandbox, path: str) -> str:
    read: list[bytes] = []
    size = 0
    async for chunk in sandbox.read_file(path):
        read.append(chunk)
        size += len(chunk)
        if size >= INSTRUCTION_FILE_MAX_BYTES:
            break
    return b"".join(read)[:INSTRUCTION_FILE_MAX_BYTES].decode(errors="replace")


def _framed(text: str, escapes: tuple[tuple[str, str], ...]) -> str:
    for token, escape in escapes:
        text = text.replace(token, escape)
    return text


def _block(body: str) -> str:
    return f"<{BLOCK_TAG}>\n{BLOCK_NOTE}\n\n{body}\n</{BLOCK_TAG}>"


def _entry(file: InstructionFile, room: int) -> str | None:
    head = f'<{FILE_TAG} path="{_framed(file.path, PATH_ESCAPES)}">\n'
    foot = f"\n</{FILE_TAG}>"
    body = _framed(file.text.strip(), CLOSE_ESCAPES)
    if len(head) + len(body) + len(foot) <= room:
        return f"{head}{body}{foot}"
    note = CUT_NOTE.format(total=len(body))
    kept = room - len(head) - len(note) - len(foot)
    if kept <= 0:
        return None
    return f"{head}{body[:kept]}{note}{foot}"

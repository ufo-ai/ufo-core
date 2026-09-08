"""The checked-out repositories' own `AGENTS.md` files, as one context block for a coding child.

Codex builds the same chain once per run: the files from the project root down to the working
directory, one per directory, concatenated root-first so a nested rule reads last and wins. It
never enumerates a directory — it probes exact paths, walking up from the working directory to a
`.git` marker and then stat-ing the candidate filenames in each directory of that one chain, so
the cost is the depth of the path and not the size of the repository.

A ufo turn has no working directory, so the chain here covers every checkout the workspace holds.
Discovery keeps Codex's rule that nothing is enumerated: the coding skill clones into
`/workspace/<org-repo>`, so a marker is found by reading `/workspace` and its children alone, and
each root's files come from git's index rather than from a walk of its tree. The index is also the
answer to which files are *this* repository's — a nested checkout's rules, and an ignored build or
dependency tree, belong to somebody else.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from uuid import UUID

from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    UserPromptSubmit,
)
from ufo.sdk.o11y import log_error
from ufo.sdk.sandbox import WORKSPACE_DIR, Sandbox

INSTRUCTION_FILENAMES = ("AGENTS.override.md", "AGENTS.md")
"""In per-directory preference order: an override outranks the file it overrides."""
GIT_DIRNAME = ".git"
VENDOR_DIRNAMES = ("node_modules", ".venv")
MARKER_MAX_DEPTH = 2
REPO_INSTRUCTIONS_MAX_CHARS = 40_000
INSTRUCTION_FILES_MAX = 16
"""A monorepo can hold hundreds, and the reads run inside a 5-second hook deadline."""
INSTRUCTION_FILE_MAX_BYTES = 2 * REPO_INSTRUCTIONS_MAX_CHARS
DISCOVERY_TIMEOUT_SECONDS = 3
CACHED_TURNS_MAX = 64
BLOCK_TAG = "repo_instructions"
BLOCK_NOTE = (
    "The instruction files the repositories checked out in this workspace carry, from each "
    "repository root down to its most specific directory. Follow them as the repository's own "
    "rules; where two files disagree, the one deeper in the tree wins. A directory's "
    "AGENTS.override.md replaces its AGENTS.md."
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
TRUNCATION_MARK = "\n[truncated at the repo-instruction cap]"
DROPPED_NOTE = "{dropped} less-specific file(s) were dropped at the {cap}-character cap."
REPO_ROOTS_SCRIPT = f"""exec 2>/dev/null
cd "$1" || exit 0
find . -mindepth 1 -maxdepth {MARKER_MAX_DEPTH} -name {GIT_DIRNAME} -print0
"""
"""The coding skill clones into `/workspace/<org-repo>`, so every marker sits at or above the
second level and this reads the workspace and its children alone. It walks `.` so the markers come
back relative: a carrier whose `/workspace` is a host directory rewrites the argv it is handed and
nothing of what the command prints, so an absolute path out of stdout names the host."""
_INCLUDED = " ".join(f"'{name}' '*/{name}'" for name in INSTRUCTION_FILENAMES)
_EXCLUDED = " ".join(f"':!{name}/*' ':!*/{name}/*'" for name in VENDOR_DIRNAMES)
INSTRUCTION_FILES_SCRIPT = f"""exec 2>/dev/null
cd "$1" || exit 0
git ls-files -z -- {_INCLUDED} {_EXCLUDED}
"""
"""One root's own tracked instruction files, read out of the index. A root arrives as its own argv
element, so no path is ever interpolated into a script, and a `.git` whose gitdir the sandbox does
not hold answers with nothing rather than with somebody else's files."""


@dataclass(frozen=True)
class InstructionFile:
    path: str
    text: str


@dataclass
class _TurnBlock:
    block: str
    injected: bool = False


HELD_TURNS: OrderedDict[UUID, _TurnBlock] = OrderedDict()


def repo_roots(markers: Iterable[str], root: str) -> tuple[str, ...]:
    """The directory each `.git` marker sits in, under `root` — git's own directory, or the file a
    worktree carries instead. Markers arrive relative to the root the probe walked, so the path
    this returns is the one the sandbox's file tools name rather than a host path."""
    return tuple(
        sorted(
            {
                str(PurePosixPath(root) / PurePosixPath(marker).parent)
                for marker in markers
                if marker.strip()
            }
        )
    )


def instruction_chain(found: Iterable[str]) -> tuple[str, ...]:
    """The instruction files to load, ordered root-first and most-specific-last, one per
    directory."""
    by_directory: dict[str, dict[str, str]] = {}
    for path in found:
        posix = PurePosixPath(path)
        if posix.name not in INSTRUCTION_FILENAMES:
            continue
        by_directory.setdefault(str(posix.parent), {})[posix.name] = str(posix)
    selected = [
        next(names[name] for name in INSTRUCTION_FILENAMES if name in names)
        for names in by_directory.values()
    ]
    return tuple(sorted(selected, key=lambda path: (len(PurePosixPath(path).parents), path)))


def render_repo_instructions(
    files: Sequence[InstructionFile], *, cap: int = REPO_INSTRUCTIONS_MAX_CHARS
) -> str:
    """The one labelled block a turn carries, or `""` where the workspace holds no instructions.

    The budget is spent from the most specific file backwards, so a root file crowded with prose
    can never starve the checkout's own rules: a file that no longer fits is dropped along with
    every less specific one, and the block says how many went. The most specific file is truncated
    to what fits rather than dropped."""
    named = [file for file in files if file.text.strip()]
    kept: list[str] = []
    budget = cap
    for file in reversed(named):
        room = budget - len(ENTRY_JOIN) if kept else budget
        entry = _entry(file)
        if len(entry) <= room:
            kept.append(entry)
            budget = room - len(entry)
            continue
        head = None if kept else _head(file, room)
        if head is not None:
            kept.append(head)
        break
    if not kept:
        return ""
    dropped = len(named) - len(kept)
    note = (
        BLOCK_NOTE
        if not dropped
        else f"{BLOCK_NOTE} {DROPPED_NOTE.format(dropped=dropped, cap=cap)}"
    )
    body = ENTRY_JOIN.join(reversed(kept))
    return f"<{BLOCK_TAG}>\n{note}\n\n{body}\n</{BLOCK_TAG}>"


async def sandbox_repo_instructions(
    sandbox: Sandbox, *, root: str = WORKSPACE_DIR, cap: int = REPO_INSTRUCTIONS_MAX_CHARS
) -> str:
    """Discover and read the workspace's instruction files through the sandbox, and render the
    block. Every probe and read runs inside the container, so this reaches a checkout whichever
    carrier holds it. A workspace with no checkout in it costs one probe and yields no block.

    A file the index names but the tree does not hold — one removed without staging the removal, a
    sparse checkout's absent blob — is dropped alone. It cannot cost the turn the rules that did
    read."""
    roots = repo_roots(await _probed(sandbox, REPO_ROOTS_SCRIPT, root), root)
    if not roots:
        return ""
    listed = await asyncio.gather(*(_listed(sandbox, one) for one in roots))
    chain = instruction_chain(path for paths in listed for path in paths)
    chain = chain[-INSTRUCTION_FILES_MAX:]
    texts = await asyncio.gather(*(_read(sandbox, path) for path in chain), return_exceptions=True)
    unread = [
        path for path, text in zip(chain, texts, strict=True) if isinstance(text, BaseException)
    ]
    if unread:
        log_error("coding.agents_md.file_unread", paths=", ".join(unread))
    files = [
        InstructionFile(path=path, text=text)
        for path, text in zip(chain, texts, strict=True)
        if isinstance(text, str)
    ]
    return render_repo_instructions(files, cap=cap)


def repo_instruction_hooks(profiles: frozenset[str]) -> tuple[HookSpec, ...]:
    """The two hooks that carry the block into a turn on one of `profiles`: one opens the turn with
    it, one closes every `load_skill` result with it, so a workflow the model pulls mid-turn never
    reads as the last word over the repository's rules.

    The founding hook is best-effort — a workspace it cannot read leaves the turn running without
    the block rather than denying it — and injects once, since `user_prompt_submit` fires again for
    every arrival a running turn absorbs."""

    async def inject(ctx: HookContext) -> HookOutcome:
        if not isinstance(ctx.payload, UserPromptSubmit):
            return None
        held = await _turn_block(ctx, profiles)
        if held is None or held.injected or not held.block:
            return None
        held.injected = True
        return InjectContext(text=held.block)

    async def append(ctx: HookContext) -> HookOutcome:
        held = await _turn_block(ctx, profiles)
        if held is None or not held.block:
            return None
        return InjectContext(text=held.block)

    return (
        HookSpec(event="user_prompt_submit", handler=inject, best_effort=True),
        HookSpec(event="post_tool_use", handler=append, tools=("load_skill",)),
    )


async def _turn_block(ctx: HookContext, profiles: frozenset[str]) -> _TurnBlock | None:
    turn = ctx.turn
    if turn is None or turn.subagent_profile not in profiles:
        return None
    if ctx.sandbox is None:
        return None
    held = HELD_TURNS.get(turn.id)
    if held is not None:
        HELD_TURNS.move_to_end(turn.id)
        return held
    try:
        block = await sandbox_repo_instructions(ctx.sandbox)
    except Exception:
        log_error("coding.agents_md.unread", turn_id=str(turn.id))
        block = ""
    held = _TurnBlock(block=block)
    HELD_TURNS[turn.id] = held
    while len(HELD_TURNS) > CACHED_TURNS_MAX:
        HELD_TURNS.popitem(last=False)
    return held


async def _probed(sandbox: Sandbox, script: str, *args: str) -> tuple[str, ...]:
    result = await sandbox.sh(script, *args, timeout_s=DISCOVERY_TIMEOUT_SECONDS)
    # Both scripts report what they could read whatever they could not, so the exit code says less
    # than the output does: find exits non-zero on one unreadable directory, git on a broken root.
    return tuple(path for path in result.stdout.split("\0") if path)


async def _listed(sandbox: Sandbox, root: str) -> tuple[str, ...]:
    relative = await _probed(sandbox, INSTRUCTION_FILES_SCRIPT, root)
    return tuple(f"{root}/{path}" for path in relative)


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


def _entry(file: InstructionFile) -> str:
    path = _framed(file.path, PATH_ESCAPES)
    body = _framed(file.text.strip(), CLOSE_ESCAPES)
    return f'<{FILE_TAG} path="{path}">\n{body}\n</{FILE_TAG}>'


def _head(file: InstructionFile, budget: int) -> str | None:
    allowance = budget - len(_entry(replace(file, text=""))) - len(TRUNCATION_MARK)
    if allowance <= 0:
        return None
    # Cut the escaped text, not the raw: an escape grows what it replaces, and the cut is measured
    # against the budget the entry has to fit.
    held = _framed(file.text.strip(), CLOSE_ESCAPES)[:allowance]
    return _entry(replace(file, text=held + TRUNCATION_MARK))

"""The builtin tool set: bash, read, write, edit, glob, grep, share_file, spawn_subagent,
ask_user, request_credentials, load_skill, connect_account,
wait_for_subagents, cancel_subagent, message_subagent.

Each file/shell handler reaches the workspace only through `ctx.sandbox`, so the carrier's scoping
and egress rules apply whether a byte arrives via a shell command or a file op. `read`, `edit`, and
`write` run the in-sandbox `sbxfs` CLI, so windowing, ripgrep, and PDF/image render happen in the
container and only a bounded JSON result crosses back — the host never pulls a whole file over to
loop on it. `read` records every path it returns so `edit`/`write` can refuse to touch a file the
turn has not read — the guard that keeps a blind string-replace from clobbering content the model
never saw. `glob` and `grep` run the in-sandbox `sbxfs` matcher and ripgrep, so file discovery and
content search happen in the container and a bounded result crosses back. `share_file` streams a
produced workspace file straight out of the mount into the blob
store under `artifacts/<uuid>/` and returns a TTL-token URL core's artifact route serves — the only
path that hands a file back outside the sandbox, with no read cap and no whole-file buffer.
`spawn_subagent` delegates a typed subtask to a child turn through `ctx.spawn`. `ask_user` is
chat-native: it
structures a question or confirmation the agent poses in its reply, whose answer rides the member's
next message — no out-of-band prompt. `request_credentials` is its secret-collecting sibling: it
seals which slots the speaking owner will fill and ends the turn; a capable surface prompts for the
values privately and fulfillment lands them in the encrypted store, never the transcript.
`load_skill` mounts a skill's `SKILL.md` and assets — and those of the whole chain it `depends` on —
into the workspace, and returns each one's workflow followed by one tree of everything mounted; the
system prompt's `<available_skills>` block is its complete per-turn index.
`wait_for_subagents`, `cancel_subagent`, and `message_subagent` reach `ctx.subagents`, the same
Subagents workflow that backs `spawn`, to await a background child's terminal, cancel a running one,
or queue it a follow-up message that runs as its next turn — scoped to the children this turn
spawned."""

import json
import mimetypes
import shlex
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, Field

from ufo.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_KEY_PREFIX,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from ufo.artifacts import artifact_object_names
from ufo.db import workspace_tx
from ufo.grants import installed_connect_flow
from ufo.sandbox.session import WORKSPACE_DIR, workspace_path
from ufo.schema import tables
from ufo.schema.records import AskUserInput, ConnectRequest, CredentialPrompt, CredentialRequest
from ufo.skills.runtime import loaded_context, mount_skill
from ufo.tools.context import (
    ImageContent,
    TextContent,
    ToolContext,
    ToolResult,
    UnknownSubagentProfile,
)
from ufo.tools.registry import ToolDef

GREP_HEAD_LIMIT = 100
ARTIFACT_FALLBACK_NAME = "download"
SHARE_PREFLIGHT_TIMEOUT_SECONDS = 300

SHARE_PREFLIGHT_PROG = """
import hashlib, json, sys
path = sys.argv[1]
h = hashlib.sha256()
size = 0
head = b""
with open(path, "rb") as f:
    while True:
        chunk = f.read(1048576)
        if not chunk:
            break
        if len(head) < 4096:
            head += chunk[: 4096 - len(head)]
        size += len(chunk)
        h.update(chunk)
stat = {"size": size, "digest": "sha256:" + h.hexdigest(), "is_text": b"\\x00" not in head}
print(json.dumps(stat))
"""


MAX_BASH_TIMEOUT_MS = 600_000
MAX_REQUESTED_SLOTS = 4


class BashInput(BaseModel):
    command: str = Field(description="The shell command to execute.")
    timeout: int | None = Field(
        default=None, description="Optional timeout in milliseconds. Max 600000 (10 minutes)."
    )
    user_description: str | None = Field(
        default=None,
        description="Brief plain-language description for non-technical users, shown in the "
        "activity timeline. Never include raw commands or file paths.",
    )


class ReadInput(BaseModel):
    file_path: str = Field(description="Absolute path to the file to read.")
    offset: int | None = Field(
        default=None,
        description="Line/page number to start reading from. Only provide if the file is too large "
        "to read at once.",
    )
    limit: int | None = Field(
        default=None,
        description="Number of lines/pages to read. Only provide if the file is too large to read "
        "at once.",
    )


class WriteInput(BaseModel):
    file_path: str = Field(
        description="Absolute path to the file to write, e.g. /workspace/output.json."
    )
    content: str = Field(description="The text content to write to the file.")


class FileEdit(BaseModel):
    old_string: str = Field(description="The exact text to replace.")
    new_string: str = Field(description="The replacement text.")
    replace_all: bool = Field(
        default=False,
        description="Replace all occurrences instead of requiring old_string to be unique.",
    )


class EditInput(BaseModel):
    file_path: str = Field(description="Absolute path to the file to modify.")
    edits: tuple[FileEdit, ...] = Field(
        min_length=1,
        description="List of edits to apply sequentially. Each edit is an object with old_string, "
        "new_string, and optionally replace_all.",
    )


class GlobInput(BaseModel):
    pattern: str = Field(
        description="The glob pattern to match files against, e.g. '**/*.py', '*.json', "
        "'src/**/*.ts'."
    )
    path: str | None = Field(
        default=None,
        description="Absolute path to the directory to search in. If omitted, searches from the "
        "workspace root.",
    )


class GrepInput(BaseModel):
    pattern: str = Field(description="The regex pattern to search for.")
    glob: str | None = Field(
        default=None, description="Glob pattern to filter which files to search, e.g. '**/*.py'."
    )
    context: int | None = Field(
        default=None, description="Number of context lines to show around each match."
    )
    ignore_case: bool | None = Field(default=None, description="Case-insensitive search.")
    output_mode: Literal["content", "files_with_matches", "count"] | None = Field(
        default=None,
        description="How to display results: 'content' (default, shows matching lines), "
        "'files_with_matches' (just filenames), 'count' (match counts per file).",
    )
    head_limit: int | None = Field(default=None, description="Limit output to first N results.")


class ShareFileInput(BaseModel):
    file_path: str = Field(description="Absolute path to the file to share.")
    name: str | None = Field(
        default=None,
        description="Logical asset name, e.g. 'quarterly_report.xlsx'. Use the SAME name when "
        "sharing updated versions to enable version history. Defaults to the filename; a name "
        "without a recognizable extension gets the source file's extension appended so the "
        "recipient receives an openable file.",
    )
    subject: str | None = Field(
        default=None, description="Optional caption shown when a chat surface posts the file."
    )


class SpawnSubagentInput(BaseModel):
    profile: str = Field(
        description="The subagent profile to run — a registered profile name that fixes the "
        "child's prompt, tool set, and input/output schema."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Arguments matching the profile's input schema."
    )
    background: bool = Field(
        default=False,
        description="Run in the background and return the child turn id immediately instead of "
        "waiting for its validated output.",
    )


class LoadSkillInput(BaseModel):
    name: str = Field(
        description="The skill name, e.g. 'office/pptx', 'data/visualization'. Choose from the "
        "system prompt's <available_skills> index."
    )


class ConnectAccountInput(BaseModel):
    provider: str = Field(
        description="The connector source_id to connect an account for, exactly as "
        "list_external_tools returns it (e.g. 'github', 'googlecalendar', 'notion'). Look it up "
        "there first rather than guessing — an unknown provider is refused."
    )
    shared: bool = Field(
        default=False,
        description="Connect the account for the whole workspace rather than privately to the "
        "speaking member. Set it only when the member's words say the account is for the team.",
    )


class RequestCredentialsInput(BaseModel):
    reason: str = Field(
        description="Why these values are needed, shown to the member above the prompts."
    )
    prompts: tuple[CredentialPrompt, ...] = Field(
        min_length=1,
        max_length=MAX_REQUESTED_SLOTS,
        description="The slots to fill and what to ask for each.",
    )


class WaitForSubagentsInput(BaseModel):
    subagent_ids: tuple[str, ...] = Field(
        min_length=1, description="List of subagent IDs to wait for. Must always be specified."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class CancelSubagentInput(BaseModel):
    subagent_id: str = Field(description="The subagent ID to cancel.")
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class MessageSubagentInput(BaseModel):
    subagent_id: str = Field(description="The subagent ID to message.")
    message: str = Field(
        description="The follow-up message to deliver, run as the subagent's next turn."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult:
    timeout_s = int(min(args.timeout, MAX_BASH_TIMEOUT_MS) / 1000) if args.timeout else None
    result = await ctx.sandbox.bash(args.command, timeout_s=timeout_s)
    output = result.stdout + result.stderr
    if result.exit_code == 0:
        return ToolResult(content=(TextContent(text=output),))
    exit_line = f"exit code: {result.exit_code}"
    return ToolResult(
        content=(TextContent(text=f"{output}\n{exit_line}" if output else exit_line),),
        is_error=True,
    )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"sbxfs read returned no {field}")
    return value


def _pdf_result(result: dict[str, object]) -> ToolResult:
    """A paginated render as model content — PDF pages or the PPTX slides converted through it: a
    text block (extracted text, the page window, any render note) then one image block per rendered
    page or slide. Renders are absent when poppler/libreoffice is unavailable in the sandbox,
    leaving a text-only result."""
    lines: list[str] = []
    text = result.get("text")
    if isinstance(text, str) and text.strip():
        lines.append(text.rstrip())
    total = result.get("total_pages")
    start = result.get("start_page")
    returned = result.get("pages_returned")
    if (
        isinstance(total, int)
        and isinstance(start, int)
        and isinstance(returned, int)
        and returned > 0
    ):
        unit = "slides" if result.get("type") == "pptx" else "pages"
        kind = "pptx" if result.get("type") == "pptx" else "pdf"
        footer = f"[{kind} {unit} {start}-{start + returned - 1} of {total}]"
        next_page = result.get("next_page")
        if isinstance(next_page, int):
            footer += f"; more {unit} - read with offset={next_page}"
        lines.append(footer)
    for key in ("note", "quality_reminder"):
        value = result.get(key)
        if isinstance(value, str) and value:
            lines.append(value)
    blocks: list[TextContent | ImageContent] = []
    if lines:
        blocks.append(TextContent(text="\n\n".join(lines)))
    pages = result.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                raise RuntimeError("sbxfs read returned a malformed pdf page")
            blocks.append(
                ImageContent(
                    media_type=_require_str(page.get("media_type"), "media_type"),
                    data=_require_str(page.get("data"), "data"),
                )
            )
    if not blocks:
        raise RuntimeError("sbxfs read returned an empty pdf result")
    return ToolResult(content=tuple(blocks))


async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult:
    params: dict[str, object] = {"path": args.file_path}
    if args.offset is not None:
        params["offset"] = args.offset
    if args.limit is not None:
        params["limit"] = args.limit
    result = await ctx.sandbox.run_sbxfs("read", params)
    ctx.read_paths.add(args.file_path)
    if result.get("type") == "image":
        return ToolResult(
            content=(
                ImageContent(
                    media_type=_require_str(result.get("media_type"), "media_type"),
                    data=_require_str(result.get("data"), "data"),
                ),
            )
        )
    if result.get("type") in ("pdf", "pptx"):
        return _pdf_result(result)
    if result.get("is_empty"):
        return ToolResult(content=(TextContent(text="(file is empty)"),))
    start = result.get("start_line")
    total = result.get("total_lines")
    returned = result.get("lines_returned")
    if not (isinstance(start, int) and isinstance(total, int) and isinstance(returned, int)):
        raise RuntimeError("sbxfs read returned a malformed text result")
    if returned == 0:
        return ToolResult(
            content=(TextContent(text=f"(no lines at offset {start}; file has {total} lines)"),)
        )
    content = result.get("content")
    if not isinstance(content, str):
        raise RuntimeError("sbxfs read returned no content")
    footer = f"\n\n[lines {start}-{start + returned - 1} of {total}]"
    remaining = result.get("remaining_lines")
    if isinstance(remaining, int) and not isinstance(remaining, bool) and remaining > 0:
        footer += f"; {remaining} more - read with offset={result.get('next_offset')}"
    return ToolResult(content=(TextContent(text=content + footer),))


async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult:
    existed = await ctx.sandbox.file_exists(args.file_path)
    if existed and args.file_path not in ctx.read_paths:
        raise ValueError(f"file {args.file_path} must be read before it is written")
    data = args.content.encode()
    await ctx.sandbox.write_file(args.file_path, data)
    ctx.read_paths.add(args.file_path)
    trailing = 1 if data and not data.endswith(b"\n") else 0
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "path": args.file_path,
                        "created": not existed,
                        "size_bytes": len(data),
                        "lines": args.content.count("\n") + trailing,
                    }
                )
            ),
        )
    )


async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult:
    if args.file_path not in ctx.read_paths:
        raise ValueError(f"file {args.file_path} must be read before it is edited")
    edits = [
        {"old_string": e.old_string, "new_string": e.new_string, "replace_all": e.replace_all}
        for e in args.edits
    ]
    result = await ctx.sandbox.run_sbxfs("edit", {"path": args.file_path, "edits": edits})
    return ToolResult(content=(TextContent(text=json.dumps(result)),))


async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult:
    """Match files by glob pattern inside the container through the `sbxfs` CLI, so the traversal
    runs in the sandbox and only the matching paths cross back. Defaults to the workspace root."""
    result = await ctx.sandbox.run_sbxfs(
        "glob", {"pattern": args.pattern, "path": args.path or WORKSPACE_DIR}
    )
    return ToolResult(content=(TextContent(text=json.dumps(result)),))


async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult:
    """Search file contents for a regex across the workspace through the in-sandbox `sbxfs` ripgrep,
    so the scan runs in the container and a bounded result crosses back. `head_limit` caps the
    matches returned."""
    params: dict[str, object] = {
        "pattern": args.pattern,
        "path": WORKSPACE_DIR,
        "head_limit": args.head_limit if args.head_limit is not None else GREP_HEAD_LIMIT,
    }
    if args.glob is not None:
        params["glob"] = args.glob
    if args.context is not None:
        params["context"] = args.context
    if args.output_mode is not None:
        params["output_mode"] = args.output_mode
    if args.ignore_case:
        params["ignore_case"] = True
    result = await ctx.sandbox.run_sbxfs("grep", params)
    return ToolResult(content=(TextContent(text=json.dumps(result)),))


async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult:
    """Stream a produced workspace file into the artifact store under `artifacts/<uuid>/<name>`,
    record it as a shared_artifact of this turn, and mint a TTL download token core's artifact route
    serves — the only path a produced file leaves the sandbox. A preflight in the container streams
    the file to derive its size and sha256 without loading it whole; the carrier then copies it out
    of the workspace mount into the blob store the same way, so any file type and size shares
    without a read cap or a whole-file host buffer. The shared_artifact record is what an async
    surface (Slack) reads to upload the file into the turn's posted reply; `subject` is an optional
    caption — absent, the file renders under its plain name."""
    if not ctx.artifact_token_secret:
        raise RuntimeError("artifact sharing is not configured (no artifact token secret set)")
    scoped = workspace_path(args.file_path)
    preflight = await ctx.sandbox.bash(
        f"python3 -c {shlex.quote(SHARE_PREFLIGHT_PROG)} {shlex.quote(scoped)}",
        timeout_s=SHARE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if preflight.exit_code != 0:
        raise RuntimeError(preflight.stderr.strip() or "artifact preflight failed")
    stat = json.loads(preflight.stdout)
    basename = PurePosixPath((args.name or args.file_path).replace("\\", "/")).name
    safe_name = basename if basename not in ("", ".", "..") else ARTIFACT_FALLBACK_NAME
    source_suffix = PurePosixPath(args.file_path.replace("\\", "/")).suffix
    if (
        source_suffix
        and mimetypes.guess_type(safe_name)[0] is None
        and not safe_name.lower().endswith(source_suffix.lower())
    ):
        safe_name += source_suffix
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{safe_name}"
    await ctx.sandbox.export_file(args.file_path, ctx.blob, key)
    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    shared_at = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=ctx.turn.id,
                blob_key=key,
                workspace_id=ctx.turn.workspace_id,
                filename=safe_name,
                subject=args.subject,
                media_type=media_type,
                size_bytes=stat["size"],
                created_at=shared_at,
                updated_at=shared_at,
            )
        )
        identities = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id, tables.shared_artifact.c.filename)
                .select_from(
                    tables.shared_artifact.join(
                        tables.turn, tables.shared_artifact.c.turn_id == tables.turn.c.id
                    )
                )
                .where(tables.shared_artifact.c.workspace_id == ctx.turn.workspace_id)
                .distinct()
            )
        ).all()
    expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_TOKEN_TTL_SECONDS
    token = mint_artifact_token(ctx.artifact_token_secret, key, safe_name, expires_at)
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "url": f"{ARTIFACT_DOWNLOAD_PATH}?token={token}",
                        "name": safe_name,
                        "artifact": artifact_object_names(
                            [(row.conversation_id, row.filename) for row in identities]
                        )[(ctx.turn.conversation_id, safe_name)],
                        "size_bytes": int(stat["size"]),
                        "digest": str(stat["digest"]),
                        "is_text": bool(stat["is_text"]),
                    }
                )
            ),
        )
    )


async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult:
    try:
        result = await ctx.spawn(args.profile, args.payload, args.background)
    except UnknownSubagentProfile as error:
        return ToolResult(content=(TextContent(text=str(error)),), is_error=True)
    if result.output is None:
        return ToolResult(
            content=(TextContent(text=f"spawned {args.profile} subagent (turn {result.turn_id})"),)
        )
    return ToolResult(
        content=(TextContent(text=result.output.model_dump_json()),), untrusted=result.untrusted
    )


ASK_USER_DIRECTIVE = (
    "Ask these in your reply, then end your turn — the user's answer arrives as the next message."
)


async def ask_user_handler(ctx: ToolContext, args: AskUserInput) -> ToolResult:
    """Chat-native interaction: the question rides the agent's reply and the answer rides the
    member's next message, never an out-of-band prompt. Returns the structured question so a rich
    surface can render it and the model presents it faithfully, plus the directive to end the turn
    and wait."""
    payload = {
        "awaiting": "question",
        "title": args.title,
        "questions": [question.model_dump(exclude_none=True) for question in args.questions],
    }
    return ToolResult(content=(TextContent(text=f"{ASK_USER_DIRECTIVE}\n{json.dumps(payload)}"),))


async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult:
    """Resolve the named skill and the full chain of what it `depends` on, mount every one's files
    into the workspace under `.skills/<name>/`, and return each one's `SKILL.md` workflow — the
    asked-for skill first, so its workflow leads — closing with one tree of everything mounted. A
    workflow the context already holds is named in one note instead of injected again, while its
    files still mount, so re-loading is cheap and self-healing rather than an error. An unknown name
    fails loud as a recoverable tool error."""
    loaded = ctx.skills.closure(args.name)
    for entry in loaded:
        await mount_skill(ctx.sandbox, entry.skill)
    text = loaded_context(loaded, ctx.loaded_skills.in_context)
    return ToolResult(content=(TextContent(text=text),))


CONNECT_ACCOUNT_DIRECTIVE = (
    "Tell the member to use the private connection control in your reply, then end your turn — "
    "the authorization URL never appears in this conversation."
)


async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult:
    """Leave a provider-validated private OAuth handoff for the speaking member."""
    if ctx.speaker_member_id is None:
        raise ValueError("connect requires a speaking member to gate the grant")
    await installed_connect_flow().validate_provider(args.provider)
    request = ConnectRequest(provider=args.provider, shared=args.shared)
    return ToolResult(
        content=(TextContent(text=f"{CONNECT_ACCOUNT_DIRECTIVE}\n{request.model_dump_json()}"),)
    )


REQUEST_CREDENTIALS_DIRECTIVE = (
    "Tell the member what you need in your reply, then end your turn — their terminal prompts for "
    "each value privately, and the entered secrets never appear in this conversation."
)


async def request_credentials_handler(
    ctx: ToolContext, args: RequestCredentialsInput
) -> ToolResult:
    """Collect BYOK secrets from the speaking owner without them touching the transcript: seal
    which slots this member will fill (the same Fernet that guards the slots signs the grant), and
    return the structured request so a capable surface prompts for the values privately and
    fulfills against the seal. Ends the turn like ask_user — the member returns once entered. Slots
    are workspace-global, so only the owner may fill them; a non-owner speaker, an undeclared slot,
    or a deploy without a credential key raises, surfacing as a recoverable tool error."""
    if ctx.speaker_member_id is None:
        raise ValueError("collecting credentials requires a speaking member")
    if ctx.audience_member_id != ctx.speaker_member_id:
        raise ValueError("collecting credentials requires the speaker's private audience")
    if ctx.requestable_credentials is None:
        raise ValueError("no credential key is configured — this deploy cannot store secrets")
    if not await ctx.speaker_is_owner():
        raise ValueError("only the workspace owner can fill credential slots")
    sealed = ctx.requestable_credentials.seal(
        ctx.turn.workspace_id,
        ctx.speaker_member_id,
        tuple(prompt.slot for prompt in args.prompts),
    )
    request = CredentialRequest(reason=args.reason, prompts=args.prompts, sealed=sealed)
    return ToolResult(
        content=(TextContent(text=f"{REQUEST_CREDENTIALS_DIRECTIVE}\n{request.model_dump_json()}"),)
    )


async def wait_for_subagents_handler(ctx: ToolContext, args: WaitForSubagentsInput) -> ToolResult:
    """Await each background subagent's terminal and report its status and final answer. A malformed
    id raises a recoverable tool error; a control surface that is not wired fails loud."""
    if ctx.subagents is None:
        raise RuntimeError("subagent control is not available in this context")
    statuses = await ctx.subagents.wait(tuple(UUID(raw) for raw in args.subagent_ids))
    done = [
        {"subagent_id": str(status.turn_id), "status": status.status, "output": status.text}
        for status in statuses
    ]
    return ToolResult(
        content=(TextContent(text=json.dumps({"subagents": done})),),
        untrusted=any(status.untrusted for status in statuses),
    )


async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult:
    """Cancel a running subagent and report its current status; a subagent that already finished is
    a no-op whose committed terminal stands. Refuses a turn id this turn did not spawn."""
    if ctx.subagents is None:
        raise RuntimeError("subagent control is not available in this context")
    status = await ctx.subagents.cancel(UUID(args.subagent_id))
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps({"subagent_id": str(status.turn_id), "status": status.status})
            ),
        )
    )


async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult:
    """Send a running background subagent a follow-up message; it runs as the subagent's next turn
    against its accumulated context once the turn in flight ends, and the returned id addresses that
    follow-up for a later wait. Refuses a turn id this turn did not spawn."""
    if ctx.subagents is None:
        raise RuntimeError("subagent control is not available in this context")
    status = await ctx.subagents.message(UUID(args.subagent_id), args.message)
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps({"subagent_id": str(status.turn_id), "status": status.status})
            ),
        )
    )


BUILTIN_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="bash",
        description=(
            "Execute shell commands in the secure sandboxed workspace container. Pre-installed: "
            "Python 3, Node.js, ripgrep, poppler, tesseract, libreoffice, pandoc, chromium, and "
            "standard Unix tools. Working directory: /workspace. Use absolute paths. Do NOT use "
            "for file reads/edits/searches — use the dedicated read/edit/glob/grep tools instead."
        ),
        input_model=BashInput,
        handler=bash_handler,
    ),
    ToolDef(
        name="read",
        description=(
            "Reads a file from the workspace. Returns up to 2000 lines by default; use "
            "offset/limit for large files. Lines longer than 2000 chars are truncated. For "
            "images: returns visual content for analysis. For PDFs: extracts text and renders "
            "page images (default 20 pages). For PPTX: renders slides as images (default 20 "
            "slides). Cannot read binary files."
        ),
        input_model=ReadInput,
        handler=read_handler,
        parallel_safe=True,
    ),
    ToolDef(
        name="write",
        description=(
            "Create a file in workspace storage at a given path. Does NOT send to user — call "
            "share_file afterward to share it. Use for creating new files; use edit for modifying "
            "existing ones."
        ),
        input_model=WriteInput,
        handler=write_handler,
    ),
    ToolDef(
        name="edit",
        description=(
            "Performs exact string replacements in files. An edit FAILS if old_string is not "
            "unique in the file (unless replace_all=true). Multiple edits are applied "
            "sequentially; all must succeed or none are applied."
        ),
        input_model=EditInput,
        handler=edit_handler,
    ),
    ToolDef(
        name="glob",
        description=(
            "Fast file pattern matching using glob patterns. Returns matching file paths. Use "
            "instead of bash `find` or `ls`."
        ),
        input_model=GlobInput,
        handler=glob_handler,
        parallel_safe=True,
    ),
    ToolDef(
        name="grep",
        description=(
            "Search for a regex pattern in file contents across the workspace. Use instead of bash "
            "`grep` or `rg`. Pattern is a regex, not a literal string — escape metacharacters such "
            "as ( ) . * + ? [ ] { } | when searching for a literal name, e.g. a function call "
            "site: `foo\\(`."
        ),
        input_model=GrepInput,
        handler=grep_handler,
        parallel_safe=True,
    ),
    ToolDef(
        name="share_file",
        description=(
            "Send a file to the user as a downloadable link. The ONLY way to make a produced file "
            "visible outside the sandbox — the user CANNOT see a workspace file until this is "
            "called. The file must be under the /workspace directory. Any file type and size works "
            "(reports, code, csv, json, images, PDFs, large archives); it is streamed out, never "
            "read whole into memory. `name` sets the download name — include the file extension "
            "(e.g. 'report.xlsx') so the recipient gets an openable file; any directory "
            "components in it are stripped. `subject` is an optional caption shown when a chat "
            "surface posts the file. Supports version history: use the same `name` parameter "
            "for updated versions. Files shared in other sessions can be fetched into the "
            "workspace as `artifact` objects with object_get."
        ),
        input_model=ShareFileInput,
        handler=share_file_handler,
    ),
    ToolDef(
        name="spawn_subagent",
        description=(
            "Delegate a subtask to a named subagent profile. `payload` must match the profile's "
            "input schema; foreground (default) returns the profile's validated JSON output, "
            "background returns the child turn id at once."
        ),
        input_model=SpawnSubagentInput,
        handler=spawn_subagent_handler,
    ),
    ToolDef(
        name="ask_user",
        description=(
            "Ask the user one or more questions before non-trivial work when a missing detail "
            "would change how you proceed, or to confirm an irreversible, expensive, or "
            "high-impact action (sending a message, a purchase, a deletion) — a confirmation is a "
            "question whose `options` are the choices. Each question may carry choice `options` "
            "(with `multi_select`), a `header`, or `free_text_only`. Do not use it for small talk, "
            "quick factual questions, or anything you can settle with a reasonable default. After "
            "calling it, ask in your reply and end your turn; the answer arrives as the next "
            "message."
        ),
        input_model=AskUserInput,
        handler=ask_user_handler,
    ),
    ToolDef(
        name="load_skill",
        description=(
            "Load a skill — a bundle of workflow instructions and files — so you can follow it. "
            "The skill and anything it depends on are mounted under the workspace, and each one's "
            "instructions come back with a tree of the files it mounted, so any path the workflow "
            "cites is already there to read. Load a skill proactively whenever its subject is "
            "relevant to the task. Cheap operation — be aggressive about loading."
        ),
        input_model=LoadSkillInput,
        handler=load_skill_handler,
    ),
    ToolDef(
        name="connect_account",
        description=(
            "Connect an external account to this agent through OAuth when the member asks in chat "
            "to connect a provider (for example their Gmail or GitHub). The account is connected "
            "privately to the asking member by default — only their own turns (and their scheduled "
            "jobs) can use it; set `shared` only when they say it is for the team. This leaves a "
            "private connection control for the member who asked; tell them to use it and end your "
            "turn. Never invent or expose an authorization URL in the conversation."
        ),
        input_model=ConnectAccountInput,
        handler=connect_account_handler,
    ),
    ToolDef(
        name="request_credentials",
        description=(
            "Ask the speaking member to fill credential slots (API keys, bot tokens, signing "
            "secrets) without the values passing through this conversation — their terminal "
            "prompts for each one privately. Use it when a capability needs a secret a member "
            "must supply; never ask for a secret in chat prose. Only the workspace owner can "
            "fill slots. After calling it, explain what you need in your reply and end your "
            "turn; verify the slots once the member says they have entered them."
        ),
        input_model=RequestCredentialsInput,
        handler=request_credentials_handler,
    ),
    ToolDef(
        name="wait_for_subagents",
        description=(
            "End your turn and wait for background subagent results. Call this after spawning "
            "subagents when you have no other independent work to do. You are automatically woken "
            "when all awaited subagents complete or when the user sends a message."
        ),
        input_model=WaitForSubagentsInput,
        handler=wait_for_subagents_handler,
    ),
    ToolDef(
        name="cancel_subagent",
        description=(
            "Cancel a running subagent. Sets its status to 'cancelled'. If the subagent has "
            "already finished, this is a no-op and returns its current status."
        ),
        input_model=CancelSubagentInput,
        handler=cancel_subagent_handler,
    ),
    ToolDef(
        name="message_subagent",
        description=(
            "Send a follow-up message to a background subagent. It runs as the subagent's next "
            "turn against its accumulated context once its current turn ends; the returned id "
            "addresses that follow-up for a later wait_for_subagents."
        ),
        input_model=MessageSubagentInput,
        handler=message_subagent_handler,
    ),
)

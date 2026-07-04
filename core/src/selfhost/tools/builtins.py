"""The builtin tool set: bash, read, write, edit, glob, grep, share_file, spawn_subagent,
load_sessions, ask_user, load_skill, connect_account, pause_and_wait, list_skills,
wait_for_subagents, cancel_subagent.

Each file/shell handler reaches the workspace only through `ctx.sandbox`, so the carrier's scoping
and egress rules apply whether a byte arrives via a shell command or a file op. `read`, `edit`, and
`write` run the in-sandbox `sbxfs` CLI, so windowing, ripgrep, and PDF/image render happen in the
container and only a bounded JSON result crosses back — the host never pulls a whole file over to
loop on it. `read` records every path it returns so `edit`/`write` can refuse to touch a file the
turn has not read — the guard that keeps a blind string-replace from clobbering content the model
never saw. `glob` and `grep` run the in-sandbox `sbxfs` matcher and ripgrep, so file discovery and
content search happen in the container and a bounded result crosses back. `share_file` streams a
produced workspace file straight out of the mount into the blob
store under `artifacts/<uuid>/` and returns a TTL-token URL the web surface serves — the only path
that hands a file back outside the sandbox, with no read cap and no whole-file buffer.
`spawn_subagent` delegates a typed subtask to a child turn through `ctx.spawn`. `load_sessions`
reads specific past conversation transcripts back from the blob store, scoped to the speaking
member's own conversations. `ask_user` is chat-native: it
structures a question or confirmation the agent poses in its reply, whose answer rides the member's
next message — no out-of-band prompt. `load_skill` mounts a skill's `SKILL.md` and assets into the
workspace and returns its workflow instructions. `list_skills` reports the loadable skills so the
agent can discover a workflow before starting. `pause_and_wait` is chat-native like `ask_user`: it
structures a wait the agent poses in its reply and ends the turn, resuming on the next inbound.
`wait_for_subagents` and `cancel_subagent` reach `ctx.subagents`, the same Subagents workflow that
backs `spawn`, to await a background child's terminal or cancel a running one — scoped to the
children this turn spawned."""

import json
import mimetypes
import shlex
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, Field, JsonValue

from selfhost.artifact_token import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_KEY_PREFIX,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from selfhost.blob import BlobNotFound
from selfhost.db import workspace_tx
from selfhost.grants import installed_connect_flow
from selfhost.models.interface import TextBlock
from selfhost.sandbox.session import WORKSPACE_DIR, workspace_path
from selfhost.schema import tables
from selfhost.skills.runtime import mount_skill
from selfhost.tools.context import ImageContent, TextContent, ToolContext, ToolResult
from selfhost.tools.registry import ToolDef
from selfhost.transcript import TranscriptDecodeError, decode, transcript_key

GREP_HEAD_LIMIT = 100
MAX_LOAD_SESSIONS = 25
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


class BashInput(BaseModel):
    command: str


class ReadInput(BaseModel):
    file_path: str
    offset: int | None = None
    limit: int | None = None


class WriteInput(BaseModel):
    file_path: str
    content: str


class FileEdit(BaseModel):
    old_string: str
    new_string: str
    replace_all: bool = False


class EditInput(BaseModel):
    file_path: str
    edits: tuple[FileEdit, ...] = Field(min_length=1)


class GlobInput(BaseModel):
    pattern: str
    path: str | None = None


class GrepInput(BaseModel):
    pattern: str
    glob: str | None = None
    context: int | None = None
    ignore_case: bool | None = None
    output_mode: Literal["content", "files_with_matches", "count"] | None = None
    head_limit: int | None = None


class ShareFileInput(BaseModel):
    file_path: str
    name: str | None = None
    subject: str | None = None


class SpawnSubagentInput(BaseModel):
    profile: str
    payload: dict[str, Any] = Field(default_factory=dict)
    background: bool = False


class LoadSessionsInput(BaseModel):
    session_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_LOAD_SESSIONS)


MAX_USER_QUESTIONS = 4


class QuestionOption(BaseModel):
    label: str
    description: str | None = None


class AskQuestion(BaseModel):
    question: str
    options: tuple[QuestionOption, ...] | None = None
    multi_select: bool | None = None
    free_text_only: bool | None = None
    header: str | None = None
    allow_attachments: bool | None = None


class AskUserInput(BaseModel):
    title: str
    questions: tuple[AskQuestion, ...] = Field(min_length=1, max_length=MAX_USER_QUESTIONS)


class LoadSkillInput(BaseModel):
    name: str


class ConnectAccountInput(BaseModel):
    provider: str


class PauseAndWaitInput(BaseModel):
    ai_response: str
    wait_minutes: int
    next_steps: str
    reason: str
    metadata: dict[str, JsonValue] | None = None


class ListSkillsInput(BaseModel):
    pass


class WaitForSubagentsInput(BaseModel):
    subagent_ids: tuple[str, ...] = Field(min_length=1)
    user_description: str


class CancelSubagentInput(BaseModel):
    subagent_id: str
    user_description: str


async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult:
    result = await ctx.sandbox.bash(args.command)
    return ToolResult(
        content=(TextContent(text=result.stdout + result.stderr),),
        is_error=result.exit_code != 0,
    )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"sbxfs read returned no {field}")
    return value


def _pdf_result(result: dict[str, object]) -> ToolResult:
    """A PDF read as model content: a text block (extracted text, the page window, any poppler note)
    then one image block per rendered page. Page renders are absent when poppler is unavailable in
    the sandbox, leaving a text-only result."""
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
        footer = f"[pdf pages {start}-{start + returned - 1} of {total}]"
        next_page = result.get("next_page")
        if isinstance(next_page, int):
            footer += f"; more pages - read with offset={next_page}"
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
    if result.get("type") == "pdf":
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
    record it as a shared_artifact of this turn, and mint a TTL download token the web surface
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
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{safe_name}"
    await ctx.sandbox.export_file(args.file_path, ctx.blob, key)
    media_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
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
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_TOKEN_TTL_SECONDS
    token = mint_artifact_token(ctx.artifact_token_secret, key, safe_name, expires_at)
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "url": f"{ARTIFACT_DOWNLOAD_PATH}?token={token}",
                        "name": safe_name,
                        "size_bytes": int(stat["size"]),
                        "digest": str(stat["digest"]),
                        "is_text": bool(stat["is_text"]),
                    }
                )
            ),
        )
    )


async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult:
    result = await ctx.spawn(args.profile, args.payload, args.background)
    if result.output is None:
        text = f"spawned {args.profile} subagent (turn {result.turn_id})"
    else:
        text = result.output.model_dump_json()
    return ToolResult(content=(TextContent(text=text),))


async def load_sessions_handler(ctx: ToolContext, args: LoadSessionsInput) -> ToolResult:
    """Load specific past conversation transcripts by id, scoped to the speaking member's own
    conversations in this workspace. Each id resolves to its durable transcript, rendered as the
    user/assistant text exchange; an id that is malformed, not the member's, not in this workspace,
    or whose transcript is missing or corrupt is collected into `failed` and never aborts the
    call."""
    requested: dict[UUID, str] = {}
    failed: list[str] = []
    for raw in args.session_ids:
        try:
            requested[UUID(raw)] = raw
        except ValueError:
            failed.append(raw)
    scope = (
        tables.conversation.c.member_id.is_(None)
        if ctx.member_id is None
        else tables.conversation.c.member_id == ctx.member_id
    )
    surfaces: dict[UUID, str] = {}
    if requested:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.conversation.c.id, tables.conversation.c.surface).where(
                        tables.conversation.c.id.in_(list(requested)),
                        tables.conversation.c.workspace_id == ctx.turn.workspace_id,
                        scope,
                    )
                )
            ).mappings().all()
        surfaces = {row["id"]: row["surface"] for row in rows}
    sessions: list[dict[str, object]] = []
    for conversation_id, raw in requested.items():
        if conversation_id not in surfaces:
            failed.append(raw)
            continue
        try:
            transcript = decode(await ctx.blob.get(transcript_key(conversation_id)))
        except (BlobNotFound, TranscriptDecodeError):
            failed.append(raw)
            continue
        messages: list[dict[str, str]] = []
        for message in transcript.messages:
            if isinstance(message.content, str):
                text = message.content
            else:
                text = "\n".join(
                    block.text for block in message.content if isinstance(block, TextBlock)
                )
            if text:
                messages.append({"role": message.role, "text": text})
        sessions.append(
            {"session_id": raw, "surface": surfaces[conversation_id], "messages": messages}
        )
    return ToolResult(
        content=(TextContent(text=json.dumps({"sessions": sessions, "failed": failed})),)
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
    """Resolve the named skill and its dependency closure, mount each into the workspace under
    `.skills/<name>/`, and return their instructions so the workflow is in front of the model at
    once. An unknown name fails loud as a recoverable tool error."""
    loaded = ctx.skills.tree(args.name)
    for skill in loaded:
        await mount_skill(ctx.sandbox, skill)
    header = "Loaded skill(s): " + ", ".join(skill.name for skill in loaded)
    bodies = "\n\n---\n\n".join(skill.prompt_body() for skill in loaded)
    return ToolResult(content=(TextContent(text=f"{header}\n\n{bodies}"),))


async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult:
    """Begin the OAuth handoff for the speaking member: the grantor is this turn's member and the
    grant binds to this turn's agent and conversation, all read from the context — the speaker gates
    the granting act, never the caller identity of a route. Returns the provider's authorize URL so
    the agent hands the member a link in its reply. A missing speaker, an uninstalled provider, or
    no credential key raises, surfacing to the model as a recoverable tool error."""
    if ctx.member_id is None:
        raise ValueError("connect requires a speaking member to gate the grant")
    url = installed_connect_flow().authorize(
        workspace_id=ctx.turn.workspace_id,
        agent_id=ctx.turn.agent_id,
        provider=args.provider,
        grantor_member_id=ctx.member_id,
        conversation_id=ctx.turn.conversation_id,
    )
    return ToolResult(content=(TextContent(text=url),))


PAUSE_DIRECTIVE = (
    "Pause here and end your turn — you resume when the awaited event arrives or the wait elapses."
)


async def pause_and_wait_handler(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult:
    """Chat-native pause: structure the wait the agent poses in its reply, then end the turn. Like
    ask_user, the resume rides the next inbound message (a member reply or a scheduled tick), never
    an out-of-band timer the loop holds — the payload's `wait_minutes` is advisory."""
    payload = {
        "awaiting": "timer",
        "ai_response": args.ai_response,
        "wait_minutes": args.wait_minutes,
        "next_steps": args.next_steps,
        "reason": args.reason,
    }
    return ToolResult(content=(TextContent(text=f"{PAUSE_DIRECTIVE}\n{json.dumps(payload)}"),))


async def list_skills_handler(ctx: ToolContext, args: ListSkillsInput) -> ToolResult:
    """List the loadable skills, each with its one-line description, so the agent can discover a
    workflow to load_skill before starting a domain task."""
    skills = [
        {"name": name, "description": description} for name, description in ctx.skills.index()
    ]
    return ToolResult(content=(TextContent(text=json.dumps({"skills": skills})),))


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
    return ToolResult(content=(TextContent(text=json.dumps({"subagents": done})),))


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


BUILTIN_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="bash",
        description="Run a shell command in the workspace and return its combined output.",
        input_model=BashInput,
        handler=bash_handler,
    ),
    ToolDef(
        name="read",
        description=(
            "Reads a file from the workspace. Returns up to 2000 lines by default; use "
            "offset/limit for large files. Lines longer than 2000 chars are truncated. For "
            "images: returns visual content for analysis. For PDFs: extracts text and renders "
            "page images (default 20 pages). Cannot read binary files."
        ),
        input_model=ReadInput,
        handler=read_handler,
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
    ),
    ToolDef(
        name="share_file",
        description=(
            "Send a file to the user as a downloadable link. The ONLY way to make a produced file "
            "visible outside the sandbox — the user CANNOT see a workspace file until this is "
            "called. The file must be under the /workspace directory. Any file type and size works "
            "(reports, code, csv, json, images, PDFs, large archives); it is streamed out, never "
            "read whole into memory. `name` sets the download name; any directory components in it "
            "are stripped. `subject` is an optional caption shown when a chat surface posts the "
            "file. Supports version history: use the same `name` parameter for updated versions."
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
        name="load_sessions",
        description=(
            "Load one or more past conversation transcripts by id, on demand — use when you need "
            "to recall content from specific past conversations not already in context. Each id is "
            "one of your own conversations; per-id failures (an unknown id, or one that is not "
            "yours) are reported in the 'failed' list and do not abort the call."
        ),
        input_model=LoadSessionsInput,
        handler=load_sessions_handler,
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
            "The skill and anything it depends on are mounted under the workspace and its "
            "instructions are returned at once. Load a skill proactively whenever its subject is "
            "relevant to the task. Cheap operation — be aggressive about loading."
        ),
        input_model=LoadSkillInput,
        handler=load_skill_handler,
    ),
    ToolDef(
        name="connect_account",
        description=(
            "Connect an external account to this agent through OAuth when the member asks in chat "
            "to connect a provider (for example their Gmail or GitHub). Returns an authorization "
            "URL — reply with the link so the member can open it and grant access; the account is "
            "linked once they finish. The connection is bound to the member who asked and this "
            "conversation."
        ),
        input_model=ConnectAccountInput,
        handler=connect_account_handler,
    ),
    ToolDef(
        name="pause_and_wait",
        description=(
            "Pause a workflow until an external event occurs or a timer expires. Use for waiting "
            "on verification emails, manual approvals, or API cooldowns. NOT for user-requested "
            "reminders or delayed actions — use a scheduled task for those."
        ),
        input_model=PauseAndWaitInput,
        handler=pause_and_wait_handler,
    ),
    ToolDef(
        name="list_skills",
        description="List the Skills this turn can load.",
        input_model=ListSkillsInput,
        handler=list_skills_handler,
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
)

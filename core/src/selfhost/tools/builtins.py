"""The builtin tool set: bash, read, write, edit, share_file, spawn_subagent, memory_search,
memory_update.

Each file/shell handler reaches the workspace only through `ctx.sandbox`, so the carrier's scoping
and egress rules apply whether a byte arrives via a shell command or a file op. `read` records every
path it returns so `edit` can refuse to touch a file the turn has not read — the guard that keeps a
blind string-replace from clobbering content the model never saw. `share_file` takes a workspace
file's bytes into the blob store under `artifacts/<uuid>/` and returns a TTL-token URL the web
surface serves — the only path that hands a produced file back outside the sandbox. `spawn_subagent`
delegates a typed subtask to a child turn through `ctx.spawn`. `memory_search` recalls facts and
searches synced source pages through `ctx.memory`, and `memory_update` commits — both scoped to the
conversation's subject (`{member, shared}`)."""

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from selfhost.artifact_token import (
    ARTIFACT_KEY_PREFIX,
    ARTIFACT_TOKEN_TTL_SECONDS,
    mint_artifact_token,
)
from selfhost.grants import installed_connect_flow
from selfhost.memory.service import SHARED_SUBJECT, member_subject, recall_subjects
from selfhost.schema.records import FACT, ItemClass, MemoryWrite
from selfhost.tools.context import TextContent, ToolContext, ToolResult
from selfhost.tools.registry import ToolDef

DEFAULT_READ_LIMIT = 2000
MEMORY_SEARCH_LIMIT = 8
ARTIFACT_DOWNLOAD_PATH = "/web/artifacts/download"
ARTIFACT_FALLBACK_NAME = "download"


class BashInput(BaseModel):
    command: str


class ReadInput(BaseModel):
    path: str
    offset: int = 0
    limit: int = DEFAULT_READ_LIMIT


class WriteInput(BaseModel):
    path: str
    content: str


class EditInput(BaseModel):
    path: str
    old_string: str
    new_string: str


class ShareFileInput(BaseModel):
    path: str
    filename: str | None = None


class SpawnSubagentInput(BaseModel):
    profile: str
    payload: dict[str, Any] = Field(default_factory=dict)
    background: bool = False


class MemorySearchInput(BaseModel):
    query: str
    limit: int = MEMORY_SEARCH_LIMIT


class MemoryUpdateInput(BaseModel):
    body: str
    item_class: ItemClass = FACT
    shared: bool = False
    source_ref: str | None = None


class ConnectAccountInput(BaseModel):
    provider: str


async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult:
    result = await ctx.sandbox.bash(args.command)
    return ToolResult(
        content=(TextContent(text=result.stdout + result.stderr),),
        is_error=result.exit_code != 0,
    )


async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult:
    lines = (await ctx.sandbox.read_file(args.path)).decode().splitlines()
    ctx.read_paths.add(args.path)
    window = lines[args.offset : args.offset + args.limit]
    if not window:
        text = f"(no lines at offset {args.offset}; file has {len(lines)} lines)"
    else:
        text = "\n".join(window)
    return ToolResult(content=(TextContent(text=text),))


async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult:
    await ctx.sandbox.write_file(args.path, args.content.encode())
    return ToolResult(content=(TextContent(text=f"wrote {args.path}"),))


async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult:
    if args.path not in ctx.read_paths:
        raise ValueError(f"file {args.path} must be read before it is edited")
    current = (await ctx.sandbox.read_file(args.path)).decode()
    occurrences = current.count(args.old_string)
    if occurrences == 0:
        raise ValueError(f"old_string not found in {args.path}")
    if occurrences > 1:
        raise ValueError(f"old_string is not unique in {args.path}: {occurrences} occurrences")
    updated = current.replace(args.old_string, args.new_string)
    await ctx.sandbox.write_file(args.path, updated.encode())
    return ToolResult(content=(TextContent(text=f"edited {args.path}"),))


async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult:
    """Store a workspace file as an artifact under `artifacts/<uuid>/<sanitized-name>` and mint a
    TTL download token. The returned URL is served only where a delivery surface (the web surface)
    is mounted. The sandbox read is raw bytes, so any file — text or binary — round-trips exactly;
    a file over the read cap is refused rather than streamed into memory."""
    if not ctx.artifact_token_secret:
        raise RuntimeError("artifact sharing is not configured (no artifact token secret set)")
    data = await ctx.sandbox.read_file(args.path)
    basename = PurePosixPath((args.filename or args.path).replace("\\", "/")).name
    safe_name = basename if basename not in ("", ".", "..") else ARTIFACT_FALLBACK_NAME
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{safe_name}"
    await ctx.blob.put(key, data)
    expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_TOKEN_TTL_SECONDS
    token = mint_artifact_token(ctx.artifact_token_secret, key, safe_name, expires_at)
    return ToolResult(content=(TextContent(text=f"{ARTIFACT_DOWNLOAD_PATH}?token={token}"),))


async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult:
    result = await ctx.spawn(args.profile, args.payload, args.background)
    if result.output is None:
        text = f"spawned {args.profile} subagent (turn {result.turn_id})"
    else:
        text = result.output.model_dump_json()
    return ToolResult(content=(TextContent(text=text),))


async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult:
    subjects = recall_subjects(ctx.member_id)
    recalled = await ctx.memory.recall(args.query, subjects, args.limit)
    sources = await ctx.memory.search_sources(args.query, subjects, args.limit)
    if not recalled and not sources:
        return ToolResult(content=(TextContent(text="No matching memory."),))
    lines = [f"- [{item.item_class}] {item.body}" for item in recalled]
    lines.extend(f"- [source] {match.text}" for match in sources)
    return ToolResult(content=(TextContent(text="\n".join(lines)),))


async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult:
    subject = (
        SHARED_SUBJECT
        if args.shared or ctx.member_id is None
        else member_subject(ctx.member_id)
    )
    await ctx.memory.commit(
        MemoryWrite(
            subject=subject, body=args.body, item_class=args.item_class, source_ref=args.source_ref
        )
    )
    return ToolResult(content=(TextContent(text=f"Remembered ({subject})."),))


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


BUILTIN_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="bash",
        description="Run a shell command in the workspace and return its combined output.",
        input_model=BashInput,
        handler=bash_handler,
    ),
    ToolDef(
        name="read",
        description="Read a workspace file, returning a line window from offset up to limit lines.",
        input_model=ReadInput,
        handler=read_handler,
    ),
    ToolDef(
        name="write",
        description="Write content to a workspace file, creating or overwriting it.",
        input_model=WriteInput,
        handler=write_handler,
    ),
    ToolDef(
        name="edit",
        description="Replace a unique string in a workspace file that has already been read.",
        input_model=EditInput,
        handler=edit_handler,
    ),
    ToolDef(
        name="share_file",
        description=(
            "Share a workspace file the agent produced as a downloadable link: read its bytes, "
            "store them as an artifact, and return a time-limited URL to hand back to the user — "
            "the only way to deliver a produced file outside the sandbox. Any file type works "
            "(reports, code, csv, json, images, PDFs); a file larger than the sandbox read cap is "
            "refused. `filename` sets the download name; any directory components in it are "
            "stripped."
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
        name="memory_search",
        description=(
            "Search memory for facts, notes, and synced source documents relevant to a query, "
            "over the current member's memory and shared memory. Returns the best-matching items "
            "and document snippets; use it to recall context before answering."
        ),
        input_model=MemorySearchInput,
        handler=memory_search_handler,
    ),
    ToolDef(
        name="memory_update",
        description=(
            "Record a durable memory item so later turns and conversations can recall it. Writes "
            "to the current member's memory by default, or shared memory when `shared` is true."
        ),
        input_model=MemoryUpdateInput,
        handler=memory_update_handler,
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
)

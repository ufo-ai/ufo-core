"""The builtin tool set: bash, read, write, edit, share_file, spawn_subagent, memory_search,
memory_update.

Each file/shell handler reaches the workspace only through `ctx.sandbox`, so the carrier's scoping
and egress rules apply whether a byte arrives via a shell command or a file op. `read`, `edit`, and
`write` run the in-sandbox `sbxfs` CLI, so windowing, ripgrep, and PDF/image render happen in the
container and only a bounded JSON result crosses back — the host never pulls a whole file over to
loop on it. `read` records every path it returns so `edit`/`write` can refuse to touch a file the
turn has not read — the guard that keeps a blind string-replace from clobbering content the model
never saw. `share_file` streams a produced workspace file straight out of the mount into the blob
store under `artifacts/<uuid>/` and returns a TTL-token URL the web surface serves — the only path
that hands a file back outside the sandbox, with no read cap and no whole-file buffer.
`spawn_subagent` delegates a typed subtask to a child turn through `ctx.spawn`. `memory_search`
recalls facts and searches synced source pages through `ctx.memory`, and `memory_update` commits —
both scoped to the conversation's subject (`{member, shared}`)."""

import json
import shlex
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
from selfhost.sandbox.session import workspace_path
from selfhost.schema.records import FACT, ItemClass, MemoryWrite
from selfhost.tools.context import TextContent, ToolContext, ToolResult
from selfhost.tools.registry import ToolDef

MEMORY_SEARCH_LIMIT = 8
ARTIFACT_DOWNLOAD_PATH = "/web/artifacts/download"
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


class ShareFileInput(BaseModel):
    file_path: str
    name: str | None = None


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
    params: dict[str, object] = {"path": args.file_path}
    if args.offset is not None:
        params["offset"] = args.offset
    if args.limit is not None:
        params["limit"] = args.limit
    result = await ctx.sandbox.run_sbxfs("read", params)
    ctx.read_paths.add(args.file_path)
    if result.get("type") in ("image", "pdf"):
        return ToolResult(content=(TextContent(text=json.dumps(result)),))
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


async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult:
    """Stream a produced workspace file into the artifact store under `artifacts/<uuid>/<name>` and
    mint a TTL download token the web surface serves — the only path a produced file leaves the
    sandbox. A preflight in the container streams the file to derive its size and sha256 without
    loading it whole; the carrier then copies it out of the workspace mount into the blob store the
    same way, so any file type and size shares without a read cap or a whole-file host buffer."""
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
        name="share_file",
        description=(
            "Send a file to the user as a downloadable link. The ONLY way to make a produced file "
            "visible outside the sandbox — the user CANNOT see a workspace file until this is "
            "called. The file must be under the /workspace directory. Any file type and size works "
            "(reports, code, csv, json, images, PDFs, large archives); it is streamed out, never "
            "read whole into memory. `name` sets the download name; any directory components in it "
            "are stripped."
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

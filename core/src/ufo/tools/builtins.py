"""The builtin tool set: bash, read, write, edit, glob, grep, share_file, spawn,
ask_user, request_credentials, load_skill, skill_search, connect_account,
cancel_spawn, message_spawn.

Each file/shell handler reaches files only through `ctx.sandbox`, so the carrier's path and egress
rules apply whether a byte arrives via a shell command or a file op. `read`, `edit`, and `write` run
the in-sandbox `ufo fs` CLI, so windowing happens beside the files and only a bounded JSON result
crosses back. Document reads send bounded bytes to `ufo-preview`; a connected
terminal relays those bytes through the deploy because it cannot reach the synthetic preview host.
`read` records every path it returns so `edit`/`write` can refuse to touch a file the
turn has not read — the guard that keeps a blind string-replace from clobbering content the model
never saw. `glob` and `grep` run the in-sandbox `ufo fs` matcher and ripgrep, so file discovery and
content search happen in the container and a bounded result crosses back. `share_file` lands
produced workspace files in the blob store under `artifacts/<uuid>/` — a directory as a `.tar.gz`
of itself, and on S3 the sandbox uploads each itself to a presigned PUT bound to the size and
sha256 a preflight measured — and returns a TTL-token URL per file that core's artifact route
serves: the only path that hands a file back outside the sandbox, with no read cap and no
whole-file buffer.
`spawn` delegates a typed subtask to a child turn through `ctx.spawn` — a subagent profile or a
workspace agent, one verb over both. It blocks on the child, and a message arriving on the
conversation ends that wait the way a foreground budget ends bash's: the child keeps running in the
background and delivers its own result, so the turn is free to answer the member. `ask_user` is
chat-native: it
structures a question or confirmation the agent poses in its reply, whose answer rides the member's
next message — no out-of-band prompt. `request_credentials` is its secret-collecting sibling: it
seals which slots the speaking owner will fill and ends the turn; a capable surface prompts for the
values privately and fulfillment lands them in the encrypted store, never the transcript.
`load_skill` loads a skill's `SKILL.md` and assets — and those of the whole chain it `depends` on —
under `$UFO_HOME/skills`, and returns each one's workflow followed by one tree of those files; the
system prompt's `<available_skills>` block indexes the deploy tier and a member turn's
`<saved_skills>` block the agent's saved skills. `skill_search` ranks every loadable skill's
routing card by keyword and returns matching lines, the reach into whatever neither block shows.
`cancel_spawn` and `message_spawn` reach `ctx.subagents`, the same
Subagents workflow that backs `spawn`, to cancel a running child or queue it a follow-up message
that runs as its next turn — scoped to the children this turn
spawned."""

import json
import mimetypes
import shlex
from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import AfterValidator, BaseModel, Field

from ufo.access.grants import installed_connect_flow
from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.db import workspace_tx
from ufo.kinds.agents import RESTORE_APPLICATION_TOOL_DEF
from ufo.kinds.artifacts import artifact_object_names
from ufo.kinds.members import ADD_MEMBER_TOOL_DEF
from ufo.media.artifact_url import (
    ARTIFACT_KEY_PREFIX,
    artifact_media_type,
    artifact_url_expiry,
    mint_artifact_url,
)
from ufo.o11y import log
from ufo.sandbox.preview import PREVIEW_HOST
from ufo.sandbox.session import TOOL_OUTPUT_DIRNAME, WORKSPACE_DIR, shell_path, workspace_path
from ufo.schema import tables
from ufo.schema.records import AskUserInput, ConnectRequest, CredentialPrompt, CredentialRequest
from ufo.skills.runtime import load_skills, loaded_context
from ufo.skills.selection import SKILL_LINE_MAX_CHARS, lexical_score
from ufo.tools.context import (
    AmbiguousSpawnTarget,
    ImageContent,
    TextContent,
    ToolContext,
    ToolResult,
    UnknownSpawnTarget,
)
from ufo.tools.file_changes import FILE_CHANGE_PATH_MAX_CHARS
from ufo.tools.registry import ToolDef
from ufo.tools.tasks import (
    BACKGROUND_TASKS_DIR,
    run_task,
    task_handles,
    task_id,
    timeout_notice,
)

GREP_HEAD_LIMIT = 100
FILE_PATH_JSON_MAX_CHARS = 10_000
FILE_TOOL_RESULT_MAX_CHARS = 20_000
ARTIFACT_FALLBACK_NAME = "download"
SHARE_PREFLIGHT_TIMEOUT_SECONDS = 300
SHA256_DIGEST_PREFIX = "sha256:"
ARTIFACT_PUT_MAX_BYTES = 5 * 1024 * 1024 * 1024
ARTIFACT_PUT_TTL_SECONDS = 900
ARTIFACT_PUT_TIMEOUT_SECONDS = 900
ARTIFACT_PREVIEW_SUFFIXES = frozenset((".csv", ".docx", ".md", ".pdf", ".pptx", ".svg", ".xlsx"))
ARTIFACT_PREVIEW_MEDIA_TYPE = "image/png"
ARTIFACT_PREVIEW_MAX_WIDTH = 1000
ARTIFACT_PREVIEW_MAX_HEIGHT = 1400
ARTIFACT_PREVIEW_TIMEOUT_SECONDS = 330
ARTIFACT_PREVIEW_DETAIL_CHARS = 500

SHARE_DIR_PROBE_CMD = "[ -d {path} ] && [ ! -L {path} ]"
SHARE_PACK_TIMEOUT_SECONDS = 900
SHARE_PACK_CMD = (
    "root={path}\n"
    'name=$(basename "$root")\n'
    'tar -czf {archive} -C "$(dirname "$root")" {exclude} "$name"'
)

SHARE_PREFLIGHT_CMD = (
    "p={path}\n"
    '[ -f "$p" ] && [ ! -L "$p" ] || {{ printf %s "$p is not a regular file" >&2; exit 1; }}\n'
    'size=$(wc -c < "$p" | tr -d " ") || exit 1\n'
    'digest=$(openssl dgst -sha256 "$p") || exit 1\n'
    "digest=${{digest##* }}\n"
    'kept=$(head -c 4096 "$p" | tr -d "\\000" | wc -c | tr -d " ")\n'
    'seen=$(head -c 4096 "$p" | wc -c | tr -d " ")\n'
    'text=true; [ "$kept" = "$seen" ] || text=false\n'
    'printf \'{{"size":%d,"digest":"sha256:%s","is_text":%s}}\' "$size" "$digest" "$text"\n'
)
"""Measure a produced file's size, sha256 and text-ness with tools every carrier has — `wc`,
`openssl`, `head`, `tr` — so the same one command runs in the container and on a member's own
machine, where no baked `ufo` client or usable `python3` exists. The size and digest bind the S3
presigned PUT (§`_store_artifact`), so a file changing between the measure and the upload fails at
S3 rather than landing as a self-consistent lie. A symlink at the target is refused, the one
containment the share path needs: the bytes it copies out must be the file the agent named, not a
link's target."""


MAX_REQUESTED_SLOTS = 4


class BashInput(BaseModel):
    command: str = Field(description="The shell command to execute.")
    timeout: int | None = Field(
        default=None,
        description="How long to wait for the command in the foreground, in milliseconds. Max "
        "600000 (10 minutes). A command still running at the deadline is not stopped — it "
        "continues in the background and the result hands back its task id, log path, and pid.",
    )
    background: bool = Field(
        default=False,
        description="Run the command detached and return at once with its task id, log path, and "
        "pid instead of waiting for it. The result names its log, pid, and exit files under "
        "`$UFO_HOME/runs/<id>/tasks`. The command's network egress ends with this turn, "
        "and a sandbox that suspends between turns advances it only while awake — use background "
        "for compute that needs no network past this turn: builds, test runs, data processing.",
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


def _bounded_file_path(path: str) -> str:
    if len(json.dumps(path, ensure_ascii=False)) > FILE_PATH_JSON_MAX_CHARS:
        raise ValueError("file path expands beyond its result envelope")
    return path


_FilePath = Annotated[
    str,
    Field(max_length=FILE_CHANGE_PATH_MAX_CHARS),
    AfterValidator(_bounded_file_path),
]


class WriteInput(BaseModel):
    file_path: _FilePath = Field(
        description="Absolute path to the file to write, e.g. /workspace/output.json.",
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
    file_path: _FilePath = Field(description="Absolute path to the file to modify.")
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
    path: str | None = Field(
        default=None,
        description="Absolute path to the directory to search in. If omitted, searches from the "
        "workspace root.",
    )
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


class SharedFileSpec(BaseModel):
    file_path: str = Field(
        description="Absolute path to the file to share. A directory path is packed and shared "
        "as a .tar.gz archive of that directory — no need to tar it yourself."
    )
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


class ShareFileInput(BaseModel):
    files: list[SharedFileSpec] = Field(
        min_length=1, description="The files to share, delivered in this order."
    )


class SpawnInput(BaseModel):
    target: str = Field(
        description="What to run — a subagent profile or a workspace agent, by name, each fixing "
        "the child's prompt, tool set, and input/output contract. The spawn-catalog skill lists "
        "every target and its payload. A name both kinds hold needs its qualified form "
        "('profile:research' or 'agent:research')."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Arguments matching the target's input schema."
    )
    background: bool = Field(
        default=False,
        description="Run in the background and return the child turn id immediately instead of "
        "waiting for its validated output. Waiting is safe either way: a spawn still running when "
        "a message arrives on this conversation is not stopped — it keeps running in the "
        "background and the result hands back its spawn id.",
    )
    name: str = Field(
        default="",
        description="A short display name for this run, at most four words, e.g. 'UK sports news'.",
    )


class LoadSkillInput(BaseModel):
    name: str = Field(
        description="The skill name, e.g. 'office/pptx', 'data/visualization'. Choose from the "
        "system prompt's <available_skills> index, a <saved_skills> block, or a skill_search "
        "result."
    )


class SkillSearchInput(BaseModel):
    query: str = Field(description="Keywords naming the task or capability to find a skill for.")
    limit: int = Field(default=8, ge=1, le=8, description="Maximum results.")


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
    agent: str = Field(
        default="",
        description="Grant the connection to this agent instead of yourself, by name. Only the "
        "workspace main agent may name another agent. Use it when a member asks you to finish "
        "setting up an agent that cannot ask for itself.",
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


class AskUserCall(AskUserInput):
    """The `ask_user` call: the question record the terminal frame carries, plus the activity
    narration that never reaches the frame."""


class CancelSpawnInput(BaseModel):
    spawn_id: str = Field(description="The spawn ID to cancel.")


class MessageSpawnInput(BaseModel):
    spawn_id: str = Field(description="The spawn ID to message.")
    message: str = Field(
        description="The follow-up message to deliver, run as the spawn's next turn."
    )


async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult:
    """Run one command through the task journal and report what ended it. A command still running at
    its budget is not stopped: it goes on detached and the result hands back the handles it can be
    watched by, so work already paid for keeps running while the caller does something else — a
    foreground budget is how long the caller waits, never how long the work may take. Foreground and
    background differ only in whether this waits.

    Only the sandbox failing to run the command at all is an error, and it reports the deadline that
    fired rather than a bare `exit code: 124`, which `timeout` inside the command produces just as
    the sandbox does — a caller reading the code alone cannot tell which fired."""
    if args.background:
        return await _bash_background(ctx, args.command)
    run = await run_task(ctx, args.command, args.timeout)
    if (applied_s := run.result.timed_out_after_s) is not None:
        if run.pid is None:
            notice = timeout_notice(applied_s, run.requested_s)
            return ToolResult(content=(TextContent(text=notice),), is_error=True)
        handles = task_handles(run.task_id, run.pid, run.display_base, applied_s=applied_s)
        return ToolResult(content=(TextContent(text=handles),))
    output = run.result.stdout + run.result.stderr
    if run.result.exit_code == 0:
        return ToolResult(content=(TextContent(text=output),))
    notice = f"exit code: {run.result.exit_code}"
    return ToolResult(
        content=(TextContent(text=f"{output}\n{notice}" if output else notice),),
        is_error=True,
    )


async def _bash_background(ctx: ToolContext, command: str) -> ToolResult:
    """Detach the command and hand back its handles without ever waiting on it — the same launch a
    foreground command rides, stopping at the pid."""
    task = task_id(ctx)
    base = await ctx.sandbox.runtime_path(f"{BACKGROUND_TASKS_DIR}/{task}")
    display_base = await ctx.sandbox.runtime_display_path(f"{BACKGROUND_TASKS_DIR}/{task}")
    started = await ctx.sandbox.bash_task(command, base, detach=True)
    if started.exit_code != 0 or not started.stdout.strip():
        return ToolResult(
            content=(TextContent(text=started.stderr or "the command did not detach"),),
            is_error=True,
        )
    return ToolResult(
        content=(TextContent(text=task_handles(task, started.stdout.strip(), display_base)),)
    )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"ufo fs read returned no {field}")
    return value


def _document_result(result: dict[str, object]) -> ToolResult:
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
        kind = result.get("type")
        if kind not in ("pdf", "pptx", "docx", "xlsx"):
            raise RuntimeError("ufo fs read returned an unknown document type")
        unit = "slides" if kind == "pptx" else "pages"
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
                raise RuntimeError("ufo fs read returned a malformed document page")
            blocks.append(
                ImageContent(
                    media_type=_require_str(page.get("media_type"), "media_type"),
                    data=_require_str(page.get("data"), "data"),
                )
            )
    if not blocks:
        raise RuntimeError("ufo fs read returned an empty document result")
    return ToolResult(content=tuple(blocks))


async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult:
    params: dict[str, object] = {"path": args.file_path}
    if args.offset is not None:
        params["offset"] = args.offset
    if args.limit is not None:
        params["limit"] = args.limit
    result = await ctx.sandbox.run_ufo_fs("read", params)
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
    if result.get("type") in ("pdf", "pptx", "docx", "xlsx"):
        return _document_result(result)
    if result.get("is_empty"):
        return ToolResult(content=(TextContent(text="(file is empty)"),))
    start = result.get("start_line")
    total = result.get("total_lines")
    returned = result.get("lines_returned")
    if not (isinstance(start, int) and isinstance(total, int) and isinstance(returned, int)):
        raise RuntimeError("ufo fs read returned a malformed text result")
    if returned == 0:
        return ToolResult(
            content=(TextContent(text=f"(no lines at offset {start}; file has {total} lines)"),)
        )
    content = result.get("content")
    if not isinstance(content, str):
        raise RuntimeError("ufo fs read returned no content")
    footer = f"\n\n[lines {start}-{start + returned - 1} of {total}]"
    remaining = result.get("remaining_lines")
    if isinstance(remaining, int) and not isinstance(remaining, bool) and remaining > 0:
        footer += f"; {remaining} more - read with offset={result.get('next_offset')}"
    return ToolResult(content=(TextContent(text=content + footer),))


async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult:
    data = args.content.encode()
    staged = f"{WORKSPACE_DIR}/ufo-write-{uuid4().hex}.stage"
    await ctx.sandbox.write_file(staged, data)
    result = await ctx.sandbox.run_ufo_fs(
        "write",
        {
            "path": args.file_path,
            "staged_path": staged,
            "allow_existing": args.file_path in ctx.read_paths,
        },
    )
    trailing = 1 if data and not data.endswith(b"\n") else 0
    result.update(
        {
            "path": args.file_path,
            "size_bytes": len(data),
            "lines": args.content.count("\n") + trailing,
        }
    )
    tool_result = _file_tool_result(result)
    ctx.read_paths.add(args.file_path)
    return tool_result


async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult:
    if args.file_path not in ctx.read_paths:
        raise ValueError(f"file {args.file_path} must be read before it is edited")
    edits = [
        {
            "old_string_b64": b64encode(e.old_string.encode(), altchars=b"-_").decode(),
            "new_string_b64": b64encode(e.new_string.encode(), altchars=b"-_").decode(),
            "replace_all": e.replace_all,
        }
        for e in args.edits
    ]
    result = await ctx.sandbox.run_ufo_fs("edit", {"path": args.file_path, "edits": edits})
    result["path"] = args.file_path
    return _file_tool_result(result)


def _file_tool_result(result: dict[str, object]) -> ToolResult:
    """A write's or an edit's result, bounded. What the file now says is the model's own to recall
    or to read back, so the result states what landed and where — never a diff of it, which is a
    question the workspace answers once for every writer rather than one tool answering per call.
    The edit snippet is the one part that can outgrow the bound, and it goes first."""
    content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(content) <= FILE_TOOL_RESULT_MAX_CHARS:
        return ToolResult(content=(TextContent(text=content),))
    result.pop("snippet", None)
    if "message" in result:
        result["message"] = f"{result.get('replacements', 0)} replacements"
    content = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(content) > FILE_TOOL_RESULT_MAX_CHARS:
        raise RuntimeError("file tool result exceeds its limit")
    return ToolResult(content=(TextContent(text=content),))


async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult:
    """Match files by glob pattern inside the container through the `ufo fs` CLI, so the traversal
    runs in the sandbox and only the matching paths cross back. Defaults to the workspace root."""
    result = await ctx.sandbox.run_ufo_fs(
        "glob", {"pattern": args.pattern, "path": args.path or WORKSPACE_DIR}
    )
    return ToolResult(content=(TextContent(text=json.dumps(result)),))


async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult:
    """Search file contents for a regex under one directory through the in-sandbox `ufo fs`
    ripgrep, so the scan runs in the container and a bounded result crosses back. `head_limit` caps
    the matches returned."""
    params: dict[str, object] = {
        "pattern": args.pattern,
        "path": args.path or WORKSPACE_DIR,
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
    result = await ctx.sandbox.run_ufo_fs("grep", params)
    return ToolResult(content=(TextContent(text=json.dumps(result)),))


async def _store_artifact(
    ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str
) -> None:
    """Put the preflighted file under `key`, by the one route the store offers.

    S3: serve mints a presigned PUT bound to `size_bytes` and `digest`, and the sandbox uploads to
    it over the egress proxy — the bytes go sandbox → S3 and never cross this process, and S3
    refuses any body that is not the measured one, so a file still being written between the
    preflight and the upload fails loudly instead of landing as a self-consistent lie. The URL is an
    argv element of one `curl`, which is what the sandbox already does to stage a connector's file
    inputs; binding it to those measurements is what makes holding it worth nothing beyond this one
    upload. A non-2xx carries S3's own error document on stdout, so a failure names its cause.

    Filesystem: there is no URL to sign, so the bytes stream out of the container through the
    carrier and into the store in bounded chunks."""
    match ctx.blob.backend:
        case S3BlobStore():
            if size_bytes > ARTIFACT_PUT_MAX_BYTES:
                raise ValueError(
                    f"{scoped} is {size_bytes} bytes; a shared file is capped at "
                    f"{ARTIFACT_PUT_MAX_BYTES} bytes"
                )
            checksum = b64encode(bytes.fromhex(digest.removeprefix(SHA256_DIGEST_PREFIX))).decode()
            url = await ctx.blob.presigned_put(key, size_bytes, checksum, ARTIFACT_PUT_TTL_SECONDS)
            put = await ctx.sandbox.bash(
                f"curl -sS --fail-with-body -T {shell_path(scoped)} "
                f"-H {shlex.quote(f'x-amz-checksum-sha256: {checksum}')} "
                f"--url {shlex.quote(url)}",
                timeout_s=ARTIFACT_PUT_TIMEOUT_SECONDS,
            )
            if put.exit_code != 0:
                detail = put.stdout.strip() or put.stderr.strip()
                raise RuntimeError(detail or f"uploading {scoped} to the artifact store failed")
        case FilesystemBlobStore():
            await ctx.blob.put_stream(key, ctx.sandbox.read_file(scoped))


@dataclass(frozen=True)
class ArtifactPreview:
    """The rendered picture of a shared document, as a second blob beside the file's own bytes."""

    blob_key: str
    media_type: str
    size_bytes: int


async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str) -> ArtifactPreview | None:
    """Render the first page of a shared document so a member sees the file rather than its name.

    The picture is produced by the preview service (RFC 0037), not in the sandbox: the file streams
    to it over the egress proxy in one `curl`, the service renders and PUTs the PNG to a presigned
    URL core mints for the preview key, and answers only the metadata core records. The URL is
    unmeasured — core cannot know the render's size before it exists — and its key is fixed here, so
    the service can store this one preview and nothing else. The store must be S3 for core to mint
    that URL; a filesystem dev store renders no preview and the share still lands.

    A render that fails, times out, or comes back unparseable yields no preview and never the share:
    the member's file is already stored, and a missing picture is not a reason to lose it. The
    reason is logged, so a format that never renders is visible rather than merely absent."""
    suffix = PurePosixPath(safe_name).suffix.lower()
    if suffix not in ARTIFACT_PREVIEW_SUFFIXES:
        return None
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{PurePosixPath(safe_name).stem}.png"
    match ctx.blob.backend:
        case S3BlobStore():
            put_url = await ctx.blob.presigned_put_unmeasured(key, ARTIFACT_PUT_TTL_SECONDS)
        case _:
            return None
    request_json = json.dumps(
        {
            "kind": suffix[1:],
            "max_width": ARTIFACT_PREVIEW_MAX_WIDTH,
            "max_height": ARTIFACT_PREVIEW_MAX_HEIGHT,
            "pages": 1,
            "sink": {"put_url": put_url},
        }
    )
    render = await ctx.sandbox.bash(
        "curl -sS --fail-with-body "
        f"-F {shlex.quote('request=' + request_json)} "
        f"-F file=@{shell_path(scoped)} "
        f"--url {shlex.quote(f'https://{PREVIEW_HOST}/render')}",
        timeout_s=ARTIFACT_PREVIEW_TIMEOUT_SECONDS,
    )
    if render.exit_code != 0:
        log(
            "share_file.preview.refused",
            filename=safe_name,
            detail=(render.stderr.strip() or render.stdout.strip())[:ARTIFACT_PREVIEW_DETAIL_CHARS],
        )
        return None
    try:
        size_bytes = int(json.loads(render.stdout)["size_bytes"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        log(
            "share_file.preview.unparsed",
            filename=safe_name,
            detail=render.stdout[:ARTIFACT_PREVIEW_DETAIL_CHARS],
        )
        return None
    return ArtifactPreview(
        blob_key=key, media_type=ARTIFACT_PREVIEW_MEDIA_TYPE, size_bytes=size_bytes
    )


@dataclass(frozen=True)
class _StagedShare:
    """One file of a share, already stored: the measurements the upload was bound to, the blob key
    it landed under, and the preview rendered beside it — everything the row insert and the result
    entry need."""

    safe_name: str
    key: str
    size_bytes: int
    digest: str
    is_text: bool
    subject: str | None
    preview: ArtifactPreview | None


async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None:
    """A directory at the share path becomes a `.tar.gz` of itself, packed in the container into
    the engine's own offload dir — the archive is the file the rest of the share measures, uploads,
    and names. A regular file passes through as None, and a symlink at the path falls through to
    the preflight's refusal. A pack that reaches the offload dir (the workspace root) leaves it
    out: the archive is written there, so packing it would tar the archive into itself and ship
    the engine's scratch renders beside the member's tree.

    The exclusion is the member name `tar` itself stores, which the shell derives from the packed
    directory rather than from the logical path: the local and terminal carriers serve `/workspace`
    from a host directory named after the conversation, so a pattern spelled `workspace/...` here
    would match nothing there and the offload dir would pack."""
    probe = await ctx.sandbox.bash(
        SHARE_DIR_PROBE_CMD.format(path=shlex.quote(scoped)),
        timeout_s=SHARE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if probe.exit_code != 0:
        return None
    await ctx.sandbox.ensure_tool_output_dir()
    packed = await ctx.sandbox.runtime_path(f"{TOOL_OUTPUT_DIRNAME}/share-{uuid4().hex}.tar.gz")
    exclude = ""
    pack = await ctx.sandbox.bash(
        SHARE_PACK_CMD.format(
            path=shlex.quote(scoped), archive=shell_path(packed), exclude=exclude
        ),
        timeout_s=SHARE_PACK_TIMEOUT_SECONDS,
    )
    if pack.exit_code != 0:
        raise RuntimeError(pack.stderr.strip() or f"packing {scoped} failed")
    return packed


async def _staged_share(ctx: ToolContext, spec: SharedFileSpec) -> _StagedShare:
    """Stage one file into the artifact store under `artifacts/<uuid>/<name>`. A directory is
    packed into a `.tar.gz` of itself first (`_packed_directory`) and the archive is what shares.
    A preflight in the container confines the path through the containment guard and streams the
    file off the fd that descent pinned to derive its size and sha256 without loading it whole, so
    a link the agent planted at the name is refused rather than copied out, and the upload is then
    bound to those two measurements, so nothing crosses on the sandbox's word and no whole-file
    buffer ever forms in this process."""
    scoped = workspace_path(spec.file_path)
    normalized = spec.file_path.replace("\\", "/")
    packed = await _packed_directory(ctx, scoped)
    source = scoped if packed is None else packed
    default_name = normalized if packed is None else f"{PurePosixPath(normalized).name}.tar.gz"
    source_suffix = PurePosixPath(normalized).suffix if packed is None else ".tar.gz"
    preflight = await ctx.sandbox.bash(
        SHARE_PREFLIGHT_CMD.format(path=shell_path(source)),
        timeout_s=SHARE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if preflight.exit_code != 0:
        raise RuntimeError(
            preflight.stderr.strip() or f"artifact preflight failed for {spec.file_path}"
        )
    stat = json.loads(preflight.stdout)
    basename = PurePosixPath((spec.name or default_name).replace("\\", "/")).name
    safe_name = basename if basename not in ("", ".", "..") else ARTIFACT_FALLBACK_NAME
    if (
        source_suffix
        and mimetypes.guess_type(safe_name)[0] is None
        and not safe_name.lower().endswith(source_suffix.lower())
    ):
        safe_name += source_suffix
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{safe_name}"
    await _store_artifact(ctx, source, key, int(stat["size"]), str(stat["digest"]))
    preview = await _shared_preview(ctx, source, safe_name)
    return _StagedShare(
        safe_name=safe_name,
        key=key,
        size_bytes=int(stat["size"]),
        digest=str(stat["digest"]),
        is_text=bool(stat["is_text"]),
        subject=spec.subject,
        preview=preview,
    )


async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult:
    """Land produced workspace files in the artifact store, record each as a shared_artifact of
    this turn, and mint a TTL download token per file that core's artifact route serves — the only
    path a produced file leaves the sandbox. Every file stages (`_staged_share`) before any row
    lands, so a refused path shares nothing, and the rows commit in one transaction with
    `created_at` stamped a microsecond apart in list order — share order is what every surface
    orders on, so the files arrive in the order the call named them. The shared_artifact records
    are what an async surface (Slack) reads to upload the files into the turn's posted reply; a
    file's `subject` is an optional caption — absent, the file renders under its plain name."""
    if not ctx.artifact_token_secret:
        raise RuntimeError("artifact sharing is not configured (no artifact token secret set)")
    staged = [await _staged_share(ctx, spec) for spec in args.files]
    shared_at = datetime.now(UTC)
    async with workspace_tx() as connection:
        for index, share in enumerate(staged):
            stamp = shared_at + timedelta(microseconds=index)
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    id=uuid4(),
                    turn_id=ctx.turn.id,
                    blob_key=share.key,
                    workspace_id=ctx.turn.workspace_id,
                    filename=share.safe_name,
                    subject=share.subject,
                    media_type=artifact_media_type(share.safe_name),
                    size_bytes=share.size_bytes,
                    preview_blob_key=None if share.preview is None else share.preview.blob_key,
                    preview_media_type=None if share.preview is None else share.preview.media_type,
                    preview_size_bytes=None if share.preview is None else share.preview.size_bytes,
                    created_at=stamp,
                    updated_at=stamp,
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
    object_names = artifact_object_names(
        [(row.conversation_id, row.filename) for row in identities]
    )
    expires_at = artifact_url_expiry(datetime.now(UTC))
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    [
                        {
                            "url": mint_artifact_url(
                                ctx.artifact_token_secret,
                                share.key,
                                expires_at,
                                workspace_id=ctx.turn.workspace_id,
                            ),
                            "name": share.safe_name,
                            "artifact": object_names[(ctx.turn.conversation_id, share.safe_name)],
                            "size_bytes": share.size_bytes,
                            "digest": share.digest,
                            "is_text": share.is_text,
                        }
                        for share in staged
                    ]
                )
            ),
        )
    )


SPAWN_DETACHED_LEAD = "The spawn runs in the background."
SPAWN_MOVED_LEAD = (
    "A message arrived on this conversation, so the spawn did not answer in front of you and "
    "continues in the background. Its output is not coming back here: answer the message rather "
    "than waiting on the spawn again."
)
SPAWN_BACKGROUND_DIRECTIVE = (
    "Its validated output — or the question it ends asking — arrives on this conversation as a "
    "message when it finishes. `message_spawn` sends it a follow-up and `cancel_spawn` ends it."
)


def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str:
    """The handles a background spawn is reached by, whichever way it got there — one text for a
    spawn backgrounded on request and one an arriving message moved, so the two can never drift
    apart, the way `task_handles` holds bash's two."""
    lead = SPAWN_MOVED_LEAD if moved else SPAWN_DETACHED_LEAD
    payload = {"spawn_id": str(turn_id), "target": target, "status": "running"}
    return f"{lead} {SPAWN_BACKGROUND_DIRECTIVE}\n{json.dumps(payload)}"


async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult:
    """Run the target as a child turn and report what ended the call. A foreground spawn returns the
    child's validated output, unless a message arrives on this conversation first: the child is then
    not cancelled — it keeps running in the background and delivers its own result — and the result
    hands back the id it is reached by, so the parent answers the member instead of waiting. That is
    what `bash` does with a command still running at its foreground budget."""
    try:
        result = await ctx.spawn(
            args.target,
            args.payload,
            args.background,
            dedup_key=ctx.idempotency_key,
            delivers_result=args.background,
            name=args.name,
            detach_on_arrival=True,
        )
    except (AmbiguousSpawnTarget, UnknownSpawnTarget) as error:
        return ToolResult(content=(TextContent(text=str(error)),), is_error=True)
    if result.terminal is not None and result.terminal.question is not None:
        return ToolResult(
            content=(
                TextContent(
                    text=json.dumps(
                        {
                            "spawn_id": str(result.turn_id),
                            "status": "question",
                            "question": result.terminal.question.model_dump(exclude_none=True),
                        }
                    )
                ),
            ),
            untrusted=result.untrusted,
        )
    if result.output is None:
        return ToolResult(
            content=(
                TextContent(
                    text=_spawn_handles(args.target, result.turn_id, result.detached_on_arrival)
                ),
            )
        )
    return ToolResult(
        content=(TextContent(text=result.output.model_dump_json()),), untrusted=result.untrusted
    )


ASK_USER_DIRECTIVE = (
    "Ask these in your reply, then end your turn — the user's answer arrives as the next message."
)


async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult:
    """Chat-native interaction: the question rides the agent's reply and the answer rides the
    member's next message, never an out-of-band prompt. Returns the structured question so a rich
    surface can render it and the model presents it faithfully, plus the directive to end the turn
    and wait."""
    payload = {
        "awaiting": "question",
        "title": args.title,
        **({"icon": args.icon} if args.icon else {}),
        "questions": [question.model_dump(exclude_none=True) for question in args.questions],
    }
    return ToolResult(content=(TextContent(text=f"{ASK_USER_DIRECTIVE}\n{json.dumps(payload)}"),))


async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult:
    """Resolve the named skill and the full chain of what it `depends` on over routing cards,
    materialize each one's files — a deploy skill from the registry, a member skill read from its
    stored row — resolve deploy files from the verified local bundle and install member files under
    `$UFO_HOME/skills/<name>/`, and return each one's `SKILL.md` workflow — the asked-for skill
    first, so its workflow leads — closing with one tree of everything loaded. A workflow
    the context already holds is named in one note instead of injected again, while its files still
    resolve, so re-loading is cheap rather than an error. An unknown name fails loud
    as a recoverable tool error."""
    loaded = await ctx.skills.materialize(ctx.skills.closure(args.name))
    await load_skills(ctx.sandbox, loaded)
    text = loaded_context(loaded, ctx.loaded_skills.in_context)
    return ToolResult(content=(TextContent(text=text),))


SKILL_SEARCH_NO_MATCH = "No matches among {total} loadable skills."


async def skill_search_handler(ctx: ToolContext, args: SkillSearchInput) -> ToolResult:
    """Rank every loadable skill's routing card — deploy and member alike — by the lexical scorer
    the member block uses, and return the matching `name: description` lines, never a body. Zero
    matches answers with the searchable total, so the caller knows the corpus was searched rather
    than empty."""
    cards = ctx.skills.all_cards()
    ranked = sorted(
        ((lexical_score(args.query, card), card) for card in cards),
        key=lambda scored: scored[0],
        reverse=True,
    )
    matched = [card for score, card in ranked if score > 0][: args.limit]
    if not matched:
        return ToolResult(
            content=(TextContent(text=SKILL_SEARCH_NO_MATCH.format(total=len(cards))),)
        )
    lines = "\n".join(f"{card.name}: {card.description}"[:SKILL_LINE_MAX_CHARS] for card in matched)
    return ToolResult(content=(TextContent(text=lines),))


CONNECT_ACCOUNT_DIRECTIVE = (
    "Tell the member to use the private connection control in your reply, then end your turn — "
    "the authorization URL never appears in this conversation."
)


async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None:
    """The agent a connection is granted to when the asking agent names another, or None when it
    grants to itself. A grant is a change to one agent's authority, so the same rule the object
    verbs use governs it: only the workspace main agent may name another agent. The name is
    resolved here rather than at the handoff, so the durable request already carries the agent the
    seal will bind and no later step re-decides it."""
    if not name:
        return None
    async with workspace_tx() as connection:
        rows = {
            row.name: row
            for row in await connection.execute(
                sa.select(tables.agent.c.id, tables.agent.c.name, tables.agent.c.is_main).where(
                    tables.agent.c.workspace_id == ctx.turn.workspace_id,
                    tables.agent.c.archived_at.is_(None),
                )
            )
        }
    asking = next((row for row in rows.values() if row.id == ctx.turn.agent_id), None)
    if asking is None or not asking.is_main:
        raise ValueError("only the workspace main agent may connect an account for another agent")
    target = rows.get(name)
    if target is None:
        raise ValueError(f"no agent named {name!r}")
    return None if target.id == ctx.turn.agent_id else target.id


async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult:
    """Leave a provider-validated private OAuth handoff for the speaking member."""
    if ctx.speaker_member_id is None:
        raise ValueError("connect requires a speaking member to gate the grant")
    grantee = await _grantee_agent_id(ctx, args.agent.strip())
    await installed_connect_flow().validate_provider(args.provider)
    request = ConnectRequest(
        provider=args.provider,
        requester_member_id=ctx.speaker_member_id,
        shared=args.shared,
        grantee_agent_id=grantee,
    )
    return ToolResult(
        content=(TextContent(text=f"{CONNECT_ACCOUNT_DIRECTIVE}\n{request.model_dump_json()}"),)
    )


REQUEST_CREDENTIALS_DIRECTIVE = (
    "Tell the member what you need in your reply, then end your turn — a private prompt collects "
    "each value, and the entered secrets never appear in this conversation. Name no place to enter "
    "them: the surface the member is on supplies its own, and only it knows which."
)


async def request_credentials_handler(
    ctx: ToolContext, args: RequestCredentialsInput
) -> ToolResult:
    """Collect BYOK secrets from a speaking admin without them touching the transcript: seal
    which slots this member will fill (the same Fernet that guards the slots signs the grant), and
    return the structured request so a capable surface prompts for the values privately and
    fulfills against the seal. Ends the turn like ask_user — the member returns once entered. Slots
    are workspace-global, so only an admin may fill them; a non-admin speaker, an undeclared slot,
    or a deploy without a credential key raises, surfacing as a recoverable tool error."""
    if ctx.speaker_member_id is None:
        raise ValueError("collecting credentials requires a speaking member")
    if ctx.requestable_credentials is None:
        raise ValueError("no credential key is configured — this deploy cannot store secrets")
    if not await ctx.speaker_is_admin():
        raise ValueError("only a workspace admin can fill credential slots")
    sealed = ctx.requestable_credentials.seal(
        ctx.turn.workspace_id,
        ctx.speaker_member_id,
        tuple(prompt.slot for prompt in args.prompts),
    )
    request = CredentialRequest(reason=args.reason, prompts=args.prompts, sealed=sealed)
    return ToolResult(
        content=(TextContent(text=f"{REQUEST_CREDENTIALS_DIRECTIVE}\n{request.model_dump_json()}"),)
    )


async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult:
    """Cancel a running spawn and report its current status; a spawn that already finished is
    a no-op whose committed terminal stands. Refuses a turn id this turn did not spawn."""
    if ctx.subagents is None:
        raise RuntimeError("spawn control is not available in this context")
    status = await ctx.subagents.cancel(UUID(args.spawn_id))
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps({"spawn_id": str(status.turn_id), "status": status.status})
            ),
        )
    )


async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult:
    """Send a running or asking spawn a follow-up message; it runs as the spawn's next turn
    against its accumulated context once the turn in flight ends, and the follow-up delivers its
    result to this conversation when it finishes — which is how a bubbled question's answer comes
    back. Refuses a turn id this turn did not spawn."""
    if ctx.subagents is None:
        raise RuntimeError("spawn control is not available in this context")
    if ctx.idempotency_key is None:
        raise RuntimeError("message_spawn dispatched without its idempotency key")
    status = await ctx.subagents.message(
        UUID(args.spawn_id), args.message, dedup_key=ctx.idempotency_key, delivers_result=True
    )
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps({"spawn_id": str(status.turn_id), "status": status.status})
            ),
        )
    )


BUILTIN_TOOLS: tuple[ToolDef, ...] = (
    ADD_MEMBER_TOOL_DEF,
    RESTORE_APPLICATION_TOOL_DEF,
    ToolDef(
        name="bash",
        description=(
            "Execute shell commands in the secure sandboxed workspace container. Pre-installed: "
            "Python 3, Node.js, ripgrep, poppler, tesseract, libreoffice, pandoc, chromium, and "
            "standard Unix tools. Working directory: /workspace. Use absolute paths. Do NOT use "
            "for file reads/edits/searches — use the dedicated read/edit/glob/grep tools instead. "
            "A command still running at its timeout keeps running in the background rather than "
            "being stopped; set background to detach it from the start. Either way the result "
            "names the task's log and the exit file whose appearance is the completion signal."
        ),
        input_model=BashInput,
        handler=bash_handler,
        side_effecting=True,
        parallel_safe=True,
    ),
    ToolDef(
        name="read",
        description=(
            "Reads a file by absolute path. Returns up to 2000 lines by default; use "
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
            "Create a file in workspace storage at a given path. Read the file first if it already "
            "exists: overwriting a path this turn has not read is REFUSED. Does NOT send to user — "
            "call share_file afterward to share it. Use for creating new files; use edit for "
            "modifying existing ones."
        ),
        input_model=WriteInput,
        handler=write_handler,
    ),
    ToolDef(
        name="edit",
        description=(
            "Performs exact string replacements in files. Read the file first: an edit to a path "
            "this turn has not read is REFUSED. An edit FAILS if old_string is not "
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
            "Search for a regex pattern in file contents below a directory. Use instead of bash "
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
            "Send files to the user as downloadable links, delivered in list order. The ONLY way "
            "to make a produced file visible outside the sandbox — the user CANNOT see a "
            "workspace file until this is called. Every file must be under the /workspace "
            "directory. Any file type works (reports, code, csv, json, images, PDFs, archives) "
            "up to 5 GiB each; each is streamed out, never read whole into memory. A directory "
            "path is packed automatically and delivered as a .tar.gz archive of that directory — "
            "pass the directory itself rather than tarring it first. Per file, "
            "`name` sets the download name — include the extension (e.g. 'report.xlsx') so the "
            "recipient gets an openable file; any directory components in it are stripped — and "
            "`subject` is an optional caption shown when a chat surface posts the file. Supports "
            "version history: use the same `name` for updated versions. Files shared in other "
            "sessions can be fetched into the workspace as `artifact` objects with object_get."
        ),
        input_model=ShareFileInput,
        handler=share_file_handler,
    ),
    ToolDef(
        name="spawn",
        description=(
            "Delegate a subtask to a named target — a subagent profile or a workspace agent. "
            "`payload` must match the target's input schema; foreground (default) returns the "
            "target's validated JSON output, background returns the child turn id at once. A "
            "foreground spawn still running when a message arrives on this conversation is not "
            "cancelled: it keeps running in the background, the result names the spawn id, and its "
            "output arrives on this conversation as a message when it finishes. A "
            "spawn that ends asking returns its structured question with status 'question' — "
            "answer it yourself with message_spawn, or ask the member with ask_user and relay "
            "their answer."
        ),
        input_model=SpawnInput,
        handler=spawn_handler,
        side_effecting=True,
        parallel_safe=True,
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
        input_model=AskUserCall,
        handler=ask_user_handler,
    ),
    ToolDef(
        name="load_skill",
        description=(
            "Load a skill — a bundle of workflow instructions and files — so you can follow it. "
            "The skill and anything it depends on are loaded under `$UFO_HOME/skills`, and each "
            "one's instructions come back with a tree of those files, so any path the workflow "
            "cites is ready to read. Load a skill proactively whenever its subject is "
            "relevant to the task. Cheap operation — be aggressive about loading."
        ),
        input_model=LoadSkillInput,
        handler=load_skill_handler,
        parallel_safe=True,
    ),
    ToolDef(
        name="skill_search",
        description=(
            "Search every loadable skill by keyword and get back matching `name: description` "
            "lines to pass to load_skill. Use it when the task might have a skill the visible "
            "indexes do not show."
        ),
        input_model=SkillSearchInput,
        handler=skill_search_handler,
        parallel_safe=True,
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
            "secrets) without the values passing through this conversation — a private prompt "
            "collects each one. Use it when a capability needs a secret a member "
            "must supply; never ask for a secret in chat prose. Only a workspace admin can "
            "fill slots. After calling it, explain what you need in your reply and end your "
            "turn; verify the slots once the member says they have entered them."
        ),
        input_model=RequestCredentialsInput,
        handler=request_credentials_handler,
    ),
    ToolDef(
        name="cancel_spawn",
        description=(
            "Cancel a running spawn. Sets its status to 'cancelled'. If the spawn has "
            "already finished, this is a no-op and returns its current status."
        ),
        input_model=CancelSpawnInput,
        handler=cancel_spawn_handler,
        parallel_safe=True,
    ),
    ToolDef(
        name="message_spawn",
        description=(
            "Send a follow-up message to a spawn — a background run, or one that ended asking a "
            "question. It runs as the spawn's next turn against its accumulated context once its "
            "current turn ends, and delivers its result to this conversation when it finishes."
        ),
        input_model=MessageSpawnInput,
        handler=message_spawn_handler,
        side_effecting=True,
        parallel_safe=True,
    ),
)

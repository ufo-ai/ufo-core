"""The builtin tool set: bash, read, write, edit, glob, grep, share_file, spawn,
ask_user, request_credentials, load_skill, connect_account,
cancel_spawn, message_spawn.

Each file/shell handler reaches the workspace only through `ctx.sandbox`, so the carrier's scoping
and egress rules apply whether a byte arrives via a shell command or a file op. `read`, `edit`, and
`write` run the in-sandbox `sbxfs` CLI, so windowing, ripgrep, and PDF/image render happen in the
container and only a bounded JSON result crosses back — the host never pulls a whole file over to
loop on it. `read` records every path it returns so `edit`/`write` can refuse to touch a file the
turn has not read — the guard that keeps a blind string-replace from clobbering content the model
never saw. `glob` and `grep` run the in-sandbox `sbxfs` matcher and ripgrep, so file discovery and
content search happen in the container and a bounded result crosses back. `share_file` lands a
produced workspace file in the blob store under `artifacts/<uuid>/` — on S3 the sandbox uploads it
itself to a presigned PUT bound to the size and sha256 a preflight measured — and returns a
TTL-token URL core's artifact route serves: the only path that hands a file back outside the
sandbox, with no read cap and no whole-file buffer.
`spawn` delegates a typed subtask to a child turn through `ctx.spawn` — a subagent profile or a
workspace agent, one verb over both. `ask_user` is
chat-native: it
structures a question or confirmation the agent poses in its reply, whose answer rides the member's
next message — no out-of-band prompt. `request_credentials` is its secret-collecting sibling: it
seals which slots the speaking owner will fill and ends the turn; a capable surface prompts for the
values privately and fulfillment lands them in the encrypted store, never the transcript.
`load_skill` mounts a skill's `SKILL.md` and assets — and those of the whole chain it `depends` on —
into the workspace, and returns each one's workflow followed by one tree of everything mounted; the
system prompt's `<available_skills>` block is its complete per-turn index.
`cancel_spawn` and `message_spawn` reach `ctx.subagents`, the same
Subagents workflow that backs `spawn`, to cancel a running child or queue it a follow-up message
that runs as its next turn — scoped to the children this turn
spawned."""

import json
import mimetypes
import shlex
from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import AfterValidator, BaseModel, Field

from ufo.artifact_url import (
    ARTIFACT_KEY_PREFIX,
    ARTIFACT_URL_TTL_SECONDS,
    artifact_media_type,
    mint_artifact_url,
)
from ufo.artifacts import artifact_object_names
from ufo.blob import FilesystemBlobStore, S3BlobStore
from ufo.db import workspace_tx
from ufo.grants import installed_connect_flow
from ufo.image_previews import IMAGE_PREVIEW_MAX_BYTES
from ufo.members import ADD_MEMBER_TOOL_DEF
from ufo.o11y import log
from ufo.sandbox.session import TOOL_OUTPUT_DIR, WORKSPACE_DIR, workspace_path
from ufo.schema import tables
from ufo.schema.records import AskUserInput, ConnectRequest, CredentialPrompt, CredentialRequest
from ufo.skills.runtime import loaded_context, mount_skill
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
    TASK_BASH,
    TASK_DETACH,
    TASK_LAUNCH,
    TASK_WRAPPER,
    run_task,
    task_base,
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
ARTIFACT_PREVIEW_SUFFIXES = frozenset((".docx", ".pdf", ".pptx", ".svg", ".xlsx"))
ARTIFACT_PREVIEW_MEDIA_TYPE = "image/png"
ARTIFACT_PREVIEW_DPI = 100
ARTIFACT_PREVIEW_TIMEOUT_SECONDS = 180
ARTIFACT_PREVIEW_DETAIL_CHARS = 500
# One page, one raster: `soffice` reaches PDF from the Office formats and SVG (its import is
# lenient — an `xmlns`-less generated file, dead in a browser's `<img>`, still draws) and
# `pdftoppm` reaches a picture from the PDF, so a `.pdf` skips the first step and every type
# shares the second.
# `-singlefile` fixes the output at `<stem>.png` — a page-numbered name would have to be guessed
# back. The convert writes into the engine's own offload dir, never beside the member's file.
ARTIFACT_PREVIEW_PROG = """
set -e
source={source}
stem={stem}
if [ "${{source##*.}}" = "pdf" ]; then
  pdf="$source"
else
  soffice --headless --convert-to pdf --outdir "$(dirname "$stem")" "$source" >/dev/null
  pdf="$(dirname "$stem")/$(basename "${{source%.*}}").pdf"
fi
test -f "$pdf"
pdftoppm -png -r {dpi} -f 1 -l 1 -singlefile "$pdf" "$stem"
test -f "$stem.png"
"""

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
machine, where no baked `sbxfs` or usable `python3` exists. The size and digest bind the S3
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
        "pid instead of waiting for it. Its output streams to .tasks/<id>.log, its pid sits in "
        ".tasks/<id>.pid, and its exit code lands in .tasks/<id>.exit when it finishes — list "
        "every task with ls /workspace/.tasks. The command's network egress ends with this turn, "
        "and a sandbox that suspends between turns advances it only while awake — use background "
        "for compute that needs no network past this turn: builds, test runs, data processing.",
    )
    user_description: str = Field(
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
    user_description: str = Field(
        description="Which document you are opening, in plain language for the activity timeline. "
        "Name the document, never the path."
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
    user_description: str = Field(
        description="What you are creating, in plain language for the activity timeline. Name the "
        "document, never the path."
    )


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
    user_description: str = Field(
        description="What you are changing and where, in plain language for the activity timeline. "
        "Name the document, never the path."
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
    user_description: str = Field(
        description="What kind of files you are looking for, in plain language for the activity "
        "timeline."
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
    user_description: str = Field(
        description="What you are searching the files for, in plain language for the activity "
        "timeline. Never include the raw pattern."
    )


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
    user_description: str = Field(
        description="What you are sending them, in plain language for the activity timeline."
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
        "waiting for its validated output.",
    )
    user_description: str = Field(
        description="What you are handing off, in plain language for the activity timeline — the "
        "work itself, never the target name."
    )
    name: str = Field(
        default="",
        description="A short display name for this run, at most four words, e.g. 'UK sports news'.",
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
    agent: str = Field(
        default="",
        description="Grant the connection to this agent instead of yourself, by name. Only the "
        "workspace main agent may name another agent. Use it when a member asks you to finish "
        "setting up an agent that cannot ask for itself.",
    )
    user_description: str = Field(
        description="Which account you are connecting them to, in plain language for the activity "
        "timeline."
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
    user_description: str = Field(
        description="What you need from them, in plain language for the activity timeline. Name "
        "the service, never the secret."
    )


class AskUserCall(AskUserInput):
    """The `ask_user` call: the question record the terminal frame carries, plus the activity
    narration that never reaches the frame."""

    user_description: str = Field(
        description="What you are checking with them, in plain language for the activity timeline."
    )


class CancelSpawnInput(BaseModel):
    spawn_id: str = Field(description="The spawn ID to cancel.")
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
    )


class MessageSpawnInput(BaseModel):
    spawn_id: str = Field(description="The spawn ID to message.")
    message: str = Field(
        description="The follow-up message to deliver, run as the spawn's next turn."
    )
    user_description: str = Field(
        description="Brief plain-language description shown in the activity timeline."
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
        handles = task_handles(run.task_id, run.pid, applied_s=applied_s)
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
    started = await ctx.sandbox.sh(
        TASK_BASH, TASK_LAUNCH + TASK_DETACH, TASK_WRAPPER, task_base(task), command
    )
    if started.exit_code != 0 or not started.stdout.strip():
        return ToolResult(
            content=(TextContent(text=started.stderr or "the command did not detach"),),
            is_error=True,
        )
    return ToolResult(content=(TextContent(text=task_handles(task, started.stdout.strip())),))


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
    data = args.content.encode()
    await ctx.sandbox.ensure_tool_output_dir()
    staged = f"{TOOL_OUTPUT_DIR}/{uuid4().hex}.stage"
    await ctx.sandbox.write_file(staged, data)
    result = await ctx.sandbox.run_sbxfs(
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
    result = await ctx.sandbox.run_sbxfs("edit", {"path": args.file_path, "edits": edits})
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
                f"curl -sS --fail-with-body -T {shlex.quote(scoped)} "
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
    """Rasterize the first page of a shared document so a member sees the file rather than its name.

    The renderers are the sandbox's own — `soffice` for the Office formats and `pdftoppm` for the
    PDF they convert to, the same pair the `read` builtin shows the agent — so nothing new is
    installed beside the engine and the picture a member gets is the picture the agent saw. This
    runs while the sandbox is still up and the file is still local, which is the only moment both
    are true: the artifact outlives its conversation, and a later render would have to rebuild a
    container to reach a renderer.

    A render that fails, times out, or comes back oversize yields no preview and never the share:
    the member's file is already stored, and a missing picture is not a reason to lose it. The
    reason is logged, so a format that never renders is visible rather than merely absent."""
    if PurePosixPath(safe_name).suffix.lower() not in ARTIFACT_PREVIEW_SUFFIXES:
        return None
    await ctx.sandbox.ensure_tool_output_dir()
    stem = f"{TOOL_OUTPUT_DIR}/preview-{uuid4().hex}"
    render = await ctx.sandbox.bash(
        ARTIFACT_PREVIEW_PROG.format(
            source=shlex.quote(scoped), stem=shlex.quote(stem), dpi=ARTIFACT_PREVIEW_DPI
        ),
        timeout_s=ARTIFACT_PREVIEW_TIMEOUT_SECONDS,
    )
    rendered = f"{stem}.png"
    if render.exit_code != 0:
        log(
            "share_file.preview.refused",
            filename=safe_name,
            detail=(render.stderr.strip() or render.stdout.strip())[:ARTIFACT_PREVIEW_DETAIL_CHARS],
        )
        return None
    preflight = await ctx.sandbox.bash(
        SHARE_PREFLIGHT_CMD.format(path=shlex.quote(rendered)),
        timeout_s=SHARE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if preflight.exit_code != 0:
        log("share_file.preview.unmeasured", filename=safe_name)
        return None
    stat = json.loads(preflight.stdout)
    size_bytes = int(stat["size"])
    if size_bytes == 0 or size_bytes > IMAGE_PREVIEW_MAX_BYTES:
        log("share_file.preview.oversize", filename=safe_name, size_bytes=size_bytes)
        return None
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{PurePosixPath(safe_name).stem}.png"
    await _store_artifact(ctx, rendered, key, size_bytes, str(stat["digest"]))
    return ArtifactPreview(
        blob_key=key, media_type=ARTIFACT_PREVIEW_MEDIA_TYPE, size_bytes=size_bytes
    )


async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult:
    """Land a produced workspace file in the artifact store under `artifacts/<uuid>/<name>`, record
    it as a shared_artifact of this turn, and mint a TTL download token core's artifact route
    serves — the only path a produced file leaves the sandbox. A preflight in the container confines
    the path through the containment guard and streams the file off the fd that descent pinned to
    derive its size and sha256 without loading it whole, so a link the agent planted at the name is
    refused rather than copied out, and the upload is then bound to those two measurements, so
    nothing crosses on the sandbox's word and no whole-file buffer ever forms in this process. The
    shared_artifact record is what an async surface (Slack) reads to upload the file into the turn's
    posted reply; `subject` is an optional caption — absent, the file renders under its plain
    name."""
    if not ctx.artifact_token_secret:
        raise RuntimeError("artifact sharing is not configured (no artifact token secret set)")
    scoped = workspace_path(args.file_path)
    preflight = await ctx.sandbox.bash(
        SHARE_PREFLIGHT_CMD.format(path=shlex.quote(scoped)),
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
    await _store_artifact(ctx, scoped, key, int(stat["size"]), str(stat["digest"]))
    media_type = artifact_media_type(safe_name)
    preview = await _shared_preview(ctx, scoped, safe_name)
    shared_at = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                id=uuid4(),
                turn_id=ctx.turn.id,
                blob_key=key,
                workspace_id=ctx.turn.workspace_id,
                filename=safe_name,
                subject=args.subject,
                media_type=media_type,
                size_bytes=stat["size"],
                preview_blob_key=None if preview is None else preview.blob_key,
                preview_media_type=None if preview is None else preview.media_type,
                preview_size_bytes=None if preview is None else preview.size_bytes,
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
    expires_at = int(datetime.now(UTC).timestamp()) + ARTIFACT_URL_TTL_SECONDS
    url = mint_artifact_url(
        ctx.artifact_token_secret, key, expires_at, workspace_id=ctx.turn.workspace_id
    )
    return ToolResult(
        content=(
            TextContent(
                text=json.dumps(
                    {
                        "url": url,
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


async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult:
    try:
        result = await ctx.spawn(
            args.target,
            args.payload,
            args.background,
            dedup_key=ctx.idempotency_key,
            delivers_result=args.background,
            name=args.name,
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
            content=(TextContent(text=f"spawned {args.target} (turn {result.turn_id})"),)
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
                    tables.agent.c.workspace_id == ctx.turn.workspace_id
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
            "called. The file must be under the /workspace directory. Any file type works "
            "(reports, code, csv, json, images, PDFs, archives) up to 5 GiB; it is streamed out, "
            "never read whole into memory. `name` sets the download name — include the extension "
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
        name="spawn",
        description=(
            "Delegate a subtask to a named target — a subagent profile or a workspace agent. "
            "`payload` must match the target's input schema; foreground (default) returns the "
            "target's validated JSON output, background returns the child turn id at once. A "
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
            "The skill and anything it depends on are mounted under the workspace, and each one's "
            "instructions come back with a tree of the files it mounted, so any path the workflow "
            "cites is already there to read. Load a skill proactively whenever its subject is "
            "relevant to the task. Cheap operation — be aggressive about loading."
        ),
        input_model=LoadSkillInput,
        handler=load_skill_handler,
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

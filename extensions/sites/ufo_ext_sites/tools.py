"""The website tools: build a site, serve one in the sandbox with port cleanup and a readiness
probe, and host the served port at a permanent link.

Every path argument these tools take — a project directory, a dist directory, a log file — goes
through `workspace_path` before it reaches a command, so a model-named path is scoped to the
workspace rather than merely quoted into `cd`/`>` (under the local carrier those are host paths in a
host subprocess). The log files default under the run's `tool-output` directory rather than the
workspace root: a server log is scaffolding, and the workspace listing is the
member's own file list. A predictable path in a directory the agent can write is still where a
planted link would sit, so each log's name is emptied through the containment guard and the `>`
redirect runs under `set -C`, which creates it `O_CREAT|O_EXCL` rather than truncating through a
link.

Each tool runs through `ctx.sandbox`, so the container's mount and egress scoping hold. `website`
runs a build command and lists what it produced. `start_server`, `deploy_website`, and
`publish_website` bring a server up in the background: they free the port and the task journal of
the start before them, launch an owned detached task, and poll until the port is listening before
returning — so the tool returns a running, reachable server rather than a race. The served URL is
`http://localhost:<port>` inside the sandbox, which the browser tools and js_repl reach to validate
the page.

`deploy_website` and `publish_website` then register that port as a hosted site, returning its
`site_url` — the frame a member opens, gated on the site's visibility — and ask the preview service
to photograph that address into the artifact store. The picture is what the artifacts view draws
the site's card with. It is taken after the registration because registering is what retires the
site this deploy displaced from the port, and a render is long enough for a turn to end inside. A
render that fails leaves the site hosted with the picture it already had. Hosting a site is
registering the port the readiness probe just proved, so nothing moves: a re-deploy of the same
name updates the port in place and the link never changes. Visibility defaults from the
conversation's audience; an explicit argument
overrides that default, but only for the site's creator and only on a turn with a live speaker,
because choosing who can open a site is a disclosure act. `start_server` registers nothing, since a
scratch server is not a deliverable.

`deploy_website` also promotes the served directory into the workspace blob store and writes the
manifest onto the row — the site's source of record, which the ingress serves with no sandbox dial
and the site kind's `object_get` materializes back into any conversation for an edit.
`publish_website` stores
no source: its app answers from its own server, so its row states sandbox serving. A deploy naming
the acting agent's bound homepage from another conversation updates that row in place — same link,
same origin, new source and generation — rather than founding a second site beside it.

`set_homepage` binds one hosted site as the homepage of the agent it is dispatched on — its own
by default, another agent's only for that agent's owner or an admin — the pointer the portal reads.
The frame gates a homepage's viewers on the agent's visibility rather than the site's, so who may
open it is decided where the agent's audience is decided — the agent object's `visibility` — and
the bind itself is the re-gating act: it takes the site's creator acting, and a live speaker
unless the same turn deployed the site, the seed's deploy-and-bind shape."""

import json
import shlex
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.sdk.authority import authority_member_id
from ufo.sdk.context import ExtensionContext
from ufo.sdk.objects import AGENT_KIND
from ufo.sdk.sandbox import (
    WORKSPACE_DIR,
    ExecResult,
    SandboxProviderUnavailable,
    serve_port,
    shell_path,
    workspace_path,
)
from ufo.sdk.terminal import TerminalAbsent
from ufo.sdk.tools import (
    AppliedEffect,
    CommandDiagnostics,
    ObjectBinding,
    SpeakerRequired,
    TextContent,
    ToolContext,
    ToolDef,
    ToolFailure,
    ToolResult,
    clipped,
)
from ufo_ext_sites.application_audit import (
    APPLICATION_DESIGN_MAX_CHARS,
    APPLICATION_DESIGN_WIDTH,
    APPLICATION_SOURCE_MAX_CHARS,
    MAX_MESSAGE_CHARS,
    ApplicationAuditIssue,
    ApplicationAuditReport,
    ApplicationAuditVerdict,
    ApplicationDesign,
    AuditIssueCode,
    audit_application,
    validate_application_design,
    validate_application_source,
)
from ufo_ext_sites.application_homepage import (
    APPLICATION_AUDIT_SCRIPT_PATH,
    PAGE_REFUSAL_OPENING,
)
from ufo_ext_sites.objects import SITE_KIND, effective_visibility, site_object_name
from ufo_ext_sites.share_card import draw_from_page
from ufo_ext_sites.source import (
    PROJECT_CONFIG,
    PROJECT_CONFIG_BYTES,
    PROJECT_DESIGN,
    PROJECT_DIST,
    PROJECT_FILE_ABSENT,
    PROJECT_FILE_READ,
    PROJECT_PREVIEW,
    PROJECT_PREVIEW_BYTES,
    PROJECT_SOURCE,
    SOURCE_PUT_TTL_SECONDS,
    UPLOAD_SCRIPT,
    transfer,
    unpack_page_kit,
)
from ufo_ext_sites.store import (
    HostedSite,
    HostedSites,
    SiteFile,
    SourceManifest,
    Visibility,
    site_name,
)
from ufo_ext_sites.surface import site_url

START_SERVER_TOOL = "start_server"
DEPLOY_WEBSITE_TOOL = "deploy_website"
PUBLISH_WEBSITE_TOOL = "publish_website"
SET_HOMEPAGE_TOOL = "set_homepage"

START_SERVER_PORT = 5000
APPLICATION_AUDIT_TIMEOUT_SECONDS = 120
APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS = 15
APPLICATION_AUDIT_REPORT_MAX_BYTES = 1024 * 1024
APPLICATION_LIFECYCLE_DIAGNOSTIC_MAX_BYTES = 4096
APPLICATION_LIFECYCLE_EXIT = 3
APPLICATION_DESIGN_FAULT_EXIT = 4
APPLICATION_LIFECYCLE_SUFFIX = ".lifecycle.json"
APPLICATION_LIFECYCLE_UNREAD = (
    "the page did not become ready, and the audit's own record of why could not be read"
)
"""What a lifecycle refusal says when its diagnostic is gone. An issue message cannot be empty, so
an unread diagnostic would raise a validation error in place of the refusal the exit code already
earned, and the builder would lose the verdict to an internal fault."""
APPLICATION_AUDIT_SCRIPT = APPLICATION_AUDIT_SCRIPT_PATH.read_bytes()
"""One file, two callers: the deploy writes it into the sandbox and runs it on the built page,
and the agent runs it under `$UFO_HOME` on a design it just drew. A second copy would be two
design contracts."""
APPLICATION_AUDIT_REPORT_READ = """from pathlib import Path
import sys
path = Path(sys.argv[1])
data = path.read_bytes()
if len(data) > int(sys.argv[2]):
    raise SystemExit("application audit report is too large")
sys.stdout.buffer.write(data)"""


class _ApplicationLifecycleBlocking(BaseModel):
    startup: int = Field(ge=0)
    observation: int = Field(ge=0)
    unary: int = Field(ge=0)
    stream: int = Field(ge=0)
    timeout: int = Field(ge=0)
    interval: int = Field(ge=0)


class _ApplicationLifecycleSnapshot(BaseModel):
    version: Literal[1]
    generation: int = Field(ge=1)
    epoch: int = Field(ge=0)
    mounted: bool
    state: Literal["booting", "active", "idle", "unmounted"]
    revision: int = Field(ge=0)
    blocking_work: int = Field(alias="blockingWork", ge=0)
    blocking: _ApplicationLifecycleBlocking


class _ApplicationLifecycleDiagnostic(BaseModel):
    code: Literal["application_lifecycle"]
    reason: str = Field(min_length=1, max_length=400)
    snapshot: _ApplicationLifecycleSnapshot | None
    problems: tuple[str, ...] = ()


def _lifecycle_state(snapshot: _ApplicationLifecycleSnapshot) -> str:
    """The three facts the readiness check reads, so a repair aims at the work that never drained
    rather than at the page. In one recorded run this check produced eight of twelve refusals, and
    the builder answered each by drawing and building the whole page again."""

    if not snapshot.mounted:
        return "the page never mounted"
    blocking = tuple(
        f"{kind} {count}" for kind, count in snapshot.blocking.model_dump().items() if count
    )
    if not blocking and snapshot.state == "idle":
        return "the page kept re-rendering"
    named = [] if snapshot.state == "idle" else [f"state {snapshot.state}"]
    if blocking:
        named.append(f"{snapshot.blocking_work} blocking ({', '.join(blocking)})")
    return ", ".join(named)


def _lifecycle_joined(reason: str, named: tuple[str, ...], dropped: bool) -> str:
    parts = (*named, "…") if dropped else named
    return f"{reason}: {'; '.join(parts)}" if parts else reason


def _lifecycle_message(reason: str, named: tuple[str, ...]) -> str:
    """The lifecycle refusal, held to what an audit issue's message can carry.

    The audit hands up to four console lines of 500 characters each, so the joined reason reaches
    about five times the field's cap and the issue raises a validation error in place of the
    refusal the audit had already written. Lines are kept whole, in the order the audit found them,
    because a line cut in half names no fault. The first line that does not fit whole is the one
    the builder was about to read, so its head takes the room left rather than nothing — a refusal
    that names only the state is what this check exists to end. The ellipsis says the rest are in
    the lifecycle diagnostic beside the report."""

    kept: tuple[str, ...] = ()
    remaining = named
    while remaining:
        composed = _lifecycle_joined(reason, (*kept, remaining[0]), len(remaining) > 1)
        if len(composed) > MAX_MESSAGE_CHARS:
            break
        kept, remaining = (*kept, remaining[0]), remaining[1:]
    if not remaining:
        return _lifecycle_joined(reason, kept, False)
    room = MAX_MESSAGE_CHARS - len(_lifecycle_joined(reason, (*kept, ""), True))
    head = remaining[0][:room] if room > 0 else ""
    return _lifecycle_joined(reason, (*kept, head) if head else kept, True)


PORT_STOP_PROG = """import os
from pathlib import Path
import signal
import subprocess
import sys
import time

port = int(sys.argv[1])
inodes = set()
tables = tuple(
    table for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")) if table.exists()
)
for table in tables:
    for line in table.read_text().splitlines()[1:]:
        fields = line.split()
        if int(fields[1].rsplit(":", 1)[1], 16) == port and fields[3] == "0A":
            inodes.add(fields[9])
pids = set()
if tables:
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                target = os.readlink(descriptor)
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    pids.add(int(process.name))
                    break
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
else:
    try:
        listed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        listed = None
    if listed is not None:
        pids.update(int(line) for line in listed.stdout.splitlines() if line.isdigit())
for pid in pids:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
deadline = time.monotonic() + 2
while pids and time.monotonic() < deadline:
    alive = set()
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        alive.add(pid)
    pids = alive
    time.sleep(0.05)
for pid in pids:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass"""
SERVER_TASK_STOP = """pid="$1"
base="$2"
recorded=$(cat "$base.pid") || exit 1
[ "$recorded" = "$pid" ] || exit 1
if [ -e "$base.exit" ]; then exit 0; fi
kill "$pid" 2>/dev/null || [ -e "$base.exit" ]
"""
SERVER_TASK_RESET = """base="$1"
grace="$2"
pid=$(cat "$base.pid" 2>/dev/null) || pid=
if [ -n "$pid" ] && [ ! -e "$base.exit" ]; then
  kill "$pid" 2>/dev/null || true
  waited=0
  while [ ! -e "$base.exit" ] && kill -0 "$pid" 2>/dev/null; do
    [ "$waited" -lt "$grace" ] || exit 1
    sleep 1
    waited=$((waited + 1))
  done
fi
rm -f "$base.pid" "$base.log" "$base.exit" "$base.lock"
"""


READINESS_TIMEOUT_SECONDS = 30
BUILD_TIMEOUT_SECONDS = 600
LOG_TAIL_LINES = 20
LOG_TAIL_TIMEOUT_SECONDS = 15
"""Reading the tail of one log inside the sandbox, on the failure path of a start that already ended
— so the wait is short and stated rather than the 120s default."""
SERVER_NEVER_LISTENED = (
    "nothing listened on port {port} within {seconds}s and {log} holds nothing: this call serves "
    "that port alone, so the command must bind $PORT, which the sandbox sets to {port}"
)
PROBE_OUTLIVED_ITS_DEADLINE = (
    "the server never answered on port {port}: the sandbox stopped the readiness check after "
    "{seconds}s and {log} holds nothing"
)
PREVIEW_ERROR_MAX_CHARS = 500
SERVER_START_SAID_NOTHING = "the start of {command!r} failed with no output and {log} holds nothing"
TOOL_OUTPUT_DIR = "tool-output"
SERVER_LOG = f"{TOOL_OUTPUT_DIR}/server-{{port}}.log"
DEPLOY_LOG = f"{TOOL_OUTPUT_DIR}/deploy-{{port}}.log"
PUBLISH_LOG = f"{TOOL_OUTPUT_DIR}/publish-{{port}}.log"
PREVIEW_WIDTH = 1200
PREVIEW_HEIGHT = 900
LOG_CLEAR_PROG = """
import sys
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[1], sys.argv[2], create_parent=True) as target:
        target.unlink()
except ContainmentError as error:
    raise SystemExit(str(error))
"""

MAX_SITE_FILES = 1000
MAX_SITE_TOTAL_BYTES = 100 * 1024 * 1024
ENUMERATE_TIMEOUT_SECONDS = 120
SOURCE_SKIP_NAMES = (".git", "node_modules", "__pycache__", ".venv", ".DS_Store")
"""What a page's directory holds that the page is not. A member names the directory they built in —
a repository checkout on their own machine under the terminal carrier, so the walk meets the tree
they work in — and its history, its installed packages and its caches are neither bytes a visitor
asks for nor bytes the caps were written for: `.git` alone can outrun both, and every byte that
lands in the store is served at the site's own origin."""
ENUMERATE_PROG = """
import hashlib
import json
import os
import sys
from containment import ContainmentError, NotRegularFile, contained_dir, contained_file

project, workspace = sys.argv[1], sys.argv[2]
max_files, max_bytes = int(sys.argv[3]), int(sys.argv[4])
skipped = set(sys.argv[5].split(","))
try:
    root = contained_dir(project, workspace)
except ContainmentError as error:
    raise SystemExit(str(error))
files = {}
total = 0
for base, dirs, names in os.walk(root):
    dirs[:] = [
        name
        for name in dirs
        if name not in skipped and not os.path.islink(os.path.join(base, name))
    ]
    for name in names:
        full = os.path.join(base, name)
        if name in skipped or os.path.islink(full):
            continue
        path = os.path.relpath(full, root).replace(os.sep, "/")
        digest = hashlib.sha256()
        size = 0
        try:
            with contained_file(full, root) as target, target.open_bytes() as handle:
                while True:
                    chunk = handle.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
        except (FileNotFoundError, NotRegularFile):
            continue
        except ContainmentError as error:
            raise SystemExit(str(error))
        files[path] = {"size": size, "sha256": digest.hexdigest()}
        total += size
        if len(files) > max_files:
            raise SystemExit(f"{project} holds more than {max_files} files")
        if total > max_bytes:
            raise SystemExit(f"{project} holds more than {max_bytes} bytes")
if not files:
    raise SystemExit(f"{project} holds no files to host")
print(json.dumps(files))
"""
SITE_MEDIA_TYPES = {
    "html": "text/html",
    "htm": "text/html",
    "js": "text/javascript",
    "mjs": "text/javascript",
    "css": "text/css",
    "json": "application/json",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "ico": "image/x-icon",
    "txt": "text/plain",
    "md": "text/markdown",
    "ts": "text/plain",
    "tsx": "text/plain",
    "map": "application/json",
    "wasm": "application/wasm",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "pdf": "application/pdf",
}
SITE_MEDIA_TYPE_DEFAULT = "application/octet-stream"

START_SERVER_DESCRIPTION = (
    "Start a project in the background with automatic port cleanup and readiness detection. Omit "
    "command to serve a static folder. Pass command only for a project's own app server. Use this "
    "instead of bash — it polls until the port is listening and returns the route."
)
DEPLOY_WEBSITE_DESCRIPTION = (
    "Serve a website folder and host it at a permanent link the member can open. Pass the "
    "directory containing the built static output (index.html). Returns site_url — the deliverable "
    "— beside the sandbox-local url. Re-deploying the same site_name updates it behind that link. "
    'This hosts a site outside ufo. A bare "app" means a ufo app — one that lives on the Apps '
    "screen, run by its own agent — which create-application makes, never this."
)
PUBLISH_WEBSITE_DESCRIPTION = (
    "Publish a built project that runs a server: install its dependencies, run run_command with "
    "$PORT set to the port this probes, and host it at a permanent link. Returns site_url — the "
    "deliverable — beside the sandbox-local url. A folder of built files with no server of its "
    'own is deploy_website\'s. Both host outside ufo. A bare "app" means a ufo app, one that '
    "lives on the Apps screen, which create-application makes, never this."
)
NO_EXTENSION_CONTEXT = "the website tools dispatched without their ExtensionContext"
SITE_NEEDS_AN_OWNER = "a hosted site needs an owner: no member is acting on this turn"
VISIBILITY_NEEDS_A_SPEAKER = (
    "changing who can open a site is a disclosure act and needs a live member: re-deploy without a "
    "visibility argument, or have the member say what it should be"
)
VISIBILITY_DESCRIPTION = (
    "Who may open the hosted link: private (you alone), workspace (any member), or public (anyone "
    "with the link). Omit unless the member asked — a new site defaults from where it was built, "
    "and an existing one keeps the visibility it has."
)
SET_HOMEPAGE_DESCRIPTION = (
    "Bind a hosted site as this agent's homepage — the page the portal shows for it. Pass the "
    "site object name from the deploy result; binding another site later moves the homepage and "
    "the old site stays hosted. A homepage's viewers are the agent's: a workspace-visible "
    "agent's homepage opens for every member, a private agent's for its owner and workspace "
    "admins — the site's own visibility does not apply while it is bound. Binding therefore "
    "re-gates the site, so it is its creator's act: bind only a site you deployed, and bind a "
    "standing one only when the member asked. The result echoes the effective visibility."
)
HOMEPAGE_NEEDS_ITS_CREATOR = (
    "a homepage answers the agent's audience instead of the site's own visibility, so binding a "
    "site re-gates it, and that is its creator's act alone: {site} is someone else's site, so "
    "have its creator bind it or deploy a site of your own"
)
HOMEPAGE_NEEDS_THE_AGENTS_OWNER = (
    "a homepage is the agent's own page, and rewriting another agent's page is its owner's or a "
    "workspace admin's act: {agent} is not yours to direct, so bind your own homepage or have its "
    "owner ask"
)
HOMEPAGE_BIND_NEEDS_A_SPEAKER = (
    "binding a standing site moves its viewers onto the agent's audience, and that re-gating "
    "needs its creator speaking: deploy the homepage fresh in this turn, or have the creator ask"
)
HOMEPAGE_REDEPLOY_NEEDS_A_SPEAKER = (
    "redeploying the homepage replaces the page every viewer opens, and only a member speaking "
    "can ask for that: report what you built and leave the page as it is"
)
HOMEPAGE_KEEPS_THE_AGENTS_VISIBILITY = (
    "a homepage answers the agent's visibility, and its own dormant level is its creator's: "
    "redeploy without a visibility argument"
)


class StartServerInput(BaseModel):
    command: str | None = Field(
        default=None,
        description="The project's app-server command. Omit to serve the folder as static files.",
    )
    project_path: str = Field(description="The project directory to run the command in.")
    port: int | None = Field(
        default=None, description="Port the server listens on; polled until it is reachable."
    )
    log_file: str | None = Field(
        default=None,
        description="File in the workspace to capture the server's stdout/stderr.",
    )

    @model_validator(mode="after")
    def validate_port(self) -> "StartServerInput":
        if self.command is not None and not self.command.strip():
            raise ValueError("command must contain a server command or be omitted for static files")
        if self.port is not None and not 0 < self.port < 65536:
            raise ValueError("port must be between 1 and 65535")
        return self


class DeployWebsiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_path: str = Field(
        description="Directory containing the built static output (index.html)."
    )
    site_name: str = Field(description="A name for the served site; it names the hosted link.")
    entry_point: str = Field(
        default="index.html", description="The entry file to serve, e.g. index.html."
    )
    visibility: Visibility | None = Field(default=None, description=VISIBILITY_DESCRIPTION)


class DeployUfoApplicationInput(BaseModel):
    site_name: str = Field(description="A name for the application; it names the hosted link.")


class QaUfoApplicationInput(BaseModel):
    pass


class PublishWebsiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_path: str = Field(description="The web app project directory.")
    app_name: str = Field(description="A name for the published app; it names the hosted link.")
    visibility: Visibility | None = Field(default=None, description=VISIBILITY_DESCRIPTION)
    run_command: str = Field(
        description="The server command to run. It must listen on the port in $PORT."
    )
    install_command: str | None = Field(
        default=None, description="Optional command to install dependencies before serving."
    )


class SetHomepageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(description="The site object name from the deploy result.")


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


async def _free_log(ctx: ToolContext, log_path: str) -> None:
    """Leave the log's name holding nothing, so the server's redirect is the thing that creates it.

    A shell redirect follows a symlink and truncates what it points at, and the log's name is one a
    model chooses or predicts in a directory the agent writes. Creating the file here and
    redirecting onto it afterwards only narrows that — the two are separate commands, and a link
    replanted between them is what the `>` then opens. So the name is emptied instead, through the
    guard's own `O_NOFOLLOW` descent, which also makes `tool-output` on the way; `set -C` then
    makes the redirect an `O_CREAT|O_EXCL` create, so a replant fails the start rather than steers
    it. A log an earlier run on this port left behind is this call's to clear.

    Noclobber covers the redirect alone. The port cleanup writes to `/dev/null` and `command` is the
    model's own, free to redirect where it likes; the background job keeps the setting it was forked
    with, so restoring it in the parent cannot reach the redirect already made."""
    runtime_root = PurePosixPath(await ctx.sandbox.runtime_path(TOOL_OUTPUT_DIR)).parent
    root = (
        str(runtime_root) if PurePosixPath(log_path).is_relative_to(runtime_root) else WORKSPACE_DIR
    )
    result = await ctx.sandbox.python(LOG_CLEAR_PROG, log_path, root)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot clear {log_path}")


async def _stop_server(ctx: ToolContext, port: int) -> None:
    result = await ctx.sandbox.python(
        PORT_STOP_PROG, str(port), timeout_s=APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot free port {port}")


async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None:
    stopped = await ctx.sandbox.sh(
        SERVER_TASK_STOP,
        pid,
        base,
        timeout_s=APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS,
    )
    if stopped.exit_code != 0:
        raise RuntimeError(stopped.stderr.strip() or "cannot stop the server task")
    waited = await ctx.sandbox.bash_task(
        command,
        base,
        detach=False,
        model_authored=True,
        timeout_s=APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS,
    )
    if waited.timed_out_after_s is not None:
        raise RuntimeError("the server task did not stop")


async def _reset_server_task(ctx: ToolContext, base: str) -> None:
    """Leave the task journal at `base` holding nothing, so the launch below starts a server rather
    than adopting the run before it.

    `ufo run --task` reattaches to a journal that holds a pid and never launches, and the identity
    this base is built from is deliberately the same across calls a resume must not double: one
    turn's three permitted audits and a cross-attempt retry of one call all land here. The port and
    the log are already freed above, so a run recorded at this base is over or is being taken over —
    it is stopped, and every one of its files goes, since `ufo run` reads the pid and the sweep only
    reaches an hour-old journal. Stopping before the removal is what keeps the ended supervisor from
    writing its exit code back over the fresh journal."""
    reset = await ctx.sandbox.sh(
        SERVER_TASK_RESET,
        base,
        str(APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS),
        timeout_s=APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS + 5,
    )
    if reset.exit_code != 0:
        raise RuntimeError(reset.stderr.strip() or f"cannot clear the server task at {base}")


async def _log_tail(ctx: ToolContext, log_path: str) -> str:
    tail = await ctx.sandbox.bash(
        f"tail -n {LOG_TAIL_LINES} {shell_path(log_path)} 2>/dev/null || true",
        timeout_s=LOG_TAIL_TIMEOUT_SECONDS,
    )
    return tail.stdout


class ServeFailed(Exception):
    """A serve that could not bring the new server up, carrying what to tell the agent.

    Freeing the port is the first thing a serve does, so by the time a start fails the server that
    held it is already dead — the member's site is down, and a failure reporting only what the
    start said reads as though nothing changed and invites a retry of the same broken command
    while the site stays dark. The port is the identity of what was taken, so it rides the
    failure, and so does the exit code the summary reads but does not name."""

    def __init__(self, failure: ToolFailure) -> None:
        super().__init__(failure.summary)
        self.failure = failure


def _serve_failed(summary: str, result: ExecResult, project: str, port: int) -> ServeFailed:
    return ServeFailed(
        ToolFailure(
            operation=f"serve {project} on port {port}",
            summary=f"{summary}. Nothing is serving that port now.",
            applied=(
                AppliedEffect(
                    kind="port",
                    identity=str(port),
                    state="freed: whatever was serving it was stopped before this start",
                ),
            ),
            command=CommandDiagnostics(
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out_after_s=result.timed_out_after_s,
            ),
        )
    )


async def _serve(
    ctx: ToolContext, command: str, project: str, port: int, log_path: str
) -> dict[str, object]:
    await _free_log(ctx, log_path)
    await _stop_server(ctx, port)
    task_identity = f"{ctx.idempotency_key or ctx.turn.id}:{port}:{log_path}"
    task_base = await ctx.sandbox.runtime_path(
        f"{TOOL_OUTPUT_DIR}/server-tasks/{sha256(task_identity.encode()).hexdigest()[:16]}"
    )
    await _reset_server_task(ctx, task_base)
    readiness_probe = (
        "python3 - <<'PY'\n"
        "import socket\n"
        "import sys\n"
        "import time\n"
        f"deadline = time.time() + {READINESS_TIMEOUT_SECONDS}\n"
        f"port = {port}\n"
        "while time.time() < deadline:\n"
        "    sock = socket.socket()\n"
        "    try:\n"
        "        sock.settimeout(1)\n"
        "        sock.connect(('127.0.0.1', port))\n"
        "        print('ready')\n"
        "        sys.exit(0)\n"
        "    except OSError:\n"
        "        time.sleep(1)\n"
        "    finally:\n"
        "        sock.close()\n"
        "sys.exit(1)\n"
        "PY"
    )
    server_command = (
        f"cd {shlex.quote(project)}\n"
        f"set -C\n"
        f"exec env PORT={port} bash -lc {shlex.quote(command)} "
        f">{shell_path(log_path)} 2>&1"
    )
    started = await ctx.sandbox.bash_task(
        server_command,
        task_base,
        detach=True,
        model_authored=True,
        timeout_s=READINESS_TIMEOUT_SECONDS + 5,
    )
    if started.exit_code != 0:
        logged = await _log_tail(ctx, log_path)
        if logged:
            raise _serve_failed(logged, started, project, port)
        if started.timed_out_after_s is not None:
            raise _serve_failed(
                f"the server never answered on port {port}: the sandbox stopped the start after "
                f"{started.timed_out_after_s}s and {log_path} holds nothing",
                started,
                project,
                port,
            )
        raise _serve_failed(
            started.stderr
            or started.stdout
            or SERVER_START_SAID_NOTHING.format(command=command, log=log_path),
            started,
            project,
            port,
        )
    task_pid = started.stdout.strip()
    try:
        probe = await ctx.sandbox.bash(readiness_probe, timeout_s=READINESS_TIMEOUT_SECONDS + 5)
    except BaseException:
        await _stop_server_task(ctx, server_command, task_base, task_pid)
        raise
    if probe.exit_code == 0:
        return {"url": f"http://localhost:{port}", "port": port, "log": log_path}
    await _stop_server_task(ctx, server_command, task_base, task_pid)
    logged = await _log_tail(ctx, log_path)
    if logged:
        raise _serve_failed(logged, probe, project, port)
    if probe.timed_out_after_s is not None:
        raise _serve_failed(
            PROBE_OUTLIVED_ITS_DEADLINE.format(
                port=port, seconds=probe.timed_out_after_s, log=log_path
            ),
            probe,
            project,
            port,
        )
    if probe.stderr:
        raise _serve_failed(probe.stderr, probe, project, port)
    raise _serve_failed(
        SERVER_NEVER_LISTENED.format(port=port, seconds=READINESS_TIMEOUT_SECONDS, log=log_path),
        probe,
        project,
        port,
    )


def _site_media_type(path: str) -> str:
    media_type = SITE_MEDIA_TYPES.get(path.rpartition(".")[2].lower(), SITE_MEDIA_TYPE_DEFAULT)
    return f"{media_type}; charset=utf-8" if media_type.startswith("text/") else media_type


async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]:
    """Every regular file under the project directory that a visitor could ask for, sized and
    digested inside the sandbox — where the bytes are — and refused there when the tree outgrows
    what a static site may hold. `SOURCE_SKIP_NAMES` is what the walk does not descend into."""
    listed = await ctx.sandbox.python(
        ENUMERATE_PROG,
        project,
        WORKSPACE_DIR,
        str(MAX_SITE_FILES),
        str(MAX_SITE_TOTAL_BYTES),
        ",".join(SOURCE_SKIP_NAMES),
        timeout_s=ENUMERATE_TIMEOUT_SECONDS,
    )
    if listed.exit_code != 0:
        raise RuntimeError(
            f"{listed.stderr.strip() or listed.stdout.strip()} — publish_website serves a larger "
            "or dynamic app from its own server"
        )
    files = json.loads(listed.stdout)
    if not isinstance(files, dict):
        raise RuntimeError(f"listing {project} returned no file map")
    return files


async def _promote_source(
    ctx: ToolContext,
    project: str,
    conversation_id: UUID,
    name: str,
    listing: dict[str, dict[str, object]],
) -> str:
    """Store the served directory's bytes as the site's source of record and answer the manifest
    `register` writes: each deploy under its own key prefix, so the keys are immutable and every
    deploy's prefix stands in the store as the site's history. The bytes go straight from the
    sandbox to the store —
    an S3 store takes them on presigned PUTs core mints for these exact keys, curled from inside
    the container, and a filesystem dev store takes the same files as streams — so a site's source
    never crosses this process."""
    root = f"sites/{conversation_id}/{name}/{uuid4().hex}/"
    manifest = SourceManifest(
        root=root,
        files={
            path: SiteFile(
                size=int(str(entry["size"])),
                media_type=_site_media_type(path),
                sha256=str(entry["sha256"]),
            )
            for path, entry in listing.items()
        },
    )
    paths = sorted(manifest.files)
    try:
        uploads = [
            (
                f"{project}/{path}",
                await ctx.blob.presigned_put_unmeasured(f"{root}{path}", SOURCE_PUT_TTL_SECONDS),
            )
            for path in paths
        ]
    except TypeError:
        for path in paths:
            await ctx.blob.put_stream(f"{root}{path}", ctx.sandbox.read_file(f"{project}/{path}"))
    else:
        total = sum(entry.size for entry in manifest.files.values())
        await transfer(ctx, UPLOAD_SCRIPT, uploads, total)
    return manifest.model_dump_json()


async def _pictured(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> str | None:
    """Draw the site's picture, and answer with why it could not be drawn rather than raising.

    `_illustrate` runs after `register`, so by the time it can fail the site is live, reachable,
    and holding the port — and the deploy the member asked for is done. Raising here throws that
    away: the whole call reports the render error, and the agent is never told the URL its own
    deploy just published, nor that the site it displaced is already retired. The picture is
    decoration on a row that exists; the deploy is the act. So the failure is a field beside the
    hosted result, not in place of it.

    The two carrier escapes are not that. `draw_from_page` photographs the page inside the
    sandbox, so e2b raising `SandboxProviderUnavailable` or a terminal answering nothing reaches
    here — and the engine parks the turn on the first and ends it on the second. Reported as a
    preview note they would read as a finished deploy on a box that is gone."""
    try:
        await _illustrate(ctx, name, port, conversation_id)
    except (SandboxProviderUnavailable, TerminalAbsent):
        raise
    except Exception as error:
        summary = str(error).strip() or type(error).__name__
        return clipped(f"{type(error).__name__}: {summary}", PREVIEW_ERROR_MAX_CHARS)
    return None


async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None:
    """Photograph the page the registration just published, write the picture onto its row, and
    compose the site's share card from a second shot taken for the card's own box.
    `conversation_id` is the row's own — the sandbox's conversation everywhere but a homepage
    redeploy, whose scratch server runs here while the row lives with the conversation that first
    deployed it.

    Core mints the site's ingress view and the preview blob's one-key write capability, then the
    preview service visits the first and writes the PNG to the second. The member's sandbox owns no
    browser work or preview bytes. The card is separate: it is what a link unfurls as, while the
    portal's card is this picture, so a deploy that draws one and not the other moves what it can.

    This runs after `_host`, not between the serve and it. `register` is the only thing that retires
    the site this deploy displaced from the port, so a turn that ends inside the external render has
    to find that row already moved. The picture therefore lands in a write of its own, which touches
    nothing but the preview columns."""
    sites = _sites_registry(ctx)
    preview = await ctx.render_site_preview(name, port, PREVIEW_WIDTH, PREVIEW_HEIGHT)
    if preview is not None:
        await sites.set_preview(conversation_id, name, preview)
    await draw_from_page(ctx, sites, conversation_id, name, port)


def _site_extension(ctx: ToolContext) -> ExtensionContext:
    if ctx.ext is None:
        raise RuntimeError(NO_EXTENSION_CONTEXT)
    return ctx.ext


def _site_actor(ctx: ToolContext, visibility: Visibility | None) -> tuple[ExtensionContext, UUID]:
    """The extension context a hosted-site write runs through and the member the site is owned by,
    with the refusals that precede any write: the context the tools were dispatched with, an owner
    for the row, and a live speaker for a visibility argument. A missing owner is `SpeakerRequired`
    because a `requested_by` ref repairs the call — nothing about the site is wrong, only who is
    asking for it.

    `_refuse_before_serving` and `_host` both call this, so the set is asked twice — once before
    the serve kills the port and once at the write, where a concurrent deploy may have moved — and
    written once."""
    ext = _site_extension(ctx)
    creator_member_id = authority_member_id(ctx.authority)
    if creator_member_id is None:
        raise SpeakerRequired(SITE_NEEDS_AN_OWNER)
    if visibility is not None and ctx.speaker_member_id is None:
        raise SpeakerRequired(VISIBILITY_NEEDS_A_SPEAKER)
    return ext, creator_member_id


async def _refuse_before_serving(
    ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None
) -> tuple[str, HostedSite | None]:
    """Raise anything hosting would raise, while the member's site is still up, and answer with the
    slugged name and the site this deploy will displace — a homepage redeploy's to unhost once its
    scratch serve has killed that site's server.

    Serving kills whatever holds the port in a container the member's own turns share, and
    registering only afterwards is what keeps a refusal from costing them that site. The other
    order — claim the row first — trades the refusal case for a worse one: a build that fails after
    the write leaves the displaced row deleted and a live row pointing at a dead port. So every
    refusal is asked here, nothing is written, and `_host` asks the same set again when it does
    write.

    Asking first is also what lets the unhost rule stay strict. Taking a port retires the site on
    it, which needs the member to have asked, and a subagent carries no speaker — but a refused
    deploy now costs it nothing, so the delegate reports what it built and leaves the standing site
    up. What it is not told to do is re-deploy under that site's name: that act succeeds, since a
    same-name deploy displaces nothing, and it would repoint the member's live link at a build
    nobody asked to put there. Reading the child's own profile as authority would not work anyway:
    a scheduled fire holds `build_website`, so a timer would escalate through the child it
    spawns."""
    ext, creator_member_id = _site_actor(ctx, visibility)
    workspace_id = ext.store.workspace_id
    name = site_name(raw_name)
    site_url(ctx.public_base_url, workspace_id, ctx.sandbox.conversation_id, name)
    displaced = await HostedSites(workspace_id, ext.transaction).refuse_or_pass(
        ctx.sandbox.conversation_id,
        name,
        port,
        creator_member_id,
        visibility,
        ctx.speaker_member_id is not None,
    )
    return name, displaced


async def _host(
    ctx: ToolContext,
    raw_name: str,
    port: int,
    visibility: Visibility | None,
    manifest: str | None,
) -> dict[str, object]:
    """Register the port a deploy just left serving as a hosted site, and describe the link it
    answers on. The refusals ran in `_refuse_before_serving`; `register` asks the same set again
    here, since this is the write and a concurrent deploy may have moved since. The picture of that
    page arrives afterwards, in `_illustrate`'s own write. The reported visibility is the one the
    frame gates on: the agent's for a site already bound as a homepage, the row's own otherwise.

    The site is registered against the conversation whose sandbox is serving it, which is the one
    the handle names rather than the one this turn belongs to. For a member's own turn they are the
    same. For a subagent they are not: it runs in the sandbox of the turn that spawned it, so the
    port it brings up is served by the member's sandbox and the link belongs to the member's
    conversation — where it outlives the child turn, and where a rebuild lands on the same link."""
    ext, creator_member_id = _site_actor(ctx, visibility)
    workspace_id = ext.store.workspace_id
    name = site_name(raw_name)
    serving = ctx.sandbox.conversation_id
    link = site_url(ctx.public_base_url, workspace_id, serving, name)
    site = await HostedSites(workspace_id, ext.transaction).register(
        serving,
        name,
        port,
        creator_member_id,
        visibility,
        ctx.audience,
        ctx.speaker_member_id is not None,
        manifest=manifest,
    )
    return {
        "site_name": site.name,
        "visibility": effective_visibility(site, await ext.agent_visibilities()),
        "site": site_object_name(site.conversation_id, site.name),
        "site_url": link,
    }


def _build_failed(command: str, project: str, result: ExecResult) -> ToolFailure:
    """A build that failed, with what it said. Both streams are kept because a toolchain writes its
    reason to stdout as readily as to stderr, and an expired budget is named apart from a non-zero
    exit — a command running `timeout` exits 124 exactly as a carrier-stopped one does, and
    "your build is broken" and "your build needs longer" want opposite fixes."""
    expired = result.timed_out_after_s is not None
    return ToolFailure(
        operation=f"{command} in {project}",
        summary=(
            f"the sandbox stopped the build after {result.timed_out_after_s}s"
            if expired
            else f"the build exited {result.exit_code}"
        )
        + ". Nothing was served or hosted.",
        command=CommandDiagnostics(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out_after_s=result.timed_out_after_s,
        ),
    )


async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult:
    port = args.port or START_SERVER_PORT
    project = workspace_path(args.project_path)
    log_path = (
        workspace_path(args.log_file)
        if args.log_file
        else await ctx.sandbox.runtime_path(SERVER_LOG.format(port=port))
    )
    command = args.command or f"python3 -m http.server {port} --bind 0.0.0.0"
    try:
        served = await _serve(ctx, command, project, port, log_path)
    except ServeFailed as failed:
        return failed.failure.result()
    return _json_result({**served, "project_path": project})


def _unhosted(displaced: HostedSite | None, conversation_id: UUID) -> dict[str, object]:
    """What this deploy took down, named as the object it was. A conversation serves one port, so a
    deploy under a new name retires the site that held it — and the member reads a link that has
    stopped answering with nothing having said it would. Reporting the loss is what lets the reply
    state it, and what tells the agent that a second name here is a replacement rather than an
    addition."""
    if displaced is None:
        return {}
    return {"unhosted": site_object_name(conversation_id, displaced.name)}


async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult:
    source_project = workspace_path(args.project_path)
    conversation = ctx.sandbox.conversation_id
    port = serve_port(conversation)
    bound = await _sites_registry(ctx).homepage(ctx.turn.agent_id)
    slug = site_name(args.site_name)
    if (
        bound is not None
        and slug in {bound.name, site_object_name(bound.conversation_id, bound.name)}
        and bound.conversation_id != conversation
        and await _sites_registry(ctx).read(conversation, slug) is None
    ):
        return await _redeploy_homepage(ctx, args, bound, port)
    name, displaced = await _refuse_before_serving(ctx, args.site_name, port, args.visibility)
    project, listing = await _served_directory(ctx, source_project)
    manifest = await _promote_source(ctx, project, conversation, name, listing)
    command = f"python3 -m http.server {port} --bind 0.0.0.0"
    deploy_log = await ctx.sandbox.runtime_path(DEPLOY_LOG.format(port=port))
    try:
        served = await _serve(ctx, command, project, port, deploy_log)
    except ServeFailed as failed:
        return failed.failure.result()
    hosted = await _host(ctx, name, port, args.visibility, manifest)
    drawn = await _pictured(ctx, name, port, conversation)
    return _json_result(
        {**served, **hosted, "entry_point": args.entry_point}
        | _unhosted(displaced, conversation)
        | ({} if drawn is None else {"preview_error": drawn})
    )


def _verdict(code: AuditIssueCode, message: str) -> ApplicationAuditVerdict:
    return ApplicationAuditVerdict(issues=(ApplicationAuditIssue(code=code, message=message),))


def _last_words(output: str) -> str:
    """The end of a build or audit's output, which is where the fault it stopped on is written.

    Every tool in this path — vite, node, the audit script — prints its warnings first and its
    fatal error last, so a head-truncated message hands the builder the part that does not matter.
    One recorded build spent its repair round on a `configLoader` deprecation notice while the
    error that actually stopped the build sat past the cut."""

    text = output.strip()
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return "…" + text[-(MAX_MESSAGE_CHARS - 1) :]


class ApplicationPageRefused(RuntimeError):
    """The deterministic verdict that stopped an app page from becoming a site.

    The message is the repair list, because the builder's next `edit` is what reads it. It is
    raised before anything is written, so a refusal leaves no row, no server, and no blob — which
    is what makes the audit and the hosting one act: a hosted page is an audited page, and there is
    nothing else to ask.

    It refuses every attempt, and counts none. A page that fails four times fails four times; the
    bounds are the child's rounds and the member's next message, never a budget that turns the
    fourth attempt into a hosted page nobody checked."""

    def __init__(self, verdict: ApplicationAuditVerdict) -> None:
        super().__init__(
            PAGE_REFUSAL_OPENING
            + "\n"
            + "\n".join(f"- {issue.message}" for issue in verdict.issues)
        )
        self.verdict = verdict


@dataclass(frozen=True)
class ApplicationPageGate:
    """What a directory holding `app.tsx` must be true of before its bytes become a site.

    Three deterministic acts in the order that makes each one cheap: the source is read and held to
    the kit before a build is paid for, the build runs, and the browser measures the page that
    build wrote. A design sitting beside the source adds the two checks only a design can carry —
    its components and regions in the source, its layout in the rendered lane.

    An audit that cannot run is not a repair. Chromium dying, a report that will not parse, a
    wedged sandbox: those raise as themselves, so the builder is never told to edit `app.tsx` to
    fix the browser."""

    ctx: ToolContext
    project: str

    async def built_page(self) -> str:
        """The kit rules, the build, and — for a page drawn against a design — the browser audit.

        The audit drives the page in a preview that answers every read with a workspace holding
        nothing, so it measures what a page draws on its own. A page whose content is the
        workspace's own rows draws its blank state there and exposes no control, which is the
        preview's emptiness and not the page's fault. Every page the builder makes is drawn
        against a design first, so the design is what says a page was built to be measured this
        way; the app pages an extension ships carry none and are built and served as they were."""
        design = await self._design()
        await self._gate_source(design)
        await self._build()
        if design is not None:
            await self._audit(design)
        return f"{self.project}/{PROJECT_DIST}"

    async def _read(self, name: str, maximum: int) -> str | None:
        held = await self.ctx.sandbox.python(
            PROJECT_FILE_READ, f"{self.project}/{name}", WORKSPACE_DIR, str(maximum)
        )
        if held.exit_code == PROJECT_FILE_ABSENT:
            return None
        if held.exit_code != 0:
            raise RuntimeError(held.stderr.strip() or f"{name} could not be read")
        return held.stdout

    async def _design(self) -> ApplicationDesign | None:
        source = await self._read(PROJECT_DESIGN, APPLICATION_DESIGN_MAX_CHARS)
        if source is None:
            return None
        try:
            return validate_application_design(source)
        except ValueError as error:
            raise ApplicationPageRefused(_verdict("design", str(error))) from error

    async def _gate_source(self, design: ApplicationDesign | None) -> None:
        source = await self._read(PROJECT_SOURCE, APPLICATION_SOURCE_MAX_CHARS)
        if source is None:
            raise RuntimeError(f"{PROJECT_SOURCE} could not be read")
        try:
            validate_application_source(source, design)
        except ValueError as error:
            raise ApplicationPageRefused(_verdict("source", str(error))) from error

    async def _build(self) -> None:
        await self.ctx.sandbox.write_file(f"{self.project}/{PROJECT_CONFIG}", PROJECT_CONFIG_BYTES)
        await self.ctx.sandbox.write_file(
            f"{self.project}/{PROJECT_PREVIEW}", PROJECT_PREVIEW_BYTES
        )
        await unpack_page_kit(self.ctx, self.project)
        built = await self.ctx.sandbox.sh(
            f"cd {shlex.quote(self.project)} && vite build", timeout_s=BUILD_TIMEOUT_SECONDS
        )
        if built.exit_code != 0:
            raise ApplicationPageRefused(
                _verdict(
                    "build",
                    _last_words(built.stderr or built.stdout) or "the page did not build",
                )
            )

    async def _audit(self, design: ApplicationDesign | None) -> None:
        relative_root = f"{TOOL_OUTPUT_DIR}/application-audit/{self.ctx.turn.id}"
        root = await self.ctx.sandbox.runtime_path(relative_root)
        report_path = f"{root}.json"
        await self.ctx.sandbox.write_runtime_file(f"{relative_root}.cjs", APPLICATION_AUDIT_SCRIPT)
        arguments = [
            f"{root}.cjs",
            self.project,
            report_path,
            f"{root}-light.png",
            f"{root}-dark.png",
            f"{root}-interactive.html",
            f"{root}-static.html",
            str(APPLICATION_DESIGN_WIDTH),
        ]
        if design is not None:
            arguments.append(f"{self.project}/{PROJECT_DESIGN}")
        run = await self.ctx.sandbox.sh(
            'node "$@"',
            *arguments,
            timeout_s=APPLICATION_AUDIT_TIMEOUT_SECONDS,
        )
        if run.exit_code != 0:
            await self._refuse_or_raise(run, report_path)
        report_read = await self.ctx.sandbox.python(
            APPLICATION_AUDIT_REPORT_READ, report_path, str(APPLICATION_AUDIT_REPORT_MAX_BYTES)
        )
        if report_read.exit_code != 0:
            absent = "audit report is absent"
            raise RuntimeError(_last_words(report_read.stderr or report_read.stdout) or absent)
        verdict = audit_application(ApplicationAuditReport.model_validate_json(report_read.stdout))
        if not verdict.passed:
            raise ApplicationPageRefused(verdict)

    async def _refuse_or_raise(self, run: ExecResult, report_path: str) -> None:
        """Turn a failed audit run into the repair it is, or raise it as ours.

        The script exits on three kinds of fault and the builder can act on two of them. A design
        the browser measured and found wrong — text past the lane, regions overlapping — names the
        region and the overflow, which is an edit. A page that never became ready is the page's own
        fault and says so. Only a run that could not happen — no Chromium, no node, a wedged box —
        is infrastructure, and telling a model to repair `app.tsx` over that sends it hunting a
        fault that is not in the page.

        This is the distinction that mattered most in the field: eight of thirteen deploys in one
        recorded build were spent on faults the audit had already diagnosed and then discarded."""

        detail = _last_words(run.stderr or run.stdout)
        if run.exit_code == APPLICATION_DESIGN_FAULT_EXIT:
            raise ApplicationPageRefused(_verdict("design", detail or "the design did not measure"))
        if run.exit_code == APPLICATION_LIFECYCLE_EXIT:
            reason = await self._lifecycle_reason(report_path)
            raise ApplicationPageRefused(
                _verdict("lifecycle", reason or detail or APPLICATION_LIFECYCLE_UNREAD)
            )
        raise RuntimeError(detail or "the browser audit returned no error")

    async def _lifecycle_reason(self, report_path: str) -> str:
        diagnostic = await self.ctx.sandbox.python(
            APPLICATION_AUDIT_REPORT_READ,
            f"{report_path}{APPLICATION_LIFECYCLE_SUFFIX}",
            str(APPLICATION_LIFECYCLE_DIAGNOSTIC_MAX_BYTES),
        )
        if diagnostic.exit_code != 0:
            return ""
        record = _ApplicationLifecycleDiagnostic.model_validate_json(diagnostic.stdout)
        state = (_lifecycle_state(record.snapshot),) if record.snapshot else ()
        return _lifecycle_message(record.reason, state + record.problems)


async def _served_directory(
    ctx: ToolContext, project: str
) -> tuple[str, dict[str, dict[str, object]]]:
    """The directory whose bytes are hosted, with its listing: a page project's audited build
    output, or the directory it was handed.

    A directory holding `app.tsx` is source, not a site — the app pages an agent edits arrive that
    way, the one file to change and the page that names it, mounted by the skill it loaded — so this
    hands it to the gate, which holds it to the kit, builds it, and measures the page that build
    wrote. No browser runs TSX, so a directory naming one could never have been served as it stands,
    and building it is the only reading of it that works.

    The agent never runs the build itself. A page deployed as its own source is the one mistake in
    this flow, and a tool that always builds rules it out instead of describing it. The build's
    output carries its own source, so a later read of the site starts from a project again.

    A static folder is handed back untouched: the gate is what an app page is held to, not what a
    member's website is."""
    listing = await _source_listing(ctx, project)
    if PROJECT_SOURCE not in listing:
        return project, listing
    page = await ApplicationPageGate(ctx, project).built_page()
    return page, await _source_listing(ctx, page)


def _sites_registry(ctx: ToolContext) -> HostedSites:
    ext = _site_extension(ctx)
    return HostedSites(ext.store.workspace_id, ext.transaction)


async def _redeploy_homepage(
    ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int
) -> ToolResult:
    """Update the acting agent's bound homepage in place from this conversation's build.

    The row, its port, its label origin and its link all stay: the source is promoted under the
    bound row's own identity and the generation stamp remounts the portal's frame. The member's
    link never moves and no second row is founded, so a directive spoken in any conversation edits
    the one page the homepage read answers with. The creator gate deliberately relaxes here: the
    homepage answers the agent's audience, so who may direct the agent is who may reshape its
    page — but only a member speaking, since the page every viewer opens is what changes. A site
    of this conversation displaced from the scratch port is unhosted outright once the serve has
    killed its server: its row must not keep answering a port that now serves the homepage
    build."""
    if ctx.speaker_member_id is None:
        raise SpeakerRequired(HOMEPAGE_REDEPLOY_NEEDS_A_SPEAKER)
    if args.visibility is not None:
        raise ValueError(HOMEPAGE_KEEPS_THE_AGENTS_VISIBILITY)
    member_id = authority_member_id(ctx.authority)
    if member_id is None:
        raise SpeakerRequired(SITE_NEEDS_AN_OWNER)
    sites = _sites_registry(ctx)
    displaced = await sites.refuse_or_pass(
        ctx.sandbox.conversation_id,
        bound.name,
        scratch_port,
        member_id,
        None,
        True,
    )
    source_project = workspace_path(args.project_path)
    project, listing = await _served_directory(ctx, source_project)
    manifest = await _promote_source(ctx, project, bound.conversation_id, bound.name, listing)
    command = f"python3 -m http.server {scratch_port} --bind 0.0.0.0"
    deploy_log = await ctx.sandbox.runtime_path(DEPLOY_LOG.format(port=scratch_port))
    try:
        served = await _serve(ctx, command, project, scratch_port, deploy_log)
    except ServeFailed as failed:
        return failed.failure.result()
    updated = await sites.redeploy(bound.conversation_id, bound.name, manifest)
    if updated is None:
        raise RuntimeError("the homepage was unhosted while it was being redeployed")
    if displaced is not None:
        await sites.unregister(displaced.conversation_id, displaced.name)
    drawn = await _pictured(ctx, bound.name, scratch_port, bound.conversation_id)
    return _json_result(
        {
            **served,
            "site_name": updated.name,
            "visibility": await ctx.agent_visibility(),
            "site": site_object_name(updated.conversation_id, updated.name),
            "site_url": site_url(
                ctx.public_base_url,
                _site_extension(ctx).store.workspace_id,
                updated.conversation_id,
                updated.name,
            ),
            "entry_point": args.entry_point,
        }
        | ({} if drawn is None else {"preview_error": drawn})
    )


async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult:
    conversation = ctx.sandbox.conversation_id
    port = serve_port(conversation)
    name, displaced = await _refuse_before_serving(ctx, args.app_name, port, args.visibility)
    if args.install_command:
        install = await ctx.sandbox.bash(
            f"cd {shlex.quote(workspace_path(args.project_path))} && {args.install_command}",
            timeout_s=BUILD_TIMEOUT_SECONDS,
        )
        if install.exit_code != 0:
            return _build_failed(
                args.install_command, workspace_path(args.project_path), install
            ).result()
    command = args.run_command
    project = workspace_path(args.project_path)
    publish_log = await ctx.sandbox.runtime_path(PUBLISH_LOG.format(port=port))
    try:
        served = await _serve(ctx, command, project, port, publish_log)
    except ServeFailed as failed:
        return failed.failure.result()
    hosted = await _host(ctx, name, port, args.visibility, None)
    drawn = await _pictured(ctx, name, port, conversation)
    return _json_result(
        {**served, **hosted}
        | _unhosted(displaced, conversation)
        | ({} if drawn is None else {"preview_error": drawn})
    )


async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult:
    """Bind one hosted site as the homepage of the agent the call targets.

    The agent is the instance the action was dispatched on: its own kind's read admitted the row
    — shared, owned, admin, or the turn's own agent — before this handler ran, and a read is not a
    write: another agent's page is rewritten only by that agent's owner or a workspace admin,
    since the kind shows members agents they do not control. Binding writes
    nothing but the pointer, yet it moves the site's viewers onto the agent's audience — every
    member for a workspace agent, its owner and admins for a private one — so the bind answers to
    the module's disclosure rule the way a visibility change does. It is the creator's act alone:
    another member's site is refused whatever its visibility, since a bind would widen a private
    site or re-gate a shared one out from under its creator. And a standing site needs the creator
    speaking — without a live speaker the bind reaches only a site this same turn deployed, onto
    this same turn's agent, whose gate is the room's default rather than a choice anyone made —
    which is exactly the seed's deploy-and-bind shape, so seeding stays speakerless-safe while a
    scheduled turn can never re-gate what a member left standing."""
    ext = _site_extension(ctx)
    if ctx.target is None or ctx.target.name is None:
        raise RuntimeError("set_homepage dispatched without its agent target")
    agent = await ext.agent_named(ctx.target.name)
    if agent is None:
        raise ValueError(f"no live agent is named {ctx.target.name!r}")
    agent_id = agent.id
    member_id = authority_member_id(ctx.authority)
    owns = member_id is not None and agent.owner_member_id == member_id
    if agent_id != ctx.turn.agent_id and not owns and not await ctx.speaker_is_admin():
        raise ValueError(HOMEPAGE_NEEDS_THE_AGENTS_OWNER.format(agent=ctx.target.name))
    workspace_id = ext.store.workspace_id
    sites = HostedSites(workspace_id, ext.transaction)
    named = {site_object_name(site.conversation_id, site.name): site for site in await sites.all()}
    site = named.get(args.site)
    if site is None:
        raise ValueError(
            f"no hosted site is named {args.site!r}: deploy the site and bind the name its "
            "result carries"
        )
    if authority_member_id(ctx.authority) != site.creator_member_id:
        raise ValueError(HOMEPAGE_NEEDS_ITS_CREATOR.format(site=args.site))
    deployed_this_turn = (
        site.conversation_id == ctx.sandbox.conversation_id
        and site.created_at >= ctx.turn.created_at
        and agent_id == ctx.turn.agent_id
    )
    if ctx.speaker_member_id is None and not deployed_this_turn:
        raise SpeakerRequired(HOMEPAGE_BIND_NEEDS_A_SPEAKER)
    bound = await sites.set_homepage(agent_id, site.conversation_id, site.name)
    if bound is None:
        raise ValueError(f"site {args.site!r} was unhosted while it was being bound")
    return _json_result(
        {
            "site": args.site,
            "site_url": site_url(
                ctx.public_base_url, workspace_id, bound.conversation_id, bound.name
            ),
            "visibility": (await ext.agent_visibilities())[agent_id],
            "homepage_agent": str(agent_id),
        }
    )


SITES_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=START_SERVER_TOOL,
        description=START_SERVER_DESCRIPTION,
        input_model=StartServerInput,
        handler=start_server,
    ),
    ToolDef(
        name=DEPLOY_WEBSITE_TOOL,
        description=DEPLOY_WEBSITE_DESCRIPTION,
        input_model=DeployWebsiteInput,
        handler=deploy_website,
        side_effecting=True,
        bound=ObjectBinding(kind=SITE_KIND, binding="collection"),
    ),
    ToolDef(
        name=PUBLISH_WEBSITE_TOOL,
        description=PUBLISH_WEBSITE_DESCRIPTION,
        input_model=PublishWebsiteInput,
        handler=publish_website,
        side_effecting=True,
        bound=ObjectBinding(kind=SITE_KIND, binding="collection"),
    ),
    ToolDef(
        name=SET_HOMEPAGE_TOOL,
        description=SET_HOMEPAGE_DESCRIPTION,
        input_model=SetHomepageInput,
        handler=set_homepage,
        side_effecting=True,
        bound=ObjectBinding(kind=AGENT_KIND, binding="instance"),
    ),
)

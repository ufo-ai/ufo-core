"""The website tools: build a site, serve one in the sandbox with port cleanup and a readiness
probe, and host the served port at a permanent link.

Every path argument these tools take — a project directory, a dist directory, a log file — goes
through `workspace_path` before it reaches a command, so a model-named path is scoped to the
workspace rather than merely quoted into `cd`/`>` (under the local carrier those are host paths in a
host subprocess). The log files default under the run's `tool-output` directory rather than the
workspace root: a server log is scaffolding, and the workspace listing is the
member's own file list.

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
scratch server is not a deliverable. A deploy with no browser sign-in opens only a public site's
link, so a result reporting any other level says so.

`deploy_website` also promotes the served directory into the workspace blob store and writes the
manifest onto the row — the site's source of record, which the ingress serves with no sandbox dial
and the site kind's `object_get` materializes back into any conversation for an edit. An app page
builds against the kit archive `[sites] page_kit` names, so a deploy that sets none refuses one.
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

import asyncio
import json
import shlex
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.sdk.context import ExtensionContext
from ufo.sdk.objects import AGENT_KIND
from ufo.sdk.sandbox import (
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
from ufo_ext_sites.objects import SITE_KIND, effective_visibility, site_object_name
from ufo_ext_sites.share_card import draw_from_page
from ufo_ext_sites.source import (
    APPLICATION_SOURCE_MAX_CHARS,
    PROJECT_CONFIG,
    PROJECT_CONFIG_BYTES,
    PROJECT_DIST,
    PROJECT_FILE_ABSENT,
    PROJECT_FILE_READ,
    PROJECT_SOURCE,
    SOURCE_PUT_TTL_SECONDS,
    UPLOAD_SCRIPT,
    transfer,
    unpack_page_kit,
    validate_application_source,
)
from ufo_ext_sites.store import (
    HostedSite,
    HostedSites,
    SiteFile,
    SourceManifest,
    Visibility,
    site_name,
)
from ufo_ext_sites.surface import NO_BROWSER_SIGN_IN, site_url

START_SERVER_TOOL = "start_server"
DEPLOY_WEBSITE_TOOL = "deploy_website"
PUBLISH_WEBSITE_TOOL = "publish_website"
SET_HOMEPAGE_TOOL = "set_homepage"

MAX_MESSAGE_CHARS = 500
START_SERVER_PORT = 5000
SERVER_STOP_TIMEOUT_SECONDS = 15
PAGE_REFUSAL_OPENING = "This page cannot be hosted yet. Repair it and deploy again:"


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
import os
import sys

os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)
try:
    os.unlink(sys.argv[1])
except FileNotFoundError:
    pass
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

project = sys.argv[1]
max_files, max_bytes = int(sys.argv[2]), int(sys.argv[3])
skipped = set(sys.argv[4].split(","))
files = {}
total = 0
for base, dirs, names in os.walk(project):
    dirs[:] = [name for name in dirs if name not in skipped]
    for name in names:
        full = os.path.join(base, name)
        if name in skipped or not os.path.isfile(full):
            continue
        path = os.path.relpath(full, project).replace(os.sep, "/")
        digest = hashlib.sha256()
        size = 0
        with open(full, "rb") as handle:
            while chunk := handle.read(1 << 20):
                size += len(chunk)
                digest.update(chunk)
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
    "screen, run by its own agent — never this."
)
PUBLISH_WEBSITE_DESCRIPTION = (
    "Publish a built project that runs a server: install its dependencies, run run_command with "
    "$PORT set to the port this probes, and host it at a permanent link. Returns site_url — the "
    "deliverable — beside the sandbox-local url. A folder of built files with no server of its "
    'own is deploy_website\'s. Both host outside ufo. A bare "app" means a ufo app, one that '
    "lives on the Apps screen, never this."
)
NO_EXTENSION_CONTEXT = "the website tools dispatched without their ExtensionContext"
PAGE_KIT_UNCONFIGURED = (
    "this deployment sets no [sites] page_kit, so {project}, which holds "
    f"{PROJECT_SOURCE}, is an app page with no kit to build against"
)
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
    """`set -C` covers only the server's redirect, and the background job keeps the setting it was
    forked with, so restoring it in the parent cannot reach that redirect."""
    result = await ctx.sandbox.python(LOG_CLEAR_PROG, log_path)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot clear {log_path}")


async def _stop_server(ctx: ToolContext, port: int) -> None:
    result = await ctx.sandbox.python(
        PORT_STOP_PROG, str(port), timeout_s=SERVER_STOP_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot free port {port}")


async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None:
    stopped = await ctx.sandbox.sh(
        SERVER_TASK_STOP,
        pid,
        base,
        timeout_s=SERVER_STOP_TIMEOUT_SECONDS,
    )
    if stopped.exit_code != 0:
        raise RuntimeError(stopped.stderr.strip() or "cannot stop the server task")
    waited = await ctx.sandbox.bash_task(
        command,
        base,
        detach=False,
        model_authored=True,
        timeout_s=SERVER_STOP_TIMEOUT_SECONDS,
    )
    if waited.timed_out_after_s is not None:
        raise RuntimeError("the server task did not stop")


async def _reset_server_task(ctx: ToolContext, base: str) -> None:
    """`ufo run --task` reattaches to a journal holding a pid and never launches; stopping before
    removal keeps the ended supervisor from writing its exit code over the fresh journal."""
    reset = await ctx.sandbox.sh(
        SERVER_TASK_RESET,
        base,
        str(SERVER_STOP_TIMEOUT_SECONDS),
        timeout_s=SERVER_STOP_TIMEOUT_SECONDS + 5,
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
    listed = await ctx.sandbox.python(
        ENUMERATE_PROG,
        project,
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
    """The carrier escapes still raise: the engine parks the turn on `SandboxProviderUnavailable`
    and ends it on a terminal answering nothing."""
    try:
        await _illustrate(ctx, name, port, conversation_id)
    except (SandboxProviderUnavailable, TerminalAbsent):
        raise
    except Exception as error:
        summary = str(error).strip() or type(error).__name__
        return clipped(f"{type(error).__name__}: {summary}", PREVIEW_ERROR_MAX_CHARS)
    return None


async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None:
    """Runs after `_host`: `register` alone retires the site this deploy displaced, so a turn ending
    inside the render must find that row already moved."""
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
    ext = _site_extension(ctx)
    creator_member_id = ctx.speaker_member_id
    if creator_member_id is None:
        raise SpeakerRequired(SITE_NEEDS_AN_OWNER)
    if visibility is not None and ctx.speaker_member_id is None:
        raise SpeakerRequired(VISIBILITY_NEEDS_A_SPEAKER)
    return ext, creator_member_id


async def _refuse_before_serving(
    ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None
) -> tuple[str, HostedSite | None]:
    """Claiming the row before serving let a build that failed after the write leave the displaced
    row deleted and a live row pointing at a dead port."""
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
    visibility = effective_visibility(site, await ext.agent_visibilities())
    return {
        "site_name": site.name,
        "visibility": visibility,
        "site": site_object_name(site.conversation_id, site.name),
        "site_url": link,
    } | _sign_in_note(ctx, visibility)


def _sign_in_note(ctx: ToolContext, visibility: str) -> dict[str, object]:
    if visibility == "public" or ctx.sign_in_path is not None:
        return {}
    return {"sign_in": NO_BROWSER_SIGN_IN}


def _build_failed(command: str, project: str, result: ExecResult) -> ToolFailure:
    """A command running `timeout` exits 124 exactly as a carrier-stopped one does, so an expired
    budget is named apart from a non-zero exit."""
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


def _last_words(output: str) -> str:
    """Vite prints the fatal error last; a head-truncated message once
    spent a repair round on a `configLoader` deprecation notice."""

    text = output.strip()
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return "…" + text[-(MAX_MESSAGE_CHARS - 1) :]


class ApplicationPageIssue(BaseModel):
    code: Literal["source", "build"]
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ApplicationPageRefused(RuntimeError):
    """A source or build failure that prevents hosting."""

    def __init__(self, code: Literal["source", "build"], message: str) -> None:
        self.issue = ApplicationPageIssue(code=code, message=message)
        super().__init__(PAGE_REFUSAL_OPENING + "\n- " + self.issue.message)


@dataclass(frozen=True)
class ApplicationPageGate:
    """Validate source and build the page before hosting."""

    ctx: ToolContext
    project: str
    kit: bytes

    async def built_page(self) -> str:
        """Return the built directory after source and build checks pass."""
        await self._gate_source()
        await self._build()
        return f"{self.project}/{PROJECT_DIST}"

    async def _read(self, name: str, maximum: int) -> str | None:
        held = await self.ctx.sandbox.python(
            PROJECT_FILE_READ, f"{self.project}/{name}", str(maximum)
        )
        if held.exit_code == PROJECT_FILE_ABSENT:
            return None
        if held.exit_code != 0:
            raise RuntimeError(held.stderr.strip() or f"{name} could not be read")
        return held.stdout

    async def _gate_source(self) -> None:
        source = await self._read(PROJECT_SOURCE, APPLICATION_SOURCE_MAX_CHARS)
        if source is None:
            raise RuntimeError(f"{PROJECT_SOURCE} could not be read")
        try:
            validate_application_source(source)
        except ValueError as error:
            raise ApplicationPageRefused("source", str(error)) from error

    async def _build(self) -> None:
        await self.ctx.sandbox.write_file(f"{self.project}/{PROJECT_CONFIG}", PROJECT_CONFIG_BYTES)
        await unpack_page_kit(self.ctx, self.kit, self.project)
        built = await self.ctx.sandbox.sh(
            f"cd {shlex.quote(self.project)} && vite build", timeout_s=BUILD_TIMEOUT_SECONDS
        )
        if built.exit_code != 0:
            raise ApplicationPageRefused(
                "build",
                _last_words(built.stderr or built.stdout) or "the page did not build",
            )


async def _served_directory(
    ctx: ToolContext, project: str
) -> tuple[str, dict[str, dict[str, object]]]:
    listing = await _source_listing(ctx, project)
    if PROJECT_SOURCE not in listing:
        return project, listing
    if ctx.page_kit is None:
        raise RuntimeError(PAGE_KIT_UNCONFIGURED.format(project=project))
    kit = await asyncio.to_thread(ctx.page_kit.read_bytes)
    page = await ApplicationPageGate(ctx, project, kit).built_page()
    return page, await _source_listing(ctx, page)


def _sites_registry(ctx: ToolContext) -> HostedSites:
    ext = _site_extension(ctx)
    return HostedSites(ext.store.workspace_id, ext.transaction)


async def _redeploy_homepage(
    ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int
) -> ToolResult:
    if ctx.speaker_member_id is None:
        raise SpeakerRequired(HOMEPAGE_REDEPLOY_NEEDS_A_SPEAKER)
    if args.visibility is not None:
        raise ValueError(HOMEPAGE_KEEPS_THE_AGENTS_VISIBILITY)
    member_id = ctx.speaker_member_id
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
    visibility = await ctx.agent_visibility()
    return _json_result(
        {
            **served,
            "site_name": updated.name,
            "visibility": visibility,
            "site": site_object_name(updated.conversation_id, updated.name),
            "site_url": site_url(
                ctx.public_base_url,
                _site_extension(ctx).store.workspace_id,
                updated.conversation_id,
                updated.name,
            ),
            "entry_point": args.entry_point,
        }
        | _sign_in_note(ctx, visibility)
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
    site or re-gate a shared one out from under its creator. Without a live creator speaking, the
    bind is refused."""
    ext = _site_extension(ctx)
    if ctx.target is None or ctx.target.name is None:
        raise RuntimeError("set_homepage dispatched without its agent target")
    agent = await ext.agent_named(ctx.target.name)
    if agent is None:
        raise ValueError(f"no live agent is named {ctx.target.name!r}")
    agent_id = agent.id
    member_id = ctx.speaker_member_id
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
    if ctx.speaker_member_id is None:
        raise SpeakerRequired(HOMEPAGE_NEEDS_ITS_CREATOR.format(site=args.site))
    if ctx.speaker_member_id != site.creator_member_id:
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
    visibility = (await ext.agent_visibilities())[agent_id]
    return _json_result(
        {
            "site": args.site,
            "site_url": site_url(
                ctx.public_base_url, workspace_id, bound.conversation_id, bound.name
            ),
            "visibility": visibility,
            "homepage_agent": str(agent_id),
        }
        | _sign_in_note(ctx, visibility)
    )


SITES_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=START_SERVER_TOOL,
        description=START_SERVER_DESCRIPTION,
        input_model=StartServerInput,
        handler=start_server,
        retains_sandbox_authority=True,
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

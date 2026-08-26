"""The website tools: build a site, serve one in the sandbox with port cleanup and a readiness
probe, and host the served port at a permanent link.

Every path argument these tools take — a project directory, a dist directory, a log file — goes
through `workspace_path` before it reaches a command, so a model-named path is scoped to the
workspace rather than merely quoted into `cd`/`>` (under the local carrier those are host paths in a
host subprocess). The log files default under `.tool-output`, the engine's own offload directory,
rather than the workspace root: a server log is scaffolding, and the workspace listing is the
member's own file list. A predictable path in a directory the agent can write is still where a
planted link would sit, so each log's name is emptied through the containment guard and the `>`
redirect runs under `set -C`, which creates it `O_CREAT|O_EXCL` rather than truncating through a
link.

Each tool runs through `ctx.sandbox`, so the container's mount and egress scoping hold. `website`
runs a build command and lists what it produced. `start_server`, `deploy_website`, and
`publish_website` bring a server up in the background: they free the port, launch the command under
`nohup`, and poll until the port is listening before returning — so the tool returns a running,
reachable server rather than a race. The served URL is `http://localhost:<port>` inside the sandbox,
which the browser tools and js_repl reach to validate the page.

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

`set_homepage` binds one hosted site as the acting agent's homepage — the pointer the portal reads.
The frame gates a homepage's viewers on the agent's visibility rather than the site's, so who may
open it is decided where the agent's audience is decided — the agent object's `visibility` — and
the bind itself is the re-gating act: it takes the site's creator acting, and a live speaker
unless the same turn deployed the site, the seed's deploy-and-bind shape."""

import json
import shlex
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from ufo.sdk.sandbox import TOOL_OUTPUT_DIR, WORKSPACE_DIR, serve_port, workspace_path
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.application_audit import (
    APPLICATION_AUDIT_ATTEMPT_KEY,
    APPLICATION_AUDIT_TURN_CONTRACT_KEY,
    MAX_PRODUCT_QA_CONTROLS,
    ApplicationAuditContract,
    ApplicationAuditFeedback,
    ApplicationAuditIssue,
    ApplicationAuditReport,
    ApplicationProductQaResult,
    ApplicationQaProof,
    audit_application,
)
from ufo_ext_sites.application_builder import (
    APPLICATION_BUILDER_DEPLOY_GUARD_REASON,
    APPLICATION_BUILDER_DEPLOY_TOOL,
    APPLICATION_BUILDER_NAME,
    APPLICATION_BUILDER_QA_CALL_KEY,
    APPLICATION_BUILDER_QA_MAX_CALLS,
    APPLICATION_BUILDER_QA_PROOF_KEY,
    APPLICATION_BUILDER_QA_TOOL,
    APPLICATION_BUILDER_REDEPLOY_KEY,
    APPLICATION_SCAFFOLD_PATH,
    APPLICATION_SOURCE_PATH,
    APPLICATION_SOURCE_READ,
)
from ufo_ext_sites.objects import effective_visibility, site_object_name
from ufo_ext_sites.share_card import draw_from_page
from ufo_ext_sites.source import (
    PROJECT_CONFIG,
    PROJECT_CONFIG_BYTES,
    PROJECT_DIST,
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

WEBSITE_TOOL = "website"
START_SERVER_TOOL = "start_server"
DEPLOY_WEBSITE_TOOL = "deploy_website"
PUBLISH_WEBSITE_TOOL = "publish_website"
SET_HOMEPAGE_TOOL = "set_homepage"

START_SERVER_PORT = 5000
APPLICATION_AUDIT_PORT_FLOOR = 40000
APPLICATION_AUDIT_PORT_SPAN = 20000
APPLICATION_AUDIT_TIMEOUT_SECONDS = 120
APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS = 15
APPLICATION_AUDIT_REPORT_MAX_BYTES = 1024 * 1024
APPLICATION_AUDIT_MAX_ATTEMPTS = 2
APPLICATION_AUDIT_SCRIPT = (
    Path(__file__).parent / "scripts" / "audit_application.cjs"
).read_bytes()
APPLICATION_AUDIT_REPORT_READ = """from pathlib import Path
import sys
path = Path(sys.argv[1])
data = path.read_bytes()
if len(data) > int(sys.argv[2]):
    raise SystemExit("application audit report is too large")
sys.stdout.buffer.write(data)"""
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


READINESS_TIMEOUT_SECONDS = 30
BUILD_TIMEOUT_SECONDS = 600
LOG_TAIL_LINES = 20
LOG_TAIL_TIMEOUT_SECONDS = 15
"""Reading the tail of one log inside the sandbox, on the failure path of a start that already ended
— so the wait is short and stated rather than the 120s default."""
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

WEBSITE_DESCRIPTION = "Build a website in the sandbox."
START_SERVER_DESCRIPTION = (
    "Start a project in the background with automatic port cleanup and readiness detection. Omit "
    "command to serve a static folder. Pass command only for a project's own app server. Use this "
    "instead of bash — it polls until the port is listening and returns the route."
)
DEPLOY_WEBSITE_DESCRIPTION = (
    "Serve a website folder and host it at a permanent link the member can open. Pass the "
    "directory containing the built static output (index.html). Returns site_url — the deliverable "
    "— beside the sandbox-local url. Re-deploying the same site_name updates it behind that link."
)
PUBLISH_WEBSITE_DESCRIPTION = (
    "Publish a web app: install dependencies, serve the built output (and backend run_command if "
    "any) from the sandbox, and host it at a permanent link. Returns site_url — the deliverable — "
    "beside the sandbox-local url. Static files come from dist_path."
)
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
    "Bind a hosted site as your homepage — the page the portal shows for this agent. Pass the "
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


class WebsiteInput(BaseModel):
    run_command: str = Field(description="The build command to run in the project directory.")
    project_path: str | None = Field(
        default=None, description="Project directory to build in. Defaults to the workspace root."
    )
    user_description: str = Field(
        description="What you are building, in plain language for the activity timeline."
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
    user_description: str = Field(
        description="What you are starting up so they can see it, in plain language for the "
        "activity timeline."
    )

    @model_validator(mode="after")
    def validate_port(self) -> "StartServerInput":
        if self.command is not None and not self.command.strip():
            raise ValueError("command must contain a server command or be omitted for static files")
        if self.port is not None and not 0 < self.port < 65536:
            raise ValueError("port must be between 1 and 65535")
        return self


class DeployWebsiteInput(BaseModel):
    project_path: str = Field(
        description="Directory containing the built static output (index.html)."
    )
    site_name: str = Field(description="A name for the served site; it names the hosted link.")
    entry_point: str = Field(description="The entry file to serve, e.g. index.html.")
    visibility: Visibility | None = Field(default=None, description=VISIBILITY_DESCRIPTION)
    user_description: str = Field(
        description="Which site you are putting online, in plain language for the activity "
        "timeline."
    )


class DeployUfoApplicationInput(BaseModel):
    site_name: str = Field(description="A name for the application; it names the hosted link.")
    user_description: str = Field(
        description="Which application you are putting online, in plain language for the "
        "activity timeline."
    )


class QaUfoApplicationInput(BaseModel):
    user_description: str = Field(
        description="The application checks you are running, in plain language for the activity "
        "timeline."
    )


class PublishWebsiteInput(BaseModel):
    project_path: str = Field(description="The web app project directory.")
    dist_path: str = Field(description="Directory of the built static output to serve.")
    app_name: str = Field(description="A name for the published app; it names the hosted link.")
    visibility: Visibility | None = Field(default=None, description=VISIBILITY_DESCRIPTION)
    run_command: str | None = Field(
        default=None, description="Optional backend command to run alongside the static files."
    )
    install_command: str | None = Field(
        default=None, description="Optional command to install dependencies before serving."
    )
    user_description: str = Field(
        description="Which app you are publishing, in plain language for the activity timeline."
    )


class SetHomepageInput(BaseModel):
    site: str = Field(description="The site object name from the deploy result.")
    user_description: str = Field(
        description="Which site becomes the homepage, in plain language for the activity timeline."
    )


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


async def _free_log(ctx: ToolContext, log_path: str) -> None:
    """Leave the log's name holding nothing, so the server's redirect is the thing that creates it.

    A shell redirect follows a symlink and truncates what it points at, and the log's name is one a
    model chooses or predicts in a directory the agent writes. Creating the file here and
    redirecting onto it afterwards only narrows that — the two are separate commands, and a link
    replanted between them is what the `>` then opens. So the name is emptied instead, through the
    guard's own `O_NOFOLLOW` descent, which also makes `.tool-output` on the way; `set -C` then
    makes the redirect an `O_CREAT|O_EXCL` create, so a replant fails the start rather than steers
    it. A log an earlier run on this port left behind is this call's to clear.

    Noclobber covers the redirect alone. The port cleanup writes to `/dev/null` and `command` is the
    model's own, free to redirect where it likes; the background job keeps the setting it was forked
    with, so restoring it in the parent cannot reach the redirect already made."""
    result = await ctx.sandbox.python(LOG_CLEAR_PROG, log_path, WORKSPACE_DIR)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot clear {log_path}")


async def _stop_server(ctx: ToolContext, port: int) -> None:
    result = await ctx.sandbox.python(
        PORT_STOP_PROG, str(port), timeout_s=APPLICATION_AUDIT_STOP_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot free port {port}")


async def _serve(
    ctx: ToolContext, command: str, project: str, port: int, log_path: str
) -> dict[str, object]:
    await _free_log(ctx, log_path)
    await _stop_server(ctx, port)
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
    result = await ctx.sandbox.bash(
        f"cd {shlex.quote(project)}\n"
        f"set -C\n"
        f"nohup env PORT={port} {command} >{shlex.quote(log_path)} 2>&1 &\n"
        f"set +C\n"
        f"{readiness_probe}",
        timeout_s=READINESS_TIMEOUT_SECONDS + 5,
    )
    if result.exit_code != 0:
        tail = await ctx.sandbox.bash(
            f"tail -n {LOG_TAIL_LINES} {shlex.quote(log_path)} 2>/dev/null || true",
            timeout_s=LOG_TAIL_TIMEOUT_SECONDS,
        )
        if tail.stdout:
            raise RuntimeError(tail.stdout)
        if result.timed_out_after_s is not None:
            raise RuntimeError(
                f"the server never answered on port {port}: the sandbox stopped the start after "
                f"{result.timed_out_after_s}s and {log_path} holds nothing"
            )
        raise RuntimeError(result.stderr or result.stdout)
    return {"url": f"http://localhost:{port}", "port": port, "log": log_path}


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
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    sites = HostedSites(ctx.ext.store.workspace_id, ctx.ext.transaction)
    preview = await ctx.render_site_preview(name, port, PREVIEW_WIDTH, PREVIEW_HEIGHT)
    if preview is not None:
        await sites.set_preview(conversation_id, name, preview)
    await draw_from_page(ctx, sites, conversation_id, name, port)


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
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    creator_member_id = ctx.acting_member_id
    if creator_member_id is None:
        raise RuntimeError("a hosted site needs an owner: no member is acting on this turn")
    if visibility is not None and ctx.speaker_member_id is None:
        raise RuntimeError(VISIBILITY_NEEDS_A_SPEAKER)
    workspace_id = ctx.ext.store.workspace_id
    name = site_name(raw_name)
    site_url(ctx.public_base_url, workspace_id, ctx.sandbox.conversation_id, name)
    displaced = await HostedSites(workspace_id, ctx.ext.transaction).refuse_or_pass(
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
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    creator_member_id = ctx.acting_member_id
    if creator_member_id is None:
        raise RuntimeError("a hosted site needs an owner: no member is acting on this turn")
    if visibility is not None and ctx.speaker_member_id is None:
        raise RuntimeError(VISIBILITY_NEEDS_A_SPEAKER)
    workspace_id = ctx.ext.store.workspace_id
    name = site_name(raw_name)
    serving = ctx.sandbox.conversation_id
    link = site_url(ctx.public_base_url, workspace_id, serving, name)
    site = await HostedSites(workspace_id, ctx.ext.transaction).register(
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
        "visibility": effective_visibility(site, await ctx.ext.agent_visibilities()),
        "site": site_object_name(site.conversation_id, site.name),
        "site_url": link,
    }


async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult:
    project = workspace_path(args.project_path or WORKSPACE_DIR)
    result = await ctx.sandbox.bash(
        f"cd {shlex.quote(project)} && {args.run_command}", timeout_s=BUILD_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or result.stdout)
    listing = await ctx.sandbox.bash(f"ls -1A {shlex.quote(project)}")
    files = [name for name in listing.stdout.splitlines() if name]
    return _json_result({"project_path": project, "files": files})


async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult:
    port = args.port or START_SERVER_PORT
    project = workspace_path(args.project_path)
    application_builder = ctx.turn.subagent_profile == APPLICATION_BUILDER_NAME
    if application_builder and project != APPLICATION_SCAFFOLD_PATH:
        raise RuntimeError(
            f"ufo application preview project_path must be {APPLICATION_SCAFFOLD_PATH}, "
            f"not {project}"
        )
    if application_builder and args.command is not None:
        raise RuntimeError("ufo application preview does not accept a command")
    log_path = workspace_path(args.log_file or SERVER_LOG.format(port=port))
    command = args.command or f"python3 -m http.server {port} --bind 0.0.0.0"
    served = await _serve(ctx, command, project, port, log_path)
    if application_builder:
        served["url"] = f"{served['url']}/preview.html"
    return _json_result({**served, "project_path": project})


async def _application_audit_attempts(ctx: ToolContext) -> int:
    if ctx.ext is None:
        raise RuntimeError("the application audit dispatched without its extension context")
    stored = await ctx.ext.store.get(APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id))
    if stored is None:
        return 0
    if type(stored) is not int:
        raise RuntimeError("application audit attempt count is not an integer")
    return stored


async def _application_audit_feedback(
    ctx: ToolContext, issues: tuple[ApplicationAuditIssue, ...], attempts: int
) -> ApplicationAuditFeedback:
    if ctx.ext is None:
        raise RuntimeError("the application audit dispatched without its extension context")
    used = attempts + 1
    await ctx.ext.store.put(APPLICATION_AUDIT_ATTEMPT_KEY.format(turn_id=ctx.turn.id), used)
    feedback = ApplicationAuditFeedback(
        attempt=used,
        attempts_remaining=APPLICATION_AUDIT_MAX_ATTEMPTS - used,
        issues=issues,
    )
    return feedback


async def _audit_builder_application(
    ctx: ToolContext, project: str
) -> ApplicationAuditReport | ApplicationAuditFeedback:
    attempts = await _application_audit_attempts(ctx)
    if attempts >= APPLICATION_AUDIT_MAX_ATTEMPTS:
        raise RuntimeError("Application audit stopped after two failed product audits.")
    root = f"{TOOL_OUTPUT_DIR}/application-audit/{ctx.turn.id}"
    script_path = f"{root}.cjs"
    report_path = f"{root}.json"
    light_path = f"{root}-light.png"
    dark_path = f"{root}-dark.png"
    interactive_path = f"{root}-interactive.html"
    static_path = f"{root}-static.html"
    await ctx.sandbox.write_file(script_path, APPLICATION_AUDIT_SCRIPT)
    port = (
        APPLICATION_AUDIT_PORT_FLOOR + ctx.sandbox.conversation_id.int % APPLICATION_AUDIT_PORT_SPAN
    )
    command = f"python3 -m http.server {port} --bind 0.0.0.0"
    await _serve(ctx, command, project, port, f"{root}.log")
    try:
        run = await ctx.sandbox.sh(
            'node "$1" "$2" "$3" "$4" "$5" "$6" "$7"',
            script_path,
            f"http://localhost:{port}/preview.html",
            report_path,
            light_path,
            dark_path,
            interactive_path,
            static_path,
            timeout_s=APPLICATION_AUDIT_TIMEOUT_SECONDS,
        )
        if run.exit_code != 0:
            detail = (run.stderr or run.stdout or "audit returned no error").strip()[:400]
            return await _application_audit_feedback(
                ctx,
                (
                    ApplicationAuditIssue(
                        code="audit_run",
                        message=f"Run the browser audit successfully: {detail}",
                    ),
                ),
                attempts,
            )
        report_read = await ctx.sandbox.python(
            APPLICATION_AUDIT_REPORT_READ,
            report_path,
            str(APPLICATION_AUDIT_REPORT_MAX_BYTES),
        )
        if report_read.exit_code != 0:
            detail = (report_read.stderr or report_read.stdout or "audit report is absent").strip()[
                :400
            ]
            return await _application_audit_feedback(
                ctx,
                (
                    ApplicationAuditIssue(
                        code="audit_run",
                        message=f"Produce a readable browser audit report: {detail}",
                    ),
                ),
                attempts,
            )
        try:
            report = ApplicationAuditReport.model_validate_json(report_read.stdout)
            if ctx.ext is None:
                raise RuntimeError("the application audit dispatched without its extension context")
            if ctx.turn.parent_turn_id is None:
                raise RuntimeError("the application audit dispatched without its parent turn")
            stored_contract = await ctx.ext.store.get(
                APPLICATION_AUDIT_TURN_CONTRACT_KEY.format(turn_id=ctx.turn.parent_turn_id)
            )
            contract = ApplicationAuditContract.model_validate(stored_contract or {})
        except ValueError as error:
            return await _application_audit_feedback(
                ctx,
                (
                    ApplicationAuditIssue(
                        code="audit_run",
                        message=f"Produce a valid browser audit report: {str(error)[:400]}",
                    ),
                ),
                attempts,
            )
        verdict = audit_application(report, contract)
        if not verdict.passed:
            return await _application_audit_feedback(ctx, verdict.issues, attempts)
        return report
    finally:
        await _stop_server(ctx, port)


async def _application_source_sha256(ctx: ToolContext) -> str:
    source = await ctx.sandbox.python(APPLICATION_SOURCE_READ, APPLICATION_SOURCE_PATH)
    if source.exit_code != 0:
        raise RuntimeError(source.stderr or "app.tsx could not be read")
    return sha256(source.stdout.encode()).hexdigest()


async def _require_current_application_qa(ctx: ToolContext) -> ApplicationQaProof:
    if ctx.ext is None:
        raise RuntimeError("product QA dispatched without its extension context")
    stored = await ctx.ext.store.get(APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id))
    if stored is None:
        raise RuntimeError(APPLICATION_BUILDER_DEPLOY_GUARD_REASON)
    try:
        proof = ApplicationQaProof.model_validate(stored)
    except ValueError as error:
        raise RuntimeError("application builder QA proof is invalid") from error
    if await _application_source_sha256(ctx) != proof.source_sha256:
        raise RuntimeError("app.tsx changed after product QA passed")
    return proof


async def qa_ufo_application(ctx: ToolContext, args: QaUfoApplicationInput) -> ToolResult:
    if ctx.turn.subagent_profile != APPLICATION_BUILDER_NAME:
        raise RuntimeError("product QA is available only to the ufo application builder")
    if ctx.ext is None:
        raise RuntimeError("product QA dispatched without its extension context")
    call_key = APPLICATION_BUILDER_QA_CALL_KEY.format(turn_id=ctx.turn.id)
    stored_calls = await ctx.ext.store.get(call_key)
    if stored_calls is None:
        calls = 0
    elif type(stored_calls) is int:
        calls = stored_calls
    else:
        raise RuntimeError("application product QA call count is not an integer")
    if calls >= APPLICATION_BUILDER_QA_MAX_CALLS:
        raise RuntimeError(
            f"Application audit stopped after {APPLICATION_BUILDER_QA_MAX_CALLS} product audits."
        )
    calls += 1
    await ctx.ext.store.put(call_key, calls)
    audit = await _audit_builder_application(ctx, APPLICATION_SCAFFOLD_PATH)
    match audit:
        case ApplicationAuditFeedback():
            return _json_result(audit.model_dump())
        case ApplicationAuditReport():
            report = audit
    result = ApplicationProductQaResult(
        views_checked=tuple(f"{view.scheme} {view.width}px" for view in report.views),
        controls_checked=tuple(control.name for control in report.interaction.controls)[
            :MAX_PRODUCT_QA_CONTROLS
        ],
        interactions_verified=tuple(control.name for control in report.interaction.successes)[
            :MAX_PRODUCT_QA_CONTROLS
        ],
    )
    source_sha256 = await _application_source_sha256(ctx)
    await ctx.ext.store.put(
        APPLICATION_BUILDER_QA_PROOF_KEY.format(turn_id=ctx.turn.id),
        ApplicationQaProof(
            source_sha256=source_sha256,
            browser_batches=calls,
        ).model_dump(),
    )
    return _json_result(result.model_dump())


async def deploy_ufo_application(ctx: ToolContext, args: DeployUfoApplicationInput) -> ToolResult:
    if ctx.turn.subagent_profile != APPLICATION_BUILDER_NAME:
        raise RuntimeError("application deploy is available only to the ufo application builder")
    return await deploy_website(
        ctx,
        DeployWebsiteInput(
            project_path=APPLICATION_SCAFFOLD_PATH,
            site_name=args.site_name,
            entry_point="index.html",
            user_description=args.user_description,
        ),
    )


async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult:
    source_project = workspace_path(args.project_path)
    if (
        ctx.turn.subagent_profile == APPLICATION_BUILDER_NAME
        and source_project != APPLICATION_SCAFFOLD_PATH
    ):
        raise RuntimeError(
            f"ufo application deploy project_path must be {APPLICATION_SCAFFOLD_PATH}, "
            f"not {source_project}"
        )
    if ctx.turn.subagent_profile == APPLICATION_BUILDER_NAME:
        await _require_current_application_qa(ctx)
    conversation = ctx.sandbox.conversation_id
    port = serve_port(conversation)
    bound = await _agent_homepage(ctx)
    slug = site_name(args.site_name)
    if (
        bound is not None
        and slug in {bound.name, site_object_name(bound.conversation_id, bound.name)}
        and bound.conversation_id != conversation
        and await _sites_registry(ctx).read(conversation, slug) is None
    ):
        return await _redeploy_homepage(ctx, args, bound, port)
    name, _displaced = await _refuse_before_serving(ctx, args.site_name, port, args.visibility)
    project, listing = await _served_directory(ctx, source_project)
    manifest = await _promote_source(ctx, project, conversation, name, listing)
    command = f"python3 -m http.server {port} --bind 0.0.0.0"
    served = await _serve(ctx, command, project, port, DEPLOY_LOG.format(port=port))
    hosted = await _host(ctx, name, port, args.visibility, manifest)
    await _illustrate(ctx, name, port, conversation)
    return _json_result({**served, **hosted, "entry_point": args.entry_point})


async def _served_directory(
    ctx: ToolContext, project: str
) -> tuple[str, dict[str, dict[str, object]]]:
    """The directory whose bytes are hosted, with its listing: a page project's build output, or the
    directory it was handed.

    A directory holding `app.tsx` is source, not a site — the app pages an agent edits arrive that
    way, the one file to change and the page that names it, mounted by the skill it loaded — so this
    writes the deploy's config and kit beside it, builds it here, and hosts the `dist` that build
    wrote. No browser runs TSX, so a directory naming one could never have been served as it stands,
    and building it is the only reading of it that works.

    The agent never runs the build itself. A page deployed as its own source is the one mistake in
    this flow, and a tool that always builds rules it out instead of describing it. The build's
    output carries its own source, so a later read of the site starts from a project again."""
    listing = await _source_listing(ctx, project)
    if PROJECT_SOURCE not in listing:
        return project, listing
    await ctx.sandbox.write_file(f"{project}/{PROJECT_CONFIG}", PROJECT_CONFIG_BYTES)
    await unpack_page_kit(ctx, project)
    built = await ctx.sandbox.sh(
        f"cd {shlex.quote(project)} && vite build", timeout_s=BUILD_TIMEOUT_SECONDS
    )
    if built.exit_code != 0:
        raise RuntimeError(built.stderr.strip() or built.stdout.strip() or "the page did not build")
    page = f"{project}/{PROJECT_DIST}"
    return page, await _source_listing(ctx, page)


async def _agent_homepage(ctx: ToolContext) -> HostedSite | None:
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    return await _sites_registry(ctx).homepage(ctx.turn.agent_id)


def _sites_registry(ctx: ToolContext) -> HostedSites:
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    return HostedSites(ctx.ext.store.workspace_id, ctx.ext.transaction)


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
    requested_by_speaker = ctx.speaker_member_id is not None
    if (
        not requested_by_speaker
        and ctx.turn.subagent_profile == APPLICATION_BUILDER_NAME
        and ctx.turn.parent_turn_id is not None
        and ctx.acting_member_id is not None
        and ctx.ext is not None
    ):
        requester = await ctx.ext.store.get(
            APPLICATION_BUILDER_REDEPLOY_KEY.format(turn_id=ctx.turn.parent_turn_id)
        )
        requested_by_speaker = requester == str(ctx.acting_member_id)
    if not requested_by_speaker:
        raise RuntimeError(HOMEPAGE_REDEPLOY_NEEDS_A_SPEAKER)
    if args.visibility is not None:
        raise ValueError(HOMEPAGE_KEEPS_THE_AGENTS_VISIBILITY)
    if ctx.acting_member_id is None:
        raise RuntimeError("a hosted site needs an owner: no member is acting on this turn")
    sites = _sites_registry(ctx)
    displaced = await sites.refuse_or_pass(
        ctx.sandbox.conversation_id,
        bound.name,
        scratch_port,
        ctx.acting_member_id,
        None,
        True,
    )
    source_project = workspace_path(args.project_path)
    project, listing = await _served_directory(ctx, source_project)
    manifest = await _promote_source(ctx, project, bound.conversation_id, bound.name, listing)
    command = f"python3 -m http.server {scratch_port} --bind 0.0.0.0"
    served = await _serve(ctx, command, project, scratch_port, DEPLOY_LOG.format(port=scratch_port))
    updated = await sites.redeploy(bound.conversation_id, bound.name, manifest)
    if updated is None:
        raise RuntimeError("the homepage was unhosted while it was being redeployed")
    if displaced is not None:
        await sites.unregister(displaced.conversation_id, displaced.name)
    await _illustrate(ctx, bound.name, scratch_port, bound.conversation_id)
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    return _json_result(
        {
            **served,
            "site_name": updated.name,
            "visibility": await ctx.agent_visibility(),
            "site": site_object_name(updated.conversation_id, updated.name),
            "site_url": site_url(
                ctx.public_base_url,
                ctx.ext.store.workspace_id,
                updated.conversation_id,
                updated.name,
            ),
            "entry_point": args.entry_point,
        }
    )


async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult:
    conversation = ctx.sandbox.conversation_id
    port = serve_port(conversation)
    name, _displaced = await _refuse_before_serving(ctx, args.app_name, port, args.visibility)
    if args.install_command:
        install = await ctx.sandbox.bash(
            f"cd {shlex.quote(workspace_path(args.project_path))} && {args.install_command}",
            timeout_s=BUILD_TIMEOUT_SECONDS,
        )
        if install.exit_code != 0:
            raise RuntimeError(install.stderr or install.stdout)
    command = args.run_command or f"python3 -m http.server {port} --bind 0.0.0.0"
    project = workspace_path(args.project_path if args.run_command else args.dist_path)
    publish_log = PUBLISH_LOG.format(port=port)
    served = await _serve(ctx, command, project, port, publish_log)
    hosted = await _host(ctx, name, port, args.visibility, None)
    await _illustrate(ctx, name, port, conversation)
    return _json_result({**served, **hosted})


async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult:
    """Bind one hosted site as the acting agent's homepage.

    Binding writes nothing but the pointer, yet it moves the site's viewers onto the agent's
    audience — every member for a workspace agent, its owner and admins for a private one — so
    the bind answers to the module's disclosure rule the way a visibility change does. It is the
    creator's act alone: another member's site is refused whatever its visibility, since a bind
    would widen a private site or re-gate a shared one out from under its creator. And a standing
    site needs the creator speaking — without a live speaker the bind reaches only a site this
    same turn deployed, whose gate is the room's default rather than a choice anyone made — which
    is exactly the seed's deploy-and-bind shape, so seeding stays speakerless-safe while a
    scheduled turn can never re-gate what a member left standing."""
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    workspace_id = ctx.ext.store.workspace_id
    sites = HostedSites(workspace_id, ctx.ext.transaction)
    named = {site_object_name(site.conversation_id, site.name): site for site in await sites.all()}
    site = named.get(args.site)
    if site is None:
        raise ValueError(
            f"no hosted site is named {args.site!r}: deploy the site and bind the name its "
            "result carries"
        )
    if ctx.acting_member_id != site.creator_member_id:
        raise ValueError(HOMEPAGE_NEEDS_ITS_CREATOR.format(site=args.site))
    deployed_this_turn = (
        site.conversation_id == ctx.sandbox.conversation_id
        and site.created_at >= ctx.turn.created_at
    )
    if ctx.speaker_member_id is None and not deployed_this_turn:
        raise RuntimeError(HOMEPAGE_BIND_NEEDS_A_SPEAKER)
    bound = await sites.set_homepage(ctx.turn.agent_id, site.conversation_id, site.name)
    if bound is None:
        raise ValueError(f"site {args.site!r} was unhosted while it was being bound")
    return _json_result(
        {
            "site": args.site,
            "site_url": site_url(
                ctx.public_base_url, workspace_id, bound.conversation_id, bound.name
            ),
            "visibility": await ctx.agent_visibility(),
            "homepage_agent": str(ctx.turn.agent_id),
        }
    )


SITES_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=WEBSITE_TOOL,
        description=WEBSITE_DESCRIPTION,
        input_model=WebsiteInput,
        handler=website,
    ),
    ToolDef(
        name=START_SERVER_TOOL,
        description=START_SERVER_DESCRIPTION,
        input_model=StartServerInput,
        handler=start_server,
    ),
    ToolDef(
        name=APPLICATION_BUILDER_QA_TOOL,
        description=(
            "Run the complete deterministic ufo application product audit against the fixed "
            "scaffold. It checks the framed app in four views, accessible controls, visible state "
            "changes, contrast, fit, clipping, console errors, required facts, and first-screen "
            "placement. It returns passed evidence or one bounded repair batch."
        ),
        input_model=QaUfoApplicationInput,
        handler=qa_ufo_application,
        profile_only=True,
    ),
    ToolDef(
        name=APPLICATION_BUILDER_DEPLOY_TOOL,
        description=(
            "Build the fixed ufo application scaffold and host it at a permanent link after "
            "product QA passes."
        ),
        input_model=DeployUfoApplicationInput,
        handler=deploy_ufo_application,
        side_effecting=True,
        profile_only=True,
    ),
    ToolDef(
        name=DEPLOY_WEBSITE_TOOL,
        description=DEPLOY_WEBSITE_DESCRIPTION,
        input_model=DeployWebsiteInput,
        handler=deploy_website,
    ),
    ToolDef(
        name=PUBLISH_WEBSITE_TOOL,
        description=PUBLISH_WEBSITE_DESCRIPTION,
        input_model=PublishWebsiteInput,
        handler=publish_website,
    ),
    ToolDef(
        name=SET_HOMEPAGE_TOOL,
        description=SET_HOMEPAGE_DESCRIPTION,
        input_model=SetHomepageInput,
        handler=set_homepage,
    ),
)

SITES_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in SITES_TOOLS)

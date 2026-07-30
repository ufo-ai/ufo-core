"""The website tools: build a site, serve one in the sandbox with port cleanup and a readiness
probe, and host the served port at a permanent link.

Each tool runs through `ctx.sandbox`, so the container's mount and egress scoping hold. `website`
runs a build command and lists what it produced. `start_server`, `deploy_website`, and
`publish_website` bring a server up in the background: they free the port, launch the command under
`nohup`, and poll until the port is listening before returning — so the tool returns a running,
reachable server rather than a race. The served URL is `http://localhost:<port>` inside the sandbox,
which the browser tools and js_repl reach to validate the page.

`deploy_website` and `publish_website` then register that port as a hosted site and return its
`site_url` — the frame a member opens, gated on the site's visibility. Hosting a site is registering
the port the readiness probe just proved, so nothing moves: a re-deploy of the same name updates the
port in place and the link never changes. Visibility defaults from the conversation's audience; an
explicit argument overrides that default, but only for the site's creator and only on a turn with a
live speaker, because choosing who can open a site is a disclosure act. `start_server` registers
nothing, since a scratch server is not a deliverable."""

import json
import shlex

from pydantic import BaseModel, Field, model_validator

from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.objects import site_object_name
from ufo_ext_sites.store import HostedSites, Visibility, site_name
from ufo_ext_sites.surface import site_url

WEBSITE_TOOL = "website"
START_SERVER_TOOL = "start_server"
DEPLOY_WEBSITE_TOOL = "deploy_website"
PUBLISH_WEBSITE_TOOL = "publish_website"

WORKSPACE_DIR = "/workspace"
APP_SERVE_PORT = 8000
START_SERVER_PORT = 5000
READINESS_TIMEOUT_SECONDS = 30
BUILD_TIMEOUT_SECONDS = 600

WEBSITE_DESCRIPTION = "Build a website in the sandbox."
START_SERVER_DESCRIPTION = (
    "Start a server in the background with automatic port cleanup and readiness detection. Use "
    "this instead of bash for servers — it polls until the port is listening and returns the route."
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
SUBAGENT_CANNOT_HOST = (
    "a subagent turn cannot host a site: its sandbox and workspace are its own and a link into "
    "them dies with them, and the parent cannot reach these files either. Bring the site up with "
    "start_server, validate it, and share_file the built output — whatever the member is meant to "
    "open is built in the member's own conversation."
)
VISIBILITY_DESCRIPTION = (
    "Who may open the hosted link: private (you alone), workspace (any member), or public (anyone "
    "with the link). Omit unless the member asked — a new site defaults from where it was built, "
    "and an existing one keeps the visibility it has."
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
    command: str = Field(description="The server command to run in the background.")
    project_path: str = Field(description="The project directory to run the command in.")
    port: int | None = Field(
        default=None, description="Port the server listens on; polled until it is reachable."
    )
    log_file: str | None = Field(
        default=None, description="File to capture the server's stdout/stderr."
    )
    user_description: str = Field(
        description="What you are starting up so they can see it, in plain language for the "
        "activity timeline."
    )

    @model_validator(mode="after")
    def validate_port(self) -> "StartServerInput":
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


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


async def _serve(
    ctx: ToolContext, command: str, project: str, port: int, log: str
) -> dict[str, object]:
    port_cleanup = (
        f"(fuser -k {port}/tcp 2>/dev/null; "
        f"lsof -ti tcp:{port} 2>/dev/null | xargs -r kill 2>/dev/null) || true; sleep 1"
    )
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
        f"cd {shlex.quote(project)} && {port_cleanup}\n"
        f"nohup {command} >{shlex.quote(log)} 2>&1 &\n"
        f"{readiness_probe}",
        timeout_s=READINESS_TIMEOUT_SECONDS + 5,
    )
    if result.exit_code != 0:
        tail = await ctx.sandbox.bash(f"tail -n 20 {shlex.quote(log)} 2>/dev/null || true")
        raise RuntimeError(tail.stdout or result.stderr or result.stdout)
    return {"url": f"http://localhost:{port}", "port": port, "log": log}


async def _host(
    ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None
) -> dict[str, object]:
    """Register the running port as a hosted site and describe the link it now answers on. Nothing
    is written until the link exists and every refusal has fired: a site needs an owner, so a turn
    with no acting member cannot host one; a turn acting for a member hosts as that member, whoever
    queued it, but only a turn with a live speaker may name a `visibility` — the column is a
    disclosure decision and a background turn never makes one.

    A subagent turn cannot host at all. The link resolves a conversation to the sandbox serving it,
    and a subagent runs in its own conversation with its own sandbox and its own workspace, derived
    from the one parent turn that spawned it — so a site registered there dies with that sandbox and
    a rebuild mints a different link. Hosting belongs to the conversation the member is in."""
    if ctx.ext is None:
        raise RuntimeError("the website tools dispatched without their ExtensionContext")
    if ctx.turn.subagent_profile is not None:
        raise RuntimeError(SUBAGENT_CANNOT_HOST)
    creator_member_id = ctx.acting_member_id
    if creator_member_id is None:
        raise RuntimeError("a hosted site needs an owner: no member is acting on this turn")
    if visibility is not None and ctx.speaker_member_id is None:
        raise RuntimeError(VISIBILITY_NEEDS_A_SPEAKER)
    workspace_id = ctx.ext.store.workspace_id
    name = site_name(raw_name)
    link = site_url(ctx.public_base_url, workspace_id, ctx.turn.conversation_id, name)
    site = await HostedSites(workspace_id, ctx.ext.transaction).register(
        ctx.turn.conversation_id,
        name,
        port,
        creator_member_id,
        ctx.speaker_member_id,
        visibility,
        ctx.audience,
    )
    return {
        "site_name": site.name,
        "visibility": site.visibility,
        "site": site_object_name(site.conversation_id, site.name),
        "site_url": link,
    }


async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult:
    project = args.project_path or WORKSPACE_DIR
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
    log = args.log_file or f"/tmp/server-{port}.log"
    served = await _serve(ctx, args.command, args.project_path, port, log)
    return _json_result({**served, "project_path": args.project_path})


async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult:
    command = f"python3 -m http.server {APP_SERVE_PORT} --bind 0.0.0.0"
    log = f"/tmp/deploy-{APP_SERVE_PORT}.log"
    served = await _serve(ctx, command, args.project_path, APP_SERVE_PORT, log)
    hosted = await _host(ctx, args.site_name, APP_SERVE_PORT, args.visibility)
    return _json_result({**served, **hosted, "entry_point": args.entry_point})


async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult:
    if args.install_command:
        install = await ctx.sandbox.bash(
            f"cd {shlex.quote(args.project_path)} && {args.install_command}",
            timeout_s=BUILD_TIMEOUT_SECONDS,
        )
        if install.exit_code != 0:
            raise RuntimeError(install.stderr or install.stdout)
    command = args.run_command or f"python3 -m http.server {APP_SERVE_PORT} --bind 0.0.0.0"
    project = args.project_path if args.run_command else args.dist_path
    log = f"/tmp/publish-{APP_SERVE_PORT}.log"
    served = await _serve(ctx, command, project, APP_SERVE_PORT, log)
    hosted = await _host(ctx, args.app_name, APP_SERVE_PORT, args.visibility)
    return _json_result({**served, **hosted})


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
)

SITES_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for tool in SITES_TOOLS)
HOSTING_TOOL_NAMES: tuple[str, ...] = (DEPLOY_WEBSITE_TOOL, PUBLISH_WEBSITE_TOOL)
BUILD_ONLY_TOOL_NAMES: tuple[str, ...] = tuple(
    name for name in SITES_TOOL_NAMES if name not in HOSTING_TOOL_NAMES
)

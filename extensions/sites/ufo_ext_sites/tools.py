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

`deploy_website` and `publish_website` then register that port as a hosted site and return its
`site_url` — the frame a member opens, gated on the site's visibility. Hosting a site is registering
the port the readiness probe just proved, so nothing moves: a re-deploy of the same name updates the
port in place and the link never changes. Visibility defaults from the conversation's audience; an
explicit argument overrides that default, but only for the site's creator and only on a turn with a
live speaker, because choosing who can open a site is a disclosure act. `start_server` registers
nothing, since a scratch server is not a deliverable.

`set_homepage` binds one hosted site as the acting agent's homepage — the pointer the portal reads.
The frame gates a homepage's viewers on the agent's visibility rather than the site's, so who may
open it is decided where the agent's audience is decided — the agent object's `visibility` — and
the bind itself is the re-gating act: it takes the site's creator acting, and a live speaker
unless the same turn deployed the site, the seed's deploy-and-bind shape."""

import json
import shlex

from pydantic import BaseModel, Field, model_validator

from ufo.sdk.sandbox import TOOL_OUTPUT_DIR, WORKSPACE_DIR, workspace_path
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.objects import site_object_name
from ufo_ext_sites.store import HostedSites, Visibility, site_name
from ufo_ext_sites.surface import site_url

WEBSITE_TOOL = "website"
START_SERVER_TOOL = "start_server"
DEPLOY_WEBSITE_TOOL = "deploy_website"
PUBLISH_WEBSITE_TOOL = "publish_website"
SET_HOMEPAGE_TOOL = "set_homepage"

APP_SERVE_PORT = 8000
START_SERVER_PORT = 5000
READINESS_TIMEOUT_SECONDS = 30
BUILD_TIMEOUT_SECONDS = 600
LOG_TAIL_LINES = 20
LOG_TAIL_TIMEOUT_SECONDS = 15
"""Reading the tail of one log inside the sandbox, on the failure path of a start that already ended
— so the wait is short and stated rather than the 120s default."""
SERVER_LOG = f"{TOOL_OUTPUT_DIR}/server-{{port}}.log"
DEPLOY_LOG = f"{TOOL_OUTPUT_DIR}/deploy-{{port}}.log"
PUBLISH_LOG = f"{TOOL_OUTPUT_DIR}/publish-{{port}}.log"
LOG_CLEAR_PROG = """
import sys
from containment import ContainmentError, contained_file

try:
    with contained_file(sys.argv[1], sys.argv[2], create_parent=True) as target:
        target.unlink()
except ContainmentError as error:
    raise SystemExit(str(error))
"""

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
        default=None,
        description="File in the workspace to capture the server's stdout/stderr.",
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


class SetHomepageInput(BaseModel):
    site: str = Field(description="The site object name from the deploy result.")
    user_description: str = Field(
        description="Which site becomes the homepage, in plain language for the activity timeline."
    )


def _json_result(payload: dict[str, object]) -> ToolResult:
    return ToolResult(content=(TextContent(text=json.dumps(payload)),))


async def _free_log(ctx: ToolContext, log: str) -> None:
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
    result = await ctx.sandbox.python(LOG_CLEAR_PROG, log, WORKSPACE_DIR)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or f"cannot clear {log}")


async def _serve(
    ctx: ToolContext, command: str, project: str, port: int, log: str
) -> dict[str, object]:
    await _free_log(ctx, log)
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
        f"set -C\n"
        f"nohup {command} >{shlex.quote(log)} 2>&1 &\n"
        f"set +C\n"
        f"{readiness_probe}",
        timeout_s=READINESS_TIMEOUT_SECONDS + 5,
    )
    if result.exit_code != 0:
        tail = await ctx.sandbox.bash(
            f"tail -n {LOG_TAIL_LINES} {shlex.quote(log)} 2>/dev/null || true",
            timeout_s=LOG_TAIL_TIMEOUT_SECONDS,
        )
        if tail.stdout:
            raise RuntimeError(tail.stdout)
        if result.timed_out_after_s is not None:
            raise RuntimeError(
                f"the server never answered on port {port}: the sandbox stopped the start after "
                f"{result.timed_out_after_s}s and {log} holds nothing"
            )
        raise RuntimeError(result.stderr or result.stdout)
    return {"url": f"http://localhost:{port}", "port": port, "log": log}


async def _refuse_before_serving(
    ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None
) -> str:
    """Raise anything hosting would raise, while the member's site is still up, and answer with the
    slugged name.

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
    site_url(ctx.public_base_url, workspace_id, ctx.sandbox.handle.conversation_id, name)
    await HostedSites(workspace_id, ctx.ext.transaction).refuse_or_pass(
        ctx.sandbox.handle.conversation_id,
        name,
        port,
        creator_member_id,
        visibility,
        ctx.speaker_member_id is not None,
    )
    return name


async def _host(
    ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None
) -> dict[str, object]:
    """Register the port a deploy just left serving as a hosted site, and describe the link it
    answers on. The refusals ran in `_refuse_before_serving`; `register` asks the same set again
    here, since this is the write and a concurrent deploy may have moved since.

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
    serving = ctx.sandbox.handle.conversation_id
    link = site_url(ctx.public_base_url, workspace_id, serving, name)
    site = await HostedSites(workspace_id, ctx.ext.transaction).register(
        serving,
        name,
        port,
        creator_member_id,
        visibility,
        ctx.audience,
        ctx.speaker_member_id is not None,
    )
    return {
        "site_name": site.name,
        "visibility": site.visibility,
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
    log = workspace_path(args.log_file or SERVER_LOG.format(port=port))
    served = await _serve(ctx, args.command, project, port, log)
    return _json_result({**served, "project_path": project})


async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult:
    name = await _refuse_before_serving(ctx, args.site_name, APP_SERVE_PORT, args.visibility)
    command = f"python3 -m http.server {APP_SERVE_PORT} --bind 0.0.0.0"
    log = DEPLOY_LOG.format(port=APP_SERVE_PORT)
    project = workspace_path(args.project_path)
    served = await _serve(ctx, command, project, APP_SERVE_PORT, log)
    hosted = await _host(ctx, name, APP_SERVE_PORT, args.visibility)
    return _json_result({**served, **hosted, "entry_point": args.entry_point})


async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult:
    name = await _refuse_before_serving(ctx, args.app_name, APP_SERVE_PORT, args.visibility)
    if args.install_command:
        install = await ctx.sandbox.bash(
            f"cd {shlex.quote(workspace_path(args.project_path))} && {args.install_command}",
            timeout_s=BUILD_TIMEOUT_SECONDS,
        )
        if install.exit_code != 0:
            raise RuntimeError(install.stderr or install.stdout)
    command = args.run_command or f"python3 -m http.server {APP_SERVE_PORT} --bind 0.0.0.0"
    project = workspace_path(args.project_path if args.run_command else args.dist_path)
    log = PUBLISH_LOG.format(port=APP_SERVE_PORT)
    served = await _serve(ctx, command, project, APP_SERVE_PORT, log)
    hosted = await _host(ctx, name, APP_SERVE_PORT, args.visibility)
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
        site.conversation_id == ctx.sandbox.handle.conversation_id
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

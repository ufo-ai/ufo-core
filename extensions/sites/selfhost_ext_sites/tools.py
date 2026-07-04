"""The website tools: build a site, and serve one in the sandbox with port cleanup and a readiness
probe.

Each tool runs through `ctx.sandbox`, so the container's mount and egress scoping hold. `website`
runs a build command and lists what it produced. `start_server`, `deploy_website`, and
`publish_website` bring a server up in the background: they free the port, launch the command under
`nohup`, and poll until the port is listening before returning — so the tool returns a running,
reachable server rather than a race. The served URL is `http://localhost:<port>` inside the sandbox,
which the browser tools and js_repl reach to validate the page; the built files leave the sandbox
only through `share_file`."""

import json
import shlex

from pydantic import BaseModel, model_validator

from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

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
    "Serve a website folder from the workspace at a route the user can reach. Pass the directory "
    "containing the built static output (index.html). Re-deploying the same path updates the site."
)
PUBLISH_WEBSITE_DESCRIPTION = (
    "Publish a web app: install dependencies, serve the built output (and backend run_command if "
    "any) from the sandbox, and return its route. Static files come from dist_path."
)


class WebsiteInput(BaseModel):
    runCommand: str
    projectPath: str | None = None


class StartServerInput(BaseModel):
    command: str
    project_path: str
    port: int | None = None
    log_file: str | None = None

    @model_validator(mode="after")
    def validate_port(self) -> "StartServerInput":
        if self.port is not None and not 0 < self.port < 65536:
            raise ValueError("port must be between 1 and 65535")
        return self


class DeployWebsiteInput(BaseModel):
    project_path: str
    site_name: str
    entry_point: str
    should_validate: bool | None = None


class PublishWebsiteInput(BaseModel):
    project_path: str
    dist_path: str
    app_name: str
    run_command: str | None = None
    install_command: str | None = None


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


async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult:
    project = args.projectPath or WORKSPACE_DIR
    result = await ctx.sandbox.bash(
        f"cd {shlex.quote(project)} && {args.runCommand}", timeout_s=BUILD_TIMEOUT_SECONDS
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or result.stdout)
    listing = await ctx.sandbox.bash(f"ls -1A {shlex.quote(project)}")
    files = [name for name in listing.stdout.splitlines() if name]
    return _json_result({"projectPath": project, "files": files})


async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult:
    port = args.port or START_SERVER_PORT
    log = args.log_file or f"/tmp/server-{port}.log"
    served = await _serve(ctx, args.command, args.project_path, port, log)
    return _json_result({**served, "projectPath": args.project_path})


async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult:
    command = f"python3 -m http.server {APP_SERVE_PORT} --bind 0.0.0.0"
    log = f"/tmp/deploy-{APP_SERVE_PORT}.log"
    served = await _serve(ctx, command, args.project_path, APP_SERVE_PORT, log)
    return _json_result({**served, "site_name": args.site_name, "entry_point": args.entry_point})


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
    return _json_result({**served, "app_name": args.app_name})


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

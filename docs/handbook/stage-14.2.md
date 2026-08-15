# Website building and hosting extension  `stage-14.2`

This stage is a behind-the-scenes support package for website work. It gives the system a safe way to create a site, run it in a local sandbox, and share it through a hosted link. A sandbox is an isolated workspace, like a test kitchen, where the website can run without touching the rest of the system.

The __init__.py file is the package label. It tells Python that ufo_ext_sites is a group of importable code files. It does not do any work by itself, but it lets the rest of the system find the website tools.

The real machinery is in tools.py. It defines the actions an agent can call: build the website files, start a local web server to preview them, and connect that server to an outside link. It also checks file paths, port numbers, logs, and sharing settings so the system exposes only what it should. Together, these parts turn website creation into a controlled, repeatable workflow.

## Files in this stage

### Website extension package
Package setup and tool definitions for building, serving, and safely hosting sandbox websites.

### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a directory with an `__init__.py` file is treated as a package, which means code elsewhere can import things from that directory using normal Python import paths. Think of it like putting a label on a folder so the rest of the project knows, “this folder contains Python code you may refer to by name.”

Because the file is empty, it does not run setup code, expose shortcuts, or change how the site extension works. Its value is structural: without it, some Python environments or tooling might not recognize `extensions/sites/ufo_ext_sites` as an importable package, which could make imports fail or make discovery tools overlook this extension area.


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `request handling`

This file is the bridge between “I built a website” and “there is a link someone can open.” It gives the system four website-related tools: build a site, start a temporary server, deploy a static folder, and publish a fuller app. Without this file, an agent could still run shell commands, but it would have to guess when a server is ready, might overwrite unsafe log paths, might leave old processes on a port, and would not register a stable hosted link.

The file keeps everything inside the workspace by passing project and log paths through a workspace path helper. That matters because commands run in a sandbox, and paths named by a model should not accidentally point at arbitrary host files. It also treats log files carefully: before redirecting server output, it clears the log name through a containment check, then starts the server with shell “noclobber” behavior so a planted link cannot trick the redirect into overwriting another file.

The serving flow is like preparing a shop before opening the door: clear the old occupant from the port, start the server in the background, then repeatedly knock on the port until it answers. Only then does the tool return success. Deploying and publishing add one more step: they register the proven-live port as a hosted site with a stable URL and visibility rules such as private or workspace-only.

#### Function details

##### `StartServerInput.validate_port`  (lines 118–121)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents impossible or unsafe values before the tool tries to start a server.

**Data flow**: It reads the port value from the start-server input. If no port was supplied, it leaves the input alone so the default can be used later. If a port was supplied, it must be between 1 and 65535; otherwise the input is rejected with an error.

**Call relations**: This runs automatically when a StartServerInput object is validated. It protects the later start_server flow, which passes the port to the shared serving helper.


##### `DeployWebsiteInput.refuse_public_visibility`  (lines 138–141)

```
def refuse_public_visibility(cls, value: str | None) -> str | None
```

**Purpose**: This rejects attempts to deploy a site with public visibility. The tool only allows private or workspace visibility for new site hosting.

**Data flow**: It receives the raw visibility value before normal validation. If the value is exactly public, it raises an error explaining that public sharing is unavailable. Any other allowed value, or no value, passes through unchanged.

**Call relations**: This runs as part of validating DeployWebsiteInput before deploy_website begins. It prevents deploy_website from reaching the hosting step with a sharing setting the site store should not accept.


##### `PublishWebsiteInput.refuse_public_visibility`  (lines 161–164)

```
def refuse_public_visibility(cls, value: str | None) -> str | None
```

**Purpose**: This rejects attempts to publish an app with public visibility. It keeps published apps limited to supported visibility choices.

**Data flow**: It receives the raw visibility value from the publish input. If that value is public, it stops validation with an error. If it is private, workspace, or absent, it returns the value for the rest of validation.

**Call relations**: This runs before publish_website starts installing, serving, or registering anything. It keeps the publishing flow from doing work for a visibility setting that cannot be used.


##### `_json_result`  (lines 167–168)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This turns a plain Python dictionary into the standard text result returned by these tools. It gives callers machine-readable JSON instead of loose prose.

**Data flow**: It takes a payload dictionary, converts it to a JSON string, wraps that string as text content, and places it inside a tool result. The output is the final ToolResult object returned to the tool caller.

**Call relations**: The public tool functions call this at the end of a successful run. website, start_server, deploy_website, and publish_website each gather their result details first, then hand them to this helper for consistent formatting.

*Call graph*: called by 4 (deploy_website, publish_website, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 171–187)

```
async def _free_log(ctx: ToolContext, log: str) -> None
```

**Purpose**: This safely clears the chosen log file name before a background server writes to it. Its main job is to avoid a security problem where a log path could be replaced by a link to some other file.

**Data flow**: It receives the tool context and the log path. It runs a small Python program inside the sandbox that checks the path is contained where it should be, creates parent folders if needed, and removes any existing file at that name. If the sandbox command reports an error, this function raises a runtime error instead of continuing.

**Call relations**: _serve calls this immediately before starting a server. That makes the later shell redirect create a fresh log file rather than following or truncating something unsafe.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 190–230)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This starts a server command in the background and waits until the requested port is actually reachable. It is the shared “start it and prove it works” routine for temporary servers and hosted sites.

**Data flow**: It receives a sandbox context, a command, a project directory, a port, and a log path. First it safely clears the log path. Then it kills any old process using the port, starts the new command with output going to the log, and repeatedly tries to connect to the port until it answers or times out. On success it returns the local URL, port, and log path; on failure it reads the end of the log and raises an error with useful details.

**Call relations**: start_server, deploy_website, and publish_website all call this when they need a live web process. It delegates log safety to _free_log, then hands its successful serving details back to the caller so they can either return them directly or register a hosted link.

*Call graph*: calls 1 internal fn (_free_log); called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `_refuse_before_serving`  (lines 233–272)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> str
```

**Purpose**: This checks whether a site is allowed to be hosted before the tool disrupts any existing server on the shared port. It prevents a failed permission or naming decision from unnecessarily knocking a member’s current site offline.

**Data flow**: It receives the raw site name, target port, optional visibility, and context about the acting member and conversation. It confirms the extension context and owner exist, verifies that changing visibility has a live speaker, converts the name into its safe hosted form, builds the kind of URL that would be used, and asks the hosted-sites store whether registration would be refused. If all checks pass, it returns the cleaned site name without writing the registration yet.

**Call relations**: deploy_website and publish_website call this before _serve. If it approves, those flows can safely take over the serving port; later _host repeats the registration checks at write time to guard against changes that happened meanwhile.

*Call graph*: called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 275–312)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> dict[str, object]
```

**Purpose**: This registers a running sandbox port as a hosted site and returns the stable link information. It is the step that turns a server that works inside the sandbox into a deliverable URL for members.

**Data flow**: It reads the tool context, raw site name, port, and optional visibility. It checks that there is an extension context and an acting member, confirms visibility changes are allowed for this turn, normalizes the site name, builds the public site URL, and writes the registration in the hosted-sites store. It returns the hosted site name, visibility, internal site object name, and public site URL.

**Call relations**: deploy_website and publish_website call this only after _serve has proved the port is live. It works with HostedSites to save the mapping from hosted name to sandbox port, and uses the site URL and object-name helpers to describe what was created.

*Call graph*: called by 2 (deploy_website, publish_website); 4 external calls (__init__, site_object_name, site_name, site_url).


##### `website`  (lines 315–324)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This builds a website project in the sandbox and reports what files are present afterward. It is useful when the agent needs to run a build command but not necessarily serve or host the result yet.

**Data flow**: It receives a build command, an optional project path, and a user-facing description. It turns the project path into a workspace-scoped path, runs the build command there with a longer timeout, and stops with an error if the command fails. Then it lists the project directory and returns the path plus the file names as JSON.

**Call relations**: This is the handler for the website tool definition. It uses the shared JSON result helper for its output, but does not call the serving or hosting helpers because its job ends after the build.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 327–332)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This starts a scratch server in the sandbox and returns the local URL once it is reachable. It does not create a permanent hosted link, so it is for testing and previewing rather than delivering a site.

**Data flow**: It reads the requested command, project path, optional port, and optional log file. It chooses a default port and log path when needed, scopes both paths to the workspace, then asks _serve to start the process and wait for readiness. It returns the serving details and project path as JSON.

**Call relations**: This is the handler for the start_server tool definition. It relies on _serve for port cleanup, background launch, logging, and readiness probing, then formats the answer through _json_result.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `deploy_website`  (lines 335–342)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This serves a built static website folder and registers it at a stable hosted link. It is meant for the moment when a finished static site should become something the member can open.

**Data flow**: It receives the static output directory, desired site name, entry file, optional visibility, and description. First it asks _refuse_before_serving whether hosting would be allowed. Then it serves the directory with Python’s simple HTTP server on the standard app port, waits until it is reachable, and registers that live port through _host. It returns the local sandbox URL, log path, hosted site details, public site URL, and entry point as JSON.

**Call relations**: This is the handler for the deploy_website tool definition. Its flow is permission check, serve, host, result: _refuse_before_serving protects existing sites, _serve proves the server is alive, _host writes the hosted registration, and _json_result packages the final response.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 1 external calls (workspace_path).


##### `publish_website`  (lines 345–359)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This publishes a web app by optionally installing dependencies, starting either its backend command or a static file server, and registering the result at a stable hosted link. It covers apps that need a little more setup than a plain static deploy.

**Data flow**: It receives the project path, built-output path, app name, optional visibility, optional run command, optional install command, and description. It first checks whether hosting would be allowed. If an install command is supplied, it runs that in the project directory and stops on failure. Then it chooses the server command: the supplied backend command, or a simple static server for the dist folder. It starts and probes the server with _serve, registers it with _host, and returns the combined serving and hosted-site details as JSON.

**Call relations**: This is the handler for the publish_website tool definition. Like deploy_website, it uses _refuse_before_serving before touching the shared port, _serve to launch and verify the app, _host to create the hosted link, and _json_result to return the final structured answer.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 2 external calls (quote, workspace_path).

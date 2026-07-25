# Sandboxed code and website execution  `stage-11.2.1`

This stage is the workbench for running code and websites outside the browser. It supports the main work loop, when an agent needs to test an idea, run a command, keep a coding session alive, or start a local web app inside the sandbox workspace.

The local sandbox carrier runs commands directly on the host computer, but limits them to a chosen project folder. It is the lightweight option for development, useful when you want the same tool behavior and network routing as a sandbox without starting Docker or a remote sandbox.

The persistent REPL extension adds long-lived code sessions. A REPL is an interactive “try a line, see the result” programming tool. One tool runs JavaScript with Node.js, and another runs Python for Excel-style work. Because the session remembers earlier successful code, later attempts can build on it.

The site tools prepare and run web projects. They build the site, clear any old process using the needed port, start the server, and wait until it can actually be reached.

## Files in this stage

### Local sandbox carrier
The local carrier establishes direct command execution inside a workspace without Docker or cloud sandbox infrastructure.

### `core/src/ufo/sandbox/local.py`

`io_transport` · `sandbox setup and command execution`

This file makes a workspace feel like a sandbox even though it is really just a normal folder on the host computer. Commands are launched as regular subprocesses, with their current directory set to that workspace. When tools refer to `/workspace`, this carrier rewrites that path to the real host folder before running the command.

It also prepares the command environment so local runs behave like container runs in important ways. It installs small helper programs, `sbx` and `sbxfs`, into a temporary scratch directory and puts them on `PATH`, so file and egress tools can run with only Python available. It points `HTTP_PROXY` and `HTTPS_PROXY` at the sandbox proxy on localhost, includes the run token in the proxy URL, and sets placeholder model API keys plus certificate settings. In plain terms: even local commands still go through the project’s controlled network gate, where metering and key-swapping can happen.

The important warning is that this is not a security boundary. A subprocess is not locked down by the operating system like it would be in Docker or a remote sandbox. The project relies on workspace path checks before commands are built, not on kernel-level isolation. So this carrier is convenient for development, but not meant to contain untrusted code safely.

#### Function details

##### `_provision_scratch`  (lines 39–52)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a temporary support area for the local carrier. This area holds a fake home directory and copies of the `sbx` and `sbxfs` helper binaries, so commands can find the same helper tools they would have inside a container.

**Data flow**: It starts with no input. It makes a new temporary directory, creates `home` and `bin` folders inside it, copies the helper binaries from the package’s local `image` directory into `bin`, marks them executable, and returns the path to the temporary directory.

**Call relations**: This is used automatically when a `LocalCarrier` is created, through the dataclass default factory. Later, `LocalCarrier.create` uses the returned scratch directory to set `HOME`, extend `PATH`, and store the proxy certificate.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 59–87)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Prepares a local sandbox session for one conversation. It ensures the workspace folder exists and builds the environment variables that every command in that session should inherit.

**Data flow**: It receives a `SandboxSpec`, which includes the conversation ID, workspace mount, proxy settings, run token, and extra environment variables. It checks and creates the host workspace folder, writes the proxy certificate into the scratch area, builds a proxy URL containing the run token, and returns a `SandboxHandle` containing the workspace mount and the full command environment.

**Call relations**: This is the setup step before commands can run locally. It calls `_root` to turn the configured workspace mount into a host path, uses background threads for blocking filesystem writes, and hands back a `SandboxHandle` that `LocalCarrier.exec`, `LocalCarrier.export`, and other sandbox operations use later.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 89–115)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command as a normal host subprocess, but makes it behave as if it were running inside the logical `/workspace` sandbox folder. It also enforces a timeout so a command cannot run forever.

**Data flow**: It receives a sandbox handle, command arguments, input bytes for standard input, and a timeout. It finds the real host workspace path, rewrites any `/workspace` text in the command arguments to that real path, starts the subprocess in the workspace folder with the prepared environment, sends the input, waits for output, and returns an `ExecResult` with decoded stdout, stderr, and the exit code. If the timeout is hit, it kills the process and returns an exit code reserved for timeouts.

**Call relations**: This is the main run step after `LocalCarrier.create` has prepared the handle. It calls `_root` to locate the workspace, uses `asyncio.create_subprocess_exec` to start the process, waits through `asyncio.wait_for`, and wraps the final result in `ExecResult` for the rest of the sandbox system to consume.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.export`  (lines 117–123)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Copies or streams a file produced in the local workspace into the project’s blob store. This is used for larger outputs or attachments that should be stored outside the live workspace.

**Data flow**: It receives a sandbox handle, a logical sandbox path such as something under `/workspace`, a `BlobStore`, and a destination key. It converts the logical path into a path relative to the workspace, joins that to the real host workspace folder, and asks the blob store to store that file under the given key.

**Call relations**: This is used after a command has created a file that needs to be exported. It calls `_root` to find the host workspace and then hands the real file path to `BlobStore.put_file`, letting the blob store stream the file instead of loading it all into memory here.

*Call graph*: calls 2 internal fn (put_file, _root); 1 external calls (PurePosixPath).


##### `LocalCarrier.destroy`  (lines 125–127)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Provides the standard cleanup hook for sandbox carriers, but the local carrier has nothing to remove per conversation. The workspace is a durable host directory, and the command environment lives only on the handle.

**Data flow**: It receives a sandbox handle and makes no changes. There is no returned value and no cleanup action, because this carrier did not create a disposable container or remote machine for the conversation.

**Call relations**: This fits the same carrier interface as Docker or remote sandbox implementations. Higher-level code can call `destroy` uniformly during teardown, and for the local carrier the call simply completes without doing work.


##### `LocalCarrier.host`  (lines 129–137)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Rejects requests for an externally reachable host address for a sandbox port. Local commands are just host subprocesses, so this carrier does not provide a separate network-addressable sandbox service endpoint.

**Data flow**: It receives a sandbox handle and a port number, but does not use them to create an address. Instead, it raises a runtime error explaining that per-port hosting requires a remote carrier such as E2B.

**Call relations**: Higher-level code may ask a carrier how to reach a service running inside the sandbox, such as a preview server or browser debugging endpoint. For the local carrier, this function stops that flow clearly and tells the caller to use a remote carrier when that feature is needed.


##### `_root`  (lines 140–143)

```
def _root(host_path: str | None) -> Path
```

**Purpose**: Turns the configured workspace mount path into a `Path` object and rejects missing mounts. The local carrier cannot work without a real host folder to use as the workspace.

**Data flow**: It receives either a host path string or `None`. If the value is missing, it raises an error; otherwise, it converts the string into a filesystem `Path` and returns it.

**Call relations**: This helper is shared by `LocalCarrier.create`, `LocalCarrier.exec`, and `LocalCarrier.export`. It keeps the rule in one place: every local sandbox operation must have a real filesystem workspace mount before it can proceed.

*Call graph*: called by 3 (create, exec, export); 1 external calls (Path).


### Sandbox execution tools
The extension tools provide persistent REPL execution and website build/run workflows inside the sandbox workspace.

### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `extension load and tool request handling`

This file gives the system two interactive coding tools. A REPL is a “read, evaluate, print loop”: a place where code can be sent in small pieces and run immediately. The important twist here is persistence. If a code block succeeds, it is saved into a workspace state file. The next code block is run after that saved code, so variables, imports, loaded pages, and opened workbooks can carry forward. If a block fails, it is not saved, so broken definitions do not poison later runs.

The JavaScript tool is aimed at Node.js tasks such as browser automation with Playwright. Before running user code, it adds a small helper called emitImage, which lets JavaScript send images back in the tool result. It also creates links to globally installed Node packages, because ES modules do not automatically search the normal global package path.

The Python tool is aimed at Excel spreadsheet work with openpyxl. It appends a small footer that prints a JSON version of a variable named result, if the user created one.

Both tools run inside ctx.sandbox, meaning the code executes in the project’s controlled container rather than directly on the host machine. The file also declares the extension manifest: its name, version, tools, input shapes, and related data-analysis skills.

#### Function details

##### `global_modules_link`  (lines 54–72)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because ES module code cannot rely on the usual NODE_PATH lookup, so bare imports like importing Playwright need another route.

**Data flow**: It receives a tuple of possible global module directories. It turns those directory names into one shell script string. When that script later runs, it creates a local node_modules folder in the REPL state area and adds symbolic links, which are shortcut files pointing to the real installed packages.

**Call relations**: The JavaScript REPL asks this function for the linking command before it runs Node. js_repl then sends that command to the sandbox, so package imports are prepared before the user’s JavaScript code starts.

*Call graph*: called by 1 (js_repl).


##### `_candidate_source`  (lines 161–167)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be tried for the next REPL run. It combines the old successful code with the new code, unless the user asked for a reset.

**Data flow**: It receives the sandbox context, the path to the saved REPL state file, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state, it returns just the new code. If there is saved state, it reads that state and appends the new code to the end. The output is the complete candidate program to run.

**Call relations**: Both js_repl and xlsx_repl call this near the start of each tool request. It gives them a temporary full program to test. Only the caller decides later whether to commit that candidate back to the saved state after seeing whether execution succeeded.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 170–181)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Packages the result of a REPL run into the standard tool response format. It records what the program printed, what errors it wrote, whether it succeeded, and optionally any images.

**Data flow**: It receives stdout, stderr, an exit code, and optional image content. It turns stdout, stderr, and exit_code into a JSON text block, attaches any images after that text, and marks the whole tool result as an error when the exit code is not zero.

**Call relations**: Both js_repl and xlsx_repl call this after the sandbox command finishes. It is the final formatting step that turns raw process output into something the rest of the tool system can display and interpret.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_emitted_images`  (lines 189–200)

```
async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code chose to return through emitImage. It safely ignores malformed image records instead of failing the whole tool response.

**Data flow**: It checks whether the JavaScript image-output file exists in the sandbox. If not, it returns no images. If the file exists, it reads its JSON-lines contents, keeps only the most recent allowed entries, validates each line as an emitted image with a media type and base64 data, and returns them as ImageContent objects.

**Call relations**: Only js_repl calls this, after Node has finished running. The JavaScript prelude writes the image file during execution, and this function folds that file back into the final ToolResult through _repl_result.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 203–215)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a persistent JavaScript session in the sandbox. It is the handler behind the js_repl tool, used for Node.js work such as browser automation, testing websites, or producing screenshots.

**Data flow**: It receives the tool context and validated JavaScript input. It builds a candidate program from saved state plus new code, writes a temporary .mjs run file with the emitImage helper at the top, clears any old emitted-image file, prepares Node package links, and runs Node with a timeout. If Node exits successfully, it saves the candidate program as the new persistent state. It returns stdout, stderr, the exit code, and any emitted images.

**Call relations**: This is called by the tool framework when a user invokes js_repl. Inside its flow, it relies on _candidate_source to assemble code, global_modules_link to prepare imports, _emitted_images to collect image output, and _repl_result to produce the final tool response.

*Call graph*: calls 4 internal fn (_candidate_source, _emitted_images, _repl_result, global_modules_link); 1 external calls (quote).


##### `xlsx_repl`  (lines 218–226)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a persistent Python session for Excel spreadsheet work. It is the handler behind the xlsx_repl tool and is designed for code that uses openpyxl to inspect or edit workbook files.

**Data flow**: It receives the tool context and validated Python input. It builds a candidate Python program from saved state plus new code, writes a temporary run file, appends a footer that prints the variable result as JSON if it exists, and runs python3 with a timeout. If the process succeeds, it saves the candidate program as the new persistent state. It returns stdout, stderr, and the exit code.

**Call relations**: This is called by the tool framework when a user invokes xlsx_repl. It shares the same persistence pattern as js_repl by using _candidate_source before execution and _repl_result after execution, but it does not use the JavaScript image or Node package setup.

*Call graph*: calls 2 internal fn (_candidate_source, _repl_result); 1 external calls (quote).


##### `manifest`  (lines 229–248)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the wider system. It tells the system the extension’s name and version, which tools it provides, how their inputs should be shaped, which functions run them, and which skills are available to load.

**Data flow**: It takes no input. It creates a Manifest object containing two ToolDef entries, one for the JavaScript REPL and one for the Excel Python REPL, plus SkillSpec entries for the bundled data skills. The returned manifest is the extension’s public registration record.

**Call relations**: The extension loader calls this when discovering the package. The returned manifest connects external tool names to the actual handlers js_repl and xlsx_repl, so later tool requests can be routed to the right function.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool invocation during website build, preview, deploy, or publish`

This file gives the system a safe, repeatable way to work with websites in a sandboxed workspace. A sandbox is an isolated container-like place where commands can run without directly touching the outside machine. Without these tools, an agent might start a web server with a plain shell command and return too early, before the page is ready, or fail because an old process is still using the same port.

The file defines input shapes for four tools: building a website, starting any server, serving an already-built static site, and publishing a web app. These input shapes are checked with Pydantic, a library that validates structured data before the tool runs.

The key helper is `_serve`. It acts like a careful stagehand: first it clears anything already using the chosen port, then starts the server in the background, then repeatedly checks the port until something is listening. Only after that does it return a local URL such as `http://localhost:8000`.

The public tool functions use this helper and return small JSON results. Build output stays inside the sandbox unless another tool explicitly shares files, which keeps website work contained and predictable.

#### Function details

##### `StartServerInput.validate_port`  (lines 63–66)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents confusing failures later by rejecting values outside the valid range of 1 to 65535.

**Data flow**: It reads the `port` value from a `StartServerInput` object. If no port was provided, it leaves the input alone. If a port was provided but is too small or too large, it raises an error; otherwise it returns the same validated input object.

**Call relations**: This runs as part of Pydantic's input validation before the `start_server` tool receives its arguments. It protects the later serving step from being asked to listen on an impossible port.


##### `_json_result`  (lines 89–90)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This wraps a plain Python dictionary as a tool response containing JSON text. It gives all the site tools a consistent way to report results such as URLs, ports, logs, and file lists.

**Data flow**: It takes a dictionary, turns it into a JSON string, places that string inside a text content object, and then places that content inside a tool result. The output is the standardized result object expected by the tool system.

**Call relations**: The user-facing tool functions call this at the end of their work. `website`, `start_server`, `deploy_website`, and `publish_website` each gather their result details, then hand them to `_json_result` so callers receive the same response format.

*Call graph*: called by 4 (deploy_website, publish_website, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_serve`  (lines 93–130)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This starts a server inside the sandbox and waits until it is actually reachable. It is used so tools do not claim success while the server is still starting or blocked by an old process on the same port.

**Data flow**: It receives the tool context, a command to run, a project directory, a port, and a log file path. It builds a sandbox shell script that changes into the project, kills any process using that port, starts the command in the background with output written to the log, and probes the port until it opens or times out. On success it returns a dictionary with the URL, port, and log path; on failure it reads the end of the log and raises an error with that information.

**Call relations**: `start_server`, `deploy_website`, and `publish_website` all rely on `_serve` for the risky part of launching a background server. Those functions decide which command, folder, and port to use, then `_serve` performs the cleanup, launch, and readiness check.

*Call graph*: called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `website`  (lines 133–142)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This builds a website project inside the sandbox. It is useful when the system needs to run a build command and then know what files appeared in the project directory.

**Data flow**: It receives a sandbox context and build arguments. It chooses the project directory, defaulting to `/workspace` if none is provided, runs the requested build command there, and stops with an error if the command fails. If the build succeeds, it lists the top-level files in the project directory and returns the project path and file names as JSON.

**Call relations**: This is the handler for the `website` tool definition. It directly uses the sandbox to run shell commands, uses shell quoting for the project path, and then calls `_json_result` to turn its file listing into a tool response.

*Call graph*: calls 1 internal fn (_json_result); 1 external calls (quote).


##### `start_server`  (lines 145–149)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This starts an arbitrary server command in the background and returns a URL once the server is listening. It is the safer alternative to asking the sandbox shell to start a long-running server directly.

**Data flow**: It receives a command, project path, optional port, and optional log file. It fills in defaults when the port or log file is missing, asks `_serve` to launch and check the server, and then returns the server details plus the project path as JSON.

**Call relations**: This is the handler for the `start_server` tool definition. It prepares the serving options, delegates the actual launch-and-wait behavior to `_serve`, and then formats the successful result through `_json_result`.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `deploy_website`  (lines 152–156)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This serves a folder of already-built static website files from the sandbox. It is meant for cases where the build output already exists and needs to be previewed or exposed at a reachable route.

**Data flow**: It receives the directory to serve, a site name, and an entry file name. It creates a simple Python static file server command on the standard app port, asks `_serve` to run that command from the provided directory, and returns the URL, port, log path, site name, and entry point as JSON.

**Call relations**: This is the handler for the `deploy_website` tool definition. It does not implement its own server startup logic; instead it chooses a static-server command, calls `_serve` to make it reliable, and uses `_json_result` to report the route back to the caller.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `publish_website`  (lines 159–171)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This publishes a web app from the sandbox, optionally installing dependencies first and optionally running a backend command. It supports both simple static sites and apps that need a custom server process.

**Data flow**: It receives the project path, built output path, app name, optional install command, and optional run command. If an install command is present, it runs it in the project directory and stops on failure. Then it either uses the provided backend command from the project directory or starts a simple static file server from the built output directory. It asks `_serve` to launch and verify the server, then returns the app name and serving details as JSON.

**Call relations**: This is the handler for the `publish_website` tool definition. It performs any dependency installation itself, then hands server startup to `_serve`; after the server is ready, it uses `_json_result` so the caller gets a clean published-app response.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (quote).

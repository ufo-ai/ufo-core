# Coding, website, and REPL execution helpers  `stage-12.2`

This stage provides practical helper tools for work that happens inside a sandbox, which is a safe, isolated workspace where code can run without touching the user’s real machine. It supports the main work loop: writing code, testing ideas, running small experiments, and checking websites.

The coding package file is a simple doorway. It tells Python that the coding extension folder can be imported by the rest of the system. It has no moving parts itself, but it lets other coding features be found and used.

The REPL manifest describes tools for running short Python or JavaScript snippets. A REPL is an interactive scratchpad for code. Here, successful variables and imports can stay available between calls, so the agent can build up an analysis step by step instead of starting over each time.

The website tools are the site workshop. They can build files, start a local server, deploy static sites, or publish web apps in the sandbox. They also check that a server is truly reachable before saying it worked.

## Files in this stage

### Coding Package Setup
Package initialization makes the coding extension importable for higher-level sandbox workflows.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that a directory should be treated as an importable package. Think of it like a label on a folder: the label does not contain instructions, but it tells Python, “this folder belongs to the code system and can be opened by name.” Without this file, some Python environments or tools might not recognize `extensions/coding/ufo_ext_coding` as a package, which could make imports fail or make package discovery less reliable. Because the file is empty, it does not run setup code, expose shortcuts, or change any settings when imported. Its value is structural: it helps organize the coding extension as a clean module boundary.


### Persistent REPL Execution
REPL tooling runs Python or JavaScript snippets in the sandbox while preserving successful execution state for iterative work.

### `extensions/repl/ufo_ext_repl/manifest.py`

`domain_logic` · `tool registration and tool execution`

This file gives the agent two “scratchpad that remembers” tools. One runs JavaScript with Node.js, mainly for browser automation and website testing. The other runs Python for Excel work with openpyxl. The important idea is persistence: if a snippet succeeds, its code is saved into a workspace state file. The next snippet is run after that saved code, so variables, imports, and loaded files are still available. If a snippet fails, it is not saved, which prevents a broken experiment from poisoning later work. A reset deletes the saved state and starts fresh.

The tools run inside the project sandbox, so file access and internet access follow the sandbox rules rather than touching the host machine directly. For JavaScript, the file also prepares a small helper called emitImage. User code can call it to return images, such as screenshots, along with normal text output. The JavaScript runner also links globally installed Node packages into the local workspace so plain imports can find packages already present in the image.

At the end, manifest() packages these handlers, their input schemas, descriptions, and skill folders into the extension record that the larger system can discover.

#### Function details

##### `global_modules_link`  (lines 54–72)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because ES modules do not automatically look in NODE_PATH, so imports like Playwright need local-looking links to resolve.

**Data flow**: It receives a tuple of possible global package folders, or uses the default ones. It turns those folder names into one shell command that creates a workspace node_modules directory and adds symbolic links, which are shortcut files pointing to the real package folders. The output is just the command string; it does not run the command itself.

**Call relations**: When js_repl is about to run JavaScript code, it calls global_modules_link to get the setup command. js_repl then runs that command in the sandbox before launching Node, so user code can import installed packages.

*Call graph*: called by 1 (js_repl).


##### `_candidate_source`  (lines 161–167)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full code file that should be tried for the next REPL run. It combines the previously saved successful code with the new snippet, unless the caller asked for a reset.

**Data flow**: It takes the sandbox context, the path to the saved REPL state file, the new code, and a reset flag. If reset is true, it removes the saved file. If there is no saved file, it returns only the new code plus a final newline. If a saved file exists, it reads that file and appends the new code. The result is a single source-code string ready to execute.

**Call relations**: Both js_repl and xlsx_repl call this first. It gives them a safe “candidate” program to try. Only after those callers see a successful run do they save this candidate as the new persistent REPL state.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 170–181)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Turns raw command output into the standard tool response format. It packages stdout, stderr, and the exit code as JSON text, and can include images for JavaScript runs.

**Data flow**: It receives standard output, standard error, an exit code, and optionally image objects. It creates a JSON text summary containing the three command results, attaches any images after that text, and marks the whole tool result as an error when the exit code is not zero. The output is a ToolResult object for the agent to receive.

**Call relations**: js_repl and xlsx_repl call this after their sandbox command finishes. It is the final wrapping step that converts low-level process results into the content format expected by the tool system.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_emitted_images`  (lines 189–200)

```
async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript user code chose to return through emitImage. It quietly ignores missing or malformed image records so a bad image entry does not crash result collection.

**Data flow**: It uses the sandbox context to check whether the JavaScript image log file exists. If it does not exist, it returns no images. If it exists, it reads the file, looks at the most recent allowed entries, parses each line as an emitted image record, and converts valid records into ImageContent objects. The output is a tuple of images ready to attach to the tool result.

**Call relations**: js_repl calls this after Node finishes. The images it returns are handed straight into _repl_result, so the agent receives screenshots or other generated images alongside stdout and stderr.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 203–215)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a JavaScript snippet in the persistent Node.js REPL. It is the tool handler used when the agent wants to test browser code, automate a page, or generate visual output while keeping successful JavaScript state between calls.

**Data flow**: It receives the tool context and validated JavaScript input. It asks _candidate_source to combine saved state with the new code, writes a temporary .mjs run file with the image-emitting prelude at the top, clears any old image log, links global Node packages, and runs Node in the sandbox with a timeout. If Node exits successfully, it saves the candidate source as the new persistent state. It returns stdout, stderr, exit code, and any emitted images as a ToolResult.

**Call relations**: This is called by the tool framework when the js_repl tool is used. Inside its flow, it relies on global_modules_link for import setup, _candidate_source for persistence, _emitted_images for visual output, and _repl_result for the final response.

*Call graph*: calls 4 internal fn (_candidate_source, _emitted_images, _repl_result, global_modules_link); 1 external calls (quote).


##### `xlsx_repl`  (lines 218–226)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a Python snippet in the persistent Excel-focused REPL. It is meant for spreadsheet work where the agent may load a workbook once, inspect or modify it over several calls, and keep successful Python variables available.

**Data flow**: It receives the tool context and validated Python input. It asks _candidate_source to build the combined Python source, writes a temporary run file, and appends a footer that prints result as JSON if the user code defined a variable named result. It runs python3 in the sandbox with a timeout. If the run succeeds, it saves the candidate code as the new persistent state. It returns stdout, stderr, and exit code as a ToolResult.

**Call relations**: This is called by the tool framework when the xlsx_repl tool is used. It shares the persistence helper _candidate_source with js_repl, then uses _repl_result to turn the Python process output into the standard tool response.

*Call graph*: calls 2 internal fn (_candidate_source, _repl_result); 1 external calls (quote).


##### `manifest`  (lines 229–249)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger UFO tool system. It says what the extension is called, which tools it provides, which input shapes those tools expect, and which related skills are available.

**Data flow**: It reads the module constants for names, descriptions, skill folders, and handler functions. It creates ToolDef objects for the JavaScript and Excel REPLs, creates SkillSpec objects for each skill directory, and returns one Manifest object with internet access enabled for the sandbox. The output is the extension manifest consumed by the host system.

**Call relations**: The host system calls manifest when discovering or loading this extension. The returned manifest points tool calls to js_repl and xlsx_repl, and it exposes the listed skill packs so the agent can load extra data-work guidance when needed.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Website Execution Tools
Website tools build, serve, deploy, and publish sandboxed sites while checking server reachability before reporting success.

### `extensions/sites/ufo_ext_sites/tools.py`

`domain_logic` · `request handling`

This file gives the system a safe, repeatable way to build and serve websites in the sandbox, which is an isolated working area. Without it, a caller might start a server with a plain shell command and get back too early, before the port is ready, or leave old processes blocking the same port. The file defines input shapes for each tool, so callers must provide the needed details such as a project folder, a command, or a port. The central helper, `_serve`, works like a careful stagehand: it clears the requested port, starts the server in the background, writes logs to a file, then repeatedly checks whether the port is accepting connections. Only when the server is reachable does it return a local URL. The `website` tool is for running a build command and reporting what files now exist. `start_server` runs any server command. `deploy_website` serves a folder of built static files using Python’s simple web server. `publish_website` can optionally install dependencies, then serve either static output or a custom backend command. At the bottom, the file registers these actions as tool definitions so the wider system can offer them by name.

#### Function details

##### `StartServerInput.validate_port`  (lines 63–66)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents mistakes like asking the tool to listen on port 0 or a number too large to exist.

**Data flow**: It reads the `port` field from the start-server input. If no port was supplied, it leaves the input alone. If a port was supplied, it verifies that it is between 1 and 65535; invalid values become a clear validation error, while valid input is returned unchanged.

**Call relations**: This runs automatically when a `StartServerInput` object is validated by the input model. It protects `start_server` before that function tries to launch anything in the sandbox.


##### `_json_result`  (lines 89–90)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This turns a plain Python dictionary into the standard tool result format expected by the rest of the system. It is the common final packaging step for successful website tool responses.

**Data flow**: It receives a dictionary of result details, such as a URL, port, project path, or file list. It converts that dictionary into JSON text, wraps the text in a `TextContent` object, then wraps that content in a `ToolResult`. The output is a structured tool response that callers can read consistently.

**Call relations**: The user-facing tools call this after they have finished their work. `website` uses it to return build output information, while `start_server`, `deploy_website`, and `publish_website` use it to return the reachable server route and related details.

*Call graph*: called by 4 (deploy_website, publish_website, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_serve`  (lines 93–130)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This is the shared routine for starting a server safely inside the sandbox. It frees the chosen port, launches the command in the background, checks that the server is listening, and reports the URL only after it is ready.

**Data flow**: It receives the tool context, a shell command, a project directory, a port, and a log file path. It builds a sandbox shell script that changes into the project directory, kills anything already using the port, starts the command with output redirected to the log, and runs a small readiness check that repeatedly tries to connect to the port. If the check succeeds, it returns a dictionary with the local URL, port, and log path. If it fails, it reads the end of the log and raises an error with the most useful message it can find.

**Call relations**: `start_server`, `deploy_website`, and `publish_website` all hand their server-launching work to this helper. That keeps the important safety behavior—port cleanup, background launch, logging, and readiness polling—the same for all server-style tools.

*Call graph*: called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `website`  (lines 133–142)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This tool runs a website build command inside the sandbox and reports the files in the project afterward. It is useful when the system needs to compile or generate a site before serving or sharing it.

**Data flow**: It receives a tool context and build arguments containing a command and optionally a project path. It chooses the given project path or falls back to `/workspace`, runs the build command there with a longer timeout, and stops with an error if the command fails. If the build succeeds, it lists the project directory and returns the project path plus the file names as a JSON tool result.

**Call relations**: This is one of the registered site tools. Unlike the server tools, it does not call `_serve`; its job is only to run the build and summarize the resulting directory. It uses `_json_result` to return its response in the same format as the other tools.

*Call graph*: calls 1 internal fn (_json_result); 1 external calls (quote).


##### `start_server`  (lines 145–149)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This tool starts a caller-provided server command in the background and returns a reachable local URL. It should be used instead of a raw shell command when the caller needs confidence that the server is actually running.

**Data flow**: It receives a command, project path, optional port, and optional log file. It chooses a default port and log file when they are not supplied, passes those details to `_serve`, then adds the project path to the returned server information. The final output is a JSON tool result containing the URL, port, log file, and project path.

**Call relations**: This is the general-purpose server tool registered in `SITES_TOOLS`. It relies on `StartServerInput.validate_port` during input validation, delegates the launch and readiness check to `_serve`, and packages the successful result through `_json_result`.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `deploy_website`  (lines 152–156)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This tool serves an already-built static website folder from the sandbox. It is meant for cases where the site files, including an entry file like `index.html`, already exist and only need to be made reachable.

**Data flow**: It receives a project path, site name, and entry point. It creates a Python static-file server command on the standard app-serving port, chooses a deploy log file, and asks `_serve` to start that server in the provided folder. It returns the reachable URL, port, log path, site name, and entry point as JSON.

**Call relations**: This registered tool is a specialized wrapper around `_serve`. Instead of accepting any server command, it supplies a known static-file command, then uses `_json_result` to return information that identifies the deployed site.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `publish_website`  (lines 159–171)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This tool publishes a web app from the sandbox, with optional dependency installation and optional custom backend command. It covers both simple static apps and apps that need their own server process.

**Data flow**: It receives the project directory, built output directory, app name, and optional install and run commands. If an install command is supplied, it runs it in the project directory and raises an error if installation fails. Then it either uses the supplied run command from the project directory or starts a Python static-file server from the built output directory. It asks `_serve` to launch and verify the server, then returns the reachable URL and app name as JSON.

**Call relations**: This is the most complete publishing tool in `SITES_TOOLS`. It performs any requested setup itself, delegates the actual server startup and readiness check to `_serve`, and uses `_json_result` to produce the final tool response.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (quote).

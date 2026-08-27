# Tool Dispatch and Sandboxed Execution  `stage-11`

This stage is part of the main work loop. When the model asks to use a tool, the system must find that tool, check that the request is allowed, and run it without letting it escape its limits. The tool registry is the catalog: it names each tool, describes its inputs, and lets the engine look it up. The built-in tools do the practical jobs, such as shell commands, file edits, search, user questions, subagents, skills, account access, and secrets. The tool context is the guarded doorway each tool uses to reach files, sandboxes, billing, credentials, and permissions.

The sandbox support provides the safe workbench where commands and files live. It can be local, Docker-based, remote, or connected to a user terminal, while path checks keep file access inside bounds. Task tracking lets long shell work continue after a timeout.

The bridge files let code inside a sandbox request approved tools through a strict JSON contract and the normal turn loop. MCP adds outside workspace tools. The REPL extension adds persistent JavaScript and Python sessions for step-by-step coding.

## Sub-stages

- [Sandbox Carriers, Terminal Bridges, and File Boundaries](stage-11.1.md) `stage-11.1` — 15 files

## Files in this stage

### Extension Tool Providers
Workspace MCP servers and persistent REPL extensions add externally supplied tools to the system catalog.

### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `tool invocation and extension load`

This extension is the bridge between the agent and external MCP servers. A workspace can store a named list of MCP servers in a credential called `mcp_servers`. Each entry says where the server lives and, optionally, what bearer token to send for access. The agent does not keep a fixed list of these outside tools. Instead, it asks a chosen MCP server what tools it offers, then calls a selected tool by name.

The file exposes two agent tools. `list_mcp_tools` is like asking a shop for its catalog: first it returns a compact list of tool names, summaries, parameter names, and required fields. If the agent is interested in specific tools, it can ask again and receive their full input schemas, which describe exactly what arguments those tools accept. `call_mcp_tool` then sends arguments to one chosen MCP tool and returns the result.

The file is careful around trust and size. MCP servers are outside systems, so their descriptions and results may be misleading or hostile; the tools are marked as untrusted. Requests and responses are capped at about one mebibyte so a server cannot flood the agent with huge data. URLs are validated to be HTTP or HTTPS, and missing or unknown server names fail clearly instead of making a blind network call.

#### Function details

##### `McpServer._http_url`  (lines 77–80)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validator makes sure a configured MCP server URL starts with `http://` or `https://`. It prevents the extension from trying to use unsupported or surprising address types.

**Data flow**: A URL string comes in while an `McpServer` configuration object is being built. The function checks it against the allowed HTTP/HTTPS pattern. If it matches, the same URL comes out; if not, validation stops with an error.

**Call relations**: This runs automatically as part of building an `McpServer` from the workspace credential data. It protects later network setup, especially the client creation done by `mcp_client`, from receiving an invalid server address.


##### `mcp_client`  (lines 107–113)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This creates a FastMCP HTTP client connected to one configured server. The client knows how to perform the MCP startup handshake, session handling, and streaming details, so the rest of the file can simply list or call tools.

**Data flow**: A validated `McpServer` object goes in, containing a URL and optional auth token. The function builds an authorization header if there is a token, attaches it to a streamable HTTP transport, sets a timeout, and returns a ready-to-use FastMCP client.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` has found the named server. It hands the actual network conversation to FastMCP's `Client` and `StreamableHttpTransport`, keeping protocol details out of the higher-level tool logic.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 116–128)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This finds one named MCP server from the workspace's stored `mcp_servers` credential. It exists so tool calls only go to servers the workspace explicitly configured.

**Data flow**: A tool context and a server name go in. The function reads the credential slot from the extension context, parses and validates it as MCP server configuration, looks up the requested name, and returns the matching `McpServer`. If the extension context, credential, or server name is missing, it raises a clear error.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` start by calling this. It is the gatekeeper before `mcp_client` creates a network connection, making sure later steps work with a known configured server rather than arbitrary input.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 131–152)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the implementation behind the agent's `list_mcp_tools` tool. It lets the agent discover what an MCP server can do, first in a compact catalog and then, on request, with full schemas for selected tools.

**Data flow**: A tool context and parsed input come in, including the server name and optionally specific tool names. The function resolves the server, opens an MCP client, asks the server for its tools, and then either builds a compact catalog or extracts full schema entries for the requested names. It returns a `ToolResult` containing JSON text, or raises an error for unknown tool names.

**Call relations**: This is called by the tool runtime when the model invokes `list_mcp_tools`. It relies on `_server` for configuration, `mcp_client` for the MCP connection, `_catalog_entry` for compact browsing output, `_schema_entry` for detailed tool definitions, `_bounded_schemas` to avoid oversized schema replies, and `_json_result` to package the final response.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 155–157)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: This reads whether an MCP tool says it is idempotent, meaning repeated identical calls should have the same effect as one call. That hint helps describe whether a tool is likely safe to retry.

**Data flow**: An MCP tool object goes in. The function checks its annotations for an `idempotentHint` value and converts that to a simple true-or-false result. If there are no annotations, it returns false.

**Call relations**: `_catalog_entry` and `_schema_entry` call this while preparing tool information for the agent. It adds a small safety signal to both the compact catalog and the full schema view.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 160–176)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns a full MCP tool definition into a short catalog entry the agent can scan. It keeps only the tool name, a short summary, parameter names, required parameters, and the idempotency hint.

**Data flow**: A full MCP tool object goes in. The function looks at its input schema to collect parameter names and required fields, shortens its description through `_summary`, reads the idempotency hint through `_idempotent`, and returns a JSON-friendly dictionary.

**Call relations**: `_list_mcp_tools` calls this when the agent asks to browse all tools without requesting full schemas. It keeps the first discovery step small enough to fit in a normal tool result instead of dumping every server schema at once.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 179–185)

```
def _summary(description: str) -> str
```

**Purpose**: This makes a short summary from a longer MCP tool description. It is designed for docstring-like descriptions where the first line usually explains the tool and later lines list arguments.

**Data flow**: A description string goes in. The function trims it, takes the first line, keeps text before the first sentence break when possible, cuts it to the configured maximum length, and returns that short text.

**Call relations**: `_catalog_entry` uses this so the catalog shown by `_list_mcp_tools` stays readable. It avoids pulling long argument blocks into the browsing view.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 188–194)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: This turns one MCP tool into the detailed form the agent needs before calling it. It includes the full description and input schema so the agent can spell arguments correctly.

**Data flow**: An MCP tool object goes in. The function copies its name, description, input schema, and idempotency hint into a JSON-friendly dictionary. That dictionary comes out ready to include in a tool result.

**Call relations**: `_list_mcp_tools` calls this after the agent names specific tools it wants to inspect. It uses `_idempotent` to include the same retry-safety hint found in the compact catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 197–209)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the implementation behind the agent's `call_mcp_tool` tool. It sends a JSON argument object to one named tool on one configured MCP server and returns the server's answer.

**Data flow**: A tool context and parsed call input come in: server name, tool name, and arguments. The function resolves the server, checks that the serialized arguments are not too large, opens an MCP client, and calls the remote tool. If the remote tool reports an error, it returns an error `ToolResult` with bounded text. If there is structured JSON content, it returns that; otherwise it joins any text blocks and returns them as JSON.

**Call relations**: The tool runtime calls this when the model invokes `call_mcp_tool`. It depends on `_server` and `mcp_client` to reach the right MCP server, uses `_joined_text` to read text-only replies, `_bounded` to enforce response size limits, and `_json_result` to package successful output.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 212–213)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This collects plain text blocks from an MCP response into one readable string. It ignores non-text response blocks.

**Data flow**: A list of response content objects goes in. The function keeps only items that are MCP text content, takes their text fields, joins them with newline characters, and returns the combined string.

**Call relations**: `_call_mcp_tool` uses this when a remote tool fails or when it returns text rather than structured JSON. It converts MCP's block-style response into the simple text form expected in a `ToolResult`.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 216–219)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed size for text returned through this extension. It fails loudly instead of silently cutting data off.

**Data flow**: A text string goes in. The function measures its encoded byte length. If it is within the configured limit, the same text comes out; if it is too large, it raises an `McpError`.

**Call relations**: `_call_mcp_tool` uses this for error text from a remote MCP tool, and `_json_result` uses it for JSON responses. It is the final size guard before content is handed back to the agent loop.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 222–239)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: This protects the agent from receiving too many large tool schemas at once. It encourages the agent to ask only for the few schemas it is about to use.

**Data flow**: A JSON-friendly payload and the number of requested tools go in. If more than one schema was requested and the rendered payload is too large for a useful tool result, it raises a clear error. Otherwise it passes the payload to `_json_result` and returns the resulting `ToolResult`.

**Call relations**: `_list_mcp_tools` calls this when returning full schemas for named tools. It sits between `_schema_entry` output and `_json_result`, deciding whether the schema batch is small enough to send back directly.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 242–243)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This wraps a JSON-friendly dictionary into the standard tool-result shape used by the agent. It also applies the response byte limit.

**Data flow**: A dictionary goes in. The function serializes it to a JSON string, checks the string with `_bounded`, wraps it in a text content object, and returns a `ToolResult` containing that text.

**Call relations**: `_list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` all use this as their common output path for successful JSON-shaped replies. It centralizes the final formatting and size check.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 246–277)

```
def manifest() -> Manifest
```

**Purpose**: This declares the extension to the host system. It names the extension, lists the two tools it provides, and describes the credential slot needed to find MCP servers.

**Data flow**: No runtime input is needed. The function constructs a `Manifest` containing the extension name and version, two `ToolDef` entries for listing and calling MCP tools, and one `CredentialSlot` for the workspace's MCP server map. The completed manifest is returned to the extension loader.

**Call relations**: The host calls this when loading the extension. It wires the public tool names to `_list_mcp_tools` and `_call_mcp_tool`, and tells the host that the `mcp_servers` credential must be available for `_server` to read during tool calls.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/repl/ufo_ext_repl/manifest.py`

`domain_logic` · `extension load and tool request handling`

This file is the extension’s front door and its working engine. It defines a small “workspace notebook” for code: each successful JavaScript or Python block is saved, and the next block is run after all saved earlier blocks. If a block fails or times out, it is not saved. That matters because otherwise one bad variable, import, or half-finished browser session could break every later call.

The JavaScript REPL runs Node.js as an ES module, which means modern imports and top-level await work. It also creates links to globally installed Node packages so code can import image-installed tools such as Playwright. A special emitImage helper is inserted before user code; JavaScript can call it to return screenshots or other images directly in the tool result.

The Excel REPL runs Python and is aimed at spreadsheet work with openpyxl. It appends a small footer that prints the variable result as JSON when the user sets it.

Both tools run inside the project sandbox, so file access and network access follow the sandbox rules. Long-running code is launched through the shared task system. If the caller’s timeout expires, the process may keep running in the background, and the result returns task handles instead of pretending the run finished.

#### Function details

##### `_meter_run`  (lines 71–87)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for a REPL run, including which tool ran and what exit code it ended with. This helps operators tell the difference between user code failing and the interpreter itself being missing or broken.

**Data flow**: It takes the tool context, the tool name, and the process exit code. It groups uncommon exit codes under a shared “other” label, adds the current profile from the turn context, and sends a metric named repl_run_total. It does not return data; it updates observability outside the tool result.

**Call relations**: Both js_repl and xlsx_repl call this after the background task reports an exit code. It hands the information to emit_metric, using turn_profile to label the run with the active subagent profile.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 90–109)

```
def global_modules_link(directory: str, roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds a shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This is needed because ES modules do not automatically search NODE_PATH the way some older Node setups do.

**Data flow**: It takes a sandbox directory and a list of possible global module roots. It returns a shell script string that creates a node_modules folder there and symlinks each available global package into it, without overwriting packages already linked. It does not run the command itself.

**Call relations**: js_repl calls this before starting Node. The returned command is then run through the sandbox shell; shell_path is used while building the command so paths are quoted safely for the shell.

*Call graph*: called by 1 (js_repl); 1 external calls (shell_path).


##### `js_emit_relative`  (lines 116–124)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates the workspace-relative filename where one JavaScript call will write emitted images. Each call gets its own image log so a slow old run cannot overwrite images from a newer run.

**Data flow**: It takes a short call identifier string. It formats that identifier into a path like repl/js-emit-<id>.jsonl and returns the path. It does not touch the filesystem.

**Call relations**: js_repl calls this when preparing a JavaScript execution. The returned path is passed into js_emit_prelude and later into _emitted_images so the same per-call image file is written and then read back.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 127–164)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates the JavaScript code that defines emitImage for a REPL run. User JavaScript can call emitImage to attach images, such as screenshots, to the tool result.

**Data flow**: It takes the image-output path for the current call. It returns a JavaScript source string that imports file-writing helpers, defines limits, converts buffers or base64 strings into image records, and writes a rolling list of recent images as JSON lines. The returned text is prepended to the user’s code before Node runs it.

**Call relations**: js_repl calls this while constructing the temporary JavaScript run file. It uses json.dumps to safely insert the path into JavaScript source code, then _emitted_images later reads the file that this prelude writes.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `_candidate_source`  (lines 232–240)

```
async def _candidate_source(ctx: ToolContext, relative: str, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be tried for the next REPL call. It combines the saved successful history with the new code, or starts fresh when reset is requested.

**Data flow**: It receives the tool context, the saved-state file paths, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state, it returns just the new code plus a trailing newline. Otherwise it reads the existing saved code and appends the new code. The returned text is only a candidate; it is saved permanently later only if the run succeeds.

**Call relations**: Both js_repl and xlsx_repl call this before writing their temporary run file. It uses shell_path when removing or reading files through the sandbox shell.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (shell_path).


##### `_repl_result`  (lines 243–255)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Packages the result of a REPL process that actually finished. It turns standard output, standard error, and the exit code into the tool response, and marks failures clearly.

**Data flow**: It takes stdout text, stderr text, an exit code, and optional images. It builds a JSON text payload containing those values. If the exit code is not zero, it also adds a notice explaining that the REPL state did not advance. It returns a ToolResult containing the text and any images, with the error flag set for nonzero exits.

**Call relations**: js_repl and xlsx_repl call this after a run finishes without a foreground timeout. For JavaScript, js_repl may first collect images through _emitted_images and pass them along.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 258–274)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Builds the tool response for a run whose caller-side timeout expired. It explains that the REPL state was not saved and, when possible, gives handles for checking the still-running task.

**Data flow**: It takes the TaskRun record and the number of seconds actually waited. If there is no process id, it returns an error message saying the wait expired and state is unchanged. If the process still exists, it returns task id, log path, process id, and a state-unchanged note. The run may continue outside the foreground tool call.

**Call relations**: Both js_repl and xlsx_repl call this when run_task reports a timeout. It uses timeout_notice for the plain timeout message and task_handles to format follow-up information for surviving background work.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 282–296)

```
async def _emitted_images(ctx: ToolContext, emit_relative: str, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images produced by JavaScript emitImage calls and converts them into image content for the tool result. It also removes the temporary image log afterward.

**Data flow**: It takes the tool context plus the relative and absolute paths of the emit file. If the file does not exist, it returns an empty tuple. If it exists, it reads the JSON lines, deletes the file, validates each recent image record, ignores malformed lines, and returns ImageContent objects for the valid images.

**Call relations**: js_repl calls this after a JavaScript run finishes. It reads the file written by the code generated in js_emit_prelude, and uses shell_path when reading and removing that file through the sandbox shell.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, shell_path).


##### `js_repl`  (lines 299–323)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs one JavaScript REPL call in the sandbox while preserving successful previous code. It is the handler behind the js_repl tool.

**Data flow**: It receives the tool context and validated JavaScript input: code, optional timeout, and optional reset. It finds the state and run-file paths, builds candidate source, creates a unique image emit path, writes a temporary .mjs file with the emitImage prelude plus user code, links global Node modules, and starts Node through the task runner. If the run times out, it returns an expiry response. If it exits successfully, it saves the candidate source as the new REPL state. It then returns stdout, stderr, exit code, and any emitted images.

**Call relations**: The manifest registers this as the handler for the JavaScript tool. During a call it relies on _candidate_source, js_emit_relative, js_emit_prelude, global_modules_link, run_task, _meter_run, _expired_result, _emitted_images, and _repl_result to move from input code to a safe, explainable tool response.

*Call graph*: calls 8 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (shell_path, run_task, uuid4).


##### `xlsx_repl`  (lines 326–341)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs one Python REPL call for Excel-style spreadsheet work while preserving successful previous code. It is the handler behind the xlsx_repl tool.

**Data flow**: It receives the tool context and validated Python input: code, optional timeout, and optional reset. It builds the candidate source from saved state plus new code, writes a temporary Python file with a footer that prints result as JSON when present, and runs it with python3 through the task system. If it times out, it returns an expiry response. If it exits successfully, it saves the candidate source as the new state. Finally it returns stdout, stderr, and exit code.

**Call relations**: The manifest registers this as the handler for the Excel/Python tool. It shares the common state-building, timeout, metric, and result-formatting helpers with js_repl, but it does not use the JavaScript image or Node module setup.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (shell_path, run_task).


##### `manifest`  (lines 344–364)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the larger system. It tells the host which tools exist, what inputs they accept, which functions run them, which skills are available, and that sandbox internet access is allowed.

**Data flow**: It takes no input. It creates ToolDef objects for js_repl and xlsx_repl, creates SkillSpec entries for the bundled data skills, and returns a Manifest with the extension name, version, tools, skills, and sandbox internet setting.

**Call relations**: The extension loader calls this to discover what the file offers. The ToolDef entries connect outside tool calls to js_repl and xlsx_repl, while the SkillSpec entries expose the on-demand data skills stored under the skills directory.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandbox Tool Bridge
Sandboxed code requests approved tool listing, inspection, and execution through a durable bridge into the main turn loop.

### `core/src/ufo/loop/tool_bridge.py`

`orchestration` · `request handling`

A sandbox is a controlled place where an agent can run, but it should not be able to call every system tool directly. This file is the bridge. It checks what the sandbox is allowed to use, records a requested tool call in the database, sends that work to the normal turn queue, and waits for the result.

The central class, ToolBridge, works like a reception desk. First it checks that the parent turn, meaning the live agent turn that owns this sandbox run, is still running. If the sandbox only asks to list tools or fetch a tool schema, the bridge answers immediately using the tools allowed for that agent or subagent. If the sandbox asks to run a tool, the bridge creates a new conversation and turn for that tool call. It uses stable identifiers so repeating the same request is safe, but reusing the same request id for different input is rejected.

After the database record is written, the bridge asks DBOS, a durable workflow queue, to process the new turn. If enqueueing fails, it clears the “enqueued” marker so another recovery path can pick it up later. Finally, it tails the turn output until it sees a terminal result or a parked failure, then converts that into a success or failure response for the sandbox.

#### Function details

##### `ToolBridge.request`  (lines 55–89)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the main entry point for a sandbox tool-bridge request. It answers simple requests, such as listing tools or returning a tool's input schema, and it dispatches real tool calls through the durable turn loop.

**Data flow**: It receives a RunToken, which identifies the live sandbox run, and a ToolBridgeRequest, which says what the sandbox wants. It looks up the parent turn, checks whether the requested tool is available, and either returns an immediate success or failure. For an actual tool call, it records the call as a new turn, enqueues that turn for processing, waits for the terminal result, and returns a ToolBridgeResponse.

**Call relations**: This function coordinates the whole file. It asks ToolBridge._parent for the live parent turn, uses ToolBridge._allowed to filter or approve tools, calls ToolBridge._admit to write the durable database records, hands the new turn to ToolBridge._enqueue, and finally waits through ToolBridge._terminal for the result.

*Call graph*: calls 5 internal fn (_admit, _allowed, _enqueue, _parent, _terminal); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `ToolBridge._parent`  (lines 91–120)

```
async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None
```

**Purpose**: This function finds the currently running parent turn that gave the sandbox its authority. Without this check, a sandbox could try to keep making tool calls after its owning turn had already ended.

**Data flow**: It receives a RunToken containing the workspace and turn ids. It opens a workspace database transaction, joins the turn, agent, and conversation records, and only accepts a turn whose status is running. It returns the matching database row, including agent permissions and conversation details, or returns nothing if there is no live parent.

**Call relations**: ToolBridge.request calls this first for every bridge request. The returned parent row becomes the source of truth for later permission checks in ToolBridge._allowed and for the new child turn created by ToolBridge._admit.

*Call graph*: called by 1 (request); 2 external calls (select, workspace_tx).


##### `ToolBridge._allowed`  (lines 122–129)

```
def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool
```

**Purpose**: This function decides whether a specific tool may be used by the agent or subagent behind the sandbox request. It protects tools from being exposed outside the permission rules set for that agent.

**Data flow**: It receives the parent turn's database row and a ToolDef describing one tool. If the parent is a normal agent, it checks the agent's allowed tool list, or allows all tools when no list is set. If the parent is a subagent, it looks up the subagent profile, combines its own tool names with any grants when allowed, and also considers default subagent tools. It returns true or false.

**Call relations**: ToolBridge.request uses this both when building the tool list and when approving a specific tool call or schema request. It relies on the SubagentRegistry and grant map stored on the ToolBridge instance.

*Call graph*: called by 1 (request).


##### `ToolBridge._admit`  (lines 131–226)

```
async def _admit(self, run: RunToken, parent: sa.Row[tuple[object, ...]], request: ToolBridgeRequest) -> tuple[UUID, UUID] | None
```

**Purpose**: This function turns an approved sandbox tool call into durable database work. In plain terms, it writes down the tool request as a new turn so the normal system can process it reliably.

**Data flow**: It receives the sandbox run, the parent turn row, and the request. It creates stable ids for the child conversation and turn from the workspace, parent turn, and request id. Then it builds the tool-call intent, opens a database transaction, locks and rechecks that the parent turn is still running, inserts the child conversation and queued turn if they do not already exist, verifies that an existing turn matches the same request, and marks it ready to enqueue. It returns the new turn id and conversation id, or nothing if the parent stopped running.

**Call relations**: ToolBridge.request calls this after permission checks and before queueing. It uses the parent data found by ToolBridge._parent and prepares the exact turn that ToolBridge._enqueue will send to DBOS. Its idempotency check is important because retries should repeat the same call, not silently change it.

*Call graph*: called by 1 (request); 8 external calls (__init__, TypeAdapter, select, update, workspace_tx, current_traceparent, turn_id_for, uuid5).


##### `ToolBridge._enqueue`  (lines 228–257)

```
async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This function asks the durable workflow queue to process the newly admitted tool turn. It also leaves the database in a recoverable state if queueing is interrupted or fails.

**Data flow**: It receives the workspace id, turn id, and conversation id. It builds queue options that name the turn queue, workflow, workflow id, partition key, and app version, then calls DBOS to enqueue the work. If the task is cancelled or an exception happens, it clears the turn's dispatch_enqueued_at marker while the turn is still queued; on ordinary exceptions it also logs that enqueueing was deferred.

**Call relations**: ToolBridge.request calls this after ToolBridge._admit has created the queued turn. It hands the work to DBOS, which is the outside system responsible for running the turn workflow. If enqueueing fails, the later turn-processing machinery can still find the unmarked queued turn.

*Call graph*: called by 1 (request); 3 external calls (update, workspace_tx, log).


##### `ToolBridge._terminal`  (lines 259–267)

```
async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse
```

**Purpose**: This function waits for the dispatched tool turn to finish and converts the streamed ending into a bridge response. It is the point where the sandbox call blocks until there is a final answer or a clear failure.

**Data flow**: It receives the child turn id and opens a tail stream for that turn. As frames arrive, it looks for a Terminal frame, meaning the turn ended, or a Parked frame, meaning the turn cannot continue and has a message. A terminal frame is passed to ToolBridge._response; a parked frame becomes a failure response. If the stream ends without either, it raises an error because that should not happen.

**Call relations**: ToolBridge.request calls this after enqueueing the child turn. It uses the TurnTailer supplied to ToolBridge and delegates final success-or-failure formatting to ToolBridge._response.

*Call graph*: calls 1 internal fn (_response); called by 1 (request); 1 external calls (__init__).


##### `ToolBridge._response`  (lines 269–281)

```
def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse
```

**Purpose**: This function converts a finished turn's terminal record into the response shape expected by the sandbox. It separates failed turns from successful ones and makes successful output safe JSON-like data.

**Data flow**: It receives a TerminalFrame. If the terminal status is not done, it builds a readable error message from the error class, error message, or terminal text and returns a failure. If the turn succeeded, it tries to parse the terminal text as JSON; if that is not valid JSON, it keeps the text as-is. It then validates the value as JSON-compatible data and returns a success response.

**Call relations**: ToolBridge._terminal calls this when it sees the dispatched tool turn finish. Its output becomes the final ToolBridgeResponse returned all the way back through ToolBridge.request to the sandbox caller.

*Call graph*: called by 1 (_terminal); 4 external calls (__init__, __init__, loads, TypeAdapter).


### `core/src/ufo/tools/bridge.py`

`data_model` · `live-turn sandbox bridge setup and request handling`

The sandbox tool bridge is a controlled doorway between a live run and tools it may call. This file describes the messages that can pass through that doorway, so both sides agree on the same simple rules: a request can ask to list tools, fetch one tool’s schema, or execute a tool; a response is either a success with a result or a failure with an error message.

Most of the file is made of small Pydantic models. Pydantic is a validation library: it checks that incoming JSON-like data has the right shape before the rest of the system trusts it. For example, a “list” request is not allowed to include a tool name or arguments, while “get schema” and “execute” requests must name a tool. This prevents unclear or contradictory requests from entering the bridge.

The file also defines the fixed set of bridge tool names, such as object inspection and external tool calls. Think of this like a guest list at a secure door: even if an extension advertises many tools, only the approved names are admitted. The bridge_tools function gathers the built-in object tools, adds matching tools from extension manifests, and runs them through ToolRegistry as a sanity check so duplicate or invalid tool definitions are caught early.

#### Function details

##### `ToolBridgeRequest._matches_action`  (lines 50–56)

```
def _matches_action(self) -> 'ToolBridgeRequest'
```

**Purpose**: This checks that a bridge request makes sense for the action it asks for. It keeps malformed requests out, such as a tool-listing request that also tries to pass arguments.

**Data flow**: It reads the request’s action, optional tool name, and argument dictionary after the basic fields have already been parsed. If the action is “list”, it requires the request to have no tool name and no arguments. If the action is “get_schema” or “execute”, it requires a tool name. A valid request comes out unchanged; an invalid one is rejected with a clear error.

**Call relations**: This is called automatically by Pydantic when a ToolBridgeRequest is created or validated. It acts as the bridge’s first checkpoint before any requester or tool execution code sees the request.


##### `ToolBridgeRequester.request`  (lines 90–90)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is a promise that any bridge requester implementation must fulfill: given a live run identity and a validated bridge request, it must return either a success result or an error. It is not the implementation itself; it is the interface other code can rely on.

**Data flow**: It receives a RunToken, which identifies the signed live run allowed to make the request, and a ToolBridgeRequest, which says what tool action is wanted. An implementation will use those inputs to describe or run a bridge tool. It returns a ToolBridgeSuccess with JSON-like result data or a ToolBridgeFailure with an error message.

**Call relations**: Other parts of the system can depend on this protocol without caring which concrete requester is underneath. During a live sandbox turn, code can call request on whatever implementation it was given, and that implementation is responsible for doing the actual bridge work.


##### `bridge_tools`  (lines 93–106)

```
def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: This builds the actual list of tools that are allowed to appear on the bridge. It combines built-in object tools with approved tools found in extension manifests, then validates the combined set.

**Data flow**: It receives a tuple of manifests, where each manifest may declare tools directly and through connectors. It first creates the built-in object tools from ObjectVerbs. Then it scans every manifest and connector tool, keeping only tools whose names are on the bridge’s approved-name list. It combines the built-in and approved extension tools, passes them to ToolRegistry to check that the collection is valid, and returns the tools as a tuple.

**Call relations**: This function is used when the bridge tool set is being prepared. It calls ObjectVerbs to get the core object-related actions, then calls ToolRegistry as a validation step before handing the final tool list back to the caller.

*Call graph*: 2 external calls (__init__, __init__).


### Built-in Tool Surface
The built-in tool implementations connect model tool calls to controlled shell, file, search, sharing, delegation, skill, credential, and account workflows.

### `core/src/ufo/tools/builtins.py`

`domain_logic` · `request handling`

This file is like the agent's toolbox and rulebook in one place. It tells the system which tools exist, what input each tool accepts, and what should happen when the agent calls one. The file is careful about safety because these tools can touch files, run commands, share outputs, and request private access. File operations go through the sandbox, which is an isolated workspace that limits what the agent can reach. Reads, writes, edits, glob searches, and grep searches use an in-sandbox file command so large files are cut down before results return to the main process. Edits and overwrites are guarded: the agent must have read the path in the current turn before changing it, which helps prevent blind damage. Shell commands can keep running in the background instead of being killed when the caller stops waiting. Sharing files is also tightly controlled. Files are measured, uploaded to the blob store, recorded in the database, and returned as temporary download links; this is the one intended way for workspace files to leave the sandbox. The file also supports higher-level collaboration: spawning child agents, messaging or cancelling them, asking the user for missing information in chat, privately requesting credentials, connecting external accounts, and loading skill instructions. Without this file, the agent would have no standard, safe way to act on the workspace or interact with users and subagents.

#### Function details

##### `_bounded_file_path`  (lines 169–172)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the small JSON result envelope used by file tools. This prevents a huge path string from making later tool responses too large or awkward to return.

**Data flow**: It receives a path string, measures how long that path becomes when encoded as JSON, and either returns the original path unchanged or raises an error if it is too large.

**Call relations**: This is used as a validation step for file path inputs before write and edit tools accept them. It relies on JSON encoding to measure the path the same way it would appear in tool output.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 346–377)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandbox and returns its output, error, timeout notice, or background task details. It gives the agent controlled command-line access without letting the command escape the sandbox rules.

**Data flow**: It receives the tool context and a command request. If the request asks for background mode, it delegates to the background launcher. Otherwise it rejects simple long sleeps, starts the task, waits up to the requested limit, and turns the result into text for the agent. If the command failed, the returned tool result is marked as an error.

**Call relations**: This is the main handler for the built-in bash tool. It calls _bash_background when the agent wants an immediate detached run, otherwise it uses the shared task-running helpers and wraps their outcome into a ToolResult.

*Call graph*: calls 1 internal fn (_bash_background); 7 external calls (__init__, __init__, format, flat_sleeps, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 380–394)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command as a detached background task and immediately returns the information needed to find its logs and status later. This is useful for long work such as builds or tests that should continue while the agent does other things.

**Data flow**: It receives the context and command text, creates a new task id and runtime paths, asks the sandbox to start the command detached, and returns task handles if startup succeeded. If the command could not detach, it returns an error message.

**Call relations**: bash_handler calls this when the bash input asks for background execution. It shares the same task handle format as foreground commands that outlive their wait period.

*Call graph*: called by 1 (bash_handler); 4 external calls (__init__, __init__, task_handles, task_id).


##### `_require_str`  (lines 397–400)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Makes sure a value from a sandbox file-read result is a real, non-empty string. It gives clearer failures when the sandbox returns malformed image or document data.

**Data flow**: It receives an unknown value and the name of the field it should represent. If the value is a non-empty string, it returns it; otherwise it raises a runtime error naming the missing field.

**Call relations**: read_handler and _document_result call this while building image and document responses. It is a small guard between loose JSON-like data from the sandbox and the stricter content objects sent back to the agent.

*Call graph*: called by 2 (_document_result, read_handler).


##### `_document_result`  (lines 403–446)

```
def _document_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a structured document read result, such as PDF, PowerPoint, Word, or Excel, into text notes and page images the agent can inspect. It gives the agent a bounded preview rather than dumping a whole document.

**Data flow**: It receives a result dictionary from the sandbox, extracts any text, page counts, paging hints, notes, and rendered page images, and returns a ToolResult containing text blocks and image blocks. If required fields are missing or the result is empty, it raises an error.

**Call relations**: read_handler calls this after the sandbox reports that the file is a document type. It uses _require_str to validate image fields before creating ImageContent entries.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 449–486)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a file from the sandboxed workspace and returns an appropriate bounded view: text lines, an image, or document preview pages. It also records that the path was read so later writes or edits can be allowed safely.

**Data flow**: It receives a file path plus optional offset and limit, sends those to the sandbox file reader, and adds the path to the current turn's read set. Depending on the returned file type, it builds image content, document content, an empty-file notice, or text with line-range footer information.

**Call relations**: This is the built-in read tool's handler. It hands document results to _document_result and uses _require_str when converting sandbox image data into the content format returned to the agent.

*Call graph*: calls 2 internal fn (_document_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 489–511)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file while preventing accidental overwrites of files the agent has not read this turn. It is meant for creating files or replacing files the agent has already inspected.

**Data flow**: It receives a path and text content, writes the bytes to a temporary staged file in the sandbox, then asks the sandbox file tool to move that staged content to the requested path. It tells the sandbox whether overwriting is allowed based on the path's read history, adds size and line counts to the result, records the path as read, and returns a bounded JSON summary.

**Call relations**: This is the write tool's handler. It uses _file_tool_result to keep the returned summary within the tool output limit.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 514–527)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact string replacements to a file, but only after the file has been read in the current turn. This protects against blind edits where the agent guesses content it has not seen.

**Data flow**: It receives a target path and a list of replacement instructions. If the path has not been read, it stops with an error. Otherwise it encodes the old and new strings safely, sends the edit request to the sandbox file tool, adds the path to the result, and returns a bounded summary.

**Call relations**: This is the edit tool's handler. It depends on read_handler having recorded the path earlier, and it sends its final sandbox result through _file_tool_result.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 530–544)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats write and edit results as compact JSON that fits within the tool output limit. It avoids returning large diffs or snippets when those would overflow the response budget.

**Data flow**: It receives a result dictionary, serializes it to JSON, and returns it if it is small enough. If it is too large, it removes the potentially bulky snippet and shortens the message, then serializes again or raises an error if the result still cannot fit.

**Call relations**: write_handler and edit_handler both use this after the sandbox file tool finishes. It is the final size guard before the result is returned to the agent.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 547–553)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds workspace files whose names match a glob pattern, such as '**/*.py'. It lets the sandbox do the directory walking so only the matched paths come back.

**Data flow**: It receives a pattern and optional search directory, defaults the directory to the workspace root, asks the sandbox file tool to perform the match, and returns the resulting path list as JSON text.

**Call relations**: This is the glob tool's handler. It does not scan files itself; it delegates the work to the sandbox's ufo fs command and wraps the result for the agent.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 556–574)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents in the workspace for a regular expression, which is a text pattern language. It keeps the expensive scan inside the sandbox and returns only a capped result.

**Data flow**: It receives the search pattern, optional path, file filter, context options, case-sensitivity setting, output mode, and result limit. It builds a parameter object, fills in a default result cap when needed, runs the sandbox grep command, and returns the matches as JSON text.

**Call relations**: This is the grep tool's handler. Like glob_handler, it delegates the actual search to the sandbox file command instead of using the main process to inspect files.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 577–611)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies a prepared file from the sandbox into the configured artifact store, either S3 or a local filesystem store. It is designed so large files stream out without being loaded whole into the main service.

**Data flow**: It receives a sandbox path, destination blob key, measured size, and SHA-256 digest, which is a fingerprint of the file bytes. For S3, it creates a short-lived upload URL bound to that size and digest and has the sandbox upload with curl. For a filesystem store, it streams bytes from the sandbox into the blob store.

**Call relations**: _staged_share calls this after preflight measurement has proven what file is being shared. It is the low-level storage step in the share_file flow.

*Call graph*: called by 1 (_staged_share); 3 external calls (b64encode, quote, shell_path).


##### `_shared_preview`  (lines 623–679)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str) -> ArtifactPreview | None
```

**Purpose**: Tries to create a small preview image for shareable document types, so users can see a thumbnail-like first page instead of only a filename. A preview failure never blocks sharing the actual file.

**Data flow**: It receives the context, the sandbox path to the stored file, and a safe display name. If the extension supports previews and the blob store can accept preview uploads, it asks the preview service to render the first page into a PNG stored at a new blob key. It returns preview metadata, or None if previews are unsupported or rendering fails.

**Call relations**: _staged_share calls this after uploading the main file. It logs preview problems but deliberately does not raise for normal rendering failures, because the user's download should still be delivered.

*Call graph*: called by 1 (_staged_share); 8 external calls (__init__, dumps, loads, PurePosixPath, quote, log, shell_path, uuid4).


##### `_packed_directory`  (lines 697–726)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a directory selected for sharing into a .tar.gz archive inside the sandbox. This lets users share whole folders without the agent manually creating an archive first.

**Data flow**: It receives a sandbox path, checks whether that path is a real directory rather than a symbolic link, and returns None if it is not a directory. If it is a directory, it creates an output archive path, runs tar inside the sandbox, and returns the archive path.

**Call relations**: _staged_share calls this before measuring or uploading a shared path. It ensures the rest of the sharing pipeline can treat both files and directories as one regular file.

*Call graph*: called by 1 (_staged_share); 3 external calls (quote, shell_path, uuid4).


##### `_staged_share`  (lines 729–771)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec) -> _StagedShare
```

**Purpose**: Prepares one requested file or directory for sharing by naming it safely, measuring it, uploading it, and optionally creating a preview. It gathers everything needed to later write the database row and return the download link.

**Data flow**: It receives one shared file specification. It scopes the path to the workspace, packs it if it is a directory, runs a preflight command to get size, digest, and text-ness, chooses a safe filename, uploads the file through _store_artifact, tries _shared_preview, and returns a _StagedShare record.

**Call relations**: share_file_handler calls this once for each requested file before any database records are created. It coordinates _packed_directory, _store_artifact, and _shared_preview as the staging pipeline.

*Call graph*: calls 3 internal fn (_packed_directory, _shared_preview, _store_artifact); called by 1 (share_file_handler); 7 external calls (__init__, loads, guess_type, PurePosixPath, shell_path, workspace_path, uuid4).


##### `share_file_handler`  (lines 774–846)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares one or more workspace files with the user by storing them as artifacts, recording them in the database, and returning temporary download URLs. This is the controlled exit door for files produced in the sandbox.

**Data flow**: It receives the share request, verifies artifact sharing is configured, stages every file, then opens a workspace database transaction and inserts one shared-artifact row per file in the requested order. It then computes stable artifact object names, mints expiring URLs, and returns a JSON list with each file's URL, name, size, digest, and text flag.

**Call relations**: This is the share_file tool's handler. It depends on _staged_share for the upload work, then uses the database and artifact URL helpers to make the files visible to chat surfaces and users.

*Call graph*: calls 1 internal fn (_staged_share); 13 external calls (__init__, __init__, now, timedelta, dumps, insert, select, workspace_tx, artifact_object_names, artifact_media_type (+3 more)).


##### `_spawn_handles`  (lines 861–867)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Builds the message returned when a spawned child agent is running in the background. It gives the parent agent the child turn id and explains how the result will arrive later.

**Data flow**: It receives the target name, child turn id, and a flag saying whether the spawn was moved to the background because a new message arrived. It chooses the right lead text, creates a small JSON status payload, and returns one combined text message.

**Call relations**: spawn_handler calls this whenever a child has no immediate output to return. It keeps the background-spawn wording consistent for both explicitly detached and message-interrupted runs.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 870–913)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates work to a named child agent or subagent profile and returns either its validated output, its question, or background tracking details. This lets the main agent split off specialized or long-running work.

**Data flow**: It receives a target name, payload, background flag, and display name, then asks the ToolContext to start the child turn. If the target name is invalid or ambiguous, it returns a recoverable error. If the child asks a question, it returns that question. If the child is still running in the background, it returns spawn handles. Otherwise it returns the child's validated JSON output.

**Call relations**: This is the spawn tool's handler. It calls the context's spawn mechanism for the real child-turn workflow and uses _spawn_handles for background status text.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 921–932)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Packages one or more questions for the agent to ask the user in its next chat reply. It keeps the interaction chat-native instead of opening a separate prompt channel.

**Data flow**: It receives a structured question request, builds a JSON payload containing the title, optional icon, and question records, and returns it with instructions telling the agent to ask in its reply and end the turn.

**Call relations**: This is the ask_user tool's handler. It does not contact the user directly; it returns structured content that the surrounding chat surface and model response use to present the question.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 935–947)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill's instructions and files, including any skills it depends on, into the sandbox. Skills are reusable task guides, like a recipe plus supporting files.

**Data flow**: It receives a skill name, asks the skill manager for the dependency closure, materializes those skill files, installs them in the sandbox, builds the text context describing the loaded workflows and file tree, and returns that text.

**Call relations**: This is the load_skill tool's handler. It uses the skill runtime helpers to copy files into place and produce the context the agent will read.

*Call graph*: 4 external calls (__init__, __init__, load_skills, loaded_context).


##### `skill_search_handler`  (lines 953–970)

```
async def skill_search_handler(ctx: ToolContext, args: SkillSearchInput) -> ToolResult
```

**Purpose**: Searches the available skill routing cards by keyword and returns the best matching skill names and descriptions. It helps the agent discover useful skills that were not already visible in the prompt.

**Data flow**: It receives a query and limit, scores every available skill card against the query, sorts them from best to worst, keeps positive matches up to the limit, and returns short 'name: description' lines. If nothing matches, it returns a message saying how many skills were searched.

**Call relations**: This is the skill_search tool's handler. It relies on the lexical scoring helper and feeds the result names back to the agent so it can call load_skill next.

*Call graph*: 3 external calls (__init__, __init__, lexical_score).


##### `_grantee_agent_id`  (lines 979–1003)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out whether an external account connection should be granted to another agent, and enforces that only the workspace main agent may do that. This prevents ordinary agents from changing another agent's access.

**Data flow**: It receives the current context and an optional agent name. If no name is given, it returns None, meaning the grant is for the asking agent. If a name is given, it reads active agents from the workspace database, verifies the current agent is the main one, finds the named target, and returns that target's id unless it is the same as the current agent.

**Call relations**: connect_account_handler calls this before creating the connection request. It uses a workspace transaction and SQL query to resolve names into durable agent ids.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 1006–1020)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Creates a structured request for the speaking member to connect an external account through a private OAuth flow. OAuth is the standard web authorization handoff where a user grants access without giving their password to the app.

**Data flow**: It receives the provider name, sharing choice, and optional agent name. It verifies there is a speaking member, resolves any grantee agent, validates that the provider is installed, builds a ConnectRequest, and returns it with instructions not to expose an authorization URL in chat.

**Call relations**: This is the connect_account tool's handler. It calls _grantee_agent_id for agent-target rules and the installed connection flow to validate the provider before handing the request to the chat surface.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1030–1053)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin user to provide secret values, such as API keys, through a private prompt instead of chat. This keeps secrets out of the conversation transcript.

**Data flow**: It receives a reason and a small list of credential prompts. It checks that there is a speaking member, that secret storage is configured, and that the speaker is a workspace admin. It then seals the workspace, member, and requested slots into a tamper-resistant token, builds a CredentialRequest, and returns it with instructions for the agent to end the turn.

**Call relations**: This is the request_credentials tool's handler. It calls the context's admin check and uses the configured credential sealer before returning structured content for a capable user interface.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1056–1068)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a child agent run that this turn spawned, or reports its existing status if it has already finished. It gives the parent agent a way to stop delegated work.

**Data flow**: It receives a spawn id string, checks that spawn control is available, converts the id to a UUID, asks the subagent controller to cancel it, and returns JSON with the spawn id and resulting status.

**Call relations**: This is the cancel_spawn tool's handler. It talks to ctx.subagents, the same subsystem used by spawn_handler, and only works in contexts where spawn control has been provided.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1071–1089)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a spawned child agent, often to answer a question the child asked. The message becomes the child agent's next turn.

**Data flow**: It receives a spawn id and message text, checks that spawn control and an idempotency key are available, converts the id to a UUID, queues the message through the subagent controller, and returns JSON with the spawn id and new status.

**Call relations**: This is the message_spawn tool's handler. It uses ctx.subagents to continue a child run created by spawn_handler, and the idempotency key helps avoid sending the same follow-up twice.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Tool Execution Foundations
The tools package defines the execution context, catalog entries, lookup behavior, and durable task tracking used by built-ins and extensions.

### `core/src/ufo/tools/__init__.py`

`other` · `cross-cutting`

This file does not contain working code. Its job is mostly organizational: it turns the surrounding folder into an importable Python package and documents, in one sentence, what that package is about. In plain terms, this is like the sign on a drawer that says what should be inside. The drawer is for “tools,” meaning pieces of functionality the wider system can look up, prepare, and run. The docstring points to three main ideas: a registry, which is a list or directory of available tools; a handler context, which is the information a tool needs while it is being run; and the built-in tool set, which are the tools provided by the project itself. Without this file, depending on the Python packaging setup, other parts of the project might not be able to import this folder cleanly as `ufo.tools`, and newcomers would lose a small but useful clue about the folder’s purpose.


### `core/src/ufo/tools/context.py`

`orchestration` · `per-turn tool execution`

A tool in this system should not be able to reach everything directly. This file solves that by giving each tool a carefully scoped bundle of capabilities, called ToolContext. Think of it like a guest badge: it lets the tool enter only the rooms it is allowed to enter, and it records who the tool is acting for.

The file defines the shapes of tool outputs, including text and images, plus result types for child turns called subagents. It also defines clear errors for cases where a tool tries to spawn a subagent target that does not exist or is ambiguous.

The central class is ToolContext. It contains the current turn, agent, conversation audience, sandbox, blob storage, permissions, extension context, model choices, browser and search providers, and cleanup hooks. Its methods answer practical questions such as “who is this tool acting as?”, “what can this conversation read?”, “is the speaker an admin?”, and “which connected account may this tool use?”

It also provides safe helper actions. A tool can store a generated preview, share a small artifact file, charge image or video generation to the turn’s billing ledger, start credential authorization, or select a connector account without seeing private tokens. Without this file, tool code would need to repeat sensitive permission and storage logic, which would make leaks, duplicate side effects, and resource cleanup bugs much more likely.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 104–109)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when code asks for a subagent profile that is not registered. The message includes both the bad name and the valid choices so the caller can recover instead of seeing a vague lookup failure.

**Data flow**: It receives the requested profile name and the tuple of registered names. It turns those into a human-readable error message and stores both pieces of information on the exception object. The result is an exception ready to be raised and later inspected.

**Call relations**: The subagent registry calls this when a profile lookup fails. It hands back an error that can be shown or logged with enough detail to guide the next attempt.

*Call graph*: called by 1 (get).


##### `UnknownSpawnTarget.__init__`  (lines 116–123)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when a spawn request names something that is neither a known subagent profile nor a known workspace agent. It tells the caller what names are actually available.

**Data flow**: It receives the requested target name, the available profile names, and the available agent names. It formats them into one explanatory exception message and stores them as fields. The output is an exception object that carries both readable text and structured facts.

**Call relations**: The subagent spawning resolver calls this after it cannot match a requested target. The error gives the model or caller enough information to retry with a valid target.

*Call graph*: called by 1 (_resolve).


##### `AmbiguousSpawnTarget.__init__`  (lines 130–135)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error for the case where a spawn target name matches both a profile and an agent. It tells the caller to use a qualified name, such as profile:name or agent:name.

**Data flow**: It receives the ambiguous name. It creates an exception message explaining the conflict and stores the requested name on the exception. The result is an exception that points to the two exact disambiguation options.

**Call relations**: The subagent resolver calls this when a bare target name is not specific enough. It stops the system from guessing wrong and asks the caller to choose explicitly.

*Call graph*: called by 1 (_resolve).


##### `Spawn.__call__`  (lines 201–210)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnRes
```

**Purpose**: Describes the callable interface used to start a child turn, either as a subagent profile or another workspace agent. A tool uses it to delegate a typed piece of work and optionally wait for the result.

**Data flow**: The caller provides a target name, a payload, and options such as whether the child should run in the background, whether repeated attempts should reuse the same child, and whether the child should deliver its own result. The implementation validates and starts or reconnects to the child turn. It returns a SpawnResult describing the child and, when available, its finished output.

**Call relations**: This is a protocol, meaning it defines what the real spawning service must provide. ToolContext carries an implementation so tool handlers can spawn work without knowing the internal subagent machinery.


##### `SubagentControl.result`  (lines 220–220)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes how to fetch the final result of an already-spawned background subagent. A tool uses it when it knows the child turn id and wants the finished output.

**Data flow**: It takes a child turn id. The implementation looks up that child, reads its terminal state and validated output if it is done, and returns a SpawnResult. It does not create a new child; it reports on an existing one.

**Call relations**: This method is part of the SubagentControl protocol placed on ToolContext. Lifecycle tools use it to inspect child work that was started earlier.


##### `SubagentControl.wait`  (lines 222–222)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how to wait inside a tool call for one or more background subagents to reach a reportable state. It gives the caller a bounded way to pause for child progress.

**Data flow**: It receives a tuple of child turn ids. The implementation waits according to its own rules and returns a tuple of SubagentStatus objects summarizing each child’s state and text. It changes no payload; it reports status.

**Call relations**: This is used through ToolContext by tools that coordinate background subagents. It complements spawning by letting later tool calls check or wait for existing children.


##### `SubagentControl.cancel`  (lines 224–224)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how to stop a running background subagent. A tool uses it when child work is no longer needed or should be interrupted.

**Data flow**: It takes a child turn id. The implementation requests cancellation of that child and returns a SubagentStatus showing the resulting state and message. The main side effect is that the child turn is stopped or marked as cancelled.

**Call relations**: This belongs to the SubagentControl protocol exposed on ToolContext. It lets tool handlers control child turns without touching the lower-level turn loop directly.


##### `SubagentControl.message`  (lines 226–228)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Describes how to send a follow-up message to a background subagent in an idempotent way. Idempotent means a retry with the same key should not accidentally send the same instruction twice.

**Data flow**: It receives a child turn id, message text, a deduplication key, and a flag saying whether the child will deliver its own result. The implementation admits that message to the child and returns its updated status. The before state is an existing child; the after state is that the child has received a controlled follow-up.

**Call relations**: This protocol method is used by subagent lifecycle tools. It relies on the same child-turn system that implements spawning, but gives later calls a way to continue the conversation.


##### `TurnCleanup.register`  (lines 242–243)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to be run when the turn ends. Tools use this after opening a per-turn resource, such as a browser connection, so it will not be leaked.

**Data flow**: It receives a no-argument async closer function. It appends that closer to the cleanup list. Nothing is returned; the cleanup registry has one more task waiting for turn teardown.

**Call relations**: Tool code registers closers here during execution. Later, the turn loop calls TurnCleanup.drain to run them all.


##### `TurnCleanup.drain`  (lines 245–251)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions at the end of a turn. It closes resources in reverse order, like stacking dishes and washing the top one first.

**Data flow**: It reads the stored closer functions, pops them one by one, and awaits each. If a closer fails, it logs the failure instead of stopping the remaining cleanup. The output is no return value; the important result is that the registry is emptied and resources are closed.

**Call relations**: The turn loop drains this registry during teardown. It calls the shared logging helper when cleanup fails, so a bad close is visible without preventing other resources from being closed.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 296–304)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Figures out which workspace member this tool call is allowed to act for. It prefers the live speaker, and falls back to the member carried by a scheduled or delegated turn.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the context. If there is a current speaker, it returns that member id; otherwise it returns the on-behalf-of member id, which may also be absent. It changes nothing.

**Call relations**: Other ToolContext methods use this property when deciding readable subjects and connector access. It is the common answer to “whose authority is this?”


##### `ToolContext.effective_audience`  (lines 307–317)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides the exact audience label to use when the tool writes something. This prevents private or cross-organization information from being stamped as broadly shared by mistake.

**Data flow**: It reads the acting member and the current conversation audience. If there is no acting member, or the current audience is not the workspace-shared audience, it returns the current audience. If the conversation is workspace-shared and there is an acting member, it returns that member’s conversation-specific audience.

**Call relations**: It calls the audience helper that builds a member conversation audience. Write paths can use this property to label new data with the correct memory or visibility scope.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 320–329)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool may read from. A subject is a visibility label used to decide which memories or records belong to this conversation and requester.

**Data flow**: It starts with the subjects for the current audience. If there is an acting member, it adds that member’s private subject. It returns the combined set as an immutable frozenset and does not change the context.

**Call relations**: It calls helpers that translate audiences and members into subject labels. Source and memory readers use this to avoid showing information outside the conversation’s allowed scope.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.store_preview`  (lines 331–377)

```
async def store_preview(self, sandbox_path: str, name: str, *, extension: str='png') -> StoredPreview | None
```

**Purpose**: Stores an image file that a tool rendered inside the sandbox as a preview artifact. If the image cannot be read, it quietly returns None because previews are optional decoration.

**Data flow**: It receives a sandbox path, display name, and file extension. It asks the sandbox to measure the file size; if that fails, it logs the problem and returns None. It creates a fresh artifact key, then either uploads the file through a presigned S3 URL from inside the sandbox or streams it into a local blob store. On success it returns a StoredPreview containing the blob key and measured size.

**Call relations**: The sites extension calls this after composing share-card images. This method coordinates the sandbox, blob store, shell path quoting, unique key creation, and logging so extension code does not have to implement safe preview storage itself.

*Call graph*: called by 1 (_compose); 5 external calls (__init__, quote, log, shell_path, uuid4).


##### `ToolContext.render_site_preview`  (lines 379–386)

```
async def render_site_preview(self, name: str, port: int, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Asks the configured preview service to take a screenshot-like preview of a website running from the turn’s sandbox. If no preview service is configured, it returns None.

**Data flow**: It receives a name, hosted port, width, and height. It chooses the conversation id used for sandbox hosting, then passes that plus the render settings to the site previewer. The result is either a StoredPreview from the preview service or None.

**Call relations**: The sites extension calls this when it needs an illustration of a running site. ToolContext supplies the correct conversation identity and hides the details of the preview service.

*Call graph*: called by 1 (_illustrate).


##### `ToolContext.source_reader`  (lines 388–398)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Builds a SourceReader that describes who is asking to read synced source pages and what they may read. This keeps source access tied to the current agent, live requester, and audience scope.

**Data flow**: It reads the turn’s agent id, the current speaker member id, and the computed read subjects. It creates and returns a SourceReader with those values. It does not fetch pages itself; it prepares the access description used by source-reading code.

**Call relations**: Memory and sources extensions call this before searching, listing, or fetching pages. It hands them a consistent permission envelope instead of making each extension rebuild the rules.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 400–409)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images against this turn’s billing ledger. This lets image-provider extensions charge usage without owning the core billing write.

**Data flow**: It receives the provider model name, number of images, and cost in micro-dollars. It opens a workspace database transaction and writes an image usage record tied to the workspace and turn. It returns nothing; the database ledger is updated.

**Call relations**: The OpenRouter image extension calls this after image generation. This method hands off to the shared billing accounting function inside a workspace transaction.

*Call graph*: called by 1 (generate); 2 external calls (record_image_usage, workspace_tx).


##### `ToolContext.meter_videos`  (lines 411–419)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos against this turn’s billing ledger. It gives video extensions the same core billing path used for other metered work.

**Data flow**: It receives the provider model name, number of videos, and cost in micro-dollars. It opens a workspace database transaction and writes a video usage record tied to the workspace and turn. It returns nothing; the ledger gains a video charge.

**Call relations**: The OpenRouter video extension calls this after video generation. It delegates the actual ledger insert to the billing accounting layer.

*Call graph*: called by 1 (generate); 2 external calls (record_video_usage, workspace_tx).


##### `ToolContext.share_artifact`  (lines 421–446)

```
async def share_artifact(self, filename: str, data: bytes, subject: str | None=None) -> None
```

**Purpose**: Stores a small in-memory file as a shared artifact of the current turn. Tools use this when they computed bytes directly and want the member-facing surfaces to show or download the file.

**Data flow**: It receives a filename, raw bytes, and an optional visibility subject. It rejects data larger than the shared byte limit. Otherwise it creates a unique artifact key, writes the bytes to blob storage, determines the media type from the filename, and inserts a shared_artifact row in the workspace database. The result is no return value; storage and database records are created.

**Call relations**: The iMessage and sites extensions call this when they need to publish generated files. This method keeps artifact naming, size limits, media typing, timestamps, and database insertion centralized.

*Call graph*: called by 2 (run, render_application_preview); 5 external calls (now, insert, workspace_tx, artifact_media_type, uuid4).


##### `ToolContext.speaker_is_admin`  (lines 448–458)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live speaking member is a workspace admin. Background or speakerless work cannot claim admin rights through this method.

**Data flow**: It reads speaker_member_id. If there is no live speaker, it returns False. Otherwise it opens a workspace database transaction and asks the seats subsystem whether that member is an admin in this workspace.

**Call relations**: Many object and credential operations call this before allowing workspace-wide actions. The credential authorization helper also uses it to enforce that only admins authorize credential slots.

*Call graph*: called by 25 (list, restore, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization (+15 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 460–471)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current turn’s agent is the workspace’s main agent. Some operations are only visible or allowed from that main agent.

**Data flow**: It opens a workspace database transaction, selects the agent’s is_main flag by agent id and workspace id, and converts the result to a boolean. It returns True or False and does not alter the database.

**Call relations**: Member, workspace, and web audience features call this to decide what the current agent may see or change. It uses a direct database lookup so the answer reflects stored workspace state.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 473–485)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or visible to the workspace. It raises an error if the database contains a value the code does not understand.

**Data flow**: It opens a workspace database transaction and selects the visibility value for the current agent in the current workspace. If the stored value is private or workspace, it returns that value. Otherwise it raises a RuntimeError because the stored value is outside the supported set.

**Call relations**: The sites extension calls this before homepage-related actions. It provides a trusted visibility answer from the database instead of relying only on the context’s in-memory agent object.

*Call graph*: called by 2 (_redeploy_homepage, set_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 487–489)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for an extension credential slot. It returns a sealed authorization string that can later be opened if all permission checks pass.

**Data flow**: It receives a credential slot name and payload. It first calls the shared credential authorization checker to get the credential request service and authorized member id. Then it asks that service to authorize the payload for this workspace, member, and slot, returning the resulting sealed string.

**Call relations**: Coding and Slack extensions call this when they need an admin to authorize external credentials. It reuses _credential_authorization so the same checks apply to starting and opening authorizations.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 491–493)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens a previously sealed credential authorization for an extension credential slot. It verifies the same speaker, extension, deployment, and admin requirements before revealing the authorization payload.

**Data flow**: It receives a credential slot name and sealed authorization string. It calls the shared credential authorization checker, then asks the credential request service to open the sealed value for this workspace, member, and slot. It returns the opened payload string.

**Call relations**: This pairs with begin_credential_authorization. Both route through _credential_authorization so extensions cannot bypass the common credential safety checks.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext._credential_authorization`  (lines 495–504)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the common safety checks needed before credential authorization can start or be opened. It makes sure there is a live speaker, the extension declared the credential slot, credential storage is configured, and the speaker is an admin.

**Data flow**: It reads the speaker id, extension context, declared credential slots, configured credential request service, and admin status. If any requirement fails, it raises a ValueError with a clear reason. If all pass, it returns the credential request service and the speaker member id.

**Call relations**: begin_credential_authorization and open_credential_authorization both call this first. It calls speaker_is_admin to enforce the admin-only rule.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (begin_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 506–515)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the connected-account id that a connector tool should pass to the broker’s server-side execution API. It is a convenience wrapper when the caller only needs the account id, not the full connection details.

**Data flow**: It receives a provider name and optionally a specific account id. It calls connector_connection to resolve and validate the allowed connection, then returns only that connection’s account_id. If no suitable account exists, the called method raises an error.

**Call relations**: Connector execution tools call this before asking the broker to run an external action. It relies on connector_connection for the permission and ambiguity checks.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 517–554)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connected account this turn may use for a provider, including the connection id and owning member. It protects private accounts by selecting only grants available to this turn.

**Data flow**: It receives a provider name and optionally a desired account id. It loads private and shared grant tiers. If a specific account was requested, it returns it only if it appears in an allowed tier. If no account was specified, it prefers the acting member’s private grants, then shared grants; it requires exactly one account in the chosen tier. It returns a ConnectorConnection or raises a clear ValueError.

**Call relations**: connector_account and source registration code call this when they need an actual allowed connection. It calls _connector_account_tiers to gather the private and shared grant lists, then packages the chosen grant into ConnectorConnection.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 556–564)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the connected-account ids this turn may use for one connector provider. It gives tools a safe menu of available accounts without exposing tokens.

**Data flow**: It receives a provider name. It loads private and shared grant tiers, combines them, removes duplicates by account id, sorts the ids, and returns them as a tuple. It does not modify any grants.

**Call relations**: Source tools call this when resolving which account to use. It shares the same grant-filtering helper as connector_connection so listing and selecting follow the same permission rules.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 566–585)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Builds the two lists of connector grants available to this turn: private grants owned by the acting member, and shared grants for the provider. This is the core permission filter for connector account access.

**Data flow**: It receives a provider name and reads the configured GrantStore plus the acting member id. If grants are unavailable, it raises ConnectUnavailable. Otherwise it reads active grants, filters them by provider, separates private owner-matching grants from shared grants, sorts each list by account id, and returns both lists.

**Call relations**: connector_connection and connector_accounts call this before choosing or listing accounts. By centralizing the filtering, connector tools get consistent private-versus-shared behavior.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).


### `core/src/ufo/tools/registry.py`

`data_model` · `startup and tool-call dispatch`

The system lets a model call named tools, such as fetching data, writing somewhere, or asking a follow-up question. This file provides the small set of rules that make those tool calls predictable and safe. A ToolDef is like a catalog card for one tool: it records the tool’s name, human-readable description, input shape, and the actual function that runs it. It also records safety flags. For example, untrusted means the result may contain text controlled by someone outside the system, so the engine should not treat it like instructions. side_effecting means the tool may write to the outside world, so the engine needs extra care to avoid doing the same write twice. parallel_safe says whether multiple calls to this tool can safely run at the same time.

The file also adds a common requested_by field to tool input schemas when needed. This tells the model how to point to the message that gave permission for a member-specific action.

ToolRegistry is the frozen list of available tools. At creation time, it rejects duplicate tool names and rejects tools that try to define the reserved requested_by field themselves. Later, the engine can ask the registry for all wire schemas to show the model, or ask for one tool by name when dispatching a tool call.

#### Function details

##### `ToolDef.schema`  (lines 53–66)

```
def schema(self, *, include_requested_by: bool=True) -> ToolSchema
```

**Purpose**: Builds the tool description that can be sent to the model client. It turns the tool’s input model into a JSON-style schema, optionally adding the shared requested_by permission field.

**Data flow**: It starts with the ToolDef’s stored name, description, and Pydantic input model. It asks the input model for its JSON schema, adds requested_by to the schema’s properties unless told not to, and returns a ToolSchema object containing the tool name, description, and completed input schema.

**Call relations**: This is used when the registry prepares the list of tools the model is allowed to call. It hands the finished schema to ToolSchema.__init__, which packages the information in the format expected by the model-facing interface.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 73–82)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the registry is valid immediately after it is created. It prevents confusing or unsafe tool catalogs, such as two tools with the same name or a tool input model that reuses a reserved field.

**Data flow**: It reads every ToolDef in the registry. First it collects tool names and raises an error if any name appears more than once. Then it checks each tool’s input fields and raises an error if a tool already defines requested_by, because this file owns that shared field. If everything is clean, it changes nothing and construction succeeds.

**Call relations**: This runs automatically as part of creating a ToolRegistry dataclass instance. It protects later code, especially dispatch and schema generation, from having to guess which duplicate tool was meant or how to interpret a conflicting requested_by field.


##### `ToolRegistry.schemas`  (lines 84–85)

```
def schemas(self, *, include_requested_by: bool=True) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the complete set of model-facing schemas for all registered tools. This is how the system turns its internal tool catalog into descriptions the model can understand and use.

**Data flow**: It receives the registry and an option for whether requested_by should be included. It asks each ToolDef to build its own schema with that option, then returns all of those schemas as an immutable tuple.

**Call relations**: This function is the registry-level wrapper around ToolDef.schema. Instead of callers looping through tools themselves, they can ask the registry for the ready-to-send schema list in one step.


##### `ToolRegistry.get`  (lines 87–91)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the ToolDef with a given name so the engine can run the right handler. If no tool has that name, it fails loudly instead of silently doing the wrong thing.

**Data flow**: It takes a tool name as input and scans the registry’s stored ToolDef objects. When it finds a matching name, it returns that ToolDef. If it reaches the end without a match, it raises a KeyError that names the unknown tool.

**Call relations**: During tool-call dispatch, core/src/ufo/loop/engine._dispatch_segments calls this to translate a name from the model’s request into the actual registered tool definition. The returned ToolDef gives the engine the handler to run and the safety flags that affect how the call should be executed.

*Call graph*: called by 1 (_dispatch_segments).


### `core/src/ufo/tools/tasks.py`

`orchestration` · `tool execution / request handling`

This file solves a common problem for tools that run shell commands: some commands finish quickly, while others keep running longer than the current turn should wait. Instead of tying the command’s life to the caller’s patience, the file launches work through a recorded task area under the run directory. Think of it like leaving a package at a staffed counter: the caller gets a receipt, and the package can still be picked up later even if the caller walks away.

The main flow starts with `run_task`. It gives the command a stable task name, starts it in the sandbox, and waits only for the allowed time. If the command finishes, its normal result is returned. If the wait expires, the code checks whether the detached supervisor process is still alive or has already written its exit file. If it is alive, the caller can be given paths and commands for reading the log, watching for completion, or stopping it. If nothing appears alive, the file records diagnostic details about the sandbox, because that suggests the execution channel itself stopped answering.

The file also prevents one wasteful pattern: long, plain `sleep` commands in foreground work. It allows short sleeps and sleeps inside polling loops, but refuses obvious padding that would burn a turn doing nothing.

#### Function details

##### `flat_sleeps`  (lines 33–51)

```
def flat_sleeps(command: str) -> tuple[int, ...]
```

**Purpose**: This function looks for long, plain `sleep` commands that would simply waste the foreground wait time. It ignores quoted text, heredoc text, and sleeps inside shell loops, because those are usually data or part of polling rather than idle padding.

**Data flow**: It takes a shell command as text. It strips out quoted strings and heredoc bodies, scans the remaining text for `sleep`, `do`, and `done`, tracks whether it is inside a loop, and keeps only sleep durations above the allowed short limit. It returns a tuple of the too-long sleep lengths it found.

**Call relations**: This is a guard helper for tools that want to reject commands before running them. It does not launch anything itself; it just gives callers a clear signal that the command contains foreground waiting that should be rewritten as polling, a timeout, or background work.


##### `run_task`  (lines 94–131)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None) -> TaskRun
```

**Purpose**: This is the main launcher for shell work that should survive beyond the caller’s wait. It starts the command through the sandbox task journal, waits for the requested budget, and reports whether the command finished or is still reachable in the background.

**Data flow**: It receives a tool context, the command text, and an optional timeout in milliseconds. It turns the timeout into seconds, caps it at the maximum allowed value, chooses a task id, asks the sandbox for the task file location, and runs the command there. If the command finishes in time, it returns a `TaskRun` with the result and no process id. If the wait times out, it probes the task’s pid and exit file; if the task is still present, it returns a `TaskRun` containing the pid, and if not, it records timeout diagnostics before returning the timed-out result.

**Call relations**: This function coordinates the whole task lifecycle. It calls `task_id` so repeated dispatch attempts can attach to the same recorded task, and it calls `_record_exec_timeout` only when the sandbox timed out but no detached work can be found. The returned `TaskRun` is then used by higher-level tools to decide whether to show normal output, timeout text, or background task handles.

*Call graph*: calls 2 internal fn (_record_exec_timeout, task_id); 1 external calls (__init__).


##### `task_id`  (lines 134–141)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: This function chooses the short name used for a task’s files. When the current operation has an idempotency key, meaning a repeat attempt should be treated as the same work, it creates the same task id every time.

**Data flow**: It reads the idempotency key from the tool context. If there is no key, it creates a fresh random id. If there is a key, it hashes that key and uses the first part of the hash as the stable task id. The result is a short string used in task file paths.

**Call relations**: It is called by `run_task` before a command is launched. This is what lets a recovered or retried dispatch step find the first command’s journal files instead of accidentally starting the same command again.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_handles`  (lines 144–165)

```
def task_handles(task: str, pid: str, display_base: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: This function builds the human-readable message and machine-readable details for a command that is running detached. It tells the caller where to read output, how to watch for completion, and how to stop the command.

**Data flow**: It receives the task id, supervisor process id, display path for the task files, an optional expired timeout, and an optional note. It chooses the right opening sentence depending on whether the command was detached from the start or moved to the background after a timeout. It then creates a JSON payload with paths and shell snippets for the log, exit file, watch command, and stop command, and returns one combined text block.

**Call relations**: Higher-level tools use this after `run_task` reports that a command is still alive. It does not itself check or control the task; it packages the handles that were made meaningful by the task journal and sandbox supervisor.

*Call graph*: 1 external calls (dumps).


##### `timeout_notice`  (lines 168–184)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: This function writes the timeout explanation shown to the caller. It makes clear whether the timeout was the default, the requested value, or the system maximum that capped a larger request.

**Data flow**: It takes the number of seconds that actually applied and the number of seconds the caller requested, if any. It compares them and returns a plain text sentence explaining why the command was stopped or why the wait ended. It does not change any state.

**Call relations**: This is used by caller-facing tool code when a command result says a timeout happened. It complements `run_task`: `run_task` detects and records what happened, while this function turns the timeout budget into wording a user can understand.


##### `_record_exec_timeout`  (lines 187–220)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: This private helper records diagnostic information when a sandbox command times out and no detached task appears to be alive. Its job is to help operators tell the difference between slow user work and a sandbox execution channel that stopped responding.

**Data flow**: It receives the tool context, command text, actual timeout, and requested timeout. It tries, within a small extra time limit, to run a simple health check inside the sandbox that reads system load, memory, and workspace disk usage. Whether that probe succeeds or fails, it writes a log event with the profile, timeout numbers, a shortened command, and any vitals it could collect. It returns nothing and deliberately swallows its own errors so the user still gets the original timeout result.

**Call relations**: `run_task` calls this only after a wait expires and the follow-up probe cannot find a surviving task or exit file. Inside, it uses the observability logging tools to hand diagnostic facts to the monitoring system, rather than handing them directly back to the tool caller.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).

## 📊 State Registers Touched

- `reg-pack-and-feature-selection` — The chosen product packs and feature switches that decide which parts of the system are enabled.
- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-live-update-stream` — The temporary live feed of progress messages that open clients and other server processes can follow.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-egress-policy` — The shared network exit rules that decide which outside addresses sandboxes may contact and when secrets may be added.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-conversation-todo-store` — Durable conversation checklist/todo state exposed as workspace objects and updated by tools or agents across turns.
- `reg-sandbox-task-session-state` — Persistent/pollable state for long-running sandbox commands and REPL sessions that survive tool timeouts across tool calls.
- `reg-debug-feedback-store` — Stored debugger or agent-submitted problem reports and feedback records used for later operator inspection.
- `reg-turn-created-reference-store` — Saved references created by a turn, linking its work to newly produced artifacts, objects, sources, sites, or other records for later display, replay, and cleanup.
- `reg-signed-token-keyring` — Shared signing secrets, key IDs, expiry rules, and validation parameters used to mint and verify login, public-route, artifact-download, and sandbox-access tokens.
- `reg-egress-policy-generation` — Per-workspace egress-rule version or invalidation counter used to rebuild cached sandbox proxy rules after credential, grant, or network-policy changes.

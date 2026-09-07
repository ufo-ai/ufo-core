# Browser extension tool and subagent surface  `stage-12.1`

This stage is the agent’s doorway into browser work. It is not the main reasoning loop itself. It is a support layer the main agent can call when it needs to open web pages, interact with sites, or ask a browser-focused helper to do a web task.

The package file marks this folder as a browser extension module, like a sign on a toolbox that says what belongs inside. The tools file fills that toolbox. It offers concrete actions such as opening tabs, reading page content, clicking, typing, uploading files, and saving downloads. These tool calls are translated into commands for the real browser-control system underneath.

The subagent file defines a specialized child agent for browser tasks. It says what kind of assignment the child receives and what kind of answer it returns. The delegation file gives the main agent two ways to use that child: send one complete browser task, or split many small browser visits to run in parallel and collect saved results.

## Files in this stage

### Package entry point
The browser extension package marker introduces the tool pack and frames the files that make up the browser-facing surface.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `import/package discovery`

This is a very small package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, meaning other code can refer to this folder as a named module. Here, it does not define any functions or run any setup code. Its only content is a short documentation string saying that this package contains the browser tool pack: tools for using a sandboxed browser or computer-like environment, plus a browser subagent profile. Think of it like a label on a drawer. The drawer may contain many useful tools, but this file’s job is simply to name the drawer and state what kind of things are stored there. Without it, depending on the Python packaging setup, other parts of the system might not reliably import this extension package in the expected way, and readers would have one less clue about the package’s purpose.


### Browser delegation and tools
The delegation helpers, browser subagent profile, and concrete browser tools define how the main agent assigns and executes browser work.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `tool execution / request handling`

This file is the bridge between a normal agent turn and real browser automation. Instead of giving the main agent direct control of a browser, it asks a specialized “browser” subagent to do the work. That matters because browser sessions can be slow, messy, or get stuck on a website. By delegating the work, this file keeps each browser run isolated, timed, and recoverable.

The first tool, `browser_task`, is for one multi-step browser session, such as searching a site, filling a form, or extracting information. It starts a fresh browser subagent, waits for the result, and cancels the work if it runs past the allowed time. The timeout is deliberately bounded: long enough to give a difficult site a fair chance, but not so long that a leased browser session is left hanging.

The second tool, `wide_browse`, is for batch work. It reads a workspace file containing one URL or site name per line, removes duplicates, and sends each item to a browser subagent. It limits how many run at once, like opening only a safe number of checkout lanes instead of flooding the store. Each entity gets its own result row, and one failure does not throw away the whole batch. The combined results are written to `wide_browse.json`.

#### Function details

##### `_browser_task`  (lines 99–146)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one delegated browser session and returns the browser subagent’s final summary. It is used when the caller wants a full, multi-step web task done in a fresh isolated browser, with a clear time limit.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the start URL, instructions, task name, and timeout. It spawns a browser-profile child turn using the current idempotency key, then waits for that child to finish. If the wait takes too long, it cancels the child and returns a failure result saying the browser may already have changed the page. If the child finishes successfully, it validates the child’s JSON text as a `BrowserResult` and returns that JSON as the tool output.

**Call relations**: This is the handler behind the public `browser_task` tool. During a tool call, the tool system invokes it, and it uses `ToolContext.spawn` to hand the actual web automation to the browser subagent. It then waits through the subagent control interface and packages either the browser’s result or a clear failure message for the parent agent.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 149–162)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses it to get the list of URLs or site names to visit.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for a shell command, reads the file through the sandbox, and raises an error if the file cannot be read. It then trims whitespace, skips blank lines, removes duplicates while keeping the original order, and returns the resulting list of strings.

**Call relations**: `_wide_browse` calls this first, before starting any browser subagents. It acts as the small preparation step that turns the user’s input file into the batch of entities that the rest of the wide browse flow can process.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 165–207)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs browser extraction across many entities, such as many URLs or company names, and saves the combined results into a JSON file. It is used for controlled parallel browsing where each item should produce its own row of output.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing the entities file, a prompt template, and an optional output schema file. It reads and deduplicates the entities, rejects batches that are too large, reads the schema if available, and creates a concurrency limit so only a fixed number of browser tasks run at once. It launches one `visit` task per entity, gathers all outcomes, converts individual failures into error rows, writes all rows to `wide_browse.json`, and returns a short JSON response containing the rows and output file name.

**Call relations**: This is the handler behind the public `wide_browse` tool. It begins by calling `_read_lines`, then fans out work to its nested `_wide_browse.visit` helper for each entity. It uses `asyncio.gather` so the visits can run in parallel, then collects their answers into one workspace file for the parent agent to read.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 173–188)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subagent for one entity inside a wider batch. It is designed so one bad website or failed child run becomes that entity’s problem, not the whole batch’s problem.

**Data flow**: It receives one entity string from the outer `_wide_browse` function. It waits for permission from the semaphore, builds a task prompt by replacing `{entity}` in the prompt template, appends the requested output schema if one was read, and spawns a browser-profile child turn with a deterministic deduplication key. It returns a row containing the entity and the child’s JSON output, or an empty result if the child returned no output.

**Call relations**: `_wide_browse` creates one `visit` coroutine for each entity and runs them together through `asyncio.gather`. Each `visit` hands its browser work to `ctx.spawn`; `_wide_browse` later receives the returned rows or exceptions and turns them into the final batch output.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup and subagent creation`

This file is like an ID card and job description for a browser-focused helper agent. The larger system can ask this helper to open pages, search the web, save findings, and report back, without giving it every tool the main agent may have. That keeps the helper focused on browser work.

At load time, the file reads a browser-specific instruction prompt from `prompts/subagent_browser.md`. That prompt tells the subagent how to behave. It then builds a list of tools the subagent is allowed to use: the browser tool set, plus basic file tools such as reading, writing, editing, and web search. These file tools matter because the browser subagent may need to save notes or screenshots into a shared workspace where the parent agent can inspect them later.

The file also defines two simple data shapes using Pydantic, a library that checks whether data has the expected fields. `BrowserTask` is the incoming request, including the freeform task, an optional URL, an optional task name, and an `extended_context` flag that defaults to true. `BrowserResult` is the outgoing answer.

Finally, `BROWSER_PROFILE` packages all of this together as a `SubagentProfile`: name, prompt, tools, input and output formats, model choice, and the warning that its output is untrusted and should not be blindly treated as safe.


### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `tool handling during an agent turn`

This file turns browser actions into safe, reusable tools. When the agent asks to navigate, inspect a page, type into a form, or wait for a download, the functions here check the input shape, pass the request to one shared browser surface, and package the answer back into the tool-result format the rest of the system expects.

The important idea is that one browser connection is reused for the whole turn. A turn is one round of work by the agent. The helper `_browser` lazily creates a `BuaSurface`, which is the object that actually talks to Chrome through CDP, the Chrome DevTools Protocol. CDP is the browser’s remote-control interface. The surface is cached and registered for cleanup, so the browser connection and any hosted session lease are released at the end of the turn even if something fails.

Most tool handlers are thin adapters: they convert a typed input object into plain JSON, call the matching browser operation, then return the reply as text JSON. A few tools add important behavior. `computer` can return screenshots as image content and can save them into the shared workspace. It also reports partial progress if a batch of clicks or keystrokes fails midway, so the agent does not repeat actions that already happened. `wait_for_download` receives downloaded bytes from the browser and writes them into the sandbox workspace so other parts of the system can find the file by path.

#### Function details

##### `_browser`  (lines 96–119)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser-control surface for the current turn, creating it only when the first browser tool needs it. This avoids opening a new browser connection for every small action and makes sure cleanup happens at the end of the turn.

**Data flow**: It receives the tool context, which contains the cleanup registry, browser connection provider, model, sandbox, extension store, and conversation id. It looks for an existing `BuaSurface` attached to this turn’s cleanup object; if none exists, it builds one from the context and registers its close method for later cleanup. It returns the ready-to-use browser surface, or raises an error if no browser connection provider is configured.

**Call relations**: All browser tool handlers call this before doing real browser work. On first use it hands off setup to `BuaSurface.__init__`; after that, calls such as `_navigate`, `_computer`, `_find`, and the tab tools reuse the same surface instead of rebuilding it.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 122–123)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a browser reply dictionary as a standard tool result containing JSON text. It gives all the simple browser tools a common way to return structured information.

**Data flow**: It receives a dictionary reply from the browser surface. It converts that dictionary into a JSON string and places it inside a text content object, then returns a tool result containing that text. It does not change the browser or the input data.

**Call relations**: Most handlers call this after receiving a successful reply from the browser surface. It creates the `TextContent` and `ToolResult` objects that the wider tool system expects.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 126–129)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a value from a browser reply is a non-empty string. It is used when missing data would make the next step unsafe or impossible, such as decoding a screenshot or downloaded file.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns that string unchanged. If not, it raises a clear error saying the browser reply is missing that field.

**Call relations**: `_computer` uses it before decoding a screenshot to save, and `_wait_for_download` uses it before decoding download content and naming the file. It acts as a small guardrail before file-writing work happens.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 132–134)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Moves a browser tab to a URL, or performs navigation-like browser movement supported by the browser surface. Someone would use this when the agent needs to open a page or move through browser history.

**Data flow**: It receives the tool context and a `NavigateInput` containing a URL and optionally a tab id. It turns the input into JSON without empty fields, asks the shared browser surface to navigate, then wraps the browser’s reply as a JSON tool result.

**Call relations**: This is the handler behind the `navigate` tool definition. It first gets the turn’s browser surface through `_browser`, then formats the answer through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 137–138)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. It helps the agent understand what tabs exist before choosing where to act.

**Data flow**: It receives the tool context and an empty input object. It asks the shared browser surface for tab context using an empty request, then converts the reply into a JSON text result.

**Call relations**: This is the handler behind the `tabs_context` tool. It relies on `_browser` for the live browser surface and `_json_result` to return the tab information in the normal tool-result shape.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 141–155)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally at a chosen URL. If no URL is given, it opens a blank page.

**Data flow**: It receives the tool context and an optional URL. It asks the browser surface to create a tab at that URL, or at `about:blank` if none was provided. On success, it returns the browser reply as JSON. If the browser reports that a tab was opened but left blank, it returns a failure result that still records the tab as already open.

**Call relations**: This is the handler behind the `tabs_create` tool. It calls `_browser` to perform the tab creation and `_json_result` for normal success. If `TabLeftOpen` is raised, it builds a `ToolFailure` with an `AppliedEffect` so the agent knows not to blindly retry and open extra tabs.

*Call graph*: calls 2 internal fn (_browser, _json_result); 2 external calls (__init__, __init__).


##### `_tabs_close`  (lines 158–160)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, either the current tab or a specific tab when a tab id is provided. It is used to clean up browser state.

**Data flow**: It receives the tool context and a `TabsCloseInput` with an optional tab id. It converts the input into JSON without empty fields, asks the browser surface to close the tab, and returns the reply as JSON text.

**Call relations**: This is the handler behind the `tabs_close` tool. It uses `_browser` to reach the live browser and `_json_result` to package the outcome.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 163–165)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file input on a web page using files from the workspace. It lets the agent upload local workspace files through a browser form.

**Data flow**: It receives the tool context and an `UploadFileInput` containing a page reference, one or more workspace file paths, and optionally a tab id. It converts the input to JSON, sends it to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: This is the handler behind the `upload_file` tool. It delegates the actual browser interaction to the shared surface from `_browser`, then uses `_json_result` for the response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 168–170)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads a structured view of the current browser page, focused on what elements are present and how they can be interacted with. This helps the agent decide what to click, type into, or inspect next.

**Data flow**: It receives the tool context and a `ReadPageInput`, which may include depth, filtering choices, a reference id, and a tab id. It removes empty fields, asks the browser surface to read the page, and returns the structured reply as JSON text.

**Call relations**: This is the handler behind the `read_page` tool. It calls `_browser` for the browser-side page reading and `_json_result` to send the result back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 173–175)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from a browser page. It is useful when the agent needs the readable words on the page rather than a full structured element tree.

**Data flow**: It receives the tool context and an optional tab id. It converts that input into JSON, asks the browser surface for page text, and returns the text-related reply as a JSON tool result.

**Call relations**: This is the handler behind the `get_page_text` tool. Like the other read-style tools, it gets the browser surface through `_browser` and formats the returned data through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 178–180)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the browser page for elements matching a query, such as visible text, role, name, or URL. It helps the agent locate the right target before acting.

**Data flow**: It receives the tool context and a `FindInput` containing the search query and optionally a tab id. It turns the input into JSON, sends it to the browser surface’s find operation, and returns the matches as JSON text.

**Call relations**: This is the handler behind the `find` tool. It uses `_browser` to access the surface, which was created with the context’s host-side finding support, then uses `_json_result` to return the matches.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 183–185)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. It is used when the agent needs to fill in text boxes, selectors, or other page inputs.

**Data flow**: It receives the tool context and a `FormInputInput` containing the page reference, the value to set, and optionally a tab id. It converts the input into JSON, asks the browser surface to apply the form value, and returns the browser reply as JSON text.

**Call relations**: This is the handler behind the `form_input` tool. It depends on `_browser` for the actual page change and `_json_result` for the standard response format.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 188–229)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs a batch of low-level browser actions such as mouse moves, clicks, keyboard input, waits, scrolling, and screenshots. It is the tool for direct “computer use” when higher-level page operations are not enough.

**Data flow**: It receives the tool context and a `ComputerInput` containing the action list, optional tab id, and optional screenshot-saving instructions. It sends the actions to the browser surface. If the batch succeeds, it may save a returned screenshot into the sandbox workspace, may include that screenshot as image content, and returns the remaining reply as text JSON. If the batch stops part-way through, it returns a failure result that clearly says which earlier actions already reached the page.

**Call relations**: This is the handler behind the `computer` tool. It calls `_browser` to perform the action batch and `_json_result` for simple replies. It uses `_required_str` before decoding screenshots, creates `ImageContent` when returning an image, and builds `ToolFailure` plus `AppliedEffect` entries when `BatchInterrupted` shows that some actions already happened.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 8 external calls (__init__, __init__, __init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 232–240)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and writes the downloaded file into the shared workspace. It turns a browser-side download into a normal file path other agents or later steps can use.

**Data flow**: It receives the tool context and a `WaitForDownloadInput` with an optional download id, target path, and timeout. It asks the browser surface for the completed download, checks that the filename and base64-encoded content are present, decodes the bytes, writes them into the sandbox under the requested folder or the default downloads folder, and returns the saved file path, filename, and size as JSON.

**Call relations**: This is the handler behind the `wait_for_download` tool. It calls `_browser` to wait for and collect the download, `_required_str` to verify required fields, base64 decoding to turn text-safe file content back into bytes, and `_json_result` to report where the file was saved.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).

# Browser Extension Entry Points and Action Contracts  `stage-10.3.1`

This stage is the front door between an agent and the local browser engine. It is shared support used whenever the agent wants to control or inspect a browser, rather than a one-time startup or shutdown step. The package marker files, __init__.py in ufo_ext_browser and bua, make these folders importable by Python and describe where browser-use tools and the browser subagent pieces live. The actions.py file is the rulebook for browser moves. It lists the actions the agent is allowed to request, such as click, type, scroll, wait, or screenshot, and defines what information each request must include. This helps catch unclear or invalid commands before they reach the browser. The errors.py file defines a browser-specific error for made-up references, such as an element that does not exist. The tools.py file is the working adapter. It exposes useful tools like opening pages, reading content, uploading files, and saving downloads, then translates those tool calls into browser-engine requests and returns results in a form the agent can use.

## Files in this stage

### Package Entry Points
Package marker files establish the browser extension namespace and its browser-automation subpackage.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `import time`

This file is the front door label for the `ufo_ext_browser` package. It does not run logic or define functions. Its job is to tell Python that this folder is an importable package, and to give humans a quick summary of what the package contains. In plain terms, this package groups together tools for controlling or inspecting a sandboxed browser environment, along with the profile for a specialized browser subagent. Without this file, the surrounding folder might still work in modern Python as a namespace package, but the clear package identity and human-readable explanation would be missing. Think of it like a sign on a toolbox: it does not use the tools itself, but it tells you that this box contains browser automation tools and the browser-specific assistant setup.


### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import time`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the surrounding `bua` directory should be treated as an importable package. In everyday terms, it is like a label on a folder saying, “the files inside here belong together and can be referenced by name.” Without this file, depending on the Python version and how the project is loaded, imports that expect `extensions.browser.ufo_ext_browser.bua` to be a regular package could fail or behave differently. Because the file contains no code, it does not run setup steps, create objects, or change program state. Its importance is in making the package layout clear and dependable for the rest of the browser extension code.


### Action and Error Contracts
Browser automation schemas define the allowed actions agents may request and the boundary-specific errors they may encounter.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a standard order form for controlling a web browser. Instead of letting the rest of the system pass around vague instructions like “click over there” or “scroll a bit,” it defines a small, fixed vocabulary of actions and the exact details each action may need.

The main action names are collected in `ActionType`, including mouse actions, keyboard actions, scrolling, screenshots, and waiting. `CLICK_ACTIONS` is a convenience set for recognizing the actions that are simple clicks.

The two main models are built with Pydantic, a library that checks whether data has the expected shape. `ScrollParameters` describes how to scroll: which direction and how far. The distance can be a number of screen heights, or `"max"` to jump to the far end of the page. `ComputerAction` describes one complete browser instruction. Depending on the action, it may include a pixel coordinate, typed text, a keyboard shortcut, scroll settings, a wait duration, drag start and end points, or an element reference like `"e5"` found earlier on the page.

This matters because browser automation is fragile if commands are ambiguous. These models make actions predictable, validated, and easier for other parts of the system to execute safely.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `cross-cutting`

This file gives the browser automation code a clear way to name one particular kind of failure: a hallucination. In this project, the system may ask an AI model to choose or describe something on a web page. Sometimes the model can confidently provide a value that cannot actually exist, like an element reference that was never seen on the page. Rather than treating that as a vague validation problem, this file creates `HallucinationError`, a more precise kind of `ValidationError`.

The important idea is classification. `ValidationError` is the broader category: something about supplied data failed a check. `HallucinationError` narrows that down to a model-specific problem: the data is not just malformed, it appears to be invented. This helps the rest of the system react more intelligently. For example, it could report a clearer message, retry with better instructions, or distinguish model mistakes from ordinary programming or transport errors.

The file is intentionally tiny. Like putting a labeled bin in a workshop, it does not do the cleanup itself, but it makes sure this kind of problem lands in the right place.


### Browser Tool Surface
Public browser tools translate agent requests into browser-engine operations and format results back to the agent.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `tool invocation during a turn, with cleanup at turn end`

This file is the agent’s public control panel for a browser. Without it, the rest of the system might have a browser engine available, but the agent would not have clear, validated actions like “navigate,” “read the page,” or “wait for a download.”

Each tool has a small input model that describes what information is allowed. For example, navigation needs a URL, while file upload needs a page reference and workspace file paths. These models are checked before the tool runs, which helps catch bad requests early.

The central helper is `_browser`. It creates one `BuaSurface`, which is the object that actually talks to the browser, the first time a browser tool is used during a turn. It then reuses that same surface for later browser calls in the same turn. This is like opening one phone call to the browser and keeping it open instead of dialing again for every sentence. The file also registers cleanup so the browser connection and any temporary session lease are closed when the turn ends.

Most tool functions are thin translators: they convert typed inputs into plain JSON-shaped data, call the matching browser action, and wrap the reply as text. Two tools do extra work: `computer` can return a screenshot as image content and optionally save it to the workspace, while `wait_for_download` saves downloaded bytes into the workspace so other parts of the agent can use the file by path.

#### Function details

##### `_browser`  (lines 87–110)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser-control surface for the current turn, creating it only when it is first needed. This avoids opening multiple browser connections for several tool calls in the same turn.

**Data flow**: It receives the current tool context, which contains things like the cleanup registry, browser connection provider, model, sandbox, extension store, and conversation id. It checks whether a `BuaSurface` is already cached for this turn; if not, it builds one from the context, stores it, and registers its close operation for later cleanup. It returns the ready-to-use browser surface.

**Call relations**: All browser tool handlers call this before doing real browser work. When `_navigate`, `_find`, `_form_input`, `_get_page_text`, `_read_page`, `_tabs_close`, `_tabs_context`, `_computer`, and the other browser handlers need the browser, they come here first; this helper then hands them either the existing `BuaSurface` or a newly created one.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 113–114)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a browser reply dictionary as a text tool result. It gives the agent a consistent JSON text response for most browser actions.

**Data flow**: It receives a dictionary containing simple JSON-compatible values. It turns that dictionary into a JSON string, puts the string into a `TextContent` object, and returns a `ToolResult` containing that text.

**Call relations**: Most tool handlers call this after receiving a reply from the browser surface. It is the common final step for `_navigate`, `_tabs_context`, `_tabs_create`, `_tabs_close`, `_upload_file`, `_read_page`, `_get_page_text`, `_find`, `_form_input`, and parts of `_computer` and `_wait_for_download`.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 117–120)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a browser reply contains a required non-empty string. It prevents later code from silently treating missing data as valid.

**Data flow**: It receives a value and the name of the field that value is supposed to represent. If the value is a non-empty string, it returns it unchanged. If not, it raises an error explaining which browser reply field was missing.

**Call relations**: `_computer` uses this when it must save a screenshot and therefore needs valid base64 screenshot text. `_wait_for_download` uses it to make sure the browser supplied both a filename and base64 file content before writing the download into the workspace.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 123–125)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Moves the browser to a requested URL or browser-history destination. It is the tool handler behind the agent’s navigate action.

**Data flow**: It receives the tool context and validated navigation input. It converts the input into JSON-style data while leaving out unset optional fields, asks the shared browser surface to navigate, then wraps the browser’s reply as a JSON text result.

**Call relations**: The tool framework calls this through the `navigate` tool definition. Inside the flow, it first gets the turn’s browser surface from `_browser`, then hands the browser reply to `_json_result` so the agent receives a normal text response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 128–129)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. This helps the agent understand what pages are available before choosing where to act.

**Data flow**: It receives the tool context and an empty validated input object. It asks the shared browser surface for tab context using an empty request, then converts the reply into a JSON text tool result.

**Call relations**: The tool framework calls this through the `tabs_context` tool definition. It relies on `_browser` to reuse the turn’s browser connection and on `_json_result` to format the answer.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 132–134)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opening a given URL. If no URL is provided, it opens a blank page.

**Data flow**: It receives the tool context and optional tab-creation input. It builds a request with the supplied URL or `about:blank`, sends it to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: The tool framework calls this through the `tabs_create` tool definition. It gets browser access through `_browser` and uses `_json_result` to send the result back in the same format as other tab tools.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 137–139)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, either a specific one or whichever tab the browser surface treats as the default. This lets the agent clean up tabs it no longer needs.

**Data flow**: It receives the tool context and optional tab id. It converts the input into JSON-style data, omitting unset fields, asks the browser surface to close the tab, and returns the reply as JSON text.

**Call relations**: The tool framework calls this through the `tabs_close` tool definition. The function follows the common pattern in this file: get the shared browser surface with `_browser`, perform the browser action, then format the response with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 142–144)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file input on a web page using files from the shared workspace. This is how the agent can attach local workspace files to browser forms.

**Data flow**: It receives the tool context plus a page reference, one or more workspace file paths, and optionally a tab id. It converts those inputs into JSON-style data, sends them to the browser surface’s upload action, and returns the browser’s reply as JSON text.

**Call relations**: The tool framework calls this through the `upload_file` tool definition. It depends on `_browser` for the active browser surface and `_json_result` for the final response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 147–149)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads a structured view of the current web page, especially useful for finding buttons, links, fields, and other page elements. The structure can be filtered or limited by depth.

**Data flow**: It receives the tool context and read options such as depth, filter, reference id, and tab id. It turns those options into JSON-style data without unset fields, asks the browser surface to read the page, and returns the result as JSON text.

**Call relations**: The tool framework calls this through the `read_page` tool definition. As with other read-only browser tools, it asks `_browser` for the current surface and `_json_result` for standard formatting.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 152–154)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This gives the agent a simpler text-only view when it does not need the full page structure.

**Data flow**: It receives the tool context and an optional tab id. It converts the input into JSON-style data, calls the browser surface’s text extraction method, and wraps the returned data as JSON text.

**Call relations**: The tool framework calls this through the `get_page_text` tool definition. It uses `_browser` to reach the browser and `_json_result` to return the browser’s answer.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 157–159)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the browser page for elements matching a query, such as text, role, name, or URL. This helps the agent locate the right page element before interacting with it.

**Data flow**: It receives the tool context, a search query, and optionally a tab id. It converts that into JSON-style data, sends it to the browser surface’s find operation, and returns the matches as JSON text.

**Call relations**: The tool framework calls this through the `find` tool definition. It gets the shared browser surface through `_browser`; that surface was created with the context’s finding hook, so this function can use the host-side ranking behavior indirectly.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 162–164)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. This is used when the agent needs to type into or change a page control in a direct, structured way.

**Data flow**: It receives the tool context, the element reference, the value to put into the form field, and optionally a tab id. It converts the input into JSON-style data, asks the browser surface to set the form value, and returns the browser’s reply as JSON text.

**Call relations**: The tool framework calls this through the `form_input` tool definition. Like the other action tools, it routes through `_browser` for the active browser session and `_json_result` for the returned tool message.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 167–183)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs low-level browser interaction actions such as mouse, keyboard, wait, scroll, and screenshot steps. It can also return a screenshot to the agent and optionally save that screenshot into the workspace.

**Data flow**: It receives the tool context plus a list of computer-style actions, an optional tab id, and optional screenshot-saving settings. It sends the actions to the browser surface. If saving is requested, it checks that the reply contains base64 screenshot text, decodes it into bytes, writes it to the sandbox workspace path, and records that path in the reply. If the reply includes a screenshot, it returns text for the non-image fields plus image content; otherwise it returns ordinary JSON text.

**Call relations**: The tool framework calls this through the `computer` tool definition. It starts by getting the shared browser surface from `_browser`. It uses `_required_str` when screenshot bytes are required for saving, uses `_json_result` when there is no image to return, and otherwise builds a richer `ToolResult` containing both text and an image.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 186–194)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and writes the downloaded file into the shared workspace. This turns a browser-only download into a path that the agent and related subagents can use.

**Data flow**: It receives the tool context plus optional download id, target path, and timeout. It asks the browser surface to wait for the download and return its filename and base64-encoded contents. It checks those required strings, decodes the file bytes, writes them under the requested directory or the default downloads directory, and returns a JSON text result with the saved path, filename, and size.

**Call relations**: The tool framework calls this through the `wait_for_download` tool definition. It relies on `_browser` for the browser-side download data, `_required_str` to reject incomplete replies, the sandbox to write the file, and `_json_result` to report where the file was saved.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).

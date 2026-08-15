# Browser automation and web-interaction execution  `stage-13`

This stage is the system’s web browser workbench. It is used during the main work loop when an agent needs to open a site, read it, click buttons, type into forms, upload or download files, or take screenshots.

At the center, the BUA session layer leases a browser for the current turn and defines the allowed actions, like click, type, scroll, and wait. The DevTools transport is the communication cable to Chrome, using Chrome’s control protocol to send commands and receive events. Page inspection tools turn a live web page into readable text, accessibility information, and screen coordinates the agent can use. Action execution tools then turn the agent’s plan into real mouse, keyboard, form, and file actions, while waiting for the page to settle afterward.

Tabs, pop-up dialogs, and downloads are managed separately so browsing does not get stuck or lose track of files. External provider adapters can supply a local sandbox browser, a hosted Chrome session, or a cloud browsing service. Finally, `tools.py` exposes these abilities as agent tools, connecting tool calls to the browser engine underneath.

## Sub-stages

- [BUA session orchestration and action schema](stage-13.1.md) `stage-13.1` — 5 files
- [Chrome DevTools transport and runtime bridge](stage-13.2.md) `stage-13.2` — 3 files
- [Page inspection, accessibility content, and element lookup](stage-13.3.md) `stage-13.3` — 4 files
- [User interaction execution and action fixups](stage-13.4.md) `stage-13.4` — 5 files
- [Tabs, dialogs, and downloads management](stage-13.5.md) `stage-13.5` — 3 files
- [External browser providers and hosted automation adapters](stage-13.6.md) `stage-13.6` — 3 files

## Files in this stage

### Browser automation and web-interaction execution
### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `per-turn tool handling`

This file turns high-level browser requests into safe, structured calls to a single browser session for the current turn. Without it, the agent might know it wants to click, read, or download something, but it would have no standard doorway into the browser.

Each tool has an input shape, defined with Pydantic models. Pydantic checks that the tool arguments have the expected fields and types before anything touches the browser. Most functions then remove the human-facing `user_description` field, pass the remaining machine instructions to `BuaSurface`, and return the browser’s answer as JSON text.

A key detail is that `_browser` creates only one `BuaSurface` per turn and reuses it. Think of it like borrowing one browser remote control for the whole conversation turn instead of picking up a new remote for every button press. The cleanup registry closes that remote at the end, even if the turn fails.

Two tools also move files through the shared workspace. `computer` can save a screenshot image, and `wait_for_download` saves downloaded bytes into a downloads folder. This matters because other agents or later steps can then refer to those files by path.

#### Function details

##### `_browser`  (lines 105–128)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser-control surface for the current turn, creating it the first time a browser tool is used. This avoids repeatedly opening browser connections and makes sure the connection is closed when the turn ends.

**Data flow**: It receives the tool context, reads the cleanup registry, browser connection provider, sandbox, model, extension store, and conversation id from that context, and checks whether a browser surface already exists for this turn. If not, it builds a `BuaSurface`, stores it in a weak cache tied to the turn cleanup object, registers its close function for cleanup, and returns it.

**Call relations**: All browser tool handlers call this before doing their work. When it has to create a surface, it hands the needed browser and workspace ingredients to `BuaSurface.__init__`; later handlers in the same turn reuse that same surface.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 131–132)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Packages a plain dictionary reply from the browser engine into the standard tool-result format expected by the rest of the agent system.

**Data flow**: It receives a dictionary, turns it into a JSON string, wraps that string as text content, and returns a `ToolResult` containing that text.

**Call relations**: Most tool handlers call this after the browser surface replies. It is the common final step that makes browser replies look like normal tool output.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 135–138)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a browser reply contains a required non-empty string field. It is used before decoding important base64 data such as screenshots or downloaded files.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns it; otherwise it raises an error explaining that the browser reply is missing that field.

**Call relations**: `_computer` uses it before saving a screenshot, and `_wait_for_download` uses it before saving a downloaded file. It protects later file-writing steps from silently using missing or malformed data.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 141–145)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Moves the browser to a URL or performs a navigation action such as using browser history. It is the tool handler behind the agent’s navigate command.

**Data flow**: It receives validated navigation arguments, converts them into a JSON-ready dictionary while dropping the timeline-only `user_description`, sends that dictionary to the browser surface, and returns the browser reply as a JSON tool result.

**Call relations**: When the navigate tool is invoked, this function gets the shared browser surface through `_browser`, asks it to navigate, then uses `_json_result` to hand the answer back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 148–149)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Gets a summary of the currently open browser tabs. This lets the agent understand what pages are open before choosing where to work next.

**Data flow**: It receives the tool context and the validated request, asks the shared browser surface for tab context with no extra browser arguments, and returns the result as JSON text.

**Call relations**: This is called when the tabs-context tool runs. It relies on `_browser` for the active browser surface and `_json_result` to format the tab summary for the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 152–154)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opening a given URL. If no URL is supplied, it opens a blank page.

**Data flow**: It receives the requested tab information, chooses either the provided URL or `about:blank`, sends that to the browser surface, and returns the browser’s reply as a JSON tool result.

**Call relations**: This function is the handler for the tab-create tool. It gets the current turn’s browser surface through `_browser`, asks it to create the tab, then formats the reply with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 157–161)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, usually the current one or a specified tab. This helps the agent clean up pages it no longer needs.

**Data flow**: It receives validated close-tab arguments, removes fields that are only for human activity descriptions, sends the remaining tab information to the browser surface, and returns the reply as JSON text.

**Call relations**: When the close-tab tool is used, this handler calls `_browser` to reach the active browser session and `_json_result` to return the result in the standard format.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 164–168)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a webpage file-upload input using files from the shared workspace. This is how the agent attaches documents to forms in the browser.

**Data flow**: It receives a browser element reference, workspace file paths, and optional tab information, strips out the human-only description, and passes the upload request to the browser surface. The browser reply is then returned as JSON text.

**Call relations**: This handler runs for the upload-file tool. It uses `_browser` to talk to the browser engine and `_json_result` to report whether the upload action succeeded or what the browser returned.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 171–175)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page’s accessibility tree, which is a structured view of page elements such as buttons, links, text boxes, and labels. This gives the agent a more action-oriented picture of the page than raw HTML.

**Data flow**: It receives options such as depth, filter, element reference, and tab id, removes the timeline description, and asks the browser surface to read the page accordingly. It returns the structured page information as a JSON tool result.

**Call relations**: This is the read-page tool handler. It gets the shared browser surface through `_browser` and uses `_json_result` to pass the page reading back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 178–182)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts the raw visible text from a browser page. This is useful when the agent needs to read content without needing the full structure of buttons and fields.

**Data flow**: It receives the requested tab information, removes the human-only description, sends the remaining request to the browser surface, and returns the extracted text reply as JSON.

**Call relations**: When the get-page-text tool is called, this function uses `_browser` to reach the browser session and `_json_result` to turn the browser’s response into standard tool output.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 185–189)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the current page for elements by things like role, text, name, or URL. This helps the agent locate the specific button, link, field, or item it wants to use next.

**Data flow**: It receives a search query and optional tab id, drops the activity-description field, sends the search request to the browser surface, and returns the found matches as JSON text.

**Call relations**: This is the handler for the find tool. It obtains the turn’s browser surface via `_browser`; that surface can use the project’s finding or ranking hook, and the handler formats the answer with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 192–196)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Fills or changes a form field identified by a browser reference. It is used when the agent needs to type into text boxes, choose values, or otherwise set form data.

**Data flow**: It receives the target element reference, the value to put there, and optional tab information, removes the human-only description, sends the instruction to the browser surface, and returns the result as JSON.

**Call relations**: This function backs the form-input tool. It calls `_browser` for the current browser session and `_json_result` after the browser reports what happened.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 199–217)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs lower-level browser interaction actions such as mouse moves, clicks, keyboard input, scrolling, waiting, and screenshots. It can also save a screenshot into the shared workspace so later steps can use it as a file.

**Data flow**: It receives a list of computer-style actions plus options such as tab id and screenshot save path. It sends the action list to the browser surface, then checks whether a screenshot was returned. If saving is requested, it decodes the base64 screenshot bytes and writes them to the sandbox workspace. If a screenshot is present, it returns both JSON text and image content; otherwise it returns only JSON text.

**Call relations**: This handler is used by the computer tool. It depends on `_browser` for the active browser surface, `_required_str` when screenshot bytes must be present, and `_json_result` when there is no image to attach separately.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 220–228)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves the downloaded file into the shared workspace. This turns an in-browser download into a path the rest of the agent system can use.

**Data flow**: It receives optional download id, target path, and timeout settings, sends them to the browser surface, and expects a filename plus base64-encoded file content back. It validates those fields, decodes the file bytes, writes them into the sandbox under the requested folder or the default downloads folder, and returns the saved path, filename, and size as JSON.

**Call relations**: This function backs the wait-for-download tool. It gets the browser session through `_browser`, uses `_required_str` to make sure the download reply is usable, writes the file through the sandbox, then reports the final workspace path with `_json_result`.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).

## 📊 State Registers Touched

- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-browser-session` — The leased browser instance for a turn, including tabs, page state, downloads, dialogs, and provider connection details.
- `reg-provider-client-pools` — Per-process reusable transport/client state for external providers such as model APIs, search and embedding services, connector brokers, browser providers, billing services, and related retry or throttle windows.

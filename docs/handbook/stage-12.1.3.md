# Browser Action Execution and Tool Surface  `stage-12.1.3`

This stage is the browser “tool surface”: the layer that lets an agent ask Chrome to do useful work during the main task loop. It sits between high-level requests, like “click this button” or “upload this file,” and the lower-level browser connection that performs them.

The tools.py file is the public counter where the agent places requests such as opening pages, reading content, typing, clicking, downloading, or uploading. backend.py provides the per-turn workbench: it opens a Chrome connection when needed, lets the tools use it for navigation, tabs, page content, and downloads, then cleans it up afterward. actions.py defines the allowed action formats, like an order form that can be checked before use. errors.py names the special case where the model invents an impossible browser target or value.

Before actions reach Chrome, fixup.py repairs common small mistakes. forms.py handles form fields and file upload controls. keys.py translates typing and key shortcuts into messages Chrome understands. computer.py finally carries out clicks, typing, scrolling, waiting, and screenshots, then returns updated visual feedback and safety guidance.

## Files in this stage

### Tool-facing browser surface
These files expose browser capabilities to agents and manage the per-turn browser connection used to perform them.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `active during an agent turn whenever browser tools are called`

This file turns browser abilities into safe, well-shaped tools for the agent. Each tool has an input model that says what arguments are allowed, such as a URL for navigation or a browser reference for a form field. When a tool runs, it asks for the current turn’s shared browser surface, sends the cleaned-up arguments to that surface, and returns the result in the format the agent expects.

The important idea is that one browser connection is reused during a turn. The helper `_browser` lazily creates a `BuaSurface`, meaning it waits until the first browser action is actually requested. That surface is like the remote control for the browser. It is cached for the turn and registered for cleanup, so the browser connection and any leased remote session are released when the turn ends.

Most tools are thin wrappers: validate input, call the matching browser action, return JSON text. Two tools do extra work with files. `_computer` can include a screenshot as image content, and if asked, saves that screenshot into the shared workspace. `_wait_for_download` waits for a browser download, decodes the downloaded bytes, and writes them into the workspace so other parts of the agent can use the file by path.

#### Function details

##### `_browser`  (lines 87–110)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the browser surface for the current tool turn. If this is the first browser action in the turn, it creates the surface and arranges for it to be closed at the end of the turn.

**Data flow**: It receives the tool context, which contains things like the selected browser connection provider, sandbox, model, extension store, and cleanup registry. It looks up whether a browser surface is already cached for this turn; if not, it builds one from the context and registers its close method for cleanup. It returns the ready-to-use `BuaSurface`.

**Call relations**: All browser action functions call this before doing real browser work. It hands them the shared `BuaSurface`, so navigation, tab control, page reading, form filling, screenshots, uploads, and downloads all use the same browser connection during the turn.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 113–114)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain dictionary reply as a tool result containing JSON text. This gives the agent a consistent text response format for most browser actions.

**Data flow**: It receives a dictionary of reply data. It converts that dictionary to a JSON string, places it inside a text content object, and returns a tool result containing that text.

**Call relations**: Most tool handlers call this after receiving a reply from the browser surface. `_computer` also uses it when there is no screenshot image to attach, and `_wait_for_download` uses it after writing the downloaded file to the workspace.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 117–120)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a browser reply contains a required non-empty string. It is used when the code must have valid text before decoding or saving file data.

**Data flow**: It receives a value from a browser reply and the name of the field being checked. If the value is a non-empty string, it returns that string. If it is missing, empty, or not text, it raises an error that names the missing field.

**Call relations**: `_computer` uses this before decoding a screenshot that must be saved. `_wait_for_download` uses it to confirm both the downloaded filename and the base64-encoded file content are present before writing the file.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 123–125)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Moves the browser to a requested URL or performs a navigation action supported by the browser backend. It is the tool handler behind the agent’s `navigate` browser tool.

**Data flow**: It receives the tool context and validated navigation input. It turns the input model into a JSON-friendly dictionary, omitting unset optional fields, sends that to the shared browser surface, and wraps the browser’s reply as JSON text.

**Call relations**: When the agent invokes the navigation tool, this function first gets the turn’s browser surface through `_browser`, then delegates the actual browser navigation to that surface, and finally formats the answer through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 128–129)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the current browser tabs. It lets the agent understand what tabs exist before choosing one to read, close, or act in.

**Data flow**: It receives the tool context and an empty input object. It calls the shared browser surface with an empty request and returns the tab information as JSON text.

**Call relations**: This is the handler for the `tabs_context` tool. It relies on `_browser` to reuse the current turn’s browser connection, then uses `_json_result` to present the tab summary back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 132–134)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab. If no URL is supplied, it opens a blank page.

**Data flow**: It receives the tool context and optional tab creation input. It builds a request with either the supplied URL or `about:blank`, sends it to the browser surface, and returns the resulting tab details as JSON text.

**Call relations**: This backs the `tabs_create` tool. It gets the shared browser surface through `_browser`, asks it to create the tab, and formats the reply through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 137–139)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, optionally a specific one. This lets the agent tidy up tabs or remove pages it no longer needs.

**Data flow**: It receives the tool context and optional tab id. It converts the input into a JSON-friendly dictionary without empty fields, sends that request to the browser surface, and returns the close result as JSON text.

**Call relations**: This is the handler for the `tabs_close` tool. It obtains the current turn’s browser surface with `_browser`, delegates the tab-closing action to it, then uses `_json_result` to return the outcome.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 142–144)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file input on a web page using files from the workspace. This is how the agent can attach local workspace files to browser forms.

**Data flow**: It receives a browser reference, one or more workspace file paths, and an optional tab id. It serializes those inputs, sends them to the browser surface, and returns the upload result as JSON text.

**Call relations**: This backs the `upload_file` tool. It depends on `_browser` for access to the browser surface, which performs the actual file-input operation, and then passes the reply through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 147–149)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads a structured view of the current web page, especially the page’s accessibility tree. An accessibility tree is a browser-made outline of meaningful page elements, useful for deciding what to click or type into.

**Data flow**: It receives options such as depth, filter type, element reference, and tab id. It converts those options into a JSON-friendly request, asks the browser surface to read the page, and returns the structured page data as JSON text.

**Call relations**: This is the handler for the `read_page` tool. The agent calls it when it needs a map of the page; `_browser` supplies the active surface, and `_json_result` packages the surface’s reply.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 152–154)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This is useful when the agent needs the page’s words without the full structure of buttons, fields, and other elements.

**Data flow**: It receives the tool context and optional tab id. It serializes the input, asks the browser surface for page text, and returns that text response as JSON.

**Call relations**: This backs the `get_page_text` tool. It follows the common pattern in this file: get the shared browser surface through `_browser`, delegate the browser-specific work, and format the reply with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 157–159)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the page for elements by things like role, visible text, name, or URL. It helps the agent turn a human goal such as “click the Submit button” into a concrete browser reference.

**Data flow**: It receives a search query and optional tab id. It sends those as a JSON-friendly request to the browser surface, which performs the search, then returns the found matches as JSON text.

**Call relations**: This is the handler for the `find` tool. The tool gets the current browser surface via `_browser`, asks that surface to find matching page elements, and uses `_json_result` to send the results back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 162–164)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. This lets the agent fill text boxes, selectors, and similar page inputs after it has found them.

**Data flow**: It receives a target reference, the value to set, and an optional tab id. It serializes those inputs, sends them to the browser surface, and returns the result as JSON text.

**Call relations**: This backs the `form_input` tool. It is usually used after a page-reading or finding step has produced a reference to a form element; `_browser` supplies the shared surface and `_json_result` formats the browser reply.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 167–183)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs low-level browser interaction actions such as mouse moves, clicks, keyboard input, waiting, scrolling, and screenshots. It is the more direct “computer use” tool for cases where structured page actions are not enough.

**Data flow**: It receives a list of requested actions, an optional tab id, and optional screenshot-saving settings. It sends the action list to the browser surface. If the reply contains a screenshot and saving was requested, it decodes the base64 screenshot bytes and writes them into the workspace. If a screenshot is present, it returns both JSON text for the non-image data and image content for the screenshot; otherwise it returns only JSON text.

**Call relations**: This function is the handler for the `computer` tool. It calls `_browser` to get the active surface, uses `_required_str` when a screenshot must be saved, writes through the sandbox when needed, and either formats through `_json_result` or builds a mixed text-and-image tool result.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 186–194)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves the downloaded file into the workspace. This turns an in-browser download into a normal file path the agent and related tasks can use.

**Data flow**: It receives optional download id, output path, and timeout. It asks the browser surface to wait for the download, checks that the reply includes a filename and base64-encoded content, decodes the content into bytes, writes those bytes to the chosen workspace folder, and returns the saved path, filename, and size as JSON text.

**Call relations**: This is the handler for the `wait_for_download` tool. It relies on `_browser` for the download data, `_required_str` to reject incomplete browser replies, the sandbox to write the file, and `_json_result` to report where the file was saved.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser tool use and turn cleanup`

A browser task needs a safe way to talk to Chrome without every tool call knowing how Chrome was started, where it runs, or how files move in and out. This file supplies that layer through `BuaSurface`. Think of it like the front desk for a temporary browser room: the first visitor gets a key, later visitors reuse it, and checkout returns the key and cleans the room.

When a browser tool is used, `BuaSurface` first gets a `CdpLease`, which is a rented connection to a browser session using CDP, Chrome’s remote-control protocol. It then opens a `BrowserSession`, which sends the real browser commands. If no browser tool is used in a turn, none of this happens.

The file also protects important edges. For file uploads, it checks that requested files are real workspace paths, reads them from the sandbox when needed, sends them through the browser transport, and waits until the page truly has the uploaded bytes. For downloads, it asks the transport for the finished file bytes and returns a safe filename plus base64 text.

It also supports crash recovery. It stores a durable browser token for the current conversation. If the process crashes before cleanup, a retried turn can reattach to the same live browser instead of starting over. Normal cleanup clears that token so later turns do not accidentally reuse an old session.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens, or reuses, the browser session for this turn. Tool methods call this before doing browser work so they do not each need to know how to rent or connect to Chrome.

**Data flow**: It starts with the surface’s current `session` and `lease` fields. If a session already exists, it returns it. If not, it acquires a lease, asks that lease for a browser endpoint and download directory, creates a `BrowserSession`, opens it, stores it on the surface, and returns it.

**Call relations**: This is the gateway used by browser actions such as navigation, reading, finding, form input, tab work, upload, download, and computer control. When no lease exists yet, it hands off to `BuaSurface._acquire_lease`; then it builds the `BrowserSession` that the action methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets the browser lease for this turn, either by reconnecting to a saved live session or by starting a fresh one. This is what lets a recovered turn continue using the same browser after a crash when possible.

**Data flow**: It reads any stored token for the conversation. If a token exists, it asks the CDP provider to reattach to that browser session. If the session is gone, it clears the bad token. Then it leases a new browser session from the provider, stores the new token if available, and returns the lease.

**Call relations**: `BuaSurface._open` calls this when it needs a lease. This function relies on `BuaSurface._stored_token` to look for a recovery token and `BuaSurface._store_token` to save or clear that token.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser reattach token for this conversation. The token is the bookmark that can point a recovered turn back to the same hosted browser session.

**Data flow**: It checks whether both a scoped store and conversation ID are available. If not, it returns nothing. Otherwise it reads the token key from the store and returns the value only if it is a string.

**Call relations**: `BuaSurface._acquire_lease` calls this before deciding whether to reconnect to an existing browser or lease a fresh one.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser reattach token for this conversation. Saving supports crash recovery; clearing prevents a later normal turn from reusing a released browser session.

**Data flow**: It receives either a token string or `None`. If the surface has no store or conversation ID, it does nothing. Otherwise it writes that value under the conversation-specific token key.

**Call relations**: `BuaSurface._acquire_lease` uses this to save a fresh token or remove a stale one. `BuaSurface.aclose` uses it during cleanup to clear the token after the lease is released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Tells the browser to load a URL, optionally in a specific tab. It validates that the requested URL is actually text before passing it to the browser session.

**Data flow**: It receives a dictionary of tool arguments, opens the browser session, extracts `url`, checks that it is a string, converts the optional tab ID into an integer or `None`, and returns the browser session’s navigation result.

**Call relations**: A browser navigation tool calls this method. It first goes through `BuaSurface._open` to ensure Chrome is connected, uses `_tab_id` to normalize the tab choice, and then delegates the actual page load to the session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the browser’s current tabs. A tool can use this to know what pages are open and which tab it should act on.

**Data flow**: It receives tool arguments but does not need any specific values from them. It opens the browser session and returns the tab context reported by that session.

**Call relations**: The tab-context tool calls this when it needs the browser’s tab state. It uses `BuaSurface._open` for connection setup, then hands the work to the active `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If the caller does not provide a usable URL, it opens a blank page.

**Data flow**: It receives a dictionary of arguments, opens the browser session, reads the optional `url`, replaces a missing or empty URL with `about:blank`, and returns the session’s result for the new tab.

**Call relations**: The tab-creation tool calls this. Like other tool methods, it relies on `BuaSurface._open` to prepare the browser, then delegates tab creation to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes one or more browser tabs according to the caller’s arguments. It keeps tab closing behind the same per-turn browser connection as the other browser actions.

**Data flow**: It receives the close-tab arguments, opens the browser session, passes those arguments to the session, and returns the session’s response.

**Call relations**: The tab-close tool calls this. It uses `BuaSurface._open` first, then leaves the browser-specific tab closing details to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a file input on a web page. It makes sure files come from the workspace and are placed somewhere the leased browser can actually read, whether that browser is local or remote.

**Data flow**: It receives upload arguments, opens the browser session, checks that `files` is a list of non-empty strings, converts each path into a safe workspace path, and asks the lease to place each file using a reader callback. It replaces the original file list with the placed browser-readable paths, asks the session to attach them to the page, waits until the page sees the expected file sizes, and returns the upload result.

**Call relations**: The upload tool calls this when a page needs a file. It uses `BuaSurface._open` to get the browser, `workspace_path` to keep paths inside the workspace, `BuaSurface._lease` to access the transport, `BuaSurface._read` indirectly through `functools.partial` when bytes must be shipped, and `BuaSurface._settle_upload` to confirm the page really received the files.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits for uploaded files to fully arrive in the page’s file input. This avoids a subtle failure where the page shows the right filename but the remote browser has not finished receiving the bytes yet.

**Data flow**: It receives the active browser session and the upload arguments. If this surface did not ship any bytes, it returns immediately. Otherwise it compares the page’s attached file sizes with the sizes that were sent, retries by sleeping and re-attaching the files, and either returns when the sizes match or raises an error after the retry budget is used.

**Call relations**: `BuaSurface.upload_file` calls this right after the first attach. It repeatedly asks `BrowserSession.attached_sizes` what the page currently sees, uses `asyncio.sleep` between attempts, and calls `BrowserSession.upload_file` again when the attach needs to be refreshed.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the current browser lease, or fails clearly if no lease has been opened. This prevents file transport code from silently running without a browser rental.

**Data flow**: It reads the surface’s `lease` field. If the lease is present, it returns it. If not, it raises a runtime error explaining that there is no CDP lease.

**Call relations**: `BuaSurface.upload_file` uses this when asking the transport to place files for the browser. `BuaSurface.wait_for_download` uses it when fetching downloaded bytes from wherever the transport stored them.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file’s bytes from the sandbox so they can be sent to a remote browser. It enforces a size limit first so an oversized upload does not flood the host process with data.

**Data flow**: It receives a workspace path. It requires a sandbox, quotes the path safely for shell commands, runs `stat` in the sandbox to get the file size, rejects unreadable or too-large files, runs `base64` in the sandbox to stream the file as text, decodes that text back into bytes in a worker thread, records the byte count in `_shipped`, and returns the bytes.

**Call relations**: This function is supplied as a callback from `BuaSurface.upload_file` to the lease’s file-placement step. It uses `shlex.quote` to make the shell command safe and `asyncio.to_thread` so base64 decoding does not block the main event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Asks the browser to describe the current page in the structured form expected by the browser tools. This gives the rest of the system a page view without exposing raw browser protocol details.

**Data flow**: It receives tool arguments, opens the browser session, passes the arguments to the session’s page-reading method, and returns that result.

**Call relations**: The read-page tool calls this during browser work. It uses `BuaSurface._open` for session setup and then delegates the browser inspection to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets the visible or relevant text from a page. This is useful when a caller needs plain page content rather than a full interactive page description.

**Data flow**: It receives tool arguments, opens the browser session, forwards the arguments to the session’s text-reading method, and returns the text result.

**Call relations**: The page-text tool calls this. It follows the same pattern as the other browser actions: open or reuse the session through `BuaSurface._open`, then let `BrowserSession` perform the browser-specific work.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds items on the page, using an optional host-side completer to improve or finish the search. This helps browser tools locate page elements in a model-friendly way.

**Data flow**: It receives search arguments, opens the browser session, passes the arguments plus the surface’s optional `find_completer` to the session, and returns the find result.

**Call relations**: The find tool calls this when it needs to locate something on the page. It uses `BuaSurface._open` first, then gives both the request and the configured completer to `BrowserSession.find`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on the page. It gives callers a single tool-facing method while the session performs the browser-level typing and selection.

**Data flow**: It receives form-input arguments, opens the browser session, sends those arguments to the session’s form-input method, and returns the result.

**Call relations**: The form-input tool calls this during page interaction. It depends on `BuaSurface._open` for connection setup and then hands the actual page editing to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as direct interaction commands, through the current browser session. It is the surface method for actions that are not simple navigation, reading, or form filling.

**Data flow**: It receives action arguments, opens the browser session, forwards the arguments to the session’s computer-control method, and returns the session’s response.

**Call relations**: The computer-control browser tool calls this when it needs direct interaction with the page. It reuses `BuaSurface._open` and then delegates the detailed browser command to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns the downloaded bytes in a tool-friendly form. It also protects the returned filename so a web page cannot smuggle path tricks like `../` into a later file write.

**Data flow**: It receives download-wait arguments, opens the browser session, waits for the session to report a completed download, asks the lease to fetch the bytes using the download ID, base64-encodes those bytes in a worker thread, trims the filename down to a safe single path segment, and returns the filename, encoded content, and size.

**Call relations**: A download-waiting tool calls this after triggering a download. It uses `BuaSurface._open` to talk to the browser, `BuaSurface._lease` to retrieve the file from the transport, `contained_leaf` to make the filename safe, and `asyncio.to_thread` to avoid blocking the event loop during encoding.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the browser lease, and clears the saved reattach token. This is the normal end-of-turn cleanup that prevents browser sessions from leaking or being reused after release.

**Data flow**: It looks at the surface’s current session and lease. If a session exists, it closes it and clears the field. Whether or not that succeeds, it closes the lease if present, clears the lease field, and writes `None` to the stored token.

**Call relations**: Turn cleanup calls this when the browser surface is no longer needed. It uses `BuaSurface._store_token` at the end so normal cleanup removes the recovery token, while a hard crash that skips this method leaves the token behind for recovery.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loosely typed tab ID from tool arguments into an integer tab ID or no tab ID. This lets callers pass common JSON-like values while keeping boolean values from accidentally becoming tab numbers.

**Data flow**: It receives one JSON-style value. It returns `None` for booleans, empty strings, and unsupported values; returns integers as-is; converts floats with `int`; and parses non-empty strings as integers.

**Call relations**: `BuaSurface.navigate` uses this before asking the session to load a URL, so the session receives a clean tab identifier instead of raw user-provided JSON.

*Call graph*: called by 1 (navigate).


### Action contracts and repair
These files define valid browser action requests, identify invented model data, and clean up common model-produced action mistakes before execution.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is a small but important vocabulary for controlling a browser. Without it, one part of the system might say “click here” in one format while another expects a different format, causing actions to fail or be misunderstood.

It uses Pydantic models, which are Python classes that describe and validate data. In plain terms, they check that an action request has the right fields and sensible values before the browser tries to perform it. For example, a scroll direction must be one of up, down, left, or right, and a wait time cannot be more than 30 seconds.

The file first lists all supported action names in ActionType. These include mouse actions, keyboard input, scrolling, screenshots, and waiting. CLICK_ACTIONS groups the click-like actions together so other code can quickly tell whether an action is a kind of click.

ScrollParameters describes how far and in which direction to scroll. ComputerAction is the main “instruction card.” It can point to a pixel coordinate, an element reference found on the page, text to type, scroll settings, or timing information. The important idea is that every browser action is represented as structured data before anything is actually done.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `validation during browser automation requests`

This file is small, but it gives an important kind of mistake a clear name. In this project, the browser automation layer receives values from a model, such as references to page elements. Sometimes the model may return a value that looks valid in shape but cannot actually exist, for example an element reference that was never created by the browser. That situation is called a “hallucination” here, meaning the model confidently supplied invented information.

The file defines `HallucinationError`, a custom error type. It inherits from `ValidationError`, which means it is still treated as a validation problem: the system checked the model’s answer and found it unusable. But by giving this case its own class, callers can react more precisely. For example, they might log it differently, retry with clearer instructions, or report that the model chose a nonexistent browser object.

An everyday analogy: if someone gives you a library call number in the right format, but no book with that number exists, this error is the label for that exact problem.


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `request handling`

A browser automation model may return a list of actions like “click here,” “type this,” “scroll,” or “wait.” In practice, those instructions are often almost right but not quite safe to run. This file acts like a proofreader for those actions before the browser receives them.

The main repair step is `fixup_actions`. It walks through the action list and makes small, practical corrections. If a type action points at a place or page element but no click came just before it, the file inserts a click first so the text goes into the intended field. If the text contains literal strings like `\n` or `\t`, it turns them into a real newline or tab. If a scroll has no position to start from, it anchors the scroll in the center of the model’s view of the page. If a `scroll_to` action has no target reference, it downgrades it to an ordinary scroll. If a wait has no duration, it uses a default of three seconds. It also simplifies multi-clicks aimed at a referenced element into a normal left click, because referenced targets do not carry the exact point needed for double or triple clicks.

The second public helper, `split_at_waits`, divides a repaired action list into smaller batches, ending each batch after a wait. This lets the browser pause and settle before the next set of actions runs.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: Repairs a list of browser actions so they are safer and more complete before execution. Someone would use it when actions come from a model or other uncertain source and need practical cleanup.

**Data flow**: It receives a list of `ComputerAction` objects, the browser viewport size, and optionally the size the model used when choosing coordinates. It first works out the effective model size and center point, then walks through each action. Depending on the action type, it may insert a focusing click, rewrite escaped text, add a missing scroll coordinate, convert an unusable `scroll_to` into a normal scroll, add a default wait time, or simplify a referenced double/triple click. It returns a new list of actions, leaving the caller with a cleaner sequence to dispatch.

**Call relations**: This is the central repair pass in the file. When it needs a click before typing, it asks `_focus_click` to build that click. When it sees text that may contain written-out escape sequences, it asks `_unescape_text` to clean the text. It also relies on `effective_model_size` to know what “center of the screen” means, and creates `ComputerAction` and `ScrollParameters` objects when it has to replace or fill in actions.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: Splits a list of actions into batches, with each wait action ending the current batch. This is useful because a wait usually means the page needs time to react before the next instructions run.

**Data flow**: It receives a list of actions. It builds a current batch one action at a time. Whenever it sees an action whose type is `wait`, it closes that batch and starts a new one. At the end, it includes any remaining actions. The result is a list of action lists, each ready to run as a separate chunk.

**Call relations**: This function stands alone in this file. It does not call the repair helpers. In the larger flow, it would typically be used after actions have been prepared, so the browser can run one batch, pause at a wait, then continue with the next batch.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: Creates a simple left-click action that focuses the place where a later typing action is meant to go. It is a helper for the case where the model says to type into something but forgot to click there first.

**Data flow**: It receives a `ComputerAction`, usually a type action that contains either a coordinate or a reference to a page element. If there is a coordinate, it creates a left click at that coordinate. Otherwise, it creates a left click using the action’s reference. The output is a new `ComputerAction` that can be inserted before typing.

**Call relations**: `fixup_actions` calls this helper when it sees a type action with a target but no immediately previous click. `_focus_click` does only the small job of building the missing click, then hands that new action back to `fixup_actions` to place into the outgoing action list.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: Turns written-out escape sequences in typing text into the real characters they represent. For example, the two visible characters `\n` become an actual newline.

**Data flow**: It receives a `ComputerAction` and reads its text, using an empty string if there is no text. If the text does not contain known escaped literals, it returns the same action unchanged. If it finds `\t` or `\n`, it replaces them with a real tab or newline and returns a copied action with the updated text.

**Call relations**: `fixup_actions` calls this helper for type actions after any needed focus click has been considered. `_unescape_text` performs only the text cleanup and uses the action’s copy method to preserve the rest of the action while changing the text field.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Input and form primitives
These files translate form, upload, and keyboard-oriented requests into concrete browser automation operations.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

This file is the bridge between a high-level instruction, such as “type this value into that field” or “upload these files,” and the browser’s low-level control channel. The browser page gives elements short references, but the browser engine needs deeper internal IDs before it can act on them. This file looks up those references, asks the browser to resolve them, and then either runs a small piece of JavaScript on the element or calls the browser’s file-input command.

The main class, BrowserForms, is given a browser session object. That session knows how to find a tab, talk to Chrome DevTools Protocol, or CDP, which is the browser’s remote-control API, and turn a page reference into a real browser node. For normal inputs, the file runs JavaScript that sets the right kind of value: checked state for checkboxes and radio buttons, selected value for dropdowns, text for editable areas, and value for ordinary fields. It also fires input and change events, which matters because modern web pages often listen for those events before they notice a form changed.

For file uploads, it uses the browser’s native file-input command. It also includes a helper to read the actual byte sizes of attached files, because a remote upload may have a filename before the browser can really see the file contents.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It provides access to the current page or to a specific tab when a tab ID is supplied.

**Data flow**: It receives an optional tab number. The implementing browser session uses that number to choose the right page, then returns the page object that later form actions will work against.

**Call relations**: BrowserForms methods rely on this contract at the start of each operation. Before they can resolve an element reference or send browser commands, they ask the session for the correct page.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It returns the connection used to send low-level commands to the browser.

**Data flow**: It takes no extra input beyond the session itself. It returns a CDP connection, meaning the communication line used to send remote-control messages to the browser.

**Call relations**: BrowserForms uses this connection when it needs browser-native actions, such as resolving a DOM node or setting files on a file input. The session supplies the wire, and BrowserForms decides what message to send through it.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It runs a JavaScript function on a specific browser object, such as an input element.

**Data flow**: It receives a browser session ID, an object ID for the element, the JavaScript function text, and optional arguments. The browser runs that function on the target object and returns a JSON-like result.

**Call relations**: BrowserForms.input uses this to run the form-filling JavaScript on the chosen element. BrowserForms.attached_sizes uses it to inspect a file input and read the sizes of the files currently attached.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a page-level element reference into the internal browser node information needed for CDP commands.

**Data flow**: It receives a page object and a reference string that came from a prior page read. It returns the browser node plus the backend node ID, which is the browser’s stable internal handle for that element.

**Call relations**: Every BrowserForms action starts with a human-facing reference, not an internal browser ID. This method is the lookup step that lets upload_file, input, and attached_sizes move from that reference to something the browser can act on.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what file sizes a file-upload field is actually holding. It is useful because in remote browser control, a file may be named before the full upload is truly visible to the browser.

**Data flow**: It reads the optional tab ID and required element reference from the input arguments. It finds the page, resolves the reference to a browser node, asks the browser for an object ID, runs JavaScript on that element to inspect its files, and returns a list of numeric byte sizes. If the browser reply does not contain a proper list, it safely returns an empty list.

**Call relations**: This method uses _tab_id to normalize the tab argument, then relies on the browser session to find the page and resolve the element. It sends a DOM.resolveNode command through the CDP connection and then hands the resolved object to call_on so the small JS_ATTACHED_SIZES script can read the file sizes.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a browser file-input element. It is the automation equivalent of choosing files in an upload picker.

**Data flow**: It reads the optional tab ID, the target element reference, and the list of file paths from the arguments. It checks that the reference and paths are strings, resolves the element reference, and sends the file paths to the browser’s file-input command. On success it returns the same reference and the accepted file paths; if the target is not a file input, it raises a clear HallucinationError telling the caller to re-read the page and use a real file-input reference.

**Call relations**: This method uses _tab_id to choose the tab and the wire helpers to validate argument shapes. After resolve_ref gives it the browser node, it calls the CDP command DOM.setFileInputFiles. If CDP rejects the command, upload_file turns that low-level failure into a user-facing HallucinationError.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a form field in the browser. It works across common field types, including text boxes, checkboxes, radio buttons, dropdowns, and editable text areas.

**Data flow**: It reads the optional tab ID, the element reference, and the value to place into the field. It finds the right page, resolves the reference to a browser node, asks the browser to turn that node into a JavaScript object, and then runs JavaScript that sets the element’s value in the correct way and fires change notifications. It returns the browser’s reply, usually including the resulting value.

**Call relations**: This method uses _tab_id first, then asks the browser session to resolve the page reference. It sends DOM.resolveNode through the CDP connection, and then uses call_on to run JS_FORM_INPUT on the actual element. If the reference cannot be resolved, it raises HallucinationError so the caller knows the page should be read again for fresh references.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab ID supplied in different simple forms into either an integer tab ID or no tab choice at all. It lets callers pass a number or a numeric string without each form action repeating the same cleanup.

**Data flow**: It receives a JSON-like value that may be an integer, a floating-point number, a non-empty string, or something else. Integers are kept, floats are converted to integers, non-empty strings are parsed as integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.input, and BrowserForms.upload_file call this before asking the browser session for a page. In the larger flow, it is the small adapter that turns loose tool input into the tab format the browser page lookup expects.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `browser action handling`

Browsers do not just need to know “the user typed A.” They expect a detailed keyboard event: which physical key was pressed, what character it means, which modifier keys are held, whether it came from the keypad, and sometimes special Mac editing commands. This file is the translation table and small engine that builds those events.

It starts with a US keyboard layout copied from Playwright, a browser automation project. That layout says, for example, that Digit1 normally means “1” but with Shift means “!”. The file expands that layout into a lookup map so callers can ask for keys in several ways, such as “KeyA”, “a”, or “Shift”. It also keeps a small KeyboardState, like a notepad recording which keys and modifier keys are currently held down.

The main public helpers are key_down, key_up, press_combo, and type_text. They update the state and return Chrome DevTools Protocol calls, written here as method-name plus JSON data. For long text, the file intentionally skips key-by-key typing and sends one insertText call, like pasting a paragraph, because that is faster. For short text, it sends real key events so web pages that react to individual keystrokes, autocomplete, or shortcuts still see them.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds a convenient lookup table from the raw keyboard layout. It lets later code find the same key by physical code, visible character, shifted character, or common alias.

**Data flow**: It receives the US keyboard layout, where each physical key has its normal and shifted meanings. For each entry, it creates a KeyDescription for the normal key and, when needed, another version for the Shift result. It returns a larger dictionary that can answer lookups such as “KeyA”, “a”, “A”, or “Enter”.

**Call relations**: This runs when the module is loaded to create LAYOUT_CLOSURE. Later, key_down and key_up rely on that prepared lookup through _description_for instead of rebuilding keyboard knowledge each time.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Converts the currently pressed modifier keys into the number format Chrome expects. A modifier key is a key like Shift, Control, Alt, or Meta that changes what another key means.

**Data flow**: It receives a set such as {"Control", "Shift"}. It adds together the predefined bit values for the modifier names that are present. It returns one integer that Chrome can read as “these modifiers are active.”

**Call relations**: key_down and key_up call this just before creating a Chrome key event. It supplies the compact modifier value included in every dispatched keyboard event.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the browser-facing description for a requested key, taking the current keyboard state into account. It is where “1 while Shift is held” becomes “!”.

**Data flow**: It receives the current KeyboardState and a requested key name or character. It looks up that key in the prepared layout table. If the key is unknown, it raises a ValidationError. If Shift is held and the key has a shifted form, it swaps in that form. If other modifiers like Control or Alt are held, it removes typed text so the event behaves like a shortcut rather than text entry. It returns the final KeyDescription.

**Call relations**: key_down and key_up both ask this function what exact key event they should describe. It uses the lookup table built by _build_layout_closure and protects the rest of the flow from invalid key names.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up special macOS editing commands for a key combination. These commands help Chrome on Mac interpret shortcuts like Command+Arrow or Option+Backspace the way native text fields do.

**Data flow**: It receives a physical key code and the set of currently pressed modifiers. It builds a shortcut name in a fixed order, checks the Mac editing-command table, removes trailing colons from command names, and drops insert-style commands. It returns a list of command names to attach to the key event.

**Call relations**: key_down calls this only when the target browser is on Mac. Its result is placed into the Chrome DevTools Protocol event so Mac-specific editing behavior can be reproduced.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome message for pressing a key down. It also updates the remembered keyboard state so later events know which keys are still held.

**Data flow**: It receives the KeyboardState, a key name or character, and whether the browser is Mac. It asks _description_for for the exact key meaning, checks whether this is an auto-repeat press, records the key as pressed, records modifier keys if needed, optionally adds Mac editing commands, and builds an Input.dispatchKeyEvent call. The returned value is the protocol method name plus the JSON payload Chrome expects.

**Call relations**: press_combo uses this for every key in a shortcut, and type_text uses it for each short, typable character. It depends on _description_for for key identity, _mac_commands for Mac behavior, and modifiers_mask for Chrome’s modifier-number field.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome message for releasing a key. It also clears that key from the remembered pressed-key state.

**Data flow**: It receives the KeyboardState and a key name or character. It finds the current key description, removes the key from pressed_keys, removes it from pressed_modifiers if it is a modifier, and returns an Input.dispatchKeyEvent message of type keyUp with the remaining modifiers included.

**Call relations**: press_combo calls this after pressing all keys, in reverse order, so shortcuts are released naturally. type_text calls it after each synthesized character. It uses _description_for and modifiers_mask to keep the key-up event consistent with key_down.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string like “Ctrl+C” or “Shift+Enter” into a full press-and-release sequence. This is useful when automation needs to trigger browser or page shortcuts.

**Data flow**: It receives the KeyboardState, a combo string, and whether the browser is Mac. It splits the string on plus signs, trims spaces, converts common names like “ctrl” into the canonical key name “Control”, and rejects an empty combo. It then presses each key in order and releases them in reverse order. It returns the list of Chrome protocol calls.

**Call relations**: This is a higher-level helper built on key_down and key_up. Callers can provide one human-readable shortcut, and this function expands it into the individual browser events needed to perform it.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text into browser input events. It balances realism and speed: short text is typed key by key, while long text is inserted in one operation.

**Data flow**: It receives the KeyboardState, the text to enter, and whether the browser is Mac. If the text is longer than the configured limit, it returns one Input.insertText call containing the whole text. Otherwise, it walks character by character. Characters known in the keyboard layout become key_down plus key_up events; characters outside the layout are inserted directly. It returns the full list of protocol calls.

**Call relations**: This is the text-entry companion to press_combo. It calls key_down and key_up when realistic keystrokes matter, but hands long or unusual text directly to Chrome through insertText to avoid unnecessary event-by-event work.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Browser action execution
This file runs high-level browser actions against the page and returns screenshots and safety guidance after execution.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the bridge between an outside caller and a live browser tab. The caller sends a list of actions in a simple format. BrowserComputer checks and adjusts those actions, finds the right tab, performs each action, waits for the page to settle, and then reports what happened.

Think of it like a remote-control operator for a web browser. If the caller says “click this button,” the file converts model coordinates into actual screen coordinates, sends mouse events through Chrome DevTools Protocol, or CDP, which is the browser’s remote-control API, and then captures a screenshot. It also supports typing, keyboard shortcuts, dragging, scrolling, waiting, and scrolling an element reference into view.

The file adds guardrails. It warns when repeated scrolling may be inefficient, reminds the caller not to sign in without user approval if tab titles look like login pages, points out newly started downloads, and explains how to interact with native dropdown menus when normal clicking will not work. If a click happened, it draws a small blue marker on the returned screenshot so the caller can see where it clicked.

Without this file, the rest of the system could describe intended browser actions, but it would not have the central place that validates them, performs them in the browser, waits for results, and packages a useful response.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the session interface that promises a way to get the browser tab to act on. A real session class supplies the actual implementation.

**Data flow**: It receives an optional tab id. In a real implementation, that id is used to choose a tab, and the result is a tab object with a browser session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this contract at the start of a request so it knows which tab should receive the actions.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session interface that promises access to the browser’s CDP connection. CDP, or Chrome DevTools Protocol, is the remote-control channel used to send browser commands.

**Data flow**: It takes no extra input. In a real implementation, it returns the active connection object that can send commands to the browser.

**Call relations**: BrowserComputer uses this connection throughout a run to send mouse, keyboard, screenshot, scrolling, and page-inspection commands.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This interface method promises a way to summarize the current tab for the final response. The summary is included alongside the screenshot and action output.

**Data flow**: It receives a tab object. A real implementation reads the tab’s current state and returns a JSON-style dictionary of tab information.

**Call relations**: BrowserComputer.run calls it near the end, after actions and settling, so the response describes the tab after the work is done.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This interface method promises a way to read the titles of open tabs. The titles are used as a simple clue for safety reminders, such as detecting sign-in pages.

**Data flow**: It takes no direct input. A real implementation reads browser tab titles and returns them as text strings.

**Call relations**: BrowserComputer.run calls it before building the final response, then passes the titles to sign_in_warning.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This interface method promises a way to run a small JavaScript function on a specific browser object. It is used when the code needs details from an element on the page.

**Data flow**: It receives a browser session id, an object id, JavaScript code, and optional arguments. A real implementation runs that code against the object and returns a JSON-style result.

**Call relations**: BrowserComputer._select_reminder uses this contract to inspect a clicked dropdown and learn its available options.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This interface method promises a way to turn a page reference, such as a short element id returned by page-reading tools, into the browser’s internal node information.

**Data flow**: It receives a tab and a reference string. A real implementation looks up that reference and returns the matching browser node plus its backend node id.

**Call relations**: BrowserComputer.act uses it for scroll_to actions so the browser can scroll the referenced page element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This interface method promises a way to turn an element reference into a clickable point on the page. It lets callers act on named page elements instead of raw coordinates.

**Data flow**: It receives a tab and a reference string. A real implementation finds the element and returns an x, y point in viewport coordinates.

**Call relations**: BrowserComputer.point calls it whenever an action names a ref, and BrowserComputer.act then uses that point for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for carrying out a batch of browser actions. It validates the request, performs each action, waits for the page to calm down, captures a screenshot, and returns a human-readable report.

**Data flow**: It receives a JSON-style dictionary containing a tab id and a list of actions. It chooses the tab, turns raw action data into ComputerAction objects, adjusts coordinates, runs the actions in batches, gathers warnings about scrolling, sign-in pages, downloads, and dialogs, captures a JPEG screenshot, optionally marks the last click, and returns tab info, output text, last-click coordinates, and the screenshot as base64 text.

**Call relations**: This function is the conductor. It calls int_or_none to read the tab id, uses action validation and action fixup, delegates each individual action to BrowserComputer.act, asks _select_reminder for dropdown advice after clicks, uses _to_model for reporting coordinates, calls sign_in_warning before finishing, and uses mark_click in a worker thread so image editing does not block the async flow.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This function performs one browser action, such as clicking, typing, pressing a key, waiting, scrolling, or taking a screenshot. It turns the action name into the specific browser commands needed.

**Data flow**: It receives the active tab and one validated action. It first asks BrowserComputer.point for any needed target point, then branches by action type: mouse actions call _click or _drag, text and key actions call _dispatch, scroll actions call _scroll or the browser’s element-scrolling command, waits sleep for a limited time, and screenshots simply report success. It returns a short message and, when relevant, the viewport point that was clicked or dragged to.

**Call relations**: BrowserComputer.run calls this for every action in a batch. act then hands off low-level work to _click, _drag, _scroll, _dispatch, _to_viewport, _to_model, require_point, and require_coord as needed.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This function finds the target point for an action. It lets actions use either an element reference or explicit coordinates.

**Data flow**: It receives a tab and an action. If the action has a ref, it asks the browser session for that element’s point; if it has coordinates, it converts them from model space to viewport space; otherwise it returns nothing.

**Call relations**: BrowserComputer.act calls this before deciding how to perform an action. When coordinates are supplied, point uses _to_viewport so the browser receives coordinates in the form it expects.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts coordinates from the system’s model-sized coordinate grid into the browser viewport’s coordinate grid. This matters because the caller and the real browser window may use different sizes.

**Data flow**: It receives an x, y pair in model coordinates. It wraps the pair in a coordinate object, scales it using the known model size and viewport size, and returns the matching viewport x, y pair.

**Call relations**: BrowserComputer.act and BrowserComputer.point call this whenever an incoming coordinate must be sent to the browser as a real screen position.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts coordinates from the browser viewport back into the model-sized coordinate grid. It is mainly used so reports back to the caller use the same coordinate system the caller understands.

**Data flow**: It receives an x, y pair in viewport coordinates. It scales that point back to model coordinates and returns the converted pair.

**Call relations**: BrowserComputer.act uses it when writing messages like “Clicked (x,y),” and BrowserComputer.run uses it when returning the last-click location.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This function checks whether a click landed on a native HTML select dropdown and, if so, builds a reminder explaining the safer way to choose an option. Native dropdowns are special browser controls, and simulated option-clicking may not work reliably.

**Data flow**: It receives the tab and the clicked viewport point. It asks the browser what page element is at that point, walks up to a SELECT element if present, reads a sample of its option text and total option count, tries to get a usable element reference, and returns a reminder string. If inspection fails or the element is not a select dropdown, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. _select_reminder then calls select_reminder to turn the discovered dropdown details into a message for the final output.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This helper sends a prepared list of keyboard-related browser commands. It is used for typing text and pressing key combinations.

**Data flow**: It receives a tab and a list of CDP method-and-parameter pairs. It sends each command to the browser connection for that tab and does not return a value.

**Call relations**: BrowserComputer.act calls this after key helper functions have prepared the exact browser input commands for typing or keyboard shortcuts.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This helper sends one mouse event to the browser. It keeps the repeated CDP command shape in one place.

**Data flow**: It receives a tab and a dictionary of mouse event details, such as event type, button, position, and modifier keys. It sends those details through the browser connection and returns nothing.

**Call relations**: _click, _drag, and _scroll all call this to send the individual mouse movements, presses, releases, and wheel events that make up larger actions.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This function performs a real browser click at a given point. It supports left, right, double, and triple clicks by sending the needed mouse press and release events.

**Data flow**: It receives a tab, viewport coordinates, a mouse button name, and a click count. It reads the currently pressed keyboard modifiers, moves the mouse to the point, then sends matching mousePressed and mouseReleased events for each click. It changes browser state by interacting with the page, but returns no value.

**Call relations**: BrowserComputer.act calls this for click actions. _click delegates each raw mouse event to _mouse_event and uses the keyboard modifier mask so clicks happen with Shift, Ctrl, Command, or similar keys if they are currently held.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This function performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop after seeing movement along the way.

**Data flow**: It receives a tab and start and end viewport coordinates. It reads keyboard modifiers, moves to the start, presses the left mouse button, sends several intermediate move events, then releases at the end. The result is a browser-side drag action, with no direct return value.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. _drag uses _mouse_event for every movement, press, and release.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This function performs a mouse-wheel scroll at a specific point on the page. It can scroll horizontally or vertically depending on the deltas it receives.

**Data flow**: It receives a tab, viewport coordinates, and horizontal and vertical scroll amounts. It moves the mouse to the target point, then sends a mouseWheel event with those deltas. It changes the page’s scroll position and returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating the scroll direction and size. _scroll uses _mouse_event to send both the mouse move and wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This helper decides whether to show a safety reminder about signing in. It looks for login-related words in browser tab titles.

**Data flow**: It receives a list of tab titles. It lowercases them, searches for words like “login” and “sign in,” and returns the warning text if any title matches; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this while building the final output reminders, after it has collected the current tab titles.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This helper writes a clear message explaining how to interact with a native select dropdown. It includes visible option names so the caller knows what values are available.

**Data flow**: It receives an optional element reference, a list of option labels, and the total number of options. It formats the shown options, notes if more options exist, chooses instructions based on whether a ref is available, and returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked dropdown and gathered its option details.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This function draws a small translucent blue dot on a screenshot at the last clicked point. It helps the caller visually confirm where the click happened.

**Data flow**: It receives a base64-encoded screenshot and a viewport x, y point. It decodes the image, draws a circular overlay at that point, saves the image again as a JPEG, base64-encodes it, and returns the new screenshot text.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click location to show. It is run through an executor because image processing is ordinary blocking work, while run is asynchronous.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This small helper reads a possible tab id from loose JSON input. It accepts common number-like forms and treats missing or unsuitable values as no tab id.

**Data flow**: It receives a JSON value or nothing. If the value is an integer, float, or non-empty string, it converts it to an integer; otherwise it returns None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page, so the tab choice is normalized first.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that an action has a target point when one is required. It gives a clear validation error instead of letting later code fail mysteriously.

**Data flow**: It receives a possible point and the action name. If the point exists, it returns it unchanged; if not, it raises a ValidationError saying the action needs a coordinate or ref.

**Call relations**: BrowserComputer.act calls this before actions such as clicks and drag endings that cannot work without a target point.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that a required coordinate field is present. It is used when an action needs a specific coordinate beyond the main target point.

**Data flow**: It receives a possible coordinate and the field name. If the coordinate exists, it returns it unchanged; if not, it raises a ValidationError naming the missing field.

**Call relations**: BrowserComputer.act calls this for drag actions to make sure the start_coordinate was supplied before converting it and starting the drag.

*Call graph*: called by 1 (act); 1 external calls (__init__).

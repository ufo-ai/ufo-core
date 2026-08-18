# Browser providers and agent-facing adapters  `stage-10.3.1`

This stage is the bridge between agents and web browsers. It is shared support used during the main work loop whenever an agent needs to visit a site, read a page, click buttons, type text, upload files, or collect downloads.

At the center is `core/src/ufo/browser.py`, which defines a simple promise: “give me a Chrome DevTools connection for this turn.” Chrome DevTools is the control channel that lets software drive Chrome. The core code does not care where Chrome comes from.

Different providers fulfill that promise. `ufo_ext_sandbox_chrome.py` starts or reuses a real Chrome inside the conversation’s sandbox and exposes a safe connection to it. `ufo_ext_browserbase.py` instead creates a hosted Browserbase Chrome session, moves files in and out, and cleans it up afterward. `ufo_ext_browser_use.py` sends whole browsing tasks to Browser Use’s cloud agent.

On top, `backend.py` manages the per-turn browser object and opens connections only when needed. `tools.py` turns browser actions into agent-callable tools. `delegation.py` lets the main agent hand browsing to one or many separate browser agents.

## Files in this stage

### Direct browser tools
Agent-callable browser tools hand navigation and interaction requests to the per-turn browser backend.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `active during an agent turn when browser tools are called`

This file turns browser actions into safe, well-shaped tool calls. A model may ask to “navigate to this URL” or “find the submit button,” but the rest of the system needs those requests checked, routed to the right browser session, and returned in a standard format. This file does that work.

It defines input shapes for each browser tool, using Pydantic models. Those models say what information is allowed for each action, such as a URL, a tab id, a file reference, or a short user-facing description for the activity timeline.

The key helper is `_browser`. It creates one `BuaSurface` for the current turn the first time any browser tool is used. A `BuaSurface` is the object that actually talks to the browser automation backend. The surface is cached for the turn, like borrowing one set of keys for the whole visit instead of asking for new keys at every door. Cleanup is registered so the browser connection and any remote session lease are closed when the turn ends.

Most tool functions are thin translators: they remove timeline-only fields, call the matching `BuaSurface` method, and wrap the reply as JSON text. Two tools do extra file work. `computer` can attach a screenshot image to the tool result and optionally save it into the workspace. `wait_for_download` waits for downloaded bytes, decodes them, and writes the file into the shared workspace so other agents can use it by path.

#### Function details

##### `_browser`  (lines 105–128)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser surface for the current turn, creating it only if this is the first browser action in that turn. This prevents every tool call from opening its own browser connection and makes sure the connection is cleaned up afterward.

**Data flow**: It takes the tool context, looks in a per-turn cache keyed by the cleanup registry, and returns the existing `BuaSurface` if one is already there. If none exists, it reads the configured browser connection provider, the page-finding helper, the agent model, sandbox, extension store, and conversation id from the context, builds a new `BuaSurface`, stores it, registers its close method for cleanup, and returns it. If no browser connection provider is configured, it raises an error instead of pretending browser work can happen.

**Call relations**: Every browser tool function asks `_browser` for the surface before doing real browser work. `_browser` is the shared doorway into `BuaSurface`, so calls such as navigation, page reading, file upload, computer actions, and download waiting all reuse the same browser session within the turn.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 131–132)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain Python dictionary as a standard tool result containing JSON text. This gives callers a consistent way to receive browser replies.

**Data flow**: It takes a reply dictionary, converts it to a JSON string, places that string inside a text content object, and returns a tool result containing that text. It does not change the original browser reply.

**Call relations**: Most browser tool functions call `_json_result` after receiving a reply from the browser surface. It is the final packaging step for tools whose answer is ordinary structured text rather than an attached image or file.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 135–138)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a value from the browser backend is a non-empty string when the rest of the code must have one. It gives a clear error if a required field is missing or malformed.

**Data flow**: It receives a value and the name of the field that value came from. If the value is a non-empty string, it returns it. If not, it raises a `ValueError` saying which browser reply field is missing.

**Call relations**: `_computer` uses this when it needs screenshot bytes before saving them, and `_wait_for_download` uses it when it needs a filename and downloaded file content. It protects the file-writing steps from trying to decode or save bad data.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 141–145)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool, such as going to a URL or moving through browser history. It is used when the agent wants to change what page a tab is showing.

**Data flow**: It receives the tool context and validated navigation arguments. It converts the arguments into a plain JSON-friendly dictionary, leaving out empty fields and the user-facing timeline description, sends that to the browser surface’s navigation method, then returns the browser’s reply as JSON text.

**Call relations**: This function is the registered handler for the `navigate` tool. It gets the shared browser surface through `_browser`, hands off the actual browser movement to that surface, and uses `_json_result` to return the answer in the tool system’s format.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 148–149)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns context about the currently open browser tabs. It helps the agent understand what pages are open before deciding where to act next.

**Data flow**: It receives the tool context and validated arguments, but the browser request itself needs no extra fields. It asks the browser surface for tab context and wraps the returned information as JSON text.

**Call relations**: This function is the handler for the `tabs_context` tool. It uses `_browser` to reach the current browser session and `_json_result` to package the tab overview for the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 152–154)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opening a given URL. If no URL is supplied, it opens a blank page.

**Data flow**: It receives the tool context and tab creation arguments. It chooses the requested URL or falls back to `about:blank`, sends that to the browser surface, and returns the resulting tab information as JSON text.

**Call relations**: This function is the handler for the `tabs_create` tool. It does a small amount of defaulting, then relies on `_browser` for the live browser session and `_json_result` for the response format.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 157–161)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, either the current one or a specific tab if a tab id is supplied. It is used when the agent is done with a page and wants to reduce clutter.

**Data flow**: It receives the tool context and close-tab arguments. It converts the arguments into a JSON-friendly dictionary, excluding the user-facing description and empty fields, sends that to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: This function is the handler for the `tabs_close` tool. It acts as a small translator between the tool input model and the browser surface’s tab-closing operation.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 164–168)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file input on a web page using files from the shared workspace. It lets the agent upload documents without exposing local machine paths directly in the user-facing description.

**Data flow**: It receives the tool context and upload arguments, including a browser element reference and one or more workspace file paths. It removes the timeline-only description, sends the remaining data to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: This function is the handler for the `upload_file` tool. It gets the browser session from `_browser`, asks the browser surface to attach the workspace files to the page element, and uses `_json_result` to report what happened.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 171–175)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page structure in a way useful for automation, including elements the agent may click or type into. This is how the agent gets a navigable map of the page instead of just pixels.

**Data flow**: It receives the tool context and page-reading options such as depth, filter, element reference, or tab id. It converts the options into a JSON-friendly request, leaves out empty values and the user description, sends it to the browser surface, and returns the structured page information as JSON text.

**Call relations**: This function is the handler for the `read_page` tool. It relies on `_browser` for access to the active browser and `_json_result` to make the page snapshot readable by the tool caller.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 178–182)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page or a selected tab. It is useful when the agent needs the page’s words more than its clickable structure.

**Data flow**: It receives the tool context and optional tab id. It turns the arguments into a request without the timeline description or empty fields, sends that request to the browser surface, and returns the extracted text result as JSON text.

**Call relations**: This function is the handler for the `get_page_text` tool. It is a direct bridge from the tool system to the browser surface’s text extraction feature.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 185–189)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the browser page for elements matching a query, such as text, role, name, or URL. It helps the agent locate the right target before clicking, typing, or reading more deeply.

**Data flow**: It receives the tool context and a search query, plus an optional tab id. It removes the user-facing description, sends the search request to the browser surface, and returns the matches as JSON text.

**Call relations**: This function is the handler for the `find` tool. It uses `_browser` to reach the browser surface, which may use the configured finding/ranking helper from the context, then packages the search results through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 192–196)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. It is used for actions like typing text into an input, selecting a value, or filling a field with structured data.

**Data flow**: It receives the tool context, a browser element reference, a value, and optionally a tab id. It converts those into a JSON-friendly request while excluding the user description, sends the request to the browser surface, and returns the result as JSON text.

**Call relations**: This function is the handler for the `form_input` tool. It translates validated tool input into the browser surface call that actually changes the page.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 199–217)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Performs lower-level browser interaction actions such as mouse moves, keyboard input, scrolling, waiting, and screenshots. It can also save a returned screenshot into the shared workspace.

**Data flow**: It receives the tool context and a list of computer-style actions. It sends those actions to the browser surface after removing the timeline-only description. If the caller asked to save a screenshot, it checks that the reply contains base64-encoded screenshot data, decodes it into bytes, writes it to the sandbox workspace, and adds the saved path to the reply. If the reply includes screenshot data, it returns both JSON text for the non-image fields and an image attachment; if not, it returns only JSON text.

**Call relations**: This function is the handler for the `computer` tool. It uses `_browser` for the actual browser interaction, `_required_str` when screenshot bytes are mandatory for saving, `_json_result` for plain replies, and the tool content classes when it needs to return an image alongside text.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 220–228)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and writes the downloaded file into the shared workspace. This turns a browser-only download into a path that the parent agent or sibling agents can use.

**Data flow**: It receives the tool context and download options such as a download id, destination path, and timeout. It asks the browser surface for the completed download, checks that the filename and base64-encoded content are present, decodes the content into bytes, writes the file under the requested directory or the default downloads directory, and returns the saved path, filename, and size as JSON text.

**Call relations**: This function is the handler for the `wait_for_download` tool. It depends on `_browser` to observe and retrieve the download, `_required_str` to validate essential reply fields, the sandbox to write the file, and `_json_result` to report the workspace path.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `active during a browser-using turn, from first browser tool call through turn cleanup`

A turn may or may not need a browser. This file keeps that choice cheap: it does not connect to Chrome until the first browser action is requested. When a browser is needed, `BuaSurface` gets a lease from a CDP provider. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome from code. The lease tells it where the browser is, and `BrowserSession` then does the actual page work.

The file also protects continuity and cleanup. If a hard crash happens during a browser action, a stored session token can let the next recovered run reconnect to the same live browser instead of starting over. On normal turn end, `aclose` closes the browser session, releases the lease, and clears that token so later turns do not accidentally attach to an old browser.

Uploads and downloads are routed through the lease because the browser may be local to the sandbox or hosted somewhere remote. For uploads, workspace paths are checked, read with size limits, shipped if needed, and then verified so the page really received the bytes. For downloads, the file is fetched from wherever the browser wrote it and returned as base64 text, with the filename reduced to a safe single path segment.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or returns the current browser session for this turn. It is the shared doorway used by almost every browser tool so the code only creates one connection when it is actually needed.

**Data flow**: It starts with the surface's current `session` and `lease` fields. If a session already exists, it returns it. If not, it gets or creates a lease, asks the lease for the browser endpoint and download directory, builds a `BrowserSession`, opens it, stores it on the surface, and returns it.

**Call relations**: The public browser actions call this first before doing page work. When no lease exists yet, it hands off to `BuaSurface._acquire_lease`; after that it creates the `BrowserSession` that the action will use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets permission and connection details for a browser session. It first tries to reconnect to a saved session from a crash recovery, and otherwise starts a fresh browser lease.

**Data flow**: It reads a stored token, if one exists. With a token, it asks the CDP provider to reattach; if the old session is gone, it clears the token. Without a usable token, it leases a new browser, stores that lease's token for possible recovery, and returns the lease.

**Call relations**: `BuaSurface._open` calls this when it needs a lease. It relies on `BuaSurface._stored_token` and `BuaSurface._store_token` to keep the recovery token in the scoped store.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Reads the saved browser reattach token for this conversation, if token storage is available. This token is what lets a recovered turn reconnect to a still-running browser after a crash.

**Data flow**: It checks whether both a scoped store and conversation id are present. If either is missing, it returns nothing. Otherwise it reads the token key from the store and returns the value only if it is a string.

**Call relations**: `BuaSurface._acquire_lease` calls this before deciding whether to reattach to an existing browser or create a new lease.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Writes or clears the saved browser reattach token for this conversation. It is used to remember live sessions during a turn and to forget them after normal cleanup.

**Data flow**: It receives either a token string or `None`. If token storage is not available, it does nothing. Otherwise it writes that value under the conversation-specific token key, where `None` means the token is cleared.

**Call relations**: `BuaSurface._acquire_lease` calls this after creating a new lease or after discovering a dead saved session. `BuaSurface.aclose` calls it at cleanup so future turns do not reconnect to a released browser.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Moves a browser tab to a requested web address. It checks that the caller supplied a real URL string before asking the browser session to navigate.

**Data flow**: It receives an argument dictionary, opens the browser session, reads `url`, validates that it is a string, converts the optional tab id into an integer or `None`, then returns the session's navigation result.

**Call relations**: This is one of the tool-facing methods. It calls `BuaSurface._open` to get the browser and uses `_tab_id` to normalize the tab identifier before handing the request to the session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs, such as what tabs exist and which one is active. This helps tools understand the browser's current state.

**Data flow**: It receives an argument dictionary but does not need values from it. It opens the browser session and returns the tab context reported by that session.

**Call relations**: A tab-inspection tool calls this method. It uses `BuaSurface._open` first, then delegates the actual browser query to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If the caller does not give a usable URL, it opens a blank page.

**Data flow**: It receives arguments, opens the browser session, reads the optional `url`, chooses that URL if it is a non-empty string or `about:blank` otherwise, and returns the session's tab-creation result.

**Call relations**: A tab-creation tool enters here. The method gets the shared session through `BuaSurface._open` and then lets `BrowserSession` perform the browser operation.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab according to the caller's arguments. It keeps the tool layer from needing to know how the browser connection is opened.

**Data flow**: It receives the tab-closing arguments, opens the browser session, passes the arguments through to the session, and returns the close result.

**Call relations**: A tab-close tool calls this method. It uses `BuaSurface._open` for the session and then hands the actual close request to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a web page file input. It makes uploads work whether Chrome can directly see the sandbox filesystem or needs files shipped to a remote browser.

**Data flow**: It receives arguments containing a `files` list. It validates that each item is a non-empty workspace path, resolves it safely under the workspace, asks the lease to place the file where the browser can open it, and supplies a reader callback for transports that must copy bytes. It then sends the placed file paths to the browser session, waits until the page really has the shipped bytes, and returns the upload reply.

**Call relations**: This tool-facing method starts by calling `BuaSurface._open`. For each file it uses `workspace_path`, the current lease from `BuaSurface._lease`, and a partial callback to `BuaSurface._read`. After calling the session upload, it calls `BuaSurface._settle_upload` to guard against remote-upload timing problems.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until a file input actually contains the uploaded bytes. This avoids a subtle failure where a remote browser may see the filename before the file content has arrived.

**Data flow**: It looks at the byte sizes recorded when this surface shipped files. If no bytes were shipped, it returns immediately. Otherwise it repeatedly asks the browser session what file sizes are attached, compares them with the expected sizes, sleeps between attempts, and retries the upload. If the sizes never match, it raises an error.

**Call relations**: `BuaSurface.upload_file` calls this after the first attach. It works with `BrowserSession.attached_sizes` and retries `BrowserSession.upload_file` when the page has not yet received the full file content.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active browser lease, or fails loudly if no lease exists. It is a small safety check for code that must talk to the transport layer.

**Data flow**: It reads the surface's `lease` field. If it is present, it returns it. If not, it raises a runtime error because file transfer cannot happen without a lease.

**Call relations**: `BuaSurface.upload_file` uses this to place files for the browser. `BuaSurface.wait_for_download` uses it to fetch downloaded bytes from the transport.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file's bytes from the sandbox so they can be shipped to a remote browser. It enforces a size limit so large uploads do not fill server memory.

**Data flow**: It receives a resolved file path. It requires a sandbox, quotes the path for safe shell use, runs `stat` in the sandbox to get the size, rejects unreadable or oversized files, runs `base64` in the sandbox to read the bytes as text, decodes that text back into bytes in a worker thread, records the byte length as shipped, and returns the bytes.

**Call relations**: This is supplied as a callback by `BuaSurface.upload_file` when the lease needs file bytes. It uses sandbox shell commands for reading and `asyncio.to_thread` so base64 decoding does not block the main event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads the current page in the richer browser-tool format used by the rest of the browser extension. It gives callers a structured view of what is on the page.

**Data flow**: It receives read arguments, opens the browser session, passes those arguments to the session's page reader, and returns the resulting page data.

**Call relations**: A page-reading tool calls this method. It uses `BuaSurface._open` to get the session, then leaves the detailed browser inspection to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets plain text from the current page. This is useful when the caller needs the words on the page more than the full interactive structure.

**Data flow**: It receives arguments, opens the browser session, forwards the arguments to the session's text extractor, and returns the text result.

**Call relations**: A text-reading tool enters here. The method only prepares the shared browser session through `BuaSurface._open`; `BrowserSession` does the page text extraction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds items on the current page, using an optional host-side completer to improve or finish the search. This helps browser tools locate page elements in a user-facing way.

**Data flow**: It receives find arguments, opens the browser session, passes the arguments and the optional `find_completer` into the session, and returns the find results.

**Call relations**: A find tool calls this method. It gets the browser through `BuaSurface._open` and hands both the request and completer to `BrowserSession.find`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on a page. It is the surface method for typing structured input into web forms.

**Data flow**: It receives form-input arguments, opens the browser session, forwards the arguments to the session, and returns the session's result.

**Call relations**: A form-input tool calls this method. It uses `BuaSurface._open` for connection setup and relies on `BrowserSession` for the actual browser interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as interactions that are closer to screen control than page text reading. It exposes the browser session's computer-control capability through the per-turn surface.

**Data flow**: It receives action arguments, opens the browser session, forwards the arguments to the session's computer method, and returns the result.

**Call relations**: A computer-control browser tool calls this method. Like the other tool methods, it first goes through `BuaSurface._open` so the browser connection is created only once.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns the downloaded file content to the caller. It also makes the returned filename safe to join into a workspace path.

**Data flow**: It receives wait arguments, opens the browser session, waits for the session to report a completed download, asks the lease to fetch the downloaded bytes by download id, base64-encodes the bytes in a worker thread, trims the filename to a safe single leaf name, and returns the filename, encoded content, and size.

**Call relations**: A download-waiting tool calls this method. It uses `BuaSurface._open` to wait through the browser session and `BuaSurface._lease` to retrieve the bytes from the transport location, then uses `contained_leaf` to avoid unsafe path names.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved recovery token at normal turn end. This prevents leaked hosted-browser sessions and prevents later turns from reconnecting to a session that has already been released.

**Data flow**: It checks whether a session exists and closes it, then always checks whether a lease exists and closes that too, even if session closing failed. Finally it clears the stored token. The surface's `session` and `lease` fields are reset to `None` as they are released.

**Call relations**: Turn cleanup calls this after browser use. It calls `BuaSurface._store_token` to remove the reattach token; if a hard crash prevents this method from running, the token intentionally remains for recovery.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab id value from tool arguments into either an integer tab id or `None`. It accepts common input shapes while ignoring values that should not select a tab.

**Data flow**: It receives a JSON-like value. Booleans become `None`, integers stay integers, floats and non-empty strings are converted to integers, and anything else becomes `None`.

**Call relations**: `BuaSurface.navigate` uses this helper before asking the browser session to navigate, so tab ids from tool input are normalized in one place.

*Call graph*: called by 1 (navigate).


### Chrome connection providers
Hosted and sandbox Chrome extensions supply DevTools endpoints through the shared browser-provider contract.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `active during each Browserbase-backed browser turn, from session lease through cleanup`

UFO needs a Chrome browser to drive when it browses the web. This file lets that browser live on Browserbase, a hosted browser service, instead of on the local machine. Think of it like borrowing a clean rental car for one trip: the code checks one out, gives UFO the keys, handles luggage going in and out, and returns the car at the end.

The main provider, BrowserbaseCdpProvider, starts a new Browserbase session for a browser turn. It also creates or reuses a Browserbase Context, which is Browserbase's saved browser state, such as cookies, logins, and local storage. That context is tied to the current conversation so a restarted session in the same run can keep its already-earned login state. When the lease closes, the session is released and the context record is deleted so browser state does not outlive the run.

BrowserbaseApi is the small wrapper around Browserbase's web API. It creates sessions and contexts, checks whether a session is still alive, releases sessions, uploads files, and fetches downloads. BrowserbaseLease is the object UFO receives while a session is active. It provides the Chrome connection URL, a token for reattaching after recovery, and file transfer helpers.

A key detail is that remote Chrome cannot read local file paths. Uploads are sent through Browserbase's upload API, and downloads are fetched from Browserbase storage, with size limits to avoid pulling untrusted giant files into memory.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a fresh hosted Browserbase browser session attached to an existing Browserbase Context. It returns the session's ID and the connection URL UFO needs to control Chrome.

**Data flow**: It receives a context ID. It sends Browserbase a request saying to use that context, keep it persistent, and give the session a long enough timeout. Browserbase replies with JSON, and this function pulls out the session ID and connect URL and returns them as a pair.

**Call relations**: This is used when BrowserbaseCdpProvider.lease is starting a new browser run. It relies on BrowserbaseApi._json to talk to Browserbase and _field to make sure the required response fields are present.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session can still be used, and gets its current connection URL. This is needed when UFO tries to reattach to a browser after recovery.

**Data flow**: It receives a session ID, asks Browserbase for that session's status, and reads the response. If the status says the browser is still running or pending, it returns the connect URL. If the session is gone or no longer usable, it raises SessionGone so the caller knows not to reconnect.

**Call relations**: BrowserbaseCdpProvider.reattach uses this after decoding a saved token. It calls BrowserbaseApi._json for the HTTP request and _field to read required fields safely.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to stop and release a hosted browser session. This prevents the remote browser from continuing to run or bill after UFO is done with it.

**Data flow**: It receives a session ID and sends Browserbase a status change request asking for release. It does not return useful data; success means Browserbase accepted the release request.

**Call relations**: BrowserbaseLease.aclose calls this during cleanup. It uses BrowserbaseApi._json to send the request and catch failed Browserbase responses.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a new Browserbase Context, which is the saved browser state used by sessions in one browser run. This allows a remade session to keep cookies and local storage from earlier in the same run.

**Data flow**: It sends an empty create-context request to Browserbase. Browserbase replies with JSON, and the function extracts and returns the new context ID.

**Call relations**: BrowserbaseCdpProvider._context calls this when there is no stored context for the conversation yet. It uses BrowserbaseApi._json for the request and _field to verify the ID exists.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context after the browser run is finished. This is what stops saved login state from lingering after the subagent's browser session is over.

**Data flow**: It receives a context ID and sends a delete request to Browserbase. If Browserbase accepts the request, nothing is returned.

**Call relations**: BrowserbaseLease.aclose calls this after asking to release the session. It uses BrowserbaseApi._send directly because it does not need to parse a JSON body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed browser download from Browserbase storage. It waits briefly for Browserbase's download listing to catch up, and refuses oversized or unmeasured files.

**Data flow**: It receives a session ID and a download GUID, which is the browser's unique download name. It repeatedly asks Browserbase for that session's download list, looks for the matching filename, checks the reported size, then downloads and returns the file bytes. If the file never appears, is too large, or lacks a size, it raises an error instead.

**Call relations**: BrowserbaseLease.fetch_download delegates to this when UFO needs the actual downloaded file. It uses BrowserbaseApi._json to poll the listing, _size to check safety, _field to read the stored download ID, BrowserbaseApi._send to fetch bytes, and asyncio.sleep between retries.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a local file's bytes into the hosted Browserbase session so remote Chrome can use it. This is needed because a remote browser cannot open a file by a local path on UFO's machine.

**Data flow**: It receives a session ID, a remote filename, and the file bytes. It sends those bytes to Browserbase's session upload endpoint. It returns nothing once the upload succeeds.

**Call relations**: BrowserbaseLease.place_file calls this after reading and naming a workspace file. It uses BrowserbaseApi._send because this is a file upload request rather than a JSON request.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request and checks that the answer is a JSON object. It is a shared safety wrapper for Browserbase endpoints that are expected to return structured data.

**Data flow**: It receives an HTTP method, a path, and request options. It sends the request through BrowserbaseApi._send, parses the response as JSON, verifies that the parsed body is a dictionary-like object, and returns it. If the body has the wrong shape, it raises BrowserbaseError.

**Call relations**: The session, context, status, release, and download-list methods all call this so they do not each repeat the same request-and-validate pattern. It hands the actual network work to BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the current API key. It centralizes authentication, timeouts, test transport injection, and error reporting.

**Data flow**: It receives an HTTP method, API path, timeout, and optional request details. Before sending, it reads the Browserbase API key from the credential slot and puts it in the request headers. It sends the request with httpx, then returns the response if it succeeded. If Browserbase returns an error status, it raises BrowserbaseError with the status and response text.

**Call relations**: All Browserbase network operations eventually pass through this function, either directly or through BrowserbaseApi._json. Tests can supply a custom httpx transport here, while production uses the normal network client.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Extracts a required string field from a Browserbase JSON response. It prevents later code from silently continuing with a missing or empty value.

**Data flow**: It receives a response dictionary and a field name. It looks up that field, checks that it is a non-empty string, and returns it. If the value is missing or not usable, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.create_session, BrowserbaseApi.live_session, BrowserbaseApi.create_context, and BrowserbaseApi.download use this whenever a Browserbase response must contain a specific string such as an ID or URL.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the reported size of a Browserbase download. This protects the process from downloading an unexpectedly huge file into memory.

**Data flow**: It receives one download-list entry. It reads the size field, rejects booleans and non-numeric values, converts the number to an integer, and returns it. If the size is missing or invalid, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.download calls this before fetching a stored download. That makes the size check happen before any file bytes are pulled into this process.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Gives UFO the Chrome DevTools Protocol endpoint for the leased Browserbase session. The Chrome DevTools Protocol, or CDP, is the control channel UFO uses to drive the browser.

**Data flow**: It reads the lease's stored connect URL and wraps it in a CdpEndpoint object. The returned endpoint is what the browser engine uses to connect to remote Chrome.

**Call relations**: The wider browser engine calls this on the active lease when it is ready to connect. This method does not call Browserbase again; it uses the URL received when the session was created or reattached.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a reattachment token that contains everything needed to reconnect to this exact browser run. The token names the conversation, session, and context so recovery does not accidentally attach to another run's browser state.

**Data flow**: It reads the conversation ID, session ID, and context ID stored on the lease. It formats them into one slash-separated string and returns that string.

**Call relations**: The browser system can save this token while a lease is active. Later, BrowserbaseCdpProvider.reattach receives the token and uses _parse_token to recover the IDs.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Uploads a workspace file into the remote Browserbase session and returns the path that hosted Chrome can use. This replaces normal local file paths, which remote Chrome cannot see.

**Data flow**: It receives the workspace path and a callback that reads the file bytes. It takes the base filename, reads the bytes, rejects files over the upload limit, and chooses a remote name. If another uploaded file already used the same base name from a different path, it prefixes a short hash of the path to avoid overwriting. It uploads the bytes, records the staged name, and returns the remote upload path.

**Call relations**: The browser engine calls this when a web page needs a file input filled. This method uses BrowserbaseApi.upload to send the bytes, and uses PurePosixPath and sha256 to choose a safe Browserbase-side filename.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name Browserbase-hosted Chrome accepts. It tells the browser to place downloads into Browserbase session storage rather than a local disk path.

**Data flow**: It takes no outside data beyond the lease. It returns the literal string used by Browserbase for downloads.

**Call relations**: The browser engine asks the lease where downloads should go before configuring Chrome download behavior. This method is intentionally simple because Browserbase rejects normal absolute paths.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed download from Browserbase for this leased session. It hides the Browserbase storage lookup behind the lease interface the rest of UFO expects.

**Data flow**: It receives a download GUID. It passes the current session ID and that GUID to BrowserbaseApi.download, then returns the downloaded bytes.

**Call relations**: The browser engine calls this after Chrome reports that a download is finished. This method hands the real polling, size check, and byte fetch to BrowserbaseApi.download.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser run by releasing the session, deleting its context, and removing the stored context ID. This is important for cost control and for preventing browser login state from surviving after the run.

**Data flow**: It reads the session ID, context ID, conversation ID, API wrapper, and store from the lease. It first tries to release the session. Whether or not that succeeds, it then tries to delete the context. Whether or not that succeeds, it deletes the stored context key for this conversation.

**Call relations**: The browser system calls this when the lease ends. It calls BrowserbaseApi.release_session, BrowserbaseApi.delete_context, and the ScopedStore delete operation in nested cleanup blocks so one failed cleanup step does not prevent the later ones.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts a new Browserbase-backed browser lease for the current browser turn. It creates the API wrapper, finds or creates the run's context, creates a Browserbase session, and returns a lease object to the browser engine.

**Data flow**: It receives an optional sandbox session. If no sandbox is provided, it raises an error because it needs the sandbox's conversation ID to identify this browser run. With the conversation ID, it creates a BrowserbaseApi and ScopedStore, gets the context ID through BrowserbaseCdpProvider._context, creates a session, and returns a BrowserbaseLease containing all needed IDs and the connection URL.

**Call relations**: Core calls this provider method when the configuration selects Browserbase as the CDP provider. It coordinates BrowserbaseCdpProvider._context, BrowserbaseApi.create_session, and BrowserbaseLease construction.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Gets the Browserbase Context ID for a conversation, creating and storing one if needed. This gives one browser run a stable saved-state container across session remakes.

**Data flow**: It receives the API wrapper, scoped store, and conversation ID. It builds a store key, checks whether a context ID is already stored there, and returns it if valid. If not, it creates a new context through BrowserbaseApi.create_context, stores the new ID, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It uses ScopedStore.get and ScopedStore.put so the context can be found again during the same conversation.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reconnects to an existing Browserbase session using a previously saved token. If the session is still live, it returns a new lease object over that same session.

**Data flow**: It receives a token string. It parses the conversation ID, session ID, and context ID from the token, creates a BrowserbaseApi, asks Browserbase whether the session is still live, and receives a fresh connect URL. It returns a BrowserbaseLease using those recovered values.

**Call relations**: The wider browser system calls this during recovery or replay when it has a saved lease token. It relies on _parse_token to validate the token and BrowserbaseApi.live_session to confirm Browserbase still has the session.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a saved Browserbase lease token back into its three IDs. It treats malformed tokens as gone sessions so recovery can fall back cleanly.

**Data flow**: It receives a slash-separated token string. It separates the conversation ID, session ID, and context ID, checks that the session and context parts are present, converts the conversation part into a UUID, and returns all three values. If parsing fails, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this before trying to reconnect. By raising SessionGone for bad tokens, it gives the caller the same signal used for expired Browserbase sessions.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO: its name, version, required Browserbase API key credential, and the CDP provider it offers. This is how the rest of the system discovers and builds the Browserbase provider.

**Data flow**: It takes no inputs. It constructs a Manifest containing one credential slot for the Browserbase API key and one CDP provider specification for the browserbase backend. The provider spec knows how to build a BrowserbaseCdpProvider from credentials.

**Call relations**: The extension loading system calls this when registering available extensions. It creates CredentialSlot, CdpProviderSpec, and Manifest objects so configuration can select this provider with the Browserbase backend name.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and download retrieval`

This provider solves a practical hosting problem: the browser must live inside the same sandbox as the conversation, so it can see the same files and write downloads there, but the main service still needs a way to talk to it. Chrome exposes a control socket called Chrome DevTools Protocol, or CDP, which is like a remote-control port for the browser. This file starts Chrome in the sandbox if it is not already answering, then starts a small in-sandbox proxy in front of it. The proxy is important because Chrome rejects DevTools requests whose Host header does not look local; the proxy rewrites that header while still carrying WebSocket traffic through. The provider waits for the whole chain to work before returning anything, so callers do not receive a half-ready browser. It also treats “port answers” as the real proof of readiness, not merely “a process exists,” because a stuck browser process can block future attempts. Chrome and the proxy are meant to persist with the sandbox across turns, so closing a lease does not shut them down. The file also knows how to read browser downloads back out of the sandbox, with size limits so an untrusted page cannot force huge data into the service.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 304–305)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already-prepared browser control endpoint for this lease. Callers use it to connect to the sandboxed Chrome through the proxy.

**Data flow**: It reads the saved endpoint object from the lease and returns it unchanged. Nothing is started, stopped, or rewritten here; the endpoint was built earlier when the lease was created.

**Call relations**: After SandboxChromeCdpProvider.lease has brought up Chrome and built the endpoint, the browser engine can ask this lease for the connection details when it is ready to attach.


##### `SandboxChromeCdpLease.token`  (lines 307–308)

```
async def token(self) -> str
```

**Purpose**: Returns a simple string that identifies this browser connection: the endpoint URL. It is a lightweight reattach handle, even though this provider does not actually reattach from it later.

**Data flow**: It reads the URL from the stored endpoint and returns that URL as text. It does not contact the sandbox or check whether the browser is still alive.

**Call relations**: This sits beside endpoint as part of the standard lease interface. If a caller asks for a token, it receives the URL that came from the live lease.


##### `SandboxChromeCdpLease.place_file`  (lines 310–314)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells the caller that no file copy is needed before Chrome can use a file. Because Chrome is running inside the same sandbox, it can open the sandbox path directly.

**Data flow**: It receives a file path and a file-reading callback. It ignores the callback, because there is no need to read bytes out and send them elsewhere, and returns the original path.

**Call relations**: When browser code wants to make a file available to Chrome, this lease answers with the same path instead of handing off to any transfer step.


##### `SandboxChromeCdpLease.download_dir`  (lines 316–319)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the folder inside the sandbox where Chrome downloads files. This lets other browser code tell Chrome where completed downloads should land.

**Data flow**: It returns the fixed sandbox download directory path. It does not create the directory here; the browser bring-up script creates it earlier.

**Call relations**: The download flow uses this path because the Chrome process writes to the sandbox filesystem, not to the host process filesystem.


##### `SandboxChromeCdpLease.fetch_download`  (lines 321–349)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed downloaded file out of the sandbox and returns its bytes to the caller. It protects the service by checking the file size before reading the whole thing.

**Data flow**: It receives a download guid, treats that as the filename inside the sandbox download directory, and safely quotes it for a shell command. It first asks the sandbox for the file size, rejects files above the configured maximum, then asks the sandbox to base64-encode the file. Finally it decodes that base64 text into bytes in a worker thread and returns the bytes. If the file cannot be read, is too large, or the read times out, it raises an error.

**Call relations**: This is used after Chrome has downloaded something into the sandbox. It calls shlex.quote to avoid unsafe shell text, uses the sandbox command runner to inspect and read the file, and uses asyncio.to_thread so the CPU work of decoding does not block the main async event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 351–352)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease without stopping Chrome. This is intentional because the browser and proxy are kept alive inside the conversation sandbox for reuse across turns.

**Data flow**: It receives the close request and returns without changing anything. No process is killed and no connection details are cleared.

**Call relations**: At the end of a turn, callers can close the lease through the normal interface, but this provider leaves the running sandbox browser available for the next lease.


##### `SandboxChromeCdpProvider.lease`  (lines 364–381)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a usable browser lease for the current turn's sandbox. It starts or verifies the in-sandbox Chrome and proxy, then returns the connection details the rest of the system needs.

**Data flow**: It receives a sandbox session. If none is provided, it fails because this provider cannot work without a sandbox. It runs the bring-up shell command inside the sandbox. If that command fails, it asks _bring_up_failure for a useful error message. If it succeeds, it asks the sandbox how to dial the proxy port, chooses ws or wss based on whether that dial target uses TLS, extracts the WebSocket path with _ws_path, builds a CdpEndpoint with the host and required headers, and wraps it in a SandboxChromeCdpLease.

**Call relations**: This is the main entry point for the provider during a turn. It relies on SandboxSession.bash to run setup inside the sandbox, SandboxSession.dial to learn the public route to the proxy, _bring_up_failure for clear diagnostics, _ws_path to convert Chrome's local URL into a proxy URL, and then hands back a SandboxChromeCdpLease for browser use.

*Call graph*: calls 4 internal fn (bash, dial, _bring_up_failure, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 383–384)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Rejects attempts to rebuild a lease from an old token alone. This provider needs the live sandbox for the current turn, so an endpoint URL by itself is not enough.

**Data flow**: It receives a token string and immediately raises SessionGone with that token. It does not try to contact Chrome or reconstruct an endpoint.

**Call relations**: When recovery code asks this provider to reattach from a saved token, this function tells the larger system that the old session should be treated as gone and a fresh lease should be made instead.

*Call graph*: 1 external calls (__init__).


##### `_bring_up_failure`  (lines 387–401)

```
async def _bring_up_failure(sandbox: SandboxSession, result: ExecResult) -> str
```

**Purpose**: Turns a failed browser bring-up into a helpful error message. It especially helps when the sandbox command was killed by a timeout and the useful browser logs would otherwise be lost.

**Data flow**: It receives the sandbox session and the failed command result. If the command itself returned an error before timing out, it returns the command's stderr or stdout. If the sandbox timeout killed the command, it runs a short tail command inside the sandbox to read the ends of the Chrome and proxy logs, then combines that with the timeout message.

**Call relations**: SandboxChromeCdpProvider.lease calls this only on bring-up failure. This helper uses SandboxSession.bash again to recover log text, so the final error explains what Chrome or the proxy said instead of only reporting a generic timeout.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 404–409)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part from Chrome's local WebSocket debugger URL. This lets the provider reuse the same path on the sandbox proxy's public host.

**Data flow**: It receives a URL string from Chrome, trims whitespace, and checks that it starts with one of the expected local Chrome prefixes. If it does, it removes that local prefix and returns the remaining path. If the URL points somewhere unexpected, it raises an error rather than building an unsafe or incorrect endpoint.

**Call relations**: SandboxChromeCdpProvider.lease calls this after the sandbox bring-up prints Chrome's debugger URL. The returned path is then attached to the dialed proxy host to form the endpoint given to the browser engine.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 412–421)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger system. It advertises the sandbox_chrome CDP provider and tells the system how to build it.

**Data flow**: It creates a Manifest containing the extension name, version, and a CdpProviderSpec for the sandbox_chrome backend. The spec's build function returns a new SandboxChromeCdpProvider when the system selects this backend.

**Call relations**: During extension loading, the system reads this manifest to discover that the sandbox_chrome provider exists. The manifest points later provider construction toward SandboxChromeCdpProvider.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/browser.py`

`data_model` · `startup and per-turn browser setup/teardown`

This file is a boundary, or “seam,” between core and whatever browser system is actually used. Core needs a way to say, “give me a Chrome I can connect to for this turn,” but it must not care whether that Chrome is running inside the task sandbox, on a remote browser service, or somewhere else. The connection is described as a CDP endpoint. CDP means Chrome DevTools Protocol, the control channel tools use to drive Chrome.

The main idea is a lease. Like borrowing a keycard for a room, a CdpLease gives one turn temporary access to a browser endpoint, plus a way to return or release it afterward. The lease can also answer practical questions that depend on where Chrome lives: how to make a file visible to Chrome, where Chrome should write downloads, and how to fetch downloaded bytes back.

CdpProvider is the factory for these leases. It is built once when the system starts, then asked for a fresh lease during a turn. It can also reattach to an existing browser session using a saved token. If that old session no longer exists, SessionGone tells the caller to start fresh instead of pretending the old page is still available.

The file contains contracts, not implementations. Browser extensions implement these contracts; core only relies on the promises written here.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome DevTools Protocol endpoint that a browser-driving engine should connect to. This includes the URL and any connection headers, such as authentication or sandbox routing information.

**Data flow**: The caller already has a lease for the current turn. It asks the lease for its endpoint; the lease resolves where Chrome is reachable and returns a CdpEndpoint containing the connection URL and headers. If a real implementation cannot provide an endpoint, it may raise an error instead of returning a bad address.

**Call relations**: A browser extension calls this when it is ready to connect to Chrome and drive the page. The concrete provider behind the lease supplies the details, while core stays out of the browser-engine work.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: Returns a saved handle that can be used later to reconnect to the same browser session if it is still alive. For a hosted browser this might be a session id; for a fixed endpoint it might simply be the URL.

**Data flow**: The caller asks the active lease for a durable text token. The lease turns its current browser session identity into a serializable string, which the caller can persist and pass back later to reattach.

**Call relations**: The browser extension uses this during a turn so recovery code can try to resume the same browser session later. That token is later handed to CdpProvider.reattach, which either returns a new lease over the old session or reports that it is gone.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Makes a workspace file usable by the leased Chrome and returns the path or location Chrome should open. This hides the difference between a browser that shares the sandbox filesystem and a remote browser that needs the file uploaded.

**Data flow**: The caller provides the file path and a small async reader that can produce the file bytes if needed. A sandbox-local implementation may simply return the same path because Chrome can already see it. A remote implementation may call the reader, send the bytes to the provider, and return the uploaded location.

**Call relations**: The browser-driving extension calls this before asking Chrome to open a local file. The lease implementation decides whether any copying is needed, based on where that Chrome actually runs.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: Tells the caller where Chrome should write downloads for this lease. The answer depends on whether Chrome writes into the sandbox filesystem or into storage owned by a remote provider.

**Data flow**: The caller asks the lease for a download directory. The lease returns a string location that can be passed to Chrome’s download settings. Later, completed downloads can be fetched by their Chrome download identifier.

**Call relations**: The browser extension uses this when configuring Chrome downloads for a turn. It pairs with CdpLease.fetch_download, which retrieves the bytes after Chrome finishes writing the file.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Fetches the bytes for a completed Chrome download, identified by Chrome’s download guid. A guid is a unique id Chrome assigns to the download.

**Data flow**: The caller passes the download guid. The lease looks in the correct place for this kind of browser: the sandbox filesystem for sandbox Chrome, or provider storage/API for a hosted browser. It returns the downloaded file as bytes.

**Call relations**: After the browser extension has configured downloads with CdpLease.download_dir and Chrome reports a completed download, the extension calls this to bring the file content back into the system.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: Releases the browser hold when the turn is finished. For some providers this does nothing; for a remote provider it may free a hosted browser session.

**Data flow**: The caller signals that it is done with the lease. The implementation performs whatever cleanup is appropriate, such as releasing a remote session or leaving a static sandbox endpoint alone. It returns no value.

**Call relations**: Turn cleanup calls this after browser work is complete. It is the matching “return the keycard” step for CdpProvider.lease or CdpProvider.reattach.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a fresh browser lease for one turn. It may use the turn’s sandbox session if the browser is supposed to run inside that sandbox.

**Data flow**: The caller may pass a SandboxSession, which represents the task’s isolated environment. The provider decides how to obtain Chrome: resolve a sandbox Chrome endpoint, allocate a remote hosted browser, or use a fixed endpoint. It returns a CdpLease that the turn can use and later close.

**Call relations**: Per-turn setup calls this when there is no existing browser token to resume from, or when resuming failed. The returned lease is then used by the browser extension to get an endpoint, place files, configure downloads, and clean up.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser session that was saved from an earlier run. If the session no longer exists, it raises SessionGone so the caller knows to create a new lease instead.

**Data flow**: The caller provides a saved token string. The provider looks up or resolves the browser session named by that token. If it is still alive, the provider returns a new CdpLease for it; if not, it raises SessionGone and returns no lease.

**Call relations**: Recovery or resume logic calls this before starting fresh. A successful result feeds the same browser-driving flow as CdpProvider.lease; a SessionGone result sends the caller back to minting a new lease.


### Delegated browsing adapters
Higher-level tools delegate browsing work to separate browser agents or hosted Browser Use sessions.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `tool invocation during request handling`

This file is the bridge between the main agent and a specialized browser subagent. Instead of giving the main agent direct control of a browser, it asks a child agent with the browser profile to do the web work and return a summary. That keeps browser automation isolated, gives each run a fresh session, and prevents a stuck website from blocking the parent forever.

The single-task path, `browser_task`, starts one browser child turn with a URL, task instructions, and a friendly task name. It waits only up to the allowed timeout. If the browser run takes too long, it cancels the child and returns an error message instead of leaving the caller hanging.

The batch path, `wide_browse`, reads a workspace file containing one URL or site name per line, removes blanks and duplicates, and starts a controlled number of browser child tasks at once. This is like sending several researchers to different websites, but limiting how many can work at the same time so the system is not overloaded. Each child gets a prompt made from a template, optionally with an output schema attached, and the collected results are written to `wide_browse.json`.

Both tools use deterministic deduplication keys, which means if a run is retried after a crash, the system can reconnect to already-started child work instead of accidentally launching duplicates.

#### Function details

##### `_browser_task`  (lines 98–127)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one full browser automation job by starting a browser subagent and waiting for its result. It is used when the main agent needs a fresh, isolated browser session for a multi-step website task.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, timeout, and user-facing description. It starts a browser-profile child turn with the URL and task details, then waits for that child to finish within the requested time limit. If time runs out or the child is cancelled, it returns an error-style tool result saying the browser task was cancelled; if the child finishes normally, it parses the child’s browser result and returns it as text.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the tool is called, it uses the context’s spawn mechanism to create the browser child turn, then waits through the subagent control interface. It hands the child’s final text to `BrowserResult` validation before returning a clean tool response to the parent agent.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 130–143)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses it to get the URLs or site names it should visit.

**Data flow**: It receives the tool context and a file path. It asks the sandbox shell to run `cat` on the safely quoted path, checks whether the read succeeded, then splits the file into lines. It trims spaces, skips blank lines, removes duplicates while preserving the first occurrence, and returns the resulting list of entities.

**Call relations**: This is a helper for `_wide_browse`. Before any browser child tasks are started, `_wide_browse` calls this function to turn the user-provided entities file into the work queue for the batch browser run.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 146–173)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs browser automation over many URLs or site names and collects all results into a JSON file. It is meant for batch extraction, where the same prompt should be applied to many targets.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing the entities file, prompt template, output schema file, and user-facing description. First it reads and deduplicates the entities, rejects the request if there are too many, and reads the optional JSON schema text. Then it launches browser child jobs for the entities with a semaphore, which is a limit that allows only a fixed number of jobs to run at once. After all visits finish, it writes the collected rows to `wide_browse.json` and returns a tool result containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It calls `_read_lines` to prepare the batch, creates one `visit` task per entity, waits for all of them together, and then uses the sandbox file writer to save the combined output for later use.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 154–167)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subagent for one entity inside a larger `wide_browse` batch. It builds the individual task prompt and returns one result row for that entity.

**Data flow**: It receives one entity such as a URL or company name from the surrounding `_wide_browse` function. It waits for a slot in the shared concurrency limit, fills `{entity}` into the prompt template, appends the output schema if one was provided, and spawns a browser-profile child turn. When that child returns, it produces a small dictionary containing the entity and the child’s serialized result text, or an empty string if there was no output.

**Call relations**: This nested helper is created and used only inside `_wide_browse`. `_wide_browse` schedules many `visit` calls at once with `asyncio.gather`, while the semaphore inside `visit` keeps the fan-out bounded so the batch does not start too many browser sessions at the same time.


### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `tool invocation / request handling`

This file is the bridge between UFO and Browser Use, an outside service that runs browser sessions in the cloud. Instead of this project opening a browser, clicking pages, and recovering from mistakes itself, it pays Browser Use to do the whole loop: choose actions, drive the browser, and return results.

There are two user-facing tools. `browser_task` runs one full browser job, such as starting from a URL, filling forms, or collecting information. `wide_browse` runs many smaller browser jobs in parallel, one per URL or site name listed in a workspace file, then writes a combined JSON result file.

The important safety boundary is that the Browser Use API key stays on the host side. It is read through scoped credentials and sent only to the Browser Use API. Output files from a completed run can be downloaded into the workspace, but the code checks that their paths cannot escape the workspace, limits how many files it fetches, limits file size, and downloads only from HTTPS links.

A run is controlled by money and time rather than by browser steps. The code starts a run, polls until it finishes, cancels it if the deadline expires, collects the result, and reports skipped files clearly instead of silently ignoring them.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one complete Browser Use job from start to finish. It checks that the task is not too large, sends it to the hosted service, waits for it to finish or time out, collects the final text result, and optionally brings output files back into the workspace.

**Data flow**: It receives the tool context, the task text, a time limit, and optionally a deduplication key used to reconnect to an already-started run. It reads the Browser Use API key from scoped credentials, creates an HTTP client, starts or resumes the remote run, watches its status, cancels it on timeout if needed, fetches the final summary, downloads allowed output files, and returns a `RunOutcome` describing the status, result text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the main engine used by the tool handlers. It calls `_start` to create or reattach to the remote run, `_watch` and `_status` to follow progress, `_json` to safely read API replies, and `_collect` to copy permitted output files into the workspace.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Starts a new Browser Use run, unless a previous run for the same deduplication key is already stored. This prevents paying for duplicate browser jobs after retries or crashes when possible.

**Data flow**: It receives an HTTP client, the extension store, the task text, and an optional deduplication key. If the key already has a saved run handle, it validates and returns that handle. Otherwise it posts the task, model, cost limit, and proxy country to Browser Use, extracts the returned run ID and workspace ID, stores them under the key if one was provided, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this near the beginning of every run. `_start` relies on `_json` to read the API response and `_text` to require important string fields like the run ID.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Keeps checking a remote run until Browser Use says it has reached a final state. It is the polling loop, like checking a delivery tracker until the package is delivered, cancelled, or failed.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current status; if the status is terminal, it returns it, otherwise it waits a few seconds and checks again.

**Call relations**: `HostedRun.execute` uses this while the run is inside its allowed time window. `_watch` delegates each actual status read to `_status` and only controls the wait-and-repeat behavior.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is used both during normal polling and right after a timeout to avoid cancelling a run that finished at the last moment.

**Data flow**: It receives an HTTP client and a run ID. It asks the Browser Use status endpoint for that run, parses the reply as JSON, extracts the required `status` string, and returns that string.

**Call relations**: `_watch` calls this over and over while waiting. `HostedRun.execute` also calls it directly in the timeout path before deciding whether to cancel the remote run.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies allowed output files from a Browser Use workspace into the local conversation workspace. It also records which files were skipped because they were too large or missing a usable download URL.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If file saving is turned off, it returns empty results. Otherwise it asks Browser Use for a limited file listing, checks every listed file has a path and size, verifies the path stays inside the workspace, downloads acceptable files, writes them into the sandbox, and returns saved files, skipped files, and a flag saying whether more files existed than were listed.

**Call relations**: `HostedRun.execute` calls this after the remote run has reached a final status. `_collect` uses `_json` to read the file listing, `_download` to fetch each allowed file, and the shared sandbox containment check to stop unsafe paths.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a presigned Browser Use file URL without sending the Browser Use API key. It also refuses non-HTTPS URLs so file downloads do not use plain, unsafe transport.

**Data flow**: It receives a URL string. It first checks that the URL starts with `https://`, then opens a separate HTTP client with no project credential header, downloads the bytes, raises a clear error if the download fails, and returns the file contents as bytes.

**Call relations**: `_collect` calls this only for files that are small enough and have a usable URL. It keeps storage downloads separate from the authenticated Browser Use API client.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a plain dictionary, or raises a project-specific error with useful details. This gives the rest of the run flow one consistent way to deal with bad API replies.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises `BrowserUseError` with the path, status code, and body. If the body is not valid JSON, or the JSON is not an object, it raises `BrowserUseError`. Otherwise it returns the object as a dictionary with string keys.

**Call relations**: This helper is used throughout the hosted run flow: starting runs, checking status, collecting files, and reading final summaries. It keeps response validation in one place so callers do not have to repeat those checks.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts one required text field from an API response dictionary. It turns missing or wrongly typed fields into a clear Browser Use error instead of letting a vague lookup error escape.

**Data flow**: It receives a response dictionary and the name of a required field. It looks up that field, checks that the value is a string, and returns it; otherwise it raises `BrowserUseError` showing the unreadable response.

**Call relations**: `_start` uses this to read the run ID and workspace ID from a creation response. `_status` uses it to read the status string from a status response.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 350–384)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the `browser_task` tool. It runs one detailed browser session starting from a URL and returns the Browser Use result plus information about any output files copied into the workspace.

**Data flow**: It receives the tool context and validated `BrowserTaskInput` fields: starting URL, task instructions, task name, timeout, and user-facing description. It builds one self-contained task prompt, creates a `HostedRun` configured for the more capable task model and higher cost limit, waits for the run, and returns a `ToolResult`. On timeout, the result is marked as an error; otherwise it returns JSON containing the result text, saved file paths, skipped files, and whether more files exist.

**Call relations**: This function is registered as the handler for the `browser_task` tool in `BROWSER_USE_TOOLS`. It is the user-facing doorway into `HostedRun.execute` for one-off browser automation.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 387–394)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a file from the sandbox workspace by running `cat` safely through the sandbox shell. It quotes the path so a model-supplied filename cannot accidentally become a shell command.

**Data flow**: It receives the tool context and a path string. It shell-quotes the path, runs `cat` in the sandbox, raises a `ValueError` if the command fails, and returns the file contents as text if it succeeds.

**Call relations**: `_read_lines` uses this to read the entity list for batch browsing. `_wide_browse` uses it directly to read the JSON schema file that should shape each browser result.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 397–405)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace file as a clean list of unique non-empty lines. It is used to turn an input file of URLs or site names into the batch of things `wide_browse` should visit.

**Data flow**: It receives the tool context and a path. It reads the file through `_read_file`, splits it into lines, trims whitespace, skips blank lines, removes duplicates while keeping the first occurrence order, and returns the resulting list of entities.

**Call relations**: `_wide_browse` calls this at the start of a batch run to decide which entities need browser visits.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 408–454)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the `wide_browse` tool. It reads a list of sites or URLs, runs many Browser Use jobs in parallel with a shared prompt template, writes all rows to `wide_browse.json`, and returns the same rows to the caller.

**Data flow**: It receives the tool context and validated `WideBrowseInput`: an entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates entities, enforces the maximum batch size, reads the schema text, creates a semaphore to limit how many browser jobs run at once, launches one `visit` task per entity, gathers successes and per-entity failures, writes the combined rows as JSON into the sandbox, and returns a `ToolResult` containing the rows and output filename.

**Call relations**: This function is registered as the handler for the `wide_browse` tool. It calls `_read_lines` and `_read_file` for workspace inputs, creates a shared `HostedRun` runner, and uses its inner `visit` helper for each entity in the batch.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 424–439)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser job for one entity inside a `wide_browse` batch. It makes one failed website or vendor hiccup become one failed row instead of throwing away the whole batch.

**Data flow**: It receives a single entity string from the surrounding `_wide_browse` function. While holding the concurrency semaphore, it replaces `{entity}` in the prompt template, appends the output schema if present, runs the hosted browser job with a per-entity deduplication key when available, and returns a row containing the entity, final status, and result text.

**Call relations**: `_wide_browse` creates this helper and schedules it once per entity with `asyncio.gather`. The helper uses the shared `HostedRun` runner prepared by `_wide_browse`, so all rows follow the same model, cost, and timeout policy.


##### `manifest`  (lines 477–492)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It names the extension, lists the tools it provides, supplies prompt text, and declares the Browser Use API key credential it needs.

**Data flow**: It takes no input. It builds and returns a `Manifest` containing the extension name and version, the two tool definitions, the browser prompt section read from the companion markdown file, and a credential slot telling the host to provide a Browser Use API key.

**Call relations**: The host calls this when loading the extension. The returned manifest is how `browser_task` and `wide_browse` become available and how the runtime knows this extension requires the `browser_use_api_key` credential.

*Call graph*: 3 external calls (__init__, __init__, __init__).

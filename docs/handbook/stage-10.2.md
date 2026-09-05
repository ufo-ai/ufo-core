# Browser automation backends  `stage-10.2`

This stage is the browser “engine room” for a turn of work. It gives agents a safe way to borrow a browser, understand what is on the page, act on it, and then clean everything up afterward.

First, the hosted and sandbox providers supply the browser itself. They may lease a remote Browserbase or Browser Use session, or start a private sandbox Chrome. The backend opens the control connection only when a tool needs it and releases it when the turn ends.

Once connected, the CDP transport layer is the control cable to Chrome. CDP, Chrome DevTools Protocol, is Chrome’s remote-control language. It sends commands, checks messages, runs small page scripts, and reports risky failures carefully.

Page modeling then turns the live page into useful text, controls, and screen positions. Action handling converts tool requests into clicks, typing, scrolling, screenshots, and waits. State helpers manage tabs, forms, pop-up dialogs, and downloads.

The tools.py file exposes all of this as agent tools, while backend.py ties each tool call to the current leased browser session.

## Sub-stages

- [CDP transport and runtime safety](stage-10.2.1.md) `stage-10.2.1` — 4 files
- [Page content modeling and element discovery](stage-10.2.2.md) `stage-10.2.2` — 4 files
- [Browser action execution and input handling](stage-10.2.3.md) `stage-10.2.3` — 5 files
- [Browser page state, tabs, forms, dialogs, and downloads](stage-10.2.4.md) `stage-10.2.4` — 5 files
- [Hosted and sandbox browser providers](stage-10.2.5.md) `stage-10.2.5` — 3 files

## Files in this stage

### Browser tool orchestration
Defines the agent-facing browser tools and routes each tool invocation into the per-turn browser backend.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `during each agent turn when browser tools are invoked`

This file is the agent’s public control panel for browser use. Without it, the agent might have a browser engine available, but it would not have clear, validated actions like “navigate,” “find an element,” or “wait for a download.” Each tool has a small input shape, defined with Pydantic models, so invalid or missing arguments are caught before they reach the browser.

The central idea is that all browser actions in one turn share one BuaSurface, which is the object that actually talks to Chrome through CDP, the Chrome DevTools Protocol. CDP is the remote-control channel Chrome exposes for automation. The helper _browser creates this surface only when the first browser tool is used, then caches it for the rest of the turn. It also registers cleanup so the browser connection and any hosted browser lease are released at the end.

Most tool functions are thin translators: they turn typed input into a plain JSON-like dictionary, call the matching BuaSurface method, and wrap the answer as text. A few need extra care. The computer tool can return a screenshot as an image and can save it into the workspace. It also reports partial progress if a batch of clicks or keystrokes stops halfway. The download tool waits for bytes from the browser, decodes them, and writes the file into the shared workspace.

#### Function details

##### `_browser`  (lines 96–119)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser surface used for the current turn. It creates the browser connection on first use, then reuses it so multiple tool calls in the same turn share the same browser session.

**Data flow**: It receives the current ToolContext, which contains things like the selected CDP provider, sandbox, agent model, cleanup registry, and extension store. If a BuaSurface is already cached for this turn’s cleanup registry, it returns that. If not, it builds a new BuaSurface, stores it in the weak cache, registers its close method for end-of-turn cleanup, and returns it.

**Call relations**: All browser tool handlers call this before doing real browser work. When it needs to build a surface, it calls BuaSurface.__init__; after that, functions like _navigate, _find, _computer, and _wait_for_download use the returned surface to perform their specific action.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 122–123)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a browser reply into the standard tool-result format as JSON text. This gives every simple browser tool a consistent way to send structured information back to the agent.

**Data flow**: It receives a dictionary-like reply from the browser surface. It converts that reply to a JSON string, puts it inside a TextContent object, then returns a ToolResult containing that text.

**Call relations**: Most tool handlers call this after their BuaSurface call succeeds. It is the common final packaging step for actions such as navigation, reading a page, finding elements, closing tabs, and saving downloads.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 126–129)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a value from a browser reply is a non-empty string. It is used when the next step depends on text being present, such as base64-encoded file or screenshot data.

**Data flow**: It receives a value and the name of the field that value came from. If the value is a non-empty string, it returns it unchanged. If not, it raises a ValueError explaining which browser reply field is missing.

**Call relations**: _computer uses it before decoding a screenshot for workspace saving. _wait_for_download uses it before decoding the downloaded file content and before choosing the downloaded filename.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 132–134)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Moves the browser to a requested URL, or asks the browser surface to perform a navigation-style action using the provided tab information. It is the implementation behind the agent’s navigate tool.

**Data flow**: It receives the tool context and a NavigateInput containing a URL and possibly a tab id. It converts the input model into a JSON-friendly dictionary, sends it to the browser surface’s navigate method, then wraps the browser’s reply as JSON text.

**Call relations**: When the navigate tool is invoked, this function first gets the shared turn browser through _browser. It then hands the actual navigation work to the BuaSurface and uses _json_result to return the answer.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 137–138)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Asks the browser for information about all open tabs. This helps the agent understand what pages are available before choosing where to act.

**Data flow**: It receives the tool context and an empty TabsContextInput. It calls the browser surface with an empty request, receives the current tab context, and returns it as JSON text.

**Call relations**: When the tabs_context tool is invoked, this function gets the shared browser surface through _browser. It delegates the tab inspection to BuaSurface.tabs_context and then packages the response with _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 141–155)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally starting at a requested URL. It also gives a careful failure message if the browser reports that a new blank tab was already opened but left for the agent to deal with.

**Data flow**: It receives the context and an optional URL. It asks the browser surface to create a tab, using about:blank if no URL was supplied. On success, it returns the tab information as JSON text. If a TabLeftOpen error is raised, it returns a ToolFailure that says the tab really is open and records that applied effect.

**Call relations**: The tabs_create tool reaches this function when the agent wants another tab. It calls _browser to access the shared surface, then BuaSurface.tabs_create. If tab creation succeeds it uses _json_result; if the browser left a tab open while reporting an interruption, it builds a ToolFailure with an AppliedEffect so the agent does not unknowingly create extra tabs.

*Call graph*: calls 2 internal fn (_browser, _json_result); 2 external calls (__init__, __init__).


##### `_tabs_close`  (lines 158–160)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, either the specified one or whatever default the browser surface chooses. It is the implementation behind the tab-closing tool.

**Data flow**: It receives the context and a TabsCloseInput that may contain a tab id. It converts the input into a JSON-friendly dictionary without empty fields, sends it to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: When the tabs_close tool is invoked, this function obtains the shared BuaSurface through _browser. It delegates the real close operation to that surface and uses _json_result for the response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 163–165)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Connects files from the workspace to a file input on the web page. This lets the agent complete upload forms using files it can access by path.

**Data flow**: It receives a browser reference for the file input, one or more workspace file paths, and optionally a tab id. It turns those inputs into a JSON-friendly dictionary, sends them to the browser surface, and returns the result as JSON text.

**Call relations**: The upload_file tool calls this when the agent needs to set an HTML file picker. This function gets the shared surface with _browser, passes the request to BuaSurface.upload_file, then wraps the reply through _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 168–170)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page in a structured way, using the accessibility tree. The accessibility tree is the browser’s simplified description of page elements, meant for assistive technology and useful for automation.

**Data flow**: It receives options such as how deep to read, which parts to filter for, a reference id, and possibly a tab id. It converts those options into a JSON-friendly dictionary, asks the browser surface to read the page, and returns the structured reply as JSON text.

**Call relations**: When the read_page tool is invoked, this function uses _browser to reach the shared BuaSurface. It delegates the page-reading work to BuaSurface.read_page and returns the answer with _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 173–175)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This is useful when the agent needs the page’s words without the richer structure of the accessibility tree.

**Data flow**: It receives the context and optionally a tab id. It converts the input to a JSON-friendly dictionary, asks the browser surface for page text, and returns that text response as JSON.

**Call relations**: The get_page_text tool reaches this function. It obtains the turn’s shared browser surface via _browser, calls BuaSurface.get_page_text, and sends the result back through _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 178–180)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the browser page for elements matching a query, such as text, role, name, or URL. It helps the agent turn a human goal like “click the Sign in button” into a concrete browser reference.

**Data flow**: It receives a search query and optionally a tab id. It converts that input into a JSON-friendly dictionary, passes it to the browser surface’s find method, and returns the matched results as JSON text.

**Call relations**: When the find tool is invoked, this function gets the shared BuaSurface through _browser. The surface may use the host-side ranking hook supplied earlier by the context, and this function packages the final matches with _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 183–185)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. This lets the agent fill text boxes, dropdowns, or other form controls after it has found them.

**Data flow**: It receives a browser reference, a value, and optionally a tab id. It turns those fields into a JSON-friendly dictionary, sends them to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: The form_input tool calls this after the agent decides what field to change. This function gets the shared surface with _browser, hands the work to BuaSurface.form_input, and wraps the reply with _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 188–229)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs a batch of low-level browser actions such as mouse clicks, typing, scrolling, waiting, and screenshots. It is the tool used when the agent needs direct “computer use” instead of a higher-level browser command.

**Data flow**: It receives a list of action dictionaries, optional tab information, and optional screenshot-saving settings. It sends the action batch to the browser surface. If the batch is interrupted, it returns a failure that clearly says which earlier actions already happened. On success, it may decode a screenshot and write it into the sandbox workspace, and it may also return the screenshot as image content along with JSON text.

**Call relations**: The computer tool invokes this function for direct interaction. It first calls _browser to use the turn’s shared BuaSurface. If BuaSurface.computer raises BatchInterrupted, this function builds a ToolFailure with AppliedEffect entries so the agent knows not to repeat actions that already reached the page. For normal replies it uses _required_str when saving screenshots, ImageContent for visible screenshot output, and _json_result when there is no image to attach.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 8 external calls (__init__, __init__, __init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 232–240)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download, writes the downloaded bytes into the shared workspace, and returns the saved file path. This makes browser downloads available to the parent agent and sibling subagents by normal workspace path.

**Data flow**: It receives an optional download id, target path, and timeout. It asks the browser surface to wait for the download, checks that the reply includes a filename and base64-encoded content, decodes the bytes, writes them to the sandbox under the chosen downloads directory, and returns JSON with the file path, filename, and size.

**Call relations**: The wait_for_download tool reaches this function after a page starts or is expected to start a download. It uses _browser to access BuaSurface.wait_for_download, _required_str to validate the needed fields, base64 decoding to turn text back into bytes, the sandbox to write the file, and _json_result to report where it was saved.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per browser-using turn, from first browser tool call through turn cleanup`

The main class, BuaSurface, is the bridge between high-level browser tools and the lower-level BrowserSession that talks to Chrome. A useful way to picture it is as a hotel front desk for a temporary browser room: it gets a room key when the guest first asks for one, reuses that room during the visit, and returns the key at checkout.

This matters because browser access can be local or remote, and remote browser sessions may cost resources. A turn that never uses the browser should not start Chrome at all. A turn that does use it must clean up both the browser connection and the lease that owns the session.

The file also protects recovery after crashes. It stores a durable session token for the current conversation. If the process crashes before cleanup, the next run can reattach to the same live browser instead of opening a fresh page. If that old session is gone, it clears the token and starts over.

File transfer is another important job here. Uploads are first checked as workspace paths, then passed through the browser lease so the file ends up somewhere the actual Chrome instance can read. Downloads come back through the lease too, then are returned as base64 text with a safe filename. This keeps local, sandboxed, and remote browsers using one consistent path.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or returns the active browser session for this turn. It is the lazy startup point, so Chrome is contacted only when a browser tool is actually used.

**Data flow**: It starts with the BuaSurface state. If a BrowserSession already exists, it returns it. If not, it gets or creates a CDP lease, asks that lease for the browser endpoint and download directory, builds a BrowserSession, opens it, stores it, and returns the ready session.

**Call relations**: All the user-facing browser actions call this first, including navigation, reading, finding, forms, computer control, tab work, uploads, and downloads. When no lease exists yet, it hands off to BuaSurface._acquire_lease, then creates the BrowserSession that later methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets the right browser lease for this turn, either by reattaching to a still-live saved session or by creating a new one. This is what makes crash recovery possible without unnecessarily reopening the browser.

**Data flow**: It reads a stored token, if one exists. If there is a token, it asks the CDP provider to reattach using it. If that session is gone, it clears the token. Then it asks the provider for a fresh lease, stores that lease's new token, and returns the lease.

**Call relations**: BuaSurface._open calls this when it needs a lease before opening a BrowserSession. This function uses BuaSurface._stored_token and BuaSurface._store_token to coordinate with the durable store.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser session token for this conversation, if token storage is available. The token is the small durable handle used to reconnect after a crash.

**Data flow**: It checks whether both a scoped store and conversation id are present. If either is missing, it returns nothing. Otherwise it reads the conversation-specific token key and returns the value only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before deciding whether to reattach to an old session or mint a new lease.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser session token for this conversation. Saving supports crash recovery; clearing prevents later turns from reconnecting to a session that has already been released.

**Data flow**: It receives either a token string or null. If storage or the conversation id is missing, it does nothing. Otherwise it writes that value under the conversation-specific token key.

**Call relations**: BuaSurface._acquire_lease uses it to save a fresh lease token or clear a dead one. BuaSurface.aclose uses it during cleanup to remove the token after the lease is released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates the browser to a requested web address, optionally in a specific tab. It validates that the address is actually text before passing it on.

**Data flow**: It receives a dictionary of tool arguments, opens the browser session, reads the url field, and checks that it is a string. It converts the optional tab id into an integer when possible, then asks the session to navigate and returns the session's result.

**Call relations**: This is one of the browser tool entry methods. It relies on BuaSurface._open for the active session and _tab_id to normalize the tab identifier before handing the request to BrowserSession.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs. A caller uses it to understand what pages are open and which tab is active.

**Data flow**: It receives tool arguments, opens the browser session, asks the session for tab context, and returns that information unchanged.

**Call relations**: This method is called as a tab-related browser tool. Its only setup step is BuaSurface._open; the actual tab inspection is delegated to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no useful URL is supplied, it opens a blank page instead of failing.

**Data flow**: It receives tool arguments, opens the browser session, looks for a non-empty url string, and falls back to about:blank when needed. It asks the session to create the tab and returns the result.

**Call relations**: This is part of the browser tab tool flow. It uses BuaSurface._open for session setup, then hands the tab creation request to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab according to the supplied arguments. This lets a tool remove tabs that are no longer needed.

**Data flow**: It receives the tab-close arguments, opens the browser session, passes the arguments to the session's close operation, and returns the result.

**Call relations**: This method is a thin tool-facing wrapper. It first ensures the browser is open through BuaSurface._open, then lets BrowserSession do the actual tab closing.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a file input on a web page. It makes uploads work the same way whether Chrome is running in the local sandbox or in a remote hosted environment.

**Data flow**: It receives tool arguments and expects a files list. For each file path, it verifies that the value is a non-empty string, resolves it as a workspace path, and asks the current lease to place the file where Chrome can open it. For remote browsers, the lease may call back into BuaSurface._read to get the bytes. It then asks the browser session to attach the placed files, waits for the upload to settle when needed, and returns the session's reply.

**Call relations**: This method is called by the upload browser tool. It uses BuaSurface._open to get a session, BuaSurface._lease to reach the active transport lease, workspace_path to keep paths inside the workspace, and BuaSurface._settle_upload to catch remote-upload timing problems.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until the page really sees the uploaded file bytes, not just the filename. This avoids a subtle failure where a remote upload returns before the file has fully arrived.

**Data flow**: It receives the browser session and the upload arguments. If no file bytes were shipped by this surface, it returns immediately. Otherwise it compares the page's attached file sizes with the sizes that were sent. If they do not match, it waits briefly and reattaches the files, repeating for a fixed number of attempts. If the sizes still do not match, it raises an error.

**Call relations**: BuaSurface.upload_file calls this after the first attach. It asks BrowserSession for attached_sizes and may call BrowserSession.upload_file again after asyncio.sleep delays until the page reports the expected sizes.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active CDP lease and fails clearly if none exists. It is a small safety check for operations that must go through the browser transport.

**Data flow**: It reads the BuaSurface lease field. If a lease is present, it returns it. If not, it raises a runtime error saying the browser has no CDP lease.

**Call relations**: BuaSurface.upload_file and BuaSurface.wait_for_download call this after opening the browser, because file placement and download retrieval must go through the lease.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file from the sandbox so it can be shipped to a remote browser for upload. It enforces a size limit so a large file does not flood memory.

**Data flow**: It receives a resolved path. It requires a sandbox, quotes the path safely for shell use, asks the sandbox for the file size, rejects unreadable or too-large files, then runs base64 encoding in the sandbox. It decodes the base64 output back into bytes in a worker thread, records how many bytes were shipped, and returns the bytes.

**Call relations**: This function is not called directly by the tool method. BuaSurface.upload_file passes it as a callback to the lease's file-placement step, so the transport can request bytes only when it needs to send a local file to a remote Chrome.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads the current page in the structured way provided by the browser session. A caller uses it to inspect what is on the page.

**Data flow**: It receives read arguments, opens the browser session, passes the arguments to the session's read_page operation, and returns the result.

**Call relations**: This is a browser tool wrapper. It depends on BuaSurface._open for session setup and delegates the page-reading details to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets text from the current page. This is useful when the caller needs the page's readable words rather than lower-level browser details.

**Data flow**: It receives arguments, opens the browser session, forwards the arguments to the session's get_page_text operation, and returns the resulting text-oriented data.

**Call relations**: The page-text tool calls this method. It uses BuaSurface._open, then leaves the actual browser inspection to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, with optional help from a host-side find completer. The completer can improve or finish search-related work outside the raw browser session.

**Data flow**: It receives find arguments, opens the browser session, and passes both the arguments and the optional find_completer to the session. It returns whatever match or search result the session produces.

**Call relations**: This method is called by the find browser tool. It uses BuaSurface._open for the connection and then delegates to BrowserSession while supplying the completer owned by the surface.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on the web page. It is the tool-facing path for typing into inputs and similar form actions.

**Data flow**: It receives form-input arguments, opens the browser session, forwards the arguments to the session's form_input operation, and returns the result.

**Call relations**: The form input tool calls this method. It only prepares the active session through BuaSurface._open; BrowserSession performs the browser-side action.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as interactions that are closer to direct UI control. It gives the tool layer access to BrowserSession's computer operation.

**Data flow**: It receives action arguments, opens the browser session, passes the arguments through to the session's computer method, and returns the result.

**Call relations**: This browser control path is another wrapper around BrowserSession. Like the other tool methods, it first calls BuaSurface._open so the connection exists before the action is sent.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns the downloaded bytes as base64 text with a safe filename. This makes downloaded files available to the caller even when the browser is remote.

**Data flow**: It receives wait arguments, opens the browser session, and waits for the session to report a finished download. It asks the active lease to fetch the downloaded bytes by download id, base64-encodes those bytes in a worker thread, trims the filename down to a safe single path segment, and returns the filename, encoded content, and size.

**Call relations**: The download-waiting browser tool calls this. It uses BuaSurface._open to hear from the browser, BuaSurface._lease to retrieve the actual bytes from the transport, and contained_leaf to prevent unsafe path names before returning the data.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved reconnect token. This is the normal end-of-turn cleanup path.

**Data flow**: It checks whether a session exists and closes it, then always tries to close the lease even if session closing fails. Afterward it clears the stored token and resets the in-memory session and lease fields to none.

**Call relations**: Turn cleanup calls this after browser work is done. It uses BuaSurface._store_token to remove the durable token, which prevents a later turn from reattaching to a session that has already been released.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab id value from tool arguments into an integer tab id when possible. It treats missing, false-like, or inappropriate values as no tab selection.

**Data flow**: It receives a JSON-style value. Booleans become no tab id, integers are returned as-is, floats and non-empty strings are converted to integers, and all other values return none.

**Call relations**: BuaSurface.navigate calls this before sending a navigation request to BrowserSession, so the session receives either a clean integer tab id or no tab id at all.

*Call graph*: called by 1 (navigate).

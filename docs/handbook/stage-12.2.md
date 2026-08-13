# Browser Session Control  `stage-12.2`

Browser Session Control is the system’s browser workshop. It is used during the main work loop whenever an agent needs to open a site, inspect it, click, type, download a file, or clean up afterward. It can use different browser sources: a Chrome running inside the sandbox, a remote Browserbase Chrome, the Browser Use service, or the project’s own automation stack.

The provider integrations decide where the browser comes from, like choosing which car to drive. The CDP layer, named for Chrome DevTools Protocol, is the control wire that sends commands to Chrome and listens for events. It manages tabs, page loading, dialogs, downloads, and safe JavaScript execution. The action layer turns planned actions into real mouse, keyboard, scroll, form, and upload events. The extraction layer reads the page and finds the exact element the agent referred to.

tools.py exposes browser abilities as agent tools. backend.py connects each turn’s tool calls to a real Chrome session and handles recovery and file transfer. session.py holds the live session state, including tabs, dialogs, downloads, and page readiness.

## Sub-stages

- [Browser Provider Integrations](stage-12.2.1.md) `stage-12.2.1` — 3 files
- [CDP Connection, Runtime, Tabs, and Page Lifecycle](stage-12.2.2.md) `stage-12.2.2` — 6 files
- [Browser Action Execution and Input Translation](stage-12.2.3.md) `stage-12.2.3` — 5 files
- [Page Content Extraction and Element Lookup](stage-12.2.4.md) `stage-12.2.4` — 4 files

## Files in this stage

### Browser Tool Execution
Agent-facing browser tools are routed through the per-turn backend into a live Chrome session that performs page actions, screenshots, downloads, dialog handling, and cleanup.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `tool call handling during an agent turn`

This file turns browser actions into safe, well-shaped tools. An agent does not talk to Chrome directly. Instead, it calls named tools like `navigate`, `find`, or `computer`, and this file checks the inputs, passes the request to a shared browser surface, then formats the answer back into a tool result.

The important shared part is the `BuaSurface`, which is the browser-use engine for the current turn. Think of it like checking out one browser remote control at the start of a task and returning it when the task ends. The file creates that remote control only when the first browser tool is used, reuses it for later browser calls in the same turn, and registers cleanup so the browser connection and any hosted browser lease are released even if the turn ends unexpectedly.

Most handlers follow the same pattern: remove the human-only `user_description`, send the remaining data to `BuaSurface`, and return the reply as JSON text. Two tools do extra file work. `computer` can attach a screenshot image to the result and optionally save it into the shared workspace. `wait_for_download` waits for downloaded bytes from the browser and writes them into the workspace, so other agents or later steps can refer to the saved path.

#### Function details

##### `_browser`  (lines 105–128)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser surface used for the current agent turn. It creates it on first use, reuses it for later browser tools in the same turn, and makes sure it will be closed during cleanup.

**Data flow**: It receives the current tool context, which contains things like the selected browser connection provider, the sandbox, the agent model, and cleanup registry. If a browser surface is already cached for this turn, it returns that. If not, it builds a new `BuaSurface`, stores it in a weak cache keyed by the cleanup object, registers its close method for later cleanup, and returns it. If there is no browser connection provider, it raises an error instead of trying to run without a browser.

**Call relations**: All browser tool handlers call this before doing real browser work. It is the shared doorway to `BuaSurface.__init__`, so navigation, reading, finding, form entry, screenshots, uploads, downloads, and tab operations all reuse the same browser connection within the turn.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 131–132)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain dictionary reply as a tool result containing JSON text. This gives the agent a consistent text response format for most browser tools.

**Data flow**: It receives a dictionary of JSON-safe values. It converts that dictionary into a JSON string, places the string inside a text content object, and returns a tool result containing that text. It does not change the original browser state or write files.

**Call relations**: Most tool handlers use this after receiving a reply from the browser surface. It is the final packaging step for navigation, tab operations, page reading, finding, form input, uploads, downloads, and cases where `computer` does not need to attach an image separately.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 135–138)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a value from a browser reply is a non-empty string. It is used when later code must decode or save that value and cannot safely continue if it is missing.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns that string. If the value is missing, empty, or not a string, it raises a clear error naming the missing field.

**Call relations**: `_computer` uses this before decoding a screenshot for workspace storage. `_wait_for_download` uses it before saving a downloaded filename and file contents. In both flows it acts like a safety checkpoint before touching the sandbox file system.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 141–145)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool, such as opening a URL or moving through browser history. It lets the agent change what page a tab is showing.

**Data flow**: It receives the tool context and validated navigation input. It converts the input to a JSON-friendly dictionary, leaving out empty values and the `user_description` field that is only for human timeline context. It sends the remaining navigation request to the current browser surface, then returns the browser reply as JSON text.

**Call relations**: This is the handler registered for the `navigate` tool. When the agent asks to navigate, this function gets the shared browser surface through `_browser`, hands off the browser action, and uses `_json_result` to package the answer.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 148–149)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns context about the browser tabs that are currently open. It helps the agent understand what pages exist before choosing where to act.

**Data flow**: It receives the tool context and the input model, though the current browser request does not need extra fields. It asks the shared browser surface for tab context using an empty request, then wraps the reply as JSON text.

**Call relations**: This is the handler for the `tabs_context` tool. It gets the shared browser surface through `_browser` and then immediately formats the returned tab information with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 152–154)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally starting it at a given URL. If no URL is provided, it opens a blank page.

**Data flow**: It receives the tool context and tab creation input. It builds a small request containing the requested URL, or `about:blank` as the default. It sends that to the browser surface and returns the result as JSON text.

**Call relations**: This is the handler registered for the `tabs_create` tool. It asks `_browser` for the turn’s browser surface, delegates tab creation to it, and passes the reply through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 157–161)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, either the current one or a specific tab when an id is supplied. It lets the agent clean up pages it no longer needs.

**Data flow**: It receives the tool context and close-tab input. It converts the input into a JSON-friendly request, skipping empty values and removing `user_description`. It sends that request to the browser surface, then returns the browser’s reply as JSON text.

**Call relations**: This is the handler for the `tabs_close` tool. Like the other tab tools, it uses `_browser` to reach the shared browser surface and `_json_result` to produce the final tool response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 164–168)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file upload field on a web page using files from the shared workspace. It lets the agent upload documents without needing to manually operate the operating system file picker.

**Data flow**: It receives the tool context plus an upload target reference and one or more workspace file paths. It removes the human-only description and sends the upload details to the browser surface. The browser surface performs the page-side upload action, and the reply comes back as JSON text.

**Call relations**: This is the handler for the `upload_file` tool. It depends on `_browser` for browser access and `_json_result` for output formatting, while the actual upload behavior is delegated to the browser surface.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 171–175)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads a structured view of the current web page, often based on the page’s accessibility tree, which is a browser-provided outline of visible and interactive content. This helps the agent understand what can be clicked, typed into, or read.

**Data flow**: It receives the tool context and options such as depth, filter, page reference, or tab id. It converts those options into a JSON-friendly request, leaving out empty values and `user_description`. It sends the request to the browser surface and returns the structured page information as JSON text.

**Call relations**: This is the handler for the `read_page` tool. It uses `_browser` to reach the active browser surface, lets that surface inspect the page, and then uses `_json_result` to deliver the page snapshot back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 178–182)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. It is useful when the agent needs the words on a page more than a structured list of controls.

**Data flow**: It receives the tool context and optional tab id. It turns the input into a JSON-friendly request without empty values or `user_description`, asks the browser surface for page text, and returns the result as JSON text.

**Call relations**: This is the handler for the `get_page_text` tool. It follows the standard flow of getting the shared browser surface with `_browser`, delegating the browser inspection, and packaging the response with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 185–189)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the current page for elements that match a query, such as text, names, roles, or URLs. It helps the agent locate the right button, link, field, or other page item before acting.

**Data flow**: It receives the tool context and a search query, plus optional tab information. It converts the input into a JSON-friendly request, excluding empty values and `user_description`. It sends that request to the browser surface, which performs the search, then returns the matches as JSON text.

**Call relations**: This is the handler for the `find` tool. It relies on `_browser` to access the browser surface, which may use the context’s finding/ranking hook, and then `_json_result` to send the findings back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 192–196)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field or similar page control identified by a browser reference. It lets the agent fill in web forms in a targeted way.

**Data flow**: It receives the tool context, a page reference, and the value to enter. It removes the human-facing `user_description`, keeps the actual form input details, and sends them to the browser surface. The browser surface changes the page field and returns a reply, which is wrapped as JSON text.

**Call relations**: This is the handler for the `form_input` tool. It gets browser access from `_browser`, delegates the actual page edit to the browser surface, and formats the result through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 199–217)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs lower-level browser interactions such as mouse, keyboard, scroll, wait, and screenshot actions. It can also return a screenshot image and optionally save that screenshot into the shared workspace.

**Data flow**: It receives the tool context and a list of computer-style actions. It sends those actions to the browser surface after removing `user_description`. The browser may reply with normal JSON fields and a base64-encoded screenshot, which is image data written as text. If `save_to_workspace` is true, the function checks that the screenshot exists, decodes it into bytes, writes it to the sandbox at the requested path or a default screenshot path, and adds that path to the reply. If a screenshot is present, it returns both JSON text and an image content item; otherwise it returns only JSON text.

**Call relations**: This is the handler for the `computer` tool. It starts by using `_browser` to perform the actions. It uses `_required_str` when a screenshot must be saved, and it uses `_json_result` only for replies that do not need a separate image attachment.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 220–228)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves the downloaded file into the shared workspace. This turns a browser-only download into a normal workspace file path that later steps can use.

**Data flow**: It receives the tool context plus optional download id, target path, and timeout. It asks the browser surface to wait for the download and return its filename and base64-encoded contents. It checks that both required strings are present, decodes the file bytes, writes them into the sandbox under the requested folder or the default downloads folder, and returns JSON containing the saved path, filename, and reported size.

**Call relations**: This is the handler for the `wait_for_download` tool. It uses `_browser` to receive the completed download, `_required_str` to make sure the needed file data is present, and `_json_result` to report the workspace path back to the agent.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser tool handling and turn cleanup`

A browser tool call like “navigate,” “find,” or “upload this file” should not have to know where Chrome is running. It might be inside the current sandbox, or it might be a remote hosted browser. This file hides that difference behind BuaSurface, a small “tool surface” used for one conversation turn.

The first time a browser action is requested, BuaSurface asks a CDP provider for a lease. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome. A lease is like borrowing a car key: it gives access to a browser session and must be returned. BuaSurface then creates a BrowserSession, which does the direct browser work.

The file also protects recovery after crashes. It stores a durable token for the leased browser. If the process crashes before cleanup, a replayed turn can reattach to the same live browser instead of opening a new blank one. Normal cleanup removes that token so later turns do not reconnect to a released session.

Uploads and downloads also pass through this surface. Uploads are resolved as workspace paths, copied to wherever the leased browser can see them, and checked so the page really received the bytes. Downloads are fetched from the lease, base64-encoded, and returned with a safe filename.

#### Function details

##### `BuaSurface._open`  (lines 60–73)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or returns the current browser session for this turn. It avoids connecting to Chrome until a browser tool actually needs it.

**Data flow**: It starts with the BuaSurface’s stored session and lease state. If a session already exists, it returns it. Otherwise it gets or creates a lease, asks the lease for a browser endpoint and download location, builds a BrowserSession, opens it, stores it, and returns it.

**Call relations**: All user-facing browser actions come through this helper first. When there is no lease yet, it asks BuaSurface._acquire_lease for one, then hands the endpoint into BrowserSession so later calls can navigate, read, type, upload, or download through the same connection.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 75–88)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets access to a browser session, preferring to reattach to a still-live session after a crash recovery. If recovery is not possible, it creates a fresh lease.

**Data flow**: It reads any saved token for this conversation. If a token exists, it asks the provider to reattach. If that old session is gone, it clears the token. Then it leases a new browser session, stores the new token, and returns the lease.

**Call relations**: BuaSurface._open calls this when it needs browser access and has no lease yet. It uses BuaSurface._stored_token and BuaSurface._store_token so crash recovery and normal fresh starts share one path.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 90–94)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser reattach token for this conversation. The token is what lets a replayed turn reconnect to the same browser after a hard crash.

**Data flow**: It checks whether both a scoped store and conversation ID are available. If not, it returns nothing. If they are, it reads the token key from the store and returns the value only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before deciding whether to reattach or create a new lease. It is the read side of the recovery-token mechanism.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 96–99)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Writes or clears the saved browser reattach token for this conversation. This keeps recovery possible after crashes, but prevents later turns from reusing a session that was properly released.

**Data flow**: It receives a token string or null. If store information is missing, it does nothing. Otherwise it writes that value under the conversation-specific token key.

**Call relations**: BuaSurface._acquire_lease stores a fresh token or clears a dead one. BuaSurface.aclose clears the token during normal shutdown so only crash-recovery paths can reattach.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 101–106)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates a browser tab to a requested URL. It validates that the URL is actually text before sending it to the browser.

**Data flow**: It receives tool arguments, opens the browser session, reads the url field, checks that it is a string, converts the optional tab_id into an integer when possible, and returns the browser session’s navigation result.

**Call relations**: This is one of the public tool methods. It uses BuaSurface._open to ensure the browser exists and _tab_id to normalize the optional tab choice before handing the work to BrowserSession.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 108–110)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the browser’s current tabs. A tool can use this to understand what pages are open and which tab it may want to act on.

**Data flow**: It receives the tool arguments, opens the browser session, asks the session for tab context, and returns that context unchanged.

**Call relations**: This public tool method is a thin route into BrowserSession. Its only setup step is BuaSurface._open, which makes sure a valid browser connection exists.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 112–115)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab, optionally starting at a requested URL. If no usable URL is supplied, it opens a blank page.

**Data flow**: It receives arguments, opens the browser session, reads the optional url value, replaces missing or empty non-text values with about:blank, then returns the result from the browser session.

**Call relations**: This public tab tool depends on BuaSurface._open for the connection, then delegates the actual tab creation to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 117–119)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab according to the provided arguments. This lets the tool surface tidy up or switch away from pages the user no longer needs.

**Data flow**: It receives the close arguments, opens the browser session, passes those arguments to the session’s tab-closing method, and returns the session’s reply.

**Call relations**: Like the other tab tools, it uses BuaSurface._open for setup and leaves the browser-specific details to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 121–142)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a page file input, even when the browser is remote. It makes sure the page gets files from the workspace rather than arbitrary paths on the host.

**Data flow**: It receives tool arguments, opens the browser, verifies that files is a list of non-empty strings, turns each into a workspace-safe path, asks the lease to place each file where Chrome can read it, and passes those placed paths to the browser session. After attaching, it waits until uploaded bytes have really arrived, then returns the browser reply.

**Call relations**: This public upload tool combines several pieces: BuaSurface._open gives a session, BuaSurface._lease gives transport access, workspace_path confines paths to the workspace, BuaSurface._read supplies bytes when needed, and BuaSurface._settle_upload verifies the page actually received the files.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 144–164)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits for uploaded files to be fully present in the page before returning. This prevents a quiet failure where a remote browser sees a filename but the file bytes have not arrived yet.

**Data flow**: It receives a browser session and the upload arguments. If no bytes were shipped by this surface, it returns immediately. Otherwise it repeatedly asks the browser what file sizes are attached, compares them with the sizes that were sent, retries the attach after short sleeps, and raises an error if the expected bytes never appear.

**Call relations**: BuaSurface.upload_file calls this after the first attach. It talks back to BrowserSession to inspect attached sizes and repeat the upload if the remote transport is still catching up.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 166–169)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active browser transport lease, or fails clearly if code asks for it before one exists. This protects file transfer paths from silently operating without browser access.

**Data flow**: It reads the BuaSurface’s lease field. If a lease is present, it returns it. If not, it raises a runtime error.

**Call relations**: BuaSurface.upload_file uses this when placing files for the browser. BuaSurface.wait_for_download uses it when fetching downloaded bytes from wherever the browser stored them.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 171–194)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file’s bytes from the sandbox for upload to a remote browser. It checks the size first so an oversized upload does not fill this process’s memory.

**Data flow**: It receives a workspace path. It requires a sandbox, quotes the path safely for shell use, asks the sandbox for the file size, rejects unreadable or too-large files, base64-reads the file through the sandbox, decodes the content in a worker thread, records the byte count, and returns the raw bytes.

**Call relations**: BuaSurface.upload_file passes this function as the byte reader to the lease’s file-placement step. It is only used when the transport needs to ship file contents rather than letting Chrome read the workspace path directly.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 196–198)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Asks the browser session for a structured reading of the current page. This is used when a tool needs a browser-aware view of page content.

**Data flow**: It receives read arguments, opens the browser session, forwards the arguments to the session, and returns the session’s page-reading result.

**Call relations**: This public tool method is a direct wrapper around BrowserSession. BuaSurface._open supplies the session before the page read begins.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 200–202)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets text from the current page. This gives the caller a simpler text-focused view instead of a richer browser structure.

**Data flow**: It receives arguments, opens the browser session, forwards the arguments to the session’s text extraction method, and returns the result.

**Call relations**: The browser tool layer calls this when it wants page text. BuaSurface._open prepares the session, and BrowserSession performs the actual extraction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 204–206)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Searches for something on the page, optionally using a host-side find completer to improve the search. A completer is helper logic that can finish or refine a find request.

**Data flow**: It receives find arguments, opens the browser session, sends the arguments plus the optional find completer to the session, and returns the search result.

**Call relations**: This public find tool uses BuaSurface._open for the browser connection, then hands the search to BrowserSession together with the completer stored on the surface.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 208–210)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or changes form fields on the page according to the tool arguments. This is how higher-level browser tools type into web forms.

**Data flow**: It receives form input arguments, opens the browser session, forwards the arguments to the session, and returns the session’s result.

**Call relations**: The method acts as the surface-level entry for form input. It relies on BuaSurface._open for setup and BrowserSession for the browser interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 212–214)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Runs lower-level computer-style browser actions, such as interactions that are closer to operating the page directly. It gives the tool layer access to the browser session’s general control path.

**Data flow**: It receives action arguments, opens the browser session, passes the arguments to the session’s computer method, and returns the result.

**Call relations**: This public method is another route through the same per-turn browser session. BuaSurface._open ensures the session exists before BrowserSession performs the requested action.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 216–232)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits until the browser finishes a download, then returns the downloaded bytes as base64 text with a safe filename. This works whether the browser saved the file in the sandbox or in remote provider storage.

**Data flow**: It receives wait arguments, opens the browser session, waits for a download record, asks the lease to fetch the download bytes by its identifier, base64-encodes those bytes in a worker thread, cleans the suggested filename down to one safe path segment, and returns filename, content, and size.

**Call relations**: This public download tool combines BrowserSession, which knows when the page downloaded something, with BuaSurface._lease, which knows where the bytes live. It uses _file_name before returning so later code can safely write the file into a workspace path.

*Call graph*: calls 3 internal fn (_lease, _open, _file_name); 1 external calls (to_thread).


##### `BuaSurface.aclose`  (lines 234–248)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the recovery token at the end of a normal turn. This prevents paid or remote browser sessions from being orphaned and prevents later turns from reconnecting to released sessions.

**Data flow**: It starts from the stored session and lease. If a session exists, it closes it and clears the session field. Whether or not that close succeeds, it then closes the lease, clears the lease field, and stores a null token.

**Call relations**: Turn cleanup calls this after browser work is done. It uses BuaSurface._store_token to remove the durable token; if a hard crash skips this function, the token remains available for BuaSurface._acquire_lease during recovery.

*Call graph*: calls 1 internal fn (_store_token).


##### `_file_name`  (lines 251–257)

```
def _file_name(suggested: str) -> str
```

**Purpose**: Turns a download’s suggested name into a safe single filename. It blocks names like ../notes.md from escaping the folder where the caller meant to save the file.

**Data flow**: It receives the browser’s suggested filename, takes only the last path segment using PurePosixPath, and returns a default name if the result is empty, dot, or dot-dot. Otherwise it returns the cleaned name.

**Call relations**: BuaSurface.wait_for_download calls this before returning a downloaded file to the caller. It is the safety check between a web page’s chosen filename and later workspace writes.

*Call graph*: called by 1 (wait_for_download); 1 external calls (PurePosixPath).


##### `_tab_id`  (lines 260–271)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab identifier from JSON-like tool arguments into an integer tab ID when possible. It treats missing, false-like, or unsuitable values as no specific tab.

**Data flow**: It receives a JSON value. Booleans and unsupported values become null, integers pass through, floats are truncated to integers, and non-empty strings are parsed as integers.

**Call relations**: BuaSurface.navigate uses this when a navigation request includes an optional tab_id. The normalized value is then passed to BrowserSession so navigation targets the intended tab or defaults naturally.

*Call graph*: called by 1 (navigate).


### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `per browser turn: session startup, browser tool calls, and teardown`

A BrowserSession is the project’s bridge into Chrome. Chrome is controlled through CDP, the Chrome DevTools Protocol, which is a WebSocket-based control channel that lets another program inspect pages, click, type, watch downloads, and listen for browser events. Without this file, the rest of the browser tools would have no single place to open Chrome, subscribe to important events, or remember what is currently happening.

The session starts with basic state: known tabs, active downloads, out-of-process frame sessions, dialog messages, keyboard/platform details, and a settle tracker that notices when the page is still loading network resources. When opened, it resolves the CDP address, connects to Chrome, asks Chrome what platform it is running on, enables download events, starts discovering tabs, creates a blank tab, and attaches listeners for downloads, page loading, dialogs, and tab creation or destruction.

Most user-facing methods in this file are thin doorways into more focused helper objects. For example, tab actions go through BrowserTabs, page reading goes through BrowserContent, form work goes through BrowserForms, JavaScript work goes through BrowserRuntime, and low-level mouse/keyboard-style control goes through BrowserComputer. Think of BrowserSession as the front desk of a hotel: it knows who is checked in, receives alerts, and sends each request to the right specialist. Closing the session shuts tabs, closes the CDP connection, cancels background work, and resets all remembered state.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates a new session object and fills it with empty, ready-to-use state. It does not connect to Chrome yet; it only prepares the place where tabs, downloads, dialogs, and background tasks will be tracked.

**Data flow**: It receives an optional CDP endpoint, optional model name, and download directory. It chooses the coordinate size to use for screenshots, stores the connection settings, creates empty lists and maps for browser state, and creates fresh helpers for settling and tab events. The result is a BrowserSession instance that is ready to be opened.

**Call relations**: BuaSurface._open creates this object before the browser can be used. During setup it creates the settle tracker and tab event tracker, and asks the coordinate helper whether the requested model needs a special coordinate space.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the connection to Chrome if it is not already open. It protects callers from double-opening the same session.

**Data flow**: It checks whether a connection already exists. If not, it runs the bootstrap process; if bootstrapping fails partway through, it closes anything that was opened and then reports the original failure. After success, the session has a live CDP connection and an initial tab.

**Call relations**: The async context manager calls this when entering a `with` block. It delegates the real setup to BrowserSession._bootstrap and uses BrowserSession.close as cleanup if setup goes wrong.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the detailed startup work needed to make Chrome usable through this session. It connects to CDP, registers event listeners, enables downloads, discovers tabs, and creates the first blank tab.

**Data flow**: It reads the stored CDP endpoint and headers, resolves them to a WebSocket URL, opens a CDP connection, asks Chrome for version details, and records whether the browser appears to be on macOS. It then builds reader/helper objects for tabs, downloads, and dialogs, connects Chrome events to their callbacks, enables download behavior, starts target discovery, creates an about:blank tab, and stores the attached tab in the session.

**Call relations**: BrowserSession.open calls this as the main startup phase. It hands tab events to BrowserTabs, download events to BrowserDownloads, dialog events to BrowserDialogs, and network/page loading events to the settle and tab tracking machinery.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the session and returns it to a clean empty state. It is designed to be safe even if the browser is already partly closed or in an error state.

**Data flow**: It attempts to close each known Chrome tab, closes the CDP connection, and then clears all session memory: connection, tabs, frame sessions, downloads, scroll state, settle tracker, dialogs, background tasks, and tab events. It cancels any background tasks that were spawned by the session.

**Call relations**: The async context manager calls this on exit, and BrowserSession.open also calls it if startup fails. It creates fresh settle and tab event trackers so a later open starts cleanly.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python’s async context manager pattern. This means callers can write code that automatically opens the browser session before use.

**Data flow**: It receives the session itself, opens it, and returns the same session to the caller. Afterward, the caller can use the browser tools with a live connection.

**Call relations**: It is the entry side of the context-manager story. It simply calls BrowserSession.open so all normal startup rules and cleanup-on-failure behavior are reused.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Automatically closes the browser session when an async context manager block ends. This helps prevent leftover tabs, connections, or background tasks.

**Data flow**: It receives any exception information from the context manager, ignores the details, and closes the session. Afterward, the session’s stored browser state is reset.

**Call relations**: It is the exit side of the context-manager story. It delegates shutdown to BrowserSession.close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection for helpers that need to talk directly to Chrome. If the session has not been opened, it raises a clear browser-unavailable error.

**Data flow**: It checks the stored connection field. If the field is empty, it raises BrowserUnavailable; otherwise it returns the existing CdpConnection object unchanged.

**Call relations**: Reader and helper objects use this kind of access when they need to send CDP commands. It is the guardrail that prevents browser work from silently running without an open browser.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so it can be cancelled later. This is useful for browser work that must continue while the main call moves on.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session’s background task set, and arranges for the task to remove itself from the set when finished.

**Call relations**: Other browser helpers can use this when they need side work. BrowserSession.close later cancels whatever tasks are still remembered, so background work does not leak past the session lifetime.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Asks whether a browser frame is the main frame of a tab rather than an embedded frame. This matters because page events can come from many nested frames.

**Data flow**: It receives a CDP session id and frame id, creates a BrowserTabs helper, and asks that helper to decide whether the frame is top-level. It returns a true-or-false answer.

**Call relations**: This method is a small doorway into BrowserTabs. It is used when event handling needs to know whether a loading or frame event describes the main page.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready for tab/page work. This usually means enabling the browser domains and listeners needed for that target.

**Data flow**: It receives a CDP session id, creates a BrowserTabs helper, and asks it to initialize that session. The outcome is side effects in Chrome and session state rather than a returned value.

**Call relations**: Tab attachment flows call this through the BrowserTabs helper. The session acts as the stable owner of shared state while BrowserTabs performs the tab-specific setup.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the active tab, or a requested tab, as a Tab object. Callers use it when they need to know which browser page an action should apply to.

**Data flow**: It receives an optional tab id, creates a BrowserTabs helper, and asks it to find the matching tab. The result is a Tab object representing the selected browser tab.

**Call relations**: This is a tab-selection doorway into BrowserTabs. Higher-level browser actions can call it before reading or acting on a page.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new web address. It is the session-level method for telling Chrome, in plain terms, 'go to this URL.'

**Data flow**: It receives a URL and optional tab id, creates a BrowserTabs helper, and asks it to navigate the chosen tab. It returns a JSON-style dictionary describing the result.

**Call relations**: This method forwards navigation work to BrowserTabs, which knows how to talk to Chrome targets and keep tab state consistent.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a small information summary for one tab. This gives callers a structured view of a tab instead of making them inspect internal Tab fields.

**Data flow**: It receives a Tab object, creates a BrowserTabs helper, and asks it to format that tab’s information. It returns a JSON-style dictionary.

**Call relations**: This is part of the tab-status flow. BrowserSession owns the tab list, while BrowserTabs supplies the tab-specific interpretation.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about the current set of tabs. This helps the rest of the system know what pages are open and which one is relevant.

**Data flow**: It creates a BrowserTabs helper and asks it to describe the session’s tabs. The output is a JSON-style dictionary suitable for a tool response or prompt context.

**Call relations**: It delegates to BrowserTabs, which reads the session’s tab state and turns it into useful browser context.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of open tabs. This is a compact way to show what pages the browser currently has open.

**Data flow**: It creates a BrowserTabs helper and asks it for tab titles. The output is a list of strings.

**Call relations**: It is another simple doorway into BrowserTabs, used when callers need a lightweight tab overview rather than full tab details.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper tied to this session. BrowserTabs is the specialist for tab creation, closing, navigation, attachment, and tab event interpretation.

**Data flow**: It takes the current session and the fixed viewport size and creates a new BrowserTabs object. The helper can then read and update the session’s tab-related state.

**Call relations**: Many session methods call this when they need tab work. Bootstrap also uses it to remember initial targets, attach the first tab, and wire tab-related Chrome events.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper tied to this session. BrowserPage is the specialist for page structure, frame references, and screen points for page elements.

**Data flow**: It takes the session, viewport size, and maximum frame depth and creates a BrowserPage object. The helper can then interpret page references using current tab and frame information.

**Call relations**: BrowserSession.resolve_ref and BrowserSession.ref_point call this when a string reference must be turned into a frame/node location or coordinates.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper tied to this session. BrowserContent is the specialist for reading page content, extracting text, building trees, and searching.

**Data flow**: It passes the current session into a new BrowserContent object. That object can use the session’s connection and tabs to inspect the browser page.

**Call relations**: The tree, read_page, get_page_text, and find methods call this helper whenever the system needs to understand what is visible or present on a page.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper tied to this session. BrowserDownloads is the specialist for tracking Chrome download events and waiting for a download to finish.

**Data flow**: It passes the current session and maximum wait time into a new BrowserDownloads object. The helper can read and update the session’s download list.

**Call relations**: Bootstrap uses it to wire Chrome download events to callbacks. BrowserSession.wait_for_download uses it later when a caller needs to wait until a download has completed.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper tied to this session. BrowserDialogs is the specialist for noticing JavaScript alert, confirm, or prompt dialogs.

**Data flow**: It passes the current session into a new BrowserDialogs object. The helper can record dialog messages in the session when Chrome reports them.

**Call relations**: Bootstrap calls this to connect Chrome’s dialog-opening event to the dialog callback.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper tied to this session. BrowserForms is the specialist for typing into fields, uploading files, and checking attached file sizes.

**Data flow**: It passes the current session into a new BrowserForms object. The helper can use the browser connection and page references to perform form actions.

**Call relations**: Upload, attached-size checks, and form input methods all call this to send their work to the forms specialist.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper tied to this session. BrowserRuntime is the specialist for running JavaScript and calling functions on browser-side objects.

**Data flow**: It passes the current session into a new BrowserRuntime object. The helper can then use the CDP runtime features for the requested page session.

**Call relations**: BrowserSession.eval_js and BrowserSession.call_on call this whenever code needs to run inside the browser page.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, optionally at a given URL. It gives callers a session-level way to open another page.

**Data flow**: It receives a URL, defaulting to about:blank, creates a BrowserTabs helper, and asks it to create the tab. It returns a JSON-style dictionary describing the result.

**Call relations**: This forwards tab creation to BrowserTabs, keeping the session as the public tool surface while the tab helper does the Chrome-specific work.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab based on the caller’s arguments. It is the session-level doorway for removing tabs.

**Data flow**: It receives a JSON-style argument dictionary, creates a BrowserTabs helper, and asks it to close the requested tab. It returns a JSON-style result.

**Call relations**: It delegates tab-closing details to BrowserTabs, which understands tab ids and the session’s tab list.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads or attaches a file to a file input on the page. This is how browser automation supplies files to web forms.

**Data flow**: It receives a JSON-style argument dictionary, creates a BrowserForms helper, and asks it to perform the upload. The returned JSON-style dictionary describes the outcome.

**Call relations**: BuaSurface._settle_upload calls this as part of the upload flow. BrowserSession forwards the request to BrowserForms because form code knows how to reach the correct page element.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Checks the sizes of files attached through a form upload flow. This helps confirm what was attached without reading or transferring the file contents here.

**Data flow**: It receives a JSON-style argument dictionary, creates a BrowserForms helper, and asks it for attached file sizes. It returns a list of integer sizes.

**Call relations**: BuaSurface._settle_upload calls this after upload-related work. BrowserForms provides the page/form-specific logic while the session supplies shared browser state.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree of page content, optionally filtered by content type. This gives callers a readable outline of what the page contains.

**Data flow**: It receives JSON-style arguments and a filter type, creates a BrowserContent helper, and asks it to build the tree. It returns the tree as a string.

**Call relations**: It delegates page-inspection work to BrowserContent, which knows how to collect and format page structure.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured response. This is one of the main ways the system turns a live webpage into information a model or caller can use.

**Data flow**: It receives JSON-style arguments, creates a BrowserContent helper, and asks it to read the page. The output is a JSON-style dictionary with the page-reading result.

**Call relations**: It is part of the content-reading tool surface. BrowserContent performs the detailed browser inspection while BrowserSession routes the request.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts text from the current page. This is useful when the caller needs the words on the page rather than a fuller structural description.

**Data flow**: It receives JSON-style arguments, creates a BrowserContent helper, and asks it for page text. It returns a JSON-style dictionary with the extracted text result.

**Call relations**: It forwards text extraction to BrowserContent, keeping text-reading logic out of the session itself.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within the current page for requested content. An optional completer can help finish or refine the search result for the caller.

**Data flow**: It receives JSON-style search arguments and an optional FindCompleter, creates a BrowserContent helper, and asks it to find matches. It returns a JSON-style dictionary with the search result.

**Call relations**: It is the session doorway into BrowserContent’s search logic. BrowserContent does the page inspection, and the optional completer can participate in producing the final answer.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or sets input in a form field on the page. This is the browser-session method for filling out web forms.

**Data flow**: It receives JSON-style arguments, creates a BrowserForms helper, and asks it to perform the input action. It returns a JSON-style result.

**Call relations**: It delegates form-specific work to BrowserForms, which can resolve the target field and use the browser connection to change it.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a lower-level computer-style browser action, such as mouse or keyboard interaction, against the page. This is for actions that are closer to using the browser like a person would.

**Data flow**: It receives JSON-style action arguments, creates a BrowserComputer with the session, viewport, and maximum wait time, and runs it. The output is a JSON-style result.

**Call relations**: Unlike the reader factory methods, this creates BrowserComputer directly for the requested action. BrowserComputer then uses the session’s connection and state to carry out the interaction.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a download to finish and returns its download record. The file contents are not read here; the completed download is reported to whoever owns the download directory.

**Data flow**: It receives JSON-style arguments, creates a BrowserDownloads helper, and asks it to wait for the matching download. It returns a BrowserDownload object when the download completes or the wait logic finishes as defined by that helper.

**Call relations**: It uses BrowserDownloads, the same helper whose callbacks were wired during bootstrap to receive Chrome download events.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Runs a JavaScript expression inside a specific browser session. This gives helper code a controlled way to ask the page for information or perform page-side work.

**Data flow**: It receives a CDP session id and JavaScript expression, creates a BrowserRuntime helper, and asks it to evaluate the expression. It returns the JSON-like value produced by the browser runtime.

**Call relations**: It delegates JavaScript execution to BrowserRuntime, which knows the CDP runtime commands needed for the chosen page session.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on a specific browser-side object. This is useful when the page has returned an object reference and the system needs to ask that object for more detail.

**Data flow**: It receives a CDP session id, an object id, a function body or name, and optional arguments. It creates a BrowserRuntime helper, asks it to call the function on that object, and returns a JSON-style dictionary with the result.

**Call relations**: It is the object-function counterpart to eval_js. BrowserRuntime performs the actual CDP runtime call while BrowserSession supplies the public method.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame and node index it points to. This lets later actions target a specific element or frame instead of guessing.

**Data flow**: It receives a Tab object and a reference string, creates a BrowserPage helper, and asks it to resolve the reference. It returns a pair containing the frame node and an integer index.

**Call relations**: It delegates reference parsing and page-structure lookup to BrowserPage, which understands frame trees and element references.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen point for a referenced page item. This is useful when an action needs coordinates, such as clicking the center of an element.

**Data flow**: It receives a Tab object and a reference string, creates a BrowserPage helper, and asks it for the point. It returns an x and y coordinate pair.

**Call relations**: It uses BrowserPage to convert a logical page reference into physical coordinates. BrowserComputer or other interaction code can then use those coordinates for visible-page actions.

*Call graph*: calls 1 internal fn (page_reader).

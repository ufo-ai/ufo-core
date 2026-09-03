# In-process browser tool surface  `stage-10.1`

This stage is the browser control layer used during the assistant’s main work loop. It is the “tool surface” inside the running process: the assistant asks to open a page, click, type, read text, manage tabs, or save a download, and this layer turns that request into safe Chrome actions.

tools.py exposes the tools, while backend.py creates the per-turn bridge to Chrome and cleans it up afterward. session.py coordinates the live browser connection. tabs.py manages pages and loading, and downloads.py watches saved files, including PDFs. computer.py runs basic actions like click, scroll, wait, and screenshot; keys.py sends real keyboard events; coordinate.py maps screenshot points to browser pixels.

page.py, content.py, and find.py turn web pages into readable text and stable element references. forms.py fills fields and uploads files. settle.py waits until an action is ready to continue. dialogs.py handles pop-ups. fixup.py repairs small instruction mistakes before they reach Chrome.

actions.py, errors.py, wire.py, and runtime.py define shared message shapes, errors, JavaScript execution, and Chrome message checks. The __init__.py files simply make these folders importable packages.

## Files in this stage

### Tool entry lifecycle
These files clean up incoming browser requests, expose them as agent tools, and manage the per-turn browser backend and session.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `request handling`

This file is a safety net between an action-producing model and the browser that will carry out those actions. The model may produce instructions that are understandable to a human but too vague or slightly wrong for automation. For example, it may say to type into a place without first clicking there, or it may include the two characters “\n” when it really means a newline key press.

The main function, `fixup_actions`, walks through a list of requested browser actions and turns them into safer, more complete actions. It adds a click before typing when needed, converts escaped text like “\t” and “\n” into real tab and newline characters, gives scroll actions a sensible center point when no target is provided, turns an incomplete `scroll_to` into a normal scroll, and fills in a default wait time. It also simplifies multi-clicks aimed at a named reference into a single left click, because a reference points to an element rather than an exact screen coordinate.

The second public helper, `split_at_waits`, divides a long action list into batches that end at waits. This lets the browser pause and settle, like stopping between steps in a recipe so the page can finish changing before the next instruction runs.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: Repairs a batch of browser actions before they are executed. It makes vague or slightly malformed instructions concrete enough for the browser automation layer to use safely.

**Data flow**: It receives a list of `ComputerAction` objects, the current browser viewport size, and optionally the size expected by the model. It works out the effective screen size, then reviews each action one by one. It may add a focus click, rewrite text, fill in missing coordinates or durations, replace incomplete scroll instructions, or leave the action unchanged. It returns a new list of actions that is safer and more explicit than the input list.

**Call relations**: This is the central cleanup step in this file. When it sees a typing action that needs focus, it asks `_focus_click` to create the click. When it sees text that may contain escaped sequences, it asks `_unescape_text` to rewrite it. It also relies on `effective_model_size` to choose a sensible center point and creates replacement `ComputerAction` or `ScrollParameters` objects when an action needs to be made complete.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: Breaks one long action list into smaller groups, ending a group whenever a wait action appears. This gives the browser time to update before later actions are attempted.

**Data flow**: It receives a list of actions. It builds a current batch by adding actions in order. When it reaches a wait action, it closes that batch and starts a new one. At the end, it returns a list of batches, preserving the original action order.

**Call relations**: This function is useful after actions have been prepared, when the caller wants to dispatch them in stages. Unlike `fixup_actions`, it does not change any action contents; it only decides where the natural pauses should be.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: Creates a left-click action that focuses the target of a typing action. This is used when text is about to be typed but the action list did not already click into the target area.

**Data flow**: It receives a typing-related `ComputerAction`. If that action has a screen coordinate, it creates a left click at that coordinate. Otherwise, it creates a left click aimed at the action’s reference target. The result is a new `ComputerAction` that can be inserted before typing.

**Call relations**: This helper is called by `fixup_actions` during typing cleanup. It supplies the missing focus step, so the later typing action is more likely to go into the intended field instead of wherever the browser currently has focus.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: Turns literal escape sequences in typed text into the real characters they mean. For example, it changes the two-character string “\n” into an actual newline.

**Data flow**: It receives a `ComputerAction` that may contain text. It checks whether the text includes known escaped literals, currently tab and newline. If none are present, it returns the original action. If they are present, it replaces them and returns a copied action with updated text.

**Call relations**: This helper is called by `fixup_actions` whenever it processes a typing action. It keeps the main cleanup loop simple while ensuring typed text behaves as intended when sent to the browser.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `during agent tool calls within a turn`

This file turns browser actions into safe, named tools. Each tool has an input shape, so the agent must provide the right kind of information before anything reaches the browser. For example, navigation needs a URL, form input needs a page reference and a value, and download waiting can include a timeout and target path.

The important shared piece is the browser surface, `BuaSurface`. You can think of it as the remote control for the browser. The first time any browser tool is used during an agent turn, `_browser` creates this remote control from the turn's Chrome connection provider and other context. It then keeps the same remote control for the rest of that turn, so tools do not repeatedly open new browser connections. It also registers cleanup, so the connection is closed when the turn ends.

Most tool functions are thin translators: they take validated input, remove empty optional fields, call the matching `BuaSurface` method, and return the reply as JSON text. Two tools do extra file work. `_computer` can return a screenshot as image content and optionally save it into the shared workspace. `_wait_for_download` receives downloaded bytes from the browser and writes them into the workspace so other agents or later steps can use the file by path.

#### Function details

##### `_browser`  (lines 87–110)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser-control surface for the current agent turn. It creates that surface only when the first browser tool needs it, then reuses it so all browser actions in the same turn share the same browser connection.

**Data flow**: It receives the tool context, which contains the cleanup registry, Chrome connection provider, model, sandbox, store, and conversation information. It looks for an existing `BuaSurface` tied to this turn's cleanup object; if none exists, it builds one and registers its close method for end-of-turn cleanup. It returns the ready-to-use browser surface, or raises an error if no Chrome connection provider is available.

**Call relations**: Every browser tool calls `_browser` before talking to the browser. On the first such call, `_browser` creates `BuaSurface`; later calls from tools such as `_navigate`, `_find`, `_computer`, and `_wait_for_download` receive the already-created surface and continue the same browser session.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 113–114)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain Python dictionary into the standard tool result format as JSON text. This gives the agent a consistent way to receive structured replies from browser tools.

**Data flow**: It receives a reply dictionary, converts it to a JSON string, places that string inside a text content object, and returns a tool result containing that text. It does not change the original reply.

**Call relations**: Most browser tool handlers call `_json_result` after receiving a reply from `BuaSurface`. It is the common final step for tools whose output is ordinary structured text rather than a separate image or file.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 117–120)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a browser reply contains a required non-empty string field. It is used when the next step would fail or silently write bad data if the field were missing.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns that string; otherwise it raises a clear error saying which field is missing.

**Call relations**: `_computer` uses this when it must save a screenshot from base64 text, and `_wait_for_download` uses it when it must get a filename and file contents. It protects the file-writing steps from incomplete browser replies.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 123–125)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Moves a browser tab to a requested URL or navigation target. This is the tool behind the agent's ability to open web pages or move through browser history.

**Data flow**: It receives validated navigation input, including a URL and optionally a tab id. It turns that input into a JSON-friendly dictionary without empty optional fields, sends it to the browser surface's navigation method, and returns the browser's reply as JSON text.

**Call relations**: This function is registered as the handler for the `navigate` tool. When the agent asks to navigate, it gets the shared browser surface through `_browser`, hands off the navigation request, then formats the result through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 128–129)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. The agent can use this to understand what pages are open before deciding what to do next.

**Data flow**: It receives an empty input object, asks the shared browser surface for tab context, and returns the resulting tab information as JSON text.

**Call relations**: This function is the handler for the `tabs_context` tool. It relies on `_browser` to reuse the turn's browser connection and `_json_result` to package the tab information for the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 132–134)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opening a given URL. If no URL is supplied, it opens a blank page.

**Data flow**: It receives optional tab creation input. It chooses the requested URL or falls back to `about:blank`, asks the browser surface to create the tab, and returns the browser's reply as JSON text.

**Call relations**: This function is registered as the `tabs_create` tool handler. It calls `_browser` to reach the browser engine and `_json_result` to return the new-tab result.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 137–139)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab. If a tab id is supplied, it targets that tab; otherwise the browser backend decides what the default close behavior is.

**Data flow**: It receives optional tab-closing input, converts it into a JSON-friendly dictionary without empty optional fields, sends it to the browser surface, and returns the close result as JSON text.

**Call relations**: This function backs the `tabs_close` tool. Like the other tab tools, it gets the shared surface through `_browser` and sends the final response through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 142–144)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a web page file input to files from the shared workspace. This lets the agent upload files into websites without manually interacting with the operating system file picker.

**Data flow**: It receives a browser element reference, one or more workspace file paths, and optionally a tab id. It converts that input into a JSON-ready dictionary, passes it to the browser surface's file upload method, and returns the browser's reply as JSON text.

**Call relations**: This function is the handler for the `upload_file` tool. It depends on `_browser` for the actual browser action and uses `_json_result` to send the result back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 147–149)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page's accessibility tree, which is a browser-provided description of page elements meant for assistive tools such as screen readers. The agent can use it to understand buttons, fields, links, and layout in a structured way.

**Data flow**: It receives options such as depth, filter type, a reference id, and optionally a tab id. It removes unset options, asks the browser surface to read the page, and returns the structured page description as JSON text.

**Call relations**: This function backs the `read_page` tool. It obtains the active browser surface with `_browser` and formats the returned page structure with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 152–154)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This is useful when the agent needs the words on the page more than the detailed element structure.

**Data flow**: It receives optional tab selection input, converts it into a JSON-friendly dictionary without empty fields, asks the browser surface for page text, and returns that text response as JSON.

**Call relations**: This function is registered as the `get_page_text` tool handler. It calls `_browser` to access the browser and `_json_result` to return the extracted text in the normal tool-result format.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 157–159)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the page for elements matching a query, such as by visible text, role, name, or URL. This helps the agent locate the page element it should interact with next.

**Data flow**: It receives a search query and optionally a tab id. It converts the input into a JSON-ready dictionary, sends it to the browser surface's find method, and returns the matches as JSON text.

**Call relations**: This function is the handler for the `find` tool. It uses `_browser` to reach the browser-control layer and `_json_result` to pass the found element references back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 162–164)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. It gives the agent a direct way to fill text boxes, dropdown-like controls, and other form values.

**Data flow**: It receives a page element reference, the value to set, and optionally a tab id. It turns this into a JSON-friendly dictionary, asks the browser surface to apply the form value, and returns the browser's reply as JSON text.

**Call relations**: This function backs the `form_input` tool. It gets the shared browser surface through `_browser`, delegates the actual page change to that surface, and then wraps the reply with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 167–183)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs lower-level browser interaction actions such as mouse movement, clicks, keyboard input, scrolling, waiting, and screenshots. It is the more hands-on tool for cases where structured page actions are not enough.

**Data flow**: It receives a list of computer-style actions, optional tab selection, and optional screenshot-saving settings. It sends the actions to the browser surface and receives a reply that may include a screenshot as base64 text, which is a text-safe form of binary image data. If asked to save the screenshot, it checks the screenshot field, decodes it into bytes, writes it to the sandbox workspace, and adds the saved path to the reply. If a screenshot is present, it returns both JSON text for the non-image fields and separate image content; otherwise it returns a normal JSON result.

**Call relations**: This function is the handler for the `computer` tool. It starts by getting the shared browser surface through `_browser`; for ordinary replies it uses `_json_result`, but when image data is present it builds a richer tool result with text plus image content. It uses `_required_str` before saving a screenshot so missing browser data becomes a clear error.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 186–194)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download, saves the downloaded file into the shared workspace, and returns the saved path. This turns a browser-only download into a file that the rest of the agent system can use.

**Data flow**: It receives optional download id, target path, and timeout settings. It asks the browser surface to wait for and return the download, checks that the reply includes a filename and base64 file contents, decodes the contents into bytes, writes them under the requested folder or the default downloads folder, and returns the final path, filename, and size as JSON text.

**Call relations**: This function backs the `wait_for_download` tool. It calls `_browser` to listen for the browser download, uses `_required_str` to make sure the download reply is complete, writes through the sandbox so the file lands in the shared workspace, and formats the final file information with `_json_result`.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser request handling and turn cleanup`

Think of BuaSurface as the front desk for browser work during one assistant turn. The rest of the system asks it to do browser actions, and it quietly arranges the real browser connection behind the scenes. If a turn never uses the browser, it never opens Chrome. On first browser use, it asks a CDP provider for a lease. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome. The lease gives an endpoint, and BuaSurface builds a BrowserSession around it.

The file also protects continuity after crashes. When it gets a lease, it stores a durable token tied to the conversation. If the process crashes before cleanup, a recovered turn can use that token to reconnect to the same live browser session instead of opening a fresh page. Normal cleanup clears the token so later turns do not reconnect to a browser that was already released.

Uploads and downloads are also routed here because local and remote browsers need different file handling. For uploads, workspace paths are checked, read from the sandbox if needed, shipped through the lease, and then verified so the page really received the bytes. For downloads, the browser session reports a finished download, the lease fetches its bytes, and this file returns a safe filename plus base64-encoded content.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or reuses the browser session for this turn. It is the lazy startup point: the browser connection is not made until some tool actually needs it.

**Data flow**: It starts with the BuaSurface object, which may already have a session or lease. If a session exists, it returns it. If not, it gets or creates a lease, asks that lease for the browser endpoint and download directory, builds a BrowserSession, opens it, stores it, and returns the ready session.

**Call relations**: All the browser-facing tool methods come through this doorway before doing their work. When there is no lease yet, it hands off to BuaSurface._acquire_lease, then constructs the BrowserSession that later methods use for navigation, reading, tab work, form input, uploads, downloads, and computer-style actions.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets access to a Chrome session, either by reconnecting to an existing one after a crash or by creating a fresh lease. This is what keeps an in-progress browser task from losing its page during recovery.

**Data flow**: It reads a stored token, if one exists. If there is a token, it asks the provider to reattach to that browser session. If that session is gone, it clears the bad token. Then it asks the provider for a new lease, stores the new lease token, and returns the lease.

**Call relations**: BuaSurface._open calls this when it needs a lease. This function relies on BuaSurface._stored_token and BuaSurface._store_token to read and write the recovery marker, and it uses the CDP provider as the actual source of browser leases.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser-session token for this conversation. The token is used only when recovery might need to reconnect to an earlier live session.

**Data flow**: It checks whether both a scoped store and conversation id are available. If either is missing, it returns nothing. Otherwise it reads the token key from the store and returns the value only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before deciding whether to reconnect or create a new session. It is deliberately quiet when storage is unavailable, so normal browser startup can still proceed without recovery support.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the durable browser-session token for this conversation. Saving supports crash recovery; clearing prevents later turns from attaching to a released browser.

**Data flow**: It receives either a token string or null. If the store or conversation id is missing, it does nothing. Otherwise it writes that value under the conversation-specific CDP token key.

**Call relations**: BuaSurface._acquire_lease uses this to save a fresh token or remove a dead one. BuaSurface.aclose uses it during normal cleanup to erase the token after the lease has been released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates the browser to a requested URL, optionally in a specific tab. It validates that the URL is actually text before sending it to the browser session.

**Data flow**: It receives a dictionary of tool arguments. It opens the browser session, reads the url field, rejects it if it is not a string, converts the optional tab_id into a usable tab number, and returns the navigation result from the session.

**Call relations**: This is one of the public tool-surface methods. It first goes through BuaSurface._open to ensure the session exists, uses _tab_id to normalize the tab choice, then delegates the actual page navigation to BrowserSession.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs. Tools use this to understand what pages are open and which tab context is available.

**Data flow**: It receives tool arguments, though it does not need any specific values from them. It opens the browser session and returns the tab context reported by that session.

**Call relations**: This method is called as part of tab-related browser tooling. It uses BuaSurface._open as the shared session doorway, then asks BrowserSession for the current tab picture.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no usable URL is provided, it opens a blank page instead of failing.

**Data flow**: It receives a dictionary that may contain a url. It opens the browser session, chooses the provided non-empty string URL or falls back to about:blank, then returns the result of creating the tab.

**Call relations**: This public tab tool follows the same pattern as the other tools: open or reuse the session through BuaSurface._open, then hand the browser-specific operation to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab using the arguments supplied by the tool caller. It leaves the details of which tab to close to the browser session layer.

**Data flow**: It receives the caller's tab-close arguments, opens the browser session, passes the arguments through, and returns the session's close result.

**Call relations**: This sits between the tool API and BrowserSession. It does the shared setup through BuaSurface._open and then delegates the actual tab closing.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Uploads one or more workspace files into a page file input. It makes sure each requested path is a workspace path and lets the lease place the file where the current browser can access it.

**Data flow**: It receives arguments containing a files list. It opens the browser session, validates that the list contains non-empty path strings, converts each to a safe workspace path, asks the lease to place each file, and supplies a reader callback for transports that need to ship bytes. It then calls the session upload, waits until shipped bytes really appear in the page, and returns the upload response.

**Call relations**: Browser upload tools call this method. It uses BuaSurface._open for the session, BuaSurface._lease for the active transport lease, workspace_path to keep paths inside the workspace, BuaSurface._read as the byte source when needed, and BuaSurface._settle_upload to avoid a race where the page sees an empty file.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until the page's file input actually contains the uploaded bytes. This matters for remote browsers, where the upload command may return before the file has fully arrived.

**Data flow**: It looks at the sizes of files this surface had to ship. If none were shipped, it returns immediately. Otherwise it repeatedly asks the browser session what file sizes are attached, compares them with the expected sizes, sleeps briefly between tries, and re-attaches the files if needed. If the sizes never match, it raises an error explaining what the page received.

**Call relations**: BuaSurface.upload_file calls this after the initial upload. It works with BrowserSession.attached_sizes to check the page and BrowserSession.upload_file to retry attachment while the remote file placement settles.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active CDP lease and fails clearly if there is none. It is a guardrail for operations that must go through the transport lease, such as file placement and download fetching.

**Data flow**: It reads the BuaSurface object's lease field. If the lease exists, it returns it. If not, it raises a runtime error saying there is no browser lease.

**Call relations**: BuaSurface.upload_file and BuaSurface.wait_for_download call this after opening the browser session. Those methods need the lease because only the transport layer knows where files live for local versus remote Chrome.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file's bytes from the sandbox so they can be sent to a remote browser. It checks the size first to avoid pulling an unexpectedly large file into memory.

**Data flow**: It receives a resolved path. It requires a sandbox, quotes the path for safe shell use, runs a size check, rejects unreadable or over-limit files, runs base64 encoding inside the sandbox, decodes that base64 back into bytes in a worker thread, records the byte count, and returns the bytes.

**Call relations**: This function is passed as a callback by BuaSurface.upload_file when the lease may need to ship file contents. It uses shell commands through the sandbox for the actual file read and asyncio.to_thread so base64 decoding does not block the main event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads the current page through the browser session. This is used when the tool wants structured information about what is visible or available on the page.

**Data flow**: It receives read arguments, opens the browser session, passes the arguments to the session's page-reading method, and returns the result.

**Call relations**: This public tool method is a thin bridge. It shares startup through BuaSurface._open and then lets BrowserSession perform the actual page inspection.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets text from the current page. It is the simpler text-focused read path for browser tools.

**Data flow**: It receives text-reading arguments, opens the browser session, passes those arguments to BrowserSession, and returns the text result.

**Call relations**: Like the other read-style methods, it uses BuaSurface._open to ensure there is a live session, then delegates the browser-specific extraction work to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, with optional help from a host-side find completer. The completer can assist the session in turning a search request into a better page target.

**Data flow**: It receives find arguments, opens the browser session, and passes both the arguments and the configured find completer to the session. It returns the session's find result.

**Call relations**: Browser find tooling calls this method. It prepares the session through BuaSurface._open and supplies the extra find_completer dependency when handing off to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or changes form inputs on the page. This is the tool path for entering text or values into web forms.

**Data flow**: It receives form-input arguments, opens the browser session, passes the arguments to the session, and returns the result of the form action.

**Call relations**: This is a public browser tool method. It uses the common BuaSurface._open step, then relies on BrowserSession to perform the actual page interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Runs lower-level computer-style browser actions, such as interactions that are closer to direct page or screen control. It gives tools a general action channel through the browser session.

**Data flow**: It receives action arguments, opens the browser session, forwards the arguments to BrowserSession, and returns whatever result the session reports.

**Call relations**: This method is another entry in the tool surface. It follows the same shared pattern as navigation and form input: get the session through BuaSurface._open, then hand the concrete browser action to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns its contents in a safe, portable form. It also trims the filename down to one safe path segment so a downloaded name cannot write outside the intended location.

**Data flow**: It receives download-wait arguments, opens the browser session, waits for the session to report a finished download, asks the lease to fetch the downloaded bytes, base64-encodes those bytes in a worker thread, sanitizes the filename, and returns the filename, encoded content, and byte size.

**Call relations**: Download tools call this after a page action is expected to save a file. It uses BuaSurface._open for browser control, BuaSurface._lease for access to the transport's download storage, contained_leaf to keep the filename safe, and a background thread for encoding work.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the browser session and lease at the end of a non-crashed turn. It also clears the stored token so future turns do not reconnect to a session that has already been released.

**Data flow**: It checks whether a browser session exists and closes it. Whether that succeeds or fails, it then closes the lease if one exists, clears the lease field, and stores a null token. The object is left without an active session or lease.

**Call relations**: The turn cleanup system registers and calls this when the turn ends normally. It calls BuaSurface._store_token to erase recovery state, while deliberately still releasing the lease even if closing the session has trouble.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab id value from tool arguments into an integer tab id, or returns nothing when the value is not useful. It avoids treating booleans as tab numbers even though booleans are technically integer-like in Python.

**Data flow**: It receives a JSON-style value. It returns null for booleans and unrecognized values, returns integers as-is, converts floats and non-empty strings to integers, and returns null for empty strings or missing-like values.

**Call relations**: BuaSurface.navigate uses this helper before asking BrowserSession to navigate. Its job is small but important: normalize user-facing tool input into the shape the session expects.

*Call graph*: called by 1 (navigate).


### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `per browser turn: open, tool calls, cleanup`

A browser session is like a control desk for Chrome. Chrome exposes a debugging interface called CDP, short for Chrome DevTools Protocol, which lets another program inspect pages, open tabs, listen for downloads, and run page actions. This file opens that connection, subscribes to the browser events the rest of the system cares about, and then offers simple methods such as navigate, read_page, find, form_input, computer, and wait_for_download.

The session owns the changing facts for one turn of work: which tabs exist, which downloads have started or finished, whether dialogs appeared, whether network activity is still settling, and which background tasks are running. Most detailed work is delegated to smaller helper objects called readers, such as BrowserTabs for tab work, BrowserContent for page text and structure, BrowserForms for form and upload work, and BrowserDownloads for download tracking. The session creates those helpers with itself as their shared state.

The important safety behavior is cleanup. If opening the browser connection fails halfway through, the session closes what it already opened. When the session closes normally, it tries to close its tabs, shuts down the CDP connection, cancels background tasks, and resets all stored state so the next turn starts cleanly.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the connection details it will use later. It also prepares the in-memory lists and trackers for tabs, downloads, dialogs, network settling, and background tasks.

**Data flow**: It receives an optional CDP endpoint, an optional model name, and a download folder path. It turns the model name into the coordinate size the model expects, stores the endpoint and download path, and initializes all session state to safe empty values. Nothing connects to Chrome yet.

**Call relations**: The backend creates this object before opening a browser turn. The constructor prepares the shared state that later helper objects, such as tab and download readers, will read and update.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Starts the session if it is not already open. It protects callers from accidentally opening the same session twice.

**Data flow**: It checks whether a CDP connection already exists. If not, it runs the bootstrap process; if bootstrap fails, it closes any partial setup and then passes the error upward.

**Call relations**: The async context-manager entry method uses this when code enters a browser session. It hands the real setup work to _bootstrap and relies on close for rollback if setup breaks.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the actual browser startup work: connects to Chrome, registers event listeners, enables downloads, discovers tabs, and creates the first blank tab. Without this setup, later browser tools would have no live connection or tab to act on.

**Data flow**: It reads the stored CDP endpoint, resolves it into a WebSocket URL, opens the CDP connection, asks Chrome for version information, and uses that to note whether the browser is on macOS. It then attaches event callbacks for downloads, tabs, network activity, page loading, dialogs, and paused download requests. Finally, it turns on target discovery, creates an about:blank tab, attaches to it, and stores it as the current tab list.

**Call relations**: open calls this as the main setup step. During setup it creates tab, download, and dialog helper objects so their callback methods can be registered with the CDP connection.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts the session down and wipes its temporary state. This prevents old tabs, downloads, dialogs, or background tasks from leaking into the next browser turn.

**Data flow**: If there is a live connection, it tries to close each known Chrome tab, ignoring common errors during shutdown, and then closes the CDP connection. Whether or not that succeeds, it clears the connection, tab list, frame-session map, downloads, dialog list, scroll flag, settle tracker, tab events, and background tasks.

**Call relations**: The async context-manager exit method uses this for normal cleanup, and open uses it as emergency cleanup after a failed bootstrap. It also creates fresh tracking objects so the next open starts from a clean slate.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets callers use BrowserSession with Python's async with pattern, which means 'open this resource, use it, then clean it up automatically.'

**Data flow**: It receives the session object, opens the browser connection, and returns the ready-to-use session.

**Call relations**: Code that wants automatic setup and teardown enters through this method. It delegates startup to open.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Completes the async with pattern by closing the browser session when the protected block ends.

**Data flow**: It receives any exception information from the surrounding block, but does not inspect it. It closes the session so browser state and background tasks are cleaned up.

**Call relations**: This is called automatically after code using async with finishes. It delegates all cleanup work to close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection, or gives a clear error if the browser has not been opened. This keeps other parts of the system from silently trying to use a missing connection.

**Data flow**: It checks the stored connection field. If the field is empty, it raises BrowserUnavailable; otherwise, it returns the connection object.

**Call relations**: Helper objects can call this when they need to talk to Chrome. It acts as a guardrail between session setup and lower-level browser commands.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts a background asynchronous job and remembers it so it can be cancelled during cleanup. This is useful for browser-related work that must continue while the main tool call proceeds.

**Data flow**: It receives a coroutine, turns it into an asyncio task, stores the task in the session's background-task set, and arranges for the task to remove itself from the set when it finishes.

**Call relations**: Other session helpers can use this when they need side work to run in parallel. close later cancels any remembered tasks so they do not outlive the session.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Answers whether a browser frame belongs to the main page rather than an embedded frame. A frame is a page area; embedded frames are often ads, widgets, or cross-site content inside the main page.

**Data flow**: It receives an optional CDP session id and frame id, creates a tab helper, and asks that helper to decide whether the frame is top-level.

**Call relations**: This is a small forwarding method. The session keeps the shared state, while BrowserTabs contains the frame-identification logic.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so the rest of the browser tooling can observe and control it correctly.

**Data flow**: It receives a CDP session id, creates a tab helper, and asks it to perform the session initialization steps.

**Call relations**: Tab-related event flows call into this path when Chrome exposes a new attached session. BrowserTabs performs the detailed setup using the session's connection and state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be treated as the current working page, optionally by tab id. This gives higher-level tools a consistent way to pick where actions should happen.

**Data flow**: It receives an optional tab id, creates a tab helper, and asks it to find or choose the matching Tab object. The chosen Tab is returned.

**Call relations**: Browser tools that need a target page use this route. The session delegates tab selection rules to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a browser tab to a new URL. This is the session-level entry for moving the browser to a different page.

**Data flow**: It receives a URL and an optional tab id. It creates a tab helper, asks it to navigate the chosen tab, and returns the result data from that operation.

**Call relations**: Higher-level browser commands call this when a user or agent asks to visit a page. BrowserTabs does the CDP command work and updates tab-related state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a small information summary for a specific tab. This helps callers describe or report the current browser tab.

**Data flow**: It receives a Tab object, creates a tab helper, and asks that helper to turn the tab into a JSON-style dictionary of information.

**Call relations**: This is part of the session's tab tool surface. BrowserTabs owns the details of what tab information should include.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about the currently known tabs, such as what tabs exist and which one matters now. This gives page-reading or UI code a snapshot of the browser workspace.

**Data flow**: It creates a tab helper, which reads the session's tab list and related state, then returns a JSON-style dictionary describing that state.

**Call relations**: Callers use this when they need a browser-wide tab summary. The session provides access, and BrowserTabs builds the actual summary.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of the current tabs. Titles are useful as a simple human-readable overview of what is open.

**Data flow**: It creates a tab helper, which reads the known tabs and asks Chrome as needed, then returns a list of title strings.

**Call relations**: This belongs to the tab-facing API of the session. BrowserTabs performs the actual title lookup.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper for tab and target work. A target is Chrome's term for something inspectable, such as a tab or page.

**Data flow**: It uses the current session and the fixed viewport size to construct a BrowserTabs object. The returned helper can read and update the session's tab-related state.

**Call relations**: Many session methods call this whenever they need tab behavior. _bootstrap also uses it to attach event listeners and create the first tab.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page structure and element references. It supplies the helper with limits such as viewport size and maximum frame nesting depth.

**Data flow**: It combines the current session, the fixed viewport size, and the maximum frame depth into a new BrowserPage object. That helper is returned for reference resolution and coordinate lookup.

**Call relations**: resolve_ref and ref_point call this when they need to turn a page reference into a concrete frame, node, or point on the screen.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading page content, such as page text, accessibility-style trees, and search results.

**Data flow**: It wraps the current session in a BrowserContent object and returns it. The helper can then use the session's connection and tab state to inspect the page.

**Call relations**: The session's tree, read_page, get_page_text, and find methods all use this helper to do the content-specific work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for tracking and waiting on downloads. It includes the maximum wait time so download waits do not hang forever.

**Data flow**: It combines the current session and the configured wait limit into a BrowserDownloads object. The helper reads and updates the session's download list.

**Call relations**: _bootstrap uses this helper's methods as CDP download event callbacks. wait_for_download also uses it when a caller wants to wait for a completed download report.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for recording JavaScript dialogs, such as alerts or prompts, that pages open.

**Data flow**: It wraps the current session in a BrowserDialogs object and returns it. The helper can append dialog text or state to the session's dialog list when browser events arrive.

**Call relations**: _bootstrap creates this helper so its dialog callback can be registered with the CDP connection.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form actions, including typing into inputs and preparing file uploads.

**Data flow**: It wraps the current session in a BrowserForms object and returns it. That helper uses session state and the browser connection to interact with form fields.

**Call relations**: upload_file, attached_sizes, and form_input call this helper when the requested tool action concerns forms or upload controls.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript in the page. JavaScript is the scripting language pages use in the browser.

**Data flow**: It wraps the current session in a BrowserRuntime object and returns it. The helper can then send runtime commands through the CDP connection.

**Call relations**: eval_js and call_on use this helper to evaluate code or call functions inside the browser page.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, using about:blank if no URL is supplied. This is the session-level tab-opening tool.

**Data flow**: It receives a URL, creates a tab helper, asks it to create the tab, and returns a JSON-style result describing the new tab.

**Call relations**: External browser tool callers can use this to open tabs. BrowserTabs performs the actual Chrome target creation and session-state update.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one or more browser tabs according to the caller's arguments. This lets the tool surface remove unwanted pages.

**Data flow**: It receives a JSON-style argument dictionary, creates a tab helper, and asks it to interpret the arguments and close the matching tab or tabs. It returns the helper's result dictionary.

**Call relations**: This is the public session wrapper for tab closing. BrowserTabs contains the detailed rules for selecting and closing targets.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Prepares or performs a file upload through a page's file input control. This is needed because browser automation must tell Chrome which local file path to attach.

**Data flow**: It receives upload arguments, creates a form helper, and passes the arguments to that helper. The helper returns a JSON-style result describing the upload action.

**Call relations**: The backend's upload-settling flow calls this when a tool request includes a file upload. BrowserForms does the form-specific browser interaction.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports sizes for files attached during an upload flow. This helps the backend confirm or describe what was attached.

**Data flow**: It receives upload-related arguments, creates a form helper, and asks it to compute or retrieve the attached file sizes. It returns a list of integer sizes.

**Call relations**: The backend's upload-settling flow uses this after upload work. The session delegates the details to BrowserForms.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree of page content, optionally filtered by type. This gives callers a compact view of what is on the page.

**Data flow**: It receives a JSON-style argument dictionary and a filter type, creates a content helper, and asks it to build the tree string. The string is returned to the caller.

**Call relations**: This is part of the page-reading tool surface. BrowserContent performs the actual page inspection and formatting.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result for a caller. This is the main way higher-level code asks, 'What is on this page right now?'

**Data flow**: It receives page-reading arguments, creates a content helper, and passes the request through. The helper returns a JSON-style dictionary with the page reading result.

**Call relations**: Browser tool callers use this through the session. BrowserContent handles the page-specific reading work.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts plain text from the page. This is useful when the caller wants readable words rather than a full page structure.

**Data flow**: It receives arguments describing what text to get, creates a content helper, and asks it for the text result. The result comes back as a JSON-style dictionary.

**Call relations**: This is a convenience path over BrowserContent. The session provides the shared browser state, while BrowserContent extracts the text.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within the page content and can use a completion helper to support follow-up search behavior. It lets callers locate text or page elements without reading everything manually.

**Data flow**: It receives search arguments and an optional FindCompleter, creates a content helper, and passes both along. The helper returns a JSON-style search result.

**Call relations**: The session exposes this as the find tool. BrowserContent performs the actual searching and uses the completer when supplied.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types into or otherwise fills a form field on the page. This is the session-level entry point for text entry in forms.

**Data flow**: It receives form input arguments, creates a form helper, and asks it to perform the input action. The helper returns a JSON-style result.

**Call relations**: Tool callers use this when they need to fill fields. BrowserForms owns the details of finding the field and sending the browser commands.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a more general computer-style browser action, such as screen-based interaction. It gives the system a way to act on the browser using viewport coordinates and a bounded wait time.

**Data flow**: It receives an argument dictionary, creates a BrowserComputer with the current session, viewport, and maximum wait time, then runs it. The result is returned as a JSON-style dictionary.

**Call relations**: This method directly constructs the computer-action helper for each call. The helper performs the detailed interaction while the session supplies shared browser state.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to finish and returns its download record. It reports the completed download rather than reading the file bytes itself.

**Data flow**: It receives wait arguments, creates a download helper, and asks it to wait using the session's download event history. When a matching download completes, a BrowserDownload object is returned.

**Call relations**: Callers use this after an action that should trigger a download. BrowserDownloads uses the events registered during _bootstrap to know when downloads begin and finish.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a specific browser session. This is a low-level escape hatch for asking the page runtime a direct question.

**Data flow**: It receives a CDP session id and a JavaScript expression string, creates a runtime helper, and asks it to evaluate the expression. The returned JSON-like value is passed back.

**Call relations**: Higher-level page and form helpers can use this when they need script-level information. BrowserRuntime sends the actual runtime command to Chrome.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on a specific page object. This is used when the system already has a browser-side object reference and wants to ask that object to do or return something.

**Data flow**: It receives a CDP session id, a browser object id, a function body or name, and optional argument values. It creates a runtime helper, passes those details through, and returns the resulting JSON-style dictionary.

**Call relations**: This is the session wrapper for object-specific runtime calls. BrowserRuntime handles the exact CDP message needed to call the function.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame node and index it points to. A reference is a compact label used by the browser tools to refer back to an element or frame found earlier.

**Data flow**: It receives a Tab object and a reference string, creates a page helper, and asks it to resolve the reference. The result is a FrameNode plus an integer index.

**Call relations**: Callers use this before acting on a referenced page item. BrowserPage contains the reference-parsing and lookup logic.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the screen point for a referenced page item. This is needed before clicking or otherwise interacting at a coordinate.

**Data flow**: It receives a Tab object and a reference string, creates a page helper, and asks it to compute the point. It returns an x and y coordinate pair.

**Call relations**: Screen-based actions use this kind of lookup to turn a human-readable page reference into a concrete place in the viewport. BrowserPage performs the coordinate calculation.

*Call graph*: calls 1 internal fn (page_reader).


### Live browser control
These files translate high-level automation requests into tab navigation and concrete browser actions.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file exists so the rest of the system can ask for ordinary actions in human terms and have them happen inside a browser tab. Without it, a command such as “click this point” or “type this text” would not know how to become the exact mouse, keyboard, and page commands that Chrome understands.

The main class, BrowserComputer, receives a batch of requested actions. It validates them, adjusts coordinates from the model's screen size to the real browser viewport, performs the actions one by one, waits for the page to settle, then returns a fresh screenshot and a text summary of what happened. Think of it like a remote-control operator: it hears “click there,” moves the pointer, presses and releases the button, waits for the page to react, then reports back with a photo.

The file also adds safety and helpful feedback. It warns if the agent keeps scrolling instead of reading the page, warns before sign-in-like pages, reports new downloads, and explains a browser limitation around native dropdown menus. It can also mark the last click on the returned screenshot with a blue dot, which helps later steps understand exactly where the action landed.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This protocol method describes how a browser session must provide the tab that should receive actions. It is a promise that BrowserComputer can ask for a current tab, or a specific tab by id.

**Data flow**: It receives an optional tab id → the real session implementation finds or creates the matching browser tab object → it returns a tab with a browser session id and keyboard state.

**Call relations**: BrowserComputer.run calls this at the start of a batch so every later click, key press, scroll, and screenshot knows which tab to affect.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This protocol method gives access to the browser control connection. That connection is used to send low-level Chrome DevTools Protocol commands, which are messages that tell the browser to do things like capture a screenshot or dispatch a mouse event.

**Data flow**: It takes no extra input → the session supplies its active browser connection → callers use that connection to send browser commands.

**Call relations**: BrowserComputer.run, BrowserComputer.act, BrowserComputer._select_reminder, and the event helpers rely on this connection whenever they need to talk directly to the browser.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This protocol method describes how to get useful information about a tab for the response returned to the caller. It lets BrowserComputer include current tab details along with the action summary and screenshot.

**Data flow**: It receives a tab object → the real session implementation reads current tab metadata → it returns a JSON-style dictionary of tab information.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the final response describes the tab's latest state.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This protocol method describes how to list the titles of open browser tabs. BrowserComputer uses those titles to spot sign-in or registration pages and remind the agent to ask the user first.

**Data flow**: It takes no input → the session reads titles from the browser's tabs → it returns a list of title strings.

**Call relations**: BrowserComputer.run calls this after performing actions, then passes the titles to sign_in_warning to decide whether to add a safety reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This protocol method describes how to run a small JavaScript function on a specific browser object. In this file it is used to inspect a clicked native dropdown menu.

**Data flow**: It receives a browser session id, an object id, JavaScript code, and optional arguments → the session runs that code against the object in the browser → it returns the result as a JSON-style dictionary.

**Call relations**: BrowserComputer._select_reminder calls this after finding a clicked select element, so it can read the dropdown's options and build a helpful reminder.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This protocol method describes how to turn a page reference, such as a short element id returned by page-reading tools, into the browser node needed for direct action. It lets actions target page elements without relying only on screen coordinates.

**Data flow**: It receives the current tab and a reference string → the session looks up the matching browser node and backend node id → it returns both so browser commands can target that element.

**Call relations**: BrowserComputer.act uses this for scroll_to actions, where the browser is asked to bring a referenced element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This protocol method describes how to find a clickable screen point for a referenced page element. It lets commands say “click element e123” instead of manually supplying x and y coordinates.

**Data flow**: It receives the current tab and a reference string → the session finds the element's on-screen position → it returns viewport coordinates for the point to use.

**Call relations**: BrowserComputer.point calls this whenever an action carries a ref, and BrowserComputer.act then uses the returned point for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for executing a batch of browser actions. It validates the requested actions, performs them, waits for page reactions, captures a screenshot, and returns a clear report.

**Data flow**: It receives a JSON-style request containing a tab id and an actions list → it selects the tab, validates and adjusts the actions, runs them in batches, gathers warnings, downloads, tab titles, and dialogs, captures a screenshot, optionally marks the last click, and converts the last click back into model coordinates → it returns tab info, a text output, the last click location, and the screenshot as base64 text.

**Call relations**: This function coordinates the whole file. It calls int_or_none to read the tab id, asks the browser session for a page, uses fixup_actions and split_at_waits before calling BrowserComputer.act for each action, calls BrowserComputer._select_reminder after clicks, waits for settling, calls sign_in_warning, and uses BrowserComputer._to_model and mark_click when preparing the final response.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This function performs one browser action. It is the dispatcher that turns an action name, such as left_click, type, key, scroll, or screenshot, into the exact lower-level operation.

**Data flow**: It receives a tab and one validated ComputerAction → it finds the target point if needed, checks required fields, converts coordinates when necessary, sends mouse or keyboard commands, sleeps for waits, scrolls elements into view, or simply records that a screenshot was requested → it returns a short human-readable message and, for pointer actions, the final viewport point.

**Call relations**: BrowserComputer.run calls this for every action in the batch. Depending on the action, it hands work to BrowserComputer.point, BrowserComputer._click, BrowserComputer._drag, BrowserComputer._scroll, BrowserComputer._dispatch, BrowserComputer._to_viewport, BrowserComputer._to_model, require_point, and require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This function figures out where on the browser screen an action should happen. It supports both element references and raw coordinates.

**Data flow**: It receives a tab and an action → if the action has a ref, it asks the browser session for that element's point; if it has a coordinate, it converts the model coordinate to viewport coordinates; if neither is present, it returns nothing → the caller gets either a usable screen point or None.

**Call relations**: BrowserComputer.act calls this before actions that may need a location. It uses BrowserComputer._to_viewport for raw coordinates and the session's ref_point method for element references.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts coordinates from the model's screen space into the real browser viewport space. This matters because the agent may think in one screen size while the browser is actually displayed at another size.

**Data flow**: It receives an x,y pair in model coordinates → it scales and maps that point using the configured model size and viewport size → it returns the matching x,y pair in viewport coordinates.

**Call relations**: BrowserComputer.point uses it for coordinate-based actions, and BrowserComputer.act uses it for drag starts and default scroll positions.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts real browser viewport coordinates back into the model's coordinate system. It is used when reporting actions back in the coordinate language the caller expects.

**Data flow**: It receives an x,y pair in viewport coordinates → it maps that point back through the viewport and model sizes → it returns an x,y pair in model coordinates.

**Call relations**: BrowserComputer.act uses it to describe clicks and drags in the output text, and BrowserComputer.run uses it to return last_click in model coordinates.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This function detects when a click landed on a native HTML select dropdown and prepares a reminder about the right way to change it. Native dropdown options often cannot be clicked through this browser-control path, so the system suggests using form_input instead.

**Data flow**: It receives a tab and the clicked viewport point → it asks the browser which element is under that point, walks up to see whether it is inside a select element, reads the first options and total option count, and tries to find a stable element reference → it returns a reminder string, or None if no usable select element is found.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It talks to the browser connection, uses the session's call_on method to inspect the element, and passes the collected details to select_reminder.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This helper sends a sequence of keyboard-related browser commands. It is used after other helpers have translated text or a key combination into low-level calls.

**Data flow**: It receives a tab and a list of browser command calls → it sends each command to the browser connection for that tab's session → it returns nothing, but the browser receives the keyboard input.

**Call relations**: BrowserComputer.act calls this for type and key actions after type_text or press_combo has prepared the actual command list.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This small helper sends one mouse event to the browser. It keeps click, drag, and scroll code from repeating the same browser connection call.

**Data flow**: It receives a tab and a dictionary of mouse event details, such as event type, button, position, and wheel movement → it sends an Input.dispatchMouseEvent command to the browser → it returns nothing, but the browser receives that mouse event.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build complete pointer actions from individual low-level events.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This function performs a mouse click at a browser viewport point. It supports left, right, double, and triple clicks by sending the right press-and-release sequence.

**Data flow**: It receives a tab, x and y coordinates, a mouse button name, and a click count → it reads currently pressed keyboard modifiers, moves the mouse to the point, then sends one or more mouse press and release events → it returns nothing, but the page receives the click sequence.

**Call relations**: BrowserComputer.act calls this for click actions. This function uses modifiers_mask so held keys like Shift or Ctrl are included, and it sends each low-level event through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This function performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop when they see motion along the way, not a single jump.

**Data flow**: It receives a tab, starting coordinates, and ending coordinates → it reads pressed keyboard modifiers, moves to the start, presses the left mouse button, moves through intermediate points, then releases at the end → it returns nothing, but the page receives a realistic drag gesture.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It sends all movement, press, and release events through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This function sends a mouse-wheel scroll at a specific point in the browser viewport. The point matters because web pages can have nested scrollable areas, and the scroll should affect the area under the pointer.

**Data flow**: It receives a tab, x and y coordinates, and horizontal and vertical scroll distances → it reads pressed keyboard modifiers, moves the mouse to the point, then sends a wheel event with those distances → it returns nothing, but the page scrolls if possible.

**Call relations**: BrowserComputer.act calls this for scroll actions after choosing the scroll point and calculating the scroll distance. It uses BrowserComputer._mouse_event for both the pointer move and wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This helper decides whether the current tab titles suggest a sign-in, login, or registration page. If so, it returns a safety reminder that the user should confirm before signing in.

**Data flow**: It receives a list of tab title strings → it lowercases them and checks for sign-in-related keywords → it returns the warning text if any title matches, otherwise None.

**Call relations**: BrowserComputer.run calls this after reading tab titles, then appends the warning to the final output if needed.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This helper builds a clear message explaining how to change a native dropdown. It includes some available options so the caller knows what values can be used.

**Data flow**: It receives an optional element reference, a list of option labels, and the total number of options → it formats the visible options, notes if more exist, and chooses instructions based on whether a ref is available → it returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after gathering dropdown details from the browser.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This function draws a small blue marker on a screenshot at the last click location. It makes the returned screenshot easier to interpret by showing exactly where the pointer action landed.

**Data flow**: It receives a base64-encoded screenshot and a viewport point → it decodes the image, draws a translucent blue circle at the point, saves the image again as a JPEG, and encodes it back to base64 → it returns the marked screenshot text.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click or drag endpoint to show. It is run in an executor so image processing does not block the main asynchronous event loop.

*Call graph*: 7 external calls (Draw, b64decode, b64encode, alpha_composite, new, open, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This helper reads a value that might represent an integer tab id. It accepts integers, floats, and non-empty strings, and treats anything else as missing.

**Data flow**: It receives a JSON-style value or None → it converts an int, float, or non-empty string into an integer → it returns that integer, or None if the value is not usable.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page, so the optional tab_id field is normalized.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that an action has a target point when one is required. It turns a missing coordinate or reference into a clear validation error.

**Data flow**: It receives a possible point and the action name → if the point exists, it returns it unchanged; if not, it raises a ValidationError explaining that the action needs a coordinate or ref → callers either get safe coordinates or an immediate error.

**Call relations**: BrowserComputer.act calls this before click and drag-end actions, where proceeding without a point would be meaningless.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that a required coordinate field is present. It is used for values that cannot be inferred from an element reference.

**Data flow**: It receives a possible coordinate and the field path name → if the coordinate exists, it returns it unchanged; if not, it raises a ValidationError naming the missing field → callers either get the needed coordinate or a clear error.

**Call relations**: BrowserComputer.act calls this for the starting coordinate of a drag action before converting it into viewport coordinates.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`orchestration` · `request handling and browser event processing`

This file is the tab controller for the browser automation layer. It sits between higher-level commands, such as “open this URL” or “close that tab,” and the Chrome DevTools Protocol, often called CDP, which is Chrome’s remote-control API. Think of it like the receptionist for a browser: it keeps the guest list of open tabs, notices when new tabs appear or disappear, and makes sure each tab is properly connected before anyone tries to use it.

The file stores each tab as a small `Tab` object with its browser target ID, its CDP session ID, keyboard state, and frame tracking information. `BrowserTabs` then provides the real behavior. It listens to browser events about targets and page loading, attaches to new tabs, enables the browser features needed for automation, applies the requested viewport size, and keeps its local list of tabs in sync with the actual browser.

Navigation is careful rather than blind. A URL is normalized, special words like `back` and `forward` are treated as history actions, and normal URLs are sent to the browser. After navigation, the code waits for settling signals, such as loading and painting events, so later automation is less likely to act on a half-ready page. It also has a special case for downloads, because a navigation may become a file download instead of a visible page.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It leaves special commands and already-complete URLs alone, and adds `https://` when someone gives a bare site name like `example.com`.

**Data flow**: It receives a text URL or command. It checks whether the text is one of the built-in navigation words, whether it already starts with a URL scheme such as `http:`, or whether it needs a default secure web prefix. It returns the final string that should be passed to navigation.

**Call relations**: BrowserTabs.navigate calls this before deciding how to move the tab. Its result tells navigation whether to go backward, forward, stay with `about:blank`, or load a real web address.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one browser tab. It stores the IDs needed to talk to the browser tab and prepares per-tab state such as keyboard status and frame tracking.

**Data flow**: It receives a browser target ID and a CDP session ID. It saves them, creates a fresh keyboard state, starts an empty frame sequence table, and creates a root frame reference for the tab. The result is a ready-to-use `Tab` object.

**Call relations**: BrowserTabs.attach_tab calls this after the browser has accepted a connection to a target. The new object is then stored in the session’s tab list so later actions can find and use that tab.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when frame IDs from the browser are long or hard to present.

**Data flow**: It receives a frame ID. If the frame is new, it assigns the next available number and remembers it; if the frame was already seen, it reuses the existing number. It returns that frame’s number.

**Call relations**: No direct caller is shown in this file’s function facts, but it belongs to the `Tab` state used by the broader page and frame tracking system.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the promise that a CDP connection can send a command to the browser and return the browser’s answer. This is a protocol method, meaning this file describes what another object must provide rather than implementing it here.

**Data flow**: A caller provides a CDP method name, optional parameters, and optionally a session ID for a specific tab. The implementing connection sends that command to the browser and returns a dictionary-like JSON response.

**Call relations**: BrowserTabs relies on this ability throughout tab setup, navigation, history movement, and closing. The real connection object comes from BrowserTabSession.connection.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how code can start waiting for one or more browser events before an action happens. This helps avoid missing important events that arrive quickly.

**Data flow**: A caller gives event names and optionally a session ID. The implementing connection creates and returns a future, which is a placeholder for a result that will arrive later when the event is seen.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this pattern before sending navigation commands, so they can wait for the browser’s follow-up event.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how code waits for a previously expected browser event, with a time limit. The timeout prevents the automation from hanging forever if the browser never sends the event.

**Data flow**: It receives a future created by `expect` and a timeout in seconds. The implementing connection waits until the event result arrives or the timeout is reached, then returns the event data or raises an error.

**Call relations**: BrowserTabs._goto uses it while waiting for document content to load. BrowserTabs._history_step uses it while waiting for history navigation to be confirmed.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how the tab controller obtains the browser connection used to send CDP commands. It keeps `BrowserTabs` independent from the concrete connection implementation.

**Data flow**: It reads the surrounding browser session object and returns an object that can send commands, expect events, and wait for them.

**Call relations**: BrowserTabs calls this whenever it needs to talk to the browser, including attaching tabs, creating tabs, navigating, and closing tabs.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how the tab controller gets access to download tracking. This matters because a navigation can turn into a file download instead of a normal page load.

**Data flow**: It reads the surrounding browser session object and returns a download helper. That helper can compare the download list before and after a navigation attempt.

**Call relations**: BrowserTabs._goto uses this when Chrome reports a navigation error, to decide whether the “error” was actually an expected download.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how code can run JavaScript inside a tab and get the result. In this file, it is used to ask the page for simple facts like its current URL and title.

**Data flow**: It receives a tab session ID and a JavaScript expression. The implementation runs that expression in the tab and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info calls this to build human-friendly tab summaries for navigation results, tab lists, and title lists.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed when automation started. This prevents old tabs from being mistaken for newly opened tabs later.

**Data flow**: It receives a browser response containing target information. It extracts each target ID and stores the set as `initial_targets` in the session’s tab event state.

**Call relations**: This is part of the target-event bookkeeping used by BrowserTabs.on_target_created. Once initial targets are known, later creation events can be filtered accurately.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when Chrome reports a new browser target, and records it if it is a new page tab. It ignores non-page targets and targets that were already present at startup.

**Data flow**: It receives event parameters and an optional session ID. It reads the `targetInfo`, checks that it describes a page with a string target ID, and appends that ID to the created-target queue if it is genuinely new. It changes the session’s tab event state but returns nothing.

**Call relations**: This event callback feeds BrowserTabs.sync. Later, sync consumes the queued target IDs and attaches to them as real `Tab` objects.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when Chrome reports that a browser target has gone away. It marks the target as destroyed so the local tab list can be cleaned up.

**Data flow**: It receives event parameters and an optional session ID. If the event contains a string target ID, it adds that ID to the destroyed-target set. It returns nothing.

**Call relations**: BrowserTabs.sync later reads this destroyed-target set and removes matching tabs from the local tab list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. It deliberately ignores child frames, such as embedded iframes, so the whole tab is not treated as newly loading for every small embedded page.

**Data flow**: It receives loading event parameters and a session ID. If there is no session, it does nothing. If the event’s frame ID belongs to the top-level frame for that tab, it tells the settle tracker that this session is loading.

**Call relations**: It calls BrowserTabs.is_top_level_frame to filter the event. The settle tracker later uses this loading state when BrowserTabs.navigate waits for the page to become ready.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having reached the DOM content stage, meaning the page’s basic document structure is ready. This is one of the signals used to decide that a page is becoming usable.

**Data flow**: It receives event parameters and a session ID. If a session ID is present, it marks that session as loaded in the settle tracker. It returns nothing.

**Call relations**: This callback contributes to the readiness information consumed after BrowserTabs.navigate sends a navigation command.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when Chrome reports an important visual lifecycle event for the main frame. Painted means the browser has drawn meaningful page content, not just received data.

**Data flow**: It receives lifecycle event parameters and a session ID. It ignores events without a session, events whose names are not in the accepted paint-event list, and events from non-top-level frames. For a matching event, it marks the session as painted in the settle tracker.

**Call relations**: It calls BrowserTabs.is_top_level_frame for the main-frame check. BrowserTabs.navigate later waits on the settle tracker so automation does not rush ahead before the page is visibly updated.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser event belongs to the main frame of a known tab. This helps separate whole-page events from events inside embedded frames.

**Data flow**: It receives a session ID and a frame ID. It compares them with the stored tabs, where the tab’s target ID represents its top-level frame. It returns true if any open tab matches both values, otherwise false.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating page-settling state. That keeps those callbacks focused on the page the user thinks of as the tab.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the automation system to an existing browser target and prepares it for use as a tab. This is the setup step that turns a raw browser target ID into a usable `Tab` object.

**Data flow**: It receives a target ID. It asks Chrome to attach to that target, extracts the new session ID, enables needed page features through BrowserTabs.init_session, turns on document-fetch monitoring, applies the configured viewport size, and returns a new `Tab` object.

**Call relations**: BrowserTabs.create, BrowserTabs.page, and BrowserTabs.sync call this when they need to add a usable tab. It calls BrowserTabs.init_session before constructing the `Tab`, because the browser session must be enabled before normal automation can rely on it.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser domains needed for tab automation. A browser domain is a group of CDP features, such as page events, document structure, or network activity.

**Data flow**: It receives a session ID. It sends commands to enable page events, lifecycle events, DOM access, and network events for that session. It does not return a value; it changes what events and commands are available for the tab.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target. All later tab work depends on these capabilities being enabled.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Reconciles the local tab list with browser events that said tabs were created or destroyed. It keeps the program’s memory of tabs aligned with the real browser.

**Data flow**: It reads the queued destroyed targets and created targets from the session’s tab event state. It removes tabs whose targets are gone, then attaches to each newly created target unless it is already known or attachment fails. It updates the session’s tab list.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before they rely on the current tab list. When a new target must become usable, sync hands it to BrowserTabs.attach_tab.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the requested tab, creating a blank one if no tabs are open. It is the safe doorway for code that needs a tab to act on.

**Data flow**: It receives an optional tab ID. It first runs BrowserTabs.sync, then creates and attaches a blank tab if the tab list is empty. If no tab ID was requested, it returns the most recently opened tab; if an ID was requested, it validates the range and returns that tab or raises a clear validation error.

**Call relations**: BrowserTabs.navigate calls this to find the tab to move, and BrowserTabs.close calls it to find the tab to close. It calls BrowserTabs.attach_tab if it has to create the first tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new location, including normal URLs and the special history commands `back` and `forward`. It also waits for the page to settle before reporting the result.

**Data flow**: It receives a URL-like string and an optional tab ID. It gets the tab, normalizes the destination, resets the settle tracker, performs either a history step or a normal navigation, waits for the page readiness signals, and returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create calls this after opening a new tab with a requested URL. Internally it uses normalize_url, BrowserTabs.page, BrowserTabs._history_step, BrowserTabs._goto, and BrowserTabs.tab_info to complete the full navigation story.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs a normal browser navigation to a specific URL. It watches for page-load confirmation and distinguishes real navigation failures from navigations that became downloads.

**Data flow**: It receives a `Tab` and a final URL. It records the current number of downloads, starts waiting for the DOM-content event, sends `Page.navigate`, checks Chrome’s response, and either cancels the wait, accepts a download case, raises an error, or waits for the load event to arrive. It returns nothing when navigation has been started and confirmed enough.

**Call relations**: BrowserTabs.navigate calls this for ordinary destinations after normalize_url has ruled out `back` and `forward`. If Chrome reports an error, it consults BrowserTabSession.download_reader to see whether a new download explains the result.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab one step backward or forward in its browser history if that step exists. If the requested step would go beyond the available history, it quietly does nothing.

**Data flow**: It receives a `Tab` and a step number, usually -1 for back or 1 for forward. It asks Chrome for navigation history, checks the current index, calculates the desired index, starts waiting for a navigation event, tells Chrome to navigate to that history entry, and waits for confirmation. It returns nothing.

**Call relations**: BrowserTabs.navigate calls this when the normalized destination is `back` or `forward`. It uses browser history data from Chrome and waits for either a frame navigation or an in-document navigation event.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. This gives callers a small, friendly summary of where the tab ended up.

**Data flow**: It receives a `Tab`. It runs a short JavaScript expression inside that tab to read `location.href` and `document.title`, validates the returned shape, and returns a dictionary with string `url` and `title` fields.

**Call relations**: BrowserTabs.navigate uses this for its final result. BrowserTabs.tabs_context and BrowserTabs.tab_titles use it when building tab summaries.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to show or reason about the browser’s current tab state. It marks the last tab in the list as the active one.

**Data flow**: It first syncs local tab state with browser events. Then it walks through each tab, reads its URL and title with BrowserTabs.tab_info, adds its numeric ID and active flag, and returns a dictionary containing the current tab ID and the tab list.

**Call relations**: BrowserTabs.close calls this after closing a tab so it can return the updated browser state. It calls BrowserTabs.sync first to avoid reporting stale tabs.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns only the titles of the currently known tabs. This is a compact view for code that does not need full URL and tab ID details.

**Data flow**: It reads the current tab list, asks BrowserTabs.tab_info for each tab, extracts the title field, replaces missing titles with an empty string, and returns the list of title strings.

**Call relations**: No caller is shown in the provided facts, but it reuses BrowserTabs.tab_info so title reading stays consistent with the rest of the tab summaries.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new tab and optionally navigates it to a requested URL. It returns the new tab’s ID along with its final URL and title.

**Data flow**: It receives a URL, defaulting to `about:blank`. It asks Chrome to create a blank target, attaches to that target, stores the new tab, navigates that tab to the requested URL, and returns the tab’s index plus the navigation result.

**Call relations**: It calls BrowserTabs.attach_tab to prepare the new target, then BrowserTabs.navigate to load the requested destination. BrowserTabs.navigate may in turn call the lower-level navigation helpers.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one browser tab and returns the updated list of tabs. It accepts flexible tab ID input from a JSON-like argument object.

**Data flow**: It receives an argument dictionary. It converts the `tab_id` value with `_tab_id`, finds the tab with BrowserTabs.page, tells Chrome to close that target, removes the tab from the local list, clears out-of-process frame session tracking, and returns the fresh tab context.

**Call relations**: It calls BrowserTabs.page to resolve which tab should close and BrowserTabs.tabs_context to report the state afterward. It uses _tab_id to make incoming JSON values usable as a Python tab index.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a JSON-style tab ID value into either an integer tab index or `None`. This lets callers pass tab IDs as numbers or strings while still giving the rest of the code one simple type.

**Data flow**: It receives a value that may be an integer, float, string, or something else. Integers are returned directly, floats and non-empty strings are converted to integers, and missing or unsupported values become `None`.

**Call relations**: BrowserTabs.close calls this before asking BrowserTabs.page for the tab to close. Returning `None` means “use the default tab selection” rather than a specific index.

*Call graph*: called by 1 (close).


### Action side effects
These files handle common side effects of browser work, including downloads, form filling, keyboard input, and waiting for pages to settle.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download waiting`

A browser automation agent often needs the actual file a website offers, not just what Chrome shows on screen. This file solves that problem for downloads. It listens to browser events from Chrome’s DevTools Protocol, which is Chrome’s control channel for automation. When Chrome pauses a network request, this code must release it again; otherwise the page would hang like traffic stopped at a red light forever. Most requests are simply allowed to continue. But if the request is a top-level document with a content type such as PDF, the code rewrites the response headers so Chrome treats it as an attachment. In plain terms, it nudges Chrome to download the file instead of opening it in the built-in PDF viewer, which the agent may not be able to read well.

The file also records download lifecycle events. When a download starts, it stores a small record with Chrome’s download ID, the suggested filename, and its current state. When progress events arrive, it updates that state. Other code can then ask whether a navigation turned into a download, or wait until the latest download completes. This file does not read the downloaded bytes from disk; it only tracks what Chrome reports and tells the browser when to continue paused requests.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the browser connection’s ability to send a command to Chrome. Code in this file uses it to tell Chrome to continue paused network requests or responses.

**Data flow**: It receives a command name, optional command data, and an optional browser session ID. The real implementation sends that command to Chrome and returns Chrome’s JSON-like reply.

**Call relations**: BrowserDownloads._continue_request and BrowserDownloads._continue_response rely on this method after BrowserDownloads.on_fetch_paused decides that a paused request must be released.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This protocol method describes how to get the browser’s command connection. It is the doorway this file uses to talk back to Chrome.

**Data flow**: It takes the current browser session object and returns an object that can send Chrome DevTools Protocol commands. It does not transform download data itself.

**Call relations**: The continuation helpers call this method before sending Fetch.continueRequest or Fetch.continueResponse, so paused browser traffic can resume.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This protocol method describes how to start an asynchronous task without blocking the current event handler. It lets this file quickly return from browser event callbacks while still releasing paused requests.

**Data flow**: It receives a coroutine, which is a piece of asynchronous work waiting to run. The browser session schedules that work in the background and does not return a download result.

**Call relations**: BrowserDownloads.on_fetch_paused uses this to run _continue_request or _continue_response after deciding how a paused browser request should continue.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This protocol method answers whether a browser event belongs to the main page frame rather than an embedded frame, such as an iframe. That distinction matters because embedded PDFs should keep rendering inside the page.

**Data flow**: It receives a session ID and a frame ID from Chrome’s event data. It returns true when that frame is the main page frame for the session, and false otherwise.

**Call relations**: BrowserDownloads.on_fetch_paused asks this before forcing a PDF to download, so only main-page PDF navigations are rewritten.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request or response. Its main job is to make sure the request is always released, while optionally turning a top-level PDF response into a download.

**Data flow**: It reads Chrome’s paused-request details, including the request ID, response status, headers, and frame ID. If there is no valid request ID, it does nothing. If this is only the request stage, it schedules a simple continue action. If it is a response, it checks whether the content type is one that should be forced to download, then schedules a response continue action with or without rewritten headers.

**Call relations**: This is the decision point for paused fetch events. It calls _content_type to understand the response headers, asks the browser session whether the event belongs to the top-level frame, and hands off the actual Chrome command to _continue_request or _continue_response through spawn_background.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This asynchronous helper tells Chrome to continue a paused request when no response rewrite is needed. Without this, the page could stay stuck waiting for a request that was paused by automation.

**Data flow**: It receives the browser session ID and Chrome request ID. It sends a Fetch.continueRequest command through the browser connection. If Chrome rejects the command, times out, or the runtime is no longer available, it logs a warning instead of crashing the whole flow.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this helper when Chrome paused a request before response information was available. It is one of the two release paths for paused browser traffic.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This asynchronous helper tells Chrome to continue a paused response, optionally changing its headers so the browser downloads it as a file. This is how top-level PDFs are steered away from Chrome’s viewer.

**Data flow**: It receives the session ID, request ID, response status code, response headers, and a true-or-false force flag. If forcing is requested, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, which tells browsers to save the response as a file. It then sends Fetch.continueResponse to Chrome. Failures are logged as warnings.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this helper after it has inspected the response. It performs the final handoff back to Chrome, either preserving the response as-is or rewriting it into a download.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a new download. It creates a simple download entry so later code can track whether that download finishes.

**Data flow**: It reads Chrome’s download event data, especially the download GUID and suggested filename. It appends a new Download record to the browser session’s download list with the state set to inProgress. It does not return a value.

**Call relations**: Chrome download-start events call into this function. Later, on_download_progress updates the same record, and wait or became_download can observe that the list has changed.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the stored state of a download when Chrome reports progress. It is how the file knows a download has moved from running to completed or another final state.

**Data flow**: It reads the download GUID and state from Chrome’s event data. It searches the browser session’s stored downloads for the matching GUID and replaces that record’s state with the new state. It returns nothing.

**Call relations**: This function follows on_download_begin in the download lifecycle. BrowserDownloads.wait depends on these state updates to know when a download is completed.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This function gives the browser a short grace period to see whether a navigation turned into a download. It is useful because a click or navigation may not immediately show up as a download event.

**Data flow**: It receives the number of downloads that existed before an action. For up to a short fixed time, it repeatedly checks whether the browser session’s download list has grown. It returns true if a new download appears, or false if the grace period expires.

**Call relations**: Other navigation code can call this after starting an action that might become a download. It watches the download list populated by on_download_begin.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This asynchronous function waits until at least one browser download has completed, then returns the most recent completed download record. It lets callers pause until Chrome says the file is ready.

**Data flow**: It reads an optional timeout value from the input arguments and converts it with float_or_default. Until the deadline, it checks the browser session’s download list for records whose state is completed. If one or more are found, it returns the latest completed record. If time runs out, it raises TimeoutError.

**Call relations**: This function is the waiting endpoint for code that needs a completed download. It relies on on_download_begin to create records, on_download_progress to mark them completed, and float_or_default to safely interpret the caller’s timeout.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns a user-supplied timeout value into a floating-point number, or falls back to a default when no value was supplied. It keeps timeout parsing in one predictable place.

**Data flow**: It receives a JSON-like value and a default number. Numbers are converted to float, non-empty strings are parsed as floats, and None becomes the default. Any other kind of value causes a ValidationError that tells the caller the value must be numeric.

**Call relations**: BrowserDownloads.wait calls this before it starts waiting, so the rest of the waiting logic can work with one clean numeric timeout.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper extracts the response’s content type from HTTP headers. The content type tells the code whether the response is a PDF or another kind of document.

**Data flow**: It receives a list of header-like JSON values. It looks for a dictionary whose name is Content-Type, ignores any extra details after a semicolon, trims spaces, and lowercases the result. If no content type is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this when deciding whether a top-level response should be forced into a download.

*Call graph*: called by 1 (on_fetch_paused).


### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling during browser automation actions`

Web pages do not just store form values as text. A checkbox has a checked state, a file picker has hidden browser-owned files, and many sites listen for “input” and “change” events before they react. This file is the bridge between a high-level automation request and those real browser behaviors.

The main class, BrowserForms, receives a browser session object. Given a page tab and a page element reference, it finds the real browser node behind that reference, then either uploads files, writes a value, or checks what file sizes are currently attached. It uses the Chrome DevTools Protocol, often shortened to CDP, which is a browser control API. Think of CDP as a backstage intercom to the browser: instead of clicking by hand, the code asks the browser directly to resolve an element or set files on an input.

The file is careful about bad references. If the automation tries to upload to something that is not a file input, or tries to edit an element reference that no longer exists, it raises a HallucinationError. In plain terms, that means “the caller is acting on something the current page does not actually support; look at the page again and use a real reference.”

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the session contract that BrowserForms expects. It returns the browser page, optionally for a specific tab, so form actions know where to look.

**Data flow**: It receives an optional tab number. The concrete browser session uses that to find the matching page object and returns it to the caller.

**Call relations**: BrowserForms methods call this first when they need to work on a form field. It gives them the page context before they resolve an element reference.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session contract for getting the low-level browser connection. BrowserForms uses it to send direct CDP commands to the browser.

**Data flow**: It takes no extra input. The concrete session returns a Cdp connection object, which can send browser commands and receive replies.

**Call relations**: After BrowserForms has a page and an element reference, it asks the session for this connection so it can resolve browser nodes or set files on an input.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the session contract for running a small JavaScript function on a specific page element. BrowserForms uses it when the browser itself must inspect or update the element.

**Data flow**: It receives a browser session id, an element object id, JavaScript code, and optional argument values. The concrete session runs that JavaScript against the element and returns the resulting JSON-like dictionary.

**Call relations**: BrowserForms.attached_sizes uses this to ask a file input what file sizes it is holding. BrowserForms.input uses it to set a field value and fire page events so the site notices the change.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the session contract for turning a page reference string into the browser’s actual element identity. BrowserForms depends on this because outside callers use friendly refs, while CDP needs internal node ids.

**Data flow**: It receives a page object and a reference string from a page read. The concrete session looks up that reference and returns both the frame/session node information and the browser backend node id.

**Call relations**: Each BrowserForms action calls this after loading the page. The returned node and backend id are then handed to CDP commands so the browser acts on the exact element.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks whether a file input really has files attached, and reports their byte sizes. It is useful because an uploaded filename alone does not prove the browser has received the file contents yet.

**Data flow**: It reads a JSON-like argument dictionary containing a tab id and an element ref. It converts the tab id, gets the page, resolves the ref to a real browser node, asks CDP for a JavaScript object for that node, runs a small script that reads this.files, and returns a list of numeric file sizes. If the browser reply does not contain a proper list, it returns an empty list.

**Call relations**: This method starts by using _tab_id to normalize the tab value and uses the wire helpers to read typed values from the incoming arguments and CDP reply. It then relies on the browser session’s page, resolve_ref, connection, and call_on abilities to move from an outside ref to real browser file information.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a web page file input. It is the automation equivalent of choosing files in a file picker.

**Data flow**: It reads a tab id, an element ref, and a files list from the input dictionary. It normalizes and checks those values, resolves the ref to a real browser node, then sends a CDP command telling the browser to set that file input’s files. On success it returns the ref and the file paths it used. If the ref is not a real file input, it raises HallucinationError with advice to re-read the page and use a file input ref.

**Call relations**: This method uses _tab_id to interpret the optional tab id and wire helpers to validate the incoming ref and files list. It hands the resolved backend node id to the browser connection using the DOM.setFileInputFiles command; if CDP rejects that command, it translates the low-level failure into a clearer caller-facing HallucinationError.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This writes a value into a form-like element, such as a text box, checkbox, radio button, select box, or editable content area. It also triggers the browser events that many websites need before they notice the change.

**Data flow**: It reads a tab id, element ref, and value from the input dictionary. It gets the page, resolves the ref, asks CDP to turn the backend node into a JavaScript object, then runs a script on that object. The script chooses the right kind of update: checked state for checkboxes and radio buttons, selected value for selects, text content for editable areas, or value for ordinary inputs. It then dispatches input and change events and returns the browser’s reported value.

**Call relations**: This method uses _tab_id and wire helpers to turn outside JSON values into safe internal values. It depends on the browser session to resolve refs and run JavaScript on the target element. If CDP cannot resolve the element, it raises HallucinationError so the caller knows the page reference is stale or invalid.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns an optional tab id from incoming JSON into either an integer tab number or None. It lets callers pass the tab id as a number or a non-empty string.

**Data flow**: It receives a JSON value that might be an integer, float, string, or something else. Integers pass through, floats are converted to integers, non-empty strings are parsed as integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input all call this before asking the browser session for a page. It keeps tab selection rules in one place so each form action behaves the same way.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

Browser automation cannot just say “type A” and expect every web page to react correctly. Many pages listen for detailed keyboard events: the physical key code, the visible character, whether Shift is held, whether the key is on the number pad, and so on. This file builds those detailed event messages for a US keyboard layout, using data copied from Playwright so the behavior matches a well-tested browser automation tool.

The file is like a translator between everyday keyboard language and Chrome’s low-level input language. It knows that “KeyA” normally means “a”, but with Shift it means “A”. It knows aliases such as “cmd” meaning Meta, and “esc” meaning Escape. It also knows special macOS editing commands, such as Command+A meaning select all, and includes them when needed.

A small KeyboardState object records which modifier keys and physical keys are currently down. The main helpers then create Chrome DevTools Protocol calls: key_down creates a press event, key_up creates a release event, press_combo presses several keys in order and releases them in reverse, and type_text types short text key by key. For long text, it uses a direct text insertion call instead, because that is faster and avoids sending many separate key events.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: This function expands the raw US keyboard table into a lookup table that is easier to use while typing. It lets the rest of the file look up a key by physical code, visible character, shifted character, or common alias.

**Data flow**: It starts with the detailed keyboard layout definitions. For each key, it creates a KeyDescription that says what Chrome should be told: the key name, code, text, location, and numeric key codes. If the key has a Shift version, it creates that too. The result is a larger dictionary where many different input names point to the right key description.

**Call relations**: This runs when the module is loaded to create LAYOUT_CLOSURE. Later, _description_for depends on that prepared lookup table whenever key_down, key_up, or type_text needs to understand a requested key.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: This function converts the set of held modifier keys into the number format Chrome expects. Modifier keys are keys like Shift, Control, Alt, and Meta.

**Data flow**: It receives a set such as {"Shift", "Control"}. It checks the fixed bit value for each known modifier and adds together the ones that are present. It returns one integer that represents the whole modifier state.

**Call relations**: key_down and key_up call this just before sending their event data. It is the final step that turns the file’s easy-to-read modifier set into Chrome’s compact protocol value.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: This function decides exactly what a requested key means right now, given the current keyboard state. It is where Shift and other held modifier keys change the meaning of a key press.

**Data flow**: It receives the current KeyboardState and a key name or character. It looks up that key in LAYOUT_CLOSURE. If the key is unknown, it raises a ValidationError so the caller knows the request was invalid. If Shift is held and the key has a shifted form, it uses the shifted version. If other modifiers are held, it removes typed text so shortcuts like Ctrl+C do not also type the letter “c”. It returns the final KeyDescription.

**Call relations**: key_down and key_up both call this before building Chrome event messages. It is the shared interpreter that keeps press and release events talking about the same physical key.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: This function finds the special macOS editing command names that should travel with a keyboard shortcut. These commands help Chrome on macOS treat shortcuts like Command+A or Option+Backspace the way native Mac text fields do.

**Data flow**: It receives a physical key code and the set of currently held modifiers. It builds a shortcut name such as "Shift+Meta+ArrowLeft", looks that up in the macOS editing-command table, removes the trailing colon from command names, and filters out insert commands. It returns a list of command strings for Chrome, or an empty list if there is no special command.

**Call relations**: key_down calls this only when the target platform is macOS. The commands it returns are added to the key-down event sent to Chrome.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: This function creates the Chrome message for pressing a key down. It also updates the remembered keyboard state so later events know the key is being held.

**Data flow**: It receives a KeyboardState, a requested key, and whether the browser is on macOS. It asks _description_for what the key means, checks whether the same physical key was already down to mark auto-repeat, records the key as pressed, and records a modifier if the key is Shift, Control, Alt, or Meta. On macOS it also asks _mac_commands for native editing commands. It returns one Chrome DevTools Protocol call named "Input.dispatchKeyEvent" with all the fields Chrome expects.

**Call relations**: press_combo uses this to press each key in a shortcut, and type_text uses it when a character can be typed as a normal keyboard key. Inside, it relies on _description_for, _mac_commands, and modifiers_mask to build a complete and correct event.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: This function creates the Chrome message for releasing a key. It also updates the remembered keyboard state so the system no longer thinks that key is held.

**Data flow**: It receives a KeyboardState and a requested key. It looks up the current key description, removes the key from the pressed-key set, removes it from the modifier set if it is a modifier, and returns one "Input.dispatchKeyEvent" call of type "keyUp".

**Call relations**: press_combo uses this to release shortcut keys in reverse order, and type_text uses it after each per-character key press. It shares _description_for and modifiers_mask with key_down so release events use the same naming and modifier rules.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This function turns a shortcut written as text, such as "Ctrl+Shift+P", into the full sequence of key-down and key-up events. It is useful when automation wants to trigger a browser or page shortcut rather than type plain text.

**Data flow**: It receives the current KeyboardState, the combo string, and whether the browser is on macOS. It splits the combo on plus signs, trims spaces, converts friendly aliases like "ctrl" to "Control", and rejects an empty combo with a ValidationError. It presses the keys in the order given, then releases them in reverse order, and returns the full list of Chrome protocol calls.

**Call relations**: This is a higher-level helper built from key_down and key_up. It hands each individual press and release to those lower-level functions so keyboard state, modifier masks, and macOS commands stay consistent.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This function turns text into browser input events. It chooses between realistic key-by-key typing for short text and faster direct insertion for long text.

**Data flow**: It receives a KeyboardState, the text to enter, and whether the browser is on macOS. If the text is longer than the file’s character limit, it returns a single "Input.insertText" call containing the whole text. Otherwise it walks through each character. Characters found in the keyboard layout become a key_down followed by a key_up; characters not found in the layout become direct insertText calls. The result is a list of Chrome protocol calls.

**Call relations**: For short, ordinary text, this function delegates to key_down and key_up so web pages can see real keyboard events, which matters for autocomplete and key handlers. For long or unusual text, it hands Chrome a direct text insertion request instead.

*Call graph*: calls 2 internal fn (key_down, key_up).


### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `action handling`

Browser automation often needs to click, type, or navigate, then wait until the page is ready before doing the next thing. Waiting is tricky: if it waits for every network request, modern pages with ads, analytics, or live feeds may never look “done.” If it does not wait enough, the next step may run before the page has reacted.

This file solves that by keeping a small scoreboard for only the important consequences of the current action. It tracks foreground network requests, page loading, and paint events. A paint event means the browser has drawn visible content on the screen. That is treated as a strong signal that the page is usable, but the code still gives important follow-up requests a short grace period so it does not declare an empty shell ready too early.

The Settle class is the main piece. Browser event handlers tell it when useful requests start and finish, when a page starts or stops loading, and when the page paints. Then wait asks it to pause until the action’s visible and foreground work has quieted down, or until a safety time limit is reached. The result is a practical middle ground: fast on pages that keep background traffic running forever, but patient enough for real content triggered by the user’s action.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: This function decides whether a browser network request is worth waiting for. It filters out requests that are usually background noise, such as images, fonts, very-low-priority preloads, and common analytics services.

**Data flow**: It receives a dictionary of request details from Chrome DevTools Protocol, which is Chrome’s control-and-observation interface for automation. It reads the request type, priority, and URL host. If the request looks passive or analytics-related, it returns false; otherwise it returns true so the settling logic will count it as important work.

**Call relations**: Settle.on_request_started calls this before adding a request to the pending set. That means only requests judged useful by this function can delay the automation’s next step.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: This creates a fresh settling tracker. It starts with no pending requests, no painted sessions, and no loading pages recorded.

**Data flow**: It takes no outside data beyond the new object being created. It sets up four pieces of memory: pending important requests, a count of tracked requests that have started, sessions currently loading, and sessions that have painted.

**Call relations**: BrowserSession.__init__ creates one when a browser session starts. BrowserSession.close also creates one, which gives the session a clean tracker state during shutdown or reset-style cleanup.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: This clears the tracker before starting a new settling window. It lets the next wait focus only on the work caused by the next action, not leftovers from a previous one.

**Data flow**: It reads the current sets and counters stored on the Settle object, then empties pending requests and painted sessions and sets the started-request count back to zero. It leaves the loading set intact, so an already-loading session can still be recognized.

**Call relations**: No direct caller is shown in the supplied graph. Conceptually, it is the public reset button used before measuring the consequences of a new browser action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that an important network request has begun. It is used so wait can pause until that request finishes.

**Data flow**: It receives Chrome event details and an optional browser session id. It pulls out the request id, checks that the session and request id are valid, and asks tracks_request whether this request matters. If so, it stores the pair of session id and request id in pending and increments the started counter.

**Call relations**: This is the entry point for request-start events into the settling scoreboard. It relies on tracks_request to avoid counting background noise, and the stored request is later removed by Settle.on_request_finished.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that a network request is no longer pending. It is used to let wait know that one piece of foreground page work has completed.

**Data flow**: It receives Chrome event details and an optional session id. It reads the request id, and if both identifiers are valid, removes that session/request pair from the pending set. If the request was not tracked, removing it has no effect.

**Call relations**: This is the counterpart to Settle.on_request_started. Together they keep the pending-request set accurate, which Settle.wait uses when deciding whether the page has become quiet enough.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: This notes that a browser session is currently loading a document. It prevents the settling logic from moving on while a navigation is still in progress.

**Data flow**: It receives a session id and adds it to the loading set. After that, waits for this session will treat the page as still busy until the id is removed.

**Call relations**: No direct caller is shown in the supplied graph. It is designed to be called from page lifecycle or loading events before Settle.wait makes its readiness decision.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: This notes that a browser session has finished loading. It allows the settling logic to stop treating that session as actively navigating.

**Data flow**: It receives a session id and removes it from the loading set. If the id is not present, nothing changes.

**Call relations**: No direct caller is shown in the supplied graph. It pairs with Settle.mark_loading, and Settle.wait consults the loading set to decide whether it must keep waiting.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: This records that the browser has painted visible content for a session. Paint is important because it is often a better sign of usability than total network silence.

**Data flow**: It receives a session id and adds it to the painted set. Later, Settle.wait sees this and switches to a short post-paint grace period instead of waiting for the full timeout.

**Call relations**: No direct caller is shown in the supplied graph. It feeds the main wait decision: once a session is marked painted, Settle.wait hands off to Settle._drain_after_paint.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: This is the main waiting routine. It pauses after an action until the page has dealt with the important work that action caused, or until a maximum time limit is reached.

**Data flow**: It receives a Chrome DevTools connection, a session id, and a time cap in seconds. First it asks the page to run one tiny queued task, so click or input handlers have a chance to start their requests. Then it watches the painted, loading, and pending-request state. It returns when the page is painted and briefly drained, when nothing important started, or when loading and tracked requests have been quiet long enough; if none of that happens, the cap stops the wait.

**Call relations**: This is the function other browser-action code would call after doing something in the page. It first calls Settle._flush_page_tasks to catch immediate follow-up work. If a paint is seen, it calls Settle._drain_after_paint to apply the shorter grace-period rule.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: This waits briefly after the page first paints. It gives real content requests a chance to finish, but avoids waiting forever on pages that keep background activity running.

**Data flow**: It receives a session id and an absolute deadline. It creates a shorter grace deadline, then repeatedly checks whether the session is still loading or has pending important requests. If things go quiet for a small extra gap, it returns; otherwise it returns when the grace time or overall deadline is reached.

**Call relations**: Settle.wait calls this whenever the session has painted. It is the special fast path for visible pages: after paint, the code waits only a small, capped amount instead of using the longer navigation cap.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: This gives the page one quick chance to run immediate JavaScript follow-ups before settling decisions are made. It helps catch requests started by click handlers, promise callbacks, or zero-delay timers.

**Data flow**: It receives a Chrome DevTools connection and a session id. It sends a small JavaScript promise to the page and waits for it to resolve. If that control message fails or times out, it simply sleeps for a short beat so the caller still gives the page a moment.

**Call relations**: Settle.wait calls this at the very beginning. It uses Cdp.send to talk to the browser; after it returns, request-start events caused immediately by the action should already have reached Settle.on_request_started.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Page understanding
These files turn live browser pages into readable text, structured content, and safe element matches for later actions.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is easy for a person to see, but a model needs a structured description: what controls exist, what they are called, where they are, and how to refer to them later. This file builds that description from Chrome's debugging interface, known as CDP (Chrome DevTools Protocol, the browser control API). It asks Chrome for two views of the page: the DOM snapshot, which gives element layout and geometry, and the accessibility tree, which gives meaningful roles like button, link, textbox, and heading. It then stitches those views together using Chrome's backend node IDs, which act like temporary element labels until the document navigates.

Frames matter here. A page can contain iframes, which are pages inside pages. Some are normal child documents; others run in separate browser targets. This file follows both kinds, gives each frame a prefix, and splices their accessibility trees into the parent so the final output reads like one page.

It offers two renderings. `render_page` produces an action-oriented tree with roles, names, references, center coordinates, and useful state like checked or disabled. `render_markdown` produces a cleaner reading view with headings, links, lists, paragraphs, and images. The `BrowserPage` class ties all of this to a browser session and can also turn a returned reference back into a screen point for clicking.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into its frame prefix and backend element ID. This is used to check whether a model-supplied reference is real and to find the right frame later.

**Data flow**: It receives a reference string → matches it against the expected reference pattern → returns the prefix and numeric backend ID, or returns nothing if the string does not fit.

**Call relations**: When rendering a subtree, `_PageRenderer.render` uses this to understand the requested starting reference. When an action needs to use a reference, `BrowserPage.resolve_ref` uses it first so invented or malformed references can be rejected.

*Call graph*: called by 2 (resolve_ref, render).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a CDP call: send a named browser command with optional parameters and optional session ID, then receive JSON back. It is a protocol method, meaning real connection objects must provide it.

**Data flow**: It takes a CDP method name, optional command parameters, and an optional browser session ID → the real implementation sends that command to Chrome → JSON response data comes back.

**Call relations**: This file's `fetch_target` depends on it to ask Chrome for snapshots, accessibility trees, metrics, and node details. Another browser component, `Settle._flush_page_tasks`, also calls this same interface when it needs browser communication.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Defines how a tab assigns a short sequence number to a frame ID. Those numbers become the `f1`, `f2`, and similar prefixes used in element references.

**Data flow**: It receives a browser frame ID → looks up or assigns that frame's sequence number in the tab implementation → returns the number used to build reference prefixes.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it discovers an out-of-process iframe and needs to give that iframe a stable prefix for rendered references.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how `BrowserPage` gets the active CDP connection for talking to the browser. It keeps this file independent from the concrete browser connection class.

**Data flow**: It reads the session object's stored browser connection → returns an object that can send CDP commands.

**Call relations**: `BrowserPage` methods use this hook whenever they need live browser data, such as taking snapshots, resolving points, or attaching iframe sessions.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step for a newly attached browser session. This is needed when an iframe lives in a separate browser target and must be prepared before snapshotting.

**Data flow**: It receives a new session ID → the concrete browser session performs whatever initialization is needed for that target → no direct value is returned.

**Call relations**: `BrowserPage._oop_session` calls this after attaching to an out-of-process iframe so later snapshot calls can use the new session safely.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely converts a JSON value into a floating-point number. It protects the snapshot parser from missing or oddly typed browser fields.

**Data flow**: It receives a value and a fallback default → if the value is an integer or decimal number, it converts it to `float`; otherwise it uses the default → returns the resulting number.

**Call relations**: `_parse_document` uses it for scroll offsets and layout bounds. `fetch_target` uses it to read the browser's device pixel ratio before geometry is normalized.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute from Chrome's compact snapshot format. It is used for details like input type, image source, and iframe source.

**Data flow**: It receives the shared string table, a packed attribute list, and an attribute name → walks key/value pairs in the packed list → returns the matching attribute value or nothing.

**Call relations**: `_parse_document` calls this while scanning DOM nodes so useful attributes can be copied into each node's geometry record.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Turns one raw Chrome document snapshot into a simpler record of node IDs, element geometry, attributes, and iframe links. It is the first cleanup step after Chrome returns its dense snapshot data.

**Data flow**: It receives one document snapshot, Chrome's shared string table, and the device pixel ratio → validates and decodes node IDs, scroll offsets, bounds, cursor styles, and selected attributes → returns a `_RawDoc` with geometry and child-document clues.

**Call relations**: `parse_snapshot` calls this once for each document inside the DOM snapshot. It relies on `_float` for numeric fields, `_attr` for HTML attributes, and wire validation helpers to reject malformed data.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Combines all documents from a DOM snapshot and turns local element positions into page-level positions. This is what lets iframe content be placed correctly on the main page.

**Data flow**: It receives the full DOM snapshot, device pixel ratio, and starting origin → parses each document, follows parent-to-child document links through iframe nodes, accumulates iframe offsets, filters ignored extension iframes, and separates normal iframe children from out-of-process iframe placeholders → returns a list of `DocData` records.

**Call relations**: `fetch_target` calls this after capturing the browser's DOM snapshot. It delegates the per-document decoding to `_parse_document` and hands normalized document data back to the accessibility-tree joining step.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Captures one browser target, meaning a top page or iframe session, and builds a `FrameSnapshot` tree for the documents inside that target. It joins layout information with accessibility information.

**Data flow**: It receives a CDP connection, a session ID, frame-prefix rules, and an origin point → enables needed CDP domains, captures DOM layout, reads device pixel ratio, parses geometry, asks Chrome for each frame's full accessibility tree, and links child frames under iframe nodes → returns the root `FrameSnapshot` for that target.

**Call relations**: `BrowserPage._snapshot_target` calls this for the main page and for out-of-process iframe sessions. Inside, it calls `Cdp.send`, `_float`, and `parse_snapshot`, then builds the frame data used by both renderers.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain text value from a Chrome accessibility field. Chrome wraps many accessibility values inside small objects, and this helper unwraps them.

**Data flow**: It receives a JSON value that may be an accessibility object → if it contains a `value`, it converts that to text; otherwise it returns an empty string → callers get a simple string.

**Call relations**: Both `_PageRenderer._render_node` and `_MarkdownRenderer._walk` use this to read roles and names before deciding what to display.

*Call graph*: called by 2 (_walk, _render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up a named accessibility property on a node, such as `checked`, `disabled`, `url`, or heading `level`. It hides Chrome's verbose property list format from the renderers.

**Data flow**: It receives an accessibility node and a property name → scans the node's property entries → returns the property's inner value when found, or nothing when absent.

**Call relations**: `_format_extras`, `_PageRenderer._render_node`, `_MarkdownRenderer._walk`, and `_MarkdownRenderer._render_content` call this when they need state or metadata to decide how to render a node.

*Call graph*: called by 4 (_render_content, _walk, _render_node, _format_extras); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node can be skipped in the action tree. Empty containers like generic groups often add noise without helping a model act.

**Data flow**: It receives an accessibility node, its role, and its name → checks whether it is one of the skippable roles, has no name, and has no important state → returns true when its children should be shown without showing the container itself.

**Call relations**: `_PageRenderer._render_node` calls this while deciding whether a node becomes a visible line or only serves as a pass-through to its children.

*Call graph*: called by 1 (_render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so rendered page descriptions stay readable and compact. It adds an ellipsis when text is cut.

**Data flow**: It receives text and a maximum length → leaves short text alone or trims long text to fit → returns the display-safe text.

**Call relations**: `_PageRenderer._render_node`, `_format_extras`, and `_image_name` use it to keep element names, values, and image filenames from overwhelming the output.

*Call graph*: called by 3 (_render_node, _format_extras, _image_name).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Guesses a useful image label from an image URL when the accessibility tree did not provide one. For example, it can turn `/assets/logo.png` into `logo.png`.

**Data flow**: It receives an optional image source URL → extracts the path and final filename → returns a truncated filename when it looks meaningful, or an empty string otherwise.

**Call relations**: `_PageRenderer._render_node` uses it for unnamed images in the action tree, and `_MarkdownRenderer._render_content` uses it for image entries in the reading view.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (_render_content, _render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the small state suffix shown after an element line, such as `checked=true`, `disabled`, `type="password"`, or a safe URL. These details help the model understand controls without reading raw browser data.

**Data flow**: It receives an accessibility node and optional geometry record → pulls selected properties and element-specific details, skips empty or unsafe values, truncates long values → returns either an empty string or a space-prefixed text suffix.

**Call relations**: `_PageRenderer._render_node` calls this when it has decided to print a node and needs to add useful state information to that line.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (_render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a reference prefix. This is how `f1e23` can be routed to frame `f1` instead of the main frame.

**Data flow**: It receives the root frame snapshot and a prefix → searches the root and child frames recursively → returns the matching frame snapshot or nothing.

**Call relations**: `_PageRenderer.render` uses this when a caller asks to render only the subtree under a specific reference.

*Call graph*: called by 1 (render).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node ID that corresponds to a backend DOM node ID. The backend ID appears in references, while rendering walks accessibility node IDs.

**Data flow**: It receives a frame snapshot and backend element ID → scans the frame's accessibility nodes for a matching `backendDOMNodeId` → returns the accessibility node ID or nothing.

**Call relations**: `_PageRenderer.render` calls this after `_frame_by_prefix` has found the correct frame, so rendering can start from the referenced element.

*Call graph*: called by 1 (render).


##### `_PageRenderer.render`  (lines 489–504)

```
def render(self, root: FrameSnapshot, ref: str | None) -> str | None
```

**Purpose**: Starts rendering the action-oriented page tree. It can render the whole page or only the part under one element reference.

**Data flow**: It receives a root frame snapshot and optional reference → if a reference is given, it parses it, finds the frame and node, and starts there; otherwise it starts at the root accessibility node → returns the collected lines joined as text, or nothing if the reference cannot be resolved.

**Call relations**: `render_page` creates a `_PageRenderer` and calls this. This method uses `split_ref`, `_frame_by_prefix`, and `_node_by_backend` for reference targeting, then hands the real tree walking to `_render_node`.

*Call graph*: calls 4 internal fn (_render_node, _frame_by_prefix, _node_by_backend, split_ref).


##### `_PageRenderer._coord_str`  (lines 506–510)

```
def _coord_str(self, geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element's center point as model-space coordinates. This gives the model an approximate place to click or look.

**Data flow**: It receives optional element geometry → if bounds exist, computes the center, scales it from browser viewport size to model coordinate size, and formats it as `(x=...,y=...)`; otherwise returns an empty string.

**Call relations**: `_PageRenderer._render_node` calls this while building each visible line in the page tree.

*Call graph*: called by 1 (_render_node).


##### `_PageRenderer._splice`  (lines 512–515)

```
def _splice(self, frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame's accessibility tree at the iframe element where it belongs. This makes iframe content appear in the rendered tree like part of the page.

**Data flow**: It receives a parent frame, an optional backend iframe ID, and the current depth → finds the child frame attached to that backend ID → if it has a root node, continues rendering from that child root.

**Call relations**: `_PageRenderer._descend` calls this after walking normal child accessibility nodes, so iframe documents are included at the correct spot.

*Call graph*: calls 1 internal fn (_render_node); called by 1 (_descend).


##### `_PageRenderer._render_node`  (lines 517–567)

```
def _render_node(self, frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Renders one accessibility node and then continues through its children. This is the core of the action tree renderer.

**Data flow**: It receives a frame, accessibility node ID, depth, and parent name → skips hidden, duplicate, too-deep, or already-visited nodes; decides whether the node is noise, offscreen, or relevant; builds a line with role, name, reference, coordinates, and state; then descends into children → appends text lines to the renderer.

**Call relations**: `_PageRenderer.render`, `_PageRenderer._descend`, and `_PageRenderer._splice` call this as the tree walk proceeds. It uses helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_image_name`, `_format_extras`, `_coord_str`, and `_truncate` to keep the output useful.

*Call graph*: calls 8 internal fn (_coord_str, _descend, _ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); called by 3 (_descend, _splice, render); 2 external calls (as_list, as_str).


##### `_PageRenderer._descend`  (lines 569–579)

```
def _descend(self, frame: FrameSnapshot, backend_id: int | None, child_ids: list[str], depth: int, parent_name: str) -> None
```

**Purpose**: Continues rendering from a node into its normal children and any iframe child. It is the renderer's simple tree-walking step.

**Data flow**: It receives the current frame, optional backend ID, child accessibility IDs, depth, and parent name → renders each child at the same chosen depth → then splices in an iframe child if one is attached to the backend ID.

**Call relations**: `_PageRenderer._render_node` calls this after either skipping a transparent node or printing a visible one. It calls `_render_node` for ordinary children and `_splice` for iframe content.

*Call graph*: calls 2 internal fn (_render_node, _splice); called by 1 (_render_node).


##### `render_page`  (lines 582–599)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Public helper that turns a `FrameSnapshot` into the action-oriented page text used by browser tools. It applies viewport scaling and filtering options before rendering.

**Data flow**: It receives a snapshot, viewport size, optional model size, filter type, depth limit, and optional reference → computes the coordinate scale → creates a `_PageRenderer` → returns the rendered tree text or nothing if a requested reference cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a fresh snapshot. It delegates the actual walking and line creation to `_PageRenderer.render`.

*Call graph*: called by 1 (tree); 2 external calls (__init__, effective_model_size).


##### `_MarkdownRenderer.render`  (lines 613–617)

```
def render(self, root: FrameSnapshot) -> str
```

**Purpose**: Starts rendering a frame snapshot as a reading-friendly Markdown document. This view is meant for understanding page content rather than choosing clickable targets.

**Data flow**: It receives the root frame snapshot → walks from the root accessibility node, gathers blocks and inline text, flushes any remaining paragraph text → returns Markdown blocks separated by blank lines.

**Call relations**: `render_markdown` creates a `_MarkdownRenderer` and calls this. It begins the traversal with `_walk` and uses `_flush` at the end to finish pending inline content.

*Call graph*: calls 2 internal fn (_flush, _walk).


##### `_MarkdownRenderer._emit`  (lines 619–622)

```
def _emit(self, text: str) -> None
```

**Purpose**: Adds one finished Markdown block, avoiding empty text and immediate duplicates. This keeps the reading view cleaner.

**Data flow**: It receives text → trims surrounding spaces → if the result is non-empty and not the same as the last block, appends it to the block list.

**Call relations**: `_flush` uses this to turn accumulated inline text into a paragraph. `_render_content` also uses it directly for headings, list items, blocks, and images.

*Call graph*: called by 2 (_flush, _render_content).


##### `_MarkdownRenderer._flush`  (lines 624–627)

```
def _flush(self) -> None
```

**Purpose**: Turns accumulated inline words into a paragraph block. It marks a boundary between flowing text and block-level content.

**Data flow**: It reads the renderer's current inline text list → joins it with spaces and emits it as one block → clears the inline list.

**Call relations**: `_MarkdownRenderer.render`, `_MarkdownRenderer._walk`, and `_MarkdownRenderer._render_content` call this whenever a paragraph should end before another block starts.

*Call graph*: calls 1 internal fn (_emit); called by 3 (_render_content, _walk, render).


##### `_MarkdownRenderer._walk`  (lines 629–649)

```
def _walk(self, frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree to collect reading content in order. It also splices iframe content into the same reading flow.

**Data flow**: It receives a frame, accessibility node ID, and parent name → skips already-seen or hidden nodes, reads role and name, renders the node's content, walks children, flushes around block roles, and enters child frames when present → updates the renderer's blocks and inline text.

**Call relations**: `_MarkdownRenderer.render` calls this at the root. During the walk it uses `_render_content` for each node's text decision and `_flush` where layout boundaries occur.

*Call graph*: calls 4 internal fn (_flush, _render_content, _ax_property, _ax_value); called by 1 (render); 2 external calls (as_list, as_str).


##### `_MarkdownRenderer._render_content`  (lines 651–681)

```
def _render_content(self, frame: FrameSnapshot, backend_id: int | None, role: str, name: str, node: JsonDict) -> None
```

**Purpose**: Decides how one accessibility node should appear in Markdown. It turns headings into `#` headings, links into Markdown links, list items into bullets, and images into image markers.

**Data flow**: It receives the frame, optional backend ID, role, name, and node data → checks the role and relevant properties such as heading level or URL → emits a block, appends inline text, or ignores noisy roles → updates the renderer's stored text.

**Call relations**: `_MarkdownRenderer._walk` calls this for each visible node. It uses `_ax_property` for extra details and `_image_name` when an image needs a fallback label.

*Call graph*: calls 4 internal fn (_emit, _flush, _ax_property, _image_name); called by 1 (_walk).


##### `render_markdown`  (lines 684–689)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Public helper that turns a `FrameSnapshot` into a Markdown reading view. It preserves page structure better than a flat text dump.

**Data flow**: It receives the root frame snapshot → creates a `_MarkdownRenderer` → returns the Markdown text produced from the accessibility tree.

**Call relations**: `BrowserPage.markdown` calls this after taking a fresh page snapshot.

*Call graph*: called by 1 (markdown); 1 external calls (__init__).


##### `BrowserPage.tree`  (lines 698–706)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the action-oriented page tree for a tab. This is the main path used when a caller wants to see clickable controls, references, coordinates, and state.

**Data flow**: It receives a tab, filter choice, and optional reference → captures a fresh snapshot of the tab → renders it with the current viewport and model size → returns the page tree text or nothing if a requested reference cannot be resolved.

**Call relations**: This method ties together `BrowserPage.snapshot` and `render_page`. It is the high-level entry for page inspection in this file.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 708–709)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading-oriented Markdown version of a tab. This is useful when the caller wants to read the page content rather than choose an element to act on.

**Data flow**: It receives a tab → captures a fresh snapshot → renders that snapshot as Markdown → returns the Markdown string.

**Call relations**: This method connects `BrowserPage.snapshot` with `render_markdown`, using the same frame-aware snapshot pipeline as the action tree.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 711–724)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Checks and resolves a browser reference before an action uses it. If the reference is malformed or no longer known, it raises a clear `HallucinationError`, meaning the model likely invented or used an old reference.

**Data flow**: It receives a tab and reference string → parses the reference into prefix and backend ID → looks up the frame for that prefix in the tab's registered frame map → returns the frame node and backend ID, or raises an error with guidance.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for an element's position. It uses `split_ref` to enforce the same reference grammar used by the renderer.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 726–752)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Turns an element reference into a screen point near the center of that element. This is used before actions like clicking or pointing.

**Data flow**: It receives a tab and element reference → resolves the reference to a frame and backend ID, scrolls the element into view, asks Chrome for its content quad or box model, averages the four corner points, adds the frame origin, and returns integer x/y coordinates. If Chrome cannot find the element, it raises a `HallucinationError` telling the caller to re-read the page.

**Call relations**: It starts with `BrowserPage.resolve_ref`, then uses the browser CDP connection to query layout. It uses `_coord_float_or_default` to safely read numeric coordinate values from Chrome's response.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 754–758)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Captures a complete, frame-aware snapshot of the tab and refreshes the reference-to-frame map. This is the common starting point for both page tree and Markdown rendering.

**Data flow**: It receives a tab → snapshots the main target from origin `(0, 0)` → clears the tab's old reference frame map → registers every frame in the new snapshot → returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates capture to `_snapshot_target` and registration to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 760–777)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Captures one target session and, if allowed by the frame-depth limit, attaches any separate iframe targets. It is the recursive driver for full-page snapshotting.

**Data flow**: It receives a tab, session ID, root prefix, origin, and current depth → calls `fetch_target` to snapshot that session → if depth allows, looks for out-of-process iframes and attaches them → returns the completed root frame snapshot for that target.

**Call relations**: `BrowserPage.snapshot` calls this for the main page. `_snapshot_oop` calls it again for out-of-process iframe sessions, while `_attach_oop_frames` is used to fill in those iframe children.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 779–787)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Finds iframe placeholders that Chrome did not include as normal child documents and tries to snapshot them through separate sessions. These are out-of-process iframes, meaning the browser runs them as separate targets.

**Data flow**: It receives a tab, root frame snapshot, and current depth → walks the frame tree → for each out-of-process iframe backend ID, asks `_snapshot_oop` for a child snapshot → inserts any successful child snapshot under the iframe node.

**Call relations**: `BrowserPage._snapshot_target` calls this after `fetch_target` completes. It hands each discovered iframe to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 789–815)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe if it can be described and attached. If anything fails, it logs the issue and leaves that iframe out rather than breaking the whole page snapshot.

**Data flow**: It receives the tab, parent frame snapshot, iframe backend ID, and depth → asks Chrome to describe the iframe node, extracts its frame ID, gets or creates a CDP session for that frame, computes the child origin from iframe bounds, and recursively snapshots the child target → returns the child `FrameSnapshot` or nothing.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each out-of-process iframe. It relies on `_oop_session` to get the session, `PageTab.frame_seq` for the frame prefix, and `_snapshot_target` to capture the child.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 817–820)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Builds the lookup table that maps reference prefixes to frame information. Without this, later actions could not know which browser session owns a reference.

**Data flow**: It receives a tab and a frame snapshot → stores the frame's ID, session ID, and origin under its prefix → repeats the same process for every child frame.

**Call relations**: `BrowserPage.snapshot` calls this after every fresh capture. `BrowserPage.resolve_ref` later reads the map this method populated.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 822–835)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing a cached one when possible. Attaching once and caching avoids repeating browser setup work.

**Data flow**: It receives a frame ID → checks the browser's cached iframe sessions → if absent, asks Chrome to attach to that target, initializes the new session, caches it, and returns the session ID; if attach fails, returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before trying to snapshot a separate iframe target. It uses `BrowserPageSession.init_session` after a successful attach so the session is ready.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 838–847)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts one coordinate value from Chrome into a float, accepting both numbers and numeric strings. It supplies a default for missing values and rejects invalid data.

**Data flow**: It receives a value and a default → returns the numeric float for integers, decimals, or non-empty strings; returns the default for `None`; raises a validation error for anything else.

**Call relations**: `BrowserPage.ref_point` uses this when turning Chrome's quad coordinates into the averaged center point of a referenced element.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file is a small service layer for reading browser content. A browser page can contain far more information than an assistant or API response should receive at once, so this code chooses the right page, asks a page reader for the content, and trims the answer to fixed size limits when needed.

The main class, BrowserContent, does not know how to control the browser directly. Instead, it depends on a BrowserContentSession, which is a promised interface for getting the current tab, reading that tab, and returning tab details. This is like a librarian asking another worker to fetch a book, then deciding which pages to copy for the customer.

It supports three main jobs. read_page returns an accessibility-style tree of page elements, optionally narrowed to all elements, interactive elements, or visible viewport elements. get_page_text returns a markdown-like plain text version of the page plus tab metadata. find searches the page tree for elements matching a user query. That search can be done with simple local matching, or with an optional language-model completer that reads a shortened tree and chooses matches.

A small helper, _tab_id, accepts tab IDs from JSON-like input and normalizes them into an integer or no tab ID.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a required method for any page reader that can turn a browser tab into a structured element tree. The tree is used when the system needs to inspect buttons, links, fields, or other page parts.

**Data flow**: It receives a browser tab, a filter choice such as all or interactive, and optionally a reference to a specific element. An implementation uses those inputs to build text describing the matching page elements. It returns that text, or returns nothing if a requested reference cannot be found.

**Call relations**: BrowserContent.tree relies on this promised method after it has selected the correct tab. The concrete browser integration supplies the real implementation, while this file only defines what BrowserContent expects from it.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a required method for any page reader that can turn a browser tab into readable page text. It is meant for cases where the caller wants the page content, not the detailed element tree.

**Data flow**: It receives a browser tab. An implementation reads the visible or meaningful page content and converts it into markdown-style text. It returns that text as a string.

**Call relations**: BrowserContent.get_page_text calls this through the session's page reader. The method is part of the contract that lets BrowserContent work without knowing the low-level browser-reading details.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is a required method for something that can provide a browser tab. It lets callers ask for either a specific tab by ID or the default current tab.

**Data flow**: It receives an optional tab ID. The implementation looks up the requested tab, or chooses the current one if no ID is supplied. It returns a PageTab object representing that browser tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text depend on this method before reading any content. It is the doorway from request arguments into the live browser state.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is a required method for getting the object that knows how to read page content. It separates browser session access from the actual page-to-text conversion.

**Data flow**: It takes no extra input beyond the session object. The implementation returns a page reader that can produce element trees and markdown text from tabs.

**Call relations**: BrowserContent.tree uses the returned reader to ask for a tree, and BrowserContent.get_page_text uses it to ask for markdown. This keeps BrowserContent focused on request flow and response shaping.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is a required method for returning useful details about a tab, such as information the caller may need alongside page text. It enriches content responses with context about where the text came from.

**Data flow**: It receives a tab-like object. The implementation reads metadata for that tab and returns it as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text calls this after reading the page text, then merges the tab information into the final response. The session implementation decides what tab details are available.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This method returns a structured text view of a browser page or of one referenced element on that page. It is the shared helper used by both page reading and finding.

**Data flow**: It receives JSON-style request arguments and a filter type. It extracts and normalizes the optional tab ID, reads an optional element reference, asks the browser session for the right tab, and then asks the page reader for a tree. It returns the tree text, or a clear message if the requested element reference was not found.

**Call relations**: BrowserContent.read_page calls this when a client asks to read page structure. BrowserContent.find calls it when it needs searchable page structure. Inside, it uses _tab_id to clean up the tab ID from incoming JSON-like data.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This method returns the page's structured element tree in a safe response size. It is useful when a caller wants to understand what controls and content are present on the page.

**Data flow**: It receives JSON-style arguments, reads the requested filter, and accepts only known filter values: all, interactive, or viewport. It asks BrowserContent.tree for the matching tree, cuts the returned text to the maximum read length, and returns both the shortened tree and a flag saying whether anything was cut off.

**Call relations**: This method is a public-facing content-reading path built on BrowserContent.tree. It validates the filter before handing off, so the lower-level tree reader receives a predictable filter choice.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This method returns a plain text version of the current or requested browser page. It is meant for reading page content in a more natural article-like form than the element tree.

**Data flow**: It receives JSON-style arguments, extracts and normalizes the optional tab ID, asks the browser session for that tab, and asks the page reader for markdown text. It trims the text to the maximum text length, records whether it was truncated, then adds tab information from the browser session to the response.

**Call relations**: This method uses _tab_id in the same way as BrowserContent.tree, but it follows the markdown-reading path instead of the tree-reading path. It also asks the session for tab metadata so the final answer includes both content and page context.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This method searches the page's element tree for things matching a user query. It can use simple built-in matching or, if supplied, a language-model completer to interpret the query more flexibly.

**Data flow**: It receives JSON-style arguments and optionally a completion function. It pulls out the query as a required string, reads the full page tree, and then chooses a search route. Without a completer, it parses matches directly from the tree. With a completer, it sends the query and a shortened tree to that completer, then resolves the reply back against the real tree. It returns the matches and a human-readable summary.

**Call relations**: This method calls BrowserContent.tree first because searching depends on the page structure. It then hands the result to either parse_tree_matches for local matching or resolve_find_reply after asking the optional completer. Finally, format_matches turns the match list into a summary for the caller.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This helper converts a tab ID from loose JSON-style input into a clean integer tab ID. If the input is missing or unusable, it returns no tab ID so the session can choose the default tab.

**Data flow**: It receives a JSON-like value. If the value is already an integer, it returns it. If it is a float or a non-empty string, it converts it to an integer. For anything else, it returns None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a page. It acts as a small adapter between external request data, where numbers may arrive in different shapes, and the browser session, which expects an integer tab ID or no ID.

*Call graph*: called by 2 (get_page_text, tree).


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

This file is the “find things on the page” helper for the browser extension. The browser side prints an accessibility tree as plain text, with lines such as a role, a visible name, a reference id, and optional screen coordinates. This file reads that text and extracts the useful parts.

It solves two related problems. First, it can do a simple local search: split the user’s query into words, scan the tree, and return entries whose tree line contains all those words. Second, it can clean up a reply from an AI model that was asked to choose matching elements. That cleanup matters because the AI is only allowed to point to references that really exist in the current tree. The code re-checks every suggested reference against the tree, fills in the true role, name, and coordinates from the tree itself, and drops unknown or repeated references. In other words, it treats the tree as the source of truth, not the AI’s memory.

The file also formats found matches into short readable lines for display. A cap of 20 results keeps replies manageable, like showing the first page of search results instead of flooding the user.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function turns the raw accessibility-tree text into a list of small records, one per usable element line. Each record contains the element reference, role, name, coordinates, and a lowercase copy of the original line for searching.

**Data flow**: It receives the full tree as one string. It reads the tree line by line, keeps only lines that look like element entries and contain a reference id, pulls out the role, optional name, reference, and optional x/y coordinates, then returns a list of dictionaries. If coordinates are missing, it uses "0,0" so later code always has something to show.

**Call relations**: This is the first parsing step used by both search paths. parse_tree_matches calls it before doing a simple word search, and resolve_find_reply calls it to build the trusted list of references that an AI reply must be checked against.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This small helper builds the standard result shape used by the rest of the file. It copies the trusted element details and adds a short reason explaining why it matched.

**Data flow**: It receives one parsed tree entry and a reason string. It selects the reference, role, name, and coordinates from the entry, attaches the reason, and returns a new dictionary shaped like a find result.

**Call relations**: parse_tree_matches uses it when local text searching finds a match. resolve_find_reply uses it after it has verified that an AI-suggested reference really appears in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree. It is useful when the query can be matched by plain words without needing an AI model to interpret it.

**Data flow**: It receives the tree text and the user’s query. It lowers the query, extracts simple letter-and-number search terms longer than one character, parses the tree into entries, and keeps entries whose lowercase tree line contains every search term. It returns up to 20 standardized match records with empty reasons.

**Call relations**: This function starts by asking tree_entries to turn tree text into searchable records. For each successful entry, it asks _match_payload to produce the standard result object. The provided call graph does not show who calls parse_tree_matches, but it is clearly designed as the local, non-AI matching path.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function turns an AI model’s find reply into safe match results. It protects the system from using made-up, stale, or duplicated element references by checking every suggested reference against the actual tree.

**Data flow**: It receives the AI reply text and the current tree text. It parses the tree into a lookup table by reference id, then reads the reply line by line. It ignores blank lines, stops on NO_MATCHES, notes whether the reply said MORE, extracts a reference from each result line, and keeps it only if that reference exists in the current tree and has not already been used. It returns the verified matches plus a true-or-false flag saying whether more matches may exist.

**Call relations**: This is the grounding step after an AI has been asked to find elements. It relies on tree_entries for the trusted current tree data and _match_payload to make the final result records. Its main job is to ensure later browser actions are based on real references from the page, not unchecked text from the AI.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns match records into human-readable text. It is used when the system needs to show found elements clearly, including their reference, role, name, coordinates, and optional reason.

**Data flow**: It receives a list of match dictionaries and an optional flag saying whether more matches exist. It builds one display line per match, adds the reason if present, optionally appends a note suggesting that the user refine the query, and returns the joined text.

**Call relations**: This sits at the output end of the find flow. After matches have been produced by local search or by resolving an AI reply, this function packages them into readable lines for whoever needs to display or return the result.


### Shared browser foundations
These files provide package markers, shared action and error definitions, coordinate conversion, dialog handling, JavaScript runtime access, and Chrome wire-message validation.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `import time`

This is the package doorway for the browser extension area. In Python, an `__init__.py` file is used to define a folder as an importable package, like putting a label on a drawer so the rest of the program can find what is inside. Here, the file does not run setup code or define functions. Its only content is a short description explaining the package’s purpose: it groups together tools for controlling or using a sandboxed browser/computer environment, plus the profile for a browser subagent. Without this file, depending on the Python packaging setup, other code might not be able to import this folder as a normal package, and newcomers would lose a simple signpost explaining what belongs here.


### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import time`

This is an empty Python package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly tells the system where the drawer is and that it can be opened by name.

Because this file has no code, it does not create objects, run setup steps, or change behavior directly. Its value is structural. Without it, depending on the Python version and import style, code elsewhere in the project might not be able to reliably import modules from `extensions.browser.ufo_ext_browser.bua`. That could make parts of the browser extension fail to load even though the real implementation lives in neighboring files.

So this file matters not because of what it executes, but because it helps organize the project into a clear package hierarchy.


### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is a data model for browser automation actions. In plain terms, it describes the shape of an instruction like “click this button,” “type this text,” or “scroll down one screen.” Without this file, different parts of the system could disagree about what an action looks like, which fields are allowed, or what values are safe.

It uses Pydantic, a Python library that checks and documents structured data. The `ActionType` list names every action the browser tool accepts. `CLICK_ACTIONS` groups the actions that are variations of clicking, which lets other code quickly ask, “Is this a click?”

`ScrollParameters` describes how to scroll: the direction and the amount. The amount can be a number of screen heights, from 0 to 5, or the special value `"max"`, meaning jump as far as possible.

`ComputerAction` is the main instruction form. It contains the requested action plus the extra details that action may need: a screen coordinate, a text string, a keyboard shortcut, scroll settings, a wait duration, drag start and end points, or an element reference from page-reading tools. Think of it like a standardized order slip for a browser robot: each action type uses the parts of the slip that apply to it.


### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `request handling`

Browser automation with a vision model has a simple but important problem: the model may not see the browser at the same size as the actual browser window. For example, a large screenshot may be shrunk before it reaches Claude, and Gemini may describe points on a fixed 0-to-1000 grid instead of real image pixels. If the system used those model coordinates directly, a click could miss the intended button, like using a map drawn at one scale to navigate a street at another scale without converting distances.

This file defines two small value objects, Size for width and height and Coord for x and y. It then provides the conversion math that keeps the model's view and the browser's real viewport lined up.

First, it can calculate the largest screenshot size that Claude-family models can receive without extra server-side shrinking. It respects both a maximum long edge and a maximum total pixel count. Next, it decides the model's working coordinate space: either a provided override, Gemini's fixed 1000 by 1000 grid, or the computed screenshot size. Finally, it converts points in both directions: from model space to browser viewport pixels for actions like clicking, and from browser pixels back to model space when reporting or comparing positions.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: This function calculates how large a browser screenshot should be before sending it to a Claude-family vision model, so the model sees it without additional hidden shrinking. It keeps the screenshot within the model's limits for longest side and total pixels.

**Data flow**: It takes the browser viewport size as input. It first scales the width and height down if either side is longer than the allowed maximum, then checks whether the total number of pixels is still too high and shrinks both sides again if needed. It returns a new Size containing the safe screenshot width and height.

**Call relations**: When no explicit model coordinate size is provided, effective_model_size calls this function to find the coordinate space that matches the screenshot the model will actually see. This result then feeds the coordinate conversion functions.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: This function decides what coordinate space the model is using. It uses a provided model size when one is known, otherwise it falls back to the computed screenshot size.

**Data flow**: It receives the real browser viewport size and optionally a model coordinate size. If the optional size is present, it returns that unchanged. If not, it computes the screenshot dimensions from the viewport and returns those.

**Call relations**: Both model_to_viewport and viewport_to_model call this before doing their scaling math. It acts as the shared decision point so both conversion directions agree on the same model-space size.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a point reported by the model into the matching pixel position inside the real browser viewport. It is what makes a model's intended click location usable by the browser.

**Data flow**: It takes a coordinate in model space, the actual viewport size, and optionally the model's coordinate size. It first asks effective_model_size what scale the model is using. Then it stretches or shrinks the x and y values by comparing viewport size to model size, and returns a new Coord in browser pixels.

**Call relations**: This is used when the system needs to turn model output into browser input, such as dispatching a click or mouse movement. It relies on effective_model_size so it works whether the model saw screenshot pixels or a special grid such as Gemini's.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: This function identifies whether a named model uses a special coordinate system. In particular, Gemini models report points on a fixed 1000 by 1000 grid, while Claude-family models use screenshot-sized coordinates.

**Data flow**: It receives a model name, which may be missing. If the name contains "gemini" in any letter case, it returns a Size of 1000 by 1000. Otherwise, it returns None, meaning the rest of the code should use the screenshot dimensions instead.

**Call relations**: This function is a helper for code that prepares coordinate conversion for different model providers. Its result can be passed as the optional model size used by model_to_viewport or viewport_to_model.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a real browser pixel position back into the coordinate space used by the model. It is useful when the system needs to describe or compare browser positions in the same scale the model understands.

**Data flow**: It takes a coordinate in viewport pixels, the actual viewport size, and optionally the model's coordinate size. It determines the effective model size, then scales the x and y values from browser pixels into model-space units. It returns a new Coord in that model coordinate space.

**Call relations**: This is the reverse partner of model_to_viewport. It calls effective_model_size for the same shared sizing rule, so coordinates can move consistently between browser space and model space.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, or “are you sure you want to leave?” messages. In browser automation, these dialogs are not harmless: while one is open, the page can stop sending later browser events. It is like a clerk stopping a whole queue until someone answers a yes/no question. This file makes sure those questions are answered immediately.

The main class, BrowserDialogs, is given a browser session object. When the browser reports that a dialog appeared, on_dialog reads the dialog type and message. It automatically accepts alert and beforeunload dialogs, because there is usually no meaningful alternative for continuing. It dismisses confirm and prompt dialogs, so the automation does not accidentally agree to a website’s own “are you sure?” checkpoint. Either way, it adds a plain text note to the session’s dialogs list, so the rest of the agent can know the dialog happened.

The actual browser command is sent in the background by _answer_dialog. That matters because the answer must go out quickly, without blocking the event flow. If the browser command fails because the connection is gone, times out, or Chrome DevTools Protocol reports an error, the file logs a warning instead of crashing the whole run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of a low-level browser command sender. It represents the ability to send a Chrome DevTools Protocol command, which is a structured message used to control the browser.

**Data flow**: It receives a command name, optional command parameters, and optionally a session id for a specific browser target. It sends that request to the browser connection and returns the browser’s JSON-style reply.

**Call relations**: BrowserDialogs._answer_dialog relies on this capability when it needs to answer an open JavaScript dialog. The protocol keeps this file independent from one specific connection implementation.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the browser connection from a session. The returned connection is what can actually send commands to the browser.

**Data flow**: It reads the current browser session and returns an object that can send Chrome DevTools Protocol commands. It does not itself decide what command to send.

**Call relations**: BrowserDialogs._answer_dialog calls this before sending the dialog-answer command. This lets BrowserDialogs work with any session object that provides the same simple connection method.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for a browser session to start a background task. It is used when work must begin quickly without making the current event handler wait.

**Data flow**: It receives a coroutine, which is an asynchronous job that can run later or alongside other work. The session schedules that job and does not return a result from the job directly here.

**Call relations**: BrowserDialogs.on_dialog uses this to launch _answer_dialog immediately after a dialog is seen. That keeps the event-handling path short while still making sure the browser gets an answer.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog appeared. It chooses a safe automatic response, records the event in human-readable form, and starts the browser command that closes the dialog.

**Data flow**: It receives the dialog details as a JSON-like dictionary and an optional browser session id. It reads the dialog type and message, decides whether to accept or dismiss it, appends a note such as “alert accepted: hello” to the session’s dialog log, and starts _answer_dialog in the background. It does not return a value; its effect is the log entry and the scheduled browser response.

**Call relations**: This is the entry point for dialog events inside this file. When an outside browser event listener calls it, it prepares the decision and hands the actual browser command to _answer_dialog so the page is unfrozen as soon as possible.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This asynchronous helper sends the actual command that closes the browser dialog. It tells the browser either to accept or dismiss the dialog, and it protects the run from failing if that command cannot be delivered.

**Data flow**: It receives the target session id, if any, and a true-or-false accept decision. It gets the browser connection, sends the Page.handleJavaScriptDialog command with that decision, and waits for the browser to respond. If the command fails because of a protocol error, timeout, or runtime problem, it writes a warning to the log instead of raising the error further.

**Call relations**: BrowserDialogs.on_dialog creates this task whenever a dialog event arrives. This helper is the part that crosses from local decision-making into browser control by using the session’s connection to send the Chrome DevTools Protocol command.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `cross-cutting`

This small file creates one custom error type: `HallucinationError`. In this project, the browser automation layer needs to check answers or commands that come from a model. Sometimes a model may invent a browser element, reference, or value that cannot actually exist. For example, it might say to click a button using an element reference that was never provided by the real browser page. This is like someone giving directions to a room number that is not in the building.

`HallucinationError` exists so that this situation is not treated as a vague generic failure. It inherits from `ValidationError`, which means it is part of the system’s normal “this input did not pass checking” error family. The important difference is its meaning: the input is invalid specifically because it appears to be made up rather than merely badly formatted.

Without this file, other code could still raise a generic validation error, but it would lose useful meaning. By naming this case directly, logs, callers, and recovery logic can distinguish invented model output from other validation problems.


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `request handling during browser automation`

Browser automation often needs to ask the page a question: “What is this value?” or “Run this function on that page object.” The browser exposes this through the Chrome DevTools Protocol, or CDP, which is a message-based control channel for inspecting and driving a browser. This file gives the rest of the project a cleaner way to use the CDP Runtime commands without repeating the same message-building and error-checking everywhere.

The main class, BrowserRuntime, is like a translator at a service desk. Code asks it to evaluate a JavaScript expression or call a JavaScript function on an existing browser object. BrowserRuntime builds the right CDP request, sends it through the current browser connection, checks whether the page threw an exception, and then extracts the useful returned value from the nested reply.

Two protocol classes describe what BrowserRuntime expects from its surroundings: something that can send CDP messages, and something that can provide that connection. They are contracts rather than full implementations.

The important safety behavior is raise_on_exception. JavaScript errors do not automatically become Python errors in raw CDP replies; they arrive as structured data. This file notices that and raises RuntimeError, so failures are not silently mistaken for valid results.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of a browser debugging connection. Anything used here must be able to send a named CDP command, optional parameters, and optionally target a specific browser session.

**Data flow**: It receives a command name, a dictionary of command parameters, and possibly a session identifier for a particular page or target. It sends that request to the browser and returns the browser’s reply as a dictionary of JSON-like data.

**Call relations**: BrowserRuntime relies on this contract when it needs to talk to the browser. The actual implementation lives elsewhere; this file only states what kind of connection BrowserRuntime needs in order to evaluate JavaScript or call page functions.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This is the expected shape of an object that can provide access to the browser debugging connection. It lets BrowserRuntime stay independent from the exact browser/session implementation.

**Data flow**: It takes no extra input beyond the session object itself. It returns an object that knows how to send CDP messages to the browser.

**Call relations**: BrowserRuntime asks its browser session for this connection right before sending Runtime.evaluate or Runtime.callFunctionOn. This keeps the runtime helper focused on building and interpreting commands, not on owning the connection itself.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression in a browser session and returns its plain value. Someone would use it to ask the page for a simple computed answer, such as a property value or the result of a small script.

**Data flow**: It receives a browser session id and a JavaScript expression string. It sends a Runtime.evaluate command with returnByValue turned on, checks the browser reply for a page-side exception, then pulls the returned value out of the reply and gives that value back to the caller.

**Call relations**: When code needs a JavaScript expression evaluated, it calls this method instead of building a raw CDP message. This method asks the session for its connection, sends the command, hands the reply to BrowserRuntime.raise_on_exception for safety, and uses as_map to read the nested result in a predictable way.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This calls a JavaScript function on an existing browser-side object and returns the function’s plain object-like result. It is useful when automation already has a reference to a page object and wants to run logic against that object.

**Data flow**: It receives a browser session id, the browser object id to act on, a JavaScript function declaration, and optional argument values. It packages those into a Runtime.callFunctionOn request, converting each Python argument into the CDP argument format, sends the request, checks for page-side exceptions, and returns the returned value as a dictionary.

**Call relations**: This method is the companion to BrowserRuntime.eval for cases where the script should run with a specific page object as its target. Like eval, it sends through the session’s CDP connection, then passes the reply through BrowserRuntime.raise_on_exception and as_map before returning data to the caller.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This turns a JavaScript exception reported by the browser into a Python RuntimeError. It prevents failed page scripts from looking like successful empty or partial results.

**Data flow**: It receives the raw browser reply dictionary. It looks for an exceptionDetails section, extracts the most useful description or fallback text if one exists, and raises RuntimeError with that message. If there is no exception information, it returns without changing anything.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser reply. It is the shared checkpoint that makes both high-level runtime operations fail loudly and consistently when the page-side JavaScript fails.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `request handling`

The browser engine talks to Chrome through the Chrome DevTools Protocol, often called CDP. In plain terms, CDP is a stream of JSON messages: nested dictionaries, lists, strings, numbers, booleans, and null values. The problem is that raw JSON can contain almost anything, while the engine often needs something specific, such as “this field must be a non-empty string” or “this field must be a list.”

This file is the safety checkpoint at that boundary. It names the allowed JSON shapes with simple type aliases, then provides four small “narrowing” functions: one for objects, one for strings, one for integers, and one for lists. A narrowing function takes an uncertain value and either confirms it is safe to use in a more specific way, or raises a ValidationError.

That error is intentional. It means bad wire data is caught early, close to where it enters the system, instead of spreading through the engine as a mystery value and causing a harder-to-understand failure later. An everyday analogy is a mailroom that checks whether each package has the required label before sending it deeper into the building.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary-like set of named fields. It is used when later code needs to safely look up fields by name.

**Data flow**: It receives a JSON value, or None, plus a text path that says where the value came from. If the value is None, it treats it as an empty object. If the value is already a dictionary, it returns that dictionary. If it is anything else, it raises a ValidationError explaining that the named path must be an object.

**Call relations**: When parsing CDP data, code can call this before reading named fields from a value. If the value is the wrong shape, this function hands control to ValidationError so the problem is reported as a clear wire-format error instead of causing confusing failures later.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It is useful for required text fields such as identifiers, names, or URLs where an empty value would not be meaningful.

**Data flow**: It receives a JSON value, or None, plus a path describing the field being checked. If the value is a string and it is not empty, that string comes out. For None, an empty string, or any non-string value, it raises a ValidationError that names the bad field.

**Call relations**: Browser-engine parsing code can call this when a CDP field must be real text. If the check fails, the function creates a ValidationError, keeping invalid protocol data from being treated as normal application data.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer. It is used when the engine needs a whole number from Chrome, not arbitrary JSON data.

**Data flow**: It receives a JSON value, or None, and a path describing where that value came from. If the value is an integer, it returns the integer. If the value is missing or has any other shape, it raises a ValidationError saying the path must be an integer.

**Call relations**: Parsing code can call this before using a numeric CDP field as a count, index, identifier, or similar whole-number value. On bad input, it routes the failure through ValidationError so the rest of the engine does not have to guess what went wrong.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It is used when Chrome may return a collection of items and later code wants to safely loop over them.

**Data flow**: It receives a JSON value, or None, plus a path naming the field. If the value is a list, it returns that list. If the value is None, it treats it as an empty list. If the value is anything else, it raises a ValidationError that says the field must be a list.

**Call relations**: Engine code can call this before iterating over items from a CDP message. If the field is malformed, this function raises ValidationError at the wire boundary rather than letting the wrong kind of value travel deeper into the browser logic.

*Call graph*: 1 external calls (__init__).

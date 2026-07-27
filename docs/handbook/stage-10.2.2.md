# CDP transport, runtime, and session lifecycle  `stage-10.2.2`

This stage is the behind-the-scenes plumbing that lets the project use a real Chrome browser during each work turn. It starts only when a browser tool needs it, keeps the connection alive while actions are being taken, and cleans it up when the turn ends. If Chrome crashes or the link breaks, it can rebuild the session so the task can continue.

backend.py is the outer workbench used by browser tools. It opens pages, reads content, clicks, types, handles tabs, uploads, and downloads. session.py is the central live browser session: one connection to Chrome plus shared state for pages, forms, dialogs, and mouse or keyboard actions.

cdp.py is the message pipe to Chrome DevTools Protocol, Chrome’s remote-control interface. It sends commands through a WebSocket, waits for replies, and routes browser events to listeners. wire.py checks that the JSON messages coming back have the expected shape. runtime.py safely runs JavaScript inside a page and turns browser-side failures into normal Python errors.

## Files in this stage

### Browser turn lifecycle
Per-turn browser orchestration opens, recovers, exposes, and cleans up the live browser surface used by tools.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser use and turn cleanup`

Think of `BuaSurface` as the front desk for browser work during one assistant turn. The rest of the system asks it to do browser actions, and it quietly makes sure there is a real browser session behind those actions. It does not connect to Chrome immediately. Instead, the first browser action asks a CDP provider for a lease. CDP, or Chrome DevTools Protocol, is the control channel used to drive the browser. A lease is like borrowing a browser workspace; when the turn ends, it must be returned.

The file also protects recovery after a hard crash. When a lease is created, its durable token is saved in a scoped store under the conversation id. If the process crashes before cleanup, that token remains. On replay, the new `BuaSurface` tries to reattach to the same live browser session instead of opening a new one, so the page and tab state can continue. If that old session is gone, the token is cleared and a fresh lease is made.

Most public methods are simple tool-facing wrappers. They open the session if needed, validate or normalize small pieces of input, then pass the request to `BrowserSession`, which does the detailed browser work. `aclose` is the important shutdown path: it closes the session, releases the lease even if closing fails, and clears the saved token so later turns do not reconnect to a browser that was intentionally released.

#### Function details

##### `BuaSurface._open`  (lines 45–54)

```
async def _open(self) -> BrowserSession
```

**Purpose**: This prepares and returns the active browser session for the turn. It avoids opening a browser connection until the first browser tool actually needs one, and then reuses that same session for later browser actions in the same turn.

**Data flow**: It starts with the surface's current state: maybe it already has a `BrowserSession`, maybe only a lease, or maybe neither. If a session already exists, it returns it. If there is no lease, it gets one through `_acquire_lease`; then it asks the lease for its browser endpoint, creates a `BrowserSession` pointed at that endpoint, opens it, stores it on the surface, and returns it.

**Call relations**: All browser action methods call this before doing their work. When no lease exists yet, it hands off to `_acquire_lease` to either reconnect to an old browser session or borrow a new one, then constructs the `BrowserSession` that the action methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 56–69)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: This gets the browser lease for the turn, with special care for crash recovery. It tries to reuse a previously saved browser token when possible, and falls back to a new lease if there is nothing valid to reuse.

**Data flow**: It first reads any saved token through `_stored_token`. If a token exists, it asks the CDP provider to reattach to that existing session. If the provider says the session is gone, it clears the token. Then it leases a fresh browser session, saves that lease's token through `_store_token`, and returns the lease.

**Call relations**: `_open` calls this when the surface needs a browser lease for the first time. This function coordinates with `_stored_token` and `_store_token` so recovered turns can continue from the same browser when possible, while normal completed turns do not accidentally reuse released sessions.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 71–75)

```
async def _stored_token(self) -> str | None
```

**Purpose**: This reads the saved browser reattachment token for the current conversation, if one is available. That token is what allows a replayed turn to reconnect to a browser session left alive by a crash.

**Data flow**: It checks whether both a scoped store and a conversation id are present. If either is missing, it returns nothing. Otherwise, it builds the storage key for this conversation, reads the stored value, and returns it only if it is a string token.

**Call relations**: `_acquire_lease` calls this before leasing a new browser. Its result decides whether the surface first tries to reattach to an existing session or immediately creates a fresh lease.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 77–80)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: This saves or clears the browser reattachment token for the current conversation. It is the small persistence hook that makes crash recovery possible, and also prevents intentional cleanup from being mistaken for a crash.

**Data flow**: It receives either a token string or `None`. If the surface has no scoped store or no conversation id, it does nothing. Otherwise, it writes that value under the conversation-specific token key; writing `None` clears the saved token.

**Call relations**: `_acquire_lease` calls this after creating or invalidating a lease token. `aclose` calls it during normal shutdown to remove the token, so future turns do not reconnect to a session that has already been released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 82–87)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This moves a browser tab to a requested web address. It is used when a browser tool wants to load a new page, optionally in a specific tab.

**Data flow**: It receives an argument dictionary, opens or reuses the browser session, pulls out the `url`, and checks that the URL is a string. It converts the optional tab id using `_tab_id`, then passes the URL and tab choice to the browser session. The result is the session's navigation response.

**Call relations**: A browser navigation tool calls this as its surface method. It relies on `_open` to ensure there is a live session, uses `_tab_id` to make the tab input safe and consistent, and then delegates the actual page loading to `BrowserSession`.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 89–91)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This asks for the current tab situation, such as what tabs exist and which one is active. It gives browser tools the context they need before choosing where to act.

**Data flow**: It receives an argument dictionary but does not need any values from it. It opens or reuses the session, asks the session for tab context, and returns that information.

**Call relations**: Tab-inspection tools call this surface method. Its only local job is to get a live browser session through `_open`; the detailed tab lookup is done by `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 93–96)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This creates a new browser tab. If the caller does not provide a usable URL, it opens a blank page instead.

**Data flow**: It receives an argument dictionary, opens or reuses the session, and reads the optional `url`. If the URL is a non-empty string, it uses it; otherwise it substitutes `about:blank`. It passes that chosen URL to the session and returns the new-tab result.

**Call relations**: A tab-creation tool calls this when it needs another tab. `_open` supplies the live session, and `BrowserSession` performs the actual tab creation.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 98–100)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This closes one or more browser tabs according to the caller's arguments. It is the surface entry for tools that need to tidy up or switch away from pages by closing them.

**Data flow**: It receives the caller's argument dictionary, opens or reuses the browser session, passes the arguments to the session's tab-closing operation, and returns that operation's result.

**Call relations**: A tab-closing tool calls this method. The method only ensures the browser session exists through `_open`; the details of deciding and closing tabs are handed to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 102–104)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This supports browser file upload actions, such as attaching a local or sandboxed file to an upload control on a web page. It gives the tool layer a single place to ask the browser session to perform the upload.

**Data flow**: It receives upload arguments, opens or reuses the session, passes those arguments to the session upload operation, and returns the upload result.

**Call relations**: An upload browser tool calls this method when a page needs a file. `_open` makes sure the browser connection exists, then `BrowserSession` carries out the page-level upload work.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.read_page`  (lines 106–108)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This reads structured information from the current page for the browser tool. It is used when the assistant needs to understand what is visible or interactable on the page.

**Data flow**: It receives read arguments, opens or reuses the session, forwards the arguments to the session's page-reading operation, and returns the page information produced there.

**Call relations**: A page-reading tool calls this surface method. The surface handles session readiness through `_open`; `BrowserSession` does the actual browser inspection.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 110–112)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This gets the text content of a page in a form the caller can use for reading or searching. It is a simpler text-focused view compared with richer page inspection.

**Data flow**: It receives arguments describing what text is wanted, opens or reuses the session, forwards the request to the session, and returns the resulting text data.

**Call relations**: A text-reading browser tool calls this when it needs page words rather than full browser structure. `_open` supplies the active session, and `BrowserSession` extracts the text.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 114–116)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This searches for something on the page, with optional help from a host-side find completer. The completer can assist the search process rather than leaving all interpretation to the browser session alone.

**Data flow**: It receives find arguments, opens or reuses the browser session, and passes both the arguments and the surface's `find_completer` to the session. It returns whatever matches or guidance the session finds.

**Call relations**: A find tool calls this when it needs to locate text or page elements. `_open` prepares the session, and `BrowserSession` performs the search using the completer if one was supplied.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 118–120)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This fills or edits form fields on a web page. It is used for actions like typing into inputs, selecting values, or submitting structured page input through the browser session.

**Data flow**: It receives form input arguments, opens or reuses the session, forwards the arguments to the session's form-input operation, and returns the result of that operation.

**Call relations**: A form-input browser tool calls this method. The surface only provides the live browser session through `_open`; `BrowserSession` performs the actual interaction with the page.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 122–124)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This passes lower-level computer-style browser actions to the session, such as direct interaction commands. It is for cases where the browser tool needs to act more like a user controlling the screen.

**Data flow**: It receives a dictionary of computer-action arguments, opens or reuses the session, sends the arguments to the session's computer operation, and returns the resulting response.

**Call relations**: A computer-control browser tool calls this when it needs direct interactive control. `_open` ensures there is a connected session, while `BrowserSession` interprets and performs the requested browser action.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 126–128)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: This waits for a browser download to finish and reports the result. It is used after an action that should produce a file, so the caller does not continue before the file is ready.

**Data flow**: It receives waiting arguments, opens or reuses the session, forwards the request to the session's download-waiting operation, and returns the download status or file information.

**Call relations**: A download-related browser tool calls this after starting or expecting a download. The surface prepares the session through `_open`, then `BrowserSession` watches for the download completion.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.aclose`  (lines 130–144)

```
async def aclose(self) -> None
```

**Purpose**: This is the cleanup method for the per-turn browser surface. It closes the browser session, releases the lease, and clears the saved token so a normal finished turn cannot be mistaken for a crashed one.

**Data flow**: It starts with whatever session and lease are currently stored on the surface. If a session exists, it tries to close it and removes the reference. Whether that succeeds or fails, it then closes the lease if present, removes that reference, and calls `_store_token(None)` to clear the recovery token.

**Call relations**: Turn cleanup calls this at the end of non-crash turns. It uses `_store_token` to remove the durable token; this is the counterpart to `_acquire_lease`, which saves the token when a browser lease is first created.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 147–158)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: This converts a caller's tab id value into a usable integer tab id, or returns nothing when the value should be ignored. It keeps odd or unsafe tab inputs from being passed directly into navigation.

**Data flow**: It receives a JSON-style value. Booleans are rejected because in Python they can look like numbers but are not meaningful tab ids here. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything else becomes `None`.

**Call relations**: `BuaSurface.navigate` calls this when the caller may have provided a tab id. The converted result is then passed along to `BrowserSession.navigate` so navigation targets either a clear tab number or no specific tab.

*Call graph*: called by 1 (navigate).


### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `per browser session, from open/setup through tool calls to teardown`

A browser session is like a temporary control desk for Chrome. It opens a Chrome DevTools Protocol connection, which is a WebSocket channel that lets code ask Chrome to do things and receive browser events. Once connected, it sets up a fresh blank tab, a temporary downloads folder, and listeners for important events such as new tabs, network activity, downloads, page loading, and JavaScript dialogs.

The session does not do every browser task itself. Instead, it keeps the shared state — current tabs, active downloads, frame sessions, whether the browser is on macOS, and background tasks — and hands work to smaller helper objects such as BrowserTabs, BrowserContent, BrowserForms, BrowserDownloads, BrowserRuntime, and BrowserComputer. Those helpers are created on demand and all point back to this same session, so they see the same browser state.

This file also provides the public tool surface: navigate, read a page, search text, fill forms, upload files, create or close tabs, wait for downloads, run JavaScript, and perform computer-like actions. Without this file, the rest of the browser extension would have no single place to open Chrome safely, route commands, listen for browser events, or clean up tabs and temporary files afterward.

#### Function details

##### `BrowserSession.__init__`  (lines 47–64)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None) -> None
```

**Purpose**: Creates an empty browser session object before any connection to Chrome is opened. It records the Chrome endpoint to use, chooses the coordinate size expected by the model, and prepares empty lists and trackers for tabs, downloads, dialogs, loading state, and background tasks.

**Data flow**: The caller provides an optional Chrome DevTools endpoint and an optional model name. The constructor stores those, derives the model coordinate space, creates fresh tracking objects, and leaves the real browser connection unset. The result is a session object that is ready to be opened later but is not yet connected.

**Call relations**: BuaSurface._open creates this session when the browser surface needs to start. Later, open turns this prepared shell into a live connection; close resets the same fields back to a clean state.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 66–73)

```
async def open(self) -> None
```

**Purpose**: Starts the browser session if it is not already open. It is the safe public entry for connecting to Chrome.

**Data flow**: It checks whether a connection already exists. If not, it runs the setup routine; if setup fails partway through, it calls close to clean up anything that was already created, then passes the error back to the caller.

**Call relations**: The async context manager method __aenter__ calls this when code enters a session block. It delegates the real setup to _bootstrap and relies on close to undo partial setup on failure.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 75–108)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the detailed startup work for a live browser session. It connects to Chrome, creates a download folder, registers event listeners, enables target discovery, and opens the first blank tab.

**Data flow**: It reads the configured Chrome endpoint, resolves it to a WebSocket URL, opens the DevTools connection, asks Chrome for version information, creates a temporary download directory, and attaches event callbacks for downloads, tabs, network loading, frames, dialogs, and paused fetches. It then creates a blank target and stores the attached tab as the session’s first tab.

**Call relations**: open calls this during startup. It uses tab_reader, download_reader, and dialog_reader to build helper objects whose callback methods are registered with the DevTools connection.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 4 external calls (__init__, to_thread, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 110–133)

```
async def close(self) -> None
```

**Purpose**: Shuts down the session and returns it to a clean, reusable state. It tries to close browser tabs, closes the DevTools connection, removes the temporary download folder, cancels background work, and clears all per-session state.

**Data flow**: It starts with whatever tabs, connection, downloads, dialogs, and background tasks are currently stored. It asks Chrome to close each known tab, closes the connection, deletes the download directory, resets lists and trackers, cancels background tasks, and leaves the session disconnected.

**Call relations**: __aexit__ calls this when leaving an async session block. open also calls it if startup fails, so half-created resources do not leak.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 2 external calls (__init__, to_thread).


##### `BrowserSession.__aenter__`  (lines 135–137)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python’s async context manager syntax, which means setup happens automatically at the start of a block. This makes session lifetime easier and safer for callers.

**Data flow**: It receives the session object, calls open to make sure the browser connection is live, and then returns the same session for use inside the block.

**Call relations**: This is the entry side of the context-manager pair. It hands off to open for startup, while __aexit__ later handles cleanup.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 139–140)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Cleans up automatically when code leaves an async session block. This protects the system from leaving Chrome tabs, sockets, or temporary files behind.

**Data flow**: It receives the session and any exception information from the block, ignores the details, and calls close. The visible result is that session resources are released even if the block failed.

**Call relations**: This is the exit side of the context-manager pair. It complements __aenter__, which opens the session.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 142–145)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live Chrome DevTools connection for helpers that need to send commands to Chrome. If the session is not open, it raises a clear browser-unavailable error instead of returning nothing.

**Data flow**: It reads the session’s stored connection. If one exists, it returns it; if not, it raises an error explaining that the browser is not open.

**Call relations**: Reader/helper classes use this kind of access pattern when they need the underlying connection. It acts as a guardrail so browser commands are not attempted before open has finished.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 147–150)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and keeps track of it so the session can cancel it later. This is useful for browser work that must continue while the main command proceeds.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session’s background-task set, and arranges for the task to remove itself from the set when finished.

**Call relations**: Other session helpers can use this when they need side work. close later walks the stored tasks and cancels anything still running.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 152–153)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame belongs to the main page rather than being an embedded frame such as an iframe. This matters because some page events should only count for the main page.

**Data flow**: It receives a session ID and frame ID, creates a tab helper, and asks that helper to interpret the frame information using the session’s tab state. It returns true or false.

**Call relations**: It delegates the actual frame knowledge to BrowserTabs through tab_reader. This keeps frame tracking in the tab subsystem while exposing a convenient session-level method.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 155–156)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a Chrome target session after Chrome reports or creates one. This prepares the target so later page, network, and frame operations can work correctly.

**Data flow**: It receives a DevTools session ID, builds a tab helper, and asks it to initialize that session. The result is stored through the helper’s effects on the session and the browser connection.

**Call relations**: This method is a session-level shortcut into the tab subsystem. It calls tab_reader because BrowserTabs owns the details of target and frame session setup.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 158–159)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the active tab, or a requested tab by ID, in a form the browser tools can operate on. It is the common way to resolve “which tab are we talking about?”

**Data flow**: It receives an optional tab ID, passes it to the tab helper, and returns the matching Tab object. If no ID is given, the helper chooses the current/default page according to its rules.

**Call relations**: Tool methods that need a tab can use this session-level method. It delegates tab lookup to BrowserTabs through tab_reader.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 161–162)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new web address. This is the session’s public navigation command.

**Data flow**: It receives a URL and an optional tab ID, asks the tab helper to navigate the chosen tab, and returns a JSON-style result describing the outcome.

**Call relations**: External browser tools call navigate when the user or model wants to open a page. The method routes the work to BrowserTabs through tab_reader.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 164–165)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a plain data summary for one tab. This is useful when tools need to report a tab’s identity, title, URL, or related context.

**Data flow**: It receives a Tab object, passes it to the tab helper, and returns a JSON-style dictionary with tab information.

**Call relations**: It keeps the session API simple while BrowserTabs owns the exact tab-information format.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 167–168)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns an overview of the browser’s tabs for context. This lets callers understand what pages are currently open.

**Data flow**: It creates a tab helper, asks for the current tabs context, and returns a JSON-style dictionary summarizing that state.

**Call relations**: This is part of the tab tool surface. It delegates the tab-specific work to BrowserTabs through tab_reader.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 170–171)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of the open tabs. This gives a compact, human-readable view of what the browser currently contains.

**Data flow**: It asks the tab helper to read titles from the session’s known tabs and returns them as a list of strings.

**Call relations**: It is a small convenience wrapper over BrowserTabs, used when only tab names are needed rather than full tab details.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 173–174)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper tied to this session. That helper knows how to create, close, attach, inspect, and navigate tabs.

**Data flow**: It takes the current session and the fixed viewport size, constructs a BrowserTabs object, and returns it. The helper reads and updates the session’s tab-related state as needed.

**Call relations**: Startup, navigation, tab creation, tab closing, frame checks, and tab summaries all call this. It is the bridge from the session shell to tab-specific behavior.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 176–177)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page structure questions, especially resolving references to frames and screen points. It limits how deeply nested frames are explored.

**Data flow**: It takes the current session, viewport size, and maximum frame depth, constructs a BrowserPage object, and returns it.

**Call relations**: resolve_ref and ref_point call this when they need page/frame interpretation. It keeps those details outside the main session class.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 179–180)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading and searching page content. This helper turns a live web page into text, trees, and search results.

**Data flow**: It passes the current session into BrowserContent and returns the helper. The helper can then use the session’s connection and tab state to inspect page content.

**Call relations**: tree, read_page, get_page_text, and find all call this. The session exposes those tools, while BrowserContent does the content-specific work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 182–183)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for tracking and waiting on file downloads. It includes the session’s maximum wait time so downloads do not hang forever.

**Data flow**: It passes the current session and wait limit into BrowserDownloads and returns the helper. The helper uses session download state and Chrome download events.

**Call relations**: _bootstrap uses this to register download event callbacks with Chrome. wait_for_download uses it later to wait until a requested download is ready.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 185–186)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for browser popups such as JavaScript alert or confirm dialogs. These dialogs can block page interaction, so they must be noticed and handled.

**Data flow**: It passes the current session into BrowserDialogs and returns the helper. The helper records dialog events into the session’s dialog state.

**Call relations**: _bootstrap calls this while wiring Chrome event listeners, specifically for JavaScript dialog opening events.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 188–189)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form-related actions such as typing into fields and uploading files. This keeps form mechanics separate from session setup.

**Data flow**: It passes the current session into BrowserForms and returns the helper, which can then use the session connection and page references to perform form actions.

**Call relations**: form_input and upload_file call this when a tool asks to interact with a form.

*Call graph*: called by 2 (form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 191–192)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript or calling JavaScript functions inside the page. This is the low-level page scripting doorway.

**Data flow**: It passes the current session into BrowserRuntime and returns the helper. The helper uses the session’s DevTools connection to evaluate code in a browser target session.

**Call relations**: eval_js and call_on call this. The session exposes the public method, while BrowserRuntime handles the runtime protocol details.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 194–195)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page when no URL is provided. This is the public tool for opening another tab.

**Data flow**: It receives a URL, creates a tab helper, asks it to create a tab at that URL, and returns a JSON-style result describing the new tab.

**Call relations**: It routes tab creation to BrowserTabs through tab_reader, keeping the session API as the simple front door.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 197–198)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one or more browser tabs according to the provided arguments. This is the public tool for removing tabs from the session.

**Data flow**: It receives a JSON-style argument dictionary, passes it to the tab helper’s close operation, and returns a JSON-style result about what was closed.

**Call relations**: It delegates to BrowserTabs through tab_reader because the tab subsystem knows how to interpret tab IDs and update session tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 200–201)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads a file through a page’s file input control. This supports websites that ask the user to choose a local file.

**Data flow**: It receives a JSON-style argument dictionary, creates a form helper, and asks it to perform the upload. The returned JSON-style result reports the outcome.

**Call relations**: This is part of the form tool surface. It delegates the detailed page interaction to BrowserForms through form_reader.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.tree`  (lines 203–204)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a structured view of page content, optionally filtered by type. This helps a caller understand what interactive or readable elements are on the page.

**Data flow**: It receives JSON-style arguments and a filter type, creates a content helper, and asks for a tree representation. It returns that representation as a string.

**Call relations**: It is one of the content-reading tools exposed by the session. BrowserContent performs the page inspection through content_reader.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 206–207)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result suitable for a browser tool response. It gives callers a usable snapshot of what is on the page.

**Data flow**: It receives JSON-style arguments, passes them to a content helper, and returns a JSON-style dictionary with the page-reading result.

**Call relations**: It delegates to BrowserContent through content_reader, which owns the details of extracting content from Chrome.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 209–210)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts plain text from the page. This is useful when the caller needs readable wording without the full page structure.

**Data flow**: It receives JSON-style arguments, asks the content helper to get page text, and returns a JSON-style result containing that text or related metadata.

**Call relations**: It sits on the public session API and passes the actual content extraction to BrowserContent through content_reader.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 212–213)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches for text or content on the page, with optional completion support for search suggestions or follow-up matching. It helps tools locate specific information or elements.

**Data flow**: It receives JSON-style search arguments and an optional FindCompleter, creates a content helper, and returns a JSON-style search result.

**Call relations**: It delegates to BrowserContent through content_reader. The optional completer is handed along so the content layer can finish or refine the find operation.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 215–216)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or sets values in page form controls. This is the public tool for filling text fields, selecting options, or otherwise entering form data.

**Data flow**: It receives JSON-style arguments describing the form action, creates a form helper, and returns a JSON-style result from that input operation.

**Call relations**: It routes form interaction to BrowserForms through form_reader, while the session keeps shared browser state.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 218–219)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs computer-like browser actions, such as mouse, keyboard, scrolling, or other viewport-based interactions. This is used when page interaction is closer to controlling a screen than reading structured content.

**Data flow**: It receives JSON-style action arguments, creates a BrowserComputer with the session, viewport size, and wait limit, then runs the requested action. It returns a JSON-style result.

**Call relations**: Unlike many other methods, it constructs BrowserComputer directly for each call. BrowserComputer then uses the session state and connection to carry out the interaction.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 221–224)

```
async def wait_for_download(self, args: JsonDict) -> JsonDict
```

**Purpose**: Waits for a browser download to finish and returns information about it. It refuses to run if the browser session has no download directory, because that means the session is not open.

**Data flow**: It checks that a temporary download directory exists, receives JSON-style wait arguments, creates a download helper, and asks it to wait for the download in that directory. It returns a JSON-style result when the download is ready or an error if the browser is not open.

**Call relations**: _bootstrap creates the download directory and registers download events. This method later uses BrowserDownloads through download_reader to connect those tracked events with a caller waiting for a file.

*Call graph*: calls 1 internal fn (download_reader); 1 external calls (__init__).


##### `BrowserSession.eval_js`  (lines 226–227)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Runs a JavaScript expression inside a specific browser session. This is useful for inspecting or changing page state when normal page tools are not enough.

**Data flow**: It receives a DevTools session ID and a JavaScript expression, creates a runtime helper, and asks it to evaluate the expression. It returns the JSON-like value produced by the page runtime.

**Call relations**: It delegates to BrowserRuntime through runtime_reader, which knows how to speak Chrome’s runtime commands.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 229–236)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing JavaScript object inside the page. This supports more targeted page operations than evaluating a standalone expression.

**Data flow**: It receives a session ID, a JavaScript object ID, function source text, and optional argument values. It passes them to the runtime helper and returns the JSON-style result from that function call.

**Call relations**: It is the session-level wrapper for BrowserRuntime.call_on. The runtime helper handles the browser protocol details while the session provides shared connection access.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 238–239)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame node and element index it points to. This lets later actions use compact references instead of re-finding page objects from scratch.

**Data flow**: It receives a Tab and a reference string, creates a page helper, and asks it to resolve the reference. It returns the matching FrameNode and numeric index.

**Call relations**: It delegates reference interpretation to BrowserPage through page_reader. ref_point uses the same page-reader path for coordinate lookup.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 241–242)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen point for a referenced page item. This is useful when an action needs to click or move to something identified by a page reference.

**Data flow**: It receives a Tab and reference string, creates a page helper, and asks it for the point associated with that reference. It returns an x and y coordinate pair.

**Call relations**: It delegates page geometry work to BrowserPage through page_reader, keeping coordinate calculations out of the main session class.

*Call graph*: calls 1 internal fn (page_reader).


### DevTools transport
The CDP transport layer exchanges commands and events with Chrome and validates the protocol data it receives.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `active while a browser session is connected`

Chrome exposes a control interface called the Chrome DevTools Protocol, or CDP, which lets a program do things like inspect pages, click, navigate, and listen for browser events. This file wraps that protocol in a safer, easier shape. Think of it like a switchboard operator: outgoing commands get numbered, incoming replies are matched back to the right caller, and broadcast-style events are routed to anyone waiting for them.

The main class, CdpConnection, owns one WebSocket connection to Chrome. A WebSocket is a long-lived network connection where both sides can send messages at any time. When code sends a command, CdpConnection assigns it an id, stores a “future” for the eventual answer, and waits with a timeout so the program does not hang forever. When Chrome sends a message back, the background reader decides whether it is a reply to a command or an event. Replies complete the matching future. Events either wake one-time waiters created by expect and wait, or are passed to registered listeners created with on.

A key safety feature is cleanup on disconnect. If Chrome closes the connection, all pending commands and event waits are failed loudly instead of leaving callers stuck indefinitely.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Creates a clear error object for a failed Chrome DevTools Protocol command. It records which command failed, Chrome’s numeric error code, and the message Chrome sent back.

**Data flow**: It receives the command name, an error code, and an error message. It stores the command name and code on the error object, then builds a readable exception message. The result is an exception that can be attached to the waiting command instead of returning a normal result.

**Call relations**: When CdpConnection._dispatch sees that Chrome replied to a command with an error, it creates this error and gives it to the caller that was waiting for that command’s response.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual WebSocket address used to talk to Chrome. This is useful because callers may provide either the WebSocket address directly or a normal HTTP DevTools endpoint that needs to be queried first.

**Data flow**: It receives a URL and HTTP headers. If the URL already starts with a WebSocket scheme, it returns it unchanged. Otherwise, it asks the browser’s /json/version endpoint for connection details, checks that the HTTP request succeeded, extracts the webSocketDebuggerUrl field, and returns that as text.

**Call relations**: This helper prepares the address that CdpConnection.open needs. It uses an HTTP client to ask Chrome for metadata, then uses a small wire-format checker to make sure the returned WebSocket URL is really a string.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Sets up the in-memory bookkeeping for one CDP WebSocket connection. It starts with no pending commands, no listeners, no event waiters, and no reader task yet.

**Data flow**: It receives an already-open WebSocket object. It stores that socket, initializes the next command id counter, and creates empty collections for command replies, event listeners, and one-time event waiters. The new connection object is ready to be started by open.

**Call relations**: CdpConnection.open creates the WebSocket first and then calls this initializer to wrap it in the project’s CDP connection behavior.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a live WebSocket connection to Chrome and starts the background reader that listens for replies and events. This is the normal way the rest of the project creates a CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to Chrome with a large allowed message size, builds a CdpConnection around the socket, starts the private read loop as an asynchronous background task, and returns the ready connection.

**Call relations**: BrowserSession._bootstrap calls this when setting up a browser session. After open returns, other code can send commands while the reader task continuously receives and dispatches Chrome messages in the background.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader and closes the WebSocket so the browser session does not leave a dangling network connection behind.

**Data flow**: It checks whether a reader task exists. If so, it cancels that task, quietly accepts the expected cancellation result, clears the stored reader task, and then closes the WebSocket. The connection is no longer usable afterward.

**Call relations**: This is the teardown counterpart to open. It relies on the reader loop’s cleanup behavior to fail pending waits if the connection is interrupted.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one command to Chrome and waits for that command’s reply. It protects callers from waiting forever by applying a command timeout.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for a specific browser target. It assigns a new numeric id, stores a future under that id, serializes the command to JSON, and sends it through the WebSocket. Then it waits for the reader loop to complete the future with either a result or an error. On timeout, it removes the pending entry and raises a timeout error.

**Call relations**: Callers use this for request-and-response CDP operations. The background _read_loop receives Chrome’s reply and passes it to _dispatch, which matches the reply id back to the future created here.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a callback for a repeated browser event. It is used when some part of the system wants to be told every time a particular CDP event arrives.

**Data flow**: It receives an event name and a listener function. It adds the listener to the list for that event. Later, each matching incoming event will be delivered to that listener with the event parameters and optional session id.

**Call relations**: This supports ongoing event subscriptions. CdpConnection._dispatch calls the registered listeners whenever it receives a matching event from the reader loop.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one of several possible browser events. This is useful when code is about to do something and wants to pause until Chrome reports that a specific event happened.

**Data flow**: It receives one or more event names and optionally a session id. It creates a future, records the event names and session filter alongside that future, and returns the future to the caller. The future will later be completed with the event parameters when a matching event arrives.

**Call relations**: This only sets up the wait; it does not apply the timeout itself. Callers normally pass the returned future to CdpConnection.wait, while _dispatch is responsible for completing it when the matching event arrives.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for an event future created by expect, with a timeout. It also removes the waiter afterward so old waits do not pile up in memory.

**Data flow**: It receives a future and an optional timeout value. It waits until the future is completed by an incoming event or until the timeout expires. Whether it succeeds or fails, it removes that future from the connection’s waiter list. On success it returns the event data.

**Call relations**: This is the timed waiting half of the expect-and-wait pattern. _dispatch completes the future when Chrome sends a matching event; wait returns that result to the caller and cleans up the registration.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads raw messages from Chrome and sends them to the dispatcher. It is the background listener that makes command replies and browser events reach the rest of the connection object.

**Data flow**: It reads JSON text messages from the WebSocket one by one. Each message is decoded into a Python dictionary and passed to _dispatch. If the WebSocket closes, the loop stops. During cleanup, it fails every still-pending command and event wait so no caller is left waiting forever.

**Call relations**: CdpConnection.open starts this as a background task. For each incoming message it delegates the interpretation to _dispatch, and on connection close it becomes the last line of defense against silent hangs.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Sorts one incoming CDP message into the right place. It decides whether the message is a command reply or a browser event, then completes the right waiter or notifies the right listeners.

**Data flow**: It receives a decoded message dictionary. If the message has a numeric id, it treats it as a reply, finds the stored pending command, and completes that command’s future with either a result dictionary or a CdpError. If the message has an event method instead, it extracts the event parameters and optional session id, completes any matching one-time waiters, removes completed waiters, and calls all registered listeners for that event.

**Call relations**: _read_loop calls this for every message received from Chrome. It is the central routing point that connects send, expect, wait, and on: command replies go back to send callers, one-time event waits go back to expect/wait callers, and recurring events go to listeners.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during browser protocol message parsing`

The browser engine communicates with Chrome using the Chrome DevTools Protocol, often shortened to CDP. In plain terms, CDP is a JSON-based remote control interface for Chrome: the engine sends JSON messages to the browser and receives JSON messages back. Because JSON can contain many kinds of values — strings, numbers, lists, objects, or nothing — the engine needs a safe way to say, “At this point, I need an object,” or “This must be a non-empty string.”

This file is that safety layer. It names the allowed JSON value types, then offers a few small checker functions: `as_map`, `as_str`, `as_int`, and `as_list`. Each one looks at a value from the wire and either returns it in the expected form or raises `ValidationError` with a clear message. That error means “the browser response did not match what this code needed,” which can be reported as a recoverable tool problem instead of turning into a confusing crash later.

A useful analogy is a customs checkpoint. JSON messages arrive from outside the system, and these functions inspect the parcels before letting them into the rest of the engine. If a parcel is the wrong shape, it is rejected at the border with a specific explanation.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function turns an incoming JSON value into an object-like dictionary when that is what the caller expects. If the value is missing, it treats it as an empty object; if it is anything else, it reports a validation problem.

**Data flow**: It receives a JSON value and a `path`, which is a human-readable label for where that value came from. If the value is `None`, it returns an empty dictionary. If the value is already a dictionary, it returns it unchanged. If the value is a string, number, list, or any other wrong shape, it raises `ValidationError` saying that the named path must be an object.

**Call relations**: Code that reads Chrome protocol data calls this when it reaches a field that should behave like a JSON object. If the check fails, this function creates a `ValidationError` immediately, so the mistake is caught at the protocol boundary before later code tries to use the value as a dictionary.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that an incoming JSON value is a useful string. It only accepts strings that are not empty, because an empty string would not carry the required information.

**Data flow**: It receives a JSON value and a `path` describing that value’s location in the incoming data. If the value is a non-empty string, it returns that string. For `None`, an empty string, a number, a list, an object, or any other wrong shape, it raises `ValidationError` explaining that the path must be a non-empty string.

**Call relations**: Browser-parsing code calls this when it needs a field such as an identifier, name, or other text value from a CDP message. When the value is not acceptable, this function hands off to `ValidationError` so the caller gets a clear, recoverable error instead of a vague failure later.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that an incoming JSON value is an integer. It is used when the protocol data is expected to contain a whole number, such as a count, index, or numeric identifier.

**Data flow**: It receives a JSON value and a `path` that names the field being checked. If the value is an integer, it returns that integer. If the value is missing or has any other shape, it raises `ValidationError` saying that the named path must be an integer.

**Call relations**: Code that interprets browser responses calls this when it needs a whole-number field. If the data from Chrome does not match that expectation, this function raises `ValidationError` at once, keeping the bad value from spreading into the rest of the engine.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function turns an incoming JSON value into a list when a list is expected. If the value is missing, it treats that as an empty list, which is useful for optional array fields in protocol messages.

**Data flow**: It receives a JSON value and a `path` describing where the value came from. If the value is already a list, it returns it unchanged. If the value is `None`, it returns an empty list. If the value is an object, string, number, or any other wrong shape, it raises `ValidationError` explaining that the path must be a list.

**Call relations**: Browser protocol parsing code calls this when it reaches a field that should contain several items. The function either gives the caller a safe list to loop over or raises `ValidationError`, which cleanly reports that the incoming CDP data did not have the expected shape.

*Call graph*: 1 external calls (__init__).


### Page runtime evaluation
Runtime helpers safely evaluate JavaScript in the page and translate browser-side failures into Python errors.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `during browser automation requests`

Browser automation often needs to ask the page a question, such as “what is this value?” or “run this function on that page object.” The browser exposes this through the Chrome DevTools Protocol, a message-based control channel for inspecting and driving pages. This file wraps the protocol’s Runtime commands so the rest of the project does not have to build those messages by hand.

The main class, BrowserRuntime, is like a small interpreter remote control. Its eval method sends a JavaScript expression to a specific browser target and returns the value. Its call_on method calls a JavaScript function on an existing browser object and returns the function’s value. Both ask the browser to return plain JSON-style data, which is easier for Python code to use than remote browser references.

A key detail is error handling. If the JavaScript throws an uncaught exception, the browser includes exception information in its reply instead of simply failing the transport call. BrowserRuntime.raise_on_exception checks for that and raises a Python RuntimeError with the browser’s error description. Without this file, callers would need to know the exact protocol shape, repeat the same message-building code, and remember to check for hidden page exceptions every time.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the one operation BrowserRuntime needs from a browser connection: send a named debugging-protocol command with optional data and an optional session target. It is a contract, not an implementation, so different connection objects can be used as long as they provide this behavior.

**Data flow**: The caller provides a protocol method name, optional parameters, and optionally the browser session to send it to. An implementing connection sends that command to the browser and returns the browser’s reply as a dictionary-like JSON object.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on whatever object satisfies this protocol. They build Runtime.evaluate or Runtime.callFunctionOn messages, then hand them to send so the actual browser connection can transmit them.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This protocol method describes how BrowserRuntime gets access to the browser’s command connection. It lets BrowserRuntime depend on a simple promise: the session object can provide something that knows how to send protocol commands.

**Data flow**: No input is needed beyond the session object itself. The method returns a BrowserRuntimeCdp-style connection that can send commands to the browser.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on first ask the stored browser session for its connection. They then use that connection to send the actual Runtime command to the browser.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This function runs a JavaScript expression in a browser session and returns the expression’s value. It is used when Python code needs to read or compute something inside the page without dealing with raw browser protocol details.

**Data flow**: It receives a browser session id and a JavaScript expression string. It sends a Runtime.evaluate command asking the browser to run the expression and return the result by value, checks the reply for a JavaScript exception, then extracts and returns the returned value from the browser’s response.

**Call relations**: When higher-level browser automation code needs a page value, it calls this method. The method asks the session for its connection, sends the Runtime.evaluate command, calls BrowserRuntime.raise_on_exception to turn browser-side failures into Python failures, and uses as_map to safely treat nested response pieces as dictionaries before returning the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This function calls a JavaScript function on a specific object that already exists inside the browser. It is useful when the automation has a browser object reference and wants to run code against that exact object, such as reading properties or invoking page-side behavior.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body, and optional argument values. It builds the Runtime.callFunctionOn request, converts the optional Python values into protocol argument entries, sends the command, checks for browser-side exceptions, and returns the function’s JSON-style result as a dictionary.

**Call relations**: Higher-level code calls this when it needs to act on a known browser-side object rather than just evaluate a standalone expression. The method sends the protocol command through the browser connection, then hands the reply to BrowserRuntime.raise_on_exception and as_map so callers get either a clean dictionary result or a clear Python error.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This helper checks a browser Runtime command reply for an uncaught JavaScript exception. If the page code failed, it raises a Python RuntimeError so the failure cannot be accidentally mistaken for a normal result.

**Data flow**: It receives the browser’s response dictionary. If there is no exception information, it returns without changing anything. If exception details are present, it pulls out the best available description or text and raises an error message that explains the browser evaluation failed.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a Runtime response. It is the shared checkpoint that keeps both paths honest: successful browser replies continue to result extraction, while failed page executions stop with a clear exception.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).

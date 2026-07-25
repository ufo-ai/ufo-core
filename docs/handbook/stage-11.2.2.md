# Browser CDP session and lifecycle infrastructure  `stage-11.2.2`

This stage is the browser automation engine. It sits behind the tools that browse the web, fill forms, click buttons, upload files, and wait for results. It is used during each work turn: it opens a connection to Chrome only when needed, keeps it alive while actions run, then cleans it up afterward.

The backend is the front desk for one turn. It hands browser tools a usable browser surface. The session is the main control room: it owns the live Chrome connection and coordinates tabs, downloads, dialogs, page state, and user actions. The CDP pipe talks to Chrome DevTools Protocol, Chrome’s remote-control interface, by sending commands over a WebSocket and receiving replies and events. Tabs manages opening, closing, switching, and navigating pages. Runtime safely runs small JavaScript snippets inside pages and turns results into normal Python values. Downloads watches for files, waits for them, and prevents paused network requests from freezing pages. Dialogs quickly handles pop-ups. Settle decides when a page has finished enough to continue. Wire checks the raw JSON data coming back from Chrome.

## Files in this stage

### Turn and session orchestration
The top-level browser surface opens, exposes, and releases a live CDP-backed browser session for each automation turn.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser use and turn cleanup`

Think of this file as the front desk for browser automation during one assistant turn. The rest of the system asks for simple actions like “go to this URL” or “read the page.” `BuaSurface` makes sure there is a real browser session behind those requests, but it waits until the first browser action before doing any work. That means a turn that never uses the browser does not waste time or money opening Chrome.

The browser connection is leased from a `CdpProvider`. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome from code. Once a lease is obtained, this file creates a `BrowserSession`, opens it, and then forwards browser tool calls to that session.

A key detail is crash recovery. When a lease is created, its durable token is saved in a scoped store under the current conversation. If the process crashes before cleanup, the next recovered run can find that token and reattach to the same live browser instead of starting over. If the old browser is gone, the token is cleared and a new lease is created. On normal cleanup, `aclose` closes the session, releases the lease, and removes the token so later turns do not reconnect to a browser that was already released.

#### Function details

##### `BuaSurface._open`  (lines 45–54)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Makes sure there is an open browser session ready to use. It avoids opening the browser more than once during the same turn.

**Data flow**: It starts with the current `BuaSurface` state. If a session already exists, it returns it. If not, it obtains or reuses a lease, asks the lease for the browser endpoint, creates a `BrowserSession` connected to that endpoint, opens it, stores it, and returns it.

**Call relations**: All browser-facing methods call this first, so they do not each need to know how to connect to Chrome. When no lease exists yet, it hands off to `BuaSurface._acquire_lease`; after that it builds the `BrowserSession` that the tool methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 56–69)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets permission and connection details for a browser session. It first tries to reconnect to a saved live session after a crash, and only creates a new one if that is not possible.

**Data flow**: It reads any stored token for this conversation. If a token exists, it asks the CDP provider to reattach to that browser. If the provider says the session is gone, it clears the token. Then it leases a fresh browser, saves the new lease token, and returns the lease.

**Call relations**: `BuaSurface._open` calls this when it needs a lease for the first time. This function uses `BuaSurface._stored_token` and `BuaSurface._store_token` to make recovery possible without exposing token details to the browser tool methods.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 71–75)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser reattach token for the current conversation, if recovery support is available.

**Data flow**: It checks whether both a scoped store and conversation ID exist. If either is missing, it returns nothing. Otherwise it reads the token key from the store and returns the value only if it is a string.

**Call relations**: `BuaSurface._acquire_lease` calls this before creating a new lease. This lets the surface prefer reconnecting to an existing browser after a crash instead of opening a fresh browser immediately.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 77–80)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the durable browser token for this conversation. The token is what lets a recovered turn reconnect to the same browser session.

**Data flow**: It receives either a token string or `None`. If there is no scoped store or no conversation ID, it does nothing. Otherwise it writes that value under the conversation-specific token key; writing `None` clears the saved token.

**Call relations**: `BuaSurface._acquire_lease` calls this to save a new token or remove a dead one. `BuaSurface.aclose` calls it during normal cleanup so future turns do not reattach to a released browser.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 82–87)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Moves the browser to a requested web address, optionally in a specific tab. It validates that the URL really is a string before sending it to the browser session.

**Data flow**: It receives tool arguments, opens or reuses the browser session, reads `url`, checks that it is text, converts the optional `tab_id` using `_tab_id`, and returns the result from the session’s navigation action.

**Call relations**: This is one of the browser tool entry methods. It relies on `BuaSurface._open` for the live session and `_tab_id` for forgiving tab ID conversion, then hands the actual page loading work to the `BrowserSession`.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 89–91)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the browser’s current tabs. A caller uses it to understand what pages are open and which tab is active.

**Data flow**: It receives the tool arguments, opens or reuses the browser session, asks the session for tab context, and returns that information unchanged.

**Call relations**: This method is called by the tab-inspection tool path. Its main job is to ensure the session exists through `BuaSurface._open`, then delegate the browser-specific work to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 93–96)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no usable URL is provided, it opens a blank page.

**Data flow**: It receives tool arguments, opens or reuses the session, reads an optional `url`, substitutes `about:blank` when the URL is missing or empty, and returns the session’s tab-creation result.

**Call relations**: This method sits between the tab-creation tool and `BrowserSession`. It uses `BuaSurface._open` for connection setup, then asks the session to create the tab.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 98–100)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes one or more browser tabs according to the provided arguments.

**Data flow**: It receives the tab-close arguments, opens or reuses the browser session, passes those arguments to the session, and returns the session’s result.

**Call relations**: This method is part of the browser tab tool surface. It does not decide the low-level closing behavior itself; after `BuaSurface._open` supplies a session, `BrowserSession` performs the actual tab operation.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 102–104)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Lets a browser tool upload a file through the active browser session.

**Data flow**: It receives upload arguments, opens or reuses the browser session, forwards the arguments to the session’s upload routine, and returns the upload result.

**Call relations**: The upload tool calls this when a page needs a file selected. This method provides the common session setup through `BuaSurface._open` and then hands the file-upload details to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.read_page`  (lines 106–108)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads structured information from the current page so the assistant can understand what is visible and usable.

**Data flow**: It receives read arguments, opens or reuses the browser session, asks the session to read the page, and returns the page information it gets back.

**Call relations**: This is a browser-reading tool method. It depends on `BuaSurface._open` for lazy connection setup and leaves the actual page inspection to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 110–112)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets the text content of the current page. This is useful when the caller needs the page’s words rather than full interactive structure.

**Data flow**: It receives arguments, opens or reuses the session, forwards the request to the session’s page-text method, and returns the text result.

**Call relations**: The text-reading tool path calls this method. It uses the same session-opening gateway as the other browser actions, then delegates to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 114–116)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, using an optional find completer to help finish or improve the search request. A find completer is helper logic that can fill in search details from the host side.

**Data flow**: It receives find arguments, opens or reuses the browser session, passes the arguments plus the optional `find_completer` to the session, and returns the match result.

**Call relations**: The find tool calls this when it needs to locate page content or elements. `BuaSurface._open` supplies the session, and the stored `find_completer` is passed along so the lower-level browser session can use host-side search help.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 118–120)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on a web page. This is the path for actions like typing into inputs or choosing values.

**Data flow**: It receives form-input arguments, opens or reuses the browser session, forwards the arguments to the session’s form-input routine, and returns the result.

**Call relations**: The form tool calls this method. The method’s role is to connect the tool request to the active `BrowserSession` after `BuaSurface._open` has ensured the browser is ready.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 122–124)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Runs lower-level computer-style browser actions through the session, such as direct interaction steps that do not fit the simpler page-reading or form APIs.

**Data flow**: It receives action arguments, opens or reuses the browser session, passes the arguments to the session’s computer method, and returns the session’s response.

**Call relations**: The computer-control tool path calls this for browser interactions. Like the other surface methods, it centralizes connection setup in `BuaSurface._open` and delegates the concrete browser action to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 126–128)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and reports the result. This is useful when a page action starts a file download that may take time.

**Data flow**: It receives wait arguments, opens or reuses the browser session, asks the session to wait for the download, and returns the download status or information.

**Call relations**: The download-waiting tool calls this after an action that may produce a file. It relies on `BuaSurface._open` for the active session, then lets `BrowserSession` watch the browser download.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.aclose`  (lines 130–144)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the browser resources at the end of a normal turn. It closes the browser session, releases the lease, and clears the recovery token.

**Data flow**: It starts from the current stored session and lease. If a session exists, it closes it and removes the reference. Whether or not session closing succeeds, it then releases the lease if one exists and clears the stored token by writing `None`.

**Call relations**: The turn cleanup path calls this when the browser surface is no longer needed. It calls `BuaSurface._store_token` at the end so only crash recovery leaves a token behind; normal completion never leaves a later turn pointing at a released session.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 147–158)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a flexible tab ID value from tool arguments into either an integer tab ID or no tab ID. It deliberately ignores booleans because in Python booleans can look like numbers, but they are not meaningful tab IDs here.

**Data flow**: It receives one JSON-style value. Integers are returned as-is, floats and non-empty strings are converted to integers, booleans and unsupported or empty values become `None`.

**Call relations**: `BuaSurface.navigate` calls this before asking the session to navigate. This keeps the navigation method tolerant of common input shapes while still passing a clean tab ID to the browser session.

*Call graph*: called by 1 (navigate).


### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `created per browser turn; active while browser tools are being used; cleaned up at teardown`

A browser automation system needs one place that knows, “Which browser am I connected to, what tabs exist, what downloads are happening, and where should tool calls go?” This file provides that place through BrowserSession. It connects to Chrome using CDP, the Chrome DevTools Protocol, which is a WebSocket-based remote-control interface for Chrome. Think of it like plugging a control cable into the browser.

When a session opens, it resolves the real WebSocket address, connects, creates a temporary download folder, listens for browser events, discovers tabs, and starts with a blank tab. It also registers event listeners for downloads, dialogs, network activity, page loading, and tab creation or destruction. These listeners keep the session’s memory up to date while Chrome changes underneath it.

The session does not do every job itself. Instead, it creates small helper objects, called readers here, for different areas: tabs, page structure, content extraction, forms, downloads, dialogs, runtime JavaScript, and computer-like mouse or keyboard actions. BrowserSession holds the shared state those helpers need, then delegates each public tool call to the right helper.

Closing the session is just as important as opening it. It closes tabs, shuts down the connection, removes the temporary download folder, cancels background tasks, and resets its stored state. Without this file, browser actions would have no shared connection or reliable place to coordinate tabs, downloads, dialogs, and page reads.

#### Function details

##### `BrowserSession.__init__`  (lines 47–64)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None) -> None
```

**Purpose**: Creates an empty browser session object before any connection to Chrome is opened. It stores the CDP endpoint, chooses the screen size used for model-facing screenshots, and prepares lists and helpers for tabs, downloads, dialogs, loading-state tracking, and background tasks.

**Data flow**: It receives an optional CDP endpoint and optional model name. It uses the model name to choose a coordinate space, then fills the new session with safe starting values: no connection, no tabs, no download folder, empty event lists, and fresh loading and tab-event trackers.

**Call relations**: BuaSurface._open creates this session when it needs a browser control surface. Later, open uses the prepared fields to actually connect to Chrome and wire up browser events.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 66–73)

```
async def open(self) -> None
```

**Purpose**: Opens the session if it is not already open. It is a safe front door around the lower-level browser setup process.

**Data flow**: It reads the current connection field. If a connection already exists, it does nothing; otherwise it runs the bootstrap setup. If setup fails partway through, it calls close so any half-created tabs, folders, or connections are cleaned up before the error is reported.

**Call relations**: __aenter__ calls this when the session is used in an async context manager. It hands the real setup work to _bootstrap and relies on close for rollback when something goes wrong.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 75–108)

```
async def _bootstrap(self) -> None
```

**Purpose**: Performs the full startup sequence that turns a CDP endpoint into a ready-to-use browser session. This is where the control cable is plugged in and all the event listeners are attached.

**Data flow**: It reads the configured CDP endpoint, resolves its WebSocket URL, opens the CDP connection, asks Chrome for version information, creates a temporary download folder, and builds helper readers for tabs, downloads, and dialogs. It then registers event callbacks for downloads, tabs, network loading, page lifecycle events, dialogs, and paused download requests, enables target discovery, creates a blank tab, and stores that tab as the session’s starting page.

**Call relations**: open calls this during startup. It creates tab, download, and dialog readers to attach their event methods to the CDP connection, then stores the initial tab state that later tool calls use.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 4 external calls (__init__, to_thread, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 110–133)

```
async def close(self) -> None
```

**Purpose**: Shuts down the session and returns it to a clean, unopened state. It prevents leftover Chrome tabs, temporary files, background tasks, and stale state from leaking into later work.

**Data flow**: It looks at the current connection and tabs, tries to close each tab, then closes the CDP connection. Whether or not that succeeds, it deletes the temporary download folder, clears tabs, downloads, frame-session records, dialogs, and flags, resets the loading and tab-event trackers, cancels background tasks, and removes the connection reference.

**Call relations**: __aexit__ calls this at normal context-manager teardown. open also calls it if _bootstrap fails, so partial startup does not leave behind resources.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 2 external calls (__init__, to_thread).


##### `BrowserSession.__aenter__`  (lines 135–137)

```
async def __aenter__(self) -> Self
```

**Purpose**: Allows BrowserSession to be used with Python’s async context-manager pattern, which means setup happens automatically at the start of a block. This makes session lifetime easier to reason about.

**Data flow**: It receives the session itself, calls open to establish the browser connection, and then returns the ready session for use inside the surrounding block.

**Call relations**: Code that uses 'async with BrowserSession(...)' enters through this method. It delegates startup to open, and __aexit__ later performs the matching cleanup.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 139–140)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Automatically closes the browser session when an async context-manager block ends. This happens whether the block finishes normally or exits because of an error.

**Data flow**: It receives any exit or exception information from the context-manager machinery, ignores those details here, and calls close to clean up the session.

**Call relations**: It is the paired teardown step for __aenter__. It hands all cleanup work to close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 142–145)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection for helpers that need to talk directly to Chrome. It also protects callers from accidentally using a session that has not been opened.

**Data flow**: It checks the stored connection. If there is no connection, it raises BrowserUnavailable; otherwise it returns the connection object.

**Call relations**: Other browser helper classes can call this when they need the underlying Chrome control channel. It is a guardrail around the shared connection state initialized by _bootstrap and cleared by close.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 147–150)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts a background async task and keeps track of it so it can be cancelled later. This is useful for browser work that must continue while the main tool call moves on.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session’s background-task set, and arranges for the task to remove itself from the set when it finishes.

**Call relations**: Session helpers can use this when they need side work running in the background. close later cancels all tasks still stored in this set.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 152–153)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame is the main page frame rather than an embedded frame. This matters because some events should only count for the main page.

**Data flow**: It receives a session ID and frame ID, creates a tab reader, and asks that reader to decide whether the frame is top-level. It returns a true-or-false answer.

**Call relations**: It is a small doorway from session state into BrowserTabs. Event-related code can use it when interpreting frame events from Chrome.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 155–156)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a CDP sub-session so it is ready for page and frame work. A sub-session is Chrome’s way of talking to a particular target or frame separately from the main connection.

**Data flow**: It receives a session ID, creates a tab reader, and asks it to perform the needed setup for that session. It returns when setup is complete.

**Call relations**: This delegates session setup to BrowserTabs, which knows how tab and frame sessions should be prepared.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 158–159)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the active tab, or a requested tab, in a safe and consistent way. It is the common path for code that needs to know which browser tab to operate on.

**Data flow**: It receives an optional tab ID, creates a tab reader, and asks it to find or select the matching page tab. The result is a Tab object.

**Call relations**: Higher-level tool calls use this through BrowserTabs rather than inspecting the session’s tab list directly.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 161–162)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a browser tab to a URL. This is the session-level wrapper for the user-facing navigation tool.

**Data flow**: It receives a URL and optional tab ID, creates a tab reader, and asks it to perform the navigation. It returns a JSON-like dictionary describing the result.

**Call relations**: External browser tool code can call this on the session. The detailed tab and Chrome commands live in BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 164–165)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a readable information record for a specific tab. This lets the rest of the system describe a tab without knowing Chrome’s internal tab details.

**Data flow**: It receives a Tab object, creates a tab reader, and asks it to turn that tab into a JSON-like information dictionary.

**Call relations**: It delegates formatting and tab-state interpretation to BrowserTabs, keeping BrowserSession as the coordinator.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 167–168)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about the currently known browser tabs. This helps tools or agents understand what tabs exist before choosing an action.

**Data flow**: It creates a tab reader, which reads the session’s tab state and returns a JSON-like dictionary describing the tab context.

**Call relations**: It is part of the tab tool surface and passes the real work to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 170–171)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of known tabs. This is a lightweight way to summarize open pages.

**Data flow**: It creates a tab reader, which reads the session’s tab list and returns a list of title strings.

**Call relations**: It is another simple wrapper around BrowserTabs, used when callers only need names rather than full tab records.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 173–174)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper tied to this session. That helper knows how to create, close, attach, navigate, and describe tabs.

**Data flow**: It takes the current session and fixed viewport size, builds a BrowserTabs object, and returns it. The helper can then read and update the session’s shared tab state.

**Call relations**: _bootstrap uses it to attach initial tab event listeners and create the first tab. Many session methods call it whenever they need tab-specific behavior.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 176–177)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page structure tasks, such as resolving element references and finding screen points. It focuses on the shape of the page and its frames.

**Data flow**: It takes the current session, viewport size, and maximum frame depth, builds a BrowserPage object, and returns it.

**Call relations**: resolve_ref and ref_point call this when they need page-structure logic. BrowserSession stays as the shared-state holder while BrowserPage does the page-specific work.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 179–180)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading what is on the page. This covers structured page trees, text extraction, page summaries, and find operations.

**Data flow**: It passes the current session into BrowserContent and returns the new helper. The helper can use the session connection and tab state to inspect page content.

**Call relations**: tree, read_page, get_page_text, and find all call this and then delegate the content-related task to BrowserContent.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 182–183)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for tracking and waiting on downloads. It uses a fixed maximum wait time so download waits do not hang forever.

**Data flow**: It passes the current session and maximum wait seconds into BrowserDownloads, then returns that helper.

**Call relations**: _bootstrap uses it to register download event callbacks with Chrome. wait_for_download uses it later to wait for a file to finish downloading.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 185–186)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for browser pop-up dialogs, such as JavaScript alert boxes. These dialogs can block page actions if they are not noticed.

**Data flow**: It passes the current session into BrowserDialogs and returns the helper.

**Call relations**: _bootstrap uses this helper’s dialog callback when wiring Chrome’s dialog-opening event.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 188–189)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form-related actions. This includes filling inputs and preparing file uploads.

**Data flow**: It passes the current session into BrowserForms and returns the helper, which can then use the browser connection and page state to interact with forms.

**Call relations**: form_input and upload_file call this when a user-facing form tool is invoked.

*Call graph*: called by 2 (form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 191–192)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript inside the browser. This is used when the system needs information or actions that are easiest to perform in page script.

**Data flow**: It passes the current session into BrowserRuntime and returns the helper.

**Call relations**: eval_js and call_on use this helper to run JavaScript expressions or call JavaScript functions on browser objects.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 194–195)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page. This exposes tab creation as a session-level tool.

**Data flow**: It receives an optional URL, creates a tab reader, and asks it to create a tab at that URL. It returns a JSON-like result describing the new tab or operation.

**Call relations**: It delegates all Chrome target creation and session tab-state updates to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 197–198)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab based on arguments from a tool call. It provides a public session-level wrapper around tab closing.

**Data flow**: It receives a JSON-like argument dictionary, creates a tab reader, and asks that reader to close the requested tab. It returns a JSON-like result.

**Call relations**: It is part of the tab tool surface and relies on BrowserTabs to interpret the arguments and update tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 200–201)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Prepares or performs a file upload through a page form. This is needed because browser file inputs require special handling compared with normal typing.

**Data flow**: It receives a JSON-like argument dictionary, creates a form reader, and asks it to upload the file according to those arguments. It returns a JSON-like result.

**Call relations**: It passes upload work to BrowserForms, which knows the details of interacting with form controls.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.tree`  (lines 203–204)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a structured tree view of page content. This gives the rest of the system a map of what is on the page, optionally filtered by type.

**Data flow**: It receives tool arguments and a filter type, creates a content reader, and asks it to build the requested tree. It returns the tree as a string.

**Call relations**: It is a content-reading tool wrapper. BrowserContent performs the actual page inspection.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 206–207)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page in a richer way than plain text, returning page information suited for browser automation. It helps an agent understand what it can see and act on.

**Data flow**: It receives a JSON-like argument dictionary, creates a content reader, and asks it to read the page. It returns a JSON-like result.

**Call relations**: It delegates content extraction to BrowserContent, using BrowserSession only as the shared connection and state holder.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 209–210)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts text from the current page. This is the simpler page-reading path when callers mainly need words rather than a structured page map.

**Data flow**: It receives a JSON-like argument dictionary, creates a content reader, and asks it for page text. It returns a JSON-like result containing that text and related information.

**Call relations**: It is part of the content tool surface and hands the real page-reading work to BrowserContent.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 212–213)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches the page for text or matching content. It can optionally use a completion helper to support interactive or progressive find behavior.

**Data flow**: It receives search arguments and an optional FindCompleter, creates a content reader, and asks it to perform the find operation. It returns a JSON-like result with the outcome.

**Call relations**: It routes find requests to BrowserContent, which knows how to inspect and search the page.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 215–216)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Fills or edits form fields on the page. This is the session-level entry for typing into inputs in a browser-aware way.

**Data flow**: It receives a JSON-like argument dictionary, creates a form reader, and asks it to perform the input action. It returns a JSON-like result.

**Call relations**: It delegates form interaction to BrowserForms, which understands how to target and fill page controls.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 218–219)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs computer-style browser actions, such as mouse, keyboard, or screen-coordinate interactions. This is useful when an action is closer to operating the browser like a person than reading page data.

**Data flow**: It receives a JSON-like argument dictionary, builds a BrowserComputer helper with the session, viewport, and wait limit, and asks it to run the action. It returns a JSON-like result.

**Call relations**: Unlike many wrappers, it creates BrowserComputer directly for each call. BrowserComputer then uses the session’s shared connection and state to perform the requested action.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 221–224)

```
async def wait_for_download(self, args: JsonDict) -> JsonDict
```

**Purpose**: Waits for a browser download to finish and reports the result. It first checks that the session is open because downloads need the temporary download folder created during startup.

**Data flow**: It receives a JSON-like argument dictionary. If there is no download folder, it raises BrowserUnavailable; otherwise it creates a download reader and asks it to wait for a matching download in that folder. It returns a JSON-like result.

**Call relations**: _bootstrap creates the download folder and registers download event listeners. This method uses BrowserDownloads later when a caller needs to wait for a file.

*Call graph*: calls 1 internal fn (download_reader); 1 external calls (__init__).


##### `BrowserSession.eval_js`  (lines 226–227)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a browser session. This gives lower-level helpers a direct way to ask the page something or compute something in the page’s own environment.

**Data flow**: It receives a CDP session ID and JavaScript expression, creates a runtime reader, and asks it to evaluate the expression. It returns the JSON-like value Chrome reports back.

**Call relations**: It delegates JavaScript execution to BrowserRuntime, keeping BrowserSession focused on routing and shared state.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 229–236)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on a specific browser-side object. This is useful when code already has a reference to an object inside the page and needs to ask that object to do something or return data.

**Data flow**: It receives a session ID, object ID, function source text, and optional argument values. It creates a runtime reader, passes those details along, and returns a JSON-like result from the call.

**Call relations**: It is the object-specific partner to eval_js. BrowserRuntime handles the Chrome protocol details for making the function call.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 238–239)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame and index it points to. This helps other tools convert human- or model-facing references into concrete page locations.

**Data flow**: It receives a Tab and reference string, creates a page reader, and asks it to resolve the reference. It returns a frame node and numeric index.

**Call relations**: It delegates page-reference interpretation to BrowserPage, which understands frame trees and element reference numbering.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 241–242)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen point for a referenced page item. This is useful before clicking or otherwise interacting with something visible on the page.

**Data flow**: It receives a Tab and reference string, creates a page reader, and asks it to compute the point for that reference. It returns x and y coordinates.

**Call relations**: It builds on BrowserPage’s knowledge of page structure and coordinates. Tools that need physical interaction can use this to turn a page reference into a click location.

*Call graph*: calls 1 internal fn (page_reader).


### Browser resource lifecycles
These managers handle long-lived browser resources such as downloads, paused requests, tabs, and navigation targets.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`io_transport` · `request handling and download waiting`

Browsers do not treat every downloadable file the same way. A PDF, for example, may open inside Chrome’s built-in viewer instead of becoming a saved file, and that viewer may not expose useful page content to the agent. This file solves that by watching browser events and, when needed, nudging certain top-level documents such as PDFs to download as attachments instead of opening inline.

It works like a traffic officer for browser network requests. When Chrome pauses a request through the Chrome DevTools Protocol, or CDP (Chrome’s remote-control API), this file decides whether to simply wave the request through or rewrite the response headers so Chrome saves the file. It is careful to release every paused request, because leaving one paused would make the page hang.

The file also keeps a small in-memory list of downloads. When a download starts, it records its browser-provided id, suggested filename, and current state. As progress events arrive, it updates that state. Later, callers can wait until a download finishes, read the downloaded bytes from disk, and receive them as base64 text, which is a safe way to carry binary file data inside JSON.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 43–49)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the required shape of the object that can send commands to the browser through CDP, the Chrome DevTools Protocol. Code in this file depends on something implementing this method so it can tell Chrome to continue paused network requests.

**Data flow**: It receives a browser command name, optional command details, and optionally a browser session id. An implementation sends that command to Chrome and returns Chrome’s JSON-style reply.

**Call relations**: The download helper gets this sender through BrowserDownloadSession.connection. The private continuation methods use it when they need to release a paused request or response back to the browser.


##### `BrowserDownloadSession.connection`  (lines 55–55)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This is the required way for the download code to reach the live browser connection. It separates the download logic from the exact connection implementation.

**Data flow**: It takes no extra data. It returns an object capable of sending CDP commands to the browser.

**Call relations**: BrowserDownloads uses this session method inside its request-continuation helpers. Those helpers then call BrowserDownloadCdp.send to tell Chrome what to do next.


##### `BrowserDownloadSession.spawn_background`  (lines 57–57)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the required way to start a small asynchronous task without blocking the current browser event callback. It lets the code release browser requests in the background.

**Data flow**: It receives a coroutine, meaning a paused piece of asynchronous work. The session implementation schedules it to run, and there is no direct return value to the caller.

**Call relations**: BrowserDownloads.on_fetch_paused uses this whenever Chrome pauses a request. That keeps the event handler quick while _continue_request or _continue_response does the actual browser command.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 59–59)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This is the required check for whether a browser request belongs to the main page frame rather than an embedded frame. It matters because only main-page PDF navigations should be forced into downloads.

**Data flow**: It receives the browser session id and frame id from a browser event. It returns true if that frame is the top-level page frame, and false otherwise.

**Call relations**: BrowserDownloads.on_fetch_paused calls this before forcing a response to download. That prevents embedded PDFs or subframe documents from being changed in ways that would break normal page rendering.


##### `BrowserDownloads.on_fetch_paused`  (lines 67–88)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This reacts when Chrome pauses a network request. Its main job is to make sure the request is resumed, and to turn top-level PDFs into downloads when the agent would otherwise be stuck with Chrome’s built-in viewer.

**Data flow**: It receives event data from Chrome and an optional session id. It reads the request id, response status, headers, frame id, and content type; then it either schedules a plain request continuation or schedules a response continuation that may add a download header.

**Call relations**: This is the entry point for paused fetch events in this file. It calls _content_type to understand the response, asks the session whether the frame is top-level, and then hands work to _continue_request or _continue_response through spawn_background so the browser is not left waiting.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 90–96)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This tells Chrome to resume a paused request when there is no response information to inspect yet. Without this, the browser tab could hang on a request that was never released.

**Data flow**: It receives the session id and browser request id. It sends a Fetch.continueRequest command to Chrome; if the command fails, it logs a warning instead of crashing the whole download flow.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this when the paused event is at the request stage rather than the response stage. It uses the session’s browser connection to send the actual CDP command.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 98–126)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This tells Chrome to resume a paused response, optionally changing it so Chrome saves the file as a download. It is the part that turns viewer-only content, such as a top-level PDF, into an attachment.

**Data flow**: It receives the session id, request id, response status code, response headers, and a true-or-false force flag. If forcing is needed, it removes any old Content-Disposition header and adds Content-Disposition: attachment; then it sends Fetch.continueResponse to Chrome. On failure, it logs a warning.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this after it has enough response information to decide what to do. This function is the handoff point from policy decision to the concrete browser command.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 128–135)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records a new download when the browser reports that one has started. It gives later code something to wait on and a filename to return to the caller.

**Data flow**: It receives Chrome’s download-start event data. It pulls out the download guid and suggested filename, creates a Download record with state set to inProgress, and appends it to the session’s download list.

**Call relations**: This is called when the browser emits a download-begin event. The records it creates are later updated by on_download_progress and inspected by became_download and wait.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 137–142)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This updates the stored state of a download as Chrome reports progress. It lets the rest of the code know when a download has completed.

**Data flow**: It receives Chrome’s progress event data, reads the download guid and new state, finds the matching stored download, and changes that download’s state in place.

**Call relations**: This follows on_download_begin in the download event flow. BrowserDownloads.wait relies on these state updates to decide when it can safely read the finished file.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 144–150)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This briefly checks whether an action that looked like navigation actually turned into a download. It gives the browser a short grace period to report the new download.

**Data flow**: It receives the number of downloads that existed before an action. It repeatedly compares the current download count against that old count until either a new download appears or the grace time runs out, then returns true or false.

**Call relations**: This is useful after navigation-like actions that may produce a file instead of a page. It watches the same download list populated by on_download_begin, sleeping briefly between checks.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 152–170)

```
async def wait(self, args: JsonDict, download_dir: str) -> JsonDict
```

**Purpose**: This waits for a browser download to finish and returns the downloaded file contents in JSON-friendly form. It is the caller-facing way to retrieve the latest completed download.

**Data flow**: It receives arguments that may include a timeout and the directory where Chrome stores downloads. It waits until a stored download has state completed, reads the file whose name is the download guid, encodes the bytes as base64 text, and returns the original filename, encoded content, and byte size. If nothing finishes in time, it raises a timeout error.

**Call relations**: This depends on on_download_begin and on_download_progress having filled and updated the session’s download list. It calls float_or_default to interpret the timeout, uses short sleeps while polling, and reads the completed file from disk in a background thread so it does not block the async event loop.

*Call graph*: calls 1 internal fn (float_or_default); 6 external calls (sleep, to_thread, b64encode, Path, monotonic, get).


##### `float_or_default`  (lines 173–182)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This turns a user-provided timeout value into a number, or uses a default when no timeout was provided. It protects callers from silently accepting invalid timeout data.

**Data flow**: It receives a JSON value and a default number. If the value is an integer, floating-point number, or non-empty string, it converts it to a float; if the value is missing, it returns the default; otherwise it raises a validation error.

**Call relations**: BrowserDownloads.wait calls this before it starts waiting. That keeps timeout parsing in one small helper and lets wait focus on the download itself.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 185–189)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This extracts the main content type from HTTP response headers, such as turning application/pdf; charset=utf-8 into application/pdf. The download code uses that to decide whether a response should be forced into a download.

**Data flow**: It receives a list of header-like JSON values. It searches for a Content-Type header, normalizes its name and value, removes any extra parameters after a semicolon, and returns the cleaned lowercase type; if none is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this while deciding whether a paused top-level response is one of the types that should be downloaded instead of displayed in Chrome’s viewer.

*Call graph*: called by 1 (on_fetch_paused).


### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling and browser event handling`

A browser automation system needs a reliable idea of which tabs exist, which page each tab shows, and when a page is ready enough to use. This file provides that. It listens for browser events about tabs being created or destroyed, attaches to new tabs so they can be controlled, and removes tabs that have closed. Think of it like the front desk at a hotel: it keeps the room list up to date, gives new rooms their keys, and knows which rooms are occupied.

The main working object is `BrowserTabs`. It uses a browser session object to send commands to the browser, read downloads, evaluate small JavaScript snippets, and wait for pages to settle. A `Tab` stores the identifiers needed to talk to one browser tab, plus per-tab keyboard and frame information. The file also defines small protocol interfaces, which describe what the surrounding browser session and connection must provide.

Navigation is careful. Plain addresses such as `example.com` are turned into `https://example.com`, while special commands like `back` and `forward` are treated as history movement. When a page starts loading, finishes basic document loading, or paints content, event callbacks update the settle tracker so later code can wait until the page is usable. The file also notices when a navigation actually becomes a file download, so it does not report that as a normal page failure.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: This turns a user-supplied destination into something the browser can navigate to. It leaves special words like `back`, `forward`, and already complete URLs alone, but adds `https://` to plain site names.

**Data flow**: It receives a text URL or command. It checks whether the text is a special navigation command, a blank page, or already has a URL scheme such as `http:` or `file:`. It returns the original text if it is already complete, otherwise it returns the same text with `https://` added in front.

**Call relations**: `BrowserTabs.navigate` calls this before deciding whether to move through history or load a new address. This keeps navigation input forgiving for callers while still sending a valid target to the browser.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: This creates the in-memory record for one open browser tab. It stores the browser identifiers needed to control the tab and prepares helper state for keyboard input and frames inside the page.

**Data flow**: It receives a browser target ID and a session ID. It saves both, creates a fresh keyboard state, starts an empty frame-number map, and creates a root frame record for the main page area. The result is a `Tab` object ready for later browser commands.

**Call relations**: `BrowserTabs.attach_tab` calls this after the browser confirms that automation has attached to a target. The new `Tab` is then added to the session’s tab list by callers such as `sync`, `page`, and `create`.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: This gives each frame inside a tab a stable small number. It is useful when the system needs a human-friendly or repeatable label for browser frames.

**Data flow**: It receives a frame ID from the browser. If that frame has not been seen before, it assigns the next available number and stores it. It returns the stored number for that frame.

**Call relations**: This method belongs to the tab’s frame bookkeeping. It is not called elsewhere in this file, but it supports other code that needs consistent frame labels while inspecting or interacting with a page.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes how to send one command to the browser through Chrome DevTools Protocol. A protocol is a promise about what methods another object must have, rather than an implementation here.

**Data flow**: A caller provides a command name, optional command data, and optionally a session ID for a particular tab. The implementing connection sends that to the browser and returns the browser’s response as a dictionary.

**Call relations**: Many `BrowserTabs` methods rely on this connection method to attach to tabs, enable browser features, navigate pages, create tabs, and close tabs. The real sending behavior is supplied by another class outside this file.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This protocol method describes how code can start waiting for a future browser event. It is used when a command should be followed by a specific signal, such as a page load event.

**Data flow**: A caller names one or more events and optionally a tab session. The implementing connection returns a future, which is a placeholder for the event data that will arrive later.

**Call relations**: `BrowserTabs._goto` and `_history_step` use this idea before sending navigation commands, so they do not miss the event that proves the browser moved.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: This protocol method describes how to wait for an expected browser event with a time limit. The timeout prevents the automation from hanging forever.

**Data flow**: A caller passes a future from `expect` and a timeout in seconds. The implementing connection waits until the event arrives or the timeout is reached, then returns the event data or raises an error.

**Call relations**: Navigation helpers use this after asking the browser to move to a new page or history entry. The actual waiting is performed by the connection object supplied by the browser session.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: This protocol method describes how to get the browser command connection. It lets `BrowserTabs` stay independent from the concrete connection class.

**Data flow**: It takes the browser session object as input. The implementing session returns an object that can send browser commands and wait for browser events.

**Call relations**: Almost every active operation in `BrowserTabs` starts by asking the session for this connection. The method itself is only a required shape here; another file provides the real behavior.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: This protocol method describes how to get the component that checks browser downloads. It matters because some navigations do not show a page; they start a file download instead.

**Data flow**: It reads the browser session and returns a downloads helper. That helper can later compare download state before and after navigation.

**Call relations**: `BrowserTabs._goto` uses the returned helper when navigation reports an error, to decide whether the “failure” was actually a valid download.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: This protocol method describes how to run a small JavaScript expression inside a tab. It is used here to ask the page for its current URL and title.

**Data flow**: A caller provides a tab session ID and JavaScript text. The implementing session runs that script in the page and returns the JSON-like result.

**Call relations**: `BrowserTabs.tab_info` depends on this method to produce readable tab summaries. The actual JavaScript execution is implemented outside this file.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: This records the tabs or browser targets that already existed when automation started. Later, the code can tell the difference between old tabs and tabs newly opened during this run.

**Data flow**: It receives browser target information as JSON-like data. It reads the `targetInfos` list, extracts each `targetId`, converts it to text, and stores the set as `initial_targets` in the session’s tab event state.

**Call relations**: This prepares the event-tracking system used by `on_target_created`. Without it, the code might mistake tabs that were already open for tabs newly created by current activity.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This reacts when the browser reports that a new target was created, and records new page tabs that should be attached later. A target is a controllable browser object, such as a tab.

**Data flow**: It receives event data and an optional session ID. It looks for a `targetInfo` dictionary whose type is `page`, checks that the target ID is real, ignores targets that existed at startup, and appends truly new target IDs to the created-target queue.

**Call relations**: This is an event callback. It does not attach immediately; it leaves the target ID in `tab_events.created_targets` so `BrowserTabs.sync` can attach in an orderly way.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This reacts when the browser reports that a target was destroyed. In plain terms, it remembers that a tab or page target has closed.

**Data flow**: It receives event data and reads the `targetId`. If the ID is text, it adds it to the destroyed-target set in the browser’s tab event state.

**Call relations**: This callback feeds `BrowserTabs.sync`. On the next sync, closed target IDs are used to remove matching `Tab` objects from the session’s open tab list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This notices when the main frame of a tab begins loading. It tells the settle tracker that the page is no longer ready yet.

**Data flow**: It receives frame-loading event data and the session ID that produced it. If there is no session ID, it ignores the event. If the frame ID belongs to the top-level frame of that tab, it marks that session as loading.

**Call relations**: Browser event wiring calls this as page loading events arrive. It uses `is_top_level_frame` to avoid treating small embedded frames, like iframes, as full-page navigation.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This notices when a page has reached the basic document-ready point. That means the browser has parsed the page’s main HTML, though images or later activity may still be happening.

**Data flow**: It receives event data and a session ID. If there is a session ID, it marks that session as loaded in the settle tracker. It does not return a value.

**Call relations**: This is part of the page-readiness story used by navigation. Later, `BrowserTabs.navigate` asks the settle tracker to wait until the page has reached enough readiness signals.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This watches for important page lifecycle events related to painting, meaning the page has drawn visible content. It helps the system avoid acting too early on a blank or half-rendered page.

**Data flow**: It receives lifecycle event data and a session ID. It ignores events without a session, ignores event names that are not paint-related, then checks whether the event came from the tab’s main frame. If so, it marks the page as painted.

**Call relations**: Browser event wiring calls this while pages load. Like `on_frame_loading`, it uses `is_top_level_frame` so only the main page frame affects the tab’s readiness state.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This answers whether a browser frame ID is the main frame for a known tab. The main frame is the page itself, not an embedded frame inside it.

**Data flow**: It receives a session ID and a frame ID. It scans the known tabs and returns true only when it finds a tab whose session ID matches and whose target ID matches the frame ID.

**Call relations**: `on_frame_loading` and `on_lifecycle` call this before updating settle state. This keeps loading and paint signals focused on whole-page readiness.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: This connects automation to a browser tab so the project can control it. It also prepares that tab with the browser features this system expects.

**Data flow**: It receives a browser target ID. It asks the browser to attach to that target and reads back a session ID. It initializes page, DOM, lifecycle, and network support, enables document-fetch interception, applies the configured viewport size, and returns a new `Tab` object.

**Call relations**: `sync` uses this for newly discovered tabs, `page` uses it when it has to create the first tab, and `create` uses it for an explicitly opened tab. It delegates setup details to `init_session` and finishes by constructing `Tab`.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: This turns on the browser event streams and features needed for a tab session. Without these commands, the code would not receive page, DOM, network, or lifecycle information.

**Data flow**: It receives a tab session ID. It sends browser commands to enable page events, lifecycle events, DOM access, and network events for that session. It returns nothing, but the browser session is now ready for richer automation.

**Call relations**: `attach_tab` calls this immediately after attaching to a target. It is the setup step before downloads, viewport sizing, and the final `Tab` object are prepared.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: This reconciles the project’s tab list with browser events that have arrived. It removes tabs that closed and attaches to tabs that were newly opened.

**Data flow**: It reads the queued created and destroyed target IDs from `browser.tab_events`. It clears destroyed IDs after using them to filter the open tab list. Then it processes created target IDs one by one, skipping duplicates and trying to attach to each new tab. Tabs that cannot be attached are ignored.

**Call relations**: `page` calls this before returning a tab, and `tabs_context` calls it before building a tab summary. Event callbacks fill the queues that this method drains.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: This returns the requested tab, or the current tab if no tab number is provided. If no tabs exist at all, it creates a blank one so callers always have a page to work with.

**Data flow**: It receives an optional tab ID. It first syncs the known tabs with browser events. If the tab list is empty, it asks the browser to create `about:blank`, attaches to it, and stores it. Then it returns the last tab for a missing ID, returns the requested tab for a valid ID, or raises a validation error for an invalid ID.

**Call relations**: `navigate` calls this to choose where to navigate, and `close` calls it to choose what to close. It uses `sync` for freshness and `attach_tab` when it must create the first usable tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: This moves a tab to a new page, or moves it backward or forward in its history. It waits afterward until the page is considered settled enough for later automation.

**Data flow**: It receives a URL or navigation command and an optional tab ID. It gets the target tab, normalizes the URL text, resets the settle tracker, then either performs a history step or sends a normal page navigation. After that, it waits for settle signals and returns the tab’s current URL and title.

**Call relations**: `create` calls this after opening a new tab to load the requested URL. Internally it uses `page`, `normalize_url`, `_history_step`, `_goto`, and `tab_info` to complete the full navigation story.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: This performs a normal navigation to a specific URL in one tab. It carefully waits for the page’s document-loaded signal and distinguishes real failures from downloads.

**Data flow**: It receives a `Tab` and a complete URL. It records how many downloads existed before navigation, starts waiting for the DOM-content event, and sends the browser’s navigate command. If the browser reports an error, it cancels the wait, checks whether a download started, and either accepts the download case or raises a runtime error. If no loader ID is returned, it cancels the wait because no full load is expected. Otherwise it waits up to the navigation timeout.

**Call relations**: `BrowserTabs.navigate` calls this for ordinary URL navigation. It works closely with the browser connection’s event-waiting methods and with the download reader from the browser session.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: This moves a tab backward or forward in its browsing history. If there is no entry in that direction, it simply does nothing.

**Data flow**: It receives a `Tab` and a step number, where negative means back and positive means forward. It asks the browser for the tab’s navigation history, checks the current index, computes the destination index, and stops if that index is outside the history list. Otherwise it waits for a navigation-related event, tells the browser to navigate to the chosen history entry, and waits up to the history timeout.

**Call relations**: `BrowserTabs.navigate` calls this when the normalized target is `back` or `forward`. It uses browser history data and event waiting to make history movement behave like normal navigation.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: This returns the human-readable basics for one tab: its current address and page title. It gets this directly from the page using JavaScript.

**Data flow**: It receives a `Tab`. It evaluates a small JavaScript expression in that tab that reads `location.href` and `document.title`, validates the returned object, and returns a dictionary with string `url` and `title` fields.

**Call relations**: `navigate` uses this for its final result, `tabs_context` uses it for each listed tab, and `tab_titles` uses it to collect just titles. It depends on the browser session’s JavaScript evaluation method.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: This builds a summary of all open tabs for callers that need to display or reason about tab state. It marks the last tab in the list as the current active tab.

**Data flow**: It first syncs tab state with browser events. It computes the current tab index, then visits each known tab, asks for its URL and title, and builds a list of tab dictionaries containing ID, active flag, URL, and title. It returns that list plus the current tab ID.

**Call relations**: `close` calls this after removing a tab so the caller receives the updated tab picture. It uses `sync` to refresh the list and `tab_info` to fill in readable page details.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This collects the titles of all known tabs. It is a compact helper for code that only needs names, not full tab details.

**Data flow**: It reads the current tab list. For each tab, it asks `tab_info` for the page data, takes the title, converts missing titles to an empty string, and returns the list of title strings.

**Call relations**: This method builds on `tab_info` rather than reading the page title itself. It is not called elsewhere in this file, but it gives other parts of the system a simple title-only view.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: This opens a new browser tab and navigates it to the requested URL. If no URL is given, the new tab stays on a blank page.

**Data flow**: It receives an optional URL. It asks the browser to create a blank target, attaches to that target, adds the resulting `Tab` to the tab list, navigates that specific tab to the requested URL, and returns the new tab’s ID together with its URL and title.

**Call relations**: This is the explicit new-tab path. It uses `attach_tab` for setup, then reuses `navigate` so new-tab loading follows the same waiting and error behavior as any other navigation.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: This closes a selected browser tab and returns the updated tab list. It also clears out-of-process frame session tracking, because closing a tab can make those frame sessions stale.

**Data flow**: It receives an argument dictionary, reads and converts `tab_id`, finds the matching tab, asks the browser to close that tab’s target, removes the tab object from the open tab list, clears `oop_sessions`, and returns the current tab context.

**Call relations**: This method uses `_tab_id` to interpret the caller’s tab ID, `page` to validate and fetch the tab, and `tabs_context` to report the result after closing.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This converts a loosely typed tab ID from caller input into either an integer or `None`. It accepts common JSON-style forms such as numbers and non-empty strings.

**Data flow**: It receives a JSON-like value. If the value is an integer, it returns it. If it is a float, it truncates it to an integer. If it is a non-empty string, it parses it as an integer. For anything else, it returns `None`.

**Call relations**: `BrowserTabs.close` calls this before choosing which tab to close. Returning `None` lets `close` fall back to the current tab behavior provided by `page`.

*Call graph*: called by 1 (close).


### CDP page interaction helpers
These modules provide the CDP command/event pipe and higher-level helpers for dialogs, page JavaScript execution, and action settling.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `active during browser session startup and throughout browser automation runtime`

Chrome exposes a control channel called the Chrome DevTools Protocol, or CDP: a set of messages that let another program inspect pages, click things, listen for navigation, and so on. This file wraps that raw message stream into something safer and easier to use. Without it, the rest of the browser automation code would have to manually number every command, match every reply, watch for timeouts, and avoid getting stuck forever if Chrome disconnects.

The main class, CdpConnection, owns one WebSocket connection to Chrome. A WebSocket is a long-lived network conversation, like keeping a phone line open instead of making a new call for every sentence. When code sends a CDP command, this class gives it a unique id, remembers which future reply belongs to that id, sends the JSON message, and waits for the matching response. At the same time, a background reader task continuously receives messages from Chrome. Replies are routed back to the waiting command. Browser events are either given to one-time waiters, such as “tell me when this event happens,” or fanned out to registered listeners, such as “call this function every time this event happens.”

The file is also careful about failure. Commands and event waits have time limits. If the connection closes, every pending wait is failed loudly instead of silently hanging.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Builds a clear Python error when Chrome says a CDP command failed. It keeps the failed command name and Chrome’s numeric error code so callers can understand what went wrong.

**Data flow**: It receives the CDP method name, an error code, and an error message from Chrome. It stores the method and code on the error object, then creates a readable message such as “CDP X failed...” that can be raised to the caller.

**Call relations**: When CdpConnection._dispatch sees that a command reply contains an error instead of a result, it creates this error and puts it into the waiting command’s future. That makes the original CdpConnection.send call fail with useful detail.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual DevTools WebSocket address to connect to. It accepts either a WebSocket URL directly or an HTTP DevTools base URL that needs to be queried first.

**Data flow**: It takes a URL and HTTP headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise it asks the browser’s /json/version endpoint for connection details, checks that the HTTP request succeeded, extracts webSocketDebuggerUrl from the JSON response, and returns it as text.

**Call relations**: This is a setup helper used before opening a CDP connection. It talks through httpx to fetch Chrome’s advertised WebSocket address and uses as_str to make sure the expected JSON field is really a string.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Creates the in-memory bookkeeping for one live CDP connection. It starts with an existing WebSocket and prepares places to track pending commands, event listeners, and one-time event waits.

**Data flow**: It receives an already-open WebSocket connection. It stores that socket, initializes the next command id at zero, creates empty collections for pending command replies, event listeners, and event waiters, and records that no background reader task has been attached yet.

**Call relations**: CdpConnection.open constructs the object after dialing Chrome. The rest of the methods rely on these stored collections to match outgoing commands with incoming replies and to route incoming events.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a WebSocket connection to Chrome and starts the background reader that listens for incoming CDP messages. This is the normal way to create a ready-to-use CdpConnection.

**Data flow**: It receives the WebSocket URL and optional headers. It connects to Chrome with a large allowed message size, wraps the socket in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the connection object.

**Call relations**: BrowserSession._bootstrap calls this while setting up a browser session. After this method returns, other code can call send, expect, wait, and on while the reader task quietly routes incoming messages behind the scenes.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader task and then closes the WebSocket.

**Data flow**: It looks at the stored reader task. If there is one, it cancels it, waits for the cancellation to finish while ignoring the normal cancellation exception, clears the stored task reference, and then closes the WebSocket.

**Call relations**: This is used when the browser session is being torn down or the connection is no longer needed. It complements CdpConnection.open by cleaning up the long-running reader and network connection.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one CDP command to Chrome and waits for that command’s matching response. It protects callers from having to manually number messages or search the incoming stream for the right reply.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for commands aimed at a specific target such as a tab. It assigns a new numeric id, builds a JSON message, creates a future to hold the reply, stores that future under the id, sends the message over the WebSocket, and waits up to the command timeout. If Chrome replies successfully, the result dictionary comes back. If Chrome reports an error or the timeout expires, the caller gets an exception.

**Call relations**: Higher-level browser code uses this as the main command path into Chrome. The matching response is not read here directly; instead, _read_loop receives all incoming messages and _dispatch completes the stored future for this command id.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a callback to run whenever a named CDP event arrives. It is for ongoing subscriptions, such as “notify me every time this browser event happens.”

**Data flow**: It receives an event name and a listener function. It adds the listener to the list for that event, creating the list if this is the first listener for that event. It does not return a value.

**Call relations**: When _dispatch later receives an event message with this name, it calls each registered listener with the event parameters and the session id, if Chrome included one.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one of several CDP events. It is useful when code is about to do something and then wants to pause until a specific browser event confirms it happened.

**Data flow**: It receives one or more event names and optionally a session id to narrow the wait to one tab or target. It creates a future, stores it with the event names and session filter, and returns the future to the caller. The future will later contain the event parameters.

**Call relations**: Callers usually pair this with CdpConnection.wait, which adds the timeout. _dispatch watches incoming events and completes this future when one of the requested event names arrives for the right session.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for a future created by expect, with a timeout and cleanup. It prevents abandoned event waits from staying in memory forever.

**Data flow**: It receives a future and an optional timeout. It waits for the future to complete within that time and returns the event parameters if it succeeds. Whether it succeeds, times out, or is cancelled, it removes that future from the internal waiter list afterward.

**Call relations**: This is the timeout wrapper around CdpConnection.expect. _dispatch is responsible for completing the future when the event arrives; this function is responsible for waiting safely and removing the waiter when the wait is over.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads raw messages from Chrome and sends each parsed message to the router. It is the background worker that keeps commands and events moving.

**Data flow**: It reads each incoming WebSocket message as raw JSON text, turns it into a Python dictionary, and passes it to _dispatch. If the WebSocket closes, it stops reading. In all exit cases, it fails every still-pending command and event wait so no caller is left waiting forever.

**Call relations**: CdpConnection.open starts this as a background task. It is the only place that consumes the WebSocket’s incoming stream, and it hands each message to _dispatch for interpretation.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Routes one incoming CDP message to the right waiting command, event waiter, or event listener. It is the traffic controller for the WebSocket stream.

**Data flow**: It receives a decoded JSON message. If the message has a numeric id, it treats it as a command response, finds the stored pending command with that id, and either completes it with the result or fails it with CdpError. If the message is an event, it extracts the event name, parameters, and optional session id. It completes matching one-time waiters and then calls any ongoing listeners registered for that event.

**Call relations**: _read_loop calls this for every message received from Chrome. It completes futures created by send and expect, creates CdpError when Chrome reports command failure, and invokes listener functions registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `request handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, or “are you sure you want to leave?” warnings. These dialogs are a problem for an automated browser because they stop the page until someone answers them. While the dialog is open, later browser events can be blocked too, like a traffic jam caused by one car stopping in the middle of the road.

This file provides the small piece of logic that reacts when such a dialog appears. It decides what answer is safest. Simple alerts and page-unload warnings are automatically accepted, because there is usually no useful alternative. Confirmation boxes and prompts are dismissed, because accepting them could accidentally approve a website’s own safety question, such as “Do you really want to delete this?”

The file also records a plain text note, such as “confirm dismissed: Are you sure?”, so the rest of the agent can know the dialog happened. The actual browser command is sent in the background, meaning the event handler does not wait around and risk blocking more browser work. If answering the dialog fails because the browser connection is gone, times out, or reports a protocol error, the failure is logged as a warning instead of crashing the whole run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser connection used by this file. It describes a method that can send a command to the browser through CDP, the Chrome DevTools Protocol, which is the control channel used to automate Chrome-like browsers.

**Data flow**: It receives the name of a browser command, optional command details, and an optional session identifier for a particular page or frame. It sends that information to the browser and returns the browser’s JSON-like reply.

**Call relations**: BrowserDialogs._answer_dialog relies on this method when it needs to tell the browser to accept or dismiss a JavaScript dialog. The protocol definition lets this file work with any browser connection object that offers the same send behavior.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the live browser control connection from a browser session. The dialog code needs that connection so it can answer the pop-up in the browser.

**Data flow**: It reads the current browser session object and returns an object capable of sending CDP commands. It does not itself answer dialogs; it simply provides the route to the browser.

**Call relations**: BrowserDialogs._answer_dialog calls this before sending the Page.handleJavaScriptDialog command. The method is part of the session interface that BrowserDialogs expects its browser object to provide.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way to start a small asynchronous job without making the caller wait for it. Here it is used so dialog-answering happens immediately but does not hold up the event handler.

**Data flow**: It receives a coroutine, which is a task that can run asynchronously. It schedules that task to run in the background and returns without producing a value.

**Call relations**: BrowserDialogs.on_dialog uses this to launch BrowserDialogs._answer_dialog after it has decided whether to accept or dismiss the dialog. This keeps the dialog event response quick.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog appeared. It chooses a safe response, records a human-readable note about the dialog, and starts the browser command that will close it.

**Data flow**: It receives dialog details from the browser, including the dialog type and message, plus an optional session identifier. It treats missing dialog types as alerts, accepts alerts and before-unload warnings, dismisses other dialog types, appends a note to the browser session’s dialog history, and starts a background task to answer the dialog.

**Call relations**: This is the main entry point in this file for dialog events. When it is called by the browser event flow, it reads fields from the incoming JSON-like data, then hands off to BrowserDialogs._answer_dialog through BrowserDialogSession.spawn_background so the actual CDP command can be sent.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This asynchronous helper sends the actual browser command that accepts or dismisses the open JavaScript dialog. It also protects the rest of the system from common failures while doing so.

**Data flow**: It receives the browser session identifier and the already-chosen answer, true for accept or false for dismiss. It asks the browser session for its connection, sends the Page.handleJavaScriptDialog command with that answer, and returns nothing. If the command fails because of a CDP error, timeout, or runtime problem, it writes a warning log instead of raising the error further.

**Call relations**: BrowserDialogs.on_dialog creates this task whenever a dialog appears. This helper then talks to the browser through BrowserDialogSession.connection and BrowserDialogCdp.send, completing the close-the-dialog step that lets browser automation continue.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `request handling`

This file is a thin wrapper around the browser's JavaScript runtime. The browser is spoken to through the Chrome DevTools Protocol, often shortened to CDP, which is a message-based way for tools to inspect and control Chrome-like browsers. Instead of making the rest of the code build raw CDP messages every time it wants to evaluate JavaScript, this file provides BrowserRuntime as a cleaner helper.

The main idea is simple: send a JavaScript expression or function call to a specific browser target, wait for the browser's reply, check whether the JavaScript failed, and then return the useful value. It is like asking a remote assistant to run a calculation on a web page: this file writes the request in the browser's expected format, checks whether the assistant reported a problem, and hands back only the answer.

Two protocol classes describe what BrowserRuntime needs from the outside world: something that can send CDP messages, and something that can provide that connection. BrowserRuntime then offers two main actions. eval runs a JavaScript expression. call_on runs a JavaScript function against an existing browser-side object. Both use raise_on_exception so that page errors do not get silently treated as normal results.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This defines the shape of an object that can send a Chrome DevTools Protocol message to the browser. It is a promise to BrowserRuntime: give it a method name, optional parameters, and an optional browser session id, and it will return the browser's JSON-like reply.

**Data flow**: The caller provides a protocol method name such as Runtime.evaluate, a dictionary of parameters, and possibly a session id that identifies which browser target to talk to. The concrete connection object sends that request to the browser and returns a dictionary-like response from the browser.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on this capability through browser.connection().send. This file only states the expected interface; the actual sending is supplied elsewhere by a real CDP connection.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This defines the shape of an object that can provide access to the browser's CDP connection. BrowserRuntime uses it so it does not need to know how the browser session stores or creates that connection.

**Data flow**: There are no extra inputs besides the session object itself. The method returns an object that knows how to send CDP messages to the browser.

**Call relations**: BrowserRuntime calls this before sending Runtime.evaluate or Runtime.callFunctionOn messages. The session acts as the doorway, and the returned connection does the actual browser communication.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression in a browser session and returns its value as normal JSON-like Python data. It is used when the code needs to ask the page a direct question, such as reading a variable or computing a small result.

**Data flow**: It receives a browser session id and a JavaScript expression string. It sends a Runtime.evaluate request with returnByValue set, meaning the browser should send back the actual value rather than only a reference to it. After the reply comes back, it checks for a JavaScript exception, then pulls the value out of the browser's nested result object and returns it.

**Call relations**: This is one of the main public actions of BrowserRuntime. It asks the session's CDP connection to send the browser command, then hands the reply to BrowserRuntime.raise_on_exception before using as_map to safely treat nested reply parts as dictionaries.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This calls a JavaScript function on an object that already exists inside the browser, then returns the function's result as a dictionary. It is useful when the code has a browser-side object id and wants to inspect or transform that object without first copying the whole object into Python.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body, and optional argument values. It builds a Runtime.callFunctionOn request, converting each argument into the format the browser expects. The browser runs the function on the chosen object and sends back a result. The method checks for page-side exceptions, extracts the returned value, treats a missing value as an empty dictionary, and returns that dictionary.

**Call relations**: Like eval, this method is a high-level helper over the lower-level CDP send call. It uses BrowserRuntime.raise_on_exception to stop failed JavaScript from looking successful, and uses as_map to safely unpack the browser's nested response.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser runtime response for an uncaught JavaScript exception and turns it into a Python RuntimeError. It keeps failures in the web page from being silently ignored by the Python code.

**Data flow**: It receives the response dictionary from the browser. If the response has no exception details, it does nothing. If exception details are present, it looks for the best human-readable description available and raises a RuntimeError that includes that description.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser reply. It is the shared safety check that separates successful JavaScript results from browser-reported failures before either caller tries to unpack the returned value.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigation, while deciding when it is safe to continue`

When an automated browser clicks a button or opens a page, the next step should not run too early. The page may still be loading content, sending requests, or repainting the screen. But waiting for absolutely everything is also bad, because many modern pages keep sending background traffic forever. This file solves that timing problem.

It works like a careful stage manager. It watches for browser events: important network requests starting and finishing, page loading starting and ending, and paint events, which mean Chrome has drawn visible content. It tracks only requests that look relevant to the user action. It ignores passive things such as images, fonts, low-priority prefetches, and common analytics services.

The main class, `Settle`, keeps small sets of state: which requests are still pending, which browser sessions are loading, and which sessions have painted. Its `wait` method first gives the page one quick chance to run queued JavaScript tasks. Then it waits until the action’s visible consequences have quieted down, with time limits so it never waits forever. If the page paints, it gives a short extra grace period for foreground content to finish. If there is no paint, it falls back to waiting for network quiet. Without this file, automation would either race ahead before pages are ready or hang on pages that never become completely idle.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It filters out background or decorative traffic, such as images, fonts, low-priority requests, and common analytics beacons, so the system waits for meaningful page work instead of noise.

**Data flow**: It receives a dictionary of browser event details. It looks at the request type, priority, and URL host. If the request looks passive or analytics-related, it returns `false`; otherwise it returns `true`, including for unfamiliar shapes so important work is not accidentally ignored.

**Call relations**: `Settle.on_request_started` calls this when Chrome reports that a request began. The answer tells `Settle` whether to add that request to its pending-work list.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker with no remembered page work. This gives a browser session a clean place to record requests, loading state, and paint events.

**Data flow**: It takes no external data beyond the new object being created. It initializes an empty set of pending requests, a counter of tracked requests started, an empty set of loading sessions, and an empty set of painted sessions.

**Call relations**: Browser session setup creates a `Settle` object when a session is initialized, and session closing can create one again to reset the settling state. After construction, event callbacks and waiting logic use this stored state.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the action-specific settling state so the next browser action starts from a clean slate. This matters because the file is meant to wait only for work caused by the current action, not old work.

**Data flow**: It reads the object’s current pending requests, started-request count, and painted-session list. It empties pending requests, resets the started count to zero, and clears painted sessions; it leaves the loading set untouched.

**Call relations**: No direct caller is shown in the provided graph, but this method is the reset switch for the tracker. It is meant to be used before observing a new action so later calls to `wait` judge only new consequences.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records that a relevant network request has started. This lets the settling logic know that the page is still doing work caused by an action.

**Data flow**: It receives browser event details and an optional session id. It extracts the request id, asks `tracks_request` whether the request is worth waiting for, and, if so, stores the pair of session id and request id in `pending` and increments the started counter.

**Call relations**: This method is called when request-start events are fed into the `Settle` object. It relies on `tracks_request` to avoid tracking background noise; later, `Settle.wait` watches the `pending` set that this method fills.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records that a network request is no longer pending. This is how the tracker learns that one piece of page work has completed.

**Data flow**: It receives browser event details and an optional session id. If both the session id and request id are usable, it removes that request from the pending set. If the request was not being tracked, removing it has no effect.

**Call relations**: This method is used when request-finished or request-failed events reach the settling tracker. It balances the additions made by `Settle.on_request_started`, and `Settle.wait` uses the shrinking pending set to decide when the page has gone quiet.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as currently loading a document. This tells the waiting logic not to continue while a navigation is still underway.

**Data flow**: It receives a session id and adds it to the `loading` set. After that, calls to `wait` treat that session as not ready yet.

**Call relations**: No direct caller is shown in the graph, but this is the hook for page-loading lifecycle events. It feeds state into `Settle.wait`, which checks whether the session is still loading.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as no longer loading. This allows the settling logic to proceed once other tracked work, such as pending requests, is also finished.

**Data flow**: It receives a session id and removes it from the `loading` set. If that session was not marked as loading, nothing changes.

**Call relations**: No direct caller is shown in the graph, but this is the counterpart to `Settle.mark_loading`. `Settle.wait` later reads the loading set to decide whether it can return.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a session has produced a meaningful paint, meaning Chrome has drawn visible page content. Paint is used as a strong sign that the user-visible page has appeared.

**Data flow**: It receives a session id and adds it to the `painted` set. Future waiting checks can then switch from long navigation-style waiting to a short post-paint grace period.

**Call relations**: No direct caller is shown in the graph, but this method is intended to be called from paint lifecycle events. `Settle.wait` uses the flag it sets, and may hand off to `_drain_after_paint` once paint has happened.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears ready enough to continue, without waiting forever. It combines page task flushing, paint detection, loading state, pending requests, quiet gaps, and a caller-provided time cap.

**Data flow**: It receives a Chrome DevTools Protocol connection object, a session id, and a maximum number of seconds to wait. It first asks `_flush_page_tasks` to let immediate JavaScript consequences appear. Then it watches the stored state: whether the session painted, whether it is loading, and whether tracked requests are pending. It returns when the page is ready enough or when the time limit is reached; it changes no external data except through the waiting time it consumes.

**Call relations**: This is the central method other code uses after an action or navigation. It calls `_flush_page_tasks` at the start so request events caused by immediate JavaScript are counted, and it calls `_drain_after_paint` when a paint event means the page is visible but may need a short grace period for content.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After the page has painted, waits briefly for important remaining work to finish. This avoids declaring a page ready while its visible shell is still fetching content, but also avoids waiting the full cap on pages that keep background activity alive forever.

**Data flow**: It receives a session id and an absolute deadline time. It creates a shorter grace deadline, then repeatedly checks whether that session is still loading or has pending tracked requests. If things become quiet for a small gap, it returns early; otherwise it returns when the grace time runs out.

**Call relations**: `Settle.wait` calls this when it sees that the session has painted. This helper is the post-paint branch of the settling process, using the state filled by request and loading event methods.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the page one quick turn to run JavaScript tasks that were triggered by the action. This helps ensure that immediate follow-up requests are noticed before the system decides the page is quiet.

**Data flow**: It receives the DevTools connection and session id. It sends a small JavaScript expression that waits for a zero-delay timer, which lets the page process already-queued work. If the browser command fails or times out, it simply sleeps for a short beat instead, so settling can continue safely.

**Call relations**: `Settle.wait` calls this before making readiness decisions. It hands work to `Cdp.send`, which talks to the browser through the Chrome DevTools Protocol, and its result makes later request-tracking state more reliable.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Protocol boundary data
The wire layer defines and validates the JSON shapes received from Chrome DevTools Protocol before they spread through the browser engine.

### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `request handling`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often called CDP. In plain terms, CDP is a JSON-based remote control channel for the browser: the engine sends JSON commands and receives JSON replies. This file is the small checkpoint that says, “Before we trust this piece of JSON, make sure it is the kind of thing we expected.”

It first defines shared names for JSON values, such as strings, numbers, lists, and objects. Then it defines a custom ValidationError, which means “the browser sent data in a shape we cannot use.” Instead of causing a mysterious crash later, this error can be reported as a recoverable tool problem.

The four helper functions are narrowers. A narrower takes a loose JSON value and proves it is a more specific kind of value, such as a dictionary, string, integer, or list. If the value is missing in places where an empty object or list is acceptable, the helpers return an empty one. If the value has the wrong shape, they raise ValidationError with a path, which is a human-readable label saying where the bad value was found. Like a ticket inspector at a station gate, this file does not drive the train, but it stops the wrong passengers from getting on.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary with string keys. It is used when the browser engine expects a bundle of named fields from a CDP message.

**Data flow**: It receives a JSON value and a path describing where that value came from. If the value is missing, it turns it into an empty dictionary; if it is already a dictionary, it returns it unchanged. If it is anything else, it creates a ValidationError saying that this path must be an object.

**Call relations**: When other CDP-reading code needs a JSON object, it can call this function before using the value. If the check fails, this function hands the problem off by raising ValidationError, so the bad wire data is stopped at the protocol boundary.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It is useful for required text fields such as identifiers, names, or URLs where an empty value would not be meaningful.

**Data flow**: It receives a JSON value and a path naming the field being checked. If the value is a string and it is not empty, the same string comes out. For missing values, empty strings, or values of any other kind, it raises ValidationError with a message tied to that path.

**Call relations**: CDP-reading code calls this when it needs dependable text before continuing. On failure, it calls into ValidationError creation, which turns the mismatch into a clear recoverable error instead of allowing a bad value to travel deeper into the browser engine.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, meaning a whole number. It protects code that expects counts, indexes, or numeric IDs from accidentally receiving text, objects, or other wrong shapes.

**Data flow**: It takes a JSON value and a path label. If the value is an integer, it returns that integer. If not, it raises ValidationError saying that the named path must be an integer.

**Call relations**: Other parts of the browser engine can call this before doing number-based work with CDP data. If the browser response does not match the expected shape, this function raises ValidationError immediately, keeping the error close to the incoming wire data.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It is used when the browser engine expects a collection of items from a CDP response.

**Data flow**: It receives a JSON value and a path showing where the value was found. If the value is a list, it returns that list unchanged; if the value is missing, it returns an empty list. If the value is anything else, it raises ValidationError explaining that the path must be a list.

**Call relations**: Code that loops over CDP response items can call this first, so it knows it really has a list to work with. When the shape is wrong, this function creates a ValidationError, stopping the bad response before later code tries to iterate over something unsuitable.

*Call graph*: 1 external calls (__init__).

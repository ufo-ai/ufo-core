# Chrome DevTools session plumbing  `stage-12.3`

This stage is the low-level plumbing that lets the system drive a real Chrome browser. It sits behind the main browser tools: when a turn needs Chrome, backend.py opens or reuses a browser connection, recovers if Chrome crashes, moves files in or out, and cleans up afterward. session.py is the shared workspace for that live browser session. It holds the current tabs, downloads, and helper objects, then routes tasks to the right specialist.

cdp.py talks to Chrome through the DevTools Protocol, which is Chrome’s remote-control channel. It sends commands over a WebSocket connection, waits for replies, and passes browser events to listeners. tabs.py uses that channel to open, close, switch, and navigate tabs. runtime.py safely runs small pieces of JavaScript inside a page and returns the result. dialogs.py prevents pop-up alerts from blocking automation by recording and accepting or dismissing them. downloads.py detects and waits for downloads, including files Chrome might otherwise preview. settle.py decides when a page has reacted enough after an action, while ignoring harmless background activity.

## Files in this stage

### Session lifecycle
Per-turn orchestration opens, reuses, recovers, and tears down a live Chrome automation session.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per turn, from first browser tool use through turn cleanup`

The main idea in this file is lazy, safe browser access. A turn may never use the browser, so `BuaSurface` waits until the first browser action before asking a CDP provider for a lease. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome from code. Once it has a lease, it opens a `BrowserSession`, and all tool actions such as navigating, reading text, clicking, filling forms, managing tabs, uploading files, and waiting for downloads go through that session.

The lease is important because Chrome may be local inside the sandbox, or remote in hosted storage. `BuaSurface` does not assume where files live. For uploads, it resolves each requested workspace path, asks the lease to place the file somewhere Chrome can read it, and, if needed, reads the bytes from the sandbox. For downloads, it asks the lease to fetch the downloaded bytes and returns them as base64 text so they can travel safely in JSON.

It also handles a subtle recovery case. If the process crashes before cleanup, it stores a durable token for the leased browser session. On replay, it can reattach to the same living browser instead of opening a new page and losing state. On normal cleanup, it closes the session, releases the lease, and clears that token so old browser sessions are not accidentally reused.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens and returns the browser session for this turn, but only if it has not already been opened. This keeps turns that never browse from paying the cost of connecting to Chrome.

**Data flow**: It starts with the current `BuaSurface` state. If a session already exists, it returns that same session. If not, it gets or creates a CDP lease, asks the lease for the browser endpoint and download directory, builds a `BrowserSession`, opens it, stores it on the surface, and returns it.

**Call relations**: Almost every public browser action calls `_open` first. `_open` is the shared doorway: it calls `_acquire_lease` when a lease is missing, creates the `BrowserSession`, and then hands that ready session back to navigation, tab, page-reading, form, computer, upload, and download methods.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets access to a Chrome control session, reusing a surviving one after a crash when possible. This prevents a recovered browser action from losing the page it was already working with.

**Data flow**: It first reads any saved token for this conversation. If a token exists, it asks the CDP provider to reattach to that old session. If that session is gone, it clears the token. Then it creates a fresh lease, saves the new lease token if storage is available, and returns the lease.

**Call relations**: `_open` calls this when it needs a lease. This function uses `_stored_token` and `_store_token` to coordinate crash recovery with the scoped store, and it hands the resulting lease back to `_open` so the browser session can be created.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser-session token for this conversation. The token is the small piece of information that lets a later recovered turn reconnect to the same hosted browser session.

**Data flow**: It reads the surface's optional store and conversation id. If either is missing, it returns nothing. Otherwise it fetches the stored value and returns it only if it is actually a string.

**Call relations**: `_acquire_lease` calls this before creating a new lease. Its answer decides whether the code tries to reattach to an existing session or starts a new one.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser-session token for this conversation. Saving supports crash recovery; clearing prevents later turns from reconnecting to a session that was already released.

**Data flow**: It receives either a token string or `None`. If the surface has both a store and a conversation id, it writes that value under a conversation-specific key. If storage information is missing, it quietly does nothing.

**Call relations**: `_acquire_lease` calls this after making a fresh lease or after discovering an old token is no longer valid. `aclose` calls it with `None` during normal cleanup so the recovery token does not outlive the browser lease.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates the browser to a requested URL, optionally in a specific tab. It is the tool-facing wrapper around browser navigation.

**Data flow**: It receives a JSON-like argument dictionary, opens the browser session, checks that `url` is a string, converts the optional `tab_id` into an integer when possible, and returns the session's navigation result.

**Call relations**: A browser tool call for navigation comes here. It uses `_open` to get the session and `_tab_id` to interpret the tab choice, then delegates the actual browser work to the session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs. A caller uses this to understand what pages are open and which tab is active.

**Data flow**: It receives the tool arguments, opens the browser session, asks the session for tab context, and returns that tab information unchanged.

**Call relations**: This is one of the public tool methods. Like the other tab methods, it first goes through `_open` so it does not care whether the browser connection already exists.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab, using a provided URL or a blank page if no usable URL is supplied. This gives the tool layer a simple way to open another page.

**Data flow**: It reads the optional `url` value from the argument dictionary. After opening the session, it passes a non-empty string URL to the session, or `about:blank` when the input is missing or invalid, and returns the session's reply.

**Call relations**: A tab-create tool call comes here. It uses `_open` for session setup, then hands the tab creation request to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes one or more browser tabs according to the given arguments. It lets the tool layer tidy up or switch away from pages it no longer needs.

**Data flow**: It receives the argument dictionary, opens the browser session, passes the arguments through to the session's close-tab operation, and returns the result.

**Call relations**: This method is a thin public wrapper. Its main job is to ensure there is an open session before delegating the actual tab closing to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a page's file input. It protects the workspace boundary and works whether Chrome is local to the sandbox or remote somewhere else.

**Data flow**: It receives arguments containing a `files` list. It checks that each item is a non-empty string, turns each into a safe workspace path, and asks the current lease to place the file where Chrome can open it. For remote browsers, the lease may call back into `_read` to get the file bytes. It then sends the placed file paths to the session, waits for the page to actually receive the shipped bytes, and returns the upload result.

**Call relations**: A file-upload tool call comes here after the browser has been requested. It uses `_open` for the session, `_lease` for the active transport lease, `workspace_path` to keep paths inside the workspace, and `_settle_upload` to make sure remote upload timing did not leave the page with an empty file.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until uploaded files have really arrived in the browser page. This avoids a hidden failure where the page sees a filename before the remote file bytes have finished landing.

**Data flow**: It looks at the sizes of files this surface actually shipped. If none were shipped, it returns immediately. Otherwise it repeatedly asks the session what file sizes are attached in the page. If they match the expected sizes, it finishes. If not, it sleeps briefly, reattaches the files, and tries again. If the sizes never match, it raises an error explaining what was expected and what the page reported.

**Call relations**: `upload_file` calls this after the first attach. It cooperates with `BrowserSession.attached_sizes` to check the page's view of the files and with `BrowserSession.upload_file` to retry the attach while the remote transport catches up.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active CDP lease, or raises a clear error if no lease exists. It is a guardrail for operations that cannot make sense without an already-open browser lease.

**Data flow**: It reads the `lease` field on the surface. If the lease is present, it returns it. If not, it raises a runtime error saying the browser has no CDP lease.

**Call relations**: `upload_file` and `wait_for_download` call this after `_open` has established the browser session. It keeps those methods from silently continuing without the transport object they need for file movement.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file from the sandbox so it can be sent to a remote browser. It checks size first so a too-large upload fails early instead of filling memory.

**Data flow**: It receives a resolved path. It requires a sandbox, quotes the path safely for shell commands, asks the sandbox for the file size, rejects unreadable or too-large files, then runs `base64` inside the sandbox to read the bytes. Back in Python, it decodes the base64 text in a worker thread, records the byte count in `_shipped`, and returns the raw bytes.

**Call relations**: This function is not called directly by the public tool method. `upload_file` gives it to the lease as a callback, so the lease can ask for bytes only when the browser transport needs to ship a local workspace file to a remote location.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Asks the browser session for a structured reading of the current page. This is used when the tool needs page content in a form meant for interaction or understanding.

**Data flow**: It receives the tool arguments, opens the session, passes the arguments to the session's page-reading operation, and returns the result.

**Call relations**: This public method is part of the browser surface used by higher-level tools. It relies on `_open` for connection setup and lets `BrowserSession` do the actual page inspection.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets text from the current page. It is the simple text-extraction route for browser tools that need readable page content.

**Data flow**: It receives the argument dictionary, opens the browser session, forwards the request to the session, and returns the text result.

**Call relations**: A page-text tool call comes here. The method uses `_open` in the same shared pattern as the other browser actions, then hands the real browser query to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, optionally using a host-side find completer to improve or complete the search. This supports browser tools that need to locate visible targets before acting.

**Data flow**: It receives search arguments, opens the browser session, and passes both the arguments and the optional `find_completer` into the session. It returns whatever match information the session provides.

**Call relations**: The find tool reaches this method. It connects the per-turn browser session from `_open` with the optional completion helper stored on the surface, then delegates page-specific searching to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on the page. It is the tool-facing route for entering text or values into web forms.

**Data flow**: It receives form-input instructions, opens the browser session, forwards the instructions to the session, and returns the session's result.

**Call relations**: This public browser action follows the common pattern: `_open` supplies a ready session, and `BrowserSession` performs the actual interaction with the page.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as direct interaction commands, through the browser session. It gives the tool layer a general control path beyond the specialized page methods.

**Data flow**: It receives an argument dictionary, opens the browser session, passes those arguments to the session's computer operation, and returns the result.

**Call relations**: Computer-control tool calls use this method. It does not implement the browser action itself; it ensures the session exists through `_open` and then delegates to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns the downloaded file's name, size, and base64-encoded contents. It works even when the browser stored the file in a remote provider's private location.

**Data flow**: It opens the browser session and waits for the session to report a completed download. It then asks the active lease to fetch the download bytes by download id. It base64-encodes those bytes in a worker thread, trims the filename down to a safe single path segment, and returns a dictionary with filename, encoded content, and byte size.

**Call relations**: A download-waiting tool call comes here. It uses `_open` to talk to the browser, `_lease` to retrieve the bytes from wherever the transport stored them, and `contained_leaf` to prevent a page-chosen filename like `../x` from escaping the caller's intended folder.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved recovery token. This is the normal end-of-turn cleanup that prevents browser sessions and hosted resources from being orphaned.

**Data flow**: It checks whether a session exists and closes it, then always proceeds to release the lease if one exists. After that, it stores `None` as the token, which clears any saved reattach information. It also resets the in-memory session and lease fields.

**Call relations**: Turn cleanup calls this after browser tools have had their chance to run. It calls `_store_token` at the end so normal shutdown removes the crash-recovery marker, while a hard crash that skips `aclose` intentionally leaves the token behind for replay.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose JSON tab id value into an integer tab id when possible. It makes navigation tolerant of tab ids arriving as numbers or strings.

**Data flow**: It receives one JSON-like value. Booleans and missing or unrecognized values become `None`. Integers pass through, floats are converted to integers, and non-empty strings are parsed as integers. The result is either an integer tab id or `None`.

**Call relations**: `BuaSurface.navigate` uses this helper before calling the browser session. That keeps tab-id cleanup separate from the main navigation flow.

*Call graph*: called by 1 (navigate).


### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `active while a browser turn/session is open, from connection setup through tool calls to cleanup`

A BrowserSession is like a temporary control desk for Chrome. It opens one Chrome DevTools Protocol connection, which is a WebSocket link that lets code inspect and control Chrome. Once connected, it installs listeners for important browser events: tabs opening or closing, pages loading, dialogs appearing, network activity starting and ending, and downloads beginning or finishing. Without this file, the rest of the browser tools would not have a single place to connect to Chrome or remember what is happening during a turn.

The session does not do every browser task itself. Instead, it creates small helper objects when needed: BrowserTabs for tabs and navigation, BrowserContent for reading page content, BrowserForms for form input and uploads, BrowserDownloads for download tracking, BrowserRuntime for JavaScript execution, and BrowserComputer for mouse or keyboard style actions. These helpers all receive the same session, so they can share the same connection and state.

The file also handles cleanup. Closing a session tries to close browser tabs, shuts down the CDP connection, cancels background tasks, and resets stored state. This matters because the browser may persist across turns, but each session should leave no stale listeners, downloads, dialogs, or tab records behind.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the basic settings it will need later, such as the Chrome endpoint, model screen size, and download folder. It prepares storage for tabs, downloads, dialogs, background tasks, and loading-status tracking, but it does not connect to Chrome yet.

**Data flow**: The caller gives an optional CDP endpoint, optional model name, and download directory. The constructor chooses a coordinate size for screenshots, initializes empty lists and dictionaries for browser state, creates a Settle tracker for page/network quietness, and creates a BrowserTabEvents tracker. The result is a ready-but-not-open BrowserSession.

**Call relations**: BuaSurface._open creates this object before browser work begins. The constructor calls small setup helpers such as model_coordinate_space, Settle.__init__, and BrowserTabEvents.__init__ so later methods have the state holders they need.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the browser session if it is not already open. It is the safe public entry for starting the live Chrome connection.

**Data flow**: It checks whether a connection already exists. If not, it runs the bootstrap sequence; if anything fails during setup, it closes and resets the partially opened session before re-raising the error. The output is no direct value, but after success the session has a live connection and an initial tab.

**Call relations**: The async context manager method __aenter__ calls this when entering a session block. It hands the real setup work to _bootstrap and uses close as the cleanup path if setup breaks halfway.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Builds the live connection to Chrome and wires up all browser event listeners. This is the session's startup checklist.

**Data flow**: It reads the configured CDP endpoint, resolves it into a WebSocket URL, opens the CdpConnection, asks Chrome for version information, sets download behavior, discovers targets, subscribes event callbacks, and creates a blank starting tab. Afterward, the session knows whether Chrome is on macOS, has event readers attached, and has one active tab stored in self.tabs.

**Call relations**: open calls this during startup. It creates temporary reader helpers through tab_reader, download_reader, and dialog_reader, then registers their callbacks with the CDP connection so later browser events flow to the right helper.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the live browser session and clears all per-session memory. It is designed to leave the object clean even if some browser close commands fail.

**Data flow**: It looks at the current connection and tab list, tries to close each browser target, then closes the CDP connection. In all cases it resets the connection, tabs, frame sessions, downloads, dialog list, settle tracker, tab event tracker, and background task set; any background tasks are cancelled.

**Call relations**: __aexit__ calls this when a session block ends, and open calls it if startup fails. It recreates Settle and BrowserTabEvents so a later reopen starts from a fresh state.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python's async context manager pattern, meaning setup happens automatically at the start of a block. This helps callers avoid forgetting to open the browser connection.

**Data flow**: It receives the session object, calls open, waits for the connection setup to finish, and then returns the same session object for use inside the block.

**Call relations**: It is the context-manager front door. Its only handoff is to open, which then decides whether bootstrapping is needed.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Lets BrowserSession clean itself up automatically when an async context manager block ends. This helps prevent dangling Chrome connections and background tasks.

**Data flow**: It receives any exception information from the exiting block, ignores those details, and calls close. It returns no value; the visible effect is that browser session state is torn down.

**Call relations**: It is the matching exit path for __aenter__. It delegates all cleanup work to close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live Chrome connection, or gives a clear error if the session has not been opened. Helper objects use this when they need to send commands to Chrome.

**Data flow**: It reads self.conn. If the connection is missing, it raises BrowserUnavailable; otherwise it returns the CdpConnection object.

**Call relations**: This method is a guardrail used by code that depends on an open browser. It constructs BrowserUnavailable when a caller asks for Chrome too early or after cleanup.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so it can be cancelled during session cleanup. This is useful for browser work that must continue while other actions proceed.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session's background-task set, and attaches a callback that removes the task from the set when it finishes.

**Call relations**: Other browser helpers can use this session method when they need a side job. close later uses the stored task set to cancel anything still running.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame is the main frame of a tab rather than an embedded frame. This matters because page events from embedded frames should not always be treated like whole-page events.

**Data flow**: It receives a CDP session id and frame id, creates a BrowserTabs helper, and asks it to decide whether the frame is top-level. It returns true or false.

**Call relations**: This is a convenience wrapper around tab_reader. Browser tab logic owns the actual frame knowledge, while BrowserSession provides the shared state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready for browser automation. This is part of preparing tabs or frame sessions after Chrome reports them.

**Data flow**: It receives a session id, builds a BrowserTabs helper, and asks that helper to initialize the session. The result is no direct return value, but Chrome-side setup is performed for that session.

**Call relations**: It delegates to tab_reader because tab setup belongs to BrowserTabs. It is used when a CDP session must be prepared before page and tab tools can rely on it.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab object for a requested tab, or the current/default tab if none is specified. Tool code uses it when it needs a concrete tab to act on.

**Data flow**: It receives an optional tab id, creates a BrowserTabs helper, and asks it to locate the page. It returns a Tab object.

**Call relations**: This is a session-level shortcut to BrowserTabs.page. It keeps callers from needing to create the tab helper themselves.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new web address. It is the public session method behind browser navigation.

**Data flow**: It receives a URL and optional tab id, creates a BrowserTabs helper, and passes both values to its navigate method. It returns a JSON-style dictionary describing the result.

**Call relations**: This method routes navigation requests to BrowserTabs, which knows how to talk to Chrome about tab loading and page state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a plain data summary for a specific tab. Callers use it when they need details such as the tab's identity or current page state.

**Data flow**: It receives a Tab object, creates a BrowserTabs helper, and asks for that tab's information. It returns a JSON-style dictionary.

**Call relations**: It is a thin bridge from session-level code to BrowserTabs.tab_info, keeping all tab-formatting rules in the tab helper.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns a summary of the current tab situation, such as what tabs exist and which one is active. This gives higher-level tools context before they choose where to act.

**Data flow**: It creates a BrowserTabs helper, which reads the session's tab list and related tab event state. It returns a JSON-style dictionary with the tab context.

**Call relations**: It delegates to BrowserTabs through tab_reader. The session supplies shared state; BrowserTabs turns it into caller-friendly information.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of open tabs. This is a lightweight way to show what pages are available without returning full tab details.

**Data flow**: It creates a BrowserTabs helper, which inspects current tabs and reads their titles. It returns a list of strings.

**Call relations**: It is another session shortcut to BrowserTabs. It exists so callers can ask the session directly for tab titles.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper tied to this session and the standard viewport size. BrowserTabs is the specialist for tab discovery, creation, closing, navigation, and page-load events.

**Data flow**: It takes no outside input beyond the session itself. It passes the session and viewport size into BrowserTabs and returns the new helper object.

**Call relations**: Many session methods call this before doing tab-related work, including startup, navigation, tab lookup, tab closing, and tab-title requests. _bootstrap also uses it to register browser event callbacks.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page-structure tasks, especially resolving page references to real frames and coordinates. It limits how deeply frame trees are explored.

**Data flow**: It uses the current session, the standard viewport, and the configured maximum frame depth to create a BrowserPage object. It returns that helper.

**Call relations**: resolve_ref and ref_point call this when they need page-reference logic. The helper gets access to session state and the live connection through the session.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper, the specialist for reading page content and finding text or elements. This keeps content extraction separate from connection setup.

**Data flow**: It passes the session into BrowserContent and returns the helper. The helper can then use the session's connection, tabs, and page state.

**Call relations**: tree, read_page, get_page_text, and find all call this before doing content-related work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for watching download events and waiting for a download to finish. It uses the session's download list and a maximum wait time.

**Data flow**: It passes the session and timeout value into BrowserDownloads and returns the helper. The helper can read and update the session's download records.

**Call relations**: _bootstrap uses this helper's callbacks for Chrome download events, and wait_for_download uses it when a caller wants to wait for a completed file.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for browser JavaScript dialogs, such as alerts or prompts. Dialogs need special tracking because they can block page actions.

**Data flow**: It passes the session into BrowserDialogs and returns the helper. The helper can append dialog information to the session's dialog list when Chrome reports one.

**Call relations**: _bootstrap calls this so it can register the helper's dialog callback with the CDP connection.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form input, file uploads, and checking attached file sizes. This keeps form-specific browser commands in one place.

**Data flow**: It passes the current session into BrowserForms and returns the helper. The helper uses the session to reach Chrome and page references.

**Call relations**: upload_file, attached_sizes, and form_input call this whenever a form-related tool request arrives.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript in the page and calling functions on JavaScript objects. Runtime here means the page's live JavaScript environment.

**Data flow**: It passes the session into BrowserRuntime and returns the helper. The helper can then send runtime commands through the session's CDP connection.

**Call relations**: eval_js and call_on call this when they need JavaScript execution. The session only routes the request; BrowserRuntime knows the CDP details.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page if no URL is given. This is the public session method for tab creation.

**Data flow**: It receives an optional URL, creates a BrowserTabs helper, and asks it to create the tab. It returns a JSON-style dictionary describing the created tab or operation result.

**Call relations**: It delegates directly to BrowserTabs through tab_reader, keeping tab creation behavior in the tab subsystem.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab according to the caller's arguments. This gives tool callers a session-level way to remove tabs.

**Data flow**: It receives a JSON-style argument dictionary, creates a BrowserTabs helper, and passes the arguments to the helper's close method. It returns a JSON-style result.

**Call relations**: It is part of the tab tool surface and hands the actual close decision and Chrome command to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads or attaches a file to a file input on the page. It is used when a browser action needs to place a local file into a web form.

**Data flow**: It receives a JSON-style argument dictionary describing the upload target and file information. It creates a BrowserForms helper, passes the arguments to upload_file, and returns a JSON-style result.

**Call relations**: BuaSurface._settle_upload calls this during upload handling. The session routes the request to BrowserForms, which knows how to interact with file inputs.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Checks the sizes of files currently attached through a form upload flow. This can confirm what the page accepted without reading the uploaded file contents.

**Data flow**: It receives a JSON-style argument dictionary, creates a BrowserForms helper, and asks it for attached file sizes. It returns a list of integer sizes.

**Call relations**: BuaSurface._settle_upload calls this as part of upload settling. BrowserForms performs the actual page/form inspection.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree of page content, optionally filtered by type. A tree is a simplified outline of the page that helps tools understand structure.

**Data flow**: It receives JSON-style arguments and a filter type, creates a BrowserContent helper, and asks it to build the tree. It returns a string.

**Call relations**: This is a content-reading route from the session to BrowserContent. BrowserContent owns the page inspection logic.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result useful to higher-level tools. It is one of the main ways the system sees what is on the webpage.

**Data flow**: It receives JSON-style arguments, creates a BrowserContent helper, and asks it to read the page. It returns a JSON-style dictionary with the page information.

**Call relations**: It delegates to BrowserContent through content_reader, keeping page extraction separate from session connection state.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts page text in a caller-friendly form. This is useful when the caller needs the words on the page rather than full structure.

**Data flow**: It receives JSON-style arguments, creates a BrowserContent helper, and asks for page text. It returns a JSON-style dictionary with the extracted text result.

**Call relations**: It routes text-reading requests to BrowserContent. The session supplies shared browser access; the content helper does the reading.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within page content and may use a completer to finish or refine the find operation. This helps tools locate text or page items.

**Data flow**: It receives JSON-style search arguments and an optional FindCompleter, creates a BrowserContent helper, and passes both onward. It returns a JSON-style search result.

**Call relations**: It is the session-level entry for find requests. BrowserContent performs the actual search work and uses the optional completer if provided.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or sets input into a form field on the page. This supports browser automation that fills text boxes, selects values, or otherwise interacts with forms.

**Data flow**: It receives JSON-style arguments describing the target field and input value, creates a BrowserForms helper, and asks it to perform the input. It returns a JSON-style result.

**Call relations**: It routes form-entry tool calls to BrowserForms. The session keeps the shared connection and page state, while BrowserForms knows how to operate on form controls.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs lower-level computer-style browser actions, such as mouse or keyboard interactions, within the browser viewport. This is for actions that resemble using the browser visually rather than reading structured page data.

**Data flow**: It receives a JSON-style action dictionary, creates a BrowserComputer with the session, viewport, and wait limit, and asks it to run the action. It returns a JSON-style result.

**Call relations**: Unlike the reader factory methods, this constructs BrowserComputer directly for the one action. BrowserComputer then performs the detailed interaction using the session's live browser connection.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to complete and returns the download record. The file contents are not read here; the result tells the owner of the download folder what completed.

**Data flow**: It receives JSON-style wait arguments, creates a BrowserDownloads helper, and asks it to wait. It returns a BrowserDownload object when the requested download is available or complete.

**Call relations**: It is the session-level download waiting tool. _bootstrap already registered download event callbacks from BrowserDownloads, so this method can rely on the session's download records being updated.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Runs a JavaScript expression inside a specific browser session or frame. This is used when a tool needs information or behavior only available through page JavaScript.

**Data flow**: It receives a CDP session id and JavaScript expression, creates a BrowserRuntime helper, and asks it to evaluate the expression. It returns the JSON-like result from the runtime.

**Call relations**: It delegates JavaScript execution to BrowserRuntime through runtime_reader. The session provides the live connection and target session id.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on a specific JavaScript object in the page. This is more targeted than evaluating a raw expression because it operates on an existing object id.

**Data flow**: It receives a session id, object id, function source text, and optional argument list. It creates a BrowserRuntime helper, forwards those details, and returns a JSON-style result dictionary.

**Call relations**: It is the session route to BrowserRuntime.call_on. BrowserRuntime understands the Chrome runtime command needed to call the function.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the actual frame node and index it points to. A reference is a compact identifier used by tools to name something on the page.

**Data flow**: It receives a Tab and reference string, creates a BrowserPage helper, and asks it to resolve the reference. It returns a pair: the FrameNode and an integer index.

**Call relations**: It delegates page-reference interpretation to BrowserPage through page_reader. Other tools can use this when they need to convert a human/tool-facing reference into internal page structure.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen coordinate for a referenced page item. This is useful when a later action needs to click or point at that item.

**Data flow**: It receives a Tab and reference string, creates a BrowserPage helper, and asks for the reference point. It returns an x, y coordinate pair.

**Call relations**: It relies on BrowserPage through page_reader because BrowserPage knows how references map to frames and geometry. The returned point can feed visual or computer-style actions.

*Call graph*: calls 1 internal fn (page_reader).


### DevTools connection
The Chrome DevTools Protocol transport provides the command and event channel used by the browser session.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser session startup through active browser automation, until connection teardown`

Chrome’s DevTools Protocol, or CDP, is like a remote control for the browser. This file provides the wiring for that remote control. Without it, the rest of the browser automation code could not reliably ask Chrome to do things, hear back whether they worked, or react when Chrome reports that something happened.

The main class, CdpConnection, wraps a WebSocket, which is a two-way network pipe that stays open. When code sends a command, CdpConnection gives it a unique number, remembers who is waiting for the answer, and sends the command as JSON text. A background reader task constantly listens for messages coming back from Chrome. If a message is a reply to a numbered command, it completes the matching waiting task. If the reply is an error, it turns it into a CdpError with useful context.

Chrome also sends events that are not direct replies, such as page or target updates. This file supports two ways to receive them: one-shot waits through expect and wait, and longer-lived listeners registered with on. If the connection closes, it deliberately fails all pending waits instead of letting the program hang forever. That makes failures loud and easier to diagnose.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: This creates a clear error object for a failed Chrome DevTools Protocol command. It records which command failed, Chrome’s numeric error code, and the message Chrome sent back.

**Data flow**: It receives the command name, an error code, and an error message from Chrome. It stores the command name and code on the error object, then builds a human-readable exception message. The result is an exception that can be attached to the waiting command so the caller sees what went wrong.

**Call relations**: CdpConnection._dispatch uses this when Chrome replies to a command with an error field. Instead of returning a normal result, the dispatch step gives the waiting caller this CdpError so the failure travels back through the same path as the command response.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: This finds the actual DevTools WebSocket address to connect to. Callers may already have a WebSocket URL, or they may only have Chrome’s HTTP debugging address; this function supports both.

**Data flow**: It receives a URL and HTTP headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise, it asks the browser’s /json/version endpoint for connection information, checks that the HTTP request succeeded, extracts the webSocketDebuggerUrl field, and returns that string.

**Call relations**: This is a setup helper used before opening the CDP connection. It talks over HTTP using httpx when it needs to discover the WebSocket address, and it uses as_str to make sure the discovered value is really a string before handing it to connection-opening code.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: This prepares a new connection object around an already-open WebSocket. It sets up the bookkeeping needed to match outgoing commands with incoming replies and to route events to interested code.

**Data flow**: It receives a WebSocket connection. It stores it, starts the command ID counter at zero, and creates empty containers for pending command replies, event listeners, one-time event waiters, and the background reader task. It does not start reading by itself; it only prepares the object’s internal state.

**Call relations**: CdpConnection.open calls this after it has successfully connected to Chrome. The state created here is later used by send, expect, wait, _read_loop, and _dispatch as messages flow in both directions.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: This opens the live WebSocket connection to Chrome and starts the background reader. It is the main entry point for creating a usable CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to Chrome with a large allowed message size, creates a CdpConnection around that socket, starts _read_loop as an asynchronous background task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this while starting a browser session. After open returns, other parts of the session can send commands immediately, while the reader task quietly receives replies and events in the background.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: This shuts down the CDP connection cleanly. It stops the background reader task and closes the WebSocket so the browser control channel is no longer active.

**Data flow**: It checks whether a reader task exists. If so, it cancels that task, waits for the cancellation to finish while ignoring the expected cancellation error, clears the stored task, and then closes the WebSocket. The outside effect is that no more messages should be sent or received through this connection.

**Call relations**: This is used during teardown after browser automation is finished or when a session must be stopped. It works with _read_loop’s cleanup behavior: when the reader ends, pending command and event futures are failed instead of left waiting forever.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This sends one command to Chrome and waits for its matching reply. It is the normal way higher-level browser code asks Chrome to perform an action or return information.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session ID for commands aimed at a specific browser target. It assigns a new numeric ID, builds a JSON message, creates a future representing the eventual reply, remembers that future under the ID, sends the JSON over the WebSocket, and waits up to the command timeout. If a reply arrives, it returns the result data. If time runs out, it removes the pending entry and raises a timeout error.

**Call relations**: The reply path depends on _read_loop and _dispatch. send places a future in the pending table, _read_loop receives Chrome’s response, and _dispatch looks up the matching ID and completes that future with either a result or a CdpError.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: This registers a listener for repeated browser events. It is used when some part of the program wants to be notified every time a certain CDP event happens.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for the event, creating the list if needed. It returns nothing, but it changes the connection’s listener registry so future matching events call that listener.

**Call relations**: CdpConnection._dispatch consults the listener registry whenever it receives an event message from Chrome. If the event name matches, _dispatch calls each registered listener with the event parameters and optional session ID.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This creates a one-time wait for one or more browser events. It is useful when code knows that a command or action should soon cause a specific event and wants to pause until that event arrives.

**Data flow**: It receives one or more event names and optionally a session ID to narrow the wait to a specific browser target. It creates a future, stores it together with the event names and session filter, and returns the future to the caller. Nothing is awaited here yet; the returned future is the ticket that will be completed later.

**Call relations**: Callers usually pass the future returned by expect into CdpConnection.wait. Meanwhile, _dispatch checks every incoming event against the stored waiters, and when one matches, it completes the corresponding future with the event parameters.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: This waits for an event future created by expect, with a time limit. It keeps event waits from lingering forever if Chrome never sends the expected event.

**Data flow**: It receives a future and an optional timeout. It waits until the future completes or the timeout expires. Whether the wait succeeds, fails, or times out, it removes that future from the internal waiter list afterward. On success it returns the event parameters; on timeout it raises a timeout error.

**Call relations**: This completes the one-shot event waiting pattern started by expect. _dispatch is responsible for resolving the future when a matching event arrives, while wait is responsible for applying the deadline and cleaning up the stored waiter.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: This is the background receiver for the WebSocket. It continuously reads messages from Chrome and hands each one to the dispatcher.

**Data flow**: It reads raw JSON text messages from the WebSocket. For each message, it parses the JSON into data and passes it to _dispatch. If the WebSocket closes, it exits the loop. During cleanup, it fails every still-pending command and event wait so callers are told the connection closed instead of waiting forever.

**Call relations**: CdpConnection.open starts this as a background task. It feeds every incoming Chrome message into CdpConnection._dispatch, and its final cleanup protects callers of send and wait from silent hangs when the connection disappears.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: This sorts each incoming CDP message into the right destination. Replies go back to the command that sent them; events go to one-time waiters and registered listeners.

**Data flow**: It receives one parsed message from Chrome. If the message has a numeric ID, it treats it as a command reply, finds the matching pending future, and completes it with either a result object or a CdpError. If the message has an event method name instead, it extracts the event parameters and optional session ID, completes any matching one-shot waiters, removes completed waiters, and then calls all listeners registered for that event.

**Call relations**: _read_loop calls this for every message received from Chrome. It is the central traffic director for the file: it completes futures created by send and expect, constructs CdpError for failed command replies, and invokes listeners registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### Interruptions and downloads
Browser pop-ups and downloads are handled so automation can keep moving and retrieve generated files.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `request handling`

Web pages can show JavaScript dialogs, such as alerts, confirmation boxes, prompts, or “are you sure you want to leave?” messages. In a normal browser, these wait for a person to click a button. In an automated browser, that waiting can block later browser events, like a locked door in a hallway. This file is the small safety mechanism that opens that door immediately.

The main class, BrowserDialogs, is given a browser session object. When a dialog appears, on_dialog reads its type and message. It decides what to do using a simple rule: alerts and beforeunload dialogs are accepted, because they mostly just acknowledge information or allow navigation to continue. Confirmation and prompt dialogs are dismissed, so the automation does not accidentally agree to a website’s “are you sure?” question or submit hidden intent on behalf of the user.

The file also records a plain text note, such as “confirm dismissed: Delete item?”, in the session’s dialogs list. That means the higher-level agent can later know the interruption happened. The actual click is sent in the background through the Chrome DevTools Protocol, a browser control channel. If that reply fails because the browser session is gone or unresponsive, the code logs a warning instead of crashing the run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser control connection used by this file. It represents a way to send a command to the browser, such as telling it to accept or dismiss a dialog.

**Data flow**: It receives the name of a browser command, optional command details, and an optional session identifier for a specific browser target. It sends that information through the browser control channel and returns the browser’s JSON-style reply.

**Call relations**: BrowserDialogs._answer_dialog relies on this method through the session’s connection. The protocol keeps this file independent from the exact connection class, as long as that class can send browser commands in this form.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way for a dialog handler to get the browser control connection from its session. It lets BrowserDialogs talk to the browser without knowing the full session implementation.

**Data flow**: It reads the current session object and returns an object that can send browser commands. Nothing is changed directly by this protocol method itself.

**Call relations**: BrowserDialogs._answer_dialog calls this when it is time to reply to a pop-up. The returned connection is then used to send the Page.handleJavaScriptDialog command.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for the session to start a small asynchronous job without making the caller wait. Here, it is used so answering a dialog can happen immediately in the background.

**Data flow**: It receives a coroutine, which is a paused asynchronous task. The session schedules that task to run, and the original caller can continue without waiting for the task to finish.

**Call relations**: BrowserDialogs.on_dialog uses this to launch BrowserDialogs._answer_dialog. This matters because dialogs can block browser events, so the response should be started right away without tying up the event-handling path.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog appeared. It records the event in human-readable form and starts the process of accepting or dismissing the dialog.

**Data flow**: It receives the dialog details from the browser and an optional session identifier. It reads the dialog type and message, decides whether to accept it, appends a note to the session’s dialog history, and schedules a background reply to the browser. It does not return a value; its visible effects are the recorded note and the scheduled dialog response.

**Call relations**: This is the entry point for dialog events in this file. When called by the browser event flow, it prepares the decision and hands the actual browser command to BrowserDialogs._answer_dialog, which is run through the session’s background-task mechanism.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This function sends the actual instruction to the browser to accept or dismiss the currently open JavaScript dialog. It is separated from on_dialog because the send operation is asynchronous and may fail.

**Data flow**: It receives the browser session identifier and a true-or-false accept decision. It gets the browser connection, sends the Page.handleJavaScriptDialog command with that decision, and produces no normal return value. If the command fails because of a browser control error, timeout, or runtime problem, it writes a warning to the log instead of raising the failure further.

**Call relations**: BrowserDialogs.on_dialog creates this task whenever a dialog is reported. This function then talks to the lower-level browser connection, shielding the rest of the dialog event flow from temporary browser-control failures.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `browser event handling and download waiting`

This file solves a practical browser problem: not every file-like web response becomes an actual download. A PDF, for example, may open inside Chrome’s built-in viewer instead of being saved. That is bad for an automation agent that needs the downloaded bytes later. This file watches Chrome’s download and network events, then nudges certain top-level PDF navigations into real downloads by changing the response header to say “attachment.” In everyday terms, it changes “show this document in the browser window” into “put this document in the downloads tray.”

The main class, BrowserDownloads, is the coordinator. When Chrome pauses a web request, on_fetch_paused decides whether the request should simply continue or whether the response should be rewritten to force a download. It is careful to only do this for top-level documents, so embedded PDFs inside a page are not accidentally broken.

The file also records download lifecycle events. When a download starts, it stores a small Download record with a browser-generated id, a suggested filename, and a state. When Chrome reports progress, the state is updated. Code that needs a finished file can call wait, which polls until a completed download appears or a timeout is reached. This file does not read the downloaded file from disk; it only identifies that the browser finished one.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the object that sends commands to Chrome through the Chrome DevTools Protocol, often shortened to CDP. CDP is Chrome’s control channel for automation, like a remote control for browser internals.

**Data flow**: It receives a command name, optional command details, and optionally a browser session id. The real implementation sends that command to Chrome and returns Chrome’s JSON-like reply as a dictionary.

**Call relations**: BrowserDownloads uses this through browser.connection().send when it needs to release a paused request or response. This file only states the promise; another object supplies the actual connection.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This is the expected way to get the browser’s CDP connection from the surrounding browser session object. BrowserDownloads needs that connection to tell Chrome to continue paused network traffic.

**Data flow**: It takes no extra input besides the session object. It returns an object that can send CDP commands to Chrome.

**Call relations**: The private continuation helpers call this before sending Fetch.continueRequest or Fetch.continueResponse. This keeps BrowserDownloads independent from the concrete browser session implementation.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way to start an asynchronous task without blocking the current browser event handler. It matters because paused browser requests must be released, but the event callback itself should stay quick.

**Data flow**: It receives a coroutine, which is an async job that has not finished yet. The session implementation schedules it to run in the background and does not return a useful value here.

**Call relations**: on_fetch_paused uses this to run _continue_request or _continue_response. That lets the browser event flow keep moving while the release command is sent separately.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This is the expected check for whether a network event belongs to the main page rather than an embedded frame. The distinction matters because forcing a download for an embedded document could break a page that merely displays a PDF inside itself.

**Data flow**: It receives a browser session id and a frame id from Chrome’s event data. It returns true if that frame is the main page frame and false otherwise.

**Call relations**: on_fetch_paused calls this before deciding to force a PDF response into a download. It acts like a safety gate so only main-page navigations are rewritten.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a web request and decides how to let it continue. Its main job is to force top-level PDFs to download while allowing all other paused traffic to proceed normally.

**Data flow**: It reads Chrome’s event details, including the request id, response status, headers, and frame id. If the event is only at the request stage, it schedules a normal continue. If it is a response for a top-level PDF, it schedules a response continue with a changed header that asks Chrome to download it. In all cases where it has a valid request id, it makes sure the paused request is released so the page does not hang.

**Call relations**: This is called by the browser event layer when Chrome reports a paused fetch. It uses _content_type to recognize PDFs, asks the session whether the frame is top-level, then hands off to _continue_request or _continue_response in the background.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This async helper tells Chrome to resume a paused request that does not need response rewriting. It exists because every paused request must be released, or the browser page can get stuck waiting forever.

**Data flow**: It receives the browser session id and Chrome’s request id. It sends a Fetch.continueRequest command through the browser connection. If Chrome rejects the command, times out, or the connection is no longer usable, it logs a warning and otherwise leaves state unchanged.

**Call relations**: on_fetch_paused schedules this when Chrome paused traffic before a response exists. It is the simple “let it through” path.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This async helper tells Chrome to resume a paused response, optionally rewriting its headers so Chrome treats it as a download. It is the part that turns viewer-only PDFs into saved files.

**Data flow**: It receives the session id, request id, response status code, response headers, and a yes-or-no force flag. If forcing is requested, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, while keeping the status code and other headers. Then it sends Fetch.continueResponse to Chrome. If the send fails, it logs a warning.

**Call relations**: on_fetch_paused schedules this once it has response information. It is used for both normal response release and the special forced-download path.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a new download. It creates a small entry so later code can track whether that download finishes.

**Data flow**: It reads the download guid and suggested filename from Chrome’s event data. It appends a new Download object to the browser session’s downloads list, starting with the state set to inProgress. If Chrome did not suggest a filename, it uses “download.”

**Call relations**: The browser event layer calls this when Chrome announces a download has begun. BrowserDownloads.wait later reads the recorded list to find completed downloads.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the stored state of an existing download as Chrome reports progress. It keeps the local download list in step with Chrome’s view of the world.

**Data flow**: It reads the guid and new state from Chrome’s event data. It searches the stored downloads for the matching guid and changes that download’s state, for example from inProgress to completed.

**Call relations**: The browser event layer calls this for progress events after on_download_begin has created a record. wait depends on these updates to know when a download has finished.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This async function briefly checks whether a recent browser action turned into a download. It is useful after navigation, because a click or URL change may either load a page or start saving a file.

**Data flow**: It receives the number of downloads that existed before the action. For up to a short grace period, it repeatedly compares the current download count to that earlier count. It returns true if a new download appears, otherwise false.

**Call relations**: Higher-level browser navigation code can call this after an action that might trigger a download. It does not start or finish downloads itself; it only watches the session’s download list for a new entry.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This async function waits until at least one browser download has completed, then returns the most recent completed download record. It gives other code a clear answer to “which download just finished?”

**Data flow**: It reads an optional timeout value from the input arguments and converts it with float_or_default. Until the deadline, it repeatedly scans the session’s download list for entries whose state is completed. If it finds one, it returns the latest completed download. If time runs out first, it raises a TimeoutError.

**Call relations**: This is called by code that needs to wait for a browser download result. It relies on on_download_begin and on_download_progress having populated and updated the shared downloads list.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns an optional timeout-like value into a floating-point number. It lets callers provide numbers directly, numeric strings, or nothing at all.

**Data flow**: It receives a JSON-style value and a default number. If the value is an int, float, or non-empty string, it converts it to a float. If the value is missing, it returns the default. If the value is another kind of data, it raises a validation error explaining that the value must be numeric.

**Call relations**: BrowserDownloads.wait uses this to interpret its timeout argument before it starts polling. Keeping this conversion separate makes the waiting logic simpler and gives bad inputs a clear error.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper extracts the main media type from HTTP response headers, such as turning “application/pdf; charset=utf-8” into “application/pdf.” It is used to recognize responses that Chrome would otherwise display inline.

**Data flow**: It receives a list of header-like JSON values. It looks for a dictionary whose name is Content-Type, ignoring letter case. If found, it returns the value before any semicolon, trimmed and lowercased. If no content type is present, it returns an empty string.

**Call relations**: on_fetch_paused calls this while deciding whether a top-level response is a PDF that should be forced into a download. It supplies the key clue for the forced-download decision.

*Call graph*: called by 1 (on_fetch_paused).


### Page execution
Page JavaScript execution is wrapped behind a safer Runtime helper for higher-level browser tools.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `request handling`

A browser automation system often needs to ask the page questions, such as “what is this value?” or “run this function on that page object.” The browser exposes this through the Chrome DevTools Protocol, often shortened to CDP, which is a message-based control channel for inspecting and driving a browser. This file is a friendly wrapper around the CDP Runtime area, which is the part that evaluates JavaScript.

The central class, BrowserRuntime, is given a browser session object. From that session it gets a connection, sends a command such as Runtime.evaluate or Runtime.callFunctionOn, and then checks whether the page threw an exception. This check matters because the browser may return a technically successful protocol reply even though the JavaScript itself failed. Without this wrapper, callers would need to remember to inspect the nested response every time, and errors could be silently mistaken for real values.

The two Protocol classes describe the shape of the objects BrowserRuntime needs: something that can send CDP messages, and something that can provide that sender. They are like saying, “I do not care what brand of phone you use, as long as it can make this call.”

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This defines the expected way to send a command to the browser’s DevTools connection. It is a contract: any real connection object used here must be able to send a method name, optional parameters, and an optional target session id, then return a JSON-like dictionary response.

**Data flow**: It receives the name of a browser command, optional command details, and optionally which browser target session should receive it. A real implementation sends that information over the browser control connection, then returns the browser’s reply as structured data.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on an object with this behavior after asking the session for its connection. This file only defines the required shape; the actual sending is supplied elsewhere by the browser connection implementation.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This defines the expected way to get the browser control connection from a session. BrowserRuntime uses it so it can send Runtime commands without knowing how the session stores or creates its connection.

**Data flow**: It takes no extra input beyond the session object itself. It returns an object that knows how to send commands to the browser through the DevTools protocol.

**Call relations**: BrowserRuntime calls this before sending Runtime.evaluate or Runtime.callFunctionOn. Like BrowserRuntimeCdp.send, this is a protocol contract rather than the actual implementation.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the expression’s value. It is used when the caller wants a simple page-side calculation or lookup, while also getting a clear Python error if the page code throws an exception.

**Data flow**: It receives a session id and a JavaScript expression string. It sends a Runtime.evaluate command with returnByValue enabled, meaning the browser should send back the actual value rather than a remote reference. It checks the browser reply for page-side exceptions, then digs out the returned value and gives that value back to the caller.

**Call relations**: This method gets the DevTools connection from the browser session and sends the Runtime.evaluate command through it. After the browser replies, it calls BrowserRuntime.raise_on_exception to turn any JavaScript failure into a normal RuntimeError, and it uses as_map to safely read nested dictionary-shaped response fields.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This calls a JavaScript function on an existing browser-side object and returns the function’s result as a dictionary-like JSON object. It is useful when the code already has a browser object reference and wants to run logic with that object as the target.

**Data flow**: It receives a session id, the browser object id to call on, the JavaScript function text, and optional argument values. It builds the Runtime.callFunctionOn request, converting each argument into the format the browser expects, sends it to the browser, checks for JavaScript exceptions, then returns the result value as a map. If the browser returns no value, it treats that as an empty object.

**Call relations**: This method follows the same pattern as BrowserRuntime.eval, but uses Runtime.callFunctionOn instead of Runtime.evaluate. It sends through the connection provided by the session, then hands the response to BrowserRuntime.raise_on_exception and uses as_map to read the nested result safely.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime response to see whether the JavaScript code failed inside the page. If it did, it raises a Python RuntimeError with the browser’s error description so callers do not accidentally treat a failed script as a successful result.

**Data flow**: It receives the full response dictionary from the browser. It looks for an exceptionDetails field, then tries to extract a useful message from the nested exception description or fallback text. If no exception details are present, it returns without changing anything; if an exception is present, it raises an error.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser reply. It acts as their shared safety gate before either method reads and returns the JavaScript result.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### Navigation state
Settling and tab management determine when page actions are ready and which browser targets are available.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigations, while waiting for the page to become usable`

After an automated click, key press, or navigation, the browser may still be doing work: sending requests, loading a new document, or painting visible content. This file provides the waiting logic that makes automation feel patient but not stuck. Without it, the system might inspect the page too early, before the result appears, or wait too long because modern websites often keep background network traffic alive forever.

The main idea is “consequence-scoped settling”: only wait for work caused by the current action. The Settle object keeps small pieces of state: which important network requests are still pending, which browser sessions are loading, and which sessions have painted visible content. A paint event is treated as a strong sign that the page is usable, like seeing the stage lights come on even if people are still moving props backstage.

The helper tracks_request filters out traffic that is usually not worth waiting for, such as images, fonts, low-priority prefetches, and analytics beacons. The wait method first lets the page run one small round of queued JavaScript work, then watches for loading, foreground requests, and paint. If the page paints, it gives requests a short grace period to finish. If nothing important started, it returns quickly. Time limits stop the automation from waiting forever.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It keeps foreground, action-related traffic and ignores common background noise such as images, fonts, low-priority requests, and analytics beacons.

**Data flow**: It receives a dictionary of browser request details. It reads the request type, priority, and URL host; if the request looks passive or analytics-related, it returns false. If the shape is unfamiliar, it errs on the safe side and returns true so the system does not accidentally ignore important work.

**Call relations**: Settle.on_request_started calls this when Chrome reports that a request began. Its answer decides whether that request is added to the pending set that Settle.wait later watches.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh tracker for page-settling state. It starts with no pending requests, no counted started requests, no loading sessions, and no painted sessions.

**Data flow**: It takes no outside data besides the new object being created. It initializes four pieces of memory: pending requests, a count of tracked requests that started, loading session IDs, and painted session IDs. The result is a Settle instance ready to receive browser events.

**Call relations**: BrowserSession creates this object when a browser session starts, and also creates a new one during close cleanup. Other methods on Settle then update and read this state while actions are being observed.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the action-specific state so the next wait only observes new work. This is important because the file is designed to wait for consequences of the current action, not leftovers from an earlier one.

**Data flow**: It reads the current pending requests, started count, and painted sessions, then clears them back to an empty or zero state. It does not clear the loading set, because document loading may still be an active browser condition.

**Call relations**: No direct caller is shown in the provided call graph. Conceptually, this is the reset button used before tracking a new action’s consequences.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records a newly started browser request if it is worth waiting for. This lets the settling logic know that the page is still doing foreground work triggered by the action.

**Data flow**: It receives Chrome request event details and the browser session ID. It extracts the request ID, asks tracks_request whether this request matters, and if so stores the pair of session ID and request ID in the pending set. It also increments the count of tracked requests that have started.

**Call relations**: This is called when request-start events are observed. It hands the filtering decision to tracks_request, and its stored pending requests are later checked by Settle.wait and Settle._drain_after_paint.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a browser request as no longer pending. This tells the settling logic that one piece of foreground work has completed or disappeared.

**Data flow**: It receives Chrome request event details and the browser session ID. It extracts the request ID and removes that session/request pair from the pending set if present. Nothing is returned; the Settle object’s memory is updated.

**Call relations**: This is meant to run when the browser reports a request finished or failed. Settle.wait relies on these removals to know when network activity has become quiet enough to continue.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session is currently loading a document. This prevents the system from declaring the action finished while navigation is still underway.

**Data flow**: It receives a session ID and adds it to the loading set. The visible result is internal state saying, “this session is still loading.”

**Call relations**: No direct caller is shown in the provided call graph. It is a small event hook that feeds loading information into Settle.wait.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has finished loading. This removes one reason for the wait loop to keep pausing.

**Data flow**: It receives a session ID and removes it from the loading set if present. After that, waits for this session no longer treat document loading as active unless it is marked loading again.

**Call relations**: No direct caller is shown in the provided call graph. It pairs with Settle.mark_loading, and Settle.wait reads the loading set to decide whether it can return.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that the browser has painted visible content for a session. A paint is used as a strong sign that the page has shown something useful to the user.

**Data flow**: It receives a session ID and adds it to the painted set. Later, Settle.wait sees this mark and switches to a short post-paint grace period instead of waiting up to the longer full cap.

**Call relations**: No direct caller is shown in the provided call graph. It supplies the paint signal that changes Settle.wait from ordinary network-waiting into the faster _drain_after_paint path.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears settled, but not forever. It balances three signs of readiness: queued page JavaScript has run, important network requests have drained, and visible content has painted.

**Data flow**: It receives a Chrome DevTools connection object, a session ID, and a maximum number of seconds to wait. First it asks _flush_page_tasks to let immediate page work run. Then it checks the Settle state: if paint already happened, it waits only through _drain_after_paint; if no tracked work started and the session is not loading, it returns right away. Otherwise it loops until the deadline, sleeping briefly while loading or pending requests continue, and returning once the page stays quiet across a small gap.

**Call relations**: This is the central method other action code would call after causing browser activity. It delegates the JavaScript flush to _flush_page_tasks and, when a paint has happened, delegates the shorter finishing wait to _drain_after_paint.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After visible content has appeared, gives important follow-up requests a short chance to finish. This avoids calling a page ready too early when it painted a shell first, while also avoiding long waits on pages that never become fully quiet.

**Data flow**: It receives a session ID and the overall deadline. It creates a shorter grace deadline, then watches loading and pending requests. If they clear and stay clear across a small gap, it returns; if they keep going, it returns when the grace time runs out.

**Call relations**: Settle.wait calls this whenever the session has painted. It is the post-paint path that makes painted pages finish quickly instead of using the longer navigation or action cap.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the page one quick chance to run JavaScript tasks that were queued by the user action. This helps ensure that any immediate requests caused by click handlers or zero-delay timers have been seen before waiting decisions are made.

**Data flow**: It receives the Chrome DevTools connection and session ID. It sends a Runtime.evaluate command that runs a tiny JavaScript promise based on setTimeout(0), waiting for that promise to resolve. If Chrome reports an error or timeout, it falls back to a short sleep instead of failing the whole settling process.

**Call relations**: Settle.wait calls this first. It uses Cdp.send to ask the browser to run the tiny script, so later wait checks have a more complete picture of requests that the action triggered.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `browser event handling and tab-related request handling`

This file is the tab controller for the browser automation layer. It talks to Chrome through CDP, the Chrome DevTools Protocol, which is a command-and-event API for controlling a browser from another program. In plain terms, it is like the clerk at a front desk: it records which rooms exist, opens new rooms, closes old ones, and waits until a room is ready before sending someone in.

The file stores each open tab as a `Tab`, including its browser target id, its CDP session id, keyboard state, and frame references. `BrowserTabEvents` records tab creation and destruction events that arrive from the browser, so the local list can be brought back in sync later.

`BrowserTabs` is the main worker. It attaches to new browser targets, enables the browser features this system needs, sets the viewport size, watches page loading events, and waits for navigation to settle before returning page information. It also treats the words `back` and `forward` as history navigation commands, while ordinary addresses are normalized into full URLs.

An important detail is that downloads are handled specially. A navigation that looks like an error may actually have turned into a file download, so `_goto` checks for that before raising a failure.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It leaves special commands like `back`, `forward`, and `about:blank` alone, keeps already-complete URLs unchanged, and adds `https://` to plain site names.

**Data flow**: It receives a text value such as `example.com`, `https://example.com`, or `back`. It checks whether the value is a special command or already starts with a URL scheme such as `http:`. It returns the original value when it is already complete, or a new string with `https://` added when it is not.

**Call relations**: Navigation uses this first so the rest of the tab code can deal with one clear kind of destination. `BrowserTabs.navigate` calls it before deciding whether to go to a page or move through history.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one browser tab. This record ties together the browser's target id, the active CDP session id, keyboard state, and frame bookkeeping.

**Data flow**: It receives the tab's browser target id and session id. It stores them, creates a fresh keyboard state, starts an empty frame sequence map, and creates an initial frame reference for the top-level page frame. The result is a `Tab` object ready for navigation and page inspection.

**Call relations**: `BrowserTabs.attach_tab` calls this after the automation code has successfully attached to a browser target. From that point on, the new `Tab` is added to the browser session's tab list and used by navigation, closing, and information queries.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when frames need readable internal labels instead of long browser ids.

**Data flow**: It receives a browser frame id. If that id has not been seen before in this tab, it assigns the next available number. It returns the number for that frame, whether newly assigned or already known.

**Call relations**: This helper belongs to the `Tab` record and supports frame tracking elsewhere in the browser automation layer. It does not call out to the browser; it only updates local bookkeeping.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the object that can send commands to the browser through CDP. It is a contract, not an implementation, saying that tab code needs a way to send a named command with optional parameters and an optional session id.

**Data flow**: Callers provide a CDP method name, optional data for that method, and optionally the browser session to send it to. The real connection object sends that command to the browser and returns a dictionary-like JSON response.

**Call relations**: Many `BrowserTabs` methods rely on this contract when they attach tabs, enable browser features, navigate pages, create targets, and close targets. The actual sending is supplied by the browser session's connection object.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines the shape of the object that can start waiting for one of several browser events. This lets the tab code say, in advance, which event will prove that something happened.

**Data flow**: Callers provide one or more event names and optionally a session id. The real connection object creates and returns a future, which is a placeholder for a result that will arrive later when the browser emits one of those events.

**Call relations**: `BrowserTabs._goto` and `BrowserTabs._history_step` use this idea before sending navigation commands, so they do not miss the browser's response event.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines the shape of the object that waits for a previously expected browser event, but only up to a time limit. This prevents navigation from hanging forever.

**Data flow**: Callers provide a future representing an expected event and a timeout in seconds. The real connection waits until the event arrives or the timeout is reached, then returns the event data or raises an error from the underlying implementation.

**Call relations**: Navigation helpers use this after asking the browser to move to a new page or history entry. It is part of the safety net around browser actions that may be slow or never finish.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines that a browser session must be able to provide its CDP connection. `BrowserTabs` uses that connection as its pipe to the browser.

**Data flow**: It takes no extra input beyond the browser session object. The real session returns an object that can send commands, expect events, and wait for events.

**Call relations**: Almost every action in `BrowserTabs` starts by asking the session for this connection. The protocol keeps `BrowserTabs` independent from the exact connection class.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines that a browser session must be able to provide access to download tracking. This is needed because some navigations become file downloads instead of normal pages.

**Data flow**: It reads the browser session's download support and returns an object that can check whether a new download appeared. It does not itself read files or navigate pages.

**Call relations**: `BrowserTabs._goto` uses this when a navigation reports an error, so it can tell the difference between a real failure and a successful download.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines that a browser session must be able to run JavaScript inside a tab. The tab code uses this to ask the page for simple facts such as its current URL and title.

**Data flow**: Callers provide a session id and a JavaScript expression. The real session runs that expression in the browser page and returns the JSON-like result.

**Call relations**: `BrowserTabs.tab_info` depends on this contract to build human-readable tab summaries without needing a separate browser command for each field.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed before this automation layer started watching tab events. This stops old tabs from being mistaken for newly opened tabs.

**Data flow**: It receives the browser's target listing as JSON-like data. It reads each target's id, converts the list into safe Python values, and stores the ids in `tab_events.initial_targets`. Nothing is returned.

**Call relations**: This is part of startup or event-listener setup. Later, `BrowserTabs.on_target_created` compares new target events against this saved set so only genuinely new page tabs are queued for attachment.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports a new page target and queues it to be attached as a tab. It filters out non-page targets and targets that were already known.

**Data flow**: It receives event parameters from the browser. It looks for a `targetInfo` object with type `page` and a string target id. If that id is not part of the initial targets and is not already queued, it appends the id to `created_targets`. It returns nothing.

**Call relations**: This function is called by the browser event plumbing when a target-created event arrives. It does not attach immediately; `BrowserTabs.sync` later consumes the queue and performs the actual attachment.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records that the browser says a target has gone away. This lets the local tab list be cleaned up later.

**Data flow**: It receives event parameters from the browser. If the event contains a string target id, it adds that id to the `destroyed_targets` set. It returns nothing.

**Call relations**: The event system calls this when the browser closes or destroys a target. `BrowserTabs.sync` later removes matching tabs from the local list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when the browser says its main frame started loading. It ignores subframes, because a small embedded frame should not always make the whole tab look like it is navigating.

**Data flow**: It receives browser event parameters and the session id that produced the event. If there is no session id, it does nothing. If the event's frame id belongs to the top-level frame of that tab, it tells the settle tracker that this session is loading.

**Call relations**: The browser event layer calls this during page loading. It uses `BrowserTabs.is_top_level_frame` to decide whether the event matters for whole-page readiness, then hands the state change to the `Settle` object.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having reached the DOM content stage. That means the browser has built the page's document structure, even if images or later painting may still be in progress.

**Data flow**: It receives browser event parameters and a session id. If a session id is present, it tells the settle tracker that the session has loaded. It does not return any value.

**Call relations**: This is called from browser event handling when the DOM content event fires. The settle tracker later uses this signal as one ingredient in deciding when navigation is ready enough to continue.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports an important visual lifecycle event for the main frame. In everyday terms, it helps answer: has the page actually appeared on screen?

**Data flow**: It receives event parameters and a session id. It ignores events without a session id and events whose lifecycle name is not one of the paint-related names. For matching top-level frame events, it tells the settle tracker that the session has painted.

**Call relations**: The browser event layer calls this for lifecycle events. It uses `BrowserTabs.is_top_level_frame` to avoid treating iframe activity as whole-page readiness, then passes the useful signal to `Settle`.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame id is the main frame for a known tab session. This matters because page-level loading decisions should usually be based on the main frame, not embedded frames.

**Data flow**: It receives a session id and a frame id. It scans the known tabs and returns `true` only if one tab has both that session id and a target id matching the frame id. It does not change any stored data.

**Call relations**: `BrowserTabs.on_frame_loading` and `BrowserTabs.on_lifecycle` call this before updating the settle tracker. It acts as a small filter that keeps loading state focused on the main page.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the automation system to an existing browser target and prepares it to behave like a controlled tab. This is where a raw browser target becomes a usable `Tab` object.

**Data flow**: It receives a target id. It asks the browser to attach to that target and reads back a session id. It initializes that session, enables document fetch interception for downloads, sets the viewport size, and returns a new `Tab` record containing the target and session ids.

**Call relations**: `BrowserTabs.sync`, `BrowserTabs.page`, and `BrowserTabs.create` call this whenever a target must be brought under automation control. It calls `BrowserTabs.init_session` for the common setup and then constructs the `Tab` object.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for a tab session: page events, lifecycle events, DOM access, and network events. Without this, later code would not receive the signals it relies on.

**Data flow**: It receives a session id. Through the CDP connection, it sends several enable commands for that session. It returns nothing, but after it completes the browser will emit the events and allow the queries this system needs.

**Call relations**: `BrowserTabs.attach_tab` calls this immediately after attaching to a target. It is the standard setup step before fetch interception, viewport emulation, navigation, and page inspection.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list back in line with tab creation and destruction events already received from the browser. It is a cleanup and catch-up step.

**Data flow**: It reads queued destroyed target ids and removes matching tabs. Then it reads queued created target ids one by one, skips targets already known, and tries to attach each new page as a tab. If attaching fails because the target is already gone or otherwise unavailable, it ignores that target and continues.

**Call relations**: `BrowserTabs.page` calls this before choosing a tab, and `BrowserTabs.tabs_context` calls it before reporting tab state. It consumes the event queues filled by `on_target_created` and `on_target_destroyed`, using `attach_tab` when a new target should become a controlled tab.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be used for an operation. If no tabs are open, it creates a blank one so callers always have a page to work with.

**Data flow**: It receives an optional tab id. First it synchronizes the local tab list with browser events. If there are no tabs, it creates an `about:blank` browser target and attaches to it. With no tab id, it returns the most recently known tab; with a tab id, it validates the number and returns that specific tab or raises a validation error if it is not open.

**Call relations**: `BrowserTabs.navigate` uses this to find the tab to move, and `BrowserTabs.close` uses it to find the tab to shut down. It relies on `sync` for freshness and `attach_tab` when it must create a fallback tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new destination, or backward or forward in its history, then waits until the page is ready enough to inspect. It returns the tab's current URL and title afterward.

**Data flow**: It receives a URL-like string and an optional tab id. It gets the target tab, normalizes the destination, resets the settle tracker, and either performs a history step or normal page navigation. Then it waits for the settle tracker to say the page has loaded and painted within a safety cap. Finally it asks for tab information and returns it.

**Call relations**: `BrowserTabs.create` calls this after opening a new tab so the tab can go to the requested URL. Internally it delegates to `_history_step` for `back` and `forward`, `_goto` for ordinary navigation, and `tab_info` for the final summary.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs ordinary navigation to a specific URL inside one tab. It waits for the browser's document-ready event and treats downloads as a special successful outcome.

**Data flow**: It receives a `Tab` and a full target URL. It notes how many downloads already existed, starts waiting for the DOM content event, and sends the browser's navigate command. If the browser reports an error, it cancels the wait and checks whether a new download started; if so, it returns without raising. If it was a real navigation error, it raises an exception. If the browser says no new loader was created, it cancels the wait and returns. Otherwise it waits for the page's DOM content event.

**Call relations**: `BrowserTabs.navigate` calls this for normal destinations after `normalize_url` has done its work. This helper owns the low-level browser navigation details so the public navigation method can stay focused on the broader flow.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browser history when possible. If there is no entry in that direction, it quietly does nothing.

**Data flow**: It receives a `Tab` and a step value, usually `-1` for back or `1` for forward. It asks the browser for the tab's navigation history, checks the current index, computes the desired entry, and returns early if the entry would be outside the history list. Otherwise it starts waiting for a navigation-related event, tells the browser to move to that history entry, and waits for confirmation.

**Call relations**: `BrowserTabs.navigate` calls this when the normalized destination is `back` or `forward`. It uses browser history data directly, while `navigate` handles the overall settle wait and final tab summary afterward.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and page title from a tab. This gives callers a small, human-friendly summary of where the tab is.

**Data flow**: It receives a `Tab`. It runs a short JavaScript expression in that tab that returns `location.href` and `document.title`, checks that the result is shaped like a map, and returns a dictionary with string `url` and `title` values.

**Call relations**: `BrowserTabs.navigate` calls this after navigation, `BrowserTabs.tabs_context` calls it for each open tab, and `BrowserTabs.tab_titles` calls it when only titles are needed. It relies on the browser session's JavaScript evaluation support.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all known tabs for callers that need to display or reason about the browser's tab state. It includes each tab's id, whether it is considered active, URL, and title.

**Data flow**: It first synchronizes the local tab list. It treats the last tab in the list as the current tab, then loops through every tab, asks for its URL and title, and collects those into a list. It returns a dictionary containing the current tab id and the tab list.

**Call relations**: `BrowserTabs.close` calls this after closing a tab so it can return the updated state. Other higher-level code can also use it as a compact view of the browser window.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of the currently known tabs. This is a lightweight view when callers do not need full tab details.

**Data flow**: It loops through the browser session's current tab list. For each tab, it asks `tab_info` for the page details and keeps only the title, using an empty string when the title is missing. It returns the list of title strings.

**Call relations**: This function builds on `BrowserTabs.tab_info` rather than duplicating the JavaScript query. It is a convenience method for code that needs tab names but not URLs or active-tab status.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and navigates it to a requested page. It returns the new tab's id along with its final URL and title.

**Data flow**: It receives an optional URL, defaulting to `about:blank`. It asks the browser to create a blank target, attaches to that target, appends the new `Tab` to the local list, navigates that tab to the requested URL, and returns the tab's index plus the navigation result.

**Call relations**: This is the public new-tab flow. It uses `attach_tab` to prepare the target and then calls `navigate` so new tabs follow the same loading, settling, and information-return behavior as existing tabs.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one tab and returns the updated tab overview. It also clears out-of-process frame session tracking because closing a tab can invalidate those extra sessions.

**Data flow**: It receives an argument dictionary that may contain a tab id. It converts that value into an integer if possible, finds the matching tab, tells the browser to close the tab's target, removes the tab from the local list, clears `oop_sessions`, and returns the fresh tabs context.

**Call relations**: This is the public close-tab flow. It uses `_tab_id` to interpret caller input, `page` to validate and find the tab, and `tabs_context` to report the browser state after the close.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a caller-provided tab id into an integer or decides that no tab id was provided. This allows close requests to accept numbers or numeric strings.

**Data flow**: It receives a JSON-like value. If the value is an integer, it returns it. If it is a float, it truncates it to an integer. If it is a non-empty string, it converts it with `int`. For missing, empty, or unsupported values, it returns `None`.

**Call relations**: `BrowserTabs.close` uses this before asking `page` for the tab to close. It keeps input interpretation separate from the actual browser-closing work.

*Call graph*: called by 1 (close).

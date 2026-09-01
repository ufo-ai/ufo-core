# Browser DevTools Connection and Session Lifecycle  `stage-10.3.2`

This stage is the browser control room. It is used during the main work loop, whenever the agent needs to open pages, inspect them, click, type, switch tabs, run page scripts, or collect files. It also handles setup and cleanup of the live connection to Chrome.

At the outside, backend.py offers the per-turn browser tools and opens the browser connection only when needed. session.py is the central desk for one Chrome session, coordinating all browser abilities through that connection. tabs.py manages the tab list and turns actions like open, close, switch, and navigate into Chrome DevTools Protocol messages. Chrome DevTools Protocol is Chrome’s built-in remote-control channel.

cdp.py runs that channel over one WebSocket, sending commands, receiving replies, and routing browser events. wire.py checks the JSON messages at the boundary so bad data is caught early. runtime.py safely runs JavaScript inside the page and reports script failures as normal errors. settle.py waits until a page action has really finished, while ignoring unrelated background noise. downloads.py watches and completes downloads, including PDFs. dialogs.py prevents pop-up dialogs from blocking the automation.

## Files in this stage

### Browser Session Orchestration
Top-level browser tool entry points lazily create, operate, and clean up a live Chrome automation session.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser tool use and turn cleanup`

This file is the bridge between high-level browser tools and the actual Chrome browser session. A new `BuaSurface` is made for a turn, but it does not immediately connect to Chrome. It waits until the first browser action, then asks a `CdpProvider` for a lease. A lease is like a temporary key to a browser instance. From that lease it gets a browser endpoint, opens a `BrowserSession`, and then forwards tool requests to that session.

The file also protects important edges around browser lifetime. If a turn crashes before cleanup, it may leave behind a durable token, which is a saved reconnect handle. On recovery, this surface can reattach to the same live browser instead of starting over and losing the page state. On normal cleanup, it closes the session, releases the lease, and clears the token so future turns do not accidentally reconnect to an old browser.

File upload and download are handled here because the browser may run somewhere different from the workspace. For uploads, workspace paths are checked, read safely from the sandbox when needed, shipped through the lease, and then verified so the page really received the bytes. For downloads, the lease fetches the downloaded bytes, the filename is made safe, and the content is returned as base64 text.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or reuses the browser session for this turn. It is the lazy doorway: if no browser tool is used, no browser connection is made.

**Data flow**: It starts with the current `BuaSurface` state. If a session already exists, it returns it. Otherwise it gets or creates a lease, asks the lease for the browser endpoint and download directory, builds a `BrowserSession`, opens it, stores it, and returns the ready session.

**Call relations**: Nearly every browser action calls this first, including navigation, reading, finding, typing, tab actions, uploads, downloads, and computer-style control. If there is no lease yet, it hands off to `BuaSurface._acquire_lease`; then it creates the `BrowserSession` that the rest of the methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets access to a browser instance, either by reconnecting to a saved session or by creating a fresh one. This is what lets recovery continue after a crash without losing the open page, when possible.

**Data flow**: It looks for a saved token in the scoped store. If one exists, it asks the provider to reattach to that existing session. If that session is gone, it clears the bad token. Then it leases a fresh browser, saves the new token, and returns the lease.

**Call relations**: `BuaSurface._open` calls this when the surface does not yet have a lease. It uses `BuaSurface._stored_token` to check for a recovery handle and `BuaSurface._store_token` to save or clear that handle.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Reads the saved browser reconnect token for the current conversation, if this surface has enough context to do so.

**Data flow**: It checks whether both a scoped store and conversation id are available. If either is missing, it returns nothing. Otherwise it reads the conversation-specific token key and returns the value only if it is a string.

**Call relations**: `BuaSurface._acquire_lease` calls this before deciding whether to reattach to an existing browser or lease a new one.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser reconnect token for the current conversation. This controls whether a later recovery replay can reconnect to the same browser.

**Data flow**: It receives either a token string or `None`. If the store or conversation id is missing, it does nothing. Otherwise it writes that value under the conversation-specific key, where `None` means the saved token is cleared.

**Call relations**: `BuaSurface._acquire_lease` uses this to save a fresh token or remove a stale one. `BuaSurface.aclose` uses it during normal cleanup so later turns do not reattach to a released browser.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates the browser to a requested URL, optionally in a specific tab. It also checks that the URL input is actually text.

**Data flow**: It receives tool arguments, opens the browser session, reads `url`, validates that it is a string, converts the optional tab id with `_tab_id`, and passes both to the session. The result from the session is returned to the caller.

**Call relations**: This is one of the public tool-surface methods. It calls `BuaSurface._open` to ensure the browser is ready and `_tab_id` to normalize the tab identifier before handing the work to the browser session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs. This gives the tool caller a view of what tabs exist and which page state is available.

**Data flow**: It receives the tool arguments, opens the browser session, asks the session for tab context, and returns that response unchanged.

**Call relations**: As a public tab-related tool method, it first goes through `BuaSurface._open` so it has a live session, then delegates the actual browser inspection to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no usable URL is supplied, it opens a blank page.

**Data flow**: It receives arguments, opens the browser session, looks for a non-empty text `url`, falls back to `about:blank` if needed, and asks the session to create the tab. The session's reply is returned.

**Call relations**: This method is part of the tab tool surface. It uses `BuaSurface._open` for the shared browser setup and then hands the tab creation request to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab according to the given arguments. The exact tab selection is handled by the browser session.

**Data flow**: It receives the caller's arguments, opens the browser session, passes those arguments to the session's tab-close operation, and returns the session response.

**Call relations**: This public tab method relies on `BuaSurface._open` for the live connection, then delegates the browser-specific tab closing to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a page file input. It makes sure each requested path is treated as a workspace path and is placed somewhere the leased browser can actually read.

**Data flow**: It receives upload arguments, opens the session, validates that `files` is a list of non-empty strings, converts each to a safe workspace path, and asks the lease to place each file for the browser. For remote browsers, the lease may call back into `_read` to get the bytes. It then sends the placed file paths to the browser session, waits for the page to really receive the uploaded bytes, and returns the upload response.

**Call relations**: This method combines browser control with transport work. It calls `BuaSurface._open` for the session, `workspace_path` to keep paths inside the workspace, `BuaSurface._lease` to access the active lease, and `BuaSurface._settle_upload` to verify the upload after the session tries to attach the files.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until the web page's file input actually contains the uploaded bytes. This avoids a subtle failure where a remote file name appears attached before the file contents have arrived.

**Data flow**: It receives the browser session and upload arguments. If no file bytes were shipped by this surface, it returns immediately. Otherwise it compares the page-reported attached file sizes with the expected sizes. If they do not match, it sleeps briefly, re-attaches the files, and tries again until it succeeds or runs out of attempts. On failure, it raises an error explaining the mismatch.

**Call relations**: `BuaSurface.upload_file` calls this after asking the browser session to attach files. It asks the session for attached sizes and may call the session's upload operation again while waiting for the remote transport to finish.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active browser lease, and fails clearly if code asks for it before one exists.

**Data flow**: It checks the surface's `lease` field. If there is a lease, it returns it. If not, it raises a runtime error saying the browser has no CDP lease.

**Call relations**: `BuaSurface.upload_file` uses this when placing files for the browser. `BuaSurface.wait_for_download` uses it when fetching downloaded bytes from wherever the leased browser stored them.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file from the sandbox so it can be shipped to a remote browser. It limits file size first, so a huge upload does not fill the server's memory.

**Data flow**: It receives a resolved path. It requires a sandbox, safely quotes the path for shell commands, asks the sandbox for the file size, rejects unreadable or oversized files, then base64-encodes the file inside the sandbox. Back in this process, it decodes the base64 text into bytes in a background thread, records the byte length for later upload verification, and returns the bytes.

**Call relations**: This function is used as a callback by the lease placement flow started in `BuaSurface.upload_file`. It uses `shlex.quote` to protect the shell command and `asyncio.to_thread` so decoding a large file does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Asks the browser session for a structured reading of the current page. This is used when the tool needs more than just raw text.

**Data flow**: It receives reading arguments, opens the session, passes the arguments to the session's page-reading method, and returns the result.

**Call relations**: This public tool method follows the common pattern: `BuaSurface._open` prepares or reuses the session, and the session does the browser-specific page reading.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets text from the current page. This gives the caller a simpler text view of what the browser is showing.

**Data flow**: It receives arguments, opens the browser session, asks the session for page text, and returns the session's response.

**Call relations**: Like the other browser tool methods, it depends on `BuaSurface._open` for connection setup and then delegates the actual page access to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, optionally using a host-side find completer to help interpret or complete the search. This supports browser tools that need to locate page elements reliably.

**Data flow**: It receives find arguments, opens the browser session, and passes both the arguments and the optional `find_completer` into the session. The session's find result is returned.

**Call relations**: This public search method uses `BuaSurface._open` for browser access. It then hands off to the session, along with the completer object that was injected into the surface when it was created.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on the page. It is the tool-surface entry for typed user-style input into web forms.

**Data flow**: It receives form input arguments, opens the browser session, passes the arguments to the session's form input operation, and returns the result.

**Call relations**: This method is called as part of browser tool use. It follows the shared pattern of preparing the session through `BuaSurface._open` and letting `BrowserSession` perform the page interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as actions that mimic direct interaction with the visible browser. It gives the tool caller a more general control path than page reading or form filling.

**Data flow**: It receives action arguments, opens the browser session, forwards the arguments to the session's computer-control method, and returns the session's response.

**Call relations**: This public tool method depends on `BuaSurface._open` for a ready browser connection, then passes control to the session for the actual interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns the downloaded file content. It also makes the filename safe so it cannot sneak directory paths into the caller's workspace.

**Data flow**: It receives wait arguments, opens the browser session, waits for the session to report a completed download, asks the lease to fetch the downloaded bytes, base64-encodes those bytes in a background thread, trims the filename to a safe single name, and returns the filename, encoded content, and size.

**Call relations**: This method combines browser observation and transport retrieval. It calls `BuaSurface._open` to wait through the session, `BuaSurface._lease` to fetch the bytes from the leased browser's storage, `contained_leaf` to make the filename safe, and `asyncio.to_thread` to avoid blocking while encoding.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved reconnect token at the end of a normal turn. This prevents hosted browser sessions from being orphaned and prevents later turns from reconnecting to something that was intentionally released.

**Data flow**: It checks whether a session exists and closes it, then always tries to close the lease even if session closing fails. It clears both stored fields on the surface and writes `None` to the token store.

**Call relations**: Turn cleanup calls this after browser tool use. It calls `BuaSurface._store_token` to clear the durable token; if a hard crash skips this function, the token remains available for recovery, which is the intended crash-reconnect behavior.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a user-provided tab id into an integer when possible, or returns no tab id when the value is missing or not meaningful.

**Data flow**: It receives a JSON-like value. Booleans and unsupported values become `None`; integers are kept; floats are converted to integers; non-empty strings are parsed as integers. The result is either an integer tab id or `None`.

**Call relations**: `BuaSurface.navigate` calls this before asking the browser session to navigate, so tab ids from tool arguments are normalized in one small helper.

*Call graph*: called by 1 (navigate).


### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `per browser session / tool turn, from open through teardown`

A BrowserSession is the object that turns a Chrome debugging address into usable browser actions. Chrome exposes a CDP endpoint, meaning a Chrome DevTools Protocol connection: a WebSocket that lets another program inspect and control the browser. This file opens that connection, subscribes to important browser events, and keeps the short-lived state needed during a turn, such as known tabs, active downloads, pop-up dialogs, background tasks, and frame sessions.

On startup, the session resolves the WebSocket URL, connects to Chrome, checks whether the browser is on macOS, enables download reporting, starts discovering tabs, listens for page loading and network activity, and creates a fresh blank tab. After that, most public methods are simple doorways into more specialized helper objects. For example, tab actions go to BrowserTabs, page reference lookup goes to BrowserPage, text and accessibility-style page reading go to BrowserContent, file uploads and form typing go to BrowserForms, and JavaScript execution goes to BrowserRuntime.

This design matters because all those helpers need the same live connection and shared state. The session is like the front desk in a hotel: guests ask for many different services, but the desk knows which room, key, and staff member are involved. When closed, it tries to close opened tabs, disconnects, cancels background tasks, and resets its memory so stale browser state does not leak into the next use.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty session object and records the browser endpoint, model coordinate size, and download directory it will use later. It does not connect to Chrome yet; it only prepares the shared state that other browser helpers will read and update.

**Data flow**: It receives an optional CDP endpoint, optional model name, and download directory. It chooses a coordinate space for screenshots, initializes lists and maps for tabs, downloads, dialogs, out-of-process frames, and background tasks, and creates fresh settle and tab-event trackers. The result is a BrowserSession ready to be opened.

**Call relations**: BuaSurface._open creates this object before asking it to connect. During construction it uses model_coordinate_space to pick the right screen coordinate size and creates Settle and BrowserTabEvents objects so later page-loading and tab events have somewhere to be recorded.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the session if it is not already open. It is the safe public entry for starting the Chrome connection.

**Data flow**: It checks whether a connection already exists. If not, it runs the bootstrap setup; if setup fails partway through, it closes anything that was opened and then re-raises the error. The result is either a fully usable session or a cleaned-up failed attempt.

**Call relations**: The async context manager __aenter__ calls this when a session is used in an 'async with' block. It delegates the real setup to _bootstrap and relies on close to undo partial startup after errors.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the actual Chrome setup work: connect, register event listeners, configure downloads, discover tabs, and create the initial tab. Without this, the session would have no live browser connection and no event feed.

**Data flow**: It reads the stored CDP endpoint and headers, resolves the WebSocket URL, opens a CdpConnection, asks Chrome for version information, and records whether the browser looks like macOS. It builds tab, download, and dialog helper objects, wires their event callbacks into Chrome events, enables download behavior, starts target discovery, and creates a blank tab. It leaves the session with one attached tab and a ready connection.

**Call relations**: open calls this as the main startup phase. It calls resolve_ws_url and CdpConnection.open to reach Chrome, creates helper readers through tab_reader, download_reader, and dialog_reader, and uses as_str when turning Chrome's created target id into the exact string needed for tab attachment.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts the session down and forgets all per-session state. It is designed to be safe even if startup failed halfway.

**Data flow**: It looks at the current connection and tabs. If connected, it asks Chrome to close each known tab, ignores common close-time errors, then closes the CDP connection. Finally it clears the connection, tabs, frame-session map, downloads, dialogs, scroll flag, background tasks, and event trackers.

**Call relations**: __aexit__ calls this at the end of an async context block, and open calls it when bootstrap fails. It recreates Settle and BrowserTabEvents so a reused BrowserSession object starts from a clean slate after closing.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python's async context manager pattern, which automatically opens and later closes resources.

**Data flow**: It receives the session object, awaits open, and then returns the same session for use inside the context block. The visible change is that the session should now have a live Chrome connection.

**Call relations**: It calls open as the startup half of the context-manager flow. Code using 'async with BrowserSession(...) as session' gets a ready session from this method.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the session automatically when an async context block ends, whether it ended normally or because of an error.

**Data flow**: It receives exit information from Python but does not inspect it. It awaits close, which disconnects from Chrome and clears stored state. It returns nothing.

**Call relations**: It is the teardown partner to __aenter__. Its only handoff is to close, which performs the actual cleanup.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection, or raises a clear error if the browser is not open. Helper objects use this when they need to send commands to Chrome.

**Data flow**: It reads self.conn. If the connection is missing, it raises BrowserUnavailable; otherwise it returns the connection object. It does not change session state.

**Call relations**: This is a guardrail for the rest of the browser machinery. Instead of letting helpers fail mysteriously when no browser is open, it reports the specific problem.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts a background asynchronous task and remembers it so the session can cancel it during cleanup. This is useful for work that must keep running while browser actions continue.

**Data flow**: It receives a coroutine, schedules it with asyncio.ensure_future, stores the resulting task in the session's background-task set, and arranges for the task to remove itself from the set when it finishes. It returns nothing.

**Call relations**: Other session-related helpers can use this to launch side work without losing track of it. close later cancels anything still recorded in the background-task set.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main page frame rather than an embedded frame. This helps event listeners decide which page-loading signals matter most.

**Data flow**: It receives a CDP session id and frame id, creates a BrowserTabs helper, and asks that helper to decide whether the frame is top-level. It returns a true-or-false answer.

**Call relations**: It delegates to tab_reader because tab and frame knowledge lives in BrowserTabs. This keeps frame classification consistent with the rest of tab tracking.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready to receive and report page events. This is part of keeping tab or frame connections usable.

**Data flow**: It receives a session id, creates a BrowserTabs helper, and asks it to initialize that session. The visible result is that Chrome-side settings and listeners for that session are prepared.

**Call relations**: It hands the work to tab_reader, because BrowserTabs knows what setup each target session needs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the current tab, or a chosen tab, as the session's working page. Tool calls use this when they need to know which browser tab to act on.

**Data flow**: It receives an optional tab id, creates a BrowserTabs helper, and asks for the matching page. It returns a Tab object representing the selected browser tab.

**Call relations**: It delegates tab lookup rules to tab_reader. This makes page selection share the same behavior as navigation, tab information, and tab closing.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a given URL. This is the main high-level way for the tool surface to load a web page.

**Data flow**: It receives a URL and optional tab id, creates a BrowserTabs helper, and asks it to perform the navigation. It returns a JSON-like dictionary describing the result.

**Call relations**: It hands navigation to tab_reader because BrowserTabs owns target selection and page loading details. The session supplies the shared connection and tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a plain information record for a specific tab. This is useful when higher layers need to describe a tab to a user or tool caller.

**Data flow**: It receives a Tab object, creates a BrowserTabs helper, and asks for that tab's information. It returns a JSON-like dictionary with the tab details.

**Call relations**: It delegates to tab_reader so tab formatting stays next to the tab-tracking logic.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns a snapshot of the browser's tab situation. It helps callers understand which tabs exist and which one is active or relevant.

**Data flow**: It creates a BrowserTabs helper, asks it for the tabs context, and returns that context as a JSON-like dictionary. It reads session tab state through the helper.

**Call relations**: It is one of the public tab-surface methods backed by tab_reader, alongside tab_titles, tabs_create, and tabs_close.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the visible titles of the current tabs. This gives a compact human-readable view of what is open.

**Data flow**: It creates a BrowserTabs helper, asks it to collect tab titles, and returns a list of strings. It does not directly alter session state.

**Call relations**: It delegates to tab_reader, which knows how to query and format tab title information.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper tied to this session. BrowserTabs is the specialist for tab discovery, tab selection, navigation, and tab events.

**Data flow**: It takes the current BrowserSession and the fixed viewport size and builds a new BrowserTabs object. The helper can then read and update the session's tab-related state.

**Call relations**: _bootstrap uses this while wiring startup events, and many tab-facing methods use it before delegating their work. It is the common doorway from BrowserSession into tab-specific behavior.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper tied to this session. BrowserPage is used for understanding page frames and resolving references to page elements or positions.

**Data flow**: It takes the current session, viewport size, and maximum frame depth and returns a BrowserPage object. The helper can use the session connection and page state to inspect frames.

**Call relations**: resolve_ref and ref_point call this when they need page-reference logic. Keeping this in one factory makes those methods use the same viewport and frame-depth rules.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper tied to this session. BrowserContent is the specialist for reading page structure, text, and search results.

**Data flow**: It wraps the current session in a BrowserContent object and returns it. The helper can then use the session's browser connection to inspect page content.

**Call relations**: tree, read_page, get_page_text, and find call this before delegating their content-reading work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper tied to this session. BrowserDownloads tracks browser download events and waits for downloads to finish.

**Data flow**: It takes the current session and the maximum wait time and returns a BrowserDownloads object. The helper reads and updates the session's download list.

**Call relations**: _bootstrap uses it to attach download event callbacks, and wait_for_download uses it when a caller wants to wait for a completed download.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper tied to this session. BrowserDialogs records JavaScript dialogs, such as alerts or confirms, that appear while browsing.

**Data flow**: It wraps the current session in a BrowserDialogs object and returns it. The helper can append dialog messages to the session's dialog list when Chrome reports them.

**Call relations**: _bootstrap calls this while registering the Page.javascriptDialogOpening event listener.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper tied to this session. BrowserForms performs form typing and file-upload related actions.

**Data flow**: It returns a BrowserForms object that can use the session connection and shared browser state. The session itself does not do the form work directly.

**Call relations**: upload_file, attached_sizes, and form_input call this before delegating to the form specialist.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper tied to this session. BrowserRuntime runs JavaScript and calls functions on browser-side JavaScript objects.

**Data flow**: It returns a BrowserRuntime object built around the current session. That helper can send runtime commands through the session's CDP connection.

**Call relations**: eval_js and call_on call this when they need JavaScript execution behavior.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page. It exposes tab creation as part of the browser tool surface.

**Data flow**: It receives a URL, or uses about:blank if none is provided, creates a BrowserTabs helper, and asks it to create the tab. It returns a JSON-like description of the created tab or action result.

**Call relations**: It delegates to tab_reader, so tab creation updates the same session tab state used by navigation and tab listing.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab chosen by the provided arguments. It lets callers remove tabs through the session interface.

**Data flow**: It receives a JSON-like argument dictionary, creates a BrowserTabs helper, and asks it to close the requested tab. It returns a JSON-like result describing what happened.

**Call relations**: It delegates to tab_reader because BrowserTabs owns tab lookup, close commands, and session tab bookkeeping.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads a file through a web page's file input. This is needed because browser automation must tell Chrome which local file path to attach.

**Data flow**: It receives JSON-like upload arguments, creates a BrowserForms helper, and asks it to perform the upload. It returns a JSON-like result from the form helper.

**Call relations**: BuaSurface._settle_upload calls this as part of upload handling. The method delegates to form_reader, which knows how to interact with file inputs.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached during an upload flow. This can confirm that the intended files were selected.

**Data flow**: It receives JSON-like arguments, creates a BrowserForms helper, and asks it for attached file sizes. It returns a list of integer sizes.

**Call relations**: BuaSurface._settle_upload calls this after upload-related work. The method delegates to form_reader so upload details stay in BrowserForms.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree of page content, optionally filtered by type. This gives callers a structured overview of what is on the page.

**Data flow**: It receives JSON-like arguments and a filter string, creates a BrowserContent helper, and asks it to build the tree. It returns the tree as a string.

**Call relations**: It is a content-reading doorway into content_reader, which performs the actual page inspection.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result suitable for tool callers. It is a higher-level page understanding action than simply grabbing raw HTML.

**Data flow**: It receives JSON-like arguments, creates a BrowserContent helper, and asks it to read the page. It returns a JSON-like dictionary with the page-reading result.

**Call relations**: It delegates to content_reader, keeping page-content extraction outside the session's connection-and-state orchestration role.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Gets readable text from the page. This is useful when a caller needs the words on the page rather than element structure or screenshots.

**Data flow**: It receives JSON-like arguments, creates a BrowserContent helper, and asks it to collect page text. It returns a JSON-like dictionary containing the text result.

**Call relations**: It uses content_reader, the same content specialist used by read_page, tree, and find.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within page content and can use a completion helper to report or refine matches. It supports find-like behavior for browser tools.

**Data flow**: It receives JSON-like search arguments and an optional FindCompleter, creates a BrowserContent helper, and asks it to run the search. It returns a JSON-like dictionary describing the result.

**Call relations**: It delegates to content_reader, which knows how to inspect page content and use the optional completion object.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or enters data into a form field on the page. This is the session-level doorway for filling web forms.

**Data flow**: It receives JSON-like form input arguments, creates a BrowserForms helper, and asks it to apply the input. It returns a JSON-like result from that operation.

**Call relations**: It delegates to form_reader, keeping form-specific browser actions in BrowserForms.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs low-level computer-style browser actions, such as mouse or keyboard actions, against the browser viewport. This supports interactions that are closer to using the screen directly.

**Data flow**: It receives JSON-like action arguments, creates a BrowserComputer with the session, viewport, and maximum wait time, and runs it. It returns a JSON-like result of the computer action.

**Call relations**: Unlike most methods, it constructs BrowserComputer directly because each run is a self-contained action executor using the current session.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to complete and returns its recorded download information. It reports the completed download rather than reading the downloaded bytes itself.

**Data flow**: It receives JSON-like wait arguments, creates a BrowserDownloads helper, and asks it to wait for a matching download. It returns a BrowserDownload record.

**Call relations**: It delegates to download_reader, which also supplies the download event callbacks registered during _bootstrap.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a specific browser session. This is a direct way to ask the page runtime for a value or side effect.

**Data flow**: It receives a CDP session id and JavaScript expression, creates a BrowserRuntime helper, and asks it to evaluate the expression. It returns the JSON-like value reported by Chrome.

**Call relations**: It delegates to runtime_reader, which knows how to send runtime commands through the CDP connection.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side JavaScript object. This is useful when code already has a reference to an object inside the page.

**Data flow**: It receives a session id, object id, function source, and optional argument list. It creates a BrowserRuntime helper and asks it to call the function on that object. It returns a JSON-like dictionary with Chrome's result.

**Call relations**: It delegates to runtime_reader, sharing the same JavaScript execution path as eval_js.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Resolves a page reference string into the frame and index it points to. This turns a compact reference used by tools into something the browser page helper can act on.

**Data flow**: It receives a Tab and a reference string, creates a BrowserPage helper, and asks it to resolve the reference. It returns a pair: the matching FrameNode and an integer index.

**Call relations**: It delegates to page_reader, which owns frame traversal and reference interpretation.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds screen coordinates for a page reference. This lets later actions click or point at the item described by a reference string.

**Data flow**: It receives a Tab and reference string, creates a BrowserPage helper, and asks for the reference's point. It returns an x,y coordinate pair.

**Call relations**: It delegates to page_reader, using the same page-reference logic as resolve_ref but producing a coordinate for interaction.

*Call graph*: calls 1 internal fn (page_reader).


### Tabs and Page Readiness
Tab navigation and switching are paired with page-settling logic so browser actions continue only after relevant work completes.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling`

This file solves a practical problem: a browser can create and destroy tabs at any time, and the rest of the system needs a clean, reliable way to know which tabs exist and what page each one is showing. Without this file, commands like “go to this URL,” “open a new tab,” or “close tab 2” would have to deal directly with browser protocol details and race conditions.

The main object is BrowserTabs. It watches browser target events, where a “target” is Chrome’s word for something controllable, such as a page tab. When a new page target appears, BrowserTabs attaches to it, turns on page, document, network, and lifecycle events, sets the viewport size, and starts intercepting document downloads. When a target disappears, it removes the matching tab from its list.

A Tab stores the browser’s target ID, the attached session ID, keyboard state, and frame tracking information. BrowserTabs also works with a Settle object, which waits until a page has loaded and painted enough to be considered ready. This is like waiting until a shop has not only unlocked the door, but also turned on the lights and stocked the counter before sending someone in.

The file also normalizes user-friendly URLs, treats “back” and “forward” as history commands, detects download navigations, and returns simple tab summaries with URL and title.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-entered destination into something the browser can navigate to. It leaves special commands and already-complete URLs alone, but adds “https://” when someone types a bare domain like “example.com”.

**Data flow**: It receives a URL-like string. If the string is “back”, “forward”, or “about:blank”, it returns it unchanged; if it already starts with a URL scheme such as “http:”, it also returns it unchanged; otherwise it prefixes the text with “https://”.

**Call relations**: BrowserTabs.navigate calls this before deciding whether to move through history or load a new page. The regular expression check is used only to recognize whether the text already has a scheme.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one open browser tab. It stores the browser identifiers needed to talk to that tab and prepares per-tab keyboard and frame state.

**Data flow**: It receives a target ID and a session ID from the browser. It saves both, creates a fresh KeyboardState, starts an empty frame-number map, and creates a root FrameNode tied to the tab’s main target and session.

**Call relations**: BrowserTabs.attach_tab calls this after Chrome confirms that the code has attached to a target. The Tab object then becomes the in-memory handle used by navigation, closing, title lookup, and frame tracking.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when browser frame IDs are long or awkward but the system wants a simpler local ordering.

**Data flow**: It receives a frame ID. If that frame ID is already known, it returns the existing number; if not, it assigns the next number and stores it before returning it.

**Call relations**: This method is part of the Tab state object. It is not called inside this file, but other frame-related code can use it to translate raw browser frame IDs into consistent local sequence numbers.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a method that sends a command to the browser through Chrome DevTools Protocol. Chrome DevTools Protocol, or CDP, is the control channel used to ask Chrome to do things and report events.

**Data flow**: A caller provides a protocol method name, optional parameters, and optionally a session ID for a specific tab. The concrete connection sends that command to the browser and returns a dictionary-like JSON result.

**Call relations**: This is a protocol declaration, not the implementation. BrowserTabs relies on any connection object matching this shape when it attaches tabs, enables events, navigates, creates targets, and closes targets.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how callers can start waiting for one of several browser events before they send a command that should trigger it. This avoids missing fast events that arrive immediately after a command.

**Data flow**: A caller names one or more event types and optionally a session ID. The concrete connection returns a future, which is a promise-like object that will later contain the event data.

**Call relations**: BrowserTabs uses this pattern in navigation and history movement. It begins expecting an event, sends the browser command, then waits for the future through BrowserTabCdp.wait.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how callers wait for an expected browser event with a time limit. The time limit prevents the system from hanging forever if the browser never reports the event.

**Data flow**: It receives a future created by expect and a timeout in seconds. The concrete connection waits until the future completes or the timeout is reached, then returns the event data or raises an error.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this after sending navigation commands. It is part of the CDP connection contract that BrowserTabs depends on.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how BrowserTabs gets the active browser connection. The connection is the object that actually sends low-level commands to the browser.

**Data flow**: It takes no extra input beyond the session object. It returns an object that follows the BrowserTabCdp interface.

**Call relations**: Nearly every BrowserTabs operation asks the session for this connection before talking to Chrome. This is a protocol method, so the real behavior is supplied by the wider browser session object.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how BrowserTabs gets the component that can tell whether navigation turned into a file download. This matters because a download can look like a failed page load even though it is a valid user action.

**Data flow**: It reads from the browser session and returns a BrowserDownloads object. BrowserTabs can then ask that object whether a new download appeared.

**Call relations**: BrowserTabs._goto uses this after a navigation error message. If the failed navigation actually started a download, _goto treats it as acceptable instead of raising an error.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how code can run a small JavaScript expression inside a tab and get the result back. JavaScript is used here to ask the page for simple facts like its current URL and title.

**Data flow**: It receives a session ID and a JavaScript expression. The concrete browser session runs that expression in the matching tab and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info depends on this to build friendly tab summaries. This is a protocol declaration; the real evaluation logic lives outside this file.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser page targets already existed before this tab tracker started watching events. This prevents old tabs from being mistaken for newly created tabs.

**Data flow**: It receives a browser response containing target information. It reads the targetInfos list, extracts each targetId, converts it to text, and stores the set on browser.tab_events.initial_targets.

**Call relations**: Event setup code elsewhere can call this once after listing current targets. Later, BrowserTabs.on_target_created checks this stored set before deciding that a target should be added as a new tab.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when Chrome reports a new page target and queues it to become a tracked tab. It filters out non-page targets and targets that were already known at startup.

**Data flow**: It receives event parameters and an optional session ID. It reads targetInfo, checks that the target is a page with a string targetId, and appends that ID to created_targets if it is new and not already queued.

**Call relations**: This is an event callback. BrowserTabs.sync later consumes the queued target IDs and calls BrowserTabs.attach_tab so the system can actually control those tabs.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when Chrome says a target has gone away. It remembers the target ID so the local tab list can be cleaned up safely later.

**Data flow**: It receives event parameters and reads targetId. If the ID is a string, it adds it to browser.tab_events.destroyed_targets.

**Call relations**: This is an event callback. BrowserTabs.sync later removes any Tab objects whose target IDs appear in this destroyed set.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. It ignores subframes, such as embedded iframes, because those should not by themselves mean the whole tab is navigating.

**Data flow**: It receives event parameters and a session ID. If there is a session and the event’s frameId matches the tab’s top-level frame, it tells the Settle object that this session is loading.

**Call relations**: Browser event dispatch code calls this when page frame loading starts. It uses BrowserTabs.is_top_level_frame to avoid noisy child-frame events, then updates browser.settle so navigation waits know the page is not ready yet.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having loaded its document structure. This corresponds to the page reaching the point where the document is available, even if images or later visual updates may still be happening.

**Data flow**: It receives event parameters and a session ID. If a session ID is present, it marks that session as loaded in the Settle object; it does not need to inspect the event body.

**Call relations**: Browser event dispatch code calls this when the browser reports DOM content loaded. BrowserTabs.navigate later relies on the Settle object’s state when it waits for navigation to become ready.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as visually painted when Chrome reports an important paint-related lifecycle event for the main frame. In plain terms, it helps decide that the page has appeared on screen, not just started loading.

**Data flow**: It receives event parameters and a session ID. It ignores events without a session, ignores lifecycle names that are not paint-related, and only marks the session painted if the frame is the top-level frame.

**Call relations**: Browser event dispatch code calls this for lifecycle notifications. It uses BrowserTabs.is_top_level_frame, then updates browser.settle so BrowserTabs.navigate can wait for a usable page state.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main frame of a known tab. This matters because pages can contain child frames, and the code usually wants navigation state for the whole tab.

**Data flow**: It receives a session ID and a frame ID. It scans the current tab list and returns true only when a tab has the same session ID and its target ID matches the frame ID.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating load or paint state. It acts like a gatekeeper that keeps subframe activity from confusing tab-level readiness.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the system to an existing browser page target so it can be controlled as a Tab. It also prepares that tab to report useful events and display at the configured viewport size.

**Data flow**: It receives a target ID. It asks the browser to attach to that target, reads the returned session ID, initializes the session, enables document fetch interception, applies viewport dimensions, and returns a new Tab object.

**Call relations**: BrowserTabs.sync calls this for newly created targets, BrowserTabs.page calls it when it creates the first blank tab, and BrowserTabs.create calls it for a newly opened tab. It hands session setup to BrowserTabs.init_session and finishes by constructing Tab.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for a newly attached tab. These include page events, lifecycle events, document access, and network events.

**Data flow**: It receives a session ID. It sends several setup commands to Chrome for that session and does not return a value.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target. This setup must happen before the rest of the tab code can reliably hear load events, inspect the page, or watch network activity.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list into agreement with browser events that have arrived. It removes tabs that Chrome destroyed and attaches to newly created page targets.

**Data flow**: It reads browser.tab_events.destroyed_targets and created_targets. Destroyed IDs are cleared after removing matching tabs; created IDs are processed in order, skipped if already open, attached if possible, and ignored if attachment fails with a CDP error.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before using the tab list. It calls BrowserTabs.attach_tab for new targets, making event callbacks and the live tab list work together.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the Tab object that should be used for an operation. If no tabs are open, it creates and attaches a blank tab so callers always have a page to work with.

**Data flow**: It receives an optional tab ID. It first syncs the tab list, creates an about:blank target if the list is empty, then returns the newest tab when no ID is given or the requested tab when the ID is valid; invalid IDs raise a ValidationError.

**Call relations**: BrowserTabs.navigate and BrowserTabs.close call this to find their target tab. It calls BrowserTabs.sync and may call BrowserTabs.attach_tab if it has to create the first tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new place: a normal URL, the previous history entry, or the next history entry. It waits afterward until the page has settled enough to be useful.

**Data flow**: It receives a destination string and an optional tab ID. It gets the tab, normalizes the destination, resets readiness tracking, performs either a history step or a direct navigation, waits for settling, then returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create calls this after opening a new tab with the requested URL. Internally it calls normalize_url, BrowserTabs.page, BrowserTabs._history_step or BrowserTabs._goto, and finally BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work of navigating a tab to a specific URL. It also distinguishes a true navigation failure from a navigation that became a file download.

**Data flow**: It receives a Tab and a URL. It notes the current download count, starts waiting for the DOM content loaded event, sends Page.navigate, checks for error text, cancels the wait if needed, accepts download starts as a special success case, and otherwise waits for the load event when Chrome reports a loader ID.

**Call relations**: BrowserTabs.navigate calls this for ordinary destinations. It uses the browser connection directly and consults the download reader when Chrome reports an error that might really mean “download started.”

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browsing history. It safely does nothing when there is no entry in that direction.

**Data flow**: It receives a Tab and a step number, usually -1 for back or 1 for forward. It asks Chrome for navigation history, finds the current index, computes the target index, and if valid tells Chrome to navigate to that history entry, then waits for a navigation event.

**Call relations**: BrowserTabs.navigate calls this when the normalized destination is “back” or “forward”. It uses browser history data from Chrome and waits for either a full frame navigation or an in-document navigation.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Returns the simple public summary of a tab: its current URL and document title. This gives callers a friendly view instead of raw browser session details.

**Data flow**: It receives a Tab. It runs JavaScript in that tab to read location.href and document.title, validates that the result is map-like, and returns a dictionary with string URL and title values.

**Call relations**: BrowserTabs.navigate uses this after navigation, BrowserTabs.tabs_context uses it for every open tab, and BrowserTabs.tab_titles uses it to collect just titles. It depends on BrowserTabSession.eval_js for the page query.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to show or reason about tab state. It identifies the current tab as the last tab in the local list.

**Data flow**: It first syncs the local tab list. Then it loops over each tab, asks for its URL and title, marks whether it is the active one, and returns a dictionary containing current_tab and a list of tab summaries.

**Call relations**: BrowserTabs.close calls this after removing a tab so the caller receives the updated tab state. It calls BrowserTabs.sync and BrowserTabs.tab_info as part of building the snapshot.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Collects the titles of all currently tracked tabs. This is a compact view for callers that only care about page names.

**Data flow**: It reads the browser’s tab list, asks each tab for its info, extracts the title, substitutes an empty string when missing, and returns the list of title strings.

**Call relations**: This function calls BrowserTabs.tab_info for each tab. Nothing in this file calls it, but other parts of the system can use it when they need a lightweight tab overview.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new tab and navigates it to the requested URL. It returns both the new tab’s ID and its resulting page information.

**Data flow**: It receives an optional URL, defaulting to about:blank. It asks Chrome to create a blank target, attaches to it, appends it to the tab list, navigates that specific tab to the requested URL, and returns the tab index plus URL and title.

**Call relations**: This is a high-level tab action. It calls BrowserTabs.attach_tab to gain control of the new target and BrowserTabs.navigate to load the desired destination.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a tab selected by input arguments and returns the updated tab list. It also clears out-of-process iframe session tracking because those auxiliary sessions may no longer be valid.

**Data flow**: It receives an argument dictionary, converts tab_id into an integer or None, finds the tab, tells Chrome to close the target, removes that Tab object from the local list, clears oop_sessions, and returns the current tabs context.

**Call relations**: This high-level action calls _tab_id to interpret the input, BrowserTabs.page to find the tab, sends the close command through the browser connection, and then calls BrowserTabs.tabs_context to report the new state.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loosely typed tab_id value into either an integer or no selection. This lets callers pass IDs as numbers or numeric strings.

**Data flow**: It receives a JSON-like value. Integers are returned as-is, floats are converted with int(), non-empty strings are parsed with int(), and anything else becomes None.

**Call relations**: BrowserTabs.close calls this before asking BrowserTabs.page for the tab to close. It keeps input interpretation separate from the actual close logic.

*Call graph*: called by 1 (close).


### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `during browser action handling, after an action or navigation is triggered`

After automation clicks a button, submits a form, or navigates to a page, the browser may keep doing work: loading a document, fetching data, painting visible content, or sending background tracking requests. This file provides the waiting rule for that moment. Without it, the automation would either move too early, before the page is usable, or wait too long on pages that never become completely quiet.

The main idea is consequence-scoped settling: only wait for work that appears to be caused by the current action. The `Settle` object keeps small sets of facts: which important network requests are still pending, which browser sessions are loading, and which sessions have painted visible content. “Paint” means Chrome has drawn meaningful page content on screen.

It also filters out traffic that usually should not delay the user, such as images, fonts, low-priority prefetches, and common analytics hosts. This is like waiting for a restaurant order to arrive, but not waiting for the staff to finish restocking napkins.

When asked to wait, it first lets the page’s own immediate JavaScript tasks run. Then it watches for either a paint event, loading to finish, or important requests to drain. If the page paints, it gives requests a short grace period, so visible pages do not get held hostage by endless background activity.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It ignores likely background or decorative traffic, such as images, fonts, low-priority requests, and analytics beacons.

**Data flow**: It receives a dictionary of Chrome request details. It checks the request type, priority, and host name from the URL. It returns `true` when the request looks like foreground page work, and `false` when it looks safe to ignore.

**Call relations**: When `Settle.on_request_started` sees a new browser request, it asks `tracks_request` whether that request should be counted as pending work. This keeps the later waiting step focused on useful page activity rather than every network blip.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker. It starts with no pending requests, no recorded starts, no loading sessions, and no painted sessions.

**Data flow**: It takes no outside data beyond the new object being created. It prepares empty sets and counters that will later be filled by browser events. The result is a `Settle` instance ready to observe one stream of browser activity.

**Call relations**: The browser session creates a `Settle` object during `BrowserSession.__init__`, and also creates one while closing according to the call graph. This gives the session a clean place to record page activity before waiting for it to calm down.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the tracker’s action-specific memory so it can be reused for a new action. It forgets pending requests, the count of started requests, and paint events.

**Data flow**: It reads and changes the object’s stored sets and counter. Before the call, old action data may be present; after the call, those action-related records are empty again. It does not return a value.

**Call relations**: No direct caller is shown in the provided call graph. Its role is to give surrounding browser-session code a way to start a new settling window without replacing the whole object.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records a newly started browser request if it is worth waiting for. This is how the tracker learns that the current page action has created work still in progress.

**Data flow**: It receives Chrome request details and a browser session id. It pulls out the request id, asks `tracks_request` whether the request matters, and if so stores the pair of session id and request id in `pending`. It also increments the count of started tracked requests.

**Call relations**: This function relies on `tracks_request` to avoid counting passive or analytics traffic. Later, `Settle.wait` looks at the `pending` set that this function fills to decide whether it should keep waiting.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a browser request as no longer pending. It is the cleanup partner to `Settle.on_request_started`.

**Data flow**: It receives Chrome request details and a browser session id. It extracts the request id and removes that session/request pair from the pending set if present. It returns nothing; the important result is that the tracker’s stored state becomes quieter.

**Call relations**: This function feeds the same state that `Settle.wait` watches. As requests finish, removing them from `pending` allows the waiting loop to stop once the page has been quiet long enough.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has begun loading a document or page. This tells the waiting logic not to continue yet.

**Data flow**: It receives a session id and adds it to the `loading` set. Before the call, the session may not be considered loading; after the call, `Settle.wait` will treat it as active work. It returns nothing.

**Call relations**: No direct caller is shown in the provided call graph, but this method is part of the event-recording side of the settling tracker. `Settle.wait` later checks the `loading` set when deciding whether the page is still busy.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has finished loading. This removes one reason for the waiting logic to keep pausing.

**Data flow**: It receives a session id and removes it from the `loading` set if present. Before the call, the session may be marked as loading; after the call, that loading flag is gone. It returns nothing.

**Call relations**: No direct caller is shown in the provided call graph. It works together with `Settle.mark_loading`, and `Settle.wait` observes the resulting `loading` set to know when document loading is no longer blocking progress.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted visible page content. A paint is treated as a strong sign that the page is usable, even if some network activity continues.

**Data flow**: It receives a session id and adds it to the `painted` set. Before the call, the session may have no paint recorded; after the call, `Settle.wait` can switch to its shorter post-paint waiting rule. It returns nothing.

**Call relations**: No direct caller is shown in the provided call graph. Its stored signal is central to `Settle.wait`, which checks `painted` and then hands off to `_drain_after_paint` for a short grace period.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the browser work caused by the current action appears complete, or until a safety time limit is reached. This is the main function other code uses when it needs to pause after an action without waiting forever.

**Data flow**: It receives a Chrome DevTools Protocol connection object, a session id, and a maximum number of seconds to wait. It first flushes the page’s immediate JavaScript tasks, then repeatedly checks whether the session has painted, is still loading, or has important pending requests. It returns nothing; its output is time passing until the page is judged ready enough or the deadline is reached.

**Call relations**: This is the central consumer of all the state recorded by the other `Settle` methods. It calls `_flush_page_tasks` at the start so early browser events are counted, and when it sees a paint signal it calls `_drain_after_paint` to give remaining foreground requests a brief chance to finish.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After visible content appears, waits a short extra time for important requests to finish. This avoids calling a page ready the instant an empty shell appears, while still avoiding long waits on pages that keep background work alive forever.

**Data flow**: It receives a session id and an absolute deadline time. It creates a shorter grace deadline, then watches the `loading` and `pending` state until they stay quiet briefly or the grace time runs out. It returns nothing; the result is a small, capped delay after first paint.

**Call relations**: `Settle.wait` calls this when the session has painted. It does not hand off to other project functions; it simply sleeps in short intervals while watching the state that request and loading event methods update.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the web page one quick turn to run immediate JavaScript follow-up work before the settling check begins. This helps catch requests started by click handlers or zero-delay timers.

**Data flow**: It receives a Chrome DevTools Protocol connection and a session id. It asks the browser to evaluate a tiny JavaScript promise that resolves after `setTimeout(..., 0)`, which means the page gets a chance to process already-scheduled tasks. If that browser call fails or times out, it simply sleeps briefly instead. It returns nothing.

**Call relations**: `Settle.wait` calls this first, before checking pending requests and loading state. It uses `Cdp.send` to talk to Chrome through the DevTools Protocol, and its purpose is to make later settling decisions based on events that have had a fair chance to arrive.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Browser Side Effects
Downloads and JavaScript dialogs are monitored and handled so browser automation does not stall or miss generated files.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling`

A browser can receive files in a few different ways. Some are normal downloads. Others, especially PDFs, may open inside Chrome’s viewer instead of becoming a file the automation system can work with. This file sits beside the browser connection and watches Chrome’s download and network events so the rest of the system can treat downloads in a predictable way.

The main class, `BrowserDownloads`, reacts to three kinds of events. When Chrome pauses a network response, `on_fetch_paused` must always release it; otherwise the page would freeze like traffic stopped at a red light forever. Most responses are simply allowed to continue. But if the response is a top-level document and its content type is PDF, the code rewrites the response header so Chrome treats it as an attachment download instead of opening the PDF viewer.

When Chrome announces that a download has started, this file records a small `Download` object with its browser-provided id, filename, and state. Later progress events update that state. Callers can ask whether a recent navigation became a download, or wait until any download finishes. This file does not read the downloaded bytes; it only identifies that Chrome completed a download and reports which one.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the browser connection’s ability to send a Chrome DevTools Protocol command. Chrome DevTools Protocol is Chrome’s remote-control interface: commands like “continue this paused request” are sent through it.

**Data flow**: It receives a command name, optional command parameters, and optionally a browser session id. The real implementation sends that command to Chrome and returns Chrome’s JSON-like reply.

**Call relations**: This file depends on an object with this method when continuing paused requests and responses. `BrowserDownloads._continue_request` and `BrowserDownloads._continue_response` call it through `BrowserDownloadSession.connection()`.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This protocol method describes how a browser session exposes the connection used to talk to Chrome. It lets the download helper stay independent from the exact browser session class.

**Data flow**: It takes the session object and returns an object that can send Chrome DevTools Protocol commands.

**Call relations**: The continuation helpers in `BrowserDownloads` call this when they need to tell Chrome to release a paused network request or response.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This protocol method describes how the browser session starts a background asynchronous task. It is used so network events can be released without blocking the event handler.

**Data flow**: It receives a coroutine, which is a paused asynchronous job. The real session schedules that job to run in the background and does not return a download result itself.

**Call relations**: `BrowserDownloads.on_fetch_paused` uses this to start `_continue_request` or `_continue_response`. That matters because every paused browser request must be continued quickly, but the event callback itself is not written as an async function.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This protocol method tells whether a network event belongs to the main page frame rather than an embedded frame, such as an iframe. The distinction matters because embedded PDFs should usually keep rendering inside the page.

**Data flow**: It receives a browser session id and a frame id from Chrome’s event data. It returns true when that frame is the top-level page frame and false otherwise.

**Call relations**: `BrowserDownloads.on_fetch_paused` uses this before forcing a PDF response to download. This prevents the file from disrupting pages that merely embed a PDF as part of their content.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This reacts when Chrome pauses a network request or response for inspection. Its job is to always release the pause, while changing top-level PDF responses into downloads when needed.

**Data flow**: It receives Chrome’s event data and an optional session id. It reads the request id, response status, headers, frame id, and content type. If the event is before a response exists, it schedules a simple request continuation. If there is a response, it decides whether the response is a top-level PDF and schedules a response continuation, possibly with a forced attachment header.

**Call relations**: This is the front door for paused fetch events. It calls `_content_type` to understand the response headers, asks the browser session whether the frame is top-level, and then hands the actual Chrome command work to `_continue_request` or `_continue_response` through `spawn_background`.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This tells Chrome to let a paused request keep going when there is no response to rewrite. Without this, the browser page could hang waiting for permission to continue.

**Data flow**: It receives the browser session id and Chrome’s request id. It sends a `Fetch.continueRequest` command to Chrome. It does not return useful data; if Chrome rejects the command or the connection fails, it logs a warning.

**Call relations**: `on_fetch_paused` schedules this for request-stage pauses. It uses the session’s browser connection to send the command, then stops; the rest of the page loading continues inside Chrome.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This tells Chrome to release a paused response, optionally changing headers so Chrome downloads a file instead of displaying it inline. It is the small point where a PDF viewer navigation becomes a normal download.

**Data flow**: It receives a session id, request id, response code, response headers, and a true-or-false force flag. If forcing is off, it only sends the request id back to Chrome. If forcing is on, it removes any existing `Content-Disposition` header and adds `Content-Disposition: attachment`, then sends the adjusted response data. It logs a warning if Chrome cannot be told to continue.

**Call relations**: `on_fetch_paused` schedules this for response-stage pauses. It is called after `on_fetch_paused` has already decided whether the response is a top-level PDF that should become a download.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that Chrome has started a new download. It keeps just enough information for later code to identify the file: Chrome’s download id, the suggested filename, and the current state.

**Data flow**: It receives Chrome’s download-start event data. It reads the `guid` and `suggestedFilename`, falls back to `download` if no filename is provided, creates a `Download` record marked `inProgress`, and appends it to the browser session’s download list.

**Call relations**: This is called when the browser reports a download beginning. Later, `on_download_progress`, `became_download`, and `wait` all rely on the record it adds to the shared downloads list.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This updates the saved state of a download when Chrome reports progress. Most importantly, it notices when a download becomes `completed`.

**Data flow**: It receives Chrome’s progress event data, reads the download `guid` and new `state`, searches the stored downloads for the same id, and changes that record’s state. It returns nothing.

**Call relations**: This follows after `on_download_begin` has created a record. `BrowserDownloads.wait` later looks at these updated states to decide when a download has finished.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This briefly checks whether an action, such as clicking a link or navigating, caused a new download to appear. It gives Chrome a short grace period because the download event may arrive slightly after the navigation starts.

**Data flow**: It receives the number of downloads that existed before the action. It repeatedly compares the current download list length with that old count until either a new record appears or the short grace period expires. It returns true if a new download appeared, otherwise false.

**Call relations**: Other browser-flow code can call this after a navigation-like action. It relies on `on_download_begin` adding new records to the shared download list while it waits.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This waits until a browser download has completed, then returns the completed download record. It reports which file Chrome finished but deliberately does not read the file contents.

**Data flow**: It receives an argument dictionary that may include a timeout. It turns that timeout into a number using `float_or_default`, then repeatedly checks the stored download list for records whose state is `completed`. If one or more are complete, it returns the most recent completed record. If time runs out first, it raises a timeout error.

**Call relations**: This is the caller-facing wait operation. It depends on `on_download_begin` to create download records and `on_download_progress` to mark them completed.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This converts a user-provided timeout-like value into a floating-point number, or uses a default when no value was supplied. It also rejects values that are not numeric.

**Data flow**: It receives a JSON-like value and a default number. If the value is an integer, float, or non-empty string, it converts it to a float. If the value is missing, it returns the default. If the value is some other kind of data, it raises a validation error.

**Call relations**: `BrowserDownloads.wait` calls this before starting its timer. That keeps timeout parsing in one small helper instead of mixing it into the download-wait loop.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This extracts the main content type from a list of HTTP response headers. For example, it turns `application/pdf; charset=utf-8` into `application/pdf`.

**Data flow**: It receives a list of JSON-like header values. It scans for a dictionary whose name is `content-type`, ignoring letter case. If found, it returns the cleaned, lower-case media type before any semicolon options. If not found, it returns an empty string.

**Call relations**: `BrowserDownloads.on_fetch_paused` calls this when deciding whether a paused top-level document response is a PDF that should be forced into a download.

*Call graph*: called by 1 (on_fetch_paused).


### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, and “are you sure you want to leave?” warnings. These dialogs are small to a human, but they are a big problem for automation: while one is open, the page can stop sending later browser events. It is like a doorway being blocked until someone answers the question.

This file defines the small contract it needs from the rest of the browser system: something that can send Chrome DevTools Protocol commands, and a browser session that can remember dialog messages and start background work. The Chrome DevTools Protocol, or CDP, is the control channel used to tell the browser what to do.

The main policy lives in `BrowserDialogs`. When a dialog appears, it decides what answer is safest. Alerts and “before unload” warnings are accepted because there is usually no meaningful choice. Confirmation boxes and prompts are dismissed, so the automation does not accidentally agree to a website’s “are you sure?” question. The file also records a plain text note, such as “confirm dismissed: ...”, so the rest of the agent can know the dialog happened. The actual browser reply is sent in the background, and failures are logged instead of crashing the run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser control object used by this file. It represents the ability to send one command to the browser over CDP, the browser control protocol.

**Data flow**: It receives a command name, optional command data, and optionally a browser session identifier. The concrete browser connection sends that command to the browser and returns the browser’s JSON-like reply.

**Call relations**: This file does not implement the method here; it describes what any compatible connection must provide. `BrowserDialogs._answer_dialog` relies on this method when it needs to tell the browser to accept or dismiss a JavaScript dialog.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the browser control connection from a session. `BrowserDialogs` uses it when it needs to send a dialog-answer command.

**Data flow**: It takes the current browser session object and returns an object that can send CDP commands. Nothing is changed by the protocol definition itself; the real session class supplies the actual connection.

**Call relations**: This is part of the session contract used by `BrowserDialogs`. When `_answer_dialog` is ready to reply to the browser, it asks the session for its connection and then sends the command through that connection.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way to start a small asynchronous job without waiting for it to finish immediately. Here, it lets dialog handling happen right away without blocking the event callback.

**Data flow**: It receives a coroutine, which is a paused asynchronous task. The real browser session schedules that task to run in the background; this protocol method does not itself say how scheduling is done.

**Call relations**: This is called by `BrowserDialogs.on_dialog`. After `on_dialog` records the dialog and chooses whether to accept it, it hands `_answer_dialog` to `spawn_background` so the browser reply can be sent promptly.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function is called when the browser reports that a JavaScript dialog has appeared. It chooses whether to accept or dismiss the dialog, records what happened, and starts the browser reply.

**Data flow**: It reads the dialog type and message from the incoming browser event data. It accepts alerts and before-unload warnings, dismisses other dialog types such as confirms and prompts, appends a human-readable note to the session’s `dialogs` list, and schedules `_answer_dialog` to send the actual answer to the browser.

**Call relations**: This is the front door for dialog events in this file. It pulls values from the event data, calls `_answer_dialog` to build the asynchronous reply task, and passes that task to the browser session’s background runner so the page is unblocked quickly.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This function sends the actual command that closes the JavaScript dialog in the browser. It is separated from `on_dialog` because the command is asynchronous and may fail.

**Data flow**: It receives the browser session identifier and the chosen answer, either accept or dismiss. It asks the session for its CDP connection, sends the `Page.handleJavaScriptDialog` command with the chosen answer, and returns nothing. If the browser command fails, times out, or cannot run, it logs a warning instead of raising the error further.

**Call relations**: `BrowserDialogs.on_dialog` schedules this function in the background after deciding what to do. `_answer_dialog` then hands the final instruction to the browser connection through `BrowserDialogCdp.send`, completing the response to the dialog.

*Call graph*: called by 1 (on_dialog).


### DevTools Transport and Wire Safety
Chrome DevTools Protocol messaging, page-side JavaScript execution, and JSON boundary checks provide the low-level control channel.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser connection setup, command sending, event handling, and connection teardown`

Chrome can be controlled through the Chrome DevTools Protocol, often called CDP. In plain terms, CDP is a remote-control line into the browser: send a named command, get a matching answer, and also receive surprise notifications when something happens, such as a page loading. This file provides that control line.

The main class, CdpConnection, wraps a WebSocket, which is a long-lived two-way network connection. It gives every outgoing command a unique number, stores a waiting future for the reply, and sends the command as JSON. A background reader task listens for all incoming messages. If a message has a matching number, it is treated as the answer to a command. If it has an event name instead, it is either used to finish a one-time wait or sent to any registered listeners.

This is like a hotel front desk with numbered tickets: requests go out with ticket numbers, replies come back with those numbers, while public announcements are broadcast to whoever signed up to hear them. The file also protects callers from waiting forever. Commands and event waits have timeouts, and if the WebSocket closes, all pending waits are failed loudly instead of silently hanging.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: This builds a clear error object for a failed Chrome DevTools Protocol command. It keeps the command name and Chrome's error code so the caller can tell what failed and why.

**Data flow**: It receives the CDP method name, a numeric error code, and Chrome's error message. It stores the method and code on the error object, then creates a human-readable exception message. The result is an exception that can be attached to a waiting command reply.

**Call relations**: When an incoming command response contains an error, CdpConnection._dispatch creates this error and gives it to the command's waiting future. That makes the original caller of CdpConnection.send receive a normal Python exception instead of a successful result.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: This finds the actual WebSocket address used to talk to Chrome. It accepts either a WebSocket URL directly or a regular browser debugging HTTP address that needs to be queried first.

**Data flow**: It takes a URL and request headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise, it asks the browser's /json/version endpoint for connection details, checks that the HTTP request succeeded, reads the webSocketDebuggerUrl field, and returns that as text.

**Call relations**: This is a setup helper used before opening a CDP connection. It relies on httpx to make the HTTP request and on as_str to make sure the returned WebSocket URL is really a string.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: This prepares a new connection object around an already-open WebSocket. It sets up the bookkeeping needed to match replies to commands and events to listeners.

**Data flow**: It receives a WebSocket connection. It stores it, starts the command id counter at zero, and creates empty collections for pending command replies, event listeners, one-time event waiters, and the background reader task reference. Nothing is sent yet.

**Call relations**: CdpConnection.open creates the WebSocket and then calls this constructor. After this setup, other methods such as send, expect, on, and close use the stored state to coordinate browser communication.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: This opens the live WebSocket connection to Chrome and starts the background reader that watches for all incoming messages. It is the normal way to create a usable CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to that URL with a large allowed message size, wraps the WebSocket in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the ready connection.

**Call relations**: BrowserSession._bootstrap calls this during browser startup. After open returns, the rest of the browser session can send CDP commands while _read_loop continuously receives replies and events in the background.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: This shuts down the CDP connection cleanly. It stops the background reader task and then closes the WebSocket.

**Data flow**: It checks whether a reader task exists. If so, it cancels that task, waits for the cancellation to finish while ignoring the expected cancellation exception, clears the task reference, and finally closes the WebSocket. The connection is no longer usable afterward.

**Call relations**: This is used during teardown when the browser control channel is no longer needed. It coordinates with _read_loop, whose cleanup code makes sure pending command and event waiters do not remain stuck.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This sends one CDP command to Chrome and waits for the matching reply. Callers use it when they want Chrome to do something and return a result, such as navigating a page or reading browser state.

**Data flow**: It receives a command method name, optional parameters, and optionally a session id for a specific target such as a tab. It assigns a new numeric id, stores a future waiting for the response, sends the JSON command over the WebSocket, and waits up to the command timeout. If Chrome replies successfully, it returns the result data. If Chrome reports an error or the timeout expires, the caller gets an exception.

**Call relations**: This method depends on _read_loop and _dispatch to receive the matching response and complete the stored future. It is the main outgoing-command path used by higher-level browser session code.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: This registers a callback for ongoing browser events of a specific name. It is useful when code wants to be notified every time a certain kind of CDP event arrives.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for the event. Later, each matching event's parameters and optional session id are passed into that listener.

**Call relations**: _dispatch calls these listeners when event messages arrive from _read_loop. Unlike expect, this is not a one-time wait; the listener remains registered and can be called repeatedly.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This creates a one-time promise to wait for one of several named browser events. It lets code say, in effect, 'the next time this event happens, wake me up.'

**Data flow**: It receives one or more event names and optionally a session id. It creates a future, stores it together with the event names and session filter, and returns the future immediately. The future will later receive the event parameters when a matching event arrives.

**Call relations**: Callers usually pass the returned future to CdpConnection.wait to apply a timeout. _dispatch completes the future when _read_loop receives a matching event.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: This waits for a future created by expect, with a timeout and cleanup. It prevents event waits from lingering forever if the expected browser event never arrives.

**Data flow**: It receives a future and a timeout value. It waits until the future has a result or the timeout expires. Whether it succeeds or fails, it removes that future from the internal waiter list so old waits do not build up.

**Call relations**: This is the companion to expect. expect registers the event wait, _dispatch may fulfill it, and wait is the method that callers use to actually pause until the event happens or the timeout ends.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: This is the background receiver for the WebSocket. It continuously reads raw messages from Chrome and passes each decoded JSON message to the dispatcher.

**Data flow**: It reads each incoming WebSocket message as raw text or bytes, parses it as JSON, and hands the resulting dictionary to _dispatch. If the WebSocket closes, it stops reading. Before exiting, it fails every pending command and event future so callers are told the connection closed instead of waiting forever.

**Call relations**: CdpConnection.open starts this method as a background task. It feeds all incoming traffic to _dispatch, and its final cleanup supports send, expect, and wait by making connection loss visible to them.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: This sorts each incoming CDP message into either a command reply or a browser event. It is the traffic controller that makes sure each response, waiter, and listener receives the right data.

**Data flow**: It receives a decoded JSON message. If the message has an integer id, it looks up the matching pending command. It then either gives that command its result or turns Chrome's error payload into a CdpError. If the message has an event method name instead, it extracts the event parameters and optional session id, completes any matching one-time waiters, removes those completed waiters, and calls all registered listeners for that event.

**Call relations**: _read_loop calls this for every incoming WebSocket message. It completes futures created by send and expect, and it invokes listeners registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `active whenever browser automation needs to evaluate JavaScript in a page`

Browser automation often needs to ask the open page questions, such as “what is this value?” or “run this function on that page object.” This file is the helper for doing that through CDP, the Chrome DevTools Protocol, which is the message-based control interface used to talk to Chromium-based browsers.

The file defines two small expected interfaces: one for something that can send CDP messages, and one for something that can provide that connection. The main class, BrowserRuntime, then uses that connection to send two kinds of browser commands. eval runs a JavaScript expression in a specific browser session and returns the plain value. call_on runs a JavaScript function against an existing object inside the page and returns the function's result as a dictionary-like value.

A key detail is error checking. Browser CDP responses can include exceptionDetails when the JavaScript inside the page threw an error. Without checking that field, the rest of the program might continue with missing or misleading data. BrowserRuntime.raise_on_exception acts like a smoke alarm: if the page reports an uncaught exception, it raises a Python RuntimeError immediately with the browser's error description.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This describes the shape of an object that can send a command to the browser control channel. It is a protocol, meaning it is a promise about what methods another object must provide rather than an implementation itself.

**Data flow**: A caller gives it a browser command name, optional command parameters, and optionally a session identifier for the browser target to talk to. An implementing object sends that message to the browser and returns the browser's JSON-like response as a dictionary.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on this promised method after they get a connection from BrowserRuntimeSession.connection. The actual sending is done elsewhere; this file only states the contract it expects.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This describes the shape of an object that can provide access to the browser's command connection. It lets BrowserRuntime stay independent from the concrete browser session class used elsewhere in the project.

**Data flow**: It takes no extra input beyond the session object itself. It returns an object that knows how to send CDP commands to the browser.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on call this first so they can send their Runtime.evaluate or Runtime.callFunctionOn messages. The real session object is supplied from outside this file.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the expression's value. Use it when the automation code needs to ask the page for a simple computed result.

**Data flow**: It receives a session id and a JavaScript expression string. It builds a Runtime.evaluate command, asks the browser connection to run it with returnByValue enabled, checks the response for a page-side exception, then pulls the returned value out of the response and gives it back to the caller.

**Call relations**: This method is called by higher-level browser automation code when it needs a value from the page. Inside, it asks BrowserRuntimeSession.connection for the sender, sends the CDP command through BrowserRuntimeCdp.send, calls BrowserRuntime.raise_on_exception to stop on JavaScript errors, and uses as_map to safely read the expected response shape.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function against an existing browser-side object and returns the function's result as a dictionary-like value. It is useful when the code already has a browser object id and wants to inspect or transform that object inside the page.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body, and optional argument values. It builds a Runtime.callFunctionOn command, converts each argument into the format the browser expects, sends the command, checks for a JavaScript exception, then extracts the returned value and ensures it is treated as a map before returning it.

**Call relations**: Higher-level automation calls this when plain expression evaluation is not enough and work must happen on a particular page object. Like eval, it sends through the connection supplied by BrowserRuntimeSession.connection, uses BrowserRuntimeCdp.send for the protocol request, calls BrowserRuntime.raise_on_exception for safety, and uses as_map to read nested response data.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser command response for a JavaScript exception and turns it into a Python RuntimeError. It prevents failed page code from being mistaken for a valid result.

**Data flow**: It receives the JSON-like response dictionary from the browser. If there is no exceptionDetails dictionary, it does nothing. If there is an exception, it chooses the clearest available text from the browser response and raises an error that includes that description.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser response. It is the shared guard step between the raw CDP reply and returning data to the rest of the project.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during browser protocol reads`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often called CDP. In plain terms, that means Chrome sends and receives nested JSON data: strings, numbers, lists, and dictionaries. This file gives that data a shared name, `Json`, so other code can clearly say, “this value came from the wire.”

More importantly, it provides four small checking functions. Each one takes a value from Chrome and confirms it has the shape the engine expected: an object, a non-empty string, an integer, or a list. If the value is missing in places where an empty object or list is acceptable, the helper returns an empty one. If the value is the wrong kind of thing, it raises `ValidationError`.

That error is important. It turns a messy low-level surprise, like “Chrome sent a number where we expected a dictionary,” into a clear, recoverable tool error. An everyday analogy is a package inspection desk: before a package enters the warehouse, someone checks that it is labeled and shaped correctly. If it is not, it is rejected at the door rather than causing trouble deep inside the building.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary of named fields. It is used when the browser engine expects a bundle of properties from Chrome.

**Data flow**: It receives a JSON value and a text path that says where that value came from. If the value is `None`, it treats that as an empty object and returns `{}`. If the value is already a dictionary, it returns it unchanged. If it is anything else, it raises `ValidationError` with a message pointing to the bad path.

**Call relations**: When other browser-engine code reads a CDP response and needs an object, it can call `as_map` before using the value. If the shape is wrong, `as_map` stops the flow by creating a `ValidationError`, so the caller gets a clear boundary error instead of failing later in a more confusing place.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a real, non-empty string. It is useful for required text fields such as identifiers, names, or protocol values that must not be blank.

**Data flow**: It receives a JSON value and a path describing where the value was found. If the value is a string and it is not empty, it returns that string. For `None`, an empty string, or any non-string value, it raises `ValidationError` explaining that the path must contain a non-empty string.

**Call relations**: Code that reads text fields from Chrome can call `as_str` before trusting the value. If Chrome or another layer provided the wrong shape, `as_str` creates a `ValidationError` immediately, keeping the rest of the engine from treating invalid data as usable text.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, which is a whole number. It is used when the protocol field must be numeric in that exact way.

**Data flow**: It receives a JSON value and a path naming the field being checked. If the value is an integer, it returns that integer unchanged. If the value is missing or is any other kind of JSON value, it raises `ValidationError` saying the path must be an integer.

**Call relations**: When browser-engine code expects a whole-number field from a CDP message, it can call `as_int` as the gatekeeper. If the value does not fit, `as_int` hands back a clear `ValidationError` instead of letting later calculations or lookups fail unpredictably.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It is used when the browser engine expects repeated items from Chrome, such as arrays of results or child entries.

**Data flow**: It receives a JSON value and a path that identifies the source field. If the value is a list, it returns that list unchanged. If the value is `None`, it treats that as an empty list and returns `[]`. If the value is anything else, it raises `ValidationError` with a clear message.

**Call relations**: Code that reads array-like fields from CDP responses can call `as_list` before looping over them. If the field is absent, the caller gets an empty list to work with; if the field is malformed, `as_list` creates a `ValidationError` so the problem is reported at the protocol boundary.

*Call graph*: 1 external calls (__init__).

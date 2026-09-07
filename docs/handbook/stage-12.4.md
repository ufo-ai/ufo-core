# BUA session, tab, and turn lifecycle  `stage-12.4`

This stage is the browser “workbench” used during each turn of the main work loop. A turn is one round of work where the system may inspect a page, click, type, navigate, or download something. The code here keeps the browser connection lazy: it opens Chrome only when a browser action is actually needed, then cleans up safely when the turn ends.

The backend file is the surface that tools use. It offers actions like reading a page, clicking, typing, uploading files, managing tabs, and fetching downloads. The session file holds the live connection to Chrome and stores the browser state for the turn, so those actions have one shared place to work through. The tabs file keeps the system’s tab list matched with the real browser, like keeping a map updated while rooms are opened, closed, or entered. The settle file waits after actions until the page is ready enough to continue, while ignoring noisy background activity such as ads. Together, these pieces make browser work reliable and orderly.

## Files in this stage

### Per-turn browser facade
Exposes the browser tools' per-turn surface while lazily opening and safely cleaning up the underlying browser connection.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser tool handling and turn cleanup`

BuaSurface is the bridge between high-level browser tool calls and a real browser session. Think of it like a temporary front desk for one conversation turn: if nobody asks to browse, it never opens the browser; if browsing is needed, it checks out a browser connection, uses it, and returns it when the turn ends.

The browser connection comes from a CDP provider. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome from code. BuaSurface asks the provider for a lease, which is like borrowing a browser for a while. It then creates a BrowserSession, which does the lower-level page work.

A key detail is recovery. If the process crashes during a browser tool call, cleanup may not run. This file stores a durable token tied to the conversation so a replayed turn can reconnect to the same live browser instead of starting over. Normal cleanup clears that token so later turns do not attach to an already released browser.

It also protects file movement. Uploads are restricted to workspace paths, checked for size, read from the sandbox when needed, and passed through the lease so local and remote browsers both receive usable paths. Downloads come back through the lease too, with filenames trimmed to a safe single path segment.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens and returns the BrowserSession used by the browser tools. It is lazy: it does nothing until the first browser action in a turn actually needs a browser.

**Data flow**: It starts with the surface's current session and lease. If a session already exists, it returns it. Otherwise it acquires a lease, asks the lease for the browser endpoint and download directory, creates a BrowserSession, opens it, saves it on the surface, and returns it.

**Call relations**: All user-facing browser actions call this first, so they share one session instead of reconnecting each time. When no lease exists yet, it hands off to BuaSurface._acquire_lease, then builds the BrowserSession that later receives navigation, reading, tab, upload, download, and interaction requests.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets the browser lease for this turn, either by reconnecting to a still-live previous session or by starting a fresh one. This is what makes crash recovery possible without unnecessarily reopening or renavigating the browser.

**Data flow**: It looks for a stored token for the conversation. If one exists, it asks the CDP provider to reattach to that browser. If the provider says the old session is gone, it clears the token. Then it leases a fresh browser, stores that new lease's token, and returns the lease.

**Call relations**: BuaSurface._open calls this when it needs a lease. It uses BuaSurface._stored_token to look for a recovery token and BuaSurface._store_token to save or clear that token depending on whether reattachment works.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Reads the saved browser reattach token for this conversation, if one is available. The token is the small piece of information needed to reconnect after a crash.

**Data flow**: It checks whether the surface has both a scoped store and a conversation id. If either is missing, it returns nothing. Otherwise it reads the token value from the store and returns it only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before leasing a browser. If it returns a token, lease acquisition tries recovery first; if it returns nothing, acquisition starts fresh.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Writes or clears the saved browser reattach token for this conversation. This keeps recovery possible after crashes but prevents later turns from reconnecting to sessions that were intentionally closed.

**Data flow**: It receives either a token string or None. If there is no scoped store or conversation id, it does nothing. Otherwise it writes that value under the conversation-specific token key.

**Call relations**: BuaSurface._acquire_lease uses it to save a new token or clear a bad one. BuaSurface.aclose uses it during cleanup to clear the token after the lease is released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates the browser to a requested URL, optionally in a chosen tab. It validates that the URL is actually a string before passing it to the browser.

**Data flow**: It receives tool arguments, opens the session, reads the url field, and rejects the call if the URL is not text. It converts the optional tab id into an integer when possible, then sends the URL and tab id to the BrowserSession and returns that session's response.

**Call relations**: This is one of the public browser tool methods. It relies on BuaSurface._open to ensure a session exists and on _tab_id to normalize the tab identifier before handing the work to the browser session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the browser's current tabs. A caller uses this to understand what tabs are open and which one is active.

**Data flow**: It receives the tool arguments, opens the session, asks the BrowserSession for tab context, and returns the resulting tab information.

**Call relations**: As a public tab tool, it first goes through BuaSurface._open so it uses the shared per-turn browser session, then delegates the actual browser inspection to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no usable URL is provided, it opens a blank page instead of failing.

**Data flow**: It receives arguments, opens the session, checks whether the optional url value is non-empty text, and chooses that URL or about:blank. It asks the BrowserSession to create the tab and returns the session's response.

**Call relations**: This method is called as a browser tab tool. It uses BuaSurface._open for the shared connection, then hands the tab creation request to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab according to the provided arguments. It keeps tab-closing behavior inside the shared browser session.

**Data flow**: It receives the close request arguments, opens the session, passes the arguments to the BrowserSession, and returns whatever the session reports after closing the tab.

**Call relations**: The tab-close tool calls this surface method. It depends on BuaSurface._open for connection setup and then delegates the browser-specific tab close operation to the session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a page's file input, safely moving the files to wherever the leased browser can read them. This matters because a browser may be running locally in the sandbox or remotely somewhere else.

**Data flow**: It receives arguments and requires a files list. For each file path, it verifies that the value is non-empty text, resolves it as a workspace path, and asks the lease to place the file for the browser, using BuaSurface._read when bytes must be shipped. It replaces the original file paths with the placed paths, asks the BrowserSession to attach them, waits for remote uploads to settle, and returns the browser's reply.

**Call relations**: This public upload tool opens the session with BuaSurface._open, uses BuaSurface._lease to reach the active transport lease, and calls BuaSurface._settle_upload after BrowserSession.upload_file so the page does not submit an empty not-yet-arrived file.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until uploaded files are truly present in the page input, not just named there. This protects against a remote-browser race where the attach call returns before file bytes have finished arriving.

**Data flow**: It checks the sizes of files this surface actually shipped. If there were none, it returns immediately. Otherwise it repeatedly asks the BrowserSession what file sizes the page sees, compares them with the expected shipped sizes, sleeps briefly between attempts, and reattaches the files while waiting. If the sizes never match, it raises an error explaining what the page saw.

**Call relations**: BuaSurface.upload_file calls this after the first attach. It uses BrowserSession.attached_sizes to verify the page's view and BrowserSession.upload_file to retry attachment until the browser sees the expected bytes.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the current browser lease and fails clearly if there is none. It is a small guard that prevents upload or download code from pretending a transport exists when it has not been opened.

**Data flow**: It reads the surface's lease field. If the lease is present, it returns it. If not, it raises a runtime error saying the browser has no CDP lease.

**Call relations**: BuaSurface.upload_file uses this before placing files through the transport. BuaSurface.wait_for_download uses it before fetching downloaded bytes from the transport.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file's bytes from the sandbox so they can be sent to a remote browser. It enforces a size limit first, so a too-large upload fails before filling server memory.

**Data flow**: It receives a sandbox path. It requires a sandbox, safely quotes the path for shell use, runs stat in the sandbox to get the file size, rejects unreadable or oversized files, then runs base64 in the sandbox to read the bytes as text. It decodes the base64 data in a worker thread, records how many bytes were shipped, and returns the raw bytes.

**Call relations**: This function is supplied as a callback to the lease during BuaSurface.upload_file, so the transport can ask for bytes only when it needs to ship a file. It uses shlex.quote to keep the shell command path safe and asyncio.to_thread so decoding a whole file does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads the current page in the browser session, returning the structured page information produced by the lower-level browser code.

**Data flow**: It receives read arguments, opens the session, passes those arguments to BrowserSession.read_page, and returns the result.

**Call relations**: This public browser reading method uses BuaSurface._open for setup, then lets BrowserSession do the detailed page inspection.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets the text content of the current page. This is useful when a caller needs readable page text rather than a fuller structured page view.

**Data flow**: It receives arguments, opens the session, forwards the arguments to BrowserSession.get_page_text, and returns the text-focused result.

**Call relations**: Browser text-reading calls enter here. The method shares the same connection-opening path through BuaSurface._open and then delegates the page-specific work to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, using an optional host-side find completer to help interpret or complete the search. This supports browser tools that need to locate visible page elements.

**Data flow**: It receives find arguments, opens the session, and passes both the arguments and the surface's find_completer into BrowserSession.find. It returns the search result from the session.

**Call relations**: The find tool calls this method. It uses BuaSurface._open to reach the shared browser session, then hands off to BrowserSession while supplying the extra completer object held by the surface.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on the page. It is the surface method for browser tool actions that type into web forms.

**Data flow**: It receives form input arguments, opens the session, forwards those arguments to BrowserSession.form_input, and returns the session's result.

**Call relations**: Form-entry browser tools call this. It performs the common connection step through BuaSurface._open and lets BrowserSession do the actual page interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as interactions that are closer to screen or input control than simple page reading. The exact browser action is carried in the arguments.

**Data flow**: It receives action arguments, opens the session, forwards the arguments to BrowserSession.computer, and returns the result from that session.

**Call relations**: This is another public tool method. Like the other browser actions, it starts with BuaSurface._open so the action runs on the per-turn session rather than creating its own connection.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for the browser to finish a download, fetches the downloaded bytes through the lease, and returns them as base64 text. It also makes the filename safe to join into a workspace path.

**Data flow**: It receives download-wait arguments, opens the session, and asks BrowserSession to wait for a completed download. It then uses the active lease to fetch the bytes by download id, base64-encodes those bytes in a worker thread, trims the browser-provided filename down to a safe single file name, and returns the name, encoded content, and byte size.

**Call relations**: Download tools call this when they need the file contents back. It uses BuaSurface._open for the browser session, BuaSurface._lease for transport access, contained_leaf to prevent unsafe path names, and asyncio.to_thread so encoding does not block the event loop.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved recovery token at normal turn end. This prevents paid or remote browser sessions from being left behind and prevents later turns from reconnecting to a released session.

**Data flow**: It checks whether a BrowserSession exists and closes it, then clears the session reference. Whether or not session closing succeeds, it closes the lease if present, clears the lease reference, and stores None as the conversation token.

**Call relations**: Turn cleanup calls this after browser work is done. It uses BuaSurface._store_token to erase the reattach token; if a hard crash prevents this method from running, the token intentionally remains so recovery can reconnect.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab id value from tool arguments into an integer tab id when possible. It treats booleans and missing or empty values as no tab selection.

**Data flow**: It receives a JSON-like value. Integers pass through, floats and non-empty strings are converted to integers, booleans and other values become None. The result is either an integer tab id or None.

**Call relations**: BuaSurface.navigate uses this helper before calling the browser session. It keeps navigation's tab handling simple and consistent even when the incoming arguments use different JSON value types.

*Call graph*: called by 1 (navigate).


### Browser session control
Maintains the live Chrome control session and implements the main browser actions used during a turn.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `browser turn setup, tool calls, and teardown`

A browser session is the central workbench for talking to Chrome. Chrome is controlled through CDP, the Chrome DevTools Protocol, which is a WebSocket-based remote-control API. This file opens that connection, teaches it which browser events to listen for, and then hands day-to-day work to smaller helper objects for tabs, page content, forms, downloads, dialogs, JavaScript, and mouse/keyboard-like computer actions.

The important idea is that the session owns the shared live state. It remembers open tabs, download records, dialog messages, out-of-process frame sessions, whether the browser is on macOS, and background tasks. Helper classes are created as needed, like borrowing the right tool from a toolbox, but they all operate on this same session state.

On startup, the session resolves the browser endpoint, connects to Chrome, enables download behavior, subscribes to tab, network, page, dialog, and download events, and creates a fresh blank tab. On shutdown, it closes tabs, closes the connection, cancels background tasks, and resets all remembered state so the next session starts cleanly. Without this file, the rest of the browser tools would not have a safe, shared connection to Chrome or a single place to coordinate browser events and actions.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the basic settings it will need later, such as the CDP endpoint, model screen size, and download folder. It does not connect to Chrome yet.

**Data flow**: It receives an optional browser endpoint, an optional model name, and a download directory. It turns the model name into a coordinate space when possible, initializes empty lists and maps for tabs, downloads, dialogs, and frame sessions, and creates fresh helper state for page settling and tab events. The result is a ready-but-not-open session.

**Call relations**: The backend creates this session when it is ready to open a browser surface. Later, open starts the real browser connection using the state prepared here.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the session if it is not already open. It is the safe public entry point for starting browser control.

**Data flow**: It checks whether a connection already exists. If so, nothing changes. If not, it runs the bootstrap process; if anything goes wrong, it closes and resets the session before passing the error upward.

**Call relations**: The async context manager calls this when entering a session. It delegates the detailed setup to _bootstrap and uses close as cleanup if startup fails.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Performs the actual startup handshake with Chrome and wires browser events to the right session helpers. This is where the session becomes a live controller for the browser.

**Data flow**: It reads the CDP endpoint and headers, resolves them into a WebSocket URL, opens the CDP connection, asks Chrome for version information, and records whether the browser appears to be on macOS. It creates tab, download, and dialog helper objects, attaches their event callbacks to Chrome events, enables downloads into the configured folder, discovers existing targets, turns on target discovery, and creates a new blank tab. The session ends with an open connection and one attached tab.

**Call relations**: open calls this during startup. It builds readers through tab_reader, download_reader, and dialog_reader, then uses the CDP connection directly to subscribe to Chrome events and create the first tab.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the live session and returns it to a clean empty state. It is designed to run both during normal teardown and after a failed startup.

**Data flow**: It tries to close every known Chrome tab, ignores common closing errors, then closes the CDP connection. Afterward it clears the connection, tabs, frame sessions, downloads, dialogs, scroll flag, and background tasks, and creates fresh settle and tab-event state. The session object remains usable, but no longer controls a browser.

**Call relations**: The async context manager calls this on exit, and open calls it if bootstrapping fails. It is the counterpart to _bootstrap.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets callers use BrowserSession with Python's async with pattern, so setup happens automatically at the start of a block.

**Data flow**: It receives the session object, calls open to connect to Chrome if needed, and returns the same session for use inside the block.

**Call relations**: This is a convenience wrapper around open. Code that uses async with gets reliable startup without calling open manually.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Completes the async with pattern by automatically closing the browser session when the block ends.

**Data flow**: It receives any exit information from the async context block and calls close. The browser connection and remembered session state are cleaned up.

**Call relations**: This pairs with __aenter__. It delegates all teardown work to close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection, or raises a clear error if the browser has not been opened.

**Data flow**: It checks the stored connection field. If there is no connection, it raises BrowserUnavailable; otherwise it returns the connection object for other code to send browser commands.

**Call relations**: Helper objects can call this when they need to talk to Chrome. It protects them from accidentally using a session that was never opened or has already been closed.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts a background asynchronous task and remembers it so the session can cancel it later. This is for browser work that must continue while another action is running.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session's background task set, and arranges for the task to remove itself from the set when done. Nothing is returned.

**Call relations**: Other session helpers can use this to run follow-up work in the background. close later cancels any tasks still remembered here.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame is the main page frame rather than an embedded frame. A frame is a page area; embedded frames are like pages inside pages.

**Data flow**: It receives a CDP session id and frame id, creates a tab reader, and asks that reader to decide whether the frame is top-level. It returns true or false.

**Call relations**: This is a small pass-through to the tab helper. It keeps callers from needing to know which helper owns frame identity rules.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready for browser events and commands.

**Data flow**: It receives a CDP session id, creates a tab reader, and asks it to initialize that session. The result is whatever setup the tab helper performs inside Chrome; this method itself returns nothing.

**Call relations**: This belongs to tab setup flow. It delegates the detailed session configuration to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Gets the active or requested tab as a Tab object. This gives browser tools a concrete page to work on.

**Data flow**: It receives an optional tab id, creates a tab reader, and asks for the matching page. It returns the chosen Tab.

**Call relations**: Higher-level actions call this when they need a tab before reading, clicking, or navigating. The actual tab lookup rules live in BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new web address.

**Data flow**: It receives a URL and an optional tab id, creates a tab reader, and asks it to navigate the chosen tab. It returns a JSON-style dictionary describing the result.

**Call relations**: This is the session-level browser action for navigation. It hands the real navigation work to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a plain data summary for a specific tab. This lets other parts of the system report tab details without poking through the Tab object directly.

**Data flow**: It receives a Tab, creates a tab reader, and asks for that tab's information. It returns a JSON-style dictionary.

**Call relations**: It is a thin bridge from session callers to BrowserTabs, which knows how to describe tabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns information about the current set of browser tabs. This is useful when a tool needs to show or reason about the tab landscape.

**Data flow**: It creates a tab reader and asks it for tab context. The returned JSON-style dictionary contains the tab-related view prepared by the tab helper.

**Call relations**: This is part of the session's tab tool surface. BrowserTabs does the actual collection and formatting.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of the known browser tabs. Titles are the human-readable names usually shown on browser tabs.

**Data flow**: It creates a tab reader, asks it for titles, and returns a list of strings.

**Call relations**: Callers use this for lightweight tab awareness. BrowserTabs supplies the real title lookup.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper connected to this session. That helper knows how to create, close, attach, navigate, and describe tabs.

**Data flow**: It takes the current session and the fixed viewport size, constructs a BrowserTabs object, and returns it. It does not itself change browser state.

**Call relations**: Many session methods call this whenever they need tab behavior. _bootstrap also uses it to attach event listeners and create the initial tab.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page-structure operations, such as resolving element references into frames and coordinates.

**Data flow**: It takes the current session, viewport size, and maximum frame depth, constructs a BrowserPage object, and returns it.

**Call relations**: resolve_ref and ref_point call this when they need page geometry or reference resolution. The helper contains the detailed page logic.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading and searching page content. This covers text extraction, page summaries, and accessibility-like trees.

**Data flow**: It wraps the current session in a BrowserContent object and returns that helper. No browser command is sent by this factory method itself.

**Call relations**: tree, read_page, get_page_text, and find use this helper so content-reading behavior stays separate from session wiring.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for tracking and waiting on downloads.

**Data flow**: It combines the current session with the maximum wait time and returns a BrowserDownloads object.

**Call relations**: _bootstrap uses this helper's event callbacks when subscribing to Chrome download events. wait_for_download uses it later to wait for a completed download record.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for browser JavaScript dialogs, such as alert boxes.

**Data flow**: It wraps the current session in a BrowserDialogs object and returns it.

**Call relations**: _bootstrap uses this helper when wiring Chrome's dialog-opening event. The helper records or responds to dialogs through session state.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form-related work such as typing into inputs and uploading files.

**Data flow**: It wraps the current session in a BrowserForms object and returns it.

**Call relations**: upload_file, attached_sizes, and form_input call this helper so form-specific browser commands live outside the central session.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript in the browser. The runtime is Chrome's execution environment for page scripts.

**Data flow**: It wraps the current session in a BrowserRuntime object and returns it.

**Call relations**: eval_js and call_on use this helper to run JavaScript expressions or call functions on browser-side objects.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, using about:blank by default if no URL is supplied.

**Data flow**: It receives a URL, creates a tab reader, and asks it to create the tab. It returns a JSON-style dictionary describing the result.

**Call relations**: This is the public session method for tab creation. It delegates the Chrome target details to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab based on the caller's arguments.

**Data flow**: It receives a JSON-style argument dictionary, creates a tab reader, and asks it to close the requested tab. It returns a JSON-style result.

**Call relations**: This is the public session method for tab closing. BrowserTabs interprets the arguments and talks to Chrome.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads or attaches a file to a file input on the current page.

**Data flow**: It receives a JSON-style argument dictionary describing the upload request, creates a form reader, and asks it to perform the upload. It returns a JSON-style result from the form helper.

**Call relations**: The backend calls this during upload settling. This method routes the request into BrowserForms, which knows the browser commands for file inputs.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached through an upload flow. This helps confirm what was attached without reading the file bytes here.

**Data flow**: It receives a JSON-style argument dictionary, creates a form reader, and asks it for attached file sizes. It returns a list of integers.

**Call relations**: The backend calls this while settling uploads. BrowserForms performs the actual lookup.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree view of page content, optionally filtered by type. This gives tools a structured way to inspect what is on the page.

**Data flow**: It receives arguments and a filter type, creates a content reader, and asks it to build the tree. It returns the tree as a string.

**Call relations**: This is part of the read-only page inspection surface. BrowserContent owns the details of collecting and formatting the page tree.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result for a browser tool. It is a higher-level page inspection action than just getting raw text.

**Data flow**: It receives a JSON-style argument dictionary, creates a content reader, and asks it to read the page. It returns a JSON-style dictionary.

**Call relations**: Callers use this through the session tool surface. BrowserContent performs the detailed page reading.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts the visible or relevant text from a page. This is useful when a tool needs the words on the page rather than layout details.

**Data flow**: It receives a JSON-style argument dictionary, creates a content reader, and asks it for page text. It returns a JSON-style dictionary with the result.

**Call relations**: This delegates text extraction to BrowserContent while keeping the session as the simple public entry point.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within page content, optionally using a completion helper to finish or refine the search. This supports find-in-page style behavior.

**Data flow**: It receives search arguments and an optional FindCompleter, creates a content reader, and asks it to find matching content. It returns a JSON-style result.

**Call relations**: The session exposes the search action, while BrowserContent performs the actual content search and uses the completer when provided.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or otherwise enters data into a form field on the page.

**Data flow**: It receives a JSON-style argument dictionary describing the input action, creates a form reader, and asks it to perform the input. It returns a JSON-style result.

**Call relations**: This is the session-level form input tool. BrowserForms handles the page-specific and browser-command details.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a computer-style browser action, such as mouse or keyboard interaction, against the page viewport. This gives the system a way to act more like a human using the browser.

**Data flow**: It receives a JSON-style action dictionary, creates a BrowserComputer with the session, viewport size, and maximum wait time, then runs the action. It returns a JSON-style result.

**Call relations**: Unlike the reader factory methods, this creates BrowserComputer directly for one action. BrowserComputer performs the low-level interaction and waiting.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to complete and returns its download record. The file bytes are not read here; the owner of the download directory is responsible for fetching them.

**Data flow**: It receives a JSON-style argument dictionary, creates a download reader, and asks it to wait for the matching download. It returns a BrowserDownload object.

**Call relations**: This uses the download events that _bootstrap subscribed to earlier. BrowserDownloads watches the session's recorded download state and applies the wait rules.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Runs a JavaScript expression inside a specific browser session and returns the result.

**Data flow**: It receives a CDP session id and a JavaScript expression, creates a runtime reader, and asks it to evaluate the expression. It returns a JSON-compatible value.

**Call relations**: This is a direct bridge to BrowserRuntime for simple JavaScript evaluation. Other code can use it when page inspection or action needs script execution.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side object. This is useful after Chrome has returned an object reference instead of a plain value.

**Data flow**: It receives a session id, an object id, a function body or name, and optional arguments. It creates a runtime reader and asks it to call the function on that object. It returns a JSON-style dictionary with the result.

**Call relations**: This supports more advanced runtime operations than eval_js. BrowserRuntime knows how to package and send the underlying CDP call.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame and index it points to. A reference is a compact label used by tools to point back to an element or item on the page.

**Data flow**: It receives a Tab and a reference string, creates a page reader, and asks it to resolve the reference. It returns a FrameNode and an integer index.

**Call relations**: Tools that need to act on a previously reported page item use this before clicking, typing, or locating it. BrowserPage owns the reference format and lookup rules.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the screen point for a page reference, usually so an action can click or move to it.

**Data flow**: It receives a Tab and a reference string, creates a page reader, and asks it to compute the point. It returns an x and y coordinate pair.

**Call relations**: This builds on BrowserPage's knowledge of page geometry. Higher-level interaction code can use the returned coordinates for computer-style actions.

*Call graph*: calls 1 internal fn (page_reader).


### Settling and tab synchronization
Provides the lower-level waiting and tab-state coordination needed to drive pages reliably after browser actions.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigations`

After a browser action, the system needs to know when it is safe to look at the page again. That is harder than it sounds: modern pages may keep loading images, tracking beacons, or live-feed updates forever. If the code waited for absolutely all network activity to stop, many pages would never be considered ready.

This file solves that by watching only the important consequences of the current action. It keeps a small ledger of foreground network requests, page loading state, and whether the page has painted visible content. “Painted” means Chrome has drawn meaningful page content on screen, which is often a better readiness signal than total network silence.

The `Settle` object is updated by browser event listeners elsewhere: requests start and finish, frames begin or stop loading, and paint events arrive. When asked to wait, it first gives the page one quick chance to run queued browser tasks. Then it returns quickly if nothing happened, waits for loading and important requests if work is still in progress, or gives a short grace period after first paint so content can arrive without waiting forever.

A useful analogy is waiting for a restaurant order: this code waits until your ordered meal arrives, but it does not wait for every phone call, delivery truck, or background chore in the restaurant to finish.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It ignores things that are usually background noise, such as images, fonts, low-priority prefetches, and common analytics services.

**Data flow**: It receives a dictionary of browser event details. It looks at the request type, priority, and web address host. If the request looks passive or like analytics traffic, it returns `False`; otherwise it returns `True`, including when the event shape is unfamiliar so important work is not accidentally missed.

**Call relations**: When `Settle.on_request_started` hears that a request began, it asks `tracks_request` whether this request belongs in the settling ledger. `tracks_request` reads fields from the event dictionary and uses URL parsing to recognize analytics hosts.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker with empty state. It prepares the places where the object will remember active requests, loading sessions, paint events, and how many tracked requests have started.

**Data flow**: It takes no outside data beyond the new object being created. It initializes empty sets for pending requests, loading sessions, and painted sessions, and sets the request-start counter to zero. The result is a ready-to-use `Settle` instance.

**Call relations**: A `BrowserSession` creates this object when setting up browser control, and also creates a new one during close cleanup. Other browser event code then fills this tracker as page activity happens.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the action-specific settling state so the next wait starts from a clean slate. This prevents old requests or old paint events from affecting a later action.

**Data flow**: It reads the current stored pending requests, start count, and paint markers only to erase them. After it runs, pending requests and painted sessions are empty, and the started-request count is zero. The loading set is intentionally left alone, because page loading can be broader than a single action reset.

**Call relations**: This is the reset button for the tracker. Code outside this file can call it before watching a new action so that `wait` only judges work caused by that new action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records a newly started browser request if it is part of useful foreground page work. This lets later waiting know that the action has kicked off something that may still need time to finish.

**Data flow**: It receives browser event details and a browser session id. It extracts the request id, checks that both ids are valid, and asks `tracks_request` whether this request matters. If so, it adds the `(session id, request id)` pair to the pending set and increases the count of tracked requests that have started.

**Call relations**: Browser event listeners call this when Chrome reports a request has begun. It relies on `tracks_request` to filter out background noise, then stores the request so `Settle.wait` can later wait for it to finish.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Removes a browser request from the pending list once it has finished or otherwise stopped being active. This is how the tracker learns that one piece of page work is done.

**Data flow**: It receives browser event details and a browser session id. It extracts the request id and, if the ids are valid, removes that pair from the pending set. It does not return a value; it changes the tracker's internal state.

**Call relations**: Browser event listeners call this when Chrome reports that a request completed or failed. Its updates are later used by `Settle.wait` and `_drain_after_paint` to decide whether the page has quieted down.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as currently loading a document. This tells the settling logic that the page is still in the middle of navigation or document load work.

**Data flow**: It receives a session id and adds it to the loading set. The visible result is not a returned value, but a changed tracker state: that session is now considered loading.

**Call relations**: Browser lifecycle event code calls this when loading begins. `Settle.wait` and `_drain_after_paint` consult this loading set before deciding that the page is ready.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as no longer loading. This allows settling to complete once important requests have also drained.

**Data flow**: It receives a session id and removes it from the loading set if present. Afterward, that session is no longer treated as actively loading by this tracker.

**Call relations**: Browser lifecycle event code calls this when loading ends. The change feeds into `Settle.wait` and `_drain_after_paint`, which both require loading to be over before returning in quiet cases.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted visible page content. Paint is treated as a strong sign that the user can see something useful, even if the network may keep running.

**Data flow**: It receives a session id and adds it to the painted set. Later checks can see that this session has reached the visual-ready milestone.

**Call relations**: Browser lifecycle event code calls this when Chrome reports a paint event. `Settle.wait` uses this marker to switch from general waiting to the shorter post-paint drain handled by `_drain_after_paint`.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the page appears ready after the current action, but only up to a time limit. It balances being patient enough for real content and strict enough not to hang on pages that never become fully idle.

**Data flow**: It receives a Chrome DevTools Protocol connection, a session id, and a maximum wait time. First it asks `_flush_page_tasks` to let immediate browser-side work run. Then it watches the stored paint, loading, and pending-request state until one of several endings happens: the page already painted, nothing was started, requests and loading become quiet, or the deadline is reached. It returns no data; the important result is that time has passed until the page is likely safe to inspect.

**Call relations**: Higher-level browser action code calls this after doing something in the page. It delegates the initial browser task flush to `_flush_page_tasks`, and if a paint event is seen it hands off to `_drain_after_paint` for a short, capped grace period.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After the page has painted, waits briefly for important follow-up requests to finish. This avoids calling a page ready too early when the first visible shell appears before its real content, while still avoiding long waits on never-idle pages.

**Data flow**: It receives a session id and an absolute deadline time. It creates a shorter grace window, then repeatedly checks whether the session is still loading or has pending tracked requests. If things become quiet and stay quiet across a short gap, it returns; otherwise it stops when the grace time or original deadline is reached.

**Call relations**: `Settle.wait` calls this whenever paint has happened. It does not call browser APIs itself; it simply reads the state filled by request, loading, and paint event handlers.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the web page one quick turn to run work that was queued by the action, such as click handlers and zero-delay timers. This helps make sure requests triggered immediately by the action have been seen before settling decisions begin.

**Data flow**: It receives the Chrome DevTools Protocol connection and a session id. It sends a small JavaScript expression that resolves after a zero-delay timeout, asking the page to wait for that promise. If that succeeds, immediate queued page work has had a chance to run; if the browser command fails or times out, it falls back to a tiny sleep instead.

**Call relations**: `Settle.wait` calls this before checking whether anything is pending. It uses `Cdp.send` to talk to the browser, and catches DevTools or timeout failures so settling can continue rather than crash.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`orchestration` · `request handling and browser event handling`

This file is the browser tab controller. It talks to Chrome through CDP, the Chrome DevTools Protocol, which is a command-and-event interface for controlling a browser. Its job is to turn low-level browser messages like “target created” or “page loaded” into a simple tab list the rest of the system can use.

A `Tab` stores the important identity for one browser page: the browser target id, the CDP session id, keyboard state, and known frames. `BrowserTabEvents` is a small inbox for tab-related browser events, such as newly created or destroyed tabs. `BrowserTabs` is the main worker. It watches that inbox, attaches to new pages, removes closed pages, creates a blank tab if none exists, and runs navigation commands.

Navigation is careful. User-friendly inputs like `example.com` are normalized to `https://example.com`, while special commands like `back` and `forward` use browser history. After navigation, the code waits for the page to settle, meaning it has loaded and painted enough to be useful. It also detects the special case where navigation turns into a file download instead of a normal page.

Think of this file like a receptionist for browser windows: it keeps the appointment book current, opens a new room when needed, closes rooms on request, and tells callers what page is currently in each room.

#### Function details

##### `normalize_url`  (lines 25–30)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It leaves special words like `back`, `forward`, and full URLs alone, but adds `https://` to plain site names.

**Data flow**: It receives a text URL or command. It checks whether it is a known special command, `about:blank`, or already has a URL scheme such as `http:` or `chrome:`. It returns either the original text or a safer browser-ready URL with `https://` added.

**Call relations**: BrowserTabs.navigate calls this before deciding whether to go to a new address or move through history. It uses a regular expression to recognize URLs that already include a scheme.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 34–39)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the project’s local record for one open browser tab. This record ties together the browser’s target id, the CDP session id, keyboard state, and frame tracking.

**Data flow**: It receives the browser target id and session id for a page. It stores them, creates a fresh keyboard state, starts an empty frame sequence map, and creates a default top-level frame node. The result is a `Tab` object ready for later navigation and page inspection.

**Call relations**: BrowserTabs.attach_tab calls this after successfully attaching to a real browser target. It uses `KeyboardState` to track pressed keys and `FrameNode` to represent the page’s main frame.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 41–44)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable, simple sequence number. This is useful when the browser’s own frame ids are long or awkward to show or compare.

**Data flow**: It receives a frame id. If that frame has not been seen before, it assigns the next available number and remembers it. It returns the remembered number for that frame.

**Call relations**: This is a helper on `Tab` for code that needs consistent frame numbering. It does not call out to other parts of this file.


##### `BrowserTabCdp.send`  (lines 55–60)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the method used to send a command to the browser over CDP. It is a contract: real connection objects must provide this behavior.

**Data flow**: It takes a CDP method name, optional command parameters, and an optional session id. A real implementation sends that request to the browser and returns the browser’s JSON-like response.

**Call relations**: BrowserTabs relies on this contract whenever it creates targets, attaches to tabs, enables browser domains, navigates pages, or closes tabs. The actual network or protocol work is implemented elsewhere.


##### `BrowserTabCdp.expect`  (lines 62–62)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how code asks to be notified when one of several browser events happens. It lets navigation code say, “I am about to do something; tell me when the matching browser event arrives.”

**Data flow**: It receives one or more event names and an optional session id. A real implementation returns a future, which is a placeholder for a value that will arrive later when the event is seen.

**Call relations**: Navigation helpers use this contract before triggering browser actions, so they do not miss important events such as page load or frame navigation.


##### `BrowserTabCdp.wait`  (lines 64–68)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how code waits for an expected browser event, with a time limit. This prevents the system from hanging forever if the browser never sends the event.

**Data flow**: It receives a future created by `expect` and a timeout in seconds. A real implementation waits until the future is completed or the timeout is reached, then returns the event data or raises an error.

**Call relations**: The navigation helpers use this after sending commands that should cause browser events. The actual waiting behavior is supplied by the CDP connection implementation elsewhere.


##### `BrowserTabSession.connection`  (lines 78–78)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how `BrowserTabs` gets the browser connection object. It keeps this file independent from the concrete connection class.

**Data flow**: It takes no extra input beyond the session object. A real implementation returns an object that can send CDP commands and wait for CDP events.

**Call relations**: Most `BrowserTabs` methods call this when they need to talk to the browser. The protocol lets those methods work with any session object that follows the same contract.


##### `BrowserTabSession.download_reader`  (lines 80–80)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how tab navigation can ask the download system whether a navigation became a file download. This matters because downloads do not behave like normal page loads.

**Data flow**: It takes no extra input beyond the session object. A real implementation returns the download tracker used to inspect newly started downloads.

**Call relations**: BrowserTabs._goto uses this after a failed-looking navigation to check whether the browser actually started a download. The concrete download reader is provided elsewhere.


##### `BrowserTabSession.eval_js`  (lines 82–82)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how code runs JavaScript inside a browser tab. This file uses it to read simple page facts like the current URL and document title.

**Data flow**: It receives a session id and a JavaScript expression. A real implementation evaluates that expression in the page and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info calls this to ask the page for `location.href` and `document.title`. The lower-level JavaScript execution is implemented outside this file.


##### `BrowserTabs.remember_initial_targets`  (lines 90–94)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed before this tab controller started watching. This prevents old pages from being mistaken for newly opened tabs.

**Data flow**: It receives a browser response containing target information. It reads the `targetInfos` list, extracts each `targetId`, and stores those ids in `browser.tab_events.initial_targets`.

**Call relations**: This is used during setup when the browser’s existing targets are first known. It uses wire validation helpers to read the browser’s JSON-shaped data safely.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 96–108)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports a new page target and queues it to be attached as a tab. It filters out non-page targets and targets that were already present at startup.

**Data flow**: It receives event parameters from the browser. If the event describes a new page with a string target id, and that id is neither initial nor already queued, it appends the id to `created_targets`.

**Call relations**: This is an event callback fed by the browser event system. Later, BrowserTabs.sync consumes the queued target ids and calls BrowserTabs.attach_tab for each real new tab.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 110–113)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser says a target was destroyed, meaning a tab or page target may have closed. It records that id so the local tab list can be cleaned up.

**Data flow**: It receives browser event parameters. If `targetId` is a string, it adds it to the `destroyed_targets` set. It does not immediately edit the tab list.

**Call relations**: This event callback fills the cleanup queue. BrowserTabs.sync later reads `destroyed_targets` and removes matching `Tab` records from the local list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 115–119)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when the browser reports that its top-level frame started loading. It ignores subframes so an embedded ad or iframe does not make the whole tab look newly loading.

**Data flow**: It receives browser event parameters and the CDP session id where the event happened. If there is no session id, it does nothing. If the frame id belongs to the tab’s top-level frame, it tells the settle tracker that this session is loading.

**Call relations**: This callback uses BrowserTabs.is_top_level_frame to decide whether the event matters for the whole page. It then updates `browser.settle`, which navigation later waits on.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 121–123)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having reached DOM content loaded, an early page-load milestone where the HTML document is ready to inspect. This helps the settle tracker decide when navigation has progressed.

**Data flow**: It receives browser event data and a session id. If the session id exists, it records that the page for that session has loaded enough to have DOM content.

**Call relations**: This browser event callback feeds the settle system. BrowserTabs.navigate later waits for that settle system after navigation commands.


##### `BrowserTabs.on_lifecycle`  (lines 125–129)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports an important visual lifecycle event. In plain terms, this helps tell whether the page has actually drawn something, not just loaded data.

**Data flow**: It receives browser event parameters and a session id. It ignores events without a session, events that are not paint-related, and events from non-top-level frames. For a relevant event, it tells the settle tracker that the page has painted.

**Call relations**: This callback uses BrowserTabs.is_top_level_frame to avoid reacting to embedded frames. It contributes to the settle process that BrowserTabs.navigate waits for after moving to a page.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 131–134)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame id is the main frame of a known tab. This matters because page-level loading decisions should be based on the main document, not on small embedded frames.

**Data flow**: It receives a session id and a frame id. It scans the known tabs and returns true only when one tab has that session id and its target id matches the frame id.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating the settle tracker. It acts as a gatekeeper for page-level load signals.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 136–156)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects this program to an existing browser page target and prepares it for automation. Without this setup, the code could know a tab exists but could not reliably control or observe it.

**Data flow**: It receives a target id. It asks the browser to attach to that target, extracts the returned session id, initializes the session, enables document-fetch monitoring, applies the configured viewport size, and returns a new `Tab` record.

**Call relations**: BrowserTabs.page uses this when it creates the first blank tab, BrowserTabs.sync uses it for newly reported tabs, and BrowserTabs.create uses it for explicitly opened tabs. It calls BrowserTabs.init_session before constructing the final `Tab`.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 158–163)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for a tab session: page events, lifecycle events, document object model access, and network events. These are the event streams and tools the rest of tab automation depends on.

**Data flow**: It receives a CDP session id. It sends a series of enable commands to the browser for that session. It returns nothing, but after it finishes the browser will send the events this file expects.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a browser target. It prepares the session before fetch monitoring, viewport setup, and `Tab` creation continue.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 165–178)

```
async def sync(self) -> None
```

**Purpose**: Reconciles the local tab list with browser events that have arrived since the last check. It removes tabs the browser destroyed and attaches to new page targets the browser created.

**Data flow**: It reads `browser.tab_events.destroyed_targets` and `created_targets`. Destroyed ids are removed from `browser.tabs`; created ids are popped one by one and attached as new `Tab` objects unless already present or no longer attachable. It updates the stored tab list and clears consumed event queues.

**Call relations**: BrowserTabs.page calls this before choosing a tab, and BrowserTabs.tabs_context calls it before reporting open tabs. For new targets it hands off to BrowserTabs.attach_tab.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 180–193)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab to operate on, creating a blank one if no tabs are open. It also validates explicit tab ids so callers get a clear error instead of silently using the wrong page.

**Data flow**: It receives an optional tab id. It first synchronizes the local tab list. If no tabs exist, it asks the browser to create `about:blank` and attaches to it. If no tab id was requested, it returns the most recently opened tab; otherwise it returns the tab at that index or raises a validation error.

**Call relations**: BrowserTabs.navigate and BrowserTabs.close call this to find their target tab. It may call BrowserTabs.sync and BrowserTabs.attach_tab as part of making sure a usable tab exists.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 195–207)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new place: a URL, back in history, or forward in history. It waits afterward for the page to become reasonably ready before reporting the new tab information.

**Data flow**: It receives a destination string and an optional tab id. It finds the tab, normalizes the destination, resets the settle tracker, then either performs a history step or a direct navigation. Afterward it waits for the page to settle and returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create calls this after opening a new tab. Inside, it calls normalize_url, BrowserTabs.page, BrowserTabs._history_step or BrowserTabs._goto, and finally BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 209–223)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work for navigating a tab to a normal URL. It watches for page-load events and treats downloads as a special successful case when a URL triggers a file download instead of a page.

**Data flow**: It receives a `Tab` and a URL. It records how many downloads existed before navigation, starts waiting for the DOM content loaded event, sends `Page.navigate`, and inspects the browser response. On a normal navigation it waits for the load event; on a download it returns; on a real navigation error it raises an exception.

**Call relations**: BrowserTabs.navigate calls this for destinations that are not `back` or `forward`. It uses the browser connection for CDP commands and the download reader to tell download navigations apart from failures.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 225–242)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browser history when possible. If there is no history entry in that direction, it simply does nothing.

**Data flow**: It receives a `Tab` and a step number, usually -1 for back or 1 for forward. It asks the browser for the navigation history, calculates the desired entry, and if valid sends a command to navigate to that history entry. It waits for the browser to report that navigation happened.

**Call relations**: BrowserTabs.navigate calls this for the special destinations `back` and `forward`. It uses wire helpers to read the browser’s history response safely.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 244–251)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and page title from a tab. This gives callers a small, human-useful summary of what the tab is showing.

**Data flow**: It receives a `Tab`. It runs a JavaScript expression in that tab asking for `location.href` and `document.title`, checks that the result looks like a map, and returns a dictionary with string `url` and `title` fields.

**Call relations**: BrowserTabs.navigate calls this after navigation, BrowserTabs.tabs_context calls it for every open tab, and BrowserTabs.tab_titles calls it when only titles are needed.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 253–260)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to show or reason about the browser state. It marks the most recently opened tab as the active one.

**Data flow**: It first synchronizes the tab list. It then walks through each tab, reads its URL and title, adds its numeric id and active flag, and returns a dictionary containing the current tab id and the full tab list.

**Call relations**: BrowserTabs.close calls this after closing a tab so the caller gets the updated browser state. It calls BrowserTabs.sync and BrowserTabs.tab_info to make the snapshot current.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 262–267)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of the currently known tabs. This is a lightweight view for code that does not need full URLs or tab metadata.

**Data flow**: It loops over `browser.tabs`, reads each tab’s info, pulls out the title, replaces missing titles with an empty string, and returns the list of title strings.

**Call relations**: It relies on BrowserTabs.tab_info for the actual page query. Unlike `tabs_context`, it does not first synchronize with queued browser tab events.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 269–280)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and navigates it to the requested URL. If navigation fails after the tab has been opened, it raises a special error that tells callers the tab was left open.

**Data flow**: It receives an optional URL, defaulting to `about:blank`. It asks the browser to create a blank target, attaches to it, adds the new `Tab` to the tab list, finds its tab id, and then navigates it to the requested URL. It returns the new tab id together with the final URL and title.

**Call relations**: This is the high-level new-tab operation. It calls BrowserTabs.attach_tab first, then BrowserTabs.navigate; if that later step fails, it wraps the problem in `TabLeftOpen` so cleanup decisions can be made by the caller.

*Call graph*: calls 3 internal fn (__init__, attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 282–287)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab and returns the updated list of open tabs. It accepts flexible tab id input, such as an integer, float, string, or missing value.

**Data flow**: It receives an argument dictionary. It extracts and normalizes `tab_id`, finds the matching tab, tells the browser to close that target, removes the tab from the local list, clears out-of-process iframe session tracking, and returns a fresh tabs context.

**Call relations**: It calls _tab_id to interpret the input, BrowserTabs.page to find the tab, sends the close command through the browser connection, and then calls BrowserTabs.tabs_context to report the new state.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 290–299)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loose JSON-style tab id value into an integer tab id or `None`. This lets callers pass tab ids in common forms without duplicating conversion code.

**Data flow**: It receives a value that may be an integer, float, string, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything missing or unsupported becomes `None`.

**Call relations**: BrowserTabs.close calls this before choosing which tab to close. A `None` result means the close operation will use the default tab selection behavior in BrowserTabs.page.

*Call graph*: called by 1 (close).

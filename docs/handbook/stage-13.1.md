# BUA session orchestration and action schema  `stage-13.1`

This stage is the shared control layer for browser automation. It is used during the main work loop, whenever the system needs to look at or operate a web page. Think of it as the browser “driver’s seat” plus the rulebook for what commands are allowed.

The core browser contract in `core/src/ufo/browser.py` says how the system should obtain a Chrome connection for a single turn of work, without tying the core code to one specific browser provider. `backend.py` builds on that by giving browser tools a per-turn surface. It opens the browser only when a tool actually needs it, then releases it safely when the turn ends.

`session.py` represents the live browser session itself. It gathers the practical abilities: working with tabs, reading pages, filling forms, handling downloads and dialogs, running JavaScript, and using mouse or keyboard actions. `actions.py` defines the approved action shapes, such as click, type, scroll, screenshot, and wait, so requests are clear before they run. `errors.py` defines a validation error for cases where the model refers to browser state that is not really there.

## Files in this stage

### Per-turn browser surface
The turn-scoped backend exposes browser operations to tools while lazily opening and safely releasing the browser connection.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser tool use and turn cleanup`

The central idea in this file is a temporary “front desk” for browser work. Each turn gets a BuaSurface, but it does not immediately connect to Chrome. The first time a browser action is requested, it asks a CDP provider for a lease. CDP means Chrome DevTools Protocol, the control channel used to drive Chrome. A lease is like borrowing a browser session: it gives an endpoint to connect to, and later it must be returned.

Once the lease exists, BuaSurface opens a BrowserSession and forwards tool requests to it. Most methods are simple doors into that session: navigate, read the page, find content, fill forms, use low-level computer actions, and work with tabs.

The file also solves two harder problems. First, uploads and downloads may involve a browser running somewhere else, not in the same sandbox as the workspace files. So files are read through the sandbox, passed through the lease, and only then attached to the page. Downloads come back through the lease and are returned as base64 text.

Second, it supports crash recovery. It stores a durable browser-session token for the conversation. If the process crashes before cleanup, the next run can reattach to the same live browser instead of starting over. Normal cleanup closes the session, releases the lease, and clears that token.

#### Function details

##### `BuaSurface._open`  (lines 59–72)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens the browser connection the first time any browser tool needs it. If a session is already open, it reuses it so repeated actions in the same turn keep using the same browser state.

**Data flow**: It starts with the surface’s stored session and lease. If the session already exists, it returns that. Otherwise it gets or creates a lease, asks the lease for the browser endpoint and download directory, creates a BrowserSession, opens it, stores it on the surface, and returns it.

**Call relations**: All user-facing browser actions call this first, such as navigate, read_page, find, form_input, computer, tab operations, uploads, and downloads. When no lease exists yet, it hands off to BuaSurface._acquire_lease to either reconnect to an old session or borrow a new one.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 74–87)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets the CDP lease that lets this turn control a browser. It first tries to recover an existing browser session from a saved token, and only creates a fresh lease if recovery is impossible or not needed.

**Data flow**: It reads any saved token from the scoped store. If a token exists, it asks the provider to reattach to that session. If that session is gone, it clears the stale token. Then it asks the provider for a new lease, stores the new lease’s token, and returns the lease.

**Call relations**: BuaSurface._open calls this when the surface has no lease yet. It uses BuaSurface._stored_token and BuaSurface._store_token so crash recovery and normal cleanup agree on which browser session is safe to reattach to.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 89–93)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser-session token for this conversation, if token storage is available. This token is what allows a recovered turn to reconnect to the same browser after a crash.

**Data flow**: It checks whether the surface has both a store and a conversation ID. If either is missing, it returns nothing. Otherwise it reads the token key from the store and returns the value only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before deciding whether to reattach to an existing browser or create a new lease.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 95–98)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the durable browser-session token for this conversation. Saving supports crash recovery; clearing prevents later turns from reconnecting to a session that has already been released.

**Data flow**: It receives either a token string or None. If there is no store or conversation ID, it does nothing. Otherwise it writes that value under the conversation-specific token key.

**Call relations**: BuaSurface._acquire_lease uses this to save a fresh token and to clear a dead one. BuaSurface.aclose uses it during normal cleanup so released browser sessions are not reused later.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 100–105)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Moves the browser to a requested web address, optionally in a specific tab. It validates that the URL is really text before asking the browser session to navigate.

**Data flow**: It receives tool arguments, opens or reuses the browser session, reads the url field, and rejects it if it is not a string. It converts the optional tab_id into an integer when possible, then returns the BrowserSession navigation result.

**Call relations**: This is one of the tool-facing methods. It calls BuaSurface._open to ensure there is a live browser session, and uses _tab_id to normalize the tab identifier before handing the request to the session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 107–109)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the browser’s current tabs. A caller uses this to understand what pages are open and which tab is active.

**Data flow**: It receives the tool arguments, opens or reuses the browser session, asks the session for tab context, and returns that result unchanged.

**Call relations**: This method is called as part of tab-related browser tooling. It relies on BuaSurface._open for lazy browser startup, then delegates the actual tab inspection to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 111–114)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no usable URL is provided, it opens a blank page instead of failing.

**Data flow**: It receives arguments, opens or reuses the browser session, reads an optional url field, chooses that URL only if it is a non-empty string, otherwise uses about:blank, and returns the session’s result.

**Call relations**: This is the tool-facing path for opening a tab. It calls BuaSurface._open first, then hands the chosen starting URL to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 116–118)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes one or more browser tabs according to the provided arguments. It lets the browser session decide the exact tab-closing behavior.

**Data flow**: It receives tab-close arguments, opens or reuses the browser session, passes those arguments to the session, and returns the session’s reply.

**Call relations**: This method sits between the tab-close tool and BrowserSession. Its main job is to ensure the browser is connected before forwarding the request.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 120–141)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches workspace files to a file input on the web page. It is careful to make files available to the browser whether Chrome is running inside the sandbox or remotely somewhere else.

**Data flow**: It receives arguments and expects a files list. For each file path, it verifies that the path is a non-empty string, resolves it as a workspace path, and asks the lease to place the file where Chrome can read it. If the browser is remote, the lease may call BuaSurface._read to get the bytes. It then sends the placed paths to the browser session, waits until uploaded bytes are really visible to the page, and returns the session’s reply.

**Call relations**: This method is the upload tool’s main path. It calls BuaSurface._open to get a session, BuaSurface._lease to reach the transport lease, workspace_path to keep paths inside the workspace, and BuaSurface._settle_upload to avoid the page seeing an empty file too early.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 143–163)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until an uploaded file has fully arrived in the browser before letting the workflow continue. This protects against a subtle remote-browser problem where the page may see the filename before the file bytes have finished arriving.

**Data flow**: It checks the sizes of files that this surface actually shipped. If nothing was shipped, it returns immediately. Otherwise it repeatedly asks the browser session what file sizes are attached, compares them with the expected sizes, sleeps briefly between attempts, and re-attaches the file after each wait. If the sizes never match, it raises an error that explains what was expected and what the browser reported.

**Call relations**: BuaSurface.upload_file calls this after the first file attachment. It works with BrowserSession.attached_sizes to verify the page’s view of the files and BrowserSession.upload_file to retry attachment while the remote transport catches up.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 165–168)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the current CDP lease, or fails clearly if there is no lease. It is a small safety check for operations that cannot work without the transport lease.

**Data flow**: It reads the surface’s lease field. If the lease exists, it returns it. If not, it raises a runtime error saying there is no browser CDP lease.

**Call relations**: BuaSurface.upload_file uses this to place files for Chrome, and BuaSurface.wait_for_download uses it to fetch downloaded bytes. Those callers first open the browser, so a missing lease would mean the surface is in an unexpected state.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 170–193)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file’s bytes from the sandbox so they can be sent to a remote browser. It also enforces a maximum upload size so a very large file is rejected before it fills server memory.

**Data flow**: It receives a workspace path. It requires a sandbox, quotes the path safely for shell use, asks the sandbox for the file size, rejects unreadable or too-large files, then runs base64 in the sandbox to read the file contents. It decodes the base64 text into bytes in a worker thread, records the byte length in _shipped, and returns the bytes.

**Call relations**: This function is passed as a callback to the lease by BuaSurface.upload_file when a transport needs actual file bytes. It uses sandbox shell commands to read from the turn’s isolated workspace and asyncio.to_thread so base64 decoding does not block the main event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 195–197)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Asks the browser session for a structured reading of the current page. This is used when the tool needs page content in a form suitable for inspection or reasoning.

**Data flow**: It receives read-page arguments, opens or reuses the browser session, forwards the arguments to the session, and returns the session’s result.

**Call relations**: This is a direct tool-facing wrapper. Its important contribution is lazy startup through BuaSurface._open before BrowserSession does the page-reading work.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 199–201)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets the text content of the current page. It is a simpler page-reading path for callers that need plain text rather than richer page structure.

**Data flow**: It receives arguments, opens or reuses the browser session, forwards the request to BrowserSession.get_page_text, and returns the result.

**Call relations**: This method is called by the page-text browser tool. It uses BuaSurface._open to ensure the browser exists, then delegates the actual extraction to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 203–205)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Searches for something on the current page, with optional help from a host-side find completer. The completer can improve or finish the search process outside the raw browser session.

**Data flow**: It receives find arguments, opens or reuses the browser session, passes both the arguments and the surface’s find_completer to the session, and returns the session’s result.

**Call relations**: This is the find tool’s path into the browser. It calls BuaSurface._open first, then lets BrowserSession perform the search while using the injected find completer when one is available.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 207–209)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on the page. A caller uses it when it wants to type into web forms in a browser-controlled way.

**Data flow**: It receives form input arguments, opens or reuses the browser session, forwards the request to BrowserSession.form_input, and returns the result.

**Call relations**: This method is a thin bridge between the form-input tool and BrowserSession. BuaSurface._open supplies the live session; BrowserSession performs the browser interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 211–213)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style actions in the browser, such as interactions that may not fit a higher-level page-reading or form-filling command. It gives tools a more general control path.

**Data flow**: It receives action arguments, opens or reuses the browser session, sends the arguments to BrowserSession.computer, and returns the session’s reply.

**Call relations**: This method is called by the computer-style browser tool. Like the other wrappers, it depends on BuaSurface._open for connection setup and leaves the detailed browser control to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 215–231)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for the browser to finish a download and returns the downloaded file to the caller. It also cleans the filename so a web page cannot smuggle directory paths like ../ into the caller’s workspace write.

**Data flow**: It receives download-wait arguments, opens or reuses the browser session, waits for the session to report a completed download, asks the lease to fetch the file bytes by download ID, base64-encodes those bytes in a worker thread, and returns a safe filename, the base64 content, and the byte size.

**Call relations**: This is the download tool’s main path. BrowserSession detects the completed download, BuaSurface._lease reaches the transport-specific storage, and contained_leaf reduces the page-chosen filename to one safe path segment.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 233–247)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the borrowed CDP lease, and clears the saved reattach token at normal turn end. This prevents leaked hosted-browser sessions and prevents later turns from reconnecting to a browser that was intentionally released.

**Data flow**: It first tries to close the BrowserSession if one exists and clears the session field. Whether that succeeds or fails, it then closes the lease if one exists, clears the lease field, and stores None as the durable token.

**Call relations**: The turn cleanup code registers this method when it creates the surface. It calls BuaSurface._store_token during cleanup; if a hard crash skips this method, the token remains, which is exactly what allows BuaSurface._acquire_lease to reattach during recovery.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 250–261)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab identifier from tool arguments into an integer tab ID when possible. It treats missing, false, or unsuitable values as no specific tab.

**Data flow**: It receives a JSON-like value. Booleans become None, integers stay integers, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes None.

**Call relations**: BuaSurface.navigate uses this helper before forwarding a navigation request to BrowserSession. It keeps tab ID cleanup separate from the main navigation logic.

*Call graph*: called by 1 (navigate).


### Browser contracts and schemas
The shared browser connection contract, supported action shapes, and browser validation error define the typed boundaries for automation.

### `core/src/ufo/browser.py`

`data_model` · `per-turn browser setup, reconnect, file transfer, and cleanup`

This file is a boundary marker. The core system needs to use Chrome through CDP, the Chrome DevTools Protocol, which is a way for software to control Chrome over a URL. But core does not decide where Chrome runs or how it is started. Instead, it defines small promises that browser providers must keep.

The central idea is a lease. A CdpProvider can create a CdpLease for one turn. That lease gives the browser-driving code a connection endpoint, a reconnect token, and a way to clean up when the turn ends. This is like borrowing a key to a room: the lease tells you which door to use, how to get back in if interrupted, and when to return the key.

The file also covers file movement. A Chrome running inside the same sandbox can open workspace files directly. A remote Chrome may need the provider to upload those bytes somewhere first. Downloads work the same way in reverse: the lease says where Chrome should save files and how the system can fetch the finished bytes back.

If reconnecting fails because the old browser session no longer exists, providers raise SessionGone. That tells callers to start fresh instead of pretending an old page is still available.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the CDP connection information for the browser held by this lease. The browser engine uses this to know where to connect and which headers, such as authentication tokens, must be sent.

**Data flow**: The caller asks the lease for its endpoint. The lease looks up or computes the browser URL and any required connection headers. It returns a CdpEndpoint containing that URL and header dictionary, or an implementation may fail if no usable endpoint exists.

**Call relations**: This is called after a provider has created or reattached a lease. The browser-driving extension then uses the returned endpoint to connect to Chrome; core itself does not drive the browser.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: Returns a durable reconnect handle for this browser session. Callers store it so a later recovered turn can try to reconnect to the same browser instead of always starting over.

**Data flow**: The caller asks the lease for a token. The lease turns its current browser session identity into a string, such as a hosted session id or a static endpoint name. That string comes back to the caller for persistence outside the lease.

**Call relations**: This is used after a lease is obtained, especially when the system wants recovery from interruption. Later, the saved token is passed to CdpProvider.reattach to ask for a new lease over the same live browser session.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Makes a workspace file available to the leased Chrome and returns the path or location Chrome should open. This hides the difference between a browser that shares the sandbox filesystem and a remote browser that needs uploaded files.

**Data flow**: The caller provides the original workspace path and a read function that can fetch the file bytes if needed. The lease decides whether copying is necessary. For a sandbox-local browser, it can return the same path; for a remote browser, it can read the bytes, send them to the provider, and return the uploaded location.

**Call relations**: The browser engine calls this before asking Chrome to use a file. The method may call the supplied read callback only when the transport cannot see the sandbox file directly.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the directory or provider-specific location where the leased Chrome should save downloads. This lets the rest of the system request downloads without knowing where the browser is running.

**Data flow**: The caller asks where downloads should go. The lease chooses the appropriate destination for its browser environment, such as a sandbox path or remote provider storage. It returns that destination as a string.

**Call relations**: The browser engine uses this when configuring Chrome downloads. Later, when Chrome reports a completed download by its identifier, CdpLease.fetch_download is used to retrieve the actual bytes.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed browser download. The caller supplies Chrome's download identifier, and the lease knows how to find the file in either sandbox storage or remote provider storage.

**Data flow**: The caller gives a download guid, which is Chrome's unique name for the completed download. The lease looks in the place where this browser was told to save downloads. It reads or fetches the matching file and returns its bytes.

**Call relations**: This is used after downloads have been enabled through CdpLease.download_dir and Chrome has finished saving a file. It is the reverse path of place_file: data moves from the browser environment back into the system.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: Releases whatever hold this lease has on the browser session. For a static local browser this may do nothing; for a hosted browser it may tell the provider the session is no longer needed.

**Data flow**: The caller signals that the turn is finished. The lease performs any provider-specific cleanup, such as releasing or closing a remote session. It returns no value, but it may change external state by freeing the session.

**Call relations**: This belongs at the end of a turn, after the browser engine is done with the endpoint and any file transfers. It balances CdpProvider.lease or CdpProvider.reattach so borrowed browser resources are not kept forever.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a new browser lease for a turn. It may use the turn's sandbox if the browser lives inside that sandbox, or ignore it if the browser is remote or static.

**Data flow**: The caller may pass a SandboxSession, which represents the isolated workspace for the turn. The provider uses that information, if relevant, to create or locate a Chrome session. It returns a CdpLease that the rest of the turn can use for connection details, files, downloads, and cleanup.

**Call relations**: This is the normal starting point when a turn needs a browser and there is no valid saved token. After it returns, callers use the lease's endpoint, token, file, download, and close operations.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser session named by an earlier token. This supports recovery after interruption without losing the browser state, when the old session is still alive.

**Data flow**: The caller provides a saved token string. The provider tries to resolve that token to a live browser session. If it succeeds, it returns a fresh CdpLease for that session; if the session has expired or disappeared, it raises SessionGone so the caller knows to create a new lease instead.

**Call relations**: This is used before starting fresh when the system has a persisted token from CdpLease.token. A successful reattach continues the old browser session; a SessionGone result sends the caller back to CdpProvider.lease.


### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is the vocabulary sheet for browser control. Without it, different parts of the system might describe the same action in different ways, such as one part saying “click here” with coordinates and another expecting an element reference. That would make browser automation brittle and hard to trust.

The file defines the allowed action names in `ActionType`, including mouse actions, keyboard actions, scrolling, screenshots, and waiting. It also defines `CLICK_ACTIONS`, a small set used to quickly recognize actions that are clicks.

The main data shape is `ComputerAction`. Think of it like a form the system fills out before asking the browser to do something. The form has an `action` field, then optional extra details depending on the action: a screen coordinate, text to type, scroll settings, wait duration, drag start point, or an element reference such as `e5` found earlier on the page.

`ScrollParameters` is a smaller form used only for scrolling. It says which direction to scroll and how far, with limits so scroll requests stay reasonable. The file uses Pydantic models, which are Python classes that validate data and attach helpful descriptions. This matters because these action objects may be produced by another tool or model, so the system needs clear rules before touching the browser.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `cross-cutting`

This file is small, but it gives an important kind of failure a clear name. In this project, a model may be asked to choose or refer to something in a browser page, such as an element reference. Sometimes the model may “hallucinate,” meaning it invents a value that looks plausible but is not actually present. For example, it might point to a button ID that the system never saw.

The file defines `HallucinationError`, which is a more specific form of `ValidationError`. A validation error means “the supplied data did not pass the rules we require.” By making hallucinations their own error type, the system can tell the difference between ordinary invalid input and a model inventing impossible browser data.

This is like labeling a returned library book as “never belonged to this library” instead of simply “problem with book.” The broader system can then respond more intelligently, such as retrying, correcting the model, or reporting that the model output was not grounded in the current browser state.


### Live browser session
The session object implements the high-level Chrome automation behaviors used by the per-turn backend.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `active for one browser-control session, from open through tool calls to close`

A Chrome browser can only be controlled if this code first opens a Chrome DevTools Protocol connection. Chrome DevTools Protocol, or CDP, is Chrome’s remote-control interface: it lets another program ask Chrome to open tabs, inspect pages, watch downloads, run JavaScript, and react to browser events. This file wraps that raw connection in a friendlier BrowserSession object.

The session is like a temporary control desk for one turn of work. When it opens, it finds the WebSocket address for Chrome, connects to it, asks Chrome about itself, sets up download rules, starts listening for events, and creates an initial blank tab. It also wires event listeners for tab creation, tab closing, network loading, page lifecycle events, dialogs, and downloads, so later tool calls have up-to-date state.

Most public methods here are thin front doors. They do not do the page-reading or form-filling work directly. Instead, they create a specialized helper, such as BrowserTabs, BrowserContent, BrowserForms, BrowserDownloads, BrowserRuntime, or BrowserComputer, and delegate the request to it. This keeps one shared session state, while each helper focuses on one kind of browser task.

Closing the session is important. It closes tabs, shuts down the CDP connection, cancels background tasks, and clears mutable state. Without this cleanup, stale tabs, downloads, event listeners, or async tasks could leak into the next browser turn.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the basic settings it will need later, such as the CDP endpoint, model display size, and download folder. It does not connect to Chrome yet.

**Data flow**: It receives an optional browser endpoint, an optional model name, and a download directory. It chooses a coordinate space for screenshots, creates empty lists and dictionaries for tabs, downloads, dialogs, and frame sessions, and prepares helper state for loading detection and tab events. The result is a session object ready to be opened.

**Call relations**: BuaSurface._open creates this session before any browser tools can run. The constructor sets up shared state that later helpers, such as tab, download, content, and form readers, will read and update.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the session if it is not already open. It is the safe public way to connect this object to Chrome.

**Data flow**: It checks whether a connection already exists. If not, it runs the bootstrapping process. If anything goes wrong while connecting or setting up listeners, it closes and clears any partial state before passing the error onward.

**Call relations**: The async context-manager entry method calls this when someone writes code that uses the session in an automatic open-and-close block. It hands the real setup work to BrowserSession._bootstrap and relies on BrowserSession.close for cleanup after failed startup.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Performs the detailed startup work needed to make Chrome controllable. This is where the raw browser connection becomes a fully wired session.

**Data flow**: It reads the stored CDP endpoint, resolves it into a WebSocket URL, opens the CDP connection, asks Chrome for version information, and records whether it is running on macOS. It creates tab, download, and dialog helpers; registers event listeners; enables named downloads; discovers existing targets; asks Chrome to create a blank tab; and stores the attached tab as the session’s first tab.

**Call relations**: BrowserSession.open calls this during startup. It calls helper factory methods such as tab_reader, download_reader, and dialog_reader so event callbacks are connected to the same session state that later tool calls will use.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the browser session and forgets its temporary state. It is designed to be safe even if some cleanup steps fail.

**Data flow**: It tries to close each known tab through the CDP connection, ignoring expected browser-control errors during shutdown. It then closes the connection and resets fields such as tabs, frame sessions, downloads, dialogs, loading-tracking state, background tasks, and tab events. Afterward, the session is back to a closed, empty state.

**Call relations**: BrowserSession.__aexit__ calls this at the end of an async context block, and BrowserSession.open calls it if startup fails. It is the matching teardown step for BrowserSession._bootstrap.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python’s async context-manager pattern, which automatically opens it at the start of a block.

**Data flow**: It receives the session object, calls BrowserSession.open, waits for startup to finish, and returns the ready-to-use session.

**Call relations**: This method is used when callers want automatic setup and cleanup around browser work. It delegates the actual opening to BrowserSession.open.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the session automatically when an async context-manager block ends, whether the work succeeded or failed.

**Data flow**: It receives any exception information from the context block, ignores the details, and calls BrowserSession.close to release browser resources and clear session state.

**Call relations**: This is the teardown partner to BrowserSession.__aenter__. It routes all cleanup through BrowserSession.close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection, or raises a clear error if the browser is not open. Helpers use this to avoid silently working without a browser.

**Data flow**: It checks the session’s stored connection. If a connection exists, it returns it. If not, it raises BrowserUnavailable with a message explaining that the browser is not open.

**Call relations**: Specialized browser helpers call this when they need to send commands to Chrome. It protects the rest of the system from using a session before BrowserSession.open has succeeded.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts a background asynchronous task and keeps track of it so it can be cancelled when the session closes. This is useful for work that must continue while the main browser command returns.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores the task in the session’s background-task set, and adds a callback that removes it from the set once it finishes.

**Call relations**: Other browser helpers can use this session method when they need side work to run in the background. BrowserSession.close later cancels any tasks that are still running.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main page frame rather than a nested frame. This matters because browser pages can contain iframes, which are pages inside pages.

**Data flow**: It receives a CDP session ID and frame ID, creates a tab helper, and asks that helper to decide whether the frame is top-level. It returns true or false.

**Call relations**: This is a convenience wrapper around BrowserTabs logic. It keeps frame classification tied to the session’s current tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready to send and receive the browser events this system cares about.

**Data flow**: It receives a CDP session ID, creates a tab helper, and asks it to initialize that session. Any setup changes happen through the helper and the shared browser connection.

**Call relations**: This method delegates to BrowserTabs. It is used as part of the broader tab and frame setup flow after Chrome reports or creates attachable sessions.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the current tab, or a requested tab, as a Tab object. Other browser tools use this when they need to know which page they are acting on.

**Data flow**: It receives an optional tab ID, creates a tab helper, and asks it to find the matching page. It returns the selected Tab.

**Call relations**: This wrapper keeps tab lookup centralized in BrowserTabs while giving the rest of the session a simple page-selection method.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new URL. This is the main session-level way to tell Chrome, “go to this page.”

**Data flow**: It receives a URL and an optional tab ID, creates a tab helper, and asks it to navigate the chosen tab. It returns a JSON-style result describing the navigation outcome.

**Call relations**: BrowserSession.navigate delegates the browser-specific tab work to BrowserTabs. The session provides shared connection and state; the tab helper performs the navigation.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a structured description of one tab. This helps callers understand what page a tab represents.

**Data flow**: It receives a Tab object, creates a tab helper, and asks it to produce information about that tab. The output is a JSON-style dictionary.

**Call relations**: This method is a small front door to BrowserTabs. It is used when higher-level code needs tab details without knowing the tab helper’s internals.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns a summary of the session’s tab situation. It gives callers context about open tabs before choosing what to do next.

**Data flow**: It creates a tab helper, asks it to build the tab context from the session’s current tab list and browser state, and returns a JSON-style dictionary.

**Call relations**: This method routes tab-context requests to BrowserTabs, which owns the logic for turning raw tab state into useful context.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the visible titles of the current tabs. This is a lightweight way to show what pages are open.

**Data flow**: It creates a tab helper, asks it for tab titles, and returns a list of strings.

**Call relations**: This is another simple wrapper around BrowserTabs. It keeps title gathering in the tab subsystem while exposing it through the session.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper for tab-related work. The helper knows how to create, close, attach, navigate, and describe tabs.

**Data flow**: It uses the current BrowserSession and the fixed viewport size to create a BrowserTabs object. The returned helper reads and changes the session’s shared tab state.

**Call relations**: Startup and many tab-facing methods call this, including bootstrap, page lookup, navigation, title listing, tab creation, and tab closing. It is the session’s bridge to the tab subsystem.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page-frame and element-reference work. It is used when the system must turn a page reference into a real frame or screen point.

**Data flow**: It combines the current session, the viewport size, and the maximum frame depth into a BrowserPage helper. The helper can then inspect page structure through the session.

**Call relations**: BrowserSession.resolve_ref and BrowserSession.ref_point call this when they need page-reference resolution. It keeps detailed page-frame logic out of the session itself.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading and searching page content. This covers tasks such as page summaries, text extraction, accessibility-style trees, and find operations.

**Data flow**: It receives no external input beyond the session, creates a BrowserContent object, and returns it. The helper uses the session’s browser connection and tab state when later asked to read content.

**Call relations**: BrowserSession.tree, read_page, get_page_text, and find call this. It is the session’s doorway to content-reading behavior.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for download events and waiting for completed downloads. It carries the session’s download state plus the configured maximum wait time.

**Data flow**: It builds a BrowserDownloads object from the session and the wait limit. The helper reads and updates the session’s download list as Chrome reports download progress.

**Call relations**: BrowserSession._bootstrap uses this to connect Chrome download events to callbacks. BrowserSession.wait_for_download uses it later when a caller wants to wait for a download result.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for JavaScript alert, confirm, and prompt dialogs. These popups can block page interaction, so the session needs a way to notice and respond to them.

**Data flow**: It creates a BrowserDialogs object tied to this session. That helper can record dialog messages and react to dialog-opening events.

**Call relations**: BrowserSession._bootstrap calls this while wiring Chrome event listeners. The dialog helper’s callback is then invoked when Chrome reports a JavaScript dialog.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form-related actions, such as typing into fields and uploading files.

**Data flow**: It builds a BrowserForms object using the current session. The returned helper can use the browser connection and page references to perform form actions.

**Call relations**: BrowserSession.upload_file, attached_sizes, and form_input call this. It keeps form-specific browser operations separate from the session’s orchestration role.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript and calling JavaScript functions inside the browser. Runtime here means the page’s live JavaScript environment.

**Data flow**: It creates a BrowserRuntime object tied to the session. The helper uses the CDP connection to evaluate expressions or call functions in a specific browser session.

**Call relations**: BrowserSession.eval_js and BrowserSession.call_on call this. It is the session’s bridge to JavaScript execution inside Chrome.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, usually blank unless a URL is supplied.

**Data flow**: It receives a URL, defaulting to about:blank, creates a tab helper, and asks it to create the tab. It returns a JSON-style description of the created tab or operation.

**Call relations**: This method exposes tab creation through the session while delegating the actual browser command and state update to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab described by the incoming arguments. This lets callers remove tabs without talking directly to CDP.

**Data flow**: It receives a JSON-style argument dictionary, creates a tab helper, and asks it to close the requested tab. It returns a JSON-style result.

**Call relations**: This method is the session-level tab-close command. BrowserTabs performs the detailed close operation and updates session tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads a file through a page file-input control. It is used when a browser workflow needs to attach local files to a form.

**Data flow**: It receives JSON-style arguments describing the upload target and file information, creates a form helper, and asks it to perform the upload. It returns a JSON-style result from that helper.

**Call relations**: BuaSurface._settle_upload calls this as part of upload handling. The method delegates the browser interaction to BrowserForms.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached during an upload flow. This helps the caller confirm what was attached without reading the file contents here.

**Data flow**: It receives JSON-style upload arguments, creates a form helper, and asks it to compute or retrieve the attached file sizes. It returns a list of integer sizes.

**Call relations**: BuaSurface._settle_upload calls this around upload settling. It shares form-upload knowledge with BrowserForms instead of duplicating it in the session.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree of page content, optionally filtered by type. This gives a compact view of what is on the page.

**Data flow**: It receives JSON-style arguments and a filter name, creates a content helper, and asks it to build the tree. It returns the tree as a string.

**Call relations**: This session method exposes BrowserContent.tree to callers. The content helper does the page inspection, while the session supplies shared browser state.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result suitable for a browser tool response. It is a higher-level page-reading command.

**Data flow**: It receives JSON-style arguments, creates a content helper, and asks it to read the page. It returns a JSON-style dictionary containing the result.

**Call relations**: This is a front door to BrowserContent. It lets callers request a page read without knowing how content is extracted from Chrome.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts plain text from a page. This is useful when the caller needs the words on the page rather than layout or element structure.

**Data flow**: It receives JSON-style arguments, creates a content helper, and asks it to gather page text. It returns a JSON-style dictionary with the text result.

**Call relations**: This delegates to BrowserContent, keeping text extraction logic outside the session while still using the session’s live browser connection.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches the page for requested content and may use a completion helper to finish or refine the search. This supports browser “find” behavior for page content.

**Data flow**: It receives JSON-style search arguments and an optional FindCompleter, creates a content helper, and asks it to perform the find operation. It returns a JSON-style result.

**Call relations**: This method routes find requests into BrowserContent. If a completion helper is supplied, it is passed along so the content helper can use it during the search.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or otherwise enters values into a form field on the page. It is the session-level command for filling forms.

**Data flow**: It receives JSON-style arguments describing the target and input, creates a form helper, and asks it to apply the input in Chrome. It returns a JSON-style result.

**Call relations**: This method delegates to BrowserForms, which knows the details of locating form controls and sending the needed browser commands.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a lower-level computer-style action in the browser, such as actions based on coordinates, viewport, waiting, or input simulation. It treats the browser like a screen that can be interacted with.

**Data flow**: It receives JSON-style action arguments, creates a BrowserComputer with the session, viewport size, and wait limit, and runs the action. It returns a JSON-style result.

**Call relations**: Unlike the reader factory methods, this constructs BrowserComputer directly for the single action. The computer helper uses the session to drive Chrome.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to finish and returns the recorded download information. It reports the completed download rather than reading the downloaded bytes.

**Data flow**: It receives JSON-style wait arguments, creates a download helper, and asks it to wait up to the configured limit for a matching download. It returns a BrowserDownload object.

**Call relations**: BrowserSession._bootstrap wires download events into the shared session state. This method later asks BrowserDownloads to watch that state until the desired download is ready.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a specific browser session. This is useful for inspecting or changing page state that is only available through JavaScript.

**Data flow**: It receives a CDP session ID and a JavaScript expression, creates a runtime helper, and asks it to evaluate the expression. It returns the JSON-style value reported by Chrome.

**Call relations**: This delegates JavaScript execution to BrowserRuntime. The session supplies the live connection and keeps the public API simple.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing JavaScript object inside the browser. This is a more targeted runtime operation than evaluating a loose expression.

**Data flow**: It receives a CDP session ID, a JavaScript object ID, a function body or function text, and optional arguments. It creates a runtime helper, asks it to call the function on that object, and returns Chrome’s JSON-style response.

**Call relations**: BrowserSession.call_on routes object-specific JavaScript calls to BrowserRuntime. It is used when another part of the browser tooling already has a reference to a page object.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame and backend node information needed to act on it. A reference is a stable label the tools use for something on the page.

**Data flow**: It receives a Tab and a reference string, creates a page helper, and asks it to resolve the reference. It returns a FrameNode and an integer identifier for the resolved node.

**Call relations**: This delegates reference resolution to BrowserPage. Other browser actions can use the returned frame and node information to target the correct page element.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen coordinate for a referenced page element. This is needed when an action must click or move to a visible point rather than just identify an element.

**Data flow**: It receives a Tab and a reference string, creates a page helper, and asks it to calculate a point for that reference. It returns an x and y coordinate pair.

**Call relations**: This builds on BrowserPage’s page-geometry logic. It gives higher-level actions a simple way to turn a page reference into a place on the browser viewport.

*Call graph*: calls 1 internal fn (page_reader).

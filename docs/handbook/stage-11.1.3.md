# Browser session orchestration, tabs, settling, dialogs, and downloads  `stage-11.1.3`

This stage is the live control desk for browser work. It runs during the main work loop, after Chrome is connected and while the agent is using real web pages. The central piece is session.py, which represents one browser session. It connects the tools for opening pages, reading page content, clicking, typing, filling forms, managing tabs, and watching files that may be downloaded.

The other files act like specialist helpers around that desk. tabs.py manages the browser’s tabs: it can open, close, switch, and navigate them using Chrome’s DevTools Protocol, a control channel that lets software talk directly to Chrome. settle.py decides when an action is finished enough to move on, so the agent waits for useful page changes but does not wait forever for ads or background tracking. dialogs.py deals with pop-up boxes such as alerts, confirmations, prompts, and “leave this page?” warnings, answering them quickly so the browser does not stall. downloads.py detects and waits for downloads, including cases where Chrome tries to preview a PDF instead of saving it.

## Files in this stage

### Session orchestration
Defines the live Chrome session control desk that coordinates browser actions, inspection, navigation, tabs, dialogs, settling, and downloads.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `active during one browser-control turn: open connection, serve tool calls, then teardown`

A Chrome browser can be controlled through CDP, the Chrome DevTools Protocol, which is a message-based remote-control interface. This file wraps that low-level connection in a friendlier session object. Without it, the rest of the browser extension would have to know how to connect to Chrome, listen for browser events, remember open tabs, track downloads, deal with dialogs, and clean everything up afterward.

`BrowserSession` starts with a CDP endpoint, opens a WebSocket connection to Chrome, and registers event listeners. Those listeners notice things like new tabs, closed tabs, page loading, network activity, JavaScript dialogs, and download progress. It also creates an initial blank tab so there is always a page to work with.

Most user-facing browser actions are thin routes from this session to smaller helper objects. For example, tab actions go through `BrowserTabs`, page reading goes through `BrowserContent`, form work goes through `BrowserForms`, downloads go through `BrowserDownloads`, and JavaScript execution goes through `BrowserRuntime`. Think of the session as a hotel front desk: it knows the guest’s current room, records important events, and sends each request to the right department.

The session is disposable. Closing it shuts tabs, closes the CDP connection, cancels background tasks, and resets remembered state.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the basic settings it will need later, such as the CDP endpoint, model screen size, and download folder. It does not connect to Chrome yet.

**Data flow**: Inputs are the optional Chrome endpoint, optional model name, and download directory. The function chooses a coordinate size for screenshots, creates empty lists and dictionaries for tabs, downloads, dialogs, and frame sessions, and creates fresh tracking helpers for page settling and tab events. The result is a ready-but-closed session object.

**Call relations**: This is called when `BuaSurface._open` wants a new session. It prepares the state that `open` and `_bootstrap` will later fill with a real Chrome connection and live tab information.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the session if it is not already open. It is safe to call more than once because it returns immediately when a connection already exists.

**Data flow**: It reads the current connection field. If there is no connection, it asks `_bootstrap` to do the real setup. If setup fails partway through, it calls `close` so half-created tabs, tasks, or connections do not leak, then passes the error upward.

**Call relations**: `__aenter__` calls this when the session is used in an async context block. It hands the main setup work to `_bootstrap` and relies on `close` for cleanup after failed startup.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the real work of connecting to Chrome and preparing the session to receive browser events. This is the startup checklist for the browser control surface.

**Data flow**: It reads the CDP endpoint, resolves it into a WebSocket URL, opens a `CdpConnection`, checks the browser version to see whether it is running on macOS, creates helper readers, and registers event callbacks for downloads, tabs, network loading, page lifecycle events, dialogs, and paused fetch requests. It also enables downloads, starts target discovery, creates a blank tab, attaches to it, and stores it as the session’s first tab.

**Call relations**: `open` calls this during session startup. It creates readers with `tab_reader`, `download_reader`, and `dialog_reader`, opens the CDP connection, and gives Chrome the instructions needed so later tool calls and event listeners can work.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the session and returns it to a clean empty state. It is designed to be forgiving, so cleanup continues even if closing a tab fails.

**Data flow**: It looks at the current connection and tabs. For each known tab it asks Chrome to close the target, ignoring common cleanup errors. Then it closes the CDP connection and clears all session memory: connection, tabs, out-of-process frame sessions, downloads, dialogs, scroll state, settling tracker, background tasks, and tab event tracker.

**Call relations**: `__aexit__` calls this at normal teardown, and `open` calls it if startup fails. It is the reset point that makes a failed or finished session safe to discard.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets `BrowserSession` be used with Python’s async `with` pattern, where setup happens automatically at the beginning. It returns the opened session for use inside the block.

**Data flow**: It receives the session object, calls `open`, waits for the connection and tab setup to finish, and then returns the same session object.

**Call relations**: Code that wants automatic startup and cleanup enters through this method. It delegates startup to `open`; the paired `__aexit__` later handles shutdown.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Completes the async `with` pattern by closing the browser session when the block ends. It runs whether the block finished normally or because of an error.

**Data flow**: It receives any exception information from the async context manager protocol, but it does not inspect it. It simply calls `close`, which tears down browser state and the connection.

**Call relations**: This is paired with `__aenter__`. It hands all cleanup to `close` so callers do not need to remember to shut the session manually.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection, or raises a clear error if the browser is not open. It protects helper code from accidentally using a missing connection.

**Data flow**: It reads `self.conn`. If a connection exists, it returns it. If not, it raises `BrowserUnavailable` with a plain message saying the browser is not open.

**Call relations**: Other session helper objects can call this when they need to send commands to Chrome. It acts as the gatekeeper between higher-level browser actions and the low-level connection.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so the session can cancel it during cleanup. This is useful for browser work that must continue while the main tool call moves on.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores the task in the session’s background task set, and attaches a callback that removes the task from the set when it finishes.

**Call relations**: Session helpers can use this when they need side work. `close` later cancels any still-running tasks, so background work does not outlive the browser session.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to a main page frame rather than a nested frame. This matters because page-load events from inner frames should not always be treated as whole-page navigation.

**Data flow**: It receives an optional CDP session id and frame id, creates a tab reader, and asks that reader to decide whether the frame is top-level. It returns a true-or-false answer.

**Call relations**: This method is a convenience doorway into `BrowserTabs`. It is used when event handling needs tab/frame knowledge without duplicating that logic in the session.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready for browser-page events and commands. A CDP session here is a channel Chrome uses for a specific tab or frame target.

**Data flow**: It receives a CDP session id, creates a tab reader, and asks it to initialize that session. Any changes happen inside the tab reader and through Chrome commands.

**Call relations**: This delegates to `BrowserTabs`, which knows the tab setup details. It is part of the flow that turns newly discovered browser targets into usable controlled pages.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the current tab, or a requested tab, as a `Tab` object. Tool calls use this when they need to know which page they are acting on.

**Data flow**: It receives an optional tab id. It creates a tab reader, asks it to select or find the page, and returns the matching `Tab` object.

**Call relations**: Higher-level browser actions call this through the session when they need a tab. The actual tab lookup rules live in `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new web address. It is the session-level entry for “go to this URL.”

**Data flow**: It receives a URL and an optional tab id. It passes both to a tab reader, which sends the navigation command to Chrome and returns a JSON-style result describing what happened.

**Call relations**: This is one of the browser tool-surface methods exposed by the session. It delegates the details of choosing the tab and driving navigation to `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a small information summary for a specific tab. This helps callers understand a tab’s current state without poking at internal fields.

**Data flow**: It receives a `Tab` object, creates a tab reader, and asks that reader to turn the tab into a JSON-style information dictionary.

**Call relations**: The session provides the doorway, while `BrowserTabs` owns the tab-formatting details. This keeps tab knowledge in one helper rather than spread across the code.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about all open tabs, such as what is available to act on. This is useful before choosing a tab or reporting browser state.

**Data flow**: It takes no extra input, creates a tab reader, and asks it for a JSON-style summary of the current tabs kept in the session.

**Call relations**: This is a session-level tab overview call. It delegates to `BrowserTabs`, which reads the session’s tab list and tab event state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the page titles for the session’s tabs. It gives callers a simple human-readable list of what is open.

**Data flow**: It creates a tab reader, which inspects or queries the current tabs and returns a list of title strings.

**Call relations**: This method is a small convenience wrapper around `BrowserTabs`. It is used when callers need names rather than full tab records.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a `BrowserTabs` helper for tab-related work. That helper knows how to create, close, attach to, navigate, and summarize tabs.

**Data flow**: It reads the current session and the fixed viewport size, then returns a new `BrowserTabs` object linked back to this session.

**Call relations**: Many session methods call this, including startup in `_bootstrap`, tab selection, navigation, tab creation, tab closing, and tab summaries. It is the main handoff point from the session to tab-specific logic.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a `BrowserPage` helper for page-structure work, especially resolving references to elements or frames. It gives other code a consistent way to understand page coordinates and frame depth.

**Data flow**: It reads the session, viewport size, and maximum frame depth, then returns a `BrowserPage` object configured with those values.

**Call relations**: `resolve_ref` and `ref_point` call this when a tool needs to turn a page reference into a real frame or screen point. The session supplies shared state; `BrowserPage` does the page-specific calculation.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a `BrowserContent` helper for reading and searching page content. This covers things like accessibility trees, visible page information, text extraction, and find operations.

**Data flow**: It passes the session into a new `BrowserContent` object and returns that helper.

**Call relations**: `tree`, `read_page`, `get_page_text`, and `find` all call this. The session routes content requests here so page-reading behavior stays in the content helper.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a `BrowserDownloads` helper for tracking and waiting for downloads. It includes the session state and the maximum wait time.

**Data flow**: It reads the session and fixed maximum wait value, then returns a download helper connected to the session’s download list and CDP event flow.

**Call relations**: `_bootstrap` uses this helper to attach download event listeners, and `wait_for_download` uses it when a tool needs to wait for a completed download.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a `BrowserDialogs` helper for browser JavaScript dialogs, such as alert or confirm popups. These popups can block page automation if nobody notices them.

**Data flow**: It passes the session into a new dialog helper and returns it.

**Call relations**: `_bootstrap` calls this while registering Chrome event listeners. The dialog helper receives dialog-opening events and records or responds to them through session state.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a `BrowserForms` helper for form-related actions, including typing into fields and uploading files.

**Data flow**: It passes the session into a new form helper and returns it.

**Call relations**: `upload_file`, `attached_sizes`, and `form_input` call this. The session acts as the front door, while `BrowserForms` performs the form-specific browser commands.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a `BrowserRuntime` helper for running JavaScript inside a page or calling functions on JavaScript objects. This is the controlled way to use Chrome’s runtime features.

**Data flow**: It passes the session into a new runtime helper and returns it.

**Call relations**: `eval_js` and `call_on` call this. Those methods keep runtime access behind a small session wrapper instead of exposing the connection directly.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, using `about:blank` by default if no URL is supplied. It returns structured information about the created tab.

**Data flow**: It receives a URL, creates a tab reader, and asks it to create a tab at that address. The returned value is a JSON-style dictionary from the tab helper.

**Call relations**: This is the session’s tab-creation tool. It hands the actual Chrome target creation and session tab-list update to `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one or more browser tabs according to the supplied arguments. It gives callers a safe session-level way to remove tabs.

**Data flow**: It receives a JSON-style argument dictionary, creates a tab reader, and asks it to close the requested tab or tabs. It returns the tab helper’s JSON-style result.

**Call relations**: This is the session’s tab-closing tool. The details of interpreting arguments and updating tab state are delegated to `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads a file through a page’s file input. It is used when automation needs to attach a local file to a web form.

**Data flow**: It receives a JSON-style argument dictionary describing the upload target and file information. It creates a form reader, which performs the upload steps and returns a JSON-style result.

**Call relations**: `BuaSurface._settle_upload` calls this as part of upload handling. The session forwards the work to `BrowserForms`, which knows how to interact with file inputs.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached through form upload arguments. This helps the upload flow confirm what was attached.

**Data flow**: It receives a JSON-style argument dictionary, creates a form reader, and asks it to inspect the attached file sizes. It returns a list of integer sizes.

**Call relations**: `BuaSurface._settle_upload` calls this around upload settling. The file-size detail lives in `BrowserForms`, while the session provides the shared browser context.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text representation of the page tree, optionally filtered by type. A page tree is a structured view of page elements, useful for understanding what can be read or acted on.

**Data flow**: It receives JSON-style arguments and a filter type, creates a content reader, and asks it to build the tree text. The output is a string.

**Call relations**: This is part of the session’s content-reading surface. It delegates to `BrowserContent`, which knows how to inspect the page structure.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result suitable for a browser tool response. It helps callers see what is on the page in a usable form.

**Data flow**: It receives JSON-style arguments, creates a content reader, and asks it to read the page. The output is a JSON-style dictionary with the page-reading result.

**Call relations**: This method is a front door for page inspection. The session routes the request to `BrowserContent`, which performs the actual reading.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts text from the page. This gives callers a simpler text-focused view when they do not need the full page structure.

**Data flow**: It receives JSON-style arguments, creates a content reader, and asks it to collect page text. It returns a JSON-style dictionary containing the result.

**Call relations**: This belongs to the content-reading flow. The session delegates the extraction details to `BrowserContent`.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches the page content for requested text or patterns. An optional completer can help finish or refine the search result.

**Data flow**: It receives JSON-style search arguments and an optional `FindCompleter`, creates a content reader, and asks it to perform the find operation. It returns a JSON-style result.

**Call relations**: This is the session-level search tool. It sends the work to `BrowserContent`, which understands how to search within the browser page.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Fills or edits form fields on the page. This is used for actions like entering text into inputs.

**Data flow**: It receives JSON-style form input arguments, creates a form reader, and asks it to perform the input action. It returns a JSON-style result describing the outcome.

**Call relations**: This is the session’s form-entry tool. It delegates field interaction to `BrowserForms`, which has the page and element logic for forms.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs computer-like browser actions such as mouse or keyboard interactions through `BrowserComputer`. This is for low-level interaction with the page surface.

**Data flow**: It receives JSON-style action arguments, creates a `BrowserComputer` with the session, viewport, and maximum wait time, and runs it. The output is a JSON-style result.

**Call relations**: Unlike the reader factory methods, this creates `BrowserComputer` directly for each call. It gives the lower-level interaction tool access to the current session and screen geometry.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to complete and returns the recorded download information. It reports the download rather than reading the downloaded bytes.

**Data flow**: It receives JSON-style wait arguments, creates a download reader, and asks it to wait for the matching download up to the configured time limit. It returns a `BrowserDownload` record.

**Call relations**: This relies on download events registered during `_bootstrap`. The session exposes the waiting tool, while `BrowserDownloads` watches the session’s download state.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a specific browser session. This is useful for controlled inspection or small scripted actions in a page.

**Data flow**: It receives a CDP session id and a JavaScript expression string, creates a runtime reader, and asks it to evaluate the expression. It returns the JSON-compatible result from Chrome.

**Call relations**: This is a thin session wrapper over `BrowserRuntime`. It keeps JavaScript execution tied to the current browser session instead of letting callers use the raw connection directly.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing JavaScript object inside the page. This is used when code already has an object reference from Chrome and wants to operate on it.

**Data flow**: It receives a CDP session id, an object id, a function body or function declaration, and optional JSON-compatible arguments. It creates a runtime reader, which sends the call to Chrome and returns a JSON-style dictionary result.

**Call relations**: This method is part of the runtime-execution path with `eval_js`. It delegates the CDP runtime details to `BrowserRuntime`.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame node and backend node id it points to. In plain terms, it converts a label used by the tool into the exact page element Chrome understands.

**Data flow**: It receives a `Tab` and a reference string, creates a page reader, and asks it to resolve the reference. It returns a pair: the frame node and an integer node id.

**Call relations**: Actions that need a real page element can use this before clicking, typing, or inspecting. The session delegates reference interpretation to `BrowserPage`.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen point for a referenced page element. This is useful when an action needs coordinates, such as clicking the center of an element.

**Data flow**: It receives a `Tab` and a reference string, creates a page reader, and asks it to compute the point. It returns an `(x, y)` pair of integers.

**Call relations**: This supports coordinate-based actions by bridging from human/tool references to screen positions. `BrowserPage` performs the page lookup and coordinate calculation, while the session supplies the current browser state.

*Call graph*: calls 1 internal fn (page_reader).


### Dialog and download handling
Handles browser-side interruptions and artifacts by answering JavaScript dialogs and tracking files saved through Chrome.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

JavaScript dialogs are small pop-up boxes created by a web page. In an automated browser, they are more than a nuisance: while one is open, the page can stop sending later browser events until the dialog is answered. This file is the safety valve for that situation.

The main rule is simple. Alerts and before-unload dialogs are automatically accepted, because there is usually no meaningful choice to make. Confirm and prompt dialogs are dismissed, because accepting them could silently agree to something the site asked, like “Are you sure?” That keeps the agent from accidentally approving a website’s own guardrail.

When a dialog event arrives, `BrowserDialogs.on_dialog` reads its type and message, stores a plain text note in the browser session’s `dialogs` list, and immediately starts a background task to answer it. Running the answer in the background means the event handler can return right away instead of blocking the browser event stream.

The actual reply is sent through the Chrome DevTools Protocol, often shortened to CDP, which is the browser control channel used to send commands like “accept this dialog.” If that command fails because the browser is already gone, timed out, or reported a CDP error, the file logs a warning instead of crashing the agent.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser command sender. Anything used here must be able to send a named Chrome DevTools Protocol command, with optional parameters and an optional session id.

**Data flow**: It receives a command name, optional command data, and optionally the browser session the command belongs to. The implementation sends that command to the browser and returns a JSON-like dictionary response.

**Call relations**: This is a protocol, meaning it describes what another object must provide rather than implementing the behavior here. `BrowserDialogs._answer_dialog` relies on this method when it needs to tell the browser to accept or dismiss a dialog.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the browser connection from a session object. It gives dialog code access to the command channel without tying it to one concrete session class.

**Data flow**: It takes the current browser session object and returns an object that can send browser commands. No dialog decision is made here; it simply exposes the communication path.

**Call relations**: This protocol method is used by `BrowserDialogs._answer_dialog` just before sending the `Page.handleJavaScriptDialog` command. It keeps the dialog code independent from the exact browser-session implementation.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for a browser session to start a small asynchronous job in the background. It lets the dialog handler answer pop-ups immediately without stopping the current event flow.

**Data flow**: It receives a coroutine, which is a paused asynchronous task, and schedules it to run. The caller does not wait for a result; the effect is that the work continues separately.

**Call relations**: The dialog event path uses this in `BrowserDialogs.on_dialog` after it has decided whether to accept or dismiss the dialog. The background job it starts is `BrowserDialogs._answer_dialog`.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog has appeared. It chooses a safe default answer, records a note for the agent, and starts answering the dialog right away so the browser does not stay stuck.

**Data flow**: It receives the dialog details as a JSON-like dictionary and an optional browser session id. It reads the dialog type and message, decides whether to accept it, appends a human-readable record such as `alert accepted: hello` or `confirm dismissed`, and schedules the actual browser command in the background. It does not return a useful value; its output is the saved note and the started background task.

**Call relations**: This is the front door for dialog events. When called, it gathers the needed information from the event data, then hands off to `BrowserDialogs._answer_dialog` to send the browser command. It also uses the session’s background-task hook so the answer can be sent without delaying the event handler.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This function sends the actual command that accepts or dismisses the open JavaScript dialog. It is separated from the event handler so it can run asynchronously in the background.

**Data flow**: It receives the optional browser session id and a true-or-false choice saying whether to accept the dialog. It asks the browser session for its connection, sends the `Page.handleJavaScriptDialog` command with that choice, and produces no returned result. If the command fails because of a browser, timeout, or runtime problem, it writes a warning log instead of raising the error further.

**Call relations**: This function is started by `BrowserDialogs.on_dialog` after the dialog decision has already been made. It then uses the session’s `connection` method and the connection’s `send` method to deliver the command to the browser.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download waiting`

This file solves a practical browser problem: not every file-like page becomes a real download. For example, Chrome often opens PDFs inside its built-in viewer, where the automation agent may not be able to read the content. This code watches browser network events and, when a top-level page is a PDF, quietly changes the response so Chrome treats it as an attachment to download.

It also keeps a small in-memory list of downloads. When Chrome says a download has started, the file records its browser-generated id, suggested filename, and current state. When Chrome reports progress, the state is updated. Other code can then ask this file to wait until a download finishes.

The file talks to Chrome through CDP, the Chrome DevTools Protocol, which is Chrome’s remote-control message system. A key safety rule here is that every paused browser request must be resumed. If a request is paused and not released, the page can hang, like traffic stopped at a red light that never turns green. So even when this code does not change a response, it still tells Chrome to continue.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the kind of object this file needs for sending commands to Chrome. It is not the implementation itself; it describes the expected shape of a Chrome DevTools Protocol connection.

**Data flow**: It receives a command name, optional command details, and an optional browser session id. An implementation sends that command to Chrome and returns Chrome’s JSON-like reply.

**Call relations**: The download code relies on this capability when it needs to release a paused request or response. In this file, the actual calls happen after BrowserDownloads asks its browser session for a connection.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: Defines how the download code gets access to the browser’s command channel. The command channel is needed to tell Chrome to continue paused network traffic.

**Data flow**: It takes no extra input beyond the session object. It returns an object that can send Chrome DevTools Protocol commands.

**Call relations**: BrowserDownloads uses this when continuing requests and responses. The real browser session object elsewhere in the project supplies the actual connection.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Defines how this file can start a small asynchronous task without blocking the browser event callback. This matters because browser events need quick replies, but the actual Chrome command may take a moment.

**Data flow**: It receives a coroutine, which is an asynchronous job waiting to run. The browser session schedules that job in the background and does not return a useful value.

**Call relations**: BrowserDownloads.on_fetch_paused uses this to release paused requests or responses after deciding what should happen to them.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Defines how this file can ask whether a browser event belongs to the main page rather than an embedded frame. This distinction prevents embedded PDFs from being forced into downloads when they should remain visible inside a page.

**Data flow**: It receives a browser session id and frame id from Chrome’s event data. It returns true if that frame is the main page frame, and false otherwise.

**Call relations**: BrowserDownloads.on_fetch_paused uses this before forcing a PDF response to download. That keeps the special behavior limited to real page navigations.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Responds when Chrome pauses a network fetch event. It decides whether to let the request continue unchanged or rewrite a top-level PDF response so it becomes a download.

**Data flow**: It receives Chrome’s event details and the browser session id. It reads the request id, response status, headers, frame id, and content type. If the event is not a response yet, it schedules a normal request continuation. If it is a response, it checks whether it is a top-level PDF and schedules a response continuation, possibly with a forced attachment header.

**Call relations**: This is the main entry point for paused fetch events in this file. It uses _content_type to identify the response type, asks the browser session whether the frame is top-level, and then hands off to _continue_request or _continue_response in a background task so Chrome does not stay paused.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: Tells Chrome to resume a paused request when there is no response to inspect yet. This prevents the page from getting stuck.

**Data flow**: It receives the browser session id and Chrome’s request id. It sends a Fetch.continueRequest command through the browser connection. If Chrome rejects the command or the connection fails, it logs a warning instead of crashing the whole flow.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this when Chrome pauses at a stage before response details are available. It is the simple “let it keep going” path.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: Tells Chrome to resume a paused response, optionally changing it so the browser saves it as a download. This is the point where top-level PDFs can be turned from inline viewer pages into downloadable files.

**Data flow**: It receives the session id, request id, response code, response headers, and a true-or-false force flag. If forcing is needed, it removes any existing Content-Disposition header and adds one that says attachment. Then it sends Fetch.continueResponse to Chrome. If that fails, it logs a warning.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this after it has inspected a response. This function performs the actual handoff back to Chrome, either unchanged or with the download-forcing header.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records that Chrome has started a new download. This gives the rest of the system something to wait on and report later.

**Data flow**: It receives Chrome’s download-start event details. It pulls out the download id and suggested filename, fills in a default filename if needed, marks the state as in progress, and appends a new Download record to the browser session’s download list.

**Call relations**: This is called when the browser reports a download beginning. Later, on_download_progress updates the same record, and wait looks through these records to find completed downloads.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Updates the saved state of a download as Chrome reports progress. This is how the file knows when a download has completed.

**Data flow**: It receives Chrome’s progress event details. It reads the download id and new state, finds the matching download in the browser session’s list, and changes that record’s state.

**Call relations**: This follows on_download_begin in the download lifecycle. BrowserDownloads.wait depends on these updates so it can stop waiting once a download reaches the completed state.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: Briefly checks whether an action that may have looked like navigation actually turned into a download. This helps nearby browser-control code decide what happened after clicking a link or opening a URL.

**Data flow**: It receives the number of downloads that existed before the action. For up to a short grace period, it repeatedly checks whether the download list has grown. It returns true if a new download appears, otherwise false.

**Call relations**: This function is used as a small timing bridge around navigation-like actions. It does not create or update downloads itself; it watches the list populated by on_download_begin.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits until at least one browser download has finished, then returns the most recent completed one. This lets callers know which download Chrome completed without reading the file bytes here.

**Data flow**: It receives arguments that may include a timeout. It converts that timeout into a number, repeatedly checks the browser session’s download list for completed entries, and sleeps briefly between checks. If a completed download appears, it returns the newest one. If time runs out, it raises a timeout error.

**Call relations**: This is the caller-facing wait operation for completed downloads. It relies on on_download_begin and on_download_progress to keep the download list current, and uses float_or_default to interpret the optional timeout value.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Turns a user-provided timeout-like value into a floating-point number, or uses a default when no value was provided. It keeps invalid input from being silently accepted.

**Data flow**: It receives a JSON-like value and a default number. If the value is already a number, it returns it as a float. If it is a non-empty string, it converts the string to a float. If it is missing, it returns the default. For other kinds of values, it raises a validation error.

**Call relations**: BrowserDownloads.wait uses this before it starts waiting, so the waiting loop has a clear numeric timeout to compare against.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: Finds the response’s content type from its headers. This lets the download logic recognize PDFs and other file types by what the server says they are.

**Data flow**: It receives a list of response headers. It searches for the Content-Type header, strips off extra details like character set, lowercases it, and returns the clean type string. If no content type is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused uses this while deciding whether a paused response should be forced into a download.

*Call graph*: called by 1 (on_fetch_paused).


### Action settling
Determines when browser actions have completed enough useful work for automation to continue safely.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigation events`

After an automated click, form fill, or navigation, the system needs to know when it is safe to inspect the page or take the next step. Waiting too little means reading a half-loaded page. Waiting too much means hanging on modern sites that constantly fetch ads, tracking beacons, or feed updates. This file solves that timing problem.

It keeps a small scoreboard of work started by the current action: foreground network requests, frames that are still loading, and whether the page has painted visible content. “Paint” means Chrome has drawn meaningful page content on screen; it is often a better sign of readiness than total network silence.

The helper `tracks_request` filters out traffic that should not delay the automation, such as images, fonts, low-priority fetches, and common analytics hosts. The `Settle` class then records starts and finishes of important requests, loading state, and paint events. Its `wait` method first gives the browser one tiny turn to run pending JavaScript tasks, then watches the scoreboard until the page is quiet, visibly painted, or a time limit is reached.

A key behavior is that painted pages get only a short extra grace period for content requests to finish. This keeps the system responsive on “never quiet” websites while still avoiding obvious early reads of empty page shells.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It ignores passive or background traffic, such as images, fonts, low-priority requests, and common analytics services, so automation does not wait for work that does not matter to the user-visible result.

**Data flow**: It receives the event details for a network request. It checks the request type, priority, and web address host name. It returns `True` when the request looks like foreground page work, and `False` when it looks like a passive resource, a low-priority fetch, or analytics traffic.

**Call relations**: When `Settle.on_request_started` sees a new browser request, it asks this helper whether the request should count. This keeps the main settling state focused only on requests that are likely to affect the page result of the current action.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker. It starts with no pending requests, no counted request starts, no loading sessions, and no painted sessions.

**Data flow**: It takes no outside data besides the new object being created. It prepares four pieces of memory: tracked pending requests, a count of started tracked requests, sessions still loading, and sessions that have painted. The result is a ready-to-use `Settle` object.

**Call relations**: Browser session setup creates this object so later browser events have somewhere to record request, loading, and paint state. The call graph also shows it being used during browser session close, which means the session can replace or refresh this tracker when shutting down.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the per-action settling state before a new action is measured. It removes remembered pending requests and paint events, and resets the count of tracked requests that started.

**Data flow**: It reads and changes the existing `Settle` object. Pending requests are emptied, the started-request counter goes back to zero, and painted sessions are forgotten. The loading set is deliberately not cleared here, so an already-known loading state can continue to be respected.

**Call relations**: No direct caller is shown in the provided graph, but this is the reset button for starting a new settling window. Other event hooks then refill the tracker as the browser reports fresh activity.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records the start of a browser request if it is part of the useful foreground work caused by the current action. This lets the waiter know that the page is still busy with something worth waiting for.

**Data flow**: It receives a browser event and a browser session identifier. If the session identifier and request identifier are valid, it asks `tracks_request` whether this request matters. Important requests are added to the pending set, and the started-request counter is increased.

**Call relations**: This method is called by the browser-event side of the system when Chrome reports that a request began. It relies on `tracks_request` to filter noise before `Settle.wait` later checks the pending set to decide whether the page is still busy.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tracked browser request as finished. This is how the settling tracker learns that one piece of page work no longer needs to delay the next action.

**Data flow**: It receives a browser event and a session identifier. If both the session and request identifier are usable, it removes that request from the pending set. It does not fail if the request was never tracked; it simply ensures it is no longer pending.

**Call relations**: This method is meant to be called when the browser reports that a request completed or otherwise ended. `Settle.wait` observes the shrinking pending set and can continue once the important requests have drained.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as currently loading a document. This prevents the system from moving on while navigation is still underway.

**Data flow**: It receives a session identifier and adds it to the set of loading sessions. After that, calls to `Settle.wait` treat that session as not yet ready.

**Call relations**: This is a state hook for browser lifecycle events, such as navigation starting. Once marked, `Settle.wait` keeps polling until a matching loaded event removes the session or a time limit is reached.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as no longer loading. This tells the settling logic that document loading itself is no longer a reason to wait.

**Data flow**: It receives a session identifier and removes it from the loading set. If the session was not marked as loading, nothing harmful happens.

**Call relations**: This pairs with `Settle.mark_loading`. Browser lifecycle events call these methods around navigation, and `Settle.wait` uses the resulting loading set as part of its ready/not-ready decision.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted visible page content. Paint is treated as a strong sign that the user-facing page has appeared.

**Data flow**: It receives a session identifier and adds it to the painted set. Later waiting logic can see that paint happened and switch to a shorter post-paint waiting period.

**Call relations**: This method is meant to be called from Chrome lifecycle events such as first contentful paint or first meaningful paint. `Settle.wait` reacts to this mark by handing off to `_drain_after_paint` instead of waiting for long network silence.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears complete enough to continue, or until a safety time limit is reached. It balances correctness and speed: wait for real page work, but do not hang forever on noisy sites.

**Data flow**: It receives a Chrome DevTools Protocol connection, a session identifier, and a maximum number of seconds to wait. First it asks `_flush_page_tasks` to let immediate page JavaScript run. Then it watches paint, loading, and pending request state until the page is quiet, briefly quiet after a chain gap, painted with grace time, or past the deadline. It returns no value; its effect is the time it spends waiting before allowing the caller to proceed.

**Call relations**: This is the main entry point of the file’s settling logic. It calls `_flush_page_tasks` before judging the state, uses ordinary short sleeps while polling, and calls `_drain_after_paint` when a paint event says the page is visible but may still be filling in content.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After the page has painted, waits a short extra time for important remaining requests to finish. This avoids declaring a page ready while its visible shell is still fetching content, but also avoids waiting the full timeout on pages that never become network-idle.

**Data flow**: It receives a session identifier and the overall deadline. It creates a shorter grace deadline, no later than the main deadline. During that grace period, it waits while the session is loading or important requests are pending, then checks for a brief quiet gap before returning. If the grace time runs out, it returns anyway.

**Call relations**: `Settle.wait` calls this as soon as it sees that the session has painted. This helper is the post-paint path: it gives the page a final chance to settle, then hands control back so automation can continue.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the page one quick turn to run JavaScript tasks that were queued by the action. This helps ensure that any immediate network requests triggered by click handlers or timers have already been reported before settling decisions are made.

**Data flow**: It receives a Chrome DevTools Protocol connection and a session identifier. It sends a small JavaScript expression to the page that waits for a zero-delay timer, then waits for that promise to finish. If the browser command fails or times out, it sleeps briefly instead. It returns no data, but it gives browser events time to arrive in order.

**Call relations**: `Settle.wait` calls this before checking pending requests or loading state. It hands the work to `Cdp.send`, which talks to Chrome; on protocol errors, it falls back to a short sleep so the larger waiting flow can still proceed safely.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Tab lifecycle
Manages Chrome tab state, navigation, switching, opening, and closing through the DevTools Protocol.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling and browser event handling`

A browser automation system needs more than one command that says “go to this page.” It must know which tabs exist, notice when pages open or close by themselves, wait until navigation has really settled, and report useful tab details back to the rest of the program. This file provides that layer.

It talks to the browser through Chrome DevTools Protocol, often called CDP, which is a message-based control channel for Chrome-like browsers. The file defines a small `Tab` object for one open page, plus `BrowserTabs`, which is the main controller. `BrowserTabs` listens to target and page events from the browser, records newly created or destroyed tabs, attaches to new tabs so commands can be sent to them, and enables page, DOM, network, lifecycle, and download-related features for each tab.

Navigation is careful. URLs are normalized so plain text like `example.com` becomes `https://example.com`. Special words like `back` and `forward` move through browser history instead. After navigation starts, the code waits for page events and for a settling helper, so the rest of the system does not act too early. It also treats document downloads as a special case, because a download may look like a failed page navigation even though it is expected behavior.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It preserves special commands like `back`, `forward`, and already complete URLs, while adding `https://` to plain host names.

**Data flow**: It receives a text URL or command. It checks whether the text is a special browser command, `about:blank`, or already begins with a URL scheme such as `http:`. It returns the original text in those cases, otherwise it returns the same text with `https://` added at the front.

**Call relations**: Navigation calls this first so later code can work with one clean target value. After this function decides what kind of destination it is, `BrowserTabs.navigate` chooses between history movement and normal page loading.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one browser tab. It stores the browser's target and session identifiers, plus per-tab state such as keyboard status and known page frames.

**Data flow**: It receives a target ID and a session ID from the browser. It saves them, creates a fresh keyboard state, starts an empty frame sequence map, and creates an initial frame node for the top-level page. The result is a `Tab` object ready for later browser commands.

**Call relations**: `BrowserTabs.attach_tab` calls this after successfully attaching to a browser target. From then on, other methods use the `Tab` object as the local handle for that open page.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a small stable number. This is useful when frame IDs from the browser are long or hard for humans and tools to work with.

**Data flow**: It receives a browser frame ID. If that frame has already been seen, it returns the number previously assigned to it. If not, it assigns the next available number, stores it, and returns it.

**Call relations**: This is a helper on `Tab` for other page or frame-related code. It does not call out to the browser; it only keeps local numbering consistent.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Describes the required method for sending a command to the browser over CDP. This is an interface promise, not an implementation in this file.

**Data flow**: A caller provides a CDP method name, optional parameters, and optionally a session ID for a specific tab. The real connection sends that request to the browser and returns a dictionary-like JSON result.

**Call relations**: `BrowserTabs` relies on this method for almost every browser action, such as creating targets, attaching to tabs, enabling page features, navigating, and closing tabs. The actual connection object is supplied by the surrounding browser session.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Describes the required method for preparing to wait for one or more browser events. It is like telling the connection, “wake me when this happens.”

**Data flow**: A caller names one or more CDP events and may limit them to a tab session. The real connection returns a future, which is a placeholder for an event result that will arrive later.

**Call relations**: Navigation code uses this before sending commands that should trigger page events. That ordering prevents missing an event that fires quickly.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Describes the required method for waiting for an expected browser event with a time limit. The time limit keeps automation from hanging forever.

**Data flow**: It receives a future created by `expect` and a timeout in seconds. The real connection waits until the event arrives or the timeout is reached, then returns the event data or raises an error.

**Call relations**: `BrowserTabs._goto` and `BrowserTabs._history_step` use this after starting navigation. The implementation belongs to the connection object outside this file.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Describes how `BrowserTabs` gets the CDP connection for sending browser commands. This is part of the session interface expected by this file.

**Data flow**: It takes the surrounding browser session object and returns a connection object that can send commands, expect events, and wait for events.

**Call relations**: Most methods in `BrowserTabs` call this when they need to talk to the browser. The actual session class elsewhere provides the concrete connection.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Describes how `BrowserTabs` gets the download tracker. This lets navigation tell the difference between a real page-load failure and a link that turned into a file download.

**Data flow**: It reads the browser session and returns an object that can inspect download activity. No browser command is sent by this protocol method itself.

**Call relations**: `BrowserTabs._goto` uses this when a navigation reports an error, because the error may simply mean the browser started downloading a document instead of displaying one.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Describes how code can run JavaScript inside a tab. This file uses it to ask the page for simple facts like its URL and title.

**Data flow**: It receives a session ID and a JavaScript expression. The real session evaluates that expression in the browser tab and returns the JSON-like result.

**Call relations**: `BrowserTabs.tab_info` calls this to build human-readable tab summaries. The implementation is provided by the wider browser session.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed before this tab controller started watching. This prevents old tabs from being mistaken for newly opened tabs.

**Data flow**: It receives the browser's target listing as JSON-like data. It extracts each target ID and stores the set in the session's tab event state. The browser itself is not changed.

**Call relations**: Event setup code can call this before processing target-created events. Later, `on_target_created` compares new events against this saved set.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports a new page target and queues it to become a tracked tab. It filters out non-page targets and targets that were already known.

**Data flow**: It receives event parameters from the browser. If they describe a new page target with a string target ID, it appends that ID to the session's created-target queue. Otherwise it leaves state unchanged.

**Call relations**: This is called from the browser event stream. `BrowserTabs.sync` later consumes the queued target IDs and attaches to them.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports that a target has closed. It remembers the target ID so the local tab list can be cleaned up.

**Data flow**: It receives event parameters from the browser. If a string target ID is present, it adds that ID to the destroyed-target set. No tab is removed immediately here.

**Call relations**: This event callback records the fact quickly. `BrowserTabs.sync` later removes matching tabs from the local list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when the browser says the top-level frame has started loading. It ignores subframes, such as embedded iframes, so the main page state stays meaningful.

**Data flow**: It receives page event parameters and a session ID. If the event belongs to the tab's top-level frame, it tells the settling tracker that this session is loading. Otherwise nothing changes.

**Call relations**: Browser page events call this during navigation. It uses `is_top_level_frame` to avoid treating every embedded frame as a full page navigation.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having reached DOM content loaded. In plain terms, the page's basic document structure is ready, even if images or later scripts may still be running.

**Data flow**: It receives event parameters and a session ID. If there is a session ID, it tells the settling tracker that the session has loaded its document content. It does not return data.

**Call relations**: This is part of the event-driven settling story used after navigation. Later waits consult the settling tracker before saying the page is ready enough.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports an important visual lifecycle event. Painted means the page has drawn something useful on screen.

**Data flow**: It receives lifecycle event data and a session ID. It only acts when the event name is one of the paint-related events and the frame is the top-level frame. Then it updates the settling tracker for that session.

**Call relations**: This event callback works with `on_frame_loading` and `on_dom_content` to decide when navigation has settled. It calls `is_top_level_frame` to keep subframe paint events from misleading the tracker.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main page frame of a known tab. This helps separate whole-page events from events inside embedded frames.

**Data flow**: It receives a session ID and a frame ID. It compares them against the tracked tabs, where a tab's target ID represents its top-level frame. It returns true if a matching tab is found, otherwise false.

**Call relations**: `on_frame_loading` and `on_lifecycle` call this before updating page readiness state. It is a small guard that keeps settling decisions accurate.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects this automation layer to an existing browser page target and prepares it for use. Without this, the system might know a tab exists but would not be able to control it properly.

**Data flow**: It receives a browser target ID. It asks the browser to attach, reads the new session ID, initializes that session, enables document-fetch handling for downloads, applies the configured viewport size, and returns a new `Tab` record.

**Call relations**: `sync`, `page`, and `create` call this whenever a target must become a usable tracked tab. It delegates basic page setup to `init_session` and then constructs the `Tab`.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for a tab session. These include page events, lifecycle events, document access, and network events.

**Data flow**: It receives a session ID. It sends several setup commands to the browser for that session. It returns nothing, but after it finishes the tab can report navigation, DOM, lifecycle, and network activity.

**Call relations**: `attach_tab` calls this right after the browser creates a session for a target. It is the setup checklist before the tab is added to normal use.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list back in line with browser events that have already happened. It removes closed tabs and attaches to newly opened tabs.

**Data flow**: It reads the queued created and destroyed target IDs from the session's tab event state. It removes tabs whose targets were destroyed, then processes newly created targets one by one, attaching to each unless it is already known or attachment fails. It updates the session's tab list and event queues.

**Call relations**: `page` and `tabs_context` call this before they rely on the tab list. It is the bridge between asynchronous browser events and the local `tabs` array.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be used for an operation. If no tabs are open, it creates a blank one so callers always have a page to work with.

**Data flow**: It receives an optional tab index. It first synchronizes tab state, creates and attaches a blank tab if the list is empty, then returns either the latest tab or the requested tab. If the requested index is outside the open tab list, it raises a validation error.

**Call relations**: `navigate` and `close` call this to resolve user input into a real `Tab`. It may call `sync` and `attach_tab` along the way.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new destination, or through its history, and waits until the page is ready enough for later automation. This is the main navigation entry point in this file.

**Data flow**: It receives a URL or command and an optional tab index. It selects the tab, normalizes the destination, resets the settling tracker, performs either a back/forward history step or a normal page load, waits for settling, then returns the tab's current URL and title.

**Call relations**: `create` uses this after opening a new tab, and other callers can use it directly to browse. It hands off to `_goto` for normal URLs, `_history_step` for browser history, and `tab_info` for the final summary.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work of navigating one tab to one URL. It watches for the page's content-loaded event and handles the special case where navigation becomes a download.

**Data flow**: It receives a `Tab` and a normalized URL. It notes how many downloads existed, starts waiting for a DOM-content event, sends the browser navigation command, checks for browser error text, and waits for the load event when appropriate. If the browser reports an error but a download started, it treats that as acceptable.

**Call relations**: `navigate` calls this for ordinary destinations. This method does the direct CDP navigation work, while `navigate` adds URL normalization and final settling.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browser history if that step is possible. If there is no entry in that direction, it simply does nothing.

**Data flow**: It receives a `Tab` and a step value, usually -1 for back or 1 for forward. It asks the browser for history entries, calculates the target index, and if valid tells the browser to navigate to that history entry. It waits for either a full frame navigation or an in-document navigation event.

**Call relations**: `navigate` calls this when the normalized destination is `back` or `forward`. It uses browser history data directly instead of loading a new URL string.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Returns the current URL and title for a tab. This gives the rest of the system a simple, human-friendly description of where the tab is.

**Data flow**: It receives a `Tab`. It runs a small JavaScript expression in that tab to read `location.href` and `document.title`, validates the result as a map, and returns a dictionary with string `url` and `title` values.

**Call relations**: `navigate`, `tabs_context`, and `tab_titles` use this whenever they need up-to-date tab details. It depends on the session's JavaScript evaluation service.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a summary of all open tabs, including which one is considered current. This is useful for reporting browser state to callers or tools.

**Data flow**: It first synchronizes the tab list. It treats the last tab as current, then visits each tracked tab, asks for its URL and title, and returns a dictionary containing the current tab index and a list of tab summaries.

**Call relations**: `close` calls this after removing a tab so the caller gets the updated browser state. It uses `sync` to avoid stale tabs and `tab_info` for each tab's details.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Collects just the page titles for all tracked tabs. This is a compact view when callers do not need full tab details.

**Data flow**: It reads the current tab list, asks each tab for its info, extracts the title, converts missing titles to an empty string, and returns the list of title strings.

**Call relations**: This is a convenience method built on `tab_info`. Unlike `tabs_context`, it does not first synchronize the tab event queue.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and navigates it to the requested URL. It returns the new tab's index plus its final URL and title.

**Data flow**: It receives an optional URL, defaulting to a blank page. It asks the browser to create a blank target, attaches to that target, adds the resulting `Tab` to the tracked list, navigates that tab to the requested URL, and returns the new tab ID with tab information.

**Call relations**: This is the high-level “new tab” operation. It uses `attach_tab` for setup and then reuses `navigate` so new-tab loading behaves like normal navigation.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a selected browser tab and returns the updated list of tabs. It also clears out-of-process frame session mappings, because closing a tab can invalidate those extra sessions.

**Data flow**: It receives argument data that may contain a tab ID. It converts that value to an index, resolves it to a real tab, sends the browser close command for that tab's target, removes the tab from the local list, clears out-of-process session state, and returns the new tab context.

**Call relations**: Callers use this for the high-level “close tab” operation. It relies on `_tab_id` to parse input, `page` to find the tab, and `tabs_context` to produce the final state.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loosely typed tab ID from input data into either an integer index or no selection. This lets callers pass tab IDs as numbers or strings.

**Data flow**: It receives a JSON-like value. If the value is an integer, it returns it. If it is a float, it truncates it to an integer. If it is a non-empty string, it parses it as an integer. For anything else, it returns `None`.

**Call relations**: `BrowserTabs.close` uses this before asking `page` for the tab to close. It keeps input parsing separate from the actual close operation.

*Call graph*: called by 1 (close).

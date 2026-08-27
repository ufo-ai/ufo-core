# Chrome Session and CDP Infrastructure  `stage-12.1.1`

This stage is the browser engine underneath the rest of the system. It is shared support used whenever the agent needs to drive Chrome during a work turn. The session file is the hub: it opens and owns one live Chrome connection, remembers state such as open tabs and downloads, and routes requests to helper parts.

The cdp file is the main communication pipe. It uses a WebSocket, a two-way network connection, to send Chrome DevTools Protocol commands and receive events. The wire file defines the expected message shapes, so bad browser messages are caught early. Tabs turns simple requests like “open,” “switch,” or “go to this address” into those low-level Chrome commands. Runtime runs small pieces of JavaScript inside the page and reports page errors as normal Python failures.

Other helpers keep automation from getting stuck. Dialogs answers alerts and confirmation boxes. Downloads watches for files and can force tricky items like PDFs to save as real files. Settle decides when a page is ready enough to continue, ignoring background noise while waiting for meaningful loading and visual changes.

## Files in this stage

### Live Session Orchestration
Defines the central browser session that owns Chrome connectivity, state, and delegation to browser helpers.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `per-turn browser session, from open through browser actions to close`

A browser session is like the control desk for one turn of browser use. It connects to Chrome through CDP, the Chrome DevTools Protocol, which is Chrome’s remote-control interface. Without this file, the system would not have one reliable place to open the browser connection, listen for browser events, track tabs and downloads, or expose actions like navigating, reading a page, filling a form, clicking, or waiting for a download.

The session starts with basic state: known tabs, downloads, frame sessions, dialog messages, and a “settle” tracker that watches whether the page is still loading. When opened, it finds Chrome’s WebSocket address, connects to it, configures downloads, registers event listeners, discovers tabs, and creates a fresh blank tab to work from. Event listeners are important because Chrome reports things asynchronously: downloads progress later, tabs appear or disappear later, and dialogs may pop up unexpectedly.

Most public methods here are thin front doors. They create a specialist helper, such as `BrowserTabs` for tabs, `BrowserContent` for page text, `BrowserForms` for form input, or `BrowserRuntime` for JavaScript, then hand the request over. This keeps the session as the shared memory and wiring layer while the detailed browser behavior lives in focused files. Closing the session cleans up tabs, cancels background tasks, drops the connection, and resets state so no old turn leaks into the next one.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the settings it will need later, such as the Chrome endpoint, screen size, and download folder. It does not connect to Chrome yet.

**Data flow**: It receives an optional CDP endpoint, an optional model name, and a download directory. It chooses the coordinate size the model expects, then fills in blank session state: no connection, no tabs, no downloads, no dialogs, a fresh loading tracker, and an empty set of background tasks. The result is a ready-but-not-open session object.

**Call relations**: The browser backend creates this session when it is preparing a browser surface. During construction it creates helper state such as the settle tracker and tab event tracker, but the real browser connection is left for `BrowserSession.open`.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the session if it is not already open. It protects callers from opening the same browser connection twice.

**Data flow**: It checks whether a connection already exists. If there is none, it runs the startup sequence; if startup fails at any point, it closes and resets anything that may have been partly opened, then passes the error upward.

**Call relations**: The async context manager entry method calls this when code enters `async with BrowserSession`. It hands the real setup to `BrowserSession._bootstrap` and uses `BrowserSession.close` as cleanup if setup goes wrong.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Performs the actual browser startup work: connects to Chrome, registers browser event listeners, enables downloads, discovers tabs, and creates the initial blank tab.

**Data flow**: It reads the CDP endpoint and headers, resolves them to a WebSocket URL, opens the Chrome control connection, asks Chrome for version information, and records whether the browser appears to be on macOS. It then creates tab, download, and dialog helpers, attaches their event callbacks to Chrome events, enables target discovery and download events, and finally creates and attaches a new blank tab. After this, the session has a live connection and one usable tab.

**Call relations**: `BrowserSession.open` calls this as the main startup step. It builds temporary helper readers through `tab_reader`, `download_reader`, and `dialog_reader`, uses the CDP connection to talk to Chrome, and stores the resulting tab list back on the session.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the browser session and clears all per-session memory. This prevents old tabs, downloads, dialogs, and background tasks from leaking into later work.

**Data flow**: If a connection exists, it tries to close each known Chrome target, ignoring common close-time errors, then closes the CDP connection. Whether or not that succeeds, it clears the connection, tabs, frame sessions, downloads, dialog list, scrolling flag, and background task set, and replaces trackers with fresh ones.

**Call relations**: The async context manager exit method calls this at normal shutdown. `BrowserSession.open` also calls it after a failed startup so the object returns to a clean state.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets the session be used with Python’s async context-manager pattern, which means setup and cleanup can be paired automatically.

**Data flow**: It receives the session object, opens it, and returns the same session for use inside the `async with` block.

**Call relations**: Code that uses `async with BrowserSession(...)` enters through this method. It delegates all startup work to `BrowserSession.open`.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the browser session when an async context-manager block finishes, even if the block ended with an error.

**Data flow**: It receives any exception information from the context-manager protocol, ignores the details, and closes the session. Its visible effect is cleanup rather than a returned value.

**Call relations**: This is the paired cleanup step for `BrowserSession.__aenter__`. It delegates the actual shutdown and reset work to `BrowserSession.close`.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live Chrome connection, or raises a clear error if the browser has not been opened. This gives helper classes a safe way to require an active browser.

**Data flow**: It reads the session’s stored connection. If the connection is missing, it raises `BrowserUnavailable`; otherwise it returns the `CdpConnection` object.

**Call relations**: Other browser helpers can call this when they need to send commands to Chrome. It acts as a guardrail so code fails with a useful message instead of trying to use `None` as a connection.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so the session can cancel it later. This is useful for browser work that must continue while the main request moves on.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session’s background-task set, and arranges for the task to remove itself from the set when it finishes.

**Call relations**: Session helpers can use this when they need side work to run in parallel. `BrowserSession.close` later cancels any still-running tasks during cleanup.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame belongs to the main page rather than being an embedded subframe. This matters because page events can come from many frames, not all of which represent the main document.

**Data flow**: It receives a Chrome session id and frame id, creates a tab helper, and asks that helper to decide whether the frame is top-level. It returns a true-or-false answer.

**Call relations**: This method is a session-level shortcut into tab logic. It relies on `BrowserSession.tab_reader`, keeping frame classification in the tab subsystem.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached Chrome session so it is ready for browser automation. A Chrome session here means a control channel for a tab or frame.

**Data flow**: It receives a session id, creates a tab helper, and asks it to perform the setup for that session. The main effect is in Chrome and in the session’s stored tab/frame state.

**Call relations**: This forwards session initialization to `BrowserTabs`, where tab-specific setup belongs. It is used when new browser targets or frame sessions need to be prepared.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be used for an operation, either a requested tab or the current/default tab. This gives callers a simple way to find the working page.

**Data flow**: It receives an optional tab id, creates a tab helper, and asks it to resolve that id into a `Tab` object. The result is the selected tab.

**Call relations**: This is a front door into tab selection. It delegates to `BrowserSession.tab_reader` so tab lookup rules stay in the tab helper.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new URL. This is the session-level method for telling the browser to go somewhere.

**Data flow**: It receives a URL and an optional tab id, creates a tab helper, and asks it to navigate the chosen tab. It returns a JSON-style result describing the navigation outcome.

**Call relations**: Browser tools call this when a user or model requests page navigation. The method hands off to `BrowserTabs`, which knows how to send the right Chrome commands and update tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a small information record for a tab, such as details needed to describe it to callers. It turns an internal tab object into a plain JSON-style answer.

**Data flow**: It receives a `Tab`, creates a tab helper, and asks that helper to format the tab’s information. It returns a dictionary-like JSON object.

**Call relations**: This is part of the session’s tab surface. It delegates formatting and tab-specific knowledge to `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns a summary of the browser’s tab situation. This helps callers understand what tabs exist and which one they may be working with.

**Data flow**: It creates a tab helper, which reads the session’s tab list and related state, then returns that information as a JSON-style dictionary.

**Call relations**: This method exposes tab context while keeping the details in `BrowserTabs`. It is useful before or after tab operations when the caller needs orientation.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of the open tabs. Titles are a human-friendly way to identify pages.

**Data flow**: It creates a tab helper, which reads or asks Chrome for tab titles, then returns them as a list of strings.

**Call relations**: This is a convenience wrapper around `BrowserTabs`. The session offers the method, but the tab helper does the browser-specific work.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a `BrowserTabs` helper, the specialist object for tab and target work. A target is Chrome’s term for a controllable thing like a tab.

**Data flow**: It uses the current session and the fixed viewport size to construct a new tab helper. The helper can then read and update the session’s shared tab state.

**Call relations**: Many session methods call this whenever they need tab behavior, including startup, navigation, tab creation, tab closing, and tab lookup. It is a small factory method that keeps helper construction consistent.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a `BrowserPage` helper, which understands page structure, frames, and element references. It is used when the system needs to map a page reference to a real frame or point.

**Data flow**: It combines the current session, viewport size, and maximum frame depth into a page helper. The returned helper can inspect page/frame data through the session.

**Call relations**: `BrowserSession.resolve_ref` and `BrowserSession.ref_point` use this helper. This keeps page-structure logic outside the central session object.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a `BrowserContent` helper, the specialist for reading page content and searching within it.

**Data flow**: It wraps the current session in a content helper and returns that helper to the caller. The helper can then use the session’s browser connection and page state.

**Call relations**: Content-facing methods such as `tree`, `read_page`, `get_page_text`, and `find` call this. The session provides access, while `BrowserContent` performs the content work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a `BrowserDownloads` helper, which tracks and waits for browser downloads. Downloads are reported by Chrome as events over time, not as one immediate answer.

**Data flow**: It combines the session and the maximum wait time into a downloads helper. The helper reads and updates the session’s download list.

**Call relations**: Startup uses this helper to attach download event listeners. `BrowserSession.wait_for_download` uses it later when a caller wants to wait until a download finishes.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a `BrowserDialogs` helper, which records JavaScript dialogs such as alerts or confirms. These pop-ups can interrupt normal page automation.

**Data flow**: It wraps the session in a dialog helper and returns it. The helper can append dialog messages to the session’s dialog list when Chrome reports them.

**Call relations**: `BrowserSession._bootstrap` creates this helper while wiring Chrome’s dialog-opening event. After that, dialog events are routed into the session state.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a `BrowserForms` helper, the specialist for filling inputs and uploading files. Forms need special browser commands because they interact with page elements and local file attachments.

**Data flow**: It wraps the current session in a form helper and returns it. That helper can use the browser connection and shared session state to perform form actions.

**Call relations**: The session’s `upload_file`, `attached_sizes`, and `form_input` methods call this. It keeps form-specific behavior out of the session wiring layer.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a `BrowserRuntime` helper, which runs JavaScript in the browser. This is used for lower-level page inspection or actions that need code execution inside Chrome.

**Data flow**: It wraps the current session in a runtime helper and returns it. The helper can then send JavaScript evaluation or function-call commands through the active connection.

**Call relations**: `BrowserSession.eval_js` and `BrowserSession.call_on` use this helper. The session exposes the operation, while runtime details live in `BrowserRuntime`.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page if no URL is given. This lets the caller expand the browser workspace.

**Data flow**: It receives a URL, creates a tab helper, and asks it to create a tab at that URL. It returns a JSON-style description of the created tab or operation result.

**Call relations**: This is part of the tab tool surface. It forwards the request to `BrowserTabs`, which talks to Chrome and updates session tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab according to the caller’s arguments. This removes pages the system no longer needs.

**Data flow**: It receives a JSON-style argument object, creates a tab helper, and asks it to close the selected tab. It returns a JSON-style result describing what happened.

**Call relations**: This method is the session-level close-tab action. It depends on `BrowserTabs` for interpreting the arguments and sending the close command to Chrome.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads or attaches a file through a page form. This supports web pages that ask the user to choose a file.

**Data flow**: It receives JSON-style upload arguments, creates a form helper, and asks it to perform the upload. It returns a JSON-style result from the form helper.

**Call relations**: The browser backend calls this during upload settlement. The method routes the request to `BrowserForms`, where file-input details are handled.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached during an upload flow. This helps the caller confirm what was attached without reading the file contents here.

**Data flow**: It receives JSON-style arguments, creates a form helper, and asks it to calculate or retrieve attached file sizes. It returns a list of integer sizes.

**Call relations**: The browser backend calls this as part of upload settlement after or around `upload_file`. The form helper performs the file-related inspection.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text representation of the page’s structure, optionally filtered by type. This gives callers an overview of what is on the page.

**Data flow**: It receives JSON-style arguments and a filter type, creates a content helper, and asks it to build the page tree. It returns the tree as a string.

**Call relations**: This is one of the page-reading actions exposed by the session. `BrowserContent` does the actual page inspection and formatting.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured result suitable for the rest of the system. It is the broader page-understanding entry point.

**Data flow**: It receives JSON-style arguments, creates a content helper, and asks it to read the page. It returns a JSON-style dictionary containing the page-reading result.

**Call relations**: Callers use this when they need more than raw text. The session forwards the work to `BrowserContent`, which gathers and formats the content.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts readable text from the page. This is useful when the caller wants the words on the page rather than the full page structure.

**Data flow**: It receives JSON-style arguments, creates a content helper, and asks it to collect page text. It returns a JSON-style dictionary containing that text result.

**Call relations**: This sits on the session’s content tool surface. It delegates to `BrowserContent`, which knows how to inspect the page through Chrome.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within the page content, optionally using a completion helper for find results. This helps locate text or elements without reading the whole page manually.

**Data flow**: It receives JSON-style search arguments and an optional `FindCompleter`, which is a helper for finishing or enriching search results. It creates a content helper, passes both inputs to it, and returns a JSON-style search result.

**Call relations**: This is the session-level find action. It hands the actual search work to `BrowserContent`, keeping search behavior separate from connection setup.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Fills or edits form fields on the page. This supports actions such as typing into text boxes or choosing form values.

**Data flow**: It receives JSON-style form arguments, creates a form helper, and asks it to perform the input action. It returns a JSON-style result describing the outcome.

**Call relations**: This method exposes form input through the session. `BrowserForms` interprets the arguments and performs the browser interaction.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs computer-like browser actions such as mouse or keyboard interaction within the page viewport. This is the lower-level interaction surface for acting on the page visually.

**Data flow**: It receives JSON-style action arguments, creates a `BrowserComputer` with the session, viewport, and wait limit, and runs it. It returns a JSON-style result from that run.

**Call relations**: Unlike the reader factory methods, this constructs `BrowserComputer` directly for the call. The session provides shared browser state while `BrowserComputer` performs the visual or input action.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to complete and returns the download record. The file is not read here; the session reports where Chrome wrote it and what happened.

**Data flow**: It receives JSON-style wait arguments, creates a downloads helper, and asks it to wait up to the configured limit. It returns a `BrowserDownload` record when the download is ready or the helper decides the wait is over.

**Call relations**: This method relies on download events that were wired during `_bootstrap`. It delegates waiting and matching logic to `BrowserDownloads`.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a specific Chrome session. This is a low-level escape hatch for inspecting or changing the page from within the browser.

**Data flow**: It receives a Chrome session id and a JavaScript expression, creates a runtime helper, and asks it to evaluate the expression. It returns the JSON-style value reported by Chrome.

**Call relations**: This forwards JavaScript evaluation to `BrowserRuntime`. Higher-level helpers can use it when page data or behavior is easiest to access through JavaScript.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side object. This is used when Chrome has already given the system a reference to an object in the page.

**Data flow**: It receives a session id, an object id, a JavaScript function body or name, and optional arguments. It creates a runtime helper, asks it to perform the function call in Chrome, and returns a JSON-style result.

**Call relations**: This is the companion to `eval_js` for object-based runtime work. It delegates the detailed Chrome Runtime command to `BrowserRuntime`.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Resolves a page reference string into the frame and index it points to. A reference is a compact label the system uses for something found on the page.

**Data flow**: It receives a tab and a reference string, creates a page helper, and asks it to interpret the reference. It returns the matching frame node and numeric index.

**Call relations**: This method is used when a later action needs to turn a human- or model-facing reference back into page structure. `BrowserPage` owns the reference-decoding rules.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the screen point for a referenced page item. This lets the system turn a page reference into coordinates it can click or move to.

**Data flow**: It receives a tab and a reference string, creates a page helper, and asks it to calculate the point. It returns an `(x, y)` coordinate pair.

**Call relations**: This builds on page-reference logic in `BrowserPage`. Visual interaction code can use the returned point when it needs to act on a referenced element.

*Call graph*: calls 1 internal fn (page_reader).


### Browser State Domains
Manages user-visible browser state such as downloads and tabs through CDP-backed actions.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download waiting`

This file sits between browser events and the rest of the automation system. Chrome can pause network requests, report when a download starts, and report download progress. This code turns those raw browser signals into a simple list of downloads with an id, filename, and state.

The main class, BrowserDownloads, has two jobs. First, it must never leave a paused browser request stuck. A paused request is like a car stopped at a toll gate: someone must wave it through, or the page hangs. Most requests are simply continued. But if the paused response is a top-level PDF document, the code changes the response headers so Chrome treats it as an attachment download instead of opening it in the PDF viewer.

Second, it records download events. When Chrome says a download began, the file is added to the session's download list. When Chrome reports progress, the matching record is updated. Other code can then ask whether navigation turned into a download, or wait until some download completes.

The file does not read downloaded file contents. It only tracks what Chrome says happened and sends Chrome DevTools Protocol commands to release paused requests.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the promised shape of the browser connection object. Anything used here must be able to send a named Chrome DevTools Protocol command, which is a browser-control message, with optional parameters and an optional session id.

**Data flow**: It receives a command name, a dictionary of command details, and possibly a browser session id. The real implementation sends that command to Chrome and returns Chrome's reply as a dictionary.

**Call relations**: BrowserDownloads relies on this method when releasing paused requests and responses. In this file it is a protocol, meaning it describes what another object must provide rather than doing the work itself.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This is the promised way to get the live browser connection from the surrounding session. BrowserDownloads uses it whenever it needs to tell Chrome to continue a paused request or response.

**Data flow**: It takes no extra input beyond the session object. The real implementation returns an object that can send Chrome DevTools Protocol commands.

**Call relations**: BrowserDownloads._continue_request and BrowserDownloads._continue_response call this before sending commands to Chrome. Here it is only an interface requirement for the session object.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the promised way to start an asynchronous task without blocking the current browser event handler. It matters because paused browser requests must be released, but the event callback itself should stay quick.

**Data flow**: It receives a coroutine, which is an async job waiting to run. The real session implementation schedules that job in the background and does not return a download result.

**Call relations**: BrowserDownloads.on_fetch_paused uses this to schedule request or response continuation. This keeps the event-handling path from waiting directly on the browser command.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This is the promised way to ask whether a paused network response belongs to the main page frame rather than something embedded inside the page. That distinction prevents embedded PDFs from being wrongly forced into downloads.

**Data flow**: It receives a browser session id and a frame id from Chrome's event data. The real implementation checks them against the current page structure and returns true only for the top-level page frame.

**Call relations**: BrowserDownloads.on_fetch_paused calls this before forcing a PDF to download. This protocol method lets the download logic depend on a simple yes-or-no answer without knowing how frame tracking works.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request or response. Its main responsibility is to release the pause, while rewriting top-level PDF responses so they become downloads instead of unreadable in-browser viewer pages.

**Data flow**: It receives Chrome's paused-fetch event data and an optional session id. It reads the request id, response code, headers, frame id, and content type. If this is only a request-stage pause, it schedules a normal continue. If it is a response-stage pause, it decides whether it is a top-level PDF and schedules a response continue, possibly with a forced download header. It returns nothing, but it starts background work that tells Chrome to move on.

**Call relations**: This is called when the browser reports a Fetch pause. It uses _content_type to understand the headers, asks the browser session whether the frame is top-level, and then hands off to _continue_request or _continue_response through the session's background task runner.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This async helper tells Chrome to let a paused request continue unchanged. It is the safe default for pauses that happen before a response is available.

**Data flow**: It receives the session id and Chrome's request id. It sends a Fetch.continueRequest command through the browser connection. If Chrome rejects the command, times out, or the runtime is shutting down, it logs a warning instead of crashing this path.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this when the paused event does not include a response status code. It talks directly to the browser connection and does not call other helpers in this file.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This async helper tells Chrome to let a paused response continue, optionally changing its headers so the browser downloads it. It is where the PDF-as-attachment decision becomes an actual browser command.

**Data flow**: It receives the session id, request id, response status code, response headers, and a true-or-false force flag. If force is false, it sends only the request id back to Chrome. If force is true, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, then sends the response code and modified headers. On browser errors or timeouts, it logs a warning.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this for response-stage pauses. It is the function that actually sends Fetch.continueResponse to Chrome after the earlier logic has decided whether to force a download.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a new download. It creates a small download record so later code can find the download by id, filename, and state.

**Data flow**: It receives Chrome's download-begin event data and an optional session id. It reads the download guid and suggested filename, falls back to the name "download" if needed, marks the state as inProgress, and appends the new record to the browser session's download list.

**Call relations**: This is called when Chrome announces a download has begun. Later, on_download_progress updates the same record, and wait or became_download can observe it in the session's download list.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the remembered state of a download as Chrome reports progress. It lets the rest of the system know when a download has completed or moved to another state.

**Data flow**: It receives Chrome's progress event data and an optional session id. It reads the download guid and new state, searches the stored downloads for the matching guid, and changes that record's state. It returns nothing.

**Call relations**: This is called after on_download_begin has created a record. BrowserDownloads.wait later depends on these updates to know when at least one download has reached the completed state.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This function briefly checks whether an action that looked like page navigation actually turned into a download. It is useful because browsers may report the download just after the navigation attempt starts.

**Data flow**: It receives the number of downloads that existed before the action. For up to a short grace period, it repeatedly compares that old count with the current download list length. If a new download appears, it returns true; if not, it returns false.

**Call relations**: Other navigation logic can call this after attempting to open something. It does not start or update downloads itself; it simply watches the list filled by on_download_begin.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This async function waits until Chrome reports that a download has completed, then returns the completed download record. It gives callers a simple way to pause until a file is ready without reading the file itself.

**Data flow**: It receives an argument dictionary that may include a timeout. It converts that timeout with float_or_default, then repeatedly looks through the session's downloads for records whose state is completed. If one appears before the deadline, it returns the most recent completed download. If none appears in time, it raises TimeoutError.

**Call relations**: Callers use this when they need to wait for a browser download to finish. It relies on on_download_begin and on_download_progress to keep the download list up to date, and it uses float_or_default to accept either numeric or string timeout values.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns an optional timeout-like value into a real floating-point number. It accepts numbers, numeric strings, or no value at all.

**Data flow**: It receives a JSON-style value and a default number. If the value is already an integer or float, it returns it as a float. If it is a non-empty string, it converts the string to a float. If it is missing, it returns the default. For other value types, it raises a validation error explaining that the value must be numeric.

**Call relations**: BrowserDownloads.wait calls this before starting its wait loop. This keeps timeout validation in one small place instead of mixing it into the download-waiting logic.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper finds the Content-Type value in a list of HTTP response headers. Content-Type is the label that says what kind of file or document the response contains, such as application/pdf.

**Data flow**: It receives a list of header-like values from Chrome. It scans for a dictionary whose name is Content-Type, ignores letter case, takes the value before any semicolon options, trims spaces, lowercases it, and returns it. If no content type is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this while deciding whether a top-level response is a PDF that should be forced into a download. It is a small parsing helper used only for that decision.

*Call graph*: called by 1 (on_fetch_paused).


### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling and browser event handling`

A browser can have many tabs, and tabs can appear or disappear outside the direct control of this code, for example when a page opens a new window. This file is the project’s tab desk clerk: it keeps a list of open tabs, notices new or closed browser targets, attaches to each tab so it can talk to it, and returns friendly summaries like each tab’s URL and title.

The main object is BrowserTabs. It uses a browser session object to send Chrome DevTools Protocol, or CDP, messages. CDP is the browser’s remote-control API. When a new tab is found, BrowserTabs attaches to it, enables page, document, network, and lifecycle events, sets the viewport size, and starts watching document downloads. It also listens for loading and painting events so the wider system can wait until navigation has settled instead of acting while the page is still changing.

Navigation is deliberately more than just “go to URL.” The file accepts plain addresses like “example.com” and turns them into “https://example.com”; it also understands “back” and “forward.” After navigation it waits for the page to become ready, then reads the current URL and title from the page. Without this file, other parts of the project would have to juggle raw browser target IDs, sessions, timing, and tab cleanup themselves.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It preserves special commands like “back” and complete URLs, and adds “https://” when someone types a bare site name.

**Data flow**: A text URL goes in. The function checks whether it is a special word, blank-page address, or already has a scheme such as “http:”. It returns either the original text or the same text with “https://” added in front.

**Call relations**: BrowserTabs.navigate calls this before deciding whether to move backward, forward, or load a normal web address.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one open browser tab. It stores the browser’s target ID, the CDP session ID, keyboard state, and known frame information.

**Data flow**: A target ID and session ID go in. The constructor builds a new Tab object with an empty frame sequence table, a fresh keyboard state, and a root frame reference tied to that target and session.

**Call relations**: BrowserTabs.attach_tab calls this after the browser accepts an attachment to a target, turning the raw browser IDs into an object the rest of this file can use.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number. This is useful when frames need human-friendly or repeatable numbering instead of long browser frame IDs.

**Data flow**: A frame ID goes in. If the tab has seen it before, the old number comes back; if not, the function assigns the next number and stores it before returning it.

**Call relations**: This is a helper on Tab for code that needs to label frames consistently. It does not call other functions in this file.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of an object that can send one command to the browser through CDP. It is a protocol method, meaning this file describes what a connection must offer without implementing the connection here.

**Data flow**: A CDP method name, optional parameters, and optional session ID go in. A real connection sends that message to the browser and returns a JSON-like dictionary response.

**Call relations**: BrowserTabs relies on connection objects with this method when attaching tabs, enabling browser features, navigating, creating tabs, and closing tabs.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how a CDP connection can start waiting for one of several browser events. It lets code prepare for an event before sending the command that should trigger it.

**Data flow**: Event names and an optional session ID go in. A real connection returns a future, which is a placeholder for a result that will arrive later.

**Call relations**: Navigation code uses this pattern so it does not miss important events such as the page reporting that its document content has loaded.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how a CDP connection waits for a previously expected event, with a time limit. The timeout prevents the program from hanging forever if the browser never reports the event.

**Data flow**: A future and timeout value go in. A real connection waits until the future completes or the timeout is reached, then returns the event data or raises an error.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step depend on this behavior after they have asked the browser to navigate.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how a browser session provides its CDP connection. BrowserTabs uses that connection as its remote control for the browser.

**Data flow**: No data is passed in besides the session object itself. The method returns an object that can send commands and wait for browser events.

**Call relations**: Nearly every active BrowserTabs operation asks the session for this connection before talking to the browser.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how a browser session provides access to download tracking. This matters because some navigations do not display a page; they start a file download instead.

**Data flow**: The session object goes in. The method returns an object that can check whether new downloads appeared.

**Call relations**: BrowserTabs._goto uses this when navigation reports an error, so it can treat “this became a download” as a successful special case rather than a failed page load.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how code can run a small JavaScript expression inside a tab. BrowserTabs uses it to read basic page facts like the current address and title.

**Data flow**: A session ID and JavaScript expression go in. The browser runs the expression in that tab and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info uses this protocol method to build user-facing tab summaries.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed when observation began. This prevents old tabs from being mistaken for newly created tabs later.

**Data flow**: A browser response containing target information goes in. The function extracts each target ID and stores the set on the session’s tab event state.

**Call relations**: This prepares the event tracker used by BrowserTabs.on_target_created, which only queues page targets that were not part of the initial set.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports a new page target, which usually means a new tab or window. It queues that target so the tab list can be updated later.

**Data flow**: Event parameters and an optional session ID go in. If the event describes a page target with a valid target ID, and it is not old or already queued, the ID is appended to the created-target list.

**Call relations**: Browser event dispatch calls this when target-created events arrive. BrowserTabs.sync later consumes the queued target IDs and attaches to the new tabs.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports that a target has gone away. It records the target ID so the local tab list can remove it safely.

**Data flow**: Event parameters and an optional session ID go in. If a target ID is present, it is added to the destroyed-target set in the session event state.

**Call relations**: Browser event dispatch calls this when target-destroyed events arrive. BrowserTabs.sync later removes matching Tab objects from the local list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. The main frame is the top page itself, not an embedded iframe.

**Data flow**: Frame-loading event parameters and a session ID go in. If the event belongs to the top-level frame of a known tab, the settle tracker is told that this session is loading.

**Call relations**: This is part of the browser event flow. It calls BrowserTabs.is_top_level_frame to avoid treating subframe activity as whole-page loading.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having loaded its document content. This is one signal used to decide when navigation has progressed far enough.

**Data flow**: DOM-content event parameters and a session ID go in. If there is a session ID, the session’s settle tracker is marked as loaded.

**Call relations**: Browser event dispatch calls this when the page fires its DOM content event. Later, BrowserTabs.navigate waits on the settle tracker before returning.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports a relevant visual lifecycle event. In plain terms, it helps tell whether the page has actually drawn something on screen.

**Data flow**: Lifecycle event parameters and a session ID go in. The function ignores unrelated events and subframes; for a top-level paint event, it marks the session as painted in the settle tracker.

**Call relations**: This event hook calls BrowserTabs.is_top_level_frame, then updates the settle tracker used by BrowserTabs.navigate.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main frame of a known tab. This stops iframe noise from being treated as full-page navigation progress.

**Data flow**: A session ID and frame ID go in. The function compares them with the known tabs and returns true only when a tab’s session ID and target ID match.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating page-settling state.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects this controller to an existing browser tab target and prepares it for automation. It is like plugging in the remote-control cable and turning on the sensors.

**Data flow**: A browser target ID goes in. The function asks the browser to attach, validates the returned session ID, initializes page-related CDP domains, enables download interception for documents, sets the viewport size, and returns a new Tab object.

**Call relations**: BrowserTabs.create, BrowserTabs.page, and BrowserTabs.sync call this whenever a target needs to become a tracked Tab. It calls BrowserTabs.init_session before constructing the Tab.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for one tab session. Without this, the code would not receive the page, DOM, lifecycle, and network information it depends on.

**Data flow**: A session ID goes in. The function sends several CDP enable commands for that session and changes no local data directly.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target, before the tab is added to the local list.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list up to date with browser events that were queued earlier. It removes closed tabs and attaches to newly created tabs.

**Data flow**: It reads the session’s created-target and destroyed-target event lists. Destroyed targets are cleared and removed from browser.tabs; created targets are popped one by one, skipped if already known, and attached as new Tab objects when possible.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before returning tab information, so callers see the current browser state. It calls BrowserTabs.attach_tab for newly discovered targets.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the requested tab, creating a blank tab if none are open. It is the safe doorway other actions use before they work with a tab.

**Data flow**: An optional tab index goes in. The function first syncs the tab list, creates and attaches a blank tab if the list is empty, then returns either the newest tab or the tab at the requested index; invalid indexes raise a validation error.

**Call relations**: BrowserTabs.navigate and BrowserTabs.close call this to find the tab they should affect. It calls BrowserTabs.sync and may call BrowserTabs.attach_tab if it must create the first tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new place: a web address, back, or forward. It waits for the page to settle before reporting the final URL and title.

**Data flow**: A destination string and optional tab index go in. The function picks the tab, normalizes the destination, resets settling state, performs either history navigation or normal navigation, waits for the page-settle tracker, and returns tab information.

**Call relations**: BrowserTabs.create calls this after opening a new tab. Internally it calls normalize_url, BrowserTabs.page, BrowserTabs._history_step or BrowserTabs._goto, and then BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work of navigating one tab to a normal URL. It also handles the important edge case where a “navigation” actually becomes a file download.

**Data flow**: A Tab and URL go in. The function starts waiting for the document-loaded event, sends the browser navigation command, checks for browser-reported errors, treats newly started downloads as acceptable, and waits for document content if the browser says a new loader exists.

**Call relations**: BrowserTabs.navigate calls this for ordinary URLs after choosing the tab and normalizing the address.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves one tab backward or forward through its browsing history. If there is nowhere to go, it quietly leaves the tab where it is.

**Data flow**: A Tab and a step number go in, where -1 means back and 1 means forward. The function reads the browser’s navigation history, calculates the desired entry, waits for a navigation event, asks the browser to jump to that entry, and waits for confirmation.

**Call relations**: BrowserTabs.navigate calls this when the normalized destination is “back” or “forward.”

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. This provides a small, friendly summary instead of exposing raw browser session details.

**Data flow**: A Tab goes in. The function runs JavaScript in that tab to read location.href and document.title, then returns a dictionary with string URL and title values.

**Call relations**: BrowserTabs.navigate, BrowserTabs.tab_titles, and BrowserTabs.tabs_context call this whenever they need human-readable tab state.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to display or reason about the tab list. It marks the last tab as the current active tab.

**Data flow**: No explicit input goes in. The function syncs the tab list, asks each tab for its URL and title, assigns each one an ID and active flag, and returns a dictionary containing the current tab ID and the full list.

**Call relations**: BrowserTabs.close calls this after closing a tab so the caller receives the updated tab situation. It calls BrowserTabs.sync and BrowserTabs.tab_info.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of the currently tracked tabs. This is a lightweight view when callers do not need full tab details.

**Data flow**: No explicit input goes in. The function walks through the current tab list, reads each tab’s info, extracts the title, and returns the titles as a list of strings.

**Call relations**: It uses BrowserTabs.tab_info for each tab. The call graph does not show another function in this file calling it.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new tab and navigates it to a requested URL. If no URL is supplied, it creates a blank tab.

**Data flow**: An optional URL goes in. The function asks the browser to create a blank target, attaches to it, adds the new Tab to the local list, navigates that tab to the requested URL, and returns the tab ID plus its final URL and title.

**Call relations**: It calls BrowserTabs.attach_tab to prepare the new target, then BrowserTabs.navigate to load the desired destination.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one tab and returns the updated tab list. It accepts flexible tab ID input so callers can pass numbers or numeric strings.

**Data flow**: A dictionary of arguments goes in. The function extracts and converts tab_id, finds the tab, sends the browser close command, removes the Tab object locally, clears out-of-process iframe session tracking, and returns fresh tab context.

**Call relations**: It calls _tab_id to interpret the input, BrowserTabs.page to find the target tab, and BrowserTabs.tabs_context after closing to report the new state.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loose JSON value into an optional integer tab ID. It lets close requests accept common forms such as 2, 2.0, or “2”.

**Data flow**: A JSON-like value or null goes in. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything else becomes None.

**Call relations**: BrowserTabs.close calls this before asking BrowserTabs.page which tab should be closed.

*Call graph*: called by 1 (close).


### CDP Communication and Execution
Provides the DevTools transport, dialog handling, in-page JavaScript execution, and safe wire message shapes.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `active during browser session startup and throughout browser control`

Chrome’s DevTools Protocol, or CDP, is a message-based remote control channel for Chrome. This file wraps that channel so the rest of the project does not have to deal with raw WebSocket messages, message IDs, timeouts, or browser event routing.

Think of it like a receptionist for one busy phone line to Chrome. When code sends a command, the receptionist gives it a numbered ticket, sends the request to Chrome, and later matches Chrome’s reply back to the right waiting caller. At the same time, Chrome can send unscheduled news, called events, such as a page loading or a target appearing. This file lets code either subscribe to those events with listeners, or wait for one specific event once.

The main class is `CdpConnection`. `open` connects to Chrome and starts a background reader task. `send` writes a command and waits for the matching response. `expect` and `wait` let another part of the system wait for a named event. `_read_loop` continuously reads incoming messages, and `_dispatch` sorts each message into one of two buckets: a response to a previous command, or an event to fan out to waiters and listeners.

A key safety feature is that timeouts and closed connections fail loudly. Without this, browser automation could hang forever waiting for a reply that will never arrive.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Creates a clear Python error for a failed Chrome DevTools Protocol command. It records which CDP method failed, the numeric error code from Chrome, and Chrome’s message.

**Data flow**: It receives the command name, an error code, and an error message from Chrome. It stores the method and code on the error object, then builds a readable message such as “CDP X failed...” that can be raised to the caller.

**Call relations**: When `_dispatch` sees that Chrome replied to a command with an `error` field, it creates this `CdpError` and attaches it to the waiting command future. That means the original caller of `send` gets a meaningful failure instead of a vague broken response.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual WebSocket address used to talk to Chrome DevTools. Callers can pass either a ready-to-use `ws://` or `wss://` URL, or an HTTP DevTools endpoint that needs to be queried first.

**Data flow**: It takes a URL and HTTP headers. If the URL is already a WebSocket URL, it returns it unchanged. Otherwise, it requests the `/json/version` endpoint from Chrome, reads the `webSocketDebuggerUrl` field from the JSON response, checks it is a string, and returns that WebSocket URL.

**Call relations**: This is a setup helper used before opening a CDP connection. It relies on `httpx.AsyncClient` to make the HTTP request and `as_str` to make sure the returned debugger URL is really text before other code tries to connect to it.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Sets up the in-memory state for one live CDP WebSocket connection. It prepares the bookkeeping needed to match outgoing commands with incoming replies and to route browser events.

**Data flow**: It receives an already-open WebSocket connection. It stores it, starts the command ID counter at zero, and creates empty collections for pending command replies, event listeners, one-time event waiters, and the background reader task reference.

**Call relations**: This constructor is used by `CdpConnection.open` after the WebSocket is created. The state initialized here is later used by `send`, `expect`, `wait`, `_read_loop`, and `_dispatch` while the connection is alive.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a WebSocket connection to Chrome and starts listening for messages immediately. This is the normal way other parts of the project create a `CdpConnection`.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to Chrome with a large allowed message size, creates a `CdpConnection` around that socket, starts `_read_loop` as a background task, and returns the ready connection object.

**Call relations**: The browser session bootstrap code calls this when it is preparing to control Chrome. It hands off the actual socket dialing to the `websockets` library and uses `asyncio.create_task` so incoming Chrome messages are read in the background while other code sends commands.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader and closes the WebSocket so no more messages are sent or received.

**Data flow**: It checks whether the reader task exists. If it does, it cancels that task, waits for its cancellation without treating the cancellation as an error, clears the task reference, and then closes the WebSocket.

**Call relations**: This is called during cleanup when browser control is finished or the session is being torn down. It uses `contextlib.suppress` so the expected cancellation of the reader task does not look like a crash.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one CDP command to Chrome and waits for that command’s matching reply. It gives callers a simple request-and-response interface over an asynchronous WebSocket.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session ID for commands aimed at a specific browser target. It assigns a new numeric message ID, stores a future under that ID, serializes the command to JSON, sends it through the WebSocket, and waits up to the command timeout. If Chrome replies successfully, the future produces the result dictionary. If Chrome reports an error or the connection closes, the future raises an exception. If nothing replies in time, the pending entry is removed and a timeout error is raised.

**Call relations**: Other browser-control code calls this whenever it needs Chrome to do something. `_dispatch`, running from the background reader, is the piece that later finds the matching message ID and completes the future that `send` is waiting on.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a callback to be called whenever a named CDP event arrives. This is for ongoing interest in an event, not just waiting once.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for that event name, creating the list if this is the first listener for that event.

**Call relations**: Code that wants continuous notifications calls `on` before or during browser operation. Later, `_dispatch` receives events from `_read_loop` and calls every listener registered for the matching event name.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one of several possible CDP events. This is useful when code knows that the next step should trigger a specific browser event and wants to pause until it happens.

**Data flow**: It receives one or more event names and, optionally, a session ID to limit the wait to one browser target. It creates a future, stores it with the event names and session filter, and returns the future to the caller.

**Call relations**: A caller typically uses `expect` before taking an action that should cause an event, then passes the returned future to `wait`. `_dispatch` later completes that future when a matching event message arrives.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for a future created by `expect`, with a timeout and cleanup. It prevents one-time event waiters from lingering forever after success, failure, or timeout.

**Data flow**: It receives an event future and a timeout value. It waits for the future to complete within that time and returns the event parameters if it succeeds. No matter what happens, it removes that future from the connection’s waiter list afterward.

**Call relations**: This is the companion to `expect`. Callers use `expect` to register what event they want, then `wait` to actually pause for it. `_dispatch` is responsible for completing the future while `wait` is waiting.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads raw messages from Chrome and passes each one to the dispatcher. It is the background worker that keeps the CDP connection alive and responsive.

**Data flow**: It reads each raw WebSocket message, parses the JSON text into a dictionary, and gives it to `_dispatch`. If the WebSocket closes, it stops reading. In all exit cases, it fails any command replies or event waits that are still pending, then clears those pending lists.

**Call relations**: `open` starts this as an `asyncio` background task. It feeds every incoming message to `_dispatch`. Its cleanup behavior is important because callers of `send` or `wait` should get an immediate error when the connection dies, rather than waiting until their timeout expires or hanging forever.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Sorts one incoming CDP message into the right destination. It either completes a waiting command response, completes one-time event waiters, or notifies ongoing event listeners.

**Data flow**: It receives a parsed message dictionary. If the message has a numeric `id`, it treats it as a reply to an earlier command, finds the pending future for that ID, and either sets a result dictionary or raises a `CdpError` if Chrome reported failure. If the message has a string `method` instead, it treats it as an event, extracts its parameters and optional session ID, completes any matching one-time waiters, removes completed waiters from the list, and calls all listeners registered for that event.

**Call relations**: `_read_loop` calls this for every message received from Chrome. It is the central traffic controller for the connection: it wakes `send` callers when command replies arrive, wakes `wait` callers when expected events arrive, and triggers callbacks registered through `on`.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

JavaScript dialogs are small pop-up boxes created by a web page. In an automated browser, they are more than an annoyance: while one is open, the page can stop sending later browser events, like a person blocking a doorway until someone answers them. This file makes sure every dialog gets an immediate answer.

The file defines two small “protocols,” which are contracts saying what the surrounding browser session must provide: a way to send commands to the browser, and a way to start background work. The main class, BrowserDialogs, applies the project’s policy. Alerts and “beforeunload” warnings are accepted because there is usually no meaningful safer choice. Confirm and prompt dialogs are dismissed so the agent does not accidentally agree to a website’s own “Are you sure?” question.

Whenever a dialog appears, BrowserDialogs records a plain text note in the browser session’s dialog list, so the rest of the system can know what happened. It then starts a background task to send the actual Chrome DevTools Protocol command. Chrome DevTools Protocol, or CDP, is the control channel used to tell the browser what to do. If answering fails because the browser has moved on, timed out, or closed, the file logs a warning instead of crashing the run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the required shape of the browser command-sending method. Anything used here as a browser connection must be able to send a named command, optional data, and an optional browser session identifier, then return a JSON-like result.

**Data flow**: It receives a command name, optional parameters, and optionally the specific browser session to target. The real implementation sends that command to the browser and returns the browser’s response as a dictionary-like JSON object.

**Call relations**: BrowserDialogs._answer_dialog relies on this contract when it tells the browser to answer a JavaScript dialog. The actual method is supplied by the larger browser connection object, not implemented in this file.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the required way for a browser session to provide access to its command connection. BrowserDialogs uses it when it needs to talk to the browser.

**Data flow**: It takes the current browser session object and returns an object that can send browser commands. It does not transform dialog data itself; it simply gives access to the communication channel.

**Call relations**: BrowserDialogs._answer_dialog calls this before sending Page.handleJavaScriptDialog. The concrete browser session elsewhere in the system supplies the real connection.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the required way for a browser session to start a small asynchronous job without waiting for it inline. BrowserDialogs uses it so dialog answering can happen immediately without blocking the event handler.

**Data flow**: It receives a coroutine, which is an asynchronous task that can be run later or alongside other work. The real session schedules that task and returns nothing directly.

**Call relations**: BrowserDialogs.on_dialog calls this with BrowserDialogs._answer_dialog. That lets the event handler record the dialog and hand off the browser command to the session’s background task runner.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog appeared. It chooses whether to accept or dismiss the dialog, records what happened, and starts the work needed to answer the browser.

**Data flow**: It receives the dialog details as a JSON-like dictionary and an optional session identifier. It reads the dialog type and message, decides whether the type should be accepted or dismissed, appends a human-readable note to the browser session’s dialog list, and schedules _answer_dialog to send the answer to the browser.

**Call relations**: This is the front door for dialog events. When the browser event stream reports a dialog, this function makes the policy decision and then hands the actual browser command to BrowserDialogs._answer_dialog through the session’s background task mechanism.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This asynchronous helper sends the actual command that closes the JavaScript dialog in the browser. It accepts or dismisses the dialog according to the decision made by on_dialog.

**Data flow**: It receives the target session identifier and a true-or-false accept choice. It asks the browser session for its connection, sends the Page.handleJavaScriptDialog command with that choice, and produces no returned value. If the command fails because of a browser control error, timeout, or runtime problem, it logs a warning instead of raising the failure further.

**Call relations**: BrowserDialogs.on_dialog creates this task after deciding what should happen to the dialog. This function then talks to the browser through BrowserDialogSession.connection and BrowserDialogCdp.send, keeping the low-level CDP command separate from the event policy.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `during browser automation commands`

Browser automation often needs to ask the page a question, such as “what is this value?” or “run this function on that page object.” Browsers expose that ability through the Chrome DevTools Protocol, a message-based control channel for inspecting and driving a browser. This file wraps the protocol’s Runtime.evaluate and Runtime.callFunctionOn commands in a cleaner Python interface.

The two Protocol classes describe the minimum shape of the browser connection this wrapper needs: something that can send a command, and something that can provide that sender. A Protocol is like a checklist: an object does not need to inherit from it, but it must provide the listed methods.

BrowserRuntime is the useful wrapper. Its eval method sends a JavaScript expression to a specific browser session and returns the plain value from the page. Its call_on method runs a JavaScript function against an existing browser-side object and can pass simple arguments. Both ask the browser to return values directly rather than remote handles.

The important safety feature is raise_on_exception. Browser protocol replies can contain exceptionDetails when JavaScript failed on the page. Without checking this, the caller might treat a broken page script as a successful empty result. This file makes that failure loud and clear by raising RuntimeError.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This defines the kind of browser connection BrowserRuntime expects: something that can send a named browser protocol command with optional parameters and an optional session id. It is a contract, not an implementation.

**Data flow**: A caller provides a protocol method name, a dictionary of parameters, and possibly a browser session id. The concrete connection sends that request to the browser and returns the browser’s reply as a dictionary-like JSON object.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on use this connection method through browser.connection().send when they need the browser to evaluate JavaScript or call a JavaScript function.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This defines the kind of session object BrowserRuntime can work with: it must be able to provide a browser protocol connection. It lets BrowserRuntime stay independent from the project’s concrete browser session class.

**Data flow**: The session object is asked for its connection. It returns an object that satisfies BrowserRuntimeCdp, meaning it can send commands to the browser and return replies.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on first ask the stored browser session for this connection, then use that connection to send Runtime commands.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the expression’s value. Someone would use it when Python code needs to read or compute something directly from the page.

**Data flow**: It receives a session id and a JavaScript expression. It builds a Runtime.evaluate request with returnByValue turned on, sends it through the browser connection, checks whether the page reported an exception, then extracts and returns the value from the browser’s result object.

**Call relations**: This is one of the main public actions in BrowserRuntime. It calls BrowserRuntime.raise_on_exception immediately after the browser replies, so JavaScript failures become Python errors, and it uses as_map to safely treat nested JSON fields as dictionaries before reading the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function on an existing browser-side object and returns the function’s plain result. It is useful when earlier browser work produced an object id and Python now wants to ask that object to do or reveal something.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body, and optional argument values. It packages those into a Runtime.callFunctionOn request, converting each argument into the browser protocol’s expected value format, sends the request, checks for a page exception, then returns the result value as a dictionary.

**Call relations**: Like eval, this is a public BrowserRuntime action built on the lower-level send connection. It hands the browser’s reply to BrowserRuntime.raise_on_exception for error checking, then uses as_map to safely walk through the nested JSON response and produce a usable dictionary for the caller.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime reply for a JavaScript exception and raises a Python RuntimeError if one happened. It prevents page script failures from being mistaken for normal results.

**Data flow**: It receives the browser’s reply dictionary. If there is no exceptionDetails dictionary, it does nothing. If exception information is present, it tries to pull out a useful description, falls back to the browser’s text message, and raises an error that includes that description.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this right after receiving a browser response and before returning any value. That makes it the shared safety gate for all JavaScript execution in this file.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `browser protocol message parsing`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often shortened to CDP. In plain terms, CDP is a JSON-based remote control channel for the browser: the engine sends JSON commands and receives JSON replies or events. This file is the small safety gate for that channel.

It first names the kinds of values that can appear in JSON: null, booleans, numbers, strings, lists, and dictionaries with string keys. Then it provides a few simple checker functions. Each checker says, “I expected this value to be shaped like X.” If the value matches, it returns the value in a form the rest of the code can safely use. If it does not match, it raises ValidationError with a clear message that includes where the bad value was found.

This matters because JSON from a browser or remote service is not automatically trustworthy. Without these checks, later code might assume it has a string or list and fail in a less obvious place. The functions are like a customs desk at the border: they inspect incoming packages before letting them into the system. A small convenience is that missing objects and lists can become empty ones, which lets callers treat absent optional data as “nothing here” rather than as a failure.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary of named fields. It is used when the browser engine expects a group of properties rather than a single value.

**Data flow**: It receives a JSON value, which may also be missing, plus a text path that says where that value came from. If the value is missing, it turns it into an empty dictionary. If it is already a dictionary, it passes it through. If it is anything else, it raises ValidationError explaining that this path must contain an object.

**Call relations**: When higher-level browser code reads a CDP response and expects a field bundle, it calls on as_map before using that data. If the shape is wrong, as_map stops the flow by creating a ValidationError, so the error can be reported as a recoverable tool problem instead of becoming a later crash.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It is useful for required text fields such as identifiers, names, or protocol values where an empty string would not be meaningful.

**Data flow**: It receives a JSON value, which may be missing, plus a path describing its location. If the value is a string and it is not empty, the same string comes out. If the value is missing, empty, or a different kind of JSON value, it raises ValidationError saying the path must be a non-empty string.

**Call relations**: Code that decodes browser protocol messages uses as_str when it is about to rely on a text value. as_str either hands back safe text or hands off to ValidationError to clearly mark the incoming wire data as invalid.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, meaning a whole number. It protects code that expects counts, indexes, or numeric identifiers from accidentally receiving text, null, or a more complex value.

**Data flow**: It takes a JSON value, which may be missing, and a path naming where it was found. If the value is an integer, it returns that integer. If not, it raises ValidationError with a message that points to the bad location.

**Call relations**: When browser-engine code pulls a whole-number field out of a CDP message, as_int is the gatekeeper before that number is used. If the browser or provider sent the wrong shape, as_int creates the ValidationError that keeps the problem localized to message validation.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It is used when the engine expects a sequence of items, such as entries from a browser response.

**Data flow**: It receives a JSON value, possibly missing, and a path string that describes the value’s location. If the value is a list, it returns that list. If the value is missing, it returns an empty list. If the value is anything else, it raises ValidationError saying the path must be a list.

**Call relations**: Code reading CDP data calls as_list before looping over a field that should contain multiple items. as_list either supplies a safe list to loop through or raises ValidationError so malformed protocol data is caught immediately.

*Call graph*: 1 external calls (__init__).


### Action Settling
Determines when browser actions have completed enough for automation to continue reliably.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigations`

Browser automation often needs to click a button or load a page, then wait until the result is ready. Waiting for absolutely everything can hang forever, because modern pages keep fetching ads, analytics, images, and live updates. Waiting too little is also bad, because the next step may run before the page has actually changed. This file provides the middle ground.

The Settle class keeps a small scoreboard for the current action: which important requests are still pending, whether the page is loading, and whether the page has painted visible content. “Paint” means the browser has drawn meaningful content on the screen, which is often a better readiness signal than total network silence.

The helper tracks_request filters browser network events so the system waits for foreground work caused by the action, not passive extras such as fonts, images, low-priority prefetches, or analytics calls. Think of it like waiting for the waiter to bring your meal, but not waiting for every other table in the restaurant to finish ordering.

Before waiting, Settle asks the browser to run one tiny JavaScript delay. This gives click handlers and immediate follow-up tasks a chance to start, so their requests can be counted. Then it waits until painting, loading, and tracked network activity reach a sensible stopping point, with time caps so the automation never waits forever.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It skips background or passive work, such as images, fonts, low-priority requests, and known analytics services, because those should not block automation progress.

**Data flow**: It receives a browser event dictionary describing a request. It reads the request type, priority, and URL host. If the request looks passive or analytics-related, it returns false; otherwise it returns true, meaning this request should be tracked until it finishes.

**Call relations**: When Settle.on_request_started sees a new browser request, it asks tracks_request whether that request should count as real action-related work. The answer controls whether the request is added to Settle’s pending-request scoreboard.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker with empty state. It starts with no pending requests, no loading pages, no painted pages, and a count of zero started requests.

**Data flow**: It takes no outside data beyond the new object being created. It sets up internal collections that will later store request identifiers, loading session identifiers, and painted session identifiers. The result is a Settle object ready to observe browser activity.

**Call relations**: BrowserSession creates this object when setting up browser automation state, and also creates a fresh one during close/reset behavior. Other methods in this file then update the state created here as browser events arrive.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the tracker before starting a new wait window. This prevents old requests or paint events from a previous action from affecting the next action.

**Data flow**: It reads the object’s current stored state and empties the pending-request set and painted-session set, then resets the started-request counter to zero. It does not return a value; the important result is that the object’s internal scoreboard is clean.

**Call relations**: This method is used as a boundary marker between actions. After it runs, request-start, request-finish, loading, and paint methods can record only the consequences of the next browser action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records the start of a meaningful browser request. This lets the later wait know that the page is still doing important work.

**Data flow**: It receives browser request details and the browser session they belong to. It extracts the request ID, checks that the session and ID are valid, and uses tracks_request to filter out unimportant traffic. If the request matters, it adds the session/request pair to the pending set and increases the started-request count.

**Call relations**: Browser event handling calls this when Chrome reports that a request is about to be sent. It relies on tracks_request to avoid counting noise, and the pending entry it creates is later removed by Settle.on_request_finished.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a browser request as no longer pending. This is how the tracker learns that one piece of action-related network work has completed or failed.

**Data flow**: It receives browser request details and a session ID. It extracts the request ID and, if both identifiers are valid, removes that session/request pair from the pending set. It returns nothing; it only updates the internal state.

**Call relations**: Browser event handling calls this when Chrome reports that a request has ended. The wait loop watches the pending set, so removing entries here helps Settle.wait decide when the action has quieted down.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has started loading a document. This prevents the wait from finishing while a navigation is still underway.

**Data flow**: It receives a session ID and adds it to the loading set. There is no returned value; the object simply remembers that this session is currently loading.

**Call relations**: Navigation or lifecycle event handling calls this when loading begins. Settle.wait and Settle._drain_after_paint consult this loading set before deciding the page is ready.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session is no longer loading. This removes one reason for the wait loop to keep waiting.

**Data flow**: It receives a session ID and removes it from the loading set if present. It returns nothing; the changed loading set is the result.

**Call relations**: Navigation or lifecycle event handling calls this when loading finishes. After this, Settle.wait may be able to complete if there are also no important pending requests.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted meaningful visible content. Painting is treated as a strong sign that the page has become usable, even if background network activity continues.

**Data flow**: It receives a session ID and adds it to the painted set. It returns nothing; it updates the tracker so future waiting can switch to the shorter post-paint grace period.

**Call relations**: Browser lifecycle event handling calls this when Chrome reports a paint event. Settle.wait looks for this marker and then hands control to Settle._drain_after_paint.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears complete, without waiting forever for background page activity. This is the main method a caller uses after clicking, typing, or navigating.

**Data flow**: It receives a Chrome DevTools Protocol connection, a session ID, and a maximum number of seconds to wait. First it flushes one round of page tasks so immediate request-start events can appear. Then it watches the internal loading, painted, and pending-request state until the page is painted, quiet, or the time cap is reached. It returns nothing; its value is in delaying the caller until the page is likely ready.

**Call relations**: Higher-level browser automation calls this after an action. It first calls Settle._flush_page_tasks, then either returns quickly for quiet actions, waits for loading and tracked requests to drain, or calls Settle._drain_after_paint when a paint event shows that visible content has appeared.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: Gives the page a short extra chance to finish important work after visible content appears. This avoids declaring a page ready the instant an empty shell paints, while still avoiding long waits on pages that never become fully quiet.

**Data flow**: It receives a session ID and an absolute deadline time. It creates a shorter grace deadline, then repeatedly checks whether that session is still loading or has pending important requests. If things become quiet, it returns early; otherwise it returns when the grace period or overall deadline is reached.

**Call relations**: Settle.wait calls this after it sees that the session has painted. This helper is the post-paint phase of the settling story: visible page first, then a brief wait for foreground content to land.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Lets the browser run one quick round of queued page work before the main wait begins. This catches immediate follow-up work, such as click handlers that start network requests after a zero-delay timer.

**Data flow**: It receives a Chrome DevTools Protocol connection and a session ID. It asks the page to evaluate a tiny JavaScript promise based on setTimeout with zero delay, and waits for that promise to resolve. If that browser call fails or times out, it sleeps briefly instead, so the caller still gets a small pause.

**Call relations**: Settle.wait calls this at the very start. It talks to the browser through Cdp.send, and its purpose is to make sure request events caused immediately by the user action have been delivered before the wait loop starts judging readiness.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).

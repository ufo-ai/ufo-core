# CDP connection and browser session core  `stage-10.3.2`

This stage is the core of the built-in browser automation engine. It sits behind the scenes during the main work loop, keeping a live Chrome browser session open and turning high-level browser requests into real browser control messages.

The main coordinator is session.py. It represents one active browser session: it opens Chrome’s control channel, keeps track of tabs, page loading, downloads, and pop-up dialogs, and sends each requested action to the right helper. cdp.py is the wire to Chrome. It uses Chrome’s DevTools Protocol, a remote-control interface for the browser, over a WebSocket, which is a two-way network connection. It sends commands, waits for answers, and routes browser events to whoever is listening.

runtime.py is the JavaScript bridge. It runs small scripts inside the current page, converts browser replies into normal Python values, and reports page-side script failures clearly. wire.py defines and checks the expected message shapes, like a customs desk checking paperwork before messages enter the system. errors.py defines a clear automation-specific error for impossible AI-supplied browser references. __init__.py simply makes this folder importable as a package.

## Files in this stage

### Browser session surface
The package boundary, session orchestrator, and automation-specific error define the browser-control surface used by the rest of the system.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `per browser turn: startup, tool-call handling, and teardown`

A BrowserSession is the project’s “control desk” for one Chrome browser connection. Chrome is driven through CDP, the Chrome DevTools Protocol, which is a WebSocket-based control API that lets code navigate pages, inspect content, click, type, watch downloads, and listen for browser events. Without this file, higher-level browser tools would not have one reliable place to open the connection, remember current tabs, or clean up when a turn ends.

The session starts with basic state: known tabs, out-of-process frame sessions, downloads, dialogs, whether the browser is on macOS, and a loading tracker called Settle. When opened, it resolves the CDP endpoint into a WebSocket URL, connects to Chrome, configures downloads, registers event listeners, discovers targets, and creates an initial blank tab. From then on, public methods such as navigate, read_page, form_input, computer, and wait_for_download are thin front doors. They create a focused helper, such as BrowserTabs or BrowserContent, and ask it to do the detailed work.

This is like a hotel front desk: it does not personally clean rooms, cook food, or carry bags, but it knows which specialist to call, keeps the guest record, and closes everything out when the stay is over. Its cleanup is important: it closes browser targets, shuts the connection, cancels background tasks, and resets mutable state so stale browser data does not leak into the next run.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates a new session object and fills in the bookkeeping it will need before any browser connection is opened. It records the CDP endpoint, chooses the coordinate size used for screenshots and clicks, and prepares empty lists for tabs, downloads, dialogs, and background tasks.

**Data flow**: Inputs are an optional browser endpoint, an optional model name, and a download directory. The function turns the model name into a coordinate space if possible, otherwise uses the default size, then creates fresh state holders such as Settle and BrowserTabEvents. The result is a ready-but-not-yet-connected BrowserSession.

**Call relations**: BuaSurface._open creates this object when it needs a browser session. During construction it calls the coordinate helper to pick the right screen scale and creates the loading and tab-event trackers that later readers will share.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the browser connection if it is not already open. It also makes opening safe: if setup fails halfway through, it closes whatever was started so the session is not left in a broken state.

**Data flow**: It reads the current connection field. If a connection already exists, nothing changes. Otherwise it runs the bootstrap setup; if any error happens, it calls close to reset tabs, downloads, tasks, and the connection, then passes the error back to the caller.

**Call relations**: __aenter__ calls this when the session is used in an async context block. Its main work is handed to _bootstrap, with close used as the cleanup path on failure.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the real startup work for a browser session. It connects to Chrome, installs event listeners, configures downloads, discovers tabs, and opens a fresh blank tab for the session to use.

**Data flow**: It starts with the saved CDP endpoint and headers. It resolves the endpoint into a WebSocket URL, opens the CDP connection, asks Chrome for version information, configures download behavior, registers callbacks for tab, network, dialog, and download events, starts target discovery, then creates and attaches to an initial tab. The session’s connection, platform flag, and tabs list are updated.

**Call relations**: open calls this during startup. It creates BrowserTabs, BrowserDownloads, and BrowserDialogs helpers because those helpers provide the event callbacks registered with the CDP connection.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the session and clears all per-session state. It tries to close every tab it opened, closes the CDP connection, cancels background tasks, and resets trackers so the object no longer points at stale browser state.

**Data flow**: It reads the active connection and known tabs. For each tab it tries to tell Chrome to close the target, ignoring expected close-time failures, then closes the connection. Finally it clears connection, tabs, frame sessions, downloads, dialogs, background tasks, and resets Settle and BrowserTabEvents.

**Call relations**: __aexit__ calls this at normal context-manager teardown, and open calls it if bootstrap fails. It constructs fresh loading and tab-event trackers so any later reuse begins from a clean slate.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python’s async with syntax. Entering the block opens the browser session and returns the session object for use inside the block.

**Data flow**: It receives the session object, calls open, waits until startup is complete, and returns the same object. The main change is that the session now has an active browser connection.

**Call relations**: This is the context-manager entry point. Its only handoff is to open, which performs the actual connection setup.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the browser session when an async with block ends. It runs whether the block finishes normally or exits because of an error.

**Data flow**: It receives any exception information from the context block but does not inspect it. It calls close, which removes browser targets, closes the connection, and resets session state. Nothing is returned.

**Call relations**: This is the context-manager exit point. It delegates all teardown work to close.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection, or raises a clear error if the browser is not open. This prevents deeper code from failing with a confusing missing-connection error.

**Data flow**: It reads the session’s conn field. If it is present, that connection is returned. If it is missing, BrowserUnavailable is raised with a message saying the browser is not open.

**Call relations**: Reader classes use this kind of access pattern when they need to send commands to Chrome. The function creates the BrowserUnavailable error rather than letting callers accidentally use None as a connection.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so it can be cancelled later. This is useful for browser work that must continue while the main action moves on.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session’s background-task set, and registers a callback to remove the task from the set once it finishes. It returns nothing but changes the session’s task tracking.

**Call relations**: It uses asyncio.ensure_future to put the coroutine into the event loop. close later relies on the stored task set to cancel any still-running background work.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Answers whether a browser event belongs to a page’s main frame rather than an embedded frame. This matters because page loading events from nested frames should not always count as the whole page loading.

**Data flow**: It receives a CDP session id and frame id, creates a BrowserTabs helper, and asks that helper to compare the ids with known tab and frame state. It returns true or false.

**Call relations**: This delegates to tab_reader, because BrowserTabs owns the tab and frame interpretation used during event processing.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready to send and receive page-related events. This is part of preparing tabs and frames for browser automation.

**Data flow**: It receives a CDP session id, builds a BrowserTabs helper, and asks it to initialize that session. The result is no direct return value; Chrome-side session setup is performed through the helper.

**Call relations**: It hands off to tab_reader because BrowserTabs knows which CDP features need to be enabled for tab sessions.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the active tab, or the tab identified by a given tab id. It gives other code a safe way to resolve “which page should this action use?”

**Data flow**: It receives an optional tab id. It asks BrowserTabs to find or select the corresponding Tab object and returns that Tab.

**Call relations**: It delegates to tab_reader, keeping tab selection rules inside BrowserTabs rather than duplicating them in the session.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new URL. This is the main session-level entry for telling Chrome to load a page.

**Data flow**: It receives a URL and an optional tab id. It asks BrowserTabs to perform navigation for the chosen tab, then returns the JSON-style result from that helper.

**Call relations**: It delegates to tab_reader, because BrowserTabs knows how to choose the tab, send the CDP navigation command, and update tab-related state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a small information record for a specific tab. This gives callers a consistent view of a tab’s id, title, URL, or other tab metadata supplied by BrowserTabs.

**Data flow**: It receives a Tab object, passes it to BrowserTabs, and returns a JSON dictionary describing that tab.

**Call relations**: It uses tab_reader so tab metadata formatting stays with the tab subsystem.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about the current browser tabs. This is useful when a tool or model needs to know what tabs exist before choosing one.

**Data flow**: It takes no extra input, creates a BrowserTabs helper, and asks it for a JSON dictionary describing the tab context. The session state is read but not directly changed here.

**Call relations**: It delegates to tab_reader, which owns the details of tab ordering and tab descriptions.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of the open tabs. This provides a compact human-readable summary of the browser’s current tab set.

**Data flow**: It reads the session’s tab state through BrowserTabs and returns a list of title strings. It does not directly alter the session.

**Call relations**: It uses tab_reader so title lookup follows the same tab rules as the rest of the browser session.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper tied to this session. BrowserTabs is the specialist for tab discovery, selection, navigation, creation, closing, and tab-related events.

**Data flow**: It takes the current session and the fixed viewport size, passes both into BrowserTabs, and returns the new helper object. The helper reads and updates the session’s shared tab state when its methods are used.

**Call relations**: _bootstrap uses this helper to register tab event listeners and attach the first tab. Many public methods, including init_session, page, navigate, tab_info, tab_titles, tabs_create, and tabs_close, call it before handing off tab-specific work.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper tied to this session. BrowserPage is the specialist for page structure, frames, element references, and turning a reference into a usable page location.

**Data flow**: It takes the session, viewport size, and maximum frame depth, passes them into BrowserPage, and returns the helper. The helper later reads page and frame information through the session.

**Call relations**: resolve_ref and ref_point call this when they need page-reference logic. Keeping it here means the session remains the common doorway while BrowserPage owns the detailed page-structure work.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper tied to this session. BrowserContent is the specialist for reading page content, extracting text, producing trees, and searching within a page.

**Data flow**: It passes the session into BrowserContent and returns the helper. Later method calls on that helper read browser content through the session connection and tab state.

**Call relations**: tree, read_page, get_page_text, and find call this helper before doing content-related work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper tied to this session. BrowserDownloads watches download events and waits for a download to finish.

**Data flow**: It passes the session and maximum wait time into BrowserDownloads and returns the helper. That helper reads and updates the session’s download list when download events arrive or a wait is requested.

**Call relations**: _bootstrap uses it to register download event callbacks with Chrome, and wait_for_download uses it when a caller wants to wait for a completed download.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper tied to this session. BrowserDialogs records JavaScript dialogs, such as alerts or prompts, that pages open.

**Data flow**: It passes the session into BrowserDialogs and returns the helper. The helper can then append dialog information to the session’s dialog list when Chrome reports a dialog.

**Call relations**: _bootstrap calls this so the dialog event callback can be registered with the CDP connection.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper tied to this session. BrowserForms is the specialist for typing into forms, uploading files, and checking attached file sizes.

**Data flow**: It passes the session into BrowserForms and returns the helper. Later calls through the helper use the browser connection and page references to interact with form fields.

**Call relations**: upload_file, attached_sizes, and form_input call this before handing off form-specific work.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper tied to this session. BrowserRuntime is the specialist for evaluating JavaScript and calling functions on browser-side objects.

**Data flow**: It passes the session into BrowserRuntime and returns the helper. The helper later sends runtime commands through the session’s CDP connection.

**Call relations**: eval_js and call_on call this helper when they need JavaScript execution inside Chrome.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page. This is the session-level tool for opening another tab.

**Data flow**: It receives a URL, or uses about:blank if none is provided. It asks BrowserTabs to create the tab and returns a JSON dictionary describing the result.

**Call relations**: It delegates to tab_reader, which knows how to ask Chrome for a new target and add it to the session’s tab state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes one or more browser tabs according to the provided arguments. This gives callers a controlled way to remove tabs from the session.

**Data flow**: It receives a JSON dictionary of close instructions, passes them to BrowserTabs, and returns a JSON dictionary with the close result. The session’s tab list may change through the helper.

**Call relations**: It delegates to tab_reader so tab-closing rules and Chrome target-closing details stay in BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads or attaches a file to a file input on a web page. This is used when a browser task needs to choose a local file for a form.

**Data flow**: It receives JSON arguments describing the upload target and file information. It creates a BrowserForms helper, asks it to perform the upload, and returns the helper’s JSON result.

**Call relations**: BuaSurface._settle_upload calls this during upload handling. The method hands the detailed form and file-input work to form_reader.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached to a form input. This helps verify what the page currently has selected after an upload step.

**Data flow**: It receives JSON arguments identifying the relevant form input. It asks BrowserForms to inspect the attachment sizes and returns a list of integer sizes.

**Call relations**: BuaSurface._settle_upload calls this around upload settling. The detailed page inspection is delegated to form_reader.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Builds a text tree of page content, optionally filtered by type. This gives callers a structured but readable view of what is on the page.

**Data flow**: It receives JSON arguments and a filter type, creates a BrowserContent helper, and asks it to produce the tree string. It returns that string.

**Call relations**: It delegates to content_reader, because BrowserContent owns page-reading and tree-building behavior.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page in a structured form. This is a main entry for turning the visual/browser page into information other parts of the system can use.

**Data flow**: It receives JSON arguments describing what to read, passes them to BrowserContent, and returns a JSON dictionary with the page-reading result.

**Call relations**: It delegates to content_reader so the session stays focused on coordination while BrowserContent does the detailed extraction.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts text from the page. This is useful when a caller needs the page’s words without the full structural view.

**Data flow**: It receives JSON arguments, sends them to BrowserContent, and returns a JSON dictionary containing the extracted text result.

**Call relations**: It delegates to content_reader, which knows how to collect page text from the live browser.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches for text or content on the page, optionally using a completion helper to finish or refine the search. This supports find-in-page style behavior for browser tools.

**Data flow**: It receives JSON search arguments and an optional FindCompleter. It passes both to BrowserContent and returns a JSON dictionary with the find result.

**Call relations**: It delegates to content_reader because BrowserContent owns page search behavior and any search completion integration.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or otherwise enters data into a form field. This is the session-level front door for filling web forms.

**Data flow**: It receives JSON arguments describing the target field and input value. It asks BrowserForms to perform the action and returns a JSON dictionary describing the result.

**Call relations**: It delegates to form_reader, which knows how to resolve form targets and interact with them through Chrome.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a lower-level computer-style browser action, such as actions based on screen coordinates or direct interaction. It gives the session access to a more general browser-control tool.

**Data flow**: It receives JSON arguments for the action. It creates a BrowserComputer with the session, viewport, and maximum wait time, runs it, and returns the resulting JSON dictionary.

**Call relations**: Unlike the reader factory methods, this creates BrowserComputer directly for the single action. BrowserComputer then performs the detailed interaction using the session state and connection.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a browser download to complete and returns its recorded download information. The file’s bytes are not read here; the completed download is reported to whoever owns the download directory.

**Data flow**: It receives JSON arguments describing which download to wait for or how to wait. It asks BrowserDownloads to wait up to the configured limit and returns a BrowserDownload record when complete.

**Call relations**: It delegates to download_reader. The same download subsystem was registered with Chrome during _bootstrap, so it can match progress events to this wait request.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression inside a specific browser session. This is used when higher-level code needs a direct answer from the page’s JavaScript environment.

**Data flow**: It receives a CDP session id and a JavaScript expression string. It passes both to BrowserRuntime and returns the JSON-compatible value produced by the browser.

**Call relations**: It delegates to runtime_reader, which sends the actual runtime command through CDP.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side object. This is useful after code has a reference to an object in the page and wants to ask that object for more information or perform an operation.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body or name, and optional arguments. It passes them to BrowserRuntime and returns a JSON dictionary with the call result.

**Call relations**: It delegates to runtime_reader, keeping low-level JavaScript runtime calls in BrowserRuntime.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the frame node and numeric identifier it points to. This lets other actions target page elements without having to understand the page tree format themselves.

**Data flow**: It receives a Tab and a reference string. It creates a BrowserPage helper, asks it to resolve the reference, and returns a pair: the matching FrameNode and an integer id.

**Call relations**: It delegates to page_reader, because BrowserPage owns page-reference parsing and frame lookup.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a clickable or usable screen point for a referenced page element. This is used when a higher-level page reference must become actual coordinates for browser interaction.

**Data flow**: It receives a Tab and a reference string. It asks BrowserPage to locate the reference and compute a point, then returns the x and y coordinates as integers.

**Call relations**: It delegates to page_reader, which combines page structure with viewport information to turn references into coordinates.

*Call graph*: calls 1 internal fn (page_reader).


### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: it does not contain the tools itself, but it tells Python that the drawer is part of the project’s organized structure. Without this file, some Python environments or tooling might not reliably recognize `extensions/browser/ufo_ext_browser/bua` as a package, which could make imports from this area fail or behave inconsistently. Because the file is empty, it does not run setup code, expose shortcuts, or change any state when imported. Its value is structural: it supports the package layout used by the rest of the browser extension code.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`domain_logic` · `request handling`

This small file gives the browser automation layer a clear name for one important kind of failure: a model “hallucination.” In plain terms, that means the AI claimed something was real when the system can prove it is not. For example, the model might ask to click an element using a made-up element reference that was never present on the page.

The file imports `ValidationError`, which is a general error used when incoming data fails a validity check. `HallucinationError` is a more specific version of that error. It does not add new behavior; its value is in making the reason for failure explicit. This is like labeling a rejected form as “invalid address” instead of just “bad form.” The label helps the rest of the system, logs, tests, or callers understand what went wrong and possibly react differently.

Without this file, the system could still reject impossible values, but it would have to use a vaguer error type. That would make debugging and error reporting less clear, especially in a browser-control system where distinguishing a normal validation mistake from an AI-invented browser object matters.


### CDP transport and runtime bridge
The DevTools connection, JavaScript runtime bridge, and wire-message checks provide the low-level protocol machinery behind browser sessions.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser connection setup, command sending, event listening, teardown`

Chrome’s DevTools Protocol, often shortened to CDP, is like a remote control for Chrome: you can ask the browser to open pages, inspect tabs, listen for page loads, and more. This file provides the transport layer for that remote control. Without it, higher-level browser code could send a command and never know which reply belonged to it, or could miss important browser events.

The main class, CdpConnection, wraps a WebSocket, which is a long-lived two-way network pipe. Each outgoing command gets a unique number. When Chrome sends back a message with that number, the connection completes the waiting command with either a result or an error. At the same time, a background reader keeps listening for messages from Chrome. Messages that are not command replies are treated as events, such as “this page changed” or “this target attached.”

The file supports two ways to receive events. Code can register ongoing listeners with on, or create a one-time expectation with expect and then wait for it with a timeout. This is like either subscribing to all doorbell rings, or saying “wake me only the next time this specific bell rings.” If the WebSocket closes, the file deliberately fails all pending waits instead of letting the rest of the program hang forever.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Builds a clear Python error for a failed Chrome DevTools command. It keeps the command name and error code so callers can tell which browser request failed and why.

**Data flow**: It receives the CDP method name, a numeric error code, and Chrome’s error message. It stores the method and code on the error object, then creates a readable message such as “CDP Some.method failed...” that can be raised and logged.

**Call relations**: When CdpConnection._dispatch receives a command reply from Chrome that contains an error, it creates this CdpError and attaches it to the waiting command. That way, the original caller of send gets a meaningful failure instead of a raw protocol message.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual DevTools WebSocket address to connect to. If the caller already gave a WebSocket URL, it returns it; otherwise it asks Chrome’s HTTP debug endpoint for the right address.

**Data flow**: It takes a URL and request headers. If the URL starts with ws:// or wss://, it is already ready to use. Otherwise, it calls the browser’s /json/version endpoint, reads the webSocketDebuggerUrl field from the JSON reply, checks that it is a string, and returns it.

**Call relations**: This is a preparation step before opening a CdpConnection. It uses httpx.AsyncClient to make the HTTP request and as_str to safely pull a string out of the response.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Creates the in-memory bookkeeping needed for one live CDP WebSocket connection. It starts with no pending commands, no event listeners, no one-time event waiters, and no reader task yet.

**Data flow**: It receives an already-open WebSocket connection. It stores that connection, sets the next command id to zero, and prepares empty containers for pending command replies, event listeners, event waiters, and the background reader task.

**Call relations**: CdpConnection.open creates the WebSocket and then calls this constructor. The fields prepared here are later used by send, expect, wait, _read_loop, and _dispatch to coordinate messages.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a new CDP connection and starts the background reader that listens for Chrome messages. This is the normal way other code begins talking to the browser.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to that URL, wraps the WebSocket in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this when setting up a browser session. The method hands off ongoing incoming-message work to _read_loop by starting it with asyncio.create_task.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader and closes the WebSocket so no more commands or events flow through it.

**Data flow**: It looks at the stored reader task. If one exists, it cancels it, waits for the cancellation to finish, ignores the expected cancellation exception, clears the reader reference, and then closes the WebSocket.

**Call relations**: This is used during teardown when the browser communication channel is no longer needed. It works together with _read_loop’s cleanup behavior, which makes sure pending commands and event waits do not remain stuck.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one command to Chrome and waits for the matching reply. It is the main request-and-response path for code that wants the browser to do something.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for a specific browser target. It assigns a new message id, records a future object that will hold the reply, sends the JSON message over the WebSocket, and waits up to the command timeout. On success it returns the result dictionary. On timeout it removes the pending record and raises a timeout error.

**Call relations**: Higher-level browser code calls this to issue CDP commands. The reply is not completed inside send itself; _read_loop receives incoming WebSocket messages and _dispatch matches the response id back to this command’s stored future.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a callback to be told whenever a specific CDP event arrives. This is for ongoing notifications, not just a single wait.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for the event, creating the list if this is the first listener for that event. It does not return a value.

**Call relations**: Later, when _dispatch sees an incoming event with that name, it calls each registered listener with the event parameters and optional session id.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time promise that will complete when one of the requested events arrives. This is useful when code says, “I am about to do something; wait for the browser event that proves it happened.”

**Data flow**: It receives one or more event names and optionally a session id to limit which browser target counts. It creates a future, stores it with the event names and session filter, and returns the future to the caller.

**Call relations**: Callers usually pass the returned future to CdpConnection.wait to apply a timeout. _dispatch completes the future when a matching event arrives.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for an event future, but only for a limited amount of time. It prevents code from waiting forever if the expected browser event never arrives.

**Data flow**: It receives a future created by expect and an optional timeout. It waits for the future to produce event parameters. Whether the wait succeeds, fails, or times out, it removes that future from the stored waiter list so stale waits do not pile up.

**Call relations**: This pairs with expect. expect registers the event wait, _dispatch completes it when a matching event comes in, and wait returns the event data or lets the timeout/failure reach the caller.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads messages from Chrome in the background. It is the part that keeps the connection alive from the project’s point of view, because replies and events arrive asynchronously.

**Data flow**: It reads raw WebSocket messages one by one, parses each JSON string into a dictionary, and passes it to _dispatch. If the WebSocket closes, it stops reading. In all shutdown cases, it fails any command replies or event waits that are still pending, then clears the stored pending work.

**Call relations**: CdpConnection.open starts this as a background task. It delegates message interpretation to _dispatch, and it protects callers of send, expect, and wait from hanging if the browser connection disappears.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Sorts one incoming CDP message into the right bucket: a command reply, a command error, or a browser event. This is the central traffic director for messages coming from Chrome.

**Data flow**: It receives one parsed message dictionary. If the message has an integer id, it treats it as a reply to a previous send call, finds the matching pending future, and completes it with either a result dictionary or a CdpError. If there is no id, it treats the message as an event, extracts its method name, parameters, and optional session id, completes any matching one-time waiters, and calls any ongoing listeners.

**Call relations**: _read_loop calls this for every incoming WebSocket message. It completes futures created by send and expect, constructs CdpError when Chrome reports a failed command, and notifies listeners registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `browser automation request handling`

Browser automation often needs to ask the page itself questions, such as “what is the current value of this element?” or “run this small function on that browser object.” The browser exposes this through the Chrome DevTools Protocol, or CDP, which is a message-based control channel for inspecting and driving a browser. This file wraps the CDP Runtime commands that evaluate JavaScript so the rest of the project does not have to build those messages by hand.

The main class, BrowserRuntime, is like a translator at a service desk. Other code gives it a browser session, a target page session id, and either a JavaScript expression or a function to call. It sends the right CDP command, asks the browser to return the result as a plain value, checks whether the browser reported an exception, and then extracts the useful value from the nested response.

Two small Protocol classes describe what BrowserRuntime needs from the outside world: something that can send CDP messages, and something that can provide that connection. A Protocol is a Python way to say “any object with these methods will do.” The important safety feature is raise_on_exception: without it, failed JavaScript in the page could look like a normal empty or confusing response instead of a clear Python error.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This defines the shape of an object that can send a command to the browser over the Chrome DevTools Protocol. It is not implemented here; it is a promise that any compatible connection must provide this method.

**Data flow**: It receives a command name, optional command parameters, and an optional browser session id. A real implementation would send that information to the browser and return the browser's reply as a dictionary-like JSON object.

**Call relations**: BrowserRuntime relies on this method whenever it needs the browser to run JavaScript. BrowserRuntime.eval and BrowserRuntime.call_on get a connection from the session and then hand their prepared CDP command to send.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This defines the shape of an object that can give BrowserRuntime access to a CDP connection. It lets BrowserRuntime stay independent of the exact browser session class used elsewhere.

**Data flow**: It takes no extra input beyond the session object itself. A real implementation returns an object that knows how to send CDP commands to the browser.

**Call relations**: BrowserRuntime calls this before sending Runtime.evaluate or Runtime.callFunctionOn. In practice, this is the doorway from the runtime helper into the lower-level browser communication layer.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression in a specific browser target and returns the resulting plain value. It is useful when other code needs a simple answer from the page, such as a number, string, boolean, list, or object that can be represented as JSON.

**Data flow**: It receives a browser session id and a JavaScript expression. It builds a Runtime.evaluate CDP request, sends it through the browser connection, checks the reply for a JavaScript exception, then digs into the response and returns the result value.

**Call relations**: This is one of the main public actions of BrowserRuntime. It calls the CDP connection's send method to ask the browser to evaluate code, then calls BrowserRuntime.raise_on_exception so failed page-side JavaScript becomes a clear Python RuntimeError. It uses as_map to safely treat nested JSON pieces as dictionaries before reading the value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This calls a JavaScript function on an existing browser-side object and returns the function's plain JSON-like result. It is used when automation code already has a browser object id and wants to inspect or transform that object inside the page.

**Data flow**: It receives a browser session id, a browser object id, a JavaScript function body, and optional argument values. It builds a Runtime.callFunctionOn CDP request, converts each argument into the format CDP expects, sends the request, checks for exceptions, and returns the nested result value as a dictionary.

**Call relations**: This is the companion to BrowserRuntime.eval for cases where code must operate on a specific browser object instead of just evaluating a standalone expression. Like eval, it sends a CDP command, then delegates error checking to BrowserRuntime.raise_on_exception and uses as_map to read the structured response safely.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime response and turns any uncaught JavaScript exception into a Python RuntimeError. It keeps callers from accidentally treating a failed page script as a successful result.

**Data flow**: It receives the full response dictionary from the browser. If there is no exception information, it returns without changing anything. If exception details are present, it picks the best available description or text and raises an error message that explains the browser evaluation failed.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser response. It sits between the low-level CDP reply and the returned value, acting like a checkpoint that stops bad JavaScript results from flowing farther into the system.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during CDP message parsing`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often shortened to CDP. In plain terms, CDP is a JSON-based remote control channel for the browser: the engine sends JSON commands and receives JSON replies. This file sits at that boundary and acts like a customs checkpoint for those JSON values.

It first defines shared names for JSON-like data: values can be strings, numbers, booleans, null, lists, or dictionaries with string keys. Then it provides a few small “narrowing” functions, such as `as_map`, `as_str`, `as_int`, and `as_list`. Each one takes a loose JSON value and checks whether it is safe to treat it as a specific kind of value. For example, `as_str` only accepts a non-empty string.

If a value is missing where an empty object or list is acceptable, some functions turn `None` into `{}` or `[]`. But if the shape is wrong, the file raises `ValidationError`. That matters because a bad browser response becomes a clear, recoverable tool error instead of spreading through the engine as a mysterious value that fails later in a harder-to-understand way.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value can safely be used as an object-like dictionary. It is useful when the browser response may omit an object, because `None` is treated as an empty dictionary.

**Data flow**: It receives a JSON value and a text label called `path` that says where the value came from. If the value is `None`, it returns an empty dictionary; if it is already a dictionary, it returns it unchanged. If it is anything else, it raises `ValidationError` with a message naming the bad path.

**Call relations**: When parsing CDP data, callers use this as a guard before reading fields from an object. If the value is not object-shaped, it creates a `ValidationError` immediately so the bad wire data is reported at the boundary instead of causing later confusion.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a real, non-empty string. It is used when the engine needs a meaningful text value, such as an identifier or name, not a missing or blank one.

**Data flow**: It receives a JSON value and a `path` label describing where that value was found. If the value is a non-empty string, it returns that string. For `None`, an empty string, or any other type of value, it raises `ValidationError` explaining that the path must contain a non-empty string.

**Call relations**: Callers use this before trusting text from a CDP message. Its only handoff on failure is to construct a `ValidationError`, which turns a wrong wire shape into a clear validation problem.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer. It is used when the browser data must be a whole number, such as a count, position, or numeric identifier.

**Data flow**: It receives a JSON value and a `path` label. If the value is an integer, it returns that integer unchanged. If the value is missing or is any other kind of JSON value, it raises `ValidationError` saying that the path must be an integer.

**Call relations**: Code that reads numeric fields from CDP responses can call this before doing number-based work. If the field is not an integer, the function stops the flow by creating a `ValidationError` rather than letting the wrong value travel deeper into the engine.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value can safely be used as a list. It also treats a missing list as an empty list, which is helpful for optional arrays in browser responses.

**Data flow**: It receives a JSON value and a `path` label. If the value is already a list, it returns it unchanged; if the value is `None`, it returns an empty list. If the value is anything else, it raises `ValidationError` explaining that the path must be a list.

**Call relations**: Callers use this when they expect several items from a CDP message. It either gives them a list to loop over, including an empty one when the field is absent, or constructs a `ValidationError` when the incoming data is the wrong shape.

*Call graph*: 1 external calls (__init__).

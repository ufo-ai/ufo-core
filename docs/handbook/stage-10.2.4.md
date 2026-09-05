# Browser page state, tabs, forms, dialogs, and downloads  `stage-10.2.4`

This stage is the browser’s working memory and control desk during the main work loop. It keeps track of what Chrome is doing so higher-level tools can open pages, fill forms, answer pop-ups, and collect downloaded files without getting lost.

The center is session.py. It represents one live connection to Chrome and holds the shared state used by the other pieces. Tools enter through this session when they need to control the browser. tabs.py keeps the tab list accurate and performs tab actions, such as opening a new tab, switching tabs, closing one, or navigating to a page. forms.py fills text fields and attaches files to upload buttons, then checks that the browser really received the file. dialogs.py watches for JavaScript dialogs, meaning small pop-up boxes made by the page, and responds quickly so the page does not block. It also records the result. downloads.py watches for files being saved, can push Chrome to download instead of previewing, and waits until the file is ready. Together these parts make page workflows reliable.

## Files in this stage

### Session orchestration
Defines the live Chrome-backed browser session that coordinates tabs, dialogs, downloads, forms, and input state for browser tools.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `opened for a browser turn, used during tool calls, then closed during cleanup`

A browser automation system needs one reliable place that knows how to connect to Chrome, listen for browser events, and pass work to the right specialist. This file provides that place through `BrowserSession`. Think of it like the front desk of a hotel: it does not clean every room itself, but it knows which room exists, who is checked in, where deliveries go, and which staff member should handle each request.

When opened, the session uses a Chrome DevTools Protocol endpoint, often shortened to CDP, which is Chrome’s remote-control interface. It resolves the real WebSocket address, opens the connection, asks Chrome about itself, turns on download events, starts tab discovery, and creates a blank tab to work in. It also registers event listeners for downloads, network activity, page loading, dialogs, and tab creation or removal.

After that setup, most public methods are simple doorways. For example, page reading goes to `BrowserContent`, tab work goes to `BrowserTabs`, form work goes to `BrowserForms`, JavaScript execution goes to `BrowserRuntime`, and downloads go to `BrowserDownloads`. The session keeps shared mutable state, such as the open tabs, downloads seen so far, background tasks, and whether the browser is on macOS. Closing the session carefully closes targets, shuts down the connection, cancels background work, and resets the state so the next turn starts cleanly.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object before it is connected to Chrome. It records the CDP endpoint, chooses the coordinate size used for screenshots and clicks, and prepares lists and helper objects that will later track tabs, downloads, dialogs, and page settling.

**Data flow**: Input values such as the browser endpoint, model name, and download folder come in. The constructor turns the model name into a coordinate space when possible, creates fresh tracking state, and leaves the actual browser connection unset. The result is a ready-but-not-open session object.

**Call relations**: The backend creates this session when it wants to open a browser surface. Later, `open` fills in the missing live connection and starts using the state prepared here.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Opens the live connection to Chrome if it is not already open. It protects callers from accidentally opening the same session twice.

**Data flow**: It reads the current connection field. If a connection already exists, it does nothing; otherwise it starts bootstrapping. If bootstrapping fails, it closes and resets the session before letting the error continue outward.

**Call relations**: The async context manager method `__aenter__` calls this at the start of a session. It hands the real setup work to `_bootstrap` and uses `close` as cleanup if setup breaks halfway through.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Does the actual startup work needed to make Chrome controllable. It opens the CDP WebSocket connection, registers event listeners, enables downloads and tab discovery, and creates the first blank tab.

**Data flow**: It starts with the stored CDP endpoint. It resolves that endpoint into a WebSocket URL, opens the connection, asks Chrome for version information, creates reader helpers, attaches browser event callbacks, enables download behavior, discovers existing targets, and finally stores one attached blank tab in the session.

**Call relations**: `open` calls this when the session needs to become live. It uses the tab, download, and dialog reader factories so those subsystems can receive events from Chrome as they happen.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the browser session and returns the object to a clean empty state. It tries to close open Chrome targets and always clears local state, even if some browser cleanup commands fail.

**Data flow**: It reads the current connection and tab list. For each tab it asks Chrome to close the target, then closes the CDP connection. Afterward it clears tabs, downloads, frame sessions, dialogs, background tasks, and settling state, and replaces event trackers with fresh ones.

**Call relations**: `__aexit__` calls this at the end of an async context, and `open` calls it if startup fails. It is the counterpart to `_bootstrap`.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets the session be used with Python’s async `with` syntax, which guarantees startup before use. This makes browser lifetime easier to read and safer.

**Data flow**: It receives the session object, calls `open`, waits for the connection to be ready, and returns the same session to the caller.

**Call relations**: Code that writes `async with BrowserSession(...)` enters through this method. It delegates all real startup work to `open`.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the browser session when an async `with` block ends. It runs whether the block finished normally or because an error happened.

**Data flow**: It receives any exception information from the `with` block, ignores those details here, and calls `close` to tear down the session.

**Call relations**: This is paired with `__aenter__`. It delegates cleanup to `close` so the session does not leave browser targets or background tasks behind.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection for code that needs to send commands to Chrome. If the session has not been opened, it raises a clear error instead of returning nothing.

**Data flow**: It checks the stored connection field. If present, that connection is returned; if absent, a `BrowserUnavailable` error is raised.

**Call relations**: Reader objects use the session as their shared source of truth, and this method is the safe doorway to the underlying Chrome connection.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so it can be cancelled during cleanup. This is useful for work that must continue while the main tool call proceeds.

**Data flow**: A coroutine, meaning an async task that can be scheduled, comes in. The method schedules it, stores the resulting task in a set, and arranges for the task to remove itself from that set when it finishes.

**Call relations**: Other browser subsystems can ask the session to launch background work. `close` later cancels any tasks still running so they do not leak past the session lifetime.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Answers whether a browser event refers to the main page frame rather than a nested frame such as an iframe. This helps the system decide which page-loading events matter most.

**Data flow**: It receives a CDP session id and frame id, creates a tab reader, and asks that reader to interpret the frame information using the session’s tab state. It returns true or false.

**Call relations**: This is a convenience wrapper around `BrowserTabs`. Event-processing code can ask the session this question without constructing the tab helper itself.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready to receive the browser features this automation needs. A CDP session here is a channel attached to a tab or frame.

**Data flow**: It receives the session id, creates a tab reader, and asks it to initialize that session. Any browser-side setup happens inside the tab reader.

**Call relations**: This delegates session setup to `BrowserTabs`, keeping tab-specific details out of the central session object.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be used for a tool call. If a tab id is supplied, it looks up that tab; otherwise it chooses the current/default page according to tab rules.

**Data flow**: It receives an optional tab id, passes it to a tab reader, and returns the matching `Tab` object after the reader resolves it.

**Call relations**: Higher-level browser tools call this when they need a concrete page before navigating, reading, clicking, or evaluating content.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new URL. This is the session-level doorway for page navigation.

**Data flow**: It receives a URL and optional tab id, passes both to the tab reader, and returns the JSON-style result produced by the navigation operation.

**Call relations**: Callers use this when a tool asks the browser to go somewhere. The actual tab lookup and CDP navigation command live in `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a small information record for a tab, such as details needed to describe it to a caller. It keeps the public session API from exposing tab internals directly.

**Data flow**: It receives a `Tab` object, passes it to a tab reader, and returns a dictionary-shaped result with that tab’s information.

**Call relations**: This is another doorway into `BrowserTabs`, used when surrounding code needs a clean summary of an existing tab.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about the currently known tabs, suitable for tools that need to show or reason about open browser pages. It packages tab state into a caller-friendly shape.

**Data flow**: It creates a tab reader, asks it to build the tab context from the session’s stored tab list and events, and returns the resulting dictionary.

**Call relations**: This supports tab-aware tool responses. `BrowserTabs` does the detailed work while the session provides the shared state.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Gets the titles of known tabs. This is a lightweight way to summarize what pages are open.

**Data flow**: It creates a tab reader, asks it for tab titles, and returns a list of strings.

**Call relations**: The method delegates to `BrowserTabs`, which knows how to inspect or cache tab title information.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a `BrowserTabs` helper, the specialist object for tab discovery, tab selection, tab creation, tab closing, and page-load events. The session stays as the shared state holder while the helper does tab-specific work.

**Data flow**: It takes no outside input beyond the session itself. It combines this session with the fixed viewport size and returns a new tab helper object.

**Call relations**: Startup, navigation, tab lookup, tab summaries, tab creation, and tab closing all call this when they need tab-specific behavior.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a `BrowserPage` helper, the specialist for understanding page frames and element references. It is used when the system needs to turn a saved reference into a frame or screen point.

**Data flow**: It reads the session, viewport size, and maximum frame depth setting, then returns a page helper configured with those limits.

**Call relations**: `resolve_ref` and `ref_point` call this before asking page-specific questions.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a `BrowserContent` helper, the specialist for reading page structure and text and searching within a page. This keeps content extraction separate from connection setup.

**Data flow**: It wraps the current session in a content helper and returns that helper.

**Call relations**: `tree`, `read_page`, `get_page_text`, and `find` all use this helper to do their detailed page-reading work.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a `BrowserDownloads` helper, the specialist for tracking download events and waiting for a download to finish. It includes the maximum wait time the system will tolerate.

**Data flow**: It combines the session’s download state with the configured timeout and returns a download helper.

**Call relations**: `_bootstrap` uses this helper to register download event callbacks, and `wait_for_download` uses it when a caller is waiting for a completed file.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a `BrowserDialogs` helper, the specialist for browser JavaScript dialogs such as alert or confirm popups. These dialogs can block automation if they are not noticed.

**Data flow**: It wraps the session in a dialog helper and returns it.

**Call relations**: `_bootstrap` uses this helper when wiring Chrome’s dialog-opening event to the session’s dialog tracking.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a `BrowserForms` helper, the specialist for typing into fields and uploading files. This separates form-specific behavior from the central session wiring.

**Data flow**: It wraps the session in a form helper and returns it.

**Call relations**: `upload_file`, `attached_sizes`, and `form_input` call this when a tool action involves forms or file inputs.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a `BrowserRuntime` helper, the specialist for running JavaScript in the browser and calling functions on browser-side objects. This is used for lower-level page inspection and interaction.

**Data flow**: It wraps the session in a runtime helper and returns it.

**Call relations**: `eval_js` and `call_on` call this helper when they need to execute code through Chrome’s runtime interface.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page if no URL is supplied. It exposes tab creation as a session-level tool action.

**Data flow**: It receives a URL, creates a tab reader, and asks it to create the tab. The result is returned as a dictionary-shaped response.

**Call relations**: This is a public wrapper around `BrowserTabs.create`, used when tool callers need a new tab without knowing tab internals.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab based on arguments supplied by the caller. It provides a public session-level route for tab closing.

**Data flow**: It receives a dictionary of close arguments, passes them to the tab reader, and returns the tab reader’s response.

**Call relations**: This delegates the details of choosing and closing the target tab to `BrowserTabs`.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads or attaches files through a page’s file input control. It is used when a browser tool needs to provide files to a web page.

**Data flow**: It receives upload arguments, creates a form reader, and passes the arguments through. The returned dictionary reports the outcome.

**Call relations**: The backend’s upload-settling flow calls this. The detailed file-input interaction is performed by `BrowserForms`.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached during an upload flow. This lets surrounding code verify or describe what was attached without reading the files here.

**Data flow**: It receives arguments that identify the attachment context, passes them to the form reader, and returns a list of integer sizes.

**Call relations**: The backend upload-settling flow calls this after or around upload work. `BrowserForms` provides the actual attachment-size lookup.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text representation of the page’s structure, optionally filtered by type. This is useful when the automation needs an overview of what is on the page.

**Data flow**: It receives page-reading arguments and a filter type, passes them to a content reader, and returns the tree as a string.

**Call relations**: This is a public wrapper around `BrowserContent.tree`, letting callers inspect page structure through the session.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a structured response for the caller. It is one of the main ways the system turns a visual web page into machine-readable information.

**Data flow**: It receives reading arguments, passes them to the content reader, and returns a dictionary-shaped page description.

**Call relations**: Tool callers use this through the session, while `BrowserContent` performs the detailed extraction.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts readable text from a page. This is useful when the caller cares about the words on the page more than the full structure.

**Data flow**: It receives arguments describing what page or area to read, passes them to the content reader, and returns a dictionary containing the text result.

**Call relations**: This delegates text extraction to `BrowserContent`, keeping the session focused on routing and shared browser state.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within page content and may use a completion helper to finish or refine the find operation. It gives tools a way to locate text or elements on a page.

**Data flow**: It receives search arguments and an optional `FindCompleter`, passes both to the content reader, and returns a dictionary-shaped search result.

**Call relations**: The session exposes the search action, while `BrowserContent.find` does the page-specific search work.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Fills or edits form fields on a page. It is the public session method for actions like typing into an input box.

**Data flow**: It receives form input arguments, passes them to a form reader, and returns the form helper’s result.

**Call relations**: This routes form-editing tool calls to `BrowserForms`, which knows the details of interacting with page controls.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a browser “computer” action, such as a lower-level visual or input operation against the browser viewport. It is the route for actions that act more like a user controlling the screen.

**Data flow**: It receives action arguments, creates a `BrowserComputer` configured with this session, the viewport size, and the wait timeout, then runs the action and returns its response.

**Call relations**: Unlike the reader factory methods, this constructs `BrowserComputer` directly for each call because the action is self-contained.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits until a download that Chrome reported has completed, or until the allowed wait time is reached. It returns the download record rather than reading the file bytes.

**Data flow**: It receives arguments describing which download to wait for, passes them to a download reader, and returns a `BrowserDownload` record when the helper resolves it.

**Call relations**: Download event callbacks are wired during `_bootstrap`; this method later uses the accumulated download state through `BrowserDownloads`.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Runs a JavaScript expression inside a specific browser runtime session. This is a low-level way to inspect or compute something inside the page.

**Data flow**: It receives a CDP session id and JavaScript expression, passes them to the runtime reader, and returns the JSON-like value produced by the browser.

**Call relations**: This is a public wrapper around `BrowserRuntime.eval`, used by page and content features that need browser-side execution.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side object. This is used when the system already has a reference to an object in the page and wants to ask it something or perform an operation on it.

**Data flow**: It receives the session id, object id, function source, and optional arguments. It passes all of that to the runtime reader and returns the dictionary-shaped result from Chrome.

**Call relations**: This delegates to `BrowserRuntime.call_on`, supporting lower-level page operations that work with remote browser objects.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a stored page reference into the frame node and backend identifier it points to. In plain terms, it maps a label the automation saved earlier back to the actual page item.

**Data flow**: It receives a tab and reference string, creates a page reader, and asks it to resolve the reference. It returns a pair containing the frame information and an integer identifier.

**Call relations**: This supports actions that first read the page and later need to act on a referenced item. `BrowserPage` owns the reference-resolution rules.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen point for a stored page reference, usually so the automation can click or move to it. It converts a page item reference into coordinates.

**Data flow**: It receives a tab and reference string, creates a page reader, and asks it for the point. The result is an `(x, y)` coordinate pair.

**Call relations**: This is used by higher-level interaction code that needs coordinates. The actual geometry work is delegated to `BrowserPage`.

*Call graph*: calls 1 internal fn (page_reader).


### Page workflow handlers
Handles page-level interruptions and interactions such as JavaScript dialogs, forced or observed downloads, and form filling with file uploads.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

Web page dialogs are small pop-up boxes created by JavaScript. They matter because, while one is open, the page can stop sending normal browser events. In practical terms, it is like someone holding a door shut until you answer their question. If this file did not respond right away, the browser automation could stall.

The file defines a small policy for these dialogs. Simple alerts and “before unload” warnings are automatically accepted, because there is usually no meaningful choice to make. Confirm boxes and prompts are dismissed instead, so the agent does not accidentally agree to a website’s “are you sure?” question or submit a hidden answer.

When a dialog appears, BrowserDialogs.on_dialog reads its type and message, writes a short human-readable note into the browser session’s dialog history, and starts a background task to answer it. The background task calls Chrome DevTools Protocol, often shortened to CDP, which is the browser’s control channel for automation. If that answer fails because the browser session disappeared, timed out, or rejected the command, the code logs a warning instead of crashing the whole run.

The Protocol classes in this file describe what kind of browser connection and browser session this dialog logic needs, without tying it to one concrete implementation.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of a browser-control method that sends a command over CDP, the automation channel used to talk to the browser. In this file, it is needed so dialog code can ask the browser to accept or dismiss a JavaScript dialog.

**Data flow**: It receives the name of a browser command, optional command details, and an optional session identifier for a particular page or target. It sends that request to the browser connection and returns the browser’s JSON-like reply.

**Call relations**: BrowserDialogs._answer_dialog relies on this method after on_dialog has decided whether a dialog should be accepted or dismissed. The file only describes the method through a Protocol, meaning the real connection object is supplied elsewhere.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the live browser-control connection from a browser session. The dialog code uses it when it needs to send the actual accept-or-dismiss command.

**Data flow**: It takes the current browser session object and returns an object capable of sending CDP commands. It does not itself decide anything about dialogs; it simply provides access to the communication path.

**Call relations**: BrowserDialogs._answer_dialog calls this method just before sending Page.handleJavaScriptDialog. The concrete session implementation lives outside this file, while this Protocol states what the dialog logic needs from it.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for the browser session to start an asynchronous task in the background. It lets the code answer a blocking dialog immediately without making the event-handling path wait around.

**Data flow**: It receives a coroutine, which is a piece of asynchronous work that can be run later or alongside other work. It schedules that work and does not return a dialog result directly.

**Call relations**: BrowserDialogs.on_dialog calls this after creating the _answer_dialog task. This keeps the quick event response separate from the browser command that actually accepts or dismisses the pop-up.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog has appeared. It chooses a safe response, records a note for later, and starts the work that unblocks the page.

**Data flow**: It receives dialog details from the browser, including the dialog type and message, plus an optional browser session id. It turns missing values into safe defaults, decides whether to accept or dismiss the dialog, appends a short note such as “alert accepted: message” to the session’s dialog list, and schedules _answer_dialog to send the real browser command.

**Call relations**: This is the main entry point inside this file for dialog events. It reads values from the incoming JSON-like parameter dictionary, calls _answer_dialog to perform the browser-side response, and uses the session’s background-task hook so the dialog can be answered without delaying the rest of the event flow.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This asynchronous helper sends the actual command that accepts or dismisses the open JavaScript dialog in the browser. It is separated from on_dialog so the response can run safely in the background.

**Data flow**: It receives the target session id and a true-or-false accept decision. It asks the browser connection to run the Page.handleJavaScriptDialog command with that decision. If the command succeeds, the dialog is cleared; if it fails because of a browser error, timeout, or runtime problem, it logs a warning and otherwise leaves the program running.

**Call relations**: BrowserDialogs.on_dialog creates and schedules this helper after deciding what should happen. This helper then asks the browser session for its connection and hands off the low-level CDP command to that connection.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download waiting`

This file sits between Chrome's download and network events and the rest of the automation system. Its job is to make downloads predictable. Without it, a page could hang because an intercepted browser request was never released, or a PDF could open inside Chrome's built-in viewer where the agent cannot easily read the real file bytes.

The main class, BrowserDownloads, listens for two kinds of browser events. First, when Chrome pauses a web request through the Chrome DevTools Protocol, or CDP (Chrome's remote-control API), on_fetch_paused decides whether to let the request continue normally or rewrite the response so Chrome treats it as an attachment. This is only done for top-level PDF navigations, not embedded PDFs inside a page, so normal web pages still render correctly. Think of it like a receptionist deciding whether a document should be shown on the wall or put in an envelope for pickup.

Second, it records download lifecycle events. on_download_begin adds a new Download record with Chrome's download id and suggested filename. on_download_progress updates that record as Chrome reports progress. Other code can then ask became_download whether a navigation turned into a download, or wait until a download completes. This file does not read the downloaded file contents; it only tracks which download finished and what Chrome called it.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the one ability this file needs from a CDP connection: sending a named command to Chrome and getting a JSON-style reply back. It is a contract, not the implementation itself.

**Data flow**: A command name, optional parameters, and an optional browser session id go in. The real connection sends that command to Chrome and returns a dictionary-like response. Nothing in this protocol says how the message travels; it only says what shape the call must have.

**Call relations**: BrowserDownloads uses this contract through browser.connection().send when it needs to release paused requests or responses. The actual CDP connection lives elsewhere, but it must match this method so this download code can talk to Chrome.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This protocol method describes how the download code gets access to the browser's CDP connection. It lets BrowserDownloads stay independent from the concrete browser session class.

**Data flow**: No direct input is needed beyond the browser session object. It returns an object that can send CDP commands to Chrome. The session itself is not changed by this protocol definition.

**Call relations**: BrowserDownloads calls this when continuing paused fetch requests and responses. The real browser session supplies the connection; this file only relies on the promised interface.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This protocol method describes how the download code starts a small asynchronous task without blocking the current browser event callback. That matters because paused browser requests must be released quickly.

**Data flow**: An asynchronous job, called a coroutine, goes in. The browser session schedules it to run in the background. There is no direct return value described here, but the scheduled job will later do its work.

**Call relations**: on_fetch_paused uses this to start _continue_request or _continue_response. This keeps the event-handling path short while still making sure Chrome's paused request is resumed.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This protocol method answers whether a browser frame is the main page frame, rather than an embedded frame inside the page. The download logic needs this to avoid forcing embedded documents to download.

**Data flow**: A session id and frame id go in. The browser session checks its frame knowledge and returns true if the frame is the top-level page frame, or false otherwise. It does not itself start or stop downloads.

**Call relations**: on_fetch_paused calls this before forcing a PDF response into a download. This is the guard that lets full-page PDF navigations become files while leaving embedded PDFs alone.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is called when Chrome pauses a web request that the automation layer is allowed to inspect. It always makes sure the request is released, and for top-level PDFs it changes the response so Chrome downloads the file instead of opening the built-in PDF viewer.

**Data flow**: Chrome event data and an optional session id go in. The function reads the request id, response status, response headers, frame id, and content type. If the pause happened before a response is available, it schedules a normal request continuation. If there is a response, it checks whether it is a top-level PDF and schedules response continuation, possibly with a forced attachment header. It returns nothing immediately; the actual browser command runs in the background.

**Call relations**: This is the front door for paused fetch events. It calls _content_type to understand response headers, asks the browser session whether the frame is top-level, and then uses spawn_background to hand off to _continue_request or _continue_response so Chrome does not stay stuck.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This asynchronous helper tells Chrome to let a paused request continue unchanged. It is used when the code does not yet have a response to inspect.

**Data flow**: A session id and Chrome request id go in. The function sends a Fetch.continueRequest command over the browser CDP connection. If Chrome rejects the command, times out, or the runtime is no longer usable, it logs a warning instead of crashing the event flow.

**Call relations**: on_fetch_paused schedules this helper when a paused event is at the request stage. It hands the work to the CDP connection, whose job is to actually tell Chrome to resume loading.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This asynchronous helper tells Chrome to release a paused response, optionally changing its headers so the browser saves it as a download. It is the part that turns top-level PDFs into downloadable attachments.

**Data flow**: A session id, request id, response status code, response headers, and a force flag go in. If forcing is needed, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, while keeping the response code and other headers. Then it sends Fetch.continueResponse to Chrome. On CDP errors, timeouts, or runtime failures, it logs a warning.

**Call relations**: on_fetch_paused schedules this after deciding what should happen to a paused response. It receives the decision from on_fetch_paused, then hands the final instruction to Chrome through the CDP connection.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records the moment Chrome says a new download has started. It keeps enough information for later code to identify the download and report the filename the website suggested.

**Data flow**: Chrome download-start event data goes in. The function reads the download guid and suggested filename, creates a new Download record marked inProgress, and appends it to the browser session's downloads list. It returns nothing.

**Call relations**: This is called by the browser event layer when Chrome reports a download beginning. Later, on_download_progress updates the same record, and wait can return it once it is complete.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This updates the stored state of a download as Chrome reports progress. It is how the system learns that a download has completed, failed, or is still running.

**Data flow**: Chrome progress event data goes in. The function reads the download guid and new state, looks through the saved downloads for a matching guid, and changes that record's state. It returns nothing and leaves non-matching records untouched.

**Call relations**: This is called by the browser event layer after on_download_begin has created a record. BrowserDownloads.wait later depends on these state updates to know when a download is finished.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This checks whether a browser action that may have looked like normal navigation actually turned into a download. It waits briefly because Chrome may report the download just after the navigation event.

**Data flow**: The number of known downloads before the action goes in. The function repeatedly compares that count with the current downloads list for a short grace period, sleeping briefly between checks. It returns true if a new download appears, otherwise false.

**Call relations**: Other navigation logic can call this after triggering a page load to see whether the result was a download instead of a normal page. It does not start downloads itself; it only watches the list filled by on_download_begin.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This waits until at least one browser download has completed, then returns the most recent completed download record. It gives callers a clean answer: which Chrome download finished and what filename was suggested.

**Data flow**: A dictionary of arguments goes in, including an optional timeout. The function converts the timeout with float_or_default, then repeatedly checks the browser session's downloads list for records whose state is completed. If one appears before the deadline, it returns the newest completed record. If none completes in time, it raises TimeoutError. It does not read the downloaded file bytes.

**Call relations**: This is the waiting point used by code that needs to know when a download is done. It relies on on_download_begin and on_download_progress to keep the downloads list current, and it calls float_or_default to safely interpret the caller's timeout value.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This small helper turns a caller-provided timeout value into a floating-point number, or uses a default when no value was provided. It rejects values that are not numeric.

**Data flow**: A JSON-style value and a default number go in. If the value is an integer, decimal number, or non-empty string, it converts it to a float. If the value is missing, it returns the default. If the value is the wrong kind of data, it raises ValidationError.

**Call relations**: BrowserDownloads.wait calls this before starting its deadline timer. That keeps timeout parsing in one clear place and prevents wait from silently accepting unusable input.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper reads HTTP response headers and extracts the content type, such as application/pdf. The download logic uses it to decide whether a response should be forced into a download.

**Data flow**: A list of header-like JSON values goes in. The function searches for a header named Content-Type, ignoring letter case, then returns its value without extra details like character set and in lowercase form. If no content type is found, it returns an empty string.

**Call relations**: on_fetch_paused calls this while inspecting a paused response. Its answer is compared with the file types that should be forced to download, currently PDFs.

*Call graph*: called by 1 (on_fetch_paused).


### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not treat every form field the same way. A text box, a checkbox, a drop-down list, an editable page area, and a file picker all need slightly different browser actions. This file provides a small form toolkit for browser automation so higher-level code can say “put this value in that field” or “attach these files” without knowing the browser’s low-level protocol details.

The main class, BrowserForms, receives a browser session object. That session knows how to find the current page, turn a page reference into a real browser node, and send commands through Chrome DevTools Protocol, often called CDP, which is a control channel for driving Chromium-based browsers.

For normal input, the file resolves a saved page reference, runs a short JavaScript function on the actual element, sets the right property, and fires input and change events so the web page notices the edit. For file uploads, it uses the browser’s file-input command directly. If the reference is wrong, it raises HallucinationError, meaning the automation tried to act on something that is not really available on the page.

The file also includes a helper for reading the byte sizes of files attached to an input. This matters because remote file upload can lag: a file name may appear before the browser has the actual file contents.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected interface for any browser session used by BrowserForms. It should return the browser page or tab that form actions should work against.

**Data flow**: It receives an optional tab identifier. An implementing browser session uses that to choose the right tab, then returns the page object that other form code can inspect and act on.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input rely on this ability before doing anything else. They first ask for the correct page, then use that page to resolve a stored element reference.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is the expected way for BrowserForms to get the browser control connection. That connection is used to send low-level browser commands.

**Data flow**: It takes no extra input from the caller. The implementing session returns a CDP connection, which is the channel used to ask the browser to resolve elements or set files on a file input.

**Call relations**: BrowserForms uses this whenever it must talk directly to the browser engine. attached_sizes and input use it to turn a DOM node into a JavaScript object, and upload_file uses it to attach files to an input.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is the expected way to run a JavaScript function on a specific browser object. BrowserForms uses it when setting ordinary form values or reading file sizes from a file input.

**Data flow**: It receives a browser session id, an object id inside the browser, a JavaScript function as text, and optional arguments. The implementation runs that function on the chosen object and returns the result as JSON-like data.

**Call relations**: BrowserForms.attached_sizes calls this after resolving a file input so it can inspect the files attached to it. BrowserForms.input calls it after resolving a field so it can set the field’s value in a page-aware way.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is the expected way to turn a saved page reference into the real browser node it points to. It protects the rest of the code from needing to know how references are stored.

**Data flow**: It receives a page object and a reference string from a caller. The implementation looks up that reference and returns both the browser node information and the backend node id that CDP commands need.

**Call relations**: Every BrowserForms action starts from a reference supplied by outside automation. attached_sizes, upload_file, and input all use resolve_ref to convert that reference into something the browser can actually act on.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what file sizes a file input is actually holding inside the browser. It is useful for confirming that a remote upload has finished and did not just leave behind a filename with no file contents.

**Data flow**: It receives a JSON-like argument map containing a page reference and possibly a tab id. It chooses the tab, resolves the reference to a browser node, asks the browser for a JavaScript object for that node, runs a small script that reads each attached file’s size, and returns a list of integer byte sizes. If the script result is not a list, it returns an empty list.

**Call relations**: A caller uses this after or during file upload to verify what the browser really has. The method uses _tab_id to normalize the tab value, uses the session to find the page and element, sends a CDP resolve command through the connection, then uses call_on to run the file-size JavaScript on the input.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a web page’s file input. It gives automation a reliable way to use upload controls without clicking through the operating system’s file picker.

**Data flow**: It receives a JSON-like argument map with a page reference, a list of file paths, and possibly a tab id. It normalizes the tab id, checks and converts the reference and file list, resolves the reference to a browser node, and sends a browser command that sets those files on the input. It returns the reference and file paths it used. If the reference is not a file input, it raises HallucinationError with advice to re-read the page and use a real file input reference.

**Call relations**: This is called when higher-level automation wants to upload files through the page. It uses _tab_id for tab selection, the browser session to find the target element, and the CDP connection to perform the special file-input operation. When the browser rejects the operation, it turns the low-level CdpError into a clearer HallucinationError for the automation layer.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a normal form-like element, such as a text field, checkbox, radio button, drop-down list, or editable page region. It also triggers the page events that websites usually listen for when a person changes a field.

**Data flow**: It receives a JSON-like argument map containing a page reference, a value, and possibly a tab id. It selects the tab, resolves the reference into a real browser node, asks the browser for an object id for that node, then runs JavaScript that sets the value in the right way for that kind of element. The result is the value reported back by the browser. If the reference cannot be resolved, it raises HallucinationError telling the caller to re-read the page and use a current reference.

**Call relations**: Higher-level automation calls this when it wants to fill in or change a form field. The method uses _tab_id to interpret the tab choice, uses the session to locate the element, sends a CDP resolve command, and then hands off to call_on to run the JavaScript that performs the actual edit.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id supplied in loose JSON form into either an integer tab id or no tab choice at all. It lets callers provide the id as a number or a numeric string.

**Data flow**: It receives a JSON-like value that may be an integer, float, string, or something missing or unusable. Integers pass through, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes None.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input all call this at the start of their work. It gives them a clean tab id before they ask the browser session for the target page.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### Tab navigation state
Maintains browser tab state and implements tab lifecycle and navigation actions used by the session layer.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `browser request handling and tab event processing`

A browser controlled by automation is not just one page. Tabs can be created by the user, by websites, or by the automation code itself, and tabs can disappear at any time. This file is the project’s tab desk clerk: it records which tabs exist, attaches to new ones, forgets closed ones, and asks the browser what each tab is showing.

It talks to the browser through CDP, the Chrome DevTools Protocol, which is Chrome’s remote-control API. The file does not own the network connection itself; instead, it expects a session object that can send commands, wait for browser events, evaluate JavaScript, and read downloads.

The main class, BrowserTabs, is the working controller. It listens for target events, where a “target” is Chrome’s word for something like a page tab. When a new page target appears, BrowserTabs attaches to it, enables page, DOM, network, and lifecycle events, sets the viewport size, and starts watching document fetches for downloads. Navigation is careful: it normalizes plain addresses like “example.com” into full URLs, supports “back” and “forward,” waits for the page to settle, and treats download-triggering navigations as a valid outcome. Without this file, the system would lose track of open tabs and could act on the wrong page or assume a page was ready before it had loaded.

#### Function details

##### `normalize_url`  (lines 25–30)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It leaves special commands like “back,” “forward,” and already complete URLs alone, but adds “https://” to plain website names.

**Data flow**: It receives a text URL or command. It checks whether the text is a reserved navigation word, the blank page address, or already starts with a URL scheme such as “http:”. If none of those are true, it returns the same text with “https://” added in front.

**Call relations**: BrowserTabs.navigate calls this before deciding how to move the tab. This keeps the navigation path simple because later code can assume it has either a special command or a browser-ready URL.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 34–39)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the project’s in-memory record for one browser tab. It stores the browser’s identifiers for the tab and prepares per-tab state such as keyboard state and known frames.

**Data flow**: It receives a target ID and a session ID from the browser. It saves them, creates a fresh KeyboardState, starts an empty counter for frame names, and creates a root FrameNode for the tab’s main frame. The result is a Tab object ready for later actions.

**Call relations**: BrowserTabs.attach_tab creates Tab objects after the browser confirms that automation has attached to a page target. Other tab functions then use this object as their handle for navigation, closing, and reading page information.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 41–44)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame inside a tab a stable small number. This is useful when the system needs a human-friendly or repeatable label for frames that originally have browser-generated IDs.

**Data flow**: It receives a frame ID. If the frame has not been seen before, it assigns the next number; if it has been seen, it reuses the existing number. It returns that number.

**Call relations**: This is a helper on the Tab model. It is not called by the listed functions in this file, but it supports other page or frame code that needs consistent frame numbering.


##### `BrowserTabCdp.send`  (lines 55–60)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected way to send a command to the browser over CDP, Chrome’s remote-control protocol. This is a protocol method, meaning it describes what another object must provide rather than implementing it here.

**Data flow**: A caller supplies a browser command name, optional parameters, and optionally a session ID for a specific tab. The implementing connection sends that command to Chrome and returns the response as a JSON-like dictionary.

**Call relations**: BrowserTabs relies on this method throughout: attaching to tabs, enabling browser domains, creating tabs, navigating, reading history, and closing tabs all go through this connection contract.


##### `BrowserTabCdp.expect`  (lines 62–62)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how code asks the browser connection to watch for one or more upcoming events. It is used when an action should wait until Chrome reports that something happened.

**Data flow**: A caller gives one or more event names and, optionally, a tab session ID. The implementation returns a future, which is a placeholder for a result that will arrive later when one of those events occurs.

**Call relations**: Navigation helpers use this before sending a navigation command, like setting a kitchen timer before putting food in the oven. BrowserTabs._goto and BrowserTabs._history_step then wait on the returned future.


##### `BrowserTabCdp.wait`  (lines 64–68)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how code waits for an expected browser event with a time limit. This prevents the automation from hanging forever if Chrome never sends the event.

**Data flow**: It receives a future from expect and a timeout in seconds. The implementation waits until the event arrives or the timeout is reached, then returns the event data or raises an error.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this after issuing navigation commands. It is part of the careful “ask Chrome, then wait for proof” navigation flow.


##### `BrowserTabSession.connection`  (lines 78–78)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how BrowserTabs gets the CDP connection object used to talk to the browser. It is a required doorway from tab logic to browser communication.

**Data flow**: It takes no extra input beyond the session object. The implementation returns an object that can send commands, expect events, and wait for them.

**Call relations**: Almost every active BrowserTabs operation calls this indirectly or directly when it needs to issue browser commands. This keeps BrowserTabs independent from the exact connection implementation.


##### `BrowserTabSession.download_reader`  (lines 80–80)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how BrowserTabs gets access to the download tracker. This matters because some navigations do not load a page; they start a file download instead.

**Data flow**: It reads the current session and returns a BrowserDownloads object. That object can tell whether a navigation caused a new download.

**Call relations**: BrowserTabs._goto uses this after a navigation reports an error. If the “error” was really a download starting, the navigation is treated as successful enough instead of failing.


##### `BrowserTabSession.eval_js`  (lines 82–82)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how tab code runs JavaScript inside a page and receives the result. BrowserTabs uses it for simple page facts such as the current URL and title.

**Data flow**: A caller supplies a tab session ID and a JavaScript expression. The implementation runs that expression in the page and returns the resulting JSON-like value.

**Call relations**: BrowserTabs.tab_info depends on this to ask the page itself for location.href and document.title. That information is then used by navigation results, tab lists, and title lists.


##### `BrowserTabs.remember_initial_targets`  (lines 90–94)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser page targets already existed before this tab tracker started listening. This prevents old tabs from being mistaken for newly opened tabs.

**Data flow**: It receives a browser response containing target information. It extracts each target ID and stores the set in browser.tab_events.initial_targets. The stored set becomes a baseline for later target-created events.

**Call relations**: This is used during setup of tab event tracking. Later, BrowserTabs.on_target_created compares new events against this remembered baseline before adding anything to the created-target queue.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 96–108)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when Chrome reports that a new page target exists and queues it to be attached as a tab. It filters out non-page targets and targets that were already known.

**Data flow**: It receives event parameters from Chrome. It looks for a targetInfo dictionary, checks that the target is a page, has a string target ID, was not part of the initial set, and has not already been queued. If it passes those checks, the target ID is appended to created_targets.

**Call relations**: This is an event callback. BrowserTabs.sync later consumes the created_targets queue and calls BrowserTabs.attach_tab for each new target.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 110–113)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when Chrome reports that a target has gone away. It marks that target so the local tab list can be cleaned up.

**Data flow**: It receives event parameters from Chrome. If the event contains a string target ID, it adds that ID to destroyed_targets. It does not immediately edit the tab list.

**Call relations**: This is an event callback that leaves cleanup to BrowserTabs.sync. That keeps event handling quick and lets sync update the tab list in one controlled place.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 115–119)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when Chrome says its main frame has started loading. The main frame is the top page itself, not an embedded iframe.

**Data flow**: It receives event data and the session ID that produced the event. If there is no session ID, it ignores the event. If the frame ID belongs to the tab’s top-level frame, it tells the settle tracker that this session is loading.

**Call relations**: This event callback uses BrowserTabs.is_top_level_frame to avoid reacting to subframe noise. The settle tracker later uses this loading signal to decide when navigation is finished enough to continue.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 121–123)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having reached the DOM content stage, meaning the page’s basic document structure is ready. This is one piece of the page-readiness picture.

**Data flow**: It receives event data and a session ID. If the session ID is present, it tells the settle tracker that the page has loaded its document content. It returns nothing.

**Call relations**: This is called from the browser event stream. BrowserTabs.navigate later waits on the settle tracker, which uses this and other event signals to decide when the page is ready.


##### `BrowserTabs.on_lifecycle`  (lines 125–129)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when Chrome reports an important visual lifecycle event for the main frame. “Painted” means the browser has drawn meaningful content on screen.

**Data flow**: It receives lifecycle event parameters and a session ID. It ignores events without a session ID and events that are not in the selected paint-related event set. For top-level frame events, it tells the settle tracker that the page has painted.

**Call relations**: This event callback works with BrowserTabs.on_frame_loading and BrowserTabs.on_dom_content. Together they feed the settle tracker so BrowserTabs.navigate can wait for a page that is not merely requested, but actually usable.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 131–134)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame event belongs to a tab’s main page frame. This keeps loading and paint decisions from being confused by embedded frames.

**Data flow**: It receives a session ID and a frame ID. It scans the known tabs and returns true only when it finds a tab whose session ID matches and whose target ID matches the frame ID.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating settle state. It is the small gatekeeper that separates main-page events from iframe events.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 136–156)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the automation system to an existing browser page target and prepares it for use. Attaching is like getting a remote control for that tab.

**Data flow**: It receives a browser target ID. It asks Chrome to attach to that target and gets back a session ID, initializes the session, enables fetch watching for documents, sets the viewport size, and returns a new Tab object for that target and session.

**Call relations**: BrowserTabs.create, BrowserTabs.page, and BrowserTabs.sync call this whenever a new or previously unattached tab must become part of the local tab list. It hands off setup details to BrowserTabs.init_session before constructing the Tab.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 158–163)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for a tab session. Without these switches, Chrome would not send the page, DOM, network, and lifecycle events this system depends on.

**Data flow**: It receives a session ID. It sends several enable commands to Chrome for that session: page events, lifecycle events, DOM access, and network events. It returns nothing, but the browser session is now instrumented.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target. It is the setup step before the tab can be monitored, navigated, and inspected.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 165–178)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list back in line with the browser’s latest create and destroy events. It is the housekeeping pass that turns event queues into actual tab records.

**Data flow**: It reads destroyed_targets and removes matching tabs from browser.tabs, then clears that destroyed set. It then consumes created_targets one by one, skips any already known tab, and tries to attach to each new target. Successful attaches are added to browser.tabs; failed attaches are ignored.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before reporting or choosing tabs. It receives work indirectly from BrowserTabs.on_target_created and BrowserTabs.on_target_destroyed.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 180–193)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be acted on, creating a blank one if no tabs exist. It also validates a requested tab number so callers do not accidentally use a nonexistent tab.

**Data flow**: It receives an optional tab ID. First it syncs the local tab list. If there are no tabs, it asks Chrome to create a blank target and attaches to it. If no tab ID was requested, it returns the most recently opened tab; otherwise it returns the requested tab or raises a validation error if the number is out of range.

**Call relations**: BrowserTabs.navigate and BrowserTabs.close call this to choose their target tab. It calls BrowserTabs.sync and may call BrowserTabs.attach_tab if it has to create the first tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 195–207)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new destination, or backward or forward in its history, and waits until the page has settled. It is the main public navigation operation in this file.

**Data flow**: It receives a URL-like string and an optional tab ID. It gets the target tab, normalizes the destination, resets the settle tracker, then either goes back, goes forward, or navigates to the URL. After that it waits for the settle tracker and returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create uses this after opening a new tab. Internally it calls BrowserTabs.page, normalize_url, BrowserTabs._history_step or BrowserTabs._goto, and finally BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 209–223)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level “go to this URL” browser command for one tab. It carefully distinguishes between real navigation failure and a navigation that turned into a file download.

**Data flow**: It receives a Tab and a URL. It notes how many downloads already existed, starts waiting for the DOM content event, then sends Page.navigate to Chrome. If Chrome reports an error, it cancels the wait and checks whether a new download began; if so, it accepts that outcome, otherwise it raises an error. If Chrome says there is no loader ID, it cancels the wait because no full page load is expected. Otherwise it waits for DOM content with a timeout.

**Call relations**: BrowserTabs.navigate calls this for normal URL navigation. It uses the CDP connection’s expect, send, and wait behavior, and consults the download reader when navigation might actually mean downloading a file.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 225–242)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab one step backward or forward in its browsing history when possible. If there is no entry in that direction, it quietly does nothing.

**Data flow**: It receives a Tab and a step number, usually -1 for back or 1 for forward. It asks Chrome for the tab’s navigation history, checks the current index, calculates the desired entry, and returns early if that entry is outside the history list. Otherwise it starts waiting for a navigation event, tells Chrome to navigate to the selected history entry, and waits for confirmation with a timeout.

**Call relations**: BrowserTabs.navigate calls this when the normalized target is “back” or “forward.” It is the history-specific counterpart to BrowserTabs._goto.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 244–251)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. This gives callers a simple summary of what the tab is showing.

**Data flow**: It receives a Tab. It runs a small JavaScript expression in that tab asking for location.href and document.title, checks that the result is a dictionary-like value, and returns a dictionary with string URL and title fields.

**Call relations**: BrowserTabs.navigate uses this for its return value after navigation. BrowserTabs.tabs_context and BrowserTabs.tab_titles use it to build user-facing tab summaries.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 253–260)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a full snapshot of open tabs, including which one is considered current. This is useful for showing the user or another system what browser state looks like now.

**Data flow**: It first syncs the tab list. It treats the last tab in the list as current, then loops through all tabs, asks each for its URL and title, and returns a dictionary containing the current tab index and a list of tab records with IDs, active flags, URLs, and titles.

**Call relations**: BrowserTabs.close calls this after closing a tab so it can return the updated tab state. It depends on BrowserTabs.sync and BrowserTabs.tab_info.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 262–267)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of all known tabs. This is a compact summary when callers do not need full tab details.

**Data flow**: It loops over browser.tabs, asks BrowserTabs.tab_info for each tab, pulls out the title, substitutes an empty string if there is no title, and returns the list of title strings.

**Call relations**: This is a convenience method built on top of BrowserTabs.tab_info. It does not sync first, so it reflects the tab list already stored in the session.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 269–280)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and navigates it to the requested URL. If navigation fails, it raises a special error that includes the ID of the tab that was left open.

**Data flow**: It receives an optional URL, defaulting to a blank page. It asks Chrome to create a blank target, attaches to it, stores the new Tab in browser.tabs, finds its tab ID, and then calls BrowserTabs.navigate to move it to the requested URL. On success it returns the tab ID plus the tab’s URL and title; on failure it raises TabLeftOpen with details.

**Call relations**: This is a public tab-opening operation. It combines Chrome target creation, BrowserTabs.attach_tab, and BrowserTabs.navigate into one flow, while preserving enough error information for cleanup or reporting.

*Call graph*: calls 3 internal fn (__init__, attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 282–287)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a tab and returns the updated list of open tabs. It also clears out-of-process iframe session records, because closing a tab can invalidate those frame connections.

**Data flow**: It receives an argument dictionary that may contain a tab_id. It converts that value with _tab_id, uses BrowserTabs.page to find the tab, tells Chrome to close the target, removes that Tab object from browser.tabs, resets browser.oop_sessions to an empty dictionary, and returns BrowserTabs.tabs_context.

**Call relations**: This is the public close operation. It relies on _tab_id for forgiving input parsing, BrowserTabs.page for tab lookup, and BrowserTabs.tabs_context for the final response.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 290–299)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loosely supplied tab ID into either an integer or no tab ID. This lets callers pass tab IDs as numbers or numeric strings.

**Data flow**: It receives a JSON-like value. If it is an integer, it returns it. If it is a float, it truncates it to an integer. If it is a non-empty string, it parses it as an integer. For anything else, it returns None.

**Call relations**: BrowserTabs.close calls this before asking BrowserTabs.page for the tab to close. It is a small input-cleaning helper at the edge of the close flow.

*Call graph*: called by 1 (close).

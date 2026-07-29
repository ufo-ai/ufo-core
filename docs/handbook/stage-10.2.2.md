# Chrome DevTools browser session and protocol plumbing  `stage-10.2.2`

This stage is the browser automation “plumbing” used while the system is doing its main work. It gives the rest of the project one controlled way to talk to Chrome. The session file is the central coordinator: it opens the debugging connection to Chrome, gathers the tools, and keeps track of tabs and downloads. The cdp file is the wire to Chrome DevTools Protocol, Chrome’s automation control channel. It sends commands, waits for replies, and routes events. The wire file checks the JSON messages at the border, so bad or unexpected data is caught early.

Around that core, smaller helpers handle common browser problems. Tabs opens, closes, switches, and navigates pages. Downloads notices files being saved, waits for them, and turns Chrome’s PDF viewer pages into real PDF downloads. Dialogs prevents pop-up boxes from freezing the run by accepting or dismissing them safely. Settle decides when an action has finished enough to move on, ignoring unrelated background noise. Runtime runs small JavaScript snippets in the page and reports browser errors as normal Python errors.

## Files in this stage

### Session coordination
The session layer owns the live Chrome connection, tracks browser resources, and exposes the automation tools used by the rest of the system.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `active while a browser automation turn/session is open`

This file is the front desk for browser automation. Chrome exposes a control channel called CDP, short for Chrome DevTools Protocol, which is the same kind of interface developer tools use to inspect pages. BrowserSession opens that channel, subscribes to browser events, and gives the rest of the project simple actions such as navigate, read_page, find, upload_file, click/type through computer control, manage tabs, and wait for downloads.

The session keeps the shared state that many browser actions need: open tabs, downloads in progress, frame sessions, dialog messages, whether the browser is on macOS, and background tasks. Think of it like a hotel concierge desk: guests ask for many different services, but the desk knows which room, which staff member, and which current events matter.

On startup, it resolves the CDP endpoint into a WebSocket address, connects, configures download behavior, starts listening for target, network, page, dialog, and download events, then creates a blank tab. Most public methods are intentionally thin. They create a specialist helper, such as BrowserTabs, BrowserContent, BrowserForms, BrowserDownloads, BrowserRuntime, or BrowserPage, and pass the request along. Closing the session tears down tabs, the connection, background work, and all temporary state so the next turn starts cleanly.

#### Function details

##### `BrowserSession.__init__`  (lines 48–66)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None, download_dir: str='') -> None
```

**Purpose**: Creates an empty browser session object and records the basic settings it will need later. It does not connect to Chrome yet; it only prepares the containers for tabs, downloads, dialogs, and other per-session state.

**Data flow**: It receives an optional CDP endpoint, optional model name, and download directory. It chooses the screen coordinate size to use, initializes empty lists and maps for browser state, creates fresh settle and tab-event trackers, and leaves the live connection unset until open is called.

**Call relations**: BuaSurface._open creates this object before the browser tools can be used. During construction it asks the coordinate helper for the right model coordinate space and creates the trackers that later event listeners will update.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 68–75)

```
async def open(self) -> None
```

**Purpose**: Starts the live browser connection if it is not already open. It also makes startup safe by cleaning up if anything fails partway through.

**Data flow**: It checks whether a connection already exists. If not, it runs the bootstrap sequence; if bootstrap raises an error, it closes anything that was partially opened and then passes the error back to the caller.

**Call relations**: The async context manager calls this on entry. It hands the real setup work to _bootstrap and relies on close to undo partial setup if Chrome cannot be reached or configured.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 77–113)

```
async def _bootstrap(self) -> None
```

**Purpose**: Performs the full Chrome setup: connect to CDP, attach event listeners, configure downloads, discover tabs, and create the first blank page. This is the main startup routine for the session.

**Data flow**: It reads the configured CDP endpoint and headers, turns the endpoint into a WebSocket URL, opens the connection, asks Chrome for version details, then wires browser events to the tab, download, dialog, and settle trackers. It enables target discovery, creates an about:blank tab, attaches to it, and stores it as the session's initial tab.

**Call relations**: open calls this when the session starts. It creates reader/helper objects through tab_reader, download_reader, and dialog_reader so their event callbacks can be registered with the CDP connection.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 3 external calls (__init__, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 115–135)

```
async def close(self) -> None
```

**Purpose**: Shuts down the browser session and clears all temporary state. It is designed to be safe even if the session only opened halfway or Chrome refuses some close commands.

**Data flow**: It tries to close each known tab through CDP, closes the connection, then resets the connection, tabs, frame sessions, downloads, dialog list, settle tracker, tab events, and scroll flag. It also cancels background tasks started by the session.

**Call relations**: __aexit__ calls this when leaving the async context, and open calls it after failed startup. It recreates the same clean trackers used at construction so later use starts from an empty state.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 1 external calls (__init__).


##### `BrowserSession.__aenter__`  (lines 137–139)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python's async with pattern. Entering the block automatically opens the browser session.

**Data flow**: It takes the session object, calls open, waits until startup completes, and then returns the ready-to-use session.

**Call relations**: This is the entry side of the context-manager flow. It delegates the actual connection work to open so callers do not have to remember to start the session manually.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 141–142)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the browser session automatically when an async with block ends. This helps prevent leftover browser tabs, connections, or background tasks.

**Data flow**: It receives any exception information from the leaving block, ignores those details here, and calls close to tear down session resources.

**Call relations**: This is the exit side of the context-manager flow. It hands cleanup to close, whether the block ended normally or because of an error.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 144–147)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live CDP connection for helpers that need to talk directly to Chrome. It protects callers from accidentally using a session before it is open.

**Data flow**: It checks the stored connection. If one exists, it returns it; if not, it raises BrowserUnavailable with a clear message.

**Call relations**: Reader objects use this kind of access when they need to send CDP commands. It is a guardrail around the shared connection created during _bootstrap.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 149–152)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts an asynchronous background job and remembers it so the session can cancel it later. This prevents fire-and-forget work from being lost outside the session's cleanup rules.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, stores that task in the session's background-task set, and arranges for the task to remove itself from the set when finished.

**Call relations**: Other session helpers can use this when they need work to continue in the background. close later cancels any tasks still recorded here.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 154–155)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser event refers to the main page frame rather than a nested frame. This matters because page-load events from embedded frames should not always count as the whole page loading.

**Data flow**: It receives a CDP session id and frame id, creates a tab reader, and asks that reader to decide whether the frame is top level.

**Call relations**: It is a convenience wrapper around BrowserTabs. The session provides the shared state, while the tab reader knows the rules for frame ownership.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 157–158)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready to report the browser events this system needs. This is part of preparing tabs and frames for automation.

**Data flow**: It receives a CDP session id and passes it to a BrowserTabs helper, which performs the session-specific setup.

**Call relations**: When tab-related code needs a new CDP session prepared, this method creates the tab reader and delegates the detailed setup to it.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 160–161)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Finds and returns the active tab, or a specific tab if an id is supplied. Other browser actions use this to know which page they should operate on.

**Data flow**: It receives an optional tab id, asks the tab reader to locate the right Tab object, and returns that Tab.

**Call relations**: This wraps BrowserTabs.page. It keeps tab lookup available through the session while leaving tab-selection rules in the tab helper.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 163–164)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Loads a URL in a browser tab. It is the session-level entry point for moving the browser to a new page.

**Data flow**: It receives a URL and optional tab id, passes them to the tab reader, and returns the JSON-style result reported by the tab navigation logic.

**Call relations**: Tool callers use this session method for navigation. It delegates to BrowserTabs, which knows how to choose the tab and send the correct browser command.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 166–167)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a plain data summary for a tab. This gives callers useful information about a tab without exposing the internal Tab object directly.

**Data flow**: It receives a Tab object, asks the tab reader to format information about it, and returns a dictionary-like JSON result.

**Call relations**: This is a small bridge from session-level code to BrowserTabs. The session owns the tab list, while the tab reader knows how to describe one tab.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 169–170)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns context about the current set of browser tabs. This helps tools or agents understand what pages are open.

**Data flow**: It creates a tab reader, asks it for the tab context, and returns that context as JSON-style data.

**Call relations**: It delegates the details to BrowserTabs, which reads the session's tab state and formats it for callers.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 172–173)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of the open tabs. This is a lightweight way to summarize the browser's current state.

**Data flow**: It creates a tab reader, asks it for tab titles, and returns the list of strings.

**Call relations**: It is a convenience method over BrowserTabs. BrowserSession provides the state; BrowserTabs extracts the title information.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 175–176)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper for tab-related work. This helper is the specialist for creating, closing, selecting, and tracking tabs.

**Data flow**: It takes the current session and the fixed viewport size, constructs a BrowserTabs object, and returns it.

**Call relations**: _bootstrap and nearly all tab methods call this whenever they need tab behavior. The helper receives the session so it can read and update shared tab state.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 178–179)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for page-structure work. This is used when code needs to resolve page references or find screen points for elements.

**Data flow**: It packages the session, viewport, and maximum frame depth into a BrowserPage object and returns it.

**Call relations**: resolve_ref and ref_point call this. The session stays as the state holder, while BrowserPage performs page-tree and frame calculations.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 181–182)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading and searching page content. This helper turns browser pages into text, trees, and search results.

**Data flow**: It passes the session into a new BrowserContent object and returns that object.

**Call relations**: tree, read_page, get_page_text, and find call this before delegating their content-related requests.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 184–185)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for download events and waiting. This helper understands when a Chrome download starts, progresses, and finishes.

**Data flow**: It passes the session and the maximum wait time into a BrowserDownloads object and returns it.

**Call relations**: _bootstrap uses it to register download event callbacks, and wait_for_download uses it when a caller wants to wait for a completed download report.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 187–188)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for JavaScript browser dialogs, such as alerts or prompts. This lets the session capture dialog activity from Chrome events.

**Data flow**: It passes the session into a new BrowserDialogs object and returns it.

**Call relations**: _bootstrap calls this so the dialog helper's callback can be registered for dialog-opening events.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 190–191)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form-related actions. This includes entering values and preparing file uploads.

**Data flow**: It passes the session into a new BrowserForms object and returns it.

**Call relations**: upload_file, attached_sizes, and form_input call this before handing off form work to the specialist helper.

*Call graph*: called by 3 (attached_sizes, form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 193–194)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript inside the browser. This is used for direct script evaluation and function calls on browser-side objects.

**Data flow**: It passes the session into a new BrowserRuntime object and returns it.

**Call relations**: eval_js and call_on call this. The runtime helper uses the session's live CDP connection to execute JavaScript in the right browser session.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 196–197)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, defaulting to a blank page. It exposes tab creation as a session-level tool action.

**Data flow**: It receives a URL, or uses about:blank if none is provided, passes that to the tab reader, and returns the created-tab result as JSON-style data.

**Call relations**: This delegates to BrowserTabs.create. The session method is the public doorway; the tab reader performs the Chrome-specific tab creation.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 199–200)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab based on caller-provided arguments. This lets higher-level tools remove tabs without knowing Chrome's low-level commands.

**Data flow**: It receives a dictionary of tab-close arguments, gives it to the tab reader, and returns the close result as JSON-style data.

**Call relations**: This delegates to BrowserTabs.close. The tab reader interprets the arguments and updates tab state through the session.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 202–203)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Prepares or performs a file upload through a page form. This is the session-level entry point for upload actions.

**Data flow**: It receives upload arguments, creates a form reader, passes the arguments along, and returns the upload result.

**Call relations**: BuaSurface._settle_upload calls this during upload handling. The session routes the request to BrowserForms, which knows the page/form details.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.attached_sizes`  (lines 205–206)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: Reports the sizes of files attached for upload. This helps the upload flow confirm what was attached without reading the file contents here.

**Data flow**: It receives arguments describing the upload attachments, asks the form reader to compute or retrieve their sizes, and returns a list of sizes.

**Call relations**: BuaSurface._settle_upload calls this as part of upload settling. BrowserSession delegates the form-specific work to BrowserForms.

*Call graph*: calls 1 internal fn (form_reader); called by 1 (_settle_upload).


##### `BrowserSession.tree`  (lines 208–209)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a text tree of page content, optionally filtered by type. This gives callers a structured view of what is on the page.

**Data flow**: It receives page-reading arguments and a filter name, creates a content reader, and returns the tree string produced by that helper.

**Call relations**: Tool callers use this through the session when they need page structure. BrowserContent does the actual page inspection and formatting.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 211–212)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a richer JSON-style description. This is useful when an agent needs to understand visible content and page structure.

**Data flow**: It receives read arguments, sends them to the content reader, and returns the resulting page data.

**Call relations**: This is a session-level wrapper over BrowserContent.read_page. The helper uses the session's browser connection and tab state to gather content.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 214–215)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts text from the page. This gives callers a simpler text-focused view when they do not need the full page structure.

**Data flow**: It receives text-reading arguments, passes them to the content reader, and returns text data in JSON-style form.

**Call relations**: The session exposes the method; BrowserContent performs the extraction from the browser page.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 217–218)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches within the page and can optionally report completion through a FindCompleter callback. This supports browser-tool search behavior.

**Data flow**: It receives search arguments and an optional completion helper, passes both to the content reader, and returns the search result as JSON-style data.

**Call relations**: This delegates to BrowserContent.find. The optional completer lets the broader tool flow be notified when the find operation reaches its conclusion.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 220–221)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Enters data into a form field or otherwise performs a form input action. It is the session-level route for typing into page forms.

**Data flow**: It receives form-input arguments, gives them to the form reader, and returns the result of that input operation.

**Call relations**: Tool callers reach form entry through this method. BrowserForms handles the page-specific details using the session's shared browser state.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 223–224)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a computer-control action, such as mouse or keyboard interaction, against the browser viewport. This is for actions that treat the browser like a screen to interact with.

**Data flow**: It receives action arguments, creates a BrowserComputer with the session, viewport, and wait limit, runs the action, and returns the JSON-style result.

**Call relations**: Unlike the simple reader factory methods, this constructs BrowserComputer directly for the one action. BrowserComputer uses the session connection and state to carry out low-level interaction.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 226–227)

```
async def wait_for_download(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: Waits for a download to finish and returns a download record. The file notes that the session reports the completed download rather than reading the bytes itself.

**Data flow**: It receives wait arguments, creates a download reader, waits according to those arguments and the session's download events, then returns a BrowserDownload object.

**Call relations**: This uses BrowserDownloads.wait. The download reader relies on events that _bootstrap registered when the session opened.

*Call graph*: calls 1 internal fn (download_reader).


##### `BrowserSession.eval_js`  (lines 229–230)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression in a particular browser session. This is a direct way for helpers to ask the page runtime for a value.

**Data flow**: It receives a CDP session id and JavaScript expression, passes them to the runtime reader, and returns the JSON-like value reported by Chrome.

**Call relations**: This delegates to BrowserRuntime.eval. The session supplies access to the live browser connection while the runtime helper handles the JavaScript execution details.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 232–239)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side object. This is used when code already has a reference to a page object and wants to run a function against it.

**Data flow**: It receives a CDP session id, object id, function source text, and optional arguments. It passes those to the runtime reader and returns Chrome's JSON-style response.

**Call relations**: This delegates to BrowserRuntime.call_on. The session keeps the public method small while the runtime helper knows how to address browser-side objects.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 241–242)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a page reference string into the concrete frame node and element index it points to. This helps other actions move from a human/tool reference to a specific page location.

**Data flow**: It receives a Tab and a reference string, asks the page reader to resolve the reference, and returns the matching frame node plus index.

**Call relations**: This calls BrowserPage.resolve_ref through page_reader. BrowserPage understands page frames and reference syntax, while the session provides the current browser state.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 244–245)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the screen point for a referenced page element. This is useful when a later action needs to click or point at that element.

**Data flow**: It receives a Tab and reference string, asks the page reader to compute the point, and returns x and y coordinates.

**Call relations**: This delegates to BrowserPage.ref_point through page_reader. It connects page references used by content tools with coordinate-based computer actions.

*Call graph*: calls 1 internal fn (page_reader).


### Browser interaction services
These helpers manage the visible browser workspace, including downloads, tabs, pop-up dialogs, and deciding when page actions have settled.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`io_transport` · `request handling and download waiting`

Browsers do not treat every file-like page as a download. A PDF, for example, may open inside Chrome’s own viewer. That is useful for a person, but not for an agent that needs the actual downloaded file. This file sits between browser events and the rest of the system, like a traffic officer at a checkpoint: every paused network request must be waved through, but a top-level PDF response can be given a new header that says “download this as an attachment.”

The file defines small shared shapes for download data and for the browser session it expects to talk to. The main class, BrowserDownloads, reacts to three kinds of browser news. When Chrome pauses a fetch response, it decides whether to continue normally or force a PDF download. When Chrome announces that a download has started, it records the download’s browser ID, suggested filename, and current state. When Chrome reports progress, it updates that state.

It also provides waiting helpers. One helper briefly checks whether a navigation turned into a download. Another waits until some recorded download reaches the completed state, or raises a timeout if nothing finishes. This file does not read the downloaded bytes; it only tracks Chrome’s download events and reports which browser download completed.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the browser connection’s way to send a Chrome DevTools Protocol command. Chrome DevTools Protocol, or CDP, is the message API used to control and inspect Chrome.

**Data flow**: The caller provides a command name, optional JSON-like parameters, and optionally a browser session ID. The connection sends that command to Chrome and returns a JSON-like dictionary response.

**Call relations**: BrowserDownloads uses this method through BrowserDownloadSession.connection when it needs to release a paused request or response. In this file, _continue_request and _continue_response are the places that hand commands to this connection.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This protocol method gives BrowserDownloads access to the live browser control connection. It lets this file send CDP commands without knowing the concrete browser session class.

**Data flow**: It reads the session object and returns an object that can send browser commands. No download data is changed by this method itself.

**Call relations**: BrowserDownloads calls it inside _continue_request and _continue_response after on_fetch_paused has decided that a paused browser request must be released.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This protocol method starts an asynchronous task in the background. It is used because paused browser requests must be released, but the event callback itself should not block while waiting for Chrome.

**Data flow**: It receives a coroutine, which is a not-yet-finished asynchronous job. The session schedules that job to run separately, and the callback can return immediately.

**Call relations**: on_fetch_paused uses this method to launch _continue_request or _continue_response. That keeps the browser from hanging while still letting the release command run.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This protocol method answers whether a browser frame is the main page frame rather than an embedded frame. That distinction matters because only a top-level PDF navigation should be forced into a download.

**Data flow**: It receives an optional session ID and frame ID from a browser event. It checks them against the browser session’s frame knowledge and returns true or false.

**Call relations**: on_fetch_paused calls this before deciding to rewrite a PDF response. If the PDF is embedded inside another page, this method helps the file leave it alone so the page can still render normally.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request or response. It must always release the pause, and for top-level PDFs it changes the response so Chrome downloads the file instead of opening the built-in viewer.

**Data flow**: It receives event details from Chrome and an optional session ID. It reads the request ID, response status, headers, frame ID, and content type. If this is not a response yet, it schedules a normal request continuation. If it is a response, it checks whether the response is a top-level PDF and schedules either a normal continuation or a forced-download continuation.

**Call relations**: This is the front door for fetch-paused events. It uses _content_type to recognize PDFs, asks the browser session whether the frame is top-level, then hands work to _continue_request or _continue_response through spawn_background so the browser is released without blocking the event handler.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This asynchronous helper tells Chrome to continue a paused request that does not yet have a response. Without this, the page could hang waiting for permission to keep loading.

**Data flow**: It receives a session ID and request ID. It sends Chrome the Fetch.continueRequest command for that request. If Chrome rejects the command, times out, or the runtime is no longer usable, it logs a warning rather than crashing the caller.

**Call relations**: on_fetch_paused schedules this when the pause happened before a response status code was available. It is a simple release valve: once called, it hands the request back to Chrome.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This asynchronous helper releases a paused response, optionally rewriting its headers so Chrome treats it as a file attachment. It is the part that turns a top-level PDF viewer navigation into a real download.

**Data flow**: It receives a session ID, request ID, response code, response headers, and a true-or-false force flag. If forcing is needed, it removes any existing Content-Disposition header and adds Content-Disposition: attachment. It then sends Chrome the Fetch.continueResponse command. If the send fails, it logs a warning.

**Call relations**: on_fetch_paused schedules this for response-stage pauses. The helper does the actual CDP command work after on_fetch_paused has made the higher-level decision about whether the response should remain normal or become a download.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a new download. It keeps just enough information to identify the download later: Chrome’s ID, the suggested filename, and that it is currently in progress.

**Data flow**: It receives Chrome’s download-begin event details. It pulls out the download GUID and suggested filename, creates a Download record, marks it as inProgress, and appends it to the browser session’s download list.

**Call relations**: This is called when the browser reports a new download. Later, on_download_progress updates the same record, and wait looks through these records to find a completed download.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the saved state of a known download as Chrome reports progress. It is how the system learns that a download has completed, failed, or is still running.

**Data flow**: It receives Chrome’s progress event details. It reads the download GUID and new state, searches the session’s download list for the matching record, and changes that record’s state.

**Call relations**: It follows on_download_begin in the download lifecycle. The wait function depends on these state updates, because it returns only after it sees a saved download whose state is completed.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This helper checks whether an action that looked like page navigation actually produced a download. It waits only a short grace period, because download-start events may arrive just after the navigation attempt.

**Data flow**: It receives the number of downloads that existed before the action. Until the short deadline expires, it repeatedly compares the current download count with that earlier count. It returns true if a new download appears, otherwise false.

**Call relations**: This fits into flows where another part of the browser adapter has just triggered navigation and needs to know whether the result became a download instead of a normal page. It watches the download list that on_download_begin fills.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This asynchronous function waits for a browser download to finish and returns the completed download record. It is used when the caller needs to know which file Chrome completed, but not to read the file contents here.

**Data flow**: It receives arguments that may include a timeout. It turns that timeout into a number using float_or_default, then repeatedly checks the session’s download list for records whose state is completed. If one or more are complete, it returns the most recent completed record. If the deadline passes first, it raises TimeoutError.

**Call relations**: This function depends on on_download_begin creating records and on_download_progress marking them completed. It calls float_or_default to interpret caller-supplied timeout input, and uses short sleeps so it does not busy-wait while downloads are still running.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns a timeout-like value into a floating-point number, or uses a default when no value was provided. It rejects values that are not numeric enough to be safe.

**Data flow**: It receives a JSON-like value and a default number. If the value is an integer, float, or non-empty string, it converts it to a float. If the value is absent, it returns the default. For any other kind of value, it raises a ValidationError.

**Call relations**: BrowserDownloads.wait calls this before starting its timer. That keeps timeout parsing in one small place and lets wait focus on watching for completed downloads.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper finds the Content-Type header in a browser response and normalizes it for comparison. It is used to recognize PDFs even when the header contains extra details like a character set.

**Data flow**: It receives a list of JSON-like header entries. It scans for a dictionary whose name is Content-Type, ignores letter case, takes the value before any semicolon, trims spaces, and lowercases it. If no content type is found, it returns an empty string.

**Call relations**: on_fetch_paused calls this while deciding whether a top-level response is one of the types that should be forced into a download. In this file, that forced type list contains PDF.

*Call graph*: called by 1 (on_fetch_paused).


### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`orchestration` · `browser session and tab request handling`

This file solves the practical problem of keeping the project’s idea of “open tabs” in step with the real browser. Browsers can create or destroy tabs because of user actions, links, popups, downloads, or navigation, so the code needs a careful record of what exists and which browser session belongs to each tab.

The main object is `BrowserTabs`. It listens to browser target events, remembers newly created and destroyed page targets, and then `sync` updates the local tab list. When a tab is attached, the file enables page, document, network, and lifecycle events so later code can observe loading progress. It also sets the viewport size, like telling the browser window how large its “screen” should be.

Navigation is handled in a human-friendly way. A plain hostname is turned into an HTTPS URL, while special words like `back` and `forward` use browser history. After navigation starts, the file waits for the page to become settled, meaning it has loaded and painted enough to be useful. It also has a special case for downloads: if navigating to a URL turns into a file download instead of a page, that is treated as a valid outcome rather than a failed page load.

Without this file, other parts of the system would not have a reliable way to know what tabs are open, where they are, or when navigation has finished.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-provided navigation target into something the browser can use. It leaves special commands and already complete URLs alone, and adds `https://` to plain site names.

**Data flow**: It receives a string such as `example.com`, `https://example.com`, `back`, or `about:blank`. It checks whether the string is a special navigation word or already has a URL scheme such as `http:`. It returns the original string when it is already usable, or a new string with `https://` added.

**Call relations**: `BrowserTabs.navigate` calls this before deciding how to move the tab. That lets the rest of navigation work with one clear target format instead of guessing what the caller meant.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one browser tab. It stores the browser target ID, the DevTools session ID, keyboard state, and frame tracking information.

**Data flow**: It receives a target ID and session ID from the browser. It saves them, creates a fresh keyboard state, starts an empty set of frame sequence numbers, and creates a root frame reference for the tab. The result is a `Tab` object ready for later navigation and page inspection.

**Call relations**: `BrowserTabs.attach_tab` calls this after the browser has successfully attached to a page target. The new `Tab` is then added to the browser session’s tab list by callers such as `sync`, `page`, and `create`.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when frame IDs from the browser are long or awkward to present.

**Data flow**: It receives a browser frame ID. If that frame has not been seen before, it assigns the next available number. It returns the frame’s assigned number and remembers it for future calls.

**Call relations**: This method belongs to the `Tab` record and supports code that needs consistent frame numbering. It does not call other functions in this file, and the provided call graph does not show direct callers here.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the method used to send a command to the browser through Chrome DevTools Protocol. This is an interface promise, not an implementation in this file.

**Data flow**: A caller provides a command name, optional command parameters, and optionally a session ID for a specific tab. The implementing connection sends that message to the browser and returns the browser’s JSON-like reply.

**Call relations**: Many `BrowserTabs` methods rely on a connection object that provides this method. For example, attaching tabs, creating tabs, closing tabs, and navigating all send browser commands through it.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how code asks the browser connection to watch for one or more future events. It returns a future, which is a placeholder for a result that will arrive later.

**Data flow**: A caller gives event names and optionally a tab session ID. The implementing connection starts listening and returns a future that will complete when one of those events arrives.

**Call relations**: `BrowserTabs._goto` uses this to wait for document content to load, and `_history_step` uses it to wait for history navigation. This file defines the expected behavior through the protocol.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how code waits for a previously expected browser event, with a timeout so it does not wait forever.

**Data flow**: It receives a future created by `expect` and a maximum number of seconds. The implementing connection waits until the future completes or the timeout is reached, then returns the event data or raises an error.

**Call relations**: `BrowserTabs._goto` and `BrowserTabs._history_step` use this after they start navigation. It is part of the connection interface that `BrowserTabs` depends on.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how tab code gets the browser connection used to send commands and wait for events. It is an interface method for the wider browser session object.

**Data flow**: It takes the session object as context and returns an object matching `BrowserTabCdp`. No data is transformed in this file because the real implementation lives elsewhere.

**Call relations**: Nearly every action in `BrowserTabs` begins by asking the browser session for this connection. The protocol keeps this file independent from the exact connection implementation.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how tab code gets the helper that can tell whether a navigation became a file download. It is an interface method, not the download logic itself.

**Data flow**: It reads the browser session context and returns a `BrowserDownloads` object. That returned object can inspect download activity.

**Call relations**: `BrowserTabs._goto` uses this when a navigation reports an error. If a download started, the navigation is accepted instead of treated as a page-load failure.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how code runs JavaScript inside a tab and gets the result back. JavaScript is code executed by the web page itself.

**Data flow**: A caller provides a tab session ID and a JavaScript expression. The implementing browser session runs it in that tab and returns the resulting JSON-like value.

**Call relations**: `BrowserTabs.tab_info` uses this to read `location.href` and `document.title` from the page. This protocol method lets tab code ask simple questions about the current page.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser page targets already existed at startup. This prevents old tabs from being mistaken for newly created popup tabs later.

**Data flow**: It receives browser target information as JSON-like data. It extracts each target ID from the `targetInfos` list and stores those IDs in `browser.tab_events.initial_targets`.

**Call relations**: This is normally used when event tracking is being set up. Later, `BrowserTabs.on_target_created` checks this saved set so it only queues genuinely new page targets.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports a new page target, such as a new tab or popup. It queues that target so the local tab list can attach to it later.

**Data flow**: It receives event parameters from the browser. If the event describes a page, has a string target ID, was not part of the initial browser state, and has not already been queued, it appends that target ID to `created_targets`.

**Call relations**: This is an event callback. It does not attach immediately; instead, `BrowserTabs.sync` later reads the queued target IDs and calls `attach_tab` for each one.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports that a target has gone away. It marks the target for removal from the local tab list.

**Data flow**: It receives event parameters from the browser. If there is a string target ID, it adds that ID to `destroyed_targets`.

**Call relations**: This is an event callback. `BrowserTabs.sync` later consumes the destroyed-target set and removes matching `Tab` objects from the session’s tab list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. The main frame is the top-level page, as opposed to an embedded frame like an iframe.

**Data flow**: It receives frame-loading event data and a session ID. If there is no session ID, it does nothing. If the frame belongs to the top-level page for that session, it tells the settle tracker that this session is loading.

**Call relations**: This event callback uses `is_top_level_frame` to ignore subframes. Its output feeds the `Settle` object, which `BrowserTabs.navigate` later waits on before saying navigation is finished.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having loaded its document content. This means the page’s basic HTML structure is ready, though images and later changes may still be happening.

**Data flow**: It receives browser event data and a session ID. If the session ID is present, it tells the settle tracker that the session has loaded.

**Call relations**: This event callback contributes to the same settling process used after navigation. `BrowserTabs.navigate` waits for that process so callers do not act too early.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports an important paint lifecycle event. “Painted” means the browser has drawn visible page content.

**Data flow**: It receives lifecycle event parameters and a session ID. It ignores events without a session ID, events that are not paint-related, and events for non-top-level frames. For a relevant top-level paint event, it tells the settle tracker the session has painted.

**Call relations**: This event callback uses `is_top_level_frame` and the list of paint lifecycle event names. Its marks are later used by `BrowserTabs.navigate` through the `Settle` object’s wait process.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main page frame of a known tab. This avoids treating embedded frames as if the whole tab were loading or painting.

**Data flow**: It receives a session ID and a frame ID from an event. It scans the known tabs and returns `true` only when it finds a tab whose session ID matches and whose target ID matches the frame ID.

**Call relations**: `on_frame_loading` and `on_lifecycle` call this before updating settle state. It acts like a filter at the doorway, letting only top-level page events affect navigation readiness.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects this program to a browser page target so it can control and observe that tab. It also prepares the tab for page, network, document, download, and viewport behavior.

**Data flow**: It receives a browser target ID. It asks the browser to attach to that target and gets a session ID back. It initializes the session, enables fetch interception for document downloads, sets the viewport size, and returns a new `Tab` object.

**Call relations**: `sync` calls this for tabs created by browser events, `page` calls it after creating a fallback blank tab, and `create` calls it for a newly requested tab. It calls `init_session` before constructing the `Tab` record.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for a newly attached tab. Without this setup, later code would not receive the page, DOM, lifecycle, or network events it relies on.

**Data flow**: It receives a tab session ID. It sends browser commands to enable page events, lifecycle events, document access, and network events for that session. It does not return a value, but it changes what events the browser will emit.

**Call relations**: `BrowserTabs.attach_tab` calls this immediately after attaching to a target. It is the setup step before the tab is considered ready for normal use.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list up to date with browser events that have already happened. It removes destroyed tabs and attaches to newly created ones.

**Data flow**: It reads `destroyed_targets` and `created_targets` from `browser.tab_events`. Destroyed targets are removed from `browser.tabs`; created targets are popped one by one and attached unless already known. Failed attaches caused by DevTools errors are skipped.

**Call relations**: `page` and `tabs_context` call this before answering questions about tabs. It calls `attach_tab` when it needs to add a new real browser tab to the local list.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the requested tab, making sure there is at least one open tab first. It is the common doorway for operations that need a real tab to act on.

**Data flow**: It receives an optional tab index. It first runs `sync`. If no tabs exist, it asks the browser to create `about:blank`, attaches to it, and stores it. If no index was requested it returns the most recent tab; otherwise it validates the index and returns that tab or raises a validation error.

**Call relations**: `BrowserTabs.navigate` and `BrowserTabs.close` call this to find the tab they should work on. It may call `attach_tab` if it must create the first blank tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new page, backward, or forward, then waits until the page is settled enough to use. It returns the tab’s current URL and title afterward.

**Data flow**: It receives a URL-like string and an optional tab index. It gets the tab with `page`, normalizes the target, resets the settle tracker, performs either a history step or a normal page load, waits for settling, and returns `tab_info`.

**Call relations**: `BrowserTabs.create` calls this after opening a new tab with a requested URL. Internally it calls `normalize_url`, then delegates to `_history_step` for `back` or `forward`, or `_goto` for normal navigation, and finally calls `tab_info`.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work of navigating one tab to a specific URL. It waits for the document content event when there is a real page load.

**Data flow**: It receives a `Tab` and a URL. It records how many downloads existed before navigation, starts waiting for a DOM-content-loaded event, and sends `Page.navigate`. If the browser reports an error, it cancels the wait and checks whether the navigation became a download; if not, it raises an error. If no new loader is reported, it cancels the wait because there is no full page load. Otherwise it waits up to the navigation timeout.

**Call relations**: `BrowserTabs.navigate` calls this for ordinary URL navigation. It uses the browser connection for commands and events, and consults the download reader to distinguish failed page loads from successful file downloads.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves one tab backward or forward in its browser history. If there is no history entry in that direction, it quietly does nothing.

**Data flow**: It receives a `Tab` and a step value, usually `-1` for back or `1` for forward. It asks the browser for navigation history, checks the current index, calculates the target entry, and if valid sends a command to navigate to that history entry. It waits for a navigation event or a within-document navigation event.

**Call relations**: `BrowserTabs.navigate` calls this when the normalized target is `back` or `forward`. It relies on the browser connection for history data and event waiting.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. This gives callers a simple summary of where the tab is and what the page calls itself.

**Data flow**: It receives a `Tab`. It runs a small JavaScript expression in that tab to read `location.href` and `document.title`, checks that the result is a map-like object, and returns a dictionary with string `url` and `title` fields.

**Call relations**: `navigate` calls this after movement finishes, `tabs_context` calls it for each tab, and `tab_titles` calls it to build a title-only list. It depends on the browser session’s `eval_js` method.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to show or reason about tab state. It marks the last tab in the list as the current active tab.

**Data flow**: It first runs `sync` so the local list matches recent browser events. It then loops through each tab, reads its URL and title with `tab_info`, and returns a dictionary containing the current tab index and a list of tab summaries.

**Call relations**: `BrowserTabs.close` calls this after closing a tab so the caller receives the updated tab list. It also serves as a general tab-status view for higher-level code.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns only the page titles for the currently known tabs. This is a lightweight way to summarize open pages.

**Data flow**: It loops over `browser.tabs`, reads each tab’s info with `tab_info`, extracts the title, replaces missing titles with an empty string, and returns the list of titles.

**Call relations**: It uses `tab_info` as its source of truth for each tab’s title. The provided call graph does not show which outside code calls it.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and optionally navigates it to a requested URL. It returns the new tab’s index plus its final URL and title.

**Data flow**: It receives a URL, defaulting to `about:blank`. It asks the browser to create a blank target, attaches to it, appends the resulting `Tab` to the local list, navigates that tab to the requested URL, and returns the tab ID combined with page info.

**Call relations**: This is a higher-level tab-opening operation. It calls `attach_tab` to connect to the new target, then calls `navigate` so the new tab ends up at the requested destination.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a tab and returns the updated tab list. It accepts the tab ID from a JSON-like argument object.

**Data flow**: It reads `tab_id` from the input arguments and converts it with `_tab_id`. It finds the tab with `page`, tells the browser to close the target, removes that exact tab object from the local list, clears out-of-process iframe session tracking, and returns `tabs_context`.

**Call relations**: This is the high-level close operation. It uses `page` to validate and fetch the tab, sends the close command through the browser connection, and then calls `tabs_context` so the caller sees the new state.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loose JSON value into an optional tab index. This lets callers provide the tab ID as an integer, a float, or a non-empty string.

**Data flow**: It receives a JSON-like value or `None`. Integers are returned directly, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes `None`.

**Call relations**: `BrowserTabs.close` calls this before asking `page` for the tab to close. It is a small input-cleaning helper for tab close requests.

*Call graph*: called by 1 (close).


### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, and “are you sure you want to leave?” messages. In a normal browser, these wait for a person to click a button. In an automated browser, that waiting can block later browser events and make the whole agent appear stuck, like a doorway blocked by one unanswered question.

This file provides the small policy for answering those dialogs. When a dialog appears, `BrowserDialogs.on_dialog` reads its type and message. Alerts and “beforeunload” dialogs are automatically accepted because there is usually no meaningful alternative: they are mostly notices or navigation warnings. Confirmation and prompt dialogs are dismissed, so the agent does not accidentally click “yes” to a site’s own safety check.

The event is also recorded in the browser session’s `dialogs` list, so the model or higher-level system can later know that the page asked something. The actual click is sent through CDP, the Chrome DevTools Protocol, which is the control channel used to talk to the browser. The answer is sent in the background so the event handler can return quickly. If the browser no longer accepts the command, the failure is logged instead of crashing the system.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of an object that can send commands to the browser over CDP, the Chrome DevTools Protocol. It exists so the dialog code can ask the browser to do something without caring which concrete connection object is being used.

**Data flow**: It receives a command name, optional command details, and an optional browser session identifier. An implementation sends that request to the browser and returns the browser’s JSON-style reply.

**Call relations**: The dialog-answering path relies on this ability after it gets a connection from the browser session. In practice, `_answer_dialog` uses it to send the command that accepts or dismisses the JavaScript dialog.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way for a browser session object to provide access to its browser control connection. The dialog code uses it when it needs to send a direct command back to the browser.

**Data flow**: It takes the current browser session object and returns an object capable of sending CDP commands. It does not itself decide what command to send.

**Call relations**: When `_answer_dialog` needs to answer a pop-up, it asks the session for this connection first. The returned connection is then used to send the browser command that closes the dialog.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for a browser session to start a small asynchronous task without waiting for it to finish immediately. The dialog code uses it because dialogs must be answered quickly, but the event handler should not get stuck waiting on browser communication.

**Data flow**: It receives a coroutine, meaning a piece of asynchronous work that can run later or alongside other work. The session schedules that work in the background and returns without producing a value.

**Call relations**: `on_dialog` calls this after deciding whether a dialog should be accepted or dismissed. The background task it starts is `_answer_dialog`, which performs the actual browser command.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is called when the browser reports that a JavaScript dialog has appeared. It decides whether to accept or dismiss the dialog, records what happened, and starts the actual browser reply right away.

**Data flow**: It receives the dialog details from the browser and an optional session identifier. It reads the dialog type and message, chooses “accept” for alerts and before-unload warnings and “dismiss” for other dialog types, appends a human-readable note to the session’s dialog history, then schedules `_answer_dialog` to send the reply to the browser.

**Call relations**: This is the front door for dialog events. When it runs, it calls `_answer_dialog` in the background so the browser is unblocked quickly while the rest of the event-processing flow can continue.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This sends the actual command that closes the JavaScript dialog in the browser. It is separated from `on_dialog` so the browser reply can run asynchronously in the background.

**Data flow**: It receives the browser session identifier, if there is one, and a true-or-false decision saying whether to accept the dialog. It asks the browser session for its connection, sends the `Page.handleJavaScriptDialog` command with that decision, and normally returns nothing. If the command fails because the browser connection is gone, slow, or otherwise invalid, it logs a warning instead of raising the error further.

**Call relations**: `on_dialog` creates and schedules this work after choosing the response. This function then hands the decision to the browser through the CDP connection, completing the dialog-handling flow.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `action handling`

Modern web pages often keep doing network work forever. A news site may load ads, trackers, images, and live updates long after the useful page is visible. If an automation tool waited for the whole browser to become completely quiet, it could wait too long or never move on. This file solves that problem by defining a small “settling” tracker.

The main idea is consequence-scoped waiting: after an action such as a click or navigation, it waits for the important requests and page loading that action caused, not for unrelated background traffic. It ignores passive traffic such as images, fonts, low-priority prefetches, and common analytics hosts. It also treats a browser paint event — when Chrome has drawn meaningful page content — as a strong sign that the page is usable.

The Settle object keeps simple sets: foreground requests still pending, sessions still loading, and sessions that have painted. Before waiting, it asks the browser to run one tiny JavaScript delay so click handlers and immediate follow-up tasks get a chance to start their requests. Then it waits until loading and tracked requests quiet down, with short grace periods for chained requests. If a page paints but keeps fetching forever, it returns after a brief post-paint grace instead of waiting until the full timeout.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It keeps foreground, action-related traffic, and filters out things that usually should not block progress, such as images, fonts, low-priority requests, and analytics beacons.

**Data flow**: It receives one browser event record as a dictionary-like object. It reads the request type, priority, and URL host. If the request looks passive or analytics-related, it returns false; otherwise it returns true, including when the event shape is unfamiliar so important work is not skipped by accident.

**Call relations**: Settle.on_request_started calls this when Chrome reports a new request. The result decides whether that request is added to the pending set that Settle.wait later watches.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker with no outstanding work recorded. Browser sessions use this object to remember what page requests, loading states, and paint events are currently relevant.

**Data flow**: It takes no outside data. It creates empty sets for pending requests, loading sessions, and painted sessions, plus a counter for how many tracked requests have started. The new object is ready to receive browser events.

**Call relations**: BrowserSession.__init__ creates a Settle instance when a browser session starts. BrowserSession.close also creates one, which gives the session a clean tracker state while closing or resetting its browser-side state.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the tracker before a new action or waiting window. This prevents old requests or paint events from making the next action look busy or ready for the wrong reason.

**Data flow**: It reads the current internal sets and counter, then empties pending requests and painted sessions and sets the started counter back to zero. It does not clear the loading set, so ongoing document loading remains known.

**Call relations**: This is the reset button for the Settle object. The surrounding browser-session code can call it before measuring the consequences of a new browser action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records a newly started browser request if it is worth waiting for. This is how the tracker learns that the current action has caused foreground work.

**Data flow**: It receives a browser event and the browser session id that produced it. It extracts the request id, checks that the request belongs to a session and should be tracked, then stores the pair of session id and request id in the pending set and increments the started counter.

**Call relations**: This method calls tracks_request to avoid waiting for passive or analytics traffic. Later, Settle.on_request_finished removes the same request, and Settle.wait watches the pending set to decide whether it is safe to continue.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tracked browser request as no longer pending. This lets the settling logic know that one piece of action-related network work has completed or disappeared.

**Data flow**: It receives a browser event and session id, reads the request id, and removes that session/request pair from the pending set if present. If the request was not tracked, removing it has no effect.

**Call relations**: This is the counterpart to Settle.on_request_started. Settle.wait depends on these removals so it can see when foreground network work has drained.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has started loading a document. This matters because page navigation can still be in progress even if no tracked request is currently pending.

**Data flow**: It receives a session id and adds it to the internal loading set. From then on, waits for that session treat the page as not settled until it is marked loaded or a timeout path ends the wait.

**Call relations**: This feeds loading-state information into Settle.wait. The matching Settle.mark_loaded method removes the session when loading finishes.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session is no longer loading its document. This can allow a wait to finish once tracked network requests have also quieted down.

**Data flow**: It receives a session id and removes it from the loading set. If the id was not present, nothing changes.

**Call relations**: This balances Settle.mark_loading. Settle.wait checks the loading set repeatedly, so this method is one of the signals that the page may now be ready.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted meaningful content on screen. A paint is treated as a strong sign that a page is visible and usable, even if some network traffic continues.

**Data flow**: It receives a session id and adds it to the painted set. Future waits for that session can switch to the shorter post-paint grace period.

**Call relations**: Settle.wait checks the painted set before and during its polling loop. Once a paint is seen, it hands off to Settle._drain_after_paint rather than waiting up to the longer full cap.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears settled, without waiting forever for background page noise. This is the main readiness gate used after actions such as clicks or navigations.

**Data flow**: It receives a CDP connection object, a session id, and a maximum wait time. First it flushes one round of browser-side tasks so immediate click handlers and zero-delay timers can start their requests. Then it watches paint, loading, and pending request state until the page looks ready or the time cap is reached. It returns no value; its effect is the delay itself.

**Call relations**: This method coordinates the file’s helper pieces. It calls Settle._flush_page_tasks at the start, checks the state filled by the mark and request methods, sleeps briefly between checks, and calls Settle._drain_after_paint when a paint event means the page is visible but may still be finishing foreground fetches.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: Gives a freshly painted page a short extra chance to finish important foreground work. This avoids calling a page ready the instant a blank shell appears, while still escaping pages that never become fully quiet.

**Data flow**: It receives a session id and an absolute deadline. It creates a shorter grace deadline, then repeatedly checks whether the session is still loading or has pending requests. If things stay quiet across a small gap, it returns early; otherwise it returns when the grace time runs out.

**Call relations**: Settle.wait calls this after it notices a paint. It is the post-paint branch of the settling strategy: visible page first, then a brief wait for useful follow-up content.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Asks the page to run one tiny browser task before settling begins. This gives immediate JavaScript reactions to an action, such as click handlers and zero-delay timers, time to start any network requests that should be counted.

**Data flow**: It receives a CDP connection and session id. It sends a Runtime.evaluate command that runs a promise resolved by setTimeout with zero delay. If the command succeeds, browser events caused by those immediate tasks should already have arrived; if the browser command fails or times out, it simply waits one short beat instead.

**Call relations**: Settle.wait calls this before checking pending work. It hands the actual browser communication to Cdp.send and uses a short sleep as a safe fallback if CDP, the Chrome DevTools Protocol connection, cannot complete the round trip.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Protocol execution
The protocol layer connects to Chrome DevTools and provides a safer page-runtime execution interface on top of that channel.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `active during a browser session, from connection setup through command and event traffic until shutdown`

Chrome automation works by talking to Chrome over the Chrome DevTools Protocol, often shortened to CDP. CDP is like a remote control for the browser: the program sends commands such as “open this page” or “click here,” and Chrome sends back replies and live events. This file provides the transport layer for that conversation.

The main class, CdpConnection, wraps a WebSocket, which is a long-lived two-way network connection. When a command is sent, the file gives it a unique number and stores a future, meaning a placeholder for the reply that will arrive later. A background reader task keeps listening to Chrome. When a message comes in, it checks whether the message is a reply to a command or an event from the browser.

Replies are matched back to the waiting command by their number. Events are sent either to one-time waiters created by expect and wait, or to ongoing listeners registered with on. This is like a mailroom: numbered letters go back to the person waiting for that exact answer, while announcements are broadcast to subscribers.

The file is careful not to let callers hang forever. Commands and event waits have time limits. If the connection closes, all pending waits fail loudly instead of silently waiting forever.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Creates a clear Python error when Chrome rejects a CDP command. It records which command failed, the numeric error code from Chrome, and the message Chrome sent back.

**Data flow**: It receives the command name, an error code, and an error message from a failed CDP response. It stores the command and code on the error object, then builds a readable error sentence. The result is an exception that can be given to the caller waiting for the command reply.

**Call relations**: CdpConnection._dispatch uses this when it receives a command response containing an error. Instead of returning a normal result, the waiting command future is completed with this exception so the caller sees the failure directly.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual DevTools WebSocket address to use. Callers may already have a WebSocket URL, or they may only have Chrome's HTTP debugging address; this function turns either form into the WebSocket URL needed for CDP.

**Data flow**: It takes a URL and request headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise it asks Chrome's /json/version endpoint over HTTP, reads the webSocketDebuggerUrl field from the JSON response, checks that it is a string, and returns that value.

**Call relations**: This is a setup helper for code that needs to connect to Chrome but may not yet know the WebSocket endpoint. It uses httpx.AsyncClient to make the HTTP request and as_str to safely pull the expected string out of Chrome's response.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Builds the in-memory state for one CDP connection around an already-open WebSocket. It prepares the bookkeeping needed to match outgoing commands to incoming replies and to route browser events.

**Data flow**: It receives a WebSocket connection. It stores it, starts the command id counter at zero, and creates empty collections for pending command replies, event listeners, one-time event waiters, and the background reader task reference. Nothing is sent yet; this only prepares the object.

**Call relations**: CdpConnection.open calls this after the WebSocket connection succeeds. Later, send, expect, on, wait, _read_loop, and _dispatch all use the state created here.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens the WebSocket connection to Chrome and starts the background task that reads incoming messages. This is the normal way to create a working CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to that URL, creates a CdpConnection around the socket, starts _read_loop as an asynchronous background task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this while starting a browser session. Once open returns, other code can send CDP commands while the reader task quietly receives replies and events in the background.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader task and closes the WebSocket so the browser-control channel is no longer used.

**Data flow**: It checks whether a reader task exists. If so, it cancels that task, waits for the cancellation to finish without treating the normal cancellation error as a problem, clears the task reference, and then closes the WebSocket. The connection changes from active to closed.

**Call relations**: This is used during cleanup when the browser session no longer needs the CDP channel. It works with _read_loop's cleanup behavior so pending command and event waits do not remain stuck forever.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one CDP command to Chrome and waits for that command's reply. This is the main request-response path for asking Chrome to do something.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for commands aimed at a specific browser target. It assigns a new numeric id, stores a future under that id, serializes the command to JSON, sends it over the WebSocket, and waits up to the command timeout for _dispatch to fill in the future. It returns Chrome's result dictionary, or raises an error if Chrome reports one or the timeout expires.

**Call relations**: Higher-level browser code calls this whenever it needs Chrome to perform an action. The matching response is later read by _read_loop and routed by _dispatch back into the future created here.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a recurring listener for a named browser event. Use this when code wants to be notified every time Chrome reports a certain kind of event.

**Data flow**: It receives an event name and a listener function. It adds the listener to the list for that event. Later incoming events with that name will cause the listener to be called with the event details and optional session id.

**Call relations**: This sets up ongoing event fan-out. When _dispatch receives an event message from _read_loop, it looks up listeners registered here and calls them.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one or more browser events. This is useful when code has just done something and expects Chrome to soon report a specific event, such as a page load or target creation.

**Data flow**: It receives one or more event names and optionally a session id to narrow the wait to one browser target. It creates a future, stores it with the event names and session filter, and returns the future immediately. The future will later be completed with the event parameters when a matching event arrives.

**Call relations**: Callers usually create this future before or around an action, then pass it to wait to enforce a timeout. _dispatch is the part that eventually notices a matching event and completes the future.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for an event future created by expect, with a time limit. It also removes the waiter afterward so old waits do not pile up.

**Data flow**: It receives a future and an optional timeout. It waits until the future is completed by _dispatch or until the timeout expires. Whether it succeeds or fails, it removes that future from the stored waiter list. On success it returns the event's parameter dictionary.

**Call relations**: This is the timed companion to expect. Code that needs a specific browser event asks expect for a future, then uses wait so the program will not hang forever if Chrome never sends the event.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads messages arriving from Chrome and hands each one to the dispatcher. It is the background receiver for the whole CDP connection.

**Data flow**: It reads raw messages from the WebSocket one by one, turns each JSON string into a Python dictionary, and passes it to _dispatch. If the WebSocket closes, it stops reading. In its cleanup step, it marks all pending command replies and event waits as failed so callers are told the connection closed instead of waiting forever.

**Call relations**: CdpConnection.open starts this as a background task. It feeds every incoming Chrome message into _dispatch, and close cancels it during shutdown.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Sorts one incoming CDP message into the right destination. It decides whether the message is a command reply or a browser event, then wakes the matching waiter or notifies listeners.

**Data flow**: It receives one parsed message dictionary. If the message has a numeric id, it treats it as a command response, finds the pending future for that id, and completes it with either a result dictionary or a CdpError. If the message has an event method name instead, it extracts the event parameters and session id, completes any matching one-time event futures, keeps unmatched waiters for later, and calls all registered recurring listeners for that event.

**Call relations**: _read_loop calls this for every message received from Chrome. It completes futures created by send and expect, constructs CdpError for failed command responses, and invokes listener functions registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `browser command/request handling`

Modern browsers expose a debugging protocol that lets tools ask a page to evaluate JavaScript or call a function on an existing page object. This file is a thin wrapper around that ability. Its job is to make those calls feel like ordinary Python methods instead of hand-built protocol messages.

The two protocol classes describe the shape of the outside pieces this wrapper needs: something that can send a browser command, and something that can provide that sender. They are like a plug shape: any real browser session can fit here as long as it has the right methods.

BrowserRuntime is the useful part. Its eval method sends a Runtime.evaluate command, meaning “run this JavaScript expression in the page and give me the value back.” Its call_on method sends Runtime.callFunctionOn, meaning “take this page object, run this JavaScript function against it, and return the value.” Both ask the browser to return plain JSON-style data rather than a remote browser object.

A key safety detail is raise_on_exception. Browser protocol calls can succeed as messages even when the JavaScript itself throws an error. This helper checks for that case and raises a Python RuntimeError so callers do not accidentally treat a failed page script as a valid result.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This defines the expected way to send a command to the browser debugging connection. A real implementation uses it to send a named browser command, optional data for that command, and optionally the page or target session it should run in.

**Data flow**: It receives a command name, optional command parameters, and an optional session identifier. The real sender would transmit those to the browser and return the browser's JSON-style response as a dictionary.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on an object with this method after getting it from BrowserRuntimeSession.connection. This file only states the expected shape; the actual sending is supplied elsewhere.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This defines how BrowserRuntime gets access to the browser debugging connection. It lets BrowserRuntime stay independent from the concrete browser session class.

**Data flow**: It takes the session object itself and returns an object capable of sending browser protocol commands. It does not transform page data directly; it provides the communication path.

**Call relations**: BrowserRuntime calls this before sending Runtime.evaluate or Runtime.callFunctionOn. The real session implementation lives outside this file, but it must provide this method for BrowserRuntime to work.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the expression's plain value. Callers use it when they want to ask the page a direct question, such as reading a variable or computing a small result.

**Data flow**: It receives a session id and a JavaScript expression. It builds a Runtime.evaluate request, sends it through the browser connection, checks whether the page threw an exception, then extracts the returned value from the browser's nested response and gives that value back.

**Call relations**: This method is a main entry point for simple page-side evaluation. During its work it calls BrowserRuntime.raise_on_exception to turn JavaScript failures into Python failures, and it uses as_map to safely treat nested response pieces as dictionaries before reading the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This calls a JavaScript function on an existing object inside the browser page and returns the function's plain result. It is useful when earlier browser work produced an object id, and the code now needs to inspect or use that object from Python.

**Data flow**: It receives a session id, a browser object id, a JavaScript function declaration, and optional argument values. It builds a Runtime.callFunctionOn request, converts each Python-side argument into the browser protocol's value format, sends the request, checks for browser-side exceptions, then returns the result value as a dictionary.

**Call relations**: This method is the companion to eval for object-based work. It sends through the same BrowserRuntimeSession connection, calls BrowserRuntime.raise_on_exception after the browser responds, and uses as_map to safely unpack the nested result data.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser protocol response for a JavaScript exception and raises a clear Python error if one occurred. Without it, callers might mistake a failed browser script for a successful command.

**Data flow**: It receives the browser response dictionary. If there is no exception information, it returns without changing anything. If exception details are present, it chooses the best available error message from the response and raises RuntimeError with that message.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a response from the browser. It is the shared guardrail that keeps both evaluation paths from silently ignoring page-side failures.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### Wire validation
The wire definitions validate incoming and outgoing Chrome DevTools message shapes before they enter the browser engine.

### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `request handling`

The browser engine communicates with Chrome using the Chrome DevTools Protocol, often called CDP. In plain terms, CDP is a JSON-based remote control API for the browser: the engine sends JSON messages to Chrome and receives JSON messages back. JSON is flexible, but that flexibility is risky. A field that should be a string might be missing, empty, or shaped like a list instead. If the rest of the engine assumed the value was correct, it could fail later in a confusing way.

This file solves that by defining a shared idea of what JSON can be, then offering four small “narrowing” helpers. A narrowing helper takes an unknown JSON value and proves it is the exact kind of value the engine needs: an object, a non-empty string, an integer, or a list. If the value is missing where an empty object or list is acceptable, it quietly returns an empty one. If the shape is wrong, it raises ValidationError, a deliberate error that says the outside wire data did not match what the engine expected.

The important idea is boundary safety. This file keeps uncertain browser messages at the edge of the system, so deeper code can work with clearer, safer values.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a set of named fields like a dictionary. It is used when browser data may be absent or unknown, but the next step needs a safe field map to read from.

**Data flow**: It receives a possible JSON value and a text path that names where the value came from, such as a field name used in an error message. If the value is missing, it turns it into an empty object. If it is already an object, it passes it through unchanged. If it is anything else, it raises ValidationError explaining that this path must be an object.

**Call relations**: Code that reads CDP JSON calls on as_map before treating a value as a dictionary of fields. When the value is wrong, as_map hands control to ValidationError so the problem is reported at the wire boundary instead of causing a later, harder-to-understand failure.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a real, non-empty string. It is useful for required text fields, such as identifiers or names, where an empty string would be just as unusable as a missing value.

**Data flow**: It receives a possible JSON value and a path label for error reporting. If the value is a string and it is not empty, it returns that string. For missing values, empty strings, numbers, lists, objects, or any other shape, it raises ValidationError saying the path must be a non-empty string.

**Call relations**: Browser-message parsing code uses as_str when it needs dependable text from incoming CDP JSON. If the incoming value does not meet that expectation, as_str creates a ValidationError so the caller can treat the bad wire data as a recoverable tool error.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, a whole number. It is used for fields where the browser protocol is expected to provide counts, indexes, codes, or other whole-number values.

**Data flow**: It receives a possible JSON value and a path string used to describe the location of the value if something goes wrong. If the value is an integer, it returns it unchanged. If the value is missing or has any other shape, it raises ValidationError with a message that the path must be an integer.

**Call relations**: Parsing code calls as_int before using a browser-provided value as a whole number. When the check fails, as_int routes the problem into ValidationError, keeping malformed CDP data from being mistaken for valid engine state.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list, meaning an ordered collection of JSON items. It is used when a browser response may contain repeated items, and missing data can safely mean “no items.”

**Data flow**: It receives a possible JSON value and a path name for clear error messages. If the value is already a list, it returns that list. If the value is missing, it returns an empty list. If the value is any other kind of JSON value, it raises ValidationError saying the path must be a list.

**Call relations**: Code that reads arrays from CDP responses calls as_list before looping over them. If the response contains the wrong shape, as_list raises ValidationError at once, so the caller can report a clean validation problem rather than failing midway through processing.

*Call graph*: 1 external calls (__init__).

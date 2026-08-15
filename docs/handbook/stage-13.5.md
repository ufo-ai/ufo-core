# Tabs, dialogs, and downloads management  `stage-13.5`

This stage is the browser’s side-effect control desk. It runs behind the scenes while the agent browses, keeping the visible page flow from getting stuck or losing track of state. tabs.py is the traffic controller for browser tabs. It opens and closes tabs, switches between them, and sends navigation requests. It translates higher-level requests into Chrome DevTools Protocol messages, which are the low-level control commands Chrome understands.

dialogs.py watches for pop-up JavaScript dialogs, such as alerts, confirmation boxes, text prompts, and “are you sure you want to leave?” warnings. These dialogs can block the whole page until someone answers. This file chooses a quick response and records the result so the rest of the system knows what interrupted the page.

downloads.py watches for files the browser starts downloading and can steer some content into download form. For example, it helps make PDFs appear as saved files instead of being trapped inside an in-browser viewer. Together, these parts keep browsing actions moving smoothly.

## Files in this stage

### Dialog handling
Decides and records fast responses to JavaScript dialogs so browser automation can continue safely.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

JavaScript dialogs are small browser pop-ups created by a web page. They matter here because they stop the page until someone answers them. If the agent ignored one, later browser events could get stuck behind it, like cars blocked by a closed railroad crossing.

This file gives the browser agent a simple rulebook. Alerts and “beforeunload” warnings are automatically accepted, because there is usually no meaningful choice: an alert only says “OK,” and a beforeunload dialog blocks navigation unless answered. Confirm and prompt dialogs are dismissed instead. That is safer because it avoids accidentally agreeing to a website’s own “are you sure?” question or filling in a prompt without the model choosing to do so.

When a dialog event arrives, BrowserDialogs.on_dialog reads the dialog type and message, stores a plain text note in the browser session’s dialog list, and immediately starts a background task to answer it. The actual answer is sent through Chrome DevTools Protocol, a browser control API often shortened to CDP. If that send fails because the browser session is gone, timed out, or otherwise unavailable, the file logs a warning instead of crashing the whole agent.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This describes the kind of browser connection this file expects: something that can send a command to the browser and return the browser’s reply. It is a promise about shape, not the actual implementation.

**Data flow**: It receives a command name, optional command details, and optionally the id of the browser tab or frame session to target. A real connection uses those inputs to talk to the browser and returns a JSON-like dictionary with the result.

**Call relations**: BrowserDialogs._answer_dialog relies on this method after asking the session for its connection. This file does not implement the sending itself; it only requires that the surrounding browser system provide an object with this behavior.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This describes how a dialog handler gets access to the browser connection used to send control commands. It is part of the expected session interface, not an implementation here.

**Data flow**: It takes the current browser session object and returns a connection object that knows how to send commands to the browser. Nothing is changed by this declaration in this file.

**Call relations**: BrowserDialogs._answer_dialog calls this when it is time to tell the browser to accept or dismiss the dialog. The concrete session class elsewhere supplies the real connection.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This describes how the dialog handler can start a small asynchronous job without waiting for it inline. That matters because the dialog must be answered right away, but the event callback itself stays simple and non-blocking.

**Data flow**: It receives a coroutine, which is an unfinished asynchronous task, and schedules it to run in the background. The expected result is that the task begins running separately; this protocol method itself does not return useful data.

**Call relations**: BrowserDialogs.on_dialog uses this to launch BrowserDialogs._answer_dialog. The real browser session decides exactly how background tasks are scheduled.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is called when the browser reports that a page has opened a JavaScript dialog. It chooses whether to accept or dismiss the dialog, records a human-readable note about it, and starts the work to answer it.

**Data flow**: It receives the dialog event details and an optional session id. It reads the dialog type and message from the event, decides that alerts and beforeunload dialogs should be accepted while other dialogs should be dismissed, appends a note such as “confirm dismissed: Are you sure?” to the session’s dialog list, and starts a background task to send the answer to the browser.

**Call relations**: This is the front door for dialog events in this file. After making the policy decision, it hands off to BrowserDialogs._answer_dialog through the session’s background-task helper so the browser can be unblocked promptly.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This sends the actual accept-or-dismiss command to the browser. It is separated from on_dialog so it can run asynchronously in the background.

**Data flow**: It receives the target session id and a true-or-false accept choice. It asks the browser session for its connection, sends the CDP command Page.handleJavaScriptDialog with the accept value, and produces no returned result. If the command cannot be sent because of a browser/control error, timeout, or runtime problem, it logs a warning instead of raising the error further.

**Call relations**: BrowserDialogs.on_dialog creates this task after deciding what should happen. This function then uses BrowserDialogSession.connection and the connection’s send method to deliver the decision to the browser.

*Call graph*: called by 1 (on_dialog).


### Download management
Detects, waits for, and redirects browser downloads, especially files that would otherwise open in embedded viewers.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`io_transport` · `browser event handling and download waiting`

Browsers do not treat every file the same way. A PDF, for example, may open inside Chrome’s built-in viewer instead of becoming a normal download. That is a problem for an automation agent: the page may appear loaded, but the useful file bytes are not available through the readable page content. This file watches Chrome DevTools Protocol events, which are browser messages sent over a control connection, and keeps a simple list of downloads with their ID, suggested filename, and state.

The main class, BrowserDownloads, reacts when the browser pauses a network response. Every paused request must be released, like opening a gate after briefly inspecting who is passing through; otherwise the page would hang. Most requests are simply continued. But if the response is a top-level document with a content type that Chrome would show in a viewer, currently PDF, the code rewrites the response headers so Chrome treats it as an attachment download.

The file also records when downloads begin, updates their progress, waits briefly to see whether navigation became a download, and waits until a download completes. It does not read downloaded file contents. That responsibility belongs to another layer that knows where the browser saved the file.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the promised shape of the browser control connection. Anything that satisfies this protocol can send a named Chrome DevTools Protocol command, with optional parameters, to the browser.

**Data flow**: It receives a command name, an optional dictionary of command details, and optionally a browser session ID. The real implementation sends that command to Chrome and returns Chrome’s reply as a JSON-like dictionary.

**Call relations**: BrowserDownloads uses this capability through BrowserDownloadSession.connection when it needs to release paused network traffic. This file defines the expectation; another object elsewhere provides the actual communication with the browser.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This is the promised way to get the browser control connection from a session object. BrowserDownloads relies on it whenever it needs to tell Chrome to continue a paused request or response.

**Data flow**: It takes no extra input beyond the session object. The real implementation returns an object that can send commands to Chrome.

**Call relations**: The private continue helpers call this method before sending Fetch.continueRequest or Fetch.continueResponse. This protocol method lets BrowserDownloads stay independent from the exact session class used elsewhere.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the promised way to start an asynchronous task without blocking the current event callback. It is used so paused browser requests can be released promptly while the event handler itself stays simple.

**Data flow**: It receives a coroutine, which is a piece of asynchronous work waiting to run. The real session implementation schedules that work in the background and does not return a result here.

**Call relations**: BrowserDownloads.on_fetch_paused calls this whenever it decides how a paused request should be continued. The scheduled work then runs _continue_request or _continue_response.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This is the promised test for whether a browser event belongs to the main page frame rather than an embedded frame. That distinction matters because only top-level PDF navigations should be forced into downloads.

**Data flow**: It receives the session ID and the frame ID from a browser event. The real implementation compares them with the browser’s known frame structure and returns true or false.

**Call relations**: BrowserDownloads.on_fetch_paused uses this before changing response headers. Without this check, an embedded PDF inside a page could be wrongly turned into a download and break the page’s normal rendering.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request or response for inspection. It must always arrange for the paused traffic to continue, and it may turn a top-level PDF response into a real download.

**Data flow**: It reads the request ID, response code, response headers, and frame ID from the browser event. If the event is only a request-stage pause, it schedules a plain continue. If it is a response, it checks the content type and whether the frame is the main page, then schedules a response continue, optionally with a download-forcing header change. It returns nothing immediately.

**Call relations**: This is the main event entry point for paused fetch traffic. It uses _content_type to understand response headers, asks the session whether the frame is top-level, and hands off the actual browser command to _continue_request or _continue_response through the session’s background-task runner.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This helper tells Chrome to let a paused request continue unchanged. It exists because a paused request blocks page loading until the automation layer releases it.

**Data flow**: It receives the browser session ID and Chrome’s request ID. It sends a Fetch.continueRequest command through the browser connection. If Chrome rejects the command, times out, or the connection is no longer usable, it logs a warning instead of crashing the caller.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this when Chrome paused before there is a response code to inspect. It is the simple pass-through path for request-stage pauses.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This helper tells Chrome to continue a paused response, optionally changing its headers so the browser downloads it. It is the point where a viewer-only file such as a PDF can be nudged into becoming an attachment.

**Data flow**: It receives the session ID, request ID, response code, original headers, and a true-or-false force flag. If force is false, it sends a simple Fetch.continueResponse command. If force is true, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, then sends the modified response details. Failures are logged as warnings.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this after deciding whether a response should be left alone or forced into a download. It hands the final instruction to Chrome through the session’s browser connection.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a download. It creates a small local record so later code can track completion and report the file name.

**Data flow**: It reads the download GUID, which is Chrome’s unique download ID, and the suggested filename from the browser event. It appends a new Download record to the session’s downloads list with state set to inProgress. It does not return anything.

**Call relations**: This is called when the browser reports a download-begin event. Later, on_download_progress updates the same record, became_download notices that a new record appeared, and wait can return the completed download.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the known state of an existing download. It keeps the local download list in step with Chrome’s progress events.

**Data flow**: It reads the download GUID and the new state from the event. It searches the session’s downloads list for a matching GUID and changes that record’s state. It returns nothing, and if no matching record is found, nothing is changed.

**Call relations**: This is called after on_download_begin as Chrome reports progress. BrowserDownloads.wait depends on these state updates to know when a download has completed.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This function waits briefly to see whether an attempted navigation turned into a download. It is useful because a click or navigation may not produce a normal page load if the browser starts downloading a file instead.

**Data flow**: It receives the number of downloads that existed before the navigation. For up to a short grace period, it repeatedly checks whether the downloads list has grown. It returns true if a new download appears, otherwise false.

**Call relations**: Other navigation logic can call this after starting an action to distinguish “the page is still loading” from “the browser started a download.” It watches the list populated by on_download_begin.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This function waits until a browser download has completed and then reports which download finished. It gives callers a reliable way to pause until the browser says the file is done.

**Data flow**: It reads an optional timeout value from the input arguments, using max_wait_seconds if none is provided. It repeatedly scans the session’s downloads list for records whose state is completed. If one appears before the deadline, it returns the most recent completed download. If none appears in time, it raises a TimeoutError.

**Call relations**: This is the public waiting operation for download completion. It relies on float_or_default to interpret the timeout and on on_download_progress to keep download states current.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns a caller-provided timeout value into a floating-point number, or uses a default when no value was provided. It also rejects values that are not numeric.

**Data flow**: It receives a JSON-like value and a default number. If the value is an integer, float, or non-empty string, it converts it to a float. If the value is missing, it returns the default. For other kinds of values, it raises a ValidationError.

**Call relations**: BrowserDownloads.wait calls this before it starts waiting. That keeps timeout parsing in one small place and lets wait focus on watching download state.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper extracts the main content type from HTTP response headers. It is used to recognize files, such as PDFs, that should be forced into downloads.

**Data flow**: It receives a list of JSON-like header entries. It searches for a header named Content-Type, ignoring letter case. If found, it returns the value before any semicolon, trimmed and lowercased, such as application/pdf. If no content type is present, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this while deciding whether a paused response is a top-level PDF-like document that should be changed into an attachment download.

*Call graph*: called by 1 (on_fetch_paused).


### Tab orchestration
Maintains browser tab state and translates high-level tab and navigation actions into Chrome DevTools Protocol operations.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`orchestration` · `request handling`

A browser automation tool needs to know which tabs exist, which one is current, and when a page has really finished loading enough to use. This file provides that tab layer. It listens for browser target events, where a “target” is Chrome’s low-level name for something like a page tab, and turns those events into a clean list of Tab objects.

The main class, BrowserTabs, is like a tab clerk at a front desk. When Chrome says a tab was created or destroyed, it records that news. When someone asks for the current page, it first synchronizes its local list with those recorded events. If there are no tabs, it creates a blank one.

Navigation is also centralized here. A requested address is normalized, so “example.com” becomes “https://example.com”, while special commands like “back” and “forward” stay unchanged. The file then either loads a new URL or steps through browser history. It waits for page events such as document readiness and paint events, so later code does not act too early. It also treats downloads carefully: if navigation appears to fail because the URL became a download, it does not report that as a broken page load.

Without this file, the rest of the system would have to speak directly to the browser’s low-level protocol and guess which tabs are alive, loaded, or safe to use.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied navigation target into something the browser can load. It leaves special browser commands and already-complete URLs alone, but adds “https://” to plain site names.

**Data flow**: It receives a text value such as “example.com”, “https://example.com”, “back”, or “about:blank”. It checks whether the value is a special case or already starts with a URL scheme like “http:”. It returns either the original value or a safer full URL with “https://” added.

**Call relations**: BrowserTabs.navigate calls this before deciding how to move the tab. That lets navigate work with friendly input while still sending a proper target to the browser.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the in-memory record for one browser tab. It stores the browser’s target and session identifiers, plus state needed for keyboard input and page frames.

**Data flow**: It receives a target ID and a session ID from the browser. It saves them, creates a fresh KeyboardState, starts empty frame sequence tracking, and creates a root FrameNode for the tab. The result is a Tab object ready for later browser actions.

**Call relations**: BrowserTabs.attach_tab calls this after Chrome has attached to a browser target. The new Tab is then added to the browser session’s tab list by attach_tab, sync, page, or create.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when the system needs human-friendly or repeatable frame labels instead of long browser frame IDs.

**Data flow**: It receives a frame ID. If that frame has not been seen before, it assigns the next available number. It returns the number for that frame, reusing the same number on later calls.

**Call relations**: This is a helper on Tab for other page or frame logic. It does not call out to the browser; it only updates the tab’s local frame numbering.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the low-level method used to send a command to Chrome DevTools Protocol. Chrome DevTools Protocol is the message API used to control and inspect the browser.

**Data flow**: A caller provides a protocol method name, optional parameters, and optionally a session ID for a specific tab. The implementing connection sends that message to the browser and returns the JSON-like response.

**Call relations**: BrowserTabs methods rely on this protocol method whenever they need Chrome to do something, such as attach to a tab, enable page events, navigate, create a target, or close a target.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how callers register interest in a future browser event before they trigger an action. This prevents missing an event that may arrive very quickly.

**Data flow**: A caller gives one or more event names and optionally a session ID. The implementing connection returns a future, which is a promise-like object that will later contain the event data.

**Call relations**: Navigation helpers use this before sending commands that should cause page events. They later hand the future to BrowserTabCdp.wait to pause until the expected browser event arrives.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how to wait for a previously expected browser event, with a time limit. The time limit keeps the automation from hanging forever if the browser never sends the event.

**Data flow**: It receives a future created by expect and a timeout in seconds. The implementing connection waits until the event data arrives or the timeout is reached, then returns the event data or raises an error.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this after sending navigation commands, so they can wait for page progress before continuing.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how BrowserTabs gets the active browser protocol connection. This keeps BrowserTabs independent from the concrete connection implementation.

**Data flow**: It takes the session object as context and returns an object that can send commands, expect events, and wait for them. It does not itself send browser messages.

**Call relations**: Most BrowserTabs methods call this when they need to talk to Chrome. The actual session supplies the real connection behind this protocol.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how BrowserTabs gets access to download tracking. This is needed because some navigations turn into file downloads instead of normal web pages.

**Data flow**: It reads the session context and returns a BrowserDownloads helper. BrowserTabs can then ask whether a navigation became a download.

**Call relations**: BrowserTabs._goto uses this when Chrome reports a navigation error. If a new download appeared, _goto treats the situation as an expected download rather than a failed page load.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how BrowserTabs can run JavaScript inside a tab. JavaScript is used here to ask the page for simple facts such as its current URL and title.

**Data flow**: It receives a tab session ID and a JavaScript expression. The implementing session evaluates that expression in the browser tab and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info calls this to build friendly tab summaries. That information is then used after navigation, when listing tabs, and when collecting tab titles.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed at startup. This prevents old tabs from being mistaken for newly created tabs later.

**Data flow**: It receives a browser response containing target information. It reads the target IDs from that response, validates the expected list and map shapes, and stores the IDs in tab_events.initial_targets.

**Call relations**: This is used during setup when the browser’s existing targets are first discovered. Later, BrowserTabs.on_target_created compares new target events against this remembered set.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notes that Chrome reported a new page tab. It filters out non-page targets and tabs that were already present when tracking began.

**Data flow**: It receives event parameters from Chrome and an optional session ID. It reads targetInfo, checks that the target is a page with a string target ID, and appends that ID to created_targets if it is truly new.

**Call relations**: An event dispatcher calls this when Chrome emits a target-created event. BrowserTabs.sync later consumes the recorded target IDs and attaches to those new tabs.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notes that Chrome reported a target was closed or destroyed. This lets the local tab list catch up with what actually happened in the browser.

**Data flow**: It receives event parameters and reads the targetId. If the ID is a string, it adds it to destroyed_targets. Nothing is removed immediately from the tab list here.

**Call relations**: An event dispatcher calls this when Chrome emits a target-destroyed event. BrowserTabs.sync later removes matching tabs from the stored list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. The main frame is the top-level page, not a smaller embedded frame such as an iframe.

**Data flow**: It receives browser event parameters and a session ID. If there is no session ID, it does nothing. If the event’s frame ID belongs to the tab’s top-level frame, it tells the settle tracker that this session is loading.

**Call relations**: The browser event flow calls this on frame-loading events. It uses BrowserTabs.is_top_level_frame to avoid reacting to nested frames, then updates the Settle object used by navigation waiting.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having reached the DOM content stage. This means the page’s basic document structure is available, even if every image or later script has not finished.

**Data flow**: It receives event parameters and a session ID. If a session ID is present, it tells the settle tracker that the session has loaded its document content.

**Call relations**: The browser event flow calls this when Chrome reports DOM content readiness. Navigation waiting uses this signal as one part of deciding that the page has settled enough.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when Chrome reports an important visual lifecycle event for the main frame. “Painted” means the browser has drawn meaningful content to the page.

**Data flow**: It receives event parameters and a session ID. It ignores events without a session ID and lifecycle names that are not paint-related. For paint events on the top-level frame, it tells the settle tracker that the page has painted.

**Call relations**: The browser event flow calls this for lifecycle events. It uses BrowserTabs.is_top_level_frame before updating Settle, so an embedded frame does not make the whole tab look ready too early.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main page frame of a known tab. This avoids confusing iframe activity with whole-page navigation.

**Data flow**: It receives a session ID and a frame ID from a browser event. It compares them against the stored tabs, where each tab’s target ID represents its top-level frame. It returns true if one tab matches both values, otherwise false.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating loading or paint state. It is a small guard that keeps page-settling decisions accurate.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects this automation layer to an existing browser tab target. It prepares the tab so later code can observe page events, network activity, downloads, and viewport size.

**Data flow**: It receives a target ID. It asks the browser to attach to that target, reads the returned session ID, initializes browser domains for that session, enables document fetch interception for downloads, sets the viewport size, and returns a new Tab object.

**Call relations**: BrowserTabs.sync uses this for tabs Chrome created elsewhere, BrowserTabs.page uses it when creating the first blank tab, and BrowserTabs.create uses it for explicit new tabs. It calls BrowserTabs.init_session before constructing the Tab.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser event streams needed for a tab session. Without this, the system would not receive enough information about page loading, document structure, or network activity.

**Data flow**: It receives a session ID. It sends protocol commands to enable Page events, lifecycle events, DOM access, and Network events for that session. It returns nothing, but the browser session is left ready for automation.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target. It is the setup step before the tab is added to normal tracking.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Reconciles the local tab list with browser events that were recorded earlier. It removes closed tabs and attaches to newly created ones.

**Data flow**: It reads created_targets and destroyed_targets from browser.tab_events. It clears and applies destroyed target IDs by filtering the tab list. Then it processes created target IDs one by one, skips duplicates, and tries to attach to each new target. If attaching fails because the target disappeared or is unusable, it ignores that target.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before they rely on the tab list. It calls BrowserTabs.attach_tab when a new browser target needs to become a tracked Tab.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the requested tab, creating a blank tab first if none are open. It is the safe way for other tab actions to get a usable Tab object.

**Data flow**: It receives an optional tab index. It first syncs the tab list. If there are no tabs, it asks Chrome to create an about:blank target and attaches to it. If no index was requested, it returns the last tab as the current tab. If an index was requested, it validates the index and returns that tab or raises a validation error.

**Call relations**: BrowserTabs.navigate calls this before loading a page, and BrowserTabs.close calls it before closing a tab. It uses BrowserTabs.sync and may use BrowserTabs.attach_tab if it must create the first tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new page, backward, or forward, then waits until the page is settled enough to inspect. It returns the tab’s resulting URL and title.

**Data flow**: It receives a URL-like value and an optional tab index. It gets the tab, normalizes the target string, resets the settle tracker, and either performs a history step or a normal navigation. Afterward it waits for loading and paint signals through the settle tracker, then reads and returns tab information.

**Call relations**: BrowserTabs.create calls this after opening a new tab with the requested URL. Internally it calls normalize_url, BrowserTabs.page, BrowserTabs._history_step or BrowserTabs._goto, and BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work of navigating a tab to a specific URL. It waits for the page’s DOM content event when Chrome indicates a real page load is happening.

**Data flow**: It receives a Tab and a full URL. It records how many downloads already exist, sets up an expectation for the DOM content event, sends the Page.navigate command, and checks Chrome’s response. If Chrome reports an error but a new download appeared, it treats that as success. If there is an error with no download, it raises a runtime error. If there is a loader ID, it waits for DOM content; otherwise it cancels the wait because no full load happened.

**Call relations**: BrowserTabs.navigate calls this for normal URL navigation. It works closely with the connection’s expect, send, and wait methods, and with the download reader for download-shaped navigations.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browsing history. If there is no entry in that direction, it simply does nothing.

**Data flow**: It receives a Tab and a step value, usually -1 for back or 1 for forward. It asks Chrome for the navigation history, validates the current index, calculates the desired entry, and stops if that entry is outside the list. Otherwise it expects a navigation event, sends the command to navigate to that history entry, and waits for confirmation.

**Call relations**: BrowserTabs.navigate calls this when the normalized target is “back” or “forward”. It uses browser protocol history data and waits for either a full frame navigation or an in-document navigation.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. This gives callers a simple summary of where the tab ended up.

**Data flow**: It receives a Tab. It runs a small JavaScript expression in that tab that returns location.href and document.title, validates the returned shape, and produces a dictionary with string URL and title values.

**Call relations**: BrowserTabs.navigate calls this after navigation, BrowserTabs.tabs_context calls it while building the tab list, and BrowserTabs.tab_titles calls it when only titles are needed.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a friendly snapshot of all open tabs, including which one is considered current. This is useful for reporting browser state to higher-level tools or users.

**Data flow**: It first syncs the stored tab list with browser events. It treats the last tab as current when tabs exist, then loops through each tab, reads its URL and title, and returns a dictionary containing the current tab index and a list of tab summaries.

**Call relations**: BrowserTabs.close calls this after removing a tab so the caller receives the updated tab state. It also relies on BrowserTabs.sync and BrowserTabs.tab_info to make the snapshot current and readable.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of the currently tracked tabs. This is a lightweight view when callers do not need full tab details.

**Data flow**: It loops through the stored tabs, reads each tab’s info, extracts the title, turns missing titles into empty strings, and returns the resulting list of strings.

**Call relations**: It calls BrowserTabs.tab_info for each tab. Unlike tabs_context, it does not sync first, so it reflects the tab list already stored on the browser session.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new tab and navigates it to the requested URL. It returns the new tab’s index along with its final URL and title.

**Data flow**: It receives an optional URL, defaulting to about:blank. It asks Chrome to create a blank target, attaches to that target, stores the new Tab, navigates that tab to the requested URL, and returns the tab ID plus the resulting tab information.

**Call relations**: This is the high-level new-tab operation. It uses BrowserTabs.attach_tab to prepare the browser session, then BrowserTabs.navigate to load the requested page.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a selected tab and returns the updated list of tabs. It also clears out-of-process iframe session tracking, because closing a tab can make those related sessions stale.

**Data flow**: It receives an argument dictionary, reads and converts tab_id if present, gets the matching tab, sends Chrome a close-target command, removes that Tab from the local list, clears oop_sessions, and returns the current tab context.

**Call relations**: Higher-level close-tab commands call this. It uses _tab_id to interpret the requested tab ID, BrowserTabs.page to find the tab, and BrowserTabs.tabs_context to report the state after closing.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loose JSON-style tab ID value into either an integer tab index or no selection. This lets callers pass tab IDs as numbers or numeric strings.

**Data flow**: It receives a value that may be an integer, float, string, or something else. Integers are returned as-is, floats are converted to integers, non-empty strings are parsed as integers, and all other values become None.

**Call relations**: BrowserTabs.close calls this before asking for the tab to close. Returning None means close will use BrowserTabs.page’s default current-tab behavior.

*Call graph*: called by 1 (close).

# CDP Connection, Runtime, Tabs, and Page Lifecycle  `stage-12.2.2`

This stage is the browser control room. It sits behind the main work loop and gives the rest of the system a reliable way to drive Chrome, watch what happens, and know when the page is ready for the next step. The cdp.py file is the main wire to Chrome. It opens a WebSocket, which is a two-way message pipe, sends DevTools commands, receives replies, and passes browser events to the right waiting task. On top of that, runtime.py offers a safer way to run JavaScript inside the current page and turns script failures into normal Python errors. tabs.py manages the visible workspaces: opening tabs, closing them, switching between them, and navigating to URLs. settle.py acts like a patient spotter after clicks or page loads, waiting for useful changes while ignoring endless background noise. dialogs.py quickly answers alerts, prompts, confirmations, and leave-page warnings so they do not block automation. downloads.py watches for files, triggers downloads when needed, and waits until they are actually saved.

## Files in this stage

### CDP Command Channel
The base communication layer opens and manages the Chrome DevTools Protocol WebSocket connection, command replies, and event routing.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `active during browser session communication`

Chrome’s DevTools Protocol, often called CDP, is like a remote control for Chrome: you send named commands such as “open this page” or “give me this target,” and Chrome sends back replies and spontaneous events. This file provides the transport layer for that conversation over a WebSocket, which is a long-lived two-way network connection.

The main class, CdpConnection, keeps track of every command it sends by giving it a unique number. When Chrome replies with that number, the connection completes the matching waiting task. This prevents different overlapping commands from getting their answers mixed up.

The file also supports events. Some code can register a permanent listener with on, while other code can say “I expect one of these events soon” with expect and then wait for it with a timeout. This is like leaving both a doorbell subscription and a one-time sticky note for a specific delivery.

A background reader task continuously reads incoming WebSocket messages. It separates command replies from events, turns error replies into CdpError exceptions, and wakes up the right futures. If the WebSocket closes, it fails all pending commands and waits immediately, so callers do not hang forever wondering what happened.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Builds a clear exception for a failed Chrome DevTools Protocol command. It records which command failed, Chrome’s error code, and a readable message so higher-level code can report or react to the failure.

**Data flow**: It receives the command name, a numeric error code, and Chrome’s error message. It stores the command and code on the exception object, then creates a human-readable error string. The result is an exception that can be attached to the waiting command instead of a successful reply.

**Call relations**: When an incoming command response contains an error, CdpConnection._dispatch creates this exception and gives it to the command’s waiting future. That means the code that originally called send sees a normal Python error instead of having to inspect raw protocol data.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual WebSocket address needed to talk to Chrome. If the caller already gave a WebSocket URL, it returns it unchanged; otherwise it asks Chrome’s HTTP debugging endpoint where the WebSocket is.

**Data flow**: It takes a URL and HTTP headers. If the URL starts with ws:// or wss://, that is already the final connection address and comes back directly. Otherwise it requests the /json/version endpoint, checks that the HTTP request succeeded, reads webSocketDebuggerUrl from the JSON response, and returns it as text.

**Call relations**: This is a setup helper for code that only knows Chrome’s HTTP debugging address. It uses an HTTP client to ask Chrome for the real DevTools WebSocket URL, and uses the project’s wire-format helper to make sure the returned value is actually a string.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Creates the in-memory bookkeeping for one Chrome DevTools WebSocket connection. It prepares places to store pending command replies, event listeners, one-time event waits, and the background reader task.

**Data flow**: It receives an already-open WebSocket connection. It saves it, starts the command id counter at zero, and creates empty collections for pending commands, event listeners, event waiters, and the reader task reference. Nothing is sent to Chrome yet.

**Call relations**: CdpConnection.open creates the WebSocket first, then calls this initializer to wrap it in the project’s higher-level CDP connection object. Later methods such as send, expect, on, and _read_loop all rely on the state prepared here.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a new WebSocket connection to Chrome and starts the background reader that will process all incoming messages. This is the usual way higher-level browser session code obtains a working CDP connection.

**Data flow**: It receives a WebSocket URL and optional headers. It dials the WebSocket with a large allowed message size, builds a CdpConnection around it, starts _read_loop as an asynchronous background task, and returns the ready connection.

**Call relations**: BrowserSession._bootstrap calls this during browser startup or attachment. After open returns, other code can immediately send CDP commands while the reader task quietly receives replies and events in the background.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader task first, then closes the WebSocket itself.

**Data flow**: It reads the stored reader task. If there is one, it cancels it and waits for cancellation to finish, ignoring the expected cancellation exception. Then it clears the reader reference and closes the underlying WebSocket connection.

**Call relations**: This is used when the browser session is ending or the project no longer needs to talk to Chrome. By cancelling the reader before closing the socket, it avoids leaving a background task running after the connection is no longer usable.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one command to Chrome and waits for that command’s reply. It gives each command a unique id so the reply can be matched correctly, even when several commands are in flight at the same time.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for commands aimed at a specific browser target. It builds a JSON message with a new id, stores a future under that id, sends the message over the WebSocket, and waits up to the command timeout. If Chrome replies, the future produces the result dictionary; if time runs out, the pending entry is removed and a timeout error is raised.

**Call relations**: Higher-level browser code calls this whenever it needs Chrome to do something or return information. The background _read_loop later receives Chrome’s response and passes it to _dispatch, which finds the stored future and completes the send call.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a callback for a recurring CDP event. Use this when some part of the system wants to be notified every time Chrome sends a certain event type.

**Data flow**: It receives an event name and a listener function. It adds the listener to the list for that event. It does not contact Chrome or return event data immediately; it only records the subscription locally.

**Call relations**: When _dispatch later receives an event message with this event name, it calls each registered listener with the event parameters and optional session id. This gives long-lived observers a simple way to react to browser activity.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one or more possible CDP events. This is useful when code has just done something and needs to pause until Chrome reports that a related event happened.

**Data flow**: It receives one or more event names and optionally a session id to narrow the wait to a specific browser target. It creates a future, stores it with the event names and session filter, and returns the future to the caller. The future will later contain the event parameters.

**Call relations**: Callers usually create this future before or around a CDP action, then pass it to wait to apply a timeout. When _dispatch sees a matching event, it completes the future and removes that one-time waiter.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for a previously created event future, but only for a limited time. It also cleans up the waiter afterward so old waits do not pile up.

**Data flow**: It receives a future from expect and an optional timeout. It awaits that future inside a time limit and returns the event parameters if the event arrives. Whether it succeeds, fails, or times out, it removes that future from the stored waiter list.

**Call relations**: This pairs with expect: expect declares what event should arrive, and wait actually pauses until it does or until time runs out. _dispatch is the part that completes the future when Chrome sends the matching event.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads raw messages from Chrome and feeds them into the dispatcher. It is the background worker that keeps command replies and browser events flowing to the rest of the program.

**Data flow**: It reads each raw WebSocket message, parses the JSON text into a dictionary, and passes it to _dispatch. If the WebSocket closes, it stops reading. Before it exits, it marks every still-pending command and event wait as failed, then clears those stored waits.

**Call relations**: CdpConnection.open starts this as a background task. It is the only place that reads from the WebSocket, and it hands every decoded message to _dispatch. Its final cleanup is important because send and wait callers would otherwise remain stuck forever after a connection loss.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Sorts one incoming CDP message into the right bucket: a command reply, an error reply, or a browser event. It then wakes the right waiter or calls the right listeners.

**Data flow**: It receives one decoded JSON message. If the message has an integer id, it treats it as a command response, finds the matching pending future, and either gives it the result dictionary or a CdpError. If there is no id but there is a method name, it treats the message as an event, extracts its parameters and optional session id, completes any matching one-time waiters, and calls all registered listeners for that event.

**Call relations**: _read_loop calls this for every message arriving from Chrome. It is the central traffic director: replies go back to send callers, one-time events go to expect/wait callers, and recurring events go to callbacks registered with on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### Browser Event Handling
These helpers handle browser-side interruptions and file transfers so automation can continue without freezing or missing downloads.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

JavaScript dialogs are small pop-up boxes created by a web page. In browser automation they are more than a nuisance: while one is open, the page can stop sending later browser events until the dialog is answered. This file is the safety valve that keeps the browser session moving.

The main rule is simple. Alerts and “before unload” warnings are automatically accepted, because there is usually no meaningful alternative if the automation is to continue. Confirmation boxes and prompts are dismissed, because accepting them could silently agree to something the website asked, such as “Are you sure you want to delete this?” Either way, the file writes a short human-readable note into the browser session’s dialog list, so the agent can later report that the dialog appeared.

The `BrowserDialogs` class does the real work. When a dialog event arrives, `on_dialog` reads the dialog type and message, decides whether to accept or dismiss it, records the decision, and starts a background task to send the answer to the browser. The actual browser command is sent through Chrome DevTools Protocol, often shortened to CDP, which is a control channel for driving the browser. If that command fails, the code logs a warning rather than crashing the whole session.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the object that can send commands to the browser. It represents the ability to ask the browser to do something through Chrome DevTools Protocol, the browser control channel.

**Data flow**: It receives a command name, optional command details, and optionally a browser session identifier. It sends that request to the browser and returns the browser’s JSON-like reply.

**Call relations**: This file does not implement the method; it describes what `BrowserDialogs._answer_dialog` needs from a connection object. When a dialog must be answered, `_answer_dialog` calls this method to send the browser command that accepts or dismisses the dialog.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the active browser connection from a session object. `BrowserDialogs` uses it when it needs to send a command back to the browser.

**Data flow**: It takes the current session object as its source of information and returns an object capable of sending browser commands. It does not itself decide what command to send.

**Call relations**: This is part of the session interface that `BrowserDialogs` relies on. During `_answer_dialog`, the dialog helper asks the session for its connection, then uses that connection to send the dialog response.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for a browser session to start a small asynchronous job without waiting for it immediately. Here it is used so dialog answering can begin right away without blocking the event handler.

**Data flow**: It receives a coroutine, which is a paused asynchronous task ready to run. The session schedules that task in the background; there is no direct return value used by this file.

**Call relations**: This method is called by `BrowserDialogs.on_dialog` after the dialog decision has been made. The background task it starts is `BrowserDialogs._answer_dialog`, which actually sends the accept-or-dismiss command to the browser.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is called when the browser reports that a JavaScript dialog has appeared. It decides whether to accept or dismiss the dialog, records that decision for later visibility, and starts the browser reply immediately.

**Data flow**: It receives the dialog details from the browser and an optional session identifier. It reads the dialog type and message, treats missing types as alerts, accepts alerts and before-unload warnings, dismisses other dialog types, appends a short note such as `confirm dismissed: ...` to the session’s dialog log, and schedules a background answer task. It does not wait for the browser command to finish.

**Call relations**: This is the front door for dialog events in this file. When a browser dialog event arrives, this function makes the policy decision, then hands the actual browser communication to `BrowserDialogs._answer_dialog` by asking the session to run it in the background.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This sends the actual command that tells the browser to accept or dismiss the currently open JavaScript dialog. It also protects the rest of the session from failures while doing so.

**Data flow**: It receives the relevant browser session identifier and the already-chosen accept-or-dismiss value. It asks the browser session for its connection, sends the `Page.handleJavaScriptDialog` command with that choice, and returns nothing. If the command fails because of a CDP error, timeout, or runtime problem, it logs a warning instead of raising the error further.

**Call relations**: This function is started by `BrowserDialogs.on_dialog` after the policy decision has been made. It is the final handoff from the local decision-making code to the browser control channel.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download waiting`

This file sits between Chrome’s download events and the rest of the browser agent. Its job is to make downloads predictable. Without it, a page could hang because a paused browser request was never released, or a PDF could open inside Chrome’s built-in viewer where the agent cannot read it properly.

The file listens for two kinds of browser activity. First, it watches paused network requests from Chrome’s DevTools Protocol, which is Chrome’s remote-control API. Most paused requests are simply allowed to continue. But if the request is a top-level page navigation and the response is a PDF, the file rewrites the response headers so Chrome treats it as an attachment download instead of opening it inline. This is like changing a “display this in the shop window” label into a “put this in a bag for pickup” label.

Second, it records download lifecycle events. When Chrome says a download has started, the file stores its identifier, suggested filename, and current state. When Chrome reports progress, it updates that state. Other code can then ask whether a navigation turned into a download, or wait until a download has completed. This file does not read the downloaded bytes; it only tracks Chrome’s download records and tells callers which download finished.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the low-level method that sends commands to Chrome. It exists as a protocol, meaning this file only states what the method must look like; another object supplies the real implementation.

**Data flow**: It receives a Chrome command name, optional command details, and an optional session identifier for a specific browser target. The real implementation is expected to send that command to Chrome and return Chrome’s response as a dictionary-like JSON object.

**Call relations**: The download code calls this through BrowserDownloads._continue_request and BrowserDownloads._continue_response when it needs to release a paused browser request. This protocol keeps the download logic independent from the exact connection class.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This is the expected method for getting the Chrome connection used by the download tracker. It lets this file send browser commands without knowing how the connection is stored.

**Data flow**: It takes no extra input beyond the session object. It returns an object that can send Chrome DevTools Protocol commands.

**Call relations**: BrowserDownloads._continue_request and BrowserDownloads._continue_response use this method before sending commands to Chrome. The actual session object elsewhere in the project provides the concrete connection.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected method for starting an asynchronous task in the background. The download code uses it so that releasing a paused browser request does not block the event handler.

**Data flow**: It receives a coroutine, which is a piece of asynchronous work waiting to be run. The real session implementation schedules that work and does not return a meaningful value here.

**Call relations**: BrowserDownloads.on_fetch_paused calls this whenever Chrome pauses a request. It hands off either _continue_request or _continue_response so the browser can be unblocked promptly.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This is the expected method for deciding whether a browser event belongs to the main page frame rather than an embedded frame. The distinction matters because only main-page PDF navigations should be forced into downloads.

**Data flow**: It receives a browser session identifier and a frame identifier from Chrome’s event data. It returns true if that frame is the page’s top-level frame, and false otherwise.

**Call relations**: BrowserDownloads.on_fetch_paused uses this before changing a PDF response into a download. That prevents embedded PDFs, such as a PDF shown inside another page, from being changed unexpectedly.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request. It must always release the request somehow, because leaving it paused would make the page stop loading.

**Data flow**: It receives Chrome’s pause event details and the optional browser session id. It checks for a request id, looks at whether Chrome has reached the response stage, reads the response headers, and decides whether the response should be forced into a download. It then starts background work to continue the request or response.

**Call relations**: Chrome event-dispatch code calls this when a Fetch pause event arrives. It uses _content_type to recognize PDFs, asks the session whether the request is for the top-level frame, and then hands the actual Chrome command work to _continue_request or _continue_response through spawn_background.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This asynchronous helper tells Chrome to let a paused request continue when there is no response to rewrite. It is a safety valve that keeps page loading from getting stuck.

**Data flow**: It receives the browser session id and Chrome’s request id. It sends a Fetch.continueRequest command to Chrome. If Chrome rejects the command, times out, or the runtime is no longer usable, it logs a warning instead of crashing the whole flow.

**Call relations**: BrowserDownloads.on_fetch_paused calls this in the background for pause events that happen before a response is available. It uses the session’s connection method to reach Chrome.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This asynchronous helper tells Chrome to continue a paused response, optionally changing it so Chrome downloads the file instead of displaying it. It is the point where top-level PDFs are turned into attachments.

**Data flow**: It receives the browser session id, request id, HTTP response code, response headers, and a yes-or-no decision about forcing a download. If forcing is needed, it removes any existing Content-Disposition header and adds Content-Disposition: attachment. It then sends Fetch.continueResponse to Chrome. Failures are logged as warnings.

**Call relations**: BrowserDownloads.on_fetch_paused starts this in the background after it has inspected the paused response. This helper performs the final handoff to Chrome through the session connection.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a download. It creates a local record so later code can track whether that download finishes.

**Data flow**: It receives Chrome’s download-start event details. It pulls out Chrome’s download identifier and suggested filename, falling back to "download" if no name was suggested. It appends a new download record with the state set to inProgress to the session’s download list.

**Call relations**: Browser event-dispatch code calls this when Chrome reports a download beginning. BrowserDownloads.wait and BrowserDownloads.became_download later rely on the stored record.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the saved state of a download as Chrome reports progress. It keeps the local download list in step with Chrome’s view of the download.

**Data flow**: It receives Chrome’s progress event details, reads the download identifier and state, then searches the stored downloads for the matching identifier. When it finds the match, it replaces that record’s state with the latest state from Chrome.

**Call relations**: Browser event-dispatch code calls this during download progress events. BrowserDownloads.wait later checks these updated states to know when a download has completed.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This function briefly checks whether an action that looked like navigation actually turned into a download. It gives Chrome a short grace period to emit the download-start event.

**Data flow**: It receives the number of downloads that existed before the action. It watches the session’s download list for up to a fixed short time. If the list grows, it returns true; if not, it returns false.

**Call relations**: Higher-level navigation code can call this after triggering a page load. It does not call other project code; it simply polls the stored download list and sleeps briefly between checks.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This function waits until a browser download has finished and returns the finished download record. It gives callers a simple way to say, “wait for the file Chrome is downloading.”

**Data flow**: It receives an argument dictionary that may contain a timeout. It converts that timeout with float_or_default, then repeatedly checks the stored downloads for ones marked completed. If one finishes before the deadline, it returns the most recent completed download. If none finish in time, it raises a TimeoutError.

**Call relations**: Higher-level browser commands call this when they need to wait for a download result. It depends on on_download_begin and on_download_progress having kept the session’s download list up to date.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns an optional timeout-like value into a floating-point number. It gives callers flexible input while still rejecting values that are not numeric.

**Data flow**: It receives a JSON value and a default number. If the value is an integer, float, or non-empty string, it returns it as a float. If the value is missing, it returns the default. If the value is some other kind of data, it raises a ValidationError.

**Call relations**: BrowserDownloads.wait uses this to interpret its timeout argument before starting its wait loop. Keeping this check separate makes the wait logic simpler and gives bad input a clear error.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper reads the Content-Type response header, which tells what kind of file or page Chrome is receiving. The download logic uses it to recognize PDFs.

**Data flow**: It receives a list of header objects from Chrome. It searches for a header named Content-Type, ignores letter case, removes anything after a semicolon such as character-set details, and returns the cleaned lowercase type. If no content type is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this while deciding whether a top-level response should be forced into a download. Its result is compared with the list of file types that Chrome should not show inline.

*Call graph*: called by 1 (on_fetch_paused).


### Page Runtime and Settling
These modules execute JavaScript in the page and decide when the page has finished reacting to navigations or user actions.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `request handling`

Browser automation often needs to ask the page a question, such as “what is this element’s text?” or “run this function on that page object.” The browser exposes that ability through the Chrome DevTools Protocol, often shortened to CDP, which is a JSON-based command channel for controlling and inspecting a browser. This file is a thin wrapper around CDP’s JavaScript runtime commands.

The main class, BrowserRuntime, is given a browser session. When code calls eval, it sends a Runtime.evaluate command with a JavaScript expression and asks the browser to return the result as plain JSON data. When code calls call_on, it sends Runtime.callFunctionOn, which runs a JavaScript function against an existing browser-side object. That is useful when the project already has a handle to something in the page and wants to inspect or transform it.

A key safety detail is raise_on_exception. Browser commands can succeed at the transport level while the JavaScript itself throws an error. Without this check, later code might treat a failed page script as a valid result. This file catches that case immediately and raises a Python RuntimeError with the browser’s error message.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This describes the shape of an object that can send a command to the browser over the Chrome DevTools Protocol. It is a promise-style interface: callers give it a command name, optional data, and an optional browser session id, and expect a JSON dictionary back.

**Data flow**: The caller provides a CDP method name, optional parameters, and optionally the id of the target page or frame session. An implementation sends that request to the browser and receives the browser’s JSON reply. This file only defines the expected interface, so the actual network or websocket work happens elsewhere.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both rely on this interface after asking the browser session for its connection. The protocol keeps BrowserRuntime independent from the concrete connection class, like using a standard plug shape without caring who made the socket.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This describes the shape of an object that can provide access to the browser’s CDP connection. BrowserRuntime uses it so it does not need to know how the browser session stores or creates that connection.

**Data flow**: There is no input besides the session object itself. The method returns an object that follows BrowserRuntimeCdp, meaning it can send commands to the browser and return JSON replies. As a protocol definition, this file states the contract but does not implement the storage or lookup.

**Call relations**: BrowserRuntime calls this when it needs to send Runtime.evaluate or Runtime.callFunctionOn. It acts as the doorway from the higher-level runtime helper into the lower-level browser communication layer.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the expression’s value as ordinary JSON-friendly data. It is used when Python code needs to ask the page for a simple computed value.

**Data flow**: It receives a session id and a JavaScript expression string. It sends a Runtime.evaluate command through the browser connection, asking the browser to return the result by value rather than as a remote browser object. It then checks whether the page threw a JavaScript exception; if not, it extracts the result’s value and returns it.

**Call relations**: This is one of the main public actions of BrowserRuntime. It calls BrowserRuntime.raise_on_exception right after receiving the browser response, then uses as_map to safely treat nested JSON pieces as dictionaries before reading the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function on an existing object inside the browser and returns the function’s result as a JSON dictionary. It is useful when earlier browser work produced an object id and later code wants to run page-side logic against that object.

**Data flow**: It receives a session id, a browser-side object id, a JavaScript function body, and optional argument values. It builds the CDP parameters, converts any arguments into the format CDP expects, and sends Runtime.callFunctionOn through the browser connection. After the browser replies, it raises a Python error if the JavaScript failed; otherwise it extracts the returned value and ensures it is a dictionary before returning it.

**Call relations**: Like eval, this is a main BrowserRuntime operation and immediately delegates error checking to BrowserRuntime.raise_on_exception. It also uses as_map to protect callers from malformed or unexpected JSON shapes in the browser reply.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser runtime reply for a JavaScript exception and turns it into a Python RuntimeError. It prevents failed page scripts from being silently mistaken for successful results.

**Data flow**: It receives the JSON dictionary returned by the browser. It looks for an exceptionDetails section; if none is present, it leaves everything unchanged and returns nothing. If exception details exist, it chooses the clearest available description or text from the browser response and raises an error that includes that message.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after sending a runtime command. It is the shared guardrail for both ways of running JavaScript, so the rest of the code can rely on either method failing loudly when the page-side code fails.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `active after browser actions and navigation, while deciding when it is safe to continue`

Browser automation often needs to answer a hard question: after we click something, when is the page ready enough to continue? Waiting for every network request can hang forever on modern sites, because ads, analytics, and background refreshes may never stop. Not waiting at all can read the page too early, before the click has actually changed anything.

This file provides a small state tracker called Settle. Think of it like a stage manager watching only the actors involved in the current scene. It counts foreground browser requests that the current action starts, notes when a document is loading, and records when the page has painted visible content. It deliberately ignores passive work such as images, fonts, low-priority prefetches, and common analytics hosts.

The main wait method first gives the browser one tiny task-queue turn so click handlers and immediate timers can start their requests. Then it waits according to what happened. If the page painted, it gives foreground requests a short grace period so visible shells can fill in content, but it will not wait forever. If no paint happens, it falls back to waiting until tracked network work becomes quiet, with a hard time limit. This balance makes automation faster and more reliable on busy real-world websites.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: This function decides whether a browser network request is important enough to wait for. It keeps likely foreground work, and filters out background or decorative traffic such as images, fonts, low-priority requests, and known analytics services.

**Data flow**: It receives one browser event dictionary describing a request. It reads the request type, priority, and URL host. If the request looks passive or analytics-related, it returns false; otherwise it returns true so the request can be counted as part of the current page action.

**Call relations**: When Settle.on_request_started sees a new browser request, it asks tracks_request whether that request should be added to the pending set. This keeps the later waiting logic focused on work that probably matters to the user-visible result.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: This creates a fresh settling tracker with empty bookkeeping. It prepares places to remember pending requests, whether any useful requests started, which browser sessions are loading, and which have painted visible content.

**Data flow**: It takes no outside data beyond the new object being created. It sets up empty sets for pending requests, loading sessions, and painted sessions, and sets the started counter to zero. The result is a Settle object ready to observe browser events.

**Call relations**: BrowserSession.__init__ creates one when a browser session starts, and BrowserSession.close also creates one during cleanup or reset. After that, browser event callbacks and wait operations use this shared tracker to decide when an action has settled.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: This clears the per-action settling state so the next browser action starts with a clean slate. It prevents old requests or old paint signals from making a new action look ready or busy for the wrong reason.

**Data flow**: It reads the existing tracked pending requests, started count, and painted sessions, then clears them and sets the started count back to zero. It leaves the loading set alone, so current document loading state is not accidentally forgotten.

**Call relations**: This is the reset button for the tracker before starting a new consequence-scoped wait. Other event methods can then refill the state with only the requests and paint events caused by the current action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records the start of a browser request if it belongs to a session and looks important enough to wait for. It is how the tracker learns that the current action caused real foreground work.

**Data flow**: It receives browser event data and an optional session id. It extracts the request id, checks that both ids are usable strings, and asks tracks_request whether the request matters. If so, it stores the pair of session id and request id in the pending set and increments the started counter.

**Call relations**: This method is fed by browser network-start events. It hands the filtering decision to tracks_request, then updates the state that Settle.wait later watches while deciding whether the page is still busy.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that a previously tracked browser request has ended. It removes that request from the pending work list so waiting can finish once all important work is quiet.

**Data flow**: It receives browser event data and an optional session id. It extracts the request id, and if the ids are valid, removes that session/request pair from the pending set. It does not return a value; it changes the tracker's internal state.

**Call relations**: This method is fed by browser request-finished or request-failed events. Settle.wait depends on this cleanup, because pending requests must disappear before the page can be considered settled.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: This notes that a browser session has started loading a document. It tells the settling logic not to declare the page ready while that document load is still underway.

**Data flow**: It receives a session id and adds it to the loading set. The output is a changed internal state saying that this session is currently loading.

**Call relations**: Browser lifecycle event handling calls this when loading begins. Later, Settle.wait checks the loading set alongside pending requests so it does not continue too early during navigation.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: This notes that a browser session is no longer loading a document. It clears one of the reasons the settling logic might keep waiting.

**Data flow**: It receives a session id and removes it from the loading set if present. The result is internal state showing that this session is no longer considered actively loading.

**Call relations**: Browser lifecycle event handling calls this when loading ends. Settle.wait uses the updated loading state to decide whether the page has become quiet enough to continue.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: This records that a browser session has painted visible content. A paint event is treated as strong evidence that the user can see the new page or state.

**Data flow**: It receives a session id and adds it to the painted set. That paint marker changes how Settle.wait behaves: it switches from broad waiting to a short post-paint grace period.

**Call relations**: Browser lifecycle event handling calls this when a paint event arrives. Settle.wait watches for this marker and then hands off to Settle._drain_after_paint so the code waits briefly for useful follow-up content without waiting forever.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: This is the main waiting routine used after a browser action. It pauses until the action's visible and foreground consequences have likely finished, or until a safety time limit is reached.

**Data flow**: It receives a browser control object, a session id, and a maximum number of seconds to wait. First it flushes the page's task queue so immediate click handlers and timers can start their work. Then it watches the stored paint, loading, and pending-request state. It returns when the page looks ready enough, or simply stops waiting when the deadline is reached.

**Call relations**: This is the center of the file's flow. It calls Settle._flush_page_tasks at the start, and if paint is seen it calls Settle._drain_after_paint for the shorter post-paint wait. It relies on the event-recording methods to keep pending, loading, and painted state up to date while it sleeps and checks again.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: This waits briefly after the page first paints. It gives real content a chance to arrive after an initial visual shell appears, but avoids waiting the full timeout on pages that keep making background requests.

**Data flow**: It receives a session id and an absolute deadline time. It creates a shorter grace deadline, then repeatedly checks whether the session is still loading or has pending tracked requests. If the page becomes quiet for a small gap, it returns; otherwise it returns when the grace time runs out.

**Call relations**: Settle.wait calls this once paint has been observed. It is the fast path for modern pages that become visible quickly but may continue doing network work in the background.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: This gives the browser page one tiny turn to run immediate JavaScript work triggered by the action. That helps make sure requests started by click handlers or zero-delay timers are seen before the settling wait begins.

**Data flow**: It receives the browser control object and a session id. It sends a small JavaScript promise through Chrome's debugging protocol and waits for it to resolve. If that protocol call fails or times out, it falls back to a short sleep instead of crashing the wait.

**Call relations**: Settle.wait calls this before checking readiness. It uses Cdp.send to talk to the browser, and its result makes the later pending-request checks more accurate because event messages caused by immediate page scripts have had a chance to arrive.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Tab Lifecycle
The tab layer provides the higher-level browser-page lifecycle operations for opening, closing, switching, and navigating tabs.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling`

A browser automation system needs a reliable map of what tabs exist, which browser session belongs to each tab, and whether a page has really finished loading. This file provides that map. Think of it like the front desk at a hotel: it knows which rooms exist, checks new guests in, removes rooms that closed, and gives callers the right room number before they ask for service.

The main state is a Tab, which stores the browser target ID, the DevTools session ID, keyboard state, and frame references. BrowserTabs is the working controller. It listens to browser events about pages being created or destroyed, attaches to new tabs, enables browser features such as page, DOM, and network events, and sets the viewport size. It also watches loading and painting events so navigation can wait until the page is reasonably settled instead of returning too early.

The file supports normal navigation, back and forward history movement, tab creation, tab closing, and simple tab summaries such as URL and title. It also has special care for downloads: if navigation “fails” because the URL became a file download, it treats that as a valid outcome instead of an error.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-provided destination into something the browser can navigate to. If the user types a plain domain like “example.com”, it adds “https://” so the browser gets a complete web address.

**Data flow**: It receives a string. If the string is one of the special navigation words “back”, “forward”, or “about:blank”, it leaves it alone. If it already looks like it has a scheme such as “http:” or “file:”, it leaves it alone. Otherwise it returns the same text with “https://” added at the front.

**Call relations**: BrowserTabs.navigate calls this before deciding whether to go to a URL or move through history. It uses a regular expression check to recognize already-complete URLs.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the in-memory record for one browser tab. This record ties together the browser target, the DevTools session, keyboard state, and frame tracking needed for later actions.

**Data flow**: It receives a target ID and session ID from the browser. It stores them, creates a fresh KeyboardState for this tab, starts an empty frame sequence counter, and creates an initial top-level FrameNode for the page.

**Call relations**: BrowserTabs.attach_tab calls this after the browser has successfully attached to a target. The new Tab is then stored in the browser session’s tab list.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame inside a tab a stable small number. This is useful when the system needs readable or repeatable frame identifiers instead of raw browser frame IDs.

**Data flow**: It receives a frame ID. If that frame has not been seen before, it assigns the next available number. It then returns the number for that frame.

**Call relations**: This is a helper on the Tab object. Other frame-related code can call it when it needs a consistent sequence number for a frame.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected way to send a Chrome DevTools Protocol command to the browser. The Chrome DevTools Protocol, or CDP, is the control channel used to ask Chrome to navigate, create tabs, inspect pages, and more.

**Data flow**: It is given a method name, optional parameters, and optionally a session ID for a specific tab. An implementation sends that command to the browser and returns the browser’s JSON-like reply.

**Call relations**: This file depends on this method throughout BrowserTabs, but the actual network or WebSocket implementation lives elsewhere. BrowserTabs uses it whenever it needs the browser to do something.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how code can start waiting for one or more browser events before sending a command that should trigger them. This avoids missing fast events.

**Data flow**: It receives event names and optionally a session ID. It returns a future, which is a placeholder for a result that will arrive later when one of those events is seen.

**Call relations**: Navigation helpers use this pattern before asking the browser to navigate, so they can later wait for the corresponding page event.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how to wait for an expected browser event with a time limit. The timeout prevents the automation from hanging forever if the browser never reports the event.

**Data flow**: It receives a future created by expect and a timeout in seconds. It waits until the event data arrives or the timeout is reached, then returns the event data if successful.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step rely on this behavior to pause until navigation-related events have happened.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how BrowserTabs gets the CDP connection used to talk to the browser. It hides where that connection is stored or how it is implemented.

**Data flow**: It takes no extra input beyond the session object. It returns an object that can send commands, expect events, and wait for them.

**Call relations**: BrowserTabs calls this whenever it needs browser I/O, such as attaching to a tab, creating a tab, navigating, or closing a tab.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how BrowserTabs can ask the download subsystem whether a navigation became a file download. This matters because a download may look like a failed page navigation even though it is a useful result.

**Data flow**: It takes no extra input beyond the session object. It returns the download reader object that can inspect recent browser downloads.

**Call relations**: BrowserTabs._goto uses this after a navigation error to decide whether to ignore the error because a download started.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how to run JavaScript inside a browser tab and get the result back. JavaScript is used here for simple page facts such as the current URL and document title.

**Data flow**: It receives a tab session ID and a JavaScript expression. The implementation runs that expression in the page and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info uses this interface to build tab summaries without needing a separate browser command for each page property.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser pages already existed when automation started. This lets the system tell the difference between old tabs and tabs created later.

**Data flow**: It receives a browser response containing target information. It reads the target IDs from that response, validates the shape of the data, converts IDs to strings, and stores them in tab_events.initial_targets.

**Call relations**: Later, BrowserTabs.on_target_created uses this remembered set to avoid treating already-existing pages as newly opened tabs.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports that a new page target was created. It queues that target so BrowserTabs.sync can attach to it later.

**Data flow**: It receives event parameters and an optional session ID. If the event describes a page with a string target ID, and that target was not present at startup and has not already been queued, it appends the ID to created_targets.

**Call relations**: This is an event callback. It does not attach immediately; instead, BrowserTabs.sync later consumes the queued target IDs and calls BrowserTabs.attach_tab.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser reports that a target was closed or destroyed. It records the target ID so the local tab list can be cleaned up.

**Data flow**: It receives event parameters and an optional session ID. If the parameters contain a string target ID, it adds that ID to destroyed_targets.

**Call relations**: BrowserTabs.sync later reads destroyed_targets and removes matching Tab objects from the session’s tab list.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when the browser says its top-level frame started loading. The top-level frame is the main page, not an embedded iframe.

**Data flow**: It receives event parameters and a session ID. If there is no session ID, it does nothing. If the frame ID belongs to the tab’s main frame, it tells the settle tracker that this session is loading.

**Call relations**: It uses BrowserTabs.is_top_level_frame to filter out child-frame noise. The settle tracker later helps navigation wait until the page is ready enough.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having loaded its document structure. This corresponds to the browser’s DOM content event, meaning the page’s main HTML has been parsed.

**Data flow**: It receives event parameters and a session ID. If there is a session ID, it tells the settle tracker that this session has reached the loaded stage.

**Call relations**: This event callback feeds BrowserTabs.navigate indirectly by updating the settle state that navigation waits on.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports important visual lifecycle events. “Painted” means the page has drawn something meaningful on screen.

**Data flow**: It receives event parameters and a session ID. It ignores events with no session ID and ignores lifecycle names that are not paint-related. For paint events on the top-level frame, it tells the settle tracker the session has painted.

**Call relations**: It uses BrowserTabs.is_top_level_frame to avoid treating iframe painting as full-page readiness. Its updates help BrowserTabs.navigate wait for a useful page state.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame ID is the main frame of a known tab. This prevents child frames, such as embedded ads or widgets, from being mistaken for the whole page.

**Data flow**: It receives a session ID and a frame ID. It compares them against the stored tabs and returns true only when a tab has the same session ID and its target ID matches the frame ID.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating the settle tracker, so only main-page events affect page readiness.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the automation system to an existing browser page target and prepares it for control. Without this step, the system may know a tab exists but cannot reliably inspect or drive it.

**Data flow**: It receives a target ID. It asks the browser to attach to that target, extracts the returned session ID, initializes browser event domains for that session, enables document fetch interception, applies the configured viewport size, and returns a new Tab record.

**Call relations**: BrowserTabs.sync calls this for tabs discovered through events, BrowserTabs.page calls it when creating the first blank tab, and BrowserTabs.create calls it for a newly created tab. It calls BrowserTabs.init_session and then constructs a Tab.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed for one tab session. These include page events, lifecycle events, DOM inspection, and network events.

**Data flow**: It receives a session ID. It sends several setup commands to the browser for that session and does not return a value.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target, before the tab is used for navigation or inspection.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Reconciles the local tab list with browser events that have arrived. It removes tabs that closed and attaches to newly created page targets.

**Data flow**: It reads destroyed_targets and created_targets from browser.tab_events. Destroyed IDs are cleared and matching local tabs are removed. Created IDs are processed in order; if a tab is not already known, it tries to attach to it and append it to the tab list. If attaching fails with a CDP error, it skips that target.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before using the tab list, so their view of open tabs is up to date. It calls BrowserTabs.attach_tab for new targets.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the Tab object to use for an operation. If no tabs are open, it creates a blank one so callers always have a page to work with.

**Data flow**: It receives an optional tab ID. First it syncs the tab list. If the list is empty, it asks the browser to create an about:blank target, attaches to it, and stores it. If no tab ID was requested, it returns the most recent tab. If a tab ID was requested, it validates the index and returns that tab or raises a validation error.

**Call relations**: BrowserTabs.navigate and BrowserTabs.close call this when they need a specific tab. It calls BrowserTabs.sync and may call BrowserTabs.attach_tab while creating the first tab.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new location, or moves it back or forward in its history. It also waits for the page to become reasonably ready before returning tab details.

**Data flow**: It receives a URL-like string and an optional tab ID. It gets the target tab, normalizes the destination, resets the settle tracker, then either performs a back/forward history step or goes to the destination URL. Afterward it waits for the settle tracker and returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create calls this after opening a new tab. Internally it calls normalize_url, BrowserTabs.page, BrowserTabs._history_step or BrowserTabs._goto, and finally BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level work of navigating one tab to a specific URL. It watches for page load events and handles the special case where navigation turns into a download.

**Data flow**: It receives a Tab and a URL. It records how many downloads existed before navigation, starts waiting for the DOM content event, sends the browser’s Page.navigate command, and checks the result. If the browser reports an error, it cancels the wait; if a new download appeared, it treats that as success, otherwise it raises an error. If navigation has a loader ID, it waits for the load event with a timeout.

**Call relations**: BrowserTabs.navigate calls this for ordinary URL navigation. It uses the browser connection’s expect, send, and wait behavior, and consults the download reader when navigation fails.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves one tab backward or forward through its browser history. If there is no entry in that direction, it safely does nothing.

**Data flow**: It receives a Tab and a step number, usually -1 for back or 1 for forward. It asks the browser for navigation history, validates the current index, calculates the target index, and returns early if that index is outside the history list. Otherwise it starts waiting for a navigation event, tells the browser to navigate to that history entry, and waits for confirmation.

**Call relations**: BrowserTabs.navigate calls this when the normalized target is “back” or “forward”. It validates browser response data using list and map helpers before sending the history navigation command.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Fetches a small human-readable summary of a tab: its current URL and page title. This is what callers need to report where a tab is and what it is showing.

**Data flow**: It receives a Tab. It runs a JavaScript expression in that tab that returns location.href and document.title, checks that the result is shaped like a map, and returns a dictionary with string URL and title values.

**Call relations**: BrowserTabs.navigate uses this after navigation, BrowserTabs.tabs_context uses it while building a list of tabs, and BrowserTabs.tab_titles uses it to collect titles.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to display or reason about the tab set. It marks the most recent tab as the current active one.

**Data flow**: It first syncs the tab list. It chooses the last tab as current when any tabs exist, then loops through each tab, fetches its URL and title, and returns a dictionary containing the current tab ID and a list of tab summaries.

**Call relations**: BrowserTabs.close calls this after removing a tab so it can return the updated tab state. It calls BrowserTabs.sync and BrowserTabs.tab_info.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of all known tabs. This is a compact view for code that only needs names, not full tab details.

**Data flow**: It loops over the current tab list, fetches each tab’s information, takes the title value, converts missing titles to an empty string, and returns the list of title strings.

**Call relations**: It calls BrowserTabs.tab_info for each tab. Unlike tabs_context, it does not sync first, so it works from the tab list already stored in the browser session.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and optionally navigates it to a requested URL. It returns the new tab’s ID along with its current URL and title.

**Data flow**: It receives a URL, defaulting to about:blank. It asks the browser to create a blank target, attaches to that target, adds the Tab to the local list, navigates that new tab to the requested URL, and returns the tab index plus the navigation result.

**Call relations**: It calls BrowserTabs.attach_tab to prepare the new target and BrowserTabs.navigate to load the requested destination. BrowserTabs.navigate then returns the tab information used in the final response.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab and returns the updated list of open tabs. It also clears out-of-process iframe session tracking because those sessions may no longer be valid after a tab closes.

**Data flow**: It receives an argument dictionary. It reads and converts the optional tab_id, gets the matching Tab, sends the browser a close-target command, removes that Tab from the local list, clears oop_sessions, and returns the refreshed tabs context.

**Call relations**: It calls _tab_id to interpret the input, BrowserTabs.page to find the tab, and BrowserTabs.tabs_context to report the state after closing.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a user-provided tab ID value into an integer index, or returns none when no usable tab ID was supplied.

**Data flow**: It receives a JSON-like value. Integers are returned as-is, floats are converted to integers, non-empty strings are parsed as integers, and all other values become None.

**Call relations**: BrowserTabs.close calls this before asking BrowserTabs.page for the tab to close. This keeps close accepting common input shapes without spreading conversion code elsewhere.

*Call graph*: called by 1 (close).

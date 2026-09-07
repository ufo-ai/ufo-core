# BUA CDP communication and runtime safety  `stage-12.3`

This stage is the browser automation “control room.” It is shared behind-the-scenes support used while the system is driving Chrome. Its job is to talk to Chrome safely, understand the answers, run small bits of page code, and prevent surprises from breaking the main automation flow.

The cdp.py file opens the main WebSocket connection, which is a two-way message pipe, to Chrome’s DevTools Protocol, the remote-control API for Chrome. It sends commands, waits for matching replies, and delivers browser events to the right waiting code. The wire.py file acts like a customs checkpoint for these messages. It describes the expected JSON shapes and checks incoming data before the rest of the system trusts it. The runtime.py file builds on that pipe to run JavaScript inside the current page and report page-side failures clearly. The dialogs.py file watches for web page pop-ups and accepts or dismisses them quickly so automation does not hang. The errors.py file preserves useful failure details, including what browser actions already happened, so recovery code can retry safely.

## Files in this stage

### CDP connection
The base transport opens and manages the Chrome DevTools Protocol WebSocket, sends commands, and routes replies and events.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser connection setup and active browser automation`

Chrome DevTools Protocol, or CDP, is how automation code tells Chrome things like “open this page” or “tell me when loading finishes.” This file wraps that protocol in a safer, easier shape. Without it, other parts of the browser automation system would have to manually send JSON messages over a WebSocket, match every reply to the original command, and avoid getting stuck forever if Chrome disconnects.

The main class, CdpConnection, is like a small switchboard operator. When a command is sent, it gives the command a unique number and stores a future, which is a placeholder for the answer that will arrive later. A background reader task continuously reads messages from Chrome. If a message is a reply, it finds the matching future and completes it. If the message is an event, such as a page lifecycle notification, it wakes any one-time waiters and also notifies registered listeners.

The file also protects callers from silent hangs. Commands and event waits have time limits. If the WebSocket closes, every pending command or event wait is failed loudly, so the rest of the system can recover instead of waiting forever.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: This builds a clear Python error when Chrome reports that a CDP command failed. It keeps the command name and Chrome's numeric error code so callers can understand which browser request went wrong.

**Data flow**: It receives the CDP method name, an error code, and an error message from Chrome. It stores the method and code on the error object, then creates a readable message such as “CDP Page.navigate failed...” for whoever catches the exception.

**Call relations**: CdpConnection._dispatch calls this when a response from Chrome contains an error instead of a normal result. The created exception is placed into the waiting command future, so the original CdpConnection.send caller receives a failure instead of a normal answer.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: This finds the actual DevTools WebSocket address to use. Callers may already have a WebSocket URL, or they may only have an HTTP address for Chrome's debugging endpoint; this function supports both.

**Data flow**: It takes a URL and HTTP headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise it asks the Chrome debugging HTTP endpoint for /json/version, reads the webSocketDebuggerUrl field from the JSON response, checks that it is a string, and returns that WebSocket URL.

**Call relations**: This function is a setup helper for code that needs to connect to Chrome but may not know the final WebSocket address yet. It uses httpx.AsyncClient to make the HTTP request and as_str from the wire helpers to validate the returned field before the connection-opening code uses it.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: This creates the in-memory state for one CDP WebSocket connection. It prepares the places where outgoing command replies, event listeners, one-time event waits, and the background reader task will be tracked.

**Data flow**: It receives an already-open WebSocket connection. It stores it, starts the command id counter at zero, and creates empty collections for pending command replies, event listeners, event waiters, and the reader task reference. It returns a ready CdpConnection object.

**Call relations**: CdpConnection.open creates the WebSocket and then calls this constructor. After construction, send, expect, wait, on, and the reader loop all use these stored collections to coordinate messages coming from Chrome.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: This opens a live CDP connection to Chrome and starts the background reader that listens for replies and events. It is the normal entry point for getting a usable CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to that URL with a large allowed message size, wraps the WebSocket in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the connection object.

**Call relations**: BrowserSession._bootstrap calls this during browser setup. Once open returns, other session code can call send to issue CDP commands while the reader task continuously feeds incoming messages into _dispatch.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: This shuts down the CDP connection cleanly. It stops the background reader task first, then closes the WebSocket to Chrome.

**Data flow**: It reads the stored reader task and WebSocket. If a reader task exists, it cancels it, waits for that cancellation to finish while ignoring the expected cancellation error, clears the task reference, and then closes the WebSocket. The outside effect is that no more messages are read or sent on this connection.

**Call relations**: This is used during teardown when the browser automation no longer needs the CDP link. It complements CdpConnection.open: open starts the reader and close stops it.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This sends one CDP command to Chrome and waits for that command's reply. It hides the protocol bookkeeping, so callers can ask for a browser action and simply await the result.

**Data flow**: It receives a method name, optional parameters, and optionally a session id for commands aimed at a specific browser target. It assigns a new numeric id, builds a JSON message, creates a future for the reply, stores that future under the id, sends the JSON text over the WebSocket, and waits up to the command timeout. If Chrome replies, the matching result comes back. If the timer expires, the pending entry is removed and a timeout error is raised.

**Call relations**: Other browser automation code calls this whenever it needs Chrome to do something through CDP. The response does not come back directly through the send call; instead, _read_loop receives all incoming WebSocket messages and _dispatch matches the reply id back to this send call's stored future.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: This registers a listener for repeated CDP events. It is useful when code wants to be notified every time Chrome emits a certain event, not just once.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for the event name. Later, when matching event messages arrive, the listener will be called with the event parameters and optional session id.

**Call relations**: This feeds the event fan-out side of the connection. _dispatch looks up listeners registered through on whenever it sees an incoming event and calls each listener for that event.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This creates a one-time wait for one or more CDP events. It is useful when code is about to do something and wants to wait until Chrome reports that a specific thing happened.

**Data flow**: It receives one or more event names and optionally a session id to limit the wait to one browser target. It creates a future, stores it with the event names and session filter, and returns the future. Nothing is awaited here yet; the future will be completed later when a matching event arrives.

**Call relations**: Callers usually call expect before triggering an action, then pass the returned future to CdpConnection.wait. _dispatch is the part that later checks each incoming event against these stored waiters and completes the matching future.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: This waits for a previously created event future, with a time limit and cleanup. It makes sure one-time event waits do not stay registered forever after they finish or time out.

**Data flow**: It receives a future from expect and an optional timeout. It waits for the future to complete within that time and returns the event parameters if it succeeds. Whether it succeeds, fails, or times out, it removes that future from the stored waiter list afterward.

**Call relations**: This is the second half of the expect-and-wait pattern. expect registers interest in an event; _dispatch completes the future when the event arrives; wait returns that event data to the caller and then cleans up the waiter record.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: This is the background reader for the WebSocket. It continuously receives raw messages from Chrome and hands each decoded message to the dispatcher.

**Data flow**: It reads JSON text messages from the WebSocket. For each message, it parses the text into Python data and passes it to _dispatch. If the WebSocket closes, it stops reading. In all shutdown cases, it fails every still-pending command and event wait so callers are not left hanging.

**Call relations**: CdpConnection.open starts this as an asynchronous task. It is the only place that reads incoming WebSocket messages, and it delegates the meaning of each message to CdpConnection._dispatch. Its cleanup path protects all users of send, expect, and wait from waiting forever after the connection is gone.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: This sorts each incoming CDP message into either a command reply or an event notification. It then sends the information to the right waiting command, one-time waiter, or event listener.

**Data flow**: It receives one decoded JSON message from Chrome. If the message has a numeric id, it treats it as a reply, finds the matching pending command, and completes that command's future with either a result or a CdpError. If the message has an event method name instead, it extracts the event parameters and optional session id, completes any matching one-time waiters, removes completed waiters, and calls all registered listeners for that event.

**Call relations**: CdpConnection._read_loop calls this for every message received from Chrome. It is the central switchboard for the connection: replies flow back to CdpConnection.send callers, matching events flow to futures created by CdpConnection.expect and awaited by CdpConnection.wait, and repeated events flow to listeners registered with CdpConnection.on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### Dialog and failure safety
Automation remains safe around disruptive page dialogs and preserves browser-side progress when failures occur.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `request handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, and “are you sure you want to leave?” warnings. These dialogs are like someone standing in a doorway: until they are answered, the page stops and later browser events can be blocked. This file gives the browser automation a simple rule for clearing that doorway immediately.

The main piece is BrowserDialogs. When a dialog appears, it looks at the dialog type and message. Alerts and before-unload warnings are automatically accepted because there is usually no meaningful alternative for the automation. Confirmation and prompt dialogs are dismissed instead, so the agent does not accidentally agree to a website’s own safety question, such as “Are you sure you want to delete this?” In all cases, the dialog is written into the browser session’s dialog log so the rest of the system can know the site asked something.

The actual click on the dialog is sent through CDP, the Chrome DevTools Protocol, which is the control channel used to talk to the browser. That answer is launched in the background so the event handler can return quickly. If the browser cannot answer the dialog because of a connection problem, timeout, or similar failure, the file logs a warning instead of crashing the whole run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of an object that can send a command to the browser over CDP, the Chrome DevTools Protocol. BrowserDialogs relies on this ability to tell the browser to accept or dismiss a JavaScript dialog.

**Data flow**: It receives a browser command name, optional command details, and optionally the browser session to target. It sends that request to the browser and returns the browser’s JSON-style response.

**Call relations**: BrowserDialogs gets this sender from BrowserDialogSession.connection when it needs to answer a dialog. The concrete implementation lives elsewhere; this file only states what ability is required.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This describes how a browser session provides access to its browser control connection. BrowserDialogs uses it when it needs to send the “handle this dialog” command.

**Data flow**: It takes the current session object and returns an object that knows how to send CDP commands. It does not itself decide what command to send.

**Call relations**: BrowserDialogs._answer_dialog calls this while clearing a pop-up. The returned connection is then used to send the actual command to the browser.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This describes how a browser session can start a small asynchronous job in the background. BrowserDialogs uses it so dialog answering begins immediately without making the event handler wait.

**Data flow**: It receives an asynchronous task, starts it separately, and returns nothing. The visible change is that the task continues running after the caller moves on.

**Call relations**: BrowserDialogs.on_dialog uses this to launch BrowserDialogs._answer_dialog. This keeps dialog cleanup quick, which matters because an open JavaScript dialog can block later browser events.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is called when the browser reports that a web page opened a JavaScript dialog. It decides whether to accept or dismiss the dialog, records that decision for later visibility, and starts the browser command that clears the dialog.

**Data flow**: It receives the dialog details from the browser and an optional session id. It reads the dialog type and message, chooses accept for alerts and before-unload warnings or dismiss for other dialog types, appends a plain text note to the session’s dialog list, then starts a background task to send the answer to the browser.

**Call relations**: This is the front door for dialog events in this file. After making the policy decision, it hands off to BrowserDialogs._answer_dialog to perform the actual CDP command, using the session’s background-task mechanism so the page is unblocked quickly.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This sends the actual browser command that accepts or dismisses the currently open JavaScript dialog. It is separated from on_dialog because the command is asynchronous and may fail independently.

**Data flow**: It receives the target session id and the already chosen answer, true for accept or false for dismiss. It asks the session for its browser connection, sends the Page.handleJavaScriptDialog command with that answer, and returns nothing. If the command fails because of a CDP error, timeout, or runtime problem, it writes a warning to the log instead of raising the error onward.

**Call relations**: BrowserDialogs.on_dialog starts this in the background after recording the dialog. This function is the final handoff to the browser control layer, where the policy decision becomes a real browser action.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `cross-cutting error paths during browser actions`

Browser automation is not like editing a draft in memory: once a click lands, text is typed, or a tab is opened, the real browser has already changed. This file gives the rest of the system clearer ways to report those half-finished situations.

It defines errors that carry practical recovery information. `HallucinationError` is used when a model asks for something impossible, such as a made-up page element reference. `BatchInterrupted` is used when a group of browser actions stops part-way through. Instead of pretending nothing happened, it records which actions were already applied and which original action failed. That matters because retrying the whole group could click buttons or submit forms twice. `TabLeftOpen` is used when a new tab was successfully created, but navigating it to the requested web address failed. The tab still exists, so the caller needs its tab number in order to reuse it or close it.

A useful analogy is a delivery route: if a driver completes three deliveries and then the fourth fails, you should not resend all four packages. These exceptions are the receipt that says what already happened and where the trouble started.

#### Function details

##### `BatchInterrupted.__init__`  (lines 21–32)

```
def __init__(self, applied: tuple[tuple[int, str], ...], origin: int, total: int, cause: Exception) -> None
```

**Purpose**: Builds an error for a batch of browser actions that failed after some actions had already reached the page. It gives the caller enough detail to avoid repeating successful actions during recovery.

**Data flow**: It receives the list of already applied actions, the caller’s index of the failed action, the total number of actions in the caller’s batch, and the original exception that caused the stop. It turns those into a clear error message, then stores each piece of information on the exception object. The result is an exception that says both what failed and what had already changed in the browser.

**Call relations**: When `extensions/browser/ufo_ext_browser/bua/computer.BrowserComputer.run` is running a batch and one action fails after earlier actions were sent, it creates this exception. The exception carries the partial-progress record back to higher-level code so that recovery can continue from the right point instead of replaying the whole batch.

*Call graph*: called by 1 (run).


##### `TabLeftOpen.__init__`  (lines 42–46)

```
def __init__(self, tab_id: int, url: str, cause: Exception) -> None
```

**Purpose**: Builds an error for the case where a browser tab was opened, but loading the requested address failed. It preserves the tab’s identity so the caller can clean it up or try navigation again.

**Data flow**: It receives the tab number, the web address that failed to load, and the original cause of the failure. It creates a readable error message and stores the tab number, URL, and cause on the exception object. The output is an exception that makes clear that the tab exists even though navigation failed.

**Call relations**: When `extensions/browser/ufo_ext_browser/bua/tabs.BrowserTabs.create` creates a tab but cannot get it to the requested URL, it raises this exception. That lets the caller know not to blindly open another tab on retry, because the first one is still sitting in the browser.

*Call graph*: called by 1 (create).


### Runtime calls and wire validation
JavaScript execution and typed CDP message validation turn low-level browser data into safer Python-facing operations.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `browser automation request handling`

Browser automation often needs to ask the page a question, such as “what is this value?” or “run this function on that page object.” Browsers expose this through the Chrome DevTools Protocol, often shortened to CDP, which is a message-based control channel for inspecting and driving a browser. This file wraps the CDP Runtime commands so the rest of the code does not need to build those messages by hand each time.

There are two small protocol types here. A protocol in Python is like a promise about what methods an object must provide. BrowserRuntimeCdp describes something that can send a CDP command. BrowserRuntimeSession describes something that can provide that connection.

BrowserRuntime is the useful wrapper. Its eval method sends a JavaScript expression to the browser and returns the expression’s value. Its call_on method runs a JavaScript function against an existing browser-side object. Both ask CDP to return plain JSON values, which are easy for Python to use. After each browser call, they check whether the page threw an exception. If it did, raise_on_exception turns that hidden browser failure into a normal Python RuntimeError. Without this file, callers would have to repeat fragile CDP message shapes and might accidentally ignore JavaScript errors.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This defines the shape of an object that can send a command to the browser over the Chrome DevTools Protocol. It is not an implementation; it is a contract that other connection classes must satisfy.

**Data flow**: It receives a command name, optional command parameters, and an optional browser session id. An implementing object sends that request to the browser and returns the browser’s JSON-style dictionary response.

**Call relations**: BrowserRuntime does its real browser work through an object with this method. When eval or call_on needs to talk to the browser, it obtains a connection and relies on send to carry the prepared CDP command.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This defines the shape of an object that can provide access to a browser CDP connection. It lets BrowserRuntime stay independent from the exact session or connection implementation.

**Data flow**: It takes the session-like object itself and returns an object that knows how to send CDP commands. It does not transform browser data directly; it gives other code the communication channel to do that.

**Call relations**: BrowserRuntime is built around a BrowserRuntimeSession. Before eval or call_on can send a Runtime command, they ask this session for its connection and then use that connection to send the browser message.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the resulting value. Someone would use it when Python needs to quickly ask the page to compute or reveal something.

**Data flow**: It receives a browser session id and a JavaScript expression as text. It packages them as a Runtime.evaluate CDP request, asks the browser connection to run it, checks the response for a JavaScript exception, then extracts and returns the plain value from the response.

**Call relations**: This is one of the main public actions of BrowserRuntime. It calls raise_on_exception immediately after the browser replies so failures in the page do not look like successful Python results. It also uses as_map to treat nested response pieces as dictionaries before reading the returned value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function against an existing object inside the browser and returns the function’s result as a dictionary. It is useful when code already has a browser-side object id and wants to inspect or transform that object without first converting the whole object into Python.

**Data flow**: It receives a session id, a browser object id, JavaScript function text, and optional argument values. It builds a Runtime.callFunctionOn CDP request, converts each Python argument into the browser’s expected argument format, sends the request, checks for page-side exceptions, and returns the result value as a dictionary.

**Call relations**: This is the sibling to eval for object-based work. Like eval, it delegates failure detection to raise_on_exception and uses as_map to safely read the nested CDP response. It hands the finished command to the browser connection rather than knowing anything about the connection internals.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime response for an uncaught JavaScript exception and turns it into a Python RuntimeError. It exists so callers do not accidentally continue after JavaScript in the page has failed.

**Data flow**: It receives the response dictionary from the browser. If there is no exception information, it returns without changing anything. If exception details are present, it chooses the best available message from the browser response and raises an error containing that message.

**Call relations**: Both eval and call_on call this right after receiving a browser response. It is the shared safety gate between raw CDP replies and the rest of the automation code, making page-side failures visible to Python callers.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `request handling and browser protocol parsing`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often shortened to CDP. In plain terms, CDP is a JSON-based control channel: the engine sends JSON messages to the browser and receives JSON messages back. This file is the small safety gate at that boundary.

It first defines what “JSON” means for this code: values can be null, booleans, numbers, strings, lists, or dictionaries with string keys. Then it provides a custom ValidationError for cases where Chrome, or another CDP provider, sends data in a shape the engine cannot use.

The helper functions are simple “narrowers.” They take a loose JSON value and confirm it is the specific kind of value the next part of the engine expects: an object, a non-empty string, an integer, or a list. If the value is missing where missing is harmless, some helpers return an empty object or empty list. If the value is the wrong kind, they raise ValidationError with a message that points to the bad field.

A useful analogy is a mailroom checker: packages arrive from outside, and this file confirms each package is the expected size and label before it is sent deeper into the building.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary-like collection of named fields. It is used when the engine expects structured data from Chrome and wants to avoid treating the wrong kind of value as a field map.

**Data flow**: It receives a JSON value, which may also be missing, plus a text path that names where the value came from. If the value is missing, it returns an empty dictionary. If the value is already a dictionary, it returns it unchanged. If the value is anything else, it raises ValidationError explaining that the named path must be an object.

**Call relations**: When other browser-engine code reads a CDP message and expects a nested object, it calls on as_map as the boundary check. If the check fails, as_map creates a ValidationError so the problem can be reported as a recoverable bad wire value instead of causing later code to fail in a less clear way.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It protects code that needs a real piece of text, such as an identifier or name, from accidentally accepting missing, blank, or wrongly typed data.

**Data flow**: It receives a JSON value, which may also be missing, and a text path describing the field being checked. If the value is a string and it is not empty, the function returns that string. For missing values, empty strings, or any non-string value, it raises ValidationError saying the path must be a non-empty string.

**Call relations**: Other code uses as_str when reading text fields from Chrome’s JSON replies. If Chrome’s message does not contain usable text, as_str stops the flow immediately and raises ValidationError, giving the caller a clear reason to treat the protocol response as invalid.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, meaning a whole number. It is used where the browser engine needs a count, index, ID, or other numeric value that must not be a string, decimal, object, or list.

**Data flow**: It takes a JSON value, possibly missing, and a path that describes where that value was found. If the value is an integer, it returns the number unchanged. If the value is missing or is any other kind of JSON value, it raises ValidationError saying the named path must be an integer.

**Call relations**: When code parsing CDP data expects a whole number, it routes the raw value through as_int first. On failure, as_int raises ValidationError at the protocol boundary, so later logic can rely on receiving an actual integer.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It is useful when Chrome returns a collection of items and the engine wants a predictable Python list before looping over it.

**Data flow**: It receives a JSON value, which may be missing, and a path naming the field being checked. If the value is already a list, it returns that list unchanged. If the value is missing, it returns an empty list, treating absence as “no items.” If the value is anything else, it raises ValidationError saying the path must be a list.

**Call relations**: Other browser-engine code calls as_list before processing arrays from CDP messages. If there is no list to process, as_list either gives back an empty list for a missing field or raises ValidationError for a wrongly shaped field, keeping the rest of the parsing code simple and safe.

*Call graph*: 1 external calls (__init__).

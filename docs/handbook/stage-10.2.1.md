# CDP transport and runtime safety  `stage-10.2.1`

This stage is shared behind-the-scenes support for talking to Chrome safely. It is the project’s “control cable” to the browser, used whenever higher-level code needs to inspect a page, click, type, open tabs, or run small scripts.

The cdp.py file manages the Chrome DevTools Protocol connection. This protocol is Chrome’s remote-control channel. The file opens one WebSocket, which is a two-way network pipe, sends commands through it, matches replies to the right requests, and routes browser events to any code that is listening.

The wire.py file checks the raw JSON messages that travel over that pipe. JSON is loose text data, so this file verifies the expected shapes before the rest of the system trusts them.

The runtime.py file uses the same protocol to run JavaScript inside the current page. It wraps the low-level messages in simple Python calls and reports page-side failures clearly.

The errors.py file defines careful error types for risky browser actions, where a retry might repeat a real click or typed text.

## Files in this stage

### CDP WebSocket transport
The transport layer opens the DevTools WebSocket, sends commands, waits for replies, and routes browser events to listeners.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser connection and request/event handling`

Chrome’s DevTools Protocol, often shortened to CDP, is like a remote control socket for Chrome. Through it, this project can ask the browser to do things, such as inspect pages or listen for page events, and Chrome answers back with JSON messages. This file wraps that raw message stream into something safer and easier to use.

The main class, CdpConnection, keeps track of three kinds of traffic. First, when the code sends a command, it gives that command a number and stores a future, which is a promise that a reply will arrive later. When Chrome replies with the same number, the waiting code gets its result. Second, some code may want to wait for a specific one-time browser event, such as “page loaded”; expect and wait support that pattern. Third, code can register ongoing listeners for events, and every matching event is passed to those listeners.

A background reader task continuously reads messages from the WebSocket. It decides whether each message is a command response or an event, then routes it to the right place. If the connection closes, it does not leave callers waiting forever. It fails all pending waits loudly, which makes problems visible instead of causing hidden hangs.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: This builds a clear Python error for a failed Chrome DevTools Protocol command. It keeps the command name and Chrome’s numeric error code so the caller can see which browser request failed and why.

**Data flow**: It receives the command method name, an error code, and Chrome’s error message. It stores the method and code on the error object, then creates a readable message such as “CDP Some.method failed...” that can be raised to the caller.

**Call relations**: CdpConnection._dispatch creates this error when Chrome sends a response that contains an error instead of a normal result. The error is placed into the waiting command future, so the original sender of the command receives a failure rather than a misleading empty result.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: This finds the real DevTools WebSocket address to use. If the caller already has a WebSocket URL, it returns it directly; otherwise it asks Chrome’s HTTP debugging endpoint where the WebSocket is.

**Data flow**: It takes a URL and request headers. If the URL starts with ws:// or wss://, it is already ready to use and comes back unchanged. Otherwise, the function calls the URL’s /json/version endpoint, reads the webSocketDebuggerUrl field from the JSON response, checks it is a string, and returns that value.

**Call relations**: This is a setup helper used before opening the WebSocket connection. It relies on httpx to make the HTTP request and on as_str to safely pull a string field out of the response instead of trusting the data blindly.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: This prepares a new connection object around an already-open WebSocket. It sets up the bookkeeping needed to match outgoing commands with incoming replies and to track event listeners and waiters.

**Data flow**: It receives a WebSocket connection. It stores that socket, starts the command ID counter at zero, and creates empty collections for pending command replies, ongoing event listeners, one-time event waiters, and the background reader task reference.

**Call relations**: CdpConnection.open calls this after the WebSocket is connected. The rest of the class methods then use these stored collections as the shared state for sending commands, reading responses, and routing events.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: This creates a live CdpConnection from a DevTools WebSocket URL. It opens the socket and starts the background reader that listens for Chrome’s replies and events.

**Data flow**: It receives the WebSocket URL and optional HTTP headers. It connects to Chrome with a large message-size limit, wraps the socket in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this when setting up browser control. After this method returns, other code can call send, expect, wait, and on while the background _read_loop quietly receives and dispatches messages.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: This shuts down the DevTools connection cleanly. It stops the background reader task and closes the WebSocket so no more browser messages are sent or received.

**Data flow**: It checks whether a reader task is running. If so, it cancels that task and waits for the cancellation to finish, ignoring the normal cancellation signal. Then it closes the WebSocket itself.

**Call relations**: This is used during teardown when the browser-control channel is no longer needed. It works with _read_loop’s cleanup behavior: once the reader stops, pending command and event waits are failed instead of being left stuck.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This sends one command to Chrome and waits for Chrome’s matching reply. It is the main way the rest of the project asks the browser to do something through CDP.

**Data flow**: It receives a CDP method name, optional parameters, and an optional session ID for commands aimed at a specific attached target. It assigns a new numeric ID, stores a future under that ID, sends the JSON message over the WebSocket, and waits up to 30 seconds for _dispatch to fill in the future. On success it returns Chrome’s result object; on timeout it removes the pending wait and raises a timeout error.

**Call relations**: Callers use this whenever they need a command-response exchange with Chrome. The response is not read here directly; _read_loop receives all incoming WebSocket messages and _dispatch matches the reply by ID back to the future created here.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: This registers a callback for an ongoing browser event. It is useful when code wants to be told every time a certain CDP event happens, not just once.

**Data flow**: It receives an event name and a listener function. It adds the listener to the list for that event name, creating the list if this is the first listener for that event. It returns nothing, but it changes the connection’s listener registry.

**Call relations**: Later, when _read_loop receives an event message and _dispatch recognizes its method name, _dispatch calls every listener registered here for that event. This is the fan-out path for continuous event notifications.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This creates a one-time wait for one of several browser events. It lets code say, “I expect this event soon; give me a future I can wait on.”

**Data flow**: It receives one or more event names and an optional session ID. It creates a future tied to the current async event loop, records the desired events, session filter, and future in the waiter list, and returns the future to the caller.

**Call relations**: Code typically calls expect before doing something that should trigger an event, then passes the returned future to wait. _dispatch later completes that future when a matching event arrives.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: This waits for a future created by expect, with a time limit. It keeps event waits from hanging forever if Chrome never sends the expected event.

**Data flow**: It receives an event future and an optional timeout, defaulting to 30 seconds. It waits until the future has a result or the timeout expires. Whether it succeeds, times out, or is cancelled, it removes that future from the internal waiter list so stale waits do not build up.

**Call relations**: This is the partner to expect. expect registers the desired event, _dispatch may complete the future when the event arrives, and wait gives the caller the event parameters or raises if the wait does not finish in time.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: This is the background receiver for the WebSocket connection. It continuously reads raw JSON messages from Chrome and hands each decoded message to the dispatcher.

**Data flow**: It reads each raw WebSocket message, parses it from JSON text into a Python dictionary-like object, and sends it to _dispatch. If the WebSocket closes, it stops reading. In all exit cases, it fails any still-pending command futures and event waiters, then clears those lists.

**Call relations**: CdpConnection.open starts this as a background task. It is the bridge between the network socket and the higher-level send, expect, wait, and on features; _dispatch does the actual routing for each message it reads.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: This routes one decoded CDP message to the right waiting code. It decides whether the message is a reply to a command or a browser event, then completes futures or calls listeners as needed.

**Data flow**: It receives a parsed JSON message. If the message has a numeric ID, it treats it as a command response, finds the matching pending command, and either gives that command its result or raises a CdpError if Chrome reported an error. If the message has an event method name instead, it extracts the event parameters and optional session ID, completes any matching one-time waiters, removes completed waiters, and calls all registered listeners for that event.

**Call relations**: _read_loop calls this for every incoming message. It completes futures created by send and expect, creates CdpError when command responses fail, and invokes listener functions registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### Side-effect error model
Automation-specific errors describe failures where browser state may already have changed, helping callers avoid unsafe retries.

### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `cross-cutting error handling during browser action execution`

Browser automation is not like a dry calculation: once an action reaches the page, it may have real effects. A click may submit a form, text may be typed, or a new tab may be opened. This file gives the rest of the browser layer clearer error messages for those “partly done” situations.

It defines a few custom exceptions, which are error objects with extra information attached. `HallucinationError` means the model asked for something impossible, such as a made-up page element reference. `BatchInterrupted` is used when a group of browser actions fails halfway through. Instead of only saying “it failed,” it records which earlier actions were already applied and which original action failed. That matters because retrying the whole group could repeat the successful actions.

`TabLeftOpen` covers another subtle case: creating a tab succeeded, but loading the requested web address failed. The tab still exists, so the caller needs its tab number in order to reuse it or close it. In short, this file turns vague failures into practical recovery instructions.

#### Function details

##### `BatchInterrupted.__init__`  (lines 21–32)

```
def __init__(self, applied: tuple[tuple[int, str], ...], origin: int, total: int, cause: Exception) -> None
```

**Purpose**: This builds an error for a browser action batch that failed after some actions had already affected the page. It preserves enough detail for the caller to avoid retrying actions that already happened.

**Data flow**: It receives the actions that were already applied, the caller’s index of the action that failed, the total number of actions in the caller’s batch, and the original error that caused the stop. It turns that into a clear human-readable error message, then stores all four pieces of information on the exception object. The result is an exception that says both what failed and what already changed.

**Call relations**: This is created by `extensions/browser/ufo_ext_browser/bua/computer.BrowserComputer.run` when it is running a batch of browser actions and one fails partway through. The error carries the partial progress back upward so the caller can decide what to do next without accidentally replaying actions that already reached the browser.

*Call graph*: called by 1 (run).


##### `TabLeftOpen.__init__`  (lines 42–46)

```
def __init__(self, tab_id: int, url: str, cause: Exception) -> None
```

**Purpose**: This builds an error for the case where a new browser tab was created, but navigation to its intended URL failed. It tells the caller that the tab still exists and gives the tab identifier needed to clean it up or try again.

**Data flow**: It receives the tab’s identifier, the URL it was supposed to load, and the original error. It creates a message explaining that the tab opened but did not reach the URL, then stores the tab identifier, URL, and cause on the exception object. The output is an exception that includes both the failure and the leftover browser state.

**Call relations**: This is created by `extensions/browser/ufo_ext_browser/bua/tabs.BrowserTabs.create` after tab creation succeeds but loading the requested page fails. It hands the tab information back to the caller so the caller does not unknowingly leave an extra tab open or create duplicates on retry.

*Call graph*: called by 1 (create).


### Page runtime wire safety
Runtime helpers execute JavaScript in the page while wire validators turn untrusted CDP JSON into checked Python values.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `browser automation request handling`

Browser automation often needs to ask the page a question, such as “what is this value?” or “run this small function on that page object.” The browser exposes this through the Chrome DevTools Protocol, or CDP, which is a message-based control channel for talking to the browser. This file wraps two CDP Runtime commands so the rest of the code does not have to build those messages by hand each time.

The file defines two small protocol shapes: one thing that can send CDP messages, and one thing that can provide that sender. A protocol here means “anything with these methods will work,” like saying any plug that fits this socket can be used.

The main class, BrowserRuntime, is the helper people actually use. Its eval method runs a JavaScript expression in a chosen browser session and returns the plain value. Its call_on method runs a JavaScript function against an existing browser-side object and returns the function’s value as a dictionary. Both methods ask CDP to return values directly, then check whether the browser reported an uncaught exception. If the page code threw an error, raise_on_exception turns that browser error into a Python RuntimeError. Without this file, callers would repeat fragile CDP message formatting and might miss page-side failures.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This describes the kind of object that can send a command to the browser over Chrome DevTools Protocol. Code uses this promise so BrowserRuntime can work with any connection object that knows how to send a method name, optional data, and an optional browser session id.

**Data flow**: It receives a CDP method name, optional parameters, and optionally the id of the browser target session to talk to. The real implementation sends that message to the browser and returns the browser’s JSON-like dictionary reply.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on an object with this method after getting it from BrowserRuntimeSession.connection. This file only defines the expected shape; another part of the system supplies the actual network or protocol connection.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This describes the kind of object that can provide a CDP connection for a browser session. BrowserRuntime uses it as the doorway to reach the browser.

**Data flow**: It takes no input beyond the session-like object itself. The real implementation returns an object that can send CDP commands and receive CDP replies.

**Call relations**: BrowserRuntime calls this before sending Runtime.evaluate or Runtime.callFunctionOn. Like BrowserRuntimeCdp.send, this is a protocol contract rather than the working connection itself.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and gives back the expression’s value. It is useful when automation code needs to read something from the page without manually speaking CDP.

**Data flow**: It takes a browser session id and a JavaScript expression string. It builds a Runtime.evaluate message asking the browser to return the result as a normal value, sends it through the browser connection, checks the reply for a page-side exception, then extracts and returns the value from the reply.

**Call relations**: A caller uses this when it wants to evaluate JavaScript in the page. The method hands the low-level message to BrowserRuntimeCdp.send through BrowserRuntimeSession.connection, then passes the browser reply to BrowserRuntime.raise_on_exception before using as_map to safely read the nested result data.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function against an existing object inside the browser and returns the function’s result as a dictionary. It is used when the code already has a browser object id and wants to ask that object to do or reveal something.

**Data flow**: It takes a session id, a browser-side object id, the JavaScript function text, and optional argument values. It turns the arguments into the shape CDP expects, sends a Runtime.callFunctionOn message, checks whether the browser reported an exception, then extracts the returned value and makes sure it is a dictionary before returning it.

**Call relations**: A caller uses this after it has a target object in the browser. This method sends the prepared command through the CDP connection, then uses BrowserRuntime.raise_on_exception to stop on page errors and as_map to read the nested CDP response safely.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime command reply and turns a JavaScript exception into a normal Python error. It keeps callers from accidentally treating a failed page evaluation as a successful empty result.

**Data flow**: It receives the JSON-like dictionary returned by the browser. If there is no exception information, it does nothing. If exception details are present, it pulls out the best available message and raises a RuntimeError that includes that browser-side failure description.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser reply. It is the shared safety check that makes both higher-level helpers fail loudly and consistently when JavaScript in the page throws an error.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during Chrome protocol message parsing`

The browser engine talks to Chrome through the Chrome DevTools Protocol, which is a JSON-based message format. JSON is flexible: a field might be missing, be a string, be a number, or be something completely unexpected. This file acts like a checkpoint at the edge of the system. Before the rest of the browser engine trusts a value, these helpers check that it has the shape the engine expects.

It defines a recursive Json type, meaning a JSON value can be empty, a boolean, a number, a string, a list of more JSON values, or a dictionary whose values are also JSON. It also defines ValidationError, used when a value from the wire cannot safely be treated as the needed kind of data.

The helper functions are narrowers: they take a loose JSON value and turn it into a more specific Python value, such as a dictionary, string, integer, or list. If the value is missing in places where an empty object or empty list is acceptable, they return an empty one. If the value has the wrong shape, they raise ValidationError with a message naming the bad field. This keeps bad protocol data from leaking deeper into the engine, where it would be harder to understand and recover from.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value can be treated as an object, which in Python is a dictionary. It is used when the browser engine expects a group of named fields, and it allows a missing value to mean an empty object.

**Data flow**: It receives a JSON value and a text path that says where that value came from. If the value is missing, it returns an empty dictionary; if it is already a dictionary, it returns it unchanged. If it is anything else, it creates and raises a ValidationError explaining that the named path must be an object.

**Call relations**: When code reading Chrome protocol data needs an object-shaped value, this helper is the boundary check. Its only handoff on failure is to ValidationError, which turns the bad wire shape into a clear, recoverable error instead of letting the wrong kind of value continue through the engine.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It is useful for required text fields such as identifiers, names, or protocol values where an empty string would not be meaningful.

**Data flow**: It receives a JSON value and a path describing the field being checked. If the value is a string and it is not empty, that string comes out. For a missing value, an empty string, or any non-string value, it raises a ValidationError naming the field and saying it must be a non-empty string.

**Call relations**: Code that reads text from Chrome protocol messages can call this before using the text. If the check fails, the function hands control to ValidationError so the problem is reported at the protocol boundary rather than surfacing later as a mysterious failure.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, meaning a whole number. It is used when the protocol data must represent counts, positions, IDs, or other whole-number fields.

**Data flow**: It takes a JSON value and a path label. If the value is an integer, it returns that integer. If the value is missing or has any other shape, it raises a ValidationError that says the named path must be an integer.

**Call relations**: When the browser engine needs a whole number from a Chrome message, this helper performs the safety check first. On failure, it constructs a ValidationError, making the bad input explicit instead of allowing non-number data to be used as if it were valid.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value can be treated as a list. It is used for protocol fields that contain several items, and it treats a missing list as an empty list when that is safe.

**Data flow**: It receives a JSON value and a path describing where it came from. If the value is a list, it returns that list unchanged; if it is missing, it returns an empty list. If it is any other kind of value, it raises a ValidationError saying the named path must be a list.

**Call relations**: Code that reads array-like fields from Chrome protocol messages can call this to get a predictable list before continuing. If the incoming data is not list-shaped, the function reports the issue through ValidationError so the rest of the engine does not have to guess what happened.

*Call graph*: 1 external calls (__init__).

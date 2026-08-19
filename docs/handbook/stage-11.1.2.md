# Chrome DevTools Protocol transport and runtime primitives  `stage-11.1.2`

This stage is shared behind-the-scenes support for browser automation. It is the system’s “phone line” to Chrome. Higher-level code asks Chrome to open pages, inspect them, or run scripts, but this stage provides the low-level tools that make those requests safe and reliable.

The cdp.py file manages the actual connection to Chrome’s DevTools Protocol, which is Chrome’s automation control channel. It opens a WebSocket, a two-way network pipe, sends numbered commands, waits for matching replies, and forwards browser events to any code that registered interest.

The runtime.py file builds on that connection to run JavaScript inside a browser tab. It hides the raw protocol details and turns JavaScript errors from the page into normal Python exceptions, so callers can handle failures in the usual way.

The wire.py file acts like a border checkpoint. It checks that incoming JSON messages have the expected shape before the rest of the engine trusts them. Together, these files form the safe communication layer between Python and Chrome.

## Files in this stage

### DevTools transport
Low-level WebSocket communication with Chrome DevTools Protocol, including command dispatch and event routing.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser session startup through active browser automation, with cleanup at shutdown`

Chrome can be driven through the Chrome DevTools Protocol, often called CDP. CDP is like a remote control for the browser: the program sends a command such as “open this page” or “click here,” and Chrome sends back either a reply or later events such as “a page loaded.” This file provides the transport layer for that remote control.

The main class, CdpConnection, owns one WebSocket, which is a two-way network pipe that stays open. When code sends a command, the connection gives it a unique number, remembers which future is waiting for the answer, and writes the JSON message to Chrome. A future is a placeholder for a value that will arrive later. In the background, a reader task continuously receives messages from Chrome. If a message is a reply, it finds the matching waiting command by number and completes it. If a message is an event, it wakes any one-time waiters and calls any registered listeners.

The file is careful about failure. Commands and event waits have time limits, so the program does not hang forever. If the WebSocket closes, every pending command and event waiter is failed loudly. Without this file, higher-level browser session code would have no reliable way to talk to Chrome or know when browser events happen.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: This builds a clear exception for a command that Chrome rejected. It records which CDP command failed, Chrome's numeric error code, and the error message so callers can see what went wrong.

**Data flow**: It receives the command name, an error code, and an error message from a failed Chrome reply. It stores the command name and code on the error object, then creates a human-readable message. The result is an exception object that can be placed into a waiting command future.

**Call relations**: When CdpConnection._dispatch reads a Chrome reply that contains an error, it creates this CdpError and uses it to fail the command that was waiting for that reply. That lets the original sender receive a normal Python exception instead of a raw protocol message.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: This turns a browser debugging address into the exact WebSocket URL needed to speak CDP. If the caller already gives a WebSocket URL, it simply returns it.

**Data flow**: It takes a URL and optional HTTP headers. If the URL already starts with ws:// or wss://, it comes out unchanged. Otherwise, it asks the browser's /json/version endpoint for connection details, reads the webSocketDebuggerUrl field from the JSON response, checks it is a string, and returns that string.

**Call relations**: This is a setup helper used before opening the CDP connection. It relies on an HTTP client to query Chrome's debugging endpoint and on as_str to make sure the returned WebSocket address is actually text before later code tries to connect to it.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: This creates the in-memory bookkeeping for one CDP WebSocket connection. It prepares places to track pending command replies, event listeners, one-time event waiters, and the background reader task.

**Data flow**: It receives an already-open WebSocket connection. It stores that connection, starts the command id counter at zero, and creates empty collections for pending commands, persistent event listeners, and temporary event waiters. No network messages are sent here.

**Call relations**: CdpConnection.open creates the WebSocket first and then calls this initializer to wrap it in the project's CDP helper object. Later methods such as send, expect, wait, and _read_loop all use the state prepared here.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: This opens a live CDP connection to Chrome and starts the background task that listens for messages. It is the normal entry point for creating a usable CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers. It connects to that URL with a large allowed message size, wraps the resulting socket in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this when it is setting up browser automation. After open returns, the session can send commands immediately because the reader task is already running to match Chrome's replies and events.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: This shuts down the CDP connection cleanly. It stops the background reader task and closes the WebSocket so no more browser messages are exchanged.

**Data flow**: It reads the stored reader task, cancels it if it exists, waits for the cancellation to finish while ignoring the expected cancellation exception, clears the reader reference, and then closes the WebSocket. The connection changes from active to closed.

**Call relations**: This is used during cleanup when the browser automation session no longer needs the CDP channel. It complements open: open starts the WebSocket and reader task, while close tears them down.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This sends one command to Chrome and waits for that command's reply. Callers use it when they need a specific CDP action to complete before continuing.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for commands aimed at a specific browser target. It assigns a new message id, creates a future to hold the reply, stores that future in the pending-command table, serializes the command to JSON, and sends it over the WebSocket. It then waits up to the command timeout for _dispatch to fill the future with either a result or an error. The output is the result dictionary from Chrome, or an exception if Chrome fails, the connection closes, or the timeout expires.

**Call relations**: Higher-level browser code calls send whenever it wants Chrome to do something. The other half of the story happens in _read_loop and _dispatch: the reader receives Chrome's response, _dispatch matches it by id, and the future created here is completed so send can return.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: This registers a listener for a repeating browser event. A listener is a callback function that will be called every time that named event arrives.

**Data flow**: It receives an event name and a callback. It adds the callback to the list for that event name. Nothing is returned, but the connection's listener table is changed so future incoming events will notify that callback.

**Call relations**: Code uses this when it wants ongoing updates, such as repeated page or network events. Later, when _dispatch receives an event with the same name, it calls each listener registered here with the event parameters and session id.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This creates a one-time wait for one of several possible browser events. It is useful when code is about to do something and needs to pause until Chrome reports that a particular event happened.

**Data flow**: It receives one or more event names and, optionally, a session id to narrow the wait to a specific browser target. It creates a future, stores it with the event names and session filter, and returns the future to the caller. The future has no value yet; it will be completed later when a matching event arrives.

**Call relations**: Callers usually create this future before triggering an action, then pass it to wait. _dispatch checks these waiters whenever an event arrives and completes the matching future with that event's parameters.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: This waits for an event future created by expect, with a timeout. It also cleans up the waiter afterward so stale waits do not remain in memory.

**Data flow**: It receives a future and a timeout length. It waits until the future is completed by _dispatch or until the timeout expires. In all cases, it removes that future from the waiters list before returning or raising. The output is the event's parameter dictionary if the event arrived in time.

**Call relations**: This is the second half of the expect pattern. expect registers the one-time event interest, _dispatch completes it when Chrome sends the matching event, and wait gives the caller the result while ensuring the temporary waiter is removed afterward.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: This is the background listener that continuously reads messages from Chrome. It keeps the connection useful by feeding every incoming message into the dispatcher.

**Data flow**: It reads raw WebSocket messages one by one, parses each JSON string into a dictionary-like message, and passes it to _dispatch. If the WebSocket closes, it stops reading. Before exiting, it fails every still-pending command and every still-waiting event future so callers are not left waiting forever.

**Call relations**: CdpConnection.open starts this as a background task. It is the bridge between Chrome's incoming messages and the rest of the class: each message is handed to _dispatch, and on shutdown it protects callers of send and wait from silent hangs.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: This sorts each incoming CDP message into either a command reply or a browser event, then delivers it to the right waiting code. It is the traffic controller for messages coming from Chrome.

**Data flow**: It receives a parsed message dictionary. If the message has an integer id, it treats it as a reply to a previous command, removes the matching pending future, and completes it with either a result dictionary or a CdpError. If the message has an event method name instead, it reads the event parameters and session id, completes any one-time waiters that match, keeps the non-matching waiters, and calls all persistent listeners registered for that event.

**Call relations**: _read_loop calls this for every message received from the WebSocket. It completes futures created by send and expect, creates CdpError objects for failed command replies, and calls listeners registered by on so higher-level code can react to browser events.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### Runtime safety
Safe JavaScript execution and validation of protocol data shapes returned by Chrome.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `request handling`

Browser automation often needs to ask the open web page a question, such as “what is this element’s value?” or “run this small function on that page object.” The browser exposes this through the Chrome DevTools Protocol, often shortened to CDP, which is a message-based control channel for Chrome-like browsers. This file hides the raw message details behind a simple BrowserRuntime object.

The main idea is: send a Runtime.evaluate or Runtime.callFunctionOn command to the browser, ask for the answer to be returned as a normal JSON value, then check whether the page threw an exception. If the page did throw, this file raises a Python RuntimeError instead of letting the caller accidentally treat a failed browser script as a valid result.

Two small Protocol classes describe the shape of the objects this wrapper expects: one object must be able to send CDP commands, and another must be able to provide that connection. A Protocol is like saying “anything with these methods is acceptable,” without requiring a specific class.

The important safety behavior is raise_on_exception. CDP responses can contain exceptionDetails when browser-side JavaScript fails. This file checks for that field after every runtime command, so callers get a clear failure instead of confusing missing or partial data.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This describes the browser connection method that sends one Chrome DevTools Protocol command and returns the browser’s JSON reply. It is a contract: any real connection object used here must provide this method.

**Data flow**: The caller gives it a command name, optional command parameters, and optionally a browser session id for a specific tab or target. The connection sends that command to the browser and returns the response as a JSON-like dictionary.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on this method through browser.connection().send. This file does not implement the sending itself; it only states the shape of the connection it needs so runtime commands can be sent through whatever browser session object is provided.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This describes the method that returns the active browser protocol connection. It lets BrowserRuntime avoid knowing the concrete session class used elsewhere in the project.

**Data flow**: It takes no extra input beyond the session object itself. It returns an object capable of sending Chrome DevTools Protocol commands to the browser.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on first ask the session for its connection, then use that connection to send runtime commands. This keeps the runtime helper focused on JavaScript execution rather than on how browser sessions are stored or connected.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a browser target and returns its value. Someone would use it when they need a simple answer from the page, such as reading a variable or computing a small expression.

**Data flow**: It receives a session id identifying the browser target and a JavaScript expression string. It sends a Runtime.evaluate command with returnByValue turned on, meaning the browser should send back the actual value rather than a remote reference. After the browser replies, it checks for a page-side exception and then extracts the returned value from the response.

**Call relations**: This method is a higher-level wrapper around BrowserRuntimeCdp.send. After sending the browser command, it immediately calls BrowserRuntime.raise_on_exception so JavaScript failures become Python errors. It also uses as_map to safely treat nested response pieces as dictionaries before reading the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function against an existing browser-side object and returns the function’s result as a dictionary. It is useful when automation already has a remote object id and wants to ask that object to compute or expose structured data.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body, and optional argument values. It builds the Runtime.callFunctionOn command, converts each argument into the format CDP expects, asks the browser to return the result by value, checks for an exception, and finally returns the result value as a dictionary. If the browser returns no value, it treats that as an empty dictionary.

**Call relations**: Like eval, this method sends its command through browser.connection().send and then uses BrowserRuntime.raise_on_exception to stop on browser-side JavaScript errors. It uses as_map while unpacking the nested CDP response so callers receive a plain mapping instead of dealing with the raw protocol shape.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser runtime response for a JavaScript exception and raises a clear Python RuntimeError if one occurred. It prevents failed page scripts from being mistaken for successful results.

**Data flow**: It receives the JSON dictionary returned by the browser. It looks for exceptionDetails, then tries to find the most helpful message, preferring the exception description and falling back to the text field. If no exception details are present, it returns silently; if an exception is present, it raises an error with the message.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a response from the browser. In the larger flow, it acts like a checkpoint at the border between browser JavaScript and Python code: successful responses pass through, while failed browser execution is turned into a normal Python exception for upstream callers to handle.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during CDP message parsing`

The browser engine talks to Chrome through the Chrome DevTools Protocol, often shortened to CDP. In practice, that means Chrome sends and receives JSON: plain data made from objects, lists, strings, numbers, booleans, and null. The risk is that JSON is flexible, while the engine usually needs something specific, such as “this field must be a non-empty string” or “this field must be a list.”

This file is the boundary guard for that problem. It defines shared names for JSON-shaped data, then provides small “narrowing” functions that check an unknown JSON value before the rest of the code uses it. Think of it like a customs desk for incoming data: valid shapes pass through, missing optional objects or lists can become empty containers, and invalid shapes are stopped immediately.

When a value does not have the expected shape, the helpers raise ValidationError. That matters because the error is meant to be treated as a recoverable tool problem, not as a mysterious crash deeper in the browser engine. Without this file, many later parts of the system would have to repeat the same checks, or worse, might assume a value is safe and fail in confusing ways later.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary of named fields. It is useful when protocol data may be missing or unknown, but the caller needs a safe object to read from.

**Data flow**: It receives a JSON value and a path string that names where that value came from. If the value is null or missing, it turns it into an empty dictionary; if it is already a dictionary, it returns it unchanged. If it is anything else, it raises ValidationError with a message saying that this path must be an object.

**Call relations**: Code that reads Chrome protocol data can call this before treating a value like a field map. When the value is wrong, this function hands off to ValidationError so the boundary failure is reported clearly instead of letting bad data travel farther into the engine.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It protects callers that need a real text value, such as an identifier or name, and cannot safely continue with null, an empty string, or another data type.

**Data flow**: It receives a JSON value and a path string describing the value’s location. If the value is a string and it is not empty, that string comes back out. For every other case, it raises ValidationError explaining that the named path must be a non-empty string.

**Call relations**: CDP-reading code uses this when a protocol field must be text. If the check fails, the function creates a ValidationError, making the problem explicit at the wire-data boundary before any later code relies on the string.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer. It is used when the browser protocol is expected to provide a whole number, such as a count, index, or numeric id.

**Data flow**: It receives a JSON value and a path string for error reporting. If the value is an integer, it returns that integer unchanged. If the value is missing, a string, a list, an object, or any other non-integer shape, it raises ValidationError saying the path must be an integer.

**Call relations**: Other parsing code can call this before doing number-based work with protocol data. On failure, it calls into ValidationError so the mismatch is reported as a validation problem rather than causing a later, harder-to-understand type failure.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It also treats a missing or null value as an empty list, which is helpful for optional repeated fields in protocol messages.

**Data flow**: It receives a JSON value and a path string that identifies the field being checked. If the value is a list, it returns the list as-is; if the value is null or absent, it returns an empty list. If the value is anything else, it raises ValidationError saying the path must be a list.

**Call relations**: Browser protocol parsing code can call this before looping over a field. If Chrome’s JSON has the wrong shape, this function raises ValidationError immediately, keeping the rest of the engine from accidentally iterating over invalid data.

*Call graph*: 1 external calls (__init__).

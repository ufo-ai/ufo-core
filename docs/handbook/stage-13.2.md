# Chrome DevTools transport and runtime bridge  `stage-13.2`

This stage is shared behind-the-scenes support for browser automation. It is the “cable and adapter” between the project and Chrome. Chrome DevTools Protocol is Chrome’s control channel for tools and automation. The code here lets the rest of the system talk to that channel without dealing with raw messages.

The cdp.py file manages the live connection. It opens a WebSocket, which is a two-way network pipe, sends numbered commands to Chrome, waits for the matching replies, and passes browser events to the code that subscribed to them.

The wire.py file defines what valid DevTools messages should look like. Since those messages are JSON, or structured text data, this file checks that required fields and types are present. If Chrome sends something unexpected, the problem is caught early with a clear error.

The runtime.py file builds on that transport to run JavaScript inside a page. It wraps Chrome’s Runtime commands and turns JavaScript-side failures into ordinary Python exceptions. Together, these pieces make browser control reliable and safer for higher-level code.

## Files in this stage

### DevTools transport
Opens and manages the Chrome DevTools Protocol connection, including command dispatch and message validation.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `active while a browser automation session is connected to Chrome`

Chrome DevTools Protocol, or CDP, is like a remote-control socket for Chrome. Other code can ask Chrome to open pages, inspect tabs, click things, or report browser events. This file provides the reliable messenger for that conversation.

The main class, CdpConnection, owns a WebSocket connection, which is a long-lived two-way network pipe. When code sends a CDP command, the file gives that command a unique number, remembers who is waiting for the answer, sends the JSON message to Chrome, and waits for the matching reply. If Chrome reports an error, the reply becomes a CdpError instead of a silent failure. If Chrome takes too long, the wait times out so the rest of the system does not hang forever.

At the same time, Chrome can send unsolicited events, such as page changes or target updates. CdpConnection separates these from command replies. One-time waiters can wait for a specific event, while registered listeners can be called every time an event appears. A background reader task keeps reading incoming messages and routing them to the right place.

A key safety feature is cleanup on connection loss. If the WebSocket closes, every pending command and event wait is failed loudly. Without that, callers could wait forever for answers that will never arrive.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: This builds a clear exception for a CDP command that Chrome rejected. It keeps the failed method name and error code so callers can tell which browser command failed and why.

**Data flow**: It receives the command name, Chrome’s numeric error code, and Chrome’s error message. It stores the method and code on the error object, then creates a readable message such as “CDP Page.navigate failed...”. The result is an exception that can be raised through the waiting command future.

**Call relations**: When CdpConnection._dispatch receives a command reply containing an error, it creates this CdpError and attaches it to the waiting caller. This turns Chrome’s raw error response into a normal Python failure path.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: This finds the actual DevTools WebSocket address to connect to. Callers may already have a WebSocket URL, or they may only have Chrome’s HTTP debugging address; this function accepts either form.

**Data flow**: It receives a URL and HTTP headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise, it asks the browser’s /json/version endpoint for connection details, checks that the HTTP request succeeded, extracts webSocketDebuggerUrl from the JSON response, and returns that string.

**Call relations**: This is a setup helper used before opening the WebSocket. It relies on httpx to make the HTTP request and as_str to make sure the returned webSocketDebuggerUrl is really text before the connection code uses it.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: This prepares a CdpConnection around an already-open WebSocket. It sets up the bookkeeping needed to match outgoing commands with incoming replies and to route browser events.

**Data flow**: It receives a WebSocket client connection. It stores that connection, starts the command id counter at zero, and creates empty collections for pending command replies, event listeners, one-time event waiters, and the background reader task reference. It does not send or receive anything yet.

**Call relations**: CdpConnection.open creates the WebSocket and then calls this initializer. The fields created here are later used by send, expect, wait, _read_loop, and _dispatch as the shared state of the CDP conversation.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: This opens a live CDP connection to Chrome and starts the background task that reads incoming messages. It is the normal way other code begins talking to Chrome through this file.

**Data flow**: It receives the WebSocket URL and optional headers. It connects to that WebSocket with a large allowed message size, wraps the connection in a CdpConnection, starts _read_loop as an asynchronous background task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this during browser session setup. After open returns, other code can send commands and wait for events while the reader task quietly routes all messages in the background.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: This shuts down the CDP connection cleanly. It stops the background reader task and closes the WebSocket so the browser-control channel is no longer used.

**Data flow**: It looks at the stored reader task. If one exists, it cancels it, waits for cancellation to finish while ignoring the expected cancellation exception, clears the task reference, and then closes the WebSocket. The connection moves from active to closed.

**Call relations**: This is used during cleanup or teardown. It works with _read_loop’s cleanup behavior: stopping the reader makes sure pending waits do not linger, and closing the WebSocket releases the network connection.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This sends one CDP command to Chrome and waits for that command’s reply. It is the main request-and-response path for browser automation commands.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a session id for commands aimed at a specific browser target. It assigns a new message id, stores a future for the expected reply, serializes the command as JSON, sends it over the WebSocket, and waits up to the command timeout. If the reply arrives, it returns the result data. If it times out, it removes the pending wait and raises a timeout error.

**Call relations**: Other browser-control code calls send when it needs Chrome to do something. The reply is not read here directly; _read_loop receives all incoming WebSocket messages and _dispatch matches the reply id back to this command’s waiting future.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: This registers a callback to run whenever a named CDP event arrives. It is for ongoing event observation, not just waiting once.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for that event. Later, whenever Chrome sends that event, the listener receives the event parameters and optional session id.

**Call relations**: This feeds into _dispatch’s event fan-out. After _read_loop passes an incoming event to _dispatch, _dispatch calls every listener registered here for that event name.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: This creates a one-time wait for one of several possible CDP events. It is useful when code knows that a browser event should happen soon and wants to pause until it does.

**Data flow**: It receives one or more event names and, optionally, a session id to limit the wait to one browser target. It creates a future, stores it with the event names and session filter, and returns the future immediately. The future will later complete with the event parameters when a matching event arrives.

**Call relations**: Callers usually create this future before triggering an action, then pass it to wait. _dispatch is the piece that eventually completes the future when _read_loop receives a matching event from Chrome.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: This waits for a future created by expect, with a timeout. It prevents event waits from lasting forever if Chrome never sends the expected event.

**Data flow**: It receives an event future and a timeout value. It waits for the future to complete within that time and returns the event data if it does. Whether the wait succeeds, fails, or times out, it removes that future from the stored waiter list so stale waits do not remain.

**Call relations**: This is the partner to expect. expect registers the desired event, _dispatch completes the future if the event arrives, and wait gives the caller a bounded and cleaned-up way to receive the result.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: This is the background reader that continuously receives messages from Chrome. It keeps the rest of the connection alive by feeding every incoming JSON message into the dispatcher.

**Data flow**: It reads raw messages from the WebSocket one by one, parses each JSON string into a dictionary-like message, and passes it to _dispatch. If the WebSocket closes, it exits. In all exit cases, it fails every pending command and event wait so callers are told the connection is gone instead of waiting forever.

**Call relations**: CdpConnection.open starts this as an asynchronous task. It calls _dispatch for normal message routing, and it supplies the safety net for send, expect, and wait when the underlying WebSocket disappears.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: This sorts each incoming CDP message into the right bucket: a command reply, a one-time expected event, or a broadcast event for listeners. It is the traffic director for all messages coming from Chrome.

**Data flow**: It receives a parsed CDP message. If the message has a numeric id, it treats it as a reply to a previous command, finds the matching pending future, and completes it with either a result or a CdpError. If the message has an event method name instead, it extracts the event parameters and optional session id, completes matching one-time waiters, removes completed waiters, and then calls all registered listeners for that event.

**Call relations**: _read_loop calls this for every message received from the WebSocket. It completes futures created by send and expect, creates CdpError when Chrome reports command errors, and triggers listener callbacks registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `cross-cutting during CDP message parsing`

The browser engine talks to Chrome using the Chrome DevTools Protocol, often shortened to CDP. CDP is a stream of JSON messages: plain data made from objects, lists, strings, numbers, booleans, and null values. The risk with this kind of data is that the program may expect one shape, such as an object or a string, but receive something else. If that mistake is not caught at the edge, it can travel deeper into the system and become much harder to understand.

This file acts like a small customs checkpoint for incoming wire data. It defines broad JSON type aliases so the rest of the code can say, “this value came from JSON.” Then it provides four simple narrowing functions: one for objects, one for non-empty strings, one for integers, and one for lists. Each function either returns the value in the expected shape or raises ValidationError with a message that names the bad field path.

A small but important detail is that missing objects and lists can become empty ones. That lets callers treat absent optional fields as “nothing here” instead of special-casing null every time. Strings and integers are stricter because an empty or missing value would not be useful where those are required.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary-like set of named fields. It is used when the browser engine expects a bundle of properties and wants a safe object back.

**Data flow**: It receives a JSON value, or None, plus a text path that says where the value came from. If the value is None, it turns that into an empty object. If the value is already an object, it returns it unchanged. If it is anything else, it creates a ValidationError saying that this path must be an object.

**Call relations**: When parsing CDP data, higher-level code can call this at the boundary before reading fields from a value. If the value has the wrong shape, this function stops the flow by raising ValidationError, rather than handing bad data deeper into the browser engine.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a useful string. It rejects missing, non-string, and empty-string values because those would not safely identify or describe anything.

**Data flow**: It receives a JSON value, or None, plus a path naming the field being checked. If the value is a non-empty string, it returns that string. For every other case, it raises ValidationError with a message that says the path must be a non-empty string.

**Call relations**: Code that reads required text fields from CDP messages can call this before using the text. If the browser sends the wrong kind of value, this function reports the problem immediately through ValidationError.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer, a whole number. It is useful for fields such as counts, indexes, or identifiers where a different kind of value would make later logic unsafe.

**Data flow**: It receives a JSON value, or None, and a path that describes where the value was found. If the value is an integer, it returns it. Otherwise, it raises ValidationError saying that the named path must be an integer.

**Call relations**: When other browser-engine code needs a whole number from a CDP response, it can call this function first. The function either hands back a trusted integer or stops the parse with ValidationError so later code does not have to guess.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list, meaning an ordered collection of JSON items. It helps callers safely loop over fields that may contain multiple entries.

**Data flow**: It receives a JSON value, or None, and a path naming the field. If the value is a list, it returns it unchanged. If the value is None, it returns an empty list, treating a missing optional collection as having no items. If the value is anything else, it raises ValidationError saying the path must be a list.

**Call relations**: Code that reads arrays from CDP messages can call this before iterating over them. This function turns absent lists into empty lists for convenience, and uses ValidationError to block values that are not list-shaped at all.

*Call graph*: 1 external calls (__init__).


### Page runtime bridge
Provides the safe JavaScript execution layer built on top of the DevTools Runtime domain.

### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `browser automation calls that evaluate JavaScript in a page`

Browser automation often needs to ask the page questions, such as “what is this element’s text?” or “run this small function on that page object.” The browser exposes this through CDP, the Chrome DevTools Protocol, which is a message-based control channel for Chrome-like browsers. This file hides the raw message details behind a simple BrowserRuntime helper.

The main idea is: code elsewhere provides a browser session that knows how to send CDP messages, and BrowserRuntime uses it to send two specific kinds of messages. eval runs a JavaScript expression and returns its value. call_on runs a JavaScript function against an existing browser-side object and returns the function’s value. Both ask the browser to return plain JSON-style values, so the rest of the Python code does not have to deal with remote browser objects when it only needs data.

A key safety feature is raise_on_exception. JavaScript can fail inside the page, just like Python can fail locally. CDP reports that failure inside the response instead of automatically throwing a Python exception. This file checks for that field and raises RuntimeError, so callers do not accidentally treat a failed browser script as a successful result.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the low-level object that can send a command to the browser over CDP, the browser control protocol. It is a protocol, meaning it describes what another object must be able to do rather than implementing the sending itself.

**Data flow**: A caller gives it a CDP method name, optional parameters, and optionally a browser session id that says which page or target to talk to. The implementing object sends that request to the browser and returns the browser’s reply as a dictionary-like JSON object.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on an object with this send ability. They build the Runtime.evaluate or Runtime.callFunctionOn request, then hand it to this lower-level connection to actually reach the browser.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This describes how a browser session should expose its underlying CDP connection. It lets BrowserRuntime stay focused on JavaScript runtime commands without knowing how the session stores or creates its connection.

**Data flow**: It takes no extra input beyond the session object itself. It returns an object that can send CDP commands to the browser.

**Call relations**: BrowserRuntime keeps a BrowserRuntimeSession and calls connection when it needs to send a browser command. The returned connection is then used by eval and call_on to deliver their requests.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser session and returns the expression’s plain value. Someone would use it when they need to ask the page a quick question or compute a value in the page’s own JavaScript environment.

**Data flow**: It receives a session id and a JavaScript expression string. It sends a Runtime.evaluate command to the browser with returnByValue turned on, meaning the browser should send back the actual JSON value rather than a handle to a remote object. It then checks whether the page threw an exception, extracts the result value from the browser’s nested response, and returns that value.

**Call relations**: This is one of the main public actions of BrowserRuntime. It asks the session’s CDP connection to send the command, then immediately calls BrowserRuntime.raise_on_exception so failed page scripts become Python errors. It uses as_map to safely treat nested response pieces as dictionary-like data before reading the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function on an existing browser-side object and returns the function’s plain object result. It is useful when the automation code already has a reference to something in the page, like a DOM element, and wants to inspect or transform it with JavaScript.

**Data flow**: It receives a session id, a browser object id, a JavaScript function body, and optional argument values. It builds a Runtime.callFunctionOn request, converting each Python-side argument into the value format CDP expects. The browser runs the function against the target object and returns a response. The function checks for JavaScript exceptions, pulls out the returned value, treats a missing value as an empty object, and returns it as a dictionary.

**Call relations**: This works alongside eval as the other main BrowserRuntime operation. It sends its prepared command through the session’s CDP connection, then relies on BrowserRuntime.raise_on_exception to stop the flow if the page code failed. It uses as_map to verify and unpack the nested response structure before giving data back to its caller.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime response for a JavaScript exception and turns it into a Python RuntimeError. It prevents callers from accidentally ignoring script failures reported by the browser.

**Data flow**: It receives the full response dictionary from the browser. If there is no exceptionDetails dictionary, it does nothing and returns normally. If exception details are present, it looks for the most helpful message, first from the exception description and then from the fallback text, and raises RuntimeError with that message.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this right after receiving a browser response. It acts like a gatekeeper: successful JavaScript results pass through, while failed JavaScript execution stops the calling flow with a clear Python error.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).

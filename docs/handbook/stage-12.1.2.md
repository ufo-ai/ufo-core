# CDP transport, runtime, and browser settling  `stage-12.1.2`

This stage is the browser automation “control room.” It sits behind the main work loop, especially after actions like clicking, typing, or opening a page. Its job is to talk to Chrome, run small bits of page code, avoid freezes, and decide when it is safe to move on.

The cdp.py file is the main phone line to Chrome. It opens a WebSocket, which is a long-lived two-way connection, sends commands, receives replies, and passes browser events to the code waiting for them. wire.py checks the shape of the JSON messages on that line, so the rest of the system gets clean, predictable Python data instead of raw, uncertain input.

runtime.py uses that connection to run JavaScript inside a tab and translates the result back into normal Python values, or raises a clear error if the page code fails. dialogs.py watches for pop-up alerts and accepts or dismisses them before they block everything. settle.py is the patience timer: it waits for real page work to finish while ignoring endless background noise like ads or analytics.

## Files in this stage

### CDP transport and wire safety
Core DevTools Protocol communication and validation of the JSON message shapes exchanged with Chrome.

### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser session startup through request handling`

Chrome can be automated through the Chrome DevTools Protocol, often shortened to CDP. In plain terms, CDP is a remote-control language for Chrome: the program sends messages like “open this page” or “click here,” and Chrome sends back replies and events. This file provides the transport layer for that conversation.

The main class, CdpConnection, wraps a WebSocket, which is a long-lived two-way network connection. It gives each outgoing command a unique number, remembers who is waiting for the answer, and then sends the command as JSON text. In the background, a reader task continuously receives messages from Chrome. If a message is a reply, it finds the matching waiting command and finishes it. If a message is an event, it wakes any one-time waiters and also calls any registered listeners.

This is like a receptionist for a busy office phone line: outgoing requests get ticket numbers, incoming replies are matched to the right ticket, and general announcements are passed to anyone who subscribed. A key safety feature is that if the connection closes, all pending waits are failed immediately instead of leaving the rest of the program hanging forever.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Creates a clear Python error when Chrome says a CDP command failed. It keeps the failed method name and Chrome’s error code so callers can understand which command went wrong.

**Data flow**: It receives the command name, a numeric error code, and Chrome’s message → stores the method and code on the error object and builds a readable error sentence → produces an exception that can be attached to the waiting command result.

**Call relations**: When CdpConnection._dispatch receives a reply from Chrome that contains an error, it uses CdpError.__init__ to turn that raw protocol error into a normal Python exception for the code that was waiting on the command.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the actual DevTools WebSocket address to use. If the caller already gave a WebSocket URL, it returns it; otherwise it asks Chrome’s HTTP debugging endpoint where the WebSocket lives.

**Data flow**: It receives a URL and HTTP headers → if the URL already starts with ws:// or wss://, it is returned unchanged → otherwise it requests the /json/version endpoint, checks that the response succeeded, reads webSocketDebuggerUrl from the JSON body, and returns it as a string.

**Call relations**: This helper prepares the address that CdpConnection.open can dial. It relies on httpx.AsyncClient to make the HTTP request and as_str to check that the value from Chrome is really a string.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Sets up the in-memory bookkeeping for one CDP WebSocket connection. It starts with no pending commands, no event listeners, and no event waiters.

**Data flow**: It receives an already-open WebSocket connection → saves it, starts the command id counter at zero, and creates empty collections for pending command replies, long-lived event listeners, one-time event waiters, and the background reader task → returns a ready CdpConnection object.

**Call relations**: CdpConnection.open creates the WebSocket first, then calls this initializer before starting the background read loop. The rest of the methods depend on these stored tables to match replies and events to the right waiting code.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a new WebSocket connection to Chrome and starts listening for incoming CDP messages right away. This is the normal way to create a working CdpConnection.

**Data flow**: It receives a WebSocket URL and optional headers → dials Chrome using the websockets library with a large message-size limit → wraps that socket in CdpConnection → starts a background task that reads incoming messages → returns the live connection.

**Call relations**: BrowserSession._bootstrap calls this during browser setup. After open finishes, the returned connection can send commands while its background reader task quietly routes replies and events.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the CDP connection cleanly. It stops the background reader task and then closes the WebSocket.

**Data flow**: It reads the stored reader task and WebSocket → if a reader task exists, it cancels it and waits for cancellation without treating that expected cancellation as an error → clears the reader reference and closes the socket → leaves the connection no longer usable for new communication.

**Call relations**: This is used when the browser automation session is being cleaned up. It works together with _read_loop’s final cleanup behavior, so pending commands or event waits are not silently left unresolved.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one CDP command to Chrome and waits for its matching reply. It protects callers from waiting forever by applying a command timeout.

**Data flow**: It receives a CDP method name, optional parameters, and an optional session id → assigns a new numeric message id, builds a JSON command, stores a future as the placeholder for the reply, and sends the JSON over the WebSocket → waits until _dispatch completes that future with either a result or an error → returns the result dictionary, or raises a timeout or CDP error.

**Call relations**: Higher-level browser code uses this whenever it needs Chrome to do something. The actual reply arrives later in _read_loop, which passes it to _dispatch; _dispatch matches the reply id back to the future created here.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a listener for a recurring CDP event. A listener is a callback function that should run whenever Chrome announces that specific event.

**Data flow**: It receives an event name and a callback → adds the callback to the list for that event → future matching events will call that callback with the event parameters and optional session id.

**Call relations**: This supports ongoing subscriptions, unlike expect, which waits for only one event. When _dispatch later receives an event message, it calls all listeners registered through this method.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one of several possible CDP events. This is useful when code knows the next step depends on Chrome announcing something, such as a page load or target creation.

**Data flow**: It receives one or more event names and an optional session id filter → creates a future, stores it with the event names and session filter → returns the future so the caller can later wait for it.

**Call relations**: Callers usually pair this with CdpConnection.wait. The future made here is completed by _dispatch when a matching event arrives from the reader loop.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for a future created by expect, with a timeout. It also removes the waiter afterward so old waits do not pile up.

**Data flow**: It receives an event future and an optional timeout → waits for that future to be completed by an incoming event, but only up to the timeout → returns the event parameters if successful → always removes that future from the internal waiter list when finished or timed out.

**Call relations**: This is the second half of the expect-and-wait pattern. expect registers interest in an event; _dispatch fulfills that interest when the event arrives; wait gives the caller the result and cleans up the registration.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads raw messages from Chrome and sends them to the dispatcher. It is the background worker that keeps the connection alive and responsive.

**Data flow**: It reads JSON text messages from the WebSocket → converts each message from text into a Python dictionary → passes each dictionary to _dispatch → if the socket closes, it stops reading and fails every pending command and event wait so callers are told the connection ended.

**Call relations**: CdpConnection.open starts this as a background task. Its main partner is _dispatch, which does the actual routing of each message. Its cleanup path protects callers of send, expect, and wait from hanging after a disconnect.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Routes one incoming CDP message to the right waiting code. It decides whether the message is a command reply or a browser event, then completes futures or calls listeners accordingly.

**Data flow**: It receives one decoded message dictionary → if the message has a numeric id, it treats it as a command reply, finds the matching pending command, and completes its future with either a result dictionary or a CdpError → if the message has an event method name, it extracts the event parameters and session id, completes matching one-time waiters, keeps unmatched waiters, and calls any registered listeners → it returns nothing but updates the connection’s internal waiting lists.

**Call relations**: _read_loop calls this for every message received from Chrome. It is the central switchboard for the file: it completes futures created by send and expect, uses CdpError.__init__ for failed command replies, and invokes callbacks registered through on.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).


### `extensions/browser/ufo_ext_browser/bua/wire.py`

`io_transport` · `request handling`

The browser engine communicates with Chrome using the Chrome DevTools Protocol, which is a JSON-based language for asking the browser to do things and reading back what happened. JSON is flexible, so a value that should be a string, list, or object might arrive missing or in the wrong shape. This file is the boundary guard for that problem.

It first names the kinds of JSON values the code expects: empty values, booleans, numbers, strings, lists, and dictionaries. Then it provides four small checking functions. Each one receives a raw JSON value and a path string that describes where that value came from, such as a field name. If the value has the expected shape, the function returns it in a more useful form. If it does not, the function raises `ValidationError`, which means “the outside data was not shaped the way this engine needs.”

The important design choice is that bad wire data is caught early, at the edge of the system, instead of being passed deeper into the browser engine as an unknown value. Like checking a package label before sending it through a warehouse, these helpers make sure later code can trust what it is holding.

#### Function details

##### `as_map`  (lines 22–29)

```
def as_map(value: Json | None, path: str) -> JsonDict
```

**Purpose**: This function checks that a JSON value is an object, meaning a dictionary with string keys. It is used when the browser engine expects a group of named fields and wants an empty dictionary if the value is missing.

**Data flow**: It receives a JSON value and a text path that explains where the value came from. If the value is `None`, it treats that as an empty object and returns `{}`. If the value is already a dictionary, it returns it unchanged. If the value is anything else, it raises `ValidationError` with a message naming the bad path.

**Call relations**: When code reading Chrome’s JSON needs a dictionary-shaped value, it calls `as_map` before continuing. If the check fails, `as_map` creates a `ValidationError` so the problem is reported as invalid incoming data instead of causing confusing failures later.

*Call graph*: 1 external calls (__init__).


##### `as_str`  (lines 32–37)

```
def as_str(value: Json | None, path: str) -> str
```

**Purpose**: This function checks that a JSON value is a non-empty string. It is useful for required text fields where an empty string would be just as unusable as a missing value.

**Data flow**: It receives a JSON value and a path describing that value’s location. If the value is a string and it is not empty, the function returns that string. For `None`, an empty string, or any non-string value, it raises `ValidationError` explaining that the path must contain a non-empty string.

**Call relations**: Code that pulls required text out of Chrome’s JSON uses `as_str` to make sure the text is actually present. On failure, it hands off to `ValidationError`, turning the mismatch into a clear boundary error.

*Call graph*: 1 external calls (__init__).


##### `as_int`  (lines 40–45)

```
def as_int(value: Json | None, path: str) -> int
```

**Purpose**: This function checks that a JSON value is an integer. It is used when the engine expects a whole-number field, such as an identifier, count, or index.

**Data flow**: It receives a JSON value and a path label. If the value is an integer, it returns that integer. If the value is missing or has any other shape, it raises `ValidationError` saying that the named path must be an integer.

**Call relations**: When later browser-engine code needs to rely on a number being a whole number, it calls `as_int` first. If Chrome’s response does not match that expectation, `as_int` raises `ValidationError` immediately rather than letting the wrong type travel deeper.

*Call graph*: 1 external calls (__init__).


##### `as_list`  (lines 48–55)

```
def as_list(value: Json | None, path: str) -> list[Json]
```

**Purpose**: This function checks that a JSON value is a list. It also treats a missing value as an empty list, which is useful for optional collections.

**Data flow**: It receives a JSON value and a path string. If the value is a list, it returns that list unchanged. If the value is `None`, it returns an empty list. If the value is anything else, it raises `ValidationError` with a message that names the path that should have been a list.

**Call relations**: Code that expects several items from Chrome’s JSON calls `as_list` before looping over them. If the field is absent, the caller can safely proceed with no items. If the field has the wrong shape, `as_list` creates a `ValidationError` so the bad wire data is caught at the boundary.

*Call graph*: 1 external calls (__init__).


### Runtime execution and dialogs
Helpers for running JavaScript in the page and preventing browser dialogs from blocking automation.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, or “are you sure you want to leave?” warnings. In a normal browser, these wait for a human click. In an automated browser, they are dangerous because they can block later browser events until someone answers them. This file is the automatic “person at the keyboard” for those dialogs.

The main rule is cautious: simple alerts and before-unload warnings are accepted, because there is usually no meaningful choice. Confirm and prompt dialogs are dismissed, because accepting them might silently agree to something the website asked, such as deleting data or confirming a purchase. Either way, the event is written into the browser session’s dialog log so the agent can later know that it happened.

The file talks to the browser through CDP, the Chrome DevTools Protocol, which is the message-based control channel used to drive Chromium-like browsers. It does not block while answering the dialog. Instead, it starts a background task that sends the reply. That matters because the dialog itself can stall the browser’s event stream, so the answer must be sent quickly without holding up other code. If answering fails, the file logs a warning rather than crashing the whole browser session.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser connection’s send method. It represents the ability to send a command to the browser over CDP, the control channel used to automate the browser.

**Data flow**: It receives a command name, optional command details, and an optional browser session identifier. The connection sends that command to the browser and returns a dictionary-like response from the browser.

**Call relations**: BrowserDialogs._answer_dialog relies on an object with this method when it needs to tell the browser to accept or dismiss a JavaScript dialog.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This describes how a browser session gives access to its browser-control connection. Code uses it when it needs to send a direct command to the browser.

**Data flow**: It takes the current session object as context and returns an object that can send CDP commands. It does not itself decide what command to send.

**Call relations**: BrowserDialogs._answer_dialog uses this connection when it is time to send the actual dialog-answer command.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This describes how a browser session starts a task in the background. It is used here so dialog answering can happen immediately without making the current event handler wait.

**Data flow**: It receives a coroutine, which is an async job waiting to be run. The session schedules that job to run separately and does not return a dialog result itself.

**Call relations**: BrowserDialogs.on_dialog uses this when a dialog appears, handing it the work of answering the dialog so the event path can keep moving.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is called when the browser reports that a JavaScript dialog has appeared. It decides whether to accept or dismiss the dialog, records that decision for later visibility, and starts the reply immediately.

**Data flow**: It receives the dialog details from the browser and the optional session identifier for the page where it appeared. It reads the dialog type and message, chooses accept for alerts and before-unload dialogs or dismiss for other dialog types, appends a short human-readable note to the session’s dialog list, and schedules a background task to send the answer. It does not wait for the browser reply before returning.

**Call relations**: When the browser event stream reports a dialog, this function is the first responder. It prepares the decision and then hands the actual browser command to BrowserDialogs._answer_dialog, running it through the session’s background-task mechanism.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This sends the actual command that answers the frozen browser dialog. It is separated from on_dialog so it can run as an asynchronous background task.

**Data flow**: It receives the session identifier and the accept-or-dismiss choice. It sends a Page.handleJavaScriptDialog command to the browser with that choice. If the command succeeds, the dialog is answered; if the browser reports an error, times out, or the runtime is no longer usable, it records a warning in the log instead of raising the problem outward.

**Call relations**: BrowserDialogs.on_dialog creates this task whenever a dialog appears. This function then uses the browser session’s connection to send the CDP command that actually unblocks the page.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/runtime.py`

`io_transport` · `browser automation request handling`

Browser automation often needs to ask the page itself a question, such as “what is this value?” or “run this function on that page object.” The browser exposes this through the Chrome DevTools Protocol, often shortened to CDP, which is a message-based control channel for inspecting and driving Chrome-like browsers. This file wraps the CDP “Runtime” commands so the rest of the project does not have to build those messages by hand every time.

The main class, BrowserRuntime, is like a translator at a service desk. The caller gives it a browser session, a target session id, and either a JavaScript expression or a function to run. BrowserRuntime sends the right CDP command, asks the browser to return the answer as a plain JSON value, checks whether the browser reported a JavaScript exception, and then gives back the useful part of the reply.

Two small protocol classes describe what BrowserRuntime expects from the surrounding browser code: something that can send CDP messages, and something that can provide that connection. The important safety behavior is raise_on_exception: without it, a failed script in the page could look like a normal response and the rest of the system might continue with missing or misleading data.

#### Function details

##### `BrowserRuntimeCdp.send`  (lines 10–15)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This describes the one ability BrowserRuntime needs from a CDP connection: send a named browser command with optional data and get back a JSON-style dictionary. It is a protocol method, meaning it states the expected shape of another object rather than doing the work here.

**Data flow**: The caller provides a CDP method name, optional parameters, and optionally a browser session id. An implementation is expected to send that message to the browser and return the browser's reply as a dictionary-like JSON object.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on rely on an object with this behavior after they ask their browser session for a connection. This method is the handoff point from the friendly wrapper in this file to the lower-level browser communication layer.


##### `BrowserRuntimeSession.connection`  (lines 19–19)

```
def connection(self) -> BrowserRuntimeCdp
```

**Purpose**: This describes how BrowserRuntime gets access to the CDP connection for a browser session. It is a small contract: any browser session used here must be able to provide something that can send CDP commands.

**Data flow**: There are no input values besides the session object itself. The method returns a CDP connection object that BrowserRuntime can use to talk to the browser.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on call this first so they can send their Runtime commands. The actual connection object then carries the message to the browser.


##### `BrowserRuntime.eval`  (lines 28–35)

```
async def eval(self, session_id: str, expression: str) -> Json
```

**Purpose**: This runs a JavaScript expression inside a specific browser target and returns its value. Someone would use it when they need a quick answer from the page, such as reading a variable or evaluating a small script.

**Data flow**: It receives a browser session id and a JavaScript expression. It builds a Runtime.evaluate CDP request, asks the browser to return the result as a simple value, sends the request through the session connection, checks the reply for a page-side exception, and then returns the result's value field.

**Call relations**: This function starts the “evaluate JavaScript” path. After the browser replies, it calls BrowserRuntime.raise_on_exception so failures in the page become Python errors, and it uses as_map to safely treat nested reply pieces as dictionaries before extracting the final value.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.call_on`  (lines 37–55)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This runs a JavaScript function against an existing browser-side object and returns the function's result as a dictionary. It is useful when the project already has a reference to a page object and wants to ask that object to do or compute something.

**Data flow**: It receives a session id, a browser object id, a function body, and optional argument values. It turns the arguments into the shape CDP expects, sends a Runtime.callFunctionOn request, checks for a JavaScript exception, then pulls out the returned value and makes sure it is a dictionary before giving it back.

**Call relations**: This is the “call a function on a page object” path. It hands the low-level command to the CDP connection, then passes the reply through BrowserRuntime.raise_on_exception and as_map so callers get either a clean dictionary result or a clear failure.

*Call graph*: calls 1 internal fn (raise_on_exception); 1 external calls (as_map).


##### `BrowserRuntime.raise_on_exception`  (lines 58–66)

```
def raise_on_exception(result: JsonDict) -> None
```

**Purpose**: This checks a browser Runtime reply for an uncaught JavaScript exception and turns it into a Python RuntimeError. It keeps callers from accidentally treating a failed page script as a successful result.

**Data flow**: It receives the browser's reply dictionary. If there is no exceptionDetails dictionary, it leaves everything unchanged and returns nothing. If exception details are present, it looks for a useful description or text message and raises an error that includes that message.

**Call relations**: BrowserRuntime.eval and BrowserRuntime.call_on both call this immediately after receiving a browser response. It acts as the shared safety gate before either function extracts and returns useful data from the response.

*Call graph*: called by 2 (call_on, eval); 1 external calls (get).


### Browser settling
Waiting logic that determines when browser activity caused by an automation step is complete enough to continue.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigations`

Browser automation often needs a simple answer to a hard question: after I click something, when is the page ready for the next step? Waiting for every network request is too strict, because many modern pages keep sending analytics or background requests forever. Not waiting at all is too loose, because the automation may act before the page has changed.

This file solves that by watching only the consequences of the current action. It keeps a small ledger of foreground requests that matter, pages that are still loading, and pages that have painted something visible on screen. “Paint” here means the browser has drawn meaningful page content. That is treated as a strong sign that the page is usable.

The helper `tracks_request` filters out requests that are usually not worth waiting for, such as images, fonts, low-priority preloads, and common analytics hosts. The `Settle` class then records when important requests start and finish, when a page enters or leaves loading, and when it paints.

When asked to wait, `Settle.wait` first gives the page one tiny turn to run any immediate JavaScript work triggered by the action. Then it waits until either the page paints and its important requests briefly quiet down, or no relevant work remains, or a time limit is reached. The effect is like waiting for a restaurant order until the main dish arrives, but not staying forever because background music is still playing.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: This function decides whether a browser network request is important enough to wait for. It ignores traffic that is usually decorative or background-only, such as images, fonts, low-priority requests, and known analytics services.

**Data flow**: It receives a dictionary of browser event details. It reads the request type, priority, and URL host. If the request looks passive or analytics-related, it returns `False`; otherwise it returns `True`, meaning the settling logic should count this request as real foreground work.

**Call relations**: When the browser reports that a request has started, `Settle.on_request_started` calls this function before adding the request to the pending-work ledger. This keeps the rest of the waiting logic focused on work likely caused by the user action, rather than unrelated page noise.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: This creates a fresh settling tracker. It starts with no pending requests, no recorded started requests, no loading pages, and no painted pages.

**Data flow**: It takes no outside data besides the new object being created. It sets up four pieces of internal memory: important requests still in flight, a count of important requests that have started, browser sessions that are loading, and browser sessions that have painted. The result is a ready-to-use `Settle` object.

**Call relations**: A browser session creates this tracker when it starts, and creates a fresh one again during close-related cleanup. Other parts of the browser session then feed it request, loading, and paint events so it can later answer the question: is this action done enough to continue?

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: This clears the tracker before measuring a new action. It makes sure old requests and old paint signals do not affect the decision for the next click, key press, or navigation.

**Data flow**: It reads the object’s current internal state and empties the pending request set, resets the started-request count to zero, and clears remembered paint events. The loading set is left as-is, so ongoing page loading is still remembered.

**Call relations**: This is used as a boundary between actions. After a reset, the later event-recording methods can build a fresh picture of only the work caused by the next action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that an important browser request has begun. It only counts requests that have a valid browser session, a valid request id, and pass the `tracks_request` filter.

**Data flow**: It receives browser event details and the session id they belong to. It pulls out the request id, asks `tracks_request` whether the request matters, and if so stores the pair of session id and request id in the pending set. It also increases the count of important requests that have started.

**Call relations**: This is called when the browser emits a request-start event. It hands the first judgment to `tracks_request`, then updates the state that `Settle.wait` later watches while deciding whether the page has quieted down.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that a browser request is no longer pending. It removes the request from the tracker whether the request succeeded, failed, or otherwise ended, as long as it can identify it.

**Data flow**: It receives browser event details and a session id. It reads the request id and, if both the session id and request id are valid, removes that pair from the pending set. It does not return a value; it changes the tracker’s internal ledger.

**Call relations**: This is called when the browser reports that a request has finished. Its updates are what allow `Settle.wait` and `_drain_after_paint` to see that foreground network work has drained and that automation may be able to continue.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: This marks a browser session as currently loading a document. It tells the settling logic that even if network requests are quiet right now, the page itself is still in a loading phase.

**Data flow**: It receives a session id and adds it to the internal loading set. Nothing is returned; the object’s memory is updated so later waiting checks know that this session is not yet fully loaded.

**Call relations**: This is called by event-handling code when the browser indicates loading has started. `Settle.wait` and `_drain_after_paint` consult this loading set before deciding that things are quiet enough.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: This marks a browser session as no longer loading. It removes one reason that the settling logic might keep waiting.

**Data flow**: It receives a session id and removes it from the internal loading set if present. It returns nothing; the visible effect is that future waiting checks may now consider the session quiet if there are also no pending important requests.

**Call relations**: This is called when browser loading completes. It pairs with `mark_loading`, and its result is used by `Settle.wait` and `_drain_after_paint` when they check whether the page has reached a calm state.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: This records that a browser session has painted visible content. Painting is treated as an important sign that the page has become usable, even if some background network activity continues.

**Data flow**: It receives a session id and adds it to the internal painted set. It returns nothing; it updates the tracker so waiting logic can switch from broad waiting to a short post-paint grace period.

**Call relations**: This is called when the browser reports a paint lifecycle event. `Settle.wait` checks this signal and, once it appears, hands off to `_drain_after_paint` to wait only a short extra time for important follow-up requests.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: This is the main waiting routine. It pauses automation until the current browser action appears to have produced its useful visible and network effects, or until a safety time limit is reached.

**Data flow**: It receives a browser communication object, a session id, and a maximum number of seconds to wait. First it asks `_flush_page_tasks` to let immediate page JavaScript run. Then it repeatedly checks whether the session has painted, is still loading, or has pending important requests. It returns nothing; its result is the passage of enough time for the page to be reasonably ready, without waiting forever.

**Call relations**: This is the central consumer of all the event records gathered by the other `Settle` methods. It calls `_flush_page_tasks` at the start so newly triggered requests are counted, and calls `_drain_after_paint` once a paint signal arrives so painted pages get only a short extra grace period.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: This waits briefly after the page has painted. It gives important content requests a chance to finish, but avoids waiting the full timeout on pages that keep doing background work forever.

**Data flow**: It receives a session id and an absolute deadline. It creates a shorter grace deadline, then loops while there is still loading or pending important request work. If things become quiet for a small gap, it returns early; otherwise it returns when the grace time runs out.

**Call relations**: `Settle.wait` calls this as soon as it notices that the session has painted. This helper is the reason painted pages can move on quickly while still allowing a small window for content that arrives just after the first visible draw.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: This gives the page one quick chance to run JavaScript tasks caused by the action before the settling check begins. That helps make sure immediate follow-up network requests have already been reported and counted.

**Data flow**: It receives the browser communication object and a session id. It sends a tiny JavaScript promise to the page through the Chrome DevTools Protocol, which is Chrome’s automation control channel, and waits for it to resolve. If that fails or times out, it simply sleeps for a short beat instead. It returns nothing, but it improves the accuracy of the later wait.

**Call relations**: `Settle.wait` calls this first. It hands off to `cdp.send` to run the small script in the browser, and if that route is unavailable it falls back to a short sleep so the larger settling flow can still continue.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).

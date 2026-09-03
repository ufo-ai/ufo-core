# Browser automation and hosted browser sessions  `stage-10`

This stage gives the assistant a working web browser during its main work loop. When a task needs the web, it either starts, rents, or connects to Chrome, then controls pages through the Chrome DevTools Protocol, a standard “remote control” channel for Chrome.

The in-process browser tool surface is the part the assistant talks to directly. It turns requests such as open a page, click a button, type text, upload a file, read page content, switch tabs, or take a screenshot into careful browser actions. It also waits for pages to settle, handles pop-ups, tracks downloads, and cleans up at the end of the turn.

The remote and sandbox-hosted Chrome providers supply the actual browser. They can run Chrome inside the sandbox, connect to Browserbase, or delegate work to Browser Use, depending on what the task needs.

The cdp.py file is the message pipe to Chrome. It sends commands over a WebSocket, waits for replies, and routes browser events back to the right waiting code.

## Sub-stages

- [In-process browser tool surface](stage-10.1.md) `stage-10.1` — 21 files
- [Remote and sandbox-hosted Chrome providers](stage-10.2.md) `stage-10.2` — 3 files

## Files in this stage

### Browser automation and hosted browser sessions
### `extensions/browser/ufo_ext_browser/bua/cdp.py`

`io_transport` · `browser session connection and event loop`

Chrome automation works like a conversation: the program sends Chrome a numbered command, and Chrome later sends back either an answer with the same number or a separate event such as “page loaded.” This file keeps that conversation organized so the rest of the project does not have to deal with raw WebSocket messages.

The main class, CdpConnection, wraps a WebSocket connection to Chrome. When it opens, it starts a background reader task. That reader continuously receives JSON messages, which are text messages shaped like nested dictionaries and lists. If a message is a reply to a command, the file finds the matching waiting task and gives it the result. If Chrome reports an error, it turns that into a clear CdpError. If a message is an event, the file either wakes up one-time waiters that were expecting that event, or calls registered listeners.

This is important because browser automation has many things happening at once. Without the bookkeeping here, one command could accidentally receive another command’s answer, or a task could wait forever after the browser disconnects. The file also enforces timeouts and, when the connection closes, loudly fails all pending waits instead of leaving them stuck.

#### Function details

##### `CdpError.__init__`  (lines 30–33)

```
def __init__(self, method: str, code: int, message: str) -> None
```

**Purpose**: Creates a clear Python error for a failed Chrome DevTools Protocol command. It keeps the command name and Chrome’s numeric error code so callers can see what failed and why.

**Data flow**: It receives the command name, an error code, and Chrome’s error message. It stores the command name and code on the error object, then builds a readable message such as “CDP Page.navigate failed...” that can be raised to the caller.

**Call relations**: CdpConnection._dispatch uses this when Chrome sends an error response for a command. Instead of passing raw protocol details upward, _dispatch turns that response into this focused exception and attaches it to the waiting command future.

*Call graph*: called by 1 (_dispatch).


##### `resolve_ws_url`  (lines 36–42)

```
async def resolve_ws_url(url: str, headers: dict[str, str]) -> str
```

**Purpose**: Finds the real DevTools WebSocket address to connect to. A caller can give either a direct WebSocket URL or an HTTP Chrome debugging URL, and this function turns it into the WebSocket URL Chrome expects.

**Data flow**: It receives a URL and HTTP headers. If the URL already starts with ws:// or wss://, it returns it unchanged. Otherwise it asks the given HTTP endpoint for /json/version, reads the webSocketDebuggerUrl field from the response, checks that it is a string, and returns it.

**Call relations**: This is a preparation helper for code that needs to open a Chrome DevTools connection. It uses httpx.AsyncClient to make the HTTP request and as_str to validate the response field before another part of the system connects with CdpConnection.open.

*Call graph*: 2 external calls (AsyncClient, as_str).


##### `CdpConnection.__init__`  (lines 46–52)

```
def __init__(self, ws: ClientConnection) -> None
```

**Purpose**: Builds the in-memory state needed to track one live DevTools WebSocket connection. It prepares places to remember outstanding commands, event listeners, one-time event waits, and the background reader task.

**Data flow**: It receives an already-open WebSocket connection. It stores that socket, starts the command id counter at zero, and creates empty collections for pending command replies, event listeners, event waiters, and the future reader task reference.

**Call relations**: CdpConnection.open creates the WebSocket first, then calls this initializer to wrap it in a higher-level connection object. Other methods on the object depend on the state set up here to match replies and route events correctly.


##### `CdpConnection.open`  (lines 55–59)

```
async def open(cls, ws_url: str, headers: dict[str, str] | None=None) -> Self
```

**Purpose**: Opens a new WebSocket connection to Chrome and starts listening for incoming DevTools messages. This is the usual entry point for creating a usable CdpConnection.

**Data flow**: It receives the WebSocket URL and optional headers. It connects to Chrome with a large allowed message size, creates a CdpConnection around that socket, starts the background read loop as an asynchronous task, and returns the ready connection object.

**Call relations**: BrowserSession._bootstrap calls this while setting up a browser session. After open returns, the rest of the session can call send, expect, wait, and on while the background _read_loop keeps processing Chrome’s replies and events.

*Call graph*: called by 1 (_bootstrap); 2 external calls (create_task, connect).


##### `CdpConnection.close`  (lines 61–67)

```
async def close(self) -> None
```

**Purpose**: Shuts down the DevTools connection cleanly. It stops the background reader task first, then closes the WebSocket to Chrome.

**Data flow**: It reads the stored reader task and WebSocket. If the reader task exists, it cancels it, waits for the cancellation to finish, ignores the expected cancellation error, clears the task reference, and then closes the socket.

**Call relations**: This is used during cleanup when the browser automation session no longer needs the DevTools connection. It works together with _read_loop’s shutdown behavior, which makes sure pending commands and event waits do not remain silently stuck.

*Call graph*: 1 external calls (suppress).


##### `CdpConnection.send`  (lines 69–85)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Sends one DevTools command to Chrome and waits for that command’s response. It is the main way the rest of the project asks Chrome to do something, such as inspect a page or perform browser actions.

**Data flow**: It receives a command name, optional command parameters, and optionally a session id for a specific browser target. It assigns a new numeric id, stores a future under that id, sends the JSON command over the WebSocket, and waits up to the command timeout for _dispatch to fill in the future. It returns Chrome’s result dictionary, or raises a timeout or protocol error.

**Call relations**: Callers use this when they need a direct command-and-answer exchange with Chrome. The sent message later comes back through _read_loop, which passes it to _dispatch; _dispatch matches the reply by id and completes the future that send is awaiting.

*Call graph*: 3 external calls (get_running_loop, timeout, dumps).


##### `CdpConnection.on`  (lines 87–88)

```
def on(self, event: str, listener: EventListener) -> None
```

**Purpose**: Registers a callback to be run whenever a particular DevTools event arrives. This is for ongoing event watching, not just waiting once.

**Data flow**: It receives an event name and a listener function. It adds that listener to the list for the event, changing the connection’s listener table; it does not return a value.

**Call relations**: Other parts of the browser session use this when they want to react repeatedly to Chrome events. When _dispatch later sees an event with the matching name, it calls each registered listener with the event parameters and optional session id.


##### `CdpConnection.expect`  (lines 90–93)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Creates a one-time wait for one of several possible DevTools events. It is like putting a note on the desk saying, “wake me when this event happens.”

**Data flow**: It receives one or more event names and optionally a session id to narrow the wait to one browser target. It creates a future, stores it with the event names and session filter, and returns the future to the caller.

**Call relations**: Callers typically call expect before doing something that should trigger an event, then pass the returned future to wait. When _dispatch sees a matching event, it completes this future with the event parameters.

*Call graph*: 1 external calls (get_running_loop).


##### `CdpConnection.wait`  (lines 95–104)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float=EVENT_TIMEOUT_S) -> JsonDict
```

**Purpose**: Waits for an event future created by expect, with a timeout. It also removes the waiter afterward so old waits do not build up.

**Data flow**: It receives a future and an optional timeout. It awaits the future for at most that many seconds and returns the event parameters if the event arrives. Whether it succeeds, times out, or is cancelled, it removes that future from the connection’s waiter list.

**Call relations**: This pairs with expect: expect registers interest in an event, and wait turns that interest into a bounded wait. _dispatch is the part that completes the future when Chrome sends the matching event.

*Call graph*: 1 external calls (timeout).


##### `CdpConnection._read_loop`  (lines 106–120)

```
async def _read_loop(self) -> None
```

**Purpose**: Continuously reads incoming WebSocket messages from Chrome and feeds them into the dispatcher. It is the background worker that keeps command replies and browser events moving.

**Data flow**: It reads raw text messages from the WebSocket, parses each JSON message into a dictionary, and passes it to _dispatch. If the WebSocket closes, it stops reading. Before exiting, it gives every still-pending command and event waiter a clear connection-closed error and clears the stored lists.

**Call relations**: CdpConnection.open starts this loop as an asynchronous background task. It is the only path by which incoming Chrome messages reach _dispatch, and it protects callers of send and wait from hanging if the connection disappears.

*Call graph*: calls 1 internal fn (_dispatch); 1 external calls (loads).


##### `CdpConnection._dispatch`  (lines 122–161)

```
def _dispatch(self, message: JsonDict) -> None
```

**Purpose**: Sorts one incoming DevTools message into the right bucket: a command response, a command error, or a browser event. This is the message router for the connection.

**Data flow**: It receives one parsed message dictionary. If the message has a numeric id, it treats it as a reply to a previous command, finds the matching pending future, and completes it with either a result dictionary or a CdpError. If the message has an event method name instead, it extracts the event parameters and optional session id, completes any matching one-time waiters, keeps the non-matching waiters, and calls registered listeners for that event.

**Call relations**: _read_loop calls this for every message received from Chrome. It completes the futures that send, expect, and wait rely on, and it uses CdpError when Chrome reports a failed command.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_read_loop); 2 external calls (get, as_map).

## 📊 State Registers Touched

- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.

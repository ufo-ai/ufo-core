# Browser lifecycle, tabs, downloads, dialogs, and settling  `stage-10.3.3`

This stage is shared support for the browser automation loop. After the system asks the browser to do something, these helpers keep the browser safe, usable, and in sync before the next step begins. The tabs helper is like the browser’s map. It knows which tabs are open, which one is active, and how to open, close, switch, or navigate them while listening for real browser changes. The dialogs helper watches for JavaScript pop-ups, which are small browser message boxes that can block all work until answered. It records them, accepts simple required ones, and dismisses ones that might confirm a risky action. The downloads helper notices when a file download starts, waits for it to finish, and removes blocks that would stop it. It also turns PDF pages into real downloads when Chrome would otherwise hide them in its built-in viewer. The settle helper decides when a page has reacted enough to continue, without waiting on unimportant background noise like ads or tracking requests.

## Files in this stage

### Blocking browser prompts
Handles browser interruptions that could stall automation, including JavaScript dialogs and download flows.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `request handling`

Web pages can show JavaScript dialogs such as alerts, confirmation boxes, prompts, or “are you sure you want to leave?” messages. In a normal browser, a person clicks a button. In an automated browser, that dialog can stop the page from doing anything else until it is answered. If this file did not exist, one surprise pop-up could block every later browser event and leave the agent stuck.

The main class, BrowserDialogs, is the small decision-maker for those pop-ups. When a dialog appears, on_dialog reads its type and message. Alerts and “beforeunload” dialogs are automatically accepted, because there is not much meaningful choice there. Confirmation and prompt dialogs are dismissed, so the automation does not accidentally agree to a web site’s own “are you sure?” question.

It also writes a plain text note into the browser session’s dialogs list, so the rest of the system, including the model using the browser, can know that the dialog appeared and how it was answered. The actual click on the dialog is sent through Chrome DevTools Protocol, a browser control channel often shortened to CDP. That reply is launched in the background because it must happen quickly. If the browser has already moved on or the command fails, the file logs a warning instead of crashing the whole run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser control connection. Anything used here must be able to send a named command, with optional parameters and an optional browser session id, and return a JSON-like result.

**Data flow**: It receives a command name, optional command details, and optionally which browser session to target. It sends that command through the browser control channel and returns the browser’s response as a dictionary-like JSON object.

**Call relations**: BrowserDialogs._answer_dialog relies on this method to tell the browser to accept or dismiss the active JavaScript dialog. The file defines it as a protocol, meaning it describes what another object must provide rather than implementing the transport itself.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the browser control connection from a browser session. It lets dialog code stay independent from the concrete browser session class.

**Data flow**: It takes no extra input beyond the session object. It returns an object that can send commands to the browser through the control channel.

**Call relations**: BrowserDialogs._answer_dialog calls this when it is time to send the actual dialog answer. Like BrowserDialogCdp.send, this is part of a protocol: it states what the surrounding browser session must offer.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way for a browser session to start a small asynchronous task without waiting for it immediately. Here it is used so dialog answering can happen right away without blocking the event handler.

**Data flow**: It receives a coroutine, which is a paused asynchronous job. It schedules that job to run in the background and does not return a result from that job to the caller.

**Call relations**: BrowserDialogs.on_dialog uses this to launch BrowserDialogs._answer_dialog after recording the dialog. The protocol keeps this file from needing to know exactly how the larger browser system schedules background work.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog appeared. It decides whether to accept or dismiss it, records a human-readable note, and starts the actual browser reply immediately.

**Data flow**: It receives the dialog details from the browser and the optional session id where it happened. It reads the dialog type and message, chooses accept for alerts and before-unload warnings or dismiss for other dialog types, appends a note such as “confirm dismissed: ...” to the session’s dialog history, and schedules the answer command in the background.

**Call relations**: This is the main entry point in this file for dialog events. When a browser event dispatcher notices a dialog, it would call on_dialog; on_dialog then hands off the browser command work to BrowserDialogs._answer_dialog so the page can be unfrozen quickly.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This function sends the actual command that clicks the dialog’s accept or dismiss choice inside the browser. It is careful not to crash the run if the dialog is already gone or the browser command fails.

**Data flow**: It receives the target session id and a true-or-false accept decision. It asks the browser session for its connection, sends the Page.handleJavaScriptDialog command with that decision, and produces no returned value. If the command fails because of a browser control error, timeout, or runtime problem, it writes a warning to the log.

**Call relations**: BrowserDialogs.on_dialog schedules this function in the background after deciding what should happen. _answer_dialog then uses the session’s connection and its send method to carry out that decision through the browser control channel.

*Call graph*: called by 1 (on_dialog).


### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `browser event handling and download waits`

Browser downloads are awkward for an automation agent. A web page may start a download, Chrome reports progress through events, and some files, especially PDFs, may open inside Chrome instead of becoming downloadable bytes. This file is the small download coordinator for that situation.

It keeps a simple list of downloads, each with a Chrome download id, a suggested filename, and a state such as in progress or completed. When Chrome says a download has begun, the file records it. When Chrome reports progress, it updates the matching record. Code that wants a downloaded file can then wait until one reaches the completed state.

The file also works with Chrome's Fetch domain, which can pause network requests. A paused request is like traffic stopped at a gate: if this code does not open the gate again, the page hangs. Most paused requests are simply released. But if the response is a top-level document and its content type is PDF, this code rewrites the response headers so Chrome treats it as an attachment. In plain terms, it nudges Chrome from “show this in the built-in viewer” to “download this file.”

The important safety rule is that every paused request is continued, even if something goes wrong. Failures are logged rather than allowed to freeze the browser session.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the shape of the object that can send commands to Chrome's debugging interface. A real implementation uses it to tell Chrome things like “continue this paused request.”

**Data flow**: It receives a command name, optional command data, and optionally a browser session id. The real sender sends that to Chrome and returns Chrome's JSON-like reply.

**Call relations**: BrowserDownloads._continue_request and BrowserDownloads._continue_response rely on this ability after BrowserDownloadSession.connection gives them the active connection. This file defines the expectation, while another part of the system provides the real network connection.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This protocol method describes how the download helper gets the Chrome command connection from the surrounding browser session.

**Data flow**: It takes the current browser session object and returns an object that can send commands to Chrome. It does not transform download data itself.

**Call relations**: The continue helpers call this when they need to release a paused request or response. The actual session object elsewhere in the project supplies the concrete connection.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This protocol method describes how the download helper starts a small asynchronous task without blocking the current event callback. It is used because Chrome events must be answered quickly.

**Data flow**: It receives a coroutine, which is a suspended asynchronous job, and schedules it to run in the background. Nothing is returned to the caller.

**Call relations**: BrowserDownloads.on_fetch_paused uses this to continue paused browser requests and responses. That keeps the event handler lightweight while still making sure Chrome is unblocked.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This protocol method answers whether a browser event belongs to the main page frame rather than an embedded frame. That matters because only main-page PDF navigations should be forced into downloads.

**Data flow**: It receives a session id and a frame id from Chrome's event data. It returns true if that frame is the page's top-level frame, otherwise false.

**Call relations**: BrowserDownloads.on_fetch_paused calls this before changing PDF response headers. This prevents embedded PDFs from being forced into downloads when a page expects to display them inside itself.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request. It always arranges for the request to continue, and for top-level PDFs it changes the response so the browser downloads the file instead of opening Chrome's PDF viewer.

**Data flow**: It reads Chrome's paused-request data, including the request id, response code, response headers, and frame id. If there is no valid request id, it stops. If this is an early request pause, it schedules a simple continue. If this is a response pause, it checks the content type and top-level frame status, then schedules a response continue, possibly with a forced attachment header.

**Call relations**: This is called by the browser event layer when a Fetch pause event arrives. It uses _content_type to recognize PDFs, asks BrowserDownloadSession.is_top_level_frame whether the navigation is the main page, and hands the actual Chrome command work to _continue_request or _continue_response through BrowserDownloadSession.spawn_background.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This asynchronous helper releases a paused request that has not yet reached the response stage. Its job is to prevent Chrome from waiting forever.

**Data flow**: It receives the browser session id and Chrome request id. It sends a Fetch.continueRequest command to Chrome. If Chrome rejects the command, times out, or the session is no longer usable, it logs a warning and does not raise the error further.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this when the paused event does not include a response status code. It gets the command channel through BrowserDownloadSession.connection and sends the low-level Chrome command there.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This asynchronous helper releases a paused response, optionally changing its headers so Chrome treats it as a download. It is the point where top-level PDFs are turned into attachments.

**Data flow**: It receives the session id, request id, response code, response headers, and a true-or-false force flag. If force is false, it only tells Chrome to continue the response. If force is true, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, then sends the modified response data to Chrome. Errors are logged so the browser event flow does not crash.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this for response-stage pauses. It sends its command through BrowserDownloadSession.connection, and it is the practical follow-through after on_fetch_paused has decided whether a PDF should be forced to download.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records a new download when Chrome announces that one has started. It creates the local entry that later progress and wait logic can find.

**Data flow**: It reads the Chrome event fields for the download guid and suggested filename. It appends a new Download object to the browser session's downloads list, starting with the state set to inProgress. If Chrome does not provide a filename, it uses the fallback name “download.”

**Call relations**: This is called by the browser event layer when Chrome emits a download-begin event. BrowserDownloads.on_download_progress later updates the same record, and BrowserDownloads.wait later looks for records that have completed.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the saved state of a known download when Chrome reports progress. It keeps the local download list in step with the browser.

**Data flow**: It reads the download guid and new state from Chrome's event data. It scans the browser session's downloads list and, for the matching guid, replaces the stored state with the new state.

**Call relations**: This is called by the browser event layer after on_download_begin has created a download record. BrowserDownloads.wait depends on these updates to know when a download has reached the completed state.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This function briefly checks whether an action that looked like navigation actually turned into a download. It gives Chrome a short grace period to report a new download.

**Data flow**: It receives the number of downloads that existed before the action. For up to a small fixed time, it repeatedly compares the current download count with that earlier count. It returns true if a new download appears, otherwise false.

**Call relations**: Other browser-flow code can call this after triggering a navigation or click, to decide whether the page changed normally or a download started instead. Internally it just watches the shared downloads list and pauses briefly between checks.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This asynchronous function waits until a browser download finishes and returns the completed download record. It does not read the file bytes; it only reports which browser download completed.

**Data flow**: It reads an optional timeout value from the input arguments, using max_wait_seconds as the fallback. It repeatedly scans the browser session's downloads list for downloads whose state is completed. If one or more are found, it returns the most recent completed one. If the deadline passes first, it raises a TimeoutError.

**Call relations**: This is used by higher-level code that needs to wait after asking the browser to download something. It relies on on_download_begin and on_download_progress having filled and updated the shared downloads list, and it uses float_or_default to interpret the timeout argument safely.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns a user-supplied timeout-like value into a floating-point number, or falls back to a default when no value was supplied. It rejects values that are not numeric.

**Data flow**: It receives a JSON-like value and a default number. If the value is an integer, float, or non-empty string, it converts it to a float. If the value is missing, it returns the default. For other kinds of values, it raises a ValidationError explaining that the value must be numeric.

**Call relations**: BrowserDownloads.wait calls this before starting its wait loop. This keeps timeout parsing in one small place, so the waiting logic can work with a clean number.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper finds the Content-Type response header and returns just the main media type, such as application/pdf. It ignores extra details like character sets.

**Data flow**: It receives a list of header-like JSON values. It looks for a dictionary whose name is Content-Type, ignoring letter case. If found, it takes the value before any semicolon, trims spaces, lowercases it, and returns it. If no content type is present, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this when deciding whether a paused top-level response is a PDF. That decision controls whether _continue_response is asked to add the attachment header.

*Call graph*: called by 1 (on_fetch_paused).


### Action settling
Determines when the browser has reacted enough after an action for automation to safely continue.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `request handling after browser actions`

Browser automation often needs to click a button, type text, or navigate, then wait until the page is ready for the next step. Waiting for “nothing at all is happening” sounds simple, but modern web pages may keep loading ads, analytics, images, or live-feed data forever. This file solves that by waiting only for the work that seems relevant to the current action.

The Settle object keeps small pieces of state: which important network requests are still pending, whether a page is loading, and whether it has painted visible content. A “paint” is when the browser has drawn page content on screen, which is often a better readiness signal than total network silence.

When a request starts, the file first filters it through tracks_request. Passive things like images, fonts, low-priority prefetches, and known analytics hosts are ignored. Foreground requests are counted until they finish. When waiting, Settle first gives the page one tiny task-queue turn, so click handlers and immediate follow-up work can start. Then it waits until either the page paints and gets a short grace period for useful content, or the tracked work becomes quiet, or a time cap is reached. Like waiting for a restaurant order, it watches the kitchen tickets you caused, not every background chore in the building.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: This function decides whether a network request is important enough to wait for. It skips things that are usually background noise, such as images, fonts, low-priority requests, and common analytics services.

**Data flow**: It receives a browser event dictionary describing a request. It reads the request type, priority, and URL host, then compares them with known passive categories and analytics host fragments. It returns true when the request looks like foreground work caused by the action, and false when it looks safe to ignore.

**Call relations**: When Settle.on_request_started sees a new browser request, it asks tracks_request whether that request should be counted. This keeps the later waiting logic focused on useful page work instead of unrelated background traffic.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: This creates a fresh settling tracker. It starts with no pending requests, no loading pages, and no painted pages recorded.

**Data flow**: It takes no outside data beyond the new object being created. It sets up empty sets for pending requests, loading sessions, and painted sessions, and initializes the count of tracked started requests to zero. The result is a Settle instance ready to observe browser events.

**Call relations**: BrowserSession.__init__ creates one when a browser session starts, so the session can decide when actions have settled. BrowserSession.close also creates one according to the call graph, likely to reset or replace settling state during shutdown.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: This clears the action-specific settling state so the next action starts with a clean slate. It prevents old requests or old paint signals from making a later wait return too early or too late.

**Data flow**: It reads the current stored pending requests, started count, and painted sessions, then clears them. Afterward, there are no remembered pending requests or paint events for the current action, and the started count is back to zero. It does not clear the loading set.

**Call relations**: No direct caller is shown in the provided graph, but this method is the reset button for the tracker. It is meant to be used between browser actions so each wait only considers consequences of the current action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records an important network request when the browser reports that one has begun. It only counts requests tied to a browser session and judged relevant by tracks_request.

**Data flow**: It receives the browser event data and an optional session identifier. It extracts the request ID, checks that both the session and request ID are usable, and asks tracks_request whether the request matters. If so, it adds the session/request pair to the pending set and increases the started counter.

**Call relations**: This is the entry point for request-start events into the settling tracker. It hands the filtering decision to tracks_request, then stores only the requests that Settle.wait should later wait to finish.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This marks a network request as no longer pending when the browser says it finished or otherwise stopped. It lets the wait logic know that one piece of relevant work is done.

**Data flow**: It receives browser event data and an optional session identifier. It extracts the request ID, and if the session and request ID are valid, removes that pair from the pending set. The output is a changed internal state: one less request may be blocking readiness.

**Call relations**: This pairs with Settle.on_request_started. Started requests are added to the pending set, and finished requests are removed so Settle.wait can eventually see that the page has gone quiet.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: This records that a browser session is currently loading a document. It tells the settling logic not to call the page ready while navigation is still in progress.

**Data flow**: It receives a session identifier and adds it to the loading set. After that, waits for that session treat the page as still busy until it is marked loaded.

**Call relations**: This feeds navigation state into Settle.wait. While the session is marked loading, the wait loop keeps giving the page time instead of returning immediately.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: This records that a browser session is no longer loading its document. It removes one reason for the wait logic to keep waiting.

**Data flow**: It receives a session identifier and removes it from the loading set if present. Afterward, that session is no longer considered to be in document-loading state.

**Call relations**: This complements Settle.mark_loading. Once loading is cleared, Settle.wait may return if there are also no important pending requests.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: This records that a browser session has painted visible content. A paint is treated as a strong sign that the page has become usable, though it may still get a short grace period for foreground requests.

**Data flow**: It receives a session identifier and adds it to the painted set. Future waits for that session can switch from general waiting to the shorter after-paint drain period.

**Call relations**: This supplies the readiness signal that Settle.wait looks for. Once a session is painted, Settle.wait hands off to Settle._drain_after_paint instead of waiting for full network quiet up to the larger cap.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: This waits until the current browser action appears settled, or until a time limit is reached. It balances speed and safety by watching paint, loading state, and only the important network requests.

**Data flow**: It receives a Chrome DevTools Protocol connection, a session identifier, and a maximum number of seconds to wait. First it asks the page to run one tiny queued task through _flush_page_tasks, so immediate work caused by the action has a chance to appear. Then it checks for paint, loading, and pending tracked requests until the page is ready or the deadline arrives. It returns nothing; its effect is the delay before the caller continues.

**Call relations**: This is the main waiting method that other browser-session code would use after an action. It calls _flush_page_tasks at the start, and when a paint has happened it delegates to _drain_after_paint for the shorter post-paint waiting behavior.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: This gives a just-painted page a short extra chance to finish important foreground requests. It prevents returning too early for pages that draw a shell first and load real content immediately after.

**Data flow**: It receives a session identifier and an absolute deadline. It creates a shorter grace deadline, then repeatedly checks whether the session is still loading or has pending tracked requests. If the page stays quiet across a small gap, it returns; otherwise it stops when the grace time runs out.

**Call relations**: Settle.wait calls this whenever the session has painted. It is the fast path for modern pages that may never become completely network-idle but are visibly ready enough after a brief grace period.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: This gives the browser page one quick chance to run immediate JavaScript work before settling decisions are made. That helps catch requests started by click handlers or zero-delay timers.

**Data flow**: It receives the Chrome DevTools Protocol connection and a session identifier. It sends a Runtime.evaluate command that runs a tiny promise resolved by setTimeout(..., 0), then waits for it. If the command fails or times out, it sleeps briefly instead. It returns nothing, but after it finishes, immediate page-side follow-up work is more likely to have been observed.

**Call relations**: Settle.wait calls this first. It uses Cdp.send to talk to the browser through the Chrome DevTools Protocol, and its purpose is to make later checks of pending requests more reliable.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).


### Tab lifecycle
Keeps the automation model of tabs synchronized with the real browser while supporting tab navigation and switching.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling and browser event handling`

A browser automation tool needs a reliable map of what tabs exist, which page each tab shows, and whether a page has finished loading. This file is that map and the set of controls around it. It talks to the browser through CDP, the Chrome DevTools Protocol, which is a command-and-event interface for controlling Chromium-based browsers.

The central idea is simple: each real browser tab is represented by a small Tab object, holding the browser’s target ID, the communication session ID, keyboard state, and frame references. BrowserTabs is the main controller. It notices when the browser reports that tabs were created or destroyed, attaches to new tabs, removes closed ones, and creates a blank tab if there are none.

For navigation, it accepts friendly inputs like “example.com”, “back”, or “forward”. It normalizes ordinary web addresses, sends the right browser command, waits for loading signals, and then reports the page URL and title. It also treats downloads carefully: if navigation appears to fail because the link became a download, it does not incorrectly report a page-load failure.

Without this file, higher-level code would have to juggle raw browser IDs, loading events, tab lists, and history navigation by hand. It is like the tab strip of the automation system: it keeps the labels, the selected page, and the open/close buttons connected to the real browser underneath.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied navigation target into something the browser can understand. It leaves special commands and already-complete URLs alone, and adds “https://” to plain site names.

**Data flow**: It receives a text value such as “back”, “about:blank”, “https://example.com”, or “example.com”. It checks whether the value is a special tab command or already has a URL scheme like “http:”. If not, it returns the same text with “https://” added at the front.

**Call relations**: BrowserTabs.navigate calls this before deciding what kind of navigation to perform. This keeps the rest of navigation code from having to guess whether a user typed a full URL or just a domain name.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the in-memory record for one browser tab. This record stores the browser IDs needed to talk to the tab, plus helper state for keyboard input and page frames.

**Data flow**: It receives a target ID and a session ID from the browser. It saves those IDs, creates a fresh keyboard state, prepares an empty numbering table for frames, and creates a root frame reference for the page. The result is a Tab object ready for later commands.

**Call relations**: BrowserTabs.attach_tab calls this after the browser accepts an attachment to a target. From then on, other tab operations use the Tab object instead of passing raw browser IDs around.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when frames need human-friendly or repeatable labels instead of long browser IDs.

**Data flow**: It receives a frame ID. If that frame has not been seen before in this tab, it assigns the next available number. It returns the number for that frame, whether it was newly assigned or already known.

**Call relations**: This is a helper on Tab for code that needs to refer to page frames consistently. It is not called inside this file, but it supports the broader page/frame tracking model.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the required shape of a method that sends a command to the browser through CDP, the Chrome DevTools Protocol. It is a contract, not an implementation in this file.

**Data flow**: A caller provides a CDP method name, optional parameters, and optionally a session ID for a specific tab. The implementing connection sends that command to the browser and returns the browser’s JSON-like response.

**Call relations**: BrowserTabs relies on this method for nearly every browser action, such as attaching to tabs, enabling page events, navigating, and closing targets. The actual connection object is supplied by BrowserTabSession.connection.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines the required shape of a method that waits for one of several browser events to happen later. It lets navigation code say, “I expect this loading event soon.”

**Data flow**: A caller gives one or more event names and optionally a session ID. The implementing connection returns a future, which is a promise-like object that will later contain the event data.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this before sending a navigation command, so they do not miss the browser’s response event.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines the required shape of a method that waits for a previously expected event, with a time limit. This prevents navigation from hanging forever if the browser never sends the event.

**Data flow**: It receives a future and a timeout in seconds. The implementing connection waits until the future completes or the timeout is reached, then returns the event data or raises an error according to the connection’s behavior.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this after issuing browser navigation commands. It is the timed waiting step paired with BrowserTabCdp.expect.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how the tab controller gets the browser connection object. This keeps BrowserTabs independent from the exact connection implementation.

**Data flow**: It takes no extra input beyond the session object. The implementation returns an object that can send commands, expect events, and wait for events.

**Call relations**: BrowserTabs calls this whenever it needs to talk to the browser. The protocol lets tests or different browser backends provide their own compatible connection.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how the tab controller gets the download tracker. This is needed because some navigations turn into file downloads instead of normal web pages.

**Data flow**: It takes no extra input beyond the session object. The implementation returns an object that can tell whether a new download has started.

**Call relations**: BrowserTabs._goto uses this when a navigation reports an error. It checks whether the apparent error is actually the expected start of a download.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how code can run JavaScript inside a tab and get a result back. In this file it is used to read the page URL and title.

**Data flow**: It receives a session ID and a JavaScript expression. The implementation runs that expression in the matching browser tab and returns the JSON-like result.

**Call relations**: BrowserTabs.tab_info calls this to ask the page itself for location.href and document.title. The protocol keeps that JavaScript-running detail outside this file.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser targets already existed when tracking began. This prevents old tabs from being mistaken for newly opened tabs.

**Data flow**: It receives browser data containing target information. It reads the target IDs from that data and stores them in the session’s initial_targets set. Nothing is returned; the session’s event state is updated.

**Call relations**: This is used during browser setup or target discovery. Later, BrowserTabs.on_target_created compares new target events against this saved set so only genuinely new page tabs are queued for attachment.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Responds when the browser reports a new target, and queues it if it is a new page tab. A target is the browser’s internal name for something it can control, such as a tab.

**Data flow**: It receives event parameters and an optional session ID. It checks whether the event describes a page, whether the target ID is valid, whether it was not present at startup, and whether it has not already been queued. If all checks pass, it appends the target ID to the created_targets list.

**Call relations**: Browser event dispatch code calls this when a target-created event arrives. BrowserTabs.sync later consumes the queued target IDs and attaches to them as real Tab objects.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Responds when the browser reports that a target was destroyed, usually meaning a tab was closed. It marks that target for removal from the local tab list.

**Data flow**: It receives event parameters and an optional session ID. If the event contains a valid target ID, it adds that ID to the destroyed_targets set. It returns nothing and only updates the stored event state.

**Call relations**: Browser event dispatch code calls this when a target-destroyed event arrives. BrowserTabs.sync later removes matching Tab objects from the session.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. It ignores subframes, such as embedded ads or iframes, so the page’s top-level state stays accurate.

**Data flow**: It receives event parameters and the session ID that emitted the event. If there is no session ID, it does nothing. If the frame ID belongs to the tab’s top-level frame, it tells the settling tracker that this session is loading.

**Call relations**: Browser event dispatch code calls this on frame loading events. It uses BrowserTabs.is_top_level_frame to avoid treating every embedded frame as a full page load, then hands the state change to the Settle object.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as having loaded its document content. This is one of the signals used to decide when navigation has progressed far enough.

**Data flow**: It receives event parameters and a session ID. If there is a session ID, it tells the settling tracker that the tab has reached the loaded state. It does not return a value.

**Call relations**: Browser event dispatch code calls this when the browser reports DOM content loaded. BrowserTabs.navigate later waits on the settling tracker, which uses this kind of signal.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports an important visual lifecycle event for the top-level frame. “Painted” means the page has drawn visible content.

**Data flow**: It receives event parameters and a session ID. It ignores events without a session ID and events that are not one of the configured paint-related lifecycle events. If the event belongs to the top-level frame, it tells the settling tracker that the tab has painted.

**Call relations**: Browser event dispatch code calls this for lifecycle events. It uses BrowserTabs.is_top_level_frame to focus on the main page, then updates Settle so navigation waiting can know when the page is visually ready.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame event belongs to the main frame of a known tab. This separates full-page activity from activity inside embedded frames.

**Data flow**: It receives a session ID and a frame ID. It compares them against the known tabs, where the tab’s target ID is treated as the top-level frame ID. It returns true if a matching tab is found, otherwise false.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating loading or painting state. It acts like a gatekeeper for page-level events.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the automation system to an existing browser tab target and prepares it for use. It enables the browser features this project needs and applies the configured viewport size.

**Data flow**: It receives a target ID. It asks the browser to attach to that target and reads back a session ID. It initializes the session, enables document-fetch interception for downloads, sets device metrics such as width and height, and returns a new Tab object.

**Call relations**: BrowserTabs.sync calls this for tabs discovered from browser events, BrowserTabs.page calls it when creating the first blank tab, and BrowserTabs.create calls it after opening a new target. It delegates the shared setup commands to BrowserTabs.init_session.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser event streams needed for a tab session. Without this setup, the code would not receive page, lifecycle, document, or network information.

**Data flow**: It receives a session ID. It sends browser commands to enable page events, lifecycle events, DOM access, and network events for that session. It returns nothing after the browser has accepted those commands.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target. It is the standard preparation step before a Tab object is considered usable.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Reconciles the local tab list with tab create and destroy events received from the browser. It keeps the in-memory list from drifting away from reality.

**Data flow**: It reads the session’s queued destroyed target IDs and created target IDs. It removes closed tabs from the local list, clears the destroyed queue, then attaches to each newly created target that is not already known. If attaching fails because the browser target is gone or invalid, it skips that target.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before they rely on the tab list. It consumes the events collected by BrowserTabs.on_target_created and BrowserTabs.on_target_destroyed, using BrowserTabs.attach_tab for new tabs.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the requested tab, creating a blank one if no tabs are open. It is the safe entry point for code that needs a usable tab.

**Data flow**: It receives an optional tab index. It first syncs the local tab list with browser events. If no tabs exist, it asks the browser to create an about:blank target and attaches to it. If no index was requested, it returns the most recently opened tab. If an index was requested, it returns that tab or raises a validation error if the index is not open.

**Call relations**: BrowserTabs.navigate calls this to choose which tab to navigate, and BrowserTabs.close calls it to choose which tab to close. It relies on BrowserTabs.sync and may call BrowserTabs.attach_tab when a first tab must be created.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new page, or backward or forward in its history, then waits until the page has settled. It returns a simple summary of the tab afterward.

**Data flow**: It receives a URL-like text or the special words “back” or “forward”, plus an optional tab index. It gets the target tab, normalizes the URL, resets loading-settle state, performs the correct navigation action, waits for the page to settle, then returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create uses this after opening a new tab. Internally it calls normalize_url, BrowserTabs.page, BrowserTabs._goto for normal URLs, BrowserTabs._history_step for back or forward, and BrowserTabs.tab_info for the final report.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs the low-level browser command for normal URL navigation. It also distinguishes a real navigation failure from a link that became a file download.

**Data flow**: It receives a Tab object and a URL. It records how many downloads existed before navigation, starts expecting a DOM-content-loaded event, and sends the browser’s Page.navigate command. If the browser reports an error, it cancels the loading wait, checks whether a new download started, and either accepts the download case or raises an error. If no real page loader was created, it cancels the wait. Otherwise it waits for the load event with a timeout.

**Call relations**: BrowserTabs.navigate calls this for ordinary URL targets. It uses the browser connection for command sending and event waiting, and asks the download reader whether an apparent navigation error actually means a download began.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browser history by one step. If there is no page in that direction, it simply does nothing.

**Data flow**: It receives a Tab object and a step value, usually -1 for back or 1 for forward. It asks the browser for the tab’s navigation history, reads the current position, calculates the requested history entry, and stops if that entry does not exist. If it exists, it expects a navigation event, tells the browser to go to that history entry, and waits for the event with a timeout.

**Call relations**: BrowserTabs.navigate calls this when the user target is “back” or “forward”. It uses browser history data and waits for either a full frame navigation or an in-document navigation.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. It returns just the simple information that callers usually need to display or report a tab.

**Data flow**: It receives a Tab object. It runs a small JavaScript expression in that tab to read location.href and document.title, checks that the result is map-like data, and returns a dictionary with string URL and title fields.

**Call relations**: BrowserTabs.navigate calls this after navigation, BrowserTabs.tabs_context calls it for each tab when building a tab list, and BrowserTabs.tab_titles calls it when only titles are needed.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all known tabs for outside callers. The snapshot includes each tab’s index, whether it is the current tab, its URL, and its title.

**Data flow**: It first syncs the local tab list with browser events. It treats the last tab in the list as the current tab, then loops through all tabs, reads each tab’s info, and returns a dictionary containing current_tab and a tabs list.

**Call relations**: BrowserTabs.close calls this after closing a tab so the caller receives the updated tab state. It relies on BrowserTabs.sync and BrowserTabs.tab_info.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of all known tabs. This is a compact view for callers that only need names, not full tab details.

**Data flow**: It loops over the current in-memory tab list. For each tab, it reads the tab information and extracts the title, using an empty string if the title is missing. It returns the list of title strings.

**Call relations**: This helper uses BrowserTabs.tab_info for the actual page query. It is available to higher-level code that wants a lightweight tab overview.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and navigates it to the requested URL. It returns the new tab’s index together with its final URL and title.

**Data flow**: It receives an optional URL, defaulting to about:blank. It asks the browser to create a new blank target, attaches to that target, adds the new Tab to the local list, navigates that specific tab to the requested URL, and returns the tab ID plus navigation info.

**Call relations**: This is a high-level tab-opening operation. It uses BrowserTabs.attach_tab for setup and BrowserTabs.navigate for the actual page load.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a tab and returns the updated list of tabs. It accepts flexible tab ID input so callers can pass a number-like value from JSON.

**Data flow**: It receives an argument dictionary. It extracts and converts the optional tab_id, finds the matching tab, sends the browser command to close that target, removes the Tab object from the local list, clears out-of-process iframe session tracking, and returns the new tabs context.

**Call relations**: This is the high-level tab-closing operation. It uses _tab_id to parse input, BrowserTabs.page to find the target tab, and BrowserTabs.tabs_context to report the state afterward.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a JSON-like tab ID value into an integer tab index, or returns no index if the value is missing. This lets callers pass tab IDs as numbers or numeric strings.

**Data flow**: It receives a value that may be an integer, float, string, or something else. Integers are returned as-is, floats are converted to integers, non-empty strings are parsed as integers, and all other values become None.

**Call relations**: BrowserTabs.close calls this before choosing which tab to close. It keeps input parsing separate from the closing logic.

*Call graph*: called by 1 (close).

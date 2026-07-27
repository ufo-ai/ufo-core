# Forms, downloads, and browser interruptions  `stage-10.2.5`

This stage supports the browser while the agent is doing its main work on websites. It covers the awkward moments that can interrupt normal browsing: forms that need typing, files that need uploading, downloads that must be captured, PDFs that Chrome wants to display instead of save, and pop-up dialogs that can freeze progress.

The forms part takes a simple instruction, such as “enter this text here” or “upload this file,” checks that the target page element exists and can accept the action, then sends the right low-level browser command to do it. These low-level commands use Chrome DevTools Protocol, which is a control channel for driving Chrome from software.

The downloads part watches for a download to start, waits until it is complete, and returns the downloaded data to the rest of the system. It also changes PDF behavior so a PDF link is saved as a file instead of opened in Chrome’s built-in viewer.

The dialogs part acts like a quick receptionist for browser pop-ups. It accepts, dismisses, or answers them so automation does not get stuck waiting.

## Files in this stage

### Dialog interruptions
Handles JavaScript dialogs quickly so browser automation is not blocked by alerts, confirmations, prompts, or navigation warnings.

### `extensions/browser/ufo_ext_browser/bua/dialogs.py`

`domain_logic` · `browser event handling`

JavaScript dialogs are small browser pop-ups that can freeze a page until someone clicks a button. In browser automation, that is dangerous: if the dialog is not answered right away, later browser events can stop arriving and the agent can appear hung. This file is the safety valve for that situation.

The main class, BrowserDialogs, is told when a dialog appears. It records a plain text note about what happened, so the rest of the system or the model can know that the page tried to interrupt. Then it chooses a response. Simple alerts and “beforeunload” dialogs are accepted, because there is usually no meaningful alternative. Confirm and prompt dialogs are dismissed, because accepting them could accidentally approve something important, like a site’s “Are you sure?” checkpoint.

The actual click is sent through Chrome DevTools Protocol, or CDP, which is a browser control channel used by automation tools. The file does this in the background, like asking an assistant to immediately close the pop-up while the main conversation keeps moving. If that attempt fails because the browser connection is already gone, too slow, or otherwise broken, the file logs a warning instead of crashing the whole run.

#### Function details

##### `BrowserDialogCdp.send`  (lines 17–22)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the browser connection used by this file. It represents the ability to send a named command, with optional details, to the browser through CDP, the browser automation control channel.

**Data flow**: The caller provides a command name, optional command data, and optionally a browser session id. The connection sends that command to the browser and returns the browser’s JSON-like reply.

**Call relations**: BrowserDialogs._answer_dialog relies on this method when it needs to press accept or dismiss on a JavaScript dialog. This file only defines the promise that such a connection must keep; the real connection object is supplied from elsewhere.


##### `BrowserDialogSession.connection`  (lines 28–28)

```
def connection(self) -> BrowserDialogCdp
```

**Purpose**: This is the expected way to get the active browser connection from the surrounding session. BrowserDialogs uses it when it needs to send the command that closes a dialog.

**Data flow**: It reads the session’s current state and returns an object that knows how to talk to the browser. It does not itself decide what to send.

**Call relations**: BrowserDialogs._answer_dialog calls this method just before sending the dialog response. The session provides the communication line, while BrowserDialogs provides the decision about accepting or dismissing.


##### `BrowserDialogSession.spawn_background`  (lines 30–30)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way to start a small asynchronous job without blocking the current event handler. Here it is used so the system can answer a dialog immediately while letting the rest of the browser event flow continue.

**Data flow**: It receives a coroutine, which is a paused asynchronous task. The session schedules that task to run in the background and does not return a result from the task to the caller.

**Call relations**: BrowserDialogs.on_dialog calls this after deciding how a dialog should be answered. It hands off BrowserDialogs._answer_dialog so the actual browser command can run separately.


##### `BrowserDialogs.on_dialog`  (lines 37–49)

```
def on_dialog(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when the browser reports that a JavaScript dialog has appeared. It records what happened, chooses a safe default response, and starts the work needed to close the dialog.

**Data flow**: It receives the dialog details and an optional browser session id. It reads the dialog type and message, decides whether to accept it or dismiss it, appends a human-readable note to the session’s dialog list, and starts a background task to send the answer to the browser. It returns nothing directly.

**Call relations**: This is the front door for dialog events in this file. When called, it uses the dialog data to decide the response, then calls BrowserDialogs._answer_dialog in the background through the session’s spawn_background hook so the page is unfrozen as quickly as possible.

*Call graph*: calls 1 internal fn (_answer_dialog); 1 external calls (get).


##### `BrowserDialogs._answer_dialog`  (lines 51–57)

```
async def _answer_dialog(self, session_id: str | None, accept: bool) -> None
```

**Purpose**: This function sends the actual browser command that accepts or dismisses the open JavaScript dialog. It is separated from on_dialog so it can run asynchronously in the background.

**Data flow**: It receives the target session id and the chosen answer, true for accept or false for dismiss. It asks the browser connection to send the Page.handleJavaScriptDialog command with that answer. If the command fails because of a CDP error, timeout, or runtime problem, it logs a warning and does not raise the error further.

**Call relations**: BrowserDialogs.on_dialog schedules this function after deciding what should happen. This function then gets the session’s browser connection and sends the CDP command that actually clears the blocking pop-up.

*Call graph*: called by 1 (on_dialog).


### Download retrieval
Detects browser downloads, waits for completion, returns downloaded bytes, and forces PDFs into downloadable form instead of Chrome's built-in viewer.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download waiting during browser automation`

When an automated browser clicks a link, the result may be a normal web page, a file download, or a PDF that Chrome tries to display inside its own viewer. This file is the download helper for that situation. Without it, the system could miss downloaded files, hang a page by failing to release paused browser requests, or get stuck looking at a PDF viewer that the agent cannot read well.

The main piece is BrowserDownloads. It listens to browser events sent through Chrome DevTools Protocol, a control channel that lets code inspect and steer Chrome. When Chrome pauses a network response, BrowserDownloads always lets it continue so the page does not freeze. But if the paused response is a top-level document with a PDF content type, it changes the response header to say “attachment,” which tells Chrome to save the file instead of showing it inline.

The file also keeps a small in-memory list of downloads. A download starts with a unique browser id, a suggested filename, and an initial state. Later progress events update that state. The wait method polls until a download is complete, then reads the downloaded file from disk and returns its filename, size, and content encoded as base64, which is a text-safe way to carry raw bytes in JSON.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 43–49)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the expected shape of the object that can send commands to Chrome DevTools Protocol, the browser control channel. Code in this file uses it to tell Chrome to continue paused network requests or responses.

**Data flow**: It receives a command name, optional command details, and optionally a browser session id. It sends that instruction to the browser and returns the browser’s JSON-like reply.

**Call relations**: BrowserDownloads gets this sender through BrowserDownloadSession.connection. The concrete implementation lives elsewhere; this file only states what abilities it must provide.


##### `BrowserDownloadSession.connection`  (lines 55–55)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This is the expected way for the download helper to reach the browser control connection. It exists so BrowserDownloads does not need to know the concrete browser session class.

**Data flow**: It takes no extra data beyond the session object. It returns an object that can send Chrome DevTools Protocol commands.

**Call relations**: BrowserDownloads calls this when it needs to release a paused request or response. The actual session object supplies the real connection.


##### `BrowserDownloadSession.spawn_background`  (lines 57–57)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This is the expected way to start a small asynchronous task without blocking the current browser event callback. It matters because paused browser requests must be released promptly, but event handling should stay lightweight.

**Data flow**: It receives a coroutine, which is a piece of async work waiting to run. It schedules that work in the background and does not return a result to this file.

**Call relations**: BrowserDownloads.on_fetch_paused uses it to run _continue_request or _continue_response. The surrounding browser session decides exactly how background tasks are scheduled.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 59–59)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This is the expected check for whether a browser event belongs to the main page frame rather than an embedded frame. The distinction matters because only a main-page PDF navigation should be forced into a download.

**Data flow**: It receives a browser session id and a frame id from an event. It returns true when that frame represents the main page, and false for subframes or unrelated frames.

**Call relations**: BrowserDownloads.on_fetch_paused calls this before changing PDF responses. That prevents embedded PDFs from being accidentally turned into downloads when the page expected to display them.


##### `BrowserDownloads.on_fetch_paused`  (lines 67–88)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This reacts when Chrome pauses a network request or response. Its job is to make sure the browser is always allowed to continue, while selectively turning top-level PDFs into real downloads.

**Data flow**: It receives the paused-event details and an optional browser session id. It checks for a request id, whether this is a response-stage pause, the response headers, the frame, and the content type. It then schedules either a plain request continue or a response continue, possibly with a changed header that makes Chrome download the file.

**Call relations**: This is called by the browser event layer when Fetch pause events arrive. It uses _content_type to understand headers, asks the session whether the frame is top-level, and hands the actual browser command work to _continue_request or _continue_response in the background.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 90–96)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This releases a paused browser request when there is no response information to inspect yet. It keeps the page from hanging on an intercepted request.

**Data flow**: It receives the browser session id and the paused request id. It sends Chrome a Fetch.continueRequest command. If Chrome rejects the command or the connection fails, it logs a warning rather than crashing the whole browser flow.

**Call relations**: on_fetch_paused schedules this when Chrome paused before a response code is available. It uses the session’s connection to talk to Chrome.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 98–126)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This releases a paused browser response, optionally rewriting it so Chrome treats it as a download. It is the point where a PDF response can be changed from “show in browser” to “save as file.”

**Data flow**: It receives the session id, request id, HTTP response code, response headers, and a force flag. If forcing is requested, it removes any existing Content-Disposition header and adds Content-Disposition: attachment. It then sends Chrome a Fetch.continueResponse command. Failures are logged as warnings.

**Call relations**: on_fetch_paused schedules this after deciding whether the response should pass through untouched or be forced into a download. It sends the final instruction through the browser control connection.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 128–135)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This records that Chrome has started a new download. It gives the rest of the system something to watch while the file is being saved.

**Data flow**: It receives download-start event details, reads the browser’s unique download id and suggested filename, and appends a new Download record with state set to inProgress. If the browser does not provide a filename, it uses “download.”

**Call relations**: This is called by the browser event layer when a download begins. Later, on_download_progress updates the same record, and wait looks for records that have reached the completed state.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 137–142)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This updates the saved state of a download as Chrome reports progress. It lets the waiting code know when a file has finished.

**Data flow**: It receives a progress event, reads the download id and new state, then searches the stored downloads for the matching id. When it finds it, it replaces that download’s state with the new state.

**Call relations**: This is called after on_download_begin has created a record. BrowserDownloads.wait depends on these updates to know when it can safely read the downloaded file.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 144–150)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This briefly checks whether an action that looked like navigation actually turned into a download. It gives the browser a short grace period to emit the download-start event.

**Data flow**: It receives the number of downloads that existed before an action. For up to a small fixed time, it checks whether the download list has grown. It returns true if a new download appears, otherwise false.

**Call relations**: This is meant to be used after a navigation-like browser action. It watches the same download list filled by on_download_begin and waits using short sleeps so it does not block the event loop.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 152–170)

```
async def wait(self, args: JsonDict, download_dir: str) -> JsonDict
```

**Purpose**: This waits for a browser download to finish and returns the finished file in a JSON-friendly form. It is the main “give me the downloaded file” operation.

**Data flow**: It receives arguments that may include a timeout and the download directory path. It turns the timeout into a number, polls the tracked downloads until one is completed or time runs out, then reads the completed file from disk using the browser’s download id as the filename. It returns the suggested filename, the file size, and the file bytes encoded as base64 text. If nothing finishes in time, it raises TimeoutError.

**Call relations**: This depends on on_download_begin and on_download_progress having filled and updated the download list. It calls float_or_default to understand the timeout value, then reads the file in a worker thread so disk I/O does not stall the async browser loop.

*Call graph*: calls 1 internal fn (float_or_default); 6 external calls (sleep, to_thread, b64encode, Path, monotonic, get).


##### `float_or_default`  (lines 173–182)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This turns a user-provided timeout value into a floating-point number, or uses a default when no value was provided. It keeps timeout parsing consistent and rejects values that are not numeric.

**Data flow**: It receives a JSON-like value and a default number. If the value is an integer, float, or non-empty string, it converts it to a float. If the value is missing, it returns the default. For other kinds of values, it raises a validation error.

**Call relations**: BrowserDownloads.wait calls this before polling for a completed download. That means bad timeout inputs are caught before the wait loop starts.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 185–189)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This extracts the main media type from HTTP headers, such as turning “application/pdf; charset=utf-8” into “application/pdf.” It helps decide whether a response is a PDF that should be forced to download.

**Data flow**: It receives a list of header-like JSON values. It searches for a header named Content-Type, ignoring letter case, strips off any extra semicolon options, lowercases the result, and returns it. If no content type is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused uses this while inspecting paused responses. Its result is compared with the forced-download types before _continue_response is scheduled.

*Call graph*: called by 1 (on_fetch_paused).


### Form interaction
Fills page forms and uploads files by validating target elements and issuing the necessary Chrome DevTools Protocol commands.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not change just because an automation tool knows which field it wants to edit. The tool must find the exact page element, talk to the browser in the browser’s own control language, and trigger the same events a real user action would trigger. This file is the small bridge that does that for forms.

The main class, BrowserForms, receives a browser session object. That session knows how to get the current tab, resolve a saved page reference into a real browser node, and send commands through CDP, the Chrome DevTools Protocol. CDP is the control channel Chrome exposes for tools that inspect or drive pages.

For file uploads, the file resolves a stored element reference, then asks Chrome to attach local file paths to that input element. If the reference is not actually a file input, it raises a HallucinationError. In this project, that means the caller asked for something that does not match the real page, so it should read the page again and use a valid reference.

For normal input, it resolves the page element into a JavaScript object, then runs a short JavaScript function on it. That function knows about checkboxes, radio buttons, select menus, editable text areas, and ordinary fields. After setting the value, it fires input and change events, which is important because modern websites often listen for those events before they notice a field changed.

#### Function details

##### `BrowserFormSession.page`  (lines 35–35)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It provides the page or tab that form actions should work against.

**Data flow**: It receives an optional tab number. The implementing browser session uses that to find the right page, then returns the page object that later steps can use to resolve element references.

**Call relations**: BrowserForms.upload_file and BrowserForms.input rely on this ability at the start of their work. This file only states that the method must exist; the real behavior is supplied by whatever browser session object is passed in.


##### `BrowserFormSession.connection`  (lines 37–37)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It gives access to the browser control connection used to send Chrome DevTools Protocol commands.

**Data flow**: It takes no extra input. The implementing session returns a CDP connection object, which can send low-level commands to the browser and receive results or errors.

**Call relations**: BrowserForms.upload_file uses this connection to set files on an upload field. BrowserForms.input uses it to turn a page node into a JavaScript object before setting its value.


##### `BrowserFormSession.call_on`  (lines 39–45)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It runs a JavaScript function on a specific object inside the page.

**Data flow**: It receives a browser session id, a JavaScript object id, the JavaScript function text, and optional argument values. The browser session sends that request into the page and returns the result as a JSON-like dictionary.

**Call relations**: BrowserForms.input calls this after it has resolved a form element into a JavaScript object. It hands off the small JS_FORM_INPUT script so the page field is changed in a way the website can detect.


##### `BrowserFormSession.resolve_ref`  (lines 47–47)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It translates a saved page reference, such as one returned by a page-reading step, into the real browser node needed for automation.

**Data flow**: It receives a page object and a reference string. The implementing session looks up that reference and returns two things: the node information, including its browser session id, and the backend node id that Chrome uses internally.

**Call relations**: Both BrowserForms.upload_file and BrowserForms.input call this before touching a form element. Without this translation step, the code would only have a human-facing reference and could not tell Chrome which exact element to edit.


##### `BrowserForms.upload_file`  (lines 54–71)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This function attaches one or more local files to a file-upload field on a web page. It exists because file inputs cannot be filled like ordinary text fields; the browser must be told directly which files to use.

**Data flow**: It reads the requested tab id, element reference, and file paths from the input dictionary. It normalizes the tab id, checks that the reference is a string, checks that the files value is a list, and turns every file path into a string. Then it asks the browser session to resolve the element reference and sends Chrome a DOM.setFileInputFiles command. On success, it returns the reference and the file list it used. If Chrome rejects the element, it raises a clear error telling the caller to use a real file input reference.

**Call relations**: This is called when an outside request wants to upload files through the browser. It first uses _tab_id to understand the optional tab value, then uses the browser session to find the target element and the CDP connection to perform the upload. If the request was based on an invalid or stale page reference, it stops and reports a HallucinationError instead of pretending the upload worked.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 73–88)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This function sets the value of a form field, such as a text box, checkbox, radio button, select menu, or editable page area. It changes the field in the page and triggers the events that websites normally expect after user input.

**Data flow**: It reads the optional tab id, the target element reference, and the value to enter from the input dictionary. It finds the requested page, turns the stored reference into a real browser node, and asks Chrome to resolve that node into a JavaScript object. It then runs the JS_FORM_INPUT script on that object with the requested value. The result is whatever the browser returns from that script, usually including the field’s resulting value. If the reference cannot be resolved, it raises a clear error telling the caller to re-read the page and use a current reference.

**Call relations**: This is the normal form-filling path. It uses _tab_id at the start, then depends on the browser session to locate the page element and the CDP connection to prepare it for JavaScript execution. Finally, it hands off to BrowserFormSession.call_on so the JavaScript value-setting code runs inside the actual web page.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 91–100)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This helper turns a tab identifier from loose JSON input into either an integer tab id or no tab id at all. It makes the public form methods tolerant of common input shapes, such as numbers or numeric strings.

**Data flow**: It receives a JSON value that may be an integer, float, string, empty value, or missing value. Integers pass through unchanged, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes None. The result is used as the tab selector when asking the browser for a page.

**Call relations**: BrowserForms.upload_file and BrowserForms.input call this before they ask the browser session for a page. It keeps tab-id cleanup in one place so both form actions interpret tab input the same way.

*Call graph*: called by 2 (input, upload_file).

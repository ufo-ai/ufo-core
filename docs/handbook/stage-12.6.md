# BUA action execution, input, forms, and downloads  `stage-12.6`

This stage is the action layer for browser automation. It sits in the main work loop, between an agent’s plain request, such as “click this button” or “download that PDF,” and the low-level messages Chrome needs to receive. The actions file defines the menu of allowed requests and what information each one must carry, like a standard order form. Before an order is sent, fixup cleans up small missing details, such as adding a default wait time or making sure a field is focused before typing. Computer then performs the action by sending real browser input events for clicks, typing, scrolling, screenshots, and waits. Keys handles the tricky keyboard details, including modifier keys like Ctrl or Shift and Mac-style shortcuts, so fake typing behaves like real typing. Forms provides safer helpers for filling fields and uploading files, with clear errors when a page element cannot be used. Downloads watches for files, forces download behavior when needed, and waits until the automation agent can access the saved file.

## Files in this stage

### Action execution and normalization
Core browser actions are executed, cleaned up, and validated against the shared action definitions.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the browser “hands and eyes” layer. An outside caller sends a batch of planned actions in model-style coordinates, and this code applies them to the current browser tab through Chrome DevTools Protocol, a control channel that lets software drive Chrome. Without this file, the agent could read or plan, but it could not reliably click buttons, type into fields, drag items, scroll pages, or return a fresh screenshot afterward.

The main class, BrowserComputer, first validates and adjusts the requested actions so they fit the real browser viewport. It then runs them in batches, stopping at waits so the page has time to settle after changes. Each action is translated into lower-level browser commands: mouse movements, presses, releases, wheel events, keyboard events, or DOM calls. After the actions, it waits for the page to become stable, checks for downloads and dialogs, captures a screenshot, and returns a human-readable summary.

A few safety and usability touches matter here. Repeated scrolling triggers a reminder to use page-reading tools instead. Pages whose tab titles look like sign-in screens produce a warning. Clicking a native select dropdown gives special advice because normal clicking cannot choose its options in this browser setup. The screenshot can also be marked with a small blue dot showing the last click, like leaving a sticker on a map.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This defines the promise that a browser session can provide the tab where actions should happen. A real session class must supply the active tab, or a specific tab when asked.

**Data flow**: It receives an optional tab number → the session looks up or chooses the matching browser tab → it returns a tab object with a session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this session method at the start of an action batch so it knows which tab to operate on. The method is declared here as part of the expected shape of a browser session, while the actual work is done by another class.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This defines how BrowserComputer gets the browser control connection. That connection is used to send Chrome DevTools Protocol commands, which are remote-control messages for the browser.

**Data flow**: It takes no extra input → the session provides its current browser connection → callers receive an object that can send commands to Chrome.

**Call relations**: BrowserComputer uses this whenever it needs to capture screenshots, send mouse or keyboard input, scroll an element into view, or wait for the page to settle. This protocol method keeps BrowserComputer independent from the concrete connection implementation.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This defines how to collect current tab details for the response returned after actions finish. It gives the caller context such as which tab is active or what page is being viewed.

**Data flow**: It receives a tab object → the session gathers descriptive information about that tab → it returns a JSON-style dictionary to merge into the final result.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the response reflects the browser’s current state. The concrete browser session decides exactly what tab information is included.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This defines how to read the titles of open tabs. The titles are used as a simple safety signal for pages that may involve signing in.

**Data flow**: It takes no extra input → the session reads tab titles from the browser → it returns a list of title strings.

**Call relations**: BrowserComputer.run asks for tab titles after acting, then passes them to sign_in_warning. This lets the action response include a reminder when the browser appears to be on a login or signup page.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This defines how to run a small JavaScript function against a specific browser object. BrowserComputer uses it when it needs information from an element on the page, such as the options inside a dropdown.

**Data flow**: It receives a session id, an object id, JavaScript code, and optional arguments → the browser session runs that code against the object → it returns the result as a JSON-style dictionary.

**Call relations**: BrowserComputer._select_reminder uses this after finding a select element under a click point. The protocol keeps the element-inspection detail outside BrowserComputer while still letting BrowserComputer ask targeted questions.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This defines how to turn a page reference, such as a short id returned by a page-reading tool, into a browser node. It is needed when an action targets an element by reference instead of by coordinates.

**Data flow**: It receives a tab and a reference string → the session looks up the real browser node behind that reference → it returns the node and its backend browser id.

**Call relations**: BrowserComputer.act uses this for scroll_to actions. If a caller asks to scroll to a referenced element, this method is the bridge from the readable reference to the browser’s internal element id.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This defines how to find the screen point for a referenced page element. It lets an action say “click this known element” instead of supplying raw x and y coordinates.

**Data flow**: It receives a tab and a reference string → the session locates the element and chooses a point on it → it returns viewport x and y coordinates.

**Call relations**: BrowserComputer.point calls this when an action includes a ref. That point then flows into BrowserComputer.act, which can click, drag to, or scroll at that location.


##### `BrowserComputer.run`  (lines 97–166)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for applying a batch of browser actions. It validates the request, performs the actions, waits for the page, captures the result, and returns a summary plus screenshot.

**Data flow**: It receives a JSON-style request containing a tab id and actions → it chooses the tab, validates and adjusts actions, performs them batch by batch, gathers warnings, downloads, dialogs, tab info, and a screenshot → it returns a JSON-style response with output text, last click location, tab details, and screenshot data.

**Call relations**: This function is the conductor for the file. It calls act for each action, asks _select_reminder for special dropdown advice after clicks, uses _to_model for reporting coordinates, uses int_or_none for the tab id, and calls sign_in_warning before building the final response.

*Call graph*: calls 6 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning, __init__); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 168–247)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one browser action, such as a click, drag, type, key press, wait, scroll, scroll-to-element, or screenshot marker action. It turns a human-sized instruction into the exact browser operation needed.

**Data flow**: It receives the target tab and one validated action → it resolves any target point, chooses the matching behavior, sends input or browser commands, and builds a short message → it returns that message and, when relevant, the viewport point that was clicked or dragged to.

**Call relations**: BrowserComputer.run calls this for every action in a batch. act delegates the physical input details to _click, _drag, _scroll, and _dispatch, uses point to resolve coordinates, and uses require_point or require_coord when an action is missing information it cannot work without.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 249–254)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds the on-screen point an action should use. It supports both element references and explicit model coordinates.

**Data flow**: It receives a tab and an action → if the action names a page reference, it asks the browser session for that element’s point; if the action has coordinates, it converts them to viewport coordinates → it returns the point, or nothing if the action has no point target.

**Call relations**: BrowserComputer.act calls this before deciding how to perform most actions. It passes coordinate conversion to _to_viewport when the input comes from the model coordinate space.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 256–258)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the model’s virtual screen size into the real browser viewport size. It keeps clicks accurate even when the model and browser use different dimensions.

**Data flow**: It receives an x and y coordinate in model space → it scales that point using the model size and viewport size → it returns x and y in viewport space.

**Call relations**: BrowserComputer.point uses this for coordinate-based actions, and BrowserComputer.act uses it when calculating drag starts or default scroll positions. It is one half of the coordinate translation pair in this file.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 260–262)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts real browser viewport coordinates back into the model’s coordinate system. It is mainly used so responses can report positions in the same terms the caller understands.

**Data flow**: It receives an x and y coordinate from the browser viewport → it scales that point back to the model size → it returns model-space x and y.

**Call relations**: BrowserComputer.act uses this when writing click and drag messages, and BrowserComputer.run uses it for the last_click field in the final response. It mirrors _to_viewport.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 264–293)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click landed on a native HTML select dropdown and, if so, prepares a helpful reminder. Native select menus need a different tool here because clicking their options does not work reliably through this browser driver.

**Data flow**: It receives the tab and clicked viewport point → it asks the browser what element is under that point, walks up to a select element if one exists, reads its option text and browser reference → it returns a reminder string, or nothing if there is no useful select element.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses select_reminder to turn the raw dropdown details into user-facing advice, while browser session calls provide the page inspection data.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 295–297)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared series of keyboard-related browser commands. It is used for typing text and pressing key combinations.

**Data flow**: It receives a tab and a list of low-level browser calls → it sends each call to the browser connection for that tab → it returns nothing after the browser has received them.

**Call relations**: BrowserComputer.act calls this for type and key actions after helper code has translated text or key combos into Chrome DevTools Protocol calls. It is the final delivery step for keyboard input.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 299–302)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one low-level mouse event to the browser. It is the common doorway for moving the mouse, pressing buttons, releasing buttons, and wheel scrolling.

**Data flow**: It receives a tab and a dictionary of mouse event details → it sends an Input.dispatchMouseEvent command to the browser connection → it returns nothing once the command is sent.

**Call relations**: _click, _drag, and _scroll all call this so they do not each repeat the browser-send logic. It is the small transport helper underneath every mouse action in this file.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 304–343)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a left, right, double, or triple click at a specific browser viewport point. It sends the same sequence a real mouse would: move, press, release.

**Data flow**: It receives a tab, x and y coordinates, a mouse button name, and a click count → it reads the current keyboard modifier keys, moves the mouse, then sends press and release events the requested number of times → it changes the page through browser input and returns nothing.

**Call relations**: BrowserComputer.act calls this for click actions. _click uses _mouse_event for each browser event and includes modifier-key state so clicks with Shift, Control, Command, or similar keys behave correctly.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 345–398)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop after seeing movement along the way.

**Data flow**: It receives a tab, start coordinates, and end coordinates → it reads modifier-key state, moves to the start, presses the left button, sends several intermediate move events, then releases at the end → the page sees a realistic drag gesture and the function returns nothing.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. _drag relies on _mouse_event for each step, acting like a careful hand moving an item rather than teleporting it.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 400–424)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This scrolls the page at a chosen point using a mouse wheel event. It supports horizontal and vertical movement by sending wheel deltas to the browser.

**Data flow**: It receives a tab, x and y coordinates, and horizontal and vertical scroll amounts → it reads modifier-key state, moves the mouse to the target point, then sends a wheel event → the page scrolls and the function returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after it has chosen the target point and calculated how far to move. _scroll uses _mouse_event for both the initial positioning and the wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 427–431)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser is on a login or signup page and returns a safety reminder. It helps prevent the agent from signing in or creating accounts without user confirmation.

**Data flow**: It receives a list of tab titles → it lowercases them and checks for sign-in-related words → it returns the warning text if any title matches, otherwise nothing.

**Call relations**: BrowserComputer.run calls this while building the final response. Its result may be added as a system reminder alongside scroll, download, dialog, and dropdown reminders.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 434–446)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This creates the message shown after the user clicks a native select dropdown. The message explains the correct way to choose an option and shows the available option text when possible.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total number of options → it formats a short instruction and option preview → it returns the complete reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after inspecting the clicked select element. This function only writes the user-facing explanation; the browser inspection happens before it.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 449–465)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This adds a small blue dot to a screenshot at the last clicked point. It helps the caller visually confirm where the browser action landed.

**Data flow**: It receives a base64-encoded screenshot and a viewport point → it decodes the image, draws a translucent circle over the point, saves it again as JPEG, and base64-encodes it → it returns the marked screenshot string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click-like action. It is run in a background executor so image processing does not block the main asynchronous browser flow.

*Call graph*: 7 external calls (Draw, b64decode, b64encode, alpha_composite, new, open, BytesIO).


##### `int_or_none`  (lines 468–477)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This safely converts a possible tab id into an integer, or returns nothing when there is no usable value. It accepts common JSON forms such as numbers and non-empty strings.

**Data flow**: It receives a JSON value or nothing → if the value is an int, float, or non-empty string, it converts it to an int; otherwise it treats it as absent → it returns an int or None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page. It lets callers pass tab ids in flexible JSON-friendly forms without spreading that conversion logic through the main flow.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 480–483)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This checks that an action which needs a target point actually has one. If not, it raises a clear validation error instead of letting the action fail later in a confusing way.

**Data flow**: It receives a possible point and the action name → if the point exists, it passes it through unchanged; if it is missing, it raises an error explaining that the action needs a coordinate or reference → the caller either gets a point or the batch stops with a useful message.

**Call relations**: BrowserComputer.act uses this before click, right-click, and drag-end operations. It protects the lower-level mouse functions from being called with missing coordinates.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 486–489)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This checks that a required coordinate field is present. It is used when an action has a separate required coordinate, such as the start of a drag.

**Data flow**: It receives a possible coordinate and the field name → if the coordinate exists, it returns it; if it is missing, it raises a validation error naming the missing field → the caller either continues with a real coordinate or gets a clear failure.

**Call relations**: BrowserComputer.act uses this for drag actions before converting the start coordinate to viewport space. Like require_point, it keeps error reporting close to the user-facing action rather than deep inside mouse input code.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `action preprocessing before browser dispatch`

This file acts like a careful assistant checking a to-do list before handing it to the browser. The browser action system receives batches of actions such as clicks, typing, scrolling, and waiting. Some action lists can be incomplete or slightly awkward, especially if they came from a model that guessed what to do. Without this cleanup step, typing might go nowhere because no field was focused, a scroll might fail because it has no starting point, or a wait might not wait for any useful amount of time.

The main idea is to turn each requested action into one or more “planned” actions. A planned action keeps the original action number from the caller. That matters because this file may insert an extra action, such as a click before typing. If something later fails, the system can still report the failure using the caller’s original numbering, not the expanded internal list.

The repairs are practical and conservative. Typing at a coordinate or reference gets a focus click first if there was not already a click. Text like "\\n" and "\\t" is turned into real newline and tab characters. Scrolls without a coordinate are aimed at the center of the model’s view. A missing wait duration becomes a default three-second wait. After planning, another helper can split the list into smaller batches whenever a wait appears, so the browser has time to settle before the next actions run.

#### Function details

##### `fixup_actions`  (lines 26–70)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[PlannedAction]
```

**Purpose**: This is the main cleanup function for a batch of browser actions. It repairs common missing details and records which original action each repaired or inserted action belongs to.

**Data flow**: It takes a list of requested browser actions, the current viewport size, and optionally the size used by the model. It first works out the effective model size and the center point of that space. Then it walks through the actions one by one, sometimes leaving an action alone, sometimes changing it, and sometimes inserting an extra action first. It returns a new list of PlannedAction objects, where each item contains the repaired action and the original caller index it came from.

**Call relations**: This function is the place where the smaller helpers are used. When it sees typing that needs focus, it asks _focus_click to build a click action. When it sees typed text that may contain literal escape sequences, it asks _unescape_text to turn those into real characters. It also creates replacement actions directly when it needs default scroll, scroll-to, wait, or click behavior.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 4 external calls (__init__, __init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 73–84)

```
def split_at_waits(planned: list[PlannedAction]) -> list[list[PlannedAction]]
```

**Purpose**: This function divides a planned action list into smaller groups, ending each group after a wait action. This lets the browser pause and settle before later actions continue.

**Data flow**: It takes the already planned actions as input. It builds a current group, adds actions to it in order, and whenever it sees a wait action it closes that group and starts a new one. At the end, any remaining actions become the final group. The result is a list of batches, each batch preserving the original order.

**Call relations**: This function is meant to run after fixup_actions has prepared the actions. It does not change individual actions; it only decides where the action stream should be paused and resumed around waits.


##### `_focus_click`  (lines 87–90)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper creates a simple left-click action that focuses the same target a typing action is about to use. It exists so typing is more likely to land in the intended field.

**Data flow**: It receives a typing action that has either a coordinate or a reference to a page element. If there is a coordinate, it creates a left click at that coordinate. Otherwise it creates a left click using the same reference. The output is the new click action; the original action is not changed.

**Call relations**: fixup_actions calls this helper when it finds a type action that points at a target but was not preceded by a click. The click it returns is inserted before the typing action, using the same original action number so later error reporting still points back to the caller’s request.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 93–99)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns visible escape text such as "\\n" and "\\t" into real newline and tab characters before typing. This makes model-produced text behave like the user intended.

**Data flow**: It receives a typing action and reads its text, treating missing text as an empty string. If the text does not contain the known escaped forms, it returns the same action unchanged. If it does contain them, it replaces those literal character pairs with the actual special characters and returns a copied action with the corrected text.

**Call relations**: fixup_actions calls this helper for every type action after any needed focus click has been added. The corrected action then goes into the planned action list that will later be dispatched to the browser.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is a small but important rulebook for browser control. When another part of the system wants to drive a browser, it needs a clear way to say “click here,” “type this,” or “scroll down.” Without a shared action format, different parts of the system could disagree about names, coordinates, scroll amounts, or required details, which would make browser automation unreliable.

The file uses Pydantic models, which are Python classes that describe and check structured data. In plain terms, they work like forms with built-in validation: if someone fills in an impossible value, such as a wait time that is too long or a scroll direction that is not allowed, the model can reject it.

`ActionType` lists every action name the system recognizes. `CLICK_ACTIONS` groups the actions that behave like mouse clicks. `ScrollParameters` describes how scrolling works: a direction and an amount, where the amount can be a number of screen-heights or the special value `max` for jumping far across the page. `ComputerAction` is the main action shape. It can carry a target coordinate, text to type, scroll settings, a wait duration, drag start and end points, or a page element reference such as `e5`.

A useful way to think about this file is as a menu for a remote-control browser: it lists the buttons available and the extra details each button needs.


### Download handling
Download utilities recognize, force, track, and wait for browser downloads, including files Chrome might otherwise preview.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling`

This file solves a practical browser automation problem: a page may navigate to a file, but Chrome might display it inline instead of downloading it. For an automated agent, that can be useless, because the browser’s built-in viewer may not expose the content in a readable way. So this code watches browser network events and download events, then gently nudges certain responses into becoming real downloads.

The central piece is `BrowserDownloads`. It listens for paused browser requests from Chrome’s DevTools Protocol, often shortened to CDP, which is a remote-control interface for Chrome. Every paused request must be released, like opening a gate after briefly checking who is coming through; otherwise the page freezes. Most requests are simply allowed to continue. But if the response is a top-level document with a content type such as `application/pdf`, the code rewrites the response headers to say “treat this as an attachment,” which makes Chrome download it.

The file also records download start and progress events. It stores each download’s browser-provided id, suggested filename, and current state. Other code can ask whether a recent navigation turned into a download, or wait until a download has completed. This file does not read downloaded bytes from disk; it only identifies and tracks the browser-side download.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 41–47)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This is the promised shape of an object that can send commands to Chrome through CDP, Chrome’s remote-control protocol. Code in this file relies on this ability to release paused requests and responses.

**Data flow**: It receives a CDP method name, optional parameters, and optionally a browser session id. An implementation sends that command to Chrome and returns Chrome’s JSON-like reply.

**Call relations**: The continuation helpers in `BrowserDownloads` use the browser session’s connection to call this method. In the bigger flow, `on_fetch_paused` decides what should happen, then the helper sends the actual command through this interface.


##### `BrowserDownloadSession.connection`  (lines 53–53)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This describes how the download code gets access to the browser connection that can talk to Chrome. It keeps `BrowserDownloads` independent from the concrete browser implementation.

**Data flow**: It takes no extra data beyond the session object. It returns an object capable of sending CDP commands.

**Call relations**: `BrowserDownloads._continue_request` and `BrowserDownloads._continue_response` call this when they need to tell Chrome to resume a paused network operation. The session supplies the transport; this file supplies the download-specific decisions.


##### `BrowserDownloadSession.spawn_background`  (lines 55–55)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This describes how the download code starts small asynchronous tasks without blocking the current event callback. It matters because paused browser requests must be released quickly, but the release command itself is asynchronous.

**Data flow**: It receives a coroutine, which is a piece of asynchronous work waiting to be run. The session schedules it to run in the background and does not return a useful value.

**Call relations**: `BrowserDownloads.on_fetch_paused` uses this to launch `_continue_request` or `_continue_response`. The event handler makes the decision immediately, then hands off the actual browser command so the event stream can keep moving.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 57–57)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This describes how the download code asks whether a browser event belongs to the main page frame, rather than an embedded frame such as an iframe. That distinction prevents embedded PDFs from being wrongly forced into downloads.

**Data flow**: It receives a browser session id and a frame id from a browser event. It returns true if that frame is the main page frame, and false otherwise.

**Call relations**: `BrowserDownloads.on_fetch_paused` calls this before forcing a PDF-like response to download. The check keeps the intervention narrow: only main-page navigations are changed, while embedded documents are allowed to render normally.


##### `BrowserDownloads.on_fetch_paused`  (lines 65–86)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function reacts when Chrome pauses a network request or response for inspection. It decides whether to simply let it continue, or to rewrite a top-level PDF response so Chrome downloads it instead of opening it in the built-in viewer.

**Data flow**: It receives the event details from Chrome and an optional session id. It reads the request id, response status, response headers, content type, and frame information. If the event is not a response yet, it schedules a normal request continuation. If it is a response, it checks whether it is a top-level forced-download type, then schedules a response continuation with or without header rewriting. It returns nothing directly; its effect is to unblock Chrome in the background.

**Call relations**: This is the main decision point for paused fetch events. It calls `_content_type` to understand the response headers, asks the browser session whether the frame is top-level, and then uses `spawn_background` to hand off either `_continue_request` or `_continue_response`. Those helpers send the actual CDP commands.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 88–94)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This asynchronous helper tells Chrome to continue a paused request when there is no response to inspect yet. Without this, the browser could hang waiting for permission to proceed.

**Data flow**: It receives a session id and request id. It sends a `Fetch.continueRequest` command through the browser connection. If Chrome rejects the command, times out, or the connection is not usable, it logs a warning instead of crashing the whole flow.

**Call relations**: `on_fetch_paused` schedules this helper when the paused event is still at the request stage. It is the simple “open the gate and let it through” path.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 96–124)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This asynchronous helper tells Chrome to continue a paused response, optionally changing its headers so Chrome treats it as a file download. It is where the decision made by `on_fetch_paused` becomes an actual browser command.

**Data flow**: It receives a session id, request id, response status code, response headers, and a yes-or-no `force` flag. If `force` is false, it simply tells Chrome to continue the response. If `force` is true, it removes any existing `Content-Disposition` header and adds `Content-Disposition: attachment`, then sends the modified response continuation command. On CDP errors, timeouts, or runtime failures, it logs a warning.

**Call relations**: `on_fetch_paused` schedules this after inspecting a paused response. This helper then talks to Chrome through the session connection, using the force flag to decide whether to preserve the response or turn it into a download.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 126–133)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function records that Chrome has started a new download. It creates a small download record so later code can wait for it and report its id and filename.

**Data flow**: It receives Chrome’s download-start event details and a session id. It reads the download guid and suggested filename, falls back to `download` if no filename is given, marks the state as `inProgress`, and appends the new record to the browser session’s download list.

**Call relations**: Chrome download events call into this when a download begins. Later, `on_download_progress`, `became_download`, and `wait` all rely on the record this function added.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 135–140)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This function updates the stored state of a download as Chrome reports progress. It lets the rest of the system know when a download has completed.

**Data flow**: It receives Chrome’s progress event details and a session id. It reads the download guid and new state, finds the matching stored download, and changes that record’s state. It returns nothing.

**Call relations**: After `on_download_begin` has added a download record, Chrome progress events call this to keep that record current. `wait` later looks for records whose state has become `completed`.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 142–148)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This function briefly checks whether an action that just happened caused a new download to appear. It is useful after a navigation or click, where the browser might either load a page or start downloading a file.

**Data flow**: It receives the number of downloads that existed before the action. For up to a short grace period, it repeatedly checks whether the download list has grown. It returns true if a new download appears, or false if none appears in time.

**Call relations**: Other navigation or action code can call this after doing something that may become a download. It does not start or finish downloads itself; it watches the list that `on_download_begin` fills.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 150–165)

```
async def wait(self, args: JsonDict) -> BrowserDownload
```

**Purpose**: This function waits until at least one browser download has completed, then returns the most recent completed download record. It gives callers a clean way to know which file Chrome finished downloading.

**Data flow**: It receives arguments that may include a timeout. It turns that timeout into a number using `float_or_default`, then repeatedly checks the stored downloads for ones marked `completed`. If one appears before the deadline, it returns the last completed record. If time runs out, it raises a timeout error. It does not read the file contents.

**Call relations**: Callers use this when they need to wait for a download result. It depends on `on_download_begin` to create records and `on_download_progress` to mark them completed. It delegates timeout parsing to `float_or_default`.

*Call graph*: calls 1 internal fn (float_or_default); 3 external calls (sleep, monotonic, get).


##### `float_or_default`  (lines 168–177)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns a user-provided timeout value into a floating-point number, or uses a default when no value was provided. It gives `wait` a small, predictable input check.

**Data flow**: It receives a JSON-like value and a default number. If the value is an integer, float, or non-empty string, it converts it to a float. If the value is missing, it returns the default. If the value is some other kind of data, it raises a validation error saying the value must be numeric.

**Call relations**: `BrowserDownloads.wait` calls this before starting its waiting loop. That keeps the waiting logic focused on downloads while this helper handles the small job of interpreting the timeout value.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 180–184)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper extracts the main content type from HTTP response headers, such as turning `application/pdf; charset=utf-8` into `application/pdf`. The download decision uses this to recognize file types that should be forced to download.

**Data flow**: It receives a list of JSON-like header values. It searches for a dictionary whose name is `content-type`, ignoring letter case. If found, it takes the value, removes anything after a semicolon, trims spaces, lowercases it, and returns it. If no content type is found, it returns an empty string.

**Call relations**: `BrowserDownloads.on_fetch_paused` calls this while inspecting a paused response. Its result is compared with the forced-download type list to decide whether `_continue_response` should add an attachment header.

*Call graph*: called by 1 (on_fetch_paused).


### Form filling and uploads
Form helpers safely fill page fields and attach files while reporting clear errors for unusable elements.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not always react if a program only changes a field’s stored value. Many sites listen for browser events, such as “input” or “change,” before they enable buttons or save form data. This file solves that problem by setting form values inside the browser and then firing the same events a real user action would trigger.

The main class, BrowserForms, is given a browser session object. That session knows how to find a page tab, turn a saved page reference into a real browser node, and send commands through Chrome DevTools Protocol, often called CDP, which is the control channel browsers expose for automation.

There are three main actions. One checks the byte sizes of files currently attached to a file input, which helps confirm that an upload actually reached the browser. One attaches local file paths to a file input. One fills in ordinary fields, including checkboxes, radio buttons, dropdowns, editable text areas, and normal inputs.

The file is careful about bad references. If a caller points at something that is not a file input, or at an element that can no longer be resolved, it raises a HallucinationError with advice to re-read the page and use a valid reference. In other words, it protects the rest of the system from acting on imagined or stale page elements.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This protocol method describes how a browser session should provide access to a page or tab. BrowserForms relies on it to choose the right browser tab before touching a form field.

**Data flow**: It receives an optional tab id. A concrete browser session uses that id to find the matching page, or the current/default page if no id is given, and returns a page-like object.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input call this first so they know which page contains the referenced form element. This file only states the expected shape of the method; the real work is supplied by another browser session implementation.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This protocol method describes how to get the browser’s CDP connection, meaning the low-level control line used to send commands into the browser. BrowserForms uses it when it needs the browser itself to resolve or update page elements.

**Data flow**: It takes no input beyond the session object. It returns a Cdp connection object that can send browser commands and receive replies.

**Call relations**: BrowserForms uses this method before commands such as resolving a DOM node or setting files on a file input. The protocol keeps BrowserForms independent from the exact browser connection implementation.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This protocol method describes how to run a small JavaScript function on a specific browser object. BrowserForms uses it to inspect file inputs and to fill form fields in the same environment as the web page.

**Data flow**: It receives a browser session id, a browser object id, JavaScript source code, and optional JSON-style arguments. The concrete session runs that function on the chosen page object and returns a dictionary-like JSON result.

**Call relations**: BrowserForms.attached_sizes uses it to ask a file input what file sizes it holds. BrowserForms.input uses it to run the form-filling script that changes values and fires page events.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This protocol method describes how to turn a page reference string into the actual browser node it points to. A reference is like a claim ticket: useful to the outside caller, but it must be exchanged for the real page element before work can happen.

**Data flow**: It receives a page object and a reference string. It returns the node information, including the browser session id, plus the backend node id that CDP uses internally.

**Call relations**: Each BrowserForms action calls this after selecting the page. The returned node and backend id are then used to resolve the element, set files on it, or run JavaScript against it.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what file sizes a file upload input is actually holding inside the browser. It is used to tell the difference between a file name that was requested and a file that has really arrived and been attached.

**Data flow**: It reads a JSON-like argument dictionary containing an optional tab id and a required element reference. It converts the tab id into an integer if possible, opens that page, resolves the reference to a browser node, asks CDP for a JavaScript object for that node, then runs a small script that returns the sizes of the attached files. It returns a list of integer byte sizes, or an empty list if the browser reply does not contain a usable size list.

**Call relations**: This is called when the larger browser automation flow needs to verify upload progress or completion. It uses _tab_id to normalize the tab selector, the session’s page and resolve_ref methods to find the field, the CDP connection to resolve the node, and call_on to run the size-checking JavaScript.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more file paths to a web page’s file input element. It is the automation equivalent of a user choosing files in an upload picker.

**Data flow**: It reads a JSON-like argument dictionary containing an optional tab id, an element reference, and a list of file paths. It normalizes the tab id, checks that the reference and file list are strings, resolves the referenced page element, and sends the browser a DOM.setFileInputFiles command with those paths. If the target is not a real file input, it raises a clear HallucinationError. On success, it returns the reference and the file paths it asked the browser to attach.

**Call relations**: This function is used when an outside request wants to upload files through the browser. It leans on _tab_id for tab parsing, the browser session for page lookup and reference resolution, and the CDP connection for the actual file-input update. If CDP rejects the action, it turns that low-level failure into a user-facing explanation.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This fills a form element with a requested value and makes the page notice the change. It supports common form controls such as text fields, checkboxes, radio buttons, dropdowns, and editable content areas.

**Data flow**: It reads a JSON-like argument dictionary containing an optional tab id, an element reference, and a value. It selects the page, resolves the reference, asks CDP to turn the DOM node into a JavaScript object, and then runs a script on that object. The script sets the right property for the element type, fires input and change events, and returns the field’s resulting value or text.

**Call relations**: This function is called when the automation layer needs to type or choose something in a page form. It uses _tab_id for tab selection, browser session methods to find the element, CDP to resolve it, and call_on to run the form-input JavaScript. If the reference cannot be resolved, it raises HallucinationError so the caller knows to read the page again and use a fresh reference.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a loose JSON value into a tab id the browser session can use. It accepts common input shapes, such as an integer, a float, or a non-empty string.

**Data flow**: It receives a JSON value or nothing. If the value is an integer, it returns it as-is; if it is a float or non-empty string, it converts it to an integer; otherwise it returns None, meaning no specific tab was chosen.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input all call this before asking the browser session for a page. It keeps tab parsing consistent across all form-related actions.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### Keyboard event synthesis
Keyboard helpers translate human key actions into Chrome input events with realistic modifier and shortcut behavior.

### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

Browsers do not accept “type this” as a vague instruction when automation needs realistic behavior. Chrome expects detailed keyboard events through CDP, the Chrome DevTools Protocol, which is the control channel automation tools use to talk to Chrome. This file is the translator between friendly actions and those detailed browser events.

It starts with a US keyboard map: each physical key has a code, displayed key value, numeric key code, shifted version, and keypad location when relevant. It also includes Mac editing command names, because on macOS some key combinations are interpreted as text-editing commands, such as moving by word or deleting to the start of a line.

The file builds a larger lookup table so callers can ask for keys in several natural ways, such as “KeyA”, “a”, “A”, “Enter”, or a newline. A small KeyboardState remembers which keys and modifier keys are currently held down. That state matters: pressing “a” while Shift is down should become “A”, and holding a key twice should mark the second event as auto-repeat.

The public helpers then create CDP calls for pressing a key down, releasing it, pressing a full shortcut, or typing text. Short text is sent key by key so web pages can react to each keystroke. Long text is inserted in one faster browser call, like pasting a paragraph instead of tapping every letter.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds the practical keyboard lookup table used by the rest of the file. It expands the raw keyboard layout so the same key can be found by physical code, character, shifted character, or common alias.

**Data flow**: It takes the raw US keyboard layout, where each entry describes one physical key. For each key, it creates a richer KeyDescription, adds shifted versions when available, adds aliases like “Shift” for “ShiftLeft”, and adds direct character lookups like “a” or “A”. The result is a dictionary that later functions can search quickly.

**Call relations**: This runs when the module prepares its shared LAYOUT_CLOSURE table. It uses KeyDescription objects to package each key’s browser-facing details, and dataclasses.replace to make adjusted copies, such as shifted versions, without rewriting every field by hand.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Turns the currently held modifier keys into the number format Chrome expects. Modifier keys are keys like Shift, Control, Alt, and Meta.

**Data flow**: It receives a set of modifier names that are currently pressed. It checks each known modifier and adds that modifier’s assigned bit value if present. It returns one integer that represents the whole modifier state.

**Call relations**: key_down and key_up call this right before sending an event to Chrome. It gives those events the compact modifier value Chrome needs, so the browser can tell whether a key happened with Shift, Control, Alt, or Meta held down.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the browser-ready description for a requested key, taking the current keyboard state into account. It is where “a” can become “A” if Shift is already held.

**Data flow**: It receives the current KeyboardState and a key name or character. It looks up that key in the expanded layout table. If the key is unknown, it raises a ValidationError. If Shift is pressed and the key has a shifted form, it swaps in that shifted description. If other modifiers are pressed, it clears normal text output because shortcut keys usually should not type visible characters. It returns the final KeyDescription.

**Call relations**: key_down and key_up both call this before building their Chrome events. It is the shared interpreter that keeps press and release events talking about the same physical key, while still respecting Shift and shortcut behavior.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up macOS-specific editing commands for a key combination. These commands tell Chrome about native Mac text-editing actions, such as moving the cursor or deleting words.

**Data flow**: It receives a physical key code and the set of currently pressed modifiers. It builds a shortcut name like “Shift+Alt+ArrowLeft”, checks the Mac command table, removes trailing colons from command names, skips insertion commands, and returns a list of command strings for Chrome.

**Call relations**: key_down calls this only when the caller says the target is macOS. The returned commands are included in the key-down event so Chrome can behave more like a real Mac app when text fields receive editing shortcuts.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome event for pressing a key down and updates the remembered keyboard state. This is the main building block for realistic keyboard input.

**Data flow**: It receives a KeyboardState, a requested key, and whether the browser is on macOS. It asks _description_for what that key means right now, checks whether the key was already down to mark auto-repeat, records the key as pressed, and records it as a modifier if it is Shift, Control, Alt, or Meta. On macOS it also asks _mac_commands for editing commands. It returns one CDP call named “Input.dispatchKeyEvent” with all the fields Chrome expects, including key code, text, modifiers, location, and keypad status.

**Call relations**: press_combo calls this for every key in a shortcut before releasing them. type_text calls it for each short, keyboard-mappable character. Inside, it relies on _description_for for key meaning, _mac_commands for Mac editing behavior, and modifiers_mask for Chrome’s compact modifier number.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome event for releasing a key and updates the remembered keyboard state. It is the matching half of key_down.

**Data flow**: It receives a KeyboardState and a requested key. It finds the current description for that key, removes the key from the pressed-key set, removes it from the modifier set if it was a modifier, and returns one “Input.dispatchKeyEvent” CDP call of type “keyUp”. The returned event includes the remaining modifier state after the release.

**Call relations**: press_combo calls this in reverse order after pressing all keys, like lifting fingers from a shortcut. type_text calls it after each key_down for short text. It uses _description_for so it releases the right physical key, and modifiers_mask so Chrome sees the updated modifier state.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut written as text, such as “Ctrl+A” or “Shift+Enter”, into a full press-and-release sequence. This lets higher-level code describe keyboard shortcuts in a human-friendly way.

**Data flow**: It receives the current KeyboardState, a plus-separated combo string, and whether the target is macOS. It splits the string, trims spaces, converts common names like “ctrl” into canonical names like “Control”, and rejects an empty combo with a ValidationError. It presses each key in order, then releases the same keys in reverse order. It returns the list of CDP calls to send to Chrome.

**Call relations**: This is a convenience layer above key_down and key_up. It does not build Chrome events itself; instead, it asks key_down and key_up to do that so modifier state, Mac commands, and key descriptions stay consistent with normal typing.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text into browser input events. It uses realistic per-key events for short text, but switches to direct text insertion for long text so large strings are fast.

**Data flow**: It receives the current KeyboardState, the text to type, and whether the target is macOS. If the text is longer than the configured limit, it returns one “Input.insertText” call containing the whole string. For shorter text, it walks through each character. If the character exists in the keyboard layout, it creates a key_down and key_up pair. If not, it uses “Input.insertText” for that character. The output is an ordered list of CDP calls.

**Call relations**: This function is the typing-focused wrapper around key_down and key_up. It uses those helpers when individual keystrokes matter, such as triggering page key handlers or autocomplete, and bypasses them with direct insertion when long text would be wasteful to synthesize key by key.

*Call graph*: calls 2 internal fn (key_down, key_up).

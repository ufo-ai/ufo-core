# Browser action vocabulary and input execution  `stage-10.3.5`

This stage is the action layer for the browser. It sits in the main work loop, after the assistant has decided what it wants to do and before Chrome actually does it. The actions file defines the allowed “verbs” of this small language, such as click, type, scroll, wait, screenshot, and key press. It also says what details each verb must include, so bad requests can be caught early.

Before an action reaches the browser, fixup cleans it up. For example, it can add a missing wait time or make sure a text field is focused before typing. Computer is the main bridge that takes these cleaned, high-level instructions and turns them into real browser events on the active tab.

Some actions need special handling. Forms fills page fields and handles file uploads through Chrome’s debugging protocol, which is a control channel for driving the browser. Keys translates human keyboard ideas like “Ctrl+A” or normal text into the exact key-down and key-up messages Chrome expects, while remembering which keys are being held.

## Files in this stage

### Action dispatch bridge
Translates model-requested browser operations into concrete events sent to the active browser tab.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

A browser automation system needs more than a screenshot. It must be able to act on the page in a safe, consistent way and then report what happened. This file provides that action layer.

The main class, BrowserComputer, receives a list of requested actions. It checks and normalizes them, converts coordinates from the assistant’s “model” screen size into the real browser viewport, performs each action, waits for the page to settle, and returns a fresh screenshot plus human-readable notes. Think of it like a remote-control operator: it reads a list of button presses, carries them out on the browser, then sends back a photo and a receipt.

It supports clicks, double-clicks, drags, typing, keyboard shortcuts, waits, scrolling, scrolling to known page elements, and screenshots. It also adds practical safety messages. For example, it warns if the page looks like a sign-in page, if repeated scrolling may be inefficient, if a file download started, or if a clicked native dropdown needs a different input method.

The file also contains small helpers for marking the last click on the screenshot, checking required coordinates, and formatting reminders. Without this file, higher-level code could decide what to do, but it would have no reliable way to make the browser do it or explain the result.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the session contract that BrowserComputer relies on. It promises that a browser session can provide the tab to act on, either the current tab or a requested tab.

**Data flow**: It receives an optional tab identifier. An implementation elsewhere uses that to find the matching browser tab and returns an object with a session id and keyboard state.

**Call relations**: BrowserComputer.run calls this at the start of an action request so all later mouse, keyboard, and screenshot work is aimed at the right tab.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session contract for getting the live browser connection. That connection is used to send low-level browser commands.

**Data flow**: It takes no direct input. An implementation elsewhere returns the Chrome DevTools Protocol connection, which is the channel used to talk to the browser.

**Call relations**: BrowserComputer.run, BrowserComputer.act, and several helper methods depend on this connection whenever they need to capture a screenshot, send mouse input, scroll an element, or wait for the page.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This session hook supplies summary information about the current tab for the final response. It lets the action result include context such as the active tab details.

**Data flow**: It receives a tab object. An implementation elsewhere reads tab state and returns a JSON-friendly dictionary that can be merged into the output.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the response describes the tab as it is after the work is done.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This session hook returns the titles of open tabs. BrowserComputer uses those titles to notice likely sign-in or registration pages and add a safety reminder.

**Data flow**: It takes no direct input. An implementation elsewhere reads the browser’s open tab titles and returns them as a list of strings.

**Call relations**: BrowserComputer.run calls this after performing actions, then passes the titles to sign_in_warning to decide whether to include a user-confirmation reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This session hook runs a small JavaScript function on a specific browser object. In this file it is used to inspect a clicked dropdown element.

**Data flow**: It receives a browser session id, an object id inside the page, JavaScript source text, and optional arguments. An implementation elsewhere executes that JavaScript in the browser and returns the result as a dictionary.

**Call relations**: BrowserComputer._select_reminder calls this after finding a native select element, so it can learn what options the dropdown contains and build a useful reminder.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This session hook turns a page reference, such as a stored element id, into the browser node needed for direct browser commands. It lets actions target elements by reference instead of only by coordinates.

**Data flow**: It receives the current tab and a reference string. An implementation elsewhere looks up that reference and returns the matching browser node plus its backend node id.

**Call relations**: BrowserComputer.act uses this for scroll_to actions, where the browser is asked to bring a known element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This session hook finds a clickable point for a referenced page element. It lets a high-level action say “click this element” without manually supplying screen coordinates.

**Data flow**: It receives the current tab and a reference string. An implementation elsewhere locates the element and returns an x,y point in the browser viewport.

**Call relations**: BrowserComputer.point calls this when an action includes a ref, and BrowserComputer.act then uses the returned point for clicking, dragging, or scrolling.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for executing a batch of browser actions. It performs the actions, waits for the page to calm down, captures a screenshot, and returns a clear report.

**Data flow**: It receives a JSON-like request containing a tab id and an actions list. It chooses the tab, validates and adjusts the actions, runs them in order, collects messages and warnings, marks downloads as noticed, captures a JPEG screenshot, optionally marks the last click on it, and returns tab info, text output, last-click coordinates, and the screenshot as base64 text.

**Call relations**: This method is the conductor for the file. It calls int_or_none to read the tab id, validates actions with ComputerAction.model_validate, uses fixup_actions and split_at_waits to prepare batches, calls BrowserComputer.act for each action, asks _select_reminder for dropdown guidance after clicks, uses sign_in_warning for safety messaging, and calls _to_model before reporting the final click position.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This executes one browser action and returns a short message saying what happened. It is where action names like left_click, type, scroll, and wait become real browser behavior.

**Data flow**: It receives the active tab and one parsed action. It finds the action’s target point if needed, checks required coordinates, sends mouse or keyboard commands, sleeps for waits, scrolls pages or elements, and returns a message plus the last viewport point if the action produced one.

**Call relations**: BrowserComputer.run calls this for every action in the request. Depending on the action, it hands work to point, _click, _drag, _scroll, _dispatch, _to_viewport, _to_model, require_point, or require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This decides where on the browser viewport an action should happen. It supports both element references and explicit coordinates.

**Data flow**: It receives the tab and an action. If the action names a page reference, it asks the browser session for that element’s point; if the action has model-space coordinates, it converts them to viewport coordinates; otherwise it returns no point.

**Call relations**: BrowserComputer.act calls this before deciding how to execute an action. It uses _to_viewport when coordinates come from the assistant’s model-sized screen.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the assistant’s model screen size into the real browser viewport size. It keeps clicks and drags landing in the right place even when the two coordinate systems differ.

**Data flow**: It receives an x,y coordinate in model space. It uses the configured viewport size and model size to scale that point, then returns an x,y coordinate in browser viewport space.

**Call relations**: BrowserComputer.point uses this for normal coordinate actions, and BrowserComputer.act uses it for drag start points and default scroll positions.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts real browser viewport coordinates back into the assistant’s model coordinate system. It is used when reporting results so the caller sees coordinates in the same system it used to request actions.

**Data flow**: It receives an x,y coordinate from the browser viewport. It scales that point into model space and returns the converted x,y pair.

**Call relations**: BrowserComputer.act uses this to write readable click and drag messages, and BrowserComputer.run uses it to return the last clicked point.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click landed on a native HTML select dropdown and, if so, builds a reminder about the correct way to change it. This matters because clicking dropdown options may not work reliably in this browser automation path.

**Data flow**: It receives the tab and clicked viewport point. It asks the browser what element is under that point, walks up to a select element if one exists, reads a sample of its options and its browser node id, and returns a reminder string; if anything fails or the click was not on a select, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action that has a point. It hands the gathered dropdown details to select_reminder to turn them into plain guidance.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared series of keyboard-related browser commands. It is the final delivery step for typing text or pressing key combinations.

**Data flow**: It receives the tab and a list of browser command calls. It sends each command through the browser connection using the tab’s session id and returns nothing after the commands have been delivered.

**Call relations**: BrowserComputer.act calls this for type and key actions after other helpers have translated text or a shortcut into concrete browser commands.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one low-level mouse event to the browser. It is the shared helper behind clicking, dragging, and scrolling.

**Data flow**: It receives the tab and a dictionary of mouse event details, such as event type, position, button, and wheel movement. It sends those details to the browser’s input system and does not return a value.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build up realistic mouse behavior from simple steps.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a mouse click at a browser viewport position. It can do left, right, double, or triple clicks while preserving currently held keyboard modifiers like Shift or Ctrl.

**Data flow**: It receives the tab, x,y position, mouse button name, and click count. It reads the tab’s pressed modifier keys, moves the mouse to the point, then sends matching press and release events for each click.

**Call relations**: BrowserComputer.act calls this for left_click, double_click, triple_click, and right_click actions. This method relies on _mouse_event to send each individual browser input event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize dragging after seeing motion between press and release.

**Data flow**: It receives the tab, start x,y, and end x,y. It reads pressed keyboard modifiers, moves to the start, presses the left mouse button, sends several intermediate mouse moves toward the end, and releases at the target.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It uses _mouse_event for each piece of the drag sequence.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This performs a wheel scroll at a chosen viewport point. It supports both vertical and horizontal scrolling by sending wheel movement to the browser.

**Data flow**: It receives the tab, x,y position, and horizontal and vertical scroll amounts. It reads pressed keyboard modifiers, moves the mouse to the position, sends a wheel event with the requested deltas, and returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating direction and distance. It uses _mouse_event to deliver the mouse move and wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser is on a login, sign-in, or registration page and returns a safety reminder if so. It helps avoid account creation or sign-in without user confirmation.

**Data flow**: It receives a list of tab titles. It lowercases them, searches for sign-in-related words, and returns the warning text if any title matches; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after reading tab titles, then includes the returned reminder in the final output when appropriate.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This writes the user-facing reminder shown after clicking a native select dropdown. It explains that the dropdown should be changed through form_input instead of by clicking visible options.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total number of options. It formats a short instruction, includes the known options, notes if more options exist, and returns the reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked dropdown and gathered the available option text.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This draws a small blue marker on a screenshot at the last clicked point. It makes the returned screenshot easier to understand, like circling the spot on a printed photo.

**Data flow**: It receives a base64-encoded screenshot and an x,y point. It decodes the image, draws a translucent circle at that point, saves the image back as a JPEG, encodes it as base64 again, and returns the new image text.

**Call relations**: BrowserComputer.run uses this after capturing the screenshot when there was a recent click. Because image editing can take time, run sends it through an executor rather than doing it directly in the async flow.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This reads a possible tab id from loose JSON input and turns it into an integer when possible. It allows callers to send the id as a number or a non-empty string.

**Data flow**: It receives a JSON value or nothing. Integers pass through, floats are truncated to integers, non-empty strings are parsed as integers, and anything else becomes None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for the target page.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action which needs a screen point actually has one. It turns a missing coordinate into a clear validation error.

**Data flow**: It receives a possible x,y point and the action name. If the point exists, it returns it unchanged; if not, it raises an error explaining that the action needs a coordinate or reference.

**Call relations**: BrowserComputer.act calls this before click and drag actions that cannot proceed without a target point.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used when an action needs a specific coordinate, such as the starting point of a drag.

**Data flow**: It receives a possible coordinate and a field name for the error message. If the coordinate exists, it returns it unchanged; if not, it raises a validation error naming the missing field.

**Call relations**: BrowserComputer.act calls this for drag actions before converting the drag start coordinate into viewport space.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### Action normalization and vocabulary
Cleans up imperfect browser-action requests and defines the allowed action schema they must conform to.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `request handling`

This file is a safety net for batches of browser actions. A model or caller may ask the browser to do things like click, type, scroll, or wait, but those requests are not always complete enough for a real browser to carry out reliably. This file patches the most common gaps before dispatch.

The main function, fixup_actions, walks through a list of requested actions and returns a safer list. For example, if an action says to type into a place on the page but the previous action did not click there first, it inserts a left click so the target is focused. This is like tapping a text box on a phone before using the keyboard. It also turns written escape sequences such as "\\n" into a real newline, gives scroll actions a fallback screen position, turns an incomplete scroll_to into a regular scroll, gives duration-free waits a default length, and simplifies ref-based double or triple clicks into a normal click when there is no exact coordinate.

The second public helper, split_at_waits, breaks one long action list into smaller batches whenever a wait appears. That lets the browser pause and settle before the next group of actions runs. Without this file, action execution would be more brittle: text might go nowhere, scrolls might lack an anchor point, and waits might not actually pause for a useful amount of time.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: This function repairs a list of browser actions so they are more likely to work when sent to the browser. It fills in missing details, inserts a click before typing when needed, and normalizes a few awkward action shapes.

**Data flow**: It takes a list of ComputerAction objects plus the visible browser size and, optionally, the size used by the model that produced coordinates. It first works out the effective model size and the center point of that area. Then it reads each action in order, sometimes copying it with safer values, sometimes inserting a new action before it, and sometimes replacing it with a simpler action. It returns a new list of actions and does not modify the original list directly.

**Call relations**: This is the main repair step in the file. When it needs to insert a focusing click before typing, it delegates to _focus_click. When it needs to turn literal text like "\\t" or "\\n" into real tab or newline characters, it delegates to _unescape_text. It also creates new ComputerAction and ScrollParameters objects when an incomplete action needs a usable replacement.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: This function divides a list of actions into smaller groups, ending each group at a wait action. Someone would use it so the browser has a chance to pause and settle before continuing.

**Data flow**: It takes one ordered list of ComputerAction objects. It builds a current group as it walks through the list. Each time it sees a wait action, it closes that group and starts a fresh one. At the end, it returns a list of action groups, preserving the original order.

**Call relations**: This function is a batching helper. After actions have been prepared, a dispatcher can use these groups to run actions up to a wait, pause, and then continue with the next batch. It does not call other project functions; it simply organizes the action list.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper creates a simple left-click action aimed at the same place as a typing action. Its job is to focus the field or page area before text is typed.

**Data flow**: It receives a ComputerAction, usually a type action that points to either a coordinate or a page reference. If there is a coordinate, it creates a left click at that coordinate. Otherwise, it creates a left click using the reference. The output is a new ComputerAction that can be inserted before the original typing action.

**Call relations**: fixup_actions calls this helper when it finds a type action that has a target but was not immediately preceded by a click-like action. The helper hands back the focusing click, and fixup_actions places it before the text entry action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper fixes text that contains written escape sequences, such as the two characters "\\n", when the intended result is an actual newline. It makes typed text match what the caller likely meant.

**Data flow**: It reads the text field from a ComputerAction. If the text does not contain any known escaped literal, it returns the action unchanged. If it does, it replaces "\\t" with a real tab and "\\n" with a real newline, then returns a copied action with the corrected text.

**Call relations**: fixup_actions calls this helper for every type action. The helper performs only the text cleanup part, while fixup_actions decides where that corrected action belongs in the overall repaired action list.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a standard order form for controlling a browser. Instead of letting the rest of the system send vague instructions like “do something on the page,” it defines a clear set of allowed actions and the fields that can go with them. That matters because browser automation is easy to get wrong: a click needs either a screen position or an element reference, a scroll needs a direction and distance, and a wait should not accidentally pause forever.

The file uses Pydantic models, which are Python classes that describe data and can validate it. Validation means checking that incoming data has the right shape before it is trusted. `ActionType` lists the permitted action names. `CLICK_ACTIONS` groups the click-like actions so other code can quickly ask, “is this one of the clicking actions?”

`ScrollParameters` describes how far and in which direction to scroll. The amount can be a number of screen heights, or the special value `max` to jump as far as possible. `ComputerAction` is the main action request. It can carry a target coordinate, typed text, key combination, scroll settings, wait duration, drag start point, or an element reference such as one returned by page-reading tools. This file does not perform the actions itself; it defines the safe, shared language other code uses to request them.


### Input execution helpers
Implements specialized execution paths for form filling, file uploads, and realistic keyboard events.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages are full of forms: text boxes, checkboxes, drop-downs, editable areas, and file upload buttons. This file is the bridge between a higher-level automation request and the real browser page. Without it, the system could read a page but would not have a reliable way to type into fields or upload files.

The main class, BrowserForms, receives a browser session object. That session knows how to find the right tab, turn a saved page reference into a real browser node, and send commands to the browser. BrowserForms then does three practical jobs. It can check the sizes of files currently attached to an upload input, which proves that the browser really received the file contents. It can attach local file paths to a file input. And it can set the value of normal form controls.

For normal input, it runs a small JavaScript function inside the page. That script treats checkboxes and radio buttons differently from text fields, select boxes, and editable text areas, then fires “input” and “change” events. Those events matter because many modern websites only notice changes when these signals are sent, much like a doorbell telling the site “something changed.”

If the caller gives a bad page reference, this file raises a HallucinationError with advice to re-read the page and use a real returned reference.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It gives form code access to the current tab, or to a specific tab when an identifier is provided.

**Data flow**: It receives an optional tab number. It returns a page or tab object that later steps can use to find elements on that page.

**Call relations**: BrowserForms methods rely on this first, before touching any form field. They ask the session for the right page, then use that page to resolve the caller’s saved element reference.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It provides the low-level connection used to send commands to the browser through the Chrome DevTools Protocol, which is Chrome’s remote-control API.

**Data flow**: It takes no extra input from the form operation. It returns a connection object that can send browser commands such as resolving a page node or setting files on an upload input.

**Call relations**: BrowserForms uses this after it has found the relevant page element. The returned connection is the path from the Python code into the live browser.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It runs a JavaScript function on a specific page object, such as a form element.

**Data flow**: It receives a browser session id, a browser object id, the JavaScript code to run, and optional argument values. It returns the JSON-like result produced by that JavaScript.

**Call relations**: BrowserForms.attached_sizes uses it to ask a file input what file sizes it currently holds. BrowserForms.input uses it to set a field’s value and trigger page events.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a stored page reference, the kind returned by page-reading code, back into the real browser element it points to.

**Data flow**: It receives a tab object and a reference string. It returns two pieces: a node with the browser session id, and a backend node id that Chrome understands.

**Call relations**: Every BrowserForms action depends on this lookup. The higher-level caller speaks in stable references, while the browser protocol needs internal node ids, so this method connects those two worlds.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what file sizes a file-upload input is actually holding in the browser. It is used to tell whether an upload really arrived, not just whether a filename was mentioned.

**Data flow**: It reads a tab id and element reference from the input arguments. It finds the tab, resolves the reference into a browser node, asks the browser for a JavaScript object tied to that node, then runs a small script that reads the sizes of the selected files. It returns a list of integer byte sizes, or an empty list if the page does not return the expected shape.

**Call relations**: This method starts by using _tab_id to normalize the optional tab id. It then uses the browser session to find the page and element, uses the browser connection to resolve that element, and finally uses call_on to run the file-size JavaScript inside the page.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more files to a browser file-upload input. It lets the automation system perform the same kind of action a person would do by choosing files in an upload dialog.

**Data flow**: It reads the tab id, element reference, and file path list from the input arguments. It checks that the file list is really a list and that each path is a string, resolves the page reference to a browser node, and sends Chrome a command to set those files on that node. It returns the reference and the file paths that were submitted. If the reference is not a file input, it raises a clear HallucinationError instead of silently failing.

**Call relations**: This method uses _tab_id before asking the browser session for the right page. It uses the session’s reference lookup to find the target element, then hands the final file-setting work to the browser connection. When Chrome rejects the operation, it converts that low-level protocol failure into a message the higher-level agent can understand and recover from.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a form element, such as a text box, checkbox, radio button, drop-down, or editable text area. It also triggers the page events that websites commonly rely on to notice the change.

**Data flow**: It reads the tab id, element reference, and desired value from the input arguments. It finds the relevant tab, resolves the reference into a browser object, and runs JavaScript on that object to update it in the right way for its element type. It returns the result from the page script, usually including the value the element ended up with. If the reference cannot be resolved, it raises a HallucinationError telling the caller to re-read the page.

**Call relations**: This method uses _tab_id to interpret the optional tab id, then depends on the browser session to locate the element. After the browser connection turns the node into a JavaScript object, input hands off to call_on so the change happens inside the live page, where the page’s own scripts can react.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a loose tab identifier into either an integer tab id or no tab id at all. It accepts the common forms a caller might send, such as a number or a numeric string.

**Data flow**: It receives a JSON-like value. If the value is an integer, it returns it. If it is a float or non-empty string, it converts it to an integer. For anything else, it returns None, meaning “use the default tab.”

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input all call this at the start. It keeps tab-id cleanup in one place so the main form actions can focus on browser work instead of input-shape details.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `keyboard action handling`

Browser automation cannot simply say “put the letter A here” for every keyboard action. Web pages often listen for real key events, including which physical key was pressed, whether Shift or Control was held, and whether a key is repeating. This file supplies that translation layer.

It starts with a US keyboard map. That map says, for example, that the physical key named “Digit1” normally means “1”, but with Shift it means “!”. It also includes macOS editing shortcuts, such as Command+ArrowLeft meaning “move to the start of the line”. These details are copied from Playwright so this project behaves like a well-tested browser automation tool.

The central idea is a small keyboard memory, `KeyboardState`, which records pressed modifier keys such as Shift and pressed physical keys. When asked to press or release a key, the file looks up the key’s browser-facing description, adjusts it for Shift or other modifiers, updates the memory, and returns a Chrome DevTools Protocol call. Chrome DevTools Protocol is Chrome’s remote-control API.

For short text, it sends realistic key-down and key-up events character by character. For long text, it uses a direct text insertion call, like pasting a paragraph instead of pretending to press every key.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds a convenient lookup table from the raw US keyboard layout. It lets the rest of the file find a key by several names, such as by physical code, visible character, or common alias.

**Data flow**: It receives the detailed keyboard layout. For each key, it creates a browser-ready description with the key name, code, text, key number, and location. It also adds shifted versions, such as “!” for Shift+1, and simple aliases like newline for Enter. The result is a dictionary that later functions can search quickly.

**Call relations**: This runs when the module is loaded to create `LAYOUT_CLOSURE`. Later, key lookup functions rely on that prepared table instead of working from the raw layout every time.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Turns the current set of held modifier keys into the number format Chrome expects. A modifier key is a key like Shift, Control, Alt, or Meta that changes what another key does.

**Data flow**: It receives a set of modifier names. It checks which known modifier keys are present, adds their assigned bit values together, and returns one integer. That integer is placed into outgoing Chrome keyboard messages.

**Call relations**: When `key_down` or `key_up` builds a Chrome DevTools Protocol event, they call this so Chrome can know exactly which modifier keys are active at that moment.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the browser description for a requested key, taking the current keyboard state into account. It is the point where “what the user asked for” becomes “what key should Chrome see right now.”

**Data flow**: It receives the current `KeyboardState` and a key name or character. It looks that key up in the prepared layout table. If the key is unknown, it raises a validation error so the caller gets a clear failure. If Shift is held, it switches to the shifted version when one exists. If other modifiers are held, it clears normal text output because shortcuts should not type characters. It returns the adjusted key description.

**Call relations**: `key_down` and `key_up` both call this before creating browser events. It feeds them the exact key data they need, including the physical code, visible key value, text, and location.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up macOS-specific editing commands for a key combination. This matters because Chrome on macOS can receive special editing command names, not just ordinary key events.

**Data flow**: It receives a physical key code and the currently held modifiers. It builds a shortcut name such as `Shift+Meta+ArrowLeft`, looks that up in the macOS command table, removes trailing punctuation from command names, skips text-insertion commands, and returns a list of command names for Chrome.

**Call relations**: `key_down` calls this only when the caller says the target system is macOS. The returned commands are added to the key-down event so Chrome can perform native-feeling editing behavior.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome message for pressing a key down. It also updates the remembered keyboard state so later events know which keys are still held.

**Data flow**: It receives the current keyboard state, the requested key, and whether the target browser is on macOS. It asks `_description_for` what this key means right now, detects whether the key was already held for auto-repeat, records the key as pressed, and records it as a modifier if it is Shift, Control, Alt, or Meta. On macOS it also adds any matching editing commands. It returns a Chrome DevTools Protocol call named `Input.dispatchKeyEvent` with all the key details Chrome expects.

**Call relations**: `press_combo` uses this for every key in a shortcut, and `type_text` uses it for each short, typable character. It depends on `_description_for`, `_mac_commands`, and `modifiers_mask` to assemble a realistic browser event.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome message for releasing a key. It also removes that key from the remembered pressed-key state.

**Data flow**: It receives the current keyboard state and the key to release. It looks up the key description, removes the key from the pressed modifier set if needed, removes the physical key code from the pressed key set, computes the remaining modifier mask, and returns a Chrome `Input.dispatchKeyEvent` message of type `keyUp`.

**Call relations**: `press_combo` calls this after key-down events, in reverse order, so a shortcut is released cleanly. `type_text` calls it after each synthetic key press. It shares the same key lookup path as `key_down` so press and release events describe the same key.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string like `Ctrl+A` or `Shift+Enter` into the full sequence of key-down and key-up browser messages. This lets callers describe shortcuts in a human-friendly way.

**Data flow**: It receives the current keyboard state, a shortcut string, and whether the target is macOS. It splits the string around plus signs, trims spaces, translates common names like `ctrl` into Chrome-style names like `Control`, and rejects an empty shortcut. It presses each key in order, then releases them in reverse order, returning the complete list of Chrome calls.

**Call relations**: Higher-level keyboard actions would call this when they need a shortcut rather than plain text. It delegates the real event construction to `key_down` and `key_up`, which update state and create the protocol messages.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text input into browser actions. It uses realistic key events for short text, but switches to direct insertion for long text to avoid slow, unnecessary per-key simulation.

**Data flow**: It receives the current keyboard state, the text to type, and whether the target is macOS. If the text is longer than the configured limit, it returns one `Input.insertText` call containing the whole string. Otherwise, it walks through each character. Characters found in the keyboard layout become key-down and key-up calls; characters not represented by the layout are inserted directly. The output is a list of Chrome calls.

**Call relations**: This is the text-typing companion to `press_combo`. It calls `key_down` and `key_up` when individual key behavior might matter, such as for page key handlers or autocomplete, and uses Chrome’s direct text insertion path when character-by-character simulation is not useful.

*Call graph*: calls 2 internal fn (key_down, key_up).

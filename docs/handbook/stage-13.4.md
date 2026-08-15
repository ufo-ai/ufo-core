# User interaction execution and action fixups  `stage-13.4`

This stage is part of the main work loop, where a planned user action is turned into something the browser actually does. It is like the robot hand between the system’s decisions and the open web page. computer.py is the main driver: it receives actions such as click, type, scroll, wait, or screenshot, sends the right Chrome DevTools commands to the tab, then reports back with a new screenshot and warnings. Before that happens, fixup.py checks the action for common small problems, such as trying to type before a field is focused, or waiting without a time. forms.py handles form-specific work, including filling fields and attaching files, and confirms that uploads really reached the page. keys.py translates human keyboard ideas like “Ctrl+A” or typed text into low-level key press and release events, while remembering which keys are held down. settle.py watches for page loading, network activity, and visual updates so the system knows when it is safe to continue.

## Files in this stage

### Action execution orchestration
Top-level browser action handling translates model instructions into DevTools operations while applying preflight corrections for common action mistakes.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the browser “hands and eyes” layer. A caller sends a batch of actions in a model-friendly format: coordinates, refs to page elements, key presses, waits, and scrolls. BrowserComputer validates and adjusts those actions, performs them in the browser, waits for the page to settle, then captures a screenshot and reports what happened.

The browser is controlled through Chrome DevTools Protocol, or CDP, which is a remote-control interface for Chrome. Think of it like sending commands to a robotic mouse and keyboard: move the pointer, press the button, type keys, or spin the wheel. The file also translates between two coordinate systems: the model’s screen size and the browser viewport’s actual pixel size.

A few guardrails are built in. Repeated scrolling triggers a reminder to use page-reading tools instead. Clicking a native dropdown explains that its options should be chosen through form_input, because this browser control cannot be operated reliably by clicking options. Page titles that look like sign-in pages produce a safety reminder. New downloads and automatically handled JavaScript dialogs are also reported.

Without this file, the rest of the system could describe browser actions, but it would not have a practical way to execute them and show the result.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the session contract. It promises that a browser session can provide the tab that should receive the requested actions.

**Data flow**: It receives an optional tab id. The concrete session implementation uses that to choose a browser tab and returns an object with a session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this contract at the start of an action batch so it knows which tab to operate on. The actual work is supplied by whichever browser session class implements this protocol.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session contract for getting the browser remote-control connection. That connection is used to send Chrome DevTools Protocol commands.

**Data flow**: It takes no input beyond the session object. It returns a CDP connection object that can send commands to Chrome.

**Call relations**: BrowserComputer.run, act, and the helper methods use this connection whenever they need to capture a screenshot, scroll an element into view, dispatch mouse events, or send keyboard events.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This contract method returns summary information about a tab after actions have run. It helps the final response include context such as the active tab state.

**Data flow**: It receives a tab object. The concrete session reads browser-side details for that tab and returns them as a JSON-style dictionary.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the response includes both what happened and where it happened.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This contract method returns the titles of open tabs. The titles are used as a simple safety signal for sign-in or registration pages.

**Data flow**: It reads tab information from the concrete browser session and returns a list of title strings.

**Call relations**: BrowserComputer.run calls it after performing actions, then passes the titles to sign_in_warning to decide whether to add a user-confirmation reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This contract method runs a JavaScript function against a specific browser object. It is used when the code needs details from an element already found inside the page.

**Data flow**: It receives a session id, an object id, JavaScript source text, and optional arguments. The concrete session asks the browser to run that JavaScript and returns the result as a JSON-style dictionary.

**Call relations**: BrowserComputer._select_reminder uses this to inspect a clicked select dropdown and learn its option text. The protocol hides the browser-specific details from BrowserComputer.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This contract method turns a stable page reference, such as a ref returned by a page-reading tool, into the browser node needed for low-level commands.

**Data flow**: It receives a tab and a ref string. The concrete session looks up the matching page node and returns both that node and its browser backend id.

**Call relations**: BrowserComputer.act uses this for scroll_to actions, where it must ask Chrome to bring a referenced element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This contract method finds a clickable point for a referenced page element. It lets actions use element refs instead of raw coordinates.

**Data flow**: It receives a tab and a ref string. The concrete session locates the element and returns an x, y point in the browser viewport.

**Call relations**: BrowserComputer.point calls this whenever an action names a ref. That point is then used by BrowserComputer.act for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main batch runner. It receives a request containing browser actions, performs them, waits for the page to calm down, and returns a screenshot plus a plain text summary.

**Data flow**: It takes a JSON-style argument dictionary. It chooses the target tab, validates and adjusts the action list, runs each action, records the last clicked point, waits after each group, drains new download notices, captures a screenshot, marks the last click on the screenshot, adds safety reminders, and returns tab info, output text, last-click coordinates, and screenshot data.

**Call relations**: This is the top-level method other code calls when it wants the browser operated. It hands individual actions to BrowserComputer.act, asks _select_reminder for dropdown guidance after clicks, uses _to_model to report coordinates in the caller’s coordinate system, calls sign_in_warning for safety messaging, and uses int_or_none to interpret the optional tab id.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one browser action. It turns a single high-level instruction, such as click, type, key press, wait, scroll, or screenshot, into the appropriate browser operation.

**Data flow**: It receives the target tab and one validated ComputerAction. It first finds the point to act on if the action needs one, then dispatches mouse, keyboard, wait, scroll, or DOM commands. It returns a short human-readable message and, for pointer actions, the viewport point where the action ended.

**Call relations**: BrowserComputer.run calls this for every action in the batch. act delegates the physical work to _click, _drag, _scroll, and _dispatch, uses point to resolve refs or coordinates, uses _to_viewport and _to_model for coordinate conversion, and uses require_point or require_coord to reject incomplete actions.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds the screen point for an action. It lets the rest of the code treat “use this element ref” and “use these coordinates” in a single way.

**Data flow**: It receives a tab and an action. If the action has a ref, it asks the browser session for the element’s point; if it has coordinates, it converts them from model coordinates to viewport pixels; if neither is present, it returns nothing.

**Call relations**: BrowserComputer.act calls this before deciding how to perform an action. For coordinate conversion it uses _to_viewport; for element refs it relies on the browser session’s ref_point contract.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts a point from the model’s coordinate system into the browser viewport’s real pixel coordinates. It keeps clicks accurate even when the model and browser use different screen sizes.

**Data flow**: It receives an x, y pair in model coordinates. It wraps that pair as a Coord, scales it using the known viewport size and model size, and returns the matching viewport x, y pair.

**Call relations**: BrowserComputer.point uses this for actions with coordinates. BrowserComputer.act also uses it when it needs a default scroll point or a drag start point.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts a browser viewport point back into the model’s coordinate system. It makes reported click locations meaningful to the caller.

**Data flow**: It receives an x, y pair in viewport pixels. It scales that point back to the model size and returns the model-coordinate x, y pair.

**Call relations**: BrowserComputer.act uses this to write messages like “Clicked (x,y)” in the caller’s coordinate system. BrowserComputer.run uses it to return the final last_click value.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click landed on a native HTML select dropdown and, if so, prepares a helpful reminder about how to choose an option correctly. This matters because clicking dropdown options is unreliable in this browser-control setup.

**Data flow**: It receives the tab and clicked viewport point. It asks the browser what element is under that point, walks up to a SELECT element if one exists, reads a sample of its options, tries to get a ref for it, and returns reminder text; if anything cannot be inspected safely, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses select_reminder to turn the raw dropdown details into user-facing guidance.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared sequence of keyboard-related browser commands. It is the small bridge between key/text helpers and the CDP connection.

**Data flow**: It receives a tab and a list of CDP method-and-parameter pairs. It sends each command to the browser using the tab’s session id and returns nothing after all commands have been sent.

**Call relations**: BrowserComputer.act calls this for type and key actions. The key helpers build the command list, and _dispatch delivers it to the browser in order.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one mouse event to the browser. It is the common low-level sender used by clicking, dragging, and scrolling.

**Data flow**: It receives the target tab and a dictionary of mouse event parameters, such as event type, position, buttons, and modifier keys. It sends an Input.dispatchMouseEvent command through the browser connection and returns nothing.

**Call relations**: _click, _drag, and _scroll call this repeatedly to build realistic mouse behavior from smaller events.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a left, right, double, or triple click at a viewport point. It creates the same kind of press and release sequence a real mouse would send.

**Data flow**: It receives the tab, x and y viewport coordinates, a mouse button name, and the number of clicks. It reads currently pressed keyboard modifiers, moves the mouse to the point, sends press and release events for each click, and changes the page through those browser input events.

**Call relations**: BrowserComputer.act calls this for click actions. _click uses _mouse_event for each low-level event and modifiers_mask so held keys like Shift or Control affect the click correctly.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop after seeing intermediate movement.

**Data flow**: It receives the tab and start and end viewport coordinates. It reads active keyboard modifiers, moves to the start, presses the left button, sends several mouse-move events along the path, then releases at the end.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. _drag uses _mouse_event for the actual browser input and modifiers_mask to preserve held modifier keys.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This scrolls the page by sending a mouse wheel event at a specific viewport point. The point matters because some pages scroll the panel under the cursor rather than the whole window.

**Data flow**: It receives the tab, cursor point, and horizontal and vertical scroll distances. It reads active keyboard modifiers, moves the mouse to the point, sends a wheel event with the requested deltas, and returns after the browser has received it.

**Call relations**: BrowserComputer.act calls this for scroll actions after it calculates the scroll amount from the viewport size and requested direction. _scroll uses _mouse_event for both the mouse move and wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser is on a sign-in or account-creation page. If it sees one, it returns a safety reminder to confirm with the user before signing in.

**Data flow**: It receives a list of tab titles. It lowercases them, checks for words like “login” or “sign up,” and returns the warning text if any title matches; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after fetching tab titles. Its result is appended to the final output as a system reminder when appropriate.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This writes the user-facing message shown after clicking a native select dropdown. It explains the safer way to choose an option and shows the available option text when known.

**Data flow**: It receives an optional element ref, a list of visible option labels, and the total option count. It formats a short option preview, chooses instructions based on whether a ref is available, and returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it inspects the clicked dropdown. The returned message is later included by BrowserComputer.run in the batch output.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This draws a small blue marker on a screenshot at the last click location. It helps the caller visually confirm where the browser was clicked.

**Data flow**: It receives a base64-encoded JPEG screenshot and a viewport point. It decodes the image, draws a translucent circle over the point, saves the image as JPEG again, base64-encodes it, and returns the new screenshot string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click-like action. It is run in an executor so the image work does not block the async browser workflow.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This converts an optional tab id value into an integer when possible. It makes the incoming request tolerant of tab ids supplied as numbers or strings.

**Data flow**: It receives a JSON value or nothing. Integers pass through, floats are truncated to integers, non-empty strings are parsed as integers, and anything else becomes None.

**Call relations**: BrowserComputer.run uses this before asking the browser session for a tab. That lets the session either choose the named tab or fall back to its default behavior.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action needing a screen point actually has one. It turns a missing coordinate or ref into a clear validation error.

**Data flow**: It receives a possible point and the action name. If the point exists, it returns it unchanged; if it is missing, it raises a ValidationError explaining that the action needs a coordinate or ref.

**Call relations**: BrowserComputer.act calls this before click, right-click, double-click, triple-click, and drag-end operations. It prevents low-level mouse commands from being sent with no target.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used for action fields that cannot be guessed safely.

**Data flow**: It receives a possible coordinate pair and the field path name. If the coordinate exists, it returns it unchanged; otherwise it raises a ValidationError naming the missing field.

**Call relations**: BrowserComputer.act calls this for the start_coordinate of a drag action. That keeps drag behavior explicit: the code must know both where the drag starts and where it ends.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `before browser action dispatch`

Browser automation actions are like instructions given to a remote hand: click here, type this, scroll there, wait a bit. This file acts as a careful editor for those instructions before they are carried out. Its job is not to decide what the user wants, but to make the action list safer and more realistic for a browser to perform.

The main function, `fixup_actions`, walks through a list of `ComputerAction` objects and repairs predictable problems. If a typing action names a place to type but the previous action did not click or otherwise focus it, the file inserts a left click first. If text contains the literal characters `\n` or `\t`, it turns them into a real newline or tab. If a scroll action has no anchor point, it uses the center of the model’s screen. If `scroll_to` has no target reference, it turns it into a normal scroll. If a wait has no duration, it gives it a default of three seconds. It also simplifies multi-clicks that target a reference instead of a coordinate.

The other public helper, `split_at_waits`, divides a long action list into smaller batches whenever a wait appears, so the browser can settle before the next batch runs. Together, these functions make generated browser actions less brittle.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: This function repairs a batch of browser actions before they are sent onward. It makes small, practical corrections that help the browser understand and carry out the actions reliably.

**Data flow**: It receives a list of actions, the browser viewport size, and optionally the size the model was thinking in. It first works out the effective model size, then walks through each action and either keeps it, changes it, or adds an extra action before it. For example, typing may get a focus click inserted before it, missing wait times become three seconds, and scrolls without a position get the screen center. It returns a new list of corrected actions and does not change the original list directly.

**Call relations**: This is the main repair step in the file. While checking each action, it calls `_focus_click` when typing needs a click first and `_unescape_text` when typed text may contain escaped characters. It also uses `effective_model_size` to choose a sensible center point for scroll actions, and creates new `ComputerAction` or `ScrollParameters` objects when an action must be rewritten.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: This function breaks one long action list into smaller groups, ending each group at a wait action. That lets the browser pause and settle before later actions run.

**Data flow**: It receives a list of actions. It builds a current batch one action at a time; whenever it sees a wait action, it closes that batch and starts a fresh one. At the end, it returns a list of batches, where each batch is itself a list of actions.

**Call relations**: This function is a companion to the repair step rather than a repair itself. In the provided call information it is not shown as being called by another listed function, but its purpose is to prepare fixed actions for staged execution, with waits acting like natural stopping points.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper creates a simple left-click action that focuses the place where a later typing action is meant to go. It is used when the action list tries to type into a field without first clicking into it.

**Data flow**: It receives a typing-related action. If that action has a coordinate, it creates a new left click at that coordinate. If not, it creates a new left click aimed at the action’s reference target. The result is a new `ComputerAction` that can be inserted before typing.

**Call relations**: `fixup_actions` calls this helper during its typing repair path. The helper hands back a focus click, which `fixup_actions` places into the outgoing action list before the original typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns visible escape sequences in typed text, such as the two characters `\n`, into the real character they mean, such as an actual newline. This fixes text that was generated as a literal string instead of as the intended keystrokes.

**Data flow**: It receives a `ComputerAction` and reads its text field. If the text does not contain known escaped literals, it returns the same action. If it finds `\t` or `\n`, it replaces them with a real tab or newline and returns a copied action with the corrected text.

**Call relations**: `fixup_actions` calls this helper whenever it processes a type action. The helper uses the action’s copy method to produce a corrected version, which `fixup_actions` then adds to the final action list.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Input and form primitives
Specialized helpers perform form filling, file uploads, and realistic keyboard event generation for browser interactions.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web forms are tricky for an automation system because typing text, choosing a dropdown item, ticking a checkbox, and uploading a file all work differently inside the browser. This file gives the rest of the project a small, safe interface for those actions. Think of it like a careful assistant at a computer: it first finds the right browser tab, then finds the exact page element by a saved reference, then performs the action in the browser itself.

The file defines the shape of the browser session it needs through `BrowserFormSession`: something that can return a page, talk to Chrome DevTools Protocol (CDP, the browser's remote-control API), run JavaScript on a page element, and turn a page reference into a real browser node. `BrowserForms` uses that session to do the work.

For normal form input, it runs a small JavaScript function on the chosen element. That script knows the common cases: checkboxes and radio buttons get checked or unchecked, select boxes get a selected value, editable text areas get text, and other inputs get a value. It then fires browser events so the web page notices the change.

For file uploads, it asks the browser to set the selected files directly. If the reference is wrong, it raises a `HallucinationError`, meaning the automation tried to act on something that the current page does not support. The file-size check exists because remote uploads may take time; a file name is not enough proof that the browser actually has the file contents.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It promises that a session can return the current browser page, or a specific tab when a tab id is provided.

**Data flow**: It receives an optional tab id. The real browser-session implementation uses that to choose a page, then returns the page object that later form actions will work against.

**Call relations**: The methods in `BrowserForms` call this first so they know which tab they are acting on. This file only defines the promise; another part of the browser system supplies the real behavior.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It provides access to the Chrome DevTools Protocol connection, which is the channel used to ask the browser to resolve page nodes or set uploaded files.

**Data flow**: It takes no input beyond the session itself. The real implementation returns a CDP connection object that can send commands to the browser.

**Call relations**: `BrowserForms.attached_sizes`, `BrowserForms.upload_file`, and `BrowserForms.input` rely on this connection when they need the browser itself to look up an element or change a file input.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It promises a way to run a piece of JavaScript on one specific page object, such as an input field.

**Data flow**: It receives a browser session id, an object id for the page element, JavaScript code, and optional arguments. The real implementation runs that code in the browser and returns a JSON-like result.

**Call relations**: `BrowserForms.attached_sizes` uses it to ask a file input what file sizes it contains. `BrowserForms.input` uses it to set a form value and trigger the page's normal input and change events.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a stored page reference, such as one returned by a page-reading step, into the browser node needed for later actions.

**Data flow**: It receives a page object and a reference string. The real implementation finds the matching browser element and returns both the node information and the browser's backend node id for it.

**Call relations**: Every public action in `BrowserForms` starts from a human-facing reference and depends on this method to find the actual browser element. If the reference points to the wrong thing, later CDP calls will fail and the file converts some failures into clearer errors.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what file sizes a browser file input is actually holding. It is used to tell the difference between an upload that has really arrived in the browser and a file name that was set before the file contents were available.

**Data flow**: It receives an argument dictionary with a page reference and optionally a tab id. It chooses the tab, resolves the reference to a browser element, asks the browser for an object id for that element, runs JavaScript that reads `this.files`, and returns a list of byte sizes. If the browser reply does not contain a usable list, it returns an empty list.

**Call relations**: This method is called when the automation needs proof that a file upload is complete. It uses `_tab_id` to normalize the tab id, uses the session to find and resolve the element, then hands a small JavaScript snippet to `BrowserFormSession.call_on` so the browser reports the file sizes from inside the page.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a browser file-upload input. It protects the rest of the system from silently pretending a non-file field can accept files.

**Data flow**: It receives an argument dictionary containing a page reference, a list of file paths, and optionally a tab id. It validates and converts those values, resolves the reference to a browser element, and asks the browser to set that element's selected files. On success it returns the same reference and the file paths it attached; if the target is not a file input, it raises a clear `HallucinationError`.

**Call relations**: This method is used when a higher-level browser action wants to upload files. It uses `_tab_id` to pick the tab, `resolve_ref` to find the element, and the CDP connection to perform the actual browser-side file selection. If CDP rejects the action, this method translates that low-level failure into advice to re-read the page and use a real file input reference.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a form element, such as a text field, checkbox, radio button, dropdown, or editable area. It also makes the page notice the change by firing normal browser events.

**Data flow**: It receives an argument dictionary with a page reference, a value, and optionally a tab id. It chooses the tab, resolves the reference to a browser node, asks the browser for a JavaScript object id for that node, then runs a script that applies the value in the right way for that kind of element. It returns the browser script's result, usually including the value now present on the element. If the reference cannot be resolved, it raises a `HallucinationError` explaining that the page should be read again.

**Call relations**: This is the main path for form filling. It relies on `_tab_id` for tab selection, the browser session for turning a reference into a live element, the CDP connection for resolving that element, and `BrowserFormSession.call_on` for running the input-setting JavaScript inside the page.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id supplied in different JSON-friendly forms into either an integer tab id or `None`. It lets callers pass a number or a numeric string without every form action repeating the same conversion rules.

**Data flow**: It receives a value that may be an integer, float, string, or missing value. Integers pass through unchanged, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes `None`, meaning no specific tab was requested.

**Call relations**: `BrowserForms.attached_sizes`, `BrowserForms.upload_file`, and `BrowserForms.input` all call this at the start. It gives them a clean tab id before they ask the browser session for the right page.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

Browsers do not accept “type hello” as a real keyboard action by itself. They expect detailed key events: which physical key was pressed, what character it produced, whether Shift or Control is held, whether it came from the keypad, and so on. This file is the translator between friendly keyboard actions and those low-level Chrome instructions.

Most of the file is a US keyboard map, borrowed from Playwright, that says things like “Digit1 normally means 1, but with Shift it means !”. It also includes a table of Mac editing shortcuts, such as Command+ArrowLeft moving to the start of a line. The helper code builds a searchable version of this map so callers can ask for keys by code, character, or common aliases.

A small KeyboardState object acts like a memory of the keyboard: which keys and modifier keys are currently down. Functions such as key_down and key_up update that memory and return a Chrome DevTools Protocol call, which is a method name plus JSON-like data. Higher-level helpers press_combo and type_text use those pieces to produce a full sequence of browser input events. For long text, the file deliberately uses one direct insertText call instead of pretending to press every character, because that is faster and avoids unnecessary per-key behavior.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds an easy-to-search keyboard lookup table from the raw US keyboard layout. This lets the rest of the file find a key by its physical code, by its visible character, or by a friendly alias such as “Shift”.

**Data flow**: It receives the raw layout table, where each physical key has basic facts like its normal character and Shift character. It creates richer KeyDescription entries, adds shifted versions when needed, and adds aliases for common names and characters. The result is a dictionary that later functions can use to quickly translate user input into browser-ready key details.

**Call relations**: This runs when the module creates LAYOUT_CLOSURE. It relies on KeyDescription objects to describe each key and uses dataclasses.replace to make slightly changed copies, such as the shifted form of a key.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Turns the current modifier keys into the number format Chrome expects. Modifier keys are keys like Shift, Control, Alt, and Meta/Command that change the meaning of other keys.

**Data flow**: It receives a set of modifier names that are currently pressed. It looks up each one’s numeric bit value and adds the matching values together. It returns a single integer that represents the whole modifier state.

**Call relations**: key_down and key_up call this right before creating a Chrome keyboard event, so each event says which modifier keys are active at that exact moment.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the exact browser-facing description for a requested key, taking the current Shift state into account. It also prevents invalid key names from silently producing bad browser events.

**Data flow**: It receives the current KeyboardState and a key name or character. It looks up that key in the prepared layout table. If Shift is held and the key has a shifted version, it switches to that version; if non-Shift modifiers are held, it clears the typed text so shortcuts do not accidentally insert characters. It returns a KeyDescription, or raises a ValidationError if the key is unknown.

**Call relations**: key_down and key_up call this before they build their Chrome event. It is the point where friendly input becomes precise key facts such as code, key name, text, location, and key code.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Finds special Mac editing commands for a key press, such as copy, paste, move-left, or delete-word-backward. Chrome can use these command names to make synthesized Mac keyboard shortcuts behave more like native Mac input.

**Data flow**: It receives a physical key code and the set of currently pressed modifiers. It builds a shortcut name like “Shift+Meta+ArrowLeft”, looks it up in the Mac editing command table, removes the trailing colon used in the source table, and filters out insert commands. It returns a list of command names for Chrome to include in the key event.

**Call relations**: key_down calls this only when the caller says the target system is Mac. Its output is placed into the Chrome keyDown or rawKeyDown event so Mac text editing shortcuts can work correctly.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome event for pressing a key down. It also updates the remembered keyboard state, so later events know which keys and modifiers are being held.

**Data flow**: It receives the mutable KeyboardState, a requested key, and whether the browser should behave like Mac. It asks _description_for for the key’s browser details, checks whether this is an auto-repeat because the key was already down, records the key as pressed, and records modifier keys such as Shift or Control. It then returns a Chrome DevTools Protocol call named Input.dispatchKeyEvent with all needed fields, including text, key code, modifiers, keypad location, repeat status, and Mac commands when relevant.

**Call relations**: press_combo calls this for each key in a shortcut before releasing them. type_text calls it for each short, keyboard-mappable character. Inside, it uses _description_for for key facts, _mac_commands for Mac-specific editing behavior, and modifiers_mask to encode the active modifiers.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome event for releasing a key. It updates the remembered keyboard state so the system no longer thinks that key or modifier is held.

**Data flow**: It receives the mutable KeyboardState and the key to release. It looks up the key details, removes the key from the pressed-keys set, removes it from the pressed-modifiers set if it was a modifier, and returns an Input.dispatchKeyEvent call of type keyUp. The returned event includes the remaining modifier state after the release.

**Call relations**: press_combo calls this after pressing all keys in a shortcut, releasing them in reverse order like a person usually would. type_text calls it immediately after each short character’s key_down event.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string such as “Ctrl+A” or “Shift+Enter” into the full press-and-release event sequence. It accepts common user-facing names and normalizes them to the key names used by the layout table.

**Data flow**: It receives the current KeyboardState, a combo string, and whether the target is Mac. It splits the string on plus signs, trims empty space, converts aliases like “ctrl” to “Control”, and rejects an empty shortcut with a ValidationError. It returns a list of Chrome calls: first key_down for each key in order, then key_up for each key in reverse order.

**Call relations**: This is a higher-level helper built from key_down and key_up. A caller can use it when it wants a full shortcut action rather than manually asking for each individual down and up event.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text into browser input events. It chooses between realistic per-character key presses for short text and a faster direct text insertion for long text.

**Data flow**: It receives the current KeyboardState, the text to type, and whether Mac behavior is needed. If the text is longer than the file’s character limit, it returns one Input.insertText call containing the whole string. For shorter text, it walks through each character: if the character exists in the keyboard layout, it emits key_down and key_up events; otherwise it uses Input.insertText for that single character. It returns the complete list of Chrome calls.

**Call relations**: This function is the text-entry partner to press_combo. It uses key_down and key_up when realistic key events matter, such as triggering autocomplete or key listeners, but bypasses them for long or unmapped text by handing Chrome direct insertText calls.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Action completion checks
Settling logic determines when page activity triggered by an action has finished enough for automation to proceed.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigations, before the next automation step`

Browser automation often needs to click a button, type into a field, or navigate, then wait before taking the next step. Waiting is tricky: if it waits too little, the next step may run before the page is ready; if it waits for every network request to stop, modern pages with ads, analytics, or live feeds may never look “done.” This file solves that balance.

The main class, Settle, keeps a small scoreboard of what happened after the current action. It records foreground requests that matter, pages that are still loading, and pages that have visibly painted. A “paint” is a browser signal that something has appeared on screen. That is treated as the strongest sign that the page is usable.

The file deliberately ignores low-value traffic such as images, fonts, preflight checks, and common analytics hosts. This is like waiting for a restaurant order to arrive, but not waiting for every background radio ad to finish playing.

When asked to wait, Settle first gives the page one quick turn to run queued JavaScript. Then it watches for either a paint event, a quiet network period, or a time limit. If the page paints, it gives foreground requests a short grace period to finish, but it will not wait forever. This keeps automation both patient and practical.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It filters out requests that usually do not affect whether the page is ready, such as images, fonts, low-priority fetches, and analytics beacons.

**Data flow**: It receives the request details reported by Chrome. It checks the request type, priority, and web address host. If the request looks like passive page decoration or analytics, it returns false; otherwise it returns true, so the request will be counted as work the page still needs to finish.

**Call relations**: When Chrome reports that a request has started, Settle.on_request_started asks this function whether the request should join the pending-work scoreboard. This keeps the waiting logic focused on useful page work instead of noisy background traffic.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker. It starts with no pending requests, no recorded loads, and no recorded paint events.

**Data flow**: It takes no outside data. It creates empty sets for pending requests, loading sessions, and painted sessions, plus a counter for how many tracked requests have started. The result is a ready-to-use Settle object with a clean internal scoreboard.

**Call relations**: BrowserSession creates this tracker when a browser session is set up, and also creates a fresh one during close-related cleanup. Other methods then add and remove entries from this tracker as browser events arrive.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the action-specific waiting state so a new browser action can be judged on its own. This prevents old requests or paint events from making the next wait decision misleading.

**Data flow**: It reads the existing internal scoreboard and empties the pending-request list, resets the started-request count to zero, and clears remembered paint events. It does not clear the loading set, so current document loading state can still be respected.

**Call relations**: This is used when the caller wants to begin a new settle window. After reset, event callbacks such as Settle.on_request_started and Settle.mark_painted rebuild the scoreboard for the next action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records that a meaningful network request has begun. This lets the waiting logic know there is still page work in progress.

**Data flow**: It receives Chrome’s request event data and the browser session that produced it. It extracts the request id, checks that the session and id are valid, and asks tracks_request whether the request matters. If so, it adds the session-and-request pair to the pending set and increases the started counter.

**Call relations**: This is called when the browser reports a new request. It hands the filtering decision to tracks_request, then supplies Settle.wait with the pending-request information it uses to decide whether to keep waiting.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records that a network request is no longer pending. This helps the tracker notice when the page has gone quiet after an action.

**Data flow**: It receives Chrome’s finished-request event data and the session id. It extracts the request id and, if both pieces are valid, removes that session-and-request pair from the pending set. The output is a smaller pending-work scoreboard.

**Call relations**: This is called when Chrome says a request completed or otherwise stopped. Settle.wait later reads the pending set; once it becomes empty, the wait can finish after a short gap that allows follow-up requests to appear.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as currently loading a document. This tells the wait logic not to continue too early during a navigation.

**Data flow**: It receives a session id and adds it to the loading set. After that, Settle.wait treats that session as still busy until it is marked loaded.

**Call relations**: This is called by browser event handling code when loading begins. It feeds directly into Settle.wait and Settle._drain_after_paint, which both check the loading set before deciding the page is ready.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as no longer loading its document. This allows the waiting logic to finish once other important work is also done.

**Data flow**: It receives a session id and removes it from the loading set if present. The before state may say “this session is loading”; the after state says it is not considered loading anymore.

**Call relations**: This is called by browser event handling code when loading completes. Settle.wait uses this updated state together with pending requests and paint events to decide whether it can return.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that the browser has painted visible content for a session. Paint is treated as an important sign that the page is usable.

**Data flow**: It receives a session id and adds it to the painted set. From then on, Settle.wait can switch from broad waiting to a short post-paint grace period.

**Call relations**: This is called when Chrome reports a paint lifecycle event. Settle.wait notices this marker and hands off to Settle._drain_after_paint so the page gets a brief chance to finish foreground requests without forcing automation to wait for endless background traffic.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears complete, or until a safety time limit is reached. It is the main decision point that prevents the automation from moving too fast or hanging forever.

**Data flow**: It receives a Chrome DevTools connection, a session id, and a maximum number of seconds to wait. First it asks the page to run one tiny queued JavaScript task, so requests triggered by the action can be noticed. Then it watches the internal scoreboard: if the page painted, it waits only a short grace period; if nothing started and nothing is loading, it returns quickly; otherwise it waits for loading and pending requests to stop, allowing a short gap for follow-up requests. It returns no value, but time passes until the page is judged ready or the deadline arrives.

**Call relations**: This is the method other browser-control code calls after an action. It begins by calling Settle._flush_page_tasks, may hand off to Settle._drain_after_paint when a paint has happened, and otherwise uses sleep intervals and the recorded event state filled by request, loading, and paint callbacks.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After visible content appears, gives important remaining work a short chance to finish. This avoids calling a page ready the instant a blank shell paints, while still avoiding long waits on pages that never become fully quiet.

**Data flow**: It receives a session id and an absolute deadline. It creates a shorter grace deadline and repeatedly checks whether the session is still loading or has pending important requests. If the page becomes quiet and stays quiet through a short chain gap, it returns; otherwise it returns when the grace time runs out.

**Call relations**: Settle.wait calls this once it sees that the session has painted. This helper is the post-paint path: it narrows the waiting strategy from “watch for readiness” to “give useful foreground work a brief final window.”

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the web page one quick turn to run JavaScript tasks that may have been triggered by the user action. This helps ensure the tracker has seen any immediate network requests before deciding whether the page is quiet.

**Data flow**: It receives the Chrome DevTools connection and a session id. It sends a small JavaScript expression that resolves after a zero-delay timer, and waits for the result. If Chrome cannot run it or times out, it simply sleeps for a short beat instead. It returns no value, but it gives browser events time to arrive in order.

**Call relations**: Settle.wait calls this at the very start of waiting. It uses Cdp.send to ask Chrome to evaluate JavaScript in the page, and its result makes later checks of pending requests more reliable.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).

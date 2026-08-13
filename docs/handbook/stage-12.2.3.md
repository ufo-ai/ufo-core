# Browser Action Execution and Input Translation  `stage-12.2.3`

This stage is the hands and keyboard of the browser automation system. After another part of the system decides what should happen next, this stage turns that decision into real Chrome input, such as moving the mouse, clicking, typing, scrolling, filling a form, or uploading a file.

The main runner is computer.py. It takes actions like “click here” or “type this,” sends the right input events to the browser, then captures a fresh screenshot and reports any warnings. coordinate.py acts like a map legend. It converts positions from the resized screenshot seen by the AI back to the browser’s true screen coordinates, so clicks land in the right place. fixup.py is a safety checker. It cleans up small mistakes in planned actions before they reach Chrome. keys.py translates everyday keyboard instructions, such as “Ctrl+A” or typed text, into Chrome’s exact key event format. forms.py handles direct form work, including setting field values and attaching files to upload boxes. Together, these pieces make browser actions precise, safe, and repeatable.

## Files in this stage

### Action Execution
Runs normalized browser actions against Chrome and maps model-visible coordinates to real browser positions.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the browser-control bridge for an automated assistant. The assistant thinks in model-friendly actions and coordinates, but the browser needs exact Chrome DevTools Protocol commands, which are low-level messages sent to the browser. This file sits between those two worlds.

The main class, BrowserComputer, receives a batch of requested actions. It chooses the right browser tab, checks and cleans up the action list, converts coordinates between the model’s screen size and the actual browser viewport, then performs each action. Clicks become mouse press and release events. Typing becomes keyboard events. Drags are sent as several small moves, because many web pages only recognize drag-and-drop when the pointer moves gradually. Scrolls become mouse wheel events.

After each group of actions, it waits for the page to settle, meaning it gives the page time to react, load, or update. Then it checks for downloads, sign-in pages, repeated scrolling, JavaScript dialogs, and native dropdowns that need special handling. Finally, it captures a screenshot. If there was a click, it draws a small blue mark on the screenshot so the assistant can see exactly where it clicked. Without this file, the assistant would have no reliable way to operate the browser or understand the result of its actions.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This describes the session method that returns the browser tab the computer should act on. It is part of a protocol, meaning this file says what shape a browser session must have without providing the actual implementation here.

**Data flow**: It receives an optional tab id. The real session implementation uses that id to find or choose a tab, then returns a tab object with a session id and keyboard state.

**Call relations**: BrowserComputer.run calls this at the start of a request so all later clicks, key presses, and screenshots go to the correct tab.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This describes how to get the live browser connection used to send commands. The connection is the pipe to Chrome DevTools Protocol, the browser’s remote-control interface.

**Data flow**: It takes no extra input from the caller. The real session returns a connection object, which other functions use to send browser commands and receive replies.

**Call relations**: BrowserComputer.run, BrowserComputer.act, BrowserComputer._select_reminder, BrowserComputer._dispatch, and BrowserComputer._mouse_event rely on this connection whenever they need the browser to do something or report something.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This describes the method that gathers summary information about a tab for the final response. That information is returned alongside the screenshot and action output.

**Data flow**: It receives a tab object. The real session reads useful details about that tab and returns them as a dictionary that can be sent back to the caller.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the response includes current tab details.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This describes the method that returns the titles of open browser tabs. This file uses those titles to notice when the user may be near a sign-in or sign-up flow.

**Data flow**: It takes no direct input. The real session reads tab titles from the browser and returns them as a list of strings.

**Call relations**: BrowserComputer.run calls this after actions finish, then passes the titles to sign_in_warning to decide whether to add a safety reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This describes a helper for running a JavaScript function on a specific browser object. Here it is used to inspect a clicked dropdown element.

**Data flow**: It receives a browser session id, an object id from the browser, a JavaScript function as text, and optional arguments. The real implementation asks the browser to run that function on that object and returns the result as a dictionary.

**Call relations**: BrowserComputer._select_reminder calls this after finding a native select element, so it can read the dropdown’s option text and build a useful reminder.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This describes how a page reference, such as a short id returned by page-reading tools, is turned back into a real browser node. It lets actions target page elements without relying only on screen coordinates.

**Data flow**: It receives a tab and a reference string. The real session looks up that reference and returns the browser node plus its backend node id, which Chrome can use.

**Call relations**: BrowserComputer.act uses this during a scroll_to action, when it needs to ask the browser to bring a referenced element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This describes how to find a clickable point for a referenced page element. It lets the assistant say “click element e12” instead of manually calculating screen coordinates.

**Data flow**: It receives a tab and a reference string. The real session finds the element’s on-screen position and returns an x,y point in viewport coordinates.

**Call relations**: BrowserComputer.point calls this when an action includes a ref, and BrowserComputer.act then uses the returned point for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for performing a batch of browser actions. It validates the request, performs the actions, waits for page reactions, captures the updated screenshot, and returns a human-readable result.

**Data flow**: It receives a dictionary containing a tab id and an actions list. It chooses the tab, validates each action, adjusts coordinates, runs actions in batches, waits for the page to settle, checks downloads and warnings, captures a screenshot, optionally marks the last click, and returns tab information, output text, last click position, and screenshot data.

**Call relations**: This function is the conductor. It calls int_or_none to read the tab id, uses BrowserComputer.act for each action, asks BrowserComputer._select_reminder for dropdown advice after clicks, calls sign_in_warning for safety messaging, uses BrowserComputer._to_model for reporting coordinates, and calls mark_click through a background executor so image editing does not block the async browser flow.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one requested browser action. It understands the action type and chooses the right lower-level browser operation, such as clicking, typing, dragging, waiting, scrolling, or taking a screenshot.

**Data flow**: It receives a tab and one parsed ComputerAction. It first finds the target point if needed, then turns the action into browser input or browser commands. It returns a short message describing what happened and, for clicks or drags, the final viewport point that was touched.

**Call relations**: BrowserComputer.run calls this for every action in a batch. Depending on the action, it hands work to BrowserComputer.point, BrowserComputer._click, BrowserComputer._drag, BrowserComputer._scroll, BrowserComputer._dispatch, BrowserComputer._to_viewport, BrowserComputer._to_model, require_point, or require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds the screen point an action should use. It supports both element references and direct coordinates.

**Data flow**: It receives a tab and an action. If the action has a ref, it asks the browser session for that element’s point. If it has a model coordinate, it converts that coordinate to viewport coordinates. If neither exists, it returns nothing.

**Call relations**: BrowserComputer.act calls this before actions that may need a target location. It uses BrowserComputer._to_viewport for coordinate conversion when the action supplies coordinates directly.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts a coordinate from the model’s screen scale to the browser viewport’s actual scale. It keeps clicks from landing in the wrong place when the assistant and browser use different screen sizes.

**Data flow**: It receives an x,y coordinate in model space. It applies the known model size and viewport size, then returns the matching x,y point in viewport space.

**Call relations**: BrowserComputer.point uses this for direct action coordinates, and BrowserComputer.act uses it for drag start points and default scroll positions.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts a browser viewport coordinate back into the model’s coordinate scale. It is mainly used for reporting results in the same coordinate system the assistant understands.

**Data flow**: It receives an x,y point in viewport space. It converts it using the viewport size and model size, then returns the matching model-space point.

**Call relations**: BrowserComputer.act uses this to describe click and drag results. BrowserComputer.run uses it to report the last click in the final response.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click landed on a native HTML select dropdown and, if so, builds a reminder about the safer way to choose an option. This matters because native dropdown option clicks may not work through this browser-control path.

**Data flow**: It receives a tab and the clicked point. It asks the browser which element is at that point, walks up to a select element if one exists, reads a sample of its options, gets a page reference if possible, and returns a reminder string. If anything cannot be inspected safely, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses BrowserComputerSession.call_on through the browser session and then hands the collected option list to select_reminder.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared list of keyboard-related browser commands. It is the final delivery step for typing text or pressing key combinations.

**Data flow**: It receives a tab and a list of Chrome DevTools Protocol calls. It sends each method and parameter set to the browser connection for that tab. It returns nothing, but the browser receives the keyboard input.

**Call relations**: BrowserComputer.act calls this for type and key actions after other helpers have translated the text or key combo into concrete browser commands.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one mouse event to the browser, such as moving, pressing, releasing, or using the wheel. It is the shared low-level helper behind clicks, drags, and scrolls.

**Data flow**: It receives a tab and a dictionary of mouse event parameters. It sends those parameters to the browser connection as an Input.dispatchMouseEvent command. It returns nothing, but the browser acts as if the mouse event happened.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build complete human-like mouse gestures out of individual events.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a mouse click at a specific viewport point. It supports left, right, double, and triple clicks by sending the correct sequence of mouse events.

**Data flow**: It receives a tab, x and y coordinates, a button name, and a click count. It reads the current keyboard modifier state, moves the mouse to the point, then sends matching press and release events one or more times. It returns nothing, but the browser receives the click.

**Call relations**: BrowserComputer.act calls this for click actions. This function delegates each individual mouse message to BrowserComputer._mouse_event and uses the keyboard modifier mask so clicks can respect held keys like Shift or Control.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one point to another. It moves in several small steps because many web pages only detect drag-and-drop after gradual movement.

**Data flow**: It receives a tab, a start point, and an end point. It reads keyboard modifiers, moves to the start, presses the left mouse button, sends several intermediate move events while holding the button, and releases at the end. It returns nothing, but the browser sees a real drag gesture.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. BrowserComputer._drag uses BrowserComputer._mouse_event for each piece of the gesture and includes modifier state so dragging with held keys behaves correctly.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This performs a mouse-wheel scroll at a specific point in the viewport. It can scroll vertically or horizontally depending on the deltas it receives.

**Data flow**: It receives a tab, x and y coordinates, and horizontal and vertical scroll amounts. It reads keyboard modifiers, moves the mouse to the point, then sends one wheel event with those scroll deltas. It returns nothing, but the page scrolls.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating direction and distance. BrowserComputer._scroll uses BrowserComputer._mouse_event to send the actual browser messages.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser may be on a login, sign-in, registration, or sign-up page. It returns a safety reminder because the assistant should not sign in or create accounts without user confirmation.

**Data flow**: It receives a list of tab titles. It lowercases them, searches for sign-in-related keywords, and returns a warning string if any title matches. If there is no match, it returns nothing.

**Call relations**: BrowserComputer.run calls this after collecting current tab titles, then includes the warning in the final output if needed.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This writes the actual reminder shown when the user clicked a native select dropdown. It explains how to choose an option using form_input instead of trying to click dropdown options.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total option count. It formats a short instruction, includes the first options, notes if more options exist, and returns the finished reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked select element and gathered its option text.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This draws a small blue dot on a screenshot at the last clicked point. It gives the assistant visual feedback about where its click landed.

**Data flow**: It receives a base64-encoded screenshot and a viewport point. It decodes the image, draws a translucent circle over that point, saves the result as a JPEG, encodes it back to base64, and returns the new screenshot string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click. It runs it in a background executor because image editing is regular CPU work and the rest of the browser controller is asynchronous.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This converts a possible tab id into an integer, or returns nothing if no usable value was provided. It accepts ids that arrive as numbers or non-empty strings.

**Data flow**: It receives a JSON-like value. If the value is an int, float, or non-empty string, it converts it to an int. Otherwise it returns None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page, so a request can identify a tab even if the tab id came in as a string or float.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action has a usable target point. It turns a missing point into a clear validation error instead of letting a later click fail mysteriously.

**Data flow**: It receives a point that may be missing and the action name. If the point exists, it returns it unchanged. If it is missing, it raises a validation error explaining that the action needs a coordinate or reference.

**Call relations**: BrowserComputer.act calls this before actions such as clicks and drag endings that cannot work without a target point.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used when an action needs a separate coordinate, such as the starting point of a drag.

**Data flow**: It receives a coordinate that may be missing and the field name to report. If the coordinate exists, it returns it unchanged. If it is missing, it raises a validation error naming the required field.

**Call relations**: BrowserComputer.act calls this for drag actions before converting the drag start coordinate into viewport space.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting`

Browser automation often asks a vision model to look at a screenshot and point to something, like a button. The hard part is that the model may not see the screenshot at the browser's exact pixel size. Some model services shrink large images before processing them, and Gemini uses its own fixed 0-to-1000 coordinate grid. If the system used the model's coordinates directly, clicks could land in the wrong place.

This file is the measuring tape that keeps those spaces aligned. It defines two tiny value objects: Size, for width and height, and Coord, for x and y positions. It then works out the screenshot size that Claude-family models effectively see without hidden server-side shrinking. It also knows that Gemini reports points on a fixed square grid.

The main idea is simple: before sending a browser action, scale the model's point up or down to match the real browser viewport. In the other direction, if the browser has a pixel position and the system needs to describe it to the model, scale it into the model's coordinate space. Like converting inches to centimeters, the meaning is the same, but the numbers must change.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Figures out the largest screenshot size Claude-family vision models can receive without the service shrinking it further. This helps the rest of the system use the same pixel grid the model actually sees.

**Data flow**: It takes a browser viewport size as input. It first shrinks the width and height if either side is longer than Claude's long-edge limit, then shrinks again if the total pixel count is still too high. It returns a new Size containing the final width and height.

**Call relations**: When no explicit model coordinate size is supplied, effective_model_size calls this function to decide the model's working image size. The result is then used by coordinate conversion functions so browser clicks match what the model saw.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Chooses the coordinate space the model is using. It uses a caller-provided size when there is one, otherwise it calculates the screenshot size from the browser viewport.

**Data flow**: It receives the viewport size and, optionally, a model-specific size. If the optional size exists, it passes that through unchanged. If not, it asks compute_screenshot_dimensions for the best Claude-style screenshot size and returns that.

**Call relations**: Both model_to_viewport and viewport_to_model call this first so they can agree on which coordinate grid they are converting from or to. It acts as the shared rule for deciding the model's effective canvas.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point reported by the model into real browser viewport pixels. This is used when the automation needs to turn a model answer into an actual click or pointer position.

**Data flow**: It takes a model-space coordinate, the browser viewport size, and optionally a model coordinate size. It finds the effective model size, then scales x by the ratio between viewport width and model width, and y by the ratio between viewport height and model height. It returns a new Coord in browser pixels.

**Call relations**: This function calls effective_model_size before doing the conversion. It is the outward path from AI reasoning to browser action: the model says where something is, and this function turns that into the position the browser input system can use.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Returns the special coordinate grid used by a named model, when the model has one. In particular, it recognizes Gemini models, which report points on a fixed 1000-by-1000 grid rather than screenshot pixels.

**Data flow**: It receives a model name, or no model name. If the name contains "gemini" in any letter case, it returns a Size of 1000 by 1000. Otherwise, it returns None, meaning the usual screenshot-size rules should be used.

**Call relations**: This function provides the optional model_size value that other coordinate conversion helpers can use. It separates model-specific knowledge from the general scaling functions, so the conversion code does not need to know every model's naming rule.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser pixel position back into the model's coordinate space. This is useful when the system needs to describe or compare browser locations using the same grid the model uses.

**Data flow**: It takes a browser viewport coordinate, the viewport size, and optionally a model coordinate size. It finds the effective model size, then scales x by the ratio between model width and viewport width, and y by the ratio between model height and viewport height. It returns a new Coord in model-space numbers.

**Call relations**: Like model_to_viewport, it starts by calling effective_model_size so it uses the correct grid. It is the reverse path: from browser reality back into the coordinate language the model understands.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Action Fixups
Cleans and adjusts model-produced browser actions before they are dispatched.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `before browser action dispatch`

This file acts like a proofreader for a list of browser commands. A model may ask the browser to type somewhere, scroll, wait, or click, but it may leave out details that a real browser needs. Without this cleanup step, typing might happen in the wrong place, scrolling might have no starting point, waits might be too short or undefined, and text such as "\\n" might appear literally instead of becoming a real newline.

The main function, fixup_actions, walks through a batch of ComputerAction objects and repairs common problems. If a typing action names a target but was not preceded by a click, it adds a left click first so the target is focused. If typed text contains escaped forms like "\\t" or "\\n", it turns them into real tab or newline characters. If a scroll has no coordinate, it uses the center of the model’s effective screen size. If a scroll_to action has no reference target, it is converted into a normal scroll. If a wait has no duration, it gets a default of three seconds. If a double-click or triple-click points to a reference without coordinates, it is simplified to a single left click on that reference.

The second public helper, split_at_waits, breaks a cleaned action list into smaller batches ending at waits, giving the browser time to settle between chunks.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: Repairs a list of browser actions before they are carried out. It fills in missing details and rewrites fragile actions into simpler, safer ones.

**Data flow**: It receives a list of ComputerAction objects, the browser viewport size, and optionally the model’s own screen size. It first works out the effective model size and uses its center as a safe fallback point. Then it reads each action in order, possibly adding a focus click before typing, converting escaped text, adding missing scroll coordinates, replacing incomplete scroll_to actions, giving waits a default duration, or simplifying reference-based multi-clicks. It returns a new list of actions, leaving the caller with a cleaned sequence ready to dispatch.

**Call relations**: This is the main cleanup step in the file. When it needs to create a focusing click, it hands that job to _focus_click. When it needs to repair typed text, it hands that job to _unescape_text. It also relies on effective_model_size to choose a sensible coordinate system, and it creates ComputerAction and ScrollParameters objects when an action must be rewritten.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: Splits a list of actions into smaller groups so each group ends after a wait action. This lets the browser pause and settle before the next group begins.

**Data flow**: It receives one ordered list of ComputerAction objects. It builds a current batch one action at a time. Whenever it sees a wait action, it closes that batch and starts a new one. At the end, it includes any leftover actions that did not end with a wait. The result is a list of action batches.

**Call relations**: This function is independent of the repair helpers. It is meant to be used after or around action preparation, when the larger browser flow wants to dispatch actions in stages instead of sending one long uninterrupted list.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: Creates a simple left-click action that focuses the same place a typing action wants to type into. This helps make sure later text input goes to the intended target.

**Data flow**: It receives a ComputerAction, usually a type action that has either a coordinate or a reference target. If the action has a coordinate, it creates a new left-click at that coordinate. Otherwise, it creates a new left-click aimed at the same reference. The output is a fresh ComputerAction representing that focusing click.

**Call relations**: fixup_actions calls this when it sees typing aimed at a location or reference but no recent click that would focus the input area. _focus_click does the small construction step, then hands the new click action back to fixup_actions to insert before the typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: Turns literal escape sequences in typed text into the real characters they stand for. For example, it changes the two visible characters "\\n" into an actual newline.

**Data flow**: It receives a ComputerAction and reads its text field. If the text does not contain any known escaped literals, it returns the original action unchanged. If it finds escaped tab or newline markers, it replaces them with real tab or newline characters and returns a copied action with the updated text.

**Call relations**: fixup_actions calls this for type actions after any needed focus click has been added. _unescape_text uses the action’s model_copy method to preserve the rest of the action while changing only the text.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Form and Keyboard Input
Translates form, upload, and keyboard requests into safe browser commands and Chrome key events.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling during browser form actions`

Web pages do not expose form fields as simple Python objects. To change them, this file has to find the right page element, ask the browser for a usable handle to it, and then run a small piece of JavaScript inside the page. Think of it like giving instructions to someone inside the browser window: “find this field, type this value, and tell the page the field changed.”

The file defines a small expected browser interface, `BrowserFormSession`, so the form code does not need to know the full browser implementation. It only needs a current page, a Chrome DevTools Protocol connection, a way to run JavaScript on a page element, and a way to turn a saved element reference into a real browser node. The Chrome DevTools Protocol, or CDP, is the control channel used to ask Chrome-like browsers to inspect and change pages.

`BrowserForms` provides the actual actions. `input` fills text boxes, checkboxes, radio buttons, select boxes, and editable content, then fires the page’s normal input and change events so website code notices the update. `upload_file` sets files on a file input and reports a clear user-facing error if the reference was wrong. `attached_sizes` checks what file sizes the browser really sees, which is important because remote file uploads may look named before the bytes have fully arrived.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It promises that a browser session can return the page, or tab, that a form action should work on.

**Data flow**: It receives an optional tab id. An implementation uses that id, if present, to choose a browser tab, then returns the page object for later element lookup.

**Call relations**: The form action methods in `BrowserForms` depend on this capability before they can touch any form field. The protocol itself does not perform the lookup; it states what the real browser session must provide.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It gives form code access to the browser control connection used to send low-level commands.

**Data flow**: It takes no extra input. An implementation returns a CDP connection object, which later receives commands such as resolving a page node or setting files on an upload input.

**Call relations**: `BrowserForms.attached_sizes`, `BrowserForms.upload_file`, and `BrowserForms.input` all need this kind of connection when they ask the browser to work with real page elements. This protocol method describes that dependency without tying the file to one concrete browser class.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It promises a way to run a JavaScript function on a specific page element.

**Data flow**: It receives a browser session id, an object id for a page element, JavaScript source code, and optional arguments. The implementation runs that JavaScript against the element and returns the result as a JSON-like dictionary.

**Call relations**: `BrowserForms.attached_sizes` uses this ability to inspect the files attached to an input, and `BrowserForms.input` uses it to set a field value and trigger page events. The protocol defines the hook; the browser session supplies the actual behavior.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a saved page reference into the browser node needed for real work.

**Data flow**: It receives a page object and a reference string that came from an earlier page read. The implementation finds the matching page element and returns both its browser session information and its backend node id, which is the browser’s internal identifier for that element.

**Call relations**: Every main form action starts by resolving a reference this way, because user-facing references are not enough for Chrome’s low-level commands. The protocol keeps this file focused on form behavior rather than on how references are stored.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks which files a file upload input is truly holding by asking the browser for the byte sizes of the attached files. It is used to tell the difference between a file name that has been requested and a file whose data has really arrived in the browser.

**Data flow**: It reads `tab_id` and `ref` from the input dictionary. `_tab_id` normalizes the tab id, `as_str` validates the reference, the browser session resolves that reference to a real page node, and the CDP connection turns the node into a JavaScript object. It then runs a small script on the element and returns a list of numeric file sizes, or an empty list if the browser did not return sizes in the expected shape.

**Call relations**: When an upload needs verification, this method is the checker. It calls `_tab_id` to interpret the optional tab choice, uses wire helpers such as `as_str` and `as_map` to safely read incoming JSON-like data, then hands off to the browser connection and `call_on` to inspect the live page element.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a web page’s file input. If the given browser reference is not actually a file input, it raises a clear `HallucinationError`, meaning the automation tried to act on something that was not present or was not the right kind of element.

**Data flow**: It reads `tab_id`, `ref`, and `files` from the input dictionary. `_tab_id` chooses the tab, `as_str` validates the reference and each file path, and `as_list` ensures the file list really is a list. After resolving the reference to a browser node, it sends the browser command `DOM.setFileInputFiles`; on success it returns the reference and the file paths it set, and on a CDP failure it turns the low-level error into a helpful message.

**Call relations**: This is the main upload action. It calls `_tab_id` and the wire validation helpers before handing the real work to the browser’s CDP connection. If CDP reports that the operation is invalid, it creates a `HallucinationError` so the caller knows to re-read the page and use a proper file-input reference.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a form-like page element, such as a text box, checkbox, radio button, select menu, or editable area. It also fires normal browser events so the website reacts as if a person changed the field.

**Data flow**: It reads `tab_id`, `ref`, and `value` from the input dictionary. `_tab_id` normalizes the tab id, `as_str` validates the reference, and the browser session resolves that reference to a real page node. The method asks CDP to turn the node into a JavaScript object, then runs the form-input script with the requested value and returns the script’s reply; if the reference cannot be resolved, it raises a helpful `HallucinationError`.

**Call relations**: This is the main “fill in this field” action. It calls `_tab_id` and wire helpers to clean up incoming data, asks the browser connection to resolve the element, and then uses `call_on` to run the JavaScript that actually changes the page.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper converts an optional tab id from incoming JSON-like data into either an integer tab id or `None`. It lets callers accept tab ids whether they arrive as a number or as a non-empty string.

**Data flow**: It receives a possible JSON value. If the value is an integer, it returns it; if it is a float or a non-empty string, it converts it to an integer; otherwise it returns `None`, meaning no specific tab was requested.

**Call relations**: `BrowserForms.attached_sizes`, `BrowserForms.upload_file`, and `BrowserForms.input` all call this at the start of their work. It keeps tab-id cleanup in one place so the rest of each action can simply ask the browser for the right page.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

A browser does not understand “press A” in the same loose way a person does. Chrome expects a detailed message with the key name, physical key code, modifier keys such as Shift or Control, whether the key is on the number pad, and sometimes Mac-specific editing commands. This file builds those messages.

It starts with a US keyboard map: a lookup table that says what each physical key means, including shifted versions like “1” becoming “!”. It also includes Mac editing shortcuts, such as Command+A meaning “select all”. From that raw table, the file builds a more convenient lookup so callers can ask for keys by code, visible character, or common alias.

The central idea is a small `KeyboardState`, which remembers which keys and modifier keys are currently held down. Functions such as `key_down` and `key_up` update that state and return Chrome DevTools Protocol calls. The Chrome DevTools Protocol, or CDP, is the control channel used to tell Chrome what to do. Higher-level helpers then use those building blocks to press combinations or type text. For long text, the file deliberately uses one direct insert call, like pasting, because key-by-key typing would be slow.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds a practical key lookup table from the raw US keyboard layout. It lets later code find a key by physical code, visible character, or common alias such as “Enter” for newline.

**Data flow**: It receives the raw keyboard layout table. For each key, it creates a `KeyDescription` with the information Chrome needs, adds a shifted version when there is one, and stores extra names that should point to the same key. The result is a larger lookup dictionary used by the rest of the file.

**Call relations**: This function prepares the key map that later typing functions rely on. While building the map, it creates `KeyDescription` objects and uses `dataclasses.replace` to make safe variations, such as shifted versions, without rewriting every field by hand.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Turns the currently held modifier keys into the number Chrome expects. A modifier key is a key like Shift, Control, Alt, or Meta that changes what another key does.

**Data flow**: It receives a set of modifier names. It checks which known modifier names are present, adds their assigned bit values together, and returns one integer that represents the whole set.

**Call relations**: When `key_down` and `key_up` prepare a CDP key event, they call this helper so Chrome can see exactly which modifier keys are held at that moment.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the full Chrome-ready description for a requested key, taking the current keyboard state into account. This is where “a” can become “A” if Shift is held.

**Data flow**: It receives the current `KeyboardState` and a key name or character. It looks up the key in the prepared layout table, raises a validation error if the key is unknown, applies the shifted version when Shift is down, and removes normal text output when non-Shift modifiers are held. It returns the final key description to use in an event.

**Call relations**: `key_down` and `key_up` both call this before producing browser events. It is the translator between a caller’s simple key request and the detailed fields Chrome needs.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Finds Mac-specific editing commands for a key press, such as copy, paste, move cursor, or select text. These commands help synthesized input behave like native Mac keyboard input.

**Data flow**: It receives a physical key code and the set of currently held modifiers. It builds a shortcut name like “Shift+Meta+ArrowLeft”, looks that up in the Mac command table, removes the trailing colon used by the source command names, and returns the commands that are not direct text insertion commands.

**Call relations**: `key_down` calls this only when the target platform is Mac. The returned command names are included in the CDP key event so Chrome can perform Mac-style editing behavior.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome event for pressing a key down. It also updates the remembered keyboard state so future keys know what is already being held.

**Data flow**: It receives the current keyboard state, a requested key, and whether the browser should behave like a Mac. It gets the key description, checks whether this is an auto-repeat because the key was already down, records the key as pressed, records modifier keys when needed, adds Mac editing commands when appropriate, and returns one CDP call for `Input.dispatchKeyEvent`.

**Call relations**: `press_combo` uses this to press each key in a shortcut, and `type_text` uses it for characters that can be typed as normal keyboard keys. Inside, it relies on `_description_for` for key details, `_mac_commands` for Mac behavior, and `modifiers_mask` for the modifier number sent to Chrome.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome event for releasing a key. It also cleans up the remembered keyboard state so the system no longer thinks the key is held down.

**Data flow**: It receives the current keyboard state and a requested key. It finds the matching key description, removes that key and modifier from the pressed-state records, and returns one CDP `Input.dispatchKeyEvent` call describing the key release.

**Call relations**: `press_combo` calls this after pressing shortcut keys, in reverse order, and `type_text` calls it after each per-character key press. It uses `_description_for` to identify the key and `modifiers_mask` to report the remaining modifiers to Chrome.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string such as “Ctrl+Shift+A” into a sequence of key-down and key-up browser events. This lets callers describe shortcuts in a familiar, human-readable way.

**Data flow**: It receives the keyboard state, a combo string, and whether Mac behavior is needed. It splits the combo on plus signs, normalizes aliases like “ctrl” to “Control”, rejects an empty combo, presses each key in order, then releases them in reverse order. It returns the complete list of CDP calls.

**Call relations**: This is a higher-level helper built from `key_down` and `key_up`. Callers use it when they want a whole shortcut, and it delegates the detailed event creation to the lower-level functions.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a string of text into browser input events. It chooses between realistic key-by-key typing for short text and a faster direct text insert for long text.

**Data flow**: It receives the keyboard state, the text to type, and whether Mac behavior is needed. If the text is longer than the configured limit, it returns one `Input.insertText` call. Otherwise, it walks through each character: characters known in the keyboard layout become key-down and key-up events, while unknown characters are inserted directly. It returns the list of CDP calls.

**Call relations**: For short text, this function calls `key_down` and `key_up` so page key handlers and autocomplete can react as if a person typed. For longer or unsupported text, it hands Chrome direct insert commands instead, avoiding a slow or impossible key-by-key simulation.

*Call graph*: calls 2 internal fn (key_down, key_up).

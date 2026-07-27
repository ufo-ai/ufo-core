# Browser action execution and input normalization  `stage-10.2.4`

This stage is part of the main work loop, where an automated agent’s plans become real browser actions. It starts with actions.py, which acts like a menu of permitted commands: click, type, scroll, wait, screenshot, and so on. Each command has an expected shape, so bad requests can be caught early. If the model invents an impossible value, errors.py provides a clear custom error for that case.

Before anything reaches the browser, fixup.py tidies common mistakes, such as adding a needed focus step before typing or filling in a missing wait time. coordinate.py then translates the model’s idea of “where” into the browser’s actual pixel positions, accounting for screenshot scaling and model-specific coordinate rules.

computer.py is the main driver. It takes the cleaned action, sends the right mouse, keyboard, scroll, or wait operation, and returns the new page state, screenshot, and safety notes. For keyboard work, keys.py converts text and shortcuts into Chrome’s low-level input messages. Finally, settle.py waits until the page has meaningfully finished reacting, so the next step sees a stable browser.

## Files in this stage

### Action execution orchestration
The main browser computer layer receives high-level actions, dispatches input work, and returns updated page state and safety context.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the bridge between an outside caller that describes browser actions in simple terms and the browser itself, which only understands lower-level commands. Without it, the system could know that it wants to click or type, but it would not know how to perform those actions in a live tab or report what happened afterward.

The main class, BrowserComputer, receives a batch of requested actions. It first chooses the target tab, checks and adjusts the action data, then performs the actions one by one. For mouse work, it converts between the model’s coordinate system and the actual browser viewport, like translating from a map grid to real screen pixels. For keyboard work, it sends the right key events. For scrolling, dragging, waiting, and screenshots, it sends the needed browser commands through Chrome DevTools Protocol, a browser control interface.

After each batch, it waits for the page to settle so the caller does not immediately act on a half-loaded page. It then collects useful feedback: action messages, tab information, new downloads, auto-handled dialogs, sign-in warnings, and a fresh screenshot. If the last action clicked somewhere, it can draw a small blue mark on the screenshot so the caller can see exactly where the click landed.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the session contract. It promises that a browser session can return the active tab, or a specific tab, so actions know where to happen.

**Data flow**: A tab id may come in. The session uses that to find the right browser tab and returns an object with a session id and keyboard state. This file only defines the promise; another class supplies the real behavior.

**Call relations**: BrowserComputer.run relies on this capability at the start of a request. It asks the session for the tab before any click, key press, screenshot, or page lookup can be sent.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session contract. It gives access to the browser control connection used to send low-level browser commands.

**Data flow**: No action data goes in. The session returns a Chrome DevTools Protocol connection, which other methods use to talk to the browser. This file only states that such a connection must exist.

**Call relations**: BrowserComputer.run, BrowserComputer.act, and helper methods use this connection whenever they need the browser to capture a screenshot, move the mouse, scroll, inspect the page, or wait for settling.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is part of the session contract. It promises a summary of the current tab, such as information the caller needs after actions are complete.

**Data flow**: A tab object goes in. The session reads its current state and returns a dictionary of tab details. This protocol method does not define the exact storage or lookup itself.

**Call relations**: BrowserComputer.run calls this near the end, after actions and settling, so the final response includes fresh tab information alongside the screenshot and action output.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This is part of the session contract. It provides the titles of open tabs so the system can notice possible sign-in pages.

**Data flow**: Nothing specific goes in. The session reads browser tab titles and returns them as text strings. This file uses those titles only for safety messaging.

**Call relations**: BrowserComputer.run asks for tab titles after the action batch. It passes them to sign_in_warning so the response can remind the caller to get user confirmation before signing in.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the session contract. It runs a small JavaScript function on a specific page object, such as a form control found in the page.

**Data flow**: A browser session id, an object id, JavaScript source text, and optional arguments go in. The browser session executes that function on the object and returns structured data. The actual execution is provided elsewhere.

**Call relations**: BrowserComputer._select_reminder uses this when it suspects the user clicked a native dropdown. It asks the page for the dropdown’s visible options so it can give a useful reminder.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This is part of the session contract. It translates a page reference, such as an element id returned by page-reading tools, into the browser node needed for direct browser commands.

**Data flow**: A tab and a reference string go in. The session finds the matching browser node and returns both the node and its backend browser id. This file depends on that translation but does not implement it.

**Call relations**: BrowserComputer.act uses this for the scroll_to action. Once the reference is resolved, it can ask the browser to bring that element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This is part of the session contract. It finds a clickable screen point for a referenced page element.

**Data flow**: A tab and a reference string go in. The session locates the element and returns an x,y point in the browser viewport. This protocol only defines what must be returned.

**Call relations**: BrowserComputer.point uses this when an action names a page reference instead of giving coordinates. That point then feeds into click, drag, or scroll behavior.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main action runner. It accepts a batch of browser actions, performs them in order, waits for the page to settle, and returns the result with a screenshot and warnings.

**Data flow**: A JSON-like request comes in with an optional tab id and an actions list. The method validates the actions, fixes coordinates, runs each action, waits between batches when needed, checks downloads and dialogs, captures a screenshot, optionally marks the last click, and returns tab info, text output, last-click coordinates, and screenshot data.

**Call relations**: This is the top-level method other parts of the browser automation system call for action execution. It delegates individual actions to BrowserComputer.act, uses BrowserComputer._select_reminder for dropdown advice, uses sign_in_warning for safety text, uses int_or_none for tab ids, and calls BrowserComputer._to_model when reporting coordinates back to the caller.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one requested browser action. It is the switchboard that decides whether the action means clicking, typing, pressing keys, waiting, scrolling, dragging, taking a screenshot, or scrolling to a referenced element.

**Data flow**: A tab and one validated action go in. The method finds any needed point, sends the right browser input commands, and returns a short human-readable message plus the viewport point if the action ended at a visible location. It may also raise a validation error if required information is missing.

**Call relations**: BrowserComputer.run calls this for every action in a batch. BrowserComputer.act then hands off to helpers such as BrowserComputer._click, BrowserComputer._drag, BrowserComputer._scroll, BrowserComputer._dispatch, BrowserComputer.point, BrowserComputer._to_viewport, and BrowserComputer._to_model depending on the action type.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds where an action should happen on the page. It accepts either a page reference or model coordinates and turns them into browser viewport coordinates.

**Data flow**: A tab and an action go in. If the action has a reference, it asks the browser session for that element’s point. If the action has coordinates, it converts them to viewport pixels. If neither is present, it returns nothing.

**Call relations**: BrowserComputer.act calls this before actions that may need a location. When coordinates need translation, it uses BrowserComputer._to_viewport.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the model’s coordinate space into the browser viewport’s coordinate space. It keeps clicks and scrolls landing in the right place even when the model and browser use different sizes.

**Data flow**: A model-space x,y pair goes in. The method wraps it as a coordinate object, scales it from model size to viewport size, and returns viewport x,y pixels.

**Call relations**: BrowserComputer.act uses this for drag start points and default scroll positions. BrowserComputer.point uses it whenever an action provides direct coordinates.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts browser viewport coordinates back into the model’s coordinate space. It is used when reporting results to the caller in the coordinate system the caller understands.

**Data flow**: A viewport x,y pair goes in. The method scales it from viewport size to model size and returns model x,y coordinates.

**Call relations**: BrowserComputer.act uses this to write click and drag messages. BrowserComputer.run uses it to report the final last_click value.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click landed on a native HTML select dropdown and, if so, prepares a reminder explaining the correct way to choose an option. This matters because clicking dropdown options may not work through this browser automation path.

**Data flow**: A tab and a clicked viewport point go in. The method asks the page what element is at that point, walks up to a select element if one exists, reads some option labels and a browser reference, and returns a reminder string. If the page lookup fails or the clicked element is not a select, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action that has a point. It uses select_reminder to turn the dropdown details into plain guidance for the caller.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared list of keyboard-related browser commands. It is a small delivery loop for typing text or pressing key combinations.

**Data flow**: A tab and a list of browser command calls go in. The method sends each command through the browser connection for that tab. It returns nothing, but the browser receives the key events.

**Call relations**: BrowserComputer.act calls this for type and key actions after keyboard helper functions have built the exact commands to send.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one mouse event to the browser, such as move, press, release, or wheel. It is the common low-level path used by all mouse actions in this file.

**Data flow**: A tab and a dictionary of mouse event details go in. The method sends an Input.dispatchMouseEvent command to the browser for that tab. It returns nothing, but the browser acts as if the mouse event occurred.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build larger gestures from simple mouse events.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a mouse click at a specific viewport point. It supports left, right, double, and triple click behavior by sending the correct press and release events.

**Data flow**: A tab, x and y viewport coordinates, a mouse button name, and a click count go in. The method reads the current keyboard modifiers, moves the mouse to the point, then sends one or more press-and-release pairs. It returns nothing, but the page receives the click gesture.

**Call relations**: BrowserComputer.act calls this for left_click, double_click, triple_click, and right_click actions. It sends each individual mouse event through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop when the pointer travels gradually.

**Data flow**: A tab, start coordinates, and end coordinates go in. The method reads keyboard modifiers, moves to the start, presses the left mouse button, moves through intermediate points, then releases at the end. It returns nothing, but the page receives a realistic drag gesture.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. BrowserComputer._drag builds the gesture by sending many low-level events through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This performs a scroll wheel action at a specific point on the page. It can scroll vertically or horizontally depending on the deltas it receives.

**Data flow**: A tab, viewport x,y coordinates, and horizontal and vertical scroll amounts go in. The method moves the mouse to the scroll point, then sends a wheel event with those amounts. It returns nothing, but the page scrolls.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating the direction and distance. It sends the needed mouse move and wheel events through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser may be on a login or signup page. It returns a safety reminder so the system does not sign in or create an account without user confirmation.

**Data flow**: A list of tab titles goes in. The function lowercases them and checks for words such as “sign in,” “login,” or “register.” It returns the warning text if any title matches, otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after collecting tab titles. Its result may be added to the final output as a system reminder.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This writes a clear instruction for dealing with a native select dropdown. It explains that clicking options will not work here and tells the caller to use a form input action instead.

**Data flow**: An optional element reference, a list of visible option names, and the total option count go in. The function formats a short options preview and chooses instructions based on whether a reference is available. It returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected a clicked dropdown. The returned text is later included by BrowserComputer.run in the response reminders.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This draws a small blue dot on a screenshot at the last clicked point. It helps the caller visually confirm where the browser action landed.

**Data flow**: A base64-encoded screenshot and a viewport x,y point go in. The function decodes the image, draws a translucent circle over the point, saves it again as a JPEG, encodes it back to base64, and returns the new image string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a last clicked point. It runs it outside the main async event loop work so image processing does not block other tasks.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This turns a loose JSON value into an integer tab id when possible. It accepts common forms such as an integer, a float, or a non-empty string.

**Data flow**: A JSON value or nothing goes in. If it is an int, float, or non-empty string, the function converts it to an int. For anything else, it returns nothing.

**Call relations**: BrowserComputer.run uses this before asking the session for a page, so callers can pass tab_id in a few practical JSON-friendly forms.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action has a usable point when the action cannot work without one. It gives a clear validation error instead of letting the action fail later in a confusing way.

**Data flow**: A possible x,y point and an action name go in. If the point exists, it is returned unchanged. If it is missing, the function raises a validation error saying the action requires a coordinate or reference.

**Call relations**: BrowserComputer.act calls this before click and drag actions that must know where to happen.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used for fields where there is no sensible default.

**Data flow**: A possible coordinate and the name of the input path go in. If the coordinate exists, it is returned unchanged. If it is missing, the function raises a validation error naming the missing field.

**Call relations**: BrowserComputer.act calls this for the starting coordinate of a drag action before converting it into viewport coordinates.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### Action normalization and schemas
Action requests are repaired where possible and checked against the allowed browser action definitions before execution.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `request handling`

This file acts like a final proofreader for a list of browser actions. A model may ask the browser to type, scroll, click, or wait, but those instructions are sometimes incomplete or slightly awkward. Without this cleanup step, typing might go nowhere because no input field was focused, a scroll might not know where on the page to start, or a wait might have no duration.

The main function, `fixup_actions`, walks through each requested action and rewrites only the cases that are known to cause trouble. If it sees a typing action aimed at a location or page element, but there was no click just before it, it inserts a click first so the target is focused. If text contains the two visible characters `\n` or `\t`, it turns them into a real newline or tab. If a scroll has no position, it uses the middle of the model's view as a safe default. If `scroll_to` has no target reference, it becomes a normal scroll. If a wait has no duration, it gets a default of three seconds.

The file also provides `split_at_waits`, which breaks one long action list into smaller batches ending at wait actions. This lets the browser settle between groups, much like pausing between steps in a recipe before checking the result.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: This function repairs a batch of browser actions before they are dispatched. It makes reasonable guesses for missing details and adds small helper actions when needed so the browser is more likely to do what the user intended.

**Data flow**: It receives a list of `ComputerAction` objects, the current viewport size, and optionally the model's own coordinate size. It first works out the effective model size, then walks through the actions one by one. It may add a focus click before typing, replace visible escape text with real control characters, fill in missing scroll positions, convert an unusable `scroll_to` into a normal scroll, give empty waits a default duration, or simplify multi-clicks that point to a reference. It returns a new list of corrected actions and does not edit the original list directly.

**Call relations**: This is the main cleanup step in the file. When it needs to create a focus click, it calls `_focus_click`; when it needs to clean typed text, it calls `_unescape_text`. It also relies on `effective_model_size` to find a sensible center point for scroll actions, and creates new `ComputerAction` and `ScrollParameters` objects when replacing or filling in actions.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: This function divides a list of actions into smaller groups, with each wait action ending its group. It is useful when the browser should be given time to update before the next actions run.

**Data flow**: It receives one ordered list of actions. It builds a current batch, adding actions until it sees an action whose name is `wait`. At that point it saves the batch and starts a new one. After all actions are read, it returns a list of batches, including any final batch that did not end with a wait.

**Call relations**: This function is independent from the repair logic in `fixup_actions`. It is used as a batching helper after actions have been assembled, so later browser-dispatch code can run a group, pause at the wait, then continue with the next group.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper creates a left-click action aimed at the same place or page element as a typing action. Its job is to focus the target before text is typed.

**Data flow**: It receives a `ComputerAction`, usually a typing action that has either a coordinate or a reference to a page element. If there is a coordinate, it creates a new left-click at that coordinate. Otherwise, it creates a left-click using the same reference. The result is a new `ComputerAction` that can be inserted before typing.

**Call relations**: `fixup_actions` calls this helper when it sees typing that appears to target a field but was not immediately preceded by a click. `_focus_click` hands back a simple click action, which `fixup_actions` places into the outgoing action list before the original typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns visible escape sequences in typed text into the real characters they mean. For example, the two characters `\n` become an actual line break.

**Data flow**: It receives a `ComputerAction` and reads its text field. If the text does not contain supported escape literals, it returns the action unchanged. If it finds `\t` or `\n`, it replaces them with a real tab or newline and returns a copied action with the corrected text.

**Call relations**: `fixup_actions` calls this helper for every typing action. The helper uses the action's copy method to preserve the rest of the action while changing only the text, then gives the corrected action back to `fixup_actions` for the final output list.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a menu and order form for controlling a browser. The menu says which actions are allowed: left click, right click, type text, press a key combination, scroll, drag, take a screenshot, and so on. The order form says what details each action may need, such as a screen coordinate, an element reference, text to type, or how far to scroll.

The main reason this exists is safety and clarity. Instead of passing around loose dictionaries where fields may be missing, misspelled, or impossible, the code uses Pydantic models. Pydantic is a validation library: it checks that incoming data has the expected shape before other code tries to use it. For example, a wait duration must be between 0 and 30 seconds, and a scroll amount must be between 0 and 5 screen-heights or the special value "max".

The file defines `ScrollParameters` for scroll direction and distance, and `ComputerAction` for the full action request. It also defines `ActionType`, the list of legal action names, plus `CLICK_ACTIONS`, a small set used to recognize click-like actions. Without this file, different parts of the browser automation system would have no shared contract for what an action looks like, making mistakes harder to catch and browser control less predictable.


### Coordinate mapping and invalid values
Model-facing coordinates are translated into browser pixels, with a dedicated error type for impossible model-provided values.

### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting`

When an AI model looks at a browser screenshot, it may not be seeing the browser at its original size. Some vision models shrink large images before processing them, and some models report positions on their own fixed grid instead of using screenshot pixels. This file keeps those coordinate systems lined up.

It defines two tiny data shapes: `Size`, meaning a width and height, and `Coord`, meaning an x and y position. The main job is scaling. If the browser viewport is large, `compute_screenshot_dimensions` works out the largest screenshot size that can be sent without being silently reduced by Claude-family vision models. That is important because a click at “x=500, y=300” only makes sense if everyone agrees what image size those numbers belong to.

For Gemini models, `model_coordinate_space` records a different rule: Gemini uses a fixed 0-to-1000 coordinate grid, no matter the screenshot dimensions. The conversion functions then map points back and forth. `model_to_viewport` turns a model-reported point into actual browser pixels for input. `viewport_to_model` does the reverse, useful when browser positions need to be described in model terms. Like using a map scale, the file makes sure a marked point on the model’s “map” lands on the right spot in the real browser.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Figures out how large a browser screenshot should be so a Claude-family vision model can receive it without the server shrinking it further. This avoids a hidden resize that would make reported coordinates less trustworthy.

**Data flow**: It takes a viewport size with a width and height. It first scales the image down if either side is longer than the model’s long-edge limit, then checks the total pixel count and shrinks again if needed. It returns a new `Size` containing the final screenshot width and height.

**Call relations**: This is the fallback sizing rule used by `effective_model_size`. When no caller provides a custom model coordinate size, the rest of the coordinate conversion flow relies on this function to decide what image size the model is effectively seeing.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Chooses the coordinate space that should be used for model calculations. It uses an explicit model size when one is provided, otherwise it computes the screenshot size from the browser viewport.

**Data flow**: It receives the browser viewport size and, optionally, a model-specific size. If the optional size exists, it returns that unchanged. If not, it sends the viewport to `compute_screenshot_dimensions` and returns the calculated screenshot size.

**Call relations**: `model_to_viewport` and `viewport_to_model` call this before scaling points. It acts as the shared decision point so both directions of conversion use the same idea of what size the model is working in.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point from the model’s coordinate system into real browser viewport pixels. This is what lets a model’s suggested click location become an actual browser action location.

**Data flow**: It takes a model-space coordinate, the real viewport size, and optionally a model coordinate size. It asks `effective_model_size` what coordinate space the model used, scales x by the ratio between viewport width and model width, and scales y by the ratio between viewport height and model height. It returns a new `Coord` in browser pixel coordinates.

**Call relations**: This function is used when information flows from the AI model toward the browser. After `effective_model_size` supplies the model’s working size, this function produces the viewport coordinate that input-dispatch code can use.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Identifies whether a named model uses a special fixed coordinate grid. In particular, it captures the rule that Gemini reports points on a 1000-by-1000 grid instead of screenshot pixels.

**Data flow**: It receives a model name, or nothing. If the name contains “gemini” in any mix of uppercase or lowercase letters, it returns a `Size` of 1000 by 1000. For other models, or when no model name is given, it returns `None`, meaning the normal screenshot-size rule should be used.

**Call relations**: This function provides the optional model size that can later be passed into `model_to_viewport` or `viewport_to_model`. It sits before the conversion step, deciding whether the model needs a special coordinate space.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser pixel position back into the coordinate system the model uses. This is useful when the system needs to describe a browser location in terms the model can understand.

**Data flow**: It takes a viewport coordinate, the viewport size, and optionally a model coordinate size. It asks `effective_model_size` what size the model space is, then scales x and y from browser pixels into that model space. It returns a new `Coord` expressed in model coordinates.

**Call relations**: This function is the reverse path of `model_to_viewport`. It calls `effective_model_size` for the same sizing decision, then hands back a model-space point that can be included in prompts, comparisons, or other model-facing logic.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `request handling`

This file is very small, but it gives an important kind of mistake a clear name. In this project, an AI model may suggest actions for a browser, such as clicking a page element. Sometimes the model may refer to something that does not actually exist, like an invented element reference. That is often called a “hallucination” in AI systems: the model produces an answer that sounds valid but is not grounded in reality.

The file defines `HallucinationError`, which is a more specific form of `ValidationError`. A validation error means “the supplied value did not pass the checks needed for safe use.” By making hallucinations their own error type, the rest of the system can react more precisely. For example, it can report that the model invented a browser element, retry with better context, or stop before doing an unsafe action.

Without this file, these impossible model outputs would likely be lumped together with ordinary bad input. The custom name acts like a labeled warning light on a dashboard: it tells maintainers not just that something is wrong, but what kind of wrong it is.


### Keyboard event transport
Human-style keyboard input is converted into Chrome DevTools Protocol events that behave like real keystrokes.

### `extensions/browser/ufo_ext_browser/bua/keys.py`

`io_transport` · `request handling`

Browser automation cannot just say “type A” and hope the page reacts correctly. Web pages listen for detailed keyboard events: which physical key was pressed, whether Shift or Control is held, what text should appear, whether the key is on the number pad, and, on macOS, which editing command the key means. This file is the translator for that job.

It starts with a map of a standard US keyboard. That map says, for example, that the physical key `KeyA` normally means `a`, but with Shift it means `A`. It also includes special keys such as arrows, Enter, function keys, and number-pad keys. A second map records macOS editing shortcuts, such as Command+A for select all or Option+Backspace for deleting a word.

The file keeps a small `KeyboardState`: which modifier keys are currently held and which physical keys are down. Using that state, it builds Chrome DevTools Protocol calls such as `Input.dispatchKeyEvent` and `Input.insertText`. In everyday terms, it is like a careful court stenographer for keyboard actions: it does not just write the final letters, it records the exact key presses and releases so the browser can respond naturally.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: This prepares the keyboard lookup table used by the rest of the file. It expands the raw US keyboard layout so keys can be found by physical code, visible character, or common alias.

**Data flow**: It receives the raw keyboard layout, where each physical key has its normal and shifted meaning. It creates richer key descriptions, adds shifted versions where needed, and adds shortcut names such as `Shift` or newline aliases for Enter. The result is a ready-to-use dictionary for later key lookups.

**Call relations**: This runs when the module is loaded to create `LAYOUT_CLOSURE`. Later, key lookup flows depend on this prepared table through `_description_for`, which is then used by key press and release builders.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: This converts the currently held modifier keys into the number format Chrome expects. A modifier key is a key like Shift, Control, Alt, or Meta that changes the meaning of another key.

**Data flow**: It receives a set of modifier names, checks which known modifier bits apply, and adds those bit values together. It returns one integer that represents the active modifier keys in Chrome’s event format.

**Call relations**: Both `key_down` and `key_up` call this right before building a Chrome keyboard event, so each event accurately says which modifiers are active at that moment.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: This finds the correct browser-facing description for a requested key, taking the current keyboard state into account. It answers questions like “does this key produce lowercase `a`, uppercase `A`, or no text because Control is held?”

**Data flow**: It receives the current `KeyboardState` and a requested key name or character. It looks up that key in the prepared layout table, applies Shift if appropriate, and removes typed text when non-Shift modifiers are held. It returns a `KeyDescription`, or raises a validation error if the key is unknown.

**Call relations**: `key_down` and `key_up` both rely on this before they can create Chrome events. It is the shared checkpoint that turns user-friendly key names into precise key details.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: This translates certain macOS keyboard shortcuts into macOS editing command names that Chrome understands. These commands cover actions like copy, paste, select all, move by word, or delete by word.

**Data flow**: It receives a physical key code and the currently held modifiers. It builds a shortcut name such as `Meta+KeyA`, looks it up in the macOS editing command table, removes the trailing colon from command names, and skips commands that directly insert text. It returns a list of command names for Chrome to include in the key event.

**Call relations**: `key_down` calls this only when the browser is being driven as macOS. Its output is added to the key-down event so Chrome can perform native-style editing behavior.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: This creates the Chrome message for pressing a key down. It also updates the remembered keyboard state so later events know which keys and modifiers are still held.

**Data flow**: It receives the current keyboard state, a key name or character, and whether the target platform is macOS. It looks up the key description, detects repeat presses, records the key as pressed, records modifier keys when needed, adds macOS editing commands if relevant, and calculates the modifier mask. It returns one Chrome DevTools Protocol call for a key-down-style event.

**Call relations**: `press_combo` uses this for each part of a shortcut, and `type_text` uses it for each short, keyboard-typeable character. It delegates lookup to `_description_for`, modifier encoding to `modifiers_mask`, and macOS shortcut behavior to `_mac_commands`.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: This creates the Chrome message for releasing a key. It also updates the remembered keyboard state so the system knows that key is no longer held.

**Data flow**: It receives the current keyboard state and a key name or character. It looks up the key description, removes the key from the pressed-key set, removes it from the modifier set if it is a modifier, recalculates the modifier mask, and returns one Chrome DevTools Protocol key-up call.

**Call relations**: `press_combo` uses this after pressing shortcut keys, in reverse order, like a person releasing keys after a chord. `type_text` uses it after each synthesized character press. It shares key lookup and modifier encoding with `key_down`.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This turns a shortcut string such as `Ctrl+A` or `Shift+Enter` into a full sequence of key-down and key-up browser events. It gives callers a simple way to express keyboard combinations in familiar terms.

**Data flow**: It receives the keyboard state, a plus-separated shortcut string, and the macOS flag. It splits the string into parts, normalizes common names like `ctrl` to `Control`, rejects an empty combo, presses each key in order, then releases them in reverse order. It returns the list of Chrome calls needed to perform the shortcut.

**Call relations**: Higher-level input code can call this when it needs a keyboard chord. Internally it hands each individual press to `key_down` and each release to `key_up`, so state and Chrome event details stay consistent.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This turns text into browser input calls. For short text it simulates real key presses, while for long text it uses a direct text insertion call to avoid sending many separate key events.

**Data flow**: It receives the keyboard state, the text to enter, and the macOS flag. If the text is longer than the file’s character limit, it returns one `Input.insertText` call. Otherwise, it walks through each character: characters found in the keyboard layout become key-down and key-up events, while characters not on the layout are inserted directly. It returns the complete list of Chrome calls.

**Call relations**: This is used when automation wants to type ordinary text. It calls `key_down` and `key_up` for short, keyboard-like typing so page key handlers and autocomplete can react, but bypasses them for long text where direct insertion is more practical.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Action settling
Post-action waiting logic determines when browser work caused by an action has quieted enough for automation to continue.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after browser actions and navigation events`

Browser automation often needs to know when it is safe to take the next step. If it moves too soon, the page may not have reacted yet. If it waits for every network request to stop, it may hang on modern pages that constantly load ads, tracking beacons, or live updates. This file solves that timing problem.

The main idea is consequence-scoped waiting: only wait for work caused by the current action. The helper `tracks_request` filters browser network events so the system pays attention to foreground requests, not passive resources such as images, fonts, or analytics calls. The `Settle` object then keeps small sets of facts: which tracked requests are still pending, which browser sessions are still loading, and which sessions have painted visible content.

When asked to wait, `Settle.wait` first gives the page one tiny turn to run queued JavaScript. That is like waiting for someone to finish the sentence they already started before judging whether they are done. Then it chooses the right stopping rule. If the page has painted, it allows a short grace period for important follow-up requests. If nothing started, it returns quickly. Otherwise, it waits until loading and tracked requests go quiet, but never beyond a time cap. This keeps automation both patient and practical.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It ignores requests that are usually background decoration or telemetry, such as images, fonts, low-priority prefetches, and analytics beacons.

**Data flow**: It receives a dictionary of browser event details. It reads the request type, priority, and URL host. If the request looks passive or belongs to a known analytics host, it returns `false`; otherwise it returns `true`, including for unfamiliar shapes so important work is not accidentally missed.

**Call relations**: When a request-start event arrives, `Settle.on_request_started` asks this function whether the request should count as part of the current action. This function uses the event dictionary and URL parsing to make that yes-or-no decision before the request is added to the pending set.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh tracker for page-settling state. It starts with no pending requests, no known loads, no painted pages, and a count of zero started tracked requests.

**Data flow**: It takes no outside data beyond the new object being created. It prepares empty sets for pending requests, loading sessions, and painted sessions, and sets the started-request counter to zero. The result is a `Settle` object ready to observe browser events.

**Call relations**: A `BrowserSession` creates this object when it starts, and also creates a fresh one during close according to the provided call graph. From there, browser event callbacks feed it request, loading, and paint information so later waits can make a decision.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the action-specific settling state so the next browser action starts with a clean slate. This prevents old requests or paints from influencing the next wait.

**Data flow**: It reads the current stored pending requests, started count, and painted sessions only to discard them. After it runs, pending requests and painted markers are empty and the started counter is back to zero. It does not clear the loading set, because page loading may still be a current browser fact.

**Call relations**: No direct caller is shown in the provided graph. It is the natural reset point before or between actions, so the `Settle` object can measure only the consequences of the current action rather than mixing in earlier activity.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records a newly started network request if it belongs to a browser session and is worth waiting for. This is how the tracker learns that the current action has started useful work.

**Data flow**: It receives browser event details and an optional session ID. It pulls out the request ID, asks `tracks_request` whether the request matters, and, if everything is valid, stores the pair of session ID and request ID in the pending set. It also increments the count of tracked requests that have started.

**Call relations**: This function is fed by browser request-start events. It hands the filtering decision to `tracks_request`; if the request passes, later calls to `Settle.wait` will see it as unfinished work and avoid moving on too early.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a network request as no longer pending. It is used when the browser reports that a request finished, failed, or otherwise stopped being active.

**Data flow**: It receives browser event details and an optional session ID. It extracts the request ID and removes the session/request pair from the pending set if it is present. The output is not a returned value but a changed internal state: one less possible reason to keep waiting.

**Call relations**: This function complements `Settle.on_request_started`. Start events add tracked requests to the pending set, finish events remove them, and `Settle.wait` watches that set to decide when the page has become quiet enough.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has begun loading a document. This tells the waiting logic that the page may still be in the middle of a navigation or reload.

**Data flow**: It receives a session ID and adds it to the loading set. Nothing is returned. Afterward, `Settle.wait` treats that session as still busy until it is marked loaded.

**Call relations**: This is meant to be called from browser lifecycle events that indicate loading has started. Its state is later read by `Settle.wait` and `_drain_after_paint` so they do not declare the page ready while the document is still loading.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Notes that a browser session has finished loading. This removes one major reason for the automation to keep waiting.

**Data flow**: It receives a session ID and removes it from the loading set if present. Nothing is returned. Afterward, waits for that session can finish if there are no important pending requests and no other delay rule applies.

**Call relations**: This pairs with `Settle.mark_loading`. Browser lifecycle events call it when loading ends, and the waiting routines use the updated loading set to decide whether the current action has settled.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted visible content. A paint event means the user could see something on the page, so the waiting strategy can switch from long network quiet to a shorter post-paint grace period.

**Data flow**: It receives a session ID and adds it to the painted set. Nothing is returned. Afterward, `Settle.wait` knows this session has reached the visible-content milestone.

**Call relations**: This is meant to be called from browser lifecycle paint events such as first contentful paint. `Settle.wait` checks this marker and, when present, hands off to `_drain_after_paint` for a shorter, more user-centered wait.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears complete, without waiting forever for noisy background activity. It is the main decision point that says, “the page is ready enough to continue.”

**Data flow**: It receives a browser protocol connection, a session ID, and a maximum wait time. First it calls `_flush_page_tasks` so already-scheduled page JavaScript gets a chance to run and emit any request events. Then it watches painted, loading, and pending-request state until one of its readiness rules is satisfied or the time cap is reached. It returns no value; the important result is the passage of time until the page is considered settled.

**Call relations**: Higher-level browser-session code uses this after an action or navigation. Inside, it delegates the initial JavaScript queue check to `_flush_page_tasks` and, once a paint is seen, delegates the post-paint waiting rule to `_drain_after_paint`. It uses short sleeps between checks so other asynchronous browser events can arrive.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After visible content appears, waits briefly for important follow-up loading to finish. This avoids calling a page ready the instant an empty shell appears, while still avoiding long waits on pages that never become fully quiet.

**Data flow**: It receives a session ID and an absolute deadline. It creates a shorter grace deadline, limited by the overall deadline. During that grace period, it watches the loading set and pending requests; if they become quiet and stay quiet for a short gap, it returns. If they never quiet down, it returns when the grace period ends.

**Call relations**: `Settle.wait` calls this whenever it sees that the session has painted. It does not talk to the browser directly; it relies on state updated by request, loading, and paint event methods while it sleeps and checks.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the page one quick chance to run JavaScript work that is already queued. This helps make sure request events caused by the user action have been reported before the settling decision is made.

**Data flow**: It receives the browser protocol connection and a session ID. It asks the browser to evaluate a tiny JavaScript promise based on `setTimeout(0)`, which waits one turn of the page’s task queue. If that protocol call fails or times out, it simply waits for a short beat instead. It returns nothing; its effect is timing alignment.

**Call relations**: `Settle.wait` calls this before checking whether the page is quiet. The function sends a `Runtime.evaluate` command through the Chrome DevTools Protocol connection, and catches protocol or timeout failures so settling can continue safely even if the flush cannot be completed.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).

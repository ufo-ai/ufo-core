# Browser input, forms, and high-level tool actions  `stage-11.2.4`

This stage is part of the system’s main work loop, when an agent is using a browser to get tasks done. It sits between the agent’s simple requests and Chrome’s detailed control interface. The tools file is the front counter: it defines actions the agent can ask for, such as opening a page, reading content, clicking, typing, uploading a file, or saving a download. Those requests then move into helpers that make them safe and precise. The computer file turns broad actions like “click” or “scroll” into browser commands. The coordinate file maps positions from what the AI sees in a screenshot to the real pixels in the browser window, so clicks land in the right place. The fixup file tidies common mistakes in model-made actions before they reach Chrome. The keys file converts human keyboard ideas, like “Ctrl+A”, into exact key events. The forms file handles filling fields and file uploads. Together, these pieces act like translators and proofreaders for browser control.

## Files in this stage

### Action translation and fixups
Translates AI-style browser actions into concrete browser operations, maps visual coordinates to real pixels, and corrects common model-produced action errors.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file exists so the rest of the system can ask the browser to behave like a person using a mouse and keyboard, without having to know the browser-control details. It receives a batch of requested actions, checks and adjusts them, performs them in order, waits for the page to settle, then returns what happened along with a fresh screenshot.

The main class, BrowserComputer, is like a remote-control operator. It gets the current browser tab from a BrowserComputerSession, converts model coordinates into real viewport coordinates, and sends Chrome DevTools Protocol commands. The Chrome DevTools Protocol is the browser’s built-in remote-control API.

The file also adds safety and usability help. It warns if the user keeps scrolling when text-reading tools may work better. It warns before sign-in-like pages. It notices downloads and tells the caller to wait for them properly. If a click lands on a native HTML select dropdown, it explains that the caller should use form_input instead of trying to click menu options.

A small but useful touch is screenshot marking: after a click, the returned screenshot gets a blue dot over the clicked spot. Without this file, browser actions would not reliably become clicks, drags, keys, scrolls, waits, screenshots, and helpful reminders.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This protocol method promises that a browser session can provide the tab that should receive actions. A tab here means the active browser page plus its keyboard state.

**Data flow**: It receives an optional tab id. The real session implementation uses that id, or its default choice, to return a tab object with a session id and keyboard state.

**Call relations**: BrowserComputer.run calls this at the start of an action batch so every later click, key press, scroll, or screenshot knows which browser tab to talk to.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This protocol method promises access to the browser control connection. That connection is used to send Chrome DevTools Protocol commands, which are low-level browser remote-control messages.

**Data flow**: It takes no extra input. The real session implementation returns a connection object that can send commands to the browser.

**Call relations**: BrowserComputer.run, BrowserComputer.act, BrowserComputer._select_reminder, BrowserComputer._dispatch, and BrowserComputer._mouse_event rely on this connection whenever they need the browser to actually do something or report something.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This protocol method promises a summary of a tab to include in the final response. It gives the caller context such as which tab was acted on.

**Data flow**: It receives a tab object. The real session implementation reads whatever tab details it tracks and returns them as a JSON-style dictionary.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the final result includes both the action outcome and current tab information.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This protocol method promises a list of browser tab titles. The file uses those titles to spot pages that may be asking for sign-in or account creation.

**Data flow**: It takes no extra input. The real session implementation reads current tab titles and returns them as plain strings.

**Call relations**: BrowserComputer.run calls this after performing actions, then passes the titles to sign_in_warning to decide whether to add a safety reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This protocol method promises a way to run a JavaScript function on a specific browser-side object. In this file it is used to inspect a select dropdown after the user clicks it.

**Data flow**: It receives a browser session id, a browser object id, JavaScript function text, and optional arguments. The real implementation runs that function in the page and returns the result as a JSON-style dictionary.

**Call relations**: BrowserComputer._select_reminder calls this after finding a select element, so it can learn the option labels and build a helpful reminder.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This protocol method promises to turn a page reference, such as an element id returned by a page-reading tool, into the browser node needed for low-level commands.

**Data flow**: It receives a tab and a reference string. The real implementation looks up that reference and returns the matching browser node plus its backend node id.

**Call relations**: BrowserComputer.act uses this for the scroll_to action, because Chrome needs a real DOM node id rather than a human-facing reference string.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This protocol method promises to find a clickable point for a referenced page element. It lets actions target an element by reference instead of by raw coordinates.

**Data flow**: It receives a tab and a reference string. The real implementation finds the element’s screen position and returns an x, y point in viewport coordinates.

**Call relations**: BrowserComputer.point calls this when an action includes a ref, and BrowserComputer.act then uses the returned point for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main batch runner. It accepts a request containing browser actions, performs them, waits for the page to calm down, and returns messages, tab information, and a screenshot.

**Data flow**: It receives a JSON-style dictionary with a tab id and an actions list. It validates the actions, adjusts coordinates, groups actions around waits, performs each action, gathers warnings, marks downloads as reported, captures a screenshot, optionally draws a dot on the last click, and returns a JSON-style result.

**Call relations**: This is the top-level method other parts of the browser automation system call for computer-use actions. It calls int_or_none to read the tab id, uses ComputerAction validation and fixup_actions to prepare actions, calls BrowserComputer.act for each action, calls BrowserComputer._select_reminder after clicks when useful, calls sign_in_warning for safety text, and uses BrowserComputer._to_model when reporting the last clicked point.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one browser action. It is the switchboard that decides whether an action should become mouse events, keyboard events, a wait, a scroll, or a special browser command.

**Data flow**: It receives the target tab and one validated action. It finds the target point if needed, sends the right low-level commands, and returns a short human-readable message plus the viewport point if the action landed somewhere visible.

**Call relations**: BrowserComputer.run calls this for every action in a batch. Depending on the action, it hands off to BrowserComputer.point, BrowserComputer._click, BrowserComputer._drag, BrowserComputer._scroll, BrowserComputer._dispatch, BrowserComputer._to_viewport, BrowserComputer._to_model, require_point, and require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds the screen point an action should use. It lets actions point either to a known page element reference or to model-style coordinates.

**Data flow**: It receives a tab and an action. If the action has a ref, it asks the browser session for that element’s point; if it has coordinates, it converts them to viewport coordinates; otherwise it returns nothing.

**Call relations**: BrowserComputer.act calls this before actions that may need a location. It calls BrowserComputer._to_viewport when coordinates must be translated into the browser’s current visible area.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the model’s coordinate system into the browser viewport’s coordinate system. In plain terms, it translates “where the AI thinks the point is” into “where Chrome expects the point to be.”

**Data flow**: It receives an x, y coordinate in model space. It uses the model size and viewport size to scale the point and returns an x, y coordinate in viewport space.

**Call relations**: BrowserComputer.point uses this for coordinate-based targets, and BrowserComputer.act uses it when it needs a default scroll point or a drag start point.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts browser viewport coordinates back into the model’s coordinate system. It is used so reports back to the caller use the same coordinate language the caller understands.

**Data flow**: It receives an x, y coordinate from the browser viewport. It scales it using the viewport size and model size and returns the matching model-space x, y coordinate.

**Call relations**: BrowserComputer.act uses this when writing messages like “Clicked (x,y)”, and BrowserComputer.run uses it when returning the final last_click value.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click hit a native HTML select dropdown and, if so, prepares a reminder explaining the safer way to choose an option. Native select menus often cannot be operated by normal screenshot-based clicking in this browser setup.

**Data flow**: It receives a tab and a viewport point. It asks the browser what element is at that point, walks up to a select element if one exists, reads a short list of option labels and the element reference, and returns reminder text or nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses the browser connection and BrowserComputerSession.call_on to inspect the page, then calls select_reminder to turn the raw dropdown information into plain guidance.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared sequence of keyboard-related browser commands. It is used for typing text and pressing key combinations.

**Data flow**: It receives a tab and a list of Chrome DevTools Protocol calls. It sends each call to the browser connection for that tab and returns nothing after the browser has received them.

**Call relations**: BrowserComputer.act calls this for type and key actions after helper functions have converted text or key combinations into low-level command lists.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one mouse event to the browser, such as move, press, release, or wheel. It is the small shared doorway through which all mouse-like actions reach Chrome.

**Data flow**: It receives a tab and a dictionary of mouse event details. It sends an Input.dispatchMouseEvent command to the browser for that tab and returns nothing.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build complete user gestures from individual mouse events.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a left, right, double, or triple click at a given browser viewport point. It sends the same kind of press and release events a real mouse would create.

**Data flow**: It receives a tab, x and y coordinates, a mouse button name, and a click count. It reads currently pressed keyboard modifiers, moves the mouse to the point, then sends matching press and release events the requested number of times.

**Call relations**: BrowserComputer.act calls this for click actions. It calls BrowserComputer._mouse_event for each low-level mouse step and uses modifiers_mask so held keys like Shift or Ctrl are included.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop when they see movement along the way, not a single jump.

**Data flow**: It receives a tab, starting coordinates, and ending coordinates. It reads current keyboard modifiers, moves to the start, presses the left mouse button, moves gradually toward the end, then releases the button.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It uses BrowserComputer._mouse_event for each move, press, and release, and modifiers_mask to preserve held keyboard modifiers during the drag.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This scrolls at a particular point in the browser viewport. It first positions the mouse there, then sends a wheel event with horizontal and vertical scroll amounts.

**Data flow**: It receives a tab, x and y coordinates, and scroll distances dx and dy. It reads current keyboard modifiers, moves the mouse to the point, sends a mouse wheel event, and returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after deciding the scroll direction and amount. It calls BrowserComputer._mouse_event for the movement and wheel command.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser is on a login, sign-in, sign-up, or registration page. It supports the system’s rule that signing in should be confirmed with the user first.

**Data flow**: It receives a list of tab titles. It lowercases them, checks for sign-in-related words, and returns a warning string if any title looks relevant; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after getting current tab titles. If it returns text, BrowserComputer.run appends that text as a system reminder in the final output.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This builds the message shown after a user clicks a native select dropdown. It explains that clicking dropdown options is unreliable here and tells the caller to use form_input instead.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total option count. It formats a short option preview and returns a plain reminder with the best next step.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked select element. The returned reminder is later included by BrowserComputer.run in the final response.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This draws a small blue marker on a screenshot at the last clicked point. It helps the caller visually confirm where the browser action landed.

**Data flow**: It receives a base64-encoded JPEG screenshot and a viewport point. It decodes the image, draws a translucent blue circle over the point, saves the image back as JPEG, encodes it as base64 again, and returns the new string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click-like action. Because image editing can be CPU work, BrowserComputer.run runs it through an executor rather than blocking the async flow.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This safely reads an optional integer value, mainly for tab ids that may arrive as numbers or strings. It keeps missing or unusable values as None.

**Data flow**: It receives a JSON-style value or nothing. If the value is an integer, float, or non-empty string, it converts it to an integer; otherwise it returns None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page, so tab ids from incoming JSON are normalized before use.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action needing a target point actually has one. It turns a missing coordinate or reference into a clear validation error.

**Data flow**: It receives an optional point and the action name. If the point exists, it returns it unchanged; if not, it raises a ValidationError explaining that the action needs a coordinate or ref.

**Call relations**: BrowserComputer.act calls this before click and drag-end actions. It stops the action early with a clear message instead of sending an incomplete browser command.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used when an action has a separate starting coordinate, such as a drag.

**Data flow**: It receives an optional coordinate and the field name to report. If the coordinate exists, it returns it unchanged; if not, it raises a ValidationError naming the missing field.

**Call relations**: BrowserComputer.act calls this for the start_coordinate of a left_click_drag action before converting that start point into viewport coordinates.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting during screenshot capture and browser input`

When an AI model looks at a browser screenshot and says “click at x=500, y=300,” those numbers may not match the browser’s actual pixel grid. The screenshot may have been shrunk before sending it to the model, and some models use their own fixed coordinate grid. This file is the small measuring tool that keeps those spaces aligned.

It defines two simple value types: Size, for width and height, and Coord, for x and y positions. The main work is scaling coordinates back and forth. For Claude-style vision models, screenshots are fitted under known limits so the provider does not silently shrink them on the server. That matters because hidden resizing would make coordinates inaccurate. For Gemini, the model reports points on a fixed 0-to-1000 grid, so this file can tell the rest of the browser automation code to use that coordinate space instead.

An everyday analogy is a map: the model may point to a place on a reduced map, but the browser needs the matching place in the real room. This file converts between the map and the room so automated clicks land where intended.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Chooses the largest screenshot size that Claude-family vision models can receive without the provider shrinking it further. This helps keep the model’s reported positions trustworthy.

**Data flow**: It takes the browser viewport size as input. It first shrinks the width and height if either side is longer than the allowed maximum, then shrinks again if the total pixel count is still too high. It returns a new Size containing the final screenshot width and height.

**Call relations**: This is the fallback sizing rule used when no model-specific coordinate size is supplied. effective_model_size calls it when the rest of the code needs to know what coordinate grid the model is seeing.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Decides what size coordinate space the model should be treated as using. It uses an explicit model size when one is provided, otherwise it computes the screenshot size from the viewport.

**Data flow**: It receives the real browser viewport size and, optionally, a model coordinate size. If the optional size exists, it returns that unchanged. If not, it calculates a screenshot size that fits the vision-model limits and returns that.

**Call relations**: This is the shared decision point for both directions of coordinate conversion. model_to_viewport and viewport_to_model call it before doing their scaling, so they both agree on the same model-side measuring system.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point from the model’s coordinate space into real browser viewport pixels. This is used when the AI says where something is and the browser automation needs to click or move there.

**Data flow**: It takes a model-space coordinate, the browser viewport size, and optionally the model’s coordinate size. It finds the effective model size, compares that size to the real viewport, scales x and y separately, and returns a Coord in browser pixels.

**Call relations**: This function sits at the handoff from AI perception to browser action. Before creating the browser-pixel coordinate, it asks effective_model_size what scale the model was using.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Returns a special coordinate grid for models that do not report positions in screenshot pixels. In particular, it identifies Gemini models, which use a fixed 1000-by-1000 coordinate space.

**Data flow**: It receives a model name, or no model name. If the name contains “gemini” in any letter case, it returns a Size of 1000 by 1000. Otherwise, it returns nothing, meaning the normal screenshot-size rules should be used.

**Call relations**: This function is a small model-specific lookup. Other code can call it before coordinate conversion to provide the right model_size override for effective_model_size, model_to_viewport, or viewport_to_model.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point from real browser viewport pixels back into the model’s coordinate space. This is useful when browser-side positions need to be described in the same coordinates the model understands.

**Data flow**: It takes a browser-pixel coordinate, the viewport size, and optionally the model’s coordinate size. It finds the effective model size, scales x and y from viewport proportions into model proportions, and returns a Coord in model coordinates.

**Call relations**: This is the reverse path of model_to_viewport. It also relies on effective_model_size so that browser positions are translated using the same model-space assumptions as the rest of the system.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `before browser action dispatch`

A model may ask the browser to type, scroll, wait, or click, but its instructions are not always complete or browser-friendly. This file acts like a careful editor checking a to-do list before someone follows it. For example, typing into a page usually only works if the right place is focused first, so a missing click can be added before a typing action. Text like "\\n" or "\\t" can be turned into a real newline or tab, so it is typed as the user expects rather than as two visible characters. Scroll actions are also repaired when they lack a starting point: the file uses the middle of the visible page as a safe default. If a request says “scroll to” but does not name a target, it becomes an ordinary scroll instead. Wait actions get a default duration when none is provided, and multi-clicks aimed at a named page element are simplified into a single click because that is safer when only a reference is available. The file can also split a long action list into smaller batches that end at waits, giving the browser time to settle between steps.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: This function takes a batch of planned browser actions and repairs common mistakes before the actions are carried out. It makes the action list safer and more complete without changing the overall intent.

**Data flow**: It receives a list of actions, the current browser viewport size, and optionally the size used by the model that created the actions. It first works out the effective screen size and center point, then walks through each action. Depending on the action, it may add a focus click before typing, convert escaped text into real tabs or newlines, add a missing scroll anchor, turn an unusable scroll-to request into a normal scroll, add a default wait time, or simplify a referenced double or triple click. It returns a new list of actions, possibly with repaired or added actions.

**Call relations**: This is the main cleanup step in the file. When it sees typing that needs focus, it asks _focus_click to build the extra click. When it sees text with escaped characters, it asks _unescape_text to rewrite the text. It also relies on the coordinate helper effective_model_size to decide where the center of the model-visible page is, and creates replacement ComputerAction and ScrollParameters objects when an action needs to be rewritten.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: This function breaks a single action list into smaller groups, ending each group after a wait action. This lets the browser pause and settle before the next group runs.

**Data flow**: It receives a list of actions and starts collecting them into a current batch. Each action is added to the current batch. If the action is a wait, that batch is saved and a fresh batch begins. After all actions are read, any leftover actions are saved too. The result is a list of action batches.

**Call relations**: This function is a companion to the repair step. After actions have been prepared, another part of the system can use these batches to send actions to the browser in stages, pausing after waits instead of rushing through the whole list at once.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper builds a simple left-click action that can focus the place where typing is supposed to happen. It is used when a typing action points at a location or page element but no earlier click has focused it.

**Data flow**: It receives the typing action that needs focus. If that action has exact coordinates, it creates a left-click at those coordinates. Otherwise, it creates a left-click using the same named page reference. The result is a new click action that can be inserted before the typing action.

**Call relations**: fixup_actions calls this helper during its typing repair path. The helper does one small job: turn the target of a typing action into a click, then hand that click back so it can be placed before the typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns literal escape strings in typed text into the real characters they represent. For example, it changes the two-character text "\\n" into an actual newline.

**Data flow**: It receives a typing action and reads its text, treating missing text as empty. If the text does not contain known escaped literals, it returns the original action unchanged. If it finds escaped tab or newline markers, it replaces them with real tab or newline characters and returns a copied action with the corrected text.

**Call relations**: fixup_actions calls this helper whenever it processes a typing action. The helper uses the action's copy method so the corrected text is placed into a new version of the action while preserving the rest of the action's details.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Form and keyboard input
Handles structured form filling, file uploads, and conversion of human keyboard intent into Chrome-compatible input events.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

A web page seen by the automation system does not expose raw HTML elements directly. Instead, the system gives each useful page element a short reference, and later actions use that reference to find the real browser node again. This file is the bridge between those friendly references and actual form changes in the browser.

The central piece is BrowserForms, which offers two actions. upload_file finds a referenced file input and tells the browser to attach local file paths to it. input finds a referenced form element and runs a small JavaScript function inside the page to set its value. That JavaScript knows about common form types: checkboxes and radio buttons are checked or unchecked, select boxes get a selected value, editable text areas get text content, and normal inputs get a value. It then fires input and change events, which is important because many modern web pages only notice changes when those events happen.

The file also defines BrowserFormSession as a protocol, meaning a promise about what any browser session object must be able to do: provide a page, provide a Chrome DevTools connection, run JavaScript on a node, and turn a saved reference back into a browser node. If a reference is stale or points to the wrong kind of element, the code raises a HallucinationError with guidance to reread the page. This protects the rest of the system from silently acting on imagined or outdated page elements.

#### Function details

##### `BrowserFormSession.page`  (lines 35–35)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It should return the browser page, or a specific tab when a tab id is supplied, so form actions know where to look.

**Data flow**: It receives an optional tab id. The concrete browser session uses that id to choose the right open page, then returns a page-like object that later steps can search for element references.

**Call relations**: BrowserForms.upload_file and BrowserForms.input rely on this method at the start of their work. They first choose the correct tab, then use the returned page when resolving a saved element reference.


##### `BrowserFormSession.connection`  (lines 37–37)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It should return the Chrome DevTools Protocol connection, which is the low-level channel used to ask the browser to do things.

**Data flow**: It takes no extra input. The concrete session returns a Cdp connection object, and callers use that object to send browser commands such as resolving a DOM node or setting files on a file input.

**Call relations**: BrowserForms.upload_file uses this connection to send the file-upload command. BrowserForms.input uses it to turn a saved backend node id into a JavaScript object that can be acted on.


##### `BrowserFormSession.call_on`  (lines 39–45)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It should run a JavaScript function on a specific browser object, such as an input field, and return the result.

**Data flow**: It receives the browser session id, the target JavaScript object id, the JavaScript function text, and optional arguments. The concrete session runs that function inside the page on the target object, then returns the JSON-like result from the browser.

**Call relations**: BrowserForms.input hands off to this method after it has found and resolved the target element. The JavaScript it passes in is the small form-filling routine defined in this file.


##### `BrowserFormSession.resolve_ref`  (lines 47–47)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It should translate a friendly page reference back into the real browser node it represents.

**Data flow**: It receives the current tab or page object and a reference string. The concrete session looks up that reference and returns two things: a node record with the browser session id, and the backend node id used by Chrome DevTools.

**Call relations**: Both BrowserForms.upload_file and BrowserForms.input use this method after reading the requested ref from the tool arguments. It is the step that connects the user's remembered page reference to a real element in the browser.


##### `BrowserForms.upload_file`  (lines 54–71)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This action attaches one or more local files to a file-upload field on a web page. It is used when the automation system needs to operate an HTML file input without manually clicking through an operating-system file picker.

**Data flow**: It reads a possible tab id, the required element ref, and a list of file paths from the incoming argument dictionary. It chooses the page, checks and normalizes the ref and file paths, resolves the ref into a real browser node, then sends Chrome DevTools the DOM.setFileInputFiles command. On success it returns the same ref and the file paths it attached; if the ref is not a file input, it raises a clear HallucinationError telling the caller to reread the page and use a file input ref.

**Call relations**: This method is called when a higher-level browser action asks to upload files. It uses _tab_id to understand the tab choice, uses the wire helpers to validate incoming JSON-like values, asks the browser session to resolve the page ref, and then sends the final command through the browser's DevTools connection.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 73–88)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This action fills or changes a form-like element on a web page. It covers common cases such as text fields, checkboxes, radio buttons, select dropdowns, and content-editable areas.

**Data flow**: It reads a possible tab id, the required element ref, and the desired value from the incoming argument dictionary. It chooses the page, resolves the ref into a browser node, asks Chrome DevTools to turn that node into a JavaScript object, extracts the object id, and then runs the JS_FORM_INPUT script on that object with the requested value. The result from the in-page script is returned; if the node cannot be resolved, it raises a HallucinationError explaining that the caller should reread the page and use a current ref.

**Call relations**: This method is called when a higher-level browser action wants to type into or set a form control. It uses _tab_id and the wire validation helpers first, then depends on the browser session for reference lookup, low-level node resolution, and finally call_on to execute the form-setting JavaScript inside the page.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 91–100)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id supplied in loose JSON-style input into either an integer tab id or no tab choice at all. It lets callers pass tab ids as numbers or non-empty strings.

**Data flow**: It receives a value that may be missing, an integer, a float, a string, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserForms.upload_file and BrowserForms.input call this helper before asking the browser session for a page. It keeps tab-id cleanup in one place so both form actions choose tabs in the same way.

*Call graph*: called by 2 (input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling for browser keyboard input actions`

This file is a small keyboard translator for browser automation. A person can say “type hello” or “press Shift+Enter”, but Chrome needs a detailed Chrome DevTools Protocol message, or CDP message, with fields such as the physical key code, visible character, modifier keys, and keypad location. This file builds those messages.

It starts with a US keyboard map copied from Playwright, a browser automation project. That map says, for example, that Digit1 normally means “1” but means “!” when Shift is held. It also includes Mac editing commands, such as which command name Chrome expects for shortcuts like Meta+ArrowLeft.

The file keeps a small KeyboardState, like a clipboard note saying which keys are currently held down. When a key goes down, it updates that state, works out whether Shift changes the character, clears typed text when Control/Alt/Meta are held, and returns a CDP call. When the key comes up, it removes it from the state and returns a matching key-up call.

For short text, it sends realistic per-character key-down and key-up events, so web page key handlers and autocomplete can react. For long text, it uses a single insertText call, which is faster and avoids sending many individual events.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: This prepares the raw keyboard layout into a lookup table that is easy to use later. It lets later code find a key by physical code, by visible character, or by common aliases such as Enter for newline.

**Data flow**: It receives the full US keyboard layout. For each key, it builds a KeyDescription with the normal meaning, optionally builds a shifted version, and adds aliases where useful. The result is a richer dictionary that later functions can search quickly when turning user input into browser key events.

**Call relations**: This runs when the module is loaded to create LAYOUT_CLOSURE. It uses KeyDescription objects to describe keys and dataclasses.replace to make shifted variants without rewriting every field by hand.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: This converts the currently held modifier keys, such as Shift or Control, into the number format Chrome expects. Chrome represents modifiers as bit flags, meaning each modifier contributes a specific number.

**Data flow**: It receives a set of modifier key names. It checks each known modifier, adds that modifier’s number if it is present, and returns the total. Nothing else is changed.

**Call relations**: key_down and key_up call this right before building their CDP payloads, so every outgoing keyboard event tells Chrome which modifier keys are currently held.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: This finds the exact keyboard description for one requested key in the current keyboard state. It also applies Shift and suppresses typed text when non-text modifiers like Control or Meta are held.

**Data flow**: It receives the current KeyboardState and a requested key name or character. It looks up that key in LAYOUT_CLOSURE, raises a ValidationError if the key is unknown, swaps in the shifted version when Shift is pressed, and clears the text field when Control, Alt, or Meta are active. It returns the final KeyDescription to use for the event.

**Call relations**: key_down and key_up both ask this function what key they are really dealing with before they update state or build a Chrome event. It is the shared “what does this key mean right now?” step.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: This finds special Mac editing commands for a key plus modifier combination. These commands help Chrome treat shortcuts like Mac text-editing keys instead of ordinary typed characters.

**Data flow**: It receives a physical key code and the set of currently held modifiers. It builds a shortcut name such as Shift+Meta+ArrowLeft, looks that up in the Mac command table, removes the trailing colon from command names, filters out insert commands, and returns a list of command strings.

**Call relations**: key_down calls this only when the target browser is running as Mac. The returned commands are placed into the CDP key-down event so Chrome can perform native-style Mac editing behavior.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: This creates the Chrome message for pressing a key down. It also updates the remembered keyboard state so later events know which keys and modifiers are currently held.

**Data flow**: It receives a KeyboardState, a key name or character, and whether Mac behavior should be used. It asks _description_for for the key’s current meaning, checks whether this is an auto-repeat because the key is already down, records the pressed key, records modifier keys when needed, optionally adds Mac editing commands, and returns one CDP call named Input.dispatchKeyEvent with a keyDown or rawKeyDown payload.

**Call relations**: press_combo uses this for every key in a shortcut before releasing them. type_text uses it for each short, keyboard-known character. Inside, it relies on _description_for, _mac_commands, and modifiers_mask to build the event Chrome expects.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: This creates the Chrome message for releasing a key. It also updates the remembered keyboard state so released keys no longer count as held.

**Data flow**: It receives a KeyboardState and a key name or character. It finds the key description, removes that key from the pressed-key set, removes it from the modifier set if it is a modifier, converts the remaining modifiers into Chrome’s number format, and returns one Input.dispatchKeyEvent CDP call with type keyUp.

**Call relations**: press_combo calls this in reverse order after pressing the keys in a shortcut, like lifting fingers off a keyboard. type_text calls it after each synthetic character press. It uses _description_for and modifiers_mask to keep the outgoing event accurate.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This turns a shortcut string such as “Ctrl+A” or “Shift+Enter” into a realistic sequence of key-down and key-up browser events. It accepts common shortcut names and aliases so callers do not need to know Chrome’s exact key names.

**Data flow**: It receives the current KeyboardState, a combo string, and whether Mac behavior should be used. It splits the string on plus signs, normalizes aliases like ctrl to Control, rejects an empty combo, presses each key in order, then releases the keys in reverse order. It returns the full list of CDP calls.

**Call relations**: This is a higher-level helper built from key_down and key_up. It is the shortcut path: first it asks key_down to make the browser think the fingers went down, then it asks key_up to lift them in the opposite order.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This turns text into browser input. It chooses between realistic key-by-key typing for short text and a faster direct text insertion for long text.

**Data flow**: It receives the current KeyboardState, the text to type, and whether Mac behavior should be used. If the text is longer than the configured limit, it returns one Input.insertText call. Otherwise, for each character, it sends key_down and key_up when the character exists in the keyboard layout, or falls back to Input.insertText for characters the layout does not know.

**Call relations**: This is the text-entry path. For ordinary short text, it delegates to key_down and key_up so web pages can observe real-looking keystrokes. For long text or unusual characters, it hands text directly to Chrome through insertText.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Agent browser tools
Exposes the high-level browser tool interface agents call for navigation, page inspection, clicking, typing, uploads, downloads, and related actions.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `active during each agent turn when browser tools are called`

This file turns browser actions into safe, reusable tools for the agent. Without it, the agent might know it wants to visit a website or click a button, but it would not have a standard way to send that request to the browser or return the result.

Each tool has an input model, which is a small shape-checking class that says what information the tool expects. For example, navigation needs a URL, while file upload needs a browser reference and workspace file paths. When a tool runs, it asks for the turn’s shared browser surface through `_browser`. That surface is like a rented car for the current turn: it is created only when first needed, reused for later browser actions in the same turn, and returned automatically during cleanup.

Most tool functions do the same simple pattern: remove fields that are only meant for human context, send the remaining browser instructions to `BuaSurface`, then wrap the reply as JSON text. Two tools do a little more. `_computer` can include a screenshot as image output and can optionally save that screenshot into the shared workspace. `_wait_for_download` waits for a browser download, decodes its stored bytes, and writes the downloaded file into the workspace so other parts of the agent can use it by path.

At the bottom, `BROWSER_TOOLS` publishes the available tools and their descriptions.

#### Function details

##### `_browser`  (lines 95–118)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: This function gets the one browser surface used for the current tool turn. It creates the browser connection only on the first browser-tool call, then reuses it so later actions in the same turn keep the same browser state.

**Data flow**: It receives the tool context, which contains things like the selected browser connection provider, sandbox, model, store, and cleanup registry. It checks whether a browser surface is already cached for this turn; if not, it builds a `BuaSurface`, stores it, and registers its close function for cleanup. It returns the ready-to-use browser surface, or raises an error if no browser provider is configured.

**Call relations**: All browser action functions call `_browser` before doing their work. `_browser` is the shared doorway to `BuaSurface`, so navigation, tab actions, page reading, form input, screenshots, uploads, and downloads all go through the same per-turn browser connection.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 121–122)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This helper turns a plain Python dictionary into a tool result containing JSON text. It gives most browser tools a consistent way to send structured answers back to the agent.

**Data flow**: It receives a dictionary reply from the browser layer. It converts that dictionary into a JSON string and wraps it in a text content object inside a tool result. The output is a standard `ToolResult` ready for the agent runtime.

**Call relations**: Most tool handlers call `_json_result` after receiving a reply from the browser surface. `_computer` uses it only when there is no screenshot image to attach, while the other browser tools generally use it for their normal response.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 125–128)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: This helper checks that a browser reply contains a required non-empty string. It prevents later code from silently writing bad files or decoding missing data.

**Data flow**: It receives a value from a browser reply and the name of the field being checked. If the value is a non-empty string, it returns that string. If the value is missing, empty, or not a string, it raises a clear error naming the missing field.

**Call relations**: `_computer` uses this before decoding and saving a screenshot. `_wait_for_download` uses it before saving a downloaded file, checking both the filename and the file content.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 131–135)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: This tool asks the browser to move to a URL or perform a navigation action such as browser history movement. It is the agent’s basic way to open or change pages.

**Data flow**: It receives validated navigation input from the agent, including a URL and optional tab id. It converts the input to a JSON-friendly dictionary while leaving out the human-only description. It sends that instruction to the browser surface and returns the browser’s reply as JSON text.

**Call relations**: `_navigate` is registered as the `navigate` tool in `BROWSER_TOOLS`. When the agent calls that tool, this function gets the shared browser surface through `_browser`, delegates the real browser work to it, and formats the answer through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 138–139)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: This tool asks for information about the currently open browser tabs. It helps the agent understand what pages are available before choosing where to act.

**Data flow**: It receives an empty tabs-context input. It sends an empty request to the browser surface’s tab-context operation. It returns the list or summary of tab information as JSON text.

**Call relations**: `_tabs_context` is registered as the `tabs_context` tool. It calls `_browser` to reuse the current turn’s browser surface, then uses `_json_result` to send the tab context back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 142–144)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: This tool creates a new browser tab. If the caller does not provide a URL, it opens a blank page.

**Data flow**: It receives the requested tab creation input, including an optional URL. It builds a request using the given URL or `about:blank` as the default. It asks the browser surface to create the tab and returns the reply as JSON text.

**Call relations**: `_tabs_create` is registered as the `tabs_create` tool. It gets the shared browser surface through `_browser`, asks it to open the tab, and passes the result through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 147–149)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: This tool closes a browser tab. The caller can name a specific tab, or rely on the browser layer’s default behavior when no tab id is supplied.

**Data flow**: It receives tab-close input, possibly including a tab id. It converts that input into a JSON-friendly dictionary without empty fields. It sends the request to the browser surface and returns the reply as JSON text.

**Call relations**: `_tabs_close` is registered as the `tabs_close` tool. It follows the common pattern in this file: get the current browser surface through `_browser`, delegate the browser action, then format the response with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 152–154)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: This tool fills a browser file input with one or more files from the workspace. It lets the agent upload files to websites without needing a human file picker.

**Data flow**: It receives a browser reference for the file input, a list of workspace file paths, and optionally a tab id. It converts those inputs into a JSON-friendly dictionary and sends them to the browser surface. It returns the browser’s upload reply as JSON text.

**Call relations**: `_upload_file` is registered as the `upload_file` tool. It relies on `_browser` for the current browser connection and `_json_result` for the standard text response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 157–161)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: This tool reads the page in a structured way, using the browser’s accessibility tree. An accessibility tree is a browser-made outline of page elements meant to describe what a user can see or interact with.

**Data flow**: It receives options such as depth, filter, reference id, and tab id, plus a human description of the user’s goal. It removes the human-only description and sends the remaining options to the browser surface. It returns the structured page information as JSON text.

**Call relations**: `_read_page` is registered as the `read_page` tool. The agent calls it when it needs a machine-readable view of a page, and `_read_page` passes that request through `_browser` to the browser layer before wrapping the result with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 164–168)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: This tool extracts the raw visible text from the current browser page. It is useful when the agent needs the words on the page more than the detailed structure of buttons and fields.

**Data flow**: It receives the user’s description and optionally a tab id. It drops the description before sending the request to the browser surface. It returns the extracted page text and related reply data as JSON text.

**Call relations**: `_get_page_text` is registered as the `get_page_text` tool. It uses `_browser` to reach the shared browser surface and `_json_result` to produce the standard tool response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 171–175)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: This tool searches the browser page for elements matching a query, such as text, a role, a name, or a URL. It helps the agent locate the thing it wants to click, type into, or inspect.

**Data flow**: It receives a search query, a user description, and optionally a tab id. It removes the human-only description and sends the search request to the browser surface. It returns the matching elements or search result as JSON text.

**Call relations**: `_find` is registered as the `find` tool. When the agent needs page targets, this function gets the current browser surface through `_browser`, asks it to perform the search, and formats the answer through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 178–182)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: This tool sets the value of a form field identified by a browser reference. It is how the agent types or selects values in web forms in a controlled way.

**Data flow**: It receives the field reference, the desired value, a user description, and optionally a tab id. It removes the description, sends the reference and value to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: `_form_input` is registered as the `form_input` tool. It depends on `_browser` for the shared browser surface and `_json_result` for the response format.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 185–203)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: This tool performs low-level browser interactions such as mouse actions, keyboard input, waiting, scrolling, and taking screenshots. It is the agent’s more direct “use the computer” interface when higher-level page tools are not enough.

**Data flow**: It receives a sequence of action dictionaries, a user description, optional tab id, and optional screenshot-saving choices. It removes the human-only description and sends the actions to the browser surface. If asked to save a screenshot, it checks that screenshot data exists, decodes the base64 text into bytes, and writes it into the workspace. If the browser reply includes screenshot data, it returns text for the non-image fields plus an image attachment; otherwise it returns plain JSON text.

**Call relations**: `_computer` is registered as the `computer` tool. It calls `_browser` to perform the browser actions, uses `_required_str` when a screenshot must be saved, may write to the sandbox workspace, and either uses `_json_result` or builds a richer `ToolResult` containing both text and image content.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 206–214)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: This tool waits for a browser download to finish and saves the downloaded file into the shared workspace. It turns an in-browser download into a path that the rest of the agent can use.

**Data flow**: It receives options such as a download id, output path, timeout, and a user description. It removes the description and asks the browser surface to wait for the download. From the browser reply, it requires a filename and base64-encoded content, decodes the content into bytes, writes the file under the chosen directory, and returns the saved file path, filename, and size as JSON text.

**Call relations**: `_wait_for_download` is registered as the `wait_for_download` tool. It uses `_browser` to receive the download data, `_required_str` to make sure the needed fields are present, the sandbox to write the file, and `_json_result` to report where the file was saved.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).

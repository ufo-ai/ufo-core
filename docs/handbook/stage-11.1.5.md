# Browser action vocabulary and input/form execution  `stage-11.1.5`

This stage is the action toolbox for driving a browser. It sits in the main work loop, after an agent decides what it wants to do on a web page and before Chrome actually receives the command. The actions.py file defines the common “language” for requests such as click, type, scroll, wait, screenshot, fill a form, upload a file, or press keys. fixup.py acts like a proofreader for those requests, correcting small model mistakes, such as typing before selecting a field or leaving out a wait time. computer.py is the main bridge to Chrome: it turns these cleaned-up actions into real mouse, keyboard, scrolling, and screenshot operations. keys.py handles the fine details of keyboard input, translating things like “Ctrl+A” or plain text into the exact key events Chrome expects. forms.py gives safer, more focused tools for filling fields and attaching files on real pages. errors.py provides a clear way to report when the agent asks for something impossible, such as a browser value that does not exist.

## Files in this stage

### Action normalization and dispatch
High-level browser requests are cleaned up and routed into concrete automation behavior.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is like the hands and eyes of the browser automation system. Other parts of the project decide what should happen on a web page; this file actually performs those actions and reports back what changed. It accepts a batch of requested actions, cleans them up, runs them against the current browser tab, waits for the page to settle, then returns a fresh screenshot and helpful messages.

The browser is controlled through CDP, the Chrome DevTools Protocol, which is Chrome’s remote-control API. A click becomes a series of mouse move, mouse down, and mouse up messages. Typing becomes keyboard events. Scrolling becomes a mouse wheel event. Coordinates are translated between the model’s screen size and the real browser viewport, because the agent and the browser may not use the same pixel dimensions.

The file also adds safety and usability help. It warns if the user seems to be on a sign-in page, if repeated scrolling would be inefficient, if downloads started, or if a native dropdown was clicked in a way that will not work reliably. After a click, it can draw a blue marker on the returned screenshot so the caller can see exactly where the click landed. Without this file, the system could describe browser actions but could not reliably carry them out or explain their results.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: Defines the session operation that returns the browser tab to act on. A real session class supplies this so BrowserComputer can find the right tab before sending clicks, keys, or screenshots.

**Data flow**: It receives an optional tab id → the session implementation chooses or opens the matching tab object → it returns a tab with a browser session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this contract at the start of an action batch so every later action has a concrete tab to target.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how BrowserComputer gets the live browser control connection. That connection is used to send Chrome DevTools Protocol commands to the browser.

**Data flow**: It reads the session’s stored browser connection → exposes it as a CDP sender → callers use that sender to request screenshots, mouse events, keyboard events, and page operations.

**Call relations**: BrowserComputer.run, BrowserComputer.act, and helper methods call this whenever they need to talk to Chrome.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: Defines the session operation that describes the current tab for the final response. This lets the caller know which tab is active and what state it is in after actions finish.

**Data flow**: It receives a tab object → gathers tab details from the session implementation → returns those details as JSON-ready data.

**Call relations**: BrowserComputer.run calls this near the end, combining the tab information with the action output and screenshot.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Defines how to get the titles of open tabs. This file uses those titles to spot likely sign-in or registration pages and warn the caller.

**Data flow**: It reads the session’s current tabs → extracts their titles → returns a list of title strings.

**Call relations**: BrowserComputer.run calls this after actions complete, then passes the titles to sign_in_warning.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Defines a way to run a JavaScript function on a specific page object. In this file, it is mainly used to inspect a clicked dropdown element.

**Data flow**: It receives a browser session id, a page object id, JavaScript code, and optional arguments → runs that code against that object in the browser → returns the JSON-like result.

**Call relations**: BrowserComputer._select_reminder uses this contract after finding a select element, so it can read the dropdown’s option text.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: Defines how a stable page reference, such as an element ref returned by page-reading tools, is turned into a browser node. This is needed when an action names an element instead of giving screen coordinates.

**Data flow**: It receives a tab and a ref string → looks up the matching browser-side node and backend id → returns both so Chrome can operate on that element.

**Call relations**: BrowserComputer.act uses this for scroll_to actions, where the goal is to bring a named page element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: Defines how to turn an element reference into a clickable point on the screen. This lets callers say “click this element” without knowing its exact coordinates.

**Data flow**: It receives a tab and a ref string → finds the referenced element and computes a point within it → returns viewport x and y coordinates.

**Call relations**: BrowserComputer.point uses this whenever an action carries a ref instead of a coordinate.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a full batch of browser actions and builds the response the caller sees. It performs the requested actions, waits for the page to calm down, captures a screenshot, and adds useful warnings.

**Data flow**: It receives JSON arguments with a tab id and actions → chooses the tab, validates and adjusts the actions, runs them in batches, waits after each batch, collects downloads and warnings, captures a screenshot, optionally marks the last click, and returns tab info, text output, last click location, and screenshot data.

**Call relations**: This is the main entry for the file’s behavior. It calls int_or_none to read the tab id, validates ComputerAction objects, asks act to perform each action, calls _select_reminder after clicks, uses _to_model for user-facing coordinates, and calls sign_in_warning before returning the final response.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: Performs one browser action, such as click, drag, type, key press, wait, scroll, or screenshot. It translates a simple action name into the specific browser operation needed.

**Data flow**: It receives a tab and one validated action → finds the target point if needed, chooses behavior based on the action type, sends mouse or keyboard commands or waits, and returns a short human-readable message plus the last clicked point when relevant.

**Call relations**: BrowserComputer.run calls this for each action in a batch. It delegates details to point, _click, _drag, _scroll, _dispatch, _to_viewport, _to_model, require_point, and require_coord depending on the action.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: Finds where an action should happen on the screen. It supports both element references and direct coordinates.

**Data flow**: It receives a tab and action → if the action has an element ref, it asks the browser session for that element’s point; if it has a model coordinate, it converts it to viewport coordinates; otherwise it returns nothing.

**Call relations**: BrowserComputer.act calls this before actions that may need a location. It uses _to_viewport when the caller supplied coordinates in the model’s coordinate system.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: Converts coordinates from the automation model’s screen size to the real browser viewport size. This keeps clicks accurate when the model and browser use different dimensions.

**Data flow**: It receives an x and y coordinate in model space → scales that point into viewport space → returns browser-ready x and y values.

**Call relations**: BrowserComputer.act and BrowserComputer.point use this before sending mouse actions to Chrome.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: Converts real browser viewport coordinates back into the model’s coordinate system. This makes returned click locations meaningful to the caller.

**Data flow**: It receives an x and y coordinate from the browser viewport → scales it back into model space → returns model x and y values.

**Call relations**: BrowserComputer.act uses this when writing messages like “Clicked (x,y)”, and BrowserComputer.run uses it for the final last_click field.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: Checks whether a click landed on a native HTML select dropdown and, if so, prepares a reminder explaining the safer way to choose an option. Native dropdowns are a special case because clicking their opened options may not work through this browser control path.

**Data flow**: It receives a tab and click point → asks the browser what element is under that point, walks up to any surrounding select element, reads its option labels and element reference, and returns a reminder string; if anything cannot be inspected, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It calls select_reminder to turn the discovered dropdown information into a user-facing message.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: Sends a prepared list of keyboard-related browser commands. It is used after other code has translated text or a key combination into Chrome commands.

**Data flow**: It receives a tab and a list of CDP method-and-parameter pairs → sends each command to the tab’s browser session → changes the page as those keyboard events take effect, with no direct return value.

**Call relations**: BrowserComputer.act calls this for type and key actions after type_text or press_combo has built the command list.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: Sends one mouse event to the browser, such as moving, pressing, releasing, or wheel scrolling. It is the small shared doorway through which all mouse actions pass.

**Data flow**: It receives a tab and mouse event parameters → sends an Input.dispatchMouseEvent command to Chrome for that tab → the browser processes the event as if a user moved or used the mouse.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build larger mouse gestures.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: Performs a left, right, double, or triple click at a browser viewport point. It sends the same kind of down-and-up mouse events a real mouse would generate.

**Data flow**: It receives a tab, x and y coordinates, a mouse button, and click count → calculates active keyboard modifiers like Shift or Ctrl → moves the mouse there and sends press/release pairs the requested number of times.

**Call relations**: BrowserComputer.act calls this for click actions. It uses _mouse_event for each low-level mouse message and modifiers_mask to include any currently held modifier keys.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: Performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize dragging after seeing gradual movement.

**Data flow**: It receives a tab, start coordinates, and end coordinates → sends a move to the start, presses the left mouse button, sends several intermediate moves, then releases at the end → the page receives a realistic drag gesture.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It relies on _mouse_event for the individual mouse messages and modifiers_mask for held keyboard modifiers.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: Scrolls the page or an area under the mouse pointer. It does this by sending a mouse wheel event at a chosen point.

**Data flow**: It receives a tab, x and y coordinates, and horizontal and vertical scroll distances → moves the mouse to that point → sends a wheel event with those distances.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating direction and amount. It uses _mouse_event to send both the positioning move and the wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: Looks for signs that an open tab is about signing in or registering. It returns a safety reminder so the system does not sign in or create accounts without user approval.

**Data flow**: It receives a list of tab titles → lowercases them and searches for sign-in-related phrases → returns the warning text if any title matches, otherwise returns nothing.

**Call relations**: BrowserComputer.run calls this near the end of a batch and appends its message as a system reminder when needed.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: Builds a clear instruction message for native select dropdowns. It tells the caller to use form input instead of trying to click dropdown options directly.

**Data flow**: It receives an optional element ref, a list of visible option labels, and the total option count → formats a short list of choices and the recommended next step → returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected a clicked select element.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: Draws a visible blue dot on a screenshot at the last click location. This helps the caller verify where the browser action landed.

**Data flow**: It receives a base64-encoded screenshot and a viewport point → decodes the image, draws a translucent circle over that point, re-saves it as JPEG, re-encodes it as base64, and returns the updated screenshot string.

**Call relations**: BrowserComputer.run schedules this after capturing a screenshot when there was a recent click. It is run outside the main async event loop thread so image processing does not block other async work.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: Safely reads a tab id that may arrive as an integer, float, string, or missing value. It normalizes acceptable values to an integer.

**Data flow**: It receives a JSON value or nothing → converts integers, floats, and non-empty strings to int → returns that int, or returns None for anything else.

**Call relations**: BrowserComputer.run uses this when reading the optional tab_id argument before asking the session for a page.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: Checks that an action has a usable screen point when one is mandatory. It turns a missing coordinate into a clear validation error.

**Data flow**: It receives a possible point and the action name → if the point exists, returns it unchanged; if not, raises an error saying the action needs a coordinate or ref.

**Call relations**: BrowserComputer.act calls this before click, right-click, drag-end, and similar actions that cannot happen without a target point.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: Checks that a required coordinate field is present. It is used for fields where a fallback would be unsafe or ambiguous.

**Data flow**: It receives a possible coordinate and the field name → returns the coordinate when present; otherwise raises a validation error naming the missing field.

**Call relations**: BrowserComputer.act calls this for the starting coordinate of a drag action before converting it to browser viewport coordinates.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `action dispatch preparation`

This file acts like a careful proofreader for a list of browser actions. A model may produce actions that are mostly right but incomplete in ways that would fail or behave oddly in a real browser. For example, it might say “type this text” at a location without first clicking there, or say “scroll” without giving a place to start from. This file patches those cases into safer, more explicit actions.

The main function, `fixup_actions`, walks through the action list one item at a time and builds a corrected list. If typing needs focus, it inserts a left click first. If text contains visible escape strings like `\n`, it turns them into real newline characters. If a scroll has no anchor point, it uses the center of the model’s effective screen size. If a wait has no duration, it gives it a default of 3 seconds. It also simplifies double-clicks or triple-clicks aimed at a named page element into a single left click, because clicking by reference is already a higher-level instruction.

The second public helper, `split_at_waits`, divides one long action list into smaller batches whenever a wait appears. This gives the browser time to settle between groups of actions, like pausing between steps in a recipe before checking what changed.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: Repairs a batch of browser actions so they are more likely to work when sent to the browser. It fills in missing details, adds focus clicks before typing, cleans escaped text, and replaces ambiguous actions with clearer ones.

**Data flow**: It receives a list of `ComputerAction` objects, the browser viewport size, and optionally the size used by the model. It first works out the effective model size and the center point of that area. Then it reads each action and either keeps it, copies it with safer values, or inserts a replacement action. The result is a new list of actions; the original list is not directly rewritten.

**Call relations**: This is the main cleanup step in the file. When it needs to focus a typing target, it calls `_focus_click` to create a click action. When it needs to turn visible escape strings into real characters, it calls `_unescape_text`. It also relies on `effective_model_size` to choose sensible fallback coordinates, and creates `ComputerAction` or `ScrollParameters` objects when replacing incomplete actions.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: Splits a corrected action list into smaller groups, ending a group whenever a wait action appears. This lets the browser pause and update before the next group of actions runs.

**Data flow**: It receives one ordered list of actions. It builds a current batch, adds each action to it, and whenever it sees an action named `wait`, it closes that batch and starts a new one. It returns a list of batches, where each batch is itself a list of actions.

**Call relations**: This function is a companion to action cleanup. After actions have been prepared, this can be used before dispatch so waits become natural stopping points rather than just another item in one continuous stream.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: Creates a left-click action that focuses the place where text is about to be typed. This matters because browsers usually send typed text to whatever field or page area is currently focused.

**Data flow**: It receives a typing action. If that action has screen coordinates, it creates a left click at those coordinates. Otherwise, it creates a left click aimed at the same page reference as the typing action. The output is a new `ComputerAction` that performs the focus click.

**Call relations**: `fixup_actions` calls this when it sees a typing action that points to a place but was not immediately preceded by a click-like action. `_focus_click` hands back the extra click that should be inserted before the typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: Turns visible escape sequences in typed text, such as the two characters `\n`, into the real character they mean, such as an actual newline. This helps model-produced text behave like intended text when typed into the browser.

**Data flow**: It receives a `ComputerAction`, reads its `text` field, and checks for known literal escape strings. If none are present, it returns the action unchanged. If it finds any, it replaces them with their real tab or newline characters and returns a copied action with the updated text.

**Call relations**: `fixup_actions` calls this for every typing action after any needed focus click has been added. `_unescape_text` uses the action’s copy method so the corrected text is placed into a new version of the action rather than mutating the existing object directly.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Form and file interactions
Focused helpers fill fields and attach files while reporting impossible browser-state claims clearly.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not just store text and files as plain data. A field on a page is a live browser object, and changing it correctly often means talking to the browser itself, not just editing a saved copy of the page. This file is the bridge for that job.

The main class, BrowserForms, receives small JSON-like command arguments. Those arguments usually name a browser tab, a page element reference, and a value or file path. The class asks the browser session for the right tab, turns the saved element reference back into a real browser node, and then sends Chrome DevTools Protocol commands. The Chrome DevTools Protocol is the control channel that lets automation code inspect and operate the browser.

There are three main actions. One checks the actual byte sizes of files currently attached to a file input, which helps tell whether an upload really reached the browser. One attaches local file paths to a file input. One sets the value of a normal form control and fires the usual input and change events, so the web page reacts as if a person had typed or selected something.

If the requested element cannot be used, the file raises a HallucinationError with guidance to re-read the page and use a valid reference. That matters because automation agents can otherwise act on stale or imagined page elements.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This protocol method describes how BrowserForms gets the current browser page, or a specific tab if one was requested. It is a promise that the real browser session object must provide this ability.

**Data flow**: It receives an optional tab number. The implementing browser session uses that to choose a page object, then returns that page so form actions can work in the right tab.

**Call relations**: BrowserForms calls on this capability at the start of each form action. It is the first step before resolving an element reference or sending commands to the browser.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This protocol method describes how BrowserForms gets the low-level browser control connection. That connection is used to send Chrome DevTools Protocol commands, which are structured instructions to the browser.

**Data flow**: It takes no extra input. The implementing browser session returns a connection object that can send commands to the browser and receive replies.

**Call relations**: BrowserForms uses this after it has chosen a page and resolved an element reference. The connection is what actually performs operations such as resolving a DOM node or setting files on a file input.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This protocol method describes how BrowserForms runs a small JavaScript function on a specific browser-side object. It is used when a form action needs page behavior, not just a raw browser command.

**Data flow**: It receives a session id, a browser object id, a JavaScript function as text, and optional arguments. The implementing session runs that function on the object in the browser and returns the JSON-like result.

**Call relations**: BrowserForms uses this for checking attached file sizes and for setting form values. It is the handoff point from Python code into JavaScript that runs inside the web page.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This protocol method describes how BrowserForms turns a saved page reference into the browser’s real internal node information. In plain terms, it finds the actual page element that a previous page read named.

**Data flow**: It receives a page object and a reference string. The implementing session looks up that reference and returns both a node description and a backend node id, which the browser control connection can use.

**Call relations**: Every real form action depends on this. BrowserForms calls it after getting the page, then uses the returned node and backend id to target the correct element.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what files a browser file input is actually holding by reading their byte sizes. It is useful because a file name alone does not prove the upload data has arrived inside the browser.

**Data flow**: It reads a JSON-like argument object containing an optional tab id and a required element reference. It chooses the tab, resolves the reference to a real browser node, asks the browser for a JavaScript object id for that node, then runs a small JavaScript function that reads this.files and returns file sizes. The output is a list of integer byte sizes; if the browser response is not shaped as expected, it safely returns an empty list.

**Call relations**: This action starts by using _tab_id to normalize the requested tab. It then relies on the browser session to find the page and resolve the element reference, uses the browser connection to resolve the DOM node, and finally uses call_on to run JavaScript on the element.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more file paths to a file input element on the page. It is the automation equivalent of a user choosing files in a file picker.

**Data flow**: It reads a JSON-like argument object containing an optional tab id, an element reference, and a list of file paths. It checks that the reference and paths are strings, finds the real browser node for the reference, and sends the browser command that sets the file input’s files. It returns a small confirmation object containing the same reference and the file paths it sent. If the referenced element is not a file input, it raises a clear HallucinationError instead of silently failing.

**Call relations**: This action uses _tab_id to choose the tab, then asks the browser session to resolve the page element. It hands the actual file-setting work to the browser connection through the DOM.setFileInputFiles command. If the browser rejects that command, it turns the low-level CdpError into user-facing guidance.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a form field on the page and triggers the normal browser events that web apps listen for. It works for text inputs, checkboxes, radio buttons, select menus, and editable page areas.

**Data flow**: It reads a JSON-like argument object containing an optional tab id, an element reference, and a value. It chooses the tab, resolves the reference, turns the browser node into a JavaScript object id, and runs a small JavaScript function on that element. The JavaScript updates the right property, fires input and change events, and returns the element’s resulting value or text.

**Call relations**: This action begins with _tab_id, then uses the browser session to reach the page and locate the element. It uses the browser connection to resolve the node and call_on to run the JavaScript that performs the page-visible change. If the reference cannot be resolved, it raises HallucinationError with instructions to re-read the page.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from incoming JSON-like data into either an integer tab number or no tab choice at all. It accepts numbers and non-empty strings because external callers may send tab ids in slightly different forms.

**Data flow**: It receives a JSON-like value. If the value is an integer, it returns it unchanged; if it is a float, it converts it to an integer; if it is a non-empty string, it parses it as an integer. For anything missing or unsupported, it returns None, meaning “use the default page.”

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input all use this helper before asking the browser session for a page. It keeps tab selection rules consistent across all form actions.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### Shared action and error vocabulary
Common data definitions describe supported browser actions and the dedicated error used for invented values.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `cross-cutting`

This file is like a form that every browser action must fill out before it can be carried out. Instead of letting different parts of the system describe a click or scroll in different ways, it defines one common language: an action has a type, and then optional details such as a screen coordinate, typed text, scroll distance, wait duration, or page element reference.

The main idea is safety and clarity. A browser automation agent may say “click this element,” “type this text,” or “scroll down half a page.” These requests need to be precise enough for another layer to execute them. The Pydantic models in this file act as checked containers: they describe what fields are allowed, what values make sense, and what each field means. For example, a wait duration must be between 0 and 30 seconds, and a scroll amount can be a number from 0 to 5 viewport heights or the special value “max.”

The file also separates simple click-like actions from other actions with `CLICK_ACTIONS`, which lets later code quickly recognize actions that behave like mouse clicks. Without this file, browser actions would likely be passed around as loose dictionaries or strings, making mistakes harder to catch and harder for humans to understand.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `request handling`

This file contains one custom error type: `HallucinationError`. In this project, the browser automation layer receives information from a model, and that model may sometimes return a value that sounds plausible but is not real. For example, it might refer to a browser element that does not exist. That kind of mistake is often called a “hallucination” in AI systems.

`HallucinationError` is a more specific form of `ValidationError`, which means it belongs to the family of errors raised when incoming data fails a validity check. The point of giving this case its own name is clarity. Code that catches errors can tell the difference between an ordinary malformed input and a model inventing a browser reference.

Without this file, the system could still raise a general validation error, but it would lose an important bit of meaning. This named error acts like a label on a package: it tells later code, logs, or debugging tools exactly what kind of bad input was found.


### Keyboard event translation
Keyboard helpers convert human text and shortcut requests into Chrome DevTools Protocol key events.

### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

This file is the browser automation keyboard translator. A person or higher-level action may say “type hello” or “press Shift+Enter”, but Chrome needs a detailed message for each key: which physical key it was, what character it produced, whether Shift or Control is held, whether it came from the keypad, and so on. This file builds those messages.

It starts with a US keyboard map copied from Playwright, a browser automation project. That map is like a keyboard dictionary: it knows that `KeyA` means `a`, that Shift changes it to `A`, and that `Enter` has a special code. It also includes macOS editing commands, because on Macs some shortcuts are not just characters; they are commands such as “move to beginning of line” or “delete word backward”.

A small `KeyboardState` remembers which keys and modifier keys are currently held down. That matters because pressing `a` while Shift is down should produce `A`, and pressing the same key twice while held should be marked as an auto-repeat. The public helpers then produce Chrome DevTools Protocol calls, which are structured instructions such as `Input.dispatchKeyEvent` or `Input.insertText`. For long text, it skips key-by-key typing and inserts the text in one go, like pasting, because that is faster and avoids unnecessary event noise.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds a lookup table that lets the rest of the file find a key by several names, such as its physical code, its visible character, or a friendly alias. This makes later key lookup simple and consistent.

**Data flow**: It takes the raw US keyboard layout definitions as input. For each key, it creates a richer description that includes the normal character, shifted character if any, physical key code, text output, and key location. It returns a new dictionary where many possible names point to the right key description.

**Call relations**: This runs when the file is loaded to create `LAYOUT_CLOSURE`, the main keyboard lookup table. Later, `_description_for` relies on that table instead of searching through the raw keyboard map each time a key is pressed.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Converts the currently held modifier keys, such as Shift or Control, into the number format Chrome expects. Chrome DevTools Protocol uses bits in one integer rather than a list of names.

**Data flow**: It receives a set of modifier names. It checks each known modifier and adds its assigned bit value if that modifier is present. It returns a single integer that represents the whole modifier state.

**Call relations**: Both `key_down` and `key_up` call this right before creating a Chrome key event. It is the final translation step from this file’s human-readable modifier set into Chrome’s compact modifier field.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the full browser-facing description for a requested key, taking the current Shift state into account. It also rejects unknown keys with a clear validation error.

**Data flow**: It receives the current keyboard state and a key name or character. It looks up that key in `LAYOUT_CLOSURE`; if Shift is held and the key has a shifted form, it switches to that shifted version. If other modifiers like Control or Alt are held, it clears the text output because shortcut keys usually should not type a character. It returns the final key description to use for the event.

**Call relations**: `key_down` and `key_up` both call this before building their protocol messages. It is the gatekeeper that decides what a key means at this moment, based on both the requested key and the keys already being held.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up special macOS editing commands for a key plus its modifiers. This helps synthetic keyboard events behave more like real keyboard input in Chrome on a Mac.

**Data flow**: It receives a physical key code and the set of currently held modifiers. It builds a shortcut name such as `Shift+Meta+ArrowLeft`, searches the macOS command table, removes the trailing colon from command names, and filters out simple insert commands. It returns a list of command names to attach to the Chrome key event.

**Call relations**: `key_down` calls this only when the target environment is macOS. The commands it returns are placed directly into the Chrome DevTools key event so Chrome can perform the expected native-style editing action.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome instruction for pressing a key down. It also updates the remembered keyboard state so later keys know what is being held.

**Data flow**: It receives the mutable keyboard state, the requested key, and whether the browser is on macOS. It asks `_description_for` what the key should mean, checks whether this is an auto-repeat, records the key as pressed, records modifier keys like Shift if needed, optionally adds macOS editing commands, and builds an `Input.dispatchKeyEvent` call. The output is one protocol call ready to send to Chrome, and the state is changed to show the key is now down.

**Call relations**: `press_combo` uses this to press each key in a shortcut from left to right, and `type_text` uses it for short text characters that can be represented as real key presses. Inside, it relies on `_description_for`, `_mac_commands`, and `modifiers_mask` to fill in the event accurately.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome instruction for releasing a key. It also updates the remembered keyboard state so the system no longer thinks that key or modifier is being held.

**Data flow**: It receives the mutable keyboard state and the key to release. It looks up the key description, removes the key from the pressed-key set, removes it from the modifier set if it is a modifier, calculates the remaining modifier mask, and returns an `Input.dispatchKeyEvent` call of type `keyUp`. The state changes from “this key is down” to “this key is no longer down”.

**Call relations**: `press_combo` calls this in reverse order after pressing all keys in a shortcut, like releasing `A` before releasing Control. `type_text` calls it after each character key press. It uses `_description_for` and `modifiers_mask` to keep the release event consistent with the press event.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string like `Ctrl+Shift+A` into the sequence of key-down and key-up events Chrome needs. This lets callers use common shortcut wording instead of manually building several low-level events.

**Data flow**: It receives the current keyboard state, a combo string, and whether the target is macOS. It splits the string around plus signs, normalizes friendly names like `ctrl` to `Control`, rejects an empty combo, presses each key in order, then releases them in reverse order. It returns a list of Chrome protocol calls, and the keyboard state is restored after the releases unless an error occurs.

**Call relations**: This is a higher-level helper built on `key_down` and `key_up`. A caller that wants a shortcut asks this function for the whole event sequence instead of calling the lower-level functions one by one.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text into browser input events. It uses real key presses for short, simple text so web pages can react to keystrokes, but uses direct text insertion for long text because that is faster.

**Data flow**: It receives the current keyboard state, the text to type, and whether the target is macOS. If the text is longer than the file’s character limit, it returns one `Input.insertText` call containing the whole string. Otherwise, it walks character by character: known keyboard characters become a key-down followed by a key-up, while characters not in the keyboard map are inserted directly. It returns the full list of protocol calls and updates the state during each press and release.

**Call relations**: This is the main helper for normal typing actions. For short recognized characters it delegates to `key_down` and `key_up`, preserving realistic browser events; for long or unmapped text it hands Chrome a direct insert instruction instead.

*Call graph*: calls 2 internal fn (key_down, key_up).

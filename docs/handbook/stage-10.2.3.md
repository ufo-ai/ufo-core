# Browser action execution and input handling  `stage-10.2.3`

This stage is part of the main work loop, where an automation agent’s plan is turned into real browser behavior. It starts with actions.py, which acts like the rulebook. It lists the browser actions the agent is allowed to request, such as clicking, typing, scrolling, waiting, taking screenshots, and using shortcuts, and defines what information each request must include.

Before anything reaches the browser, fixup.py tidies the plan. It fixes small, predictable mistakes, like adding a focus step before typing or filling in a sensible wait time when one is missing.

computer.py is the main driver. It receives the cleaned-up action and sends the matching browser input, such as mouse movement, clicks, scrolls, screenshots, or waits. For keyboard work, it relies on keys.py, which translates text and shortcuts into Chrome DevTools Protocol messages. That protocol is Chrome’s remote-control language. keys.py also tracks held keys so combinations like Ctrl+C or shifted characters work correctly.

Finally, settle.py waits until the page has reacted enough to continue, while ignoring unrelated background noise such as ads or tracking requests.

## Files in this stage

### Action definitions and fixups
Defines the browser action request shapes and normalizes small planning mistakes before execution.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is a shared vocabulary for controlling a browser. Instead of letting other parts of the system pass around loose, unclear instructions like “do something on the page,” it gives them a strict menu of actions and the fields each action may need. This matters because browser automation is easy to get wrong: a click needs either a screen coordinate or an element reference, typing needs text, scrolling needs a direction and distance, and waiting should not pause forever.

The file uses Pydantic models, which are Python classes that describe and validate structured data. You can think of them like a form with labeled boxes: if the action is “scroll,” the form has a place for scroll direction and amount; if the action is “wait,” it has a duration, limited to a safe range.

`ActionType` lists every action name the system understands. `CLICK_ACTIONS` groups the click-like actions so other code can quickly recognize them. `ScrollParameters` describes how far and which way to scroll, including a special `max` option for jumping to the end of a page. `ComputerAction` is the main action request: it combines the action name with optional details such as coordinates, text, duration, drag start point, or an element reference found earlier on the page.


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `request handling, just before dispatching browser actions`

A browser automation model may produce a list of actions that is almost right but not quite safe to run as-is. This file acts like a helpful editor for that list before execution. For example, if an action says to type into a field but no click happened first, the code inserts a click so the field gets focus. If text contains the literal characters "\\n" or "\\t", it turns them into a real newline or tab. If a scroll has no position to start from, it uses the center of the model's screen area. If a wait has no duration, it fills in a default wait time.

The important detail is that this file remembers where each corrected or inserted action came from. It wraps every outgoing action in a PlannedAction, which stores the original action index as origin. That matters when something fails: the caller should hear about the action they actually asked for, not about an extra helper click that was quietly inserted.

The file also splits action plans into batches at wait actions. A wait means “pause and let the page settle,” so later actions are kept for the next batch instead of being fired immediately.

#### Function details

##### `fixup_actions`  (lines 26–70)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[PlannedAction]
```

**Purpose**: This function takes a caller's browser actions and returns a safer, more explicit plan. It repairs predictable mistakes, while preserving a link back to the original action number so errors can be reported clearly.

**Data flow**: It receives a list of ComputerAction objects, the current viewport size, and optionally the model's working screen size. It first works out the effective model size and screen center. Then it walks through each action: it may insert a focus click before typing, replace escaped text with real tab or newline characters, add a center point to anchorless scrolls, turn an incomplete scroll_to into a normal scroll, add a default wait duration, or simplify reference-based double and triple clicks into a left click. The result is a new list of PlannedAction objects, each carrying both the action to run and the original action index it belongs to.

**Call relations**: This is the main repair step in the file. When it needs a focus click, it asks _focus_click to build one. When it needs to clean typed text, it asks _unescape_text to rewrite it. It also relies on effective_model_size to know what screen size to use, and it creates new ComputerAction, ScrollParameters, and PlannedAction objects as needed for the corrected plan.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 4 external calls (__init__, __init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 73–84)

```
def split_at_waits(planned: list[PlannedAction]) -> list[list[PlannedAction]]
```

**Purpose**: This function divides a planned action list into smaller batches, ending each batch with a wait. It is used so the browser gets time to settle before later actions continue.

**Data flow**: It receives a list of PlannedAction objects. It builds a current batch one item at a time. Whenever it sees an action whose type is wait, it closes that batch and starts a new one. At the end, any remaining actions become the final batch. It returns a list of batches, where each batch is itself a list of PlannedAction objects.

**Call relations**: This function fits after fixup_actions has produced the corrected plan. It does not create or change actions; it only groups them so the later browser-dispatch step can pause at natural waiting points before sending more work.


##### `_focus_click`  (lines 87–90)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper builds the click that should happen before typing into a specific place. It makes sure the target field is focused, like clicking into a text box before using the keyboard.

**Data flow**: It receives the typing action that needs focus. If that action has a coordinate, it creates a left_click at that coordinate. Otherwise, it creates a left_click using the action's reference target. The output is a new ComputerAction representing the click.

**Call relations**: fixup_actions calls this when it sees a type action that points at a location or reference but was not preceded by a click. The click it returns is inserted into the planned action list before the typing action, and both are tied back to the same original caller action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 93–99)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns written escape sequences in typed text into the actual characters they mean. For example, it changes the two characters "\\n" into a real newline.

**Data flow**: It receives a ComputerAction, reads its text field, and checks for known escaped literals such as "\\t" and "\\n". If none are present, it returns the original action unchanged. If any are present, it replaces them in the text and returns a copied action with the cleaned text.

**Call relations**: fixup_actions calls this for every type action after any needed focus click has been added. The cleaned action then goes into the planned action list, so the browser receives the intended keystrokes rather than the visible backslash-letter sequences.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Input execution
Translates normalized browser actions into concrete clicks, typing, scrolling, screenshots, and keyboard protocol events.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is like the hands and eyes of the browser automation system. Other parts of the system decide on actions in a simple form, such as “click this reference” or “type this text.” BrowserComputer takes those requests, checks and adjusts them, performs them in the active browser tab, waits for the page to settle, and returns a fresh screenshot plus a plain summary of what happened.

It also adds safety and usability help. If the user is repeatedly scrolling, it reminds them that reading page text may be better. If tab titles look like sign-in pages, it warns that signing in needs user confirmation. If a click lands on a native HTML select dropdown, it explains that normal option-clicking will not work in this browser path and suggests using form input instead. Downloads and automatically handled JavaScript dialogs are also reported back.

The file works through the Chrome DevTools Protocol, often shortened to CDP, which is a control channel browsers expose for tools to send mouse, keyboard, page, and DOM commands. Coordinates are translated between the model’s coordinate space and the real browser viewport, so the agent can work in a stable screen size while the browser may have a different one.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This protocol method describes how BrowserComputer asks for the browser tab it should act on. A real session object must provide this method so actions can be aimed at the right tab.

**Data flow**: It receives an optional tab identifier. The real implementation uses that to find or choose a tab, then returns an object with a browser session id and keyboard state.

**Call relations**: BrowserComputer.run calls this near the start of an action request. The returned tab is then passed through the rest of the action flow so clicks, keys, and screenshots go to the correct browser session.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This protocol method describes how BrowserComputer gets the live browser control connection. That connection is used to send low-level browser commands.

**Data flow**: It takes no extra input. The real implementation returns a CDP connection object, which BrowserComputer then uses to send commands such as mouse events, screenshots, and DOM queries.

**Call relations**: BrowserComputer.run, BrowserComputer.act, BrowserComputer._select_reminder, BrowserComputer._dispatch, and BrowserComputer._mouse_event rely on this connection whenever they need the browser itself to do something.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This protocol method describes how to collect current information about a tab for the final response. It gives the caller context about the browser state after actions run.

**Data flow**: It receives a tab object. The real implementation reads tab state and returns a JSON-style dictionary that is merged into BrowserComputer.run's output.

**Call relations**: BrowserComputer.run calls this after performing actions and taking a screenshot, so the response includes both the visual result and useful tab metadata.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This protocol method describes how BrowserComputer asks for the titles of open tabs. The titles are used to spot sign-in or account-creation situations.

**Data flow**: It takes no extra input. The real implementation returns a list of tab title strings, which are checked for words like “sign in” or “register.”

**Call relations**: BrowserComputer.run calls it near the end of a batch. Its result is handed to sign_in_warning so a safety reminder can be added when needed.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This protocol method describes how to run a JavaScript function on a specific browser object. BrowserComputer uses it when it needs more information about an element inside the page.

**Data flow**: It receives a session id, an object id from the browser, JavaScript code, and optional arguments. The real implementation runs that code against the browser object and returns a JSON-style result.

**Call relations**: BrowserComputer._select_reminder uses this after finding a clicked select element. It asks the page for option text so the final response can explain what choices are available.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This protocol method describes how a stable page reference, such as an element ref returned by page reading, is turned back into a browser node. It lets actions target page elements without relying only on screen coordinates.

**Data flow**: It receives the current tab and a reference string. The real implementation looks up the referenced element and returns a browser node plus its backend node id.

**Call relations**: BrowserComputer.act uses this for scroll_to actions. Once the reference is resolved, the browser can be told to bring that element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This protocol method describes how BrowserComputer finds a clickable point for a referenced page element. It lets an action say “click this ref” instead of giving exact pixels.

**Data flow**: It receives the current tab and a reference string. The real implementation calculates a viewport coordinate for that element and returns it as x and y values.

**Call relations**: BrowserComputer.point calls this when an action contains a ref. BrowserComputer.act then uses the returned point for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–166)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for carrying out a batch of browser actions. It validates the requested actions, performs them, waits for the page after each group, and returns a summary plus a fresh screenshot.

**Data flow**: It receives a JSON-style request containing actions and optionally a tab id. It chooses the tab, converts raw action data into ComputerAction objects, adjusts coordinates, performs each action, tracks messages and warnings, marks the last click on the screenshot, drains new download notices, and returns tab info, output text, last-click coordinates, and screenshot data.

**Call relations**: This function is the top-level coordinator in the file. It calls int_or_none to read the tab id, BrowserComputer.act for each individual action, BrowserComputer._select_reminder after clicks, BrowserComputer._to_model for reporting coordinates, sign_in_warning for safety reminders, and mark_click in a worker thread so image editing does not block the async event loop.

*Call graph*: calls 6 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning, __init__); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 168–247)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This function performs one browser action, such as clicking, typing, pressing a key, waiting, scrolling, or taking a screenshot. It turns a simple action name into the concrete browser work needed for that action.

**Data flow**: It receives the current tab and one validated ComputerAction. It first finds a target point when needed, then sends mouse, keyboard, scroll, wait, or DOM commands, and returns a human-readable message plus the final clicked point if there was one.

**Call relations**: BrowserComputer.run calls this for each action in the batch. Depending on the action, it hands off to BrowserComputer.point, BrowserComputer._click, BrowserComputer._drag, BrowserComputer._scroll, BrowserComputer._dispatch, coordinate conversion helpers, or validation helpers such as require_point and require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 249–254)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This function finds where on the browser screen an action should happen. It supports both element references and direct model coordinates.

**Data flow**: It receives a tab and an action. If the action names a ref, it asks the browser session for that element’s point; if the action gives coordinates, it converts them into viewport pixels; otherwise it returns no point.

**Call relations**: BrowserComputer.act calls this before deciding how to execute an action. When direct coordinates are used, it relies on BrowserComputer._to_viewport so the browser receives coordinates in its own screen space.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 256–258)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts coordinates from the model’s screen size into the browser viewport’s real pixel space. Without this, clicks could land in the wrong place when the two sizes differ.

**Data flow**: It receives an x and y coordinate in model space. It uses the configured viewport size and model size to scale that point, then returns viewport x and y values.

**Call relations**: BrowserComputer.point uses it for coordinate-based actions, and BrowserComputer.act uses it when calculating drag starts or default scroll positions.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 260–262)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts real browser viewport coordinates back into the model’s coordinate space. It is used when reporting actions in the same coordinate system the caller understands.

**Data flow**: It receives an x and y coordinate from the browser viewport. It scales the point back to the model size and returns model x and y values.

**Call relations**: BrowserComputer.act uses it to describe where clicks and drags occurred. BrowserComputer.run uses it to return the last click in model coordinates.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 264–293)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This function checks whether a click landed on a native HTML select dropdown and, if so, builds a helpful reminder. This matters because this browser path cannot reliably choose select options by clicking them normally.

**Data flow**: It receives the tab and clicked viewport point. It asks the page what element is at that point, walks up to a select element if present, reads up to ten option labels and the total count, tries to get a stable element ref, and returns a reminder string or nothing if no select was found.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses the browser connection, BrowserComputerSession.call_on, and select_reminder to turn page details into advice the caller can act on.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 295–297)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This helper sends a prepared sequence of keyboard-related browser commands. It is used for typing text and pressing key combinations.

**Data flow**: It receives a tab and a list of CDP calls, where each call has a method name and parameters. It sends each command to the browser session in order and returns nothing.

**Call relations**: BrowserComputer.act calls this for type and key actions after other keyboard helpers have prepared the exact low-level events.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 299–302)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This helper sends one mouse event to the browser. It keeps the repeated CDP command shape in one place for clicks, drags, and scrolling.

**Data flow**: It receives a tab and a dictionary of mouse event parameters. It sends those parameters as an Input.dispatchMouseEvent command to the browser and changes the page only through that browser event.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this whenever they need to move the mouse, press or release a button, or send a wheel event.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 304–343)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This function performs a real mouse click, double-click, triple-click, or right-click at a viewport point. It sends the same kind of press and release events a user’s mouse would create.

**Data flow**: It receives the tab, x and y coordinates, a mouse button name, and a click count. It reads the current keyboard modifiers, moves the mouse to the point, sends one or more press-and-release pairs, and returns nothing after the browser has received the events.

**Call relations**: BrowserComputer.act calls this for click actions. It relies on BrowserComputer._mouse_event for each browser event and modifiers_mask so held keys like Shift or Ctrl are reflected in the click.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 345–398)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This function performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop after intermediate movement, like a real hand dragging an item.

**Data flow**: It receives a tab, starting coordinates, and ending coordinates. It reads active keyboard modifiers, moves to the start, presses the left mouse button, moves through intermediate points, releases at the end, and returns nothing.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It sends the actual browser events through BrowserComputer._mouse_event and uses modifiers_mask to include held keyboard modifiers.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 400–424)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This function scrolls the page at a chosen point using a mouse wheel event. It supports both vertical and horizontal scrolling by sending delta values to the browser.

**Data flow**: It receives a tab, x and y coordinates, and scroll distances dx and dy. It moves the mouse to the point, sends a wheel event with those distances and current modifiers, and returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after computing the scroll direction and amount. BrowserComputer._scroll then uses BrowserComputer._mouse_event to send the movement and wheel events.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 427–431)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This helper looks at tab titles and decides whether to show a safety warning about signing in or creating accounts. It helps prevent the agent from taking account-related actions without user confirmation.

**Data flow**: It receives a list of tab title strings. It lowercases them, searches for sign-in and registration keywords, and returns the warning text if any match is found; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after reading tab titles. Its result may be added to the system reminders in the final action response.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 434–446)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This helper writes the user-facing reminder for a clicked native select dropdown. It explains the safer way to choose an option and shows the available option text.

**Data flow**: It receives an optional element ref, a list of option labels, and the total option count. It formats the visible options, notes if more exist, chooses instructions based on whether a ref is known, and returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked select element. The returned text is passed up to BrowserComputer.run and may appear in the final output.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 449–465)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This function draws a small translucent blue dot on a screenshot at the last click location. It makes the returned screenshot easier to understand by showing exactly where the action landed.

**Data flow**: It receives a base64-encoded screenshot and a viewport point. It decodes the image, draws a circular overlay at that point, saves the image again as JPEG, encodes it back to base64, and returns the new screenshot string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click-like action. It is run through an executor so the image work does not pause the asynchronous browser workflow.

*Call graph*: 7 external calls (Draw, b64decode, b64encode, alpha_composite, new, open, BytesIO).


##### `int_or_none`  (lines 468–477)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This small helper reads an optional integer value from JSON-like input. It allows tab ids to arrive as an integer, float, string, or absent value.

**Data flow**: It receives a JSON value or nothing. If the value is an int, float, or non-empty string, it converts it to an int; otherwise it returns None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page. The result tells BrowserComputerSession.page whether a specific tab was requested.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 480–483)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This validation helper makes sure an action that needs a screen point actually has one. It gives a clear error instead of letting a click or drag fail later in a confusing way.

**Data flow**: It receives a possible point and the action name. If the point exists, it returns it unchanged; if not, it raises a validation error saying the action needs a coordinate or ref.

**Call relations**: BrowserComputer.act calls this before click, right-click, and drag-end operations. It stops invalid action requests before low-level browser events are sent.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 486–489)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This validation helper makes sure a required coordinate field is present. It is mainly used for drag actions that need a starting point.

**Data flow**: It receives a possible coordinate and the field name to report. If the coordinate exists, it returns it unchanged; if not, it raises a validation error naming the missing field.

**Call relations**: BrowserComputer.act calls this when preparing a left_click_drag action. It ensures the drag has a valid start before converting coordinates and sending mouse events.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `during browser keyboard actions`

Browser automation cannot just say “type A” and hope the page reacts like a real keyboard was used. Web pages often listen for detailed key events: the physical key code, the visible character, whether Shift or Control is held, and whether the key came from the number pad. This file provides that translation layer.

It starts with a US keyboard map. That map says, for example, that the physical key named Digit1 normally means “1”, but with Shift means “!”. It also includes special keys like Enter, arrows, function keys, and number pad keys. A helper builds a lookup table so callers can ask for keys in several friendly ways, such as “Enter”, “\n”, or a literal character.

The file keeps a small KeyboardState, like a notepad recording which modifier keys are down and which physical keys are already pressed. From that state, key_down and key_up build Chrome DevTools Protocol calls, which are structured messages Chrome understands. On Mac, it also adds native editing command names for shortcuts such as Command+A or Option+Arrow.

For longer text, it skips per-key simulation and uses Chrome’s direct text insertion message. That is faster, while short text still goes key by key so page shortcuts, autocomplete, and key handlers can react naturally.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds a practical lookup table from the raw keyboard layout. It lets the rest of the file find a key description not only by physical key name, but also by aliases and printable characters.

**Data flow**: It receives the US keyboard layout, where each physical key has its normal and sometimes shifted meaning. For each entry, it creates a richer KeyDescription, adds shifted versions where needed, and records friendly aliases such as Enter for newline. The result is a dictionary that can answer questions like “what key event should the character ! produce?”

**Call relations**: This runs when the module is loaded to create LAYOUT_CLOSURE. Later, _description_for depends on that lookup table whenever key_down or key_up needs to turn a requested key into Chrome-ready details.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Converts the currently held modifier keys into the numeric flag value Chrome expects. A modifier key is a key like Shift, Control, Alt, or Meta that changes what another key means.

**Data flow**: It receives a set of modifier names that are currently pressed. It checks each known modifier and adds its assigned bit value if present. It returns one number representing the whole modifier state.

**Call relations**: key_down and key_up call this right before building their Chrome DevTools Protocol message. It gives those messages the compact modifier value Chrome uses internally.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the correct description for a requested key, taking the current keyboard state into account. This is where Shift can turn “1” into “!” and other held modifiers can suppress text output for shortcut-style key events.

**Data flow**: It receives the current KeyboardState and a key name or character. It looks up that key in the prepared layout table. If the key is unknown, it raises a validation error. If Shift is held and the key has a shifted form, it uses that form. If other modifiers such as Control or Alt are held, it clears the text field because shortcuts usually should not type visible characters. It returns the final KeyDescription.

**Call relations**: key_down and key_up both call this before creating their event payloads. It is the shared translator that keeps press and release events consistent.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up Mac-specific editing commands for a key plus its modifiers. These commands help Chrome behave like a native Mac app for text movement, deletion, copy, paste, and similar shortcuts.

**Data flow**: It receives a physical key code and the set of currently pressed modifiers. It builds a shortcut string such as Shift+Meta+ArrowLeft, looks that up in the Mac command table, removes the trailing colon from matching command names, and filters out direct insert commands. It returns a list of command names to include in the Chrome key event.

**Call relations**: key_down calls this only when the caller says the target browser is running as Mac. The returned commands are placed into the key-down event so Chrome can perform native-style editing behavior.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome message for pressing a key down and updates the remembered keyboard state. This is the main building block for both typing characters and starting keyboard shortcuts.

**Data flow**: It receives the current KeyboardState, the requested key, and whether Mac behavior should be used. It asks _description_for what this key means right now, checks whether the same physical key is already pressed to mark auto-repeat, records the key as pressed, and records modifier keys if this key is Shift, Control, Alt, or Meta. On Mac it may also add editing commands. It returns a tuple naming the Chrome DevTools method and the event data to send.

**Call relations**: press_combo calls this for each part of a shortcut, and type_text calls it for each short, keyboard-typeable character. It relies on _description_for, _mac_commands, and modifiers_mask to produce a complete event Chrome can understand.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome message for releasing a key and updates the remembered keyboard state. It completes the key press started by key_down.

**Data flow**: It receives the current KeyboardState and the requested key. It finds the key’s description, removes the key from the pressed-key record, and removes it from the pressed-modifier record if it was a modifier. It then returns a Chrome DevTools keyUp message with the updated modifier state.

**Call relations**: press_combo calls this in reverse order after pressing all keys in a shortcut, which mimics how people release shortcut keys. type_text calls it after each synthesized character key. It uses _description_for and modifiers_mask so the release event matches the press event.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string such as Ctrl+C or Shift+Enter into the sequence of Chrome key events needed to press and release those keys. It accepts common user-friendly names and aliases.

**Data flow**: It receives the current KeyboardState, a combo string, and whether Mac behavior should be used. It splits the string on plus signs, trims spaces, translates aliases like ctrl to Control or cmd to Meta, and rejects an empty combo. It presses each key in order, then releases the same keys in reverse order. It returns the full list of Chrome DevTools calls.

**Call relations**: Higher-level browser actions can use this when they need to perform a shortcut. Internally it delegates the actual event creation and state updates to key_down and key_up.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text into Chrome input messages. For short text it simulates real key presses, while for long text it uses a faster direct text insertion path.

**Data flow**: It receives the current KeyboardState, the text to enter, and whether Mac behavior should be used. If the text is longer than the configured limit, it returns one Input.insertText call containing the whole string. Otherwise, it walks through each character. Characters known in the keyboard layout become key_down followed by key_up; characters outside the layout are inserted directly. It returns the list of Chrome DevTools calls.

**Call relations**: Higher-level typing actions call this to enter text into the browser. It uses key_down and key_up when per-character keyboard behavior matters, but bypasses them for long or unusual text where direct insertion is better.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Action settling
Waits for page work triggered by browser actions to quiet down while ignoring unrelated background activity.

### `extensions/browser/ufo_ext_browser/bua/settle.py`

`domain_logic` · `after each browser action or navigation`

When an automated browser clicks a button, submits a form, or navigates to a page, the next step should not run too early. But waiting for a web page to become completely quiet can also be wrong, because many modern pages keep loading ads, tracking beacons, or live updates forever. This file solves that timing problem.

The main idea is “wait for the consequences of this action, not for the whole internet to go silent.” The Settle class keeps a small checklist: important network requests that started, pages that are still loading, and pages that have painted visible content. A “paint” means Chrome has drawn useful content on screen, which is often a better sign of readiness than total network silence.

The helper tracks_request filters out requests that are unlikely to matter to the user action, such as images, fonts, low-priority prefetches, and common analytics hosts. Unknown request shapes are kept rather than ignored, which is safer.

Settle.wait first gives the page one tiny task-queue turn so click handlers and immediate timers can start their work. Then it waits until either the page paints and any short follow-up work drains, or the tracked loading and request activity becomes quiet. Every wait has a time cap, so pages that never stop working do not freeze the automation.

#### Function details

##### `tracks_request`  (lines 42–54)

```
def tracks_request(params: JsonDict) -> bool
```

**Purpose**: Decides whether a browser network request is important enough to wait for. It keeps foreground work that may affect the result of the action, and ignores likely background noise such as images, fonts, analytics beacons, and very low-priority requests.

**Data flow**: It receives a dictionary of Chrome request details. It reads the request type, priority, and URL host, then compares them with lists of passive resource types, low priorities, and known analytics domains. It returns true if the request should be tracked, or false if it is safe to ignore.

**Call relations**: When Chrome reports that a request has started, Settle.on_request_started asks this function whether that request belongs on the waiting checklist. This keeps Settle focused on action-related work instead of unrelated page chatter.

*Call graph*: called by 1 (on_request_started); 2 external calls (get, urlparse).


##### `Settle.__init__`  (lines 69–73)

```
def __init__(self) -> None
```

**Purpose**: Creates a fresh settling tracker. It starts with no pending requests, no counted requests, no loading sessions, and no painted sessions.

**Data flow**: It takes no outside data beyond the new object being created. It prepares empty sets for pending requests, loading pages, and painted pages, plus a counter for how many tracked requests have started. The result is a Settle object ready to observe browser events.

**Call relations**: BrowserSession creates this object when a browser session starts, and also creates a fresh one during close cleanup. Other methods in this file then fill and clear these sets as browser events arrive.

*Call graph*: called by 2 (__init__, close).


##### `Settle.reset`  (lines 75–78)

```
def reset(self) -> None
```

**Purpose**: Clears the current action’s settling state so the next action starts with a clean slate. This prevents old requests or paint events from affecting the wait for a later click or navigation.

**Data flow**: It reads the current internal sets and counter, then empties the pending request list, resets the started-request count to zero, and clears recorded paint events. It leaves the loading set intact, so ongoing page load state is not falsely forgotten.

**Call relations**: This is used when the surrounding browser session wants to begin measuring a new action separately. It supports the file’s main promise: wait only for work caused by the current action.


##### `Settle.on_request_started`  (lines 80–84)

```
def on_request_started(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Records a newly started browser request if it looks relevant to the user action. This gives the settling logic something concrete to wait for.

**Data flow**: It receives Chrome’s request details and the session id for the page or frame that made the request. It extracts the request id, asks tracks_request whether the request matters, and if so stores the pair of session id and request id in the pending set. It also increases the count of tracked requests that have started.

**Call relations**: This method is called by browser event wiring when Chrome announces a request. It relies on tracks_request to filter noise, and its stored pending requests are later checked by Settle.wait and Settle._drain_after_paint.

*Call graph*: calls 1 internal fn (tracks_request); 1 external calls (get).


##### `Settle.on_request_finished`  (lines 86–89)

```
def on_request_finished(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Removes a browser request from the pending checklist once Chrome says it has finished or failed. This is how the waiter knows that one piece of action-related network work is done.

**Data flow**: It receives Chrome’s request details and the session id. It extracts the request id and, if both identifiers are valid, removes that request from the pending set. Nothing is returned; the internal state becomes quieter.

**Call relations**: Browser event wiring calls this after request completion events. Settle.wait watches the pending set shrink, and can move on once no relevant requests remain for long enough.

*Call graph*: 1 external calls (get).


##### `Settle.mark_loading`  (lines 91–92)

```
def mark_loading(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as currently loading a document. This tells the waiter that a navigation or page load is still in progress.

**Data flow**: It receives a session id and adds it to the internal loading set. It returns nothing, but future wait checks will see that this session is not ready yet.

**Call relations**: The browser session’s event layer uses this when Chrome reports loading activity. Settle.wait and Settle._drain_after_paint consult this loading set before deciding the page is settled.


##### `Settle.mark_loaded`  (lines 94–95)

```
def mark_loaded(self, session_id: str) -> None
```

**Purpose**: Marks a browser session as no longer loading. This removes one reason to keep waiting.

**Data flow**: It receives a session id and removes it from the internal loading set if present. It returns nothing; the page’s recorded state changes from loading to not loading.

**Call relations**: The browser event layer calls this when loading completes. Settle.wait uses the updated loading state together with pending requests and paint events to decide whether the browser action is finished enough.


##### `Settle.mark_painted`  (lines 97–98)

```
def mark_painted(self, session_id: str) -> None
```

**Purpose**: Records that a browser session has painted visible content. This is an important readiness signal because many pages become usable before their background network traffic stops.

**Data flow**: It receives a session id and adds it to the painted set. It returns nothing, but later wait logic can choose the faster post-paint path.

**Call relations**: Chrome lifecycle event handling calls this when a paint event is seen. Settle.wait notices the paint mark and hands off to Settle._drain_after_paint for a short grace period instead of waiting for the full timeout.


##### `Settle.wait`  (lines 100–115)

```
async def wait(self, cdp: Cdp, session_id: str, cap: float) -> None
```

**Purpose**: Waits until the current browser action appears complete enough to continue, without waiting forever. It balances three signs of readiness: immediate page tasks have run, visible content has painted, and relevant network work has quieted.

**Data flow**: It receives a Chrome DevTools connection object, a session id, and a maximum number of seconds to wait. First it asks the page to run one tiny JavaScript delay so click handlers and immediate follow-up tasks can start. Then it watches the internal painted, loading, and pending-request state until the page is ready or the deadline arrives. It returns nothing; its result is the passage of time until the page is safe enough for the next step.

**Call relations**: This is the main method callers use after an action. It calls Settle._flush_page_tasks at the start, then may call Settle._drain_after_paint if a paint event has happened. It depends on earlier event methods having recorded requests, loading state, and paint state.

*Call graph*: calls 2 internal fn (_drain_after_paint, _flush_page_tasks); 2 external calls (sleep, monotonic).


##### `Settle._drain_after_paint`  (lines 117–129)

```
async def _drain_after_paint(self, session_id: str, deadline: float) -> None
```

**Purpose**: After the page has painted visible content, this gives important follow-up work a short chance to finish. It avoids calling a page ready too early when content is still arriving, but also avoids waiting the full cap on pages that never go quiet.

**Data flow**: It receives a session id and an absolute deadline time. It creates a shorter grace deadline, then repeatedly checks whether that session is still loading or has pending tracked requests. If things stay quiet through a small gap, it returns early; otherwise it returns when the grace time runs out.

**Call relations**: Settle.wait calls this whenever it sees that the page has painted. This helper is the post-paint branch of the settling strategy: visible page first, then a brief wait for foreground network work.

*Call graph*: called by 1 (wait); 2 external calls (sleep, monotonic).


##### `Settle._flush_page_tasks`  (lines 131–142)

```
async def _flush_page_tasks(self, cdp: Cdp, session_id: str) -> None
```

**Purpose**: Gives the web page one quick turn to run immediate JavaScript work caused by the action. This helps ensure that requests started by click handlers or zero-delay timers are seen before settling decisions are made.

**Data flow**: It receives the Chrome DevTools connection and a session id. It sends a small JavaScript expression that waits for a zero-delay timer, and asks Chrome to wait for that promise. If Chrome reports an error or timeout, it simply sleeps for a short beat instead. It returns nothing, but it gives browser events time to arrive.

**Call relations**: Settle.wait calls this before checking readiness. It hands the tiny JavaScript evaluation to Cdp.send, and it provides a fallback pause if that round trip fails, so the rest of the waiting logic still has a fair starting point.

*Call graph*: calls 1 internal fn (send); called by 1 (wait); 1 external calls (sleep).

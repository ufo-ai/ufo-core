# Browser Page Reading, Targeting, and Input Execution  `stage-10.3.3`

This stage is the browser automation “eyes and hands” used during the main work loop. It reads what is on a web page, identifies the right target, and turns model instructions into safe browser actions. page.py and content.py make a live page understandable by converting it into readable text or an accessibility tree, which is a structured outline of controls like buttons, links, and fields. find.py helps search that outline and checks that chosen element labels really exist before anything is clicked or typed.

Once a target is known, coordinate.py translates between screenshot positions seen by the AI model and real browser pixels, so actions land in the right place. computer.py performs the actual input, such as clicks, typing, and scrolling, then returns a fresh screenshot and safety notes. keys.py builds accurate keyboard events, including shortcuts and special characters. forms.py handles form filling and file uploads through Chrome’s debugging connection. fixup.py acts like a proofreader, cleaning up small mistakes in model-issued actions before they reach the browser.

## Files in this stage

### Browser action entrypoint
This group introduces the main executor that turns model-issued browser actions into real input events and returns post-action context.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the bridge between an outside agent that thinks in simple browser actions and a live browser tab that only understands low-level commands. Without it, the system could describe what it wants to do, but it could not reliably press keys, move the mouse, scroll, wait for the page to settle, or report what changed afterward.

The main class, BrowserComputer, receives a batch of requested actions. It validates them, adjusts coordinates between the agent’s screen size and the real browser viewport, then performs each action in order. After each batch it waits for the page to calm down, much like pausing after pushing an elevator button so the doors can open before doing the next thing.

It also protects the user and the agent from common browser pitfalls. It warns if the agent keeps scrolling when page-reading tools would be better, reminds it not to sign in without user approval, notices new downloads, and explains why clicking native dropdown options may not work. Finally, it captures a JPEG screenshot, optionally marks the last click with a small blue dot, and returns tab information plus a plain-text action summary.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This protocol method promises that a browser session can provide the tab the action should run in. A real session class implements it so BrowserComputer can work without knowing the session’s exact class.

**Data flow**: It receives an optional tab identifier → the session finds or chooses the matching browser tab → it returns a tab object with a session id and keyboard state.

**Call relations**: BrowserComputer.run calls this at the start of an action request, so every later mouse, keyboard, and screenshot command knows which tab to target.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This protocol method promises access to the browser command connection. That connection is used to send Chrome DevTools Protocol commands, which are low-level messages understood by Chromium-based browsers.

**Data flow**: It takes no new data beyond the session itself → it retrieves the active browser command channel → it returns an object that can send browser commands.

**Call relations**: BrowserComputer.run, BrowserComputer.act, and helper methods use this connection whenever they need to dispatch input, scroll a node into view, wait for stability, or capture a screenshot.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This protocol method promises a summary of the current tab, such as information the caller needs after an action completes.

**Data flow**: It receives a tab object → the session gathers current details about that tab → it returns those details as a JSON-style dictionary.

**Call relations**: BrowserComputer.run calls this near the end, then combines the tab details with the action output, click location, and screenshot.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This protocol method promises a list of open tab titles. BrowserComputer uses those titles to spot sign-in or account-creation pages and warn the agent to ask the user first.

**Data flow**: It reads the browser session’s current tabs → extracts their titles → returns a list of title strings.

**Call relations**: BrowserComputer.run asks for titles after actions finish, then passes them to sign_in_warning to decide whether to add a safety reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This protocol method promises a way to run a small JavaScript function on a specific browser object. It is used here to inspect special page elements, such as a dropdown menu.

**Data flow**: It receives a session id, a browser object id, JavaScript function text, and optional arguments → the session asks the browser to run that function on that object → it returns the function’s JSON-style result.

**Call relations**: BrowserComputer._select_reminder uses this after finding a clicked select element, so it can learn what options are inside and build a useful reminder.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This protocol method promises to turn a page reference string into the browser node it points to. A reference is a stable label returned by page-reading tools, like a tag on a shelf.

**Data flow**: It receives a tab and a reference string → the session looks up the matching browser-side node and backend node id → it returns both pieces so browser commands can target the real element.

**Call relations**: BrowserComputer.act uses this for scroll_to actions, where the agent wants a referenced element brought into view instead of clicking raw coordinates.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This protocol method promises to find a clickable screen point for a referenced page element. It lets actions target meaningful page references instead of fragile coordinates.

**Data flow**: It receives a tab and a reference string → the session locates the element and computes a point in the browser viewport → it returns that x and y position.

**Call relations**: BrowserComputer.point calls this when an action includes a ref, and BrowserComputer.act then uses the returned point for clicks, drags, or scrolls.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for executing a batch of browser actions. It validates the request, performs each action, waits for the page to settle, captures the result, and returns a screenshot plus messages.

**Data flow**: It receives a JSON-style request with a tab id and actions → chooses the tab, validates and adjusts the actions, runs them in batches, records messages and the last click, drains new download notices, checks warnings, captures a screenshot, and marks the last click if there was one → it returns tab info, output text, last click coordinates in model space, and a base64 screenshot.

**Call relations**: This method is the coordinator for the file. It calls int_or_none to read the tab id, fixup and split helpers to prepare actions, BrowserComputer.act for each action, BrowserComputer._select_reminder after clicks, sign_in_warning for safety, BrowserComputer._to_model for reporting, and mark_click in a background thread to annotate the screenshot.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This function performs one requested browser action, such as clicking, typing, pressing a key, waiting, scrolling, or taking a screenshot. It turns one high-level instruction into the lower-level browser commands needed to make it happen.

**Data flow**: It receives the current tab and one validated action → finds any needed target point, chooses behavior based on the action type, sends mouse or keyboard commands, waits if requested, or scrolls an element into view → it returns a short human-readable message and, for pointer actions, the final viewport point.

**Call relations**: BrowserComputer.run calls this for every action in the request. Depending on the action, it hands work to BrowserComputer.point, _click, _drag, _scroll, _dispatch, coordinate conversion helpers, and validation helpers such as require_point and require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This function decides where an action should happen on the screen. It accepts either a page reference or a coordinate and converts that into a browser viewport point.

**Data flow**: It receives a tab and an action → if the action has a ref, it asks the browser session for that element’s point; if it has model coordinates, it converts them to viewport coordinates; if it has neither, it returns no point → the result is either an x-y point or None.

**Call relations**: BrowserComputer.act calls this before actions that may need a location. It uses BrowserComputer._to_viewport when coordinates come from the model’s coordinate system.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts coordinates from the agent’s model-sized screen into the real browser viewport. This matters because the agent and browser may use different screen sizes.

**Data flow**: It receives an x-y coordinate in model space → wraps it as a coordinate object and scales it using the configured viewport and model sizes → it returns the matching x-y coordinate in viewport space.

**Call relations**: BrowserComputer.point uses it for coordinate-based actions, and BrowserComputer.act uses it when converting drag start points or default scroll positions.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts browser viewport coordinates back into the agent’s model-sized coordinate system. It is mainly used for clear reporting to the caller.

**Data flow**: It receives an x-y coordinate from the real viewport → scales it back to the model coordinate system → it returns the model-space x-y coordinate.

**Call relations**: BrowserComputer.act uses it to write click and drag messages in the coordinates the caller understands. BrowserComputer.run uses it to report the last click in the final response.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This function checks whether a click landed on a native HTML select dropdown and, if so, creates a reminder about the safer way to choose an option. Native dropdowns often cannot be controlled by simply clicking their opened options through this browser interface.

**Data flow**: It receives a tab and the clicked viewport point → asks the browser what element is at that point, walks up to a select element if there is one, reads up to the first ten option labels and the total count, and tries to get a page reference → it returns a reminder string or None if no useful dropdown was found.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses the browser connection, BrowserComputerSession.call_on, response parsing helpers, and select_reminder to turn browser details into plain guidance.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This helper sends a sequence of keyboard-related browser commands. It is used after other code has already translated text or key combinations into concrete browser messages.

**Data flow**: It receives a tab and a list of command-method plus parameter pairs → sends each command to the browser connection for that tab → it returns nothing, but the page receives the typed text or key presses.

**Call relations**: BrowserComputer.act calls this for type and key actions after type_text or press_combo has prepared the needed commands.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This helper sends one mouse event to the browser, such as move, press, release, or wheel. It keeps the actual browser command name in one place.

**Data flow**: It receives a tab and a dictionary of mouse event details → sends an Input.dispatchMouseEvent command to the browser for that tab → it returns nothing, but the browser receives the mouse event.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll all call this to perform their low-level mouse steps.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This function performs a left, right, double, or triple click at a specific viewport point. It sends the same kind of move, press, and release events a real mouse would create.

**Data flow**: It receives a tab, x-y viewport coordinates, a button name, and a click count → reads currently pressed keyboard modifiers, moves the mouse to the point, then sends the requested number of press-and-release pairs → it returns nothing, but the page receives the click.

**Call relations**: BrowserComputer.act calls this for click actions. It relies on modifiers_mask to preserve keys like Shift or Ctrl during the click, and uses BrowserComputer._mouse_event for each actual browser event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This function performs a left-button drag from one point to another. It moves in several small steps because many web pages only recognize drag-and-drop when they see intermediate movement.

**Data flow**: It receives a tab and start and end viewport coordinates → reads active keyboard modifiers, moves to the start, presses the left mouse button, moves gradually toward the end, then releases → it returns nothing, but the page experiences a drag gesture.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It uses BrowserComputer._mouse_event for the move, press, stepped movement, and release events.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This function scrolls at a particular point in the viewport. It simulates putting the mouse over an area and using the mouse wheel.

**Data flow**: It receives a tab, x-y viewport coordinates, and horizontal and vertical scroll amounts → reads active keyboard modifiers, moves the mouse to that point, then sends a mouse wheel event with the requested deltas → it returns nothing, but the page scrolls.

**Call relations**: BrowserComputer.act calls this for scroll actions after it calculates the scroll direction and amount. It sends its low-level events through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This function checks tab titles for words that suggest a sign-in, login, or registration page. It helps enforce the rule that the agent should not sign in or create accounts without asking the user.

**Data flow**: It receives a list of tab titles → lowercases them and searches for sign-in related phrases → it returns the warning text if any title matches, otherwise None.

**Call relations**: BrowserComputer.run calls this after actions complete and adds its result to the system reminders in the final output.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This function writes a clear instruction for what to do after clicking a native select dropdown. It tells the caller to use form_input instead of trying to click dropdown options.

**Data flow**: It receives an optional element reference, a list of option labels, and the total number of options → formats the visible options and notes if more exist → it returns one reminder string with the recommended next step.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked dropdown and gathered its options.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This function draws a small blue dot over the last clicked point in a screenshot. It makes the returned screenshot easier to understand, like marking a map with a pin.

**Data flow**: It receives a base64-encoded screenshot and a viewport point → decodes the image, draws a translucent circle at that point, re-saves it as a JPEG, and base64-encodes it again → it returns the modified screenshot string.

**Call relations**: BrowserComputer.run uses this when there was a click or drag ending point. Because image processing can block, run calls it through the event loop’s executor rather than doing the work directly inside the async flow.

*Call graph*: 7 external calls (Draw, b64decode, b64encode, alpha_composite, new, open, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This small helper reads a tab id that may arrive as an integer, float, string, or missing value. It normalizes usable values into an integer and treats anything else as absent.

**Data flow**: It receives a JSON-style value → if it is an int, float, or non-empty string, it converts it to an int; otherwise it returns None → the caller gets either a tab id or no tab id.

**Call relations**: BrowserComputer.run uses this before asking the browser session for the target page.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that an action needing a screen point actually has one. It turns a missing point into a clear validation error instead of letting the action fail later in a confusing way.

**Data flow**: It receives an optional point and the action name → if the point exists, it passes it through unchanged; if not, it raises a validation error explaining that a coordinate or reference is required → successful callers get a definite x-y point.

**Call relations**: BrowserComputer.act calls this before click and drag-end operations that cannot work without a target location.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that a required coordinate field is present. It is used when an action has one point from normal targeting but also needs a second explicit coordinate, such as a drag start.

**Data flow**: It receives an optional coordinate and the field name → if the coordinate exists, it returns it; if not, it raises a validation error naming the missing field → successful callers get a definite coordinate.

**Call relations**: BrowserComputer.act calls this for drag actions to make sure the start_coordinate was provided before converting it and sending mouse events.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### Readable targets and coordinates
This group covers the tools that expose page content, validate element references, and translate model-visible positions into browser pixels.

### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

A browser page can be large, messy, and hard for an assistant or tool to inspect directly. This file acts like a librarian for the current tab: it knows how to fetch a simplified view of the page, trim it to reasonable limits, and search it for useful elements.

The main class, BrowserContent, does not talk to the browser directly. Instead, it depends on a BrowserContentSession, which is a promised interface for getting a page, a page reader, and basic tab details. The page reader can provide either an accessibility-style tree, meaning a structured list of page elements, or markdown text, meaning a plain text version of the page.

There are three main user-facing actions. read_page returns a trimmed page tree, optionally limited to all elements, interactive elements, or visible viewport elements. get_page_text returns trimmed markdown plus tab information. find searches the tree for a query, either with a built-in parser or, if supplied, a FindCompleter, which is an external completion function that can interpret the page tree more flexibly.

The file also protects the rest of the system from runaway output by cutting long trees and text at fixed character limits. Without this file, callers would each need to know how to pick tabs, read pages, search trees, and prevent oversized responses.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a contract for something that can turn a browser tab into a structured page tree. The tree is useful when the system needs to reason about buttons, links, fields, and other page elements rather than just plain text.

**Data flow**: It receives a browser tab, a filter type such as all or interactive, and optionally a reference to a specific element. An implementation reads the page and returns a string tree, or returns nothing if the requested referenced element cannot be found.

**Call relations**: BrowserContent.tree relies on this promised method after it has chosen the right tab and cleaned up the requested reference. The actual browser-specific reader lives elsewhere and plugs into this interface.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a contract for something that can turn a browser tab into plain markdown-style text. It is used when the caller wants the readable page content instead of a detailed element map.

**Data flow**: It receives a browser tab. An implementation reads the visible or available document content and returns it as a text string.

**Call relations**: BrowserContent.get_page_text calls this method after selecting the tab. The returned text is then shortened if needed and combined with tab information.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is a contract for getting the browser tab that content should be read from. It lets callers request a specific tab by ID or fall back to the current/default tab.

**Data flow**: It receives an optional tab ID. An implementation uses that ID, or its own default selection rules, and returns a PageTab object representing the chosen browser tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before reading anything from a page. This keeps tab selection in the browser session layer rather than duplicating it in content-reading code.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is a contract for getting the object that knows how to read page content. It separates choosing a browser tab from extracting useful text or tree data from that tab.

**Data flow**: It takes no extra input beyond the session object. It returns a page reader that can produce a page tree or markdown text.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this reader after they have selected a tab. The session supplies the browser-specific implementation, while BrowserContent decides which kind of reading is needed.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is a contract for collecting basic information about a tab, such as details the caller may need alongside extracted text. It makes text responses more useful by including page context.

**Data flow**: It receives a tab object. An implementation reads tab metadata and returns it as a JSON-style dictionary.

**Call relations**: BrowserContent.get_page_text calls this after reading markdown from the tab. The tab information is merged into the final response sent back to the caller.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This returns a structured tree view of a browser page or of a referenced part of the page. It is the shared helper used by both page-reading and search features.

**Data flow**: It receives request arguments and an optional filter type. It pulls out tab_id and ref_id, converts the tab ID into an integer when possible, asks the browser session for that tab, asks the page reader for a tree, and returns either the tree text or a clear 'No element found' message.

**Call relations**: BrowserContent.read_page calls this when it needs a tree to return to the user. BrowserContent.find calls it when it needs a complete page tree to search. Internally it uses _tab_id to normalize the tab ID before asking the browser session for a page.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This prepares a safe page-tree response for callers that want to inspect the browser page. It also enforces a maximum size so huge pages do not flood the system.

**Data flow**: It receives request arguments, reads the optional filter setting, accepts only known filter values, and falls back to all elements for anything unexpected. It asks BrowserContent.tree for the page tree, cuts the result to the maximum allowed length, and returns both the cut tree and a flag saying whether anything was trimmed.

**Call relations**: This is a higher-level action built on BrowserContent.tree. It does the caller-facing cleanup: validating the filter choice and reporting whether the output was truncated.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the page as plain readable text, plus information about the tab it came from. It is useful when the caller cares about article-like content rather than individual page controls.

**Data flow**: It receives request arguments, converts any tab_id into the form the browser session expects, gets the chosen tab, and asks the page reader for markdown text. It trims the text to the maximum allowed length, marks whether it was truncated, adds tab metadata, and returns the combined dictionary.

**Call relations**: This function works alongside read_page but uses the page reader's markdown path instead of the tree path. It uses _tab_id for tab selection and asks the browser session for tab_info before returning the final result.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the current page tree for elements matching a user query. It can use either a simple built-in matcher or an optional completion helper that interprets the page tree more intelligently.

**Data flow**: It receives request arguments and possibly a FindCompleter. It extracts the query as a required string, gets the full page tree, and then searches it. Without a completer, it parses matches directly from the tree; with a completer, it sends the query and a shortened tree to that helper and resolves the reply back into matches. It returns the matches plus a human-readable summary.

**Call relations**: This function calls BrowserContent.tree first because searching depends on having a page tree. It then hands work to parse_tree_matches for local matching, or to the supplied completer plus resolve_find_reply for assisted matching, and finally uses format_matches to describe the result.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab ID from JSON-style input into an integer tab ID when possible. It lets callers pass the tab ID as a number or a non-empty string.

**Data flow**: It receives a value that may be missing, an integer, a float, a string, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and missing or unsupported values become None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this before asking the browser session for a page. That way the rest of the content code can pass a clean optional integer instead of worrying about the many shapes user input may take.

*Call graph*: called by 2 (get_page_text, tree).


### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `request handling`

When an AI model looks at a browser screenshot and says “click at x=500, y=300,” those numbers may not match the browser’s actual screen pixels. Some models receive a resized screenshot, and some report locations on their own fixed grid. This file is the small conversion guide that keeps those worlds aligned.

It defines two simple data shapes: Size for width and height, and Coord for x and y positions. The key idea is that the browser has a real viewport size, while the model may see a smaller or differently scaled version of it. For Claude-style vision models, large screenshots are reduced so they stay within Anthropic’s image limits: no side longer than 1568 pixels and no more than about 1.15 million pixels total. For Gemini, coordinates are treated as a fixed 0-to-1000 grid, no matter the screenshot size.

The helper functions first decide what size the model is reasoning in, then scale points forward or backward. This is like reading a map: if the map is half the size of the real room, every point on the map must be doubled before you walk to it. Without this file, automated clicks, drags, and position checks could be consistently offset, especially on large browser windows or with different model providers.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: This function works out the largest screenshot size that can be sent to a Claude-style vision model without the server shrinking it again. It keeps the screenshot proportional, so the image does not get stretched or squashed.

**Data flow**: It starts with the browser viewport size. It first scales the image down if either side is longer than the allowed maximum, then checks whether the total number of pixels is still too high. If needed, it shrinks both width and height again by the same ratio. It returns a new Size containing the final screenshot dimensions.

**Call relations**: This is the fallback sizing rule used by effective_model_size when no model-specific size has been supplied. In the larger flow, it gives later coordinate conversion functions the image size that the model actually sees.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: This function decides which coordinate space the model is using. If the caller gives an explicit model size, it trusts that; otherwise it calculates the screenshot size from the browser viewport.

**Data flow**: It receives the real browser viewport size and, optionally, a model-space size. If the optional size is present, it returns it unchanged. If not, it asks compute_screenshot_dimensions to produce the model-facing screenshot size, then returns that.

**Call relations**: Both model_to_viewport and viewport_to_model rely on this function before doing any scaling. It acts as the shared checkpoint that makes sure both conversion directions use the same idea of the model’s coordinate system.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a point reported by the AI model into real browser viewport pixels. It is what makes a model’s suggested click location usable for actual browser input.

**Data flow**: It takes a coordinate from the model, the browser viewport size, and optionally the model’s coordinate size. It first finds the effective model size, then scales the x value by the ratio between viewport width and model width, and scales the y value by the ratio between viewport height and model height. It returns a Coord in browser pixel space.

**Call relations**: After a model chooses a point on the image it saw, this function is the bridge to browser automation. It calls effective_model_size so it can handle either normal screenshot-sized coordinates or a special coordinate system such as Gemini’s fixed grid.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: This function identifies whether a model uses a special coordinate grid. In particular, it marks Gemini models as using a 1000-by-1000 coordinate space instead of screenshot pixels.

**Data flow**: It receives a model name, or no name. If the name exists and contains “gemini” ignoring letter case, it returns a Size of 1000 by 1000. For all other names, it returns None, meaning the normal screenshot-size coordinate system should be used.

**Call relations**: This function is meant to be used before coordinate conversion when the caller knows the model name. Its result can be passed into model_to_viewport or viewport_to_model as the explicit model size, so those functions scale points correctly for Gemini versus Claude-style models.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a real browser pixel position back into the coordinate system the AI model uses. It is useful when the system needs to describe or compare browser positions in the same terms as the model.

**Data flow**: It takes a browser viewport coordinate, the viewport size, and optionally the model’s coordinate size. It finds the effective model size, then scales the x value from browser width into model width and the y value from browser height into model height. It returns a Coord in model-space units.

**Call relations**: This is the reverse path of model_to_viewport. It calls effective_model_size for the same reason: both directions must agree on whether the model is using resized screenshot pixels or a fixed grid.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `during browser element search and result formatting`

When an automation system wants to click or inspect something on a web page, it needs a safe way to identify the right page element. This file works with a plain-text accessibility tree, where each line describes one element, such as its role, visible name, internal reference, and screen position. Think of it like reading a building directory: each line says what the room is, what it is called, and its room number.

The file does three main jobs. First, it reads tree lines and turns them into small structured records with a reference, role, name, coordinates, and searchable text. Second, it can do a simple local search: it breaks a user query into words and keeps tree entries whose line contains all those words. Third, it can clean up and verify a reply from a language model. The model may suggest references that match a query, but this file “grounds” those suggestions by checking them against the real tree. If a suggested reference is missing, duplicated, or malformed, it is ignored.

This matters because browser automation should not trust guessed element IDs. The file makes sure downstream code receives only real, current element references, with role, name, and position copied from the source tree rather than from a possibly mistaken reply.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: Turns the text accessibility tree into a list of structured element records. It extracts the useful facts from each valid tree line: the element reference, role, name, and coordinates.

**Data flow**: It receives the whole tree as one string. It reads it line by line, skips lines that do not look like element lines or do not contain a reference, pulls out the role, optional name, reference, and optional x/y position, then returns a list of dictionaries. Each dictionary also keeps a lowercase copy of the original line so later searches can compare text easily.

**Call relations**: This is the shared reader for the rest of the file. parse_tree_matches uses it before doing a simple word search over the tree, and resolve_find_reply uses it to build the trusted set of references that a model reply is allowed to mention.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: Builds the standard result shape for one found element. It keeps only the fields that search results need and adds the explanation for why the element matched.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the entry’s reference, role, name, and coordinates, attaches the reason, and returns a new dictionary for the final match list. It does not change the original entry.

**Call relations**: This is a small helper used whenever the file turns a tree entry into a user-facing match. parse_tree_matches uses it for local text matches, and resolve_find_reply uses it after verifying that a replied reference really exists in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: Performs a simple built-in search over the accessibility tree without needing a language model. It finds elements whose tree line contains all meaningful words from the user’s query.

**Data flow**: It receives the tree text and a query string. It lowercases the query, pulls out letter-and-number terms longer than one character, parses the tree into entries, and checks whether every query term appears in each entry’s lowercase line. Matching entries are converted into standard match dictionaries, up to the maximum allowed number of results, and returned as a list.

**Call relations**: This function calls tree_entries to get searchable records and _match_payload to package each result. It is the direct local-search path: given a query and a tree, it produces matches without involving the reply-verification flow.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: Checks and cleans a search reply, usually from a language model, against the real accessibility tree. It prevents invented or stale element references from being passed onward.

**Data flow**: It receives a reply string and the original tree text. It first parses the tree and builds a lookup table from real references to real entries. Then it reads the reply line by line, ignoring blank lines, stopping on NO_MATCHES, noticing a MORE marker, and extracting references from result lines. A reference is kept only if it exists in the tree and has not already been used. The function returns the verified match list plus a true-or-false flag saying whether the reply claimed more matches exist.

**Call relations**: This function calls tree_entries to know what references are valid and _match_payload to create trusted result records. It sits between an outside search reply and the rest of the browser automation flow, acting like a ticket checker: only references printed in the current tree are allowed through.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: Turns a list of match records into readable text for a person or another system component. It presents each element with its reference, role, name, coordinates, and optional reason.

**Data flow**: It receives a list of match dictionaries and an optional flag saying whether more results exist. It builds one display line per match, adds the reason when present, and appends a note asking the user to refine the query if there are more matches. It returns the finished multi-line string.

**Call relations**: No caller is shown in the provided graph, but this is the final presentation step for results produced by parse_tree_matches or resolve_find_reply. It does not search or verify anything itself; it only turns already-prepared matches into a compact readable form.


### Instruction cleanup and input primitives
This group handles repair of browser action instructions and the lower-level form and keyboard operations used to carry them out safely.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `action preprocessing before browser dispatch`

This file is a safety net between generated browser actions and the real browser. A model may ask to type, scroll, wait, or click, but its instructions are not always complete enough to run safely. Without this file, typing might go nowhere because no input box was focused, scrolling might fail because it has no starting point, and waits might have no length.

The main function, `fixup_actions`, walks through a list of intended actions and repairs predictable problems. If a typing action points at a place or page element but there was no click right before it, the code adds a left click first, like tapping a form field before typing into it. If text contains written escape sequences such as "\\n", it turns them into the real character, such as an actual newline. It also gives scroll actions a default anchor point in the middle of the model’s screen, turns incomplete `scroll_to` actions into normal scrolls, gives empty waits a default duration, and simplifies multi-clicks that target a referenced element.

The second public helper, `split_at_waits`, divides a long action list into smaller batches whenever a wait appears. That lets the browser pause and settle before the next batch continues.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: Repairs a batch of browser actions so they are more likely to work when sent to the browser. It fills in missing details and fixes common model output mistakes before anything is dispatched.

**Data flow**: It receives a list of `ComputerAction` objects, the current browser viewport size, and optionally the model’s own screen size. It first decides the effective model size and finds the center point. Then it reads each action in order and may copy it unchanged, replace it, or insert an extra helper action before it. The result is a new list of safer, more complete actions; the original list is not directly edited.

**Call relations**: This is the main repair pass in the file. When it sees typing that may need focus, it asks `_focus_click` to create the click. When it sees text that may contain written escape sequences, it asks `_unescape_text` to clean the text. It also creates replacement `ComputerAction` and `ScrollParameters` objects itself, and uses `effective_model_size` to choose sensible coordinates for fallback scrolling.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: Breaks one long action list into smaller groups, ending a group whenever a wait action appears. This makes waits act like natural pauses between batches of browser work.

**Data flow**: It receives a list of actions. It builds a current batch one action at a time. When it reaches an action whose type is `wait`, it closes that batch and starts a fresh one. It returns a list of batches, where each inner list is a chunk of actions to run together.

**Call relations**: This function is separate from the repair logic. After actions have been prepared, another part of the system can use this helper to run actions up to a wait, let the browser settle, and then continue with the next batch.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: Creates a simple left-click action that focuses the place where a later typing action wants to type. It is used when typing names a target but no previous click focused that target.

**Data flow**: It receives a typing-related `ComputerAction`. If that action has screen coordinates, it creates a new left-click at those coordinates. If not, it creates a left-click aimed at the referenced page element. The output is the new click action.

**Call relations**: `fixup_actions` calls this helper when it detects a type action that needs focus first. The helper hands back a plain left click, which `fixup_actions` inserts immediately before the typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: Turns written escape sequences in typed text into the real characters they mean. For example, it changes the two visible characters `\n` into an actual newline.

**Data flow**: It receives a `ComputerAction` and reads its text field. If the text does not contain any known escaped literals, it returns the same action. If it does, it replaces those literals with their real characters and returns a copied action with the cleaned text.

**Call relations**: `fixup_actions` calls this helper for type actions before adding them to the repaired output. The helper uses the action’s copy method so the cleaned version can be returned without manually rebuilding every field.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not accept form input just because a program changes a Python value somewhere. The automation code has to find the real page element, talk to the browser, change the element, and fire the same kind of events a user action would normally trigger. This file is the small bridge that does that for form fields.

The central piece is BrowserForms. It receives a browser session object, finds the requested tab, turns a page “ref” into the browser’s internal node identity, and then asks the browser to act on that node. For normal fields, it runs a short JavaScript function in the page. That script knows the common cases: checkboxes and radio buttons get checked or unchecked, select boxes get a selected value, editable text areas get text, and other inputs get a value. It then sends input and change events so the website notices, much like ringing the doorbell after dropping off a package.

For file uploads, it uses the browser’s DevTools protocol, which is Chrome’s remote-control API, to set the actual files on a file input. It also has a helper to check the byte sizes of files currently attached, because with remote uploads a filename may appear before the file data is really available. If a requested page reference is stale or points to the wrong kind of element, the file raises a HallucinationError with guidance to re-read the page and use a real ref.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser session interface. It provides the page or tab that the form action should work on.

**Data flow**: It receives an optional tab id, meaning “use this browser tab if given.” It returns the page object for that tab, or the current/default page when no tab id is supplied.

**Call relations**: BrowserForms methods call this first so every form action starts from the correct browser tab. The actual implementation lives in whatever browser session object is passed in.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser session interface. It gives access to the Chrome DevTools Protocol connection, which is the low-level remote-control channel to the browser.

**Data flow**: It takes no input besides the session object. It returns a Cdp connection object that can send commands such as resolving a page node or setting files on an upload input.

**Call relations**: BrowserForms uses this when it needs direct browser commands, especially to resolve page references and to attach files. The protocol declaration here says what BrowserForms needs from its browser partner.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser session interface. It runs a JavaScript function on a specific object inside the web page and returns the result.

**Data flow**: It receives a browser session id, a page object id, a JavaScript function as text, and optional arguments. It runs that function against the chosen page element and returns a JSON-like dictionary with the function’s answer.

**Call relations**: BrowserForms uses this after it has resolved a page ref into a real browser object. It is the step that lets the Python code make a page element report file sizes or accept a form value.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser session interface. It converts a page-level reference string into the browser’s internal identity for that element.

**Data flow**: It receives a page object and a ref string that came from reading the page. It returns a browser node object plus a backend node id, which are the identifiers needed for lower-level browser commands.

**Call relations**: Every BrowserForms action depends on this. The form methods start with a human-facing ref and use resolve_ref to find the exact element the browser should touch.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what file sizes a file-upload input is actually holding inside the browser. It is used to tell whether an upload has really reached the browser, not just whether a filename has been mentioned.

**Data flow**: It reads a request dictionary containing a possible tab_id and a required ref. It finds the tab, resolves the ref into a browser node, turns that node into a JavaScript object, runs a small script that reads this.files, and returns a list of integer byte sizes. If the script result is not a list, it returns an empty list.

**Call relations**: This method uses _tab_id to normalize the tab choice, uses wire helpers to safely read typed values from the request, asks the browser session to locate the element, and then uses the browser connection and call_on to inspect the live page element.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a web page’s file-upload input. Someone would use it when an automated browser task needs to upload a document, image, or other file through a normal website form.

**Data flow**: It reads a request dictionary with tab_id, ref, and files. It converts the file list into strings, finds the requested tab, resolves the ref into the browser’s internal node id, and sends a browser command to set those files on the input. On success it returns the ref and the file paths it set; if the ref is not a file input, it raises a clear HallucinationError instead of failing obscurely.

**Call relations**: This method is called as the file-upload action in the form automation flow. It relies on _tab_id and wire helpers to clean up incoming data, then hands the real work to the browser’s DevTools connection. If Chrome rejects the operation, it translates that low-level CdpError into advice the higher-level agent can understand.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This fills or changes a form field on the page, including text inputs, select boxes, checkboxes, radio buttons, and editable content areas. It makes the page react as though the value changed by normal user interaction.

**Data flow**: It reads a request dictionary with tab_id, ref, and value. It finds the right tab, resolves the ref into a browser node, converts that node into a JavaScript object, and runs the form-input script with the requested value. The script changes the element, fires input and change events, and returns the element’s resulting value. If the ref cannot be resolved, it raises a HallucinationError telling the caller to re-read the page.

**Call relations**: This is the main form-filling path. It uses _tab_id and typed JSON helpers at the boundary, asks the browser session to find the page element, uses the DevTools connection to resolve it, and then delegates the page-side edit to call_on.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from incoming JSON into either an integer tab id or None. It keeps the public form methods from each having to repeat the same input cleanup.

**Data flow**: It receives a JSON value that may be an integer, float, string, empty value, or missing value. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything else becomes None, meaning “no specific tab requested.”

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input all call this before asking the browser session for a page. It is the shared first step that makes tab selection consistent across all form actions.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `browser action execution`

Browsers do not just need to know “the user typed A.” They expect a detailed keyboard event: which physical key was used, what character it produced, whether Shift or Control was held, whether it came from the keypad, and so on. This file is the translator between simple user-facing actions and those detailed Chrome DevTools Protocol calls, which are messages sent to Chrome to simulate input.

The large keyboard tables describe a US keyboard layout and Mac editing commands. They are based on Playwright’s behavior so this project can match a well-tested browser automation tool. The file first builds a lookup table that lets code ask for a key by many names: a physical code like `KeyA`, a character like `a`, or an alias like `Enter` for a newline.

A `KeyboardState` keeps track of what is currently held down, especially modifier keys such as Shift, Alt, Control, and Meta. That state matters because pressing `a` while Shift is down should produce `A`, and pressing a key twice while it is already down should be marked as an auto-repeat.

The public helpers then create browser calls for three common actions: pressing a key down, releasing it, and typing text or a key combination. For long text, the file skips fake key-by-key typing and sends one direct text insertion call, which is faster but does not trigger every individual key handler.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds the practical key lookup table used by the rest of the file. It expands the raw keyboard layout so a key can be found by its physical code, visible character, shifted character, or friendly alias.

**Data flow**: It starts with the US keyboard layout table. For each key, it creates a `KeyDescription`, which is the browser-ready description of that key. If the key has a shifted version, such as `1` becoming `!`, it creates that too. The result is a dictionary where many possible names point to the right key description.

**Call relations**: This runs when the module is loaded to create `LAYOUT_CLOSURE`. Later, `key_down` and `key_up` rely on `_description_for`, which reads from this completed lookup table. Without this setup step, typing shortcuts and characters would have no reliable way to become browser events.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Turns the currently held modifier keys into the number format Chrome expects. A modifier key is a key like Shift, Alt, Control, or Meta that changes the meaning of another key.

**Data flow**: It receives a set of modifier names that are currently pressed. It checks each known modifier and adds its assigned bit value if present. It returns a single integer that represents all active modifiers at once.

**Call relations**: Both `key_down` and `key_up` call this when building Chrome keyboard event data. It is the final conversion from this file’s easy-to-read set of names into Chrome’s compact numeric format.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the browser-ready description for a requested key, taking the current keyboard state into account. This is where `a` can become `A` if Shift is already held down.

**Data flow**: It receives the current `KeyboardState` and a requested key name or character. It looks that key up in the prepared keyboard table. If the key is unknown, it raises a validation error. If Shift is pressed and the key has a shifted form, it swaps in the shifted description. If another modifier such as Control or Alt is held, it removes normal text output because shortcuts usually should not type visible characters.

**Call relations**: `key_down` and `key_up` call this before creating their Chrome event messages. It is the decision point that keeps low-level events consistent with the keys already held in `KeyboardState`.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Adds Mac-specific editing instructions for shortcuts that Chrome understands, such as moving the cursor or deleting words. This helps simulated keyboard input behave more like real typing on macOS.

**Data flow**: It receives a physical key code and the set of pressed modifiers. It builds a shortcut name like `Shift+Meta+ArrowLeft`, looks it up in the Mac editing command table, filters out direct text-insertion commands, trims the trailing colon used in the source command names, and returns a list of command strings.

**Call relations**: `key_down` calls this only when the caller says the target browser is on Mac. Its result is placed into the Chrome key event so Chrome can perform native-style editing behavior for recognized shortcuts.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome command for pressing a key down. It also updates the remembered keyboard state so later events know which keys and modifiers are being held.

**Data flow**: It receives the current `KeyboardState`, the requested key, and whether the target is Mac. It asks `_description_for` what this key means right now, checks whether the key is already pressed to mark auto-repeat, records the key as pressed, and records it as an active modifier if it is Shift, Alt, Control, or Meta. On Mac, it also asks `_mac_commands` for matching editing commands. It returns one `Input.dispatchKeyEvent` call with all the details Chrome needs.

**Call relations**: `press_combo` uses this to press each key in a shortcut, and `type_text` uses it for each short, typable character. It depends on `_description_for` for the key meaning, `_mac_commands` for Mac editing behavior, and `modifiers_mask` to encode held modifiers for Chrome.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome command for releasing a key. It also updates the remembered keyboard state so the system no longer thinks that key is being held.

**Data flow**: It receives the current `KeyboardState` and the key to release. It looks up the key description, removes that key from the pressed-key set, removes it from the pressed-modifier set if appropriate, and returns one `Input.dispatchKeyEvent` call of type `keyUp`.

**Call relations**: `press_combo` calls this after pressing a shortcut, releasing keys in reverse order like a real user often would. `type_text` calls it after each character key press. It uses `_description_for` to identify the key and `modifiers_mask` to report the remaining held modifiers to Chrome.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string like `Ctrl+C` or `Shift+Enter` into a realistic sequence of key-down and key-up browser events.

**Data flow**: It receives the current `KeyboardState`, a combo string, and whether the target is Mac. It splits the combo on plus signs, cleans up spaces, converts friendly aliases like `ctrl` into canonical names like `Control`, and rejects an empty combo. It then presses each key in order and releases them in reverse order. The output is a list of Chrome calls to send.

**Call relations**: This is the higher-level shortcut helper. It delegates the actual event creation to `key_down` and `key_up`, so it benefits from all the same state tracking, modifier handling, and Mac command support.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a text string into browser input events. Short text is typed character by character so page key handlers and autocomplete can react; long text is inserted in one faster operation.

**Data flow**: It receives the current `KeyboardState`, the text to type, and whether the target is Mac. If the text is longer than the configured limit, it returns one direct `Input.insertText` call. Otherwise, it walks through each character. Characters known in the keyboard layout become a key-down followed by a key-up. Characters outside the layout are inserted directly one at a time.

**Call relations**: This is the main text-entry helper. For realistic short typing, it calls `key_down` and `key_up`; for long or unknown-character input, it hands Chrome direct text insertion calls instead.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Page representation internals
This group details how live browser pages become model-readable text with stable element references for later actions.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A browser page is visually rich, but a model needs a simpler map: what is on the page, what each item is called, whether it is clickable, and roughly where it is. This file builds that map from Chrome’s debugging interface, called CDP (Chrome DevTools Protocol), which is the same low-level channel developer tools use to inspect pages.

The file combines two browser views. One is a DOM snapshot, which gives element positions and details such as input type or image source. The other is the accessibility tree, which describes the page as screen readers see it: buttons, links, headings, text boxes, and names. It stitches these together, including frames and iframes, so embedded pages appear in the right place instead of being missing or detached.

It can then render the result in two ways. `render_page` creates a structured action map with roles, names, references, coordinates, and useful state like checked or disabled. `render_markdown` creates a reading view with headings, links, lists, images, and paragraphs. `BrowserPage` is the higher-level wrapper that takes snapshots, resolves references, and finds a clickable point for a referenced element. Without this file, the model would either see messy raw browser data or have no reliable way to connect text it read earlier to the element it wants to click later.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference, such as `e12` or `f1e3`, into its frame prefix and browser element id. This is used to check that a model is using a real reference format instead of inventing one.

**Data flow**: It receives a reference string. It matches it against the expected pattern, then returns the frame prefix and numeric backend element id; if the string does not fit, it returns nothing.

**Call relations**: When a page rendering starts from a specific reference, `_PageRenderer.render` uses this to find the target element. When an action needs to use a reference, `BrowserPage.resolve_ref` uses it before looking up the frame.

*Call graph*: called by 2 (resolve_ref, render).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Describes the browser-debugging call used by this file to ask Chrome for information or tell it to do something. It is a protocol method, meaning this file only states the shape of the method that real connection objects must provide.

**Data flow**: It takes a CDP method name, optional parameters, and an optional browser session id. A real implementation sends that request to Chrome and returns a JSON-like dictionary response.

**Call relations**: The snapshot code calls this method repeatedly in `fetch_target` to enable browser domains, capture snapshots, and read accessibility data. Other browser-settling code can also use the same connection shape to flush page tasks.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a short, repeatable number for a browser frame id so frame references can be written compactly. For example, a child frame may get a prefix like `f1`.

**Data flow**: It receives a browser frame id and returns an integer sequence number for that frame. The tab object owns the mapping from frame ids to numbers.

**Call relations**: When `BrowserPage._snapshot_oop` snapshots an out-of-process iframe, it asks the tab for this number so references inside that iframe get the right prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Describes how `BrowserPage` gets access to the underlying Chrome debugging connection. It is part of the session protocol rather than a concrete implementation.

**Data flow**: It reads the browser page session object and returns a CDP connection object. That returned object is then used to send browser commands.

**Call relations**: The `BrowserPage` methods use this connection whenever they need fresh page data, element geometry, or a new iframe session.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Describes the setup step required after attaching to a new browser debugging session. This matters for out-of-process iframes, which may need their own session before they can be inspected.

**Data flow**: It receives a session id. A real implementation prepares that session for later browser commands and returns when setup is complete.

**Call relations**: After `_oop_session` attaches to an out-of-process frame, it calls this session initializer before caching and using the session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It gives a fallback when the browser omits a value or sends something unexpected.

**Data flow**: It receives a JSON value and a default number. If the value is already numeric, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it while converting browser geometry and scroll values. `fetch_target` also uses it to read the page’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute from Chrome’s compact snapshot format. Chrome stores attributes as indexes into a shared string table, so this helper translates that into an ordinary value.

**Data flow**: It receives the snapshot string table, a flat list of attribute key/value indexes, and the attribute name to look for. It returns the matching attribute value as text, or nothing if it is absent.

**Call relations**: `_parse_document` calls this when it needs details such as an input’s `type`, an image’s `src`, or an iframe’s `src`.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Extracts useful per-document facts from one DOM snapshot document: element ids, positions, cursor style, input types, image sources, and iframe links. It turns Chrome’s dense snapshot tables into easier project data.

**Data flow**: It receives one document from the browser snapshot, the shared string table, and the device pixel ratio. It validates and decodes the browser data, converts coordinates into normal CSS pixels, records selected element details, and returns a `_RawDoc` object.

**Call relations**: `parse_snapshot` calls this once for each document in Chrome’s snapshot. This function uses `_float` for numeric cleanup and `_attr` for reading compact HTML attributes.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s full DOM snapshot into one `DocData` record per reachable document. It also works out where child documents sit on the page, like placing nested maps inside the rectangle of each iframe.

**Data flow**: It receives the raw snapshot, a device pixel ratio, and a starting page origin. It parses every document, follows iframe document links, adjusts element bounds so they are in page-level coordinates, ignores extension iframes, and returns document data ready to combine with accessibility data.

**Call relations**: `fetch_target` calls this after asking Chrome for a DOM snapshot. It depends on `_parse_document` for each individual document and then adds the cross-document positioning logic.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects a full structured snapshot for one browser debugging target. A target is usually the main page or a separate iframe process.

**Data flow**: It receives a CDP connection, a session id, frame reference-prefix rules, and a base origin. It enables the needed browser data sources, captures DOM geometry, reads the device pixel ratio, fetches accessibility trees for each frame, joins the data together, and returns a `FrameSnapshot` tree rooted at the target’s main frame.

**Call relations**: `BrowserPage._snapshot_target` calls this as the core snapshot step. It sends CDP commands through `Cdp.send`, uses `parse_snapshot` to decode geometry, and creates `FrameSnapshot` objects that renderers later consume.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Pulls the plain value out of a Chrome accessibility value object. Chrome wraps many accessibility fields in small dictionaries, and this helper unwraps them consistently.

**Data flow**: It receives a possible JSON value. If it is a dictionary with a `value` field, it returns that value as text; otherwise it returns an empty string.

**Call relations**: Both `_PageRenderer._render_node` and `_MarkdownRenderer._walk` use this to read a node’s role and name before deciding how to display it.

*Call graph*: called by 2 (_walk, _render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Finds a named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading `level`. This hides Chrome’s list-based property format from the rest of the renderer.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s properties, unwraps the matching property value if needed, and returns it; if the property is absent, it returns nothing.

**Call relations**: Page and markdown rendering use this helper whenever they need state, visibility, link URLs, heading levels, or other accessibility details.

*Call graph*: called by 4 (_render_content, _walk, _render_node, _format_extras); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node should be left out of the action-map text. It removes empty containers while keeping anything that has meaningful state.

**Data flow**: It receives an accessibility node, its role, and its name. If the role is one of the container-like roles, has no name, and has no important state property, it says to skip it; otherwise it keeps it.

**Call relations**: `_PageRenderer._render_node` uses this as part of deciding whether a node should appear as its own line or whether only its children should be shown.

*Call graph*: called by 1 (_render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so rendered page output stays readable and compact. It adds an ellipsis when text is cut.

**Data flow**: It receives text and a maximum length. If the text fits, it returns it unchanged; otherwise it returns a shortened version ending with `…`.

**Call relations**: The renderers use this for names, values, and image filenames so one unusually long field does not overwhelm the page summary.

*Call graph*: called by 3 (_render_node, _format_extras, _image_name).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Builds a fallback name for an image from its source URL when the accessibility tree does not provide one. It uses the filename part of the URL if it looks useful.

**Data flow**: It receives an image source string or nothing. It extracts the URL path, takes the final filename, truncates it if needed, and returns it only when it appears to have a file extension.

**Call relations**: `_PageRenderer._render_node` and `_MarkdownRenderer._render_content` call this when rendering unnamed images.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (_render_content, _render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Creates the extra state text appended to a rendered page line. This is where details like input type, current value, checked state, disabled state, and safe URLs become visible to the model.

**Data flow**: It receives an accessibility node and optional geometry/details for the matching DOM node. It reads selected properties, drops empty or unsafe-looking values, truncates long text, and returns a space-prefixed string of extras or an empty string.

**Call relations**: `_PageRenderer._render_node` calls this when building each visible line in the action-oriented page tree.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (_render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that belongs to a reference prefix, such as the empty prefix for the main page or `f1` for a child frame. It searches through the nested frame tree.

**Data flow**: It receives the root frame snapshot and a prefix. It checks the root, then recursively checks child frames, returning the matching frame or nothing.

**Call relations**: `_PageRenderer.render` uses this when rendering only the subtree named by a specific reference.

*Call graph*: called by 1 (render).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node id that corresponds to a browser backend DOM node id. This joins an element reference back to its accessibility-tree entry.

**Data flow**: It receives a frame snapshot and a backend element id. It scans the frame’s accessibility nodes and returns the matching accessibility node id, or nothing if it cannot find one.

**Call relations**: `_PageRenderer.render` uses this after `_frame_by_prefix` so it can start rendering from the exact referenced element.

*Call graph*: called by 1 (render).


##### `_PageRenderer.render`  (lines 489–504)

```
def render(self, root: FrameSnapshot, ref: str | None) -> str | None
```

**Purpose**: Starts rendering the action-oriented page tree. It can render the whole page or only the subtree under a specific element reference.

**Data flow**: It receives the root frame snapshot and an optional reference. If a reference is provided, it parses it, finds the matching frame and node, and renders from there; otherwise it renders from the root accessibility node. It returns the collected lines as one text block.

**Call relations**: `render_page` creates a `_PageRenderer` and calls this. This method delegates the actual tree walk to `_render_node`, using `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when a reference narrows the starting point.

*Call graph*: calls 4 internal fn (_render_node, _frame_by_prefix, _node_by_backend, split_ref).


##### `_PageRenderer._coord_str`  (lines 506–510)

```
def _coord_str(self, geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the model-sized coordinate system. This gives the model a rough clickable location next to the element description.

**Data flow**: It receives optional geometry. If bounds are available, it computes the rectangle center, scales it to the model’s coordinate size, and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: `_PageRenderer._render_node` calls this when writing a visible node line.

*Call graph*: called by 1 (_render_node).


##### `_PageRenderer._splice`  (lines 512–515)

```
def _splice(self, frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree under the iframe element where it belongs. This makes iframe contents read like part of the page instead of a separate disconnected document.

**Data flow**: It receives the current frame, an optional iframe backend id, and the depth to render at. If that backend id has a child frame with a root node, it renders that child root at the same place in the output.

**Call relations**: `_PageRenderer._descend` calls this after normal child nodes, so iframe content is stitched into the rendered tree.

*Call graph*: calls 1 internal fn (_render_node); called by 1 (_descend).


##### `_PageRenderer._render_node`  (lines 517–567)

```
def _render_node(self, frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Renders one accessibility node and then walks its children. This is the main decision point for what appears in the action map and what is treated as invisible structure.

**Data flow**: It receives a frame, an accessibility node id, a depth, and the parent’s name. It skips repeated or hidden nodes, reads role/name/state/geometry, applies filtering rules, writes a formatted line when useful, and then descends into children.

**Call relations**: This method is called by `_PageRenderer.render`, recursively by `_descend`, and by `_splice` for iframe roots. It uses helpers such as `_ax_value`, `_ax_property`, `_should_skip`, `_format_extras`, `_coord_str`, `_image_name`, and `_truncate` to keep each rendering decision small.

*Call graph*: calls 8 internal fn (_coord_str, _descend, _ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); called by 3 (_descend, _splice, render); 2 external calls (as_list, as_str).


##### `_PageRenderer._descend`  (lines 569–579)

```
def _descend(self, frame: FrameSnapshot, backend_id: int | None, child_ids: list[str], depth: int, parent_name: str) -> None
```

**Purpose**: Continues the page-tree walk through a node’s children and then through any frame attached at that node. It keeps the output order close to how the accessibility tree presents the page.

**Data flow**: It receives the current frame, the current element’s backend id, child accessibility ids, depth, and parent name. It renders each child at the given depth, then asks `_splice` to add iframe content if this node owns a child frame.

**Call relations**: `_PageRenderer._render_node` calls this after either skipping a transparent node or writing a visible one.

*Call graph*: calls 2 internal fn (_render_node, _splice); called by 1 (_render_node).


##### `render_page`  (lines 582–599)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Public helper that turns a `FrameSnapshot` into the model’s action-oriented page text. It includes roles, names, references, coordinates, and selected state.

**Data flow**: It receives the snapshot root, viewport size, optional model size, filter type, maximum depth, and optional starting reference. It computes the coordinate scale, creates a `_PageRenderer`, and returns the renderer’s text output.

**Call relations**: `BrowserPage.tree` calls this after taking a fresh snapshot. It is the bridge between raw snapshot data and the text shown to the model for page navigation.

*Call graph*: called by 1 (tree); 2 external calls (__init__, effective_model_size).


##### `_MarkdownRenderer.render`  (lines 613–617)

```
def render(self, root: FrameSnapshot) -> str
```

**Purpose**: Starts producing a reading-friendly markdown version of a page snapshot. This view favors document structure over click targets.

**Data flow**: It receives the root frame snapshot. If there is a root accessibility node, it walks the tree, flushes any pending inline text, and returns markdown blocks separated by blank lines.

**Call relations**: `render_markdown` creates a `_MarkdownRenderer` and calls this. The detailed traversal happens in `_walk`.

*Call graph*: calls 2 internal fn (_flush, _walk).


##### `_MarkdownRenderer._emit`  (lines 619–622)

```
def _emit(self, text: str) -> None
```

**Purpose**: Adds one finished markdown block if it is not empty and not a duplicate of the previous block. This keeps the reading view cleaner.

**Data flow**: It receives text, trims surrounding whitespace, and appends it to the block list only when useful. It changes the renderer’s stored blocks and returns nothing.

**Call relations**: `_flush` and `_render_content` call this whenever inline text or a structured item, such as a heading or list item, is ready to become a markdown block.

*Call graph*: called by 2 (_flush, _render_content).


##### `_MarkdownRenderer._flush`  (lines 624–627)

```
def _flush(self) -> None
```

**Purpose**: Turns accumulated inline words into a paragraph block. It is used at boundaries such as headings, blocks, and iframe transitions.

**Data flow**: It checks the renderer’s inline text buffer. If there is text waiting, it joins the pieces with spaces, emits the paragraph, clears the buffer, and returns nothing.

**Call relations**: `_MarkdownRenderer.render`, `_walk`, and `_render_content` call this to make sure paragraphs end in the right places.

*Call graph*: calls 1 internal fn (_emit); called by 3 (_render_content, _walk, render).


##### `_MarkdownRenderer._walk`  (lines 629–649)

```
def _walk(self, frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and feeds each meaningful node into the markdown renderer. It also stitches child frames into the reading order.

**Data flow**: It receives a frame, an accessibility node id, and the parent name. It avoids revisiting nodes, skips hidden nodes, renders the current node’s content, walks children, flushes at block boundaries, and then walks any child frame attached to the same backend element.

**Call relations**: `_MarkdownRenderer.render` starts the walk from the root. `_walk` calls `_render_content` for formatting decisions and uses `_ax_value` and `_ax_property` to read accessibility data.

*Call graph*: calls 4 internal fn (_flush, _render_content, _ax_property, _ax_value); called by 1 (render); 2 external calls (as_list, as_str).


##### `_MarkdownRenderer._render_content`  (lines 651–681)

```
def _render_content(self, frame: FrameSnapshot, backend_id: int | None, role: str, name: str, node: JsonDict) -> None
```

**Purpose**: Converts one accessibility node into markdown content when appropriate. It knows how to represent headings, links, list items, images, blocks, and ordinary inline text.

**Data flow**: It receives frame context, optional backend id, role, name, and the node data. Depending on the role, it may flush existing text, emit a markdown block, append inline text, or use image source data as fallback alt text.

**Call relations**: `_MarkdownRenderer._walk` calls this for each visible node before walking its children. It uses `_ax_property` for details like heading level and URL, and `_image_name` for unnamed images.

*Call graph*: calls 4 internal fn (_emit, _flush, _ax_property, _image_name); called by 1 (_walk).


##### `render_markdown`  (lines 684–689)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Public helper that turns a `FrameSnapshot` into a markdown reading view. It is for understanding page content rather than choosing click targets.

**Data flow**: It receives the root frame snapshot, creates a markdown renderer, and returns the rendered markdown text.

**Call relations**: `BrowserPage.markdown` calls this after taking a fresh snapshot.

*Call graph*: called by 1 (markdown); 1 external calls (__init__).


##### `BrowserPage.tree`  (lines 698–706)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Returns the current page as the model’s structured action map. This is the high-level method used when the model needs to inspect what it can see or click.

**Data flow**: It receives a tab, a filter type, and an optional starting reference. It takes a fresh snapshot, renders it with the configured viewport and model size, and returns the text or nothing if the requested reference cannot be rendered.

**Call relations**: This method combines `BrowserPage.snapshot` with `render_page`, hiding the lower-level snapshot and rendering details from callers.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 708–709)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Returns the current page as markdown for reading. This is useful when the model needs the article-like content of a page rather than an interaction map.

**Data flow**: It receives a tab, takes a fresh snapshot, renders the snapshot as markdown, and returns the markdown string.

**Call relations**: This method combines `BrowserPage.snapshot` with `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 711–724)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Checks and resolves a model-provided element reference into the frame and backend element id it points to. It raises a clear model-facing error when the reference is invalid or stale.

**Data flow**: It receives a tab and a reference string. It parses the reference, looks up the frame prefix in the tab’s registered frame map, and returns the frame node plus backend id; if anything fails, it raises `HallucinationError` with guidance to re-read the page.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for an element’s screen position. It relies on `split_ref` and on `snapshot` having registered frame prefixes earlier.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 726–752)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the center point of a referenced page element so an action can click or point at it. It also scrolls the element into view first when possible.

**Data flow**: It receives a tab and a reference. It resolves the reference, asks Chrome to scroll the element into view, reads its content quad or box model, averages the four corners, adds the frame origin, and returns integer x/y coordinates. If Chrome cannot resolve the element, it raises a helpful `HallucinationError`.

**Call relations**: Callers use this after receiving refs from `tree` or similar page-reading flows. It starts with `resolve_ref`, sends CDP commands through the browser connection, and uses `_coord_float_or_default` to clean coordinate values.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 754–758)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a fresh full snapshot of the tab, including frames, and records which frame prefix each future reference belongs to. This prepares both rendering and later reference resolution.

**Data flow**: It receives a tab. It snapshots the main target, clears the tab’s old reference-frame map, registers every frame in the new snapshot tree, and returns the root snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates the browser inspection to `_snapshot_target` and frame-reference bookkeeping to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 760–777)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and, if allowed by depth limits, attaches snapshots for out-of-process iframes. This is the common path for both the main page and separate iframe targets.

**Data flow**: It receives tab context, a session id, a root prefix, an origin, and the current frame depth. It fetches the target snapshot, optionally attaches out-of-process child frames, and returns the completed root frame snapshot for that target.

**Call relations**: `BrowserPage.snapshot` uses this for the main tab session, and `_snapshot_oop` uses it for iframe sessions. It calls `fetch_target` for the base snapshot and `_attach_oop_frames` for separate-process frames.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 779–787)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Walks an existing frame snapshot tree and fills in iframe children that live in separate browser processes. These are called out-of-process iframes, and they need separate sessions to inspect.

**Data flow**: It receives a tab, a root frame snapshot, and the current depth. It walks through frames, tries to snapshot each listed out-of-process iframe, and attaches successful child snapshots under the matching iframe backend id.

**Call relations**: `_snapshot_target` calls this after a normal target snapshot when the maximum frame depth has not been reached. It delegates each iframe attempt to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 789–815)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe, if Chrome can describe and attach to it. It safely gives up when the iframe cannot be inspected.

**Data flow**: It receives the tab, parent frame, iframe backend id, and depth. It asks Chrome which frame id belongs to the iframe, gets or creates a session for that frame, computes the child origin from iframe bounds, and returns a nested `FrameSnapshot`; on known CDP failures it logs and returns nothing.

**Call relations**: `_attach_oop_frames` calls this for each out-of-process iframe marker. It uses `_oop_session` to get the child debugging session, `PageTab.frame_seq` to build the child reference prefix, and `_snapshot_target` to take the actual child snapshot.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 817–820)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame in a snapshot tree so later element references can be resolved. This is the lookup table behind frame prefixes in refs.

**Data flow**: It receives a tab and a frame snapshot. It stores the frame id, session id, and origin under the frame’s prefix, then repeats the same registration for child frames.

**Call relations**: `BrowserPage.snapshot` calls this after building the snapshot tree. `BrowserPage.resolve_ref` later depends on the registered `tab.ref_frames` entries.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 822–835)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a Chrome debugging session for an out-of-process iframe, reusing a cached one when possible. This avoids attaching to the same iframe over and over.

**Data flow**: It receives a frame id. If a session id is already cached, it returns it; otherwise it asks Chrome to attach to the target, initializes the new session, stores it in the cache, and returns the session id. If attaching fails, it returns nothing.

**Call relations**: `_snapshot_oop` calls this before trying to snapshot a separate-process iframe. It uses the browser connection and then calls the browser session’s initializer for new sessions.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 838–847)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts one coordinate value from Chrome into a float, accepting numbers and numeric strings. It supplies a default for missing values and rejects invalid ones.

**Data flow**: It receives a possible coordinate value and a default. Numbers become floats, non-empty strings are parsed as floats, `None` becomes the default, and other types raise a validation error.

**Call relations**: `BrowserPage.ref_point` uses this while averaging Chrome’s element corner coordinates into a single center point.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).

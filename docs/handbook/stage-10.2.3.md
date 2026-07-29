# Browser page understanding and action execution tools  `stage-10.2.3`

This stage is the browser “hands and eyes” of the system. It is part of the main work loop, where an agent needs to understand a web page and then act on it. The tools file is the front desk: it offers actions like open a page, read content, click, fill forms, upload files, and gather downloads, while sharing one browser connection for the turn.

The action definitions say exactly what requests are allowed and what information they must include. Before an action runs, the fixup code corrects small model mistakes, such as typing before focusing a field. The page and content code turn a live web page into readable text or a structured map of buttons, fields, links, and coordinates. The find code helps match a user’s description to a real page element.

Once the target is known, computer, forms, keys, and coordinate code turn the request into real browser events: clicks, scrolling, typing, shortcuts, uploads, and coordinate-correct mouse moves. The errors file gives a clear failure when the model asks for something impossible.

## Files in this stage

### Agent tool facade
Agent-facing browser tools open, read, act on, and clean up shared browser sessions.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `request handling`

This file is the bridge between the agent's tool system and the browser automation engine. Without it, the agent would not have a clean, validated set of browser commands, and each command might open its own separate browser connection or lose track of files and screenshots.

Each public browser action is defined as a tool with an input shape, using Pydantic models to check that the incoming arguments make sense. The tool functions then translate those checked arguments into plain dictionaries and pass them to `BuaSurface`, the object that actually talks to the browser. Think of `BuaSurface` as the driver's seat, while this file is the labeled dashboard: each button is named, checked, and wired to the right control.

A key detail is `_browser`: it creates the browser surface only when the first browser tool is used in a turn. It then reuses that same surface for later browser tools in the same turn and registers cleanup so the browser connection closes properly. This avoids waste and keeps browser state consistent.

Most tools return JSON text. A few also move bytes into the shared workspace: `computer` can save a screenshot, and `wait_for_download` saves downloaded file contents. That makes browser-produced files visible to the rest of the agent system by path.

#### Function details

##### `_browser`  (lines 105–128)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser control surface for the current turn. If this is the first browser action in the turn, it creates the surface, connects it to the selected browser transport, and arranges for it to be closed at the end.

**Data flow**: It receives the current tool context, which contains things like the cleanup registry, browser connection provider, sandbox, model, and extension store. It checks whether a browser surface is already cached for this turn; if not, it builds one and registers its close method for later cleanup. It returns the ready-to-use `BuaSurface` object.

**Call relations**: Every browser tool function calls this before doing real browser work. It is the shared doorway into `BuaSurface`, so navigation, reading, form input, downloads, and computer-style actions all use the same browser session during a turn.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 131–132)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a browser reply dictionary as a tool result containing JSON text. This gives the agent a consistent text format for ordinary browser responses.

**Data flow**: It takes a dictionary reply, converts it into a JSON string, places that string inside a text content object, and returns it as a tool result. It does not change the original browser state or write files.

**Call relations**: Most tool functions call this after `BuaSurface` returns a reply. It is the common final step for turning browser-engine answers into the tool-system format.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 135–138)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a value from a browser reply is a non-empty string. It is used where the code cannot continue safely without a specific text value, such as encoded file contents or a filename.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns it; otherwise it raises an error saying the browser reply is missing that field.

**Call relations**: `_computer` uses it before saving a screenshot, and `_wait_for_download` uses it before saving a downloaded file. It acts like a small safety gate before bytes are decoded and written to the workspace.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 141–145)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool, such as going to a URL or using browser history. It lets the agent move the active tab to the page it needs.

**Data flow**: It receives the tool context and validated navigation arguments. It removes the human-facing activity description, sends the remaining browser instructions to the shared browser surface, and returns the browser's reply as JSON text.

**Call relations**: This is the handler behind the `navigate` tool definition. When the agent asks to navigate, this function gets the shared browser surface through `_browser`, delegates the real browser action, then formats the response with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 148–149)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. This helps the agent understand what pages are available before deciding where to act.

**Data flow**: It receives the tool context and the caller's plain-language reason for checking tabs. It asks the shared browser surface for tab context with no extra browser arguments, then wraps the reply as JSON text.

**Call relations**: This is the handler behind the `tabs_context` tool. It relies on `_browser` to reuse the turn's browser session and on `_json_result` to present the tab summary back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 152–154)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opening a given URL. If no URL is provided, it opens a blank page.

**Data flow**: It receives the tool context and tab creation arguments. It chooses the requested URL or falls back to `about:blank`, sends that to the browser surface, and returns the created-tab reply as JSON text.

**Call relations**: This is the handler behind the `tabs_create` tool. It gets the shared browser surface through `_browser`, asks it to create the tab, and passes the result through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 157–161)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, usually because the agent is done with that page. It can target a specific tab when a tab ID is supplied.

**Data flow**: It receives the tool context and close-tab arguments. It removes the human-facing description, sends the remaining close request to the browser surface, and returns the reply as JSON text.

**Call relations**: This is the handler behind the `tabs_close` tool. It fits into the same pattern as the other browser commands: get the turn's browser surface, ask it to perform the action, then format the answer.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 164–168)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file input on a web page using files from the shared workspace. This is how the agent attaches local workspace files to browser forms.

**Data flow**: It receives the tool context and upload arguments, including a browser element reference and workspace file paths. It strips out the activity description, sends the upload request to the browser surface, and returns the browser's reply as JSON text.

**Call relations**: This is the handler behind the `upload_file` tool. It depends on `_browser` because the browser surface knows how to connect the page element with the sandbox-backed workspace files.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 171–175)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads a structured view of the current web page, focused on what the browser exposes for accessibility and interaction. This helps the agent identify buttons, links, fields, and other page parts.

**Data flow**: It receives the tool context and page-reading options such as depth, filter, reference ID, or tab ID. It removes the activity description, sends the reading request to the browser surface, and returns the structured page reply as JSON text.

**Call relations**: This is the handler behind the `read_page` tool. The browser surface does the actual page inspection, while this function validates and forwards the request and then formats the result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 178–182)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. It is useful when the agent needs the page's words more than its clickable structure.

**Data flow**: It receives the tool context and optional tab information. It removes the activity description, asks the shared browser surface for page text, and returns that reply as JSON text.

**Call relations**: This is the handler behind the `get_page_text` tool. It sits between the tool system and the browser surface, keeping the input shape consistent and the output format predictable.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 185–189)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the current page for elements matching a query, such as text, role, name, or URL. This helps the agent locate the thing it wants to click, read, or fill.

**Data flow**: It receives the tool context and a search query, plus optional tab information. It removes the activity description, sends the query to the browser surface, and returns the matched elements as JSON text.

**Call relations**: This is the handler behind the `find` tool. It uses `_browser` to reach the browser session and then hands the browser's search results back through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 192–196)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. This lets the agent type or select values in web forms without guessing screen coordinates.

**Data flow**: It receives the tool context, a browser element reference, and the value to put into that element. It removes the activity description, sends the form update to the browser surface, and returns the reply as JSON text.

**Call relations**: This is the handler behind the `form_input` tool. It relies on earlier page-reading or finding steps to supply a useful element reference, then delegates the actual page update to `BuaSurface`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 199–217)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs lower-level browser actions such as mouse moves, clicks, keyboard input, waits, scrolling, and screenshots. It is the flexible tool for cases where page-structure tools are not enough.

**Data flow**: It receives the tool context and a list of computer-style actions. It sends those actions to the browser surface. If asked to save a screenshot, it checks that the reply includes screenshot data, decodes the base64 text into bytes, and writes it to the workspace. If screenshot data is present, it returns both JSON text for the non-image details and image content for display; otherwise it returns JSON text only.

**Call relations**: This is the handler behind the `computer` tool. It starts like the other tools by getting the shared browser surface, but it has extra output work because screenshots may be returned as image content and may also be persisted through the sandbox.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 220–228)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves the downloaded file into the shared workspace. This turns a browser-only download into a file the rest of the agent system can use by path.

**Data flow**: It receives the tool context and optional download details such as a download ID, target path, or timeout. It asks the browser surface for the completed download, checks that the reply includes a filename and base64-encoded content, decodes the content into bytes, writes those bytes under the chosen downloads folder, and returns the saved file path, filename, and size as JSON text.

**Call relations**: This is the handler behind the `wait_for_download` tool. It combines the browser surface's download result with the sandbox's file-writing ability, so browser downloads become normal workspace files.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### Action execution pipeline
High-level browser actions are normalized, validated, and executed against the live page.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the bridge between an outside agent’s idea of using a browser and the browser’s actual controls. The agent sends actions in a model-friendly coordinate system or by page references. This file checks and adjusts those actions, converts coordinates to the real browser viewport, sends mouse and keyboard events through Chrome DevTools Protocol (CDP, a control channel for Chrome-like browsers), waits for the page to settle, and then reports what happened.

The main class, BrowserComputer, works like a remote-control operator. Its run method receives a batch of actions, cleans them up, performs them in order, waits after groups of actions, gathers warnings, captures a screenshot, and returns a response. Individual actions are handled by act, which chooses the right lower-level helper: clicking, dragging, typing, pressing keys, scrolling, waiting, or scrolling an element into view.

The file also includes safeguards and usability hints. It warns if the agent keeps scrolling when page-reading tools may be better. It warns before sign-in-like pages. It detects native select dropdowns, because clicking their options often does not work in this browser-control setup, and tells the caller to use a form-input tool instead. After a click, it can mark the screenshot with a small blue dot, like putting a sticky note on a map to show where the last action happened.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the expected browser-session interface. It should return the browser tab that actions should be applied to, either the current tab or a specific tab if one is requested.

**Data flow**: It receives an optional tab identifier. The concrete browser session uses that to find the right tab and returns a tab object with a session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this interface at the start of a request so it knows which tab to control. The actual implementation lives elsewhere; this file only states what BrowserComputer needs.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It provides the CDP connection, which is the channel used to send commands into the browser.

**Data flow**: It takes no input beyond the session object. It returns a connection object that can send browser commands such as mouse events, screenshots, and DOM operations.

**Call relations**: BrowserComputer.run and the lower-level action helpers use this whenever they need to talk to the browser. The method is defined as an interface here so BrowserComputer does not need to know how the connection was created.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It should collect summary information about a tab, such as details the caller needs after actions finish.

**Data flow**: It receives a tab object. The concrete session reads information about that tab and returns it as a JSON-like dictionary.

**Call relations**: BrowserComputer.run calls this near the end of an action batch and merges the returned tab details into the final response.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This is part of the expected browser-session interface. It should return the titles of open tabs so this file can spot sign-in or account-creation situations.

**Data flow**: It reads the browser’s current tab titles and returns them as a list of strings.

**Call relations**: BrowserComputer.run uses these titles to call sign_in_warning, adding a safety reminder if any tab title suggests logging in or registering.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It runs a small JavaScript function on a specific browser object, such as a dropdown element.

**Data flow**: It receives a browser session id, an object id inside the browser, a JavaScript function string, and optional arguments. It runs that function against the object and returns the result as a JSON-like dictionary.

**Call relations**: BrowserComputer._select_reminder uses this to inspect a native select dropdown after a click, so it can tell the caller what options exist and how to choose one properly.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a page reference, such as a short element id returned by a page-reading tool, into the browser’s internal node information.

**Data flow**: It receives a tab and a reference string. The concrete session looks up that reference and returns the browser node plus the backend node id that CDP can use.

**Call relations**: BrowserComputer.act uses this for the scroll_to action, where it must ask the browser to bring a referenced element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This is part of the expected browser-session interface. It finds a clickable point for a referenced page element.

**Data flow**: It receives a tab and a reference string. The concrete session resolves the reference and returns x and y coordinates in the browser viewport.

**Call relations**: BrowserComputer.point calls this when an action names a page element instead of giving raw coordinates.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main entry for performing a batch of browser actions. It validates the requested actions, runs them, waits for the page to calm down, captures a screenshot, and returns a human-readable result with warnings.

**Data flow**: It receives a JSON-like request containing a tab id and actions. It selects the tab, converts raw action data into ComputerAction objects, adjusts coordinates, runs actions in batches, collects messages and warnings, drains newly started downloads, captures a screenshot, optionally marks the last click, and returns tab info, output text, last-click coordinates, and screenshot data.

**Call relations**: This method is the conductor for the whole file. It calls act for each individual action, uses _select_reminder after clicks, uses sign_in_warning for safety reminders, uses mark_click through a background executor for image editing, and asks the browser session for tab information and screenshots before replying.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one requested browser action. It decides whether the action means clicking, dragging, typing, pressing a key, waiting, scrolling, taking a screenshot, or moving an element into view.

**Data flow**: It receives the target tab and one normalized action. It first works out the target point if the action needs one, then sends the proper browser input events or waits, and finally returns a short message plus the last viewport point if the action touched the page at a specific place.

**Call relations**: BrowserComputer.run calls this for every action in a batch. This method delegates the physical work to helpers such as _click, _drag, _scroll, _dispatch, _to_viewport, and _to_model, and it uses require_point or require_coord to reject incomplete action requests.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds where an action should happen on the screen. The action may name a page element by reference or give a coordinate directly.

**Data flow**: It receives a tab and an action. If the action has a reference, it asks the browser session for that element’s point; if it has a coordinate, it converts from model coordinates to viewport coordinates; if neither is present, it returns nothing.

**Call relations**: BrowserComputer.act calls this before actions that may need a screen location. It hands coordinate conversion to _to_viewport when the caller supplied model-space coordinates.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the model’s screen size to the browser’s actual viewport size. This matters because the agent and the browser may not use the same pixel dimensions.

**Data flow**: It receives an x and y pair in model coordinates. It scales that point using the model size and viewport size, then returns the matching viewport x and y pair.

**Call relations**: BrowserComputer.point uses this for normal coordinate-based actions. BrowserComputer.act also uses it for drag starts and default scroll positions.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the browser viewport back into the model’s coordinate system. It is used so responses describe positions in the same coordinate system the caller understands.

**Data flow**: It receives an x and y pair from the real browser viewport. It scales that point back to model coordinates and returns the converted pair.

**Call relations**: BrowserComputer.act uses this when building messages like “Clicked (x,y).” BrowserComputer.run uses it to report the final last_click value.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a click landed on a native HTML select dropdown and, if so, prepares a helpful reminder. Native select menus are a special case because clicking their opened options may not work through this browser-control path.

**Data flow**: It receives the tab and clicked viewport point. It asks the browser which element is at that point, walks up to a select element if one exists, reads its first options and total count, finds a reference if possible, and returns a reminder string; if anything cannot be inspected, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It uses select_reminder to turn the technical dropdown details into a caller-friendly instruction.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a sequence of keyboard-related CDP commands to the browser. It is used after another helper has translated text or a key combination into low-level browser events.

**Data flow**: It receives a tab and a list of CDP method-and-parameter pairs. It sends each command to the browser session for that tab and returns nothing after all commands have been sent.

**Call relations**: BrowserComputer.act calls this for type and key actions. The event lists are produced by the keyboard helpers type_text and press_combo outside this file.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one mouse event to the browser, such as move, press, release, or wheel. It is the common delivery point for all mouse-like actions.

**Data flow**: It receives a tab and a dictionary of mouse-event details. It sends an Input.dispatchMouseEvent command through the browser connection for that tab and returns nothing.

**Call relations**: _click, _drag, and _scroll all call this instead of sending CDP commands directly. That keeps mouse event delivery in one small place.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a left, right, double, or triple click at a specific viewport location. It recreates the normal sequence a real mouse would make: move there, press, release.

**Data flow**: It receives a tab, x and y viewport coordinates, a mouse button name, and a click count. It reads the currently pressed keyboard modifiers, sends a mouse move, then sends matching press and release events for each click, and returns nothing.

**Call relations**: BrowserComputer.act calls this for click actions. It uses _mouse_event for each browser event and modifiers_mask so held keys such as Shift or Ctrl are included correctly.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one viewport point to another. It moves in several small steps because many web pages only recognize drag-and-drop if the mouse travels through intermediate positions.

**Data flow**: It receives a tab, a start point, and an end point. It reads current keyboard modifiers, moves to the start, presses the left mouse button, sends several gradual mouse moves toward the end, releases the button, and returns nothing.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It sends all of its browser mouse commands through _mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This scrolls the page at a specific viewport point. It uses a mouse wheel event, which is how browsers normally receive scroll input.

**Data flow**: It receives a tab, a viewport x and y location, and horizontal and vertical scroll amounts. It includes any currently held keyboard modifiers, moves the mouse to the target point, sends a wheel event with the scroll deltas, and returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating scroll direction and distance. It uses _mouse_event for both the mouse move and wheel event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser may be on a login, sign-in, or registration page. It exists to remind the caller not to sign in or create accounts without the user’s permission.

**Data flow**: It receives a list of tab titles, lowercases them, and searches for sign-in-related words. It returns the warning message if any title matches, otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after gathering tab titles, then adds the returned message as a system reminder in the action response.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This builds a plain-language instruction for what to do after clicking a native select dropdown. It tells the caller to use a form-input tool instead of trying to click the dropdown’s options.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total number of options. It formats a short list of options, notes if more exist, chooses instructions based on whether a reference is known, and returns the full reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked select element. This function is only responsible for turning those details into a helpful message.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This adds a small blue marker to a screenshot at the last clicked point. It helps the caller visually confirm where the browser was clicked.

**Data flow**: It receives a base64-encoded screenshot and a viewport coordinate. It decodes the image, draws a translucent circle at that point, saves the image back as a JPEG, encodes it again as base64, and returns the new screenshot string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a recent click. Because image editing can take time, run calls it through a background executor rather than blocking the event loop.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This safely turns a possible tab id into an integer, or returns nothing if no usable id was supplied. It lets callers provide the id as a number or a non-empty string.

**Data flow**: It receives a JSON-like value. If the value is an integer, float, or non-empty string, it converts it to an integer; for anything else, it returns None.

**Call relations**: BrowserComputer.run uses this before asking the browser session for a page, so missing or loosely typed tab ids are handled consistently.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action has a usable target point. If a click-like action has neither coordinates nor a reference, this gives a clear validation error.

**Data flow**: It receives a possible point and the action name. If the point exists, it returns it unchanged; if not, it raises a ValidationError explaining that the action needs a coordinate or reference.

**Call relations**: BrowserComputer.act calls this before actions that cannot proceed without a screen point, such as clicks and drag endings.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used when an action needs a specific coordinate value, such as the starting point of a drag.

**Data flow**: It receives a possible coordinate and the field name. If the coordinate exists, it returns it unchanged; if not, it raises a ValidationError naming the missing field.

**Call relations**: BrowserComputer.act calls this for drag actions before converting the drag start coordinate into viewport space.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `pre-dispatch during browser action handling`

This file acts like a helpful proofreader for a list of browser commands. A model may say things like “type hello at this spot” without first clicking the spot, or it may write the two characters “\n” when it really means a newline. If these rough commands were sent straight to the browser, some would fail or behave oddly. This file repairs those cases before dispatch.

The main function, fixup_actions, walks through each ComputerAction and returns a corrected list. For typing, it makes sure the target is focused first by inserting a left click when needed. It also turns escaped text like “\t” and “\n” into real tab and newline characters. For scrolling, it fills in a missing anchor point with the center of the model’s working area. For “scroll_to” commands that do not name a target reference, it changes them into a normal scroll. For waits without a duration, it supplies a default of three seconds. It also simplifies double-click or triple-click actions aimed only at a reference into a single left click, because a coordinate is needed for those multi-clicks.

The second public helper, split_at_waits, divides an action list into batches ending at waits. That lets the browser pause and settle between groups of actions, instead of rushing through everything at once.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: Repairs a batch of browser actions so they are safer and more complete before the browser receives them. It fills in missing details and rewrites a few fragile commands into simpler ones that are more likely to work.

**Data flow**: It receives a list of ComputerAction objects, the current viewport size, and optionally the model’s own coordinate size. It first decides the effective model size and finds its center point. Then it reads each action in order, possibly adding a focus click, fixing escaped text, adding a scroll coordinate, converting an incomplete scroll_to into a scroll, giving waits a default duration, or replacing certain reference-based multi-clicks with a left click. It returns a new list of actions; the original list is not modified in place.

**Call relations**: This is the main repair step in the file. When it sees a type action that needs focus, it asks _focus_click to build the needed click. When it sees text that may contain escaped characters, it asks _unescape_text to clean it. It also relies on effective_model_size to understand the coordinate space, and creates ComputerAction and ScrollParameters objects when it needs replacement actions.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: Splits a long action list into smaller batches, with each wait action ending a batch. This gives the browser time to settle after actions that are meant to pause.

**Data flow**: It receives a list of ComputerAction objects. It walks through them in order, collecting actions into a current batch. Whenever it reaches an action whose action name is "wait", it closes that batch and starts a new one. At the end, it returns a list of batches, keeping every original action in the same order.

**Call relations**: This function is a companion to the action-fixup flow. After actions have been prepared, another part of the system can use these batches to dispatch commands in chunks, pausing at waits instead of sending one uninterrupted stream.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: Builds a left-click action that focuses the place where a later typing action is supposed to happen. It is a small helper used when typing needs a target to be clicked first.

**Data flow**: It receives a ComputerAction, usually a type action with either a coordinate or a reference. If the action has a coordinate, it creates a left-click at that coordinate. Otherwise, it creates a left-click aimed at the same reference. The result is a new ComputerAction that can be inserted before the typing action.

**Call relations**: fixup_actions calls this helper when it finds a type action that has a target but was not already preceded by a click-like action. The helper hands back the focus click, and fixup_actions places it into the corrected action list before the original typing command.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: Turns written escape sequences in typed text into the real characters they stand for. For example, it changes the two visible characters "\n" into an actual newline.

**Data flow**: It receives a ComputerAction and reads its text field. If the text does not contain any known escaped literal, it returns the action unchanged. If it finds known escaped literals such as "\t" or "\n", it replaces them with real tab or newline characters and returns a copied action with the corrected text.

**Call relations**: fixup_actions calls this helper for type actions. The helper uses the action’s copy method to make the text change without mutating the original object, then gives the corrected action back to fixup_actions for inclusion in the outgoing action list.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a form template for controlling a browser. Instead of letting the rest of the system pass around loose, unclear instructions like “click there” or “scroll a bit,” it gives every browser action a strict, named structure. That matters because browser automation needs precision: a click needs either screen coordinates or an element reference, typing needs text, scrolling needs a direction and amount, and waiting needs a safe duration.

The file uses Pydantic models, which are Python classes that validate data automatically. In plain terms, they check that incoming action requests have the right fields and sensible values before anything tries to perform them. For example, scroll direction must be one of up, down, left, or right. Scroll amount can be a number from 0 to 5 screen-heights, or the special value "max" to jump far through a page. Waiting is capped at 30 seconds, so a bad request cannot pause forever.

The main model, `ComputerAction`, describes one requested browser move. It covers action type, target location, typed text, scroll settings, wait duration, drag start point, and element references found earlier by page-reading tools. Without this file, different parts of the browser extension could disagree about what an action looks like, making automation brittle and error-prone.


### Page understanding and lookup
Live pages are converted into model-friendly representations and searched for usable element references.

### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is messy: it has HTML nodes, accessibility nodes, nested frames, off-screen content, browser-only extension frames, and coordinates in different units. This file acts like a translator between Chrome and the rest of the system. It asks Chrome for two views of the page: a DOM snapshot, which gives element geometry and attributes, and an accessibility tree, which gives roles like button, link, textbox, and names a screen reader would see. It then joins those views together so each visible item can have a role, label, stable reference, and center point.

Frames are important. Normal iframes appear inside the same snapshot, but out-of-process iframes live in separate browser targets. This file detects both kinds and stitches them into one tree, like joining pages from separate booklets into the right place in one manual.

The main output is used in two ways. `render_page` creates a structured action map for the model, with refs such as `e12` or `f1e3`. `render_markdown` creates a calmer reading view for understanding content. `BrowserPage` ties this all to a browser session, keeps frame refs resolvable, and can turn a returned ref back into a screen point.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser reference string, such as `e12` or `f1e3`, into its frame prefix and element id. This protects the system from acting on made-up or malformed references.

**Data flow**: It receives a text ref. It checks whether the text matches the expected pattern, then returns the frame prefix plus the numeric backend element id; if the text does not match, it returns nothing.

**Call relations**: When rendering a subtree or resolving an action target, `render_page` and `BrowserPage.resolve_ref` call this first so they know which frame and which browser node the ref points to.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the Chrome DevTools Protocol sender used by this file. Chrome DevTools Protocol, or CDP, is Chrome’s remote-control API for asking about pages and performing browser operations.

**Data flow**: A caller provides a CDP method name, optional parameters, and optionally a browser session id. The implementation sends that request to Chrome and returns a JSON-like dictionary response.

**Call relations**: `fetch_target` relies on this interface to collect page snapshots and accessibility trees. Other browser subsystems, such as page-settling code, can use the same sending contract.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Defines how a tab assigns a short sequence number to a frame. That number becomes part of refs for elements inside iframes, so refs stay readable and unique.

**Data flow**: It receives a browser frame id. It returns a small integer sequence chosen by the tab.

**Call relations**: When `_snapshot_oop` discovers a separate iframe target, it asks the tab for this sequence so the child frame can be rendered with a prefix such as `f1`.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how this page helper obtains the active connection to Chrome. It keeps snapshot and ref code independent from the concrete browser connection class.

**Data flow**: It reads the session object’s connection state and returns an object that can send CDP commands.

**Call relations**: Methods on `BrowserPage` call this when they need to ask Chrome for snapshots, element geometry, or frame attachment.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step for a newly attached browser session, especially for out-of-process iframes. Without this, a newly attached frame might not be ready for the same commands as the main page.

**Data flow**: It receives a session id for a browser target and performs whatever initialization the broader browser layer requires. It does not return page content.

**Call relations**: `BrowserPage._oop_session` calls it right after attaching to an iframe target, before storing and reusing that session.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It gives the snapshot code a simple fallback when Chrome omits a value or sends an unexpected type.

**Data flow**: It receives a value and a default number. If the value is already numeric, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll offsets and element bounds. `fetch_target` uses it to read the page’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute inside Chrome’s compact snapshot format. This is used for details like input type, image source, and iframe source.

**Data flow**: It receives Chrome’s shared string table, a flat list of attribute key/value indexes, and the attribute name to look for. It walks the pairs and returns the matching attribute text, or nothing if absent.

**Call relations**: `_parse_document` calls this while decoding DOM nodes that matter for rendering or frame discovery.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Decodes one document from Chrome’s DOM snapshot into simpler page data. It extracts element ids, geometry, useful attributes, iframe links, scroll-adjusted positions, and cursor style.

**Data flow**: It receives one raw document snapshot, Chrome’s shared string table, and the device pixel ratio. It validates and converts the compact arrays, builds geometry records keyed by backend node id, records iframe relationships, and returns a `_RawDoc`.

**Call relations**: `parse_snapshot` calls this for every document Chrome returned, then combines the results across iframe boundaries.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s full DOM snapshot into per-document data with coordinates in one shared page space. This is what lets elements inside same-process iframes appear in the right place.

**Data flow**: It receives the raw snapshot, the device pixel ratio, and an optional starting origin. It parses each document, follows iframe document links, accumulates iframe offsets, adjusts element bounds, ignores extension iframes, and returns `DocData` records.

**Call relations**: `fetch_target` calls this before it joins geometry with accessibility-tree nodes.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects all page data for one browser target, such as the main page or an out-of-process iframe. It combines DOM geometry with accessibility information into a `FrameSnapshot` tree.

**Data flow**: It receives a CDP connection, a browser session id, frame-prefix rules, and a base origin. It enables needed CDP domains, captures DOM geometry, reads device pixel ratio, parses documents, requests accessibility trees, joins child frames under iframe nodes, and returns the root frame snapshot.

**Call relations**: `BrowserPage._snapshot_target` calls this as the core snapshot step. It delegates DOM decoding to `parse_snapshot` and uses `Cdp.send` for all browser communication.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain text value from an accessibility-tree field. Chrome wraps many accessibility values in small objects, and this hides that wrapping.

**Data flow**: It receives a JSON value. If it is a dictionary with a `value`, it returns that value as text; otherwise it returns an empty string.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use it to read roles and names before deciding what to show.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `expanded`, `url`, or `hidden`. This gives renderers a uniform way to read state.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s property list, unwraps the stored value if needed, and returns the result or nothing.

**Call relations**: `_format_extras`, `render_page.render_node`, and `render_markdown.walk` call it whenever they need state, links, heading levels, or hidden status.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node can be skipped in the action tree. Empty containers often add noise without helping the model act.

**Data flow**: It receives a node, its role, and its name. If the role is a skippable container, has no name, and has no important state, it returns true; otherwise false.

**Call relations**: `render_page.render_node` uses this to make the output shorter while still keeping meaningful children.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long labels and values so page output stays readable. It uses an ellipsis to show that text was cut.

**Data flow**: It receives text and a maximum length. It returns the original text if it fits, or a shortened version ending in `…`.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use it before putting names or values into model-facing text.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a useful fallback name for an image from its source URL. This helps when an image has no accessibility label but its filename is meaningful.

**Data flow**: It receives an image source URL. It extracts the last path segment, keeps it only if it looks like a filename with an extension, truncates it if needed, and returns that text.

**Call relations**: `render_page.render_node` and `render_markdown.walk` call it when an image lacks a normal name.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the small state suffix shown after a rendered page item, such as `checked=true`, `disabled`, or `value="hello"`. This gives the model important context beyond role and label.

**Data flow**: It receives an accessibility node and optional geometry details. It reads input type, value, checked state, expansion state, disabled state, and other useful properties, filters unsafe or noisy URLs, truncates long values, and returns a formatted string.

**Call relations**: `render_page.render_node` appends this string to each visible line in the action-oriented page tree.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a given ref prefix. For example, an empty prefix means the main page, while `f1` points to a child frame.

**Data flow**: It receives the root frame and a prefix. It searches the frame tree recursively and returns the matching frame, or nothing if no frame uses that prefix.

**Call relations**: `render_page` uses this when asked to render only the subtree below a specific ref.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node id that corresponds to a browser backend DOM node id. This bridges the ref’s element id back to the accessibility tree.

**Data flow**: It receives a frame snapshot and a backend id. It scans the frame’s accessibility nodes and returns the matching accessibility node id, or nothing.

**Call relations**: `render_page` calls this after `_frame_by_prefix` when starting rendering from a specific ref.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders a frame snapshot as a compact action map for the model. Each line shows a role, optional name, stable ref, center coordinate, and important state.

**Data flow**: It receives a root snapshot, viewport size, optional model coordinate size, filter choice, depth limit, and optional starting ref. It scales coordinates, walks the accessibility tree, splices child frames under iframe nodes, filters noise or offscreen items when requested, and returns newline-separated text or nothing if a requested ref cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a snapshot. It uses helper routines such as `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when rendering from a specific element.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the rendered page tree. The center point is easier for a model or automation code to aim at than raw box corners.

**Data flow**: It receives optional geometry from the surrounding render process. If bounds exist, it scales the element center into model coordinates and returns text like `(x=...,y=...)`; otherwise it returns an empty string.

**Call relations**: The inner `render_node` helper calls it while building each output line in `render_page`.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree at the iframe node where it belongs. This makes frames read like part of one page instead of separate islands.

**Data flow**: It receives the current frame, an optional iframe backend id, and the current depth. If that backend id has a child frame with a root node, it starts rendering that child at the same place in the output.

**Call relations**: `render_page.render_node.descend` calls this after walking normal child nodes, so iframe content appears under its iframe.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether to show it, skip it, or show its children. This is the main line-by-line renderer for the action tree.

**Data flow**: It receives a frame, an accessibility node id, a depth, and the parent name. It avoids cycles, ignores hidden nodes, reads role/name/state/geometry, filters unhelpful nodes, writes one output line when appropriate, and then descends into children.

**Call relations**: `render_page` starts this helper at the root node or a requested ref. It calls helpers for accessibility values, properties, extras, image fallback names, skipping rules, and truncation.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering through a node’s children without duplicating traversal code. It also makes sure iframe children are added at the same logical spot.

**Data flow**: It receives the child depth and parent name to pass down. It renders each accessibility child, then asks `splice` to render any child frame attached to the current DOM node.

**Call relations**: `render_page.render_node` uses this both when a node is transparent and after it emits a visible line.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders the page snapshot as markdown for reading rather than acting. It preserves useful structure like headings, links, list items, paragraphs, and images.

**Data flow**: It receives the root frame snapshot. It walks the accessibility tree, gathers inline text into paragraphs, emits block-level content, follows iframe children, removes duplicates, and returns markdown text separated by blank lines.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. Its internal helpers collect and flush text as the tree walk moves between block and inline content.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished markdown block if it has real text and is not a duplicate of the previous block. This keeps the reading view cleaner.

**Data flow**: It receives text, trims whitespace, compares it to the last emitted block, and appends it to the block list only when useful.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` call this whenever a paragraph, heading, list item, image, or block name is ready.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline words into one paragraph block. It marks the boundary between flowing text and a new structural item.

**Data flow**: It reads the current inline text buffer. If the buffer has content, it joins the pieces with spaces, emits the paragraph, and clears the buffer.

**Call relations**: `render_markdown.walk` calls it before headings, blocks, list items, images, and iframe content so text does not run together.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Traverses the accessibility tree and converts nodes into markdown-like reading content. It chooses different output styles for headings, links, list items, images, and plain text.

**Data flow**: It receives a frame, node id, and parent name. It skips repeated or hidden nodes, reads role/name/properties, appends inline text or emits blocks, walks child nodes, flushes at block boundaries, and continues into child frames.

**Call relations**: `render_markdown` starts this helper at the root frame. It uses accessibility helpers and `_image_name` to turn browser data into readable markdown.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the action-oriented page tree for a tab. This is the high-level method other code can call when it wants refs, roles, names, coordinates, and state.

**Data flow**: It receives a tab, a filter type, and an optional ref. It takes a fresh snapshot, passes it to `render_page` with viewport and model sizing information, and returns the rendered text or nothing if a requested ref is invalid.

**Call relations**: This is a public-facing wrapper around `snapshot` and `render_page`, hiding the lower-level CDP and rendering steps.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading view of the current tab. This is useful when the model needs to understand page content rather than choose an element to interact with.

**Data flow**: It receives a tab. It takes a fresh snapshot, renders it with `render_markdown`, and returns the markdown text.

**Call relations**: This is the high-level entry to the markdown path, pairing `snapshot` with the markdown renderer.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a model-facing ref back into the frame information and backend element id needed for browser commands. It raises a clear hallucination error when the ref was invented or is stale.

**Data flow**: It receives a tab and ref text. It parses the ref, looks up the frame prefix in the tab’s registered frame map, and returns the matching `FrameNode` plus backend id; invalid refs become user-facing errors.

**Call relations**: `BrowserPage.ref_point` calls this before asking Chrome for element coordinates.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a clickable center point for a referenced element. It scrolls the element into view first so Chrome can report useful geometry.

**Data flow**: It receives a tab and ref. It resolves the ref, asks Chrome to scroll the element into view, reads its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer x/y coordinates.

**Call relations**: This method builds on `resolve_ref`, uses the browser connection for CDP geometry commands, and uses `_coord_float_or_default` to safely read coordinate numbers.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a complete fresh snapshot of the tab and records which frame prefixes are valid. This is the shared starting point for both tree and markdown rendering.

**Data flow**: It receives a tab. It snapshots the main target, clears the tab’s old ref-to-frame map, registers every frame in the new snapshot, and returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates the actual browser collection to `_snapshot_target` and ref bookkeeping to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and, if allowed, adds any out-of-process iframe snapshots below it. A target is a separately controlled browser page or frame.

**Data flow**: It receives the tab, session id, frame prefix, origin, and current frame depth. It calls `fetch_target` for the target, then attaches and snapshots separate iframe targets if the depth limit allows, and returns the completed frame snapshot.

**Call relations**: `snapshot` uses it for the main tab. `_snapshot_oop` uses it recursively for iframe targets.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Walks a snapshot tree looking for iframes that Chrome reported as separate targets, then attaches their snapshots. This fills gaps that a normal same-target snapshot cannot see.

**Data flow**: It receives a tab, a root frame snapshot, and the current depth. It scans frames with a stack, asks `_snapshot_oop` for each out-of-process iframe, and inserts successful child snapshots under the iframe backend id.

**Call relations**: `_snapshot_target` calls this after fetching the normal target snapshot.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe if Chrome can identify and attach to it. If anything fails, it logs the problem and leaves that frame out instead of breaking the whole page snapshot.

**Data flow**: It receives the tab, parent frame, iframe backend id, and depth. It asks Chrome which frame id belongs to the iframe node, gets or creates a session for that frame, computes the child origin from iframe bounds, and calls `_snapshot_target` for the child.

**Call relations**: `_attach_oop_frames` calls this for each separate iframe. It uses `PageTab.frame_seq` for the frame prefix and `_oop_session` for the browser session.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix in the tab so later refs can be resolved. Without this map, a ref like `f1e3` could not be turned back into the right browser session.

**Data flow**: It receives a tab and a frame snapshot. It stores the frame id, session id, and origin under that frame’s prefix, then repeats the same work for child frames.

**Call relations**: `snapshot` calls this after taking a fresh snapshot, before any rendered refs are handed to the model.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a reusable CDP session for an out-of-process iframe. It attaches to the frame only once and caches the session for later snapshots.

**Data flow**: It receives a frame id. It first checks the browser session cache; if absent, it asks Chrome to attach to the target, initializes the new session, stores it, and returns the session id. If attachment fails, it returns nothing.

**Call relations**: `_snapshot_oop` calls this before trying to snapshot a separate iframe target.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts a coordinate value from Chrome into a float, with a default for missing values. It accepts numbers and numeric strings, but rejects other shapes.

**Data flow**: It receives a JSON value and a default. Numbers become floats, non-empty strings are parsed as floats, `None` becomes the default, and other values raise a validation error.

**Call relations**: `BrowserPage.ref_point` uses this while averaging Chrome’s returned element corner coordinates.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file is like a small service desk for browser page content. Other parts of the system can ask it questions such as “show me the page structure,” “give me the readable text,” or “find the button or element related to this query.” Without this layer, each caller would need to know how to pick the right tab, talk to the page reader, limit very large results, and format search results safely.

The file defines two protocols, which are lightweight promises about what a browser session and page reader must be able to do. A page reader must provide a page “tree” — a structured text view of elements on the page — and markdown text. A browser session must be able to return a tab, return a page reader, and provide basic tab information.

The main class, `BrowserContent`, uses those promises. It converts incoming JSON-like arguments into useful values, such as a tab id or filter choice. It then asks the browser for the right page and asks the page reader for content. To protect callers from huge responses, it cuts page trees and text down to fixed maximum sizes and reports whether anything was truncated.

The search path is also here. It can either do a simple local text match against the page tree, or ask an outside completion function, such as a language model helper, to interpret the query and return likely matches.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This describes the page reader ability to return a structured view of a browser page. The structure is used when the system needs to understand elements on the page, such as buttons, links, and visible regions.

**Data flow**: It receives a browser tab, a filter telling what kind of elements to include, and optionally a reference to a specific element. A real implementation reads the page through the browser and returns a text tree, or returns nothing if the requested element cannot be found.

**Call relations**: This is a protocol method, meaning this file only states that page readers must provide it. `BrowserContent.tree` relies on this promise when callers ask to read a page tree or search within a page.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This describes the page reader ability to turn a browser page into readable markdown text. Markdown is plain text with simple markers for things like headings and links.

**Data flow**: It receives a browser tab. A real implementation reads the page content and returns a markdown string representing the page text.

**Call relations**: This is part of the page reader contract. `BrowserContent.get_page_text` uses it when a caller wants the human-readable text of the current or selected tab.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This describes how to get a browser tab from the current browser session. It allows callers to ask for a specific tab by id, or fall back to whatever tab the session considers current.

**Data flow**: It receives an optional tab id. A real implementation finds or selects the matching page tab and returns a `PageTab` object that other browser-reading code can use.

**Call relations**: This is a protocol method supplied by the browser session implementation. `BrowserContent.tree` and `BrowserContent.get_page_text` call it before they can read anything from the page.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This describes how the browser session provides an object that knows how to read page content. It separates browser tab selection from the actual work of extracting trees and text.

**Data flow**: It takes no input beyond the session itself. A real implementation returns a page reader object that supports the `tree` and `markdown` operations.

**Call relations**: This is used after `BrowserContent` has chosen a tab. `BrowserContent.tree` uses the returned reader for page trees, and `BrowserContent.get_page_text` uses it for markdown text.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This describes how to collect basic information about a tab, such as metadata that should travel with page text. It helps responses include context about where the text came from.

**Data flow**: It receives a tab-like object. A real implementation reads session or browser details about that tab and returns a JSON-style dictionary.

**Call relations**: This protocol method is used by `BrowserContent.get_page_text` after page text has been read. The returned tab information is merged into the final response.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This gets a structured text view of a page or of one referenced element within the page. It is the shared helper used by both page reading and searching.

**Data flow**: It starts with JSON-like arguments that may include a `tab_id` and `ref_id`. It converts the tab id into a number when possible, asks the browser session for that tab, turns a non-empty `ref_id` into an element reference, then asks the page reader for a tree using the requested filter. If the page reader cannot find the referenced element, it returns a clear message saying so; otherwise it returns the tree text.

**Call relations**: `BrowserContent.read_page` calls this when returning a page tree to a caller, and `BrowserContent.find` calls it before searching. It uses `_tab_id` to normalize the tab id, then hands the real reading work to the session’s page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns a page tree response suitable for an API caller. It also limits the result size so a very large page does not overwhelm the caller.

**Data flow**: It receives JSON-like arguments, reads an optional `filter`, and accepts only known choices: all elements, interactive elements, or viewport elements. It asks `BrowserContent.tree` for the matching page tree, cuts the text to the maximum allowed read length, and returns both the shortened tree and a flag showing whether text was cut off.

**Call relations**: This is a public-facing content operation. It delegates the actual page-tree retrieval to `BrowserContent.tree`, then shapes the result into a predictable response.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the readable text of a browser page, rather than the structured element tree. It is useful when the system needs the page’s written content, not its clickable layout.

**Data flow**: It receives JSON-like arguments that may include a `tab_id`. It converts that id, asks the browser session for the tab, asks the page reader for markdown text, cuts the text to the maximum allowed length, marks whether it was truncated, and adds tab information from the browser session.

**Call relations**: This operation works alongside `read_page`, but uses the page reader’s markdown path instead of the tree path. It uses `_tab_id` for tab selection and then combines page text with `tab_info` before returning.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page tree for elements related to a user’s query. It can either use a simple built-in matcher or, when provided, an outside completion helper that can make a smarter judgment.

**Data flow**: It receives JSON-like arguments and an optional completion function. It reads the required `query`, gets the full page tree through `BrowserContent.tree`, and then chooses a search method. Without a completion function, it parses matches directly from the tree and marks that there may be more if the result limit is reached. With a completion function, it sends the query and a shortened page tree to that helper, then resolves the helper’s reply back into page-tree matches. It returns the match items and a human-readable summary.

**Call relations**: This is the search entry point for browser content. It depends on `BrowserContent.tree` for the page structure, uses local find helpers for parsing or interpreting matches, and finishes by formatting the results for the caller.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id from JSON-style input into a Python integer, or returns nothing if no usable id was given. It lets callers pass tab ids as numbers or strings.

**Data flow**: It receives a value that may be an integer, floating-point number, string, or missing value. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything else becomes `None`, meaning “use the default tab.”

**Call relations**: `BrowserContent.tree` and `BrowserContent.get_page_text` call this before asking the browser session for a page. It keeps tab-id cleanup in one place so the main content methods stay focused on reading the page.

*Call graph*: called by 2 (get_page_text, tree).


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser accessibility tree is a text map of what is on a page: buttons, links, text boxes, their labels, hidden references, and sometimes screen coordinates. This file is the “reader” for that map. Without it, the rest of the system would have a hard time turning a plain user request like “find the submit button” into a real page element it can point at or click.

The file understands the line format produced elsewhere, such as a line with a role, a visible name, a reference like `[ref=e12]`, and optional coordinates. It first extracts these lines into small records containing the element reference, role, name, coordinates, and a lowercase copy of the original line for searching.

It supports two ways of finding things. One is a simple local search: split the query into words and keep tree entries whose line contains all meaningful words. The other is for replies from a language model: the model may say which references match, but this file checks those references against the original tree before trusting them. That is important because a model can invent or misremember a reference. Think of it like checking a claimed seat number against the actual theater seating chart before sending someone there.

Finally, it formats matches into readable lines for humans, including a note when there are more matches than were returned.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```


### Element input helpers
Specialized helpers translate form filling, file uploads, keyboard input, and coordinates into safe browser operations.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not expose form controls directly to the automation engine. Instead, the engine receives small page references, then has to ask the browser what those references mean and run the right browser-side action. This file is the bridge for that work.

The main class, BrowserForms, works with a browser session supplied from elsewhere. For every action, it first chooses the right tab, then turns a page reference into a real browser node. A node is like a street address for an element inside the page. Once it has that address, it uses the Chrome DevTools Protocol, or CDP (a control channel for talking to Chromium-based browsers), to inspect or change the element.

There are three user-facing abilities here. attached_sizes checks what file sizes a file input is actually holding, which helps tell whether an upload really finished rather than only having a filename. upload_file tells the browser to place local file paths into a file input. input runs a small JavaScript helper inside the page to set a value, tick a checkbox, choose a select option, or edit content, then fires normal input and change events so the page notices.

A key safety behavior is that bad or stale element references are turned into HallucinationError messages. That tells the caller to re-read the page and use a real reference instead of guessing.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It supplies the page or tab object that form actions will operate on.

**Data flow**: It receives an optional tab id. The concrete browser session uses that id to find the matching page, or chooses a default page when no id is given, then returns that page object.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input rely on this capability at the start of their work so they know which browser tab contains the form element.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It gives access to the CDP connection, the channel used to send low-level commands to the browser.

**Data flow**: It takes no extra input. The concrete browser session returns a connection object that can send commands such as resolving a page node or setting files on an input.

**Call relations**: BrowserForms uses this connection after it has found the target element. attached_sizes and input use it to turn a page node into a callable JavaScript object, while upload_file uses it to set files directly on a file input.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It runs a JavaScript function on a specific page object, such as a form field.

**Data flow**: It receives a browser session id, an object id for the page element, JavaScript source code, and optional argument values. The browser runs that JavaScript with the element as its target and returns a JSON-like result.

**Call relations**: BrowserForms.attached_sizes uses this to ask a file input what file sizes it contains. BrowserForms.input uses it to set a form value in a way that also triggers the page’s normal change notifications.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a human-facing page reference into the browser’s internal node information.

**Data flow**: It receives a page object and a reference string from the caller. It looks up that reference in the current page data and returns both the node/session information and the browser backend node id.

**Call relations**: Every BrowserForms action calls this after choosing a tab. Without this step, the code would only have a label from the page summary, not the actual browser element to change or inspect.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks the byte sizes of files currently attached to a file input element. It is used to confirm that an upload is real and complete, not just a name that has not reached the browser yet.

**Data flow**: It reads tab_id and ref from the input dictionary. It converts the tab id into a number if possible, opens that tab, resolves the reference to a browser node, asks CDP for a JavaScript object for that node, then runs a small script that reads this.files and returns each file’s size. The final output is a list of integer sizes; if the browser reply does not contain a proper list, it returns an empty list.

**Call relations**: This method is called when the automation engine needs evidence of what a file input actually contains. It uses _tab_id to normalize the tab choice, browser session methods to reach the element, and wire helpers such as as_str and as_map to validate incoming and returned data.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a file input on the page. It is the form-upload action used when a caller wants the browser to select files as if through a file picker.

**Data flow**: It reads tab_id, ref, and files from the input dictionary. It normalizes the tab id, checks that the reference is a string, checks that files is a list of strings, resolves the reference to a browser node, and sends CDP the DOM.setFileInputFiles command with those paths. If the browser rejects the command because the target is not a file input, it raises a clear HallucinationError. On success, it returns the same ref and the accepted file paths.

**Call relations**: This method sits between a high-level upload request and the low-level browser command. It calls _tab_id for tab selection, uses the browser session to find the target element, and translates CDP failures into a user-facing message that tells the caller to re-read the page and choose a real file input reference.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This fills or changes a form-like element on the page. It supports ordinary text fields, checkboxes, radio buttons, select boxes, and editable page regions.

**Data flow**: It reads tab_id, ref, and value from the input dictionary. It opens the requested tab, resolves the reference to a browser node, asks CDP to turn that node into a JavaScript object, then runs a helper script on the element. The script sets the right property for the element type, fires input and change events so the web page reacts, and returns the element’s resulting value or text.

**Call relations**: This method is used when the automation engine wants to enter data into the page. It uses _tab_id and the browser session to locate the element, then hands the actual field-changing work to BrowserFormSession.call_on. If the reference cannot be resolved, it raises HallucinationError so the caller knows the page information is stale or guessed.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns an optional tab id from incoming JSON-style data into a Python integer, or into None when no tab was provided.

**Data flow**: It receives a value that may be an integer, float, string, or something else. Integers pass through, floats are truncated to integers, non-empty strings are parsed as integers, and anything missing or unsupported becomes None.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input call this before asking the browser session for a page. It keeps tab-id cleanup in one place so each form action can use the same rules.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`domain_logic` · `request handling`

A browser viewport may be large, but an AI vision model may not actually see the screenshot at that full size. Some models shrink large images before reading them, and others report positions on their own fixed grid. This file is the small conversion tool that keeps those different spaces consistent.

It defines two simple data shapes: Size, meaning a width and height, and Coord, meaning an x and y point. The main idea is: before asking a model to reason about a screenshot, the system works out what size the model will effectively see. For Claude-family models, that means fitting the screenshot under Anthropic’s image limits so the server does not silently downscale it. For Gemini, coordinates are treated as a fixed 0-to-1000 square grid.

Once the model’s coordinate space is known, the file can convert both ways. If the model says “click at this point,” model_to_viewport stretches that point back onto the real browser viewport so the browser input lands in the right place. If the browser has a real pixel point and the system needs to express it in model terms, viewport_to_model performs the reverse conversion. Without this file, clicks and visual references could drift, especially on large screens or with models that use different coordinate rules.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: Works out the largest screenshot size that Claude-like vision models can receive without extra hidden downscaling by the provider. This helps the system know what image size the model is really seeing.

**Data flow**: It receives the browser viewport size. It first shrinks the width and height only if the longest side is above the allowed limit, then checks whether the total number of pixels is still too high and shrinks again if needed. It returns a new Size containing the final width and height.

**Call relations**: This is the fallback size calculator used by effective_model_size. Whenever no special model coordinate size is supplied, effective_model_size calls this function to turn the browser viewport into the screenshot size the model should reason over.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: Chooses the coordinate space that should be used for model reasoning. It uses an explicit model size if one is provided; otherwise it computes the screenshot size from the viewport.

**Data flow**: It receives the real browser viewport size and, optionally, a model-specific size. If the optional size exists, it returns that unchanged. If not, it calls compute_screenshot_dimensions and returns the calculated Size.

**Call relations**: This is the shared decision point used before converting coordinates. model_to_viewport and viewport_to_model both call it so they agree on the same model-space size before scaling points in either direction.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a point reported by the AI model into real browser pixels. This is what lets the system turn a model instruction like “click here” into an actual browser input location.

**Data flow**: It receives a point in model coordinates, the real viewport size, and optionally a custom model size. It asks effective_model_size what coordinate space the model is using, then scales the x value by the viewport-to-model width ratio and the y value by the viewport-to-model height ratio. It returns a new Coord in browser viewport pixels.

**Call relations**: This function is used at the point where model output needs to become browser action. It relies on effective_model_size first, then creates the translated Coord that can be passed on to input dispatch code elsewhere.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: Identifies whether a model uses a special fixed coordinate grid. In particular, it marks Gemini models as using a 1000 by 1000 coordinate space instead of screenshot pixels.

**Data flow**: It receives a model name, or no model name. If the name exists and contains “gemini” regardless of capitalization, it returns a Size of 1000 by 1000. Otherwise it returns None, meaning the caller should use the normal screenshot-size calculation.

**Call relations**: This function prepares the optional model_size value that other conversion functions can use. For Gemini, it supplies the fixed grid; for Claude-like behavior, it leaves the decision to effective_model_size and compute_screenshot_dimensions.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: Converts a real browser pixel point back into the coordinate system the AI model uses. This is useful when browser-side positions need to be described in the model’s terms.

**Data flow**: It receives a browser viewport point, the viewport size, and optionally a custom model size. It asks effective_model_size for the model coordinate space, then scales the point down or up from viewport pixels into that space. It returns a new Coord in model coordinates.

**Call relations**: This is the reverse partner of model_to_viewport. It uses the same effective_model_size decision so round-trip conversions are based on the same idea of what the model sees.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

This file is the keyboard translator for the browser automation layer. A person or higher-level tool may say “type hello” or “press Meta+A,” but Chrome needs a detailed event with fields such as the physical key code, visible text, modifier state, and whether the key came from the keypad. This file supplies that detail.

It starts with a US keyboard map. Think of it like a labeled diagram of a keyboard: each physical key has a browser code, a normal character, and sometimes a shifted character. It then builds a lookup table that accepts several ways of naming the same key, such as “Enter,” newline, or carriage return.

The file keeps a small `KeyboardState`, which remembers which modifier keys and physical keys are currently held down. That matters because pressing “a” while Shift is down should become “A,” and pressing a key twice without releasing it should be marked as auto-repeat.

The main public helpers create Chrome DevTools Protocol calls, often shortened to CDP calls. CDP is Chrome’s remote-control protocol. `key_down` and `key_up` make single press and release events. `press_combo` presses several keys in order and releases them in reverse, like a real shortcut. `type_text` sends short text key by key, but uses a direct text insertion call for longer text so large typing actions stay efficient.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds the practical key lookup table used by the rest of the file. It expands the raw keyboard layout so callers can find keys by physical code, visible character, shifted character, or common alias.

**Data flow**: It receives the base keyboard layout, where each physical key has its official browser details. For each key, it creates a `KeyDescription` for the normal form and, when needed, another form for the shifted version. It adds useful alternate names, such as mapping a character like `a` back to its key, and returns one combined lookup dictionary.

**Call relations**: This function is used during module setup to create `LAYOUT_CLOSURE`, the lookup table that later keyboard actions depend on. It uses `KeyDescription` to package browser-facing key details and `dataclasses.replace` to make small adjusted copies, such as the shifted version of a key.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Converts the currently held modifier keys into the number format Chrome expects. Modifier keys are keys like Shift, Control, Alt, and Meta.

**Data flow**: It receives a set of modifier names that are currently pressed. It checks each known modifier and adds its assigned bit value if present. It returns a single integer that represents the whole modifier state.

**Call relations**: Both `key_down` and `key_up` call this right before building a CDP keyboard event. It is the small translation step between this file’s easy-to-read set of names and Chrome’s compact numeric modifier field.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the browser-ready description for a requested key, taking the current keyboard state into account. It decides, for example, whether a key should produce `a` or `A`.

**Data flow**: It receives the current `KeyboardState` and a key name or character. It looks up that key in the prepared layout table. If the key is unknown, it raises a validation error. If Shift is currently held and the key has a shifted form, it uses that form. If other modifiers are held, it clears the text output because shortcuts usually should not type visible characters. It returns the final `KeyDescription`.

**Call relations**: `key_down` and `key_up` both call this before sending an event so they agree on the key’s code, text, and location. It relies on the layout table built by `_build_layout_closure` and uses `dataclasses.replace` when it needs a version with altered text.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up special Mac editing commands for a key plus modifiers. These commands tell Chrome about Mac-style text editing behavior, such as moving by word or selecting to the end of a line.

**Data flow**: It receives a physical key code and the set of currently pressed modifiers. It builds a shortcut name like `Shift+Meta+ArrowLeft`, checks the Mac command table, removes the trailing colon from command names, and filters out text insertion commands. It returns a list of command names for Chrome to include with the key event.

**Call relations**: `key_down` calls this only when the target platform is Mac. The result is added to the CDP key-down event so Chrome can treat synthesized shortcuts more like real Mac keyboard input.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome event for pressing a key down. It also updates the remembered keyboard state so later events know which keys and modifiers are being held.

**Data flow**: It receives the current keyboard state, the requested key, and whether the browser should behave like Mac. It asks `_description_for` what this key means right now, checks whether the physical key was already down, records the key as pressed, and records modifier keys such as Shift or Control. On Mac it also gathers editing commands. It returns one CDP call named `Input.dispatchKeyEvent` with all the key details Chrome needs.

**Call relations**: `press_combo` uses this to press each key in a shortcut, and `type_text` uses it for characters that can be represented by the keyboard layout. Inside, it calls `_description_for`, `_mac_commands`, and `modifiers_mask` to assemble a complete browser event.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome event for releasing a key. It also removes that key from the remembered pressed-key state.

**Data flow**: It receives the current keyboard state and a key name or character. It finds the key description, removes the key from the pressed-key set, and if it is a modifier, removes that modifier from the pressed-modifier set. It returns one `Input.dispatchKeyEvent` CDP call describing the key release.

**Call relations**: `press_combo` calls this after pressing a shortcut, releasing keys in reverse order like a person would. `type_text` calls it after each synthesized character key press. It uses `_description_for` and `modifiers_mask` so the release event matches Chrome’s expected format.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string such as `Ctrl+C` or `Meta+ArrowLeft` into a realistic sequence of key-down and key-up browser events. This is used when automation needs to trigger keyboard shortcuts rather than type plain text.

**Data flow**: It receives the current keyboard state, a plus-separated shortcut string, and the Mac flag. It splits the string into parts, normalizes common names like `ctrl` to `Control`, and rejects an empty shortcut. It presses each key in order, then releases the same keys in reverse order. It returns the full list of CDP calls.

**Call relations**: This is a higher-level helper built on `key_down` and `key_up`. Callers can ask for one shortcut, and this function expands it into the low-level events Chrome needs.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns text into browser input events. It uses detailed key events for short text, where key handlers and autocomplete may matter, and a faster direct insertion method for long text.

**Data flow**: It receives the current keyboard state, the text to type, and the Mac flag. If the text is longer than the configured limit, it returns one `Input.insertText` call containing the whole string. For shorter text, it walks character by character: characters known to the keyboard layout become key-down and key-up events, while unsupported characters are inserted directly. It returns the resulting list of CDP calls.

**Call relations**: This is the main helper for typing visible text. It calls `key_down` and `key_up` when a character can be faithfully synthesized as a key press, but falls back to Chrome’s direct text insertion call when that is safer or more efficient.

*Call graph*: calls 2 internal fn (key_down, key_up).


### Shared validation errors
Common browser automation errors report impossible or invalid model-produced page references.

### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `cross-cutting validation`

This file gives the project a clear name for one important kind of failure: the model has “hallucinated.” In this context, hallucination means the model supplied a value that looks like it should refer to something real, but does not actually exist in the browser automation world. For example, it might name an element reference that was never seen on the page.

The file imports `ValidationError`, which is a general error used when incoming data fails a check. `HallucinationError` is a more specific version of that error. This is useful because the rest of the system can tell the difference between ordinary bad input and a model inventing something. It is like having a form rejection stamp that says not just “invalid,” but “this item was never in the catalog.”

There are no functions here and no extra behavior inside the class. Its value is in classification. By raising or catching `HallucinationError`, other code can react more precisely: report the issue clearly, retry with better instructions, or stop an unsafe browser action before it uses an impossible reference.

# Rendered content perception and browser interaction primitives  `stage-12.4`

This stage is the system’s eyes and hands for rendered content. It sits in the main work loop, after a page or document is visible and before the agent decides or performs the next action. For documents, document_renderer.py asks a rendering service to make safe page images and text, then checks the result before use. For live browser pages, content.py collects page text, the element tree, or search matches. page.py turns that page view into model-friendly text and maps references like “e12” back to real screen points. find.py searches the accessibility tree, which is the browser’s list of visible controls such as buttons and fields, and formats usable matches. coordinate.py keeps model image coordinates aligned with real browser pixels. Once an action is chosen, fixup.py corrects common incomplete instructions. computer.py sends the final clicks, typing, scrolling, waiting, screenshots, and page commands to Chrome. keys.py builds the exact keyboard messages Chrome expects. forms.py fills fields, attaches files, and verifies that uploads really happened.

## Files in this stage

### Browser action orchestration
High-level browser automation requests are converted into concrete browser input and page-control operations with aligned pointer coordinates.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`orchestration` · `request handling`

This file is like the remote control for an automated browser. Other parts of the system send it a batch of actions in a simple form: click here, type this, scroll down, wait, or take a screenshot. BrowserComputer checks and adjusts those actions, converts coordinates from the model’s screen size to the real browser viewport, performs the actions through Chrome’s debugging protocol, waits for the page to settle, and returns a fresh screenshot plus a short written summary.

It also adds guardrails. If the agent scrolls repeatedly, it reminds it that page-reading tools may be better. If tab titles look like a sign-in page, it warns that the user must confirm before logging in. If a download begins, it tells the caller how to finish saving it. If the last action was a click, it draws a small blue mark on the returned screenshot so the caller can see exactly where it clicked.

The file also handles tricky browser behavior. Native dropdowns cannot be reliably operated by clicking options in this setup, so after a click it checks whether a dropdown was involved and suggests using form input instead. Overall, without this file, the system could understand requested browser actions but would not have a safe, consistent way to carry them out or report what happened.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the session contract. It promises that a browser session can provide the current tab, or a requested tab, so actions know where to run.

**Data flow**: It receives an optional tab number. The real session implementation uses that to choose a browser tab and returns an object with a browser session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this kind of method at the start of an action batch so it can aim all later clicks, typing, scrolling, and screenshots at the right tab.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session contract. It gives access to the browser connection used to send low-level commands to Chrome.

**Data flow**: It takes no extra input. The real session implementation returns a Chrome debugging protocol connection, which is the pipe used to ask the browser to dispatch input events, capture screenshots, or inspect the page.

**Call relations**: BrowserComputer uses this connection throughout a run, especially when waiting for the page to settle, sending mouse and keyboard events, checking dropdowns, and taking screenshots.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is part of the session contract. It promises a way to describe the current tab in the final response.

**Data flow**: It receives a tab object. The real implementation reads tab details, such as identifiers or page state, and returns them as a JSON-like dictionary.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the response includes both what happened and which tab it happened in.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This is part of the session contract. It provides the titles of open tabs so the system can spot risky situations like sign-in pages.

**Data flow**: It takes no direct input. The real implementation reads the browser’s open tab titles and returns them as plain strings.

**Call relations**: BrowserComputer.run uses these titles before building its final message, then passes them to sign_in_warning to decide whether to add a safety reminder.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the session contract. It promises a way to run a small JavaScript function on a specific page object.

**Data flow**: It receives a browser session id, a page object id, JavaScript code, and optional arguments. The real implementation runs that code in the browser and returns the result as a JSON-like dictionary.

**Call relations**: BrowserComputer._select_reminder uses this after a click to inspect a native dropdown and learn which options it contains.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This is part of the session contract. It turns a page reference string, such as one returned by a page-reading tool, into the browser node needed for direct browser commands.

**Data flow**: It receives the current tab and a reference string. The real implementation looks up the matching page element and returns both its browser node and backend element id.

**Call relations**: BrowserComputer.act uses this for scroll_to actions, where the browser needs an actual element id before it can scroll that element into view.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This is part of the session contract. It finds a clickable screen point for a referenced page element.

**Data flow**: It receives the current tab and a reference string. The real implementation locates the element and returns a viewport coordinate where input should be aimed.

**Call relations**: BrowserComputer.point calls this when an action names an element by reference instead of giving an explicit coordinate.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main action-batch runner. It receives a request containing browser actions, performs them in order, waits for the page to settle, and returns the updated tab information, a summary, and a screenshot.

**Data flow**: It starts with JSON-like input containing a tab id and a list of actions. It chooses the tab, validates and adjusts the actions, runs them in batches, collects messages and warnings, drains any new download notices, captures a screenshot, optionally marks the last click, and returns a JSON-like response with tab info, output text, last click location, and screenshot data.

**Call relations**: This is the top-level method other request-handling code would call for computer-style browser control. It delegates each individual action to BrowserComputer.act, asks _select_reminder about dropdown clicks, converts the last click with _to_model, calls sign_in_warning for safety messaging, and uses int_or_none to interpret the requested tab id.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This performs one browser action, such as clicking, typing, pressing a key, waiting, scrolling, or scrolling an element into view. It also creates a short human-readable message describing what it did.

**Data flow**: It receives the active tab and one validated action. It first works out the target point if the action needs one, then sends the right browser input commands or waits, and finally returns a message plus the viewport point if the action ended at a meaningful screen location.

**Call relations**: BrowserComputer.run calls this for every action in the request. Depending on the action type, it hands work to _click, _drag, _scroll, _dispatch, _to_viewport, _to_model, require_point, and require_coord.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This finds where on the browser screen an action should happen. It supports both element references and raw coordinates.

**Data flow**: It receives a tab and an action. If the action has an element reference, it asks the browser session for that element’s point; if it has a model-space coordinate, it converts it to viewport-space; if neither exists, it returns no point.

**Call relations**: BrowserComputer.act calls this before deciding how to perform an action. It uses _to_viewport when the action gives a coordinate instead of a page reference.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts coordinates from the model’s imagined screen size to the browser’s actual viewport size. This matters because the agent may think in one resolution while Chrome is displaying another.

**Data flow**: It receives an x and y pair in model coordinates. It scales that point using the model size and viewport size, then returns the matching x and y inside the real browser viewport.

**Call relations**: BrowserComputer.act uses this for drag starting points and default scroll locations. BrowserComputer.point uses it when an action supplies a direct coordinate.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This converts real browser viewport coordinates back into the model’s coordinate system. It lets the final messages describe locations in the same coordinate language the caller uses.

**Data flow**: It receives an x and y pair from the browser viewport. It scales that point back into the model screen size and returns the converted x and y.

**Call relations**: BrowserComputer.act uses this when reporting where clicks and drags happened. BrowserComputer.run uses it when returning the final last_click field.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This checks whether a recent click landed on a native HTML select dropdown and, if so, prepares a helpful reminder. Native dropdown options cannot be clicked normally in this browser-control setup, so the caller needs different instructions.

**Data flow**: It receives the active tab and the clicked viewport point. It asks the browser which page element is at that point, walks up to see whether it is inside a select element, reads a sample of its options and its browser reference, and returns a reminder string; if anything cannot be inspected safely, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action in a batch. It delegates the final wording to select_reminder.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This sends a prepared list of keyboard-related browser commands. It is used after higher-level helpers have turned text or key combinations into low-level browser events.

**Data flow**: It receives a tab and a list of Chrome debugging protocol calls. It sends each method and parameter set to the browser session for that tab, changing the page as those keyboard events take effect.

**Call relations**: BrowserComputer.act calls this for type and key actions after type_text or press_combo has built the needed event list.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This sends one mouse event to the browser, such as move, press, release, or wheel. It is the small shared pipe used by clicks, drags, and scrolling.

**Data flow**: It receives the active tab and a dictionary of mouse-event details. It sends those details to Chrome as an Input.dispatchMouseEvent command, causing the browser to react as if a real mouse event happened.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll call this repeatedly to build complete user gestures out of individual mouse events.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This performs a left, right, double, or triple click at a viewport point. It includes currently pressed keyboard modifier keys, such as Shift or Control, so modified clicks behave correctly.

**Data flow**: It receives a tab, x and y viewport coordinates, a mouse button name, and a click count. It moves the mouse to the point, then sends matching press and release events the requested number of times.

**Call relations**: BrowserComputer.act calls this for click actions. Internally it sends each individual mouse movement, press, and release through _mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This performs a left-button drag from one viewport point to another. It moves in several small steps because many web pages only recognize dragging after seeing intermediate movement.

**Data flow**: It receives a tab, starting coordinates, and ending coordinates. It moves to the start, presses the left mouse button, sends several in-between move events while holding the button, and then releases at the end.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. The function builds the gesture from repeated calls to _mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This performs a mouse-wheel scroll at a specific viewport point. It can scroll vertically or horizontally depending on the wheel deltas it receives.

**Data flow**: It receives a tab, a target x and y point, and horizontal and vertical scroll amounts. It moves the mouse to that point and sends a wheel event with those amounts, changing the page’s scroll position if the page accepts it.

**Call relations**: BrowserComputer.act calls this for scroll actions after calculating direction and distance. It sends the move and wheel events through _mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This looks for signs that the browser may be on a login, sign-up, or registration page. If it finds one, it returns a safety warning that the user must confirm before signing in.

**Data flow**: It receives a list of tab titles. It lowercases them, searches for sign-in-related words, and returns the warning text if any title matches; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this while building the final response so sign-in safety guidance appears alongside the action result.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This writes the user-facing reminder for native dropdowns. It explains that clicking dropdown options will not work here and tells the caller to use a form-input tool instead.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total number of options. It formats a concise instruction, including the known reference when available and showing only a capped list of option names with a count of any hidden extras.

**Call relations**: BrowserComputer._select_reminder calls this after it has inspected the clicked select element and gathered its option information.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This draws a small blue dot on a screenshot at the last clicked point. It makes the returned screenshot easier to understand, like putting a sticker on a map to show where the action happened.

**Data flow**: It receives a base64-encoded screenshot and a viewport coordinate. It decodes the image, draws a translucent circle over the click point, saves the image back as JPEG, base64-encodes it again, and returns the updated screenshot string.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a recent click. It is run in an executor because image editing is ordinary CPU work rather than browser I/O.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This turns a loose JSON value into an integer tab id when possible. It lets callers provide the tab id as a number or a non-empty string.

**Data flow**: It receives a JSON-like value or nothing. If the value is an int, float, or non-empty string, it converts it to an int; otherwise it returns None.

**Call relations**: BrowserComputer.run calls this before asking the browser session for a page, so missing or blank tab ids simply mean “use the default tab.”

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This enforces that an action has a target point when one is required. It turns a missing coordinate or reference into a clear validation error.

**Data flow**: It receives a possible point and the action name. If the point exists, it returns it unchanged; if not, it raises a ValidationError explaining that the action needs a coordinate or reference.

**Call relations**: BrowserComputer.act calls this before click, right-click, double-click, triple-click, and drag end actions, so invalid requests fail before any browser input is sent.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This enforces that a required coordinate field is present. It is used when an action needs a separate starting point, such as a drag.

**Data flow**: It receives a possible coordinate and the field name to report. If the coordinate exists, it returns it; if not, it raises a ValidationError naming the missing field.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions before converting the drag start coordinate into viewport space.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### `extensions/browser/ufo_ext_browser/bua/coordinate.py`

`util` · `cross-cutting`

When an AI model looks at a browser screenshot, it may not be seeing the browser at its original size. Some models shrink large images before reading them, and some report positions on their own fixed grid instead of normal pixels. If the system sent a click using the model’s raw coordinates without translating them, it could click the wrong place on the page.

This file is the small measuring tool that prevents that mismatch. It defines simple Size and Coord records: one for width and height, and one for x and y positions. It knows the image-size limits used for Claude-style vision input, so it can predict the largest screenshot size the model will effectively see without extra server-side shrinking. It also knows that Gemini-style models use a fixed 0-to-1000 coordinate grid.

The main flow is like resizing a map before using its pins. First, the file figures out the model’s map size. Then it scales a point from that map back to the browser viewport, or the other way around. This matters anywhere the AI describes a point on the screen and the browser automation must turn that point into a real mouse or touch action.

#### Function details

##### `compute_screenshot_dimensions`  (lines 22–33)

```
def compute_screenshot_dimensions(viewport: Size) -> Size
```

**Purpose**: This function predicts the screenshot size that a Claude-style vision model can receive without the service shrinking it further. It is used so the rest of the system can reason in the same pixel space the model actually sees.

**Data flow**: It takes the browser viewport size as width and height. It first scales the image down if either side is longer than the allowed long edge, then shrinks it again if the total number of pixels is still too high. It returns a new Size with the fitted width and height.

**Call relations**: When no explicit model coordinate size is provided, effective_model_size calls this function to decide the model’s working image size. It creates and returns the Size object that later coordinate conversions rely on.

*Call graph*: called by 1 (effective_model_size); 1 external calls (__init__).


##### `effective_model_size`  (lines 36–38)

```
def effective_model_size(viewport: Size, model_size: Size | None=None) -> Size
```

**Purpose**: This function chooses the coordinate space the model is using. If a caller already knows that space, it uses it; otherwise it computes the screenshot size from the viewport.

**Data flow**: It receives the viewport size and, optionally, a model-specific size. If the optional size is present, it passes that through unchanged. If not, it sends the viewport to compute_screenshot_dimensions and returns the fitted screenshot size.

**Call relations**: Both model_to_viewport and viewport_to_model call this before doing their scaling. It acts as the shared first step that makes sure both conversion directions use the same idea of the model’s coordinate space.

*Call graph*: calls 1 internal fn (compute_screenshot_dimensions); called by 2 (model_to_viewport, viewport_to_model).


##### `model_to_viewport`  (lines 41–47)

```
def model_to_viewport(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a point reported by the AI model into real browser viewport pixels. It is what makes a model-selected point usable for actions such as clicking or moving the pointer.

**Data flow**: It takes a coordinate from the model, the actual browser viewport size, and optionally the model’s coordinate size. It finds the effective model size, compares that to the viewport, scales x and y separately, and returns a Coord in browser pixels.

**Call relations**: This function calls effective_model_size to learn the scale of the model’s map, then builds a new Coord for the browser’s map. It is the outward conversion path from model reasoning to browser input dispatch.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


##### `model_coordinate_space`  (lines 50–55)

```
def model_coordinate_space(model: str | None) -> Size | None
```

**Purpose**: This function identifies special coordinate rules for a named model. In particular, it recognizes Gemini models, which report points on a fixed 1000 by 1000 grid instead of screenshot pixels.

**Data flow**: It receives a model name or no model name. If the name contains “gemini” in any letter case, it returns a Size of 1000 by 1000. Otherwise, it returns None, meaning the caller should use the normal screenshot-size logic.

**Call relations**: This function supplies an optional model size that can be passed into the conversion functions. It creates a Size only for Gemini-style behavior; for other models it leaves effective_model_size to compute the screenshot dimensions.

*Call graph*: 1 external calls (__init__).


##### `viewport_to_model`  (lines 58–64)

```
def viewport_to_model(coord: Coord, viewport: Size, model_size: Size | None=None) -> Coord
```

**Purpose**: This function converts a real browser viewport point back into the coordinate system used by the model. It is useful when the system needs to describe or compare browser positions in the model’s own scale.

**Data flow**: It takes a browser pixel coordinate, the viewport size, and optionally the model’s coordinate size. It finds the effective model size, scales x and y from viewport pixels into that model space, and returns a new Coord.

**Call relations**: Like model_to_viewport, it starts by calling effective_model_size so it uses the right scale. It then performs the reverse trip: from the browser’s real pixel map back to the model’s map.

*Call graph*: calls 1 internal fn (effective_model_size); 1 external calls (__init__).


### Rendered content capture
Documents and live browser tabs are transformed into safe, readable content for model consumption.

### `core/src/ufo/runtime/media/document_renderer.py`

`io_transport` · `request handling`

This file exists so the system can show documents in a way that is useful to both people and tools: each page becomes a PNG image, and any readable text is collected alongside it. Without this layer, callers would either have to understand many document formats themselves, or trust raw output from an outside renderer without size and safety checks.

The main piece is `DocumentRenderer`. It takes document bytes, asks a service called `ufo-preview` to render a limited range of pages, and receives a zip bundle in return. Think of the renderer service like a print shop: this file hands over the document and asks for only a few pages, not the whole book.

The code is deliberately strict. It caps the input size, the number of pages, the downloaded bundle size, the manifest size, each image size, and the total image data size. It also checks that the service returned exactly the pages requested, in the right order, with sensible page numbers and dimensions. Each image must really look like a PNG file before it is accepted.

The final result is a plain dictionary containing the original path, document type, combined text, page count information, base64-encoded page images, and a reminder to inspect the rendered pages for visual problems.

#### Function details

##### `DocumentRenderer.render`  (lines 60–107)

```
async def render(self, path: str, kind: str, content: bytes, start_page: int, limit: int) -> dict[str, object]
```

**Purpose**: This is the public async method that asks the external preview service to render a document. It limits how much work can be requested, sends the file over HTTP, collects the returned zip bundle, and then passes that bundle to the checker and unpacker.

**Data flow**: It receives a file path, a document kind, the raw document bytes, and a requested page range. It first rejects documents that are too large, then adjusts the requested start page and page count to safe limits. It builds a small JSON request, sends that request and the document bytes to the render service, and reads the response in chunks so it can stop if the bundle grows too large. If the service reports an error, it reads a short error message and raises a clear exception. On success, it sends the downloaded bundle to `_unpack` in a background thread and returns the cleaned preview dictionary that `_unpack` produces.

**Call relations**: This method is the front door for document rendering. Callers use it when they need a paginated preview of a document. It relies on `httpx.AsyncClient` and `httpx.Timeout` to talk to the rendering service, `json.dumps` to format the render request, and `asyncio.to_thread` to run `_unpack` without blocking the async event loop while zip files and images are checked.

*Call graph*: 4 external calls (to_thread, AsyncClient, Timeout, dumps).


##### `DocumentRenderer._unpack`  (lines 109–196)

```
def _unpack(self, path: str, kind: str, start_page: int, limit: int, bundle: bytes) -> dict[str, object]
```

**Purpose**: This method opens and validates the zip bundle returned by the preview service. It turns trusted page image files into base64 text strings and combines the extracted page text into one readable block.

**Data flow**: It receives the original path and document kind, the safe page range that was requested, and the raw zip bundle bytes. It opens the bundle, reads `manifest.json`, and validates that the manifest matches the request and describes sensible, consecutive pages. It then checks that the archive contains only the manifest and the expected page PNG files. For each page, it enforces image size limits, confirms the file begins like a PNG, base64-encodes the image so it can travel inside JSON-like data, and collects non-empty text. It returns a dictionary with page images, text, total page count, next-page information, and a quality reminder.

**Call relations**: This method is called by `DocumentRenderer.render` after the HTTP response has been fully received. It does the local verification work that protects the rest of the system from malformed, oversized, or surprising renderer output. It uses `io.BytesIO` and `zipfile.ZipFile` to read the in-memory zip archive, and `base64.b64encode` to convert binary PNG images into text-safe strings for the final result.

*Call graph*: 3 external calls (b64encode, BytesIO, ZipFile).


### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file is a small bridge between browser automation and higher-level tools that need to understand a webpage. A browser page is messy: it has tabs, elements, visible text, hidden parts, and sometimes too much content to safely return in one answer. This file gives the rest of the project simple, predictable commands for reading that content.

The main class, BrowserContent, works with a browser session. The session knows how to choose a tab, read the page, and report tab details. BrowserContent asks for the right tab, asks a page reader for either an element tree or markdown-style text, and then trims very large results so callers are not flooded with huge responses.

There are two reading styles. read_page returns a structured tree of page elements, optionally filtered to things like interactive controls or the visible viewport. get_page_text returns the page as text, closer to what a person would read. find searches the tree for matching elements. It can do this with simple built-in matching, or it can ask an outside completion tool, such as a language model, to interpret the query and pick likely matches.

The helper _tab_id accepts tab identifiers that arrive as JSON values, such as numbers or strings, and turns them into a normal integer tab id. Without this file, callers would have to repeat all this tab picking, page reading, size limiting, and search formatting themselves.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a contract for objects that can read a browser tab as an element tree. The tree is a text description of the page’s structure, useful for finding buttons, links, fields, and other page parts.

**Data flow**: It receives a browser tab, a filter choice such as all elements or only interactive ones, and optionally a reference to a specific element. An implementation reads the tab and returns the matching tree text, or returns nothing if the requested referenced element cannot be found.

**Call relations**: BrowserContent.tree relies on this capability through the browser session’s page_reader. This file defines the promise, while another part of the system supplies the real browser-specific reader.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a contract for objects that can read a browser tab as ordinary text. It is meant for cases where the caller wants page content, not the full page structure.

**Data flow**: It receives a browser tab, reads the visible or meaningful page content from that tab, and returns it as a markdown-style string, which is plain text with simple formatting conventions.

**Call relations**: BrowserContent.get_page_text calls this through the session’s page_reader. The protocol lets BrowserContent stay independent from the details of how a particular browser is inspected.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is a contract for choosing which browser tab should be used. It lets callers either request a specific tab or fall back to the current/default tab.

**Data flow**: It receives an optional tab id. The implementation uses that id, or its own default-selection rule when no id is given, and returns a PageTab object representing the chosen browser tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before reading content. This keeps tab selection in the browser session layer instead of mixing it into content-reading code.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is a contract for getting the object that knows how to extract content from a tab. It separates the idea of a browser session from the details of reading page structure and text.

**Data flow**: It takes no input beyond the session itself. It returns a BrowserContentPageReader, which can then produce an element tree or markdown text for a selected tab.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text use this after they have selected a tab. The session provides the reader, and BrowserContent uses it to perform the actual page reading.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is a contract for reporting extra information about a browser tab, such as details the caller may need alongside the page text. It helps returned page text carry useful context about where it came from.

**Data flow**: It receives a tab-like object, reads session-specific information about that tab, and returns a JSON-style dictionary of details.

**Call relations**: BrowserContent.get_page_text calls this after reading the page text, then merges the tab details into the response. The protocol leaves the exact tab metadata to the browser session implementation.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This returns a text version of a browser page’s element tree. Callers use it when they need to inspect the page structure, possibly narrowed to a referenced element or a kind of element.

**Data flow**: It reads the optional tab_id and ref_id from the incoming JSON-like arguments. It converts the tab id into an integer when possible, asks the browser session for that tab, asks the page reader for a tree using the requested filter and reference, and returns the tree text. If a requested referenced element is not found, it returns a clear message saying so.

**Call relations**: BrowserContent.read_page calls this when serving a page-tree read request, and BrowserContent.find calls it before searching. It hands tab-id conversion to _tab_id and delegates actual browser reading to the session’s page reader.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the public-style operation for reading a page as an element tree. It also protects callers from receiving an overly large response by cutting the tree to a fixed maximum size.

**Data flow**: It reads a filter value from the arguments and accepts only known choices: all, interactive, or viewport. It asks BrowserContent.tree for the corresponding page tree, then returns a dictionary containing the tree text shortened to the allowed limit and a flag saying whether anything was cut off.

**Call relations**: This function sits above BrowserContent.tree as a safer, request-friendly wrapper. It is the place where raw tree reading is turned into a bounded response suitable for transport back to a caller.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This returns the page as readable text instead of an element tree. It is useful when the caller wants the content of the page, like reading an article or understanding visible text.

**Data flow**: It reads an optional tab_id from the arguments, converts it with _tab_id, asks the browser session for the chosen tab, and asks the page reader for markdown-style text. It returns that text shortened to the maximum allowed size, a truncation flag, and extra tab information from the browser session.

**Call relations**: This is a sibling to read_page: both read from a tab, but this one uses the page reader’s markdown path and enriches the result with tab_info. It depends on the browser session for tab selection, page reading, and tab metadata.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This searches the page’s element tree for things matching a user’s query. It can use simple built-in matching, or an optional completion tool that can make a more flexible judgment about what the query means.

**Data flow**: It reads the query from the input arguments and checks that it is a string. It gets the full page tree through BrowserContent.tree. If no completion tool is provided, it parses matches directly from the tree. If a completion tool is provided, it sends the query and a shortened version of the tree to that tool, then resolves the tool’s reply back against the real tree. It returns the matching items and a human-readable summary.

**Call relations**: This function builds on BrowserContent.tree because searching needs the page structure first. It then hands the search work either to parse_tree_matches for local matching or to the completion flow using the find prompt and resolve_find_reply, and finally uses format_matches to summarize the result.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id received from JSON into a normal integer, or returns nothing when no usable tab id was supplied. It makes the rest of the file accept tab ids sent as numbers or strings.

**Data flow**: It receives a JSON value that may be an integer, a floating-point number, a non-empty string, or something else. Integers are returned directly, floats and non-empty strings are converted to integers, and missing or unsupported values become None.

**Call relations**: BrowserContent.tree and BrowserContent.get_page_text call this before asking the browser session for a tab. It keeps tab-id cleanup in one place, so the reading functions can simply pass along either a valid integer id or None.

*Call graph*: called by 2 (get_page_text, tree).


### Element discovery
Browser accessibility and page representations are searched and converted into stable references or click targets.

### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser accessibility tree is like an inventory of what is on a web page: each line can say there is a button, link, text box, or other element, often with a name, a reference ID, and screen coordinates. This file is the reader for that inventory. It knows the simple line format produced elsewhere and extracts the useful parts: the element’s role, visible name, unique reference, and position.

The file supports two ways of finding things. First, it can do a simple local search: break a query into words and keep tree entries whose line contains all those words. Second, it can check a reply from a language model that was asked to find matching elements. That check is important because the model might invent or misremember a reference. This file only accepts references that actually appear in the tree, then fills in the trusted role, name, and coordinates from the tree itself.

Finally, it formats the matches into short readable lines. The overall job is safety and clarity: find likely elements, keep search results tied to real page entries, limit the number of results, and tell the user when more matches may exist.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the text accessibility tree and turns each valid element line into a small structured record. It is used when later code needs reliable fields such as reference ID, role, name, and coordinates instead of raw text.

**Data flow**: It receives the full tree as a string. It looks at the tree one line at a time, keeps only lines that look like element entries and contain a reference ID, extracts the role, optional name, optional x/y coordinates, and a lowercase copy of the line, then returns a list of dictionaries. If a line has no coordinates, it uses "0,0" as a fallback.

**Call relations**: This is the shared first step for both local searching and reply checking. parse_tree_matches calls it before comparing query words against tree lines, and resolve_find_reply calls it to build the trusted list of real references that a model reply is allowed to mention.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This helper builds the standard match record returned by search functions. It keeps every match shaped the same way, with the element details plus a short explanation.

**Data flow**: It receives one parsed tree entry and a reason string. It copies the entry’s reference ID, role, name, and coordinates, adds the reason, and returns a new dictionary representing one search result. It does not change the original entry.

**Call relations**: parse_tree_matches uses it when a tree entry matches the query directly. resolve_find_reply uses it after it has confirmed that a reference from a reply really exists in the tree.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree without asking another system to reason about it. It is useful for quick matching when the query words can be found directly in the tree text.

**Data flow**: It receives the tree text and the user’s query. It lowers the query, pulls out simple letter-and-number terms longer than one character, parses the tree into entries, and keeps entries whose lowercase tree line contains every query term. Each kept entry becomes a match record with an empty reason. The result is capped at the file’s maximum number of matches.

**Call relations**: It starts by using tree_entries to make the raw tree searchable as records. For every entry that passes the word check, it calls _match_payload to produce the common result shape used elsewhere in the file.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function checks and cleans up a search reply, typically one written as references plus explanations. Its main purpose is to prevent made-up or stale element references from being trusted.

**Data flow**: It receives a reply string and the original tree text. It parses the tree into a lookup table by reference ID, then reads the reply line by line. It ignores blank lines, stops on NO_MATCHES, notes whether the reply says MORE, extracts a reference from each result line, and keeps it only if that reference exists in the tree and has not already been used. For accepted results, it takes the official role, name, and coordinates from the tree and pairs them with the reply’s reason. It returns both the cleaned match list and a true-or-false flag saying whether more matches exist.

**Call relations**: This function sits between an outside finder reply and the rest of the browser automation flow. It calls tree_entries to know what references are real, then calls _match_payload to build safe results. Anything not grounded in the actual tree is silently dropped.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns a list of match records into readable text. It is used when results need to be shown back to a person or passed along in a compact, understandable form.

**Data flow**: It receives a list of matches and an optional flag saying whether more matches exist. For each match, it creates a line with the reference ID, role, name, and coordinates, and adds the reason if one is present. If the more flag is true, it adds a final note telling the reader to refine the query. It returns one joined string.

**Call relations**: This is the presentation step after matches have already been found or verified. Unlike parse_tree_matches and resolve_find_reply, it does not inspect the tree; it simply turns prepared match data into human-readable output.


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is not naturally easy for a model to use. The browser has a visual layout, a document tree, accessibility information, nested frames, and sometimes frames running in separate browser targets. This file joins those views into one practical snapshot.

It asks Chrome, through the Chrome DevTools Protocol (CDP, a browser control API), for two main things: the DOM snapshot, which gives element geometry and attributes, and the accessibility tree, which gives human-facing roles and names such as “button”, “Search”, or “heading”. It then stitches child frames under their iframe elements, including special out-of-process iframes that Chrome exposes separately.

From that snapshot it can render two views. `render_page` produces a compact action-oriented tree with roles, names, stable references, coordinates, and useful state like checked or disabled. `render_markdown` produces a calmer reading view, closer to an article outline. The references are important: they are the bridge between “the model saw this button” and “the automation can click that exact browser node later”. Without this file, the model would either see messy raw browser data or plain text with no reliable way to act on the page.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Breaks a browser element reference into the frame part and the element id part. It is used to check that a model-supplied reference has the expected shape before the system trusts it.

**Data flow**: It receives a text reference such as `e12` or `f1e3`. It matches that text against the expected pattern, then returns the frame prefix and numeric backend node id; if the text does not match, it returns nothing.

**Call relations**: When the system renders only part of a page, `render_page` uses this to find the requested starting element. When the model asks to act on a reference, `BrowserPage.resolve_ref` uses this first so invented or malformed references can be rejected clearly.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Describes the one operation expected from a Chrome DevTools Protocol connection: send a command to the browser and receive a JSON-like reply. This is a protocol definition, not the actual network implementation.

**Data flow**: It takes a command name, optional command parameters, and optionally a browser session id. The concrete connection sends that command to Chrome and returns the response data.

**Call relations**: Snapshot collection in `fetch_target` relies on this shape to ask Chrome for accessibility trees, DOM snapshots, device scale, and node details. Other browser-control code, such as settle logic, can use the same kind of connection.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a stable short number for a frame id so references can use compact prefixes like `f1`. This keeps frame-aware element references readable.

**Data flow**: It receives a browser frame id. The tab object returns the sequence number assigned to that frame.

**Call relations**: `BrowserPage._snapshot_oop` calls this when it finds a separately running iframe and needs to build the prefix that will later appear in rendered references.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Describes how a browser page session exposes its CDP connection. It lets this file talk to the browser without caring about the concrete connection class.

**Data flow**: It reads the session object and returns a connection object that can send browser commands.

**Call relations**: Browser page methods call this before collecting snapshots, resolving references, or attaching to out-of-process frames.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Describes the setup step needed after attaching to a new browser session. This makes sure a newly attached frame target is ready for the same commands as the main page.

**Data flow**: It receives a browser session id and performs whatever initialization the concrete browser session requires. It returns when that session is ready.

**Call relations**: `BrowserPage._oop_session` uses this after attaching to an out-of-process iframe, before that iframe is snapshotted.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number, with a fallback when the value is missing or not numeric. It keeps browser data parsing from failing on harmless absent fields.

**Data flow**: It receives a value and a default number. If the value is an integer or float, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll offsets and rectangle numbers. `fetch_target` uses it to read the browser’s device pixel ratio.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute in Chrome’s compact snapshot format. Chrome stores attribute names and values as indexes into a shared string table, so this helper makes that readable.

**Data flow**: It receives the shared string list, a flat list of attribute key/value indexes, and the desired attribute name. It scans pairs until it finds the name and returns the matching value text, or nothing if absent.

**Call relations**: `_parse_document` uses it when it wants input types, image sources, and iframe sources from the DOM snapshot.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Turns one raw document from Chrome’s DOM snapshot into useful page data: element ids, boxes on screen, selected attributes, iframe links, and pointer-cursor hints. It is the first cleanup pass over Chrome’s dense snapshot format.

**Data flow**: It receives one document record, the shared string table, and the device pixel ratio. It validates and decodes arrays from Chrome, converts coordinates into CSS pixels, subtracts scroll position, records geometry by backend node id, and notes which DOM nodes are embedded documents or iframes. It returns a `_RawDoc` with this organized information.

**Call relations**: `parse_snapshot` calls this once for each document inside a captured DOM snapshot. It depends on small helpers like `_float` and `_attr` so the larger parsing flow stays understandable.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Combines all document records from a DOM snapshot into frame-level geometry data. Its key job is to make coordinates line up across nested iframes.

**Data flow**: It receives Chrome’s full DOM snapshot, a device pixel ratio, and a starting origin. It parses each document, walks parent-to-child document links, accumulates iframe offsets like adding street addresses inside apartment buildings, adjusts element boxes into page-wide coordinates, and returns a list of `DocData` objects.

**Call relations**: `fetch_target` calls this after receiving `DOMSnapshot.captureSnapshot`. The resulting document data is later joined with accessibility-tree data to build `FrameSnapshot` objects.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Collects a complete snapshot for one browser target or session. It gathers both layout data and accessibility data, then joins them into frame snapshots.

**Data flow**: It receives a CDP connection, a session id, frame-prefix rules, and a coordinate origin. It enables needed browser domains, captures the DOM snapshot, reads device pixel ratio, parses geometry, asks for an accessibility tree for each document frame, and builds a tree of `FrameSnapshot` objects connected through iframe nodes. It returns the root frame snapshot.

**Call relations**: `BrowserPage._snapshot_target` calls this whenever it needs to snapshot the main page or a separately attached iframe target. Internally it uses `Cdp.send` for browser commands and `parse_snapshot` to make the raw DOM data usable.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain value from a Chrome accessibility field. Chrome wraps many values in small objects, and this helper unwraps them consistently.

**Data flow**: It receives a JSON field that may be an object with a `value`. If that shape is present, it returns the value as text; otherwise it returns an empty string.

**Call relations**: Both `render_page.render_node` and `render_markdown.walk` use this when reading roles and names from accessibility nodes.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `disabled`, `url`, or heading `level`. This turns Chrome’s property list into a simple lookup.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s properties, unwraps the matching value if found, and returns it; otherwise it returns nothing.

**Call relations**: `_format_extras` uses it to display element state. The page and markdown renderers also use it to skip hidden nodes and preserve structure such as heading levels and links.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node can be skipped in the action-oriented page view. This removes empty wrapper nodes that would make the tree noisy.

**Data flow**: It receives an accessibility node, its role, and its name. If the role is one of the known wrapper roles, has no name, and has no important state property, it returns true; otherwise false.

**Call relations**: `render_page.render_node` uses this while deciding whether to print a node or pass straight through to its children.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text to a fixed length and adds an ellipsis. This keeps page renderings compact enough for a model to read.

**Data flow**: It receives text and a maximum length. If the text is already short enough, it returns it unchanged; otherwise it returns a shortened version ending in `…`.

**Call relations**: Image-name extraction, extra state formatting, and page rendering all use this before putting potentially long names or values into the model-facing text.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Gets a useful fallback label for an image from its source URL. This helps identify images that have no accessible name.

**Data flow**: It receives an image source URL. It takes the final filename from the URL path, keeps it only if it looks like a file with an extension, truncates it if needed, and returns that name or an empty string.

**Call relations**: `render_page.render_node` uses this when printing unnamed images. `render_markdown.walk` uses it when creating image entries in the reading view.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the small state suffix shown after an element in the page tree, such as input type, value, checked, expanded, disabled, or URL. These details help the model choose the right action.

**Data flow**: It receives an accessibility node and optional geometry/DOM details. It reads selected accessibility properties, filters out empty or unsafe values, truncates long text, and returns a formatted string beginning with a space, or an empty string if there is nothing useful to add.

**Call relations**: `render_page.render_node` calls this just before adding a visible line to the page tree.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that owns a given reference prefix. This is how a reference like `f2e45` points into the right iframe instead of the main page.

**Data flow**: It receives the root frame snapshot and a prefix. It checks the root, then recursively searches child frames, returning the matching frame or nothing.

**Call relations**: `render_page` uses this when asked to render only the subtree under a specific reference.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility node id that corresponds to a DOM backend node id. It links the reference id back to the accessibility tree.

**Data flow**: It receives a frame snapshot and a backend node id. It scans the frame’s accessibility nodes until it finds one whose DOM backend id matches, then returns that accessibility node id or nothing.

**Call relations**: `render_page` uses this after `split_ref` and `_frame_by_prefix` so it can start rendering from the referenced element.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders the browser snapshot as a compact tree for acting on the page. The output names elements, shows roles, includes stable references, and adds center coordinates scaled to the model’s coordinate space.

**Data flow**: It receives a root frame snapshot, viewport size, optional model size, filtering options, depth limit, and optionally a starting reference. It computes coordinate scaling, chooses the correct starting node, walks accessibility nodes and spliced iframe children, filters noisy or offscreen entries when requested, and returns the final multiline text or nothing if the requested reference cannot be found.

**Call relations**: `BrowserPage.tree` calls this after collecting a fresh snapshot. It uses `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when rendering a subtree, and its inner helpers do the actual line building.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for display in the page tree. This gives the model a rough visual location for an element.

**Data flow**: It receives optional geometry. If bounds are present, it calculates the center of the box, scales it from browser viewport coordinates into model coordinates, and returns text like `(x=100,y=200)`; otherwise it returns an empty string.

**Call relations**: This helper is used inside `render_page.render_node` when a node is printed as a visible line.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Drops a child frame’s accessibility tree into the place where its iframe appears. This makes nested pages read like one continuous tree.

**Data flow**: It receives the current frame, an optional iframe backend id, and the depth where the child should appear. If that backend id has a child frame with a root node, it asks `render_node` to render that child root at the same place.

**Call relations**: `render_page.render_node.descend` calls this after walking normal child accessibility nodes, so iframe contents appear under their iframe element.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether to print it, skip it, or descend into its children. This is the heart of the action-oriented rendering.

**Data flow**: It receives a frame, an accessibility node id, current indentation depth, and the parent name. It avoids loops, ignores hidden nodes, reads role/name/geometry/state, applies filters, creates a reference when possible, appends one formatted line for useful nodes, and then walks child nodes and child frames.

**Call relations**: It is the main worker inside `render_page`. It calls helpers for accessibility values, properties, extra state, image fallback names, truncation, and skip rules.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues walking from a node into its children and any iframe content attached to that node. It keeps the traversal logic in one small place.

**Data flow**: It receives the depth to use for children and the name to treat as the parent name. It calls `render_node` for each child accessibility id, then asks `splice` to render an attached child frame if the current node is an iframe.

**Call relations**: `render_page.render_node` uses this both when a wrapper node is skipped and after a visible node has been printed.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders the snapshot as a reading-focused Markdown view instead of an action tree. It keeps headings, links, list items, images, and paragraphs in a form that is easier to read.

**Data flow**: It receives a root frame snapshot. It walks the accessibility tree, collects inline text into paragraphs, emits block-level items, follows child frames at iframe points, and returns Markdown blocks separated by blank lines.

**Call relations**: `BrowserPage.markdown` calls this after taking a fresh snapshot. Its inner helpers collect text and walk the frame tree.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one finished Markdown block if it is not empty or a duplicate of the previous block. This keeps the reading view clean.

**Data flow**: It receives text, trims whitespace, checks whether it should be kept, and appends it to the block list when useful.

**Call relations**: `render_markdown.flush` and `render_markdown.walk` call this whenever a paragraph, heading, list item, image, or named block is ready.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline text into a Markdown paragraph. It is like emptying a small notepad before starting a new section.

**Data flow**: It checks the current inline text list. If there is text, it joins the pieces with spaces, emits the result as a block, and clears the list.

**Call relations**: `render_markdown.walk` calls this before and after block-like nodes so paragraphs do not run together.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and translates nodes into Markdown pieces. It chooses different output for headings, links, list items, images, blocks, and ordinary text.

**Data flow**: It receives a frame, an accessibility node id, and the parent name. It skips repeated or hidden nodes, reads role/name/properties, appends inline text or emits blocks, then walks child nodes and any child frame spliced under an iframe.

**Call relations**: This is the main worker inside `render_markdown`. It uses `_ax_value`, `_ax_property`, and `_image_name` to turn accessibility data into readable Markdown.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Produces the model-facing action tree for a tab. This is the high-level method callers use when they want the page described with clickable references.

**Data flow**: It receives a tab, a filter type, and optionally a reference to focus on. It takes a fresh snapshot of the tab, renders that snapshot with viewport and model sizing, and returns the text tree or nothing if a requested reference is invalid.

**Call relations**: Callers use this instead of calling `snapshot` and `render_page` separately. It wires those two steps together.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Produces a reading view of a tab as Markdown. This is useful when the caller wants content structure more than clickable controls.

**Data flow**: It receives a tab. It takes a fresh snapshot and passes it to the Markdown renderer, returning the resulting text.

**Call relations**: It is the public wrapper around `snapshot` plus `render_markdown`.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Checks and resolves a model-provided browser reference before an action uses it. It protects the system from acting on made-up or stale references.

**Data flow**: It receives a tab and a reference string. It parses the reference, looks up the frame prefix in the tab’s registered frame map, and returns the frame information plus backend node id. If the reference is malformed or no longer known, it raises a clear `HallucinationError` telling the caller to re-read the page.

**Call relations**: `BrowserPage.ref_point` calls this before asking the browser for coordinates. It uses `split_ref` for the syntax check.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the on-screen center point of a referenced browser element. This lets later automation click or point at the element the model chose.

**Data flow**: It receives a tab and a reference. It resolves the reference, scrolls the element into view, asks Chrome for its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer x/y coordinates. If Chrome cannot resolve the node, it raises a `HallucinationError`.

**Call relations**: This method builds on `BrowserPage.resolve_ref` and uses `_coord_float_or_default` to read coordinate values safely from browser responses.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Takes a fresh, fully stitched snapshot of the tab and records which frame prefixes are valid for later references. This is the common starting point for both tree and Markdown output.

**Data flow**: It receives a tab. It snapshots the main target, clears the tab’s old reference-frame map, registers every frame in the new snapshot, and returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates collection to `_snapshot_target` and bookkeeping to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Snapshots one browser target and, if allowed by the depth limit, attaches any out-of-process iframe snapshots under it. This keeps frame recursion controlled.

**Data flow**: It receives the tab, a session id, a reference prefix, a coordinate origin, and the current frame depth. It calls `fetch_target` for the immediate target, then optionally looks for separately running iframe targets and attaches them. It returns the root frame snapshot for that target.

**Call relations**: `BrowserPage.snapshot` uses this for the main tab. `_snapshot_oop` uses it recursively for out-of-process iframes.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Searches a snapshot tree for iframes that Chrome did not include as normal child documents, then tries to snapshot and attach them. These are out-of-process iframes, meaning Chrome runs them in a separate target.

**Data flow**: It receives the tab, a root frame snapshot, and the current depth. It walks through frames already in the tree, checks their list of out-of-process iframe backend ids, snapshots each one if possible, and stores successful child snapshots under the iframe’s backend id.

**Call relations**: `BrowserPage._snapshot_target` calls this after the basic target snapshot is complete. It delegates each individual iframe to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Snapshots one out-of-process iframe and returns it as a child frame snapshot. It quietly skips frames that cannot be described, attached, or snapshotted.

**Data flow**: It receives the tab, the parent frame, the iframe backend node id, and the current depth. It asks Chrome which frame id belongs to that iframe, obtains or creates a CDP session for it, calculates its origin from the iframe bounds, and calls `_snapshot_target` recursively. It returns the child snapshot or nothing on recoverable failure.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for every out-of-process iframe it finds. It uses `_oop_session` for session attachment and `PageTab.frame_seq` to create a frame prefix.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records every frame prefix in the tab so future references can be resolved. This is what makes rendered refs meaningful after the page tree is returned.

**Data flow**: It receives a tab and a frame snapshot. It stores a `FrameNode` for the frame’s prefix, then recursively registers all child frames.

**Call relations**: `BrowserPage.snapshot` calls this after collecting a snapshot and before returning it to renderers.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing a cached one when possible. This avoids repeatedly attaching to the same frame target.

**Data flow**: It receives a frame id. If a session id is already cached, it returns it. Otherwise it asks Chrome to attach to that target, initializes the new session, caches it, and returns the new session id; if attach fails, it returns nothing.

**Call relations**: `BrowserPage._snapshot_oop` calls this before it can snapshot a separately running iframe.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Reads one coordinate value from browser data as a float. It accepts numbers and numeric strings, supplies a default for missing values, and rejects unexpected types.

**Data flow**: It receives a JSON value and a default. Numeric values become floats, non-empty strings are converted to floats, `None` becomes the default, and other values raise a validation error.

**Call relations**: `BrowserPage.ref_point` uses this when averaging the four corners returned by Chrome for a referenced element.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).


### Input correction and primitives
Automation instructions are repaired and translated into reliable form uploads and keyboard interactions.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `request handling`

This file acts like a proofreader for batches of browser actions. The rest of the system may receive a list of intended actions such as click, type, scroll, or wait. Those actions can be slightly incomplete or awkward, especially when they come from a model that is trying to control a browser. Without this cleanup step, the browser might type into the wrong place, fail to scroll because no scroll point was given, or pause for an undefined amount of time.

The main function, fixup_actions, walks through the actions one by one and rewrites only the cases that are likely to break. If it sees typing aimed at a location or page element, but there was no recent click, it inserts a click first so the right field is focused. If text contains written-out escape sequences like "\\n", it turns them into real newlines. If a scroll has no anchor point, it uses the center of the browser's model-sized screen. If a wait has no duration, it gives it a safe default of three seconds.

There is also a helper, split_at_waits, that breaks one long list into smaller batches ending at each wait. This lets the browser pause and settle before the next batch continues, like stopping at commas while reading instructions aloud.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: This function repairs a list of browser actions before they are executed. It makes incomplete or model-style instructions safer and more practical for a real browser to follow.

**Data flow**: It receives a list of ComputerAction objects, the current browser viewport size, and optionally the size used by the model that chose the actions. It first works out the effective model screen size and its center point. Then it reads each action and either keeps it, copies it with safer values, or inserts a new helper action before it. The result is a new list of actions, possibly longer or slightly changed, ready to dispatch to the browser.

**Call relations**: This is the main cleanup step in the file. When it needs to focus a field before typing, it calls _focus_click. When it needs to turn text like "\\n" into an actual newline, it calls _unescape_text. It also asks the coordinate helper for the effective model size, and creates new ComputerAction or ScrollParameters objects when it needs replacement actions.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: This function divides a long action list into smaller groups, with each wait action ending its group. It is useful when the browser should be allowed to pause and update before more actions are sent.

**Data flow**: It receives a list of ComputerAction objects. It builds a current batch while reading the list in order. Whenever it sees an action whose type is "wait", it closes the current batch and starts a new one. It returns a list of batches, preserving the original action order.

**Call relations**: This function is separate from the action-repair logic. After actions have been prepared, callers can use it to decide where execution should pause, so a wait becomes a natural boundary between browser interaction bursts.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper creates a click action that focuses the same target a typing action is aimed at. It exists because typing into a browser field usually only works after that field has been selected.

**Data flow**: It receives a ComputerAction, normally a typing action that includes either a coordinate or a page reference. If the action has a coordinate, it returns a new left-click at that coordinate. Otherwise, it returns a new left-click using the action's reference. It does not change the original action.

**Call relations**: fixup_actions calls this helper when it sees a typing action that appears to need focus first. The helper hands back a left-click action, which fixup_actions inserts immediately before the typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns written escape sequences in typed text into the real characters they mean. For example, it changes the two characters "\\n" into an actual line break.

**Data flow**: It receives a ComputerAction and reads its text field. If the text does not contain known escaped literals, it returns the original action unchanged. If it does, it replaces supported literals such as "\\t" and "\\n" with real tab and newline characters, then returns a copied action with the corrected text.

**Call relations**: fixup_actions calls this helper for every typing action. It gives fixup_actions either the original action, when no correction is needed, or a copied action whose text will type as the user likely intended.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `browser action handling`

Web forms are not changed by simply editing text in this Python program. The code has to talk to the live browser, find the exact page element, and make the browser behave as if a user changed it. This file is the small bridge that does that for form controls.

The file defines a browser-session shape, BrowserFormSession, saying what the surrounding browser object must be able to do: find a page, talk to Chrome DevTools Protocol, run JavaScript on a page element, and turn a stored element reference back into a real browser node. Chrome DevTools Protocol is the control channel used to inspect and operate a Chromium browser.

BrowserForms is the working part. For normal inputs, it resolves a saved element reference, runs a small JavaScript function on that element, sets its value or checked state, and fires input and change events so the web page notices. For file uploads, it uses the browser’s own file-input command, because web pages do not allow ordinary scripts to set local file paths. It also includes a check that reads the byte sizes of files actually attached to an input, which matters when files arrive through a remote transport and may not be visible to the browser until writing is complete.

If an automation step points at the wrong element, this file turns low-level browser errors into a clearer HallucinationError, telling the caller to reread the page and use a valid reference.

#### Function details

##### `BrowserFormSession.page`  (lines 41–41)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It should return the browser page, or a specific tab when a tab id is supplied.

**Data flow**: A caller gives an optional tab id. The real session implementation looks up the matching browser page and returns it for later element lookup.

**Call relations**: BrowserForms methods rely on this first, because every form action needs to know which page or tab contains the target element.


##### `BrowserFormSession.connection`  (lines 43–43)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It should provide the connection used to send low-level commands to the browser.

**Data flow**: No extra input is needed. The real session implementation returns a Chrome DevTools Protocol connection object, which other methods use to ask the browser to resolve nodes or attach files.

**Call relations**: BrowserForms uses this connection whenever it needs the browser itself to do something that page JavaScript alone cannot safely do.


##### `BrowserFormSession.call_on`  (lines 45–51)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It should run a JavaScript function on one specific browser object, such as an input element.

**Data flow**: The caller provides a browser session id, an object id for the page element, JavaScript code, and optional arguments. The implementation runs that code in the browser and returns the JSON-like result.

**Call relations**: BrowserForms.input uses it to set form values and fire page events. BrowserForms.attached_sizes uses it to inspect the files currently attached to a file input.


##### `BrowserFormSession.resolve_ref`  (lines 53–53)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a saved page reference into the browser node needed for direct browser commands.

**Data flow**: The caller gives a page object and a reference string from an earlier page read. The implementation finds the matching element and returns its node information plus the browser’s internal backend node id.

**Call relations**: Every action in BrowserForms starts from a human-facing reference, so this method is the handoff from the automation system’s page model to the live browser.


##### `BrowserForms.attached_sizes`  (lines 60–74)

```
async def attached_sizes(self, args: JsonDict) -> list[int]
```

**Purpose**: This checks what files a file input is truly holding by reading the byte sizes of its attached files. It is useful because a remote upload can appear requested before the browser has actually received the file data.

**Data flow**: It receives arguments containing a page reference and optionally a tab id. It selects the tab, resolves the reference to a real browser node, asks the browser for a JavaScript object for that node, runs a small script that reads each attached file’s size, and returns a list of integer byte sizes. If the browser reply is not shaped as expected, it safely returns an empty list.

**Call relations**: This method uses _tab_id to normalize the tab value, the browser session to find and resolve the element, the DevTools connection to get an object id, and call_on to run the file-size JavaScript on that exact element.

*Call graph*: calls 1 internal fn (_tab_id); 3 external calls (get, as_map, as_str).


##### `BrowserForms.upload_file`  (lines 76–93)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This attaches one or more local file paths to a browser file-upload input. It uses the browser’s own file-input command, because web security rules prevent ordinary page JavaScript from setting file paths.

**Data flow**: It receives arguments containing a target element reference, a list of file paths, and optionally a tab id. It cleans and checks those values, finds the target element in the chosen tab, and sends the browser a command to set that file input’s files. On success it returns the same reference and the file paths it asked the browser to attach. If the target is not a file input, it raises a clear HallucinationError.

**Call relations**: This method is called when the automation wants to upload files. It depends on _tab_id for tab parsing, resolve_ref for finding the element, and the browser connection for the special DevTools file-upload command.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 95–110)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This sets the value of a form control, such as a text field, checkbox, radio button, select box, or editable page area. It also tells the page that the value changed, so site code reacts as it would to a real user edit.

**Data flow**: It receives arguments containing a target reference, a value, and optionally a tab id. It chooses the tab, resolves the target reference, asks the browser for a callable object id, and runs JavaScript on the element. That script sets checked, selected, text, or value as appropriate, fires input and change events, and returns the resulting value information. If the element reference cannot be resolved, it raises a HallucinationError explaining that the page should be reread.

**Call relations**: This is the main form-filling path. It uses _tab_id and the browser session to reach the right element, then hands off to call_on so the actual change happens inside the live web page.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 113–122)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns a tab id supplied in different JSON-friendly forms into either an integer tab id or no tab id. It keeps the public form methods from repeating that conversion.

**Data flow**: It receives a value that may be an integer, a float, a non-empty string, or something else. Integers are returned directly, floats and non-empty strings are converted to integers, and missing or unsupported values become None.

**Call relations**: BrowserForms.attached_sizes, BrowserForms.upload_file, and BrowserForms.input call this before asking the browser session for a page, so all three methods interpret the optional tab id consistently.

*Call graph*: called by 3 (attached_sizes, input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`domain_logic` · `request handling`

A browser automation tool cannot just say “type A” and expect Chrome to guess all the details. Chrome needs a precise key event: the visible key, the physical keyboard code, numeric key codes, modifier flags such as Shift or Control, and sometimes platform-specific editing commands. This file is the translator between a simple user action and those low-level browser messages.

It starts with a US keyboard map, borrowed from Playwright, that says what each physical key means. For example, the same key can produce “1” normally and “!” when Shift is held. The file expands that map into a faster lookup table so callers can ask by physical code, common name, or typed character.

A `KeyboardState` object acts like the file’s memory. It records which modifier keys and physical keys are currently down. That matters because pressing “a” while Shift is held should become “A”, and pressing an already-held key should be marked as an auto-repeat.

The main outputs are Chrome DevTools Protocol calls, represented as method name plus JSON-like data. `press_combo` presses a shortcut down in order and releases it in reverse. `type_text` types short text key by key, but sends long text as one insert operation, which is faster and avoids unnecessary per-character events.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: Builds a practical lookup table from the raw keyboard layout. It lets later code find a key description not only by physical key name, but also by aliases like “Shift” or by the actual character such as “a” or “!”.

**Data flow**: It receives the raw layout table, where each physical key has its normal and sometimes shifted meaning. For each entry, it creates a `KeyDescription` with the values Chrome needs, adds any shifted version, and adds convenient aliases. It returns a new dictionary that later functions can search quickly.

**Call relations**: This runs when the module is loaded to create `LAYOUT_CLOSURE`. It uses `KeyDescription` to package each key’s browser-facing details and `dataclasses.replace` to make adjusted copies, such as the shifted version of a key.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: Turns the currently held modifier keys into the numeric flag value Chrome expects. A modifier key is a special key like Shift, Control, Alt, or Meta that changes the meaning of another key.

**Data flow**: It receives a set of modifier names that are currently pressed. It checks each known modifier and adds its assigned bit value when present. It returns one integer that summarizes all active modifiers.

**Call relations**: Both `key_down` and `key_up` call this when building Chrome key-event data. They need this number so Chrome knows, for example, that a key was pressed while Control and Shift were held.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: Finds the browser-ready description for one requested key, taking the current keyboard state into account. It is the place that decides whether a key should produce its normal form, its shifted form, or no text because another modifier is held.

**Data flow**: It receives the current `KeyboardState` and a key name or character. It looks that key up in `LAYOUT_CLOSURE`; if it cannot find it, it raises a validation error so bad input is rejected early. If Shift is down and the key has a shifted meaning, it uses that version. If another modifier such as Control or Meta is down, it clears the text field because shortcut key events usually should not insert characters. It returns the final `KeyDescription`.

**Call relations**: `key_down` and `key_up` both rely on this before producing Chrome events. It is the shared interpreter that keeps key presses and releases speaking the same keyboard language.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: Looks up extra Mac editing commands for a key shortcut. These commands help Chrome on macOS treat shortcuts like arrow movement, delete-word, copy, paste, and undo the way native Mac text fields expect.

**Data flow**: It receives a physical key code and the set of currently held modifiers. It builds a shortcut name such as `Shift+Meta+ArrowLeft`, searches the Mac editing command table, removes the trailing colon used by the original Mac command names, and skips insert-style commands. It returns a list of command names for Chrome to attach to the key event.

**Call relations**: `key_down` calls this only when the caller says the target browser is on macOS. The returned commands are placed inside the Chrome key-down event so text editing shortcuts behave more naturally on that platform.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: Creates the Chrome message for pressing a key down. It also updates the shared keyboard state so later events know that this key, and possibly its modifier effect, is now active.

**Data flow**: It receives a `KeyboardState`, a requested key, and whether the target is Mac. It asks `_description_for` what this key means right now, checks whether the physical key was already pressed, records it as pressed, and records it as an active modifier if it is Shift, Control, Alt, or Meta. On Mac, it also asks `_mac_commands` for editing commands. It returns one Chrome DevTools Protocol call named `Input.dispatchKeyEvent` with all the key details, modifier mask, text, repeat flag, location, and keypad flag.

**Call relations**: `press_combo` uses this to press every key in a shortcut, and `type_text` uses it for each short, keyboard-typeable character. Inside, it depends on `_description_for`, `_mac_commands`, and `modifiers_mask` to assemble a complete event Chrome can understand.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: Creates the Chrome message for releasing a key. It also removes that key from the remembered pressed state so future key events are not affected by a key that is no longer held.

**Data flow**: It receives a `KeyboardState` and a requested key. It resolves the key with `_description_for`, removes the key from the active modifier set if appropriate, removes its physical code from the pressed-key set, and returns a Chrome `Input.dispatchKeyEvent` call of type `keyUp` with the remaining modifier mask and key identity information.

**Call relations**: `press_combo` calls this after pressing a shortcut, releasing keys in reverse order, and `type_text` calls it after each synthesized character press. It uses `modifiers_mask` so the release event still reports which modifiers remain held.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a shortcut string like `Ctrl+C` or `Shift+Enter` into the sequence of key-down and key-up messages needed to perform it. This is useful when callers want shortcut behavior rather than ordinary text insertion.

**Data flow**: It receives the current keyboard state, a shortcut string, and whether the target is Mac. It splits the string around plus signs, normalizes friendly names such as `ctrl` to `Control`, rejects an empty shortcut, presses each key in order with `key_down`, then releases them in reverse order with `key_up`. It returns the full list of Chrome calls.

**Call relations**: This is a higher-level helper built on `key_down` and `key_up`. External browser-action code can call it for one named shortcut, and it supplies the correctly ordered low-level events.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: Turns a text string into browser input. For short text it behaves like real typing, while for long text it uses a direct text insertion call to avoid a large and slow stream of individual key events.

**Data flow**: It receives the current keyboard state, the text to enter, and whether the target is Mac. If the text is longer than the configured limit, it returns one `Input.insertText` call containing the whole string. Otherwise it walks through each character: if the character exists in the keyboard layout, it creates a key-down and key-up pair; if not, it inserts that character directly. It returns the list of Chrome calls.

**Call relations**: This helper is the text-entry counterpart to `press_combo`. For keyboard-typeable characters it delegates to `key_down` and `key_up`, so normal key handlers and autocomplete can still fire; for unsupported or long input, it hands Chrome direct text insertion instead.

*Call graph*: calls 2 internal fn (key_down, key_up).

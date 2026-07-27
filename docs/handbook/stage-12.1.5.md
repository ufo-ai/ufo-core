# Browser actions, input, tabs, forms, and downloads  `stage-12.1.5`

This stage is part of the system’s main work loop. It is where the agent acts like a person using a browser: opening tabs, clicking buttons, typing text, filling forms, scrolling pages, uploading files, and saving downloads. It sits above the low-level browser connection and turns simple instructions into exact browser actions.

The tabs module is the page manager. It opens, closes, switches, and navigates tabs by sending Chrome DevTools Protocol messages, which are structured commands that Chromium browsers understand. The computer module is the main “hands and eyes” layer. It performs actions such as clicking, typing, and scrolling, then reports what the page looks like afterward, including screenshots and safety notes. The keys module is the keyboard translator. It converts text and shortcuts like Ctrl+C into realistic key press messages. The forms module focuses on web forms, safely setting field values and attaching files for upload. The downloads module watches for saved files, can force files like PDFs to download instead of opening in the browser, waits for them to finish, and makes them available to the rest of the system.

## Files in this stage

### Tab orchestration
Browser tab tracking and navigation provide the page context for later actions.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`orchestration` · `request handling`

This file is like the tab desk at the front of a browser automation system. Other parts of the project can ask for “open this page,” “go back,” “close that tab,” or “tell me what tabs are open,” without knowing the low-level browser messages needed to do those things.

It tracks each open tab with a small Tab object. A tab stores the browser target id, the attached session id, keyboard state, and frame information. The BrowserTabs class then uses a browser session object to talk to the browser through the Chrome DevTools Protocol, often called CDP. CDP is a remote-control API for Chrome-like browsers.

The file also listens to browser events. When the browser reports that a page target was created or destroyed, this code records that and later syncs its local tab list. When a page starts loading, finishes its document content, or reaches paint-related lifecycle events, it updates a settling tracker. “Settling” means waiting until a page is likely ready enough to interact with.

Navigation has a few careful details. Plain addresses like “example.com” are turned into “https://example.com”. Special words like “back” and “forward” trigger history navigation instead of a new URL. If navigation starts a download rather than a normal page load, that is treated as a valid outcome instead of a failure.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied address into something the browser can navigate to. It preserves special commands like “back” and “forward,” keeps already-complete URLs as they are, and adds “https://” to plain site names.

**Data flow**: It receives a text URL or command. It checks whether it is a known special value, then checks whether it already has a URL scheme such as “http:” or “file:”. It returns either the original text or a safer browser-ready URL with “https://” added.

**Call relations**: BrowserTabs.navigate calls this before choosing how to move the page. Its result decides whether the flow goes to browser history navigation or normal page navigation.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the project’s local record for one browser tab. This record remembers how to talk to that tab and stores state needed for keyboard and frame tracking.

**Data flow**: It receives a browser target id and a session id. It stores both, creates a fresh keyboard state, prepares a numbering table for frames, and creates a root frame reference for the tab. The result is a Tab object ready for later browser actions.

**Call relations**: BrowserTabs.attach_tab creates Tab objects after the browser confirms that the automation code has attached to a real browser target.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number. This is useful when frames need friendly internal labels instead of long browser-generated ids.

**Data flow**: It receives a frame id. If that frame has not been seen before, it assigns the next available number. It returns the number for that frame, reusing the same number on later calls.

**Call relations**: This is a helper on Tab for other frame-aware code. It does not call out to the browser; it only updates the tab’s local memory.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the shape of the method used to send a command to the browser through CDP. It is part of a protocol, meaning this file says what abilities it expects from a connection object without implementing the connection here.

**Data flow**: A caller provides a command name, optional command data, and optionally a session id for a specific tab. The concrete connection sends that to the browser and returns the browser’s JSON-like reply.

**Call relations**: BrowserTabs methods rely on this capability throughout tab setup, navigation, and closing. The actual implementation lives elsewhere; this file only depends on the promised behavior.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines how code can start waiting for one or more future browser events. It is used when an action, such as navigation, should be followed by a specific confirmation event.

**Data flow**: A caller names the events it wants and may give a session id. The concrete connection returns a future, which is a placeholder for an event result that will arrive later.

**Call relations**: Navigation code uses this before sending commands that should trigger page events, so it does not miss the event while the command is in progress.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines how code waits for an expected browser event with a time limit. The time limit prevents automation from hanging forever if the browser never reports the event.

**Data flow**: It receives a future event and a timeout in seconds. The concrete connection waits until the event arrives or the time runs out, then returns the event data or raises an error.

**Call relations**: BrowserTabs._goto and BrowserTabs._history_step use this after setting up expected page events.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how BrowserTabs obtains the CDP connection used to control the browser. It is a required capability of the surrounding browser session object.

**Data flow**: It takes no extra data beyond the session object. It returns the connection object that can send browser commands and wait for browser events.

**Call relations**: Almost every active BrowserTabs operation asks the browser session for this connection before sending commands.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how BrowserTabs gets access to download tracking. This matters because a navigation can turn into a file download instead of a normal page load.

**Data flow**: It reads the browser session and returns an object that can inspect recent downloads. BrowserTabs uses that object to decide whether a navigation error actually means a download began.

**Call relations**: BrowserTabs._goto calls this when the browser reports a navigation error, so downloads are not mistaken for broken navigation.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how this file can run JavaScript inside a tab. JavaScript is used here to read simple page facts such as the current URL and document title.

**Data flow**: It receives a tab session id and a JavaScript expression. The browser session runs that expression in the page and returns the resulting JSON-like value.

**Call relations**: BrowserTabs.tab_info uses this to build human-readable tab summaries.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser page targets already existed before this tab tracker started watching. This prevents old tabs from being mistaken for newly created tabs.

**Data flow**: It receives target information from the browser. It reads the list of target records, extracts each target id, and stores those ids as the initial set on the browser session’s tab event state.

**Call relations**: This is used during event setup so BrowserTabs.on_target_created can later tell the difference between existing pages and truly new pages.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser says a new page target was created. If it is a new page that was not part of the startup list, it queues that target so the tab tracker can attach to it later.

**Data flow**: It receives event data from the browser. It checks that the event describes a page, extracts its target id, compares it with already-known targets, and appends it to the created-target queue if needed.

**Call relations**: Browser event dispatch calls this when target-created events arrive. BrowserTabs.sync later consumes the queued target ids and turns them into tracked Tab objects.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Notices when the browser says a target was destroyed. It records the target id so the local tab list can remove the closed tab.

**Data flow**: It receives event data, looks for a target id, and adds that id to the destroyed-target set. It does not immediately rewrite the tab list.

**Call relations**: Browser event dispatch calls this on target-destroyed events. BrowserTabs.sync later uses the recorded ids to clean up the local list of open tabs.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when its main frame starts loading. This helps the rest of the system know that the page is not settled yet.

**Data flow**: It receives frame-loading event data and the browser session id that produced it. If the event belongs to the tab’s top-level frame, it tells the settling tracker that this session is loading.

**Call relations**: Browser event dispatch calls this during page loading. It uses BrowserTabs.is_top_level_frame to avoid treating subframes, such as embedded ads or iframes, as full-page navigation.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a session as having loaded its document content. This is one milestone on the path from “page is loading” to “page is ready enough.”

**Data flow**: It receives event data and a session id. If there is a session id, it tells the settling tracker that the document content loaded for that session.

**Call relations**: Browser event dispatch calls this when the browser reports the DOM content event. Later, navigation waits use the settling tracker’s state.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports certain visual lifecycle events for the main frame. “Painted” means the page has drawn something on screen.

**Data flow**: It receives lifecycle event data and a session id. It checks that the event name is one of the paint-related events and that the frame is the tab’s top-level frame, then updates the settling tracker.

**Call relations**: Browser event dispatch calls this for lifecycle events. It uses BrowserTabs.is_top_level_frame so only the main page frame affects readiness.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a frame id belongs to the main frame of a tracked tab. This avoids confusing embedded frames inside a page with the page itself.

**Data flow**: It receives a session id and a frame id. It compares them with the known tabs, where a tab’s target id identifies its top-level frame, and returns true or false.

**Call relations**: BrowserTabs.on_frame_loading and BrowserTabs.on_lifecycle call this before updating the settling tracker.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects the automation system to a browser page target and prepares that tab for use. This is the setup step that makes later page control possible.

**Data flow**: It receives a target id. It asks the browser to attach to that target, extracts the new session id, enables page, DOM, network, lifecycle, and fetch features, sets the viewport size, and returns a new Tab object.

**Call relations**: BrowserTabs.sync uses this for newly discovered tabs, BrowserTabs.page uses it when creating the first blank tab, and BrowserTabs.create uses it after opening a new browser target. It calls BrowserTabs.init_session for the common setup commands.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser features needed inside one tab session. Without this, the browser would not send the page, lifecycle, DOM, and network events this system relies on.

**Data flow**: It receives a session id. It sends several enable commands to the browser for that session. It returns nothing, but the browser session is now configured to report useful events.

**Call relations**: BrowserTabs.attach_tab calls this immediately after attaching to a target and before the Tab object is used.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list back in line with browser events that arrived earlier. It removes tabs the browser destroyed and attaches to newly created page targets.

**Data flow**: It reads the queued created-target ids and destroyed-target ids from the browser session. It removes gone tabs, clears the destroyed set, then processes each new target id and adds a Tab object when attachment succeeds. It ignores targets that disappeared or cannot be attached.

**Call relations**: BrowserTabs.page and BrowserTabs.tabs_context call this before reporting or choosing tabs, so callers see a current view of the browser.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the requested tab, creating a blank tab if none exist. It is the safe doorway for operations that need a real tab to work with.

**Data flow**: It optionally receives a tab id. It first syncs the tab list, creates and attaches a blank tab if the list is empty, then returns either the most recent tab or the tab at the requested index. If the requested index is not open, it raises a validation error.

**Call relations**: BrowserTabs.navigate and BrowserTabs.close call this to find the tab they should act on. It may call BrowserTabs.attach_tab if a new blank tab is needed.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new page or through its history, then waits until the page is reasonably settled. This is the main navigation method used by higher-level code.

**Data flow**: It receives a URL or special command and an optional tab id. It gets the tab, normalizes the URL, resets the settling tracker, performs either back, forward, or normal navigation, waits for the page-settling process, and returns the tab’s current URL and title.

**Call relations**: BrowserTabs.create calls this after opening a new tab. Inside, it calls normalize_url, BrowserTabs.page, BrowserTabs._history_step or BrowserTabs._goto, and finally BrowserTabs.tab_info.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs normal URL navigation inside one tab. It watches for the page’s document-content event and also handles the special case where navigation becomes a download.

**Data flow**: It receives a Tab and a browser-ready URL. It records how many downloads existed before navigation, starts waiting for the page-load event, sends the browser navigation command, checks for navigation errors, and waits for the load event when needed. If the error was caused by a new download, it treats that as acceptable.

**Call relations**: BrowserTabs.navigate calls this for ordinary URLs. It uses the CDP connection to send and wait for browser events, and it asks the download reader whether a failed-looking navigation became a download.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves one tab backward or forward in its browser history. If there is no entry in that direction, it simply leaves the tab where it is.

**Data flow**: It receives a Tab and a step value, where -1 means back and 1 means forward. It asks the browser for the navigation history, calculates the target entry, sends a command to jump to that entry, and waits for a navigation-related event.

**Call relations**: BrowserTabs.navigate calls this when the normalized target is “back” or “forward.” It relies on browser history data and validates the returned list and entry data before navigating.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and title from a tab. This gives callers a simple summary of what page the tab is showing.

**Data flow**: It receives a Tab. It runs a small JavaScript expression in that tab to read location.href and document.title, converts the result into a map, and returns a dictionary with string URL and title fields.

**Call relations**: BrowserTabs.navigate uses this after navigation, BrowserTabs.tabs_context uses it for every open tab, and BrowserTabs.tab_titles uses it when only titles are needed.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all open tabs for callers that need to display or reason about the current browser state. It marks the last tab in the list as the active one.

**Data flow**: It first syncs the local tab list. It then asks each tab for its URL and title, adds an id and active flag, and returns an object containing the current tab id and the tab list.

**Call relations**: BrowserTabs.close calls this after removing a tab so the caller gets the updated tab state. Other higher-level code can also use it to inspect open tabs.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the page titles for the currently tracked tabs. This is a compact view when callers do not need full tab details.

**Data flow**: It reads each Tab in the current local list, calls BrowserTabs.tab_info for each one, extracts the title, and returns a list of title strings.

**Call relations**: It builds on BrowserTabs.tab_info instead of duplicating the JavaScript used to read titles.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and navigates it to the requested URL. If no URL is given, it opens a blank page.

**Data flow**: It receives an optional URL. It asks the browser to create a blank target, attaches to that target, adds the new Tab to the local list, navigates that tab to the requested URL, and returns the new tab id along with the final URL and title.

**Call relations**: This is a higher-level tab action. It uses BrowserTabs.attach_tab for setup and BrowserTabs.navigate for the actual page movement.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a selected tab and returns the updated list of open tabs. It also clears out-of-process frame session tracking because those sessions may no longer be valid after a close.

**Data flow**: It receives an argument object that may contain a tab id. It converts the tab id, finds the matching tab, tells the browser to close the target, removes that Tab from the local list, clears stored out-of-process sessions, and returns the fresh tab context.

**Call relations**: It calls _tab_id to interpret the input, BrowserTabs.page to find the tab, sends a browser close command, and then calls BrowserTabs.tabs_context to report the result.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loose input value into an optional tab id number. This lets callers pass a tab id as an integer, a float, a non-empty string, or omit it.

**Data flow**: It receives a JSON-like value. Integers are returned directly, floats are converted to integers, non-empty strings are parsed as integers, and anything else becomes None. The output is used as the requested tab id or as “no specific tab.”

**Call relations**: BrowserTabs.close calls this before asking BrowserTabs.page which tab should be closed.

*Call graph*: called by 1 (close).


### Page interaction
High-level user-like browser actions are translated into executable page events and updated observations.

### `extensions/browser/ufo_ext_browser/bua/computer.py`

`domain_logic` · `request handling`

This file is the browser-control bridge for the project. An outside caller sends a batch of actions in a simple JSON-like shape. BrowserComputer checks and cleans those actions, finds the right browser tab, performs each action through Chrome DevTools Protocol, and then reports what happened. Chrome DevTools Protocol, or CDP, is the browser’s remote-control interface: it lets code send mouse, keyboard, screenshot, and page commands to Chrome.

The file matters because higher-level agents do not directly know how to press a browser mouse button or convert their screen coordinates to the real viewport. This code is the translator. It maps model coordinates to browser coordinates, sends clicks, drags, key presses, scrolls, and waits, and waits for the page to settle after each group of actions. Like a careful remote-control operator, it also watches for special cases: repeated scrolling gets a warning, sign-in pages trigger a safety reminder, downloads are reported, JavaScript dialogs are noted, and clicks on native select dropdowns get advice because normal option-clicking may not work in this browser setup.

At the end of a run, it captures a fresh screenshot. If there was a click, it marks the click location with a small blue dot so the caller can see exactly where the action landed.

#### Function details

##### `BrowserComputerSession.page`  (lines 70–70)

```
async def page(self, tab_id: int | None=None) -> ComputerTab
```

**Purpose**: This is part of the session contract. It promises that a browser session can provide the tab that actions should run in, optionally chosen by tab id.

**Data flow**: A caller gives an optional tab id. The session looks up or chooses the matching browser tab and returns an object with a session id and keyboard state.

**Call relations**: BrowserComputer.run relies on this kind of method at the start of an action request so it knows which tab to control. The actual implementation lives outside this file; this file only states what BrowserComputer needs from it.


##### `BrowserComputerSession.connection`  (lines 72–72)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the session contract. It promises access to the browser’s CDP connection, the channel used to send low-level browser commands.

**Data flow**: No extra input is needed. The session returns a connection object that can send commands such as mouse events, screenshots, and DOM operations.

**Call relations**: BrowserComputer.run, BrowserComputer.act, BrowserComputer._select_reminder, BrowserComputer._dispatch, and BrowserComputer._mouse_event use this connection whenever they need the browser itself to do something. The concrete connection is supplied by the surrounding browser session.


##### `BrowserComputerSession.tab_info`  (lines 74–74)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is part of the session contract. It promises to summarize the current tab for the response sent back to the caller.

**Data flow**: It receives a tab object, reads whatever tab details the session tracks, and returns a dictionary of tab information.

**Call relations**: BrowserComputer.run calls this near the end, after actions and screenshot capture, so the final response includes current tab metadata along with the action output.


##### `BrowserComputerSession.tab_titles`  (lines 76–76)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: This is part of the session contract. It promises to return the titles of open browser tabs.

**Data flow**: No direct input is needed. The session reads the browser’s known tabs and returns their title text as a list.

**Call relations**: BrowserComputer.run asks for tab titles after performing actions. It passes those titles to sign_in_warning so the response can include a reminder if the browser appears to be on a sign-in or account-creation page.


##### `BrowserComputerSession.call_on`  (lines 78–84)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the session contract. It promises to run a small JavaScript function on a specific browser object, such as an element on the page.

**Data flow**: The caller provides a session id, an object id, JavaScript function text, and optional arguments. The session runs that function in the browser and returns the JSON-like result.

**Call relations**: BrowserComputer._select_reminder uses this when it has found a select dropdown element and needs to inspect its options. The real browser-session implementation performs the actual remote call.


##### `BrowserComputerSession.resolve_ref`  (lines 86–86)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserNode, int]
```

**Purpose**: This is part of the session contract. It promises to turn a page reference, such as a ref returned by page-reading tools, into the browser node needed for DOM commands.

**Data flow**: It receives a tab and a reference string. It resolves that reference into a browser node plus a backend node id, which is the browser’s internal identifier for the page element.

**Call relations**: BrowserComputer.act uses this for scroll_to actions. If the reference cannot be resolved, the action reports that the caller should re-read the page and use a fresh reference.


##### `BrowserComputerSession.ref_point`  (lines 88–88)

```
async def ref_point(self, tab: Any, ref: str) -> tuple[int, int]
```

**Purpose**: This is part of the session contract. It promises to find a clickable screen point for a referenced page element.

**Data flow**: It receives a tab and a reference string. It looks up the element and returns an x,y point in the browser viewport.

**Call relations**: BrowserComputer.point uses this when an action names a page ref instead of giving raw coordinates. This lets higher-level code click or scroll relative to page elements without knowing their exact pixel positions.


##### `BrowserComputer.run`  (lines 97–163)

```
async def run(self, args: JsonDict) -> JsonDict
```

**Purpose**: This is the main batch runner. It takes a caller’s requested browser actions, performs them in order, waits for the page when needed, and returns a human-readable result plus a screenshot.

**Data flow**: It receives a dictionary containing a tab id and an actions list. It chooses the tab, validates each action, adjusts coordinates to the real viewport, runs actions in batches, collects messages, marks downloads as reported, captures a screenshot, optionally draws a dot on the last click, adds safety reminders, and returns tab info, output text, last click coordinates, and the screenshot as base64 text.

**Call relations**: This is the top-level flow inside the file. It calls int_or_none to read the tab id, uses ComputerAction.model_validate and fixup_actions to prepare actions, calls BrowserComputer.act for each action, calls BrowserComputer._select_reminder after clicks when useful, uses BrowserComputer._to_model for reporting coordinates, calls sign_in_warning for safety messaging, and uses mark_click in a worker thread so image editing does not block the async event loop.

*Call graph*: calls 5 internal fn (_select_reminder, _to_model, act, int_or_none, sign_in_warning); 8 external calls (get_running_loop, model_validate, fixup_actions, split_at_waits, get, as_list, as_map, as_str).


##### `BrowserComputer.act`  (lines 165–244)

```
async def act(self, tab: ComputerTab, action: ComputerAction) -> tuple[str, tuple[int, int] | None]
```

**Purpose**: This function performs one browser action, such as clicking, typing, pressing a key, waiting, scrolling, or taking a screenshot. It turns a single high-level instruction into the exact lower-level operation the browser needs.

**Data flow**: It receives the current tab and one parsed ComputerAction. It first finds the action point if one is needed, then branches by action type: clicks become mouse press and release events, typing becomes keyboard events, waits become a timed sleep, scrolls become wheel events, and scroll_to becomes a DOM command. It returns a short message describing what happened and, for pointer actions, the viewport point used.

**Call relations**: BrowserComputer.run calls this for every action in the request. BrowserComputer.act delegates the detailed work to BrowserComputer.point, BrowserComputer._click, BrowserComputer._drag, BrowserComputer._scroll, BrowserComputer._dispatch, BrowserComputer._to_viewport, BrowserComputer._to_model, require_point, and require_coord so each small helper does one clear job.

*Call graph*: calls 9 internal fn (_click, _dispatch, _drag, _scroll, _to_model, _to_viewport, point, require_coord, require_point); called by 1 (run); 7 external calls (__init__, __init__, __init__, sleep, press_combo, type_text, as_str).


##### `BrowserComputer.point`  (lines 246–251)

```
async def point(self, tab: ComputerTab, action: ComputerAction) -> tuple[int, int] | None
```

**Purpose**: This function decides where on the browser page an action should happen. It supports either a page element reference or explicit coordinates.

**Data flow**: It receives a tab and an action. If the action has a ref, it asks the browser session for that element’s point; if it has coordinates, it converts them from model space to viewport space; if it has neither, it returns nothing.

**Call relations**: BrowserComputer.act calls this before deciding how to perform an action. It calls BrowserComputer._to_viewport when coordinates need conversion, and it relies on the session’s ref_point method when the caller used an element reference.

*Call graph*: calls 1 internal fn (_to_viewport); called by 1 (act).


##### `BrowserComputer._to_viewport`  (lines 253–255)

```
def _to_viewport(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts coordinates from the caller’s model-sized screen into the actual browser viewport. It keeps clicks landing in the right place even when the model’s coordinate grid and the browser’s real pixel size differ.

**Data flow**: It receives an x,y coordinate in model space. It wraps it as a Coord, scales it using the known viewport size and model size, and returns the matching x,y coordinate in viewport pixels.

**Call relations**: BrowserComputer.point uses this for direct coordinate actions, and BrowserComputer.act uses it for drag starts and default scroll positions. It hands the conversion work to model_to_viewport from the coordinate module.

*Call graph*: called by 2 (act, point); 2 external calls (__init__, model_to_viewport).


##### `BrowserComputer._to_model`  (lines 257–259)

```
def _to_model(self, coord: tuple[int, int]) -> tuple[int, int]
```

**Purpose**: This helper converts real browser viewport coordinates back into the caller’s model coordinate system. It is mainly used so returned messages use the same coordinate language the caller understands.

**Data flow**: It receives an x,y coordinate in viewport pixels. It wraps it as a Coord, scales it using the viewport size and model size, and returns the matching x,y coordinate in model space.

**Call relations**: BrowserComputer.act uses this when describing clicks and drags, and BrowserComputer.run uses it when returning the last click. It hands the conversion work to viewport_to_model from the coordinate module.

*Call graph*: called by 2 (act, run); 2 external calls (__init__, viewport_to_model).


##### `BrowserComputer._select_reminder`  (lines 261–290)

```
async def _select_reminder(self, tab: ComputerTab, point: tuple[int, int]) -> str | None
```

**Purpose**: This helper checks whether a click landed on a native HTML select dropdown and, if so, prepares advice for using it correctly. Native select menus often cannot be controlled by simply clicking the visible options in this remote-browser setup.

**Data flow**: It receives a tab and a clicked viewport point. It asks the browser what element is at that point, walks up to a select element if present, reads up to ten option labels and the total option count, tries to find a reusable element reference, and returns a reminder message. If anything fails or the click was not on a select, it returns nothing.

**Call relations**: BrowserComputer.run calls this after the first click-like action that has a point. It uses the browser connection, BrowserComputerSession.call_on, and select_reminder to turn raw page inspection into a clear instruction for the caller.

*Call graph*: calls 1 internal fn (select_reminder); called by 1 (run); 2 external calls (as_list, as_map).


##### `BrowserComputer._dispatch`  (lines 292–294)

```
async def _dispatch(self, tab: ComputerTab, calls: list[CdpCall]) -> None
```

**Purpose**: This helper sends a prepared list of keyboard-related CDP commands to the browser. It is used for typing text and pressing key combinations.

**Data flow**: It receives a tab and a list of method-and-parameter pairs. It sends each command over the browser connection using the tab’s session id and returns nothing after all commands have been sent.

**Call relations**: BrowserComputer.act calls this for type and key actions after key helpers have translated text or key combinations into CDP calls. This keeps the action logic separate from the repeated send loop.

*Call graph*: called by 1 (act).


##### `BrowserComputer._mouse_event`  (lines 296–299)

```
async def _mouse_event(self, tab: ComputerTab, params: JsonDict) -> None
```

**Purpose**: This helper sends one mouse event to the browser. It is the shared doorway for mouse movement, clicks, drags, and scroll-wheel events.

**Data flow**: It receives a tab and a dictionary of mouse event details, such as event type, button, coordinates, and wheel delta. It sends an Input.dispatchMouseEvent command through the browser connection and changes the browser page by simulating that event.

**Call relations**: BrowserComputer._click, BrowserComputer._drag, and BrowserComputer._scroll all call this. Those higher-level helpers decide the event sequence, while this function performs the actual CDP send.

*Call graph*: called by 3 (_click, _drag, _scroll).


##### `BrowserComputer._click`  (lines 301–340)

```
async def _click(self, tab: ComputerTab, x: int, y: int, button: str, click_count: int) -> None
```

**Purpose**: This helper performs a browser click at a specific viewport point. It supports left, right, double, and triple clicks by sending the same kind of events a real mouse would send.

**Data flow**: It receives a tab, x,y coordinates, a mouse button name, and a click count. It reads the current keyboard modifiers, moves the mouse to the point, then sends press and release events the requested number of times. It returns nothing, but the page receives the click.

**Call relations**: BrowserComputer.act calls this for left_click, double_click, triple_click, and right_click actions. It uses modifiers_mask to include currently pressed Shift, Ctrl, Alt, or similar keys, and it sends each event through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._drag`  (lines 342–395)

```
async def _drag(self, tab: ComputerTab, x0: int, y0: int, x1: int, y1: int) -> None
```

**Purpose**: This helper performs a left-button drag from one point to another. It moves in small steps because many web pages only recognize drag-and-drop after seeing gradual movement, not a sudden jump.

**Data flow**: It receives a tab and start and end viewport coordinates. It reads the active keyboard modifiers, moves to the start, presses the left mouse button, sends several intermediate mouse moves toward the end, and releases the button. It returns nothing, but the page experiences a drag gesture.

**Call relations**: BrowserComputer.act calls this for left_click_drag actions. It uses modifiers_mask for keyboard state and BrowserComputer._mouse_event for every mouse movement, press, and release.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `BrowserComputer._scroll`  (lines 397–421)

```
async def _scroll(self, tab: ComputerTab, x: int, y: int, dx: float, dy: float) -> None
```

**Purpose**: This helper performs a scroll-wheel action at a specific point on the page. The point matters because many pages scroll the area under the mouse, not always the whole window.

**Data flow**: It receives a tab, x,y viewport coordinates, and horizontal and vertical scroll amounts. It reads current keyboard modifiers, moves the mouse to the point, sends a wheel event with the requested deltas, and returns nothing.

**Call relations**: BrowserComputer.act calls this for scroll actions after it has calculated the scroll direction and distance. BrowserComputer._scroll sends its mouse move and wheel event through BrowserComputer._mouse_event.

*Call graph*: calls 1 internal fn (_mouse_event); called by 1 (act); 1 external calls (modifiers_mask).


##### `sign_in_warning`  (lines 424–428)

```
def sign_in_warning(titles: list[str]) -> str | None
```

**Purpose**: This helper looks for signs that the browser is on a sign-in, login, registration, or account-creation page. It helps enforce the rule that the user should confirm before signing in or creating accounts.

**Data flow**: It receives a list of tab titles. It lowercases them, checks for sign-in-related keywords, and returns a safety reminder if any are found; otherwise it returns nothing.

**Call relations**: BrowserComputer.run calls this after reading current tab titles. Its result is added to the final output as a system reminder when relevant.

*Call graph*: called by 1 (run).


##### `select_reminder`  (lines 431–443)

```
def select_reminder(ref: str | None, options: list[str], total: int) -> str
```

**Purpose**: This helper builds a clear message explaining how to interact with a native select dropdown. It tells the caller to use form_input rather than trying to click dropdown options directly.

**Data flow**: It receives an optional element reference, a list of visible option labels, and the total number of options. It formats the shown options, notes if more exist, chooses instructions based on whether a ref is available, and returns one reminder string.

**Call relations**: BrowserComputer._select_reminder calls this after inspecting a clicked select element. This function does not talk to the browser; it only turns the gathered dropdown details into a helpful message.

*Call graph*: called by 1 (_select_reminder).


##### `mark_click`  (lines 446–462)

```
def mark_click(screenshot_b64: str, point: tuple[int, int]) -> str
```

**Purpose**: This helper draws a small blue marker on a screenshot at the last click position. It makes the returned screenshot easier to interpret because the caller can see exactly where the click happened.

**Data flow**: It receives a base64-encoded screenshot and an x,y point. It decodes the image, creates a transparent overlay, draws a circle at the point, combines the overlay with the screenshot, saves it as a JPEG, encodes it back to base64, and returns the new image text.

**Call relations**: BrowserComputer.run uses this after capturing a screenshot when there was a click. It is run through an executor because image decoding and drawing are blocking CPU work, while the rest of the browser flow is asynchronous.

*Call graph*: 7 external calls (alpha_composite, new, open, Draw, b64decode, b64encode, BytesIO).


##### `int_or_none`  (lines 465–474)

```
def int_or_none(value: Json | None) -> int | None
```

**Purpose**: This small helper reads a tab id that may arrive as an integer, float, string, or missing value. It normalizes those forms into either an integer id or no id.

**Data flow**: It receives a JSON-like value. If the value is an int, it returns it; if it is a float or non-empty string, it converts it to an int; otherwise it returns None.

**Call relations**: BrowserComputer.run calls this when reading args.get("tab_id") before asking the browser session for a page. It keeps tab-id cleanup out of the main run flow.

*Call graph*: called by 1 (run).


##### `require_point`  (lines 477–480)

```
def require_point(point: tuple[int, int] | None, action: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that an action has a usable point when one is required. It turns a missing coordinate or reference into a clear validation error.

**Data flow**: It receives a possible x,y point and the action name. If the point exists, it returns it unchanged; if it is missing, it raises a ValidationError explaining that the action requires a coordinate or ref.

**Call relations**: BrowserComputer.act calls this before click, right-click, and drag-end operations. This prevents lower-level mouse code from receiving missing coordinates and failing unclearly.

*Call graph*: called by 1 (act); 1 external calls (__init__).


##### `require_coord`  (lines 483–486)

```
def require_coord(value: tuple[int, int] | None, path: str) -> tuple[int, int]
```

**Purpose**: This helper enforces that a coordinate field is present when an action needs it. It is used for values such as the start point of a drag.

**Data flow**: It receives a possible coordinate and the field path name. If the coordinate exists, it returns it unchanged; if it is missing, it raises a ValidationError naming the required field.

**Call relations**: BrowserComputer.act calls this for the start_coordinate of a left_click_drag action. It catches bad input before coordinate conversion and mouse-event sending begin.

*Call graph*: called by 1 (act); 1 external calls (__init__).


### Downloads
Download handling detects, forces, waits for, and reads files saved by the browser.

### `extensions/browser/ufo_ext_browser/bua/downloads.py`

`domain_logic` · `request handling and download events`

Browsers do not treat every file link as a download. A PDF, for example, may open inside Chrome’s viewer, where the automation agent may not be able to read the actual bytes. This file solves that problem by watching browser network events and, when needed, rewriting a top-level PDF response so Chrome saves it as an attachment instead of displaying it inline.

The file works like a small traffic controller. When Chrome pauses a network request, BrowserDownloads.on_fetch_paused decides whether the request should simply continue or whether the response headers should be changed to force a download. It must always release paused requests; otherwise the page would hang, like a car stopped forever at a red light.

The file also tracks download lifecycle events. When a download begins, it records a Download object with its browser-provided id, suggested filename, and current state. As progress events arrive, it updates that state. Other code can then ask whether a navigation turned into a download, or wait until a download completes.

Once complete, BrowserDownloads.wait reads the downloaded file from disk using the browser’s download id as the filename, encodes the bytes as base64 text, and returns the original filename, encoded content, and size. Base64 is used because raw binary data does not travel safely in ordinary JSON messages.

#### Function details

##### `BrowserDownloadCdp.send`  (lines 43–49)

```
async def send(self, method: str, params: JsonDict | None=None, *, session_id: str | None=None) -> JsonDict
```

**Purpose**: This protocol method describes the browser connection’s ability to send a command to Chrome through the Chrome DevTools Protocol, often called CDP. CDP is Chrome’s control channel for automation, like a remote control for browser internals.

**Data flow**: It receives a command name, optional JSON-style parameters, and an optional session id that identifies a browser target or frame. An implementation sends that command to Chrome and returns Chrome’s JSON-style reply.

**Call relations**: BrowserDownloads uses this capability inside _continue_request and _continue_response when it needs to tell Chrome to release a paused network request or response. This file only states the shape of the method; another part of the system provides the real connection.


##### `BrowserDownloadSession.connection`  (lines 55–55)

```
def connection(self) -> BrowserDownloadCdp
```

**Purpose**: This protocol method describes how the download helper gets access to the browser control connection. It gives BrowserDownloads a way to send CDP commands without knowing the concrete browser session class.

**Data flow**: It takes the current browser session object and returns an object that can send commands to Chrome. It does not transform download data itself; it supplies the communication channel used by other methods.

**Call relations**: BrowserDownloads._continue_request and BrowserDownloads._continue_response call this when releasing paused fetches. The protocol keeps this file loosely connected to the wider browser session implementation.


##### `BrowserDownloadSession.spawn_background`  (lines 57–57)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: This protocol method describes how to start a small asynchronous task without blocking the current event handler. It is used because paused browser requests must be released, but the event callback itself should stay quick.

**Data flow**: It receives a coroutine, which is an asynchronous unit of work waiting to be run. The browser session schedules it in the background, causing the requested work to happen later while the caller can return immediately.

**Call relations**: BrowserDownloads.on_fetch_paused uses this to schedule _continue_request or _continue_response. That means network events can be acknowledged promptly while the actual CDP command runs asynchronously.


##### `BrowserDownloadSession.is_top_level_frame`  (lines 59–59)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: This protocol method answers whether a browser event belongs to the main page frame, rather than an embedded frame such as an iframe. This matters because only main-page PDF navigations should be forced into downloads.

**Data flow**: It receives a session id and a frame id from a browser event. It checks them against the browser session’s knowledge of frames and returns true if the event is for the top-level page.

**Call relations**: BrowserDownloads.on_fetch_paused calls this before forcing a PDF response to download. This prevents embedded PDFs from being changed unexpectedly when a page intentionally displays them inside itself.


##### `BrowserDownloads.on_fetch_paused`  (lines 67–88)

```
def on_fetch_paused(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This is the main decision point for paused browser network traffic. It makes sure every paused request is released, and it only changes responses when a top-level PDF would otherwise open in Chrome’s unreadable viewer.

**Data flow**: It receives the browser’s paused-request details and an optional session id. It checks for a request id, whether the pause happened at the response stage, the response headers, the content type, and whether the frame is the top-level page. It then schedules either a simple continue or a response continue that may add a download-forcing header.

**Call relations**: This method is called when the browser reports a Fetch pause event. It calls _content_type to understand the response headers, asks the browser session whether the frame is top-level, and uses spawn_background to hand off to _continue_request or _continue_response so the page does not stay frozen.

*Call graph*: calls 3 internal fn (_continue_request, _continue_response, _content_type); 1 external calls (get).


##### `BrowserDownloads._continue_request`  (lines 90–96)

```
async def _continue_request(self, session_id: str | None, request_id: str) -> None
```

**Purpose**: This method releases a paused browser request when there is no response yet to inspect or change. Its job is to keep the page moving.

**Data flow**: It receives a session id and request id. It sends Chrome a Fetch.continueRequest command for that request. If Chrome rejects the command, times out, or the connection is no longer usable, it logs a warning instead of crashing the caller.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this method when a Fetch pause happens before a response status code is available. It uses the browser session’s connection to talk to Chrome.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads._continue_response`  (lines 98–126)

```
async def _continue_response(self, session_id: str | None, request_id: str, response_code: int, headers: list[Json], force: bool) -> None
```

**Purpose**: This method releases a paused browser response, optionally changing its headers so Chrome treats it as a file download. It is the part that turns certain inline PDFs into saved files.

**Data flow**: It receives a session id, request id, HTTP response code, response headers, and a true-or-false force flag. If forcing is requested, it removes any existing Content-Disposition header and adds Content-Disposition: attachment, then sends Chrome a Fetch.continueResponse command. On communication failure, it logs a warning.

**Call relations**: BrowserDownloads.on_fetch_paused schedules this after deciding that a response is safe to continue and possibly should be forced into a download. It hands the final instruction to Chrome through the browser connection.

*Call graph*: called by 1 (on_fetch_paused).


##### `BrowserDownloads.on_download_begin`  (lines 128–135)

```
def on_download_begin(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This method records that Chrome has started a new download. It creates the local bookkeeping entry that later methods use to track completion and read the file.

**Data flow**: It receives download-start event details from Chrome. It pulls out the download guid, which is Chrome’s unique id for the download, and the suggested filename, falling back to 'download' if none is provided. It appends a new Download record with state set to inProgress.

**Call relations**: This is called when the browser emits a download-begin event. BrowserDownloads.became_download and BrowserDownloads.wait later inspect the downloads list that this method adds to.

*Call graph*: 2 external calls (__init__, get).


##### `BrowserDownloads.on_download_progress`  (lines 137–142)

```
def on_download_progress(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: This method updates the saved state of a download as Chrome reports progress. It lets the rest of the system know when a download has completed.

**Data flow**: It receives a progress event with a guid and state. It searches the browser session’s download list for the matching guid and replaces that record’s state with the latest value from Chrome.

**Call relations**: This is called for browser download-progress events after on_download_begin has created the record. BrowserDownloads.wait depends on these updates to know when it is safe to read the finished file.

*Call graph*: 1 external calls (get).


##### `BrowserDownloads.became_download`  (lines 144–150)

```
async def became_download(self, before_count: int) -> bool
```

**Purpose**: This method briefly checks whether an action that looked like navigation actually turned into a browser download. It is useful after clicking a link, because a download may be reported slightly after the click.

**Data flow**: It receives the number of downloads that existed before the action. For up to a short grace period, it repeatedly compares the current download count with that earlier count, sleeping briefly between checks. It returns true if a new download appears, otherwise false.

**Call relations**: Other navigation or click-handling code can call this after starting an action. It watches the same downloads list filled by on_download_begin.

*Call graph*: 2 external calls (sleep, monotonic).


##### `BrowserDownloads.wait`  (lines 152–170)

```
async def wait(self, args: JsonDict, download_dir: str) -> JsonDict
```

**Purpose**: This method waits for a browser download to finish, then returns the downloaded file in a JSON-friendly form. It gives callers the actual file bytes without requiring them to know where Chrome stored the file.

**Data flow**: It receives arguments that may include a timeout and the browser download directory. It turns the timeout into a number, waits until at least one tracked download has state 'completed', reads the newest completed file from disk using its guid as the filename, base64-encodes the bytes, and returns the suggested filename, encoded content, and byte size. If nothing completes before the deadline, it raises a timeout error.

**Call relations**: This method is called when higher-level code wants to collect a finished download. It relies on on_download_begin and on_download_progress having kept the download list current, and it uses float_or_default to interpret the optional timeout.

*Call graph*: calls 1 internal fn (float_or_default); 6 external calls (sleep, to_thread, b64encode, Path, monotonic, get).


##### `float_or_default`  (lines 173–182)

```
def float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: This helper turns a value into a floating-point number, or uses a default when no value was provided. It gives download waiting code a clean way to accept numeric timeouts from JSON input.

**Data flow**: It receives a value that may be a number, a non-empty string, None, or something invalid, plus a default number. Numbers are converted directly, strings are parsed as numbers, None becomes the default, and other types cause a validation error.

**Call relations**: BrowserDownloads.wait calls this when reading its timeout argument. If the caller provides a bad timeout type, this helper raises the clear error before waiting begins.

*Call graph*: called by 1 (wait); 1 external calls (__init__).


##### `_content_type`  (lines 185–189)

```
def _content_type(headers: list[Json]) -> str
```

**Purpose**: This helper finds the response’s Content-Type header and normalizes it for comparison. It lets the download logic recognize PDFs even when the header includes extra details such as a character set.

**Data flow**: It receives a list of header-like JSON values. It looks for a dictionary whose name is Content-Type, ignores case, takes the value before any semicolon, trims spaces, lowercases it, and returns that plain media type. If no content type is found, it returns an empty string.

**Call relations**: BrowserDownloads.on_fetch_paused calls this while deciding whether a top-level response is one of the types that should be forced into a download.

*Call graph*: called by 1 (on_fetch_paused).


### Forms and keyboard input
Form filling, file upload, and keyboard synthesis support structured text entry and field manipulation.

### `extensions/browser/ufo_ext_browser/bua/forms.py`

`domain_logic` · `request handling`

Web pages do not all accept input the same way. A text box, a checkbox, a dropdown, a file picker, and an editable area each need slightly different treatment. This file is the form-control helper for the browser extension code: it receives a page reference, finds the real browser element behind that reference, and then performs the requested form action.

The main class, BrowserForms, offers two actions. upload_file uses the browser’s debugging protocol to attach local file paths to a file input element. input resolves a saved page reference into a live browser object, then runs a small JavaScript function on that element. That JavaScript knows the common cases: checkboxes and radio buttons use checked, dropdowns use value, editable regions use textContent, and normal inputs use value. It also fires input and change events, which is important because many websites only notice changes when those events happen.

The BrowserFormSession protocol describes what the surrounding browser session must provide: access to a page, a protocol connection, a way to run JavaScript on an element, and a way to turn a human-facing reference into a browser node. If a reference is stale or points to the wrong kind of element, the file raises HallucinationError with guidance to re-read the page and use a valid reference.

#### Function details

##### `BrowserFormSession.page`  (lines 35–35)

```
async def page(self, tab_id: int | None=None) -> Any
```

**Purpose**: This is part of the expected browser-session interface. It should return the browser page or tab that a form action will work on.

**Data flow**: It receives an optional tab identifier. The concrete browser session uses that to choose a page and returns whatever page object the rest of the browser layer understands.

**Call relations**: BrowserForms.upload_file and BrowserForms.input call on this capability before touching a form element, because they first need to know which tab contains the referenced field.


##### `BrowserFormSession.connection`  (lines 37–37)

```
def connection(self) -> Cdp
```

**Purpose**: This is part of the expected browser-session interface. It should provide the connection to the browser’s control channel, called CDP here, which is the Chrome DevTools Protocol: a structured way to send commands to the browser.

**Data flow**: It takes no input beyond the session object. It returns a connection object that can send low-level browser commands.

**Call relations**: BrowserForms uses this connection when it needs the browser itself to do something, such as resolving a page node or assigning files to a file input.


##### `BrowserFormSession.call_on`  (lines 39–45)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: This is part of the expected browser-session interface. It runs a JavaScript function on a specific browser object, such as an input field on the page.

**Data flow**: It receives a browser session id, an object id, JavaScript source code, and optional argument values. The concrete session sends that work to the browser and returns the JSON-like result from the JavaScript call.

**Call relations**: BrowserForms.input uses this after it has found the real browser object for a field. It hands off the form-filling JavaScript and the requested value so the browser changes the element directly.


##### `BrowserFormSession.resolve_ref`  (lines 47–47)

```
def resolve_ref(self, tab: Any, ref: str) -> tuple[BrowserFormNode, int]
```

**Purpose**: This is part of the expected browser-session interface. It turns a page reference, such as one returned by a previous page-reading step, into the internal browser node needed to act on that element.

**Data flow**: It receives a page object and a reference string. It looks up the matching element and returns both a node object, which includes the browser session id, and a backend node id, which the browser protocol can use.

**Call relations**: BrowserForms.upload_file and BrowserForms.input both depend on this step. The user-facing reference is like a coat-check ticket; resolve_ref turns the ticket back into the actual item the browser can operate on.


##### `BrowserForms.upload_file`  (lines 54–71)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: This fills a web page’s file upload control with one or more local file paths. It is used when automation needs to choose files for an HTML file input, the same kind of control a person would click before selecting a file.

**Data flow**: It reads tab_id, ref, and files from the input dictionary. It converts tab_id into a number if present, checks that ref is a string, and checks that files is a list of strings. Then it opens the chosen tab, resolves the reference to a browser node, and sends a browser protocol command to attach those files to that input. On success it returns the same reference and the accepted file paths. If the reference is not a file input, it raises a clear HallucinationError instead of pretending the action worked.

**Call relations**: This method starts from a high-level tool request and uses the session’s page lookup, reference resolver, and browser protocol connection to perform the upload. It relies on _tab_id to normalize the tab choice and uses the wire helpers to reject malformed request data before sending anything to the browser.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_list, as_str).


##### `BrowserForms.input`  (lines 73–88)

```
async def input(self, args: JsonDict) -> JsonDict
```

**Purpose**: This changes the value of a normal form element, such as a text box, checkbox, radio button, dropdown, or editable text region. It also triggers the page events that websites usually listen for after a user changes a field.

**Data flow**: It reads tab_id, ref, and value from the input dictionary. It opens the chosen tab, validates the reference string, and resolves that reference into a browser node. It asks the browser to turn that node into a live JavaScript object id, then runs the built-in JS_FORM_INPUT function on it with the requested value. The returned value is whatever that JavaScript reports after changing the element. If the reference cannot be resolved, it raises HallucinationError telling the caller to re-read the page and use a fresh reference.

**Call relations**: This method is the main path for form filling. It uses _tab_id to interpret the optional tab choice, uses the session to find and resolve the target element, and then hands off to BrowserFormSession.call_on so the change happens inside the page itself.

*Call graph*: calls 1 internal fn (_tab_id); 4 external calls (__init__, get, as_map, as_str).


##### `_tab_id`  (lines 91–100)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This small helper turns the optional tab identifier from request data into either an integer tab id or no tab choice at all. It accepts numbers and non-empty strings because external requests may encode the same id in different simple forms.

**Data flow**: It receives a JSON-like value. If the value is an integer, it returns it unchanged; if it is a float or non-empty string, it converts it to an integer; otherwise it returns None. Returning None means “use the default or current page.”

**Call relations**: BrowserForms.upload_file and BrowserForms.input call this at the start of their work. It keeps tab selection rules in one place so both form actions interpret tab_id consistently.

*Call graph*: called by 2 (input, upload_file).


### `extensions/browser/ufo_ext_browser/bua/keys.py`

`io_transport` · `browser action execution`

Browsers do not just need the letter someone typed. They expect a detailed keyboard event: the physical key code, the visible character, whether Shift or Control is held, whether the key is on the number pad, and sometimes special Mac editing commands. This file supplies that translation layer.

Most of the file is a keyboard map for a US keyboard, plus a table of Mac text-editing shortcuts. Think of it like a bilingual dictionary: on one side are user-friendly key names and characters, and on the other side are the exact fields Chrome expects in its input protocol.

The file first builds a richer lookup table so keys can be found by physical code, character, or alias such as "Enter" and newline. A small KeyboardState records which modifier keys, such as Shift or Alt, are currently down and which physical keys are being held. The public helpers then use that state to create Chrome DevTools Protocol calls. A key-down updates the state and emits a key event. A key-up clears the state and emits a release event. A key combination presses each key in order and releases them in reverse order. Text typing uses real per-character key events for short text, but for longer text it uses one direct insertText call, which is faster and avoids sending many separate events.

#### Function details

##### `_build_layout_closure`  (lines 313–341)

```
def _build_layout_closure(layout: dict[str, KeyDefinition]) -> dict[str, KeyDescription]
```

**Purpose**: This builds the practical lookup table used by the rest of the file. It takes the raw US keyboard layout and adds convenient ways to find the same key, such as by code, visible character, shifted character, or simple alias.

**Data flow**: It starts with the raw layout entries, where each physical key has details like its normal character and Shift character. For each entry, it creates a KeyDescription, adds a shifted version when needed, and stores aliases such as "Shift" for "ShiftLeft" or newline for "Enter". The result is a dictionary that later code can use to quickly turn many forms of key input into one complete key description.

**Call relations**: This runs when the module prepares its shared LAYOUT_CLOSURE table. Later, key_down and key_up depend on that table through _description_for, so this function is the setup step that makes all later keyboard translation possible.

*Call graph*: 2 external calls (__init__, replace).


##### `modifiers_mask`  (lines 409–410)

```
def modifiers_mask(modifiers: set[str]) -> int
```

**Purpose**: This converts the currently held modifier keys into the small number Chrome expects in keyboard events. A modifier key is a key like Shift, Control, Alt, or Meta that changes what another key means.

**Data flow**: It receives a set of modifier names that are currently pressed. It checks each known modifier and adds its assigned bit value when present. It returns one integer that represents the whole modifier state.

**Call relations**: key_down and key_up call this just before building their Chrome DevTools Protocol event. It gives those events the correct modifier field, so Chrome can tell, for example, the difference between pressing A and pressing Shift+A.

*Call graph*: called by 2 (key_down, key_up).


##### `_description_for`  (lines 413–421)

```
def _description_for(state: KeyboardState, key: str) -> KeyDescription
```

**Purpose**: This finds the full browser-facing description for a requested key. It also adjusts that description when Shift or other modifiers are currently held.

**Data flow**: It receives the current KeyboardState and a key name or character. It looks the key up in the prepared keyboard table. If the key is unknown, it raises a validation error so bad input is rejected early. If Shift is held and the key has a shifted form, it uses that shifted form. If other modifiers are held, it clears the text field because shortcut keys usually should not type visible characters. It returns the final KeyDescription.

**Call relations**: key_down and key_up both call this before they can emit an event. It is the gatekeeper that turns a user’s key request into the exact key details those functions need.

*Call graph*: called by 2 (key_down, key_up); 2 external calls (__init__, replace).


##### `_mac_commands`  (lines 424–428)

```
def _mac_commands(code: str, modifiers: set[str]) -> list[str]
```

**Purpose**: This adds Mac-specific editing command names for keyboard shortcuts. These command names help Chrome on macOS behave like native Mac text editing, such as moving by word or selecting text.

**Data flow**: It receives a physical key code and the set of currently held modifiers. It builds a shortcut name like "Shift+Meta+ArrowLeft", looks it up in the Mac editing-command table, removes trailing colons from command names, and filters out insert commands. It returns a list of command names to include in the key event.

**Call relations**: key_down calls this only when the caller says the browser is running as a Mac. The returned commands are placed into the outgoing Chrome event so Mac shortcut behavior is preserved.

*Call graph*: called by 1 (key_down).


##### `key_down`  (lines 431–455)

```
def key_down(state: KeyboardState, key: str, is_mac: bool) -> CdpCall
```

**Purpose**: This creates the Chrome keyboard message for pressing a key down. It also updates the remembered keyboard state so later events know which keys and modifiers are still held.

**Data flow**: It receives the current KeyboardState, a key name or character, and whether Mac behavior is needed. It gets the key description, checks whether this is an auto-repeat because the key is already pressed, records the key as pressed, and records modifier keys when appropriate. On Mac it also asks for matching editing commands. It returns one Chrome DevTools Protocol call named Input.dispatchKeyEvent with all the fields Chrome needs.

**Call relations**: press_combo uses this to press every key in a shortcut, and type_text uses it for characters that can be represented by the keyboard layout. Inside, it relies on _description_for for key lookup, _mac_commands for Mac behavior, and modifiers_mask for the numeric modifier value.

*Call graph*: calls 3 internal fn (_description_for, _mac_commands, modifiers_mask); called by 2 (press_combo, type_text).


##### `key_up`  (lines 458–472)

```
def key_up(state: KeyboardState, key: str) -> CdpCall
```

**Purpose**: This creates the Chrome keyboard message for releasing a key. It also removes that key, and any matching modifier, from the remembered keyboard state.

**Data flow**: It receives the current KeyboardState and a key name or character. It finds the matching key description, removes the key from the pressed-key set, removes it from the pressed-modifier set if it is a modifier, computes the remaining modifier mask, and returns one Input.dispatchKeyEvent call of type keyUp.

**Call relations**: press_combo calls this after pressing keys, releasing them in reverse order, and type_text calls it after each synthesized character press. It shares the same key lookup path as key_down through _description_for.

*Call graph*: calls 2 internal fn (_description_for, modifiers_mask); called by 2 (press_combo, type_text).


##### `press_combo`  (lines 475–482)

```
def press_combo(state: KeyboardState, combo: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This turns a shortcut string such as "Ctrl+Shift+P" into the sequence of browser events needed to press and release that shortcut. It accepts common shortcut names and normalizes them to the names used by the keyboard table.

**Data flow**: It receives the keyboard state, a plus-separated combo string, and whether Mac behavior is needed. It splits the string into parts, trims spaces, translates aliases like "ctrl" to "Control" and "cmd" to "Meta", and rejects an empty combo. It then creates key-down events for the keys in order and key-up events for the same keys in reverse order. It returns the full list of Chrome DevTools Protocol calls.

**Call relations**: This is the shortcut-level helper built on top of key_down and key_up. It does not itself know the details of Chrome’s event fields; instead it delegates each press and release to those lower-level functions.

*Call graph*: calls 2 internal fn (key_down, key_up); 1 external calls (__init__).


##### `type_text`  (lines 485–497)

```
def type_text(state: KeyboardState, text: str, is_mac: bool) -> list[CdpCall]
```

**Purpose**: This turns a text string into browser input. It uses realistic key events for short text, but switches to direct text insertion for long text to avoid sending many small events.

**Data flow**: It receives the keyboard state, the text to type, and whether Mac behavior is needed. If the text is longer than the configured limit, it returns one Input.insertText call containing the whole string. Otherwise, it walks through each character. Characters found in the keyboard layout become a key-down followed by a key-up; characters not found in the layout become direct insertText calls. It returns the ordered list of browser protocol calls.

**Call relations**: This is the text-entry helper built on top of key_down and key_up for normal keyboard characters. When a character cannot be represented by the US keyboard table, or when the whole string is long, it bypasses key synthesis and hands Chrome text directly through Input.insertText.

*Call graph*: calls 2 internal fn (key_down, key_up).

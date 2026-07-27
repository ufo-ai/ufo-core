# Page state, tabs, and element lookup  `stage-10.2.3`

This stage is the browser’s working memory during the main interaction loop. It keeps track of which tabs exist, what is on each page, and how a requested page element can be found again when it is time to click or type.

The tab controller keeps the “tab list” current. It opens, closes, and navigates tabs, and listens for browser events so the rest of the system knows when pages change. The content tools are the safe front door for reading a page: they fetch page text and provide simple search functions without exposing the lower-level browser machinery directly.

The page mapper turns a live browser page into a structured map that an AI model can understand, like turning a messy webpage into a labeled floor plan. The finder works with the accessibility tree, a plain-text view of visible controls such as buttons, links, and fields. It searches that tree, builds clean matches, and checks that any element name invented by the model points to a real target before an action is taken.

## Files in this stage

### Browser page state and lookup
Tab state management leads into page-content access, accessibility-tree search, and structured page mapping for resolving model element references.

### `extensions/browser/ufo_ext_browser/bua/tabs.py`

`domain_logic` · `request handling`

This file is the part of the browser extension code that treats browser tabs like a small, reliable workspace. Without it, the system would not know which tabs are open, which newly opened pages need to be attached to, or when a navigation has actually finished enough to be useful.

It talks to the browser through CDP, the Chrome DevTools Protocol, which is a command-and-event API for controlling Chrome-like browsers. Think of CDP as a remote control: this file presses buttons like “open a tab,” “go to this URL,” and “tell me the page title,” then waits for the browser to report what happened.

The `Tab` object stores the local memory for one browser tab: its browser target ID, its CDP session ID, keyboard state, and frame references. `BrowserTabs` is the main worker. It records which browser targets existed at startup, notices newly created or destroyed targets, attaches to new pages, enables the browser features it needs, and keeps its tab list in sync.

Navigation has a few careful details. Plain text like `example.com` is turned into `https://example.com`. Special words like `back` and `forward` use browser history instead of loading a URL. After navigation, the code waits for page “settling” signals, meaning the page has loaded and painted enough that later automation is less likely to race ahead too early. It also detects when a navigation is actually a download rather than a normal page.

#### Function details

##### `normalize_url`  (lines 24–29)

```
def normalize_url(url: str) -> str
```

**Purpose**: Turns a user-supplied destination into something the browser can navigate to. It leaves special commands and already complete URLs alone, but adds `https://` to simple site names.

**Data flow**: A string such as `example.com`, `https://example.com`, `back`, or `about:blank` goes in. The function checks whether it is a special browser command or already has a URL scheme such as `http:`. It returns the original string when it is already complete, or a new string with `https://` added.

**Call relations**: When `BrowserTabs.navigate` is asked to go somewhere, it calls this first so the rest of the navigation code can work with a clean target value.

*Call graph*: called by 1 (navigate); 1 external calls (match).


##### `Tab.__init__`  (lines 33–38)

```
def __init__(self, target_id: str, session_id: str) -> None
```

**Purpose**: Creates the local record for one browser tab. It stores the browser IDs needed to talk to that tab and prepares per-tab state such as keyboard state and frame tracking.

**Data flow**: A browser target ID and CDP session ID go in. The constructor saves them, creates a fresh keyboard state, starts an empty frame sequence map, and creates an initial top-level frame reference. The result is a `Tab` object ready for navigation and page inspection.

**Call relations**: After `BrowserTabs.attach_tab` successfully attaches to a browser page, it calls this constructor to make the project’s in-memory representation of that page.

*Call graph*: called by 1 (attach_tab); 2 external calls (__init__, __init__).


##### `Tab.frame_seq`  (lines 40–43)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Gives each frame in a tab a stable small number the first time it is seen. This is useful when frames need human-friendly or repeatable numbering instead of raw browser IDs.

**Data flow**: A frame ID goes in. If the frame has been seen before, its existing number is returned. If it is new, the function assigns the next number and returns it, changing the tab’s frame sequence map.

**Call relations**: This is a helper on `Tab` for other page or frame code that needs consistent frame numbering while working inside a tab.


##### `BrowserTabCdp.send`  (lines 54–59)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a method that sends a command to the browser through CDP. It is part of a protocol, meaning it describes what a compatible connection object must provide.

**Data flow**: A CDP method name, optional parameters, and an optional session ID go in. A compatible implementation sends that command to the browser and returns the browser’s JSON-like reply.

**Call relations**: Many `BrowserTabs` methods rely on this capability through `browser.connection()`, for actions such as creating tabs, attaching to targets, navigating, and enabling page events.


##### `BrowserTabCdp.expect`  (lines 61–61)

```
def expect(self, *events: str, session_id: str | None=None) -> asyncio.Future[JsonDict]
```

**Purpose**: Defines the expected shape of a method that starts waiting for one or more browser events. It lets code say, “I expect this event soon,” before sending the command that should trigger it.

**Data flow**: One or more event names and an optional session ID go in. A future comes out; a future is a placeholder for a result that will arrive later.

**Call relations**: `BrowserTabs._goto` and `BrowserTabs._history_step` use this pattern before navigation commands so they can wait for the browser’s confirmation event afterward.


##### `BrowserTabCdp.wait`  (lines 63–67)

```
async def wait(self, future: asyncio.Future[JsonDict], timeout: float) -> JsonDict
```

**Purpose**: Defines the expected shape of a method that waits for an expected browser event, but only up to a time limit. This prevents automation from hanging forever if the browser never sends the event.

**Data flow**: A future and a timeout in seconds go in. A compatible implementation waits until the future completes or the timeout is reached, then returns the event data or raises an error.

**Call relations**: `BrowserTabs._goto` and `BrowserTabs._history_step` call this after starting a navigation so they can pause until the browser reports progress.


##### `BrowserTabSession.connection`  (lines 77–77)

```
def connection(self) -> BrowserTabCdp
```

**Purpose**: Defines how the tab code gets the CDP connection used to talk to the browser. It is part of the session protocol that `BrowserTabs` expects.

**Data flow**: No ordinary data is passed in beyond the session object. A CDP connection object comes out, giving callers the ability to send commands and wait for events.

**Call relations**: Nearly every active operation in `BrowserTabs` begins by asking the browser session for this connection.


##### `BrowserTabSession.download_reader`  (lines 79–79)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Defines how the tab code gets the object that knows about browser downloads. This matters because some navigations do not show a page; they start a file download instead.

**Data flow**: The session object is read. A download reader comes out, which can answer questions about whether a new download appeared.

**Call relations**: `BrowserTabs._goto` uses this when navigation reports an error, so it can distinguish a failed page load from a successful download.


##### `BrowserTabSession.eval_js`  (lines 81–81)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Defines how the tab code can run JavaScript inside a browser tab. In this file it is used to ask the page for simple facts like its current URL and title.

**Data flow**: A session ID and a JavaScript expression go in. The browser evaluates the expression in that tab and returns the result as JSON-like data.

**Call relations**: `BrowserTabs.tab_info` relies on this to build tab summaries for navigation results and tab listings.


##### `BrowserTabs.remember_initial_targets`  (lines 89–93)

```
def remember_initial_targets(self, targets: JsonDict) -> None
```

**Purpose**: Records which browser pages already existed before this tab controller started watching. This prevents old tabs from being mistaken for newly opened tabs.

**Data flow**: A browser response containing target information goes in. The function reads each target ID and stores the set of IDs as the initial baseline in `browser.tab_events`. It does not return a value.

**Call relations**: This is used during setup of target tracking. Later, `BrowserTabs.on_target_created` compares new target events against this saved baseline.

*Call graph*: 3 external calls (get, as_list, as_map).


##### `BrowserTabs.on_target_created`  (lines 95–107)

```
def on_target_created(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Responds to the browser saying that a new target was created. A target is a browser-controlled thing such as a page; this function only records newly created page targets that should become tabs.

**Data flow**: An event payload and optional session ID go in. The function reads the target information, checks that it is a page with a string target ID, ignores targets that existed at startup or were already recorded, and appends the new ID to the pending-created list.

**Call relations**: Browser event dispatch code calls this when target creation events arrive. `BrowserTabs.sync` later consumes the recorded IDs and actually attaches to those tabs.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_target_destroyed`  (lines 109–112)

```
def on_target_destroyed(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Responds to the browser saying that a target was destroyed. It marks the matching target ID so the local tab list can remove it later.

**Data flow**: An event payload and optional session ID go in. If the payload contains a string target ID, that ID is added to the destroyed-target set. The function returns nothing but changes the stored tab event state.

**Call relations**: Browser event dispatch code calls this when tabs or pages disappear. `BrowserTabs.sync` later uses this record to remove closed tabs from `browser.tabs`.

*Call graph*: 1 external calls (get).


##### `BrowserTabs.on_frame_loading`  (lines 114–118)

```
def on_frame_loading(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loading when the browser reports that its top-level frame has started loading. The top-level frame is the main page, not an embedded iframe.

**Data flow**: A frame-loading event and session ID go in. The function checks that the event belongs to the main frame of a known tab. If so, it tells the settling tracker that this tab is loading.

**Call relations**: Browser event dispatch code calls this during page load. It uses `BrowserTabs.is_top_level_frame` to avoid treating subframe activity as a whole-page navigation signal.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.on_dom_content`  (lines 120–122)

```
def on_dom_content(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as loaded when the browser reports that the page’s DOM content is ready. The DOM is the browser’s structured model of the page.

**Data flow**: An event payload and session ID go in. If there is a session ID, the function tells the settling tracker that this session has reached the loaded stage. It returns nothing.

**Call relations**: This is called from browser event handling during navigation. The settling tracker later uses this signal when `BrowserTabs.navigate` waits for the page to become usable.


##### `BrowserTabs.on_lifecycle`  (lines 124–128)

```
def on_lifecycle(self, params: JsonDict, session_id: str | None) -> None
```

**Purpose**: Marks a tab as painted when the browser reports an important visual lifecycle event for the top-level frame. “Painted” means the browser has drawn page content on screen.

**Data flow**: A lifecycle event payload and session ID go in. The function ignores events without a session, events that are not paint-related, and events for subframes. For the main frame, it tells the settling tracker that the page has painted.

**Call relations**: Browser event dispatch code calls this for page lifecycle events. It uses `BrowserTabs.is_top_level_frame`, and its result helps `BrowserTabs.navigate` wait until the page is visually ready.

*Call graph*: calls 1 internal fn (is_top_level_frame); 1 external calls (get).


##### `BrowserTabs.is_top_level_frame`  (lines 130–133)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame ID refers to the main frame of a known tab. This prevents embedded frames from being confused with the full page.

**Data flow**: A session ID and frame ID go in. The function compares them with the known tabs, where each tab’s target ID is treated as its top-level frame ID. It returns `true` if a match is found, otherwise `false`.

**Call relations**: `BrowserTabs.on_frame_loading` and `BrowserTabs.on_lifecycle` call this before updating page-settling state, so only whole-page signals count.

*Call graph*: called by 2 (on_frame_loading, on_lifecycle).


##### `BrowserTabs.attach_tab`  (lines 135–155)

```
async def attach_tab(self, target_id: str) -> Tab
```

**Purpose**: Connects this system to an existing browser page target and prepares it for automation. Attaching is like plugging the remote control into that specific tab.

**Data flow**: A browser target ID goes in. The function asks CDP to attach to it, reads the new session ID, initializes page-related browser domains, enables document download interception, sets the viewport size, and returns a new `Tab` object.

**Call relations**: `BrowserTabs.sync`, `BrowserTabs.page`, and `BrowserTabs.create` call this whenever a browser page needs to become a tracked tab. It calls `BrowserTabs.init_session` as part of setup, then constructs the `Tab` record.

*Call graph*: calls 2 internal fn (init_session, __init__); called by 3 (create, page, sync); 1 external calls (as_str).


##### `BrowserTabs.init_session`  (lines 157–162)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Turns on the browser event streams and capabilities needed for one tab session. Without this, the code would not receive page, lifecycle, DOM, and network signals for that tab.

**Data flow**: A CDP session ID goes in. The function sends setup commands to enable page events, lifecycle events, DOM access, and network tracking for that session. It returns nothing, but the browser session becomes observable and controllable.

**Call relations**: `BrowserTabs.attach_tab` calls this immediately after attaching to a target, before the tab is returned to the rest of the system.

*Call graph*: called by 1 (attach_tab).


##### `BrowserTabs.sync`  (lines 164–177)

```
async def sync(self) -> None
```

**Purpose**: Brings the local tab list up to date with browser target events that arrived earlier. It removes tabs that were destroyed and attaches to newly created page targets.

**Data flow**: It reads `browser.tab_events`, especially the destroyed-target set and created-target queue. It filters closed tabs out of `browser.tabs`, clears the destroyed set, then processes each pending new target and appends an attached `Tab` when possible. It returns nothing but updates the stored tab list.

**Call relations**: `BrowserTabs.page` and `BrowserTabs.tabs_context` call this before they rely on the tab list. When new tabs are found, it hands each target ID to `BrowserTabs.attach_tab`.

*Call graph*: calls 1 internal fn (attach_tab); called by 2 (page, tabs_context).


##### `BrowserTabs.page`  (lines 179–192)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the tab that should be used for an operation. If no tab exists yet, it creates a blank one so callers always have a page to work with.

**Data flow**: An optional tab index goes in. The function first synchronizes the tab list. If there are no tabs, it creates and attaches to a blank target. If no index was requested, it returns the most recently tracked tab; otherwise it validates the index and returns that tab. Invalid indexes raise a validation error.

**Call relations**: `BrowserTabs.navigate` uses this to choose the tab to navigate, and `BrowserTabs.close` uses it to choose the tab to close. It calls `BrowserTabs.sync` and may call `BrowserTabs.attach_tab` if a new blank page is needed.

*Call graph*: calls 2 internal fn (attach_tab, sync); called by 2 (close, navigate); 2 external calls (__init__, as_str).


##### `BrowserTabs.navigate`  (lines 194–206)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Moves a tab to a new location, or steps backward or forward in its history, then waits until the page is settled enough to inspect. It is the main navigation entry point in this file.

**Data flow**: A URL or special command, plus an optional tab index, goes in. The function selects a tab, normalizes the destination, resets settling state, performs either a history step or normal navigation, waits for settling signals, then returns the tab’s current URL and title.

**Call relations**: `BrowserTabs.create` calls this after opening a new tab with a requested URL. Internally it uses `normalize_url`, `BrowserTabs.page`, `BrowserTabs._goto`, `BrowserTabs._history_step`, and `BrowserTabs.tab_info`.

*Call graph*: calls 5 internal fn (_goto, _history_step, page, tab_info, normalize_url); called by 1 (create).


##### `BrowserTabs._goto`  (lines 208–222)

```
async def _goto(self, tab: Tab, url: str) -> None
```

**Purpose**: Performs a normal browser navigation to a specific URL and waits for the basic page-load event. It also treats file downloads as a valid outcome when the browser reports navigation trouble.

**Data flow**: A `Tab` and URL go in. The function notes how many downloads existed before navigation, starts waiting for the DOM-content event, sends the browser navigation command, checks for errors, and waits for the load event when there is a real new loader. It returns nothing, but the tab may move to a new page or start a download.

**Call relations**: `BrowserTabs.navigate` calls this for ordinary destinations. It works closely with the CDP connection and the download reader so navigation results are interpreted correctly.

*Call graph*: called by 1 (navigate).


##### `BrowserTabs._history_step`  (lines 224–241)

```
async def _history_step(self, tab: Tab, step: int) -> None
```

**Purpose**: Moves a tab backward or forward in its browser history if such a history entry exists. If the requested step would go past the start or end of history, it quietly does nothing.

**Data flow**: A `Tab` and a step value such as `-1` or `1` go in. The function asks the browser for navigation history, calculates the target entry, waits for a navigation-related event, sends the command to jump to that history entry, and waits briefly for confirmation. It returns nothing, but the tab’s displayed page may change.

**Call relations**: `BrowserTabs.navigate` calls this when the normalized destination is `back` or `forward`. It uses browser history data and waits for browser navigation events before control returns.

*Call graph*: called by 1 (navigate); 2 external calls (as_list, as_map).


##### `BrowserTabs.tab_info`  (lines 243–250)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Reads the current URL and page title from a tab. This gives callers a small, human-readable summary of where the tab is and what page it shows.

**Data flow**: A `Tab` goes in. The function runs JavaScript in that tab to read `location.href` and `document.title`, validates the result as a map, and returns a dictionary with string `url` and `title` fields.

**Call relations**: `BrowserTabs.navigate` uses this for its final result, `BrowserTabs.tabs_context` uses it for each tab in the tab list, and `BrowserTabs.tab_titles` uses it to collect only titles.

*Call graph*: called by 3 (navigate, tab_titles, tabs_context); 1 external calls (as_map).


##### `BrowserTabs.tabs_context`  (lines 252–259)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Builds a snapshot of all known tabs for display or for another subsystem to understand the browser state. It marks the most recently tracked tab as the current active one.

**Data flow**: No direct input is needed. The function synchronizes the tab list, calculates the current tab index, asks each tab for its URL and title, and returns a dictionary containing the current tab ID and a list of tab summaries.

**Call relations**: `BrowserTabs.close` calls this after closing a tab so the caller receives the updated tab list. It depends on `BrowserTabs.sync` and `BrowserTabs.tab_info`.

*Call graph*: calls 2 internal fn (sync, tab_info); called by 1 (close).


##### `BrowserTabs.tab_titles`  (lines 261–266)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns just the titles of all tracked tabs. This is a lightweight way to summarize open pages when full tab details are not needed.

**Data flow**: It reads the current `browser.tabs` list. For each tab, it asks for tab information, extracts the title, substitutes an empty string when missing, and returns the list of titles.

**Call relations**: This helper uses `BrowserTabs.tab_info` for each tab. It can be used by higher-level code that only needs names of open pages.

*Call graph*: calls 1 internal fn (tab_info).


##### `BrowserTabs.create`  (lines 268–275)

```
async def create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Opens a new browser tab and optionally navigates it to a requested URL. It returns the new tab’s ID along with its final URL and title.

**Data flow**: An optional URL goes in, defaulting to `about:blank`. The function creates a blank browser target, attaches to it, adds the new `Tab` to the local list, navigates that tab to the requested URL, and returns a dictionary with the tab index and page info.

**Call relations**: This is the high-level “new tab” operation. It calls `BrowserTabs.attach_tab` to connect to the browser target and then `BrowserTabs.navigate` to load the requested page.

*Call graph*: calls 2 internal fn (attach_tab, navigate); 1 external calls (as_str).


##### `BrowserTabs.close`  (lines 277–282)

```
async def close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a selected browser tab and returns the updated tab list. It also clears out-of-process iframe session tracking because closing a tab can make those frame sessions stale.

**Data flow**: A dictionary of arguments goes in, possibly containing `tab_id`. The function converts that value to an index, selects the tab, sends the browser command to close its target, removes it from the local tab list, clears `oop_sessions`, and returns the new tab context.

**Call relations**: This is the high-level “close tab” operation. It uses `_tab_id` to interpret the input, `BrowserTabs.page` to find the tab, and `BrowserTabs.tabs_context` to report the new browser state.

*Call graph*: calls 3 internal fn (page, tabs_context, _tab_id); 1 external calls (get).


##### `_tab_id`  (lines 285–294)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: Converts a loosely typed tab ID value into either an integer index or `None`. This lets callers pass tab IDs as numbers or numeric strings.

**Data flow**: A JSON-like value goes in. If it is an integer, it is returned as-is; if it is a float or non-empty string, it is converted to an integer; otherwise the function returns `None`.

**Call relations**: `BrowserTabs.close` calls this before selecting which tab to close, so the close operation can accept common input formats.

*Call graph*: called by 1 (close).


### `extensions/browser/ufo_ext_browser/bua/content.py`

`domain_logic` · `request handling`

This file solves a practical problem: a browser page can contain a lot of structured information, but callers need simple answers like “show me the page tree,” “give me the page text,” or “find the button matching this query.” Without this layer, every caller would need to know how to pick a browser tab, read page structure, limit huge output, and format search results.

The file defines two protocols, which are like contracts. `BrowserContentSession` describes what a browser session must be able to provide: a page tab, a page reader, and basic tab information. `BrowserContentPageReader` describes how page content can be read: either as a structured tree or as markdown text.

`BrowserContent` is the main helper. It receives a browser session, chooses the requested tab if one is provided, asks the page reader for content, and returns plain JSON-friendly results. It also protects callers from overly large responses by cutting page trees and text at fixed character limits and reporting whether truncation happened.

The `find` method searches the page tree. It can do this with built-in matching, or, if given a completion function, ask an AI-style completer to interpret the query and page tree. A small helper, `_tab_id`, turns a tab identifier from incoming JSON into an integer or `None`.

#### Function details

##### `BrowserContentPageReader.tree`  (lines 23–23)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: This is a required method for any page reader that wants to provide a structured view of a browser page. The tree is a text representation of page elements, optionally narrowed to a type of element or a referenced element.

**Data flow**: It receives a page tab, a filter name such as all or interactive, and an optional reference id. An implementation reads the page through the browser and returns the matching tree text, or returns nothing if the requested referenced element cannot be found.

**Call relations**: This method is part of the reader contract used by `BrowserContent.tree`. The concrete browser reader supplies the real behavior, while this file relies on the contract so it can request page structure without knowing the browser internals.


##### `BrowserContentPageReader.markdown`  (lines 25–25)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: This is a required method for any page reader that can turn the current browser page into readable text. It is meant for callers that want the page contents rather than the element-by-element tree.

**Data flow**: It receives a page tab. An implementation reads that page and returns markdown, which is plain text with lightweight formatting such as headings and links.

**Call relations**: This method is used by `BrowserContent.get_page_text`. The content layer asks for markdown and then wraps it with truncation information and tab details.


##### `BrowserContentSession.page`  (lines 29–29)

```
async def page(self, tab_id: int | None=None) -> PageTab
```

**Purpose**: This is a required method for browser sessions that can provide the active page tab or a specific tab by id. It lets content commands say which browser tab they want to read.

**Data flow**: It receives an optional tab id. The session resolves that into a `PageTab`, either the requested tab or the default current tab.

**Call relations**: This contract is used by `BrowserContent.tree` and `BrowserContent.get_page_text` before they read any content. The concrete browser session decides how tab lookup really works.


##### `BrowserContentSession.page_reader`  (lines 31–31)

```
def page_reader(self) -> BrowserContentPageReader
```

**Purpose**: This is a required method for browser sessions that can supply an object able to read page content. It keeps the content logic separate from the browser-specific reading code.

**Data flow**: It takes no direct input beyond the session itself. It returns a page reader that knows how to produce a page tree or markdown text.

**Call relations**: This contract is used when `BrowserContent.tree` asks for a tree and when `BrowserContent.get_page_text` asks for markdown. The session hands off the actual reading work to its reader.


##### `BrowserContentSession.tab_info`  (lines 33–33)

```
async def tab_info(self, tab: Any) -> JsonDict
```

**Purpose**: This is a required method for browser sessions that can provide useful metadata about a tab. That metadata can include information such as the tab identity or page state, depending on the implementation.

**Data flow**: It receives a tab object. The session inspects it and returns a JSON-style dictionary of tab information.

**Call relations**: This contract is used by `BrowserContent.get_page_text` after the page text is read. The final response includes both the text and the tab details so callers know what page the text came from.


##### `BrowserContent.tree`  (lines 40–47)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: This method returns a structured text tree for a browser page. Callers use it when they need to inspect page elements, optionally around a particular referenced element.

**Data flow**: It reads `tab_id` and `ref_id` from the incoming arguments. It turns `tab_id` into a usable integer with `_tab_id`, asks the browser session for that tab, asks the page reader for a tree using the chosen filter and optional reference, and returns the tree text. If the referenced element is not found, it returns a clear message saying so.

**Call relations**: `BrowserContent.read_page` calls this when a user asks to read the page tree, and `BrowserContent.find` calls it before searching. This method is the shared path that turns loose request arguments into a concrete page-tree read.

*Call graph*: calls 1 internal fn (_tab_id); called by 2 (find, read_page); 1 external calls (get).


##### `BrowserContent.read_page`  (lines 49–53)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: This method returns the page tree in a JSON-friendly response. It also limits the size of the returned tree so a very large page does not overwhelm the caller.

**Data flow**: It reads a `filter` value from the incoming arguments and accepts only known filters: all, interactive, or viewport. It calls `BrowserContent.tree` with that filter, cuts the result to `MAX_READ_CHARS`, and returns the cut tree plus a `truncated` flag showing whether anything was left out.

**Call relations**: This is a public-facing wrapper around `BrowserContent.tree`. It is used when the caller wants a page tree response with safe output size and simple metadata.

*Call graph*: calls 1 internal fn (tree); 1 external calls (get).


##### `BrowserContent.get_page_text`  (lines 55–62)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: This method returns the browser page as readable markdown text. It is useful when the caller wants the page’s human-readable content instead of the technical element tree.

**Data flow**: It reads `tab_id` from the incoming arguments, converts it with `_tab_id`, and asks the browser session for that tab. It then asks the page reader for markdown text, cuts the text to `MAX_TEXT_CHARS`, records whether it was truncated, adds tab information from the browser session, and returns everything as a dictionary.

**Call relations**: This method follows the same tab-selection pattern as `BrowserContent.tree`, but it hands off to the page reader’s markdown path instead of the tree path. It also asks the browser session for tab information so the response has context.

*Call graph*: calls 1 internal fn (_tab_id); 1 external calls (get).


##### `BrowserContent.find`  (lines 64–77)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: This method searches the page tree for elements matching a user’s query. It can use a simple built-in parser or, if supplied, a completer function that can interpret the query more flexibly.

**Data flow**: It reads and validates the `query` argument as text, then calls `BrowserContent.tree` to get the full page tree. If no completer is supplied, it parses the tree directly for matches. If a completer is supplied, it sends the query and a shortened page tree to that completer, then resolves the reply back into concrete tree matches. It returns the matches and a short human-readable summary.

**Call relations**: This method builds on `BrowserContent.tree` because searching needs the page structure first. It then hands the matching work either to `parse_tree_matches` for local matching or to the completer plus `resolve_find_reply` for assisted matching, and finishes by formatting the result with `format_matches`.

*Call graph*: calls 1 internal fn (tree); 5 external calls (format_matches, parse_tree_matches, resolve_find_reply, get, as_str).


##### `_tab_id`  (lines 80–89)

```
def _tab_id(value: Json | None) -> int | None
```

**Purpose**: This helper turns a tab id from JSON input into an integer the browser session can use. It also treats missing or empty values as no specific tab requested.

**Data flow**: It receives a JSON value that might be an integer, float, string, empty value, or something else. Integers are returned as-is, floats and non-empty strings are converted to integers, and anything missing or unsupported becomes `None`.

**Call relations**: `BrowserContent.tree` and `BrowserContent.get_page_text` call this before asking the browser session for a page. It keeps tab-id cleanup in one place so those methods can work with consistent input.

*Call graph*: called by 2 (get_page_text, tree).


### `extensions/browser/ufo_ext_browser/bua/find.py`

`domain_logic` · `request handling`

A browser accessibility tree is like a simplified map of a web page: each line describes an element, its type, its label, a stable reference ID, and sometimes its screen coordinates. This file is the search-and-checking side of that system. It reads the tree format produced elsewhere and extracts useful facts from each line: the element reference, role, name, and position.

It supports two ways to find elements. The simpler path, `parse_tree_matches`, searches locally by breaking a user query into words and keeping tree entries whose line contains all those words. The more careful path, `resolve_find_reply`, is built for replies from a language model. The prompt tells the model to answer with element references, but the code does not blindly trust those references. It checks every returned reference against the actual tree and fills in the role, name, and coordinates from the tree itself. This prevents a guessed or misspelled reference from being used later.

Finally, `format_matches` turns the structured results back into a readable list. The file also caps results at 20, so callers do not get overwhelmed.

#### Function details

##### `tree_entries`  (lines 31–50)

```
def tree_entries(tree: str) -> list[JsonDict]
```

**Purpose**: This function reads the text form of an accessibility tree and pulls out the usable element records. It is the parser that turns lines of text into small dictionaries containing an element's reference, role, name, coordinates, and searchable line text.

**Data flow**: It takes the full tree as one string. It walks through the tree one line at a time, keeps only lines that look like element lines and contain a reference ID, then extracts the role, optional name, optional coordinates, and lowercased original line. It returns a list of structured entries; lines that do not match the expected tree format are ignored.

**Call relations**: Both search paths depend on this first step. `parse_tree_matches` calls it before doing a local word search, and `resolve_find_reply` calls it to build the trusted list of real references that an outside reply must be checked against.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `_match_payload`  (lines 53–60)

```
def _match_payload(entry: JsonDict, reason: str) -> JsonDict
```

**Purpose**: This small helper builds the standard result shape for one matched element. It keeps the important element facts together with the reason it matched, so later code can treat all results the same way.

**Data flow**: It takes one parsed tree entry and a reason string. It copies the element reference, role, name, and coordinates from the entry, adds the reason, and returns a new dictionary. It does not change the original entry.

**Call relations**: `parse_tree_matches` uses it when a local text search finds a matching entry. `resolve_find_reply` uses it after confirming that a replied reference really exists in the tree, adding the explanation text that came after the separator.

*Call graph*: called by 2 (parse_tree_matches, resolve_find_reply).


##### `parse_tree_matches`  (lines 63–71)

```
def parse_tree_matches(tree: str, query: str) -> list[JsonDict]
```

**Purpose**: This function performs a simple built-in search over the accessibility tree without asking an outside model. It is useful when a query can be matched by plain words appearing in the tree lines.

**Data flow**: It takes the tree text and a user query. It lowercases the query, extracts word-like search terms longer than one character, parses the tree into entries, and keeps entries whose searchable line contains every term. Each kept entry is converted into the standard match format with an empty reason, and the function stops once it reaches the maximum result count.

**Call relations**: This is one consumer of `tree_entries`: first it turns the tree into records, then it uses `_match_payload` to package each successful match. It is the direct local-search path, unlike `resolve_find_reply`, which validates a model's answer.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries); 1 external calls (findall).


##### `resolve_find_reply`  (lines 74–102)

```
def resolve_find_reply(reply: str, tree: str) -> tuple[list[JsonDict], bool]
```

**Purpose**: This function checks and cleans up a search reply that names accessibility-tree references. It protects the rest of the system from using references that were invented, repeated, or not present in the current tree.

**Data flow**: It takes a reply string and the tree text. First it parses the tree and builds a lookup table of real references. Then it reads the reply line by line, ignoring empty lines, stopping on `NO_MATCHES`, noticing a `MORE` marker, and looking for a reference ID near the start of each result line. If the reference exists and has not already been used, it creates a trusted match using the tree's own role, name, and coordinates plus the reply's reason text. It returns two things: the list of verified matches and a true-or-false flag saying whether more matches were reported.

**Call relations**: This function sits between an outside finder reply and the rest of the browser automation flow. It calls `tree_entries` so it can verify references against the actual tree, and it calls `_match_payload` only after that check passes. The important story is that the reply suggests candidates, but the tree remains the source of truth.

*Call graph*: calls 2 internal fn (_match_payload, tree_entries).


##### `format_matches`  (lines 105–114)

```
def format_matches(matches: list[JsonDict], has_more: bool=False) -> str
```

**Purpose**: This function turns structured match results into a short human-readable text list. It is used when the system needs to show found elements in a clear, compact form.

**Data flow**: It takes a list of match dictionaries and an optional flag saying whether more matches exist. For each match, it writes a line with the reference, role, name, and coordinates, and appends the reason if one is present. If the `has_more` flag is true, it adds a final note asking the user to refine the query. It returns the joined text.

**Call relations**: This is the final presentation step after either local searching or reply validation has produced match dictionaries. It does not call the parser or matcher; it simply formats the already-prepared results for display.


### `extensions/browser/ufo_ext_browser/bua/page.py`

`domain_logic` · `request handling`

A web page is messy: it has visible boxes, hidden helper elements, nested frames, and browser-only identifiers. This file builds a cleaner view of that page by combining two browser reports from Chrome DevTools Protocol, or CDP, which is Chrome’s remote-control API. One report describes the page’s layout and element positions. The other is the accessibility tree, a simplified tree browsers use for screen readers. Together, they let the system say things like: “button ‘Submit’ at x=300,y=500 [ref=e42]”.

The file first captures page snapshots, including iframes. Some iframes live in separate browser targets, so it can attach to those separately and stitch them back into one tree. It then renders that tree in two ways: `render_page` creates an action-oriented outline with roles, names, coordinates, state, and stable references; `render_markdown` creates a reading view with headings, links, lists, and paragraphs.

The references are important. They are like numbered claim tickets: the model sees `[ref=e42]`, and later the system can resolve that ticket back to the browser element. If this file were missing, the model would have to act from raw page text or guessed coordinates, which would make browser control much less reliable.

#### Function details

##### `split_ref`  (lines 116–120)

```
def split_ref(ref: str) -> tuple[str, int] | None
```

**Purpose**: Checks whether a browser element reference has the expected shape, such as `e12` or `f1e3`, and separates it into a frame prefix and an element id. This keeps later browser actions from trusting invented or malformed references.

**Data flow**: It receives a reference string. It matches it against the allowed pattern, then returns the frame part and numeric backend element id, or returns nothing if the string is not valid.

**Call relations**: When page rendering is asked to start from a specific reference, `render_page` uses this to understand the reference. When an action needs to target an element, `BrowserPage.resolve_ref` uses the same check before looking up the frame.

*Call graph*: called by 2 (resolve_ref, render_page).


##### `Cdp.send`  (lines 124–126)

```
async def send(self, method: str, params: JsonDict | None=None, session_id: str | None=None) -> JsonDict
```

**Purpose**: Defines the expected shape of a CDP command sender. CDP, Chrome DevTools Protocol, is the browser remote-control channel used to ask Chrome for snapshots, element boxes, and frame information.

**Data flow**: It receives a browser method name, optional parameters, and an optional session id for a particular browser target. It sends that command to the browser and returns the browser’s JSON-style response.

**Call relations**: This is a protocol method, so the real connection object supplies the implementation. `fetch_target` relies on it heavily to collect page data, and other browser-settling code can also use the same command path.

*Call graph*: called by 2 (fetch_target, _flush_page_tasks).


##### `PageTab.frame_seq`  (lines 161–161)

```
def frame_seq(self, frame_id: str) -> int
```

**Purpose**: Provides a stable short number for a frame id so frame references can be written compactly, such as `f2e17`. This makes references readable while still distinguishing elements in different frames.

**Data flow**: It receives a browser frame id. It returns a sequence number assigned to that frame by the tab object.

**Call relations**: When `_snapshot_oop` finds a separate iframe target, it asks the tab for this number so the child frame’s references get the right prefix.

*Call graph*: called by 1 (_snapshot_oop).


##### `BrowserPageSession.connection`  (lines 168–168)

```
def connection(self) -> Cdp
```

**Purpose**: Defines how a browser page session exposes its CDP connection. This lets the page logic ask the browser for snapshots and element geometry without knowing the concrete connection class.

**Data flow**: It reads the session object’s stored browser connection and returns an object that can send CDP commands.

**Call relations**: The `BrowserPage` methods call this whenever they need to talk to Chrome, especially while taking snapshots or resolving element positions.


##### `BrowserPageSession.init_session`  (lines 170–170)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Defines the setup step needed after attaching to a new browser target, such as an out-of-process iframe. It prepares that target so later CDP commands work consistently.

**Data flow**: It receives a CDP session id for a newly attached target. It performs whatever setup the browser session requires and returns when the target is ready.

**Call relations**: `BrowserPage._oop_session` calls this right after attaching to a child frame target, before saving that session for reuse.


##### `_float`  (lines 190–195)

```
def _float(value: Json | None, default: float=0.0) -> float
```

**Purpose**: Safely turns a JSON value into a floating-point number. It protects the snapshot code from missing or oddly typed browser values.

**Data flow**: It receives a value and a default. If the value is already a number, it returns it as a float; otherwise it returns the default.

**Call relations**: `_parse_document` uses it for scroll offsets and layout bounds. `fetch_target` uses it to read the device pixel ratio reported by the browser.

*Call graph*: called by 2 (_parse_document, fetch_target).


##### `_attr`  (lines 198–203)

```
def _attr(strings: list[str], attrs: list[Json], name: str) -> str | None
```

**Purpose**: Finds one named HTML attribute, such as `type` or `src`, inside Chrome’s compact snapshot format. Chrome stores strings in a shared string table, so this helper hides that bookkeeping.

**Data flow**: It receives the shared strings list, a compact attribute list, and the attribute name to search for. It walks the key/value pairs and returns the matching attribute value, or nothing if it is absent.

**Call relations**: `_parse_document` uses this while reading inputs, images, and iframes so later rendering can show input types, image names, and frame sources.

*Call graph*: called by 1 (_parse_document).


##### `_parse_document`  (lines 206–266)

```
def _parse_document(doc: JsonDict, strings: list[str], dpr: float) -> _RawDoc
```

**Purpose**: Reads one document from Chrome’s DOM snapshot and extracts the practical details this project needs: element ids, positions, pointer cursor hints, input types, image sources, and iframe links.

**Data flow**: It receives one raw document snapshot, the shared string table, and the device pixel ratio. It validates and decodes the compact browser data, normalizes positions into CSS pixels, records special element metadata, and returns a `_RawDoc` summary.

**Call relations**: `parse_snapshot` calls this once for each document in the browser snapshot. It relies on `_float` for numeric cleanup and `_attr` for HTML attributes.

*Call graph*: calls 2 internal fn (_attr, _float); called by 1 (parse_snapshot); 6 external calls (__init__, __init__, get, as_int, as_list, as_map).


##### `parse_snapshot`  (lines 269–318)

```
def parse_snapshot(snapshot: JsonDict, dpr: float, base_origin: tuple[float, float]=(0.0, 0.0)) -> list[DocData]
```

**Purpose**: Turns Chrome’s whole DOM snapshot into document summaries with correct page coordinates and iframe relationships. This is the step that makes nested documents line up in one shared coordinate space.

**Data flow**: It receives the raw snapshot, device pixel ratio, and the starting origin. It parses each document, follows parent-to-child document links through iframes, adjusts element bounds by frame position and scroll, then returns `DocData` objects.

**Call relations**: `fetch_target` calls this after capturing the DOM snapshot. Its output is later joined with accessibility-tree data so visible geometry and semantic roles can be rendered together.

*Call graph*: calls 1 internal fn (_parse_document); called by 1 (fetch_target); 5 external calls (__init__, get, as_list, as_map, as_str).


##### `fetch_target`  (lines 321–383)

```
async def fetch_target(cdp: Cdp, session_id: str, *, root_prefix: str, prefix_for: Callable[[str], str], base_origin: tuple[float, float]=(0.0, 0.0)) -> FrameSnapshot
```

**Purpose**: Captures everything needed from one browser target: layout snapshots, accessibility trees, frame metadata, and iframe nesting. A browser target is one remotely controlled page or frame session.

**Data flow**: It receives a CDP connection, a session id, reference-prefix rules, and a base position. It enables browser snapshot features, captures DOM geometry, reads device pixel ratio, fetches accessibility trees for each document, and returns a `FrameSnapshot` tree.

**Call relations**: `BrowserPage._snapshot_target` calls this as the main capture step. It calls CDP commands through `Cdp.send`, uses `parse_snapshot` to decode geometry, and then builds `FrameSnapshot` objects that rendering functions can consume.

*Call graph*: calls 3 internal fn (send, _float, parse_snapshot); called by 1 (_snapshot_target); 5 external calls (__init__, gather, as_list, as_map, as_str).


##### `_ax_value`  (lines 386–390)

```
def _ax_value(value: Json | None) -> str
```

**Purpose**: Extracts the plain text value from an accessibility-tree value object. It turns Chrome’s nested value shape into a simple string.

**Data flow**: It receives a JSON value. If the value is a dictionary with a `value` field, it returns that field as text; otherwise it returns an empty string.

**Call relations**: `render_page.render_node` and `render_markdown.walk` use this to read node roles and names before deciding what to display.

*Call graph*: called by 2 (walk, render_node); 1 external calls (get).


##### `_ax_property`  (lines 393–401)

```
def _ax_property(node: JsonDict, name: str) -> Json
```

**Purpose**: Looks up one named property on an accessibility node, such as `checked`, `expanded`, `url`, or `hidden`. This lets rendering code ask simple questions about a node’s state.

**Data flow**: It receives an accessibility node and a property name. It scans the node’s property list and returns the property’s actual value, or nothing if it is not present.

**Call relations**: `_format_extras` uses it to add useful state text. Both page rendering and markdown rendering use it to skip hidden nodes and understand headings, links, and controls.

*Call graph*: called by 3 (_format_extras, walk, render_node); 3 external calls (get, as_list, as_map).


##### `_should_skip`  (lines 404–410)

```
def _should_skip(node: JsonDict, role: str, name: str) -> bool
```

**Purpose**: Decides whether a structural accessibility node should be skipped in the action-oriented page outline. Some nodes are just containers, like invisible shelves, and showing them would clutter the model’s view.

**Data flow**: It receives an accessibility node, its role, and its name. If the node is an unnamed container role with no important state, it returns true; otherwise it returns false.

**Call relations**: `render_page.render_node` calls this while walking the tree. If it says to skip, rendering continues through the node’s children without printing the container itself.

*Call graph*: called by 1 (render_node); 3 external calls (get, as_list, as_map).


##### `_truncate`  (lines 413–416)

```
def _truncate(text: str, max_len: int) -> str
```

**Purpose**: Shortens long text so rendered page output stays compact. This prevents huge labels, values, or filenames from crowding out the useful page structure.

**Data flow**: It receives text and a maximum length. If the text is short enough it returns it unchanged; otherwise it cuts it and adds an ellipsis.

**Call relations**: `_image_name`, `_format_extras`, and `render_page.render_node` use this whenever user-visible text is added to the rendered page outline.

*Call graph*: called by 3 (_format_extras, _image_name, render_node).


##### `_image_name`  (lines 419–426)

```
def _image_name(src: str | None) -> str
```

**Purpose**: Creates a fallback name for an image from its file URL when the accessibility tree has no useful image name. For example, an image source ending in `logo.png` can become `logo.png`.

**Data flow**: It receives an image source URL. It extracts the last path segment, keeps it only if it looks like a filename with an extension, truncates it if needed, and returns that name or an empty string.

**Call relations**: `render_page.render_node` uses this for unnamed image entries. `render_markdown.walk` uses it for image alt text when the page does not provide one.

*Call graph*: calls 1 internal fn (_truncate); called by 2 (walk, render_node); 1 external calls (urlparse).


##### `_format_extras`  (lines 429–459)

```
def _format_extras(node: JsonDict, geom: NodeGeom | None) -> str
```

**Purpose**: Builds the small state suffix shown after a rendered page node, such as input type, value, checked state, disabled state, or URL. These details help the model choose the right element.

**Data flow**: It receives an accessibility node and optional geometry metadata. It reads known accessibility properties, filters unsafe or unhelpful URLs, truncates long values, and returns one formatted string to append to the line.

**Call relations**: `render_page.render_node` calls this after choosing to display a node. It depends on `_ax_property` for state lookup and `_truncate` for keeping output short.

*Call graph*: calls 2 internal fn (_ax_property, _truncate); called by 1 (render_node).


##### `_frame_by_prefix`  (lines 462–469)

```
def _frame_by_prefix(root: FrameSnapshot, prefix: str) -> FrameSnapshot | None
```

**Purpose**: Finds the frame snapshot that belongs to a reference prefix. This is needed because references from iframes include a frame marker before the element id.

**Data flow**: It receives the root frame snapshot and a prefix. It searches the root and child frames recursively and returns the matching frame snapshot, or nothing if none matches.

**Call relations**: `render_page` uses this when asked to render only a subtree starting from a specific reference.

*Call graph*: called by 1 (render_page).


##### `_node_by_backend`  (lines 472–476)

```
def _node_by_backend(frame: FrameSnapshot, backend_id: int) -> str | None
```

**Purpose**: Finds the accessibility-tree node that corresponds to a browser backend element id. This connects the stable browser element id to the readable accessibility node.

**Data flow**: It receives a frame snapshot and a backend node id. It scans that frame’s accessibility nodes and returns the matching accessibility node id, or nothing if no node has that backend id.

**Call relations**: `render_page` uses this after `_frame_by_prefix` when rendering from a supplied reference instead of from the page root.

*Call graph*: called by 1 (render_page).


##### `render_page`  (lines 479–575)

```
def render_page(root: FrameSnapshot, *, viewport: Size, model_size: Size | None=None, filter_type: str='all', max_depth: int=DEFAULT_MAX_DEPTH, ref: str | None=None) -> str | None
```

**Purpose**: Renders a captured page as a compact, action-friendly outline for the model. Each useful line can include a role, name, reference, center coordinate, and state details.

**Data flow**: It receives a `FrameSnapshot`, viewport size, optional model coordinate size, filtering choices, depth limit, and optional starting reference. It walks the accessibility tree, splices child frames under iframe nodes, filters clutter, scales coordinates, and returns the rendered text or nothing if a requested reference cannot be found.

**Call relations**: `BrowserPage.tree` calls this after taking a snapshot. Inside, it uses helpers such as `split_ref`, `_frame_by_prefix`, and `_node_by_backend` when focused on one reference, and nested helpers to walk and print the tree.

*Call graph*: calls 3 internal fn (_frame_by_prefix, _node_by_backend, split_ref); called by 1 (tree); 1 external calls (effective_model_size).


##### `render_page.coord_str`  (lines 494–498)

```
def coord_str(geom: NodeGeom | None) -> str
```

**Purpose**: Formats an element’s center point for the rendered page outline. Coordinates are shown in the model’s coordinate system, not necessarily the browser’s raw viewport pixels.

**Data flow**: It receives optional geometry for one node. If bounds exist, it computes the center, applies the scaling chosen by `render_page`, and returns text like ` (x=10,y=20)`; otherwise it returns an empty string.

**Call relations**: This helper lives inside `render_page` and is used whenever a node line is printed.


##### `render_page.splice`  (lines 500–503)

```
def splice(frame: FrameSnapshot, backend_id: int | None, depth: int) -> None
```

**Purpose**: Inserts a child frame’s accessibility tree at the iframe element where it belongs. This makes iframe contents appear as part of one page outline.

**Data flow**: It receives the current frame, an optional backend id for the iframe node, and the current depth. If a child frame is registered at that backend id, it starts rendering the child frame root at the same place in the outline.

**Call relations**: This helper is used inside `render_page.render_node` after normal children are visited, so iframe documents are stitched into the visible tree.


##### `render_page.render_node`  (lines 505–559)

```
def render_node(frame: FrameSnapshot, ax_id: str, depth: int, parent_name: str) -> None
```

**Purpose**: Walks one accessibility node and decides whether to print it, skip it, or continue into its children. This is the heart of the action-oriented page rendering.

**Data flow**: It receives a frame, accessibility node id, indentation depth, and parent name. It reads the node’s role, name, state, backend id, children, and geometry; filters hidden or unhelpful nodes; builds a rendered line when appropriate; then visits children and spliced frames.

**Call relations**: This nested function is driven by `render_page`, starting either at the page root or at a referenced node. It calls helpers for accessibility values, state, image fallback names, truncation, and extra property formatting.

*Call graph*: calls 6 internal fn (_ax_property, _ax_value, _format_extras, _image_name, _should_skip, _truncate); 2 external calls (as_list, as_str).


##### `render_page.render_node.descend`  (lines 521–524)

```
def descend(child_depth: int, child_parent_name: str) -> None
```

**Purpose**: Continues rendering through a node’s children and any iframe attached to that node. It keeps the tree walk readable by centralizing the child-walking step.

**Data flow**: It receives the depth and parent name to use for children. It renders each child id, then asks `splice` to include a child frame if the current node is an iframe.

**Call relations**: This helper is used inside `render_page.render_node` both when a node is printed and when a transparent container is skipped.


##### `render_markdown`  (lines 583–651)

```
def render_markdown(root: FrameSnapshot) -> str
```

**Purpose**: Renders the captured page as a reading-oriented Markdown document. It focuses on content structure, such as headings, links, lists, paragraphs, and images, rather than clickable references.

**Data flow**: It receives a root frame snapshot. It walks the accessibility tree, collects inline text into paragraphs, emits Markdown blocks for headings and list items, includes links with URLs, splices iframe content, and returns one Markdown string.

**Call relations**: `BrowserPage.markdown` calls this after taking a snapshot. It uses nested helpers to collect text and a recursive walker to move through the accessibility tree.

*Call graph*: called by 1 (markdown).


##### `render_markdown.emit`  (lines 592–595)

```
def emit(text: str) -> None
```

**Purpose**: Adds one completed Markdown block if it has useful text and is not just a duplicate of the previous block. This keeps the reading view clean.

**Data flow**: It receives text, trims surrounding whitespace, checks it against the last emitted block, and appends it to the block list when appropriate.

**Call relations**: This helper lives inside `render_markdown` and is called by `flush` and by the tree walker whenever a complete content block is found.


##### `render_markdown.flush`  (lines 597–600)

```
def flush() -> None
```

**Purpose**: Turns accumulated inline words into a finished paragraph. It is like ending the current sentence group before starting a heading, list item, or new block.

**Data flow**: It checks the current inline text buffer. If there is text, it joins the pieces with spaces, emits the result, and clears the buffer.

**Call relations**: This helper is used inside `render_markdown.walk` whenever the traversal crosses a block boundary or before special Markdown blocks are emitted.


##### `render_markdown.walk`  (lines 602–646)

```
def walk(frame: FrameSnapshot, ax_id: str, parent_name: str) -> None
```

**Purpose**: Walks the accessibility tree and translates each useful node into reading-friendly Markdown content. It avoids hidden nodes and duplicate static text.

**Data flow**: It receives a frame, accessibility node id, and parent name. It reads role, name, backend id, properties, and children; appends inline text or emits Markdown blocks; then visits children and child frames.

**Call relations**: This nested function is started by `render_markdown` at the root node. It uses `_ax_value`, `_ax_property`, and `_image_name` to interpret accessibility nodes.

*Call graph*: calls 3 internal fn (_ax_property, _ax_value, _image_name); 2 external calls (as_list, as_str).


##### `BrowserPage.tree`  (lines 660–668)

```
async def tree(self, tab: PageTab, filter_type: str, ref: str | None) -> str | None
```

**Purpose**: Provides the public page-outline operation for a browser tab. It captures the current page and renders it in the action-oriented format used by the model.

**Data flow**: It receives a tab, a filter type, and an optional reference. It takes a fresh snapshot, passes it to `render_page` with viewport and model sizing, and returns the rendered outline or nothing if the reference cannot be rendered.

**Call relations**: Higher-level browser tools call this when they need the model to inspect or find elements on a page. It connects snapshot collection with the text renderer.

*Call graph*: calls 2 internal fn (snapshot, render_page).


##### `BrowserPage.markdown`  (lines 670–671)

```
async def markdown(self, tab: PageTab) -> str
```

**Purpose**: Provides the public reading-view operation for a browser tab. It captures the current page and turns it into Markdown for content-focused reading.

**Data flow**: It receives a tab. It takes a fresh snapshot, passes it to `render_markdown`, and returns the Markdown text.

**Call relations**: Higher-level browser tools call this when they need page content rather than clickable element references.

*Call graph*: calls 2 internal fn (snapshot, render_markdown).


##### `BrowserPage.resolve_ref`  (lines 673–686)

```
def resolve_ref(self, tab: PageTab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Turns a model-visible reference like `f1e42` into the frame information and backend element id needed for browser commands. It rejects invalid or stale references with a clear model-facing error.

**Data flow**: It receives a tab and reference string. It parses the reference, looks up the frame prefix in the tab’s registered frame map, and returns the frame node plus backend id; if anything is wrong, it raises `HallucinationError`.

**Call relations**: `BrowserPage.ref_point` calls this before trying to locate an element. It uses `split_ref` to enforce the same reference grammar used by rendering.

*Call graph*: calls 1 internal fn (split_ref); called by 1 (ref_point); 1 external calls (__init__).


##### `BrowserPage.ref_point`  (lines 688–714)

```
async def ref_point(self, tab: PageTab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds the clickable center point for a previously rendered page reference. This is how a text reference becomes a concrete screen coordinate.

**Data flow**: It receives a tab and reference. It resolves the reference, asks the browser to scroll the element into view, gets its content quadrilateral or box model, averages the four corners, adds the frame origin, and returns integer coordinates.

**Call relations**: Action code can call this before clicking or pointing. It depends on `BrowserPage.resolve_ref` for safety and `_coord_float_or_default` for reading browser coordinate values.

*Call graph*: calls 2 internal fn (resolve_ref, _coord_float_or_default); 3 external calls (__init__, as_list, as_map).


##### `BrowserPage.snapshot`  (lines 716–720)

```
async def snapshot(self, tab: PageTab) -> FrameSnapshot
```

**Purpose**: Captures a complete page snapshot for one tab and refreshes the tab’s reference-to-frame map. This is the common starting point for both page outline and Markdown rendering.

**Data flow**: It receives a tab. It snapshots the main target, clears old frame reference data, registers every frame from the new snapshot, and returns the root frame snapshot.

**Call relations**: `BrowserPage.tree` and `BrowserPage.markdown` call this before rendering. It delegates capture to `_snapshot_target` and reference registration to `_register_frames`.

*Call graph*: calls 2 internal fn (_register_frames, _snapshot_target); called by 2 (markdown, tree).


##### `BrowserPage._snapshot_target`  (lines 722–739)

```
async def _snapshot_target(self, tab: PageTab, session_id: str, root_prefix: str, origin: tuple[float, float], depth: int) -> FrameSnapshot
```

**Purpose**: Captures one browser target and, if allowed by the frame-depth limit, adds out-of-process iframe snapshots below it. It is the recursive building block for full-page snapshots.

**Data flow**: It receives a tab, session id, reference prefix, origin, and current depth. It calls `fetch_target` to capture that target, then optionally attaches child iframe targets, and returns the root `FrameSnapshot` for that target.

**Call relations**: `BrowserPage.snapshot` calls this for the main page. `_snapshot_oop` calls it again for child frame targets.

*Call graph*: calls 2 internal fn (_attach_oop_frames, fetch_target); called by 2 (_snapshot_oop, snapshot).


##### `BrowserPage._attach_oop_frames`  (lines 741–749)

```
async def _attach_oop_frames(self, tab: PageTab, root: FrameSnapshot, depth: int) -> None
```

**Purpose**: Searches a captured frame tree for iframes that live in separate browser targets and attaches their snapshots. Out-of-process iframes are common for cross-site frames, and they need separate CDP sessions.

**Data flow**: It receives a tab, root frame snapshot, and depth. It walks the current frame tree, tries to snapshot each listed out-of-process iframe, and adds successful child snapshots under the matching iframe backend id.

**Call relations**: `BrowserPage._snapshot_target` calls this after capturing a target. It hands each separate iframe to `_snapshot_oop`.

*Call graph*: calls 1 internal fn (_snapshot_oop); called by 1 (_snapshot_target).


##### `BrowserPage._snapshot_oop`  (lines 751–777)

```
async def _snapshot_oop(self, tab: PageTab, frame: FrameSnapshot, backend_id: int, depth: int) -> FrameSnapshot | None
```

**Purpose**: Captures one out-of-process iframe, if the browser allows it. It finds the iframe’s frame id, opens or reuses a CDP session for it, computes its on-page origin, and snapshots it.

**Data flow**: It receives the tab, parent frame snapshot, iframe backend id, and depth. It asks Chrome to describe the iframe node, gets the frame id, gets a session through `_oop_session`, derives the child origin from iframe bounds, and returns a child `FrameSnapshot` or nothing on failure.

**Call relations**: `BrowserPage._attach_oop_frames` calls this for each separate iframe. It uses `PageTab.frame_seq` to give the child frame a reference prefix and `_snapshot_target` to capture the child.

*Call graph*: calls 3 internal fn (_oop_session, _snapshot_target, frame_seq); called by 1 (_attach_oop_frames); 1 external calls (as_map).


##### `BrowserPage._register_frames`  (lines 779–782)

```
def _register_frames(self, tab: PageTab, frame: FrameSnapshot) -> None
```

**Purpose**: Records which frame each reference prefix belongs to. This lookup table is what later lets `f1e42` be resolved back to the right browser session.

**Data flow**: It receives a tab and a frame snapshot. It stores a `FrameNode` for the frame’s prefix, then recursively stores all child frames.

**Call relations**: `BrowserPage.snapshot` calls this after every fresh snapshot so later calls to `resolve_ref` use current frame information.

*Call graph*: called by 1 (snapshot); 1 external calls (__init__).


##### `BrowserPage._oop_session`  (lines 784–797)

```
async def _oop_session(self, frame_id: str) -> str | None
```

**Purpose**: Gets a CDP session for an out-of-process iframe, reusing an existing one when possible. This avoids reattaching to the same frame every time the page is read.

**Data flow**: It receives a frame id. It checks the session cache; if absent, it asks Chrome to attach to that target, initializes the new session, saves it, and returns the session id, or returns nothing if attachment fails.

**Call relations**: `BrowserPage._snapshot_oop` calls this before trying to snapshot a separate iframe target.

*Call graph*: called by 1 (_snapshot_oop); 1 external calls (as_str).


##### `_coord_float_or_default`  (lines 800–809)

```
def _coord_float_or_default(value: Json | None, default: float) -> float
```

**Purpose**: Converts a browser coordinate value into a float while allowing missing values to fall back to a default. It accepts numbers and numeric strings because browser responses can vary.

**Data flow**: It receives a coordinate-like value and a default. It returns a float for numeric inputs, returns the default for `None`, or raises a validation error for anything else.

**Call relations**: `BrowserPage.ref_point` uses this when averaging the corner coordinates returned by Chrome for a referenced element.

*Call graph*: called by 1 (ref_point); 1 external calls (__init__).

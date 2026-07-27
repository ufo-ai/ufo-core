# Browser session orchestration and agent tools  `stage-12.1.3`

This stage is the browser “workbench” the agent uses during a turn. It is shared behind-the-scenes support for browser tasks, not the whole main loop. When the agent decides to open a page, click, type, read text, or download a file, these files provide one safe path from the agent’s request to the live Chrome browser.

The actions file defines the common vocabulary for browser moves, with clear shapes for commands like click, type, scroll, screenshot, and wait. The tools file exposes those moves as callable tools for the agent and makes sure they reuse the same browser connection during the turn. The backend is the tool-facing surface: it performs navigation, tab work, uploads, downloads, and page reading, opening Chrome only when needed. The session file is the central hub underneath, holding the live Chrome connection, watching browser events, and offering higher-level browser actions. Before any action runs, fixup cleans small model mistakes, such as typing before focusing a field. Errors defines a special signal for impossible model output, such as naming an element that is not there.

## Files in this stage

### Instruction fixups
Normalizes and repairs model-proposed browser instructions before they are executed.

### `extensions/browser/ufo_ext_browser/bua/fixup.py`

`domain_logic` · `before browser action dispatch`

This file acts like a careful editor for a list of browser actions. A model may produce instructions such as “type this text,” “scroll,” or “double click,” but those instructions are not always complete enough for a real browser to carry out safely. Without this cleanup step, typing might go nowhere because no input field was focused, scrolling might fail because there is no starting point, or a wait action might not pause at all because it has no duration.

The main function, `fixup_actions`, walks through the action list and repairs predictable problems. If text is about to be typed into a known place, it adds a click first so the browser is focused there. If text contains written escape sequences like `\n`, it turns them into real newlines. If a scroll has no coordinate, it uses the center of the model’s screen. If `scroll_to` is missing the target reference it needs, it falls back to an ordinary scroll. If a wait has no time, it uses a default of three seconds.

The second public helper, `split_at_waits`, breaks a long action list into smaller batches whenever a wait appears. This lets the browser settle before the next batch continues, like pausing between steps in a recipe.

#### Function details

##### `fixup_actions`  (lines 10–44)

```
def fixup_actions(actions: list[ComputerAction], viewport: Size, model_size: Size | None=None) -> list[ComputerAction]
```

**Purpose**: This function takes a batch of planned browser actions and makes them safer and more complete before execution. It is used when actions may come from a model that understands intent but may leave out details a browser needs.

**Data flow**: It receives a list of `ComputerAction` objects, the current browser viewport size, and optionally the size the model thinks it is working with. It first chooses the effective model screen size and finds its center point. Then it checks each action: it may insert a click before typing, replace literal text escapes with real tab or newline characters, add a missing scroll coordinate, convert an incomplete `scroll_to` into a regular scroll, give a wait a default duration, or simplify a multi-click on a reference into a single left click. It returns a new list of actions, leaving the caller with a cleaner batch to send onward.

**Call relations**: This is the main repair step in the file. During its pass through the actions, it calls `_focus_click` when typing needs a prior click, `_unescape_text` when typed text may contain written escape sequences, and `effective_model_size` to decide the screen size used for fallback coordinates. It also creates new `ComputerAction` and `ScrollParameters` objects when an action needs to be replaced or filled in.

*Call graph*: calls 2 internal fn (_focus_click, _unescape_text); 3 external calls (__init__, __init__, effective_model_size).


##### `split_at_waits`  (lines 47–58)

```
def split_at_waits(actions: list[ComputerAction]) -> list[list[ComputerAction]]
```

**Purpose**: This function divides an action list into smaller groups, ending a group whenever a wait action appears. Someone would use it so the browser can pause and settle before the next set of actions starts.

**Data flow**: It receives one list of `ComputerAction` objects. It walks through them in order, collecting actions into a current batch. When it sees an action whose type is `wait`, it closes the current batch and starts a new one. At the end, it returns a list of batches, each batch being a list of actions.

**Call relations**: This helper is independent of the repair logic in `fixup_actions`. In the larger flow, it would be used after or around action preparation to make sure waits become real breakpoints between dispatches, rather than just another item in one uninterrupted stream.


##### `_focus_click`  (lines 61–64)

```
def _focus_click(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper creates a click action that focuses the place where typing is about to happen. It is needed because browsers usually send typed text to whatever field or page element is currently focused.

**Data flow**: It receives a typing action that points either to a coordinate or to a referenced page element. If there is a coordinate, it creates a left-click action at that coordinate. Otherwise, it creates a left-click action using the same reference. The result is a new `ComputerAction` that can be inserted before the original typing action.

**Call relations**: `fixup_actions` calls this helper when it sees a type action that has a target but was not immediately preceded by a click-like action. The helper hands back the focus click, and `fixup_actions` places it into the outgoing action list before the typing action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (__init__).


##### `_unescape_text`  (lines 67–73)

```
def _unescape_text(action: ComputerAction) -> ComputerAction
```

**Purpose**: This helper turns written escape sequences in typed text into the real characters they mean. For example, it changes the two visible characters `\n` into an actual newline.

**Data flow**: It receives a `ComputerAction`, reads its text, and checks for known escaped literals such as `\t` and `\n`. If none are present, it returns the original action. If they are present, it replaces them with real tab or newline characters and returns a copied action with the corrected text.

**Call relations**: `fixup_actions` calls this helper for type actions before adding them to the cleaned output list. The helper uses the action’s copy method so the corrected text is carried forward without manually rebuilding every other part of the action.

*Call graph*: called by 1 (fixup_actions); 1 external calls (model_copy).


### Agent tool surface
Exposes browser capabilities as agent-callable tools and coordinates shared per-turn browser access.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `request handling / per agent turn`

This file is the agent’s browser control panel. It defines the public tools the agent can use and the small handler functions behind each tool. The important idea is that a single agent turn should not open a fresh browser connection for every action. Instead, the first browser tool call creates one BuaSurface, which is the object that actually talks to the browser through CDP, the Chrome DevTools Protocol, a remote-control interface for Chromium-based browsers. Later browser tool calls in the same turn reuse that surface, like several errands using the same hired car instead of ordering a new one each time.

The file also defines input shapes using Pydantic models, which check that tool arguments have the expected fields and types before they reach the browser layer. Most handlers simply remove fields meant only for the human-facing tool description, pass the cleaned request to BuaSurface, and return the browser’s reply as JSON text.

Two tools do extra file work. The computer tool can return a screenshot as image content, and optionally save that screenshot into the shared workspace. The download tool waits for a browser download, decodes the downloaded bytes, and writes the file into the workspace so other parts of the agent can use it by path. Without this file, the agent would have no stable, validated bridge between tool calls and the browser backend.

#### Function details

##### `_browser`  (lines 95–118)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the browser surface for the current tool turn. If one does not exist yet, it creates it, connects it to the configured browser provider, and registers cleanup so the browser connection is closed at the end of the turn.

**Data flow**: It receives the tool context, which contains the cleanup registry, browser provider, model, sandbox, extension store, and conversation id. It looks up an existing BuaSurface using the turn cleanup object as the key; if none exists, it builds a new BuaSurface from the context, stores it for reuse, and registers its close method. It returns the ready-to-use browser surface, or raises an error if no browser provider is configured.

**Call relations**: All browser action handlers call this first when they need to talk to the browser. It is the shared doorway into BuaSurface.__init__, and it prevents handlers like _navigate, _find, _computer, and _wait_for_download from each creating their own separate browser session.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 121–122)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain Python dictionary as a tool result containing JSON text. This gives browser replies back to the agent in a consistent format.

**Data flow**: It receives a reply dictionary from a browser action. It converts that dictionary to a JSON string and places it inside a TextContent object, then wraps that in a ToolResult. The output is the standard return object expected by the tool system.

**Call relations**: Most handlers call this after BuaSurface returns a reply. It is the final packaging step for navigation, tab actions, page reading, finding elements, form input, downloads, and computer actions when no image attachment is needed.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 125–128)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a value from the browser reply is a non-empty string. It is used when the next step needs real text data, such as base64-encoded file bytes.

**Data flow**: It receives an unknown JSON value and the name of the field being checked. If the value is a non-empty string, it returns that string unchanged. If the value is missing, empty, or not a string, it raises a ValueError explaining which browser reply field was missing.

**Call relations**: _computer uses it before decoding a screenshot to save into the workspace. _wait_for_download uses it before decoding a downloaded file name and file content, so bad browser replies fail clearly instead of producing corrupt files.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 131–135)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool. It can send the browser to a URL or perform navigation-related actions supported by the backend.

**Data flow**: It receives the tool context and validated NavigateInput. It turns the input model into a JSON-ready dictionary, leaving out empty fields and the user_description field, then sends that to the shared browser surface’s navigate method. It returns the browser’s reply as JSON text.

**Call relations**: This is the handler registered for the navigate tool. When the agent asks to navigate, the tool system calls this function; it gets the shared browser through _browser and uses _json_result to package the result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 138–139)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the current browser tabs. This lets the agent understand what tabs are open before choosing what to do next.

**Data flow**: It receives the tool context and an empty TabsContextInput. It calls the shared browser surface with an empty request because this operation does not need extra arguments. It returns the tab context reply as JSON text.

**Call relations**: This is the handler registered for the tabs_context tool. It asks _browser for the current turn’s browser surface and then hands the result to _json_result for standard tool output.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 142–144)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab. If no URL is supplied, it opens a blank page.

**Data flow**: It receives the tool context and TabsCreateInput. It builds a small request containing either the requested URL or about:blank as a safe default, sends that to the browser surface’s tab creation method, and returns the reply as JSON text.

**Call relations**: This is the handler registered for the tabs_create tool. Like the other browser handlers, it reuses the shared surface from _browser and formats the response with _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 147–149)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, optionally a specific one. This gives the agent a way to clean up tabs it no longer needs.

**Data flow**: It receives the tool context and TabsCloseInput. It converts the input to a JSON-ready dictionary while dropping unset values, sends that request to the browser surface’s close-tab method, and returns the browser reply as JSON text.

**Call relations**: This is the handler registered for the tabs_close tool. It sits between the tool system and the browser backend, using _browser for access and _json_result for the final response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 152–154)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file upload field in the browser using files from the workspace. This is how the agent can attach local workspace files to web forms.

**Data flow**: It receives the tool context and UploadFileInput, including a browser element reference and one or more workspace file paths. It converts the input into a JSON-ready request, sends it to the browser surface’s upload_file method, and returns the reply as JSON text.

**Call relations**: This is the handler registered for the upload_file tool. It relies on _browser to reach the browser backend, where the actual interaction with the file input happens, and then uses _json_result to return the outcome.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 157–161)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the browser page in a structured way, based on the page’s accessibility tree. An accessibility tree is the browser’s description of page elements for assistive technology, and it is useful for identifying buttons, links, inputs, and visible content.

**Data flow**: It receives the tool context and ReadPageInput, including optional depth, filter, element reference, and tab id. It removes unset values and the user_description field, sends the cleaned request to the browser surface’s read_page method, and returns the structured page reply as JSON text.

**Call relations**: This is the handler registered for the read_page tool. The agent calls it when it needs a map of the page; _browser supplies the active browser surface, and _json_result packages the page data.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 164–168)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This is useful when the agent needs the page’s words without the richer structure of the accessibility tree.

**Data flow**: It receives the tool context and GetPageTextInput. It converts the input into a JSON-ready request, omits unset fields and user_description, sends it to the browser surface’s get_page_text method, and returns the text extraction reply as JSON text.

**Call relations**: This is the handler registered for the get_page_text tool. It follows the common path: obtain the shared browser with _browser, delegate the real browser work, then wrap the reply with _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 171–175)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Finds browser page elements that match a query, such as by role, visible text, accessible name, or URL. This helps the agent locate the exact thing it should click, type into, or inspect.

**Data flow**: It receives the tool context and FindInput, including the search query and optional tab id. It removes unset fields and user_description, sends the cleaned query to the browser surface’s find method, and returns matching element information as JSON text.

**Call relations**: This is the handler registered for the find tool. It uses _browser to share the turn’s browser surface and _json_result to return the search results in the normal tool format.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 178–182)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. This is how the agent fills text boxes, selectors, or other supported form controls after locating them.

**Data flow**: It receives the tool context and FormInputInput, including the target reference and the value to enter. It converts that input into a JSON-ready request without unset fields or user_description, sends it to the browser surface’s form_input method, and returns the reply as JSON text.

**Call relations**: This is the handler registered for the form_input tool. It usually follows a find or read_page step that gave the agent a browser reference, then delegates the actual form update through _browser and returns the result through _json_result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 185–203)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs low-level browser interaction actions such as mouse moves, clicks, keyboard input, scrolling, waiting, and screenshots. It can also save a screenshot into the shared workspace.

**Data flow**: It receives the tool context and ComputerInput, including a list of actions and optional screenshot-saving settings. It sends the cleaned action request to the shared browser surface’s computer method. If save_to_workspace is true, it requires a screenshot_base64 field, decodes it from base64 text into bytes, writes it to the sandbox path, and adds that path to the reply. If a screenshot is present, it returns JSON text for the non-image fields plus an ImageContent attachment; otherwise it returns plain JSON text.

**Call relations**: This is the handler registered for the computer tool. It calls _browser to perform the real browser actions, uses _required_str when a screenshot must be saved, writes through the sandbox when requested, and either uses _json_result or directly builds a ToolResult with both text and image content.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 206–214)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves it into the workspace. This turns an in-browser download into a normal file path the rest of the agent can use.

**Data flow**: It receives the tool context and WaitForDownloadInput, including optional download id, target path, and timeout. It asks the shared browser surface to wait for the download, then requires a filename and base64-encoded file content in the reply. It decodes the content into bytes, writes those bytes to the sandbox under the requested directory or the default downloads directory, and returns JSON containing the saved file path, filename, and reported size.

**Call relations**: This is the handler registered for the wait_for_download tool. It depends on _browser for the browser-side waiting, _required_str for checking the download reply, the sandbox for writing the file, and _json_result for returning the saved-file summary.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### Backend orchestration
Provides the per-turn backend interface that browser tools use to navigate, inspect, manipulate pages, and manage cleanup.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser tool use and turn cleanup`

The main idea in this file is “rent a browser only when someone actually uses it.” A BuaSurface starts with access to a CDP provider, which is a source of browser debugging connections. CDP means Chrome DevTools Protocol, the remote-control interface used to drive Chrome. The surface does not immediately connect. On the first browser action, it leases a browser connection, opens a BrowserSession, and then reuses that session for the rest of the turn.

It also protects recovery after a crash. When it gets a browser lease, it saves a durable token in a scoped store under the conversation id. Think of the token like a cloakroom ticket: if the process crashes before returning the browser, a restarted turn can use the ticket to reconnect to the same live session instead of opening a fresh browser and losing the page state. If the old session is gone, the token is cleared and a new lease is created.

Most public methods here are thin doorways to BrowserSession methods. They first call _open, then pass along the request. The important cleanup method is aclose: it closes the browser session, releases the lease, and clears the saved token during normal turn ending. If cleanup is skipped because of a hard crash, the token intentionally remains for recovery.

#### Function details

##### `BuaSurface._open`  (lines 45–54)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or reuses the browser session for this turn. It is the lazy-start gatekeeper: no Chrome connection is made until a browser tool actually needs one.

**Data flow**: It starts with the BuaSurface’s current state. If a BrowserSession is already present, it simply returns it. If not, it gets or creates a CDP lease, asks that lease for its connection endpoint, builds a BrowserSession with that endpoint and the chosen model name, opens it, stores it on the surface, and returns it.

**Call relations**: All browser-facing methods call this first, such as navigate, find, form_input, read_page, tab operations, uploads, computer actions, and download waiting. If there is no lease yet, it hands off to BuaSurface._acquire_lease; after that it creates the BrowserSession that the rest of the methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 56–69)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets the browser lease that this turn should use, either by reconnecting to a saved live session or by creating a new one. This is what allows crash recovery to continue with the same browser page when possible.

**Data flow**: It first asks BuaSurface._stored_token for a previously saved token. If one exists, it asks the CDP provider to reattach to that session. If that session is gone, it clears the bad token. When no usable token exists, it asks the CDP provider for a fresh lease, saves the new lease’s token through BuaSurface._store_token, and returns the lease.

**Call relations**: BuaSurface._open calls this when it needs a lease for the first time. It coordinates with BuaSurface._stored_token and BuaSurface._store_token so that normal runs get a fresh session, while recovered runs can reconnect to the session that survived a crash.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 71–75)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Reads the saved browser reattach token for this conversation, if one is available. The token is the small piece of information needed to reconnect to an existing hosted browser session.

**Data flow**: It checks whether both a scoped store and conversation id exist. If either is missing, it returns no token. Otherwise it looks up the conversation-specific key in the store and returns the value only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before creating a new lease. Its result decides whether the surface tries to reconnect to an existing browser session or starts from scratch.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 77–80)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser reattach token for this conversation. This is how the system remembers a live browser session across a crash, and how it avoids reconnecting to a session that was already released normally.

**Data flow**: It receives either a token string or None. If there is no store or no conversation id, it does nothing. Otherwise it writes the value under the conversation-specific token key; writing None clears the remembered token.

**Call relations**: BuaSurface._acquire_lease uses this to save a fresh token or clear a dead one. BuaSurface.aclose uses it during normal cleanup so later turns do not reconnect to a browser session that has already been closed.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 82–87)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates a browser tab to a requested web address. It validates that the provided url is really a string before asking the browser session to go there.

**Data flow**: It receives an arguments dictionary, opens the session, reads args["url"], and rejects it if it is not a string. It also converts the optional tab_id using _tab_id. Then it passes the url and tab id to the BrowserSession and returns that session’s result.

**Call relations**: Browser tools call this when they need to load a page. It relies on BuaSurface._open to ensure the browser exists, and on _tab_id to turn loosely typed JSON input into the tab id format the session expects.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 89–91)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs. This gives the tool layer a picture of what tabs exist and which page state is available.

**Data flow**: It receives an arguments dictionary, though it does not need any values from it. It opens or reuses the session, asks the BrowserSession for tab context, and returns that result.

**Call relations**: This is one of the public browser tool surface methods. It first goes through BuaSurface._open, then delegates the actual tab inspection to the BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 93–96)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab, optionally starting at a supplied URL. If no useful URL is supplied, it opens a blank page instead.

**Data flow**: It receives an arguments dictionary and opens the session. It reads the optional "url" value; if it is a non-empty string, that is used, otherwise it uses "about:blank". It asks BrowserSession to create the tab and returns the result.

**Call relations**: Browser tab-creation tools use this method. It depends on BuaSurface._open for the shared session and leaves the actual tab creation to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 98–100)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes one or more browser tabs according to the supplied arguments. This lets the tool layer remove pages that are no longer needed.

**Data flow**: It receives the tab-closing arguments, opens or reuses the session, passes those arguments to BrowserSession.tabs_close, and returns the result from the session.

**Call relations**: This public method is called when a browser tool asks to close tabs. It uses BuaSurface._open for setup and delegates the browser-specific work to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 102–104)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Uploads a file through the browser session using the supplied request details. This supports web pages that ask the user to choose a local file.

**Data flow**: It receives an arguments dictionary describing the upload, opens or reuses the session, passes the arguments to BrowserSession.upload_file, and returns whatever the session reports back.

**Call relations**: The upload browser tool enters through this method. BuaSurface._open provides the active browser connection, and BrowserSession performs the page-level upload behavior.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.read_page`  (lines 106–108)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads structured information from the current page. This is used when the tool layer needs a browser-aware view of what is on screen.

**Data flow**: It receives read options in an arguments dictionary, opens or reuses the session, sends the arguments to BrowserSession.read_page, and returns the session’s page-reading result.

**Call relations**: This method is part of the browser tool surface. It uses BuaSurface._open for connection setup and hands the actual page inspection to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 110–112)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets the text content from a page. This is useful when the caller wants readable page text rather than a richer browser state description.

**Data flow**: It receives an arguments dictionary, opens or reuses the browser session, passes the arguments to BrowserSession.get_page_text, and returns the text-related result.

**Call relations**: Browser text-reading calls come through this method. It follows the same pattern as the other surface methods: open through BuaSurface._open, then delegate to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 114–116)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, with optional help from a find completer. A find completer is helper logic that can complete or refine what should be searched for.

**Data flow**: It receives search arguments, opens or reuses the session, and passes both the arguments and the surface’s find_completer to BrowserSession.find. It returns the search result from the session.

**Call relations**: The find browser tool calls this method. BuaSurface._open supplies the session, and BrowserSession does the actual page search using the injected completer when one is available.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 118–120)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or edits form fields on a web page. This supports actions like typing into text boxes or choosing form values.

**Data flow**: It receives form-input instructions, opens or reuses the session, forwards the instructions to BrowserSession.form_input, and returns the result.

**Call relations**: Form-entry tool calls come through this surface method. It uses BuaSurface._open to ensure a browser is ready, then lets BrowserSession interact with the page.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 122–124)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Runs a lower-level computer-style browser action, such as interacting with the page through screen or input primitives. It is a general doorway for actions that are not covered by the more specific methods.

**Data flow**: It receives an arguments dictionary describing the computer action, opens or reuses the session, passes the request to BrowserSession.computer, and returns the session’s response.

**Call relations**: This method is called by the browser tool layer for computer-style actions. Like the other actions, it depends on BuaSurface._open for the browser connection and delegates the actual behavior to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 126–128)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to complete or become available according to the supplied arguments. This lets the tool layer pause until a file produced by the page is ready.

**Data flow**: It receives download-waiting options, opens or reuses the session, forwards the options to BrowserSession.wait_for_download, and returns the result.

**Call relations**: Download-related browser tools call this method. It uses BuaSurface._open to reach the active session, then BrowserSession watches the browser-side download state.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.aclose`  (lines 130–144)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the browser resources at the end of a normal turn. It closes the session, releases the lease, and clears the saved reconnect token so future turns do not attach to a session that was intentionally ended.

**Data flow**: It looks at the surface’s stored session and lease. If a session exists, it closes it and removes it from the surface. Whether that succeeds or fails, it then closes the lease, removes it, and calls BuaSurface._store_token with None to clear the saved token.

**Call relations**: Turn cleanup calls this after browser tool use. It calls BuaSurface._store_token to remove the recovery ticket. Its try/finally shape is important: even if closing the browser session has trouble, the lease is still released so hosted browser resources are not left behind.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 147–158)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loosely typed JSON tab id into either an integer tab id or no tab id. This keeps navigation tolerant of common input shapes while avoiding treating true or false as tab numbers.

**Data flow**: It receives a JSON value. Booleans become None, integers stay integers, floats are converted to integers, non-empty strings are parsed as integers, and everything else becomes None.

**Call relations**: BuaSurface.navigate calls this when it prepares the optional tab id for BrowserSession.navigate. It is a small helper that sits between flexible tool input and the stricter form expected by the browser session.

*Call graph*: called by 1 (navigate).


### Action and error contracts
Defines the validated browser action vocabulary and the specific error type for impossible model outputs.

### `extensions/browser/ufo_ext_browser/bua/actions.py`

`data_model` · `request handling`

This file is like a menu of actions that an automated browser can perform. Without it, different parts of the project might describe the same action in different ways, such as one piece saying “click here” with coordinates and another using a page element reference. That would make browser control fragile and hard to check.

The file uses Pydantic models, which are Python data objects that also validate their contents. Validation means the system can reject unclear or impossible instructions early, before they reach the browser. For example, a wait time must be between 0 and 30 seconds, and a scroll amount must either be a number from 0 to 5 screen-heights or the word “max”.

The `ActionType` list defines the allowed action names. `CLICK_ACTIONS` groups the click-like actions so other code can quickly ask, “Is this a click?”

`ScrollParameters` describes how to scroll: in which direction and by how much. `ComputerAction` is the main action envelope. It can hold the action name plus the extra details that action needs, such as a screen coordinate, typed text, a key combination, a scroll instruction, a duration, a drag starting point, or an element reference found earlier on the page. This keeps browser commands structured, predictable, and safe to pass between system components.


### `extensions/browser/ufo_ext_browser/bua/errors.py`

`data_model` · `request handling`

This file is very small, but it names an important failure case. In this project, an AI model may suggest actions or values for browser automation. Sometimes the model may “hallucinate,” meaning it confidently invents something that is not real, such as an element reference that was never present on the page. Without a dedicated error for that, the system would have to treat this like any other validation problem, making it harder to understand what went wrong and respond appropriately.

The file imports `ValidationError`, which is a general error used when incoming data does not pass checks. It then defines `HallucinationError` as a more specific kind of `ValidationError`. This is like putting a special label on one kind of rejected form: the form is still invalid, but now everyone knows the reason is “the supplied thing cannot exist,” not merely “the format was wrong.”

There is no extra behavior here. The value is in the name and inheritance: other code can raise or catch `HallucinationError` when it detects invented model output, while still allowing broader validation error handling to catch it too.


### Browser session lifecycle
Maintains the live Chrome connection, browser event handling, tab state, page operations, and download waiting used by the backend.

### `extensions/browser/ufo_ext_browser/bua/session.py`

`orchestration` · `active for one browser automation turn, from session startup through tool calls to teardown`

A BrowserSession is like the control desk for one round of browser automation. It connects to Chrome through CDP, the Chrome DevTools Protocol, which is Chrome’s remote-control interface. Once connected, it sets up listeners for important browser events: new tabs, closed tabs, page loading, network activity, downloads, JavaScript dialogs, and paused download requests. Without this file, the rest of the browser tools would not have a shared place to keep the current tabs, downloads, frame sessions, and cleanup tasks in sync.

The session itself does not do every job directly. Instead, it creates short-lived helper objects, called readers here, for each area of work: tabs, page structure, page content, downloads, dialogs, forms, JavaScript runtime, and computer-like input. Those helpers all receive the same session object, so they can use the same connection and shared state.

Opening a session resolves the browser endpoint, creates the WebSocket connection, configures downloads into a temporary folder, discovers tabs, and opens a blank starting tab. Closing reverses that work: it tries to close tabs, shuts down the connection, deletes the temporary download folder, cancels background tasks, and resets state. The file also supports use as an async context manager, so callers can safely say “open before this block, close after it,” even if an error happens.

#### Function details

##### `BrowserSession.__init__`  (lines 47–64)

```
def __init__(self, cdp: CdpEndpoint | None=None, model: str | None=None) -> None
```

**Purpose**: Creates an empty browser session object and prepares all the shared state it will need later. It does not connect to Chrome yet; it only sets up the local bookkeeping.

**Data flow**: It receives an optional CDP endpoint and optional model name. It stores those, chooses the coordinate size used for screenshots and clicks, and initializes empty lists and maps for tabs, frame sessions, downloads, dialogs, background tasks, and loading-state tracking. The result is a ready-but-not-open session object.

**Call relations**: BuaSurface._open creates this session before any browser work begins. The constructor also creates the settle tracker and tab-event tracker that later event listeners update during browsing.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_open); 2 external calls (__init__, model_coordinate_space).


##### `BrowserSession.open`  (lines 66–73)

```
async def open(self) -> None
```

**Purpose**: Opens the browser session if it is not already open. It is the safe public entry point for starting the Chrome connection.

**Data flow**: It checks whether a connection already exists. If not, it runs the full startup process; if startup fails for any reason, it calls close to clean up partial work and then lets the error continue outward.

**Call relations**: The async context manager calls this when entering a session block. It delegates the real setup to _bootstrap and uses close as the cleanup path if bootstrapping breaks halfway through.

*Call graph*: calls 2 internal fn (_bootstrap, close); called by 1 (__aenter__).


##### `BrowserSession._bootstrap`  (lines 75–108)

```
async def _bootstrap(self) -> None
```

**Purpose**: Performs the actual connection and browser setup work. This is where the session becomes attached to Chrome and starts listening to browser events.

**Data flow**: It reads the configured CDP endpoint, resolves it into a WebSocket URL, opens a CdpConnection, asks Chrome for version details, creates a temporary download folder, and registers event listeners for downloads, tabs, page loading, dialogs, network activity, and fetch pauses. It then enables target discovery and creates a blank tab, leaving the session with an active connection and one attached tab.

**Call relations**: open calls this during startup. It creates tab, download, and dialog readers so their event callbacks can be registered with the CDP connection.

*Call graph*: calls 4 internal fn (open, dialog_reader, download_reader, tab_reader); called by 1 (open); 4 external calls (__init__, to_thread, resolve_ws_url, as_str).


##### `BrowserSession.close`  (lines 110–133)

```
async def close(self) -> None
```

**Purpose**: Shuts down the session and removes temporary resources. It is meant to leave no open browser connection, temporary download folder, or background task behind.

**Data flow**: It tries to close each known tab through Chrome, closes the CDP connection, deletes the temporary download directory, clears all session state, resets loading and tab-event trackers, cancels background tasks, and empties task tracking. Afterward, the session is back to a closed, clean state.

**Call relations**: The async context manager calls this at the end of a session block. open also calls it if startup fails, so even partial sessions are cleaned up.

*Call graph*: calls 1 internal fn (__init__); called by 2 (__aexit__, open); 2 external calls (__init__, to_thread).


##### `BrowserSession.__aenter__`  (lines 135–137)

```
async def __aenter__(self) -> Self
```

**Purpose**: Lets BrowserSession be used with Python’s async context manager pattern. This gives callers an easy way to guarantee the browser opens before use.

**Data flow**: It receives the session object, calls open, waits for startup to finish, and then returns the same session for use inside the context block.

**Call relations**: Callers enter the session block through this method. It hands off to open for the actual startup work.

*Call graph*: calls 1 internal fn (open).


##### `BrowserSession.__aexit__`  (lines 139–140)

```
async def __aexit__(self, *exc: object) -> None
```

**Purpose**: Closes the browser session when an async context block ends. This keeps cleanup automatic even when an error happens inside the block.

**Data flow**: It receives any exception information from the context block, ignores the details, and calls close. The visible effect is that browser resources and local temporary files are cleaned up.

**Call relations**: This is paired with __aenter__. It hands off to close for the actual teardown work.

*Call graph*: calls 1 internal fn (close).


##### `BrowserSession.connection`  (lines 142–145)

```
def connection(self) -> CdpConnection
```

**Purpose**: Returns the live Chrome connection for helpers that need to send browser commands. It also protects callers from accidentally using a closed session.

**Data flow**: It checks the stored connection. If one exists, it returns it; if not, it raises BrowserUnavailable with a clear message.

**Call relations**: Reader objects use this kind of access when they need to talk to Chrome. It acts as the gatekeeper between session state and lower-level CDP commands.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.spawn_background`  (lines 147–150)

```
def spawn_background(self, coro: Coroutine[Any, Any, None]) -> None
```

**Purpose**: Starts a background async task and remembers it so the session can cancel it later. This is useful for work that must continue while other browser actions proceed.

**Data flow**: It receives a coroutine, schedules it as an asyncio task, adds that task to the session’s task set, and registers a callback to remove it from the set when it finishes.

**Call relations**: Other session helpers can use this when they need side work to run in parallel. close later uses the stored task set to cancel anything still running.

*Call graph*: 1 external calls (ensure_future).


##### `BrowserSession.is_top_level_frame`  (lines 152–153)

```
def is_top_level_frame(self, session_id: str | None, frame_id: Json | None) -> bool
```

**Purpose**: Checks whether a browser frame is the main page frame rather than an embedded frame. This matters because page events can come from many frames, but some actions only apply to the top-level page.

**Data flow**: It receives a CDP session id and frame id, creates a tab reader, and asks that reader to decide whether the frame is top-level. It returns true or false.

**Call relations**: This is a small bridge from session-level event handling to the tab helper, where frame and tab knowledge lives.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.init_session`  (lines 155–156)

```
async def init_session(self, session_id: str) -> None
```

**Purpose**: Initializes a newly attached CDP session so it is ready for browser automation. A CDP session here means a control channel for a specific tab or frame target.

**Data flow**: It receives a session id, creates a tab reader, and asks it to set up that session. The setup changes Chrome-side state rather than returning a data object.

**Call relations**: When tab discovery or attachment creates a new session, this method routes the setup work to BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.page`  (lines 158–159)

```
async def page(self, tab_id: int | None=None) -> Tab
```

**Purpose**: Returns the active tab, or a specific tab if an id is provided. Tool code uses this when it needs to know which browser page to act on.

**Data flow**: It receives an optional tab id, creates a tab reader, and asks it to find the matching tab. It returns a Tab object representing that page.

**Call relations**: Higher-level browser actions call this through the session when they need a current page. The selection rules are owned by BrowserTabs.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.navigate`  (lines 161–162)

```
async def navigate(self, url: str, tab_id: int | None=None) -> JsonDict
```

**Purpose**: Navigates a tab to a new URL. This is the session-level browser action for going to a web address.

**Data flow**: It receives a URL and an optional tab id, creates a tab reader, and asks it to perform navigation. It returns a JSON-style result describing the outcome.

**Call relations**: External tool calls reach navigation through this method. The session routes the request to BrowserTabs because tab selection and page loading belong there.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_info`  (lines 164–165)

```
async def tab_info(self, tab: Tab) -> JsonDict
```

**Purpose**: Builds a plain data summary for one tab. This gives callers a simple description of a tab instead of exposing all internal tab details.

**Data flow**: It receives a Tab object, creates a tab reader, and asks it to turn that tab into a JSON-style dictionary. The returned dictionary can be sent back to tool users.

**Call relations**: This is a wrapper around BrowserTabs.tab_info. It keeps tab-reporting logic in the tab helper while exposing it through the session.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_context`  (lines 167–168)

```
async def tabs_context(self) -> JsonDict
```

**Purpose**: Returns a summary of the current tab situation. This helps tools or agents understand what tabs exist and which one is active.

**Data flow**: It creates a tab reader, asks it for the current tabs context, and returns that information as a JSON-style dictionary.

**Call relations**: Session-level callers use this as the public doorway. BrowserTabs supplies the actual tab knowledge.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_titles`  (lines 170–171)

```
async def tab_titles(self) -> list[str]
```

**Purpose**: Returns the titles of known tabs. This is a lightweight way to identify open pages.

**Data flow**: It creates a tab reader, asks it to collect tab titles, and returns them as a list of strings.

**Call relations**: The session exposes this simple tab query while BrowserTabs does the browser-specific lookup.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tab_reader`  (lines 173–174)

```
def tab_reader(self) -> BrowserTabs
```

**Purpose**: Creates a BrowserTabs helper for tab-related work. The helper uses this session’s connection and state, plus the fixed viewport size.

**Data flow**: It reads the current session object and the viewport constant, then returns a new BrowserTabs instance. It does not itself change browser state.

**Call relations**: Many session methods call this before doing tab work, including startup, navigation, tab creation, tab closing, and tab queries.

*Call graph*: called by 10 (_bootstrap, init_session, is_top_level_frame, navigate, page, tab_info, tab_titles, tabs_close, tabs_context, tabs_create); 1 external calls (__init__).


##### `BrowserSession.page_reader`  (lines 176–177)

```
def page_reader(self) -> BrowserPage
```

**Purpose**: Creates a BrowserPage helper for understanding page frames and element references. This is used when a human-facing reference, such as an element id, must be mapped back to the browser page.

**Data flow**: It reads the session, viewport size, and maximum frame depth, then returns a BrowserPage instance. The returned helper can inspect frame trees and resolve references.

**Call relations**: resolve_ref and ref_point call this when they need page-structure logic. The session keeps the public method, while BrowserPage performs the detailed page mapping.

*Call graph*: called by 2 (ref_point, resolve_ref); 1 external calls (__init__).


##### `BrowserSession.content_reader`  (lines 179–180)

```
def content_reader(self) -> BrowserContent
```

**Purpose**: Creates a BrowserContent helper for reading and searching page content. This covers tasks such as page text, page trees, and find results.

**Data flow**: It passes the current session into BrowserContent and returns that helper. The helper then uses the session connection and tab state when called.

**Call relations**: tree, read_page, get_page_text, and find all call this. It keeps content-specific behavior out of the central session class.

*Call graph*: called by 4 (find, get_page_text, read_page, tree); 1 external calls (__init__).


##### `BrowserSession.download_reader`  (lines 182–183)

```
def download_reader(self) -> BrowserDownloads
```

**Purpose**: Creates a BrowserDownloads helper for download tracking and waiting. It includes the session and the maximum wait time allowed for downloads.

**Data flow**: It reads the session and timeout constant, then returns a BrowserDownloads instance. The helper can update download state from events or wait for a file to finish.

**Call relations**: _bootstrap uses this to register download event callbacks. wait_for_download uses it later when a caller wants to wait for an actual downloaded file.

*Call graph*: called by 2 (_bootstrap, wait_for_download); 1 external calls (__init__).


##### `BrowserSession.dialog_reader`  (lines 185–186)

```
def dialog_reader(self) -> BrowserDialogs
```

**Purpose**: Creates a BrowserDialogs helper for JavaScript dialog events such as alerts and confirms. This keeps dialog-specific behavior separate from general session setup.

**Data flow**: It passes the session into BrowserDialogs and returns the helper. The helper can then record or respond to dialog events using shared session state.

**Call relations**: _bootstrap calls this so it can register a dialog-opening callback with the CDP connection.

*Call graph*: called by 1 (_bootstrap); 1 external calls (__init__).


##### `BrowserSession.form_reader`  (lines 188–189)

```
def form_reader(self) -> BrowserForms
```

**Purpose**: Creates a BrowserForms helper for form-related actions. This includes entering values and uploading files.

**Data flow**: It passes the current session into BrowserForms and returns that helper. The returned object uses session state and browser commands when its methods are called.

**Call relations**: form_input and upload_file call this to route form work to BrowserForms instead of keeping form details in the session.

*Call graph*: called by 2 (form_input, upload_file); 1 external calls (__init__).


##### `BrowserSession.runtime_reader`  (lines 191–192)

```
def runtime_reader(self) -> BrowserRuntime
```

**Purpose**: Creates a BrowserRuntime helper for running JavaScript inside the browser. Runtime here means Chrome’s JavaScript execution environment for a page or frame.

**Data flow**: It passes the session into BrowserRuntime and returns the helper. The helper can then evaluate expressions or call functions on browser-side objects.

**Call relations**: eval_js and call_on call this when a higher-level action needs JavaScript execution.

*Call graph*: called by 2 (call_on, eval_js); 1 external calls (__init__).


##### `BrowserSession.tabs_create`  (lines 194–195)

```
async def tabs_create(self, url: str='about:blank') -> JsonDict
```

**Purpose**: Creates a new browser tab, optionally at a given URL. It is the session-level entry point for opening tabs.

**Data flow**: It receives a URL, defaulting to about:blank, creates a tab reader, and asks it to create the tab. It returns a JSON-style result describing the new tab or action outcome.

**Call relations**: Tool callers use this through the session. BrowserTabs performs the actual Chrome target creation and state update.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.tabs_close`  (lines 197–198)

```
async def tabs_close(self, args: JsonDict) -> JsonDict
```

**Purpose**: Closes a browser tab based on arguments from the caller. This lets higher-level tools remove tabs without knowing Chrome’s target details.

**Data flow**: It receives a JSON-style argument dictionary, creates a tab reader, and asks it to close the requested tab. It returns a JSON-style result.

**Call relations**: The session routes tab-closing tool calls to BrowserTabs, where tab identity and Chrome close commands are handled.

*Call graph*: calls 1 internal fn (tab_reader).


##### `BrowserSession.upload_file`  (lines 200–201)

```
async def upload_file(self, args: JsonDict) -> JsonDict
```

**Purpose**: Uploads a local file through a page’s file input. This supports browser tasks that need to choose a file on a website.

**Data flow**: It receives a JSON-style argument dictionary, creates a form reader, and asks it to perform the upload. It returns a JSON-style result from the form helper.

**Call relations**: The session exposes the tool-facing method, while BrowserForms contains the details of finding the file input and sending the file path to Chrome.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.tree`  (lines 203–204)

```
async def tree(self, args: JsonDict, filter_type: str='all') -> str
```

**Purpose**: Returns a structured view of the page content, optionally filtered by type. This is useful when a caller needs a map of what is on the page rather than just visible text.

**Data flow**: It receives arguments and a filter type, creates a content reader, and asks it to build the page tree. It returns the tree as a string.

**Call relations**: The session sends content-inspection requests to BrowserContent, which knows how to inspect and format the page structure.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.read_page`  (lines 206–207)

```
async def read_page(self, args: JsonDict) -> JsonDict
```

**Purpose**: Reads the current page into a tool-friendly summary. This gives an automation agent a digest of what is visible or relevant on the page.

**Data flow**: It receives a JSON-style argument dictionary, creates a content reader, and asks it to read the page. It returns a JSON-style result.

**Call relations**: Tool calls for page reading come through this method. BrowserContent does the page inspection work and returns the data.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.get_page_text`  (lines 209–210)

```
async def get_page_text(self, args: JsonDict) -> JsonDict
```

**Purpose**: Extracts text from the page. This is the simpler text-focused counterpart to more structured page reading.

**Data flow**: It receives a JSON-style argument dictionary, creates a content reader, and asks it for page text. It returns a JSON-style result containing the extracted text information.

**Call relations**: The session provides the public method, while BrowserContent performs the actual content extraction.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.find`  (lines 212–213)

```
async def find(self, args: JsonDict, complete: FindCompleter | None=None) -> JsonDict
```

**Purpose**: Searches for content on the page, with optional completion support for repeated or assisted finding. This helps locate text or elements before taking action.

**Data flow**: It receives search arguments and an optional FindCompleter, creates a content reader, and passes both along. It returns a JSON-style result with the find outcome.

**Call relations**: Find requests flow through the session to BrowserContent. The optional completer lets outside search-assistance logic participate without the session needing to know its details.

*Call graph*: calls 1 internal fn (content_reader).


##### `BrowserSession.form_input`  (lines 215–216)

```
async def form_input(self, args: JsonDict) -> JsonDict
```

**Purpose**: Types or sets values in page form fields. This supports actions such as filling search boxes, login forms, and other inputs.

**Data flow**: It receives a JSON-style argument dictionary, creates a form reader, and asks it to apply the input. It returns a JSON-style result describing what happened.

**Call relations**: The session routes form-entry tool calls to BrowserForms, which contains the page and input-specific logic.

*Call graph*: calls 1 internal fn (form_reader).


##### `BrowserSession.computer`  (lines 218–219)

```
async def computer(self, args: JsonDict) -> JsonDict
```

**Purpose**: Runs a computer-like browser interaction, such as mouse or keyboard actions in the viewport. This is for lower-level interaction when semantic page tools are not enough.

**Data flow**: It receives a JSON-style argument dictionary, creates a BrowserComputer with the session, viewport, and timeout, and runs the requested action. It returns a JSON-style result.

**Call relations**: Unlike the reader factory methods, this creates BrowserComputer directly for one run. It hands the action to BrowserComputer because that component knows how to translate tool commands into browser input.

*Call graph*: 1 external calls (__init__).


##### `BrowserSession.wait_for_download`  (lines 221–224)

```
async def wait_for_download(self, args: JsonDict) -> JsonDict
```

**Purpose**: Waits for a browser download to complete and reports the result. It protects callers from waiting when the browser was never opened.

**Data flow**: It first checks that a temporary download directory exists. If not, it raises BrowserUnavailable. Otherwise it creates a download reader and asks it to wait for the requested download in that directory, returning a JSON-style result.

**Call relations**: Download events are registered during _bootstrap, and this method later uses BrowserDownloads to connect those events to an actual completed file.

*Call graph*: calls 1 internal fn (download_reader); 1 external calls (__init__).


##### `BrowserSession.eval_js`  (lines 226–227)

```
async def eval_js(self, session_id: str, expression: str) -> Json
```

**Purpose**: Evaluates a JavaScript expression in a specific browser session. This is a direct escape hatch for reading or computing something inside the page.

**Data flow**: It receives a CDP session id and JavaScript expression, creates a runtime reader, and asks it to evaluate the expression. It returns the JSON-like value reported by Chrome.

**Call relations**: Higher-level helpers can call this when they need page-side JavaScript. BrowserRuntime owns the details of sending the runtime command to Chrome.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.call_on`  (lines 229–236)

```
async def call_on(self, session_id: str, object_id: str, function: str, arguments: list[Json] | None=None) -> JsonDict
```

**Purpose**: Calls a JavaScript function on an existing browser-side object. This is used when code already has a reference to a page object and wants to run a function against it.

**Data flow**: It receives a session id, browser object id, JavaScript function text, and optional arguments. It creates a runtime reader, passes those values along, and returns a JSON-style dictionary with the call result.

**Call relations**: This is the session-level doorway into BrowserRuntime.call_on. It keeps JavaScript runtime mechanics outside the main session class.

*Call graph*: calls 1 internal fn (runtime_reader).


##### `BrowserSession.resolve_ref`  (lines 238–239)

```
def resolve_ref(self, tab: Tab, ref: str) -> tuple[FrameNode, int]
```

**Purpose**: Resolves a page reference string into the frame and index it points to. A reference is a compact label used by tools to talk about page elements.

**Data flow**: It receives a Tab object and reference string, creates a page reader, and asks it to decode the reference. It returns the matching frame node and numeric index.

**Call relations**: When another action needs to turn a user-facing element reference into an internal page location, it comes through this method and delegates to BrowserPage.

*Call graph*: calls 1 internal fn (page_reader).


##### `BrowserSession.ref_point`  (lines 241–242)

```
async def ref_point(self, tab: Tab, ref: str) -> tuple[int, int]
```

**Purpose**: Finds a screen point for a referenced page element. This is useful before clicking, dragging, or otherwise interacting at coordinates.

**Data flow**: It receives a Tab object and reference string, creates a page reader, and asks it to compute the point. It returns an x and y coordinate pair.

**Call relations**: Coordinate-based actions can use this method to bridge from an element reference to an actual point on the page. BrowserPage performs the page inspection and geometry work.

*Call graph*: calls 1 internal fn (page_reader).

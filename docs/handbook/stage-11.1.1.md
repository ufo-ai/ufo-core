# Agent-facing browser tool surface and browser acquisition contract  `stage-11.1.1`

This stage is the agent’s front door to the browser. It belongs to the main work loop, where an agent may need to open pages, inspect them, click or type, upload files, take screenshots, or wait for downloads. Its job is to offer these abilities in a simple, stable way while hiding the messy details of how Chrome is actually reached.

The core browser contract in core/src/ufo/browser.py is like a rental agreement for a browser connection. The rest of the system can ask for Chrome for one turn of work without caring whether it is running locally, in a sandbox, or on a remote machine.

The tools file in extensions/browser/ufo_ext_browser/tools.py names the actions the agent can call, such as “navigate” or “read page,” and connects those names to real browser behavior.

The backend in extensions/browser/ufo_ext_browser/bua/backend.py does the turn-by-turn work. It opens the browser only when needed, performs requested actions, and cleans up safely when the turn ends.

## Files in this stage

### Browser Acquisition Contract
Defines the core turn-scoped contract for obtaining a Chrome browser connection without exposing where or how the browser is hosted.

### `core/src/ufo/browser.py`

`io_transport` · `startup and per-turn browser setup/teardown`

This file is a boundary, or “seam,” between the core system and whatever provides Chrome. The core needs a Chrome DevTools Protocol endpoint, often called CDP, which is a URL that automation tools use to control Chrome. But core deliberately does not decide where Chrome runs. That choice belongs to extensions.

The main idea is a lease. A CdpProvider is like a rental desk for browsers. For each turn, it gives out a CdpLease, which is a temporary hold on a browser connection. The lease can reveal the endpoint URL, provide a token that can be saved for reconnecting later, and release the browser when the turn ends.

The file also covers a practical problem: files and downloads may live in different places depending on where Chrome runs. If Chrome is inside the same sandbox as the task, a file path can often be used directly. If Chrome is remote, the provider may need to upload the file first, or fetch downloaded bytes through an external service. The lease interface hides those differences so the browser-driving engine can ask simple questions like “where should Chrome open this file?” and “give me the bytes for this download.”

Without this file, core would have to know too much about every browser provider, making local sandbox Chrome and remote hosted Chrome much harder to swap.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This method gives the browser-driving code the CDP connection details for the leased Chrome. CDP is the control channel used to talk to Chrome, and the result includes both the URL and any needed connection headers.

**Data flow**: The lease already knows what browser session it represents. When asked, it returns a CdpEndpoint containing the connectable URL and optional headers, such as authentication or routing information. If a real provider cannot supply an endpoint, its implementation may fail rather than returning a useless connection.

**Call relations**: A browser extension uses this after a provider has created or reattached a lease. The provider supplies the lease; the engine asks the lease for the endpoint; then the engine connects to Chrome using that information.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: This method returns a saved handle that can be used to reconnect to the same browser session later. It matters when a turn is recovered or resumed and should try to continue with the existing browser instead of always starting over.

**Data flow**: The lease starts with provider-specific knowledge, such as a hosted session ID or a stable endpoint name. The method turns that into a string that can be stored safely. Later, that string can be passed back to a provider to try reattaching.

**Call relations**: The browser extension persists this value while using a lease. On a later run, orchestration code can give the token to CdpProvider.reattach so the provider can try to rebuild a fresh lease around the still-living session.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This method tells the browser where it can open a workspace file. It hides the difference between a browser that shares the sandbox filesystem and a remote browser that needs the file uploaded first.

**Data flow**: It receives a path and a read callback. For a sandbox-local browser, the implementation can return the same path because Chrome can already see the file. For a remote browser, the implementation can call the read callback to get the file bytes, upload them somewhere the remote Chrome can reach, and return that remote location.

**Call relations**: The browser engine calls this before asking Chrome to open or upload a file. The lease decides whether any copying is needed, because only the provider knows where Chrome actually runs.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: This method tells Chrome where it should put downloaded files for this lease. It gives the browser engine one simple place to ask, even though local and remote providers store downloads differently.

**Data flow**: The lease uses its provider-specific storage setup to return a directory or location suitable for downloads. A sandbox browser may return a sandbox path. A hosted browser may return a provider-controlled download area.

**Call relations**: The browser engine calls this when configuring Chrome downloads for the turn. Later, when Chrome reports a completed download by its identifier, the engine can ask CdpLease.fetch_download to retrieve the actual bytes.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This method retrieves the bytes of a completed download. It lets the rest of the system treat downloads the same whether Chrome saved them in the sandbox or in a remote provider’s storage.

**Data flow**: It receives a download GUID, which is Chrome’s unique identifier for the completed download. The implementation uses that identifier to find the stored file, reads or fetches its bytes, and returns those bytes to the caller.

**Call relations**: After the browser engine has configured downloads with CdpLease.download_dir and Chrome finishes a download, the engine calls this method. The lease bridges from Chrome’s download identifier back to bytes the task can use.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: This method releases whatever hold the lease has on the browser session. It is the cleanup step at the end of a turn.

**Data flow**: The lease contains any resources or session references created by the provider. When closed, an implementation may do nothing for a static local endpoint, or it may release a remote hosted session so it is not left running unnecessarily. It returns no value; the important result is cleanup.

**Call relations**: The turn-level code calls this when browser use is finished. It is the matching end step for a lease created by CdpProvider.lease or CdpProvider.reattach.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This method creates a new browser lease for a turn. It is how core asks, “Give me a Chrome I can use now,” without knowing where that Chrome comes from.

**Data flow**: It may receive a SandboxSession, which represents the task’s isolated working environment. A sandbox-based provider can use that session to find Chrome inside the sandbox, while a remote or static provider may ignore it. The method returns a CdpLease that the browser engine can use for connection, files, downloads, and cleanup.

**Call relations**: This is called when a new turn needs a browser and there is no usable saved session to reconnect to. It hands back the lease that the browser extension uses through the rest of the turn.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This method tries to reconnect to a browser session named by a previously saved token. It supports recovery after interruption, as long as the old session still exists.

**Data flow**: It receives a token string that came from CdpLease.token. The provider interprets that token in its own way, such as looking up a hosted browser session or resolving a stable endpoint. If the session is still alive, it returns a new CdpLease for it. If the session is gone, it raises SessionGone so the caller knows to start fresh.

**Call relations**: This is used before minting a new lease when the system wants to resume prior browser state. If it succeeds, the browser engine continues through the returned lease; if it raises SessionGone, the caller falls back to CdpProvider.lease.


### Agent Browser Tool Surface
Exposes browser capabilities to agents through callable tools and routes those requests into the per-turn browser backend that performs and cleans up real browser work.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `active during an agent turn when browser tools are called`

This file gives the agent a safe, consistent way to use a browser. Each browser action has a small input shape, so the agent must provide the right information before the action runs. The file then forwards that request to one shared `BuaSurface`, which is the browser-control layer for the current turn.

The important idea is that all browser tools in one turn reuse the same browser surface. That is like checking out one set of car keys for a trip instead of asking for new keys at every stop. The helper `_browser` creates the surface only when the first browser tool needs it, keeps it for the rest of the turn, and registers cleanup so the browser connection and any hosted browser session are released afterward.

Most tool functions are thin wrappers. They pass validated practical arguments to the browser surface and return replies as JSON text. Two tools do extra work with files: `computer` can save a screenshot into the shared workspace, and `wait_for_download` decodes downloaded bytes and writes them to the workspace. Without this file, the agent would not have the named browser tools, and browser sessions, screenshots, and downloads would not be connected cleanly to the rest of the system.

#### Function details

##### `_browser`  (lines 105–128)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser-control surface for the current turn, creating it if this is the first browser tool call. This prevents every browser action from opening its own separate connection and makes cleanup reliable at the end of the turn.

**Data flow**: It receives the tool context, which contains the browser connection provider, the agent model, the sandbox, extension storage, and turn information. It looks up an existing `BuaSurface` using the turn cleanup registry as the key. If none exists, it builds a new surface from the context, stores it for reuse, and registers its close method for later cleanup. It returns the ready-to-use browser surface.

**Call relations**: All browser tool handlers call this before doing real browser work. When `_navigate`, `_find`, `_form_input`, `_get_page_text`, `_read_page`, `_tabs_close`, `_tabs_context`, `_computer`, and the other browser actions need Chrome, this helper supplies the shared `BuaSurface` that actually performs the action.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 131–132)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain Python reply dictionary as a tool result containing JSON text. This gives every simple browser tool a consistent response format.

**Data flow**: It receives a dictionary returned by the browser surface. It converts that dictionary to a JSON string and places the string inside a text content object, then places that content inside a tool result. The output is the standard result object returned to the agent.

**Call relations**: Most tool handlers call this after the browser surface replies. It is the final packaging step for navigation, tab operations, page reading, finding elements, form input, downloads, and computer actions when no separate image response is needed.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 135–138)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a reply contains a needed non-empty string, such as screenshot data or a downloaded filename. It fails early with a clear error if the browser reply is missing something essential.

**Data flow**: It receives a value from a browser reply and the name of the field being checked. If the value is a non-empty string, it returns that string. If not, it raises an error that names the missing field.

**Call relations**: `_computer` uses this before saving a screenshot, and `_wait_for_download` uses it before writing a downloaded file. In both cases, this helper protects later file-writing code from trying to decode or save missing data.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 141–145)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool, such as opening a URL or moving through browser history. It is used when the agent needs to change what page is shown.

**Data flow**: It receives the tool context and validated navigation input. It turns the input into a JSON-friendly dictionary, leaving out empty values. It sends that dictionary to the shared browser surface’s navigation method, then wraps the reply as JSON text for the agent.

**Call relations**: This function is registered as the handler for the `navigate` tool. When the agent calls that tool, `_navigate` gets the shared browser surface through `_browser`, hands off the navigation request, and uses `_json_result` to return the browser’s reply.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 148–149)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. It helps the agent understand what pages are already available before choosing where to act next.

**Data flow**: It receives the tool context and a small input object used mainly for the activity description. It sends an empty request to the browser surface’s tab-context method. The browser surface returns tab information, which this function wraps as JSON text.

**Call relations**: This function is registered as the handler for the `tabs_context` tool. When invoked, it asks `_browser` for the turn’s shared surface and then formats the surface’s tab summary through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 152–154)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opening a given URL. If no URL is supplied, it opens a blank page.

**Data flow**: It receives the tool context and the tab creation input. It builds a small request with either the provided URL or `about:blank`. It sends that to the browser surface, receives the new-tab reply, and returns it as JSON text.

**Call relations**: This function is registered as the handler for the `tabs_create` tool. It gets the shared browser surface from `_browser`, asks it to create the tab, and uses `_json_result` to send the result back to the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 157–161)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, usually the current tab or a specific tab named by its id. It is used when the agent is done with a page and wants to reduce clutter.

**Data flow**: It receives the tool context and close-tab input. It converts the input to a JSON-friendly dictionary, omitting empty fields. It sends the request to the browser surface and returns the browser’s reply as JSON text.

**Call relations**: This function is registered as the handler for the `tabs_close` tool. It follows the common pattern in this file: get the shared surface with `_browser`, hand off the tab operation, then package the reply with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 164–168)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a file input on a web page using files from the workspace. It lets the agent upload existing workspace files through the browser, like attaching a document to a form.

**Data flow**: It receives the tool context and upload input, including a browser element reference and one or more workspace file paths. It removes empty values and the activity-log description, then sends the practical upload request to the browser surface. The browser’s reply is returned as JSON text.

**Call relations**: This function is registered as the handler for the `upload_file` tool. It relies on `_browser` to reach the active page and on the browser surface to perform the actual file-input operation, then `_json_result` formats the reply.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 171–175)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page structure that the browser exposes for accessibility, such as buttons, links, headings, and text regions. This helps the agent understand and interact with the page without relying only on pixels.

**Data flow**: It receives the tool context and page-reading options, such as depth, filtering mode, element reference, or tab id. It turns those options into a JSON-friendly request without the human-only description, sends the request to the browser surface, and returns the resulting page structure as JSON text.

**Call relations**: This function is registered as the handler for the `read_page` tool. It asks `_browser` for the shared surface, delegates the page inspection to that surface, and then uses `_json_result` so the agent receives a normal text result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 178–182)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw visible text from a browser page. It is useful when the agent needs the words on the page more than the full interactive structure.

**Data flow**: It receives the tool context and optional tab information. It converts the input to a JSON-friendly dictionary, leaving out empty fields. It sends the request to the browser surface’s text extraction method and wraps the reply as JSON text.

**Call relations**: This function is registered as the handler for the `get_page_text` tool. Like the other page-reading tools, it uses `_browser` to reuse the turn’s browser surface and `_json_result` to return the surface’s answer.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 185–189)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the current browser page for elements that match a query, such as a label, role, text, name, or URL. It helps the agent locate the right target before clicking or filling something.

**Data flow**: It receives the tool context and a search query. It removes empty fields and the human activity description, then sends the search request to the browser surface. The browser surface returns matching element information, and this function returns it as JSON text.

**Call relations**: This function is registered as the handler for the `find` tool. It gets the shared browser surface with `_browser`; that surface can use the context’s find-ranking hook to improve search results. `_find` then packages the matches with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 192–196)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form field identified by a browser reference. It is used for actions like typing into an input, selecting a value, or filling a field in a web form.

**Data flow**: It receives the tool context and form input data: the target reference, the value to set, and optional tab information. It converts that to a JSON-friendly request without empty fields or the human-only description. It sends the request to the browser surface and returns the browser’s reply as JSON text.

**Call relations**: This function is registered as the handler for the `form_input` tool. It follows the shared tool pattern: obtain the browser surface through `_browser`, delegate the form update, then return the result through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 199–217)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs lower-level browser interaction actions, such as mouse, keyboard, scrolling, waiting, and screenshots. It is the tool for computer-like control when higher-level page tools are not enough.

**Data flow**: It receives the tool context and a sequence of computer actions. It sends those actions to the browser surface after removing empty values and the activity description. The surface may return a screenshot as base64 text, which is a text-safe encoding of image bytes. If the caller asked to save the screenshot, this function decodes it and writes it into the sandbox workspace, then records the saved path in the reply. If there is screenshot data, it returns both JSON text and an image content item; otherwise it returns only JSON text.

**Call relations**: This function is registered as the handler for the `computer` tool. It uses `_browser` to perform the actual browser interaction, `_required_str` when a saved screenshot must be present, the sandbox to write the image file, and either `_json_result` or a mixed text-and-image `ToolResult` to send the outcome back.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 220–228)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves the downloaded file into the shared workspace. This makes downloaded files available by path to the parent agent or sibling subagents.

**Data flow**: It receives the tool context and download options, such as a download id, output path, and timeout. It asks the browser surface to wait for the download and return the file name plus base64-encoded content. It checks that the filename and content are present, decodes the file bytes, writes them into the sandbox under the chosen download directory, and returns JSON containing the saved path, filename, and size.

**Call relations**: This function is registered as the handler for the `wait_for_download` tool. It gets the shared browser surface with `_browser`, validates the important fields with `_required_str`, writes the completed file through the sandbox, and formats the final file information with `_json_result`.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `active during a browser-using turn, from first browser tool call through turn cleanup`

The central piece in this file is `BuaSurface`, a small bridge between higher-level browser tools and a live Chrome-like browser connection. Think of it like a temporary rental desk for a browser: if a turn never uses the browser, nothing is rented; on the first browser action, it leases a browser connection, opens a `BrowserSession`, and then reuses that session for the rest of the turn.

The file also protects continuity and cleanup. If the process crashes in the middle of a browser action, a saved token can let the next recovery attempt reconnect to the same live browser instead of starting over. On normal shutdown, that token is cleared so later turns do not accidentally attach to an old browser.

File movement is another important job here. Uploads may need to move bytes from the turn’s workspace to a browser running somewhere else, so this file reads workspace files safely, limits their size, sends them through the lease, and checks that the page really received the uploaded bytes. Downloads work in the opposite direction: the browser’s transport fetches the downloaded bytes, and this file returns them as base64 text with a safe filename. Without this file, browser tools would either connect too early, leak browser sessions, lose crash recovery, or mishandle files crossing between the workspace and the browser.

#### Function details

##### `BuaSurface._open`  (lines 65–78)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens or returns the current browser session for this turn. It avoids creating a browser connection until the first browser action actually needs one.

**Data flow**: It starts with the surface’s stored `session` and `lease` fields. If a session already exists, it returns it. Otherwise it gets or creates a lease, asks that lease for a browser endpoint and download directory, builds a `BrowserSession`, opens it, saves it on the surface, and returns the ready session.

**Call relations**: All user-facing browser actions call this first, including navigation, page reading, tab work, uploads, downloads, form input, find, and computer control. If no lease exists yet, it hands off to `BuaSurface._acquire_lease`; then it creates the lower-level `BrowserSession` that actually talks to the browser.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 80–93)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets the browser lease for the turn, either by reconnecting to a saved live session after a crash or by creating a new lease. This is what makes browser work both lazy and recoverable.

**Data flow**: It looks for a stored reconnect token. If one is present, it asks the CDP provider to reattach to that existing browser session. If that session is gone, it clears the bad token. If there is no usable token, it requests a fresh lease from the provider, stores the new token if available, and returns the lease.

**Call relations**: `BuaSurface._open` calls this when it needs a lease for the first time. This function reads through `BuaSurface._stored_token` and writes through `BuaSurface._store_token`, so the reconnect state follows the conversation across a crash but is refreshed when a new lease is made.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 95–99)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Reads the saved browser reconnect token for this conversation, if the surface has a store and conversation ID. The token is a durable note saying “this browser session may still exist.”

**Data flow**: It checks whether both the scoped store and conversation ID are available. If not, it returns nothing. If they are available, it reads the conversation-specific key and returns the value only if it is a string.

**Call relations**: `BuaSurface._acquire_lease` calls this before deciding whether to reconnect to an existing browser session or create a new one. It is the read side of the crash-recovery token flow.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 101–104)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser reconnect token for this conversation. Saving supports crash recovery; clearing prevents future turns from reusing a browser session that has already been released.

**Data flow**: It receives either a token string or `None`. If the surface has no store or conversation ID, it does nothing. Otherwise it writes that value to the conversation-specific key in the scoped store.

**Call relations**: `BuaSurface._acquire_lease` uses this to save a fresh token or clear a dead one. `BuaSurface.aclose` uses it at normal turn cleanup to erase the token after the lease has been released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 106–111)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Navigates the browser to a requested URL, optionally in a specific tab. It validates that the URL is actually text before passing it on.

**Data flow**: It receives an argument dictionary, opens the browser session, reads `url`, checks that it is a string, converts the optional `tab_id` into an integer or `None`, and returns the result from the session’s navigation command.

**Call relations**: This is one of the public tool methods on the surface. It starts by calling `BuaSurface._open` to ensure a browser is ready, uses `_tab_id` to normalize the tab identifier, and then hands the real navigation work to the lower-level browser session.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 113–115)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the browser’s current tabs. This gives callers enough context to understand what pages are open.

**Data flow**: It receives the tool arguments, opens the browser session, asks the session for its tab context, and returns that information unchanged.

**Call relations**: This public tab tool follows the common pattern in this file: first call `BuaSurface._open`, then delegate the browser-specific work to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 117–120)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If no usable URL is provided, it opens a blank page instead.

**Data flow**: It receives an argument dictionary, opens the browser session, reads the optional `url`, replaces a missing or empty URL with `about:blank`, and returns the session’s response after creating the tab.

**Call relations**: Like the other tab methods, it depends on `BuaSurface._open` for lazy connection setup and then asks the browser session to perform the actual tab creation.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 122–124)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes one or more browser tabs according to the provided arguments. The exact tab-selection details are left to the browser session.

**Data flow**: It receives the arguments, opens the current browser session, passes the arguments through to the session’s close-tab operation, and returns the session’s result.

**Call relations**: This method is part of the public browser tool surface. It uses `BuaSurface._open` for setup and delegates the actual close operation to `BrowserSession`.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 126–147)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Uploads workspace files into a page’s file input. It makes sure each requested path is a workspace path, passes the file through the browser lease, and checks that the page actually received the bytes.

**Data flow**: It receives arguments containing a `files` list. For each file path, it validates the value, resolves it under the workspace, asks the lease to place the file somewhere the browser can open, and provides `_read` as the callback for transports that need the bytes. It then replaces the original file list with the placed browser-visible paths, tells the session to attach them, waits for upload settlement, and returns the session’s initial reply.

**Call relations**: This public tool starts by calling `BuaSurface._open`, then uses `BuaSurface._lease` to reach the transport lease. It uses `workspace_path` to keep file reads inside the intended workspace, passes `BuaSurface._read` through `functools.partial` as the byte reader, and finishes by calling `BuaSurface._settle_upload` so remote uploads do not appear attached before their contents have arrived.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 149–169)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until the page’s file input reports the same file sizes that this surface sent. This prevents a subtle remote-browser problem where the filename is attached before the file contents have landed.

**Data flow**: It looks at the surface’s `_shipped` byte counts. If nothing was shipped, it returns immediately. Otherwise it repeatedly asks the session what sizes are attached, compares them with the expected sizes, sleeps briefly between attempts, and re-attaches the files while waiting. If the sizes never match, it raises an error explaining what the browser reported versus what was sent.

**Call relations**: `BuaSurface.upload_file` calls this after the first file attachment. During the wait, it calls `BrowserSession.attached_sizes` to inspect the page and `BrowserSession.upload_file` to retry the attach after a short `asyncio.sleep`.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 171–174)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the current browser lease, or fails clearly if no lease exists. It is a guardrail for operations that must talk to the transport layer.

**Data flow**: It reads the surface’s `lease` field. If the lease is present, it returns it. If not, it raises a runtime error saying the browser has no CDP lease.

**Call relations**: `BuaSurface.upload_file` uses this when placing files for the browser. `BuaSurface.wait_for_download` uses it when fetching downloaded bytes back from wherever the browser stored them.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 176–205)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file from the sandbox so it can be sent to a remote browser. It checks that the file is readable and not too large before loading its bytes.

**Data flow**: It receives a workspace-resolved path. It requires a sandbox, quotes the path safely for shell use, asks the sandbox for the file size, rejects unreadable or oversized files, then runs `base64` inside the sandbox to read the file as text. It decodes that base64 data back into bytes in a worker thread, records how many bytes were shipped, and returns the bytes.

**Call relations**: `BuaSurface.upload_file` passes this function as a callback to the lease’s file-placement step. The function uses `shlex.quote` to avoid unsafe shell path handling and `asyncio.to_thread` so base64 decoding does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 207–209)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Reads the current page in the browser using the richer page-reading behavior provided by the browser session.

**Data flow**: It receives the caller’s arguments, opens the browser session, sends those arguments to the session’s page-reading method, and returns the resulting page data.

**Call relations**: This public tool method follows the common surface pattern: call `BuaSurface._open` to ensure the session exists, then let `BrowserSession` do the detailed browser interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 211–213)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets text from the current page. This is useful when the caller wants page contents in a simpler text form.

**Data flow**: It receives an argument dictionary, opens the browser session, passes the arguments to the session’s text-extraction method, and returns the session’s result.

**Call relations**: It is called as part of the browser tool surface. It relies on `BuaSurface._open` for connection setup and delegates the actual page text extraction to the browser session.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 215–217)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Searches or locates something on the page, with optional help from a host-side find completer. The completer can improve or finish the search request before the browser acts on it.

**Data flow**: It receives find arguments, opens the browser session, passes the arguments plus the surface’s `find_completer` to the session, and returns whatever matches or navigation aids the session reports.

**Call relations**: As a public browser tool method, it starts with `BuaSurface._open`. It then hands both the request and the optional completer to `BrowserSession.find`, where the browser-specific page search happens.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 219–221)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or changes form fields on the page. It gives higher-level tools a simple way to type into browser forms.

**Data flow**: It receives form-input arguments, opens the browser session, forwards the arguments to the session’s form input operation, and returns the browser’s response.

**Call relations**: This method is part of the public tool surface. It uses `BuaSurface._open` for session readiness and then delegates to `BrowserSession` for the actual page interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 223–225)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-style browser actions, such as visual or input operations, through the active browser session.

**Data flow**: It receives an argument dictionary, opens the browser session, passes the arguments to the session’s computer-control method, and returns the result.

**Call relations**: This is another public browser action. It shares the same setup path as the rest of the surface by calling `BuaSurface._open`, then lets `BrowserSession.computer` carry out the browser-side action.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 227–243)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for the browser to finish a download and returns the downloaded file’s name, size, and contents. It also makes the filename safe so it cannot escape into an unexpected folder.

**Data flow**: It receives wait arguments, opens the browser session, waits for the session to report a completed download, asks the lease to fetch the bytes for that download, base64-encodes those bytes in a worker thread, and returns a dictionary containing a safe one-segment filename, the encoded content, and the byte size.

**Call relations**: This public download tool calls `BuaSurface._open` to reach the browser and `BuaSurface._lease` to reach the transport that can fetch the stored download. It uses `contained_leaf` to strip unsafe path pieces from the filename and `asyncio.to_thread` so encoding the file does not block the async event loop.

*Call graph*: calls 2 internal fn (_lease, _open); 2 external calls (to_thread, contained_leaf).


##### `BuaSurface.aclose`  (lines 245–259)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved reconnect token at normal turn end. This prevents leaked browser resources and stops later turns from reattaching to a released session.

**Data flow**: It checks whether a session exists and closes it, then clears the session field. In a `finally` block, it releases the lease if present, clears the lease field, and stores `None` as the reconnect token. The `finally` matters because the lease is released even if closing the session fails.

**Call relations**: The turn cleanup code is expected to call this when the browser surface is no longer needed. It uses `BuaSurface._store_token` to remove the recovery token; if a hard crash prevents this function from running, the token intentionally remains available for recovery.

*Call graph*: calls 1 internal fn (_store_token).


##### `_tab_id`  (lines 262–273)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a loose tab identifier from tool input into either an integer tab ID or `None`. It treats booleans as not-a-tab-ID because booleans are technically integers in Python but are not meaningful tab numbers here.

**Data flow**: It receives a JSON-like value. If the value is an integer, float, or non-empty string, it converts it to an integer. If it is a boolean, empty, missing, or another type, it returns `None`.

**Call relations**: `BuaSurface.navigate` calls this before asking the browser session to navigate. It keeps tab selection input forgiving while still giving the session a clean integer-or-nothing value.

*Call graph*: called by 1 (navigate).

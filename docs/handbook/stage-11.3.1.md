# Web browsing, research, and site publishing tools  `stage-11.3.1`

This stage provides the agent’s web-facing tools. It is part of the main work loop: when the agent needs to look something up, operate a browser, or publish a site, these files translate that request into safe, structured actions.

The browser tools are like a remote control for a real web browser. They let the agent open pages, read what is on them, click buttons, type into fields, upload files, and save downloads. They also keep all browser actions in the same turn using one shared connection, then clean it up afterward so nothing is left hanging.

The research tools are the fast lookup desk. They check that a search or fetch request is valid, send it to the configured search service, and return results in a simple JSON format the agent can read.

The site tools are the publishing station. They build, preview, deploy, publish, or assign hosted websites from sandbox files or running apps, while checking ownership, visibility, safety, and whether the site is ready.

## Files in this stage

### Browser Automation
Agent-facing browser tools coordinate page navigation, interaction, content extraction, uploads, downloads, and shared browser-session cleanup.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `request handling during browser tool calls in an agent turn`

This file is the bridge between the agent's tool system and the browser automation engine. Without it, the agent might know that a browser tool exists, but it would not have a safe, consistent way to turn a request like “navigate to this URL” or “save this download” into real browser work.

The file defines small input models for each browser action, so each tool receives arguments in a predictable shape. When a browser tool is used, the helper `_browser` creates one `BuaSurface`, which is the object that actually talks to the browser through CDP, the Chrome DevTools Protocol. CDP is Chrome’s remote-control interface. That browser surface is created only once per agent turn and is reused by later browser calls in the same turn, like keeping one phone call open instead of dialing again for every sentence. It is also registered for cleanup so the connection and any hosted browser session are released at the end of the turn.

Most handlers are thin translators: they validate or serialize their inputs, call the matching browser-surface method, and return JSON text. Two tools also move bytes through the workspace sandbox: `computer` can save a screenshot file, and `wait_for_download` writes a downloaded file into the shared workspace so other parts of the agent can use it by path.

#### Function details

##### `_browser`  (lines 87–110)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser-control surface for the current agent turn. It creates the surface on first use, reuses it for later browser tools in the same turn, and arranges for it to be closed when the turn ends.

**Data flow**: It receives the tool context, which contains things like the CDP provider, sandbox, model, cleanup registry, and extension storage. If a surface already exists for this turn’s cleanup registry, it returns it. If not, it builds a new `BuaSurface`, stores it in the per-turn cache, registers its close operation for cleanup, and returns the new surface. If no CDP provider is configured, it stops with an error because there is no browser transport to use.

**Call relations**: Every browser tool handler asks `_browser` for the shared surface before doing real browser work. `_browser` is the doorway to `BuaSurface`: handlers such as `_navigate`, `_find`, `_computer`, and `_wait_for_download` depend on it so they all talk to the same browser session during the turn.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 113–114)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Wraps a plain Python reply into the tool system’s standard text result. It is used when a browser action returns structured information rather than an image or file bytes.

**Data flow**: It takes a dictionary reply, converts it to a JSON string, puts that string inside a text content object, and returns a tool result containing that text. It does not change the original browser state or write files.

**Call relations**: Most tool handlers call `_json_result` after receiving a reply from the browser surface. It is the final packaging step for actions like navigation, tab listing, page reading, finding elements, form input, and download completion metadata.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 117–120)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a browser reply contains a required non-empty string field. It prevents later code from silently treating missing data as valid.

**Data flow**: It receives a value and the name of the field that value came from. If the value is a non-empty string, it returns it. If the value is missing, empty, or not a string, it raises a clear error saying which browser reply field was missing.

**Call relations**: `_computer` uses it before decoding a screenshot that should be present when saving to the workspace. `_wait_for_download` uses it before trusting the returned filename and downloaded content.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 123–125)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool, such as opening a URL or moving through browser history. It is the tool handler behind the public `navigate` tool.

**Data flow**: It receives the tool context and a `NavigateInput` containing a URL and optionally a tab id. It turns the input into a JSON-friendly dictionary, sends it to the shared browser surface’s navigation action, then wraps the browser’s reply as JSON text for the caller.

**Call relations**: When the agent calls the `navigate` tool, this handler gets the current browser surface through `_browser`, delegates the real navigation to that surface, and hands the resulting status back through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 128–129)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. This gives the agent situational awareness before choosing which tab to use or close.

**Data flow**: It receives the tool context and an empty input object. It asks the shared browser surface for tab context using an empty request, then returns the surface’s reply as JSON text.

**Call relations**: This is the handler for the `tabs_context` tool. It uses `_browser` to reach the active browser session and `_json_result` to package the tab information for the agent.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 132–134)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab. If the caller does not provide a URL, it opens a blank page.

**Data flow**: It receives the tool context and an optional URL. It builds a request containing the given URL or `about:blank`, sends that request to the browser surface, and returns the browser’s response as JSON text.

**Call relations**: This backs the `tabs_create` tool. It gets the shared browser surface from `_browser`, asks that surface to create the tab, then uses `_json_result` to report what happened.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 137–139)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, optionally a specific one. This lets the agent tidy up or remove pages it no longer needs.

**Data flow**: It receives the tool context and a `TabsCloseInput`, which may include a tab id. It removes unset fields from the input, sends the remaining request to the browser surface, and returns the reply as JSON text.

**Call relations**: This is called when the `tabs_close` tool is invoked. It relies on `_browser` for the current session and `_json_result` for the standard text response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 142–144)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Connects workspace files to a file-upload control in the browser page. It lets the agent choose files by workspace path instead of needing direct local file access.

**Data flow**: It receives a browser reference for the file input, one or more workspace file paths, and optionally a tab id. It converts those arguments into a JSON-friendly request, sends it to the browser surface, and returns the browser’s reply as JSON text.

**Call relations**: This backs the `upload_file` tool. The handler itself does not perform the browser file selection; it prepares the request and hands it to the shared `BuaSurface` obtained through `_browser`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 147–149)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page’s accessibility tree, which is a structured view of page elements meant to describe what is on the page and how users can interact with it. This helps the agent understand buttons, links, fields, and visible regions.

**Data flow**: It receives options such as depth, filter, reference id, and tab id. It drops unset options, sends the request to the browser surface, and returns the structured page description as JSON text.

**Call relations**: This is the handler for `read_page`. It asks `_browser` for the active browser surface, delegates the page-reading work there, and uses `_json_result` to return the structured result.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 152–154)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This is useful when the agent needs page wording more than a structured map of controls.

**Data flow**: It receives the tool context and optionally a tab id. It serializes that input, asks the browser surface to extract page text, and returns the response as JSON text.

**Call relations**: This handler runs for the `get_page_text` tool. Like the other read-style tools, it uses `_browser` for access to the current browser session and `_json_result` to package the answer.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 157–159)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the browser page for elements that match a query, such as text, role, name, or URL. It helps the agent locate the right thing to click, read, or edit.

**Data flow**: It receives a search query and optionally a tab id. It converts the input into a JSON-friendly request, sends it to the browser surface’s find operation, and returns the matched results as JSON text.

**Call relations**: This is the handler behind the `find` tool. It depends on `_browser` to use the current browser surface, which may also use the host-side ranking hook configured in the tool context.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 162–164)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets the value of a form control on the page, such as filling a text box or choosing a value. The caller identifies the control by a browser reference.

**Data flow**: It receives a reference to the page element, the value to put there, and optionally a tab id. It serializes those fields, sends them to the browser surface, and returns the surface’s reply as JSON text.

**Call relations**: This backs the `form_input` tool. It uses `_browser` to reach the active page and `_json_result` to return the outcome after the browser engine performs the edit.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 167–183)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs low-level browser interaction actions such as mouse movement, keyboard input, scrolling, waiting, and screenshots. It can also save a returned screenshot into the workspace.

**Data flow**: It receives a list of computer-style actions, an optional tab id, and optional screenshot-saving settings. It sends the actions to the shared browser surface. If the caller asked to save the screenshot, it checks that screenshot data is present, decodes the base64 text into bytes, writes those bytes to the sandbox workspace, and adds the saved path to the reply. If screenshot data is present, it returns JSON metadata plus image content; otherwise it returns only JSON text.

**Call relations**: This is the handler for the `computer` tool. It uses `_browser` for the actual browser interaction, `_required_str` when it must trust screenshot data, `_json_result` for plain replies, and the sandbox when the screenshot should become a workspace file.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 186–194)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and writes the downloaded file into the shared workspace. This turns an in-browser download into a path that the agent and related workers can use later.

**Data flow**: It receives an optional download id, output path, and timeout. It asks the browser surface to wait for the download, then checks that the returned filename and base64 file content are present. It decodes the file bytes, writes them under the requested directory or the default downloads folder in the sandbox, and returns JSON containing the saved path, filename, and size.

**Call relations**: This backs the `wait_for_download` tool. It depends on `_browser` for the browser-side waiting, `_required_str` to validate required download fields, the sandbox to persist the bytes, and `_json_result` to report the final workspace location.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).


### Web Research
Research tools validate search and fetch requests, call configured web backends, and return normalized results for general, image, and academic queries.

### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `tool invocation during a turn`

This file is the bridge between an agent asking for outside information and the search service that can provide it. Without it, the agent would not have a safe, consistent way to do web research during a turn.

It defines three tools: `search_web` for general web searches, `fetch_url` for reading a specific HTTP or HTTPS page, and `search_vertical` for focused searches like images, people, videos, shopping, or academic papers. Each tool has an input model that describes what arguments are allowed. These models are built with Pydantic, a validation library that checks inputs before the tool runs. For example, web search is limited to five distinct queries, and requested result counts must stay within a set range.

When a tool runs, it first finds the turn’s configured `SearchProvider`, meaning the host-side search backend. This matters because API keys and crawler sessions stay outside the sandbox. The agent gets the answers, but not the provider’s secrets.

Search results are converted into JSON with URLs, titles, snippets, dates, and highlights. Fetch results also include a clear provenance warning: the page was fetched by the provider’s crawler, not from the user’s workspace or account session. The file also records searches and fetched pages as observations when extension context is available, so the system can keep track of what outside information was used.

#### Function details

##### `_provider`  (lines 133–136)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This function gets the search backend for the current tool call. It fails loudly if no backend is configured, because a search or fetch tool cannot honestly do its job without one.

**Data flow**: It receives the current `ToolContext`, which contains turn-level information such as the selected search provider. If `ctx.search_provider` is present, it returns that provider. If it is missing, it raises an error instead of silently pretending search is available.

**Call relations**: The three tool handlers call this first: `_search_web`, `_fetch_url`, and `_search_vertical`. It acts like the front desk that confirms there is actually a research service available before any search or fetch request is sent.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 139–153)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search results into a JSON string that the agent can read in a predictable format. It keeps only the useful public-facing fields: URL, title, text snippet, published date, highlights, and an optional answer.

**Data flow**: It receives a list of `SearchHit` objects and an optional answer string. It copies the important fields from each hit into plain dictionaries, adds them under a `results` key, includes `answer` when available, and returns the whole package as JSON text.

**Call relations**: `_search_web` and `_search_vertical` call this after getting results from the provider. It is the final formatting step before those tools wrap the JSON text in a `ToolResult` for the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 156–174)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler for the general web search tool. It runs one search for each user-supplied query, combines the results, records what was found when possible, and returns the combined results to the agent.

**Data flow**: It receives the tool context and validated `SearchWebInput`, including queries, result count, date filters, and allowed domains. It gets the configured provider, sends each query as a `SearchQuery`, collects all returned hits, keeps the first provider-supplied answer if one exists, optionally records the hits as observations, and returns JSON text inside a `ToolResult`.

**Call relations**: When the agent uses `search_web`, this function drives the whole flow. It calls `_provider` to find the backend, creates search requests for the backend, calls `record_search_hits` if observation tracking is available, then calls `_results_json` to shape the final response.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 177–198)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler for fetching the contents of a specific web page. It checks whether the configured provider can fetch pages, asks it to retrieve the URL, records the page when possible, and returns the page text with a warning about crawler provenance.

**Data flow**: It receives the tool context and validated `FetchUrlInput`, including the URL, optional extraction prompt, length limit, and cache-bypass flag. It gets the provider, returns an error result if that provider does not support fetching, otherwise sends a `FetchRequest`. The fetched page comes back with text and possibly a summary; the function records it if it can, adds the crawler provenance note, converts the reply to JSON, and returns it in a `ToolResult`.

**Call relations**: When the agent uses `fetch_url`, this function is the path from request to returned page content. It depends on `_provider` for the backend and hands fetched pages to `record_fetched_page` so the broader research extension can remember what source material was read.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 201–210)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler for specialized searches, such as image, people, academic, video, or shopping results. It folds the selected content type into the search request so the provider knows what kind of results to look for.

**Data flow**: It receives the tool context and validated `SearchVerticalInput`, containing a vertical name and a query. It gets the provider, sends a `SearchQuery` with the default result count and the chosen vertical, optionally records the returned hits, formats the hits and optional answer as JSON, and returns that text in a `ToolResult`.

**Call relations**: When the agent uses `search_vertical`, this function follows the same pattern as general search but with a content-type focus. It calls `_provider` to reach the backend, `record_search_hits` to preserve observations when available, and `_results_json` to produce the final response.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


### Site Publishing
Hosted site tools manage building, previewing, deploying, publishing, and assigning sandbox-based websites with safety and ownership checks.

### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool execution during build, preview, deploy, publish, QA, and homepage binding`

This file is the control panel for website work. It lets an agent run a build, start a local server, publish that server behind a permanent site link, save static files as the site’s source, create preview images, and bind a site as an agent homepage. Without it, a model could still run shell commands, but the system would lose the safer path: ports might collide, servers might be reported before they are ready, logs could overwrite unsafe paths, links would not be registered, and visibility rules could be bypassed.

The main flow is like opening a pop-up shop. First it clears the stall: it frees the port and removes old task records. Then it starts the server and waits until the door actually opens. For static deployments, it also walks the served directory, records each file’s size and hash, uploads those bytes to the project’s storage, and writes a manifest so the site can later be restored or edited. After the server is live, it registers the port as a hosted site and asks the preview service to take a screenshot for cards and artifacts.

The file also contains special rules for UFO application-builder agents. They must pass a browser-based product QA audit before deploying. Homepage binding has its own checks because it changes who can see the page: a homepage follows the agent’s visibility, not the site’s normal visibility.

#### Function details

##### `StartServerInput.validate_port`  (lines 446–451)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This validates the user’s request before a server is started. It rejects an empty command and rejects ports outside the normal network-port range.

**Data flow**: It reads the parsed input fields on the StartServerInput object. If the command is blank, or the port is not between 1 and 65535, it raises a clear validation error. If everything is acceptable, it returns the same input object unchanged.

**Call relations**: This runs automatically as part of Pydantic input validation before start_server receives its arguments. It prevents bad requests from reaching the sandbox server-starting flow.


##### `_json_result`  (lines 491–492)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This wraps a Python dictionary as the standard JSON response format for these tools. It keeps all tool replies consistent and easy for callers to parse.

**Data flow**: It receives a dictionary, converts it to a JSON string, places that text into a TextContent object, and returns it inside a ToolResult. It does not change outside state.

**Call relations**: The public tools call this at the end of successful work, including website, start_server, deploy_website, publish_website, QA, homepage redeploy, and set_homepage. It is the final packaging step before the result leaves this file.

*Call graph*: called by 7 (_redeploy_homepage, deploy_website, publish_website, qa_ufo_application, set_homepage, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_free_log`  (lines 495–515)

```
async def _free_log(ctx: ToolContext, log_path: str) -> None
```

**Purpose**: This safely clears the chosen log-file name before a new server writes to it. The point is to avoid a security problem where a log path could be a link to some other file.

**Data flow**: It receives the tool context and a log path. It decides whether that path is in the runtime output area or the workspace, then runs a guarded cleanup script inside the sandbox. If cleanup fails, it raises an error; otherwise the path is left ready for the server to create a fresh log.

**Call relations**: _serve calls this before starting any background server. It prepares the log path so the later shell redirect can create the file safely instead of following a planted link.

*Call graph*: called by 1 (_serve); 1 external calls (PurePosixPath).


##### `_stop_server`  (lines 518–523)

```
async def _stop_server(ctx: ToolContext, port: int) -> None
```

**Purpose**: This frees a port before a new server is started. It prevents old or stray processes from keeping the requested port busy.

**Data flow**: It receives a port number and runs a small Python program inside the sandbox to find processes listening on that port. The script asks them to stop, then force-kills any that do not exit quickly. If the cleanup command fails, this function raises an error.

**Call relations**: _serve calls this after preparing the log and before launching the new server. It is part of the “clear the stall before opening the shop” sequence.

*Call graph*: called by 1 (_serve).


##### `_stop_server_task`  (lines 526–542)

```
async def _stop_server_task(ctx: ToolContext, command: str, base: str, pid: str) -> None
```

**Purpose**: This stops a background server task that was just launched but failed to become ready. It makes sure a half-started server does not keep running after the tool reports failure.

**Data flow**: It receives the context, the server command, the task journal base path, and the process id. It checks that the recorded task matches the expected process, sends a stop signal, then waits for the sandbox task wrapper to finish. If stopping or waiting fails, it raises an error.

**Call relations**: _serve calls this when the readiness check fails or is interrupted. It is the cleanup branch for a server that started but did not become usable.

*Call graph*: called by 1 (_serve).


##### `_reset_server_task`  (lines 545–563)

```
async def _reset_server_task(ctx: ToolContext, base: str) -> None
```

**Purpose**: This clears the saved task record for a server slot before starting a new one. It prevents the sandbox task runner from reattaching to an old task instead of launching a fresh server.

**Data flow**: It receives the context and a task journal base path. It runs a shell reset script in the sandbox, which stops an old recorded process if needed and removes the task’s pid, log, exit, and lock files. If the reset cannot complete, it raises an error.

**Call relations**: _serve calls this after freeing the port and before launching the detached task. It keeps repeated deploys or retries from accidentally reusing stale task state.

*Call graph*: called by 1 (_serve).


##### `_serve`  (lines 566–633)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log_path: str) -> dict[str, object]
```

**Purpose**: This is the shared server-starting engine. It starts a command in the background, waits until the port accepts connections, and returns only when the server is actually reachable.

**Data flow**: It receives the context, a shell command, a project directory, a port, and a log path. It clears the log, stops anything already using the port, resets the task journal, starts the command with PORT set, and probes localhost until the port responds. On success it returns the sandbox-local URL, port, and log path; on failure it stops the new task and reports useful log output when available.

**Call relations**: start_server, deploy_website, publish_website, and _redeploy_homepage all rely on this function. It hands back the live server details that later steps either return directly or register as a hosted site.

*Call graph*: calls 4 internal fn (_free_log, _reset_server_task, _stop_server, _stop_server_task); called by 4 (_redeploy_homepage, deploy_website, publish_website, start_server); 3 external calls (sha256, quote, shell_path).


##### `_site_media_type`  (lines 636–638)

```
def _site_media_type(path: str) -> str
```

**Purpose**: This guesses the correct content type for a file based on its extension. That helps browsers understand whether a stored file is HTML, JavaScript, CSS, an image, and so on.

**Data flow**: It receives a file path, looks at the text after the last dot, and checks it against a known map of web file types. It returns a media type string, adding UTF-8 charset information for text files, or a generic binary type if the extension is unknown.

**Call relations**: _promote_source uses this while building the source manifest for a static site. The returned type becomes part of the stored record used later when serving files back.

*Call graph*: called by 1 (_promote_source).


##### `_source_listing`  (lines 641–662)

```
async def _source_listing(ctx: ToolContext, project: str) -> dict[str, dict[str, object]]
```

**Purpose**: This lists the files that make up a static site and records their sizes and SHA-256 hashes. A SHA-256 hash is a fingerprint used to confirm file contents have not changed.

**Data flow**: It receives the context and a project directory. Inside the sandbox, it walks that directory, skips things like .git and node_modules, refuses oversized sites, hashes each regular file, and returns a dictionary of paths to size-and-hash information. If the directory is empty, too large, or unsafe, it raises an error.

**Call relations**: _served_directory calls this to understand what will be hosted. Its result feeds _promote_source, which uploads and records those exact files.

*Call graph*: called by 1 (_served_directory); 1 external calls (loads).


##### `_promote_source`  (lines 665–706)

```
async def _promote_source(ctx: ToolContext, project: str, conversation_id: UUID, name: str, listing: dict[str, dict[str, object]]) -> str
```

**Purpose**: This saves a static site’s files into the workspace blob store and creates the manifest that describes them. This makes the deployed site’s source durable instead of only living in the sandbox.

**Data flow**: It receives the context, the served project path, the conversation id, the site name, and the file listing. It creates a unique storage prefix, builds a SourceManifest with each file’s size, hash, and media type, then uploads the files either through presigned upload links or direct streams. It returns the manifest as JSON.

**Call relations**: deploy_website and _redeploy_homepage call this before registering or updating a static site. It calls _site_media_type for each file and may hand the actual upload work to transfer.

*Call graph*: calls 1 internal fn (_site_media_type); called by 2 (_redeploy_homepage, deploy_website); 4 external calls (__init__, __init__, transfer, uuid4).


##### `_illustrate`  (lines 709–731)

```
async def _illustrate(ctx: ToolContext, name: str, port: int, conversation_id: UUID) -> None
```

**Purpose**: This creates visual previews for a newly hosted or updated site. These images are what people see as cards or previews in the product interface.

**Data flow**: It receives the context, site name, port, and the conversation id where the site row lives. It asks the core preview service to render the hosted page, stores the preview if one is returned, then asks the share-card code to draw a card image. It changes preview-related site records but does not change the served site itself.

**Call relations**: deploy_website, publish_website, and _redeploy_homepage call this after the site has already been registered or updated. It depends on HostedSites storage and the draw_from_page helper.

*Call graph*: calls 1 internal fn (render_site_preview); called by 3 (_redeploy_homepage, deploy_website, publish_website); 2 external calls (__init__, draw_from_page).


##### `_refuse_before_serving`  (lines 734–774)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> tuple[str, HostedSite | None]
```

**Purpose**: This checks whether a site is allowed to be hosted before the tool kills anything on the target port. It protects an existing live site from being taken down by a deploy that would later be refused.

**Data flow**: It receives the context, requested site name, port, and optional visibility. It confirms there is an acting member, checks that visibility changes have a live speaker, turns the name into a safe site slug, validates the URL shape, and asks the site registry whether this deploy would be allowed. It returns the slugged name and any site that would be displaced.

**Call relations**: deploy_website and publish_website call this before building or serving. _host later repeats the important checks when it actually writes the registration, because another deploy may have changed the world in between.

*Call graph*: called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 777–821)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None, manifest: str | None) -> dict[str, object]
```

**Purpose**: This registers a live sandbox port as a hosted site with a stable public link. It is the step that turns “a server is running” into “there is a deliverable URL.”

**Data flow**: It receives the context, raw site name, port, optional visibility, and optional source manifest. It checks owner and speaker rules, normalizes the name, builds the public site URL, and writes the site registration to HostedSites. It returns the site name, effective visibility, object name, and public URL.

**Call relations**: deploy_website and publish_website call this after _serve has proved the port is reachable. _illustrate usually follows it so the registered page can be photographed.

*Call graph*: called by 2 (deploy_website, publish_website); 5 external calls (__init__, effective_visibility, site_object_name, site_name, site_url).


##### `website`  (lines 824–833)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This tool runs a build command inside a project directory and reports the files now present there. It is useful for compiling or preparing a website before serving or deploying it.

**Data flow**: It receives a tool context and WebsiteInput. It scopes the requested project path to the workspace, runs the build command in the sandbox with a timeout, then lists the project directory. On success it returns JSON with the project path and file names; on build failure it raises the command output as an error.

**Call relations**: This is one of the public tool handlers in SITES_TOOLS. It does not register a site; it only prepares the project and returns through _json_result.

*Call graph*: calls 1 internal fn (_json_result); 2 external calls (quote, workspace_path).


##### `start_server`  (lines 836–856)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This tool starts a project server in the sandbox for testing. It returns a local sandbox URL only; it does not create a permanent hosted site.

**Data flow**: It receives StartServerInput, chooses a default port if needed, scopes the project and log paths, and chooses either the user’s server command or a simple static-file server. It calls _serve to launch and probe the server, adjusts the URL for application-builder preview pages when needed, and returns the server details as JSON.

**Call relations**: This is a public tool handler. It delegates the hard work to _serve, then packages the result with _json_result.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (workspace_path).


##### `_application_audit_attempts`  (lines 859–867)

```
async def _application_audit_attempts(ctx: ToolContext) -> int
```

**Purpose**: This reads how many failed application-audit attempts have already happened in the current turn. It enforces the limit on repeated product QA failures.

**Data flow**: It receives the context, checks that extension storage is available, and reads the attempt count keyed by the turn id. If nothing is stored, it returns zero; if the stored value is not an integer, it raises an error.

**Call relations**: _audit_builder_application calls this at the start of an audit. The count is later updated by _application_audit_feedback when the audit returns repair feedback.

*Call graph*: called by 1 (_audit_builder_application); 1 external calls (format).


##### `_application_audit_feedback`  (lines 870–882)

```
async def _application_audit_feedback(ctx: ToolContext, issues: tuple[ApplicationAuditIssue, ...], attempts: int) -> ApplicationAuditFeedback
```

**Purpose**: This records a failed application-audit attempt and builds the feedback object that tells the builder what to fix. It keeps repair loops bounded.

**Data flow**: It receives the context, a tuple of audit issues, and the previous attempt count. It increments and stores the count, then returns an ApplicationAuditFeedback object with the new attempt number, remaining attempts, and issues.

**Call relations**: _audit_builder_application calls this whenever the browser audit cannot run, cannot produce a valid report, or finds product problems. qa_ufo_application then returns this feedback to the caller.

*Call graph*: called by 1 (_audit_builder_application); 2 external calls (__init__, format).


##### `_audit_builder_application`  (lines 885–1027)

```
async def _audit_builder_application(ctx: ToolContext, project: str) -> ApplicationAuditReport | ApplicationAuditFeedback
```

**Purpose**: This runs the full browser-based product audit for a UFO application builder project. It checks that the app matches an accepted design and behaves correctly before deployment is allowed.

**Data flow**: It receives the context and project path. It checks the attempt limit, reads the accepted design and its evidence, verifies their hash, writes the audit script into runtime storage, runs it with Node, reads the report, validates the report structure and design regions, loads the audit contract, and evaluates the result. It returns either a full audit report on success or bounded feedback on failure.

**Call relations**: qa_ufo_application calls this as its main work. It uses _application_audit_attempts and _application_audit_feedback to enforce retry limits and calls into the application_audit module to validate the final report.

*Call graph*: calls 2 internal fn (_application_audit_attempts, _application_audit_feedback); called by 1 (qa_ufo_application); 9 external calls (__init__, model_validate_json, model_validate, model_validate_json, sha256, format, audit_application, application_design_acceptance_relative, application_design_evidence_relative).


##### `_application_source_sha256`  (lines 1030–1036)

```
async def _application_source_sha256(ctx: ToolContext) -> str
```

**Purpose**: This computes a fingerprint of the application source file. It is used to prove that the app deployed is the same app that passed QA.

**Data flow**: It reads the application source file from the sandbox through a guarded script. If reading fails, it raises an error. Otherwise it returns the SHA-256 hash of the file contents.

**Call relations**: qa_ufo_application stores this hash after QA passes. _require_current_application_qa later compares the current hash to the stored one before deploy_website can deploy an application-builder project.

*Call graph*: called by 2 (_require_current_application_qa, qa_ufo_application); 1 external calls (sha256).


##### `_require_current_application_qa`  (lines 1039–1051)

```
async def _require_current_application_qa(ctx: ToolContext) -> ApplicationQaProof
```

**Purpose**: This blocks deployment unless the current application source has already passed product QA. It prevents a builder from changing app.tsx after approval and deploying the untested version.

**Data flow**: It reads the QA proof from extension storage for the current turn. It validates that proof, recomputes the current source hash, and compares the two. If the proof is missing, invalid, or stale, it raises an error; otherwise it returns the proof.

**Call relations**: deploy_website calls this only for the application-builder profile. It relies on _application_source_sha256 and the proof written by qa_ufo_application.

*Call graph*: calls 1 internal fn (_application_source_sha256); called by 1 (deploy_website); 2 external calls (model_validate, format).


##### `qa_ufo_application`  (lines 1054–1096)

```
async def qa_ufo_application(ctx: ToolContext, args: QaUfoApplicationInput) -> ToolResult
```

**Purpose**: This tool runs product QA for the special UFO application builder. If the app passes, it records proof that deployment is allowed.

**Data flow**: It checks that the current turn belongs to the application-builder profile and that extension storage exists. It reads and increments the QA call count, refuses too many calls, runs _audit_builder_application, and either returns repair feedback or a summary of checked views and interactions. On success it stores an ApplicationQaProof containing the source hash and audit batch count, then returns JSON.

**Call relations**: This is a profile-only public tool. It is the gatekeeper before deploy_ufo_application or application-builder deploy_website flows can publish the app.

*Call graph*: calls 3 internal fn (_application_source_sha256, _audit_builder_application, _json_result); 4 external calls (__init__, __init__, format, format).


##### `deploy_ufo_application`  (lines 1099–1109)

```
async def deploy_ufo_application(ctx: ToolContext, args: DeployUfoApplicationInput) -> ToolResult
```

**Purpose**: This is a convenience deploy tool for the UFO application builder. It deploys the fixed application scaffold as a normal website after the required QA checks are satisfied elsewhere.

**Data flow**: It checks that the current turn is using the application-builder profile. It creates a DeployWebsiteInput pointing at the fixed scaffold path, using index.html as the entry point, and passes that request to deploy_website. The result is whatever deploy_website returns.

**Call relations**: This public profile-only tool is a thin wrapper around deploy_website. It exists so the application-builder profile has a focused deploy command with fewer choices.

*Call graph*: calls 1 internal fn (deploy_website); 1 external calls (__init__).


##### `deploy_website`  (lines 1112–1143)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This tool deploys a static website directory to a permanent hosted link. It can also rebuild special app-source directories and update an agent homepage in place when appropriate.

**Data flow**: It scopes the source project path, enforces application-builder restrictions and QA proof when needed, chooses the conversation’s serve port, checks whether this deploy should update an existing bound homepage, then performs the normal deploy path. The normal path checks permissions before serving, prepares the served directory, uploads the source manifest, starts a static server with _serve, registers it with _host, creates previews with _illustrate, and returns the combined JSON result.

**Call relations**: deploy_ufo_application calls this, and it is also a public tool handler itself. It coordinates _agent_homepage, _redeploy_homepage, _refuse_before_serving, _served_directory, _promote_source, _serve, _host, _illustrate, and _json_result.

*Call graph*: calls 11 internal fn (_agent_homepage, _host, _illustrate, _json_result, _promote_source, _redeploy_homepage, _refuse_before_serving, _require_current_application_qa, _serve, _served_directory (+1 more)); called by 1 (deploy_ufo_application); 4 external calls (serve_port, workspace_path, site_object_name, site_name).


##### `_served_directory`  (lines 1146–1172)

```
async def _served_directory(ctx: ToolContext, project: str) -> tuple[str, dict[str, dict[str, object]]]
```

**Purpose**: This decides what directory should actually be hosted and returns its file listing. If the input is app source rather than built web files, it builds the app first and hosts the generated dist folder.

**Data flow**: It receives the context and a workspace-scoped project path. It lists the project files; if the special source file is absent, it returns the project as-is with its listing. If the source file is present, it writes project config, unpacks the page kit, runs a Vite build, and returns the dist directory with a fresh listing.

**Call relations**: deploy_website and _redeploy_homepage call this before uploading source and starting the static server. It uses _source_listing and may call unpack_page_kit for app-style projects.

*Call graph*: calls 1 internal fn (_source_listing); called by 2 (_redeploy_homepage, deploy_website); 2 external calls (quote, unpack_page_kit).


##### `_agent_homepage`  (lines 1175–1178)

```
async def _agent_homepage(ctx: ToolContext) -> HostedSite | None
```

**Purpose**: This looks up the hosted site currently bound as the acting agent’s homepage, if any. It helps deploy_website decide whether a deploy request should update that homepage instead of creating a separate site.

**Data flow**: It receives the context, obtains the site registry, and asks for the homepage bound to the current turn’s agent id. It returns a HostedSite or None.

**Call relations**: deploy_website calls this early in its decision process. It gets the registry through _sites_registry.

*Call graph*: calls 1 internal fn (_sites_registry); called by 1 (deploy_website).


##### `_sites_registry`  (lines 1181–1184)

```
def _sites_registry(ctx: ToolContext) -> HostedSites
```

**Purpose**: This creates the HostedSites registry object for the current workspace transaction. It is a small helper that centralizes the extension-context check.

**Data flow**: It receives the context, verifies that extension context is available, and returns a HostedSites object using the workspace id and transaction. It does not itself read or write site rows.

**Call relations**: _agent_homepage, deploy_website, and _redeploy_homepage call this when they need to interact with hosted-site records.

*Call graph*: called by 3 (_agent_homepage, _redeploy_homepage, deploy_website); 1 external calls (__init__).


##### `_redeploy_homepage`  (lines 1187–1256)

```
async def _redeploy_homepage(ctx: ToolContext, args: DeployWebsiteInput, bound: HostedSite, scratch_port: int) -> ToolResult
```

**Purpose**: This updates an already-bound agent homepage in place, keeping the same public link and site row. It is used when the agent’s homepage belongs to another conversation but should be refreshed from the current build.

**Data flow**: It checks that a live speaker, or an approved application-builder redeploy request, authorized the homepage change. It refuses visibility changes because homepage access follows the agent, checks whether any current-conversation site would be displaced, builds or selects the served directory, uploads a new manifest under the bound site’s identity, starts the new server on a scratch port, updates the existing site row, unregisters a displaced site if needed, refreshes previews, and returns JSON with the unchanged public site link.

**Call relations**: deploy_website calls this when its requested site name matches the acting agent’s bound homepage in another conversation. It coordinates _served_directory, _promote_source, _serve, _sites_registry, _illustrate, and _json_result.

*Call graph*: calls 7 internal fn (agent_visibility, _illustrate, _json_result, _promote_source, _serve, _served_directory, _sites_registry); called by 1 (deploy_website); 4 external calls (workspace_path, format, site_object_name, site_url).


##### `publish_website`  (lines 1259–1276)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This tool publishes a web app whose own server may keep running in the sandbox. Unlike static deploy, it does not save the app’s source files as the site’s source of record.

**Data flow**: It chooses the conversation’s serve port, checks hosting permissions before serving, optionally runs an install command, chooses either the app’s run command or a static-file server for the dist directory, starts the server with _serve, registers the live port with _host using no manifest, creates previews with _illustrate, and returns JSON.

**Call relations**: This is a public tool handler. It shares the same hosting and preview steps as deploy_website, but skips _promote_source because the app is served by its running sandbox process.

*Call graph*: calls 5 internal fn (_host, _illustrate, _json_result, _refuse_before_serving, _serve); 3 external calls (quote, serve_port, workspace_path).


##### `set_homepage`  (lines 1279–1336)

```
async def set_homepage(ctx: ToolContext, args: SetHomepageInput) -> ToolResult
```

**Purpose**: This binds an existing hosted site as the homepage for the target agent. A homepage is the page the portal shows for that agent, and its viewers follow the agent’s visibility rules.

**Data flow**: It verifies extension context and target agent information, loads the named agent, checks that the acting person is allowed to alter that agent’s homepage, finds the named hosted site, checks that the acting member created the site, and requires a live speaker unless the site was deployed in this same turn for this same agent. It then writes the homepage pointer and returns the site, URL, effective agent visibility, and homepage agent id as JSON.

**Call relations**: This is a public side-effecting tool bound to an agent object. It uses HostedSites storage directly, checks admin status through the tool context, builds object names and URLs with site helpers, and finishes through _json_result.

*Call graph*: calls 2 internal fn (speaker_is_admin, _json_result); 3 external calls (__init__, site_object_name, site_url).

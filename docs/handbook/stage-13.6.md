# External browser providers and hosted automation adapters  `stage-13.6`

This stage is behind-the-scenes support for web browsing tasks. Instead of forcing the main system to run and control one local browser, it offers several outside “browser providers,” meaning places where a browser or browser-like agent can run safely and be connected to the system.

The Browser Use adapter connects UFO to a hosted cloud browser agent. It exposes two tools: one to complete a single browsing job, and another to run similar browsing work across many sites. In that mode, the cloud service handles the browser-control loop.

The Browserbase adapter starts a remote Chrome session hosted by Browserbase. It connects UFO to that browser, transfers files in and out when needed, and closes the session afterward so it does not keep running or costing money.

The sandbox Chrome adapter starts Chrome inside the same sandbox environment as the conversation. Together, these adapters act like interchangeable power outlets: the rest of UFO asks for browser access, and the chosen provider supplies it.

## Files in this stage

### External browser adapters
Adapters that connect UFO to externally hosted or sandboxed browser automation environments.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `tool invocation / request handling`

This extension is a bridge between UFO and Browser Use’s hosted web automation service. Instead of opening and steering a browser locally, it sends a plain-language task to Browser Use’s API, waits for the hosted agent to finish, and returns the result. Think of it like hiring an outside courier: UFO writes the delivery instructions, the outside service does the trip, and UFO collects the receipt and any allowed packages afterward.

The file defines two tools with familiar names, `browser_task` and `wide_browse`, so other parts of the system can swap to this hosted browser provider without changing the tool names they ask for. `browser_task` starts one fresh cloud browser session from a URL and task description. `wide_browse` reads a list of URLs or site names from a workspace file, launches several independent hosted runs in parallel, and writes the collected rows to `wide_browse.json`.

A key safety point is credential handling. The Browser Use API key is read on the host side through scoped credential access and is only sent to Browser Use’s API. Output files are downloaded through separate presigned HTTPS links without carrying that API key. The file also enforces limits: task text length, run timeouts, maximum cost, maximum batch size, and maximum output file size/count. It carefully checks downloaded file paths so a vendor-provided path cannot escape the workspace.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one complete hosted Browser Use session from start to finish. It starts or reattaches to a run, waits for completion or timeout, gathers the final result, and optionally brings output files into the workspace.

**Data flow**: It receives the tool context, the task text, a timeout, and optionally a deduplication key used to avoid paying for the same run twice after a retry. It reads the Browser Use API key from scoped credentials, opens an HTTP client, starts the run, polls its status, cancels it if the local deadline expires, then reads the final summary and downloads allowed output files. It returns a `RunOutcome` containing the final status, output text or error text, saved files, skipped files, and whether more files existed.

**Call relations**: The tool handlers call this when they need a hosted browser session. Inside the flow it hands off to `_start` to create or recover the run, `_watch` and `_status` to follow progress, `_json` to validate API replies, and `_collect` to copy finished output files back into the sandbox.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Creates a new Browser Use run, or reuses a previously recorded one when the caller supplies a deduplication key. This prevents retries from accidentally launching duplicate paid browser sessions.

**Data flow**: It receives an HTTP client, a small extension store, the task text, and an optional deduplication key. If the key already points to a stored run, it validates and returns that saved run handle. Otherwise it posts the task, model, cost limit, and proxy country to Browser Use, extracts the run id and workspace id, stores them when appropriate, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this near the beginning of every hosted session. It relies on `_json` to make sure the API response is readable and `_text` to pull required string fields from that response.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits until a Browser Use run reaches a final state such as completed, failed, stopped, or cancelled. It is the simple polling loop for a run in progress.

**Data flow**: It receives an HTTP client and a run id. It repeatedly asks `_status` for the current state; if the state is terminal, it returns it, otherwise it sleeps briefly and tries again.

**Call relations**: `HostedRun.execute` uses this while the run is inside its allowed time window. `_watch` delegates each actual status read to `_status`, keeping the repeated waiting separate from the one-shot API read.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is also used just after a timeout so the code does not cancel a run that finished in the last few seconds.

**Data flow**: It receives an HTTP client and run id, sends a status request to Browser Use, checks that the response is valid JSON with `_json`, extracts the `status` text with `_text`, and returns that status string.

**Call relations**: `_watch` calls this over and over while waiting. `HostedRun.execute` also calls it directly in the timeout path before deciding whether to cancel the remote run.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies the finished run’s output files into the conversation workspace, but only when this `HostedRun` is configured to save outputs. It records files that were too large or missing a usable download link instead of silently ignoring them.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace id. It asks Browser Use for a limited file listing, checks each listed item has a path and size, rejects paths that would escape the workspace, downloads acceptable HTTPS files, and writes them into the sandbox. It returns three pieces of information: saved files, skipped files, and whether the vendor said more files existed beyond the listing limit.

**Call relations**: `HostedRun.execute` calls this after the run has reached a terminal state and the summary has been read. `_collect` uses `_download` for each file body, `_json` for the listing response, and the shared sandbox containment check to keep writes inside the workspace.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a presigned URL supplied by Browser Use. It deliberately uses a client without the Browser Use API key so the storage host never sees the credential.

**Data flow**: It receives a URL. It first requires the URL to start with HTTPS, then fetches the bytes using a plain HTTP client. If the download fails or uses an unsafe scheme, it raises a `BrowserUseError`; otherwise it returns the file bytes.

**Call relations**: `_collect` calls this for each listed output file that is small enough and has a usable URL. This keeps the file-download rules in one place, separate from the file-listing and workspace-writing logic.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a safe dictionary, or raises a clear Browser Use-specific error. This avoids later code trying to work with missing, malformed, or non-JSON replies.

**Data flow**: It receives an HTTP response. If the status code reports failure, it includes the request path, status, and body in an error. If the body is not JSON or is not a JSON object, it raises an error. Otherwise it returns a dictionary with string keys.

**Call relations**: Most API-reading functions use this before trusting a Browser Use response. It is called by the run start, status, collection, and final summary paths so all of them share the same response checking behavior.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts a required text field from an already-read API response. It converts missing or non-text fields into the same clear error style used by the rest of this Browser Use flow.

**Data flow**: It receives a dictionary and the name of a field. It looks up the value, confirms it is a string, and returns it. If the field is missing or not text, it raises a `BrowserUseError` showing the whole response.

**Call relations**: `_start` uses this to read the new run id and workspace id. `_status` uses it to read the run status. That keeps small response-shape checks consistent across the file.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 350–384)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the public `browser_task` tool. It sends one self-contained browser instruction to Browser Use and returns the result plus any output files that were safely copied into the workspace.

**Data flow**: It receives a tool context and validated browser-task arguments: starting URL, task text, task name, timeout, and user-facing description. It builds a single task prompt, creates a `HostedRun` configured for the stronger task model and file saving, and executes it. If the run times out, it returns an error tool result; otherwise it returns JSON containing the result text, saved file paths, skipped file details, and whether extra files existed.

**Call relations**: The tool registry invokes this when an agent calls `browser_task`. It relies on `HostedRun.execute` for the actual external browser session, then packages the outcome into `TextContent` and `ToolResult` for the rest of UFO.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 387–394)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a workspace file through the sandbox shell, safely quoting the supplied path first. It is used when tool inputs point to files created inside the workspace.

**Data flow**: It receives a tool context and a path string. It shell-quotes the path so special characters cannot turn into extra shell commands, runs `cat` in the sandbox, and returns standard output. If the command fails, it raises a readable `ValueError`.

**Call relations**: `_read_lines` uses this to read the entity list for batch browsing. `_wide_browse` also uses it directly to read the JSON schema file that should guide each browser run’s output.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 397–405)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a file as a cleaned list of unique, non-empty lines. For `wide_browse`, these lines are the URLs or site names to visit.

**Data flow**: It receives a tool context and a file path. It reads the file with `_read_file`, splits it into lines, trims whitespace, skips blanks, removes duplicates while keeping the first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this at the start of a batch job to decide which entities should get their own hosted browser run.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 408–454)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the public `wide_browse` tool, which runs the same browser-extraction prompt across many URLs or site names. It is designed for batch collection, not for one interactive browser journey.

**Data flow**: It receives a tool context and validated batch arguments: an entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates the entities, rejects batches that are too large, reads the schema text, creates a lower-cost `HostedRun`, and launches visits in parallel with a fanout limit so only a fixed number run at once. It gathers every row, turns ordinary per-entity failures into error rows, writes all rows to `wide_browse.json`, and returns JSON with the rows and output file name.

**Call relations**: The tool registry invokes this when an agent calls `wide_browse`. It uses `_read_lines` and `_read_file` for workspace inputs, creates a shared `HostedRun` for the batch, and relies on its nested `visit` function to run each entity through Browser Use.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 424–439)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs Browser Use for one entity inside a `wide_browse` batch. It turns one URL or site name plus the prompt template into a single hosted browser task.

**Data flow**: It receives one entity from the outer batch. It waits for the semaphore, which is a small gate that limits how many visits run at the same time, then fills `{entity}` in the prompt template and appends the output schema when present. It executes the hosted run with a per-entity deduplication key when available, then returns a row with the entity, final status, and result text.

**Call relations**: `_wide_browse` creates this helper and schedules one copy per entity using `asyncio.gather`. Any normal exception from a visit is later converted by `_wide_browse` into an error row so one failed site does not throw away the rest of the batch.


##### `manifest`  (lines 477–492)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO runtime. It declares the extension name and version, the two tools it offers, its prompt text, and the Browser Use API key credential it needs.

**Data flow**: It takes no inputs. It builds a `Manifest` containing the predefined tool definitions, a browser prompt section loaded from the companion markdown file, and a credential slot explaining the required Browser Use API key. It returns that manifest to the extension loader.

**Call relations**: The runtime calls this during extension loading. The returned manifest is how UFO discovers `browser_task` and `wide_browse`, learns what prompt section to include, and knows which credential must be available before the tools can run.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session setup, active browser use, recovery reattach, and teardown`

This extension is the bridge between UFO's browser system and Browserbase, a service that hosts Chrome in the cloud. UFO normally needs a Chrome DevTools Protocol endpoint, which is a control socket used to drive Chrome. Browserbase provides that endpoint, but only after the code creates a remote session through Browserbase's web API.

The file has three main parts. BrowserbaseApi is the low-level client for Browserbase's REST API. It sends authenticated HTTP requests to create sessions, check whether a session is still alive, upload files, fetch downloads, and clean up saved browser contexts. BrowserbaseCdpProvider is the adapter UFO calls when it needs a browser lease. It uses the current sandbox conversation as the identity of one browser run, creates or reuses a Browserbase Context for that run, and starts a session with it. A Context is like a temporary browser profile: it keeps cookies and local storage during the run, but is deleted when the run closes.

BrowserbaseLease represents one active remote browser. It tells UFO where to connect, gives a recovery token for reattaching, uploads local files to Browserbase before a page selects them, retrieves downloaded files from Browserbase storage, and releases everything at the end. The file is careful about size limits, missing API fields, and dead sessions, because a remote browser can disappear or return unexpected data.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new hosted Browserbase browser session using an existing Browserbase Context. UFO uses this when it is ready to start driving a remote Chrome for one browser run.

**Data flow**: It receives a context ID. It sends Browserbase a request saying to attach that context, keep it persistent, and use this file's timeout setting. It reads the response and returns the new session ID together with the connection URL UFO will use to control Chrome.

**Call relations**: This is part of the startup path for a new browser lease. BrowserbaseCdpProvider.lease prepares the context first, then relies on this method to mint the actual remote browser session. Internally it uses the shared JSON request helper and the field-checking helper so missing response data becomes a clear BrowserbaseError.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still usable and returns its connection URL if it is. This is used during recovery, when UFO has a token from an earlier session and wants to reconnect instead of starting over.

**Data flow**: It receives a session ID, asks Browserbase for that session's current details, and checks the status. If the status says the session is running or pending, it returns the connection URL. If Browserbase says the session is no longer live, it raises SessionGone so the caller knows it must create a fresh session.

**Call relations**: BrowserbaseCdpProvider.reattach uses this after decoding a saved token. The method delegates HTTP and JSON parsing to BrowserbaseApi._json and validates required fields through _field.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to release a remote browser session. This is the normal cleanup step that stops the hosted browser from continuing after UFO is done with it.

**Data flow**: It receives a session ID and sends Browserbase a status update requesting release. It does not return useful data; success means Browserbase accepted the release request, and failure is reported through the shared API error path.

**Call relations**: BrowserbaseLease.aclose calls this first during teardown. It uses BrowserbaseApi._json for the HTTP request so API failures are surfaced consistently.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a Browserbase Context, which acts like a temporary browser profile for cookies, logins, and local storage. UFO uses it so a browser run can keep its state if the remote session has to be remade during the same conversation.

**Data flow**: It sends an empty create-context request to Browserbase, reads the returned JSON, and extracts the new context ID. That ID is the output and is later stored for the current conversation.

**Call relations**: BrowserbaseCdpProvider._context calls this only when there is not already a saved context ID for the conversation. The method uses BrowserbaseApi._json to talk to Browserbase and _field to require a valid ID in the response.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context after the browser run is over. This prevents cookies, logins, and storage from living longer than the subagent browser session they belonged to.

**Data flow**: It receives a context ID and sends a DELETE request to Browserbase. It returns nothing; the important effect is the remote context being removed.

**Call relations**: BrowserbaseLease.aclose calls this after asking Browserbase to release the session. It goes through BrowserbaseApi._send directly because it does not need to parse a JSON response.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a file that the remote browser downloaded. Because the browser runs in Browserbase's cloud, the local process cannot read a normal local download folder and must ask Browserbase for the stored file.

**Data flow**: It receives a session ID and a download GUID, which is the name UFO expects Browserbase to store. It repeatedly asks Browserbase for the session's download list, looks for the matching file, checks that Browserbase reports a safe size, and then downloads the file bytes. It returns those bytes, or raises an error if the file never appears or is too large.

**Call relations**: BrowserbaseLease.fetch_download hands download requests to this method. The method uses BrowserbaseApi._json to poll the download list, _size to enforce a measured size, _field to extract the stored download ID, BrowserbaseApi._send to fetch the raw bytes, and a short sleep between retries because Browserbase storage can lag behind Chrome finishing the download.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a local workspace file into the remote Browserbase session so the hosted Chrome can use it. This is needed because a cloud browser cannot access files by local filesystem path.

**Data flow**: It receives a session ID, the filename Browserbase should see, and the file bytes. It sends those bytes to the session uploads endpoint. It returns nothing; after success, Browserbase makes the file available inside the remote browser's upload directory.

**Call relations**: BrowserbaseLease.place_file prepares names and size checks, then calls this method to actually move the bytes. The method uses BrowserbaseApi._send because the upload is a multipart HTTP request rather than a simple JSON-only operation.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request and insists that the response body is a JSON object, meaning a dictionary-like structure. It gives the rest of the file one reliable way to make JSON API calls.

**Data flow**: It receives an HTTP method, an API path, and optional request data. It passes those to BrowserbaseApi._send, parses the response as JSON, checks that the result is an object, and returns that object. If Browserbase replies with something else, it raises BrowserbaseError.

**Call relations**: The session, context, status, and download-list methods call this whenever they expect Browserbase to answer with JSON. It sits above BrowserbaseApi._send: _send handles HTTP transport and errors, while _json adds response-shape validation.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the current API key. It is the central place that adds authentication, timeout settings, optional test transport, and clear errors for failed HTTP responses.

**Data flow**: It receives an HTTP method, API path, timeout, and request options. It reads the Browserbase API key from the credential slot, builds headers, opens an async HTTP client, sends the request, and checks the status code. It returns the raw HTTP response on success or raises BrowserbaseError on failure.

**Call relations**: All Browserbase network traffic passes through this method, either directly or through BrowserbaseApi._json. Higher-level methods such as upload, download, delete_context, and session creation rely on it so authentication and error handling stay consistent.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Extracts a required non-empty string field from a Browserbase response. It prevents the rest of the code from continuing with missing IDs or connection URLs.

**Data flow**: It receives a response object and a field name. It looks up that field and checks that it is a non-empty string. It returns the string if valid, or raises BrowserbaseError if the field is absent or unusable.

**Call relations**: BrowserbaseApi methods use this after successful JSON calls whenever Browserbase must provide an ID, status, or connection URL. It keeps response validation small and shared instead of repeated in every caller.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the size Browserbase reports for a listed download. This protects the UFO process from loading an unexpectedly large or unmeasured file into memory.

**Data flow**: It receives one download-list entry. It reads the size field, rejects missing, boolean, or non-number values, and returns the size as an integer. If Browserbase does not report a trustworthy size, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.download calls this before fetching download bytes. That placement matters: the file is refused before the process pulls the content into memory.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome control endpoint for this lease. UFO uses this endpoint to connect its browser-driving code to the hosted Browserbase Chrome session.

**Data flow**: It reads the lease's stored connection URL and wraps it in a CdpEndpoint object. The output is that endpoint object; it does not change remote or local state.

**Call relations**: After BrowserbaseCdpProvider.lease or BrowserbaseCdpProvider.reattach returns a lease, the browser engine calls this to learn where to connect. It is the small handoff from Browserbase session creation to UFO's normal browser-control path.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a recovery token that contains enough information to reconnect to this browser session later. The token names the conversation, session, and context so recovery does not accidentally attach to another run's browser state.

**Data flow**: It reads the lease's conversation ID, session ID, and context ID. It combines them into one slash-separated string and returns that string. Nothing remote changes.

**Call relations**: The browser system can save this token during an active run. If recovery needs to happen, BrowserbaseCdpProvider.reattach receives the token and uses _parse_token to split it back into the same pieces.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Moves a workspace file into the remote browser session and returns the path Chrome should use for file input. This solves the cloud-browser problem where a local file path is meaningless to remote Chrome.

**Data flow**: It receives a workspace path and a callback that reads the file bytes. It takes the base filename, reads the bytes, rejects files above the upload limit, and avoids name collisions by adding a short hash when different paths share the same filename. It uploads the bytes to Browserbase, records the staged name, and returns the remote upload path under /tmp/.uploads.

**Call relations**: The browser engine calls this before asking Chrome to set a file input. This method prepares the safe remote name, calls BrowserbaseApi.upload to send the bytes, and then hands back the exact remote path that Chrome can see.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Tells UFO which download directory name Browserbase-hosted Chrome accepts. Unlike a local browser, this remote browser requires the literal relative directory name "downloads".

**Data flow**: It takes no outside data beyond the lease itself. It returns the fixed string Browserbase expects and changes nothing.

**Call relations**: The browser engine asks the lease where downloads should be directed. This method keeps Browserbase's restriction hidden behind the same lease interface used by other browser providers.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed remote browser download. It is the lease-level method UFO uses after Chrome reports that a download has finished.

**Data flow**: It receives the download GUID. It passes the lease's session ID and that GUID to BrowserbaseApi.download, then returns the bytes it gets back. Any wait, size check, or missing-file error happens in the API method it calls.

**Call relations**: The browser engine calls this through the lease interface. This method is a thin bridge from the engine's download request to BrowserbaseApi.download, which knows Browserbase's download-list and file-fetch endpoints.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser session and its saved Browserbase Context when the browser run ends. This is what stops the session from continuing to run and prevents the run's authenticated browser state from outliving the run.

**Data flow**: It reads the lease's session ID, context ID, conversation ID, API client, and store. It first asks Browserbase to release the session, then asks Browserbase to delete the context, and finally removes the stored context ID for the conversation. It returns nothing, but its side effects are the important cleanup.

**Call relations**: The browser framework calls this when it is done with the lease. The cleanup is deliberately nested so later cleanup still runs even if an earlier remote call fails; for example, the local store row is removed even if deleting the remote context has a transient problem.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts a fresh Browserbase-backed browser lease for the current sandbox turn. This is the main entry used when UFO needs a new remote Chrome session.

**Data flow**: It receives the current sandbox session and reads its conversation ID. It creates a BrowserbaseApi client, opens this extension's scoped store, gets or creates the Browserbase Context for that conversation, creates a Browserbase session, and returns a BrowserbaseLease containing all IDs and the connection URL. If no sandbox is supplied, it raises an error because it cannot safely name the browser run.

**Call relations**: UFO's CDP provider system calls this when the selected browser backend is Browserbase. It calls BrowserbaseCdpProvider._context to prepare browser profile state, BrowserbaseApi.create_session through that flow to start the hosted browser, and then packages the result as a lease for the engine.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context for a conversation or creates one if it does not exist yet. This lets a remade session in the same browser run keep cookies and local storage already earned.

**Data flow**: It receives an API client, a scoped store, and a conversation ID. It builds the store key, checks whether a context ID is already saved, and returns it if present. If not, it creates a new context through BrowserbaseApi.create_context, stores the new ID, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It is the small persistence layer between UFO's conversation identity and Browserbase's context identity.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reconnects to an already-created Browserbase session using a saved token. This supports recovery when UFO still has a live remote browser and should not start from scratch.

**Data flow**: It receives a token string, parses it into conversation ID, session ID, and context ID, builds a BrowserbaseApi client, asks Browserbase whether the session is still live, and returns a BrowserbaseLease with the current connection URL. If the token is bad or the session is gone, the flow raises SessionGone.

**Call relations**: The browser framework calls this when it has a previous lease token. It relies on _parse_token for safe token decoding and BrowserbaseApi.live_session to confirm Browserbase can still provide the session.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a saved lease token back into the three IDs needed to reattach: conversation, session, and context. It treats malformed tokens as gone sessions so recovery can fall back cleanly.

**Data flow**: It receives a token string. It splits the string on slashes, checks that session and context parts are present, converts the conversation part into a UUID, and returns the three values. If any part is missing or invalid, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this before contacting Browserbase. By validating the token first, it prevents later code from making confusing API calls with incomplete IDs.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to UFO: its name, version, required credential, and the Browserbase CDP provider it adds. Without this manifest, the host would not know how to select or build the Browserbase provider.

**Data flow**: It takes no input. It creates a Manifest that includes a credential slot for the Browserbase API key and a provider specification for the Browserbase backend. The returned manifest is what the extension loader reads.

**Call relations**: This is used when UFO loads extensions. The provider specification points the CDP provider system at BrowserbaseCdpProvider, while the credential slot tells the host which secret must be available before browser sessions can be minted.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and download retrieval`

This file exists so each conversation can have its own headless Chrome, running inside that conversation's sandbox. A sandbox is an isolated workspace, like a separate little computer. Keeping Chrome there matters because the browser can directly see the same files and downloads as the task, and one conversation's browser state does not leak into another.

The main work happens when the provider is asked for a browser lease. A lease is a temporary handle saying, “here is the browser you may use for this turn.” The provider runs a bring-up script inside the sandbox. That script checks whether Chrome is already answering on its debugging port. If not, it kills any stale recorded process, starts Chrome, waits until it really serves requests, then starts a small proxy on another port.

That proxy is important. Chrome's DevTools connection rejects requests whose Host header does not look local. The proxy accepts outside traffic, rewrites that one header to localhost, and passes the WebSocket traffic through. Think of it like a receptionist who changes the envelope address so Chrome will accept the letter, while leaving the contents alone.

Once the full chain works, the provider asks the sandbox how to reach the proxy from outside, builds a WebSocket endpoint, and returns a lease. Chrome and the proxy stay alive across turns, so closing the lease does not stop them. The file also supports placing files by path and reading completed downloads back out of the sandbox.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 294–295)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already-prepared browser connection details for this lease. Other code uses this to know where to connect to Chrome's DevTools interface, which is the browser control channel.

**Data flow**: It reads the stored endpoint from the lease object and returns it unchanged. Nothing is contacted, created, or modified.

**Call relations**: After SandboxChromeCdpProvider.lease has started or found the sandbox browser and built the endpoint, callers ask this lease method for that endpoint when they are ready to connect.


##### `SandboxChromeCdpLease.token`  (lines 297–298)

```
async def token(self) -> str
```

**Purpose**: Returns a durable-looking identifier for the browser lease: the endpoint URL. In this provider, the token is mainly a label, not enough by itself to reconnect later.

**Data flow**: It reads the URL inside the stored endpoint and returns that string. It does not check whether the browser is still alive.

**Call relations**: Code that wants a reattach token can call this after receiving a lease. However, SandboxChromeCdpProvider.reattach does not actually restore from this token, because the live sandbox must be supplied again through a fresh lease.


##### `SandboxChromeCdpLease.place_file`  (lines 300–304)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells the caller that a file is already in the right place for this browser. Because Chrome runs inside the sandbox, it can open the sandbox path directly instead of needing the file copied somewhere else.

**Data flow**: It receives a path and a possible file-reading callback. It ignores the callback because no transfer is needed, and returns the original path unchanged.

**Call relations**: Browser-driving code can call this when it needs to make a file available to Chrome, for example for upload. Unlike a local-browser provider, this function does not hand off to any copying logic because the browser and workspace share the same sandbox filesystem.


##### `SandboxChromeCdpLease.download_dir`  (lines 306–309)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the directory inside the sandbox where Chrome is configured to save downloads. Callers need this so they know where browser downloads will land.

**Data flow**: It returns the fixed sandbox download path. It does not create the directory here; the bring-up script is responsible for preparing it.

**Call relations**: Browser download code can ask the lease for this path before or during download handling. The path matches the Chrome setup done by SandboxChromeCdpProvider.lease through the sandbox bring-up command.


##### `SandboxChromeCdpLease.fetch_download`  (lines 311–335)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed browser download back out of the sandbox and returns its bytes. It also protects the main process by refusing downloads larger than the configured maximum.

**Data flow**: It receives a download id, safely quotes it for a shell command, and treats it as a filename inside the sandbox download directory. It first runs a sandbox command to count the file's bytes. If the file is missing or too large, it raises an error. Otherwise it runs another sandbox command to base64-encode the file, then decodes that text back into raw bytes in a worker thread and returns those bytes.

**Call relations**: Download-handling code calls this after Chrome has saved a file. The method relies on the lease's SandboxSession to run shell commands in the sandbox, uses shlex.quote to avoid unsafe shell text, and uses asyncio.to_thread so base64 decoding a whole file does not block the main async event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 337–338)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease without shutting down Chrome. This is intentional because the browser and proxy are meant to live with the conversation's sandbox across multiple turns.

**Data flow**: It receives no extra data and returns without changing anything. Chrome, the proxy, and the sandbox remain as they were.

**Call relations**: General lease-cleanup code can call this at the end of a turn. For this provider, cleanup is a no-op because the next SandboxChromeCdpProvider.lease call should reuse the existing in-sandbox browser if it is still serving.


##### `SandboxChromeCdpProvider.lease`  (lines 350–366)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts or reuses Chrome inside the supplied sandbox and returns a lease that tells the rest of the system how to connect to it. This is the central entry point for using this browser provider.

**Data flow**: It expects a SandboxSession. If none is supplied, it raises an error because this provider cannot work without a sandbox. It runs the browser bring-up command inside the sandbox, which starts Chrome and the proxy if needed and prints Chrome's WebSocket debugger URL. If that command fails, it raises an error with the sandbox output. Then it asks the sandbox how to dial the proxy port from outside, chooses ws or wss depending on whether that outside route uses TLS, rewrites the local Chrome WebSocket URL into an externally usable URL path, attaches any required carrier headers, and returns a SandboxChromeCdpLease containing that endpoint and sandbox.

**Call relations**: The core browser system calls this when a turn needs a CDP lease. It hands work to SandboxSession.bash to prepare Chrome, SandboxSession.dial to discover the public route to the proxy, _ws_path to keep only the safe local WebSocket path, then packages the result in CdpEndpoint and SandboxChromeCdpLease.

*Call graph*: calls 3 internal fn (bash, dial, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 368–369)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that this provider cannot reconnect from a saved token alone. A fresh lease must be created with the current live sandbox instead.

**Data flow**: It receives a token string, wraps it in a SessionGone error, and raises that error. It returns no lease.

**Call relations**: Recovery or reconnect code may call this when it has an old token. This provider deliberately refuses that path because the real endpoint depends on the sandbox's current port routing, which is only known during SandboxChromeCdpProvider.lease.

*Call graph*: 1 external calls (__init__).


##### `_ws_path`  (lines 372–377)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part from Chrome's local WebSocket debugger URL and rejects URLs that do not point at local Chrome. This prevents the provider from blindly forwarding an unexpected browser address.

**Data flow**: It receives a URL string, trims surrounding whitespace, and checks whether it starts with the expected local Chrome prefixes, either 127.0.0.1 or localhost on the Chrome debugging port. If it matches, it removes that local prefix and returns the remaining path. If it does not match, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease calls this after the sandbox bring-up script prints Chrome's debugger URL. The returned path is then joined with the externally reachable proxy host, so callers connect through the sandbox carrier rather than directly to Chrome's private localhost address.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 380–389)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO system so it can be selected as a CDP provider named sandbox_chrome. A manifest is the extension's registration card.

**Data flow**: It creates and returns a Manifest containing the extension name, version, and one CDP provider specification. That provider specification says which backend name it supports and how to build a SandboxChromeCdpProvider instance.

**Call relations**: Extension-loading code calls this to discover what the file offers. The returned CdpProviderSpec lets configuration that asks for the sandbox_chrome backend create this provider when browser access is needed.

*Call graph*: 2 external calls (__init__, __init__).

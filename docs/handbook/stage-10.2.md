# Remote and sandbox-hosted Chrome providers  `stage-10.2`

This stage is behind-the-scenes support for giving the system a Chrome browser to work with, even when that browser is not running on the same machine. It covers three ways to provide a browser.

The Browser Use file connects UFO to the hosted Browser Use service. Instead of UFO driving every click and page check itself, it can hand off one browsing task, or many tasks in parallel, to that service. Sensitive API keys stay outside the sandbox, so the isolated work area does not need to hold them.

The Browserbase file connects UFO to Browserbase, a remote Chrome hosting service. It can start a new browser session, reconnect to an existing one, move files in and out, and clean up the rented session when finished.

The sandbox Chrome file starts Chrome inside the sandbox for a single turn. It exposes Chrome through the Chrome DevTools Protocol, a standard remote-control doorway for browsers. It keeps ports, downloads, proxy settings, and cleanup separate so parallel turns do not interfere.

## Files in this stage

### Hosted browser services
Integrations that delegate browser execution to external hosted providers while managing tasks, sessions, files, and cleanup.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This extension is a swap-in replacement for UFO's normal browser tools. Instead of running and steering a browser locally, it sends a written task to Browser Use's hosted API, waits for that remote run to finish, and returns the result. Think of it like hiring a remote assistant with its own computer: UFO gives the assistant instructions, checks back until the job is done, then brings back the report and any allowed files.

The file defines the public tools `browser_task` and `wide_browse`. `browser_task` starts one fresh cloud browser session from a URL and a self-contained instruction. `wide_browse` reads a workspace file of URLs or site names, removes duplicates, and sends several cheap hosted runs in parallel, saving the combined rows to `wide_browse.json`.

A central `HostedRun` object performs the shared run lifecycle: get the Browser Use API key from scoped credentials, create or reattach to a run, poll its status, cancel it on timeout, read its final output, and optionally download result files. The code is careful about boundaries. Downloaded files must use HTTPS, large files are skipped, and paths are checked so remote output cannot escape the workspace. It also records run IDs under idempotency keys, so if a request is retried, it can rejoin a run that was already paid for instead of starting a duplicate.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one hosted Browser Use job from beginning to end. It is the main doorway used by both browser tools when they need Browser Use to do actual web automation.

**Data flow**: It receives the tool context, the task text, a timeout, and optionally a deduplication key. It reads the extension store and API credential, opens an HTTP client to Browser Use, starts or reconnects to a run, waits until it finishes or times out, optionally cancels late work, fetches the final summary, collects output files if requested, and returns a `RunOutcome` describing the status, text result, saved files, skipped files, and whether more files existed.

**Call relations**: The tool handlers call this when a user asks for `browser_task` or `wide_browse`. Inside, it hands off to `_start` to create or find the run, `_watch` and `_status` to follow progress, `_json` to safely read API replies, and `_collect` to bring result files into the workspace.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Creates a Browser Use run, unless a previous attempt with the same deduplication key already created one. This avoids paying for duplicate remote browser sessions when a tool call is retried.

**Data flow**: It receives an HTTP client, the extension store, task text, and an optional deduplication key. If a stored run handle exists, it validates and returns it. Otherwise it posts the task, model, cost limit, and proxy country to Browser Use, extracts the new run ID and workspace ID, stores them when possible, and returns a `StartedRun` handle.

**Call relations**: `HostedRun.execute` calls this at the start of every hosted run. It uses the scoped store to reattach to earlier work and uses `_json` and `_text` to turn the API response into trustworthy run identifiers.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits for a Browser Use run to reach a final state. It is the polling loop that keeps checking whether the remote assistant is done.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current state; if that state is final, it returns it, otherwise it sleeps briefly and checks again.

**Call relations**: `HostedRun.execute` calls this inside a timeout window. `_watch` delegates each individual status read to `_status`, keeping the repeated waiting logic separate from the one-time API read.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is used both during normal polling and right before cancelling, so the code does not cancel a run that finished just before the deadline.

**Data flow**: It receives an HTTP client and run ID, sends a GET request to the run status endpoint, parses the JSON response, extracts the `status` string, and returns that string.

**Call relations**: `_watch` calls this over and over while waiting. `HostedRun.execute` also calls it directly after a timeout to make one last check before deciding whether cancellation is needed.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies allowed output files from the Browser Use workspace into UFO's workspace. It only does this for runs whose caller asked to save files.

**Data flow**: It receives an HTTP client, tool context, and Browser Use workspace ID. If saving is disabled, it returns empty results. Otherwise it asks Browser Use for a limited file list, checks every entry has a safe path and size, skips oversized or undownloadable files, downloads acceptable files, writes them into the sandbox workspace, and returns lists of saved and skipped files plus a flag for whether more files existed.

**Call relations**: `HostedRun.execute` calls this after a run reaches a terminal status and its summary has been read. `_collect` uses `_json` for the listing, `contained_relative` to keep paths inside the workspace, and `_download` to fetch each safe file.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a vendor-provided link without sending UFO's Browser Use API key to that storage host. It also refuses non-HTTPS links.

**Data flow**: It receives a URL. It checks the URL starts with `https://`, opens a plain HTTP client without the Browser Use credential header, fetches the bytes, raises a clear error for failed downloads, and returns the file contents as bytes.

**Call relations**: `_collect` calls this for each output file that is small enough, has a URL, and has a safe workspace path. This keeps raw file fetching separate from file-list validation and sandbox writing.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a plain dictionary, or raises a readable Browser Use error. It keeps bad API replies from being treated as empty or successful results.

**Data flow**: It receives an HTTP response. It checks for error status codes, tries to parse the body as JSON, verifies the JSON is an object rather than a list or string, converts keys to strings, and returns the dictionary.

**Call relations**: `HostedRun.execute`, `_start`, `_status`, and `_collect` all use this whenever they read Browser Use API responses. It centralizes error reporting so the rest of the run flow can assume it has a usable object.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Pulls one required string field out of an API response. It gives a clear Browser Use error if the field is missing or is not text.

**Data flow**: It receives a parsed response dictionary and a field name. It looks up that field, checks that the value is a string, returns it when valid, and raises an error otherwise.

**Call relations**: `_start` uses this to read the new run ID and workspace ID. `_status` uses it to read the current run status after `_json` has parsed the response.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 344–378)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the public `browser_task` tool. It sends one self-contained browser instruction to Browser Use and returns the remote run's result and any fetched files.

**Data flow**: It receives the tool context and validated browser-task arguments: starting URL, task text, task name, and timeout. It builds a hosted run configured for the more capable task model, includes the URL in the instruction, uses the tool idempotency key if present, then converts the `RunOutcome` into a `ToolResult`. A timeout becomes an error message; other results become JSON containing the result text, saved files, skipped files, and whether more files exist.

**Call relations**: This function is registered as the handler for the `browser_task` tool in `BROWSER_USE_TOOLS`. When the tool is called, it creates a `HostedRun` and relies on `HostedRun.execute` for the actual Browser Use API work.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 381–388)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a file from the sandbox workspace using a safely quoted shell command. It exists because `wide_browse` needs to read user-provided input files.

**Data flow**: It receives the tool context and a path string. It shell-quotes the path so special characters are treated as part of the filename, runs `cat` in the sandbox, raises a clear error if reading fails, and returns the file contents as text.

**Call relations**: `_read_lines` calls this to read the list of entities. `_wide_browse` also calls it directly to read the JSON schema file before building per-entity Browser Use tasks.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 391–399)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a text file as a clean list of unique, non-empty lines. It is used to turn a user's batch input file into the set of sites or entities to browse.

**Data flow**: It receives the tool context and file path. It reads the whole file through `_read_file`, splits it into lines, trims whitespace, ignores blank lines, removes duplicates while keeping first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this at the start of a batch browsing request. It builds on `_read_file` so file access and shell quoting stay in one place.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 402–448)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the public `wide_browse` tool. It runs the same kind of browser extraction task across many URLs or site names and writes a combined JSON result file.

**Data flow**: It receives the tool context and validated batch-browsing arguments: an entities file, a prompt template, and an output-schema file. It reads and deduplicates entities, enforces a maximum count, reads the schema, creates a shared hosted-run runner, launches per-entity visits with a concurrency limit, converts individual failures into error rows, writes all rows to `wide_browse.json`, and returns JSON containing the rows and output filename.

**Call relations**: This function is registered as the handler for the `wide_browse` tool. It calls `_read_lines` and `_read_file` for workspace inputs, uses its inner `visit` function for each entity, and uses `asyncio.gather` so multiple Browser Use runs can proceed at once without one ordinary failure discarding the whole batch.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 418–433)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs Browser Use for one entity inside a wider batch. It turns one URL or site name into one row of the final batch result.

**Data flow**: It receives an entity string from the outer `_wide_browse` function. It waits for a concurrency slot, fills `{entity}` into the prompt template, appends the schema text when provided, runs the hosted browser task with a per-entity deduplication key, and returns a dictionary with the entity, final status, and result text.

**Call relations**: `_wide_browse` creates this helper and schedules one copy per entity. Its returned rows are gathered by `_wide_browse`, while exceptions are later turned into per-entity error rows so sibling runs are not thrown away.


##### `manifest`  (lines 471–486)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO extension system. It tells UFO the extension name, version, tools, prompt text, and required Browser Use API-key credential.

**Data flow**: It takes no input. It packages constants and tool definitions into a `Manifest`, including one prompt section and one credential slot, then returns that manifest to the loader.

**Call relations**: The extension system calls this during extension loading. The returned manifest is what makes `browser_task` and `wide_browse` available and tells the host to provide the Browser Use API key securely.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session setup, browser run, file transfer, reattach, teardown`

This extension is the bridge between UFO's browser feature and Browserbase's hosted Chrome sessions. Without it, a deployment configured to use Browserbase would not be able to open a browser, keep login state during a browser run, move files into the remote browser, or cleanly stop billing for the session afterward.

The main idea is a short-lived “lease,” like borrowing a rental car for one trip. When a browser subagent starts, BrowserbaseCdpProvider creates or reuses a Browserbase Context, which is Browserbase's saved browser state such as cookies and local storage. It then asks Browserbase for a fresh browser session tied to that context. The session returns a Chrome DevTools Protocol endpoint, which is the web address the rest of the system uses to drive Chrome remotely.

Because Chrome is running on Browserbase's machines, local file paths do not work. BrowserbaseLease.place_file uploads a workspace file to Browserbase first and returns the special remote path Chrome can see. Downloads work the other way around: the browser stores them in Browserbase session storage, and this file later fetches the bytes through the Browserbase API.

The code is careful about cleanup. Closing the lease releases the session and deletes the saved context so authenticated browser state does not outlive the browser run. It also reads the API key from a host credential slot on each API call, so secrets stay outside the sandbox and key rotation can take effect quickly.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new hosted Browserbase browser session using an existing Browserbase Context. The context carries saved browser state, while the new session is the live Chrome instance the system will control.

**Data flow**: It receives a context ID. It sends a POST request to Browserbase with that context and a session timeout, then reads the returned session ID and connection URL. It returns those two strings so the caller can track and connect to the browser.

**Call relations**: This is used when a new browser lease is being minted. It relies on BrowserbaseApi._json to make the HTTP request and on _field to make sure Browserbase actually returned the required session ID and connect URL.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still alive and, if so, gets the URL needed to reconnect to it. This supports recovery after the system already has a session token.

**Data flow**: It receives a session ID, asks Browserbase for that session's details, and reads the status. If the status is not one of the live states, it raises SessionGone; otherwise it returns the current connection URL.

**Call relations**: This is part of the reattach path. BrowserbaseCdpProvider.reattach calls through this method indirectly after parsing a saved token, so the system can reuse a still-running remote browser instead of creating a new one.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Tells Browserbase to release a browser session. This is what stops the remote Chrome from continuing to run after the browser turn is done.

**Data flow**: It receives a session ID and sends Browserbase a request changing that session's status to the release request value. It returns nothing, but the outside effect is that Browserbase is asked to shut down the session.

**Call relations**: BrowserbaseLease.aclose uses this during cleanup. It goes through BrowserbaseApi._json so API errors are reported instead of silently leaving a session running.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a Browserbase Context, which is the saved browser state used across sessions in the same browser run. This lets a recovered session keep things like logins and local storage.

**Data flow**: It sends an empty context creation request to Browserbase, checks the response for an ID, and returns that ID. Nothing local is changed by this function itself.

**Call relations**: BrowserbaseCdpProvider._context calls this when no context ID is already stored for the current conversation. It uses BrowserbaseApi._json for the request and _field to validate the returned ID.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context after the browser run is over. This prevents authenticated browser state from living longer than the subagent that created it.

**Data flow**: It receives a context ID and sends a DELETE request to Browserbase. It returns nothing, but the outside effect is that Browserbase is asked to remove that saved state.

**Call relations**: BrowserbaseLease.aclose calls this after requesting session release. It uses BrowserbaseApi._send directly because it does not need to parse a JSON response.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed browser download from Browserbase session storage. It waits briefly for Browserbase's download list to catch up after Chrome finishes saving the file.

**Data flow**: It receives a session ID and a download GUID, which is the name used to identify the stored download. It repeatedly asks Browserbase for the session's download list, finds the matching entry, checks that the listed size is present and not too large, then downloads and returns the file bytes. If the file never appears, is too large, or lacks a size, it raises an error.

**Call relations**: BrowserbaseLease.fetch_download delegates to this method. Inside, it uses BrowserbaseApi._json to inspect the download list, _size and _field to validate metadata, BrowserbaseApi._send to fetch raw bytes, and asyncio.sleep between retries.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a local file's bytes into a Browserbase session so the remote Chrome browser can use it. This is needed because a hosted browser cannot read files by local filesystem path.

**Data flow**: It receives a session ID, a remote file name, and bytes. It sends those bytes as a file upload to Browserbase. It returns nothing, but Browserbase stores the file for that session.

**Call relations**: BrowserbaseLease.place_file calls this after reading and naming the workspace file. The actual HTTP work is done by BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request and requires the response body to be a JSON object. This gives higher-level methods a safe dictionary to read from.

**Data flow**: It receives an HTTP method, path, and request options. It calls BrowserbaseApi._send, parses the response as JSON, checks that the parsed value is a dictionary-like object, and returns it. If Browserbase returns something unexpected, it raises BrowserbaseError.

**Call relations**: Most API methods use this helper when they expect structured JSON back, including session creation, session lookup, session release, context creation, and download listing. It centralizes the response-shape check so callers do not repeat it.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the API key attached. It is the low-level doorway between this extension and Browserbase's REST API.

**Data flow**: It receives an HTTP method, API path, timeout, and request options. It reads the Browserbase API key from the credential store, adds it to the request headers, sends the request with httpx, and returns the response if the status is successful. If Browserbase reports an error status, it raises BrowserbaseError with the status and response text.

**Call relations**: All BrowserbaseApi network operations eventually pass through this method, either directly or through BrowserbaseApi._json. This is where credential access, timeouts, test transport injection, and HTTP error reporting come together.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Reads a required string field from a Browserbase JSON response. It turns a missing or empty value into a clear BrowserbaseError instead of letting bad data travel farther.

**Data flow**: It receives a response dictionary and a field name. It looks up that field, checks that it is a non-empty string, and returns it. If the field is missing or not a useful string, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi methods use this after JSON responses from Browserbase, such as when reading session IDs, connection URLs, context IDs, download IDs, and session statuses.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the size of a listed Browserbase download. This protects the process from blindly pulling an unmeasured or oversized file into memory.

**Data flow**: It receives one download-list entry. It checks the entry's size value, rejects missing, boolean, or non-number values, converts the number to an integer, and returns it.

**Call relations**: BrowserbaseApi.download calls this before fetching a download's bytes. It is the safety gate that makes the download size limit enforceable before the file is read.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome DevTools Protocol endpoint for this leased Browserbase session. The rest of the browser engine uses this endpoint to control the remote Chrome.

**Data flow**: It reads the lease's stored connection URL and wraps it in a CdpEndpoint object. It returns that object without changing any state.

**Call relations**: The browser runtime asks the lease for this when it is ready to connect to Chrome. This method is the simple handoff from Browserbase session creation to the generic browser-driving code.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a reattach token that contains everything needed to find this browser run again. The token includes the conversation ID, session ID, and context ID.

**Data flow**: It reads the lease's conversation ID, session ID, and context ID. It formats them into one slash-separated string and returns it. No external state changes.

**Call relations**: The runtime can save this token and later pass it to BrowserbaseCdpProvider.reattach. The paired helper _parse_token reverses this format when reconnecting.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Uploads a workspace file into the remote Browserbase session and returns the path that remote Chrome can use. It also avoids filename collisions when two different local paths share the same base name.

**Data flow**: It receives a workspace path and a callback that reads the file bytes. It extracts the filename, reads the bytes, rejects files over the upload limit, chooses a remote name, adds a short hash prefix if that name is already used for a different path, uploads the bytes, records the staged name, and returns a Browserbase remote upload path.

**Call relations**: The browser engine calls this before asking Chrome to set a file input. It hands the real upload to BrowserbaseApi.upload, and the returned path is what the engine can safely give to the remote browser.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name Browserbase accepts for hosted Chrome sessions. It deliberately returns a relative name, not a local absolute path.

**Data flow**: It takes no input beyond the lease itself and returns the literal string used by Browserbase for session-backed downloads. It changes nothing.

**Call relations**: The browser engine asks the lease where Chrome should download files. This method supplies Browserbase's required value so downloads land in Browserbase session storage rather than on a disk this process cannot read.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a browser download from Browserbase after Chrome has saved it. It hides the Browserbase-specific download API behind the generic lease interface.

**Data flow**: It receives a download GUID. It passes the current session ID and that GUID to BrowserbaseApi.download, then returns the bytes that Browserbase sends back.

**Call relations**: The browser runtime calls this when it needs the contents of a completed download. This method delegates the waiting, size checking, and byte fetching to BrowserbaseApi.download.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser run by releasing the session, deleting the saved context, and removing the stored context ID. This is what keeps remote sessions and authenticated state from lingering after the turn.

**Data flow**: It uses the lease's session ID, context ID, and store key. It first asks Browserbase to release the session, then asks Browserbase to delete the context, then deletes the local store entry for that context. The nested cleanup means later steps are still attempted even if an earlier remote cleanup fails.

**Call relations**: The runtime calls this when the browser lease ends. It hands off to BrowserbaseApi.release_session, BrowserbaseApi.delete_context, and the scoped store delete operation in the order needed to avoid reusing a bad context entry.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Starts a new Browserbase-backed browser lease for the current browser run. It ties the remote session to the sandbox conversation so the run gets its own isolated saved state.

**Data flow**: It receives the current sandbox, reads the conversation ID from it, creates a BrowserbaseApi and scoped store, gets or creates a context ID, asks Browserbase for a new session, and returns a BrowserbaseLease containing all connection and cleanup information. If no sandbox is provided, it raises an error because it cannot identify the run.

**Call relations**: This is the main entry used by the core browser provider system when Browserbase is selected. It calls BrowserbaseCdpProvider._context to handle saved state, BrowserbaseApi.create_session through the API object to mint the session, and then packages the result as a BrowserbaseLease.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context for a conversation or creates one if none exists yet. This lets a recovered session in the same run keep browser state already earned during that run.

**Data flow**: It builds a store key from the conversation ID and asks the scoped store for an existing context ID. If it finds a non-empty string, it returns it. Otherwise it creates a new Browserbase context, stores the new ID under that key, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It uses the scoped store for persistence and BrowserbaseApi.create_context only when the store does not already have a usable context.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Reconnects to an existing Browserbase session using a saved token. This supports recovery when the system still has a live remote browser to resume.

**Data flow**: It receives a token, parses it into conversation ID, session ID, and context ID, creates a BrowserbaseApi, checks that the session is still live and gets its connection URL, then returns a BrowserbaseLease for that existing session. If the token is invalid or the session is gone, the lower-level steps raise SessionGone.

**Call relations**: The runtime calls this when it wants to resume a previous browser lease. It uses _parse_token to recover the IDs and BrowserbaseApi.live_session to verify Browserbase can still provide the browser connection.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a saved Browserbase lease token back into its three parts: conversation ID, session ID, and context ID. It treats malformed tokens as gone sessions so recovery can fall back cleanly.

**Data flow**: It receives a slash-separated token string. It splits out the conversation ID, session ID, and context ID, validates that the session and context are present, converts the conversation ID into a UUID, and returns all three values. If parsing fails, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this at the start of reconnecting. It is the inverse of BrowserbaseLease.token.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the UFO extension system. It says the extension is named Browserbase, needs a Browserbase API key credential, and provides a CDP provider backend called browserbase.

**Data flow**: It creates a Manifest object containing the extension name, version, credential slot description, and provider builder. The builder receives credentials and returns a BrowserbaseCdpProvider. The function returns the completed manifest.

**Call relations**: The extension loader calls this when discovering available extensions. The returned provider spec is what lets configuration such as cdp_provider = "browserbase" become a working BrowserbaseCdpProvider.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandbox Chrome provider
A per-turn sandboxed Chrome implementation that exposes CDP access and isolates ports, files, downloads, proxies, and cleanup.

### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `active during browser lease startup, reattach, use, download fetch, and teardown`

The file solves a practical problem: the system needs to control a real headless Chrome, but that Chrome must live inside the same isolated sandbox as the work it is browsing. Without this file, browser automation would either run outside the sandbox, lose access to sandbox files, share state between turns, or fail when Chrome tries to use the sandbox’s authenticated network proxy.

The provider creates a private “browser stack” for one lease. Think of it like reserving a small hotel room: it gets its own room number, keys, phone lines, storage, and checkout cleanup. The stack reserves three local ports, starts an egress bridge that adds proxy authentication for Chrome, starts Chrome itself, then starts a small DevTools proxy that rewrites the HTTP Host header Chrome requires while leaving WebSocket traffic unchanged.

The lease returns a CDP endpoint, which is the address the browser-control engine uses to talk to Chrome. It can also report the sandbox download directory and read completed downloads back out safely, with a maximum size check.

The file is careful about crashes and retries. A durable turn id names the stack before side effects happen, so if a worker dies mid-launch, later code can either reattach to the same browser or clean it up. Cleanup only removes the exact stack owned by that lease, not the whole sandbox.

#### Function details

##### `BrowserStack.root`  (lines 89–90)

```
def root(self) -> str
```

**Purpose**: Gives the main directory for this browser stack inside the sandbox. All files for this lease live under that directory.

**Data flow**: It reads the stack’s lease id → combines it with the shared browser leases directory → returns the full sandbox path for this stack’s root folder.

**Call relations**: Other path helpers build on this idea when command builders need places for logs, profiles, scripts, downloads, and process id files.


##### `BrowserStack.chrome_log`  (lines 93–94)

```
def chrome_log(self) -> str
```

**Purpose**: Gives the path where Chrome’s output log is written. This matters because startup failures are explained by Chrome’s own log.

**Data flow**: It starts with the stack root → appends the Chrome log filename → returns that path as text.

**Call relations**: The browser startup command writes Chrome output here, and failure-reporting code reads it when launch does not succeed.


##### `BrowserStack.chrome_pid`  (lines 97–98)

```
def chrome_pid(self) -> str
```

**Purpose**: Gives the path where the Chrome process id is recorded. The process id is needed later to stop exactly this Chrome process.

**Data flow**: It uses the stack root → appends the Chrome pid filename → returns the pid-file path.

**Call relations**: Startup code writes this file, and stack cleanup code uses it to find and stop Chrome during teardown.


##### `BrowserStack.chrome_profile`  (lines 101–102)

```
def chrome_profile(self) -> str
```

**Purpose**: Gives the path for Chrome’s private user profile. This keeps cookies, cache, and browser state separate for this lease.

**Data flow**: It takes the stack root → appends the profile directory name → returns the profile path.

**Call relations**: The browser startup command passes this path to Chrome so sibling turns do not share the same browser profile.


##### `BrowserStack.download_dir`  (lines 105–106)

```
def download_dir(self) -> str
```

**Purpose**: Gives the directory where this sandboxed Chrome stores downloads. Callers use it to tell Chrome where to save files and later read them back.

**Data flow**: It takes the stack root → appends the downloads directory name → returns the download path.

**Call relations**: Browser launch creates and uses this folder, while the lease exposes it through download_dir and reads files from it through fetch_download.


##### `BrowserStack.proxy_log`  (lines 109–110)

```
def proxy_log(self) -> str
```

**Purpose**: Gives the path for the DevTools proxy log. This helps explain failures in the small proxy that sits in front of Chrome’s debugging port.

**Data flow**: It uses the stack root → appends the proxy log filename → returns the path.

**Call relations**: The browser startup command writes proxy output here, and failure reporting can include it when startup times out.


##### `BrowserStack.proxy_pid`  (lines 113–114)

```
def proxy_pid(self) -> str
```

**Purpose**: Gives the path where the DevTools proxy process id is stored. Cleanup needs this to stop the right proxy process.

**Data flow**: It uses the stack root → appends the proxy pid filename → returns that pid-file path.

**Call relations**: Startup records the proxy process here, and stack cleanup reads it when closing the lease.


##### `BrowserStack.proxy_script`  (lines 117–118)

```
def proxy_script(self) -> str
```

**Purpose**: Gives the path where the generated DevTools proxy Python script is written inside the sandbox.

**Data flow**: It uses the stack root → appends the proxy script filename → returns the script path.

**Call relations**: The browser startup command writes the proxy program to this file before running it.


##### `BrowserStack.bridge_script`  (lines 121–122)

```
def bridge_script(self) -> str
```

**Purpose**: Gives the path where the generated egress bridge Python script is written inside the sandbox. The bridge is what adds proxy authentication for Chrome’s network traffic.

**Data flow**: It uses the stack root → appends the bridge script filename → returns the script path.

**Call relations**: The bridge startup command writes the egress bridge program here before launching it as a detached task.


##### `BrowserStack.task_base`  (lines 125–126)

```
def task_base(self) -> str
```

**Purpose**: Gives the shared filename prefix used for the detached egress bridge task’s bookkeeping files. Those files record things like pid, exit status, lock, and log.

**Data flow**: It uses the stack root → appends the egress bridge task base name → returns that prefix.

**Call relations**: Bridge startup, bridge replacement, reattach checks, and stack cleanup all use this prefix to coordinate the same background task.


##### `BrowserStack.lock_path`  (lines 129–130)

```
def lock_path(self) -> str
```

**Purpose**: Gives the slot lock path for this stack. The lock marks which lease owns a reserved group of ports.

**Data flow**: It reads the stack’s slot number → combines it with the shared lock directory → returns the lock path.

**Call relations**: Allocation creates this lock, reattach verifies it, and cleanup removes it only if this lease still owns it.


##### `_stack`  (lines 133–145)

```
def _stack(lease_id: str, slot: int) -> BrowserStack
```

**Purpose**: Builds a BrowserStack object from a lease id and slot number, after checking both are safe and valid. It turns an abstract slot into the actual three ports used by Chrome, the DevTools proxy, and the egress bridge.

**Data flow**: It receives a lease id and slot → rejects malformed ids or out-of-range slots → calculates the base port for that slot → returns a BrowserStack with all three port numbers filled in.

**Call relations**: The provider uses it after finding an allocation during a new lease. Token recovery also uses it after reading a saved token, so invalid or tampered tokens cannot create arbitrary paths or ports.

*Call graph*: called by 2 (_lease, _stack_from_token); 1 external calls (__init__).


##### `_assignments`  (lines 681–682)

```
def _assignments(values: dict[str, object]) -> str
```

**Purpose**: Formats Python variable assignments that are inserted into generated sandbox scripts. This lets the outer provider pass configuration values into scripts that will run inside the sandbox.

**Data flow**: It receives a dictionary of names and values → converts each pair into a safe Python assignment line using repr-style formatting → returns one text block.

**Call relations**: All command-building helpers call this before producing shell commands, so each generated script starts with the exact paths, ports, and timeouts it needs.

*Call graph*: called by 8 (_abandon_allocation_command, _allocate_command, _bridge_down_command, _browser_up_command, _egress_bridge_up_command, _find_allocation_command, _resolve_command, _stack_down_command).


##### `_allocate_command`  (lines 685–731)

```
def _allocate_command(lease_id: str) -> str
```

**Purpose**: Builds the shell command that reserves a private browser stack slot inside the sandbox. A slot is a small bundle of three ports plus a lock file saying who owns them.

**Data flow**: It receives a lease id → embeds directory and port settings into a generated Python script → returns a shell command that creates the lease directory, probes ports, creates a lock, and prints the chosen slot.

**Call relations**: SandboxChromeCdpProvider._lease sends this command to the sandbox at the start of a new browser lease.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_lease).


##### `_find_allocation_command`  (lines 734–769)

```
def _find_allocation_command(lease_id: str) -> str
```

**Purpose**: Builds the shell command that checks whether a lease already owns a valid slot. This is used both after allocation and after a crash, when the system may need to recover the previous stack.

**Data flow**: It receives a lease id → embeds paths and wait settings → returns a shell command that waits briefly for the lease directory, slot file, ready marker, and matching lock to appear, then prints the slot.

**Call relations**: SandboxChromeCdpProvider._lease uses it to confirm allocation, and SandboxChromeCdpProvider.reattach uses it to confirm a recovered token still points to a real owned slot.

*Call graph*: calls 1 internal fn (_assignments); called by 2 (_lease, reattach).


##### `_abandon_allocation_command`  (lines 772–792)

```
def _abandon_allocation_command(lease_id: str) -> str
```

**Purpose**: Builds the shell command that removes a half-created allocation. This is the cleanup path when slot setup cannot be trusted.

**Data flow**: It receives a lease id → embeds lock and root directories → returns a shell command that removes any lock owned by that lease and deletes the lease directory.

**Call relations**: _abandon_allocation runs this command when SandboxChromeCdpProvider._lease cannot turn allocation results into a valid stack.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_abandon_allocation).


##### `_egress_bridge_up_command`  (lines 795–801)

```
def _egress_bridge_up_command(stack: BrowserStack) -> str
```

**Purpose**: Builds the shell command that starts the local egress bridge for Chrome. The bridge accepts Chrome’s ordinary proxy requests and adds the sandbox proxy credentials Chrome cannot add itself.

**Data flow**: It receives a BrowserStack → embeds the listen address and bridge port → returns a shell command that writes the bridge Python program into the stack directory and executes it.

**Call relations**: _start_bridge uses this command during both fresh startup and reattach, before Chrome traffic can safely leave the sandbox.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_start_bridge).


##### `_browser_up_command`  (lines 804–854)

```
def _browser_up_command(stack: BrowserStack) -> str
```

**Purpose**: Builds the shell command that writes the DevTools proxy script, starts Chrome, waits for it, starts the DevTools proxy, and prints Chrome’s WebSocket debugger URL. This is the main in-sandbox browser launch command.

**Data flow**: It receives a BrowserStack → embeds all needed ports, paths, Chrome flags, proxy settings, and timeouts → returns a shell command that prepares scripts, starts services, waits for readiness, and prints the local debugger URL.

**Call relations**: SandboxChromeCdpProvider._lease runs this after the egress bridge is started. If it fails, _bring_up_failure helps turn the failure into a useful error message.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_lease).


##### `_stack_down_command`  (lines 857–874)

```
def _stack_down_command(stack: BrowserStack) -> str
```

**Purpose**: Builds the shell command that shuts down the whole browser stack and removes its files. It is careful to stop only processes and locks belonging to this lease.

**Data flow**: It receives a BrowserStack → embeds pid paths, lock path, lease id, and timing settings → returns a shell command that stops the bridge task, Chrome, and proxy, deletes task records and the stack directory, and releases the slot lock if owned.

**Call relations**: _stop_stack runs this command when a lease closes, when startup fails, or when reattach discovers the stack is no longer usable.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_stop_stack).


##### `_bridge_down_command`  (lines 877–891)

```
def _bridge_down_command(stack: BrowserStack) -> str
```

**Purpose**: Builds the shell command that stops only the detached egress bridge task. This is used when reattaching, because the bridge may need to be restarted with the current turn’s proxy credentials.

**Data flow**: It receives a BrowserStack → embeds the task prefix, lock path, lease id, and timeout settings → returns a shell command that verifies ownership, stops the recorded bridge task, and removes its bookkeeping files.

**Call relations**: _stop_bridge runs this during SandboxChromeCdpProvider.reattach before _start_bridge launches a fresh bridge.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_stop_bridge).


##### `_resolve_command`  (lines 894–939)

```
def _resolve_command(stack: BrowserStack) -> str
```

**Purpose**: Builds the shell command that verifies an existing browser stack still works and asks its DevTools proxy for the current WebSocket debugger URL. This is the reattach-time readiness check.

**Data flow**: It receives a BrowserStack → embeds ports, task files, timeout settings, and log limits → returns a shell command that waits for the bridge, queries the proxied Chrome version endpoint, and prints the WebSocket URL.

**Call relations**: SandboxChromeCdpProvider.reattach runs this after restarting the bridge, then passes its output to _endpoint.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (reattach).


##### `SandboxChromeCdpLease.endpoint`  (lines 950–951)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the CDP endpoint for this leased browser. The endpoint is the address and headers the browser-control engine uses to talk to Chrome.

**Data flow**: It reads the endpoint stored in the lease → returns it unchanged.

**Call relations**: Callers use this after SandboxChromeCdpProvider._lease or reattach returns a lease, so they can connect to the sandboxed browser.


##### `SandboxChromeCdpLease.token`  (lines 953–954)

```
async def token(self) -> str
```

**Purpose**: Returns a small recovery token for this browser stack. The token lets a later worker try to reattach to the same stack after an interruption.

**Data flow**: It reads the lease’s BrowserStack → passes it to _stack_token → returns the JSON token string.

**Call relations**: The lease exposes this to the wider system. SandboxChromeCdpProvider._lease also creates this kind of token when attempting to reclaim an interrupted stack.

*Call graph*: calls 1 internal fn (_stack_token).


##### `SandboxChromeCdpLease.place_file`  (lines 956–960)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Returns the same file path the caller already has, because this Chrome runs inside the sandbox and can open sandbox files directly. No file copy is needed.

**Data flow**: It receives a path and a read callback → ignores the callback because there is nothing to transfer → returns the original path.

**Call relations**: Browser-control code can call this through the generic lease interface. For this provider, the answer is simple because browser and files are already in the same sandbox.


##### `SandboxChromeCdpLease.download_dir`  (lines 962–965)

```
async def download_dir(self) -> str
```

**Purpose**: Reports the directory inside the sandbox where Chrome writes downloads. This tells callers where browser downloads are expected to appear.

**Data flow**: It reads the stack’s download directory path → returns that sandbox path.

**Call relations**: The browser launch command configured Chrome to use this directory, and fetch_download later reads completed files from the same place.


##### `SandboxChromeCdpLease.fetch_download`  (lines 967–995)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed download from the sandbox and returns its bytes to the caller. It first checks the file size so an untrusted page cannot force the system to pull back an enormous file.

**Data flow**: It receives a download guid → builds the expected file path in the download directory → runs wc -c in the sandbox to measure it → rejects missing or too-large files → runs base64 in the sandbox to stream safe text back → decodes that text into bytes in a worker thread → returns the downloaded bytes.

**Call relations**: Callers use this through the lease after Chrome has downloaded a file. It relies on sandbox.bash commands and uses shlex.quote to avoid unsafe shell paths, then asyncio.to_thread so base64 decoding does not block the main event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 997–998)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser lease by stopping its whole browser stack. This is the checkout step that frees ports, processes, files, and locks.

**Data flow**: It reads the lease’s sandbox and BrowserStack → calls _stop_stack → leaves the stack stopped and cleaned up or raises an error if cleanup fails.

**Call relations**: The wider system calls this when it is done with the browser. _stop_stack performs the actual in-sandbox cleanup.

*Call graph*: calls 1 internal fn (_stop_stack).


##### `SandboxChromeCdpProvider.lease`  (lines 1005–1014)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new sandbox Chrome lease for a turn. It enforces that a sandbox and durable turn id are present, because both are needed to own and recover the browser stack.

**Data flow**: It receives an optional sandbox → rejects missing sandbox or missing turn id → calls _lease with recovery retry enabled → returns a CdpLease.

**Call relations**: This is the public entry used by the CDP provider interface. It hands the real work to SandboxChromeCdpProvider._lease.

*Call graph*: calls 1 internal fn (_lease).


##### `SandboxChromeCdpProvider._lease`  (lines 1016–1065)

```
async def _lease(self, sandbox: Sandbox, retry_recovered_stack: bool) -> CdpLease
```

**Purpose**: Performs the full startup path for a browser lease, including allocation, recovery, bridge startup, Chrome startup, endpoint creation, and cleanup on failure. It is the central coordinator for creating a usable sandboxed browser.

**Data flow**: It receives a sandbox and a retry flag → derives the lease id from the turn id → runs allocation and allocation-confirmation commands in the sandbox → builds a BrowserStack → either reattaches to an interrupted stack or starts the bridge and browser → converts the reported WebSocket URL into an external endpoint → returns a SandboxChromeCdpLease. If any startup step fails, it stops the stack before re-raising.

**Call relations**: SandboxChromeCdpProvider.lease calls this for normal startup. It calls command builders, sandbox.bash, _start_bridge, _browser_up_command, _bring_up_failure, _endpoint, _stop_stack, _abandon_allocation, and may call reattach when it finds a previous allocation.

*Call graph*: calls 12 internal fn (bash, reattach, _abandon_allocation, _allocate_command, _bring_up_failure, _browser_up_command, _endpoint, _find_allocation_command, _stack, _stack_token (+2 more)); called by 1 (lease); 2 external calls (__init__, shield).


##### `SandboxChromeCdpProvider.reattach`  (lines 1067–1104)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Tries to reconnect to an existing browser stack from a saved token. This is used after an interruption so the system can keep using the same sandbox Chrome instead of losing the session.

**Data flow**: It receives a token and sandbox → rejects missing sandbox or turn id → parses the token into a BrowserStack → checks the token belongs to the current turn → verifies the slot is still owned → stops the old bridge, starts a fresh bridge, resolves the current WebSocket URL, and builds a new endpoint → returns a SandboxChromeCdpLease. If the stack cannot be trusted, it cleans up and raises SessionGone.

**Call relations**: SandboxChromeCdpProvider._lease calls this when allocation suggests an interrupted stack already exists. It uses _stack_from_token, _find_allocation_command, _stop_bridge, _start_bridge, _resolve_command, _endpoint, and _stop_stack.

*Call graph*: calls 8 internal fn (bash, _endpoint, _find_allocation_command, _resolve_command, _stack_from_token, _start_bridge, _stop_bridge, _stop_stack); called by 1 (_lease); 3 external calls (__init__, __init__, shield).


##### `_endpoint`  (lines 1107–1113)

```
async def _endpoint(sandbox: Sandbox, stack: BrowserStack, local_url: str) -> CdpEndpoint
```

**Purpose**: Turns Chrome’s local WebSocket debugger URL into the public endpoint that outside browser-control code can dial. It preserves the sandbox carrier’s host, TLS choice, and headers.

**Data flow**: It receives a sandbox, BrowserStack, and local WebSocket URL → asks the sandbox to expose/dial the DevTools proxy port → extracts the safe WebSocket path from the local URL → combines the carrier host with ws or wss and the path → returns a CdpEndpoint with required headers.

**Call relations**: Both fresh startup and reattach call this after they have a local debugger URL. It calls sandbox.dial and _ws_path.

*Call graph*: calls 2 internal fn (dial, _ws_path); called by 2 (_lease, reattach); 1 external calls (__init__).


##### `_start_bridge`  (lines 1116–1128)

```
async def _start_bridge(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Starts the egress bridge as a detached sandbox task. Detached means it keeps running after the startup command returns, for as long as the browser lease needs it.

**Data flow**: It receives a sandbox and BrowserStack → builds the bridge startup command → asks the sandbox to run it as a named background task → raises an error if the task did not start cleanly.

**Call relations**: SandboxChromeCdpProvider._lease calls it before Chrome launch, and SandboxChromeCdpProvider.reattach calls it after replacing the bridge.

*Call graph*: calls 2 internal fn (bash_task, _egress_bridge_up_command); called by 2 (_lease, reattach).


##### `_stop_bridge`  (lines 1131–1140)

```
async def _stop_bridge(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Stops the current egress bridge task without tearing down Chrome. This lets reattach refresh the bridge while keeping the browser stack itself.

**Data flow**: It receives a sandbox and BrowserStack → builds the bridge shutdown command → runs it in the sandbox → raises an error if the command reports failure.

**Call relations**: SandboxChromeCdpProvider.reattach calls this before _start_bridge, so the recovered turn gets a bridge tied to its current proxy authorization.

*Call graph*: calls 2 internal fn (bash, _bridge_down_command); called by 1 (reattach).


##### `_abandon_allocation`  (lines 1143–1149)

```
async def _abandon_allocation(sandbox: Sandbox, lease_id: str) -> None
```

**Purpose**: Best-effort cleanup for a failed or suspicious allocation. It tries to remove the lease’s lock and directory, but deliberately ignores cleanup errors.

**Data flow**: It receives a sandbox and lease id → builds the abandon-allocation command → runs it in the sandbox with a timeout → swallows any exception because this is already an error-recovery path.

**Call relations**: SandboxChromeCdpProvider._lease calls this when allocation confirmation cannot produce a valid BrowserStack.

*Call graph*: calls 2 internal fn (bash, _abandon_allocation_command); called by 1 (_lease).


##### `_stop_stack`  (lines 1152–1161)

```
async def _stop_stack(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Stops Chrome, the DevTools proxy, and the egress bridge, then removes this lease’s files and port lock. This is the main teardown helper.

**Data flow**: It receives a sandbox and BrowserStack → builds the full stack cleanup command → runs it in the sandbox → raises a RuntimeError if cleanup reports failure.

**Call relations**: SandboxChromeCdpLease.aclose calls it during normal close. SandboxChromeCdpProvider._lease and reattach also call it when startup or recovery fails.

*Call graph*: calls 2 internal fn (bash, _stack_down_command); called by 3 (aclose, _lease, reattach).


##### `_stack_from_token`  (lines 1164–1175)

```
def _stack_from_token(token: str) -> BrowserStack
```

**Purpose**: Parses and validates a saved recovery token into a BrowserStack. Bad tokens are treated as gone sessions, not as ordinary data.

**Data flow**: It receives token text → parses JSON → checks the version, lease id type, and slot type → calls _stack for stricter validation and port calculation → returns the BrowserStack. If anything is wrong, it raises SessionGone.

**Call relations**: SandboxChromeCdpProvider.reattach uses this as its first step, so only well-formed provider-created tokens can drive recovery.

*Call graph*: calls 1 internal fn (_stack); called by 1 (reattach); 2 external calls (__init__, loads).


##### `_stack_token`  (lines 1178–1183)

```
def _stack_token(stack: BrowserStack) -> str
```

**Purpose**: Creates a compact JSON token that identifies a browser stack by version, lease id, and slot. The token is used for later recovery.

**Data flow**: It receives a BrowserStack → serializes the token fields as sorted compact JSON → returns the token string.

**Call relations**: SandboxChromeCdpLease.token exposes it publicly, and SandboxChromeCdpProvider._lease uses it when trying to reattach to an interrupted stack.

*Call graph*: called by 2 (token, _lease); 1 external calls (dumps).


##### `_bring_up_failure`  (lines 1186–1201)

```
async def _bring_up_failure(sandbox: Sandbox, stack: BrowserStack, result: ExecResult) -> str
```

**Purpose**: Turns a failed browser startup result into a useful human-readable error. If the sandbox command timed out, it fetches the tail of Chrome, proxy, and bridge logs because the killed command may have lost its own explanation.

**Data flow**: It receives the sandbox, BrowserStack, and failed ExecResult → uses stderr or stdout if the command itself reported an error → if the command timed out, runs tail inside the sandbox on relevant logs → returns a combined explanation string.

**Call relations**: SandboxChromeCdpProvider._lease calls this when _browser_up_command fails, before raising the startup error.

*Call graph*: calls 1 internal fn (bash); called by 1 (_lease).


##### `_ws_path`  (lines 1204–1209)

```
def _ws_path(url: str, chrome_port: int) -> str
```

**Purpose**: Extracts the path part from Chrome’s local WebSocket debugger URL and rejects non-local URLs. This prevents the provider from accidentally connecting callers to an unexpected host.

**Data flow**: It receives a WebSocket URL and the expected Chrome port → trims whitespace → accepts only URLs starting with ws://127.0.0.1:<port> or ws://localhost:<port> → returns the remaining path. Otherwise it raises an error.

**Call relations**: _endpoint calls this when building the public CDP endpoint from the local URL printed by Chrome or the proxy.

*Call graph*: called by 1 (_endpoint).


##### `manifest`  (lines 1212–1221)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It says the extension is named sandbox_chrome and offers a CDP provider backend with that name.

**Data flow**: It uses the file’s name, version, and backend constants → constructs a CdpProviderSpec whose build function creates SandboxChromeCdpProvider → returns a Manifest containing that provider.

**Call relations**: The extension loader calls this to discover and register the sandbox_chrome CDP provider.

*Call graph*: 2 external calls (__init__, __init__).

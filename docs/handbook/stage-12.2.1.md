# Browser Provider Integrations  `stage-12.2.1`

This stage is shared support for any part of the system that needs a browser. Instead of forcing the rest of the code to know where the browser comes from, these integrations provide interchangeable “browser engines,” like swapping the motor in a machine while keeping the same controls.

The Browser Use integration connects UFO to a hosted web-automation service. It offers two familiar tools: `browser_task` for running one browser job, and `wide_browse` for running many jobs in parallel. Callers can keep asking for those tools without caring that the work is happening in an outside service.

The Browserbase integration provides a remote Chrome browser. For a browser run, it creates the hosted Chrome session, connects UFO to it, transfers needed files in and results out, then shuts the session down.

The sandbox Chrome integration uses a real Chrome browser inside the conversation’s own sandbox, which is an isolated workspace. It starts Chrome or reuses an existing one, exposes a safe control link, and lets the system drive it.

## Files in this stage

### Hosted Browser Jobs
Integrates the hosted Browser Use automation service as drop-in tools for single and parallel browser tasks.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This file is an adapter between UFO and Browser Use, a paid hosted service that runs browser automation in the cloud. Instead of UFO controlling a browser directly, it sends a plain-language task to Browser Use, waits for the remote run to finish, and brings back the result and, for single tasks, any output files the run produced. Without this file, a deployment that wants to use Browser Use could not expose the same `browser_task` and `wide_browse` tools that the rest of the system already expects.

The main worker is `HostedRun`. It gets the Browser Use API key from UFO’s protected credential system, starts a run through the Browser Use REST API, polls until the run finishes, cancels it if UFO’s time limit expires, and converts the answer into a simple `RunOutcome`. For `browser_task`, it also downloads small output files into the workspace, while refusing unsafe paths or non-HTTPS download links.

The two public tool handlers are `_browser_task` and `_wide_browse`. `_browser_task` sends one detailed browser instruction. `_wide_browse` reads a workspace file of URLs or site names, removes duplicates, then runs many cheaper browser jobs in parallel and writes the combined rows to `wide_browse.json`. The file also declares the extension manifest, including tool names, prompts, and the required API-key slot.

#### Function details

##### `HostedRun.execute`  (lines 136–171)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one complete Browser Use job from start to finish. It checks the task is not too large, opens an authenticated API client, starts or reattaches to a remote run, waits for it, handles timeouts, collects output files when requested, and returns a clear summary.

**Data flow**: It receives the current tool context, the task text, a time limit, and optionally a repeat-safe key. It reads the Browser Use API key from the extension credentials and uses the extension store to remember or find a previously started run. It sends the task to Browser Use, watches the run status, cancels if the local deadline is reached, fetches the final summary, optionally copies output files into the sandbox workspace, and returns a `RunOutcome` containing status, text output, saved files, skipped files, and whether more files existed.

**Call relations**: The tool handlers call this when they need Browser Use to do real browsing work. Inside, it delegates the smaller steps to `_start`, `_watch`, `_status`, `_json`, and `_collect`, so the overall flow reads like: create or find the run, wait for it, inspect the result, and bring back any files.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 173–200)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Starts a Browser Use run, unless a previous attempt with the same deduplication key already started one. This avoids paying for duplicate remote runs when a request is retried.

**Data flow**: It receives an HTTP client, the extension store, the task text, and an optional deduplication key. If the key already maps to a stored run, it validates and returns that saved run handle. Otherwise it posts a new run request with the task, model, cost limit, and proxy country, extracts the returned run ID and workspace ID, stores them if needed, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this near the beginning of every run. It uses `_json` to make sure the API response is readable and `_text` to pull required string fields from that response before handing the run handle back to `execute`.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 202–207)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits until a Browser Use run reaches a final state. It is the polling loop, like checking a delivery tracker every few seconds until the package is delivered, failed, or cancelled.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current status; if the status is final, it returns that status, otherwise it sleeps briefly and tries again.

**Call relations**: `HostedRun.execute` calls this inside a timeout block. `_watch` relies on `_status` for each individual status check and hands the final status back to `execute`, which then decides whether to fetch results or report a timeout.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 209–213)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is used both during normal polling and immediately after a timeout to avoid cancelling a run that actually finished in the last moment.

**Data flow**: It receives an HTTP client and run ID. It sends a GET request to the run status endpoint, parses the JSON response, extracts the `status` string, and returns it.

**Call relations**: `_watch` calls this repeatedly while waiting. `HostedRun.execute` also calls it directly when the local timeout fires, so it can distinguish a genuinely still-running job from one that just completed.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 215–252)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies a completed run’s output files from Browser Use into UFO’s workspace when this run type is meant to save files. It also reports files it could not safely or reasonably fetch.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If file saving is off, it returns empty results. Otherwise it asks Browser Use for a limited file list, checks that each listed item has a safe relative path and a size, skips files that are too large or have no usable URL, downloads acceptable files, writes them into the sandbox workspace, and returns saved files, skipped files, and whether the remote listing said more files exist.

**Call relations**: `HostedRun.execute` calls this after reading the final run summary. `_collect` uses `_json` to read the file listing and `_download` to fetch each acceptable file, then passes the final file report back as part of the `RunOutcome`.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, Path).


##### `HostedRun._download`  (lines 254–270)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one Browser Use output file from its temporary URL without sending UFO’s API key to the storage host. It also refuses non-HTTPS links, because output files should not be fetched over an unencrypted or suspicious address.

**Data flow**: It receives a download URL. It checks the URL starts with `https://`, opens a plain HTTP client with no Browser Use credential header, fetches the bytes, raises a clear error if the download failed, and returns the file contents as bytes.

**Call relations**: `_collect` calls this for each file that is small enough and has a valid URL. Its returned bytes are immediately written into the sandbox workspace by `_collect`.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 273–288)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a dictionary the rest of the code can safely read. It converts bad statuses, invalid JSON, or unexpected response shapes into one consistent `BrowserUseError`.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises an error with the endpoint and response text. Otherwise it tries to parse JSON, checks the result is an object rather than a list or plain value, converts keys to strings, and returns the dictionary.

**Call relations**: Most API-facing methods use this after their HTTP requests: `_start`, `_status`, `_collect`, and `execute`. This keeps response checking in one place instead of scattering fragile parsing rules through the run flow.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 291–297)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts one required string field from a parsed Browser Use response. It gives a clear Browser Use-specific error if the field is missing or not text.

**Data flow**: It receives a response dictionary and the name of the field to read. It looks up that field, checks it is a string, returns the string when valid, and raises `BrowserUseError` otherwise.

**Call relations**: `_start` uses this to read the new run’s ID and workspace ID. `_status` uses it to read the run status. This small helper makes required API fields fail in a predictable way.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 342–376)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the public `browser_task` tool. It sends one self-contained browser task to Browser Use and returns the remote result plus a list of any output files copied into the workspace.

**Data flow**: It receives the tool context and structured browser-task arguments: starting URL, task text, task name, timeout, and user-facing description. It builds a full task prompt beginning with the URL, creates a `HostedRun` configured for the higher-quality single-task model and file saving, runs it, and turns the outcome into a `ToolResult`. If the run timed out, it returns an error message; otherwise it returns JSON containing the result text, saved file paths, skipped files, and whether more files exist.

**Call relations**: The tool registry calls this when a caller invokes `browser_task`. It relies on `HostedRun.execute` for the remote browser work, then wraps the outcome in `TextContent` and `ToolResult` so the rest of UFO can display or process it like any other tool answer.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 379–386)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a file from the sandbox workspace by running `cat` safely through the sandbox shell. It exists because `wide_browse` needs to read user-supplied files for entity lists and output schemas.

**Data flow**: It receives the tool context and a file path. It shell-quotes the path so special characters in the model-supplied path cannot become shell commands, runs `cat` in the sandbox, raises a `ValueError` if reading fails, and returns the file contents as text.

**Call relations**: `_read_lines` calls this to load the entity list. `_wide_browse` calls it directly to load the JSON schema file. It is a small safety wrapper around sandbox file reading.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 389–397)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace file as a clean list of unique, non-empty lines. For `wide_browse`, each remaining line becomes one URL or site name to visit.

**Data flow**: It receives the tool context and file path. It uses `_read_file` to get the file text, splits it into lines, trims whitespace, ignores blank lines, removes duplicates while preserving the first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this at the start of a batch. By cleaning the list before any remote runs begin, it prevents wasted duplicate Browser Use jobs.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 400–446)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the public `wide_browse` tool for browsing many entities in parallel. It reads a list of URLs or site names, runs a browser task for each one, and writes the combined results to a JSON file.

**Data flow**: It receives the tool context and structured batch arguments: an entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates entities, rejects batches that are too large, reads the output schema, creates a concurrency limiter so only a fixed number of runs happen at once, and creates a cheaper `HostedRun` runner that does not save per-run files. It gathers all per-entity visits, records ordinary per-entity failures as rows instead of throwing away the whole batch, writes all rows to `wide_browse.json`, and returns JSON containing the rows and output filename.

**Call relations**: The tool registry calls this when a caller invokes `wide_browse`. It uses `_read_lines` and `_read_file` for workspace inputs, uses its inner `visit` function for each remote Browser Use run, and uses `asyncio.gather` so the batch can proceed in parallel while still producing one combined result.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 416–431)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the Browser Use task for one entity inside a `wide_browse` batch. It is scoped so one failed entity becomes one failed row rather than cancelling the already-paid-for sibling runs.

**Data flow**: It receives one entity string from the outer `_wide_browse` function. It waits for the batch semaphore so too many runs do not start at once, fills the prompt template by replacing `{entity}`, appends the requested output schema if present, calls the shared `HostedRun.execute`, and returns a row with the entity, final status, and result text.

**Call relations**: `_wide_browse` creates this helper and passes one call per entity into `asyncio.gather`. It uses the runner and settings prepared by `_wide_browse`, then hands back rows that `_wide_browse` combines and writes to `wide_browse.json`.


##### `manifest`  (lines 469–484)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to UFO: its name, version, tools, prompt text, and required Browser Use API key. This is how the host knows what this file contributes at startup.

**Data flow**: It takes no input. It packages the two tool definitions, the browser prompt section loaded from the neighboring markdown file, and a credential slot for the Browser Use API key into a `Manifest`, then returns it.

**Call relations**: The extension loading system calls this when registering the pack. The returned manifest tells UFO to expose `browser_task` and `wide_browse`, include the prompt section, and request the configured credential before the tool handlers need it.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Remote Chrome Sessions
Connects UFO to Browserbase-hosted Chrome sessions, including setup, file transfer, and cleanup.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session lifecycle`

UFO needs a Chrome browser to drive web pages. This extension supplies that browser through Browserbase, a service that runs Chrome in the cloud. Think of it like renting a clean workstation for one task, then returning it when the task is done.

The file has three main parts. BrowserbaseApi is the small wrapper around Browserbase's web API. It creates sessions, checks whether an old session is still alive, uploads files, fetches downloads, and releases sessions. BrowserbaseCdpProvider is the piece UFO plugs into. When a browser turn starts, it finds the current conversation, gets or creates a Browserbase “context” for that run, and asks Browserbase for a session. A context is saved browser state, such as logins and local storage, kept only for that browser run. BrowserbaseLease represents the active rented browser session. It gives UFO the Chrome connection URL, stages local files into Browserbase’s remote upload folder, retrieves completed downloads, and closes everything afterward.

A few safety choices matter. API keys are read on the host each time, not sent into the sandbox. Uploads and downloads are size-limited so a web page cannot force this process to load huge files into memory. Cleanup releases the session and deletes the context, so authenticated browser state does not outlive the subagent run.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new Browserbase-hosted Chrome session tied to an existing Browserbase context. UFO uses this when it needs a fresh remote browser for a browser run.

**Data flow**: It receives a context ID, sends Browserbase a request saying to use that context and keep it persistent, then reads the returned session ID and Chrome connection URL. The result is the pair of identifiers UFO needs to connect to the remote browser.

**Call relations**: This method uses the shared JSON request helper and field checker so session creation fails loudly if Browserbase returns a bad response. It is called during the provider's lease flow after the context has been chosen.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session can still be reused, and returns its current connection URL if it can. This is used after recovery, when UFO has a saved token and wants to reattach instead of starting over.

**Data flow**: It receives a session ID, asks Browserbase for that session, checks the reported status, and returns the connect URL if the status is still live. If the session has ended or been reaped, it raises SessionGone so the caller knows not to reuse it.

**Call relations**: The reattach path relies on this check before rebuilding a lease. It delegates HTTP and response validation to the common JSON helper and field extractor, then signals failure with SessionGone when Browserbase says the session is no longer usable.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Tells Browserbase that UFO is done with a hosted browser session. This prevents the remote browser from continuing to run or bill after the browser turn ends.

**Data flow**: It receives a session ID and sends Browserbase a status update requesting release. It does not return data; the important outcome is that the remote session is marked for shutdown.

**Call relations**: BrowserbaseLease.aclose calls this first during cleanup. It uses the same JSON request helper as other API calls, so release failures surface as transport errors instead of being silently ignored.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a Browserbase context, which is the saved browser state used across sessions within the same browser run. This lets a recovered session keep things like logins already earned earlier in the run.

**Data flow**: It sends an empty context-creation request to Browserbase and extracts the returned context ID. The output is that new context ID as a string.

**Call relations**: BrowserbaseCdpProvider._context calls this only when no context is already stored for the conversation. It uses the shared JSON helper and field extractor to make sure a missing ID is treated as an error.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes the Browserbase context for a finished browser run. This removes saved browser state so authenticated data does not outlive the subagent that used it.

**Data flow**: It receives a context ID and sends Browserbase a delete request. It returns nothing; the intended change is that Browserbase removes the stored context.

**Call relations**: BrowserbaseLease.aclose calls this after asking Browserbase to release the session. It goes through the lower-level send helper because a delete response does not need to be parsed as JSON here.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed Browserbase download, while refusing files that are too large or not properly measured. This protects the host process from blindly loading untrusted large downloads into memory.

**Data flow**: It receives a session ID and a download GUID, repeatedly asks Browserbase for the session's download list, finds the entry whose filename matches the GUID, checks its listed size, and then downloads the file bytes. If the file never appears, is too large, or lacks a trustworthy size, it raises an error instead of returning unsafe data.

**Call relations**: BrowserbaseLease.fetch_download hands download requests to this method. Internally it uses the JSON helper for listings, the raw send helper for the binary file, _size to enforce the size check, _field to read required IDs, and short sleeps while waiting for Browserbase storage to catch up.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a local file's bytes into the Browserbase session so the remote Chrome can use it. This is needed because Browserbase Chrome runs on another machine and cannot read local workspace paths directly.

**Data flow**: It receives a session ID, a remote filename, and file bytes, then posts those bytes to Browserbase's session upload endpoint. It returns nothing; after success, Browserbase has staged the file for the browser.

**Call relations**: BrowserbaseLease.place_file calls this after reading and naming the file. It uses the raw send helper because the important result is a successful upload request, not a parsed response body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request and makes sure the response is a JSON object, meaning a dictionary-like response. It centralizes the common pattern used by most Browserbase API calls.

**Data flow**: It receives an HTTP method, an API path, and optional request details, sends the request through _send, parses the response as JSON, and verifies the result is an object. It returns that object or raises BrowserbaseError if the shape is wrong.

**Call relations**: Create, status, release, and download-list operations call this so they all get the same response checking. It sits between the higher-level API methods and the lower-level network helper.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the current API key attached. This is the one place that talks directly to Browserbase over the network.

**Data flow**: It receives an HTTP method, path, timeout, and request options. It reads the Browserbase API key from the credential store, adds it to the headers, sends the request with httpx, and returns the response if it succeeded. If Browserbase returns an error status, it raises BrowserbaseError with the status and response text.

**Call relations**: All other BrowserbaseApi methods eventually pass through this helper, either directly or through _json. Tests can inject a custom httpx transport here, while production uses the real network client.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Extracts a required string field from a Browserbase response. It prevents later code from continuing with missing or empty IDs and URLs.

**Data flow**: It receives a response dictionary and a field name, looks up the value, and returns it only if it is a non-empty string. Otherwise it raises BrowserbaseError explaining that Browserbase did not provide the expected field.

**Call relations**: Session creation, context creation, session reattachment checks, and download fetching use this small guard whenever they depend on a field from Browserbase. It keeps validation consistent across the file.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the listed size of a Browserbase download. This is a safety gate before the process pulls downloaded bytes into memory.

**Data flow**: It receives one download-list entry, checks that its size value is a real number and not a boolean, and returns it as an integer. If there is no trustworthy size, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.download calls this before fetching the binary download. It helps ensure the max-download limit is enforced before any potentially large file is read.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome DevTools Protocol endpoint for the active Browserbase session. The Chrome DevTools Protocol is the control channel UFO uses to drive the browser.

**Data flow**: It reads the lease's stored connection URL and wraps it in a CdpEndpoint object. The output is the endpoint object the browser engine can connect to.

**Call relations**: The wider browser engine calls this on the lease when it is ready to attach to Chrome. This function does not contact Browserbase; it simply packages the URL created during leasing or reattachment.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a reattachment token for this browser run. The token contains enough information to reconnect to the same Browserbase session and later clean it up.

**Data flow**: It reads the conversation ID, session ID, and context ID from the lease, joins them into a single formatted string, and returns that string. It does not change any state.

**Call relations**: The browser system can store this token and later pass it to BrowserbaseCdpProvider.reattach. Including all three IDs avoids guessing which context belongs to which browser run.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Copies a workspace file into the remote Browserbase session and returns the path Chrome should use there. This bridges the gap between local files and a browser running on Browserbase's machines.

**Data flow**: It receives a workspace path and a callback that reads the file bytes. It takes the file's base name, reads the bytes, rejects oversized files, avoids name collisions by adding a short path digest when needed, uploads the bytes, records the staged name, and returns the remote /tmp/.uploads path.

**Call relations**: The browser engine calls this when a page needs a file input filled. It hands the actual upload to BrowserbaseApi.upload, and its naming rules prevent two different local files with the same base name from accidentally becoming the same remote upload.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the one download directory name that Browserbase-hosted Chrome accepts. This matters because absolute local paths do not work for a remote browser.

**Data flow**: It takes no outside data and returns the literal string Browserbase expects for session downloads. It does not read or change state.

**Call relations**: The browser engine asks the lease where downloads should be directed. This lease answers with Browserbase's special remote directory rather than a path on the host machine.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed download from Browserbase session storage. It lets UFO get the actual file bytes after remote Chrome has saved them.

**Data flow**: It receives a download GUID, passes the current session ID and that GUID to the API download method, and returns the bytes it gets back. Any waiting, size checking, or errors happen inside the API method.

**Call relations**: The browser engine calls this when it wants the contents of a download. This function is the lease-level doorway that delegates the real Browserbase download lookup to BrowserbaseApi.download.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Closes the Browserbase lease by releasing the session, deleting the context, and removing the saved context record. This is the cleanup that keeps browsers, billing, and saved authenticated state from lingering after a run.

**Data flow**: It uses the lease's session ID, context ID, conversation ID, API object, and scoped store. It first asks Browserbase to release the session, then tries to delete the context, and finally removes the context entry from the local extension store even if earlier cleanup steps fail.

**Call relations**: The browser system calls this when the browser run is done. It calls BrowserbaseApi.release_session, BrowserbaseApi.delete_context, and the store delete operation in a nested cleanup sequence so a stale store entry does not poison future leases.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts a new Browserbase-backed browser lease for the current sandboxed turn. This is the main entry point UFO uses when it needs a remote Chrome session from Browserbase.

**Data flow**: It receives the sandbox session, reads the conversation ID from it, creates a Browserbase API wrapper and extension-scoped store, gets or creates the run's Browserbase context, creates a Browserbase session, and returns a BrowserbaseLease containing all of those pieces. If no sandbox is supplied, it raises an error because it cannot identify the browser run.

**Call relations**: Core calls this through the CDP provider interface when the configured backend is Browserbase. It calls _context to find the saved state bucket, then BrowserbaseApi.create_session, and finally hands back a lease the browser engine can use.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase context for a conversation, or creates and stores one if none exists. This is how a recovered browser session can keep the same browser state during one run.

**Data flow**: It builds a store key from the conversation ID, checks the extension store for an existing context ID, and returns it if present. If not, it asks Browserbase to create a new context, saves that ID in the store, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It uses ScopedStore.get and ScopedStore.put for the local record, and BrowserbaseApi.create_context only when a new Browserbase context is needed.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Rebuilds a lease around an existing Browserbase session from a saved token. This supports recovery when UFO wants to reconnect to a still-running hosted browser.

**Data flow**: It receives a token, parses out the conversation ID, session ID, and context ID, creates a Browserbase API wrapper, checks Browserbase for the live session's connection URL, and returns a new BrowserbaseLease for that same session. If the token is invalid or the session is gone, the flow raises SessionGone.

**Call relations**: Core calls this when it has a prior lease token and wants to resume. It uses _parse_token to understand the token and BrowserbaseApi.live_session to prove the remote browser still exists before handing back a lease.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a saved Browserbase lease token back into its three parts: conversation ID, session ID, and context ID. It treats malformed tokens as unusable sessions.

**Data flow**: It receives the token string, splits it at slashes, validates that the session and context parts exist, converts the conversation part into a UUID, and returns all three values. If parsing fails, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this before trying to contact Browserbase. By converting bad tokens into SessionGone, it lets the caller follow the same recovery path as it would for a genuinely expired remote session.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to UFO: its name, version, required Browserbase API key slot, and the CDP provider it offers. Without this, the rest of the system would not know how to configure or build the Browserbase provider.

**Data flow**: It creates a Manifest object containing a credential slot for the Browserbase API key and a provider specification for the Browserbase backend. The provider specification includes a small builder that turns credentials into a BrowserbaseCdpProvider.

**Call relations**: The extension loading system calls this to discover what the file contributes. The returned manifest connects deployment configuration, credentials, and the provider class used later during browser session leasing.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandbox Chrome Runtime
Starts or reuses a real Chrome instance inside the conversation sandbox and exposes it safely to the rest of the system.

### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and sandbox browser startup/reuse`

A sandbox is an isolated workspace for one conversation, like a small temporary computer. This file provides a Chrome browser inside that workspace, so web pages, uploaded files, and downloaded files all live in the same place the conversation is already using. Without this provider, the browser engine would not have a reliable Chrome DevTools Protocol endpoint to connect to. Chrome DevTools Protocol, or CDP, is the control channel tools use to drive Chrome.

The tricky part is that Chrome only accepts certain local-looking control requests. Hosted deployments reach the sandbox through a public per-port address, so this file creates a small proxy inside the sandbox. That proxy listens on one port, forwards traffic to Chrome on another port, and rewrites the HTTP Host header so Chrome believes the request is local. This is important because the browser control connection includes a WebSocket upgrade, not just a simple HTTP call.

When a lease is requested, the provider runs a bring-up script in the sandbox. The script checks whether Chrome and the proxy are already answering. If not, it kills any stale recorded process, starts fresh ones, waits for them to become ready, and reports useful logs if they fail. Chrome and the proxy stay alive across turns, so closing a lease does not shut them down.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 294–295)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already-prepared browser control endpoint for this lease. Other parts of the system use this endpoint to connect to Chrome and drive it.

**Data flow**: It reads the endpoint stored on the lease, does not change it, and gives it back to the caller. The endpoint contains the WebSocket address and any headers needed to reach the sandbox port.

**Call relations**: After SandboxChromeCdpProvider.lease has started or found Chrome and built the endpoint, callers ask this lease for the endpoint when they are ready to connect the browser engine.


##### `SandboxChromeCdpLease.token`  (lines 297–298)

```
async def token(self) -> str
```

**Purpose**: Returns a simple reattach token for the lease. In this provider, that token is just the endpoint URL.

**Data flow**: It reads the URL from the stored endpoint and returns it as text. It does not contact the sandbox or change any state.

**Call relations**: This belongs to the lease object returned by SandboxChromeCdpProvider.lease. Although it can provide a token, this provider does not actually support reconnecting from that token alone, because the live sandbox must be supplied again through a fresh lease.


##### `SandboxChromeCdpLease.place_file`  (lines 300–304)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells the caller that no file copying is needed before Chrome can use a file. Because Chrome runs inside the same sandbox as the file, the existing path is already valid.

**Data flow**: It receives a sandbox file path and a read callback for file bytes. It ignores the byte-reading callback, leaves the file where it is, and returns the same path.

**Call relations**: Browser code can call this when it wants to make a file available to Chrome. Unlike a local browser that might need a file transferred to another machine, this lease simply points Chrome at the sandbox path it can already see.


##### `SandboxChromeCdpLease.download_dir`  (lines 306–309)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the folder inside the sandbox where Chrome writes downloaded files. Callers use this to know where browser downloads will appear.

**Data flow**: It returns the fixed download directory path used by the sandbox Chrome setup. It does not inspect the sandbox or create anything itself; the bring-up script prepares the directory.

**Call relations**: This is used by browser workflow code that needs to allow, name, or later collect downloads. It lines up with the Chrome launch configuration created during SandboxChromeCdpProvider.lease.


##### `SandboxChromeCdpLease.fetch_download`  (lines 311–335)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed browser download back out of the sandbox and returns its bytes. It also protects the main process from unexpectedly huge downloads.

**Data flow**: It receives a download identifier, turns it into a safe filename piece, checks the file size inside the sandbox, refuses files above the configured limit, then runs a sandbox command to base64-encode the file. Base64 is text-safe encoding for binary data. The function decodes that text back into bytes in a worker thread and returns the bytes. If the file cannot be read, it raises an error.

**Call relations**: After Chrome downloads a file into the sandbox download directory, higher-level browser code calls this lease method to retrieve it. The function uses shlex.quote to safely place the identifier into a shell command, asks the SandboxSession to run commands in the sandbox, and uses asyncio.to_thread so CPU-heavy decoding does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 337–338)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease without shutting down Chrome. This is intentional because the browser and proxy are meant to live for the whole conversation sandbox, not just one turn.

**Data flow**: It receives no useful input, performs no cleanup, and returns nothing. The sandbox processes remain running for later reuse.

**Call relations**: Callers may close leases as part of normal turn cleanup. For this provider, cleanup is a no-op because the next SandboxChromeCdpProvider.lease call should be able to reuse the same in-sandbox Chrome.


##### `SandboxChromeCdpProvider.lease`  (lines 350–366)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a usable browser lease for the current turn's sandbox. It starts Chrome and the proxy if needed, verifies they answer, then returns the endpoint the browser engine should connect to.

**Data flow**: It takes a SandboxSession. If none is provided, it raises an error because this provider cannot work without a sandbox. It runs the bring-up script inside the sandbox, checks whether it succeeded, asks the sandbox how to dial the proxy port from outside, converts the browser's local WebSocket URL into the public sandbox URL, carries over any required connection headers, and returns a SandboxChromeCdpLease.

**Call relations**: This is the main entry point used by the core browser provider system when a turn needs Chrome. It calls SandboxSession.bash to run the startup script, SandboxSession.dial to get the externally reachable port information, _ws_path to keep only the safe WebSocket path from Chrome's local URL, then builds a CdpEndpoint and wraps it in SandboxChromeCdpLease.

*Call graph*: calls 3 internal fn (bash, dial, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 368–369)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that an old browser session cannot be reattached from a saved token alone. A fresh lease must be made with a live sandbox instead.

**Data flow**: It receives a token string, does not use it to reconnect, and raises SessionGone with that token. Nothing in the sandbox is changed.

**Call relations**: If recovery logic tries to reconnect through this provider, this function stops that path and signals that the caller should establish a new lease. That matches the design: the endpoint depends on the current sandbox's live port information, not just a stored URL.

*Call graph*: 1 external calls (__init__).


##### `_ws_path`  (lines 372–377)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part from Chrome's local WebSocket debugger URL and verifies that it really points to the sandbox-local Chrome port. This prevents the provider from blindly trusting an unexpected address.

**Data flow**: It receives a WebSocket URL as text, trims surrounding whitespace, and checks for the allowed local prefixes. If the URL starts with one of them, it removes that prefix and returns the remaining path. If not, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease calls this after the sandbox bring-up script prints Chrome's debugger URL. The provider then combines the returned path with the sandbox carrier's public host, scheme, and headers to make the final endpoint.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 380–389)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It says that the extension provides a CDP backend named sandbox_chrome and knows how to build its provider object.

**Data flow**: It creates and returns a Manifest containing the extension name, version, and one CDP provider specification. The build function inside that specification constructs a SandboxChromeCdpProvider when the system selects this backend.

**Call relations**: The extension loading system calls this to discover what this file offers. The returned Manifest includes a CdpProviderSpec, which connects the configured backend name to SandboxChromeCdpProvider.

*Call graph*: 2 external calls (__init__, __init__).

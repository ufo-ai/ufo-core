# Hosted and sandbox browser providers  `stage-10.2.5`

This stage is behind-the-scenes support for web browsing work. Instead of always using a browser installed on the same machine, it gives the system other ways to “rent” a browser for a turn. A lease means a temporary browser session that is opened, used, and then cleaned up.

The Browser Use extension adds two tools, browser_task and wide_browse. They let the rest of the system request web automation in the usual way, but the actual browsing is done by Browser Use’s hosted agent, an outside service.

The Browserbase extension connects to Browserbase, which provides a remote Chrome browser. For one browser turn, it creates the remote session, transfers needed files into or out of it, and shuts it down afterward.

The sandbox Chrome extension starts a private headless Chrome, meaning Chrome without a visible window, inside the turn’s sandbox. It connects using Chrome’s DevTools protocol, a control channel for driving the browser, and carefully tears it down if the lease ends or recovery fails.

## Files in this stage

### Alternate browser providers
Hosted and sandboxed browser backends that let the system lease web automation outside the default local browser path.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This extension is an adapter between UFO’s tool system and Browser Use’s cloud API. In plain terms, it lets UFO say, “please browse the web and do this task,” then sends that request to Browser Use, waits for the remote browser run to finish, and returns the result. Without this file, deployments that want Browser Use’s hosted browser agent would not have the standard `browser_task` and `wide_browse` tools available.

The main worker is `HostedRun`. It creates a remote run, polls its status until it finishes or times out, reads the final result, and optionally copies output files back into the workspace. It is careful with credentials: the Browser Use API key is read on the host side and is not placed inside the sandbox. Downloaded output files are fetched from separate pre-signed URLs without sending that API key.

The file exposes two tools. `browser_task` runs one full browser session from a starting URL and saves any small output files. `wide_browse` reads a list of sites or entities from a workspace file, runs many smaller browser jobs in parallel, and writes all rows to `wide_browse.json`. The extension also defines its manifest, which tells UFO the tool names, descriptions, prompt text, and required credential slot.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one Browser Use job from beginning to end. It checks the task size, gets the API key, starts or reuses a remote run, waits for completion, cancels on timeout when needed, collects output files, and returns a single summary object.

**Data flow**: It receives a tool context, a task description, a timeout, and optionally a deduplication key used to avoid paying for the same run twice. It reads the extension store and Browser Use credential, sends API requests, watches the remote run, and may write downloaded files into the sandbox workspace. It returns a `RunOutcome` containing the final status, result or error text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the top-level method used by both tool handlers. `_browser_task` uses it for one careful browsing session, while `_wide_browse.visit` uses it for each item in a batch. Inside the flow it hands work to `_start`, `_watch`, `_status`, `_json`, and `_collect` so each part of the remote-run lifecycle stays separate.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Starts a Browser Use run, or reconnects to a previously recorded one when a deduplication key exists. This prevents repeated tool retries from unnecessarily creating duplicate paid browser sessions.

**Data flow**: It receives an HTTP client, the extension’s small persistent store, the task text, and an optional deduplication key. If the store already has a run handle for that key, it validates and returns it. Otherwise it posts the task, model, cost limit, and proxy country to Browser Use, extracts the run ID and workspace ID, saves them when appropriate, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this near the beginning of every run. `_start` depends on `_json` to make sure the API response is readable and `_text` to pull out required string fields before the rest of the run can be watched.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Keeps checking a remote run until Browser Use says it has reached a final state. It is the polling loop for the hosted browser session.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current status, waits a few seconds when the run is still active, and eventually returns the terminal status such as completed, failed, stopped, or cancelled.

**Call relations**: `HostedRun.execute` calls this inside a timeout block after the run has been started. `_watch` delegates each individual status read to `_status`, which keeps the API-reading details in one place.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is also used just before cancellation, so a run that finished at the last moment is not mistakenly cancelled.

**Data flow**: It receives an HTTP client and run ID, sends a GET request to the run status endpoint, parses the response as JSON, extracts the `status` string, and returns that string. If the response is malformed, it raises a Browser Use-specific error instead of letting a raw parsing failure leak out.

**Call relations**: `_watch` calls this repeatedly during normal polling. `HostedRun.execute` also calls it directly in the timeout path to make one final check before sending a cancel request.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies a completed run’s output files into the workspace when this kind of run is supposed to save files. It also reports files that were too large, missing a usable download URL, or beyond the listing limit.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If saving outputs is disabled, it returns empty results. Otherwise it asks Browser Use for a bounded file list, checks each file’s path and size, rejects paths that would escape the workspace, downloads safe files, writes them into the sandbox, and returns three things: saved files, skipped files, and whether more files exist.

**Call relations**: `HostedRun.execute` calls this after reading the final run summary. `_collect` uses `_json` for the file listing, `_download` for each file body, and the shared sandbox containment check to make sure vendor-provided paths cannot write outside the workspace.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a Browser Use-provided URL without sending the Browser Use API key. It protects the workspace from unsafe plaintext download links by requiring HTTPS.

**Data flow**: It receives a URL string. It first checks that the URL starts with `https://`, then creates a plain HTTP client with no API credential, fetches the bytes, and returns the file content. If the URL is not HTTPS or the download fails, it raises a `BrowserUseError`.

**Call relations**: `_collect` calls this for each listed output file that is small enough and has a usable URL. This keeps file downloading separate from file listing and workspace writing.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a plain dictionary, while giving clear errors for bad status codes, non-JSON bodies, or JSON that is not an object. It is the shared gatekeeper for API responses.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises an error with the endpoint path, status code, and body text. Otherwise it parses the body as JSON, checks that the result is a JSON object, converts keys to strings, and returns the dictionary.

**Call relations**: Most Browser Use API calls pass through this helper: starting runs, checking status, collecting files, cancelling runs, and reading run summaries. By centralizing response checks, the rest of the file can assume it is working with a sensible dictionary.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts a required string field from an API response dictionary. It turns a missing or wrongly typed field into a clear Browser Use error.

**Data flow**: It receives a dictionary and a field name. It reads that field, checks that it is a string, and returns it. If the field is missing or not text, it raises `BrowserUseError` with the whole response included for diagnosis.

**Call relations**: `_start` uses this to read the new run ID and workspace ID. `_status` uses it to read the run status. It sits just after `_json` in the response-validation chain.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 344–378)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the `browser_task` tool: one hosted browser session that starts at a URL, follows the user’s detailed instructions, and returns the result plus any fetched output files.

**Data flow**: It receives the tool context and validated browser-task arguments. It builds one task string from the starting URL and instructions, creates a `HostedRun` configured for the more capable model and a higher cost limit, waits up to the requested number of minutes, and converts the outcome into a `ToolResult`. On timeout it returns an error message; otherwise it returns JSON with the result, saved file paths, skipped files, and whether more files existed.

**Call relations**: The tool registry calls this when a user or agent invokes `browser_task`. It relies on `HostedRun.execute` for all remote Browser Use work, then packages that lower-level outcome into the tool response format expected by UFO.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 381–388)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a workspace file through the sandbox shell, safely quoting the path first. It exists because some tool inputs are file paths supplied by the model, and those paths must not be allowed to run shell commands by accident.

**Data flow**: It receives a tool context and a path string. It quotes the path for a real shell command, runs `cat` in the sandbox, and returns the file’s standard output as text. If the command fails, it raises a `ValueError` using the shell error message when available.

**Call relations**: `_read_lines` uses this to read the entity list for batch browsing. `_wide_browse` also uses it directly to read the JSON schema file that should be included in each browser task.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 391–399)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a text file as a clean list of unique, non-empty lines. For `wide_browse`, those lines are the URLs or site names to visit.

**Data flow**: It receives a tool context and a file path. It reads the file with `_read_file`, splits it into lines, trims whitespace, ignores blank lines, removes duplicates while preserving first-seen order, and returns the resulting list of strings.

**Call relations**: `_wide_browse` calls this at the start of a batch. It builds the set of entities that will later be handed one by one to the nested `visit` worker.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 402–448)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the `wide_browse` tool: it runs the same kind of browsing-and-extraction prompt across many URLs or site names in parallel, then writes all results to a JSON file.

**Data flow**: It receives the tool context and validated batch-browsing arguments. It reads and deduplicates the entities file, enforces the maximum entity count, reads the output schema file, creates a shared `HostedRun` configured for cheaper batch jobs, and runs several visits at a time using a semaphore, which is a simple traffic light that limits concurrency. It gathers successful rows and per-entity errors, writes them to `wide_browse.json`, and returns JSON containing the rows and output filename.

**Call relations**: The tool registry calls this when `wide_browse` is invoked. It uses `_read_lines` and `_read_file` for local workspace inputs, then uses its nested `_wide_browse.visit` worker for each entity so one failed visit does not discard the rest of the batch.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 418–433)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser job for one entity inside a `wide_browse` batch. It is designed so an individual site failure becomes one row in the results instead of stopping the whole batch.

**Data flow**: It receives a single entity string from the outer `_wide_browse` function. It waits for permission from the semaphore, fills `{entity}` into the prompt template, appends the output schema text when present, runs Browser Use through the shared `HostedRun`, and returns a dictionary with the entity, status, and result text.

**Call relations**: `_wide_browse` launches this nested worker once per entity and collects all workers with `asyncio.gather`. Each worker calls `HostedRun.execute` with its own deduplication key so retries can reconnect to the matching remote run.


##### `manifest`  (lines 471–486)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to UFO: its name, version, tools, prompt text, and required Browser Use API key. This is how the wider system discovers and loads the extension.

**Data flow**: It takes no input. It builds a `Manifest` containing the two tool definitions, one browser prompt section read from the companion markdown file, and one credential slot for the Browser Use API key. It returns that manifest to the extension loader.

**Call relations**: The extension loading process calls this at startup. The returned manifest is what registers `browser_task` and `wide_browse` and tells the host that it must provide the `browser_use_api_key` credential.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session startup, active browser run, recovery reattach, and teardown`

A normal local browser can read files from local paths and write downloads to local folders. Browserbase is different: Chrome runs on Browserbase's machines, so this project must talk to Browserbase's web API to create sessions, upload files, fetch downloads, and release resources. This file is the bridge that makes that remote browser look like a regular browser lease to the rest of the system.

The main flow is: when a browser turn starts, `BrowserbaseCdpProvider.lease` creates or reuses a Browserbase Context for that conversation. A Context is like a small backpack of browser state: cookies, logins, and local storage. It then creates a hosted browser session tied to that Context and returns a `BrowserbaseLease`, which is the system's handle for using that browser.

During the run, the lease can provide the Chrome DevTools Protocol endpoint, which is the remote control URL for Chrome. It can also upload local workspace files into Browserbase before a page selects them, and it can fetch completed downloads back as bytes. Both uploads and downloads are size-limited so an untrusted web page cannot force this process to load huge files into memory.

When the run ends, the lease releases the Browserbase session and deletes the Context, so paid remote resources and authenticated browser state do not outlive the browser subagent. If recovery needs to reconnect, the token records exactly which conversation, session, and Context belong together.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new hosted Browserbase browser session using an existing Browserbase Context. The Context is attached with persistence turned on, so cookies and local storage can carry through a recovery inside the same browser run.

**Data flow**: It receives a Context id. It sends a POST request to Browserbase asking for a session with that Context and a fixed timeout. From Browserbase's JSON reply, it extracts the session id and the Chrome connection URL, then returns both.

**Call relations**: This is part of the lower-level API wrapper used when `BrowserbaseCdpProvider.lease` starts a fresh browser session. It relies on `_json` to make the HTTP request and `_field` to ensure Browserbase actually returned the required string fields.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still usable and returns its connection URL if it is. This is used when the system tries to reconnect to a browser after recovery.

**Data flow**: It receives a session id. It asks Browserbase for that session's current information, checks that the status is still live, and reads the connection URL. If the session is gone or no longer running, it raises `SessionGone` instead of pretending the browser can still be used.

**Call relations**: This supports the reattach path in `BrowserbaseCdpProvider.reattach`. It calls `_json` for the Browserbase lookup, `_field` to read required fields, and signals `SessionGone` when the caller should stop trying to reuse that old session.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to release a hosted browser session. This stops the remote browser from continuing to run or bill after the browser turn is done.

**Data flow**: It receives a session id. It sends Browserbase a status update that requests release. It does not return a value; the important effect is the remote session being told to shut down.

**Call relations**: This is called during `BrowserbaseLease.aclose`, before deleting the Context. It uses `_json` so failed Browserbase responses become clear errors rather than silent cleanup failures.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a new Browserbase Context, which stores browser state such as cookies and local storage for one browser run. This lets a reminted session inside the same run keep the login state it already earned.

**Data flow**: It sends an empty POST request to Browserbase's Contexts endpoint. It reads the new Context id from the JSON response and returns that id.

**Call relations**: This is called by `BrowserbaseCdpProvider._context` only when there is no stored Context id for the current conversation. It depends on `_json` for the request and `_field` for safe extraction of the id.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context when the browser run is over. This prevents authenticated browser state from living longer than the subagent that created it.

**Data flow**: It receives a Context id. It sends a DELETE request to Browserbase for that Context. It returns nothing, but the remote stored browser state should be removed.

**Call relations**: This is used by `BrowserbaseLease.aclose` after the session release attempt. It calls `_send` directly because it only needs the HTTP request to succeed, not a JSON body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed Browserbase download. It waits briefly for Browserbase's storage listing to catch up, checks the reported size first, and refuses oversized or unmeasured files.

**Data flow**: It receives a session id and a download guid, which is the name the browser used for the stored download. It repeatedly asks Browserbase for that session's download list, searches for a matching filename, verifies the listed size, then downloads the file bytes. It returns those bytes, or raises an error if the file never appears or is too large.

**Call relations**: This is the API-side work behind `BrowserbaseLease.fetch_download`. It uses `_json` to read the download list, `_size` to enforce the memory safety check, `_field` to read the stored download id, `_send` to fetch the raw bytes, and `asyncio.sleep` between retry attempts.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a file into a Browserbase session so the remote Chrome browser can use it. This is needed because a hosted browser cannot access this process's local filesystem path.

**Data flow**: It receives a session id, the remote filename to use, and the file bytes. It sends those bytes as a multipart file upload to Browserbase. It returns nothing; after success, Browserbase has staged the file inside the session.

**Call relations**: This is called by `BrowserbaseLease.place_file` after that method has read and checked the local file. It uses `_send` with a longer timeout because uploads can take more time than ordinary API calls.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request and insists that the reply is a JSON object. It gives higher-level methods a safe dictionary to read from.

**Data flow**: It receives an HTTP method, API path, and request options. It delegates the actual network request to `_send`, parses the response as JSON, checks that the parsed body is a dictionary-like object, and returns it. If Browserbase returns some other shape, it raises `BrowserbaseError`.

**Call relations**: This is the shared helper behind the session, Context, release, status, and download-list calls. It sits between higher-level Browserbase actions and the raw HTTP helper `_send`.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the API key attached. It is the single place where this file talks over the network to Browserbase.

**Data flow**: It receives an HTTP method, API path, timeout, and optional request details such as JSON, files, or headers. It reads the Browserbase API key from the credential slot, builds request headers, sends the request with `httpx`, and returns the HTTP response. If Browserbase returns an error status, it raises `BrowserbaseError` with the status and response text.

**Call relations**: All Browserbase web calls pass through this method, either directly or through `_json`. It is also where tests can inject a custom HTTP transport instead of making real network calls.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Safely reads a required string field from a Browserbase JSON response. It turns a missing or empty field into a clear Browserbase-specific error.

**Data flow**: It receives a response dictionary and a field name. It looks up the value, checks that it is a non-empty string, and returns it. If the value is absent or not usable, it raises `BrowserbaseError`.

**Call relations**: This helper is used by API methods that need important Browserbase ids or URLs, such as session ids, connection URLs, Context ids, and stored download ids. It keeps those methods from continuing with broken data.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Safely reads the reported size of a listed download. This matters because the code must know a download is small enough before pulling all of it into memory.

**Data flow**: It receives one download listing entry. It reads the `size` value, rejects missing, boolean, or non-number values, converts a valid number to an integer, and returns it.

**Call relations**: This is used only by `BrowserbaseApi.download` before it fetches the actual file bytes. It is the guardrail that prevents unmeasured downloads from being accepted.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the remote Chrome control endpoint for this lease. The rest of the browser engine uses this URL to connect to the hosted browser.

**Data flow**: It reads the lease's stored Browserbase connection URL. It wraps that URL in a `CdpEndpoint` object and returns it.

**Call relations**: This is part of the lease interface expected by the core browser system. It does not call Browserbase; it simply hands back the connection information created earlier by `BrowserbaseApi.create_session` or checked by `BrowserbaseApi.live_session`.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a reconnect token for this browser lease. The token records the conversation id, Browserbase session id, and Context id so recovery can find the exact same remote browser run.

**Data flow**: It reads the lease's conversation id, session id, and Context id. It formats them into one slash-separated string and returns that string.

**Call relations**: The reattach flow later gives this token to `BrowserbaseCdpProvider.reattach`, which parses it with `_parse_token`. The token avoids guessing which Context or session belongs to a recovered run.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Uploads a workspace file into the remote Browserbase session and returns the path Chrome should use there. It solves the problem that Browserbase's hosted Chrome cannot read this machine's local file path.

**Data flow**: It receives the original workspace path and a callback that reads the file bytes. It extracts the base filename, reads the bytes, rejects files over the upload limit, chooses a safe remote name, and uploads the bytes through `BrowserbaseApi.upload`. It records which remote names have already been staged, then returns a `/tmp/.uploads/...` path for the browser to use.

**Call relations**: This is called by the browser engine when a web page needs a file input populated. It uses `PurePosixPath` to find the filename and `sha256` to add a short digest when two different paths would otherwise use the same filename.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name Browserbase's hosted Chrome accepts. It deliberately returns `downloads` instead of an absolute local path.

**Data flow**: It takes no outside data. It returns the literal string used by Browserbase to route downloads into session storage.

**Call relations**: The browser engine asks the lease for a download directory before allowing downloads. This method supplies the Browserbase-specific value that makes later `fetch_download` calls possible.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed download from Browserbase session storage. It hides the Browserbase download API behind the lease interface the browser engine expects.

**Data flow**: It receives a download guid. It passes the lease's session id and that guid to `BrowserbaseApi.download`, waits for the bytes, and returns them.

**Call relations**: This is called after Chrome has downloaded a file and the system wants to bring it back into this process. The real polling, size checking, and byte fetching are delegated to `BrowserbaseApi.download`.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser resources for this lease. It releases the Browserbase session, deletes the Context, and removes the stored Context id for the conversation.

**Data flow**: It starts with the session id, Context id, and stored conversation key held by the lease. It asks Browserbase to release the session, then tries to delete the Context, and finally deletes the Context record from the extension store. It returns nothing, but remote compute, remote browser state, and local bookkeeping should all be gone afterward.

**Call relations**: This is called when the browser run is ending. The cleanup is nested with `finally` blocks so later cleanup still happens even if an earlier Browserbase call fails.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Starts a fresh Browserbase-backed browser lease for the current browser turn. It is the main entry used by the core system when it needs a remote Chrome session.

**Data flow**: It receives the current sandbox, which contains the conversation id for this browser run. It creates a Browserbase API wrapper and an extension-scoped store, gets or creates a Context for that conversation, creates a Browserbase session, and returns a `BrowserbaseLease` containing all the pieces needed to use and later clean up the session.

**Call relations**: The core CDP provider system calls this when `[browser] cdp_provider` is set to Browserbase. It calls `_context` for Context lookup or creation, then uses `BrowserbaseApi.create_session` through the API object and packages the result as a lease.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context id for a conversation, or creates and stores one if none exists yet. This lets a browser run survive session reminting without losing cookies and local storage.

**Data flow**: It receives a Browserbase API wrapper, a scoped store, and a conversation id. It builds a store key, checks whether a non-empty Context id is already stored, and returns it if so. Otherwise, it creates a new Context through Browserbase, stores the id under that key, and returns the new id.

**Call relations**: This is called by `BrowserbaseCdpProvider.lease` before creating a session. It reads and writes the `ScopedStore` and calls `BrowserbaseApi.create_context` only when storage does not already have a Context id.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Reconnects to an existing Browserbase session using a token from an earlier lease. This is used during recovery when the remote session may still be alive.

**Data flow**: It receives a token. It parses the token into conversation id, session id, and Context id, creates a Browserbase API wrapper, checks that the session is still live and gets its connection URL, then returns a new `BrowserbaseLease` pointed at that existing session.

**Call relations**: The core browser system calls this when it wants to recover an earlier browser lease. It uses `_parse_token` for safe token decoding and `BrowserbaseApi.live_session` through the API object to confirm Browserbase still has the session.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a reconnect token back into the ids needed to reattach to a Browserbase session. If the token is malformed, it treats the old session as gone.

**Data flow**: It receives a slash-separated token string. It splits out the conversation id, session id, and Context id, converts the conversation id into a UUID, and returns all three values. If any required part is missing or invalid, it raises `SessionGone`.

**Call relations**: This helper is used by `BrowserbaseCdpProvider.reattach`. By raising `SessionGone` on bad tokens, it tells the caller to stop trying to reconnect with unusable information.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It tells the system the extension name and version, the credential slot it needs, and how to build the Browserbase CDP provider.

**Data flow**: It takes no input. It creates a `Manifest` containing one Browserbase API key credential slot and one CDP provider spec for the `browserbase` backend, then returns that manifest.

**Call relations**: The extension loading system calls this to discover what the file provides. The provider spec's build function creates `BrowserbaseCdpProvider` when the system is configured to use Browserbase.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `browser lease startup, recovery, active browser session, and cleanup`

This file provides the "sandbox_chrome" browser provider. Its job is to give each hosted turn its own isolated browser setup: Chrome, a browser profile, a downloads folder, a DevTools proxy, and a small egress bridge for web traffic. Without this file, browser automation would either have no Chrome to talk to inside the sandbox, or different turns could collide over ports, profiles, downloads, or cleanup.

The key idea is a lease, like borrowing a private workbench. When a turn asks for a browser, the provider uses the turn's durable id to claim a numbered slot. That slot determines three private ports: one for Chrome, one for the DevTools proxy, and one for the egress bridge. The slot is recorded with files and symlinks so a worker crash can be recovered later.

Chrome is started headlessly in the sandbox. A DevTools proxy sits in front of it because Chrome rejects some non-localhost Host headers; the proxy rewrites that header, then passes WebSocket traffic through unchanged. The egress bridge is separate: it lets Chrome use the sandbox's authenticated outbound proxy even though Chromium itself does not understand credentials embedded in the usual proxy environment variables.

The file is careful about timeouts and failure messages. If Chrome or a proxy dies during startup, it reads useful log tails from inside the sandbox instead of returning only a vague timeout. When the lease closes, only that lease's processes, files, and port slot are removed.

#### Function details

##### `BrowserStack.root`  (lines 89–90)

```
def root(self) -> str
```

**Purpose**: Returns the sandbox directory that belongs to this browser lease. This is the top folder where the lease keeps its browser files, logs, profile, and downloads.

**Data flow**: It reads the stack's lease id and combines it with the shared browser leases directory. The result is a path string inside the sandbox.

**Call relations**: Other path helpers build on this root path whenever startup, cleanup, download reading, or logging needs to know where this lease's files live.


##### `BrowserStack.chrome_log`  (lines 93–94)

```
def chrome_log(self) -> str
```

**Purpose**: Gives the path to Chrome's log file for this lease. This is where Chrome's output is captured so startup failures can explain what went wrong.

**Data flow**: It starts with the stack root and appends the Chromium log filename. It returns that full sandbox path.

**Call relations**: The browser startup command writes Chrome output here, and failure-reporting code reads it when startup does not finish cleanly.


##### `BrowserStack.chrome_pid`  (lines 97–98)

```
def chrome_pid(self) -> str
```

**Purpose**: Gives the path to the file that records Chrome's process id. A process id is the operating system's number for a running program.

**Data flow**: It combines the stack root with the Chromium pid filename and returns that path.

**Call relations**: Startup writes this file after launching Chrome, and cleanup reads it so it can stop the correct process for this lease.


##### `BrowserStack.chrome_profile`  (lines 101–102)

```
def chrome_profile(self) -> str
```

**Purpose**: Returns the directory Chrome should use as its browser profile. This keeps cookies, cache, and other browser state private to one lease.

**Data flow**: It combines the stack root with a profile folder name. The output is a sandbox path passed to Chrome.

**Call relations**: The browser startup command uses this path in Chrome's command-line options so sibling turns do not share browser state.


##### `BrowserStack.download_dir`  (lines 105–106)

```
def download_dir(self) -> str
```

**Purpose**: Returns the folder where this Chrome should save downloaded files. This gives the lease a known place to retrieve downloads later.

**Data flow**: It combines the stack root with a downloads folder name and returns the path.

**Call relations**: Startup creates this directory, Chrome writes downloads there, and the lease's download-reading methods return or read from it.


##### `BrowserStack.proxy_log`  (lines 109–110)

```
def proxy_log(self) -> str
```

**Purpose**: Gives the path to the DevTools proxy's log file. This helps diagnose failures in the small proxy that sits in front of Chrome.

**Data flow**: It combines the stack root with the CDP proxy log filename. The result is a sandbox path.

**Call relations**: The proxy startup command writes here, and failure reporting tails this file along with Chrome's log.


##### `BrowserStack.proxy_pid`  (lines 113–114)

```
def proxy_pid(self) -> str
```

**Purpose**: Gives the path to the file that records the DevTools proxy process id. Cleanup uses it to stop the proxy that belongs to this lease.

**Data flow**: It combines the stack root with the CDP proxy pid filename and returns that path.

**Call relations**: Startup writes this pid file after launching the proxy, and stack cleanup reads it before sending stop signals.


##### `BrowserStack.proxy_script`  (lines 117–118)

```
def proxy_script(self) -> str
```

**Purpose**: Returns the sandbox path where the generated DevTools proxy Python script is written. The script rewrites Chrome's Host header issue and then carries traffic through.

**Data flow**: It combines the stack root with the proxy script filename. The result is used as a file path in generated shell commands.

**Call relations**: The browser startup command writes the proxy program to this path before launching it.


##### `BrowserStack.bridge_script`  (lines 121–122)

```
def bridge_script(self) -> str
```

**Purpose**: Returns the sandbox path where the generated egress bridge Python script is written. The bridge lets Chrome make authenticated outbound web connections.

**Data flow**: It combines the stack root with the bridge script filename and returns that path.

**Call relations**: The bridge startup helper writes the bridge program here, then runs it as a detached sandbox task.


##### `BrowserStack.task_base`  (lines 125–126)

```
def task_base(self) -> str
```

**Purpose**: Returns the base path used for the detached egress bridge task's bookkeeping files. Those files record things like pid, log, lock, and exit status.

**Data flow**: It combines the stack root with a task name prefix. Other code adds suffixes such as .pid or .log.

**Call relations**: Bridge startup, bridge replacement, stack cleanup, and failure reporting all use this shared base so they refer to the same task files.


##### `BrowserStack.lock_path`  (lines 129–130)

```
def lock_path(self) -> str
```

**Purpose**: Returns the lock path for the slot this stack owns. The lock is a small filesystem marker that prevents two leases from taking the same ports.

**Data flow**: It reads the stack's slot number and combines it with the shared slot-lock directory. The output is a sandbox path.

**Call relations**: Allocation creates this lock, recovery verifies it, and cleanup removes it only if it still belongs to this lease.


##### `_stack`  (lines 133–145)

```
def _stack(lease_id: str, slot: int) -> BrowserStack
```

**Purpose**: Builds a trusted BrowserStack object from a lease id and slot number. It also rejects malformed values so later commands do not operate on unsafe or nonsensical paths and ports.

**Data flow**: It receives a lease id and slot. It checks that the id looks like a 32-character hexadecimal turn id and that the slot is in range, then calculates the three ports for that slot and returns a BrowserStack.

**Call relations**: The provider uses this after allocation finds a slot, and token recovery uses it after reading a saved token. It is the central place that turns compact lease facts into all paths and ports.

*Call graph*: called by 2 (_lease, _stack_from_token); 1 external calls (__init__).


##### `_assignments`  (lines 681–682)

```
def _assignments(values: dict[str, object]) -> str
```

**Purpose**: Turns a small dictionary of settings into Python assignment lines. This is used to safely inject values into the short Python programs that are run inside the sandbox.

**Data flow**: It receives names and values, formats each value using Python's literal form, joins the lines, and returns one string of setup code.

**Call relations**: All command-building helpers call this before embedding configuration into sandbox scripts, so those scripts receive consistent values for ports, paths, deadlines, and ids.

*Call graph*: called by 8 (_abandon_allocation_command, _allocate_command, _bridge_down_command, _browser_up_command, _egress_bridge_up_command, _find_allocation_command, _resolve_command, _stack_down_command).


##### `_allocate_command`  (lines 685–731)

```
def _allocate_command(lease_id: str) -> str
```

**Purpose**: Creates the shell command that claims a free browser stack slot inside the sandbox. A slot reserves a small group of ports and a root directory for one lease.

**Data flow**: It receives a lease id, embeds allocation settings, and returns a Python heredoc command. When run in the sandbox, that command creates directories, tests ports, writes the chosen slot, creates a lock symlink, and prints the slot number.

**Call relations**: The provider's lease startup runs this command first. It depends on _assignments for configuration text and hands the actual allocation work to the sandbox shell.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_lease).


##### `_find_allocation_command`  (lines 734–769)

```
def _find_allocation_command(lease_id: str) -> str
```

**Purpose**: Creates the shell command that checks whether a lease already owns a completed slot allocation. This supports crash recovery and confirms that allocation really finished.

**Data flow**: It receives a lease id and returns a Python heredoc command. When run, that command waits briefly for the lease's slot file, ready marker, and matching lock, then prints the slot if all are valid.

**Call relations**: Fresh lease startup runs this after attempting allocation, and reattach runs it to prove the saved token still matches a live slot.

*Call graph*: calls 1 internal fn (_assignments); called by 2 (_lease, reattach).


##### `_abandon_allocation_command`  (lines 772–792)

```
def _abandon_allocation_command(lease_id: str) -> str
```

**Purpose**: Creates the shell command that removes an incomplete or unusable slot allocation. This prevents a failed startup from leaving a slot permanently stuck.

**Data flow**: It receives a lease id, embeds the relevant directories, and returns a Python heredoc command. When run, it removes any locks pointing to that lease and deletes the lease root directory.

**Call relations**: _abandon_allocation runs this command when allocation validation fails during provider startup.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_abandon_allocation).


##### `_egress_bridge_up_command`  (lines 795–801)

```
def _egress_bridge_up_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that writes and runs the egress bridge inside the sandbox. The bridge converts Chrome's proxy traffic into authenticated CONNECT tunnels through the sandbox proxy.

**Data flow**: It receives a BrowserStack, embeds the bridge's listen host and port, writes the bridge Python program to the stack's script path, and returns a command that execs it.

**Call relations**: _start_bridge uses this command when beginning a new lease and when reattaching to an existing Chrome stack.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_start_bridge).


##### `_browser_up_command`  (lines 804–854)

```
def _browser_up_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that starts the full browser side of the stack: Chrome and the DevTools proxy. It also waits for each piece to become reachable before reporting the browser WebSocket URL.

**Data flow**: It receives a BrowserStack, embeds all paths, ports, Chrome arguments, wait budgets, and proxy settings, then returns a sandbox command. When run, the command writes the proxy script, waits for the egress bridge, launches Chrome, launches the DevTools proxy, and prints the proxied WebSocket debugger URL.

**Call relations**: The provider's main _lease method runs this after starting the bridge. If it fails, _bring_up_failure reads the logs named by the same stack.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_lease).


##### `_stack_down_command`  (lines 857–874)

```
def _stack_down_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the cleanup command for an entire browser stack. It stops the bridge supervisor, Chrome, and DevTools proxy, removes task files, deletes the lease directory, and frees the slot lock.

**Data flow**: It receives a BrowserStack, embeds paths, ids, and timing settings, and returns a Python heredoc command. When run, that command verifies slot ownership before killing recorded processes and deleting this lease's files.

**Call relations**: _stop_stack runs this command when a lease closes, when startup fails, or when recovery decides a stack is no longer usable.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_stop_stack).


##### `_bridge_down_command`  (lines 877–891)

```
def _bridge_down_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that stops only the egress bridge task. This is used during reattach so the bridge can be replaced while leaving Chrome and the DevTools proxy intact.

**Data flow**: It receives a BrowserStack, embeds the lease id, lock path, task base, and timeout settings, and returns a Python heredoc command. When run, the command verifies ownership, stops the matching bridge process, and removes bridge task bookkeeping files.

**Call relations**: _stop_bridge runs this during reattach before _start_bridge launches a fresh bridge for the recovered session.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_stop_bridge).


##### `_resolve_command`  (lines 894–939)

```
def _resolve_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that proves an existing browser stack is still alive and returns its current DevTools WebSocket URL. This is the final check during reattach.

**Data flow**: It receives a BrowserStack and returns a Python heredoc command. When run, the command waits for the egress bridge port, asks the proxied Chrome /json/version endpoint for browser details, and prints the WebSocket debugger URL.

**Call relations**: SandboxChromeCdpProvider.reattach runs this after replacing the bridge. Its output is passed to _endpoint to build the public connection address.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (reattach).


##### `SandboxChromeCdpLease.endpoint`  (lines 950–951)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the DevTools endpoint that clients should connect to for this leased browser. An endpoint contains the WebSocket URL and any required connection headers.

**Data flow**: It reads the endpoint already stored in the lease and returns it unchanged. It does not touch the sandbox.

**Call relations**: Code using the lease calls this to learn where to connect. The endpoint was created earlier by _endpoint during lease startup or reattach.


##### `SandboxChromeCdpLease.token`  (lines 953–954)

```
async def token(self) -> str
```

**Purpose**: Returns a small recovery token for this browser stack. The token lets a later worker try to reconnect to the same stack after an interruption.

**Data flow**: It reads the lease's BrowserStack, turns its lease id and slot into stable JSON, and returns that string.

**Call relations**: It calls _stack_token. The provider later accepts this token in reattach to rebuild and validate the stack identity.

*Call graph*: calls 1 internal fn (_stack_token).


##### `SandboxChromeCdpLease.place_file`  (lines 956–960)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Returns the same file path the caller provided because Chrome is already running inside the same sandbox filesystem. No copy is needed.

**Data flow**: It receives a path and a lazy file reader, ignores the reader, and returns the path unchanged. Nothing is read or written.

**Call relations**: Browser automation can call this when it needs Chrome to access a file. Unlike a local browser setup, this lease does not need to shuttle the file across machine boundaries.


##### `SandboxChromeCdpLease.download_dir`  (lines 962–965)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the sandbox folder where Chrome saves downloads for this lease. Callers use it when configuring or locating browser downloads.

**Data flow**: It reads the stack's download directory path and returns it. It does not create or read files itself.

**Call relations**: The startup command created this directory, and fetch_download later reads files from the same location.


##### `SandboxChromeCdpLease.fetch_download`  (lines 967–995)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed download out of the sandbox and returns its bytes. It protects the host process by checking the file size before transferring it.

**Data flow**: It receives a download guid, builds the expected file path in the lease's download folder, runs wc -c in the sandbox to measure it, rejects files above the maximum size, runs base64 in the sandbox to stream the file as text, then decodes that text back into bytes in a worker thread.

**Call relations**: Code using the browser lease calls this after Chrome has downloaded a file. It uses sandbox commands for reading and asyncio.to_thread so large base64 decoding does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 997–998)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser lease by stopping and removing its private browser stack. This releases processes, files, and the reserved slot.

**Data flow**: It reads the lease's sandbox and BrowserStack, then asks _stop_stack to run cleanup in the sandbox. It returns nothing unless cleanup fails.

**Call relations**: Lease users call this at the end of the browser session. _stop_stack performs the actual sandbox cleanup command.

*Call graph*: calls 1 internal fn (_stop_stack).


##### `SandboxChromeCdpProvider.lease`  (lines 1005–1014)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Starts a new sandbox Chrome lease for a turn. It checks that the caller supplied a sandbox and that the sandbox has a durable turn id for ownership and recovery.

**Data flow**: It receives an optional sandbox, rejects missing sandbox or missing turn id, then delegates to _lease with recovery retry enabled. It returns a CdpLease when startup succeeds.

**Call relations**: This is the public entry used by the core browser system to get a Chrome-backed DevTools lease. The detailed allocation and startup sequence lives in _lease.

*Call graph*: calls 1 internal fn (_lease).


##### `SandboxChromeCdpProvider._lease`  (lines 1016–1065)

```
async def _lease(self, sandbox: Sandbox, retry_recovered_stack: bool) -> CdpLease
```

**Purpose**: Performs the full lease startup flow. It allocates or recovers a slot, starts the egress bridge, starts Chrome and the DevTools proxy, and returns a ready lease.

**Data flow**: It receives a sandbox and a retry flag. It derives the lease id from the sandbox turn id, runs allocation and allocation-discovery commands, validates the stack, possibly reattaches to an interrupted stack, starts the bridge, runs the browser startup command, turns the reported WebSocket URL into a public endpoint, and returns a SandboxChromeCdpLease. On failure, it cleans up the stack before re-raising.

**Call relations**: SandboxChromeCdpProvider.lease calls this. It coordinates most helpers in the file: allocation command builders, reattach, bridge start, browser startup, endpoint conversion, failure logging, abandoned allocation cleanup, and stack cleanup.

*Call graph*: calls 12 internal fn (bash, reattach, _abandon_allocation, _allocate_command, _bring_up_failure, _browser_up_command, _endpoint, _find_allocation_command, _stack, _stack_token (+2 more)); called by 1 (lease); 2 external calls (__init__, shield).


##### `SandboxChromeCdpProvider.reattach`  (lines 1067–1104)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser stack from a saved token. This is used when a worker crashed or restarted but the sandbox browser may still be alive.

**Data flow**: It receives a token and sandbox, verifies the sandbox and turn id, decodes the token into a BrowserStack, checks that the token belongs to the current turn, verifies the slot ownership, replaces the egress bridge, resolves the live WebSocket URL, builds a public endpoint, and returns a new lease. If any step proves the stack is gone or unsafe, it cleans up and raises SessionGone.

**Call relations**: _lease calls this when allocation says the turn may already have a stack. It uses _stack_from_token, _find_allocation_command, _stop_bridge, _start_bridge, _resolve_command, _endpoint, and _stop_stack to turn a token back into a safe active lease.

*Call graph*: calls 8 internal fn (bash, _endpoint, _find_allocation_command, _resolve_command, _stack_from_token, _start_bridge, _stop_bridge, _stop_stack); called by 1 (_lease); 3 external calls (__init__, __init__, shield).


##### `_endpoint`  (lines 1107–1113)

```
async def _endpoint(sandbox: Sandbox, stack: BrowserStack, local_url: str) -> CdpEndpoint
```

**Purpose**: Builds the public DevTools endpoint that outside browser-control code can use. It converts Chrome's local WebSocket path into the carrier's reachable host, scheme, and headers.

**Data flow**: It receives a sandbox, BrowserStack, and local WebSocket URL reported by Chrome. It asks the sandbox to expose or dial the DevTools proxy port, chooses ws or wss depending on whether TLS is used, extracts the safe local path with _ws_path, and returns a CdpEndpoint.

**Call relations**: Both fresh startup and reattach call this after they have a local proxied WebSocket URL. It hands the final connection details to SandboxChromeCdpLease.

*Call graph*: calls 2 internal fn (dial, _ws_path); called by 2 (_lease, reattach); 1 external calls (__init__).


##### `_start_bridge`  (lines 1116–1128)

```
async def _start_bridge(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Starts the egress bridge as a detached sandbox task. The bridge must stay alive while Chrome is using the authenticated outbound proxy.

**Data flow**: It receives a sandbox and BrowserStack, builds the bridge startup command, runs it as a detached bash task with a task base path, and checks whether task startup succeeded. It raises an error if the bridge could not start.

**Call relations**: _lease calls this before starting Chrome, and reattach calls it after stopping any old bridge. The command itself comes from _egress_bridge_up_command.

*Call graph*: calls 2 internal fn (bash_task, _egress_bridge_up_command); called by 2 (_lease, reattach).


##### `_stop_bridge`  (lines 1131–1140)

```
async def _stop_bridge(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Stops the egress bridge without tearing down the whole browser stack. This lets reattach replace the bridge while preserving the running browser.

**Data flow**: It receives a sandbox and BrowserStack, builds the bridge cleanup command, runs it in the sandbox, and checks the exit code. It raises a clear error if the bridge could not be replaced.

**Call relations**: SandboxChromeCdpProvider.reattach calls this before _start_bridge so the recovered lease gets a fresh bridge tied to the current task.

*Call graph*: calls 2 internal fn (bash, _bridge_down_command); called by 1 (reattach).


##### `_abandon_allocation`  (lines 1143–1149)

```
async def _abandon_allocation(sandbox: Sandbox, lease_id: str) -> None
```

**Purpose**: Best-effort cleanup for a failed allocation. It tries to remove any slot lock and directory left behind for a lease id.

**Data flow**: It receives a sandbox and lease id, builds the abandon-allocation command, and runs it with an allocation timeout. It suppresses any cleanup exception because this runs on an already-failing path.

**Call relations**: The provider's _lease method calls this when allocation or allocation recovery gives invalid results.

*Call graph*: calls 2 internal fn (bash, _abandon_allocation_command); called by 1 (_lease).


##### `_stop_stack`  (lines 1152–1161)

```
async def _stop_stack(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Stops and removes a whole browser stack. This is the main cleanup action for normal lease close and for many failure paths.

**Data flow**: It receives a sandbox and BrowserStack, builds the full stack cleanup command, runs it in the sandbox, and checks whether cleanup succeeded. It raises an error if the sandbox reports a cleanup failure.

**Call relations**: SandboxChromeCdpLease.aclose calls this for normal shutdown. The provider also calls it when startup, cancellation, or reattach failure needs to avoid leaving processes behind.

*Call graph*: calls 2 internal fn (bash, _stack_down_command); called by 3 (aclose, _lease, reattach).


##### `_stack_from_token`  (lines 1164–1175)

```
def _stack_from_token(token: str) -> BrowserStack
```

**Purpose**: Decodes a recovery token back into a BrowserStack. It rejects bad, old, or malformed tokens by treating the session as gone.

**Data flow**: It receives a token string, parses it as JSON, checks the token version and field types, then calls _stack to validate and construct the BrowserStack. If parsing or validation fails, it raises SessionGone.

**Call relations**: SandboxChromeCdpProvider.reattach uses this as its first step. It is the gatekeeper that prevents arbitrary token text from becoming sandbox paths or port choices.

*Call graph*: calls 1 internal fn (_stack); called by 1 (reattach); 2 external calls (__init__, loads).


##### `_stack_token`  (lines 1178–1183)

```
def _stack_token(stack: BrowserStack) -> str
```

**Purpose**: Creates the recovery token for a BrowserStack. The token stores only the version, lease id, and slot needed to find the stack again.

**Data flow**: It receives a BrowserStack and serializes its key identity fields into compact, consistently ordered JSON. The result is a string.

**Call relations**: SandboxChromeCdpLease.token returns this to callers, and _lease uses it when it needs to reattach to an interrupted stack.

*Call graph*: called by 2 (token, _lease); 1 external calls (dumps).


##### `_bring_up_failure`  (lines 1186–1201)

```
async def _bring_up_failure(sandbox: Sandbox, stack: BrowserStack, result: ExecResult) -> str
```

**Purpose**: Builds a helpful error message when browser startup fails. If the sandbox command timed out, it reads recent logs so the error says more than just "timeout."

**Data flow**: It receives the sandbox, BrowserStack, and failed execution result. If the command ended normally, it returns the reported stdout or stderr. If it timed out, it runs a tail command inside the sandbox for Chrome, proxy, and bridge logs, then combines those log lines with the timeout message.

**Call relations**: The provider's _lease method calls this when the browser startup command exits unsuccessfully. It turns low-level sandbox failure into a useful startup error.

*Call graph*: calls 1 internal fn (bash); called by 1 (_lease).


##### `_ws_path`  (lines 1204–1209)

```
def _ws_path(url: str, chrome_port: int) -> str
```

**Purpose**: Extracts the WebSocket path from Chrome's local debugger URL and verifies it points to the expected local Chrome port. This prevents accidentally forwarding a URL for some other host.

**Data flow**: It receives a WebSocket URL and Chrome port, trims whitespace, checks for accepted localhost prefixes, removes the prefix, and returns only the path. If the URL is not local to that port, it raises an error.

**Call relations**: _endpoint calls this before combining Chrome's path with the public host returned by sandbox.dial.

*Call graph*: called by 1 (_endpoint).


##### `manifest`  (lines 1212–1221)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It advertises the sandbox_chrome CDP provider and tells the system how to build it.

**Data flow**: It creates a Manifest containing the extension name, version, and a CDP provider specification whose builder returns a SandboxChromeCdpProvider. The Manifest is returned to the extension loader.

**Call relations**: The wider extension system calls this during discovery or startup so the sandbox_chrome backend becomes available as a browser provider.

*Call graph*: 2 external calls (__init__, __init__).

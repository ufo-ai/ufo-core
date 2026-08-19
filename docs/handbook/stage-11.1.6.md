# Hosted and sandboxed browser providers  `stage-11.1.6`

This stage is behind-the-scenes support for web browsing. It gives the system several ways to use a browser without depending only on the built-in, in-process browser engine. Think of it as a set of adapters for different kinds of “borrowed browsers,” so the rest of the project can ask for browsing work in the same general way.

The Browser Use provider connects to a hosted web-browsing agent. It exposes tools such as browser_task for focused automation and wide_browse for broader browsing jobs, letting the system delegate web work to an outside service.

The Browserbase provider uses remote Chrome sessions. It can create a hosted Chrome browser, reconnect to it later, move files in and out, and clean it up when finished.

The sandbox Chrome provider runs a real Chrome inside a per-conversation sandbox, which is an isolated workspace. It starts or reuses that browser, exposes a safe control connection, and retrieves downloaded files. Together, these providers offer flexible browser choices for different safety, hosting, and file-handling needs.

## Files in this stage

### Hosted and sandboxed browser providers
Alternative browser backends provide hosted-agent browsing, remote Browserbase Chrome sessions, and per-conversation sandbox Chrome access.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This extension is a bridge to an outside service: Browser Use. Instead of this project opening and driving a browser itself, it sends a written task to Browser Use’s API, waits for the hosted agent to finish, and returns the result. This matters because other parts of the system already know how to call tools named `browser_task` and `wide_browse`; by keeping those names, this hosted version can replace the local browser stack without changing callers.

The file protects a few important boundaries. The Browser Use API key is read on the host side through the project’s credential system, so it is not placed inside the sandbox where untrusted work happens. If the hosted run produces files, this code downloads only a limited number, only over HTTPS, and only into safe workspace paths. That is like accepting packages through a mailroom that checks both the sender’s link and the destination room before delivery.

There are two main flows. `browser_task` starts one careful browser session from a URL and saves any output files. `wide_browse` reads a list of sites or names from a workspace file, runs many cheaper browser sessions in parallel, and writes a combined JSON results file. Runs are limited by dollar cost and wall-clock time, not by step count.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one complete hosted Browser Use job from start to finish. It checks the task size, gets the API key, starts or reattaches to a run, waits for it, cancels it if it times out, collects any output files, and returns a clear summary.

**Data flow**: It receives the tool context, the task text, a timeout, and optionally a deduplication key used to avoid paying for the same run twice. It reads the extension store and Browser Use credential, talks to the Browser Use API, watches the remote run until it finishes or times out, then returns a `RunOutcome` containing the final status, result or error text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the main engine used by the tool handlers. `_browser_task` and the inner `wide_browse` visit flow create a `HostedRun` and call this method. Inside, it delegates the smaller jobs to `_start`, `_watch`, `_status`, `_json`, and `_collect` so each step of the remote run is checked and reported consistently.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Creates a Browser Use run, or reuses an already-created one when a deduplication key points to it. This prevents accidental duplicate hosted work when a request is retried.

**Data flow**: It receives an HTTP client, the extension’s small persistent store, the task text, and an optional deduplication key. If the key already has a saved run handle, it validates and returns that handle. Otherwise it posts the task, model, cost limit, and proxy country to Browser Use, extracts the new run ID and workspace ID, saves them if needed, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this at the beginning of every run. It uses `_json` to read the API response safely and `_text` to require the response fields it needs before handing the run handle back to `execute`.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Polls Browser Use until a run reaches a final state such as completed, failed, stopped, or cancelled. It is the waiting loop for the hosted browser job.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current state; if the state is terminal, it returns that state, otherwise it sleeps briefly and checks again.

**Call relations**: `HostedRun.execute` calls this while wrapped in a timeout. `_watch` relies on `_status` for each single status read, keeping the loop simple and making the timeout path able to do one last direct status check.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is used both during normal polling and right before cancellation, so a run that finished at the last moment is not cancelled unnecessarily.

**Data flow**: It receives an HTTP client and a run ID. It sends a GET request to the run status endpoint, parses the JSON response, extracts the required `status` string, and returns that string.

**Call relations**: `_watch` calls this repeatedly while waiting. `HostedRun.execute` also calls it after a local timeout, before deciding whether to cancel the remote run.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Fetches output files created by a finished Browser Use run and writes safe, allowed files into the project workspace. It also records files it skipped because they were too large or lacked a usable download URL.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If output saving is disabled, it returns empty results. Otherwise it asks Browser Use for a limited file list, checks each file has a path and size, verifies the path cannot escape the workspace, downloads eligible files, writes them into the sandbox workspace, and returns saved files, skipped files, and a flag saying whether more files existed.

**Call relations**: `HostedRun.execute` calls this after the run has reached a terminal status. `_collect` uses `_json` to read the file listing, `_download` to fetch each allowed file, and the shared sandbox containment check to avoid unsafe paths.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a Browser Use-provided link without sending the project’s API key to that storage host. It insists on HTTPS so the file is fetched over an encrypted web connection.

**Data flow**: It receives a URL. If the URL does not start with `https://`, it raises a Browser Use error. Otherwise it opens a separate HTTP client with no Browser Use API header, downloads the bytes, raises an error for bad HTTP responses, and returns the file content as bytes.

**Call relations**: `_collect` calls this only after deciding a listed output file is small enough and has a valid-looking URL. The returned bytes are then written into the sandbox workspace by `_collect`.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a Browser Use HTTP response into a plain Python dictionary, while turning bad responses into helpful extension errors. This keeps API failures from looking like silent empty results.

**Data flow**: It receives an HTTP response. If the status code is an error, if the body is not JSON, or if the JSON is not an object, it raises `BrowserUseError` with details. Otherwise it returns the response body as a dictionary with string keys.

**Call relations**: This helper is used throughout the hosted run flow: starting runs, reading status, collecting files, cancelling timed-out runs, and reading final summaries. By centralizing response checking, the rest of the code can assume it is working with readable API data.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Pulls one required text field out of an API response. It turns missing or non-text fields into the same kind of clear Browser Use error used elsewhere.

**Data flow**: It receives a response dictionary and a field name. It looks up that field, confirms the value is a string, and returns it; otherwise it raises `BrowserUseError` showing the unreadable response.

**Call relations**: `_start` uses this to read the new run ID and workspace ID. `_status` uses it to read the current run status.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 350–384)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the `browser_task` tool: one hosted browser session that starts at a given URL and follows the user’s detailed instructions. It returns the agent’s result and any output files that were safely fetched.

**Data flow**: It receives the tool context and validated browser-task arguments: starting URL, task text, display name, timeout, and user description. It builds a self-contained task prompt, creates a `HostedRun` using the higher-cost task model, runs it with the requested timeout, and converts the outcome into a `ToolResult`. If the run timed out, the result is marked as an error with a plain message; otherwise the result is JSON containing the run result, saved files, skipped files, and whether more files were available.

**Call relations**: The tool registry calls this when someone invokes `browser_task`. It hands the real remote-work flow to `HostedRun.execute`, then wraps the returned `RunOutcome` in the project’s standard tool response format.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 387–394)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a file from the sandbox workspace using a safely quoted shell command. It exists because file paths may come from the model or user, so they must not be allowed to act like shell code.

**Data flow**: It receives a tool context and a path string. It shell-quotes the path, runs `cat` inside the sandbox, and either returns the file contents or raises a readable error if the command failed.

**Call relations**: `_read_lines` uses this to read the entity list for wide browsing. `_wide_browse` also uses it directly to read the JSON schema file.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 397–405)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace file as a clean list of unique, non-empty lines. For `wide_browse`, each line becomes one site name or URL to visit.

**Data flow**: It receives a tool context and file path. It reads the file through `_read_file`, splits it into lines, trims whitespace, ignores blank lines, removes duplicates while keeping the first occurrence, and returns the resulting list.

**Call relations**: `_wide_browse` calls this near the start of the batch flow to decide which entities will get hosted browser runs.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 408–454)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the `wide_browse` tool: it runs the same browser-style extraction task across many entities and writes a combined JSON result file. It is useful when the caller wants structured information from many sites or names at once.

**Data flow**: It receives the tool context and validated arguments: an entities file, a prompt template, an output schema file, and a user description. It reads and de-duplicates entities, enforces the maximum batch size, reads the schema text, creates a shared `HostedRun` configured for cheaper runs with no output-file saving, and launches visits in parallel with a limit on how many run at the same time. It gathers all row results, turns ordinary per-entity failures into error rows, writes `wide_browse.json` in the workspace, and returns JSON containing the rows and output file name.

**Call relations**: The tool registry calls this when someone invokes `wide_browse`. It uses `_read_lines` and `_read_file` for inputs, its inner `visit` function for each remote browser run, and `asyncio.gather` to wait for the batch without throwing away successful sibling results.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 424–439)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the Browser Use task for one entity inside a `wide_browse` batch. Its job is to make one row of the final result table.

**Data flow**: It receives one entity string from the surrounding `_wide_browse` function. It waits for a concurrency slot, fills `{entity}` into the prompt template, appends the output schema if one was provided, runs the hosted browser task with a per-entity deduplication key when available, and returns a dictionary containing the entity, run status, and result text.

**Call relations**: `_wide_browse` creates this inner function and starts one copy for each entity. The surrounding batch gathers all visits together; if this function raises a normal exception, `_wide_browse` records that entity as an errored row instead of failing the whole batch.


##### `manifest`  (lines 477–492)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, tools, prompt text, and required credential. Without this, the host would not know what tools this file provides or what API key it needs.

**Data flow**: It takes no input. It packages the two tool definitions, the browser prompt section read from disk at import time, and a credential slot for the Browser Use API key into a `Manifest`, then returns it.

**Call relations**: The extension loader calls this during setup. The returned manifest lets the tool registry expose `browser_task` and `wide_browse`, and lets the credential system know it must provide `browser_use_api_key` before hosted runs can work.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session startup, remote browser use, recovery reattach, and teardown`

A normal browser automation run often assumes Chrome is running on the same machine as the code. Browserbase changes that: Chrome lives on Browserbase’s servers, so this file acts like a travel adapter between the project and that remote browser. Without it, choosing `cdp_provider = "browserbase"` would leave the system with no way to rent a browser, connect to it, move files into it, or release it afterward.

The main flow starts with `BrowserbaseCdpProvider.lease`. It uses the current browser turn’s conversation id as the identity of one browser run. It creates or reuses a Browserbase “Context”, which is Browserbase’s saved browser state, like cookies and local storage. Then it creates a new hosted session attached to that context and returns a `BrowserbaseLease`, which is the project’s handle for that one remote browser.

`BrowserbaseApi` is the low-level HTTP client for Browserbase’s REST API. It reads the API key from a host-side credential slot each time it sends a request, so the key is not exposed inside the sandbox and key rotation can take effect quickly.

Because the browser is remote, local file paths do not work directly. `place_file` uploads file bytes to Browserbase and returns the remote path Chrome can see. Downloads are fetched back through Browserbase’s download API, with size limits so an untrusted web page cannot force this process to load a huge file into memory. Finally, closing the lease releases the session and deletes the saved context so authenticated browser state does not outlive the browser turn.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new Browserbase-hosted browser session attached to an existing Browserbase Context. The Context keeps browser state, such as logins, while the session is the live Chrome instance to connect to.

**Data flow**: It receives a `context_id`. It sends Browserbase a POST request asking for a session that persists that context and uses the project’s chosen timeout. It returns the new session id and the Chrome connection URL that the rest of the browser engine can use.

**Call relations**: This is the final Browserbase API step in `BrowserbaseCdpProvider.lease`: after the provider has found or created a context, it calls this method to rent the actual remote browser. It relies on `_json` to send the request and `_field` to make sure Browserbase really returned the required `id` and `connectUrl`.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still usable and, if it is, returns its connection URL. This is needed when the system tries to recover or reattach to a browser run instead of starting over.

**Data flow**: It receives a session id. It asks Browserbase for that session’s current record, checks that its status is still live, and extracts the connection URL. If Browserbase says the session is no longer running or pending, it raises `SessionGone`, which tells the caller the old browser cannot be reused.

**Call relations**: This method is used during reattachment through `BrowserbaseCdpProvider.reattach`. It delegates the HTTP work to `_json`, reads fields through `_field`, and turns a non-live status into the project’s standard `SessionGone` signal.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to release a hosted browser session. This stops the remote browser from continuing to run, which matters for cleanup and billing.

**Data flow**: It receives a session id. It sends Browserbase a POST request changing that session’s status to the release-request value. It does not return useful data; the important result is the remote session being asked to shut down.

**Call relations**: This is called as part of `BrowserbaseLease.aclose`, before the context is deleted. It uses `_json` for the request so HTTP errors are reported as Browserbase transport failures instead of being ignored.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a Browserbase Context, which is the saved browser state used across sessions within one browser run. It lets a recreated session keep things like cookies and local storage earned earlier in the same run.

**Data flow**: It sends an empty POST request to Browserbase’s contexts endpoint. From Browserbase’s response, it extracts the new context id. That id is returned for later session creation.

**Call relations**: `BrowserbaseCdpProvider._context` calls this when there is no context already stored for the current conversation. This method uses `_json` to talk to Browserbase and `_field` to verify that the response contains a usable id.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context after the browser run is finished. This prevents saved authenticated browser state from surviving longer than the subagent browser turn.

**Data flow**: It receives a context id. It sends a DELETE request to Browserbase for that context. It returns nothing; the effect is remote cleanup.

**Call relations**: `BrowserbaseLease.aclose` calls this after requesting session release. It uses `_send` directly because it does not need to read a JSON response.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed Browserbase download. It waits briefly for Browserbase’s storage listing to catch up, and it refuses oversized or unmeasured files to protect this process’s memory.

**Data flow**: It receives a session id and a download guid, which is the name the browser assigned to the stored download. It repeatedly asks Browserbase for the session’s download list, looks for an entry with that filename, checks its reported size, then downloads the file bytes. It returns those bytes, or raises an error if the file never appears or is too large.

**Call relations**: `BrowserbaseLease.fetch_download` uses this when the browser engine wants the contents of a remote download. Inside the method, `_json` reads the listing, `_size` enforces the safety check, `_field` extracts the download entry id, `_send` retrieves the actual bytes, and `asyncio.sleep` spaces out retry attempts.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads file bytes into a Browserbase session so the remote Chrome browser can use them. This replaces the local-path behavior that would work only for a local browser.

**Data flow**: It receives a session id, a remote file name, and raw file bytes. It sends those bytes to Browserbase’s session upload endpoint as a file upload. It returns nothing; after it succeeds, Browserbase has staged the file inside the remote browser environment.

**Call relations**: `BrowserbaseLease.place_file` calls this after reading and naming the local workspace file. This method hands the actual HTTP upload to `_send`, using the longer upload timeout.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase request and insists that the response body is a JSON object, meaning a dictionary-like structure. It is the shared helper for API calls that expect structured data back.

**Data flow**: It receives an HTTP method, a Browserbase path, and request options such as JSON data. It sends the request through `_send`, parses the response as JSON, and checks that the result is an object. It returns that object, or raises `BrowserbaseError` if Browserbase returned some other shape.

**Call relations**: Higher-level API methods such as `create_session`, `live_session`, `release_session`, `create_context`, and `download` call this so they do not each repeat the same send-and-parse code. It depends on `_send` for authentication, timeout handling, and HTTP error reporting.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual authenticated HTTP request to Browserbase. It is the one place that attaches the Browserbase API key and turns failed HTTP status codes into clear provider errors.

**Data flow**: It receives an HTTP method, path, timeout, and optional request details. It reads the API key from the credential store, combines it with any extra headers, opens an async HTTP client, and sends the request. It returns the raw HTTP response if successful, or raises `BrowserbaseError` if Browserbase reports an error status.

**Call relations**: This is the lowest-level network helper in the file. `_json`, `delete_context`, `download`, and `upload` all pass through it, so every Browserbase REST call gets the same credential handling and error behavior.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Extracts a required string field from a Browserbase JSON response. It prevents the rest of the code from continuing with missing or empty ids and URLs.

**Data flow**: It receives a response object and a field name. It looks up the value, checks that it is a non-empty string, and returns it. If the value is missing or not a usable string, it raises `BrowserbaseError`.

**Call relations**: API methods call this after `_json` has parsed Browserbase’s response. It is used by `create_session`, `live_session`, `create_context`, and `download` whenever a response field is required for the next step.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the reported size of a downloaded file. This is a safety guard so the process does not fetch a file of unknown or excessive size into memory.

**Data flow**: It receives one download listing entry from Browserbase. It checks that the `size` value is a number and not a boolean, then converts it to an integer. If the size is absent or unusable, it raises `BrowserbaseError`.

**Call relations**: `BrowserbaseApi.download` calls this before fetching the actual download bytes. It is the safety checkpoint between seeing a download in Browserbase’s listing and pulling its contents into this process.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome connection endpoint for this remote browser session. The browser engine uses this endpoint to speak the Chrome DevTools Protocol, which is Chrome’s automation control channel.

**Data flow**: It reads the lease’s stored `connect_url`. It wraps that URL in a `CdpEndpoint` object. It returns that object without changing remote or local state.

**Call relations**: This is part of the lease object returned by `BrowserbaseCdpProvider.lease` or `BrowserbaseCdpProvider.reattach`. When the browser engine needs to connect to Chrome, this method turns the Browserbase URL into the standard endpoint shape.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a recovery token for this browser run. The token carries enough identity to reconnect to the same session and later clean up the matching context.

**Data flow**: It reads the conversation id, session id, and context id from the lease. It joins them into one string using the file’s token format. It returns that string and does not change any state.

**Call relations**: The token created here is later understood by `BrowserbaseCdpProvider.reattach`, which passes it to `_parse_token`. This pairing lets a recovered browser turn avoid guessing which conversation, session, or context it belongs to.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Moves a workspace file into the remote Browserbase browser and returns the path Chrome can use for it. This is needed because a hosted Chrome cannot read files from this process’s local disk by path.

**Data flow**: It receives a workspace path and a `read` callback that returns the file bytes. It takes the base file name, reads the bytes, checks the upload size limit, and avoids name collisions by adding a short path digest when needed. It uploads the bytes through the API, records which remote name was staged, and returns the Browserbase remote upload path.

**Call relations**: The browser engine calls this through the lease when it needs to set a file input in the page. After doing local naming and safety checks, it hands the network upload to `BrowserbaseApi.upload`; if reading or uploading fails, the staged-name record is not updated.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name that Browserbase-hosted Chrome accepts. It avoids giving Chrome an absolute local path that the hosted browser cannot use.

**Data flow**: It takes no outside data beyond the lease. It returns the literal string `downloads`. It does not touch Browserbase or local storage.

**Call relations**: The browser engine asks the lease where downloads should be directed for this kind of browser. This method supplies Browserbase’s required value so later downloads land in Browserbase session storage, where `fetch_download` can retrieve them.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed browser download from Browserbase storage. It gives the caller the actual bytes of a file that was downloaded inside the remote browser.

**Data flow**: It receives the browser download guid. It combines that with the lease’s session id and asks `BrowserbaseApi.download` to find and fetch the file. It returns the downloaded bytes.

**Call relations**: This is the lease-level method the browser engine uses after Chrome has completed a download. It delegates the polling, size checking, and HTTP byte fetch to `BrowserbaseApi.download`.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser run. It releases the Browserbase session, deletes the saved context, and removes the local store entry that pointed to that context.

**Data flow**: It reads the session id, context id, and conversation-scoped store key from the lease. It first asks Browserbase to release the session, then tries to delete the context, and finally deletes the stored context id from the extension’s store. Even if one cleanup step fails, the nested `finally` blocks make sure later cleanup steps are still attempted.

**Call relations**: This is called when the lease is closed at the end of a browser turn. It calls `BrowserbaseApi.release_session`, then `BrowserbaseApi.delete_context`, then the scoped store delete operation, in that order so a context is not deleted while a browser may still be using it.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts a fresh Browserbase-backed browser lease for the current browser turn. This is the main entry used when the system needs a remote Chrome session.

**Data flow**: It receives the current sandbox session, which contains the conversation id for this browser run. It builds a `BrowserbaseApi`, opens the extension’s scoped store, gets or creates the Browserbase context for the conversation, then creates a Browserbase session. It returns a `BrowserbaseLease` containing all ids and the connection URL needed to use and later clean up the session.

**Call relations**: This method is exposed through the provider registered in `manifest`. Its main helper is `_context`, which supplies the context id, and it then constructs the lease object that the browser engine uses for endpoint lookup, uploads, downloads, tokens, and cleanup.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context for a conversation, or creates and stores one if none exists yet. This lets a recovered session within the same browser run keep the browser state it already earned.

**Data flow**: It receives the API client, scoped store, and conversation id. It builds a store key, checks whether a context id is already saved there, and returns it if present. Otherwise it asks Browserbase to create a new context, stores that id under the key, and returns the new id.

**Call relations**: `BrowserbaseCdpProvider.lease` calls this before creating a session. It uses the scoped store’s `get` and `put` operations for local bookkeeping and `BrowserbaseApi.create_context` when Browserbase must make a new context.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Rebuilds a lease around an existing Browserbase session from a recovery token. This allows the system to reconnect after an interruption if Browserbase still has the session.

**Data flow**: It receives a token string. It parses the token into conversation id, session id, and context id, creates an API client, checks that the session is still live and gets its connection URL, then returns a new `BrowserbaseLease` for that same remote session.

**Call relations**: This is the counterpart to `BrowserbaseLease.token`. It first calls `_parse_token` to recover the ids, then uses `BrowserbaseApi.live_session` to make sure the session can still be used before constructing the replacement lease.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a Browserbase recovery token back into its three parts: conversation id, session id, and context id. It rejects malformed tokens by treating the session as gone.

**Data flow**: It receives the token string. It splits it at slashes, checks that the session and context parts exist, and converts the conversation part into a UUID. It returns the three parsed values, or raises `SessionGone` if the token cannot be trusted.

**Call relations**: `BrowserbaseCdpProvider.reattach` calls this before attempting to reconnect. By raising `SessionGone` for bad tokens, it lets the higher-level recovery flow fall back to creating a fresh session instead of using unclear or unsafe ids.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It tells the system the extension’s name, version, required Browserbase API key credential, and the CDP provider it can build.

**Data flow**: It takes no input. It creates a credential slot description for the Browserbase API key and a provider specification for the Browserbase backend. It returns a `Manifest` object that the host can load.

**Call relations**: This is how the rest of the project discovers the Browserbase provider. The provider spec’s build function constructs `BrowserbaseCdpProvider` with the host-supplied credentials, which later leads to `lease` and `reattach` during browser runs.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and sandbox browser startup`

This provider solves a practical problem: the browser must see the same files and environment as the conversation sandbox, but the main service still needs a way to control it. It does that by launching headless Chrome inside the sandbox and connecting to Chrome's DevTools Protocol, which is Chrome's remote-control interface.

The file builds a small bring-up command that runs inside the sandbox. That command creates browser directories, finds an installed Chromium or Chrome binary, starts it on port 9222 if it is not already answering, and starts a tiny proxy on port 9223. The proxy is important because Chrome rejects some remote DevTools requests unless the request looks like it came through localhost. The proxy rewrites the Host header, like a receptionist correcting the address on an envelope before forwarding it.

A lease represents one turn's usable browser connection. Closing the lease does not stop Chrome, because the browser is meant to live with the sandbox across turns. The provider also knows how to fetch downloads by reading files from the sandbox, with a size limit so a webpage cannot force the service to pull back an unexpectedly huge file.

If startup fails, the code tries hard to return useful browser logs instead of a vague timeout.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 304–305)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already-prepared browser control endpoint for this lease. Callers use this endpoint to connect to Chrome through the sandbox's exposed port.

**Data flow**: It reads the endpoint stored on the lease when the lease was created. It does not change anything; it simply gives that same endpoint back to the caller.

**Call relations**: After SandboxChromeCdpProvider.lease has started or found Chrome and built the endpoint, browser-driving code asks the lease for this value when it is ready to connect.


##### `SandboxChromeCdpLease.token`  (lines 307–308)

```
async def token(self) -> str
```

**Purpose**: Returns a durable-looking handle for the browser session, using the endpoint URL itself. In this provider, that token is mainly a label, not enough by itself to reconnect later.

**Data flow**: It reads the URL from the stored endpoint and returns it as text. Nothing is opened, closed, or changed.

**Call relations**: Code that expects every browser lease to provide a token can call this. If someone later tries to reattach with the token, SandboxChromeCdpProvider.reattach rejects it because this provider needs a live sandbox to build a fresh connection.


##### `SandboxChromeCdpLease.place_file`  (lines 310–314)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells the caller that no file copying is needed. Because Chrome runs inside the same sandbox as the file, the browser can use the path it was already given.

**Data flow**: It receives a path and a file-reading callback. It ignores the callback because there is no need to move bytes anywhere, then returns the original path unchanged.

**Call relations**: Browser workflows call this when they need to make a file available to Chrome, such as for upload. Unlike a local browser provider, this one does not hand off to any file-transfer step because the file is already in Chrome's filesystem.


##### `SandboxChromeCdpLease.download_dir`  (lines 316–319)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the folder inside the sandbox where Chrome should save downloads. This gives the browser-driving code a concrete place to watch for downloaded files.

**Data flow**: It returns the fixed sandbox download directory path. It does not inspect the sandbox or create the directory here; the bring-up script creates it earlier.

**Call relations**: Download setup code calls this before asking Chrome to save files. Later, SandboxChromeCdpLease.fetch_download reads completed downloads from the same directory.


##### `SandboxChromeCdpLease.fetch_download`  (lines 321–349)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed downloaded file out of the sandbox and returns its bytes to the main service. It also protects the service by refusing downloads larger than the configured maximum.

**Data flow**: It receives a download id, treats that id as the filename inside the sandbox download directory, and safely quotes it for shell use. First it asks the sandbox for the file size. If the file is too large or unreadable, it raises an error. Then it runs base64 inside the sandbox to print the file safely as text, decodes that text back into bytes in a worker thread, and returns the bytes.

**Call relations**: After browser automation has allowed and named a download, code calls this to retrieve the result. It uses the sandbox command runner for both the size check and the read, uses shlex.quote to avoid unsafe shell filenames, and uses asyncio.to_thread so base64 decoding does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 351–352)

```
async def aclose(self) -> None
```

**Purpose**: Ends the lease without stopping the browser. Chrome and its proxy intentionally stay alive inside the sandbox so later turns can reuse them.

**Data flow**: It receives no extra data and returns nothing. It makes no changes to Chrome, the proxy, or the sandbox.

**Call relations**: General lease cleanup code may call this at the end of a turn. For this provider, cleanup is a no-op because the browser lifetime is tied to the sandbox, not to one lease.


##### `SandboxChromeCdpProvider.lease`  (lines 364–381)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a usable browser lease for the current turn's sandbox. It starts or reuses Chrome, confirms the proxy works, and returns connection details the browser engine can use.

**Data flow**: It receives a sandbox session. If no sandbox is supplied, it raises an error because this provider cannot work without one. It runs the bring-up command inside the sandbox. If that command fails, it asks _bring_up_failure for a clearer explanation. On success, it asks the sandbox how to dial the proxy port, chooses ws or wss depending on whether that exposed connection uses TLS, replaces Chrome's local websocket host with the sandbox's public host using _ws_path, attaches any required headers, and returns a SandboxChromeCdpLease.

**Call relations**: This is the main entry used by the core CDP provider interface when a turn needs a browser. It hands command execution to SandboxSession.bash, asks SandboxSession.dial how the outside service should reach the sandbox port, calls _ws_path to keep only the websocket path, and wraps the final CdpEndpoint in a lease.

*Call graph*: calls 4 internal fn (bash, dial, _bring_up_failure, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 383–384)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that reconnecting from an old token is not supported. A fresh connection must be grounded in a live sandbox by calling lease again.

**Data flow**: It receives a token string but does not use it to rebuild an endpoint. Instead, it raises SessionGone with that token to say the old session cannot be restored this way.

**Call relations**: Recovery code may call this when it has a saved browser token. This provider deliberately stops that path, because the endpoint depends on the current sandbox's live port information, which only SandboxChromeCdpProvider.lease can obtain.

*Call graph*: 1 external calls (__init__).


##### `_bring_up_failure`  (lines 387–401)

```
async def _bring_up_failure(sandbox: SandboxSession, result: ExecResult) -> str
```

**Purpose**: Turns a failed browser startup into a useful error message. If the sandbox command was killed by a timeout, it fetches the browser and proxy logs so the caller can see what actually went wrong.

**Data flow**: It receives the sandbox and the failed command result. If the command ended normally with an error, it returns the command's stderr or stdout. If the command timed out, it runs a short tail command in the sandbox to read the last lines of the Chrome and proxy logs, then returns a message combining the timeout with those logs when available.

**Call relations**: SandboxChromeCdpProvider.lease calls this only on bring-up failure. The helper uses SandboxSession.bash again because the most useful clues live inside sandbox log files, not in the outer timeout report.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 404–409)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part of Chrome's websocket URL while making sure Chrome reported a local address. This prevents the provider from blindly trusting an unexpected remote websocket URL.

**Data flow**: It receives the websocket URL printed by the in-sandbox bring-up command. It trims whitespace, checks that the URL starts with Chrome's expected localhost address and port, and returns only the remaining path. If the URL is not local, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease uses this after the bring-up command prints Chrome's websocket debugger URL. The provider then combines the safe path with the sandbox carrier's public host and headers, rather than guessing those transport details itself.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 412–421)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the larger system. It says the extension provides a CDP backend named sandbox_chrome and shows how to build the provider object.

**Data flow**: It creates and returns a Manifest containing the extension name, version, and one CDP provider specification. The build function in that specification constructs a SandboxChromeCdpProvider when the system selects this backend.

**Call relations**: Extension loading code calls this to discover what the file offers. Through the returned CdpProviderSpec, the core system can later create the provider and call SandboxChromeCdpProvider.lease when a sandbox-backed browser is needed.

*Call graph*: 2 external calls (__init__, __init__).

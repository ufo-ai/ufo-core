# Browser providers and hosted/local launch adapters  `stage-12.2`

This stage is the browser “plug socket” for the system. When a turn needs to use the web, the core code should not care whether Chrome is running locally, in a sandbox, or on a hosted service. The shared browser boundary in core/src/ufo/browser.py defines what any browser provider must offer, like connection details and session information, without starting a browser itself.

The other files are adapters that plug real browser choices into that boundary. The Browserbase adapter starts a fresh hosted Chrome session for a run, preserves temporary login state while it is active, transfers files between the remote browser and the system, and then removes the session. The Browser Use adapter exposes two existing tool names, browser_task and wide_browse, but sends the work to Browser Use’s cloud browser agent instead of the project’s own automation. The sandbox Chrome adapter launches a private headless Chrome inside the turn’s sandbox, connects through Chrome DevTools, and manages ports, proxy helpers, downloads, reconnection, and cleanup.

## Files in this stage

### Provider contract
Defines the core boundary that concrete browser providers implement for browser-backed turns.

### `core/src/ufo/browser.py`

`io_transport` · `cross-cutting, especially per-turn browser setup and teardown`

This file is a contract, not an implementation. Its job is to keep the core system independent from any one browser setup. A turn may use Chrome running inside its own sandbox, or it may use a remote hosted browser service. Core should not need to know which one it got. It only needs a standard way to ask: “Where do I connect?”, “How do I reconnect later?”, “Where do files and downloads go?”, and “How do I clean up?”

The main idea is a CDP lease. CDP means Chrome DevTools Protocol, the remote-control interface used to drive Chrome. A `CdpProvider` creates a `CdpLease` for one turn. The lease is like borrowing a key to a browser for a limited time. It gives the connection endpoint, a durable token for reconnecting, file-placement rules, download retrieval, and a close method for releasing the browser when the turn ends.

This matters because different browser sources have different realities. A sandbox-local Chrome can open files directly from the sandbox. A hosted Chrome may need files uploaded first. Likewise, downloads may be read from the sandbox filesystem or fetched from a provider API. This file hides those differences behind one small interface so the browser-driving engine can work the same way in every environment.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This method returns the address and connection details needed to talk to the leased Chrome browser. Someone uses it when they are ready to connect a browser-driving engine to Chrome through CDP, the browser remote-control protocol.

**Data flow**: It takes no extra input beyond the lease object itself. The concrete lease looks up or prepares the browser connection information, then returns a `CdpEndpoint` containing a URL and optional headers such as authentication or routing tokens. If the implementation cannot provide a usable endpoint, it may fail instead of returning a bad connection.

**Call relations**: This file only defines the required shape of the method. Concrete `CdpLease` implementations supplied by browser providers fill it in, and the browser engine calls it when it needs to open the CDP connection for a turn.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: This method returns a saved handle that can be used to reconnect to the same browser session later. It is useful when a turn is recovered or resumed and should try to continue with the browser it had before.

**Data flow**: It reads whatever durable identity the concrete lease has for the session, such as a hosted-browser session ID or a stable endpoint string. It returns that identity as a plain string that can be stored and later passed back to a provider.

**Call relations**: The lease provides this token to higher-level browser or turn code, which can persist it. Later, that saved value is handed to `CdpProvider.reattach` so the provider can try to rebuild a lease around the existing browser session.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This method tells the browser where it can open a workspace file. It smooths over the difference between a browser that shares the sandbox filesystem and a remote browser that needs the file uploaded first.

**Data flow**: It receives a sandbox path and a `read` function that can asynchronously provide the file bytes. A sandbox-local implementation can simply return the same path, because Chrome can already see the file. A remote implementation can call `read`, send those bytes to the remote service, and return the remote path or URL where that browser can access the file.

**Call relations**: The browser-driving engine uses this when it needs Chrome to interact with a file from the turn workspace. The exact file movement is delegated to the concrete lease because only the provider knows where its Chrome is running.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: This method returns the place where the leased Chrome should write downloads. It lets the browser engine configure downloads without knowing whether Chrome is local to the sandbox or hosted somewhere else.

**Data flow**: It takes no extra input beyond the lease. The concrete lease returns a directory or provider-specific location suitable for completed browser downloads. That location is then used when telling Chrome where downloads should land.

**Call relations**: The browser engine calls this before or during download setup. Later, when Chrome reports a completed download by its CDP download identifier, related code can call `CdpLease.fetch_download` to bring the downloaded bytes back.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This method retrieves the bytes of a completed browser download. It gives the rest of the system one way to fetch downloads whether they were written in a sandbox folder or stored by a hosted browser provider.

**Data flow**: It receives a download `guid`, which is Chrome’s identifier for a completed download when downloads are named through CDP. The concrete lease uses that identifier to find the download in the right place, reads or fetches its bytes, and returns those bytes to the caller.

**Call relations**: This pairs with `CdpLease.download_dir`. The browser engine configures Chrome’s download location through the lease, then asks the same lease to retrieve a finished download when Chrome reports the identifier.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: This method releases the browser lease at the end of a turn. It prevents remote sessions, temporary resources, or provider-side holds from lingering after they are no longer needed.

**Data flow**: It takes no extra input. The concrete lease performs whatever cleanup is appropriate: it may do nothing for a static endpoint, or it may tell a hosted browser provider to release or end the session. It returns no value, but it changes the outside world by freeing the lease’s resources.

**Call relations**: Turn cleanup code calls this when browser work is finished. The implementation belongs to the provider that created the lease, because that provider knows whether anything actually needs to be closed.


##### `CdpProvider.lease`  (lines 83–83)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This method creates a fresh browser lease for a turn. It is the standard way for the system to ask a configured browser provider, “Please give this turn a Chrome I can connect to.”

**Data flow**: It may receive a `Sandbox`, meaning the isolated workspace for the turn. A sandbox-based provider can use that sandbox to find or start Chrome inside it, while a remote provider may ignore the sandbox and create a hosted session. The result is a `CdpLease` that the turn can use for connection, file placement, downloads, and cleanup.

**Call relations**: Higher-level startup or turn orchestration calls this when no previous browser session is being reused. The returned lease then supplies the endpoint to the browser engine and remains active until `CdpLease.aclose` is called.


##### `CdpProvider.reattach`  (lines 85–85)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This method tries to reconnect to a browser session that was created earlier. It is used during recovery so the system can continue with an existing page when possible instead of always starting over.

**Data flow**: It receives a saved token and may also receive the recovered turn’s sandbox. The concrete provider uses those inputs to find the old browser session and wrap it in a new `CdpLease`. If the session is gone, expired, or unreachable, it raises `SessionGone` so the caller knows to create a fresh lease instead.

**Call relations**: Recovery code calls this after reading a token that originally came from `CdpLease.token`. If reattachment succeeds, the returned lease is used like any other lease; if it fails with `SessionGone`, orchestration falls back to `CdpProvider.lease`.


### Hosted browser adapters
Connects the system to hosted browser services for remote Chrome sessions and hosted browsing tools.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session setup, browser run, recovery reattach, file transfer, teardown`

The main problem this file solves is that the browser is not running on the same machine as the rest of the system. Because Chrome lives inside Browserbase, the code cannot simply point to local files or local download folders. This file acts like a careful travel agent: it books a remote browser, gives the system the address to connect to, ships files there when a web page needs them, collects downloads back through Browserbase’s API, and cancels the booking when the run ends.

A browser run gets a Browserbase session, which is the live remote Chrome instance. It also gets a Browserbase Context, which is saved browser state such as cookies and local storage. The Context is kept only for the current conversation so recovery can reconnect without losing logins, but it is deleted when the run closes so authenticated browser state does not leak into future work.

The file also protects the host process. Uploads and downloads are capped in size so an unexpected web page cannot push huge files into memory. API replies are checked for required fields, and broken or expired sessions are reported clearly. The Browserbase API key is read from a host-side credential slot on each request, so it never enters the sandbox and key rotation can take effect on the next call.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new Browserbase-hosted Chrome session using an existing Browserbase Context. This is how a browser run gets a live remote browser and the connection URL needed to drive it.

**Data flow**: It receives a context ID. It sends Browserbase a request asking for a session that uses that Context and has a long enough timeout. It reads the returned session ID and connection URL, then returns both to the caller.

**Call relations**: This is called when the provider is leasing a new browser run. It relies on BrowserbaseApi._json to send the request and parse the reply, then uses _field to make sure the required returned values are present.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still usable and, if it is, gets its connection URL. This supports recovery after the system already has a token for a previous session.

**Data flow**: It receives a session ID. It asks Browserbase for that session’s current status. If the status says the session is still running or pending, it returns the connection URL; otherwise it raises SessionGone to say the old browser cannot be reused.

**Call relations**: BrowserbaseCdpProvider.reattach uses this after decoding a saved token. Internally it sends the API request through BrowserbaseApi._json and validates returned fields with _field.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to release a session when the browser run is finished. This prevents the remote browser from continuing to run or bill after it is no longer needed.

**Data flow**: It receives a session ID. It sends a status update to Browserbase marking the session for release. It does not return data; the important effect is the remote cleanup request.

**Call relations**: BrowserbaseLease.aclose calls this during teardown before deleting the Context. It uses BrowserbaseApi._json for the HTTP request.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a new Browserbase Context, which is a temporary saved browser state area. The Context lets a re-created session in the same conversation keep cookies, logins, and local storage.

**Data flow**: It sends Browserbase a create-context request with an empty body. It reads the returned context ID and returns that ID.

**Call relations**: BrowserbaseCdpProvider._context calls this when there is no stored Context yet for the current conversation. The function uses BrowserbaseApi._json to talk to Browserbase and _field to validate the returned ID.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context when the browser run is over. This keeps a subagent’s authenticated browser state from living longer than the subagent itself.

**Data flow**: It receives a context ID. It sends a delete request to Browserbase. It returns nothing, but the remote Context should be removed.

**Call relations**: BrowserbaseLease.aclose calls this after asking Browserbase to release the session. It uses BrowserbaseApi._send directly because it only needs the HTTP request to succeed, not a JSON body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed download from Browserbase session storage. It waits briefly for Browserbase’s download listing to catch up, and refuses downloads that are too large or have no reported size.

**Data flow**: It receives a session ID and a download GUID, which is the browser’s name for the downloaded file. It repeatedly asks Browserbase for the session’s downloads, looks for a matching filename, checks the reported size, then downloads and returns the file bytes. If the file never appears or the listing is unsafe, it raises an error.

**Call relations**: BrowserbaseLease.fetch_download calls this when the browser engine wants downloaded bytes. This function uses BrowserbaseApi._json to list downloads, _size and _field to validate the listing, BrowserbaseApi._send to fetch the actual bytes, and asyncio.sleep to wait between listing attempts.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a file into a Browserbase session so the remote Chrome browser can use it. This is needed because the hosted browser cannot read local workspace paths directly.

**Data flow**: It receives a session ID, a remote filename, and file bytes. It sends those bytes through Browserbase’s upload endpoint. It returns nothing; the result is that the file becomes available inside the remote browser’s upload area.

**Call relations**: BrowserbaseLease.place_file calls this after reading and naming the local file. The actual HTTP transfer goes through BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request and insists that the reply is a JSON object, meaning a dictionary-like response. This gives the rest of the file a safe, predictable shape to read from.

**Data flow**: It receives an HTTP method, a path, and optional request details. It sends the request through BrowserbaseApi._send, parses the response as JSON, checks that the parsed body is an object, and returns that object. If the body is not the expected shape, it raises BrowserbaseError.

**Call relations**: The higher-level API methods use this whenever they expect structured Browserbase data, such as session details, context creation results, or download listings. It delegates the network work to BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the correct API key header. It is the central doorway for all Browserbase network calls in this file.

**Data flow**: It receives an HTTP method, API path, timeout, and request options. It reads the Browserbase API key from the credential store, adds it to the request headers, sends the request with httpx, and returns the response if it succeeded. If Browserbase returns an error status, it raises BrowserbaseError with the status and response text.

**Call relations**: BrowserbaseApi._json, delete_context, download, and upload all use this to reach Browserbase. Because every request passes through here, credential reading, timeouts, test transport injection, and error reporting are kept consistent.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Reads a required string field from a Browserbase JSON response. It turns missing or empty fields into a clear BrowserbaseError instead of letting later code fail mysteriously.

**Data flow**: It receives a response dictionary and a field name. It looks up the value, checks that it is a non-empty string, and returns it. If the value is missing or not a usable string, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi methods use this after receiving Browserbase responses that should contain IDs, connection URLs, statuses, or download IDs. It is a small validation helper that keeps the higher-level flow honest.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the reported size of a listed download. This matters because the process must know the size before pulling a file into memory.

**Data flow**: It receives one download listing entry. It checks that the entry has a numeric size, rejects booleans and missing values, converts the size to an integer, and returns it. If the size cannot be trusted, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.download uses this before fetching a download’s bytes. It is the safety gate that prevents unmeasured files from being accepted.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the remote browser connection endpoint for this lease. The rest of the browser engine uses this endpoint to talk to the hosted Chrome session.

**Data flow**: It reads the lease’s stored connect URL. It wraps that URL in a CdpEndpoint object and returns it.

**Call relations**: The browser runtime calls this after a lease is created or reattached. It does not call Browserbase again; it simply packages the connection URL already obtained during session creation or lookup.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a recovery token that contains everything needed to reattach to this browser run later. The token names the conversation, session, and Context so recovery does not accidentally attach to another run’s browser state.

**Data flow**: It reads the lease’s conversation ID, session ID, and context ID. It formats them into one slash-separated string and returns that string.

**Call relations**: The browser runtime can save this token and later give it to BrowserbaseCdpProvider.reattach. That later path uses _parse_token to unpack the same pieces.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Uploads a workspace file into the remote Browserbase session and returns the path Chrome should use for it. This bridges the gap between local files and a browser running on another machine.

**Data flow**: It receives a workspace path and a callback that reads the file bytes. It extracts the base filename, reads the bytes, rejects files above the upload limit, and chooses a remote name. If another uploaded file already used the same base name for a different path, it prefixes a short hash of the path to avoid overwriting. It uploads the bytes, records the chosen name, and returns the remote upload path.

**Call relations**: The browser engine calls this before setting a file input in the remote page. It calls BrowserbaseApi.upload to send the bytes and uses local naming rules so repeated or conflicting uploads are predictable.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name Browserbase-hosted Chrome accepts. Unlike a local browser, it cannot download to an arbitrary absolute path on this machine.

**Data flow**: It takes no meaningful input beyond the lease. It returns the literal string Browserbase expects for its session download storage.

**Call relations**: The browser engine asks the lease for this directory when configuring downloads. Later, BrowserbaseLease.fetch_download retrieves the actual bytes from Browserbase storage rather than from a local folder.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed browser download from Browserbase storage. It hides the Browserbase download API behind the lease interface the rest of the browser engine expects.

**Data flow**: It receives a download GUID. It passes the current session ID and that GUID to BrowserbaseApi.download, then returns the downloaded bytes.

**Call relations**: The browser engine calls this when it needs the contents of a downloaded file. The real polling, size check, and byte transfer are handed off to BrowserbaseApi.download.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease by releasing the remote browser session, deleting its temporary Browserbase Context, and removing the stored Context reference. This is the cleanup step that stops billing and prevents saved browser state from leaking into later work.

**Data flow**: It reads the session ID, context ID, conversation ID, API object, and scoped store from the lease. It first asks Browserbase to release the session. Whether that succeeds or fails, it then tries to delete the Context. Whether that succeeds or fails, it deletes the stored Context key for this conversation.

**Call relations**: The runtime calls this when the browser run ends. It calls BrowserbaseApi.release_session, BrowserbaseApi.delete_context, and the store delete operation in nested cleanup blocks so one failure does not skip the later cleanup steps.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new Browserbase-backed browser lease for a browser turn. It ties the hosted session to the current sandbox conversation so the right temporary browser state is reused within that run.

**Data flow**: It receives the current sandbox, reads its conversation ID, creates a BrowserbaseApi and scoped store, gets or creates the conversation’s Context, then creates a Browserbase session using that Context. It returns a BrowserbaseLease containing the API object, store, IDs, and connection URL.

**Call relations**: The core browser system calls this when it needs a new CDP lease. It calls BrowserbaseCdpProvider._context to prepare saved state, BrowserbaseApi.create_session to book the remote browser, and then returns a BrowserbaseLease for the rest of the run.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context for a conversation, or creates and stores one if none exists yet. This preserves login state across a re-minted session within the same browser run.

**Data flow**: It receives a Browserbase API object, a scoped store, and a conversation ID. It builds a store key, checks whether a context ID is already stored there, and returns it if valid. Otherwise it creates a new Context through Browserbase, saves the new ID in the store, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It reads and writes through ScopedStore and calls BrowserbaseApi.create_context only when a new Context is needed.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Rebuilds a lease around an already-existing Browserbase session from a saved token. This is used during recovery when the previous session may still be alive.

**Data flow**: It receives a token and optionally a sandbox. It parses the token into conversation ID, session ID, and context ID, creates a Browserbase API object, asks Browserbase whether the session is still live, and returns a BrowserbaseLease using the recovered details and connection URL. If the token is bad or the session is gone, the flow raises SessionGone.

**Call relations**: The core browser system calls this when it has a saved lease token. It uses _parse_token to decode the token and BrowserbaseApi.live_session to confirm that Browserbase can still serve the session.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Decodes a recovery token into the three pieces needed to reattach: conversation ID, session ID, and context ID. Bad tokens are treated the same as gone sessions so the caller can fall back cleanly.

**Data flow**: It receives a token string. It splits the string on slashes, checks that the session and context parts exist, converts the conversation part into a UUID, and returns all three values. If any part is missing or malformed, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this before contacting Browserbase. Its output becomes the identity information used to check the live session and build a BrowserbaseLease.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It declares the Browserbase API key credential slot and registers Browserbase as a CDP provider option.

**Data flow**: It takes no input. It builds and returns a Manifest containing the extension name, version, required credential description, and a provider specification that can construct BrowserbaseCdpProvider when given credentials.

**Call relations**: The extension loading system calls this to discover what the file provides. The returned manifest is what makes the browserbase backend selectable through configuration.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This extension is a bridge between UFO tools and the Browser Use hosted API. Instead of opening and steering a browser locally, it sends a plain-language task to Browser Use, waits for their cloud agent to finish, and returns the result. This matters because other parts of the project expect tools named `browser_task` and `wide_browse`; keeping those names means research agents and evaluation runs can use this hosted browser provider without being rewritten.

The file defines the tool inputs, the tool registration manifest, and the run lifecycle. A `HostedRun` is the main worker. It gets the Browser Use API key from the host-side credential store, starts a remote run, polls until the run finishes or times out, collects the final text result, and optionally downloads output files into the sandbox workspace. The API key is only used for Browser Use API calls; downloaded files are fetched from their own HTTPS links without sending that key.

The single-task tool runs one careful browser session and saves output files. The batch tool reads a list of sites or URLs from a workspace file, launches several cheaper browser runs in parallel, and writes all rows to `wide_browse.json`. The code is careful about safety: it limits task size, batch size, downloaded file count and size, rejects unsafe output paths, and reports skipped files instead of silently ignoring them.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one Browser Use job from beginning to end. It starts or reattaches to a hosted browser run, waits for completion, handles timeouts, gathers the final result, and optionally brings output files into the workspace.

**Data flow**: It receives the tool context, the task text, a timeout, and optionally a deduplication key used to avoid paying for the same run twice. It reads the extension store and the Browser Use API key, opens an HTTP client, starts or resumes a run, watches its status, cancels it if the local timeout expires, then reads the run summary and downloads allowed output files. It returns a `RunOutcome` containing the final status, result or error text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the central path used by both tool handlers. It calls `_start` to create or recover the remote run, `_watch` and `_status` to follow progress, `_json` to validate API replies, and `_collect` to copy output files back after the run ends.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Creates a Browser Use run, or reuses a previously stored run when the same deduplication key is seen again. This prevents repeated calls from accidentally starting duplicate paid browser sessions.

**Data flow**: It receives an HTTP client, the extension store, the task text, and an optional deduplication key. If the key already has a saved run record, it validates and returns that record. Otherwise it posts the task, model, cost limit, and browser settings to Browser Use, extracts the new run ID and workspace ID, stores them if a key was provided, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this at the beginning of a run. `_start` depends on `_json` to turn the HTTP response into a usable object and `_text` to require the important string fields from that object.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits until a remote Browser Use run reaches a final state. It is the polling loop, like checking a delivery tracker every few seconds until the package is delivered or cancelled.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the current status, stops when that status is terminal, and sleeps briefly between checks. It returns the final status string.

**Call relations**: `HostedRun.execute` calls this after starting a run. `_watch` delegates each actual status read to `_status`, keeping the repeated wait-and-check behavior in one place.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is used both during normal polling and just before cancellation, so the code does not cancel a run that finished at the last moment.

**Data flow**: It receives an HTTP client and a run ID. It sends a GET request to the run status endpoint, checks that the reply is valid JSON using `_json`, extracts the required `status` field using `_text`, and returns that status as a string.

**Call relations**: `_watch` calls this repeatedly while waiting. `HostedRun.execute` also calls it on timeout before deciding whether to cancel the remote run.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies a completed run’s output files into the sandbox workspace, when file saving is enabled. It also records files it refused to fetch because they were too large, missing a usable download URL, or beyond the listing limit.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If this kind of run is not supposed to save files, it returns empty results. Otherwise it asks Browser Use for a limited file list, validates each entry, checks that each path stays inside the workspace, downloads acceptable HTTPS files, writes them into the sandbox, and returns three things: saved files, skipped files, and whether more files exist.

**Call relations**: `HostedRun.execute` calls this after reading the run summary. `_collect` uses `_json` for the file listing, `contained_relative` to prevent workspace escape, and `_download` to fetch each accepted file.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a Browser Use-provided link without sending the project’s Browser Use API key. It insists on HTTPS so the file is fetched over an encrypted connection.

**Data flow**: It receives a URL. It rejects the URL if it does not start with `https://`, then opens a plain HTTP client with no Browser Use credential header, fetches the file, checks for download errors, and returns the raw bytes.

**Call relations**: `_collect` calls this for each output file that is small enough and has a usable URL. This keeps storage downloads separate from authenticated Browser Use API calls.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a safe dictionary, or raises a clear Browser Use-specific error. This prevents bad API replies from becoming confusing crashes later.

**Data flow**: It receives an HTTP response. It first rejects error status codes, then tries to parse the body as JSON, then checks that the JSON value is an object rather than a list or string. It returns a dictionary with string keys.

**Call relations**: `execute`, `_start`, `_status`, and `_collect` all use this whenever they read Browser Use API responses. It is the shared gatekeeper for response quality.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts a required text field from a Browser Use response. It gives a helpful `BrowserUseError` if the field is missing or not text.

**Data flow**: It receives a response dictionary and the name of the field to read. It looks up that field, verifies it is a string, and returns it. If the value is absent or the wrong type, it raises an error that includes the response body.

**Call relations**: `_start` uses this to read the new run and workspace IDs. `_status` uses it to read the current run status.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 344–378)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the public `browser_task` tool. It runs one fresh cloud browser session starting at a given URL, then returns the agent’s result and any saved files.

**Data flow**: It receives the tool context and validated user arguments: URL, task, task name, and timeout. It builds a self-contained task prompt, creates a `HostedRun` with the more capable model and a higher cost limit, executes it, and turns the outcome into a `ToolResult`. If the run timed out, it returns an error message; otherwise it returns JSON containing the result text, saved file paths, skipped files, and whether more files were available.

**Call relations**: The tool registry calls this when an agent invokes `browser_task`. It relies on `HostedRun.execute` for the actual Browser Use API work, then wraps the answer in `TextContent` and `ToolResult` for the rest of the system.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 381–388)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a file from the sandbox workspace using the sandbox shell. It quotes the path safely so a model-supplied filename cannot accidentally become a shell command.

**Data flow**: It receives the tool context and a path. It builds a `cat` command with shell-safe quoting, runs it in the sandbox, and either returns the file contents or raises a `ValueError` with the command’s error text.

**Call relations**: `_read_lines` uses this to read the entity list for batch browsing. `_wide_browse` also uses it directly to read the JSON schema file.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 391–399)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file as a clean list of unique, non-empty lines. For `wide_browse`, each line becomes one site or URL to visit.

**Data flow**: It receives the tool context and a file path. It reads the full file with `_read_file`, splits it into lines, trims whitespace, skips blank lines, removes duplicates while keeping the first occurrence order, and returns the resulting list of strings.

**Call relations**: `_wide_browse` calls this before launching the batch of hosted browser runs.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 402–448)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the public `wide_browse` tool. It runs many small Browser Use jobs in parallel, one per URL or site name, then writes a combined JSON results file.

**Data flow**: It receives the tool context and arguments pointing to an entity list, a prompt template, and an output schema file. It reads and deduplicates the entities, checks the batch size limit, reads the schema, creates a shared `HostedRun` configured for cheaper runs without file downloads, and starts visits with a semaphore, which is a counter that limits how many jobs run at once. It gathers all results, converts per-entity failures into rows instead of failing the whole batch, writes `wide_browse.json`, and returns JSON containing the rows and output filename.

**Call relations**: The tool registry calls this when an agent invokes `wide_browse`. It uses `_read_lines` and `_read_file` for workspace inputs, defines `visit` for one entity’s browser run, uses `asyncio.gather` to run the batch, and returns the final data as a `ToolResult`.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 418–433)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the Browser Use task for one entity inside a `wide_browse` batch. It turns one URL or site name into one result row.

**Data flow**: It receives one entity from the surrounding `_wide_browse` function. It waits for permission from the semaphore so the batch does not launch too many jobs at once, substitutes the entity into the prompt template, appends the output schema when present, runs the hosted browser job with a per-entity deduplication key, and returns a dictionary with the entity, run status, and result text.

**Call relations**: `_wide_browse` creates this inner helper and passes one call per entity to `asyncio.gather`. If this helper raises an ordinary exception, `_wide_browse` records that exception as that entity’s failed row rather than throwing away the whole batch.


##### `manifest`  (lines 471–486)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO runtime: its name, version, tools, prompt text, and required credential. Without this, the runtime would not know how to load or expose `browser_task` and `wide_browse`.

**Data flow**: It takes no input. It packages the two tool definitions, the browser prompt section read from the companion Markdown file, and the Browser Use API key credential slot into a `Manifest`, then returns it.

**Call relations**: The extension loader calls this during startup. The returned manifest is what registers the tool handlers and tells the host that it must provide a Browser Use API key.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandboxed Chrome adapter
Runs and manages a private headless Chrome instance inside a turn sandbox through DevTools.

### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `active when a turn needs a sandboxed browser; lease startup, reattach, download reading, and cleanup`

This file solves a practical isolation problem: each hosted turn may need a browser, but sibling turns can share the same sandbox container. So the browser cannot use one shared profile, port, process, or cleanup target. Instead, this provider gives each turn its own “browser stack”: a Chrome process, a browser profile, a downloads folder, a small DevTools proxy, and an egress bridge for outbound web traffic.

The flow is like reserving a numbered parking bay before starting a car. The lease id reserves a slot, and that slot gives the browser three private ports. Then the file starts an authenticated egress bridge, starts Chrome, starts a DevTools proxy, and returns a CDP endpoint. CDP means “Chrome DevTools Protocol,” the WebSocket-based control interface used to drive Chrome.

Two proxy pieces are important. Chrome rejects DevTools requests with the wrong Host header, so the CDP proxy rewrites that header and then passes WebSocket bytes through unchanged. Separately, Chromium does not reliably use proxy credentials from normal environment variables, so the egress bridge adds the current turn’s proxy authorization before traffic leaves the sandbox.

The file is also careful about crashes and retries. If a worker dies during launch, the durable turn id can find the existing stack again. If launch fails, it reads short log tails from inside the sandbox so callers see the browser error, not only a generic timeout.

#### Function details

##### `BrowserStack.root`  (lines 89–90)

```
def root(self) -> str
```

**Purpose**: Gives the main directory inside the sandbox where this one browser lease stores its files. This keeps one turn’s browser files separate from every other turn’s browser files.

**Data flow**: It reads the stack’s lease id and combines it with the shared browser leases directory. The result is a sandbox path such as the root folder for logs, profile data, downloads, and helper scripts.

**Call relations**: Other path properties build on this root path. Command-building helpers use those paths when they create launch and cleanup scripts for the stack.


##### `BrowserStack.chrome_log`  (lines 93–94)

```
def chrome_log(self) -> str
```

**Purpose**: Names the log file where Chromium writes its output. This matters because browser startup failures are diagnosed from this file.

**Data flow**: It takes the stack root path and appends the Chromium log filename. The output is a full path inside the sandbox.

**Call relations**: Browser startup commands write to this path, and failure-reporting code reads it when launch does not succeed.


##### `BrowserStack.chrome_pid`  (lines 97–98)

```
def chrome_pid(self) -> str
```

**Purpose**: Names the file that records Chromium’s process id. A process id is the operating system number used to find or stop a running process.

**Data flow**: It takes the stack root path and appends the Chromium pid filename. The result is a sandbox path to a small text file containing the browser process id.

**Call relations**: Launch scripts write this file after starting Chrome. Cleanup scripts read it later so they can stop only this lease’s browser.


##### `BrowserStack.chrome_profile`  (lines 101–102)

```
def chrome_profile(self) -> str
```

**Purpose**: Gives Chrome its own user profile directory. This prevents cookies, cache, settings, and browser state from leaking between turns.

**Data flow**: It combines the stack root path with a profile folder name. The output is passed to Chrome as its user-data directory.

**Call relations**: The browser launch command uses this path when starting Chrome for a lease.


##### `BrowserStack.download_dir`  (lines 105–106)

```
def download_dir(self) -> str
```

**Purpose**: Gives Chrome a private downloads folder inside the sandbox. This is where completed downloads can later be read back by the lease.

**Data flow**: It combines the stack root path with a downloads folder name. The result is a sandbox path where Chrome stores downloaded files.

**Call relations**: The browser launch script creates and uses this directory. The lease exposes it through download_dir and reads files from it in fetch_download.


##### `BrowserStack.proxy_log`  (lines 109–110)

```
def proxy_log(self) -> str
```

**Purpose**: Names the log file for the DevTools proxy. This helps explain failures when the proxy cannot start or serve requests.

**Data flow**: It appends the proxy log filename to the stack root path. The result is a path inside the sandbox.

**Call relations**: Browser bring-up writes proxy output here, and failure reporting can include its tail when startup times out.


##### `BrowserStack.proxy_pid`  (lines 113–114)

```
def proxy_pid(self) -> str
```

**Purpose**: Names the file that records the DevTools proxy process id. This lets cleanup stop the right proxy process later.

**Data flow**: It combines the stack root path with the proxy pid filename. The output is a sandbox path to a pid file.

**Call relations**: The browser bring-up script writes this file after launching the proxy. Stack cleanup uses it to stop the proxy for this lease.


##### `BrowserStack.proxy_script`  (lines 117–118)

```
def proxy_script(self) -> str
```

**Purpose**: Names where the generated DevTools proxy Python script will be written inside the sandbox. The proxy exists because Chrome’s DevTools endpoint expects a localhost Host header.

**Data flow**: It combines the stack root path with the proxy script filename. The result is a path used by the browser startup command.

**Call relations**: The browser-up command writes the embedded proxy program to this path, then starts it as part of the browser stack.


##### `BrowserStack.bridge_script`  (lines 121–122)

```
def bridge_script(self) -> str
```

**Purpose**: Names where the generated egress bridge Python script will be written inside the sandbox. The bridge adds proxy authentication for Chrome’s outbound web traffic.

**Data flow**: It combines the stack root path with the bridge script filename. The result is a path used when starting the detached bridge task.

**Call relations**: The egress-bridge command writes the embedded bridge program to this path before executing it.


##### `BrowserStack.task_base`  (lines 125–126)

```
def task_base(self) -> str
```

**Purpose**: Gives the base name for files belonging to the detached egress bridge task. Related pid, exit, lock, and log files are built from this base.

**Data flow**: It combines the stack root path with a fixed task-name prefix. Later code appends suffixes such as .pid or .log.

**Call relations**: Bridge startup, bridge replacement, stack cleanup, and reattach checks all use this base to identify the same background task.


##### `BrowserStack.lock_path`  (lines 129–130)

```
def lock_path(self) -> str
```

**Purpose**: Names the slot lock that proves this lease owns its assigned port group. This prevents cleanup or recovery from touching another lease’s browser stack.

**Data flow**: It combines the shared slot-lock directory with the stack’s slot number. The result is a lock path inside the sandbox.

**Call relations**: Allocation creates this lock, recovery checks it, and cleanup removes it only if it still points to this lease id.


##### `_stack`  (lines 133–145)

```
def _stack(lease_id: str, slot: int) -> BrowserStack
```

**Purpose**: Builds a BrowserStack object from a lease id and slot number, while checking that both are safe and valid. It turns a small allocation record into all the concrete ports and paths needed for the browser stack.

**Data flow**: It receives a lease id and a slot number. It validates the id format and slot range, calculates three ports from the slot, and returns a BrowserStack containing the lease id, slot, Chrome port, DevTools proxy port, and egress bridge port.

**Call relations**: The provider uses this after finding an allocation during lease startup. Token recovery also uses it after decoding a saved token, so bad or stale tokens cannot become arbitrary sandbox paths or ports.

*Call graph*: called by 2 (_lease, _stack_from_token); 1 external calls (__init__).


##### `_assignments`  (lines 681–682)

```
def _assignments(values: dict[str, object]) -> str
```

**Purpose**: Turns a dictionary of Python values into simple Python assignment lines. This is used to inject configuration into short scripts that will run inside the sandbox.

**Data flow**: It receives names and values, formats each as a Python assignment using safe literal representation, and returns one text block. Nothing is executed here; it only builds script text.

**Call relations**: All command-building helpers call this before embedding their settings into allocation, startup, cleanup, and recovery scripts.

*Call graph*: called by 8 (_abandon_allocation_command, _allocate_command, _bridge_down_command, _browser_up_command, _egress_bridge_up_command, _find_allocation_command, _resolve_command, _stack_down_command).


##### `_allocate_command`  (lines 685–731)

```
def _allocate_command(lease_id: str) -> str
```

**Purpose**: Creates the shell command that reserves a private browser slot inside the sandbox. A slot means one unique lease directory and three local ports.

**Data flow**: It receives the lease id, embeds constants such as lock directories and port ranges, and returns a Python heredoc command. When run in the sandbox, that command creates the lease directory, probes for free ports, makes a symlink lock, records the slot, and prints the slot number.

**Call relations**: SandboxChromeCdpProvider._lease sends this command to the sandbox at the start of a new lease attempt.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_lease).


##### `_find_allocation_command`  (lines 734–769)

```
def _find_allocation_command(lease_id: str) -> str
```

**Purpose**: Creates the shell command that verifies or recovers an existing slot allocation. This is what lets a turn find its browser stack again after an interrupted launch.

**Data flow**: It receives the lease id and returns a command that waits briefly for the lease directory, slot file, ready marker, and matching lock. When successful, the sandbox command prints the slot number; otherwise it exits with an error.

**Call relations**: The provider calls this during fresh leasing after allocation, and during reattach when checking that a token still owns its slot.

*Call graph*: calls 1 internal fn (_assignments); called by 2 (_lease, reattach).


##### `_abandon_allocation_command`  (lines 772–792)

```
def _abandon_allocation_command(lease_id: str) -> str
```

**Purpose**: Creates the shell command that removes a failed or unusable allocation. It is a safety broom for cleaning up a lease directory and any locks that still point to that lease.

**Data flow**: It receives a lease id and returns a command that scans lock files, removes locks owned by that lease, and deletes the lease’s root directory. The result is command text, not cleanup performed locally.

**Call relations**: _abandon_allocation runs this command when lease setup cannot trust the recovered allocation.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_abandon_allocation).


##### `_egress_bridge_up_command`  (lines 795–801)

```
def _egress_bridge_up_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that writes and starts the egress bridge inside the sandbox. The bridge lets Chrome use the sandbox’s authenticated outbound proxy.

**Data flow**: It receives a BrowserStack, embeds the bridge listen host and port, writes the embedded bridge program to the stack’s bridge script path, and returns a command that execs it with Python.

**Call relations**: _start_bridge asks the sandbox to run this command as a detached task before Chrome starts or before a recovered stack is reattached.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_start_bridge).


##### `_browser_up_command`  (lines 804–854)

```
def _browser_up_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that starts the whole browser-facing side of the stack: the DevTools proxy script, Chromium, and readiness checks. It returns the command that should produce the browser WebSocket URL when successful.

**Data flow**: It receives a BrowserStack, embeds paths, ports, Chrome arguments, timeout values, and proxy settings, then returns a shell command. When run in the sandbox, the command writes the proxy script, waits for the egress bridge, starts Chrome, starts the DevTools proxy, and prints the proxied DevTools WebSocket URL.

**Call relations**: SandboxChromeCdpProvider._lease runs this after the bridge task starts. If it succeeds, _lease passes its printed URL to _endpoint.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_lease).


##### `_stack_down_command`  (lines 857–874)

```
def _stack_down_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that stops and removes an entire browser stack. It is the main cleanup script for a lease.

**Data flow**: It receives a BrowserStack and embeds its paths, lock, pid files, and timing settings. The returned command stops the bridge supervisor, Chrome, and DevTools proxy if the lease still owns the slot, deletes task files, removes the root directory, and releases the slot lock.

**Call relations**: _stop_stack runs this command when a lease closes, when startup fails after partial creation, or when reattach decides the stack is gone.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_stop_stack).


##### `_bridge_down_command`  (lines 877–891)

```
def _bridge_down_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that stops only the egress bridge task. This is used when reattaching so the bridge can be replaced with one carrying fresh turn authentication.

**Data flow**: It receives a BrowserStack and embeds the lease id, lock path, task base, and timing settings. The returned command checks that the lease still owns the slot, stops the matching bridge task, and removes its task files.

**Call relations**: _stop_bridge runs this command during reattach before _start_bridge launches a new bridge.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (_stop_bridge).


##### `_resolve_command`  (lines 894–939)

```
def _resolve_command(stack: BrowserStack) -> str
```

**Purpose**: Creates the command that checks a recovered browser stack and reads its current DevTools WebSocket URL. It is the reattach version of browser readiness checking.

**Data flow**: It receives a BrowserStack and returns a command that waits for the bridge port, checks the proxied /json/version endpoint, and prints the WebSocket debugger URL. If the bridge exited or the browser no longer answers, it exits with an explanatory error.

**Call relations**: SandboxChromeCdpProvider.reattach runs this after replacing the bridge, then passes the printed URL to _endpoint.

*Call graph*: calls 1 internal fn (_assignments); called by 1 (reattach).


##### `SandboxChromeCdpLease.endpoint`  (lines 950–951)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the CDP endpoint for the leased browser. Callers use this endpoint to connect to and drive Chrome.

**Data flow**: It reads the endpoint stored in the lease and returns it unchanged. It does not contact the sandbox or alter the browser.

**Call relations**: The provider creates the lease after startup or reattach. Browser-driving code asks the lease for this endpoint when it is ready to connect.


##### `SandboxChromeCdpLease.token`  (lines 953–954)

```
async def token(self) -> str
```

**Purpose**: Returns a small recovery token for this browser stack. The token lets the system try to reconnect to the same stack after an interruption.

**Data flow**: It reads the lease’s BrowserStack, converts its lease id and slot into a JSON string, and returns that string. It does not include secrets or browser state.

**Call relations**: It delegates the actual token formatting to _stack_token. Later, SandboxChromeCdpProvider.reattach can decode that token through _stack_from_token.

*Call graph*: calls 1 internal fn (_stack_token).


##### `SandboxChromeCdpLease.place_file`  (lines 956–960)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Returns the same file path the caller already has, because this Chrome runs inside the same sandbox where the file lives. No file copy is needed.

**Data flow**: It receives a path and a lazy file reader, ignores the reader, and returns the path unchanged. The before and after are the same path inside the sandbox filesystem.

**Call relations**: Browser-driving code can call this when it needs a browser-accessible file path. Unlike a local browser provider, this one does not have to copy bytes out and back in.


##### `SandboxChromeCdpLease.download_dir`  (lines 962–965)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the sandbox path where this browser writes downloads. Callers need this when configuring or understanding browser download behavior.

**Data flow**: It reads the stack’s download directory path and returns it. It does not create or read files itself.

**Call relations**: The browser startup command creates this directory. fetch_download later reads completed download files from the same place.


##### `SandboxChromeCdpLease.fetch_download`  (lines 967–995)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed download from the sandbox and returns its bytes to the caller. It also protects the host by refusing downloads larger than the configured maximum.

**Data flow**: It receives a download guid, builds the matching sandbox file path, asks the sandbox for the file size, rejects missing or oversized files, then asks the sandbox to base64-encode the file. It decodes that base64 text in a worker thread and returns the original bytes.

**Call relations**: Callers use this after Chrome has downloaded a file. It relies on sandbox.bash commands for disk reads, uses shlex.quote to make the guid safe in the shell command, and uses asyncio.to_thread so base64 decoding does not block the main event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 997–998)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease by stopping and removing its browser stack. This releases ports, kills helper processes, and deletes temporary browser files.

**Data flow**: It reads the lease’s sandbox and BrowserStack, then asks _stop_stack to run cleanup inside the sandbox. It returns nothing if cleanup succeeds.

**Call relations**: Lease owners call this when they are done with the browser. _stop_stack performs the actual sandbox cleanup command.

*Call graph*: calls 1 internal fn (_stop_stack).


##### `SandboxChromeCdpProvider.lease`  (lines 1005–1014)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Public entry point for getting a sandboxed Chrome lease. It checks that the caller supplied a sandbox and that the sandbox has a durable turn id.

**Data flow**: It receives an optional sandbox. If the sandbox or turn id is missing, it raises an error; otherwise it passes the sandbox to _lease and returns the resulting CdpLease.

**Call relations**: The core browser system calls this when it needs a CDP provider. This method keeps the public checks simple and leaves the detailed startup and recovery work to _lease.

*Call graph*: calls 1 internal fn (_lease).


##### `SandboxChromeCdpProvider._lease`  (lines 1016–1065)

```
async def _lease(self, sandbox: Sandbox, retry_recovered_stack: bool) -> CdpLease
```

**Purpose**: Performs the full lease startup flow, including allocation, crash recovery, bridge startup, browser startup, endpoint creation, and cleanup on failure. This is the heart of the provider.

**Data flow**: It reads the sandbox turn id, builds a lease id, runs allocation and allocation-finding commands in the sandbox, builds a BrowserStack, and either reattaches to an interrupted stack or starts a fresh bridge and browser. On success it returns a SandboxChromeCdpLease; on failure it cleans up partial work and raises a clear error.

**Call relations**: SandboxChromeCdpProvider.lease calls this for normal startup. It calls command builders, sandbox.bash, _start_bridge, _browser_up_command, _bring_up_failure, _endpoint, _stack_token, reattach, _abandon_allocation, and _stop_stack as needed to move through startup or recovery.

*Call graph*: calls 12 internal fn (bash, reattach, _abandon_allocation, _allocate_command, _bring_up_failure, _browser_up_command, _endpoint, _find_allocation_command, _stack, _stack_token (+2 more)); called by 1 (lease); 2 external calls (__init__, shield).


##### `SandboxChromeCdpProvider.reattach`  (lines 1067–1104)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser stack described by a saved token. This is used after an interruption when the browser may still be running in the sandbox.

**Data flow**: It receives a token and sandbox, decodes the token into a BrowserStack, checks that it belongs to the same turn, verifies the slot is still owned, replaces the egress bridge, resolves the browser’s current WebSocket URL, builds an endpoint, and returns a new lease. If any check fails, it cleans up and raises SessionGone.

**Call relations**: _lease calls this when allocation shows the turn already has a stack. It uses _stack_from_token, _find_allocation_command, _stop_bridge, _start_bridge, _resolve_command, _endpoint, and _stop_stack to safely reconcile the old stack with the current sandbox session.

*Call graph*: calls 8 internal fn (bash, _endpoint, _find_allocation_command, _resolve_command, _stack_from_token, _start_bridge, _stop_bridge, _stop_stack); called by 1 (_lease); 3 external calls (__init__, __init__, shield).


##### `_endpoint`  (lines 1107–1113)

```
async def _endpoint(sandbox: Sandbox, stack: BrowserStack, local_url: str) -> CdpEndpoint
```

**Purpose**: Builds the public CDP endpoint that callers should dial. It translates the sandbox’s internal proxy port into the carrier-visible WebSocket address.

**Data flow**: It receives a sandbox, BrowserStack, and local WebSocket URL printed by Chrome’s version endpoint. It asks the sandbox to expose or dial the stack’s DevTools proxy port, chooses ws or wss depending on whether TLS is used, keeps the carrier-provided headers, extracts the WebSocket path, and returns a CdpEndpoint.

**Call relations**: Both fresh startup and reattach call this after they know the browser is answering. It relies on Sandbox.dial for transport details and _ws_path to ensure the browser URL is local before using its path.

*Call graph*: calls 2 internal fn (dial, _ws_path); called by 2 (_lease, reattach); 1 external calls (__init__).


##### `_start_bridge`  (lines 1116–1128)

```
async def _start_bridge(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Starts the egress bridge as a detached sandbox task. Detached means it keeps running independently while the browser lease is alive.

**Data flow**: It receives a sandbox and BrowserStack, builds the bridge startup command, and asks the sandbox to run it as a named background task. If startup reports a nonzero exit code, it raises an error with the task output.

**Call relations**: _lease calls this before starting Chrome, and reattach calls it after stopping an old bridge. The command text comes from _egress_bridge_up_command.

*Call graph*: calls 2 internal fn (bash_task, _egress_bridge_up_command); called by 2 (_lease, reattach).


##### `_stop_bridge`  (lines 1131–1140)

```
async def _stop_bridge(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Stops the current egress bridge so it can be replaced. This is narrower than stopping the whole browser stack.

**Data flow**: It receives a sandbox and BrowserStack, builds the bridge cleanup command, runs it in the sandbox, and raises an error if the command fails.

**Call relations**: SandboxChromeCdpProvider.reattach calls this before _start_bridge. That sequence gives the recovered browser a fresh bridge tied to the current turn’s authentication.

*Call graph*: calls 2 internal fn (bash, _bridge_down_command); called by 1 (reattach).


##### `_abandon_allocation`  (lines 1143–1149)

```
async def _abandon_allocation(sandbox: Sandbox, lease_id: str) -> None
```

**Purpose**: Best-effort cleanup for an allocation that could not be trusted or completed. It deliberately ignores its own cleanup errors so the original allocation failure can be reported.

**Data flow**: It receives a sandbox and lease id, builds the abandon-allocation command, and runs it in the sandbox with a timeout. If that cleanup attempt itself fails, the exception is swallowed.

**Call relations**: SandboxChromeCdpProvider._lease calls this when allocation recovery produces invalid data. The command text comes from _abandon_allocation_command.

*Call graph*: calls 2 internal fn (bash, _abandon_allocation_command); called by 1 (_lease).


##### `_stop_stack`  (lines 1152–1161)

```
async def _stop_stack(sandbox: Sandbox, stack: BrowserStack) -> None
```

**Purpose**: Runs the full stack cleanup command and reports cleanup failure if it cannot stop the stack. This is the shared shutdown path.

**Data flow**: It receives a sandbox and BrowserStack, builds the stack-down command, runs it in the sandbox, and checks the exit code. On success it returns nothing; on failure it raises an error with sandbox output.

**Call relations**: The lease calls this from aclose. The provider also uses it when startup fails, when reattach is cancelled, or when a recovered stack proves unusable.

*Call graph*: calls 2 internal fn (bash, _stack_down_command); called by 3 (aclose, _lease, reattach).


##### `_stack_from_token`  (lines 1164–1175)

```
def _stack_from_token(token: str) -> BrowserStack
```

**Purpose**: Turns a saved recovery token back into a BrowserStack, or declares the session gone if the token is invalid. This prevents malformed tokens from being used as trusted state.

**Data flow**: It receives a token string, parses it as JSON, checks the token version and field types, and passes the lease id and slot to _stack for stricter validation. It returns a BrowserStack or raises SessionGone.

**Call relations**: SandboxChromeCdpProvider.reattach calls this at the start of recovery. It uses json.loads to decode the token and _stack to validate and compute paths and ports.

*Call graph*: calls 1 internal fn (_stack); called by 1 (reattach); 2 external calls (__init__, loads).


##### `_stack_token`  (lines 1178–1183)

```
def _stack_token(stack: BrowserStack) -> str
```

**Purpose**: Creates the compact JSON token that identifies a browser stack for later recovery. The token records only the version, lease id, and slot.

**Data flow**: It receives a BrowserStack, selects its lease id and slot, adds the token version, and serializes the data as stable JSON. The output is a string suitable for storing and passing back to reattach.

**Call relations**: SandboxChromeCdpLease.token calls this for callers. _lease also uses it when it needs to try reattaching to a stack discovered during allocation.

*Call graph*: called by 2 (token, _lease); 1 external calls (dumps).


##### `_bring_up_failure`  (lines 1186–1201)

```
async def _bring_up_failure(sandbox: Sandbox, stack: BrowserStack, result: ExecResult) -> str
```

**Purpose**: Builds a useful error message when browser startup fails. If the sandbox carrier killed the startup command for timing out, this function fetches browser logs so the real cause is not lost.

**Data flow**: It receives the sandbox, BrowserStack, and failed execution result. If the command did not time out, it returns the reported stderr or stdout. If it timed out, it asks the sandbox for the tail of the Chrome, proxy, and bridge logs, then returns a message combining the timeout with those log lines.

**Call relations**: SandboxChromeCdpProvider._lease calls this when _browser_up_command exits unsuccessfully. It uses sandbox.bash only on the failure path to gather short diagnostic logs.

*Call graph*: calls 1 internal fn (bash); called by 1 (_lease).


##### `_ws_path`  (lines 1204–1209)

```
def _ws_path(url: str, chrome_port: int) -> str
```

**Purpose**: Extracts the path part of Chrome’s local WebSocket debugger URL. It also checks that the URL really points to the expected local Chrome port.

**Data flow**: It receives a URL string and Chrome port, trims whitespace, and removes an allowed localhost prefix. It returns the remaining path, such as the DevTools browser id path. If the URL is not local to the expected port, it raises an error.

**Call relations**: _endpoint calls this while converting Chrome’s sandbox-local URL into the carrier-visible endpoint. This keeps the carrier address and TLS facts from Sandbox.dial while using only Chrome’s resolved WebSocket path.

*Call graph*: called by 1 (_endpoint).


##### `manifest`  (lines 1212–1221)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger system. It registers the sandbox_chrome CDP provider under its backend name.

**Data flow**: It creates a Manifest containing the extension name, version, and a CDP provider specification. The provider spec says that requests for the sandbox_chrome backend should build a SandboxChromeCdpProvider.

**Call relations**: The extension loading system calls this to discover what this file offers. The returned manifest is how the rest of the project learns that this CDP backend exists.

*Call graph*: 2 external calls (__init__, __init__).

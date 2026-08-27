# Browser Providers and Delegated Browser Agents  `stage-12.1.4`

This stage is shared behind-the-scenes support for doing work in a web browser. The core system may need Chrome, but it should not care where that Chrome comes from. The browser contract in core/src/ufo/browser.py is the common plug shape: it says how to get a Chrome connection for one job, so the rest of UFO can drive a local, remote, or extension-provided browser in the same way.

The provider files are different “power outlets” for that plug. The sandbox Chrome extension starts or reuses Chrome inside the same safe sandbox as the conversation, opens its control socket, and can fetch downloaded files back out. The Browserbase extension creates a short-lived remote Chrome session, connects UFO to it, moves files in and out, then cleans it up. The Browser Use extension delegates whole browsing jobs to a hosted automation service, either one task or many in parallel.

The browser subagent profile is different: it defines a specialized child worker for browser automation, including its instructions, tools, inputs, outputs, and model.

## Files in this stage

### Remote Chrome provider
Browserbase integration supplies a hosted Chrome session, connects UFO to it, transfers files, and tears the session down.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser session startup, file transfer, recovery reattach, and teardown`

A normal browser can read and write files on the same machine as the program. Browserbase is different: Chrome runs on Browserbase’s servers. This file is the bridge between UFO and that remote browser.

When a browser turn starts, the provider creates or reuses a Browserbase Context. A Context is like a small suitcase for browser state, such as logins and local storage, during that one conversation. It then creates a fresh Browserbase session using that Context and returns a lease, which is the object UFO uses while the remote browser is alive.

The file also solves two practical remote-browser problems. First, a local file path means nothing to Browserbase, so `place_file` reads the workspace file, uploads its bytes through Browserbase’s upload API, and returns the remote path Chrome can use. Second, downloads land in Browserbase session storage, not on this program’s disk, so `fetch_download` asks Browserbase for the finished file bytes.

Cleanup matters because hosted sessions can keep running and billing. When the lease closes, it asks Browserbase to release the session, deletes the Context, and removes the stored Context id. If Browserbase says an old session is gone, the code reports that clearly so UFO can recover instead of trying to drive a dead browser.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new hosted Browserbase browser session tied to an existing Browserbase Context. UFO uses this when a browser run needs a fresh remote Chrome to connect to.

**Data flow**: It receives a Context id. It sends Browserbase a request that says to use that Context, keep the Context persistent, and give the session a long enough timeout. It returns two strings: the new session id and the WebSocket-style connection URL UFO will use to talk to Chrome.

**Call relations**: This is called during leasing after the provider has found or created the Context. It relies on `BrowserbaseApi._json` to talk to Browserbase and `_field` to make sure the reply contains the required ids.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still usable and returns its connection URL if it is. This is used when UFO tries to reconnect to a previous browser session.

**Data flow**: It receives a session id. It asks Browserbase for that session’s current details, checks that the status means the browser is still alive, and returns the connect URL. If the session is no longer live, it raises `SessionGone`, which tells the caller to stop trying this old session.

**Call relations**: This sits in the reattach path. `BrowserbaseCdpProvider.reattach` calls it after unpacking the saved token, and it uses `_json` and `_field` to safely read Browserbase’s answer.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to stop a hosted browser session. This prevents remote browser work and billing from continuing after UFO is done.

**Data flow**: It receives a session id. It sends Browserbase a request setting the session status to the release value. It returns nothing, but the remote session is asked to shut down.

**Call relations**: This is part of lease cleanup. `BrowserbaseLease.aclose` calls it before deleting the Context so the browser is not still using browser state that is being removed.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a new Browserbase Context, which stores browser state such as cookies and local storage for a run. This lets a recovered session keep the login state it already earned.

**Data flow**: It sends an empty create request to Browserbase’s Context API. It reads the returned Context id and gives that id back to the caller.

**Call relations**: `BrowserbaseCdpProvider._context` calls this only when the extension’s store does not already have a Context id for the current conversation. It uses `_json` for the request and `_field` to validate the reply.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context after the browser run is over. This ensures authenticated browser state does not outlive the subagent conversation that created it.

**Data flow**: It receives a Context id. It sends a delete request to Browserbase. It returns nothing, but Browserbase is asked to remove the stored browser state.

**Call relations**: `BrowserbaseLease.aclose` calls this after releasing the session. It uses `_send` directly because a successful delete does not need a JSON body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed download from Browserbase session storage. It also protects this process from pulling in unexpectedly large files.

**Data flow**: It receives a session id and a download guid, which is the name Browserbase should list for the stored file. It repeatedly asks Browserbase for the session’s downloads until it finds that file, checks the listed size, refuses oversized or unmeasured downloads, then fetches and returns the file bytes. If the file never appears, it raises an error.

**Call relations**: `BrowserbaseLease.fetch_download` delegates to this when UFO needs the actual downloaded file. Internally it uses `_json` to list downloads, `_size` to enforce the size check, `_field` to read the download id, `_send` to fetch the bytes, and a short sleep between retry attempts.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a local file’s bytes into the remote Browserbase session. This is needed because the remote Chrome cannot see file paths on UFO’s machine.

**Data flow**: It receives a session id, a remote file name, and raw bytes. It sends those bytes to Browserbase’s uploads endpoint as a file upload. It returns nothing, but the file becomes available inside the hosted session.

**Call relations**: `BrowserbaseLease.place_file` calls this after reading and naming the workspace file. It uses `_send` because the important result is that Browserbase accepts the upload.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase request and insists that the answer is a JSON object, meaning a dictionary-like response. This gives the rest of the file a safe shape to read from.

**Data flow**: It receives an HTTP method, a path, and request options. It asks `_send` to make the actual network request, parses the response as JSON, checks that it is an object, and returns that object. If the response is not the expected shape, it raises `BrowserbaseError`.

**Call relations**: The higher-level API methods use this whenever they expect structured Browserbase data, such as session ids, statuses, or download listings. It centralizes the repeated send-and-parse pattern.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the API key attached. This is the lowest-level network doorway in the file.

**Data flow**: It receives an HTTP method, API path, timeout, optional headers, and request data. It reads the Browserbase API key from the credential slot, adds it to the request headers, sends the request with `httpx`, and returns the response. If Browserbase reports an error status, it raises `BrowserbaseError` with the status and response text.

**Call relations**: All Browserbase network work flows through this function, either directly or through `_json`. Tests can inject a custom HTTP transport at this layer, while production uses a normal async HTTP client.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Reads a required string field from a Browserbase JSON response. It prevents later code from quietly continuing with a missing or empty id.

**Data flow**: It receives a response dictionary and the name of a field to read. It checks that the value exists, is a string, and is not empty. It returns the string, or raises `BrowserbaseError` if the field is not usable.

**Call relations**: The Browserbase API wrapper uses this after calls that must return ids or URLs, such as creating sessions, reading live sessions, creating Contexts, and fetching a download id.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the listed size of a Browserbase download. This is a safety check before loading a downloaded file into memory.

**Data flow**: It receives one download-listing entry. It checks that the entry has a numeric size and rejects missing, boolean, or non-number values. It returns the size as an integer.

**Call relations**: `BrowserbaseApi.download` calls this before fetching a file’s bytes. That way the download path can reject unsafe or unclear files before pulling them into the shared process.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the connection information UFO needs to talk to the hosted Chrome session. In plain terms, it hands over the remote browser’s address.

**Data flow**: It reads the lease’s saved connect URL. It wraps that URL in a `CdpEndpoint`, the project’s object for a Chrome DevTools Protocol endpoint, and returns it.

**Call relations**: The browser engine asks the lease for this endpoint when it is ready to connect to Chrome. This function does not contact Browserbase; it just packages the URL already received during session creation or reattach.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a compact recovery handle for this browser run. UFO can later use this token to reattach to the same Browserbase session and know which Context to clean up.

**Data flow**: It reads the conversation id, session id, and Context id stored in the lease. It joins them into one slash-separated string and returns that string.

**Call relations**: The reattach flow depends on this token. Later, `BrowserbaseCdpProvider.reattach` passes the token to `_parse_token` so it can reconstruct the same session details without searching shared state.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Makes a workspace file available to the remote Browserbase Chrome and returns the remote path Chrome should use. This is how file uploads in web pages work when the browser is not running locally.

**Data flow**: It receives a workspace path and a callback that reads the file bytes. It extracts the base file name, reads the bytes, rejects files over the upload limit, avoids name collisions by adding a short path digest when needed, uploads the bytes to Browserbase, records the staged name, and returns a `/tmp/.uploads/...` remote path.

**Call relations**: The browser engine calls this before setting a file input in the page. This function hands the actual upload to `BrowserbaseApi.upload`; after it returns, the engine can give the returned remote path to Chrome.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name Browserbase accepts for hosted Chrome downloads. It avoids using a local absolute path that the remote browser would reject.

**Data flow**: It takes no outside data. It returns the literal string `downloads`, which tells Browserbase to route downloaded files into the session’s own storage.

**Call relations**: The browser engine asks the lease where downloads should go. This answer fits Browserbase’s rules and pairs with `fetch_download`, which later retrieves files from that session storage.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Gets a completed download from Browserbase and returns its bytes to UFO. This bridges the gap between remote session storage and the local process.

**Data flow**: It receives the download guid assigned by Chrome. It passes the lease’s session id and that guid to the API layer, then returns the bytes that Browserbase sends back.

**Call relations**: The browser engine calls this after a download completes. The detailed polling, size checking, and byte fetching are delegated to `BrowserbaseApi.download`.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser run. It releases the hosted session, deletes its browser-state Context, and removes the saved Context pointer from the extension store.

**Data flow**: It reads the lease’s session id, Context id, conversation id, API object, and store. It first asks Browserbase to release the session. Whether or not that succeeds, it then tries to delete the Context. Whether or not that succeeds, it deletes the local store entry for this conversation’s Context.

**Call relations**: This runs when UFO is finished with the browser lease. Its ordering is important: the session is released before the Context is deleted, and the store row is removed even if remote cleanup has trouble, so future leases do not keep reusing a bad Context id.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Starts a new Browserbase-backed browser lease for the current browser turn. This is the main entry UFO uses when configured to use Browserbase as its Chrome provider.

**Data flow**: It receives the turn’s sandbox, which contains the conversation id. It creates a Browserbase API wrapper and an extension store, gets or creates the Context for this conversation, creates a Browserbase session, and returns a `BrowserbaseLease` containing everything needed to use and later clean up that session.

**Call relations**: The core CDP-provider seam calls this when a browser run begins. It calls `_context` to decide which Context to use, then hands session creation to `BrowserbaseApi.create_session` and packages the result as a lease.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context id for a conversation, or creates and stores one if none exists yet. This preserves browser state during recovery within the same run.

**Data flow**: It receives the API wrapper, scoped store, and conversation id. It builds a store key, checks for an existing Context id, and returns it if present. If not present, it asks Browserbase to create a Context, saves the new id in the store, and returns it.

**Call relations**: `BrowserbaseCdpProvider.lease` calls this before creating a session. It talks to the store through `get` and `put`, and calls `BrowserbaseApi.create_context` only when a new Context is needed.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reconnects UFO to an already-created Browserbase session when possible. This supports recovery after a replay or interruption.

**Data flow**: It receives a token string. It parses the token into conversation id, session id, and Context id, creates a Browserbase API wrapper, asks Browserbase whether the session is still live, and returns a new `BrowserbaseLease` pointing at that same session. If the token is bad or the session is gone, the flow raises `SessionGone` instead.

**Call relations**: The core browser recovery path calls this with the token previously produced by `BrowserbaseLease.token`. It uses `_parse_token` first, then delegates the live-session check to `BrowserbaseApi.live_session`.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a saved reattach token back into the three ids needed to resume and clean up a Browserbase run. It rejects malformed tokens as unusable sessions.

**Data flow**: It receives a slash-separated token. It splits out the conversation id, session id, and Context id, checks that the session and Context parts exist, converts the conversation id into a UUID, and returns all three values. If any part is missing or invalid, it raises `SessionGone`.

**Call relations**: `BrowserbaseCdpProvider.reattach` calls this before trying Browserbase. By converting bad tokens into `SessionGone`, it lets the recovery path treat malformed handles the same as expired sessions.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to UFO: its name, version, required credential, and the Browserbase CDP provider it offers. This is how the rest of the system discovers and configures the file’s functionality.

**Data flow**: It takes no input. It builds a manifest containing a credential slot for the Browserbase API key and a provider specification for the `browserbase` backend. It returns that manifest to the extension loader.

**Call relations**: The extension system calls this when loading available extensions. The manifest tells core that, when configuration asks for the Browserbase backend, it should build a `BrowserbaseCdpProvider` using the provided credentials.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandbox Chrome provider
Sandbox Chrome integration supplies or reuses a Chrome instance inside the conversation sandbox and exposes its DevTools access safely.

### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and sandbox browser access`

This provider solves a practical deployment problem: the browser should see the same files and environment as the sandboxed task, but the main UFO process still needs a way to control it. Think of the sandbox as a small rented workshop. Chrome runs inside that workshop, and this file gives UFO a reliable intercom to talk to it.

When a browser lease is requested, the provider requires the current sandbox. It runs a bring-up script inside that sandbox. The script creates browser folders, finds Chromium, starts it on Chrome DevTools Protocol port 9222 if it is not already answering, and starts a small local proxy on port 9223. CDP, or Chrome DevTools Protocol, is the control channel used to drive Chrome. The proxy is important because Chrome rejects some remote-looking Host headers; the proxy rewrites those headers to look local while still carrying WebSocket traffic through.

Readiness is judged by asking the browser endpoint for its version document, not by trusting that a process ID exists. If an old recorded process is stuck, it is killed and replaced. If startup fails, the provider tries to return useful browser or proxy logs instead of a vague timeout.

Chrome and the proxy live as long as the conversation sandbox, so closing a lease does not stop them. Downloads stay inside the sandbox too, and this file reads them out carefully with size limits.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 307–308)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already-prepared browser control endpoint for this lease. Other parts of UFO use this endpoint to connect to Chrome through the sandbox proxy.

**Data flow**: It reads the stored endpoint from the lease object and returns it unchanged. No network call happens here; the endpoint was already checked and built when the lease was created.

**Call relations**: After SandboxChromeCdpProvider.lease has started or found the sandbox browser and built a CdpEndpoint, this method is the simple handoff point for code that wants to drive Chrome.


##### `SandboxChromeCdpLease.token`  (lines 310–311)

```
async def token(self) -> str
```

**Purpose**: Returns a durable-looking token for the current browser connection, using the endpoint URL. In this provider, the token is mainly an identifier, not enough by itself to restore a browser later.

**Data flow**: It reads the endpoint stored on the lease, takes its URL, and returns that string. It does not contact the sandbox or verify that the browser is still alive.

**Call relations**: Callers can ask a lease for a token after receiving it, but SandboxChromeCdpProvider.reattach deliberately refuses to rebuild a lease from this token alone because a live sandbox is required.


##### `SandboxChromeCdpLease.place_file`  (lines 313–317)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells callers where a file should be for Chrome to open it. Because Chrome is already inside the sandbox, the existing sandbox path is enough and no file copy is needed.

**Data flow**: It receives a file path and a reader for file bytes. It ignores the reader because there is nothing to transfer, then returns the original path unchanged.

**Call relations**: Browser-driving code can call this when it wants Chrome to use a file. Unlike a local-browser provider, this lease does not need to shuttle the file elsewhere because Chrome and the workspace share the same sandbox filesystem.


##### `SandboxChromeCdpLease.download_dir`  (lines 319–322)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the directory inside the sandbox where Chrome downloads files. This gives the rest of the system a known place to look for downloaded content.

**Data flow**: It returns the fixed sandbox download directory path. It does not create the directory here; the browser bring-up script creates it earlier.

**Call relations**: After Chrome is leased, browser automation can use this path when configuring or finding downloads. The directory matches the one prepared by the sandbox bring-up command.


##### `SandboxChromeCdpLease.fetch_download`  (lines 324–352)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed download out of the sandbox and returns its bytes to the main UFO process. It protects the system by refusing downloads larger than the configured limit.

**Data flow**: It receives a download guid, treats that as the filename in the sandbox download directory, and quotes it safely for shell use. It first runs a sandbox command to count the file size. If the file is too large or unreadable, it raises an error. Otherwise it runs another sandbox command to base64-encode the file, then decodes that text back into bytes in a worker thread so the main event loop is not blocked.

**Call relations**: Browser automation calls this after Chrome has saved a download. It relies on the sandbox command runner to inspect and read the file, uses shlex.quote to avoid unsafe shell filenames, and uses asyncio.to_thread for the CPU work of base64 decoding.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 354–355)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease from the caller’s point of view without stopping Chrome. This is intentional because the browser and proxy are meant to persist across turns in the same conversation sandbox.

**Data flow**: It receives no extra information and returns without changing anything. The sandbox browser process, proxy process, profile, and downloads remain in place.

**Call relations**: Lease users can call this during cleanup. Unlike providers where closing a lease tears down a temporary browser, this one leaves the in-sandbox browser running for the next lease to reuse.


##### `SandboxChromeCdpProvider.lease`  (lines 367–384)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Starts or reuses Chrome inside the supplied sandbox and returns a lease containing a working browser control endpoint. This is the main entry point for using the sandbox Chrome provider.

**Data flow**: It takes an optional sandbox and rejects the call if none is supplied. It runs the bring-up shell command inside the sandbox with a timeout. If that command fails, it asks _bring_up_failure for a clearer error message. If it succeeds, it asks the sandbox how to dial the proxy port, chooses ws or wss depending on whether the carrier says the connection uses TLS, combines that public host with the WebSocket path from _ws_path, attaches any required headers, and returns a SandboxChromeCdpLease.

**Call relations**: The broader browser system calls this when it needs a CDP lease for a turn. This method coordinates the sandbox command runner, sandbox port dialing, endpoint construction, and lease creation; it delegates error explanation to _bring_up_failure and URL path validation to _ws_path.

*Call graph*: calls 4 internal fn (bash, dial, _bring_up_failure, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 386–387)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that an old token cannot be used to recreate a sandbox Chrome lease. A fresh lease must be made with the live sandbox instead.

**Data flow**: It receives a token string and immediately raises SessionGone with that token. It does not inspect the token or contact the sandbox.

**Call relations**: Recovery code may try to reattach using a saved token. This provider rejects that path because the usable endpoint depends on the current sandbox and its carrier-provided port address, which are only available during a new lease.

*Call graph*: 1 external calls (__init__).


##### `_bring_up_failure`  (lines 390–404)

```
async def _bring_up_failure(sandbox: Sandbox, result: ExecResult) -> str
```

**Purpose**: Builds a helpful error message when the browser bring-up command fails. It tries to surface Chrome or proxy logs so the caller sees why startup failed, not just that it timed out.

**Data flow**: It receives the sandbox and the command result. If the command ended normally with an error, it returns the command’s stderr or stdout. If the sandbox timeout killed the command, it runs a short tail command inside the sandbox to read the ends of the Chrome and proxy logs, then returns a message that includes the timeout and any log text found.

**Call relations**: SandboxChromeCdpProvider.lease calls this only on bring-up failure. It uses Sandbox.bash again on the failure path because the most useful explanation may be inside log files left in the sandbox.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 407–412)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part of Chrome’s WebSocket debugger URL and verifies that Chrome reported a local address. This prevents the provider from trusting or forwarding an unexpected remote URL.

**Data flow**: It receives the URL printed by the in-sandbox bring-up script, trims whitespace, and checks that it starts with the expected localhost Chrome DevTools address. If it does, it removes that local prefix and returns only the path. If not, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease uses this after the sandbox script proves the proxy can reach Chrome. The returned path is joined with the carrier-provided public host so callers connect through the sandbox’s exposed proxy rather than directly to Chrome’s private local address.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 415–424)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that this file provides a CDP backend named sandbox_chrome and explains how to build its provider object.

**Data flow**: It creates a CdpProviderSpec with the backend name and a small builder function that returns SandboxChromeCdpProvider. It wraps that spec in a Manifest with the extension name and version, then returns the manifest.

**Call relations**: The extension loader calls this when discovering available features. The manifest is what lets configuration such as cdp_provider = "sandbox_chrome" select this provider later.

*Call graph*: 2 external calls (__init__, __init__).


### Browser connection contract
The core browser abstraction defines the common connection contract used by local, sandboxed, hosted, and extension-provided Chrome providers.

### `core/src/ufo/browser.py`

`data_model` · `cross-cutting`

This file is a boundary line between the core system and whatever actually supplies Chrome. The core does not start Chrome itself and does not know whether Chrome is running inside a sandbox, on the user’s machine, or in a remote browser service. Instead, it uses a small shared agreement: a provider creates a lease, and the lease gives temporary access to a Chrome DevTools Protocol endpoint. Chrome DevTools Protocol, or CDP, is the control channel that automation tools use to drive Chrome.

The main idea is like borrowing a rental car for one trip. A CdpProvider is the rental desk. A CdpLease is the car key for this turn. While the turn is active, the lease tells the browser engine where to connect, how to reconnect if work resumes later, where files should be placed so Chrome can see them, where downloads should go, and how to retrieve downloaded bytes. At the end, the lease is closed so any temporary hosted session can be released.

The file also defines CdpEndpoint, a simple data object containing the connection URL and any headers needed to connect, such as an authorization token. If a saved browser session can no longer be found, SessionGone tells the caller not to resume the old page and to start fresh instead. The important design choice is separation: core defines the shape of the handshake, but extensions provide the actual browser behavior.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This method returns the browser connection details for the current lease. A browser engine uses it to learn which Chrome endpoint to connect to and which extra connection headers, if any, are required.

**Data flow**: The lease already knows where its Chrome session lives. When this method is called, it turns that stored knowledge into a CdpEndpoint containing a URL and headers. The result is passed back to the caller so it can open the CDP connection.

**Call relations**: This is part of the CdpLease contract. A concrete browser provider implements it, and the browser-driving extension calls it when it is ready to connect to Chrome for a turn.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: This method returns a durable text handle for the leased browser session. The system can save this handle so a later run can try to reconnect to the same browser session instead of starting from scratch.

**Data flow**: The lease contains or can derive some reattachment identifier, such as a hosted session ID or a stable endpoint URL. This method returns that identifier as a string. The caller can store it and later give it to CdpProvider.reattach.

**Call relations**: This method pairs with CdpProvider.reattach. The lease provides the token during an active turn, and the provider later uses that token to rebuild a lease if the old session is still alive.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This method makes a workspace file available to the leased Chrome browser and returns the path or location Chrome should use to open it. It hides the difference between a browser that shares the sandbox filesystem and a remote browser that needs the file uploaded.

**Data flow**: The caller gives a workspace path and a read function that can fetch the file bytes if needed. A sandbox-local provider may simply return the same path because Chrome can already see the file. A remote provider may call the read function, upload the bytes somewhere the remote Chrome can access, and return that remote location.

**Call relations**: The browser engine calls this when Chrome needs to open a file from the turn’s workspace. The concrete lease decides whether it can reuse the original path or must copy the file through provider-specific storage.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: This method tells the browser engine where Chrome should write downloaded files for this lease. The answer depends on whether Chrome is local to the sandbox or hosted somewhere else.

**Data flow**: The lease looks at where its Chrome instance stores files and returns a directory path or provider-specific download location. The browser engine then gives that location to Chrome before downloads begin.

**Call relations**: This method is used before or during browser setup for a turn. It works together with CdpLease.fetch_download: one method chooses where downloads land, and the other retrieves the finished file bytes afterward.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This method retrieves the bytes of a completed Chrome download. It uses Chrome’s download identifier, called a GUID, which is a unique name Chrome assigns to the downloaded file.

**Data flow**: The caller gives the download GUID. The lease uses whatever storage belongs to its Chrome session to find that completed download, reads its bytes, and returns them. For sandbox Chrome this may mean reading from the sandbox; for hosted Chrome it may mean calling the provider’s API.

**Call relations**: After Chrome finishes a download, the browser engine calls this method to bring the file back into the system. It complements CdpLease.download_dir, which told Chrome where to put the file in the first place.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: This method releases the browser lease when the turn is over. For a remote hosted browser, that may free a paid or limited session; for a fixed local endpoint, it may do nothing.

**Data flow**: The lease starts in an active borrowed state. When this method is awaited, the concrete provider performs any cleanup it needs, such as ending or releasing a hosted session. Afterward, the lease should no longer be treated as active.

**Call relations**: The turn-level orchestration calls this at the end of work, much like returning a borrowed key. Concrete lease implementations decide whether any real cleanup is required.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This method creates a fresh CdpLease for one turn of work. It is the normal way the system obtains access to Chrome when there is no existing session to resume.

**Data flow**: The caller may pass a Sandbox, which is an isolated workspace for the turn. The provider uses that sandbox if it needs to locate a Chrome running inside it, or ignores it if it supplies Chrome remotely or from a static endpoint. The result is a new lease that can provide connection details, file placement, download retrieval, and cleanup.

**Call relations**: A concrete provider implements this method and is selected at startup through configuration and extension registration. Turn orchestration calls it when beginning a new browser-backed turn or when reattachment is impossible.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This method tries to reconnect to a browser session named by a previously saved token. It lets recovered or resumed work continue with the same browser session when that session still exists.

**Data flow**: The caller supplies a token that came from CdpLease.token. The provider looks up or resolves the session named by that token. If it is still alive, the provider returns a new lease for it; if it is gone, it raises SessionGone so the caller knows to create a fresh lease instead.

**Call relations**: This method is the resume path that pairs with CdpLease.token. The wider system calls it when it has saved browser-session state, and falls back to CdpProvider.lease if the provider reports that the old session cannot be recovered.


### Delegated browser agents
Browser-specialized delegation covers both the child-agent profile and the hosted Browser Use tools for single or parallel browser jobs.

### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `subagent setup`

This file is a small setup file for a specialized helper agent that works inside a web browser. The main agent can delegate browser-heavy tasks to this child agent, such as opening pages, searching, filling forms, collecting information, or saving screenshots and notes for the parent to read later.

The file names this helper agent "browser", loads its detailed instructions from a nearby Markdown prompt file, and gives it access to browser tools plus a few basic workspace tools like reading and writing files. In everyday terms, it is like giving a field researcher both a web browser and a notebook, then telling them where to write down what they found.

It also defines the expected task and result formats using Pydantic models. Pydantic is a library that checks that data has the expected shape. A browser task includes the freeform task text, an optional starting URL, an optional task name, and an `extended_context` flag that defaults to true. That default matters because browser work often takes many steps, and cutting it short can leave the parent agent with only a partial result that is hard to continue.

Finally, the file creates `BROWSER_PROFILE`, the object the rest of the system uses when it wants to spawn this browser subagent. The profile connects the name, prompt, allowed tools, input and output models, and chosen AI model into one reusable package.


### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `tool request handling`

This extension is a swap-in replacement for UFO’s local browser automation tools. Instead of opening and controlling a browser itself, it sends a task to Browser Use’s cloud service, waits for that hosted agent to finish, then brings back the text result and, when allowed, any output files. In everyday terms, UFO writes instructions on a work order, sends them to an outside web assistant, checks back until the assistant is done, and files the returned report in the workspace.

The file is careful about boundaries. The Browser Use API key is read from UFO’s credential system on the host side, so it does not enter the sandbox where untrusted work happens. Downloaded files are fetched through links that do not carry that key. Before writing any returned file, the code checks that its path stays inside the workspace, so a bad or surprising vendor response cannot write somewhere unsafe.

There are two user-facing tools. `browser_task` runs one full browser session, can save output files, and has a larger cost budget. `wide_browse` reads a list of sites or names from a workspace file, starts several cheaper hosted runs at once, and writes a combined JSON result file. The file also supports retry safety through stored run IDs, so repeated calls can reconnect to a run that was already started instead of paying for a duplicate.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one hosted Browser Use job from beginning to end. It starts or reconnects to a run, waits until it finishes or times out, collects the result, and optionally downloads output files into the workspace.

**Data flow**: It receives the tool context, the task text, a timeout, and an optional deduplication key. It reads the Browser Use API key from credentials, opens an HTTP client, starts or resumes the remote run, watches its status, cancels it if UFO’s timeout expires, then reads the final summary and output files. It returns a `RunOutcome` containing the final status, result or error text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the main worker used by both `_browser_task` and `_wide_browse.visit`. Inside the run, it delegates creation to `HostedRun._start`, repeated status checks to `HostedRun._watch` and `HostedRun._status`, response parsing to `HostedRun._json`, and file retrieval to `HostedRun._collect`.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Creates a new Browser Use run, unless a matching stored run already exists. This prevents duplicate paid runs when a tool call is retried with the same idempotency key.

**Data flow**: It receives an HTTP client, extension store, task text, and optional deduplication key. If the key points to a saved run, it validates and returns that saved run handle. Otherwise it sends the task, model, cost limit, and proxy country to Browser Use, reads the new run ID and workspace ID, stores them if requested, and returns a `StartedRun`.

**Call relations**: It is called by `HostedRun.execute` before any waiting begins. It uses `HostedRun._json` to make sure the API response is usable and `HostedRun._text` to pull required string fields from that response.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Keeps checking a Browser Use run until it reaches a final state. It is the polling loop, like periodically refreshing a delivery-tracking page until the package is delivered or cancelled.

**Data flow**: It receives an HTTP client and run ID. It asks `_status` for the current state, returns immediately if that state is final, or sleeps briefly before checking again. Its output is the terminal status string.

**Call relations**: It is called by `HostedRun.execute` after a run has started. It repeatedly hands off each one-time status read to `HostedRun._status` and uses `asyncio.sleep` to avoid hammering the API.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is used both during normal polling and just before cancellation, so a run that finished at the last moment is not cancelled unnecessarily.

**Data flow**: It receives an HTTP client and run ID. It sends a GET request to the run’s status endpoint, checks that the response is valid JSON, extracts the `status` text, and returns that status string.

**Call relations**: It is called by `HostedRun._watch` during the polling loop and directly by `HostedRun.execute` when a timeout has just occurred. It relies on `HostedRun._json` and `HostedRun._text` for safe response reading.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies a completed run’s output files into UFO’s workspace when file saving is enabled. It also reports files it chose not to fetch, such as files that are too large.

**Data flow**: It receives an HTTP client, tool context, and Browser Use workspace ID. If output saving is disabled, it returns empty results. Otherwise it asks Browser Use for a limited file list, checks each item has a path and size, verifies the path cannot escape the workspace, downloads acceptable files, writes them through the sandbox, and returns saved files, skipped files, and a flag saying whether more files exist.

**Call relations**: It is called by `HostedRun.execute` after the run reaches a terminal status. For each downloadable file, it calls `HostedRun._download`; for API response checking it calls `HostedRun._json`; for path safety it uses the shared sandbox containment check.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a Browser Use-provided link without sending UFO’s API key to that storage host. It refuses non-HTTPS links to avoid unsafe plain-text or unexpected internal downloads.

**Data flow**: It receives a URL. It checks that the URL starts with `https://`, opens a separate HTTP client with no Browser Use API header, fetches the bytes, raises a `BrowserUseError` for failed downloads, and returns the file contents as bytes.

**Call relations**: It is called only by `HostedRun._collect` while saving run output files. Its job is deliberately narrow: fetch one approved file body so `_collect` can write it into the workspace.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a safe Python dictionary. It gives clear extension-specific errors instead of letting unclear JSON or key errors leak out.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises an error with the API path, status, and body. Otherwise it parses JSON, checks that the top-level value is an object, converts keys to strings, and returns the dictionary.

**Call relations**: It is used throughout the hosted-run flow by `HostedRun.execute`, `HostedRun._start`, `HostedRun._status`, and `HostedRun._collect`. It is the shared gatekeeper that makes API responses predictable before other code reads fields from them.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Reads one required text field from an already-parsed Browser Use response. It turns a missing or wrongly typed field into a clear `BrowserUseError`.

**Data flow**: It receives a response dictionary and the field name to read. It looks up that field, checks that the value is a string, returns the string if valid, or raises an error describing the bad response.

**Call relations**: It is called by `HostedRun._start` to read the new run ID and workspace ID, and by `HostedRun._status` to read the current status. It usually follows `HostedRun._json`, which has already checked that the response is a dictionary.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 344–378)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the user-facing `browser_task` tool. It sends one self-contained browser instruction to Browser Use and returns a JSON summary of the result and any saved files.

**Data flow**: It receives a tool context and validated input containing a starting URL, task instructions, task name, and timeout. It builds one task prompt, creates a `HostedRun` with the higher-quality model and larger cost budget, waits for the hosted run, then returns a `ToolResult`. If the run timed out, the result is marked as an error; otherwise it includes the run output, saved file paths, skipped file details, and whether more files existed.

**Call relations**: This function is registered as the handler for the `browser_task` tool in `BROWSER_USE_TOOLS`. When the tool is called, it delegates the actual Browser Use API lifecycle to `HostedRun.execute` and wraps the outcome in `TextContent` and `ToolResult` for UFO.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 381–388)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a text file from the sandbox workspace using a shell command, while safely quoting the file path. It exists because user- or model-provided paths must not be allowed to run extra shell commands.

**Data flow**: It receives a tool context and file path. It shell-quotes the path, runs `cat` inside the sandbox, checks the command’s exit code, raises a readable error if the file cannot be read, and returns the file contents as text.

**Call relations**: It is called by `_read_lines` to read the entity list and by `_wide_browse` to read the JSON schema file. The path quoting step is important because these callers pass in paths supplied through tool input.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 391–399)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace file as a clean list of unique, non-empty lines. `wide_browse` uses this to turn a file of URLs or site names into the list of browser jobs to run.

**Data flow**: It receives a tool context and path. It calls `_read_file`, splits the returned text into lines, trims whitespace, ignores blank lines, removes duplicates while keeping the first-seen order, and returns the resulting list.

**Call relations**: It is called by `_wide_browse` at the start of the batch flow. It builds on `_read_file` so the low-level file-reading and shell-safety details stay in one place.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 402–448)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the user-facing `wide_browse` tool. It runs the same kind of browser extraction task across many URLs or site names, several at a time, then writes all results into one JSON file.

**Data flow**: It receives a tool context and validated input pointing to an entities file, a prompt template, and an output schema file. It reads and de-duplicates the entities, checks the batch size limit, reads the schema text, creates a shared hosted-runner with a cheaper model and no output-file saving, runs visits in parallel with a concurrency limit, turns per-entity failures into error rows, writes `wide_browse.json` in the workspace, and returns the rows plus the output filename.

**Call relations**: This function is registered as the handler for the `wide_browse` tool. It calls `_read_lines` and `_read_file` for inputs, defines `_wide_browse.visit` for one entity’s remote run, launches those visits with `asyncio.gather`, and packages the final result as a `ToolResult`.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 418–433)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser task for one entity inside a `wide_browse` batch. It keeps each entity’s work isolated so one failed site does not automatically discard the rest of the batch.

**Data flow**: It receives one entity string from the surrounding `_wide_browse` function. It waits for a semaphore slot so only a limited number of runs happen at once, fills `{entity}` into the prompt template, appends the requested output schema if present, executes a hosted run with a per-entity deduplication key, and returns a row containing the entity, final status, and result text.

**Call relations**: It is created and used inside `_wide_browse`. `_wide_browse` starts one `visit` task per entity with `asyncio.gather`; after all visits finish, the outer function converts exceptions into error rows and writes the combined output file.


##### `manifest`  (lines 471–486)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to UFO so it can be loaded. It names the extension, lists the tools it provides, adds prompt text, and declares the Browser Use API key it needs.

**Data flow**: It takes no input. It builds a `Manifest` containing the extension name and version, the two tool definitions, the browser prompt section read from the companion markdown file, and one credential slot for the Browser Use API key. The returned manifest is what the host uses during extension setup.

**Call relations**: This is called by UFO’s extension-loading machinery, not by the tool handlers themselves. It hands off `BROWSER_USE_TOOLS`, a `PromptSection`, and a `CredentialSlot` so the platform knows what this file contributes before any browser task is run.

*Call graph*: 3 external calls (__init__, __init__, __init__).

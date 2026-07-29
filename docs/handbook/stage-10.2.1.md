# Browser providers and sandbox-hosted website automation adapters  `stage-10.2.1`

This stage is the system’s doorway to web browsers and website previews. It is shared behind-the-scenes support used when the system needs to open a page, test a site, upload or download files, or ask a hosted browsing service to do web work.

The core browser contract defines a simple promise: “give me a Chrome debugging connection for this turn.” A debugging connection is the control address that lets software drive Chrome, like a remote control. Sandbox Chrome fulfills that promise by starting or reusing Chrome inside the conversation’s sandbox and safely exposing its control socket. Browserbase fulfills the same promise with a Chrome session hosted in the cloud, including session setup, file transfer, reconnection, and cleanup.

The browser backend sits between tools and the actual browser. It opens the connection only when needed, reuses it during the turn, moves files safely, and closes things afterward. Browser Use adds higher-level cloud browsing tools for delegated tasks. The sites tools build, preview, and publish sandbox web projects, waiting until servers are reachable before browser checks begin.

## Files in this stage

### Browser capacity providers
Adapters for obtaining Chrome debugging capacity from hosted Browserbase sessions or sandbox-local Chrome, tied together by the generic browser connection contract.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `active during each Browserbase-backed browser subagent turn`

A browser run in this system may happen inside a remote Chrome session hosted by Browserbase. This file is the bridge to that service. Without it, choosing the Browserbase CDP provider would not work: the system would have no way to create the remote browser, get its connection address, move files in or out, or stop paying for the session when the run ends.

The main idea is a short-lived lease, like borrowing a rental car for one trip. `BrowserbaseCdpProvider` starts the trip. It finds the current conversation, gets or creates a Browserbase Context for it, and asks Browserbase for a session. A Context is Browserbase’s saved browser state, such as cookies and local storage, so a restarted session in the same run can keep its logins. `BrowserbaseLease` represents the active rented browser. It gives the engine the Chrome connection URL, uploads local files to Browserbase before a web page uses them, fetches completed downloads back as bytes, and releases the session at the end.

`BrowserbaseApi` is the low-level web client. It sends authenticated HTTP requests to Browserbase, reads the API key from the host credential store each time, and checks that responses contain the fields the rest of the code needs. The file is careful about safety: uploads and downloads have size limits, downloads are polled because Browserbase may store them slightly after Chrome finishes, and cleanup removes both the session and the saved Context so browser state does not outlive the run.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new Browserbase browser session tied to an existing Browserbase Context. The Context keeps browser state for this run, while the new session gives the system a fresh remote Chrome to connect to.

**Data flow**: It receives a Context ID. It sends Browserbase a request saying to use that Context, persist its state, and keep the session alive long enough for the run. It reads the returned session ID and Chrome connection URL, then returns both.

**Call relations**: This is used after the provider has found or created a Context for the browser run. It relies on `_json` to talk to Browserbase and `_field` to make sure the reply includes the needed ID and connection URL.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether a named Browserbase session is still usable and, if it is, returns its Chrome connection URL. This is what makes recovery possible after the system already has a saved session token.

**Data flow**: It receives a session ID, asks Browserbase for that session, checks the session status, and returns the connection URL if the session is still running or pending. If Browserbase says the session is no longer live, it raises `SessionGone`, which tells the caller to stop trying to reuse it.

**Call relations**: The reattach path uses this through `BrowserbaseCdpProvider.reattach`. It calls `_json` for the service response, `_field` to read trusted string fields, and signals a dead session with `SessionGone`.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to release a session when the browser run is finished. This prevents the remote browser from continuing to run, consume resources, or bill after the turn ends.

**Data flow**: It receives a session ID and sends Browserbase a status update requesting release. It does not return data; its effect is on the remote Browserbase session.

**Call relations**: The lease cleanup flow calls this before deleting the Context. It uses `_json` so a failed Browserbase response becomes a clear transport error instead of being ignored.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a new Browserbase Context, which is the saved browser state container for a run. This lets a session recreated during the same run keep things like cookies and local storage.

**Data flow**: It sends an empty create request to Browserbase’s Context API. It reads the returned Context ID and gives that string back to the caller.

**Call relations**: `BrowserbaseCdpProvider._context` calls this when there is no saved Context ID for the current conversation. It uses `_json` to make the request and `_field` to require a valid ID in the reply.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase Context after the browser run is over. This is important because the Context may contain authenticated browser state, and that state should not survive beyond the subagent run.

**Data flow**: It receives a Context ID and sends a delete request to Browserbase. It returns nothing, but it attempts to remove the remote saved browser state.

**Call relations**: `BrowserbaseLease.aclose` uses this during cleanup after trying to release the session. It calls `_send` directly because it only needs to know whether the request succeeded, not parse a JSON body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed browser download from Browserbase session storage. It waits briefly for Browserbase’s downloads list to catch up, and it refuses files that are too large or have no reported size.

**Data flow**: It receives a session ID and a download GUID, which is the name Chrome used for the stored download. It repeatedly asks Browserbase for the session’s download list, looks for an entry with that filename, checks its size, then downloads and returns the file bytes. If the file never appears, is too large, or lacks a size, it raises an error.

**Call relations**: `BrowserbaseLease.fetch_download` hands download requests to this method. Inside, it uses `_json` to list downloads, `_size` and `_field` to validate the listing, `_send` to fetch the actual bytes, and `asyncio.sleep` to wait between listing attempts.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads file bytes into a Browserbase session so the remote Chrome can use them. This is needed because the browser is not on the same machine as the workspace, so a local file path alone would mean nothing to it.

**Data flow**: It receives a session ID, a remote filename, and the file bytes. It posts those bytes to Browserbase’s session upload endpoint. It returns nothing; the useful result is that Browserbase stores the file for Chrome to access.

**Call relations**: `BrowserbaseLease.place_file` prepares the name and bytes, then calls this method. This method uses `_send` for the actual HTTP upload, with a longer timeout than ordinary API calls.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase request and requires the response body to be a JSON object, meaning a dictionary-like structure. It centralizes the common pattern used by Browserbase API calls that expect structured data back.

**Data flow**: It receives an HTTP method, a path, and optional request details. It sends the request through `_send`, parses the response as JSON, verifies that the result is an object, and returns it. If Browserbase returns some other shape, it raises a `BrowserbaseError`.

**Call relations**: Higher-level API methods such as creating sessions, checking live sessions, creating Contexts, releasing sessions, and listing downloads use this helper so they do not each repeat the same request-and-validate logic.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual authenticated HTTP request to Browserbase. It is the one place that adds the Browserbase API key and turns failed HTTP responses into clear errors.

**Data flow**: It receives an HTTP method, API path, timeout, and request options. It reads the API key from the credential store, adds it as a request header, sends the request with `httpx`, and returns the response if Browserbase accepted it. If Browserbase returns an error status, it raises `BrowserbaseError` with the status and response text.

**Call relations**: All network-facing methods flow through this function, either directly or through `_json`. Tests can provide a custom transport here, while production uses a normal asynchronous HTTP client.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Pulls a required string field out of a Browserbase JSON response. It protects the rest of the code from silently continuing when Browserbase omitted an ID, URL, or other needed value.

**Data flow**: It receives a response object and a field name. It looks up that field, checks that it is a non-empty string, and returns it. If the field is missing, empty, or not a string, it raises `BrowserbaseError`.

**Call relations**: Browserbase API methods use this whenever they depend on a specific response field, such as session IDs, Context IDs, connection URLs, and download entry IDs.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the reported size of a Browserbase download entry. This matters because the process is about to pull the whole file into memory, so it must know the size first.

**Data flow**: It receives one download-list entry from Browserbase. It checks that the entry has a numeric size and returns it as an integer. If the size is absent or not a number, it raises `BrowserbaseError` instead of allowing an unmeasured download.

**Call relations**: `BrowserbaseApi.download` calls this before fetching download bytes. It is part of the safety gate that enforces the maximum download size.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Gives the browser engine the Chrome connection endpoint for this leased Browserbase session. The endpoint is the remote address the engine uses to control Chrome.

**Data flow**: It reads the lease’s stored connection URL and wraps it in a `CdpEndpoint` object. It returns that object without changing any remote state.

**Call relations**: The browser engine asks the lease for this when it is ready to connect to Chrome. This method creates the small endpoint object from the URL that `BrowserbaseApi.create_session` or `live_session` supplied.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a recovery token that identifies the conversation, Browserbase session, and Browserbase Context for this run. The token lets a later recovery attempt reconnect to the same remote browser if it still exists.

**Data flow**: It reads the lease’s conversation ID, session ID, and Context ID. It formats them into one slash-separated string and returns that string.

**Call relations**: The reattach flow later gives this token to `BrowserbaseCdpProvider.reattach`. Because the token carries all three IDs, reattach does not have to guess which run or Context it belongs to.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Copies a workspace file into the remote Browserbase session and returns the path that remote Chrome should use. This solves the basic remote-browser problem that a local file path is not visible inside Browserbase.

**Data flow**: It receives the original workspace path and a `read` function that returns the file bytes. It takes the base filename, reads the bytes, checks the upload size limit, chooses a safe remote name, uploads the bytes, records which path claimed that name, and returns a Browserbase upload path under `/tmp/.uploads`. If two different workspace paths have the same filename, later ones get a short path-based digest prefix to avoid overwriting the first.

**Call relations**: The browser engine calls this before telling Chrome to set a file input. This method prepares the name and bytes locally, then hands the actual upload to `BrowserbaseApi.upload`.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name Browserbase-hosted Chrome accepts. It deliberately returns `downloads` rather than a normal absolute filesystem path.

**Data flow**: It takes no extra input. It returns the literal string Browserbase expects for routing downloads into session storage.

**Call relations**: The browser engine asks the lease where downloads should go. This method gives the Browserbase-specific answer so Chrome’s download behavior works in the hosted environment.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Fetches a completed download from Browserbase for this lease’s session. It gives the caller the downloaded file as bytes.

**Data flow**: It receives a download GUID. It combines that with the lease’s session ID and asks `BrowserbaseApi.download` to wait for and retrieve the stored file. It returns the bytes from Browserbase.

**Call relations**: The browser engine calls this after a download finishes. This method is the lease-level wrapper, while `BrowserbaseApi.download` does the listing, polling, size checking, and byte transfer.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser run. It releases the Browserbase session, deletes the Browserbase Context, and removes the saved Context ID from the extension store.

**Data flow**: It reads the lease’s session ID, Context ID, conversation ID, API client, and store. It first tries to release the session, then tries to delete the Context, and finally deletes the stored Context row even if the Context delete failed. It returns nothing; its effects are cleanup in Browserbase and local extension storage.

**Call relations**: The browser run calls this when the lease is no longer needed. The order matters: release the active browser first, then remove its saved state, then remove the local pointer so later leases do not reuse a broken or deleted Context.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts a new Browserbase-backed browser lease for the current browser run. It is the main entry used when the system needs a fresh remote Chrome session.

**Data flow**: It receives the current sandbox session and reads its conversation ID. It creates a Browserbase API client and extension-scoped store, gets or creates a Context for that conversation, creates a Browserbase session using that Context, and returns a `BrowserbaseLease` containing all the IDs and connection URL. If no sandbox is provided, it raises an error because it cannot name the run.

**Call relations**: This is the provider’s normal start path. It calls `_context` to find the saved browser state container, then uses `BrowserbaseApi.create_session` through that flow and packages the result as a lease.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase Context ID for a conversation, or creates and stores one if none exists yet. This is how one browser run can recreate a session without losing browser state.

**Data flow**: It receives an API client, a scoped store, and a conversation ID. It builds a store key, checks whether a non-empty Context ID is already saved, and returns it if so. Otherwise it creates a new Context through Browserbase, stores the new ID under that key, and returns it.

**Call relations**: `BrowserbaseCdpProvider.lease` calls this before creating a session. It uses the store as the local memory of which Context belongs to the current conversation, and `BrowserbaseApi.create_context` when a new remote Context is needed.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reconnects to an existing Browserbase session using a token from an earlier lease. This supports recovery when the system still has a live remote browser and should not start over.

**Data flow**: It receives a token string, parses it into conversation ID, session ID, and Context ID, creates a Browserbase API client, asks Browserbase whether the session is still live, and returns a `BrowserbaseLease` for that same session. If the token is invalid or the session is gone, the flow raises `SessionGone`.

**Call relations**: This is the provider’s recovery path. It uses `_parse_token` to understand the token, `BrowserbaseApi.live_session` to confirm the remote session can still be used, and then constructs a lease around the existing session.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Turns a lease recovery token back into its three parts: conversation ID, session ID, and Context ID. It rejects malformed tokens by treating the session as gone.

**Data flow**: It receives a slash-separated token string. It splits out the conversation, session, and Context pieces, converts the conversation piece into a UUID, and returns all three values. If any required part is missing or the UUID is invalid, it raises `SessionGone`.

**Call relations**: `BrowserbaseCdpProvider.reattach` calls this at the start of recovery. By raising `SessionGone` for bad tokens, it lets the higher-level recovery logic fall back instead of trying to reconnect with unsafe or incomplete information.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It tells the system the extension name and version, what credential it needs, and that it provides the `browserbase` CDP backend.

**Data flow**: It takes no input. It builds and returns a `Manifest` containing a credential slot for the Browserbase API key and a CDP provider specification that constructs `BrowserbaseCdpProvider` when selected.

**Call relations**: The extension loading system calls this to discover what the file offers. The returned provider spec is what connects a configuration choice like `cdp_provider = "browserbase"` to the provider implementation in this file.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and download retrieval`

This file is the bridge between the browser-driving part of UFO and a Chrome process that lives inside a sandbox. A sandbox is an isolated workspace for one conversation, like a small private computer. The hard part is that Chrome’s DevTools protocol, the control channel used to automate Chrome, expects local-looking connections. But the main server must reach Chrome from outside the sandbox.

To solve that, the file builds two small scripts as text and runs them inside the sandbox. One script starts Chrome on port 9222 if it is not already answering. The other starts a small TCP proxy on port 9223. The proxy rewrites the HTTP Host header so Chrome believes the request is coming to localhost, then passes WebSocket traffic through unchanged. This is important because the WebSocket is the live control pipe used by the browser automation engine.

The provider waits for real readiness: it checks whether the DevTools version endpoint answers, not merely whether a process exists. If an old browser or proxy was recorded but is not serving, it is killed and replaced. Chrome and the proxy stay alive across turns in the same conversation, so closing a lease does not stop them. The file also knows how to read downloaded files back out of the sandbox, with a size limit to avoid pulling huge untrusted files into the server process.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 295–296)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already-prepared browser control endpoint. Other parts of the system use this endpoint to connect to Chrome through the sandbox proxy.

**Data flow**: It reads the stored endpoint object from the lease and returns it unchanged. Nothing is created, copied, or closed.

**Call relations**: After SandboxChromeCdpProvider.lease has started or found Chrome and built the endpoint, the browser automation layer asks this lease for the endpoint when it is ready to connect.


##### `SandboxChromeCdpLease.token`  (lines 298–299)

```
async def token(self) -> str
```

**Purpose**: Returns a simple string that identifies this browser connection: the endpoint URL. This is the durable-looking handle the lease exposes, even though this provider does not actually reattach from it later.

**Data flow**: It reads the URL from the stored endpoint and returns that URL as text. It does not check whether the browser is still alive.

**Call relations**: The surrounding CDP lease interface expects a token method. This method supplies the URL from the endpoint created by SandboxChromeCdpProvider.lease.


##### `SandboxChromeCdpLease.place_file`  (lines 301–305)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells callers that a file is already where Chrome can see it. Because Chrome runs inside the same sandbox as the workspace, there is no need to upload or copy the file for the browser.

**Data flow**: It receives a sandbox path and a file-reading callback. It ignores the callback because no file transfer is needed, and returns the same path it was given.

**Call relations**: Code that prepares files for browser use can call this through the lease interface. For this provider, the story is simple: the browser is already inside the same filesystem, so the function hands the original path straight back.


##### `SandboxChromeCdpLease.download_dir`  (lines 307–310)

```
async def download_dir(self) -> str
```

**Purpose**: Reports where Chrome writes downloaded files inside the sandbox. Callers use this path when configuring or finding browser downloads.

**Data flow**: It returns the fixed sandbox download directory path. It does not inspect the sandbox or create the directory here; the bring-up command prepares it earlier.

**Call relations**: The browser automation layer asks the lease for a download directory. This method returns the directory that the Chrome startup script configured when SandboxChromeCdpProvider.lease brought Chrome up.


##### `SandboxChromeCdpLease.fetch_download`  (lines 312–336)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed downloaded file out of the sandbox and returns its bytes to the server. It protects the server by refusing downloads larger than the configured maximum.

**Data flow**: It receives a download identifier, safely quotes it for a shell command, and builds the expected file path in Chrome’s download directory. It asks the sandbox to count the file’s bytes first. If the file cannot be read, it raises an error. If it is too large, it raises an error. Otherwise it asks the sandbox to base64-encode the file, brings that text back, decodes it into bytes in a worker thread, and returns those bytes.

**Call relations**: After Chrome has downloaded something inside the sandbox, higher-level browser code can call this lease method to retrieve it. The method uses shlex.quote to keep the download name safe inside a shell command, uses the sandbox command runner to read the file, and uses asyncio.to_thread so the CPU work of base64 decoding does not block the main async event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 338–339)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease without shutting down Chrome. This is deliberate because Chrome and its proxy are meant to persist for the whole conversation sandbox and be reused on later turns.

**Data flow**: It receives no extra input and returns nothing. It does not stop processes, delete files, or change the endpoint.

**Call relations**: The general lease interface expects an async close step at the end of use. Here that step is a no-op because SandboxChromeCdpProvider.lease may reuse the same in-sandbox Chrome on the next turn.


##### `SandboxChromeCdpProvider.lease`  (lines 351–364)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Starts or reuses the sandbox’s Chrome browser and returns a lease containing a usable remote DevTools WebSocket endpoint. This is the main entry point for getting browser automation connected to a sandbox Chrome.

**Data flow**: It receives the current SandboxSession. If none is provided, it raises an error because this provider cannot work without a sandbox. It runs the embedded bring-up command in the sandbox, which starts Chrome and the proxy if needed and prints Chrome’s local WebSocket debugger URL. If bring-up fails, it raises an error with the command output. Then it asks the sandbox for the public host for the proxy port, adds the sandbox traffic token as a connection header when present, converts Chrome’s local WebSocket path into a remote WebSocket URL, creates a CdpEndpoint, wraps it in SandboxChromeCdpLease, and returns that lease.

**Call relations**: The core browser system calls this when it needs a CDP lease for a turn. Inside the flow, it calls SandboxSession.bash to perform browser bring-up, SandboxSession.host to learn how to reach the proxy from outside, _ws_path to extract the safe local WebSocket path, _remote_ws_url to build the externally reachable WebSocket address, then hands the resulting endpoint to SandboxChromeCdpLease.

*Call graph*: calls 4 internal fn (bash, host, _remote_ws_url, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 366–367)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Refuses to recreate a lease from an old token alone. This provider needs the live sandbox session to discover and validate the current browser endpoint.

**Data flow**: It receives a token string and immediately raises SessionGone using that token. It does not attempt any network connection or sandbox lookup.

**Call relations**: The broader CDP provider interface includes reattach support, but this provider’s recovery path is to call lease again with a live sandbox. By raising SessionGone, it tells callers that the old token is not enough and a fresh lease is required.

*Call graph*: 1 external calls (__init__).


##### `_ws_path`  (lines 370–375)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part from Chrome’s local WebSocket debugger URL and checks that the URL really points to localhost. This prevents the provider from accidentally forwarding an unexpected remote browser address.

**Data flow**: It receives a WebSocket URL string printed by the sandbox bring-up command. It trims whitespace, accepts only URLs starting with the expected local Chrome address, removes that local prefix, and returns the remaining path. If the URL is not local, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease calls this after the sandbox reports Chrome’s debugger URL. The returned path is then passed to _remote_ws_url so the same browser session can be reached through the sandbox’s public proxy host.

*Call graph*: called by 1 (lease).


##### `_remote_ws_url`  (lines 378–384)

```
def _remote_ws_url(host: str, path: str) -> str
```

**Purpose**: Builds the final WebSocket URL that outside code can use to reach the in-sandbox Chrome proxy. It also chooses the secure WebSocket form when the public host uses HTTPS.

**Data flow**: It receives a public host and a WebSocket path. It first normalizes the host into a full HTTP or HTTPS URL using _remote_url, removes any trailing slash, and then converts https to wss or http to ws. It appends the WebSocket path and returns the completed browser-control URL. If the host cannot be understood as HTTP or HTTPS, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease uses this after it has the sandbox’s public proxy host and the local WebSocket path from _ws_path. This helper calls _remote_url to handle hosts that may or may not already include a scheme.

*Call graph*: calls 1 internal fn (_remote_url); called by 1 (lease).


##### `_remote_url`  (lines 387–391)

```
def _remote_url(host: str) -> str
```

**Purpose**: Turns a host name into a complete web URL with a scheme such as http or https. This lets later code treat all hosts consistently.

**Data flow**: It receives a host string. If the host already begins with http:// or https://, it returns it unchanged. If it looks like localhost, it adds http://. Otherwise it assumes a public remote host and adds https://.

**Call relations**: _remote_ws_url calls this while building the browser WebSocket endpoint. It keeps the scheme-choice rules in one small place so the URL-building step can stay simple.

*Call graph*: called by 1 (_remote_ws_url).


##### `manifest`  (lines 394–403)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that this file provides a CDP provider named sandbox_chrome and tells the system how to build it.

**Data flow**: It creates a Manifest object containing the extension name, version, and one CdpProviderSpec. That provider spec maps the sandbox_chrome backend name to a small builder that returns a SandboxChromeCdpProvider.

**Call relations**: The extension-loading system calls manifest when discovering available browser providers. The returned Manifest and CdpProviderSpec let the rest of the application select this provider when configuration asks for the sandbox_chrome CDP backend.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/browser.py`

`io_transport` · `per-turn browser setup, reconnect, file transfer, download retrieval, and turn teardown`

This file is a boundary line between the core system and whatever browser provider is plugged in. The core needs a Chrome DevTools Protocol connection, often shortened to CDP, which is the control channel used to drive Chrome. But the core should not care where that Chrome lives. It might be inside the task sandbox, or it might be a remote browser session from a service.

The main idea is a lease, like borrowing a key for one job. A CdpProvider can create a CdpLease for a turn. The lease gives the browser engine a connection endpoint, a reusable token for reconnecting later, and a way to release the browser when the turn ends.

The file also defines how files and downloads cross the boundary. If Chrome lives in the same sandbox as the workspace, opening a file can be as simple as returning the same path. If Chrome is remote, the provider may need to upload the file first and return the remote path. Downloads work the same way in reverse: the provider says where Chrome should save them and how to fetch the bytes back.

There is almost no implementation here. Instead, it is a shared promise that browser-provider extensions must keep, so the rest of the system can use browsers consistently.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the Chrome debugging endpoint that the browser-driving code should connect to. The endpoint includes the URL and any needed connection headers, such as authentication or sandbox routing information.

**Data flow**: The lease already represents a particular browser session. When this method is called, it looks up or prepares the connection details for that session and returns a CdpEndpoint containing the URL and headers. If the lease cannot provide a usable endpoint, the concrete provider is expected to fail rather than return a bad connection.

**Call relations**: A browser engine calls this after a provider has created or reattached a lease. The method hands the engine the address it needs to connect to Chrome, while hiding whether that address points into a sandbox or to a remote hosted browser.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: This returns a stable text token that can be saved and used later to reconnect to the same browser session if it is still alive. For a remote browser this might be a hosted session ID; for a static endpoint it may simply identify that endpoint.

**Data flow**: The lease starts with knowledge of the current browser session. This method turns that knowledge into a serializable string, meaning something safe to store and pass around. The output is the token; it does not itself reconnect, but it gives CdpProvider.reattach something to work with later.

**Call relations**: After a lease is created, the browser extension or turn state can ask for this token and persist it. On recovery or resume, that saved token is given back to CdpProvider.reattach so the system can try to continue with the same browser instead of starting from scratch.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This tells the system where the leased Chrome can open a workspace file. It also gives remote providers a chance to copy the file bytes somewhere the browser can reach.

**Data flow**: The input is a workspace path and a read function that can produce the file's bytes. A sandbox-local provider may simply return the same path because Chrome can already see the file. A remote provider may call the read function, upload the bytes, and return a new path or URL that the remote Chrome can open.

**Call relations**: Browser-driving code uses this before asking Chrome to open or upload a file. The method keeps file placement tied to the transport provider, because only the provider knows whether the browser shares the sandbox filesystem or needs files shipped over a remote API.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: This returns the directory or storage location where Chrome should save downloads for this leased session. It lets local and remote browser providers choose the right place for downloaded files.

**Data flow**: The lease knows where its browser can write files. When asked, it returns a path or provider-specific location that Chrome can use as its download destination. Nothing is downloaded by this method; it only supplies the target location.

**Call relations**: The browser engine calls this when configuring Chrome download behavior. Later, when Chrome reports a completed download by its CDP download identifier, CdpLease.fetch_download uses that same provider-specific setup to retrieve the actual bytes.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This retrieves the bytes of a completed browser download. It is keyed by Chrome's download identifier, so the caller does not need to know where the provider stored the file.

**Data flow**: The input is a download guid, which is Chrome's unique name for a completed download. The provider uses that guid to find the downloaded content, either in the sandbox filesystem or in remote provider storage. The output is the raw bytes of the downloaded file.

**Call relations**: After the browser engine has configured downloads with CdpLease.download_dir and Chrome finishes a download, the engine or surrounding turn logic calls this method. The method brings the file back across the same boundary that created the browser lease.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: This releases whatever hold the lease has on the browser session. For a static local browser it may do nothing; for a hosted browser it may tell the provider that the session can be released.

**Data flow**: The lease represents resources borrowed for one turn. When this method is called, the concrete provider performs any cleanup needed for that session, such as ending or releasing a remote browser. It returns no value; the important result is that the lease is no longer being kept alive by this turn.

**Call relations**: Turn orchestration calls this at the end of browser use, usually during cleanup. It is the matching close step for CdpProvider.lease or CdpProvider.reattach, ensuring remote browser resources are not accidentally left running.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This creates a fresh browser lease for a turn. It may use the current sandbox session if the provider's Chrome runs inside that sandbox, or ignore it if the provider uses a fixed or remote browser.

**Data flow**: The input is optionally a SandboxSession, which represents the task's isolated working environment. The provider uses that information, if relevant, to locate or create a Chrome session. The output is a CdpLease that the rest of the system can use to connect, move files, fetch downloads, and eventually close.

**Call relations**: At the start of browser work, the system-selected provider is asked for a lease. From that point on, browser-driving code talks to the returned CdpLease rather than directly to the provider, keeping the rest of the flow independent from the provider's concrete implementation.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This tries to reconnect to a browser session described by a previously saved token. If the old session is gone, it raises SessionGone so the caller knows to create a new lease instead.

**Data flow**: The input is a token that was earlier returned by CdpLease.token. The provider interprets that token and checks whether the named browser session or endpoint is still usable. If it is, the output is a new CdpLease over that live session; if not, the method fails with SessionGone.

**Call relations**: Recovery or resume logic calls this before starting over with a new browser. A successful reattach lets the browser engine continue with the existing session; a SessionGone result tells the caller to fall back to CdpProvider.lease and rebuild the browser context.


### Per-turn browser bridge
The browser tool backend opens, reuses, transfers files through, and cleans up a real Chrome session during a conversation turn.

### `extensions/browser/ufo_ext_browser/bua/backend.py`

`orchestration` · `per-turn browser use and turn cleanup`

This file gives the rest of the system a simple “browser surface”: methods like navigate, read the page, fill a form, switch tabs, upload a file, and wait for a download. Behind those simple actions is a Chrome DevTools Protocol connection, often shortened to CDP, which is the remote-control channel used to drive Chrome. The important idea is lazy setup: if a turn never uses the browser, this file never opens Chrome. If a browser action is requested, it leases a browser from the current provider, connects a BrowserSession to it, and then reuses that session for later browser actions in the same turn.

It also solves two practical problems. First, it supports crash recovery. It stores a durable token for the leased browser session. If the process crashes before cleanup, a replayed turn can reattach to the same live browser instead of opening a new blank one. Normal cleanup clears that token so later turns do not reconnect to a released session. Second, it makes file transfer safe. Uploads are read from the workspace sandbox, size-limited, and passed through the lease so local and remote browsers work the same way. Downloads are fetched through the lease too, then returned as base64 text with a safe filename.

#### Function details

##### `BuaSurface._open`  (lines 60–73)

```
async def _open(self) -> BrowserSession
```

**Purpose**: Opens, or reuses, the browser session for this turn. This is the gatekeeper that makes sure browser work only starts when a tool actually needs Chrome.

**Data flow**: It starts with the current BuaSurface state. If a BrowserSession already exists, it returns it. If not, it gets or creates a CDP lease, asks that lease for a browser endpoint and download directory, builds a BrowserSession around them, opens it, stores it on the surface, and returns the ready session.

**Call relations**: All browser-facing actions call this first, such as navigation, page reading, finding, form input, tab work, uploads, downloads, and computer-style interaction. When there is no lease yet, it hands off to BuaSurface._acquire_lease; once it has an endpoint, it creates the BrowserSession that the rest of the methods use.

*Call graph*: calls 2 internal fn (_acquire_lease, __init__); called by 11 (computer, find, form_input, get_page_text, navigate, read_page, tabs_close, tabs_context, tabs_create, upload_file (+1 more)).


##### `BuaSurface._acquire_lease`  (lines 75–88)

```
async def _acquire_lease(self) -> CdpLease
```

**Purpose**: Gets access to a browser session from the CDP provider. It tries to reconnect to an existing session after a crash, and only creates a new browser lease when reconnecting is not possible.

**Data flow**: It reads a stored token, if there is one. If the token exists, it asks the provider to reattach to that old session. If that session is gone, it clears the token. Then it leases a fresh browser, stores the new token for possible crash recovery, and returns the lease.

**Call relations**: BuaSurface._open calls this when the surface has no lease yet. This function uses BuaSurface._stored_token to look for a recovery token and BuaSurface._store_token to save or clear that token, so the open path and crash-recovery path stay in one place.

*Call graph*: calls 2 internal fn (_store_token, _stored_token); called by 1 (_open).


##### `BuaSurface._stored_token`  (lines 90–94)

```
async def _stored_token(self) -> str | None
```

**Purpose**: Looks up the saved browser-session token for this conversation. The token is what lets a recovered turn reconnect to the same browser after a hard crash.

**Data flow**: It reads the optional scoped store and conversation id from the surface. If either is missing, it returns nothing. Otherwise it asks the store for the token key for this conversation and returns the value only if it is a string.

**Call relations**: BuaSurface._acquire_lease calls this before asking for a browser. Its result decides whether the surface tries to reattach to an existing browser session or leases a new one.

*Call graph*: called by 1 (_acquire_lease).


##### `BuaSurface._store_token`  (lines 96–99)

```
async def _store_token(self, token: str | None) -> None
```

**Purpose**: Saves or clears the browser-session token for this conversation. This is how the file remembers a live browser across crash recovery, while also avoiding stale reconnections after normal cleanup.

**Data flow**: It receives either a token string or None. If the surface has both a scoped store and a conversation id, it writes that value under the conversation-specific token key. If the store or id is missing, it quietly does nothing.

**Call relations**: BuaSurface._acquire_lease uses this after creating a fresh lease or after discovering an old token is no longer valid. BuaSurface.aclose uses it during cleanup to clear the token once the browser lease has been released.

*Call graph*: called by 2 (_acquire_lease, aclose).


##### `BuaSurface.navigate`  (lines 101–106)

```
async def navigate(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Moves the browser to a requested web address. It also accepts an optional tab id so the caller can choose which tab should navigate.

**Data flow**: It receives a dictionary of tool arguments, opens the browser session if needed, reads the url value, and checks that it is a string. It converts the optional tab_id into an integer or None, then asks the BrowserSession to navigate and returns that session’s result.

**Call relations**: This is one of the public tool methods using BuaSurface._open. Before handing off to the BrowserSession, it uses _tab_id to normalize the tab identifier so the lower-level browser driver gets a predictable value.

*Call graph*: calls 2 internal fn (_open, _tab_id).


##### `BuaSurface.tabs_context`  (lines 108–110)

```
async def tabs_context(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Returns information about the current browser tabs. A caller uses this to understand what tabs exist and which one is active.

**Data flow**: It receives the tool arguments, though it does not need specific values from them. It opens or reuses the browser session, asks the session for tab context, and returns the resulting dictionary.

**Call relations**: This public tool method starts through BuaSurface._open like the other browser actions. Once the session is ready, it delegates the actual tab inspection to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_create`  (lines 112–115)

```
async def tabs_create(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Creates a new browser tab. If the caller does not provide a usable URL, it opens a blank page.

**Data flow**: It receives a dictionary of arguments, opens the browser session, reads the optional url value, and chooses that URL only if it is a non-empty string. Otherwise it uses about:blank, then asks the BrowserSession to create the tab and returns the result.

**Call relations**: This method is a public tab tool entry on the surface. It relies on BuaSurface._open for session setup and then hands the tab creation request to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.tabs_close`  (lines 117–119)

```
async def tabs_close(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Closes a browser tab according to the caller’s arguments. This lets tool users tidy up or leave the browser focused on the right tab.

**Data flow**: It receives the tab-closing arguments, opens or reuses the browser session, passes those arguments to the session’s tab-closing logic, and returns whatever the session reports back.

**Call relations**: This public method follows the same pattern as the other tab actions: BuaSurface._open prepares the session, then BrowserSession performs the browser-specific work.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.upload_file`  (lines 121–142)

```
async def upload_file(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Attaches one or more workspace files to a file input on the web page. It makes uploads work whether Chrome can read the workspace directly or needs the files shipped to a remote browser.

**Data flow**: It receives upload arguments and expects a files list. Each file path must be a non-empty string, is resolved as a workspace path, and is passed through the current lease. For remote browsers, the lease can call back into BuaSurface._read to get the bytes. The method then asks the BrowserSession to attach the placed files, waits until shipped files really appear in the page input, and returns the session’s reply.

**Call relations**: This public upload tool opens the session with BuaSurface._open, uses BuaSurface._lease to access the transport, and calls BuaSurface._settle_upload afterward. It uses workspace_path so callers cannot accidentally name arbitrary host paths, and partial connects each path to the later byte-reading callback.

*Call graph*: calls 3 internal fn (_lease, _open, _settle_upload); 2 external calls (partial, workspace_path).


##### `BuaSurface._settle_upload`  (lines 144–164)

```
async def _settle_upload(self, session: BrowserSession, args: dict[str, JsonValue]) -> None
```

**Purpose**: Waits until an uploaded file is truly present in the page, not just named there. This protects against a remote-upload race where the page sees an empty file because the bytes have not arrived yet.

**Data flow**: It reads the list of file sizes that this surface actually shipped. If nothing was shipped, it returns immediately. Otherwise it repeatedly asks the BrowserSession what sizes are attached in the page, compares them with the expected sizes, waits briefly if needed, and re-attaches the files. If the sizes never match, it raises an error explaining what was expected and what the page reported.

**Call relations**: BuaSurface.upload_file calls this after the first attachment attempt. During the wait loop it uses BrowserSession.attached_sizes to check the page and BrowserSession.upload_file to retry the attachment after each short sleep.

*Call graph*: calls 2 internal fn (attached_sizes, upload_file); called by 1 (upload_file); 1 external calls (sleep).


##### `BuaSurface._lease`  (lines 166–169)

```
def _lease(self) -> CdpLease
```

**Purpose**: Returns the active CDP lease, or fails clearly if no lease exists. It is a small safety check for code that must talk to the browser transport.

**Data flow**: It reads the surface’s lease field. If a lease is present, it returns it. If not, it raises a runtime error saying the browser has no CDP lease.

**Call relations**: BuaSurface.upload_file uses this when placing files where Chrome can open them. BuaSurface.wait_for_download uses it when fetching downloaded bytes back from the transport.

*Call graph*: called by 2 (upload_file, wait_for_download).


##### `BuaSurface._read`  (lines 171–194)

```
async def _read(self, path: str) -> bytes
```

**Purpose**: Reads a workspace file from the turn’s sandbox so it can be shipped to a remote browser. It enforces a size limit before loading the whole file into memory.

**Data flow**: It receives a sandbox path string. It requires a sandbox session, safely quotes the path for shell use, asks the sandbox for the file size, rejects unreadable or oversized files, then reads the file as base64 text from the sandbox. It decodes that text into bytes in a worker thread, records the byte length in _shipped, and returns the bytes.

**Call relations**: This function is supplied as a callback by BuaSurface.upload_file when the lease needs file bytes. It uses shlex.quote to avoid unsafe shell path handling and asyncio.to_thread so base64 decoding does not block other async work.

*Call graph*: 2 external calls (to_thread, quote).


##### `BuaSurface.read_page`  (lines 196–198)

```
async def read_page(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Asks the browser session for a structured reading of the current page. This is useful when the tool needs a page summary or accessibility-style view rather than raw text alone.

**Data flow**: It receives page-reading arguments, opens or reuses the browser session, passes the arguments to BrowserSession.read_page, and returns the resulting dictionary.

**Call relations**: This public browser tool method uses BuaSurface._open for connection setup. After that, the specialized page-reading work happens inside BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.get_page_text`  (lines 200–202)

```
async def get_page_text(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Gets the text content of the current page. A caller uses this when it needs readable page text without interacting with controls.

**Data flow**: It receives arguments describing what text to fetch, opens or reuses the browser session, forwards the request to BrowserSession.get_page_text, and returns the session’s answer.

**Call relations**: Like the other page tools, it begins with BuaSurface._open so callers do not have to know whether Chrome is already connected. It then delegates the actual browser inspection to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.find`  (lines 204–206)

```
async def find(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Finds something on the page, optionally using the host-side find completer to improve or finish the search. This supports browser tools that need to locate visible elements or text.

**Data flow**: It receives find arguments, opens the browser session, and passes both the arguments and the optional find_completer into BrowserSession.find. It returns the find result from the session.

**Call relations**: This public method uses BuaSurface._open for session setup. It is also where the surface injects the find completer into the lower-level BrowserSession search flow.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.form_input`  (lines 208–210)

```
async def form_input(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Fills or changes form fields on the current page. It is used for browser actions such as typing into inputs or selecting form values.

**Data flow**: It receives form-input arguments, opens or reuses the browser session, forwards the request to BrowserSession.form_input, and returns the browser session’s response.

**Call relations**: This tool method sits between the caller and BrowserSession. BuaSurface._open makes sure the browser connection exists before the session performs the form interaction.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.computer`  (lines 212–214)

```
async def computer(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Performs lower-level computer-like actions in the browser, such as direct interaction commands driven by the browser session. It gives the tool layer a general interaction path beyond the more specific methods.

**Data flow**: It receives interaction arguments, opens or reuses the browser session, passes the arguments to BrowserSession.computer, and returns the result.

**Call relations**: This public browser action follows the same surface pattern as navigation and form input. It calls BuaSurface._open first, then hands the detailed browser-control work to BrowserSession.

*Call graph*: calls 1 internal fn (_open).


##### `BuaSurface.wait_for_download`  (lines 216–232)

```
async def wait_for_download(self, args: dict[str, JsonValue]) -> dict[str, JsonValue]
```

**Purpose**: Waits for a browser download to finish and returns the downloaded bytes to the caller as base64 text. It also cleans the suggested filename so a web page cannot smuggle path tricks into the caller’s workspace.

**Data flow**: It receives wait arguments, opens the browser session, and asks it to wait for a download. It then uses the active lease to fetch the downloaded bytes by the download id. The bytes are base64-encoded in a worker thread, the suggested filename is reduced to one safe file name, and the method returns the filename, encoded content, and byte size.

**Call relations**: This public download tool starts with BuaSurface._open and then uses BuaSurface._lease because only the transport knows where the download was stored. It calls _file_name before returning so later code can safely join the name into a workspace path.

*Call graph*: calls 3 internal fn (_lease, _open, _file_name); 1 external calls (to_thread).


##### `BuaSurface.aclose`  (lines 234–248)

```
async def aclose(self) -> None
```

**Purpose**: Closes the browser session, releases the lease, and clears the saved recovery token at the end of a normal turn. This prevents paid or remote browser sessions from being left behind and prevents later turns from reattaching to a released browser.

**Data flow**: It looks at the current session and lease fields. If a session exists, it closes it and clears the field. Whether that succeeds or fails, it then closes the lease if present, clears the lease field, and stores None as the token.

**Call relations**: Turn cleanup calls this after browser use. It uses BuaSurface._store_token to remove the durable token; if a hard crash skips this function, the token remains, which is exactly what lets BuaSurface._acquire_lease reattach during recovery.

*Call graph*: calls 1 internal fn (_store_token).


##### `_file_name`  (lines 251–257)

```
def _file_name(suggested: str) -> str
```

**Purpose**: Turns a browser-suggested download name into one safe file name. It prevents names like ../notes.md or /etc/passwd from escaping the directory the caller intended.

**Data flow**: It receives the suggested filename string, treats it like a POSIX-style path, and keeps only the final name part. If that final part is empty, . , or .., it returns a default name instead.

**Call relations**: BuaSurface.wait_for_download calls this before returning a downloaded file to the caller. PurePosixPath is used so the cleanup follows web-style forward-slash paths, no matter what operating system is running the code.

*Call graph*: called by 1 (wait_for_download); 1 external calls (PurePosixPath).


##### `_tab_id`  (lines 260–271)

```
def _tab_id(value: JsonValue) -> int | None
```

**Purpose**: Converts a caller-provided tab identifier into either an integer tab id or None. This lets navigation accept a few common input shapes without passing confusing values onward.

**Data flow**: It receives a JSON-like value. Booleans become None, integers are kept, floats and non-empty strings are converted to integers, and anything else becomes None.

**Call relations**: BuaSurface.navigate calls this when preparing a navigation request. The result is then passed to BrowserSession.navigate so the session receives a simple tab id choice instead of raw user input.

*Call graph*: called by 1 (navigate).


### Hosted browsing tools
Browser Use integration exposes cloud-hosted web browsing tasks as system tools.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This extension is a replacement for the project’s usual browser automation stack. Instead of running its own browser and step-by-step browser agent, it sends a task to Browser Use’s hosted service through their REST API, which is a web interface that accepts requests and returns structured responses. The file matters because other parts of the system already know the tool names `browser_task` and `wide_browse`; by keeping those names, the rest of the project can switch browser providers without changing its prompts or evaluation code.

The main worker is `HostedRun`. Think of it like a courier for one outsourced browser job: it starts the job, checks back until it finishes, gathers the final answer, and optionally brings back files the hosted browser created. It also protects the workspace by rejecting unsafe file paths, limiting how many files and bytes are downloaded, and only downloading files over secure `https` links.

The single-task tool, `_browser_task`, runs one careful browser session and saves output files. The batch tool, `_wide_browse`, reads a list of sites or entities from a workspace file, launches several hosted runs in parallel, and writes a combined JSON results file. API keys are read through the host-side credential system, so the secret key is used to call Browser Use but is not copied into the sandbox workspace.

#### Function details

##### `HostedRun.execute`  (lines 132–158)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one complete Browser Use job from start to finish. It checks the task size, gets the API key, starts the hosted browser run, waits for it, cancels it if the local timeout expires, collects any allowed output files, and returns a clear outcome.

**Data flow**: It receives the tool context, the task text, a timeout, and optionally a deduplication key used to avoid repeating paid work. It reads the Browser Use API key from credentials, opens an HTTP client, creates or reuses a run, polls for completion, fetches the final run summary, downloads permitted output files if requested, and returns a `RunOutcome` containing the status, result or error text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the main path used by both browser tools through `HostedRun`. It hands work to `_start` to create or recover the run, `_watch` and `_status` to follow progress, `_json` to safely read API replies, and `_collect` to bring output files into the workspace.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 160–189)

```
async def _start(self, http: httpx.AsyncClient, ctx: ToolContext, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Creates a Browser Use run, or reattaches to one that was already recorded under the same deduplication key. This prevents unnecessary duplicate paid browser runs when a batch task is retried.

**Data flow**: It receives an HTTP client, the tool context, the task text, and an optional deduplication key. If the key already has stored run information, it validates and returns that. Otherwise it posts the task, model, cost limit, and browser settings to Browser Use, reads the new run ID and workspace ID from the response, stores them if a key was provided, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this near the beginning of every run. `_start` depends on `_json` to turn the API response into a usable object and `_text` to require the important string fields.

*Call graph*: calls 2 internal fn (_json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 191–196)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits until a hosted run reaches a final state such as completed, failed, stopped, or cancelled. It is the polling loop that keeps checking the outside service.

**Data flow**: It receives an HTTP client and a run ID. It repeatedly asks `_status` for the latest state. If the state is final, it returns that state; otherwise it sleeps briefly and checks again.

**Call relations**: `HostedRun.execute` calls `_watch` inside a timeout window. `_watch` delegates each actual status read to `_status`, keeping the wait loop separate from the API response parsing.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 198–202)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run once. It is deliberately small so the caller can use it both during normal polling and right before deciding whether to cancel a timed-out run.

**Data flow**: It receives an HTTP client and a run ID. It sends a GET request to the run status endpoint, parses the JSON response with `_json`, extracts the required `status` text with `_text`, and returns that status string.

**Call relations**: `_watch` calls this repeatedly while waiting. `HostedRun.execute` also calls it after a local timeout, because the run might have finished during the last sleep before cancellation.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 204–241)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies safe, allowed output files from a Browser Use workspace into the local conversation workspace. It also records which files were skipped so the user is not left guessing.

**Data flow**: It receives an HTTP client, the tool context, and the Browser Use workspace ID. If this run is not supposed to save outputs, it returns empty results. Otherwise it asks Browser Use for a limited file listing, checks that each listed item has a safe relative path and a size, skips files that are too large or missing a download URL, downloads allowed files, writes them into the sandbox workspace, and returns saved files, skipped files, and a flag saying whether the listing was cut short.

**Call relations**: `HostedRun.execute` calls this after reading the run summary. `_collect` uses `_json` for the file listing, `_download` for individual file bodies, and raises `BrowserUseError` when the vendor response or file path is unsafe.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, Path).


##### `HostedRun._download`  (lines 243–259)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a presigned URL, without sending the Browser Use API key to that storage URL. This keeps the project’s credential away from the file-hosting origin.

**Data flow**: It receives a URL from the Browser Use file listing. It first requires the URL to start with `https://`, then opens a separate HTTP client without the API header, fetches the bytes, rejects HTTP error responses, and returns the file content as bytes.

**Call relations**: `_collect` calls `_download` for each file that is small enough and has a usable URL. `_download` is the safety gate between a vendor-provided file link and writing bytes into the sandbox.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 262–277)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Browser Use into a plain dictionary, while converting bad status codes or unreadable bodies into clear `BrowserUseError` messages.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises an error with the endpoint path, code, and body. Otherwise it tries to parse JSON, requires the parsed value to be an object, normalizes keys to strings, and returns the dictionary.

**Call relations**: This helper is used throughout the run flow by `execute`, `_start`, `_status`, and `_collect`. It gives all API-reading code the same behavior when Browser Use sends an error or malformed response.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 280–286)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Extracts one required text field from an API response dictionary. It turns a missing or non-text field into the same kind of readable Browser Use error used elsewhere.

**Data flow**: It receives a response dictionary and the name of a field to read. It looks up that field, checks that the value is a string, returns it if valid, and raises `BrowserUseError` if not.

**Call relations**: `_start` uses this to read the new run ID and workspace ID. `_status` uses it to read the run status. This keeps required-field checks consistent.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 331–364)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the public `browser_task` tool. It asks Browser Use to run one full browser session starting from a given URL and returns the result plus information about any output files.

**Data flow**: It receives the tool context and validated browser-task input. It creates a `HostedRun` configured for the more capable task model, a higher cost limit, and output-file saving. It combines the starting URL and user instructions into one self-contained task, waits up to the requested timeout, and returns a `ToolResult` containing JSON with the result, saved file paths, skipped files, and whether more files exist. If the local timeout is hit, it returns an error result explaining that the run was cancelled.

**Call relations**: This function is registered as the handler for the `browser_task` tool. When invoked by the tool system, it relies on `HostedRun.execute` to do the real external browser run and wraps that outcome in the project’s standard tool-result format.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 367–374)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a workspace file through the sandbox shell, while safely quoting the path so model-supplied filenames cannot accidentally become shell commands.

**Data flow**: It receives the tool context and a file path. It shell-quotes the path, runs `cat` inside the sandbox, checks the exit code, raises a clear error if reading failed, and returns the file contents as text.

**Call relations**: `_read_lines` uses this to load the entity list for batch browsing. `_wide_browse` also uses it directly to load the JSON schema file that should guide each browser run’s output.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 377–385)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a text file as a clean list of unique, non-empty lines. This is used to turn a user-provided list of sites or entities into batch work items.

**Data flow**: It receives the tool context and a path. It reads the file with `_read_file`, splits it into lines, trims whitespace, skips blank lines, removes duplicates while preserving first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this at the start of a batch run to decide which entities should each get their own hosted browser visit.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 388–434)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the public `wide_browse` tool. It runs many small Browser Use jobs in parallel, one per URL or site name, then saves and returns a combined table of results.

**Data flow**: It receives the tool context and validated batch-browse input. It reads unique entities from one workspace file, enforces a maximum batch size, reads the output schema from another file, creates a shared `HostedRun` configured for cheaper batch work, limits how many visits run at once with a semaphore, gathers all visit results, converts ordinary per-entity failures into error rows, writes the full result list to `wide_browse.json`, and returns JSON containing both the rows and the output filename.

**Call relations**: This function is registered as the handler for the `wide_browse` tool. It calls `_read_lines` and `_read_file` for inputs, constructs `HostedRun` for the Browser Use calls, launches its nested `visit` function across the batch with `asyncio.gather`, and finally writes the aggregate output into the sandbox.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 404–419)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the hosted browser task for one entity inside a wider batch. It turns one URL or site name into one row of structured batch output.

**Data flow**: It receives a single entity from the surrounding `_wide_browse` loop. It waits for a parallelism slot, fills the prompt template by replacing `{entity}`, appends the output schema if present, chooses a deduplication key when the tool call has an idempotency key, runs `HostedRun.execute`, and returns a dictionary with the entity, final status, and result text.

**Call relations**: `_wide_browse` creates and launches this nested function once per entity. Its exceptions are collected by `_wide_browse`, which turns normal per-entity failures into error rows so one bad site does not discard the rest of the already-paid batch.


##### `manifest`  (lines 456–471)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, tools, prompt text, and required credential. Without this, the project would not know how to load or expose the Browser Use tools.

**Data flow**: It takes no input. It packages the two tool definitions, a browser prompt section read from the companion markdown file, and a credential slot for the Browser Use API key into a `Manifest`, then returns that manifest to the extension loader.

**Call relations**: The extension system calls `manifest` during loading. The returned manifest is what makes `_browser_task` and `_wide_browse` available as tools and tells the host that it must provide the `browser_use_api_key` credential.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sandbox site workflows
Site tools build, preview, and publish sandbox-hosted web projects while verifying server readiness for browser checks.

### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `tool invocation during build, preview, and publish requests`

This file gives the system a safe, repeatable way to work with websites inside a sandbox, which is an isolated container-like workspace. Without it, an assistant might run ad-hoc shell commands to build or start a site, then return too early, leave old servers occupying ports, or expose files in the wrong way.

The file defines input shapes for four tools: building a site, starting any server, serving a static website folder, and publishing a fuller web app. These input models say what information the caller must provide, such as the project folder, command to run, port, and a plain-language description for the activity timeline.

The main helper, `_serve`, is the shared “launch checklist.” It first frees the chosen port, then starts the server in the background with `nohup` so it keeps running after the command returns. It writes server output to a log file. Then it repeatedly tries to connect to the port until the server is ready or a timeout is reached. This is like waiting until a shop has unlocked its door before telling someone it is open.

The public tool functions use that helper where needed and return small JSON answers containing useful facts such as the local URL, port, log path, or produced files.

#### Function details

##### `StartServerInput.validate_port`  (lines 70–73)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents callers from asking the sandbox to listen on impossible or invalid ports.

**Data flow**: It reads the `port` value from the `StartServerInput` object. If the port is missing, it accepts that because a default will be used later; if the port is present, it must be between 1 and 65535. The same input object comes out unchanged when valid, or an error is raised when invalid.

**Call relations**: This runs automatically when a `StartServerInput` object is validated before the `start_server` tool receives it. Its job is to catch bad input early, before `_serve` tries to run shell commands with that port.


##### `_json_result`  (lines 103–104)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This turns a Python dictionary into the standard tool response format used by the rest of the system. Tool callers receive the result as JSON text rather than a raw Python object.

**Data flow**: A dictionary goes in. The function converts it to a JSON string, wraps that text in a `TextContent` object, then wraps that content in a `ToolResult`. The output is a ready-to-return tool result.

**Call relations**: The user-facing tool functions call this at the end of their work. `website`, `start_server`, `deploy_website`, and `publish_website` each gather their final facts, then hand them to `_json_result` so every site tool responds in the same shape.

*Call graph*: called by 4 (deploy_website, publish_website, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_serve`  (lines 107–144)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This starts a server inside the sandbox and waits until it is reachable before returning. It is the safety layer that avoids stale ports, half-started servers, and unclear startup failures.

**Data flow**: It receives the sandbox context, a server command, a project directory, a port, and a log file path. It builds a shell script that changes into the project folder, kills any old process using the same port, starts the new command in the background, and probes the port until it accepts a connection. If the server becomes ready, it returns a dictionary with the URL, port, and log path; if not, it reads the end of the log and raises an error with the most useful message it can find.

**Call relations**: `start_server`, `deploy_website`, and `publish_website` all rely on `_serve` instead of each implementing their own launch logic. It is the common backstage worker: the public tools decide what command and folder to use, then `_serve` does the actual port cleanup, background launch, and readiness check.

*Call graph*: called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `website`  (lines 147–156)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This builds a website project in the sandbox and reports what files are present afterward. It is useful when the task is to run a build command, such as producing static web output.

**Data flow**: It receives a tool context and validated website input. It chooses the requested project folder, or the workspace root if none was provided, then runs the build command there with a long timeout. If the build fails, it raises an error using the command output; if it succeeds, it lists the project directory and returns the project path plus the file names as JSON.

**Call relations**: This is the handler for the `website` tool definition. It uses the sandbox to run the build command, uses shell quoting for the project path, and then calls `_json_result` to package the final answer for the caller.

*Call graph*: calls 1 internal fn (_json_result); 1 external calls (quote).


##### `start_server`  (lines 159–163)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This starts a caller-specified server command in the background and returns the local URL once the server is ready. It is meant to be safer than asking the assistant to run a raw shell command for a long-running server.

**Data flow**: It receives the server command, project path, optional port, optional log file, and context. It fills in defaults for the port and log file when they are not provided, then asks `_serve` to do the actual launch and readiness check. It returns JSON containing the server URL, port, log file, and project path.

**Call relations**: This is the handler for the `start_server` tool definition. It delegates the hard parts to `_serve`, then sends the result through `_json_result` so the caller gets a clear, structured response.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `deploy_website`  (lines 166–170)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This serves a folder of already-built static website files from the sandbox. It gives the caller a reachable route for previewing files such as `index.html`.

**Data flow**: It receives the directory to serve, a site name, an entry file name, and the tool context. It creates a simple Python static-file server command on the standard app port, chooses a deploy log path, and asks `_serve` to start that server in the given folder. It returns JSON with the URL, port, log path, site name, and entry point.

**Call relations**: This is the handler for the `deploy_website` tool definition. It is a specialized wrapper around `_serve`: it does not build anything, but turns a static output folder into a running preview and then uses `_json_result` for the final response.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `publish_website`  (lines 173–185)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This publishes a web app from the sandbox, optionally installing dependencies first and optionally running a backend command. It supports both simple static output and apps that need a custom server process.

**Data flow**: It receives the project path, built output path, app name, optional install command, optional run command, and context. If an install command is provided, it runs that in the project folder and stops with an error if it fails. Then it chooses the server command: either the provided backend command or a simple Python static-file server. It also chooses the working folder: the project folder for a custom run command, or the built output folder for static serving. Finally it starts the server through `_serve` and returns JSON with the reachable URL details and app name.

**Call relations**: This is the handler for the `publish_website` tool definition. It performs the extra setup step that publishing may need, then hands server startup to `_serve` and formats the finished result with `_json_result`.

*Call graph*: calls 2 internal fn (_json_result, _serve); 1 external calls (quote).

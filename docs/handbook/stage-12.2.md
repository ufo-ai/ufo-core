# Browser provider adapters and sandbox Chrome launchers  `stage-12.2`

This stage is shared support for any part of the system that needs to use the web during a turn of work. Its job is to provide a usable Chrome browser, without forcing the main code to care where that browser lives. The core browser file defines the common contract: the system asks for a browser connection, and an adapter supplies one, whether it is local, sandboxed, or hosted.

The Browserbase adapter connects to a hosted Chrome service. It creates a fresh remote browser session for each run, keeps login and browsing state only for that run, then cleans the session up when finished. The sandbox Chrome adapter runs a real headless Chrome, meaning Chrome without a visible window, inside the same protected workspace as the conversation. It can start or reuse that browser, expose its debugging connection, and recover downloaded files. The Browser Use adapter is higher level: it sends whole browsing jobs to a cloud service through tools called browser_task and wide_browse. Together, these adapters act like interchangeable power plugs for web access.

## Files in this stage

### Browser contract
Defines the shared interface the core system uses to obtain a browser connection for one turn of work.

### `core/src/ufo/browser.py`

`data_model` · `per-turn browser setup and cleanup`

This file is a boundary, or “seam,” between the core application and whatever browser provider is plugged in. The core needs a Chrome DevTools Protocol endpoint, often called CDP, which is the URL and connection details used by automation tools to control Chrome. But core deliberately does not create Chrome itself. Instead, it asks a provider for a short-lived lease.

A lease is like borrowing a key for one job. `CdpProvider` can create a `CdpLease` for a turn, or reconnect to an older session using a saved token. The lease then answers practical questions: where is the browser endpoint, what token can be saved for later, where should files be placed so Chrome can see them, where should downloads go, and how should downloaded bytes be fetched back.

The file also defines `CdpEndpoint`, the small piece of data that carries the browser URL and optional connection headers, and `SessionGone`, the signal that a previous browser session no longer exists. The important design choice is separation: local sandbox Chrome, remote hosted Chrome, and any future provider can all fit the same shape, while the core code stays independent of those details.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the browser connection information for this lease. The caller uses it to connect a browser automation engine to Chrome.

**Data flow**: The lease already represents a chosen browser session. This method reads that session’s connection details and returns a `CdpEndpoint`, which contains a URL and any needed headers. If the lease cannot provide a usable endpoint, an implementation may raise an error instead of returning bad connection data.

**Call relations**: After a provider creates or reattaches a lease, the browser-driving extension asks the lease for its endpoint. This method is the handoff point from “we have reserved a browser” to “the automation engine can now connect to it.”


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: Returns a saved handle for this browser session. The system can store this token so a later run can try to reconnect to the same browser session.

**Data flow**: The lease reads whatever durable identifier its provider uses, such as a hosted session ID or a stable URL. It returns that identifier as a string that can be serialized and saved. It does not itself reconnect; it only supplies the value needed for reconnection later.

**Call relations**: A browser extension or turn recorder asks for this token after a lease exists. Later, that token can be passed to `CdpProvider.reattach` so the provider can try to rebuild a lease around the same live session.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Makes a workspace file available to the leased browser and returns the path or location Chrome should open. This matters because a local sandbox browser and a remote browser do not see files in the same way.

**Data flow**: The caller gives a workspace path and a `read` function that can asynchronously fetch the file’s bytes. A sandbox-local provider may simply return the same path because Chrome shares the filesystem. A remote provider may call `read`, upload the bytes somewhere the hosted browser can access, and return that remote location.

**Call relations**: When browser automation needs Chrome to open or upload a file, it asks the lease to translate the file location into the browser’s world. This method hides the difference between shared-disk and remote-browser setups from the rest of the browser engine.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the directory or provider-specific location where Chrome should write downloads for this lease. The automation engine uses this before allowing Chrome downloads.

**Data flow**: The lease checks how its browser stores downloaded files. For a sandbox browser, it can return a path inside the sandbox. For a hosted browser, it can return a location controlled by the provider. The output is a string that Chrome can be configured to use as its download destination.

**Call relations**: Before downloads happen, the browser engine asks the lease where downloads should go. Later, downloaded files can be retrieved through `CdpLease.fetch_download`, using the download identifier assigned by Chrome.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed browser download. The caller supplies Chrome’s download identifier, and the lease knows how to get the actual file back.

**Data flow**: The input is a download GUID, a unique ID Chrome assigns to a downloaded file. The lease uses the storage method appropriate for its provider: reading from the sandbox filesystem for a local browser, or calling a remote provider’s API for a hosted browser. It returns the downloaded file as raw bytes.

**Call relations**: After Chrome finishes a download, the browser engine calls this method to bring the file back into the turn’s workspace or result flow. It pairs with `CdpLease.download_dir`, which told Chrome where to write the download in the first place.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: Releases the browser lease when the turn is done. This prevents temporary browser resources from lingering longer than needed.

**Data flow**: The lease receives no new data. It performs whatever cleanup its provider requires: possibly nothing for a fixed local endpoint, or releasing a hosted browser session for a remote provider. It returns nothing once cleanup is complete.

**Call relations**: At the end of a turn, the surrounding orchestration calls this method to close out the borrowed browser access. It is the cleanup counterpart to `CdpProvider.lease` and `CdpProvider.reattach`.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a fresh browser lease for one turn of work. It may use the turn’s sandbox if the browser lives inside that sandbox.

**Data flow**: The caller may pass a `Sandbox`, which represents the isolated environment for the turn. The provider decides how to use it: a sandbox-based provider can point at Chrome inside that environment, while a remote provider can ignore it and create a hosted browser session. The result is a `CdpLease` that the rest of the system can use for connection, files, downloads, and cleanup.

**Call relations**: Turn setup calls this when there is no previous browser session to resume, or when resuming failed. The returned lease becomes the central object used by the browser extension throughout the turn.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser session that was saved earlier. If the session is gone, it signals that the caller should start fresh instead.

**Data flow**: The input is a saved token, such as a hosted session ID or stable endpoint string. The provider looks up or resolves the live browser session named by that token. If it succeeds, it returns a new `CdpLease` around that existing session. If the session no longer exists, it raises `SessionGone` rather than pretending recovery worked.

**Call relations**: Recovery or resume code calls this before creating a new browser. It connects back to the token produced by `CdpLease.token`; when it cannot, the wider flow falls back to `CdpProvider.lease` to create a new browser session.


### Chrome provider adapters
Implements concrete browser providers for hosted Browserbase sessions and sandbox-local headless Chrome instances.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `active during browser subagent turns, from remote browser session creation through cleanup`

A browser run here does not start Chrome on the local machine. Instead, it asks Browserbase to start a hosted Chrome session and returns the connection address that the rest of the system can drive. This matters because Browserbase sessions cost money and hold private browser state, such as cookies and local storage. Without this file, the system could not safely create, reconnect to, upload files into, download files from, or shut down those hosted browsers.

The file has three main parts. BrowserbaseApi is the small wrapper around Browserbase’s web API. It sends authenticated HTTP requests, checks that replies are usable, and turns bad replies into clear failures. BrowserbaseCdpProvider is the entry point the rest of the system uses when it wants a browser lease. It finds or creates a Browserbase “context,” which is Browserbase’s saved browser state, then creates a session tied to that context. BrowserbaseLease represents one active hosted browser session. It can return the browser connection URL, upload a local file into Browserbase’s special upload folder, fetch completed downloads, and close the session.

A key detail is lifetime. The context is kept only for the current browser run, so a recovery can regain logins, but closing the lease deletes both the session and the context. The API key is read on each request from host-side credentials, so it is not exposed inside the sandbox.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new Browserbase browser session that uses an existing Browserbase context for saved browser state. It returns both the session identifier and the URL used to connect to the hosted Chrome.

**Data flow**: It receives a context ID. It sends a POST request asking Browserbase for a session with that context and a fixed timeout. From the JSON reply, it extracts the new session ID and connection URL and returns them as a pair.

**Call relations**: BrowserbaseCdpProvider.lease uses this after it has found or created the context for the current browser run. Internally, this function relies on BrowserbaseApi._json to make the request and _field to make sure the needed reply fields are present.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session can still be used and returns its current connection URL. It is used when the system is trying to reconnect instead of starting over.

**Data flow**: It receives a session ID. It asks Browserbase for that session’s current details, checks that the status is still live, and extracts the connection URL. If Browserbase says the session is no longer running or pending, it raises SessionGone so the caller knows reconnecting is not possible.

**Call relations**: BrowserbaseCdpProvider.reattach calls this after decoding a saved token. It uses BrowserbaseApi._json for the lookup and _field to read required values from the reply.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Asks Browserbase to release, or shut down, a hosted browser session. This stops the remote browser from continuing to run after the browser turn is over.

**Data flow**: It receives a session ID. It sends Browserbase a request setting the session status to the release status. It returns nothing, but the remote session is requested to stop.

**Call relations**: BrowserbaseLease.aclose calls this during cleanup. The actual HTTP work goes through BrowserbaseApi._json, which checks that Browserbase did not return an error.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a Browserbase context, which is the saved browser state used by sessions in one browser run. This lets a restarted session in the same run keep cookies and local storage.

**Data flow**: It sends an empty POST request to Browserbase’s contexts endpoint. It reads the context ID from the JSON reply and returns that string.

**Call relations**: BrowserbaseCdpProvider._context calls this when there is no saved context ID for the current conversation. It uses BrowserbaseApi._json for the request and _field to verify the returned ID.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase context after the browser run is finished. This prevents authenticated browser state from living longer than the run that created it.

**Data flow**: It receives a context ID. It sends a DELETE request to Browserbase for that context. It returns nothing, but the remote saved state is requested to be removed.

**Call relations**: BrowserbaseLease.aclose calls this after releasing the session. It uses BrowserbaseApi._send directly because it only needs to know whether the request succeeded, not read a JSON body.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed download from Browserbase session storage. It waits briefly for Browserbase’s download listing to catch up, and refuses files that are too large or whose size is not reported.

**Data flow**: It receives a session ID and a download GUID, which is the name Browserbase should report for the stored file. It repeatedly asks Browserbase for the session’s download list, finds the matching entry, checks the reported size, then downloads the file bytes. If the file never appears, is too large, or lacks a size, it raises an error.

**Call relations**: BrowserbaseLease.fetch_download delegates to this when the browser engine asks for a downloaded file. This function uses BrowserbaseApi._json to inspect the listing, _size to validate the reported file size, _field to read the download entry ID, BrowserbaseApi._send to fetch the bytes, and asyncio.sleep between retry attempts.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads file bytes into a Browserbase session so the hosted browser can use them. This is needed because a remote Chrome cannot read files directly from the local workspace path.

**Data flow**: It receives a session ID, the remote filename to use, and the file’s bytes. It sends those bytes to Browserbase’s session upload endpoint. It returns nothing, but Browserbase stores the uploaded file for that session.

**Call relations**: BrowserbaseLease.place_file calls this after reading and naming a workspace file. The HTTP request itself goes through BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase request and makes sure the reply is a JSON object, meaning a dictionary-like response. It gives higher-level methods a safe, predictable response shape.

**Data flow**: It receives an HTTP method, path, and request options. It calls BrowserbaseApi._send, parses the response as JSON, checks that the result is an object, and returns it. If Browserbase replies with something unexpected, it raises BrowserbaseError.

**Call relations**: The session, context, status, release, and download-list methods call this whenever they expect JSON back from Browserbase. It centralizes the common send-and-parse step so those methods can focus on the specific fields they need.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase with the API key attached. It is the lowest-level network doorway in this file.

**Data flow**: It receives an HTTP method, API path, timeout, and request options. It reads the Browserbase API key from the credential store, adds it to the request headers, sends the request with httpx, and returns the response if it succeeded. If Browserbase returns an error status, it raises BrowserbaseError with the status and response text.

**Call relations**: BrowserbaseApi._json uses this for JSON endpoints, while delete_context, download, and upload use it directly when they need non-JSON behavior or only need success/failure. All Browserbase network traffic in this file passes through this function.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Reads a required string field from a Browserbase JSON reply. It turns missing or empty fields into a clear BrowserbaseError instead of letting bad data travel farther.

**Data flow**: It receives a dictionary-like response body and a field name. It looks up the value, checks that it is a non-empty string, and returns it. If the value is missing or not a usable string, it raises an error.

**Call relations**: BrowserbaseApi.create_session, live_session, create_context, and download use this after BrowserbaseApi._json has returned a response. It acts like a small safety gate before IDs and URLs are trusted.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the reported size of a Browserbase download. This protects the process from pulling an unknown or oversized file into memory.

**Data flow**: It receives one download-list entry. It reads the size field, rejects booleans and non-numeric values, converts the size to an integer, and returns it. If the listing does not provide a real size, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.download calls this before fetching the file bytes. It is the guard that lets the download code enforce the maximum download size before reading the file.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the connection information that the rest of the browser engine needs to talk to the hosted Chrome session. In practical terms, it hands back the remote browser’s address.

**Data flow**: It reads the lease’s stored connection URL. It wraps that URL in a CdpEndpoint object and returns it. Nothing remote changes.

**Call relations**: Higher-level browser code calls this after receiving a lease from BrowserbaseCdpProvider.lease or BrowserbaseCdpProvider.reattach. It creates the CdpEndpoint object expected by the rest of the system.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a reconnect token that names the conversation, session, and context for this browser run. The token lets a later recovery attempt reattach to the same remote session without guessing.

**Data flow**: It reads the lease’s conversation ID, session ID, and context ID. It formats them into one slash-separated string and returns it. No network request is made.

**Call relations**: Higher-level browser code can save this token while a session is active. BrowserbaseCdpProvider.reattach later receives that token and passes it to _parse_token to recover the same pieces of information.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Uploads a workspace file into the remote Browserbase session and returns the path that hosted Chrome can use. It also avoids filename clashes when different workspace files share the same base name.

**Data flow**: It receives a workspace path and a read function that returns the file bytes. It takes the file’s base name, reads the bytes, rejects files over the upload limit, chooses a remote name, uploads the bytes through BrowserbaseApi.upload, records the staged name, and returns a Browserbase upload path such as /tmp/.uploads/name.

**Call relations**: The browser engine calls this when a page needs a local file, for example for a file input. It uses PurePosixPath to find the base name and sha256 to add a short path digest if a name collision would otherwise happen, then hands the actual upload to BrowserbaseApi.upload.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name that Browserbase-hosted Chrome accepts. This prevents the system from giving remote Chrome a local absolute path it cannot use.

**Data flow**: It takes no outside data beyond the lease itself. It returns the literal string used by Browserbase for session download storage. Nothing is read from disk and nothing remote changes.

**Call relations**: The browser engine asks the lease where downloads should be directed. This function supplies the Browserbase-specific answer that makes Browserbase store downloads in its session storage.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed browser download from Browserbase storage. It is the lease-level method the rest of the system can call without knowing the Browserbase API details.

**Data flow**: It receives a download GUID. It passes the current session ID and that GUID to BrowserbaseApi.download, then returns the downloaded bytes. Any waiting, size checking, or API errors happen in the API method.

**Call relations**: Higher-level browser download code calls this on the active lease. It delegates directly to BrowserbaseApi.download, keeping the lease as the simple session-aware wrapper.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Closes the Browserbase lease by releasing the remote session, deleting the saved context, and removing the stored context reference. This is the cleanup path that stops billing and removes run-specific browser state.

**Data flow**: It reads the session ID, context ID, conversation ID, API object, and scoped store from the lease. It first tries to release the session, then tries to delete the context, and finally deletes the context entry from the local extension store. It returns nothing, but both remote and local cleanup are attempted even if part of the cleanup fails.

**Call relations**: The system calls this when a browser run ends. It calls BrowserbaseApi.release_session first so the browser is not still using the context, then BrowserbaseApi.delete_context, then the store delete operation to prevent later leases from reusing a bad or stale context ID.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new Browserbase-backed browser lease for the current browser run. It is the main method the core system uses when it wants a fresh hosted Chrome session.

**Data flow**: It receives the current sandbox, which contains the conversation ID that identifies this browser run. It creates a BrowserbaseApi and a scoped store, finds or creates the run’s context, asks Browserbase for a new session, and returns a BrowserbaseLease containing all the information needed to use and later clean up that session.

**Call relations**: The core CDP provider seam calls this when configuration selects Browserbase. It calls BrowserbaseCdpProvider._context to get the saved-state context, constructs BrowserbaseApi and ScopedStore helpers, calls BrowserbaseApi.create_session, and wraps the result in BrowserbaseLease.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase context for the current conversation, or creates one if none exists yet. This gives recovery within the same browser run a way to keep browser state such as logins.

**Data flow**: It receives the API wrapper, scoped store, and conversation ID. It builds a store key from the conversation ID, checks whether a context ID is already stored, and returns it if present. Otherwise, it creates a new Browserbase context, saves the new ID in the store, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It uses ScopedStore.get and ScopedStore.put for the local record, and BrowserbaseApi.create_context when Browserbase needs to mint a new context.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Rebuilds a lease around an existing Browserbase session from a saved token. This lets the system recover a browser run if the session is still alive.

**Data flow**: It receives a token string. It parses the token into conversation ID, session ID, and context ID, creates a BrowserbaseApi, asks Browserbase whether the session is still live and what its connection URL is, then returns a BrowserbaseLease for that existing session. If the token is bad or the session is gone, the flow raises SessionGone.

**Call relations**: Higher-level recovery code calls this when it has a saved lease token. It hands parsing to _parse_token, checks liveness through BrowserbaseApi.live_session, creates a ScopedStore for cleanup later, and returns a BrowserbaseLease if reattachment is safe.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Splits a reconnect token back into the conversation ID, session ID, and context ID it contains. It rejects malformed tokens by treating the session as gone.

**Data flow**: It receives a token string. It separates the token around slashes, checks that the session and context parts exist, converts the conversation part into a UUID, and returns the three values. If the token is incomplete or the UUID is invalid, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this before trying to contact Browserbase. It is the small validation step that prevents bad tokens from being used to create misleading leases.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It declares the Browserbase credential slot and registers Browserbase as a CDP provider option.

**Data flow**: It takes no input. It builds a Manifest containing the extension name, version, required API key credential, and a provider specification that knows how to build BrowserbaseCdpProvider from credentials. It returns that Manifest object.

**Call relations**: The extension loader calls this when discovering available extensions. The returned manifest tells the rest of the system that selecting the Browserbase backend should construct a BrowserbaseCdpProvider, and that the host must supply a Browserbase API key.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease, with Chrome persisting across the conversation sandbox`

This file solves a practical hosting problem: each conversation needs its own browser, and that browser must see the same files and network world as the sandboxed task. Instead of running Chrome somewhere else and copying files back and forth, this provider starts Chrome inside the sandbox itself.

The main job is to create a CDP endpoint. CDP means Chrome DevTools Protocol, the control channel used by automation code to drive Chrome. Chrome exposes this on port 9222, but Chrome rejects some remote requests unless the Host header looks local. To bridge that gap, the file also installs a tiny in-sandbox TCP proxy on port 9223. The proxy rewrites the Host header to look like localhost, then passes the WebSocket traffic through unchanged. Think of it like a receptionist who fixes the envelope address before handing the letter to Chrome.

When a lease is requested, the provider runs a bring-up script in the sandbox. That script checks whether Chrome and the proxy already answer. If not, it kills any stale recorded process, launches fresh ones, waits for real network readiness, and reports useful log output on failure. Chrome and the proxy are meant to live for the whole conversation, so closing a lease does not stop them.

The lease also knows where downloads land and can read a completed download back out of the sandbox, with size limits so a page cannot force the host to pull back an unbounded file.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 307–308)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already prepared browser control endpoint. Callers use this endpoint to connect their browser automation engine to the Chrome running in the sandbox.

**Data flow**: It reads the stored endpoint from the lease object and returns it unchanged. Nothing is contacted, copied, or modified.

**Call relations**: After SandboxChromeCdpProvider.lease has brought up Chrome and built the endpoint, this method is the simple handoff point used by the rest of the browser system when it is ready to connect.


##### `SandboxChromeCdpLease.token`  (lines 310–311)

```
async def token(self) -> str
```

**Purpose**: Returns a durable-looking identifier for this browser connection: the endpoint URL. In this provider, it is mainly a label, because reconnecting from the token alone is not supported.

**Data flow**: It reads the URL inside the stored endpoint and returns that string. It does not check whether the browser is still alive.

**Call relations**: The broader CDP lease interface expects leases to provide a token. This method satisfies that interface, while SandboxChromeCdpProvider.reattach later makes clear that this provider cannot rebuild a session from the token alone.


##### `SandboxChromeCdpLease.place_file`  (lines 313–317)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells the caller that a file is already in the right place for this browser. Because Chrome runs inside the same sandbox, there is no need to upload or copy the file elsewhere.

**Data flow**: It receives a path and a possible file-reading callback. It ignores the reader, because no bytes need to move, and returns the original path as the path Chrome can use.

**Call relations**: Browser-driving code can call this through the common lease interface when it wants to make a file available to the browser. For this sandbox-local browser, the function acts as a shortcut instead of handing data to another machine.


##### `SandboxChromeCdpLease.download_dir`  (lines 319–322)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the folder inside the sandbox where Chrome saves downloads. Callers need this because the browser writes files in the sandbox filesystem, not on the host filesystem.

**Data flow**: It returns the fixed sandbox download path. It does not create the directory here; the bring-up script is responsible for preparing it.

**Call relations**: The browser automation layer uses this when arranging or finding downloads. It matches the Chrome startup configuration prepared by SandboxChromeCdpProvider.lease.


##### `SandboxChromeCdpLease.fetch_download`  (lines 324–352)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a completed browser download out of the sandbox and returns its bytes to the caller. It protects the host by checking the file size before transferring the whole file.

**Data flow**: It receives a download guid, which is the filename Chrome used for the completed download. It quotes that name for safe shell use, asks the sandbox for the file size, rejects files over the configured limit, then asks the sandbox to base64-encode the file. Finally it decodes that base64 text into raw bytes in a worker thread and returns those bytes. If the file cannot be read, is too large, or the read times out, it raises an error.

**Call relations**: Download-handling code calls this after Chrome has saved a file in the sandbox. It uses shlex.quote to avoid treating the guid as shell syntax, and asyncio.to_thread so the CPU work of base64 decoding does not block other async tasks on the event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 354–355)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease without stopping Chrome. This is intentional because Chrome and its proxy are reused across turns in the same conversation sandbox.

**Data flow**: It receives no meaningful input beyond the lease object and returns without changing anything. The browser process, proxy process, and sandbox files are left in place.

**Call relations**: The common lease interface expects a close method. In this provider, cleanup is not tied to each turn; the sandbox lifetime controls when the browser actually disappears.


##### `SandboxChromeCdpProvider.lease`  (lines 367–384)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Starts or reuses the sandbox's Chrome browser and returns a lease that tells the rest of the system how to connect to it. This is the main entry point for using the provider during a turn.

**Data flow**: It receives the current turn's sandbox. If no sandbox is supplied, it raises an error because this provider cannot work without one. It runs the browser bring-up command inside the sandbox, checks whether that command succeeded, and, on failure, asks _bring_up_failure for a clearer message. On success, it asks the sandbox how to dial the proxy port, chooses ws or wss depending on whether that public port uses TLS, combines the public host with the WebSocket path returned by Chrome, copies any required connection headers, and returns a SandboxChromeCdpLease holding the endpoint and sandbox.

**Call relations**: The core browser system calls this when it needs a CDP lease for a turn. This method coordinates the sandbox command runner, the sandbox port dialer, _ws_path for safe URL rewriting, _bring_up_failure for useful errors, CdpEndpoint for the connection description, and SandboxChromeCdpLease for the final lease object.

*Call graph*: calls 4 internal fn (bash, dial, _bring_up_failure, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 386–387)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that an old browser session cannot be reattached from just a saved token. A fresh lease must be made against a live sandbox instead.

**Data flow**: It receives a token string and immediately raises SessionGone with that token. It does not inspect the sandbox or try to reconnect.

**Call relations**: The wider CDP provider interface includes reattach for providers that can recover old sessions. This provider depends on the current live sandbox and its current port mapping, so it tells the caller to treat the saved session as gone.

*Call graph*: 1 external calls (__init__).


##### `_bring_up_failure`  (lines 390–404)

```
async def _bring_up_failure(sandbox: Sandbox, result: ExecResult) -> str
```

**Purpose**: Builds a helpful error message when the in-sandbox browser startup fails. Its main value is preserving Chrome or proxy log output that would otherwise be hidden behind a generic timeout.

**Data flow**: It receives the sandbox and the failed command result. If the command ended normally with an error, it returns the command's stderr or stdout. If the sandbox carrier killed the command because it timed out, it runs a short tail command inside the sandbox to read the last lines of the Chrome and proxy logs, then returns a message that includes the timeout and any log text found.

**Call relations**: SandboxChromeCdpProvider.lease calls this only on bring-up failure. It hands back a human-useful explanation so the raised RuntimeError points toward the browser or proxy problem rather than only saying that the outer command timed out.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 407–412)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the WebSocket path from Chrome's local debugging URL and verifies that the URL really points at local Chrome. This prevents the provider from blindly forwarding an unexpected full URL.

**Data flow**: It receives the WebSocket URL printed by the bring-up script. It trims whitespace, checks that it begins with the expected local Chrome address, removes that local prefix, and returns only the path part. If the URL does not match the expected local form, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease uses this after Chrome reports its webSocketDebuggerUrl. The provider then attaches that safe path to the sandbox carrier's public host, so clients connect through the proxy rather than trying to reach 127.0.0.1 inside the sandbox directly.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 415–424)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system and names the CDP provider backend it supplies. Without this manifest, the system would not know that "sandbox_chrome" is an available browser provider.

**Data flow**: It builds and returns a Manifest containing the extension name, version, and a CdpProviderSpec. That provider spec says that when the backend name is requested, the host should construct a SandboxChromeCdpProvider.

**Call relations**: Extension loading code calls this to discover what the file contributes. The returned manifest connects configuration such as cdp_provider = "sandbox_chrome" to the provider class that can actually create browser leases.

*Call graph*: 2 external calls (__init__, __init__).


### Hosted browser automation
Connects the system to Browser Use cloud automation tools for delegated browsing tasks.

### `extensions/browser_use/ufo_ext_browser_use.py`

`io_transport` · `request handling`

This extension is a swap-in replacement for the project’s own browser stack. Instead of controlling a local browser step by step, it sends a task to Browser Use’s hosted API and waits for that service to finish the job. In plain terms, it is like hiring an outside web assistant: this file writes the work order, checks whether the assistant is done, collects the answer, and brings back any output files safely.

The file defines a `HostedRun`, which represents one cloud browser job from start to finish. It creates the run, polls for its status, cancels it if the time limit expires, reads the final result, and optionally downloads files produced by the run. It is careful about safety: the API key is read only on the host side, downloaded files must use HTTPS, oversized files are skipped, and file paths are checked so a vendor-provided path cannot write outside the workspace.

The file also defines the two public tools. `browser_task` runs one richer browsing session and saves output files. `wide_browse` reads many URLs or site names from a workspace file, runs many cheaper browser jobs in parallel, and writes a combined JSON report. Without this file, deployments that choose Browser Use would have no way to expose those browser tools to agents or evaluations.

#### Function details

##### `HostedRun.execute`  (lines 137–172)

```
async def execute(self, ctx: ToolContext, task: str, *, timeout_seconds: float, dedup_key: str | None=None) -> RunOutcome
```

**Purpose**: Runs one Browser Use job all the way through. It validates the task, opens an authenticated connection to the hosted API, starts or resumes the run, waits for completion, handles timeouts, and returns a clear outcome.

**Data flow**: It receives the tool context, task text, a timeout, and optionally a deduplication key used to avoid paying for the same run twice. It reads the Browser Use API key from scoped credentials, sends or resumes the task, watches the remote status, cancels if the local deadline passes, then reads the final summary and downloads allowed output files. It returns a `RunOutcome` containing the final status, result or error text, saved files, skipped files, and whether more files existed.

**Call relations**: This is the main driver inside `HostedRun`. Tool handlers create a `HostedRun` and call this method when they need hosted browser work. During the run it hands off to `_start` to create or reattach, `_watch` and `_status` to track progress, `_json` to safely read API responses, and `_collect` to bring files back into the workspace.

*Call graph*: calls 5 internal fn (_collect, _json, _start, _status, _watch); 3 external calls (__init__, timeout, AsyncClient).


##### `HostedRun._start`  (lines 174–201)

```
async def _start(self, http: httpx.AsyncClient, store: ScopedStore, task: str, dedup_key: str | None) -> StartedRun
```

**Purpose**: Starts a Browser Use run, or reuses an already-recorded run when a deduplication key points to one. This avoids accidentally starting duplicate paid jobs after a retry.

**Data flow**: It receives an HTTP client, extension store, task text, and optional deduplication key. If the key already has a stored run handle, it validates and returns that. Otherwise it posts the task, model, cost limit, and proxy country to the Browser Use API, extracts the new run ID and workspace ID, stores them under the key if needed, and returns a `StartedRun`.

**Call relations**: `HostedRun.execute` calls this at the beginning of a job. `_start` uses `_json` to check the API response and `_text` to pull out required string fields, then gives `execute` the run handle it needs for watching, cancellation, and file collection.

*Call graph*: calls 4 internal fn (get, put, _json, _text); called by 1 (execute); 2 external calls (__init__, post).


##### `HostedRun._watch`  (lines 203–208)

```
async def _watch(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Waits until a remote Browser Use run reaches a final state such as completed, failed, stopped, or cancelled. It is the polling loop for the hosted job.

**Data flow**: It receives an HTTP client and run ID. It repeatedly asks `_status` for the current state, pauses briefly when the run is still active, and stops once the status is terminal. It returns the final status string.

**Call relations**: `HostedRun.execute` calls this after a run has started. `_watch` relies on `_status` for each single status check and returns control to `execute` once there is a final result to inspect.

*Call graph*: calls 1 internal fn (_status); called by 1 (execute); 1 external calls (sleep).


##### `HostedRun._status`  (lines 210–214)

```
async def _status(self, http: httpx.AsyncClient, run_id: str) -> str
```

**Purpose**: Reads the current status of one Browser Use run one time. It is used both during normal polling and just before cancelling on timeout, so a run that finished at the last moment is not cancelled unnecessarily.

**Data flow**: It receives an HTTP client and run ID. It sends a GET request to the run’s status endpoint, parses the response as checked JSON, extracts the required `status` field, and returns it as text.

**Call relations**: `_watch` calls this over and over while waiting. `HostedRun.execute` also calls it in the timeout path before deciding whether to cancel the run.

*Call graph*: calls 2 internal fn (_json, _text); called by 2 (_watch, execute); 1 external calls (get).


##### `HostedRun._collect`  (lines 216–260)

```
async def _collect(self, http: httpx.AsyncClient, ctx: ToolContext, workspace_id: str) -> tuple[tuple[RunFile, ...], tuple[RunFile, ...], bool]
```

**Purpose**: Copies a completed run’s output files from Browser Use into the project workspace, while enforcing limits and path safety. It records what was saved and what was skipped.

**Data flow**: It receives an HTTP client, tool context, and Browser Use workspace ID. If file saving is disabled, it returns empty results. Otherwise it asks the API for a limited file list, checks that each entry has a path and size, rejects paths that would escape the workspace, skips files that are too large or lack a usable download URL, downloads acceptable files, and writes them into the sandbox workspace. It returns saved files, skipped files, and a flag saying whether more files existed than were listed.

**Call relations**: `HostedRun.execute` calls this after reading the run summary. `_collect` uses `_json` to validate the file listing, `contained_relative` to keep writes inside the workspace, and `_download` to fetch each allowed file.

*Call graph*: calls 2 internal fn (_download, _json); called by 1 (execute); 4 external calls (__init__, __init__, get, contained_relative).


##### `HostedRun._download`  (lines 262–278)

```
async def _download(self, url: str) -> bytes
```

**Purpose**: Downloads one output file from a presigned URL without sending the Browser Use API key to that storage host. It also blocks non-HTTPS URLs.

**Data flow**: It receives a download URL. It first checks that the URL starts with `https://`, then creates a separate unauthenticated HTTP client, downloads the bytes, and raises a `BrowserUseError` if the storage server reports an error. On success, it returns the file bytes.

**Call relations**: `_collect` calls this for each output file that is small enough, has a URL, and has passed the workspace path check. The returned bytes are then written into the sandbox.

*Call graph*: called by 1 (_collect); 2 external calls (__init__, AsyncClient).


##### `HostedRun._json`  (lines 281–296)

```
async def _json(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a Browser Use HTTP response into a safe Python dictionary, with useful errors when the response cannot be trusted or read. This keeps API failures from silently becoming empty results.

**Data flow**: It receives an HTTP response. If the status code indicates failure, it raises a `BrowserUseError` with the endpoint and body. Otherwise it tries to parse JSON, checks that the JSON is an object, converts keys to strings, and returns the dictionary.

**Call relations**: This is the shared response checker used by `execute`, `_start`, `_status`, and `_collect`. Those higher-level methods depend on it before extracting fields or deciding what to do next.

*Call graph*: called by 4 (_collect, _start, _status, execute); 2 external calls (__init__, json).


##### `HostedRun._text`  (lines 299–305)

```
def _text(body: dict[str, object], key: str) -> str
```

**Purpose**: Pulls one required text field from an API response body and reports a clear Browser Use error if it is missing or not text.

**Data flow**: It receives a response dictionary and a field name. It looks up that field, verifies that the value is a string, and returns it. If the value is absent or a different type, it raises `BrowserUseError` with the full body for debugging.

**Call relations**: `_start` uses this to read newly created run IDs and workspace IDs. `_status` uses it to read the current run status after `_json` has validated the response shape.

*Call graph*: called by 2 (_start, _status); 1 external calls (__init__).


##### `_browser_task`  (lines 344–378)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Implements the public `browser_task` tool. It sends one self-contained browser job to Browser Use, waits for the result, and returns the answer plus any saved output files.

**Data flow**: It receives the tool context and structured user arguments: starting URL, task instructions, friendly task name, and timeout. It builds a full task prompt, creates a `HostedRun` configured for the more capable model and a higher cost limit, executes it, then packages the result into a JSON string. If the run timed out, it returns an error-style tool result explaining that the job was cancelled.

**Call relations**: The tool registry points the `browser_task` tool name at this handler. When invoked, it creates a `HostedRun`, which performs the actual hosted API flow, then wraps the outcome as `TextContent` inside a `ToolResult` for the caller.

*Call graph*: 4 external calls (__init__, __init__, __init__, dumps).


##### `_read_file`  (lines 381–388)

```
async def _read_file(ctx: ToolContext, path: str) -> str
```

**Purpose**: Reads a workspace file through the sandbox shell, using safe shell quoting for the path. It exists so model-supplied paths cannot accidentally become shell commands.

**Data flow**: It receives a tool context and file path. It quotes the path for the shell, runs `cat` inside the sandbox, checks the exit code, and returns the file’s text. If the command fails, it raises a clear `ValueError` using the shell error message when available.

**Call relations**: `_read_lines` calls this to load the list of browse targets. `_wide_browse` also calls it directly to read the JSON schema file that will be included in each browser task.

*Call graph*: called by 2 (_read_lines, _wide_browse); 1 external calls (quote).


##### `_read_lines`  (lines 391–399)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a text file as a clean list of unique, non-empty lines. It is used to turn a user-provided file of URLs or site names into browse targets.

**Data flow**: It receives the tool context and a file path. It reads the file through `_read_file`, splits it into lines, trims whitespace, skips blank lines, removes duplicates while preserving first-seen order, and returns the resulting list.

**Call relations**: `_wide_browse` calls this at the start of a batch job to decide which entities should each get their own hosted browser run.

*Call graph*: calls 1 internal fn (_read_file); called by 1 (_wide_browse).


##### `_wide_browse`  (lines 402–448)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Implements the public `wide_browse` tool. It runs browser automation across many URLs or site names in parallel and writes one combined JSON report.

**Data flow**: It receives the tool context and arguments naming an entities file, a prompt template, and an output schema file. It reads and deduplicates entities, checks the maximum count, reads the schema text, creates a concurrency limit, and builds a cheaper `HostedRun` runner that does not save per-run files. It launches one visit per entity, converts individual failures into row-level error records, writes all rows to `wide_browse.json` in the sandbox, and returns the rows plus the output filename.

**Call relations**: The tool registry points the `wide_browse` tool name at this handler. It uses `_read_lines` and `_read_file` for workspace inputs, creates a `HostedRun` for Browser Use calls, and relies on its nested `visit` function to run each entity’s browser job under the parallelism limit.

*Call graph*: calls 2 internal fn (_read_file, _read_lines); 6 external calls (__init__, __init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 418–433)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the hosted browser task for one entity inside a `wide_browse` batch. Its job is to make each row independent, so one bad website or vendor hiccup does not erase the whole batch.

**Data flow**: It receives one entity string from the outer `wide_browse` function. It waits for a semaphore slot, fills the prompt template by replacing `{entity}`, appends the output schema if provided, executes the shared `HostedRun` with a per-entity deduplication key, and returns a row containing the entity, status, and result text.

**Call relations**: `_wide_browse` creates this nested helper and passes one call per entity into `asyncio.gather`. The outer function later collects these returned rows, while exceptions from ordinary failures become error rows instead of stopping sibling runs.


##### `manifest`  (lines 471–486)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It tells the system the extension name and version, which tools it provides, what prompt text to add, and which credential it needs.

**Data flow**: It takes no input. It builds a `Manifest` containing the two Browser Use tool definitions, a browser prompt section loaded from the nearby markdown file, and a credential slot for the Browser Use API key. It returns that manifest to the extension loader.

**Call relations**: The host calls this during extension loading. The returned manifest is what lets the tool registry expose `browser_task` and `wide_browse`, and what tells credential setup that this extension needs a Browser Use API key.

*Call graph*: 3 external calls (__init__, __init__, __init__).

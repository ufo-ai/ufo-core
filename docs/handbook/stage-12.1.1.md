# Browser endpoint providers and package shells  `stage-12.1.1`

This stage is behind-the-scenes support for browser automation. Its job is to give the rest of the system a usable Chrome connection without making the core code care where Chrome is running. The shared contract lives in core/src/ufo/browser.py. It defines the idea of a browser endpoint: an address for Chrome’s DevTools Protocol, which is Chrome’s remote-control socket for inspecting pages and sending browser commands.

Two providers can satisfy that contract. extensions/browserbase/ufo_ext_browserbase.py connects to Browserbase, a hosted browser service. It reads a saved connection URL and hands that URL to the browser engine. extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py does the local sandbox version. For each conversation, it starts or reuses a Chrome running inside the sandbox, exposes its debugging address in a controlled way, and returns that address.

The remaining files are package shells. extensions/browser/ufo_ext_browser/__init__.py advertises the browser extension package and its tools. extensions/browser/ufo_ext_browser/bua/__init__.py simply makes its folder importable.

## Files in this stage

### Chrome endpoint providers
Provider modules expose Chrome DevTools Protocol endpoints from Browserbase or a sandboxed Chrome while relying on the shared browser endpoint contract.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `startup registration, then browser connection setup during each turn`

This extension is the bridge between UFO and Browserbase, a service that runs browsers remotely. The system needs a Chrome DevTools Protocol endpoint, often shortened to CDP, which is a WebSocket address used to control Chrome from code. Without this file, choosing the Browserbase browser provider would not work, because the core system does not include Browserbase-specific connection logic.

The important idea is that the browser is not created fresh inside the turn's sandbox. Instead, there is one fixed Browserbase connection URL stored as a host-side credential. “Host-side” means it is read by the trusted host process, not copied into the sandbox where task code runs. That matters because the URL includes the user’s Browserbase API key.

The file defines a small lease object, `StaticLease`, that simply points at this fixed remote browser endpoint. A lease is like borrowing a key to a room, except here the room already exists and the key is just the connection URL. Closing the lease does not shut down the Browserbase session.

`BrowserbaseCdpProvider` reads the current URL from the credential slot each time a lease is requested. That means if the URL is rotated, the next lease can pick up the new value. The `manifest` function advertises this extension to the larger system: it declares the needed credential slot and registers Browserbase as a CDP provider.

#### Function details

##### `StaticLease.endpoint`  (lines 33–34)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the Browserbase CDP endpoint that the lease points to. Someone would use it when they need the actual browser connection address.

**Data flow**: It starts with a `StaticLease` that already contains an endpoint. It reads that stored endpoint and returns it unchanged. Nothing else is modified.

**Call relations**: This is part of the lease object created by `BrowserbaseCdpProvider.lease` or `BrowserbaseCdpProvider.reattach`. After a caller receives the lease, it calls `endpoint` to learn where to connect to the remote Browserbase browser.


##### `StaticLease.token`  (lines 36–37)

```
async def token(self) -> str
```

**Purpose**: This returns a reattach token for the lease. In this provider, the token is simply the Browserbase connection URL itself, because the same fixed remote endpoint is reused.

**Data flow**: It starts with the stored endpoint inside the lease. It takes the endpoint’s URL and returns that URL as a string. It does not create a new session or change anything.

**Call relations**: This belongs to the lease returned by the Browserbase provider. If the wider system wants a handle it can later use to reconnect, this function supplies one, although this provider does not really depend on the passed token when reattaching.


##### `StaticLease.aclose`  (lines 39–40)

```
async def aclose(self) -> None
```

**Purpose**: This is the close operation for the lease, but here it deliberately does nothing. The Browserbase-hosted browser session lives outside the turn, so ending the lease should not shut it down.

**Data flow**: It receives no extra information beyond the lease object. It performs no cleanup and returns nothing. The remote Browserbase session remains available.

**Call relations**: The wider system can call this when it is done with a lease, just as it would for other browser providers. For Browserbase, this keeps the common lease interface intact while avoiding any attempt to close the external hosted browser.


##### `BrowserbaseCdpProvider._endpoint`  (lines 51–52)

```
async def _endpoint(self) -> CdpEndpoint
```

**Purpose**: This privately builds the CDP endpoint object from the current Browserbase connection URL stored in credentials. It exists so both new leases and reattachments use the same fresh credential-reading step.

**Data flow**: It asks the credential access object for the value named `browserbase_cdp_url`. It then wraps that URL in a `CdpEndpoint`, which is the system’s standard object for “here is where Chrome can be controlled.” The result is returned to the caller.

**Call relations**: `BrowserbaseCdpProvider.lease` and `BrowserbaseCdpProvider.reattach` both call this before creating a `StaticLease`. This shared path is important because it means a rotated Browserbase URL can be picked up the next time either operation happens.

*Call graph*: called by 2 (lease, reattach); 1 external calls (__init__).


##### `BrowserbaseCdpProvider.lease`  (lines 54–55)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This gives the system a lease for the Browserbase remote browser. It ignores the sandbox argument because Browserbase runs outside the sandbox.

**Data flow**: It may receive a sandbox session, but it does not use it. Instead, it calls `_endpoint` to read the current Browserbase URL from credentials, wraps that endpoint in a `StaticLease`, and returns the lease.

**Call relations**: The larger browser setup flow calls this when it needs a CDP lease for a turn. This function hands off endpoint creation to `_endpoint`, then packages the result as a `StaticLease` so the rest of the system can treat it like any other browser lease.

*Call graph*: calls 1 internal fn (_endpoint); 1 external calls (__init__).


##### `BrowserbaseCdpProvider.reattach`  (lines 57–58)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This reconnects to the Browserbase browser after the system has a previous lease token. In this implementation, it reads the current credential again instead of trusting the old token.

**Data flow**: It receives a token string, but does not use it to choose the endpoint. It calls `_endpoint` to fetch the current Browserbase connection URL, creates a new `StaticLease` for that endpoint, and returns it.

**Call relations**: The wider system calls this when it wants to resume or reconnect. Like `lease`, it delegates the actual endpoint lookup to `_endpoint`, which keeps reattachment aligned with the latest credential value.

*Call graph*: calls 1 internal fn (_endpoint); 1 external calls (__init__).


##### `manifest`  (lines 61–77)

```
def manifest() -> Manifest
```

**Purpose**: This describes the Browserbase extension to the host system. It says what the extension is called, what credential it needs, and how to build the Browserbase CDP provider.

**Data flow**: It creates a credential slot named `browserbase_cdp_url`, including a description that warns the URL contains an API key. It creates a CDP provider specification for the `browserbase` backend, with a builder that turns credential access into a `BrowserbaseCdpProvider`. It returns all of that inside a `Manifest` object.

**Call relations**: The extension loading system calls this during registration. The returned manifest lets the host know that selecting the `browserbase` CDP backend should create a `BrowserbaseCdpProvider`, and that the deployment must provide the Browserbase connection URL credential.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease setup`

This file is a CDP provider. CDP means Chrome DevTools Protocol: the control channel that tools use to drive Chrome, open pages, click, inspect, and so on. In this setup, every conversation has its own sandbox, like a private workroom. This provider makes sure there is a headless Chrome running inside that workroom, then returns a reachable WebSocket address for controlling it.

The main problem it solves is that Chrome’s debugging port is local to the sandbox, but the main server must reach it from outside. The file starts Chrome on port 9222 inside the sandbox. It also starts a small proxy on port 9223. That proxy rewrites the HTTP Host header to look like localhost, because Chrome rejects some DevTools requests unless they appear local. This proxy is important: without it, the WebSocket upgrade used by CDP may fail.

A lease represents one turn’s access to the browser. Closing the lease does not shut Chrome down, because Chrome and the proxy live for the whole sandbox conversation and can be reused on later turns. If the system tries to reattach using only an old token, this provider refuses and says the session is gone, because the real endpoint must be rebuilt from the live sandbox.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 194–195)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the already-prepared browser control endpoint for the current lease. The endpoint is the address and headers the browser engine needs to connect to Chrome.

**Data flow**: It reads the stored endpoint from the lease object and gives it back unchanged. Nothing else is changed.

**Call relations**: After SandboxChromeCdpProvider.lease has started or reused Chrome and built the endpoint, callers use this method to retrieve that endpoint and connect the browser engine to it.


##### `SandboxChromeCdpLease.token`  (lines 197–198)

```
async def token(self) -> str
```

**Purpose**: This returns a simple text token for the lease. In this provider, the token is just the endpoint URL.

**Data flow**: It reads the URL inside the stored endpoint and returns that string. It does not contact the sandbox or change any state.

**Call relations**: Callers can ask a lease for a token when they want something durable to remember. However, this provider does not later reattach from that token alone; SandboxChromeCdpProvider.reattach reports that a fresh sandbox-based lease is needed.


##### `SandboxChromeCdpLease.aclose`  (lines 200–201)

```
async def aclose(self) -> None
```

**Purpose**: This closes the lease from the caller’s point of view, but deliberately does not stop Chrome. Chrome and its proxy are meant to keep running inside the conversation sandbox for reuse.

**Data flow**: It receives no extra data and returns without doing anything. No browser process, proxy process, or endpoint is changed.

**Call relations**: When a turn finishes, code may call this as normal cleanup. Here the cleanup is a no-op because SandboxChromeCdpProvider.lease is designed to reuse the same sandbox browser across turns.


##### `SandboxChromeCdpProvider.lease`  (lines 212–223)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This creates a usable browser-control lease for a turn. It makes sure Chrome and the proxy are running inside the provided sandbox, then returns an endpoint the browser engine can connect to from outside the sandbox.

**Data flow**: It takes a sandbox session as input. If no sandbox is provided, it raises an error because this provider cannot work without one. It runs commands in the sandbox to start Chrome and the proxy, asks Chrome for its local WebSocket debugger URL, trims that down to the path part, asks the sandbox for the public host of the proxy port, adds the sandbox traffic token as a connection header when present, and returns a SandboxChromeCdpLease containing the final endpoint.

**Call relations**: This is the main entry point used when the system needs browser access for a turn. It relies on _run to execute sandbox commands, _ws_path to validate and extract the Chrome WebSocket path, SandboxSession.host to find the outside address for the proxy, and _remote_ws_url to turn that address into a WebSocket URL.

*Call graph*: calls 4 internal fn (host, _remote_ws_url, _run, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 225–226)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This rejects attempts to rebuild a browser lease from an old token alone. For this provider, the live sandbox is required to discover the current reachable browser endpoint.

**Data flow**: It receives a token string, wraps it in a SessionGone error, and raises that error. It does not return a lease and does not contact Chrome or the sandbox.

**Call relations**: If higher-level recovery code tries to reattach, this method tells it that the old session cannot be restored from the token. The expected path is to call SandboxChromeCdpProvider.lease again with the current sandbox.

*Call graph*: 1 external calls (__init__).


##### `_run`  (lines 229–233)

```
async def _run(sandbox: SandboxSession, command: str, what: str) -> str
```

**Purpose**: This is a small helper for running a shell command inside the sandbox and turning failures into clear Python errors. It keeps the rest of the provider from repeating the same command-checking code.

**Data flow**: It receives a sandbox, a shell command, and a short description of what the command is trying to do. It asks the sandbox to run the command with a timeout. If the command succeeds, it returns the command’s standard output. If the command fails, it raises an error that includes the command’s error output when available.

**Call relations**: SandboxChromeCdpProvider.lease calls this for each sandbox step: starting Chrome, starting the proxy, and reading Chrome’s debugger URL. _run hands the actual execution off to SandboxSession.bash.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 236–241)

```
def _ws_path(url: str) -> str
```

**Purpose**: This checks that Chrome reported a local debugger WebSocket URL and extracts only the path part. That path is later attached to the sandbox’s public proxy host.

**Data flow**: It receives a WebSocket URL string from Chrome. It trims whitespace, verifies that the URL starts with the expected local Chrome address, removes that local prefix, and returns the remaining path. If the URL is not local, it raises an error instead of trusting it.

**Call relations**: SandboxChromeCdpProvider.lease uses this after _run reads Chrome’s /json/version response. The resulting path is passed into _remote_ws_url so the outside-facing endpoint points at the proxy while preserving Chrome’s debugger path.

*Call graph*: called by 1 (lease).


##### `_remote_ws_url`  (lines 244–250)

```
def _remote_ws_url(host: str, path: str) -> str
```

**Purpose**: This turns the sandbox proxy’s public HTTP-style host into the WebSocket URL needed for CDP. WebSocket is the long-lived connection type used for Chrome control messages.

**Data flow**: It receives a host and a WebSocket path. It first normalizes the host through _remote_url. If the normalized host is HTTPS, it produces a secure WebSocket URL starting with wss://. If it is HTTP, it produces ws://. If the host has an unsupported form, it raises an error.

**Call relations**: SandboxChromeCdpProvider.lease calls this when building the final CdpEndpoint. It depends on _remote_url to decide whether the host should be treated as local HTTP or remote HTTPS.

*Call graph*: calls 1 internal fn (_remote_url); called by 1 (lease).


##### `_remote_url`  (lines 253–257)

```
def _remote_url(host: str) -> str
```

**Purpose**: This normalizes a host string by making sure it has an http:// or https:// prefix. It chooses plain HTTP for local addresses and HTTPS for other public hosts.

**Data flow**: It receives a host string. If the string already starts with http:// or https://, it returns it unchanged. If it looks like localhost or 127.0.0.1, it adds http://. Otherwise, it adds https://.

**Call relations**: _remote_ws_url calls this before converting an HTTP-style URL into a WebSocket-style URL. This keeps the endpoint-building logic consistent whether the sandbox host already includes a scheme or not.

*Call graph*: called by 1 (_remote_ws_url).


##### `manifest`  (lines 260–269)

```
def manifest() -> Manifest
```

**Purpose**: This tells the larger extension system that this file provides a CDP backend named sandbox_chrome. It is the registration hook that makes the provider selectable by configuration.

**Data flow**: It creates and returns a Manifest object containing this extension’s name, version, and one CDP provider specification. That specification says that when the sandbox_chrome backend is requested, the system should build a SandboxChromeCdpProvider.

**Call relations**: The extension-loading code calls this to discover what the file offers. The returned Manifest includes a CdpProviderSpec whose build function creates the provider used later for lease and reattach operations.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/browser.py`

`data_model` · `cross-cutting; used when a turn needs a browser connection and when a prior browser session is restored`

This file is a boundary line between the core system and whatever browser provider an extension chooses to use. The Chrome DevTools Protocol, or CDP, is the control channel that automation tools use to talk to Chrome. Core does not start Chrome or drive pages itself. Instead, it asks a provider for a temporary lease on a CDP endpoint, a bit like borrowing a key to a room for one visit.

The main data shape is `CdpEndpoint`, which contains the URL to connect to and any connection headers, such as an authorization token. A `CdpLease` represents the right to use that endpoint during one turn. It can reveal the endpoint, provide a saved token for reconnecting later, and close the lease when the turn ends.

`CdpProvider` is the larger contract. A provider can create a new lease, possibly using a `SandboxSession` when Chrome lives inside a sandbox. It can also reattach to an earlier browser session using a saved token. If that old session has disappeared, `SessionGone` tells the caller to start fresh instead of pretending it can continue from a vanished page.

The important design choice is separation: core knows the shape of the browser connection, but extensions decide where the browser comes from and how it is actually controlled.

#### Function details

##### `CdpLease.endpoint`  (lines 45–45)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This method gives the caller the actual CDP connection details for the current lease. A browser-driving extension uses it to learn what URL to connect to and which headers to send.

**Data flow**: It starts with an existing lease object. The implementation looks up or prepares the endpoint tied to that lease, then returns a `CdpEndpoint` containing the connection URL and optional headers. If the lease cannot provide an endpoint, an implementation may raise an error instead of returning unusable connection data.

**Call relations**: This is part of the `CdpLease` protocol, so the file only defines what providers must offer. In the bigger flow, a `CdpProvider` first creates or restores a lease, then the browser extension asks this method for the connection details before it starts driving Chrome.


##### `CdpLease.token`  (lines 47–47)

```
async def token(self) -> str
```

**Purpose**: This method returns a durable text token that can be saved and used later to reconnect to the same browser session if it still exists.

**Data flow**: It starts with the active lease. The implementation turns the lease’s browser session identity into a plain string, such as a hosted session ID or a static endpoint marker, and returns that string to the caller for persistence.

**Call relations**: This method supports recovery and continuation. After a provider creates a lease, the browser extension can save this token; on a later run, the system gives the token back to `CdpProvider.reattach` to try to recover the same browser session.


##### `CdpLease.aclose`  (lines 49–49)

```
async def aclose(self) -> None
```

**Purpose**: This method releases whatever temporary hold the lease represents when the turn is finished. For some providers this may do nothing; for others it may tell a remote browser service that the session can be released.

**Data flow**: It starts with a lease that has been used during a turn. The implementation performs any needed cleanup, such as releasing a hosted session, and returns nothing once that cleanup is complete.

**Call relations**: This is the end-of-use counterpart to `CdpProvider.lease` or `CdpProvider.reattach`. The surrounding turn logic is expected to call it when browser access is no longer needed, so provider-specific resources are not left hanging.


##### `CdpProvider.lease`  (lines 62–62)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This method creates a fresh per-turn lease for a browser endpoint. It is how core asks, “give me a Chrome connection for this turn,” without caring where that Chrome lives.

**Data flow**: It receives an optional `SandboxSession`, which represents the sandbox environment for the current conversation or task. The provider may use that sandbox to find Chrome inside it, or ignore it if the browser is static or remote. It returns a `CdpLease`, which can then reveal the endpoint, provide a reconnect token, and be closed later.

**Call relations**: This is the normal starting point for browser access. A provider selected at startup implements it; turn-level orchestration calls it when a new browser connection is needed, and the returned lease is then used through `CdpLease.endpoint`, `CdpLease.token`, and finally `CdpLease.aclose`.


##### `CdpProvider.reattach`  (lines 64–64)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This method tries to reconnect to a browser session that was saved earlier. It lets a recovered or resumed turn continue with the same browser session when that session is still alive.

**Data flow**: It receives a saved token string from an earlier lease. The provider interprets that token, checks whether the named browser session or endpoint still exists, and returns a new `CdpLease` over it. If the session is gone, it raises `SessionGone` so the caller knows to create a new lease instead.

**Call relations**: This method is the recovery path paired with `CdpLease.token`. A previous run saves the token; a later run passes it here. If reattachment succeeds, the returned lease behaves like any other lease. If it fails with `SessionGone`, the caller falls back to `CdpProvider.lease` and starts with a fresh browser connection.


### Browser package shells
Package marker files expose the browser extension namespace and its browser-use automation subpackage without adding runtime behavior.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `cross-cutting`

This is the package entrance marker for the browser extension module. In Python, an `__init__.py` file tells the language that a folder should be treated as an importable package, much like putting a label on a drawer so other parts of the program can find what is inside. Here, the file does not run setup code or define any functions. Its only content is a short documentation string explaining the purpose of the package.

The package described here appears to group together browser-related tools. These are likely used when the system needs to interact with a sandboxed browser or perform “computer-use” actions, meaning actions that imitate a user operating a browser or desktop-like environment. It also mentions a browser subagent profile, which suggests a preset role or behavior pattern for an assistant specialized in browser tasks.

Without this file, depending on the Python version and packaging setup, imports from this folder could be less explicit or fail in some environments. More importantly for readers, the file provides a clear signpost: this directory is about browser automation tools and the agent profile that uses them.


### `extensions/browser/ufo_ext_browser/bua/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `extensions/browser/ufo_ext_browser/bua` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label simply tells Python that the drawer belongs in the organized cabinet of code. Because the file is empty, it does not run setup code, create shared objects, or change how the package behaves. Its value is structural: without it, some Python tooling or older import rules might not recognize the directory as a package, which could make imports fail or behave inconsistently.

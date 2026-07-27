# Browser tool entrypoints and endpoint providers  `stage-10.2.1`

This stage is the doorway between the agent and a real Chrome browser. It is used during the main work loop whenever the agent needs to look at a web page or act on it. The core browser file defines a simple promise: for one turn of work, provide a Chrome DevTools Protocol endpoint. That endpoint is the control address Chrome exposes so other software can inspect pages, click, type, and read results.

Different provider files fulfill that promise in different environments. The Browserbase adapter points the system at a hosted browser using a fixed remote connection URL, so the project does not need to run Chrome locally. The sandbox Chrome adapter starts or reuses Chrome inside the same sandbox as the conversation, then safely shares its WebSocket control address with the browser engine.

On top of those connection pieces, the browser tools file exposes practical actions to the agent: open a page, read content, fill forms, click, take screenshots, and save downloads. Together, these files separate “how to reach Chrome” from “what the agent can do with Chrome.”

## Files in this stage

### Remote endpoint providers
Adapters that obtain Chrome DevTools endpoints from hosted Browserbase or sandbox-managed Chrome instances.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `browser setup and reattach`

This extension is the bridge between UFO and Browserbase, a hosted browser service. The system needs a Chrome DevTools Protocol connection, often called CDP, which is the control channel used to drive Chrome from code. Instead of creating a browser inside the sandbox for each turn, this file points UFO at one remote Browserbase session using a stored WebSocket URL.

The important safety detail is where that URL lives. The URL contains the user’s Browserbase API key, so it is treated as a host-side credential. The sandbox never receives it. Each time the system asks for a browser lease, the provider reads the latest value from the credential slot named `browserbase_cdp_url`. That means rotating the URL can take effect on the next lease.

There are two main pieces. `BrowserbaseCdpProvider` knows how to fetch the URL and turn it into a `CdpEndpoint`, which is simply the address the browser driver should connect to. `StaticLease` wraps that endpoint in the lease shape the rest of the system expects. The lease is “static” because closing it does not shut down Browserbase, and reattaching just reconnects to the same kind of remote endpoint. The `manifest` function advertises this extension to the host: it declares the needed credential and registers the CDP provider under the Browserbase backend name.

#### Function details

##### `StaticLease.endpoint`  (lines 33–34)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the browser connection address stored inside the lease. The rest of the system uses it when it needs to connect to the hosted Chrome session.

**Data flow**: It starts with a `StaticLease` that already contains a `CdpEndpoint`. It reads that saved endpoint and returns it unchanged. Nothing else is modified.

**Call relations**: This is part of the lease object created by `BrowserbaseCdpProvider.lease` or `BrowserbaseCdpProvider.reattach`. After one of those provider methods hands back a lease, browser-driving code can ask this method where to connect.


##### `StaticLease.token`  (lines 36–37)

```
async def token(self) -> str
```

**Purpose**: This returns a simple reattach token for the lease. In this provider, the token is just the Browserbase connection URL itself.

**Data flow**: It starts with the stored endpoint, reads its `url` field, and returns that string. It does not create a new connection or change the lease.

**Call relations**: This supports the common lease interface used by the wider browser system. If something wants a handle it can store for later reconnection, this method supplies the URL as that handle.


##### `StaticLease.aclose`  (lines 39–40)

```
async def aclose(self) -> None
```

**Purpose**: This closes the lease from UFO’s point of view, but it deliberately does not close the Browserbase-hosted browser session. The remote session is assumed to outlive a single turn.

**Data flow**: It receives the lease and does no cleanup work. It returns `None`, leaving the remote Browserbase endpoint untouched.

**Call relations**: This method exists because all browser leases are expected to have a close step. For this static remote provider, that step is a no-op, unlike a local browser provider that might stop a process or release sandbox resources.


##### `BrowserbaseCdpProvider._endpoint`  (lines 51–52)

```
async def _endpoint(self) -> CdpEndpoint
```

**Purpose**: This builds the browser connection endpoint by reading the Browserbase CDP URL from the host-side credential store. It is the one place where the stored URL is turned into the endpoint object used by the browser system.

**Data flow**: It starts with a provider that has access to credentials. It asks for the `browserbase_cdp_url` value, then wraps that URL in a `CdpEndpoint`. The result is a connection address ready to be placed in a lease.

**Call relations**: Both `BrowserbaseCdpProvider.lease` and `BrowserbaseCdpProvider.reattach` call this helper before creating a `StaticLease`. Keeping this lookup here means each new lease or reattach attempt reads the current credential value.

*Call graph*: called by 2 (lease, reattach); 1 external calls (__init__).


##### `BrowserbaseCdpProvider.lease`  (lines 54–55)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This creates a new lease for the Browserbase browser endpoint. Even though it accepts an optional sandbox, it ignores it because the browser is remote, not running inside the sandbox.

**Data flow**: It receives an optional sandbox session but does not use it. It asks `_endpoint` for the current Browserbase connection address, wraps that address in a `StaticLease`, and returns the lease to the caller.

**Call relations**: The wider browser setup flow calls this when it needs a CDP lease for a turn. This method delegates URL lookup to `_endpoint`, then hands back a `StaticLease` so the rest of the system can interact with Browserbase through the normal lease interface.

*Call graph*: calls 1 internal fn (_endpoint); 1 external calls (__init__).


##### `BrowserbaseCdpProvider.reattach`  (lines 57–58)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This recreates a lease for an existing Browserbase-style browser connection. Although it receives a token, it reads the current configured URL again instead of trusting the token.

**Data flow**: It receives a token string, but does not use that string to build the endpoint. It fetches the current Browserbase URL through `_endpoint`, wraps it in a `StaticLease`, and returns the new lease wrapper.

**Call relations**: The broader browser system calls this when it wants to reconnect using a previous lease handle. In this provider, reattach follows the same path as a fresh lease: it calls `_endpoint` and returns a `StaticLease`, allowing rotated credentials to take effect.

*Call graph*: calls 1 internal fn (_endpoint); 1 external calls (__init__).


##### `manifest`  (lines 61–77)

```
def manifest() -> Manifest
```

**Purpose**: This describes the extension to the UFO host. It tells the host the extension name and version, what credential it needs, and how to build the Browserbase CDP provider.

**Data flow**: It creates a credential declaration for `browserbase_cdp_url`, creates a CDP provider specification for the Browserbase backend, and packages both into a `Manifest`. The returned manifest is the host’s instruction sheet for loading this extension.

**Call relations**: The extension-loading system calls this to discover what the file contributes. The manifest registers a builder that creates `BrowserbaseCdpProvider` with credential access, so later browser setup can request the Browserbase backend and receive this provider.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease setup, then reused across the conversation sandbox`

A conversation sandbox is like a private workroom for one user interaction. This file makes sure that workroom has its own headless Chrome browser, meaning Chrome runs without a visible window. The rest of the system talks to Chrome through CDP, the Chrome DevTools Protocol, which is Chrome’s remote-control interface.

The main challenge is that Chrome’s control port is inside the sandbox, while the serving process needs to reach it from outside. The file solves this in three steps. First, it runs a shell script in the sandbox that starts Chrome on a fixed local port if it is not already running. Second, it starts a small TCP proxy on another port. That proxy rewrites the HTTP `Host` header to `127.0.0.1`, because Chrome refuses some DevTools requests unless they look local. Third, it asks Chrome for its WebSocket debugger URL, converts the local address into the sandbox’s public per-port host, and attaches the sandbox traffic token as a connection header when needed.

Chrome and the proxy are meant to live as long as the conversation sandbox does, not just one turn. So closing a lease does not stop them. If the system tries to reattach from an old token alone, this provider refuses, because the live sandbox is needed to resolve the usable endpoint.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 194–195)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the already prepared Chrome control endpoint for this lease. Other code uses this when it is ready to connect to the sandbox’s browser.

**Data flow**: It reads the stored `endpoint_` value from the lease and returns it unchanged. Nothing else is created, contacted, or modified.

**Call relations**: A lease is created by `SandboxChromeCdpProvider.lease` after Chrome and the proxy are ready. Later, the browser engine asks this method for the endpoint it should connect to.


##### `SandboxChromeCdpLease.token`  (lines 197–198)

```
async def token(self) -> str
```

**Purpose**: Returns a simple text token for the lease. In this provider, that token is just the endpoint URL, used as a durable label for the connection target.

**Data flow**: It reads the URL from the stored endpoint and returns that string. It does not check whether Chrome is still alive or change any sandbox state.

**Call relations**: Code that works with generic CDP leases can ask for a token after receiving the lease from `SandboxChromeCdpProvider.lease`. This provider does not later reconstruct a live connection from that token by itself.


##### `SandboxChromeCdpLease.aclose`  (lines 200–201)

```
async def aclose(self) -> None
```

**Purpose**: Closes the lease from the caller’s point of view, but deliberately does not stop Chrome. This matters because Chrome and its proxy are shared across turns in the same conversation sandbox.

**Data flow**: It receives no extra information and returns without doing any cleanup. The browser process, proxy process, and sandbox remain as they were.

**Call relations**: Generic lease users can call this when a turn is done. For this provider, the close call is intentionally a no-op because the next turn should be able to reuse the same sandbox browser.


##### `SandboxChromeCdpProvider.lease`  (lines 212–223)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Prepares a usable Chrome control connection for the current sandbox. It starts or reuses Chrome, starts or reuses the proxy, finds Chrome’s debugger WebSocket path, and returns a lease containing the public endpoint.

**Data flow**: It takes a `SandboxSession`, which represents the live sandbox for the current turn. It runs setup commands inside that sandbox, reads Chrome’s local WebSocket URL, extracts just the path, asks the sandbox for the public host of the proxy port, adds a traffic access header if the sandbox has a token, and returns a `SandboxChromeCdpLease` containing a `CdpEndpoint`.

**Call relations**: This is the main entry used by the core CDP provider seam when a turn needs a browser. It relies on `_run` to execute sandbox commands, `_ws_path` to make sure the debugger URL is local, `SandboxSession.host` to learn the reachable public host, and `_remote_ws_url` to build the final WebSocket address.

*Call graph*: calls 4 internal fn (host, _remote_ws_url, _run, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 225–226)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reports that an old lease token cannot be used to reconnect by itself. A fresh lease must be created with the live sandbox instead.

**Data flow**: It receives a token string, wraps it in a `SessionGone` error, and raises that error. It returns no lease and does not contact the sandbox.

**Call relations**: When generic recovery code asks this provider to reattach from a saved token, this method stops that path. The design pushes recovery back through `SandboxChromeCdpProvider.lease`, because only a current `SandboxSession` can supply the correct public host and access token.

*Call graph*: 1 external calls (__init__).


##### `_run`  (lines 229–233)

```
async def _run(sandbox: SandboxSession, command: str, what: str) -> str
```

**Purpose**: Runs one shell command inside the sandbox and turns failure into a clear Python error. It is the common helper for starting Chrome, starting the proxy, and reading the debugger URL.

**Data flow**: It receives a sandbox, a command string, and a short label describing the action. It sends the command to `SandboxSession.bash` with a timeout, checks the exit code, returns standard output on success, and raises an error containing command output on failure.

**Call relations**: `SandboxChromeCdpProvider.lease` calls this helper for each sandbox-side step. By centralizing command execution, the lease flow gets consistent timeout behavior and error messages.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 236–241)

```
def _ws_path(url: str) -> str
```

**Purpose**: Extracts the path part from Chrome’s local WebSocket debugger URL. It also protects the system from accidentally accepting a debugger URL that points somewhere unexpected.

**Data flow**: It takes a URL string printed by Chrome, trims whitespace, and checks that it begins with the expected local Chrome address, either `127.0.0.1` or `localhost` on the browser control port. If it matches, it removes that local prefix and returns only the remaining path; otherwise it raises an error.

**Call relations**: `SandboxChromeCdpProvider.lease` calls this after `_run` reads Chrome’s `/json/version` information. The returned path is then handed to `_remote_ws_url` so the local debugger path can be rebuilt on the sandbox’s public proxy host.

*Call graph*: called by 1 (lease).


##### `_remote_ws_url`  (lines 244–250)

```
def _remote_ws_url(host: str, path: str) -> str
```

**Purpose**: Builds the final WebSocket URL that outside code can use to talk to Chrome through the sandbox proxy. It converts ordinary web schemes into WebSocket schemes.

**Data flow**: It receives a public host and a WebSocket path. It first normalizes the host through `_remote_url`, removes a trailing slash, then turns `https://` into `wss://` or `http://` into `ws://`, and appends the path. If the host cannot be understood as HTTP or HTTPS, it raises an error.

**Call relations**: `SandboxChromeCdpProvider.lease` calls this after it has both the sandbox’s public proxy host and Chrome’s debugger path. This helper delegates host normalization to `_remote_url` before returning the endpoint URL used in the `CdpEndpoint`.

*Call graph*: calls 1 internal fn (_remote_url); called by 1 (lease).


##### `_remote_url`  (lines 253–257)

```
def _remote_url(host: str) -> str
```

**Purpose**: Normalizes a sandbox host into a full HTTP or HTTPS URL. This lets the rest of the code accept either a complete URL or a bare host name.

**Data flow**: It receives a host string. If the string already starts with `http://` or `https://`, it returns it unchanged. If it is a local-looking host such as `localhost` or `127.0.0.1`, it adds `http://`; otherwise it adds `https://`.

**Call relations**: `_remote_ws_url` calls this before converting the result into a WebSocket URL. This keeps the scheme-choice rule in one small place.

*Call graph*: called by 1 (_remote_ws_url).


##### `manifest`  (lines 260–269)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the larger system. It says that the provider named `sandbox_chrome` is available and tells the system how to build it.

**Data flow**: It creates a `Manifest` containing the extension name, version, and a CDP provider specification. That specification maps the `sandbox_chrome` backend name to a small builder that returns a `SandboxChromeCdpProvider`.

**Call relations**: The extension loading system calls this function to discover what the file offers. The returned manifest connects configuration such as `[browser] cdp_provider = "sandbox_chrome"` to the provider class that later creates leases.

*Call graph*: 2 external calls (__init__, __init__).


### Browser endpoint contract
The core abstraction that lets the system request a Chrome DevTools connection without depending on a specific provider.

### `core/src/ufo/browser.py`

`data_model` · `cross-cutting; used when a turn starts, when reconnecting after recovery, and when a turn ends`

This file is a boundary line between the core system and any actual browser provider. The core does not start Chrome itself and does not choose a default browser service. Instead, it speaks through a small shared agreement: a provider can lend out a CDP endpoint for one turn, and that loan can later be closed or reattached to.

CDP means Chrome DevTools Protocol, the remote-control interface used to drive Chrome. A `CdpEndpoint` is the address and optional connection headers needed to reach that interface. A `CdpLease` is like borrowing a keycard for one visit: it gives access to the endpoint, can provide a saved token for reconnecting later, and must be returned or released when the turn ends. A `CdpProvider` is the place that issues those keycards.

The important design choice is that this file only describes the seam, not the browser engine. One extension might point to Chrome running inside a sandbox for the current conversation. Another might rent a remote browser from a hosted service. Both can fit as long as they follow this interface. If a saved browser session is gone when the system tries to reconnect, `SessionGone` tells the caller to start fresh instead of pretending the old page still exists.

#### Function details

##### `CdpLease.endpoint`  (lines 45–45)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This method gives the browser-driving code the actual CDP address to connect to, plus any headers needed for that connection. Someone uses it when they are ready to open a connection to Chrome for the current turn.

**Data flow**: It reads the lease's stored browser connection information. From that, it returns a `CdpEndpoint`, which contains a URL and optional headers. If the lease cannot provide a usable endpoint, the concrete implementation may report that failure instead of returning one.

**Call relations**: A browser extension calls this after a `CdpProvider` has created or reattached a lease. The returned endpoint is then handed to the browser engine, which connects to Chrome and drives the page.


##### `CdpLease.token`  (lines 47–47)

```
async def token(self) -> str
```

**Purpose**: This method returns a saved handle that can be written down and used later to reconnect to the same browser session if it still exists. It matters for recovery: a turn can resume without always starting from a blank browser.

**Data flow**: It reads whatever durable identity the lease has for the browser session, such as a remote session id or a stable endpoint string. It returns that identity as plain text so another part of the system can persist it.

**Call relations**: After a lease is created, the browser extension can call this to save a reattach token. Later, that token is passed to `CdpProvider.reattach` to try to rebuild a lease over the same live session.


##### `CdpLease.aclose`  (lines 49–49)

```
async def aclose(self) -> None
```

**Purpose**: This method releases the browser lease when the turn is finished. For a remote hosted browser, that may mean giving back or closing the rented session; for a static local endpoint, it may do nothing.

**Data flow**: It takes the current lease state, performs any cleanup required by the provider, and returns nothing. The visible change is outside the return value: the held browser resource may be released.

**Call relations**: The turn cleanup flow calls this after the browser is no longer needed. It is the counterpart to `CdpProvider.lease` or `CdpProvider.reattach`, making sure a per-turn browser hold does not linger unnecessarily.


##### `CdpProvider.lease`  (lines 62–62)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This method creates a fresh browser lease for a turn. It may use the current sandbox session when the browser lives inside that sandbox, or ignore it when the browser is remote or static.

**Data flow**: It receives an optional `SandboxSession`, which represents the isolated environment for the turn. The provider uses that context, or its own configuration, to locate or create a Chrome CDP endpoint, then returns a `CdpLease` that represents the turn's hold on it.

**Call relations**: At the start of a turn, the orchestration code asks the chosen provider for a lease. The returned lease is then used by the browser extension: first to get an endpoint, possibly to save a token, and finally to close the lease.


##### `CdpProvider.reattach`  (lines 64–64)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This method tries to reconnect to a browser session that was saved earlier. It is used when the system has a token from a prior run and wants to continue with the same live browser if possible.

**Data flow**: It receives a text token that names an earlier browser session. The provider looks up or resolves that session and returns a new `CdpLease` over it. If the session no longer exists, it raises `SessionGone`, which tells the caller to create a fresh lease instead.

**Call relations**: Recovery code calls this before minting a new browser session. If it succeeds, the browser extension continues through the returned lease just like a fresh one; if it fails with `SessionGone`, the caller falls back to `CdpProvider.lease`.


### Agent browser tools
Public browser automation tools that expose page navigation, interaction, inspection, screenshots, and downloads to agents.

### `extensions/browser/ufo_ext_browser/tools.py`

`orchestration` · `during an agent turn when browser tools are invoked`

This file turns browser work into a set of safe, named tools the agent can call during a turn. Think of it like a front desk for a remote-controlled browser: the agent asks for a specific service, the front desk checks the request shape, then passes it to the browser operator behind the scenes.

The browser operator is a `BuaSurface`, which is the object that actually talks to the browser through CDP, the Chrome DevTools Protocol, a control channel for Chrome-like browsers. The file creates only one browser surface per agent turn. That matters because opening browser connections and hosted sessions is expensive and must be cleaned up reliably. The surface is cached using the turn's cleanup object, and its close method is registered so it is released when the turn ends.

Most tool handlers are thin translators. They take a validated input model, remove fields meant only for human or agent context, send the remaining data to `BuaSurface`, and wrap the reply as tool output. Two tools also write files into the shared workspace: `computer` can save a screenshot, and `wait_for_download` saves downloaded bytes. At the bottom, the file publishes `BROWSER_TOOLS`, the actual tool definitions the rest of the system can register.

#### Function details

##### `_browser`  (lines 95–118)

```
def _browser(ctx: ToolContext) -> BuaSurface
```

**Purpose**: Gets the one browser control surface for the current agent turn, creating it only if this is the first browser action in that turn. This avoids opening multiple browser connections for separate actions and makes cleanup predictable.

**Data flow**: It receives the current tool context, looks in a per-turn cache keyed by the cleanup registry, and either returns the existing `BuaSurface` or builds a new one from the configured browser connection provider, search helper, model, sandbox, extension store, and conversation id. When it creates a new surface, it also registers that surface to be closed at the end of the turn.

**Call relations**: Every browser action handler calls this first before doing real browser work. If no surface exists yet, this function constructs the `BuaSurface`; after that, later handlers in the same turn reuse the same object.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 1 external calls (__init__).


##### `_json_result`  (lines 121–122)

```
def _json_result(reply: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Packages a plain Python reply dictionary as a tool result containing JSON text. It gives all the simple browser tools a consistent response format.

**Data flow**: It receives a dictionary, converts it to a JSON string, wraps that string in text content, and returns a `ToolResult` ready for the tool system to send back to the agent.

**Call relations**: Most handlers call this after receiving a reply from the browser surface. It is the final wrapping step for navigation, tab actions, page reading, finding elements, form input, downloads, and some computer-action replies.

*Call graph*: called by 11 (_computer, _find, _form_input, _get_page_text, _navigate, _read_page, _tabs_close, _tabs_context, _tabs_create, _upload_file (+1 more)); 3 external calls (__init__, __init__, dumps).


##### `_required_str`  (lines 125–128)

```
def _required_str(value: JsonValue, field: str) -> str
```

**Purpose**: Checks that a browser reply contains a required non-empty string field. It protects later file-writing code from silently using missing or malformed data.

**Data flow**: It receives a value and the name of the field being checked. If the value is a non-empty string, it returns that string; otherwise it raises an error naming the missing field.

**Call relations**: `_computer` uses it when saving a screenshot, and `_wait_for_download` uses it when saving a downloaded file. In both cases, it acts as a guard before decoding base64 data or building a file path.

*Call graph*: called by 2 (_computer, _wait_for_download).


##### `_navigate`  (lines 131–135)

```
async def _navigate(ctx: ToolContext, args: NavigateInput) -> ToolResult
```

**Purpose**: Runs the browser navigation tool, such as going to a URL or moving through browser history. It lets the agent change what page is open.

**Data flow**: It receives the tool context and validated navigation input. It turns the input into a JSON-friendly dictionary, leaves out the human-facing description, sends the request to the browser surface, and returns the browser's reply as JSON text.

**Call relations**: The `navigate` tool definition points to this handler. When the agent asks to navigate, this function gets the shared browser surface through `_browser`, hands off the navigation request, and uses `_json_result` to return the outcome.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_tabs_context`  (lines 138–139)

```
async def _tabs_context(ctx: ToolContext, args: TabsContextInput) -> ToolResult
```

**Purpose**: Returns information about the currently open browser tabs. This helps the agent understand what pages are available before choosing a tab or deciding what to close.

**Data flow**: It receives the tool context and an empty input object. It asks the browser surface for tab context with no extra options, then wraps the reply as JSON text.

**Call relations**: The `tabs_context` tool definition uses this handler. It calls `_browser` to reach the shared browser surface and `_json_result` to format the surface's answer.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_create`  (lines 142–144)

```
async def _tabs_create(ctx: ToolContext, args: TabsCreateInput) -> ToolResult
```

**Purpose**: Creates a new browser tab, optionally opened at a requested URL. If no URL is supplied, it opens a blank tab.

**Data flow**: It receives the tool context and tab creation input. It chooses the requested URL or falls back to `about:blank`, sends that to the browser surface, and returns the result as JSON text.

**Call relations**: The `tabs_create` tool definition routes here. This handler relies on `_browser` for the active surface and `_json_result` for the final tool response.

*Call graph*: calls 2 internal fn (_browser, _json_result).


##### `_tabs_close`  (lines 147–149)

```
async def _tabs_close(ctx: ToolContext, args: TabsCloseInput) -> ToolResult
```

**Purpose**: Closes a browser tab, either a specific one if a tab id is provided or whatever the browser surface treats as the default. This lets the agent tidy up tabs it no longer needs.

**Data flow**: It receives the tool context and close-tab input. It converts the input to a JSON-friendly dictionary without empty fields, sends it to the browser surface, and wraps the reply as JSON text.

**Call relations**: The `tabs_close` tool definition uses this handler. It gets the shared surface through `_browser`, delegates the close request, then returns the answer through `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_upload_file`  (lines 152–154)

```
async def _upload_file(ctx: ToolContext, args: UploadFileInput) -> ToolResult
```

**Purpose**: Sets a web page file input to files from the workspace. This is how the agent can upload files through a browser form.

**Data flow**: It receives the tool context and upload input, including a browser element reference and workspace file paths. It converts the input into a JSON-friendly dictionary, sends it to the browser surface, and returns the browser's reply as JSON text.

**Call relations**: The `upload_file` tool definition invokes this handler. The handler uses `_browser` to reach the automation engine and `_json_result` to turn the result into normal tool output.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_read_page`  (lines 157–161)

```
async def _read_page(ctx: ToolContext, args: ReadPageInput) -> ToolResult
```

**Purpose**: Reads the page's accessibility tree, which is a structured view of page elements meant for assistive technology. This gives the agent a navigable map of what is on the page, especially buttons, links, and form fields.

**Data flow**: It receives the tool context and page-reading options such as depth, filter, element reference, or tab id. It removes the human-facing description, sends the remaining options to the browser surface, and returns the structured page information as JSON text.

**Call relations**: The `read_page` tool definition points to this handler. The handler gets the browser surface with `_browser`, asks it to read the page, and formats the reply with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_get_page_text`  (lines 164–168)

```
async def _get_page_text(ctx: ToolContext, args: GetPageTextInput) -> ToolResult
```

**Purpose**: Extracts raw text from the current browser page. This is useful when the agent needs the page's written content rather than a structured list of interactive elements.

**Data flow**: It receives the tool context and input identifying the tab and the user's reason for reading. It drops the description field, sends the remaining data to the browser surface, and returns the extracted text reply as JSON text.

**Call relations**: The `get_page_text` tool definition uses this handler. It follows the common pattern: get the shared surface with `_browser`, delegate the browser work, and package the answer with `_json_result`.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_find`  (lines 171–175)

```
async def _find(ctx: ToolContext, args: FindInput) -> ToolResult
```

**Purpose**: Searches the current page for elements matching a query, such as text, role, name, or URL. It helps the agent locate the right on-page target before clicking or filling something.

**Data flow**: It receives the tool context and find input, including the search query and optional tab id. It removes the human-facing description, passes the search request to the browser surface, and returns the matches as JSON text.

**Call relations**: The `find` tool definition sends requests here. This handler uses `_browser` to access the active browser surface and `_json_result` to return the search results.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_form_input`  (lines 178–182)

```
async def _form_input(ctx: ToolContext, args: FormInputInput) -> ToolResult
```

**Purpose**: Sets a value in a form field identified by a browser reference. This lets the agent type or choose values without needing to simulate low-level keyboard actions.

**Data flow**: It receives the tool context and form input data, including the target reference and value. It removes the description field, sends the clean request to the browser surface, and returns the result as JSON text.

**Call relations**: The `form_input` tool definition is wired to this handler. It asks `_browser` for the current surface, delegates the form update, and uses `_json_result` for the response.

*Call graph*: calls 2 internal fn (_browser, _json_result); 1 external calls (model_dump).


##### `_computer`  (lines 185–203)

```
async def _computer(ctx: ToolContext, args: ComputerInput) -> ToolResult
```

**Purpose**: Runs lower-level browser interaction actions such as mouse movement, keyboard input, scrolling, waiting, and screenshots. It can also save a screenshot into the shared workspace when requested.

**Data flow**: It receives the tool context and a sequence of computer-style actions. It removes the human-facing description, sends the actions to the browser surface, and receives a reply that may include a screenshot encoded as base64 text. If saving is requested, it checks the screenshot field, decodes the bytes, writes them to the sandbox workspace, and adds the saved path to the reply. If a screenshot is present, it returns JSON for the non-image fields plus separate image content; otherwise it returns plain JSON.

**Call relations**: The `computer` tool definition routes here. This handler uses `_browser` for the browser work, `_required_str` when it must trust screenshot data enough to write it, and `_json_result` when no image needs special packaging.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 6 external calls (__init__, __init__, __init__, b64decode, model_dump, dumps).


##### `_wait_for_download`  (lines 206–214)

```
async def _wait_for_download(ctx: ToolContext, args: WaitForDownloadInput) -> ToolResult
```

**Purpose**: Waits for a browser download to finish and saves the downloaded file into the shared workspace. This makes files obtained through the browser available to the parent agent or other subagents by path.

**Data flow**: It receives the tool context and download options such as a download id, destination path, and timeout. It asks the browser surface for the completed download, checks that the filename and base64 content are present, decodes the file bytes, writes them into the sandbox workspace, and returns the saved path, filename, and size as JSON text.

**Call relations**: The `wait_for_download` tool definition uses this handler. It gets the active surface through `_browser`, validates critical returned fields with `_required_str`, writes through the sandbox, and uses `_json_result` for the final response.

*Call graph*: calls 3 internal fn (_browser, _json_result, _required_str); 2 external calls (b64decode, model_dump).

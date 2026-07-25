# Surfaces and runtime providers  `stage-3.1.4`

This stage is part of the system’s startup and discovery work. It tells UFO which “front doors” are available for people or tools to use, and which browser engine can run web tasks. A surface means a user-facing entry point, such as a web page, terminal, or debugger connection.

The web manifest is like a registration card for the web extension. It gives the extension a name and declares the web surface that UFO should mount. The UFO manifest does the same for the terminal-facing shell, including how a workspace is identified so the right session is reached. The debugger manifest registers a debugger surface and protects it with the usual workspace identity check, so only the right operator can access it.

The Browserbase provider is different: it supplies execution power rather than a visible surface. It reads a saved Chrome DevTools Protocol connection URL, which is a remote-control address for Chrome, and offers that hosted browser to the rest of the system instead of launching Chrome locally.

## Files in this stage

### Hosted browser provider
Provides a Browserbase-hosted Chrome connection as a runtime browser provider.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `startup registration and per-turn browser lease creation`

This extension is the bridge between UFO and Browserbase, a service that runs Chrome remotely. The system talks to Chrome through CDP, the Chrome DevTools Protocol, which is a standard control channel for driving a browser. Instead of creating a new browser inside each sandbox, this provider connects to one fixed Browserbase WebSocket URL stored as a host-side credential.

The main idea is simple: the browser already exists elsewhere, so a “lease” is just permission to use that fixed address. The file defines StaticLease, a tiny object that gives back the connection endpoint, uses the URL itself as the reconnect token, and does nothing when closed because the remote Browserbase session is not owned by this process.

BrowserbaseCdpProvider is the piece the system asks for a browser connection. Each time it needs a lease, it reads the current credential slot named browserbase_cdp_url. Reading it fresh matters because if the URL is rotated, the next turn picks up the new value without code changes. The sandbox argument is ignored on purpose: the browser is remote, so the local container is not involved.

Finally, manifest() advertises this extension to the host system. It declares the needed credential and registers the provider under the Browserbase backend name.

#### Function details

##### `StaticLease.endpoint`  (lines 33–34)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the browser connection address stored inside the lease. The rest of the system uses it when it needs to open the actual CDP connection to Chrome.

**Data flow**: It starts with a StaticLease that already contains a CdpEndpoint. It reads that saved endpoint and returns it unchanged. Nothing else is modified.

**Call relations**: A StaticLease is created by BrowserbaseCdpProvider.lease or BrowserbaseCdpProvider.reattach. Later, when the browser-driving code needs to connect, it asks this lease for its endpoint and receives the fixed Browserbase URL.


##### `StaticLease.token`  (lines 36–37)

```
async def token(self) -> str
```

**Purpose**: This returns a reconnect handle for the lease. In this provider, the handle is simply the Browserbase connection URL itself.

**Data flow**: It reads the URL from the lease’s stored CdpEndpoint and returns that text value. It does not contact Browserbase or change any state.

**Call relations**: After a lease has been created, higher-level code can ask for this token so it can later try to reconnect. For this static provider, the token mirrors the endpoint URL because reconnecting means going back to the same fixed Browserbase address.


##### `StaticLease.aclose`  (lines 39–40)

```
async def aclose(self) -> None
```

**Purpose**: This closes the lease, but for Browserbase there is nothing local to shut down. The hosted browser session is not created or destroyed by this lease.

**Data flow**: It receives the lease object and immediately returns without changing anything. No browser is stopped, no network connection is closed here, and no cleanup is performed.

**Call relations**: When the wider system finishes a turn, it may close any browser lease it was given. For this provider, that close request ends at StaticLease.aclose because Browserbase owns the remote session lifetime.


##### `BrowserbaseCdpProvider._endpoint`  (lines 51–52)

```
async def _endpoint(self) -> CdpEndpoint
```

**Purpose**: This builds the connection endpoint for the remote Browserbase browser. It reads the saved Browserbase CDP URL from the credential store and wraps it in the endpoint type expected by the browser system.

**Data flow**: It reads the credential named browserbase_cdp_url through CredentialAccess. That credential value is the WebSocket URL used to reach Browserbase. It then creates and returns a CdpEndpoint containing that URL.

**Call relations**: This is the shared helper used by BrowserbaseCdpProvider.lease and BrowserbaseCdpProvider.reattach. Both paths need the current Browserbase URL, so they call this helper before wrapping the endpoint in a StaticLease.

*Call graph*: called by 2 (lease, reattach); 1 external calls (__init__).


##### `BrowserbaseCdpProvider.lease`  (lines 54–55)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This gives the system a lease for using the Browserbase browser during a turn. Unlike a local browser provider, it does not start Chrome inside the sandbox; it points to the already-hosted remote browser.

**Data flow**: It may receive a SandboxSession, but ignores it because the browser is not inside that sandbox. It asks _endpoint for the current Browserbase connection URL, then puts that endpoint into a new StaticLease and returns it.

**Call relations**: The broader browser setup code calls this when it needs a CDP lease for a turn. This function delegates URL lookup to BrowserbaseCdpProvider._endpoint and hands back a StaticLease that the browser-driving code can use.

*Call graph*: calls 1 internal fn (_endpoint); 1 external calls (__init__).


##### `BrowserbaseCdpProvider.reattach`  (lines 57–58)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This reconnects to the Browserbase browser after the system already has a prior lease token. In practice, it still reads the current configured URL, so a rotated credential can take effect.

**Data flow**: It receives a token string, but does not use it to rebuild the endpoint. Instead, it reads the current browserbase_cdp_url credential through _endpoint, wraps the resulting endpoint in a StaticLease, and returns that lease.

**Call relations**: Higher-level code calls this when it wants to resume or reconnect rather than create a fresh lease. This function follows the same path as lease: it asks BrowserbaseCdpProvider._endpoint for the URL and returns a StaticLease.

*Call graph*: calls 1 internal fn (_endpoint); 1 external calls (__init__).


##### `manifest`  (lines 61–77)

```
def manifest() -> Manifest
```

**Purpose**: This tells the host system what this extension provides and what secret it needs. It registers Browserbase as a CDP browser backend and declares the credential slot where the Browserbase URL must be stored.

**Data flow**: It creates a Manifest containing the extension name and version, a CredentialSlot named browserbase_cdp_url, and a CdpProviderSpec for the browserbase backend. The provider spec includes a builder that receives credential access and constructs a BrowserbaseCdpProvider.

**Call relations**: The extension loader calls this during registration. The returned Manifest lets the host discover the required credential and later build a BrowserbaseCdpProvider when configuration selects the Browserbase CDP backend.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Runtime surface manifests
Registers debugger, terminal, and web-facing extension surfaces with the UFO host.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This is the debugger extension’s small “registration card.” When the larger UFO system discovers extensions, it needs a standard way to ask each one: What is your name? What version are you? What parts of the system do you add? This file answers those questions.

It names the extension as `debugger`, gives it version `0.1.0`, and defines a `manifest()` function that returns a `Manifest` object. A manifest is like a shipping label for an extension: it describes what the extension contains so the core system can mount it in the right place.

The important part here is the single `SurfaceSpec`. A “surface” is an exposed area of functionality, such as a set of web routes. This debugger surface uses `ROUTES` from the debugger’s surface module and is named with `SURFACE_DEBUG`. It also supplies `resolve_operator_workspace` as its identity resolver, meaning access is tied to recognizing the operator workspace. In plain terms, the debugger is not just opened everywhere; it is attached only where the system can confirm the right operator context.

Without this file, the debugger code might exist, but the host system would not know how to discover it, name it, version it, or connect its routes safely.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the object the core system reads to discover and mount this extension. It describes the extension’s name, version, exposed debugger surface, routes, and identity check.

**Data flow**: It starts with the module’s constants, such as the extension name and version, plus the imported debugger route list and surface name. It wraps the debugger surface details into a `SurfaceSpec`, then places that inside a `Manifest`. The result is a complete manifest object that the rest of the system can use; it does not change any existing state.

**Call relations**: During extension discovery, the host system calls `manifest` to ask this extension what it provides. Inside, it creates a `SurfaceSpec` to describe the debugger surface, then creates a `Manifest` to package that surface with the extension’s name and version. That manifest is handed back to the core system so the debugger routes can be mounted with the operator workspace identity resolver attached.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

Think of this file as the extension’s registration card. When the larger UFO system starts up or scans installed extensions, it needs a simple answer: “What is this extension called, what version is it, and what entry points does it expose?” This file provides that answer.

The extension is named `ufo` and has version `0.1.0`. Its main job is to declare a single “surface,” meaning a place where the core system and an outside client meet. In this case, the surface is the live stream used by the terminal shell client. The routes for that surface come from the surface module, and workspace identification is delegated to `resolve_workspace`, which decides which workspace a request belongs to.

A notable design choice is what this manifest does not include. It does not define credential storage slots or user-tunable configuration. The file-level comment explains that access is checked through a bearer token secret from the environment, `UFO_TOKEN_SECRET`, rather than through a workspace credential slot. Installed means active: once this extension is present, its surface is mounted.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the object the core system reads to learn what this extension provides. Someone would use it during extension loading to register the `ufo` surface and its routes.

**Data flow**: It starts with the fixed extension name and version from this file, plus route and workspace-identification details imported from the surface module. It wraps the surface details into a `SurfaceSpec`, then places that inside a `Manifest`. The result is a complete description of the extension that the host can mount.

**Call relations**: When the extension system asks this module for its manifest, this function creates the manifest object on demand. As part of that, it creates a surface specification first, then hands that specification into the manifest so the core can expose the terminal stream route correctly.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup / extension discovery`

This file solves a simple but important problem: the core system needs a standard way to learn what an extension provides. For the web extension, that means declaring its name, version, and the web routes it wants to expose. Without this manifest, the core would not know to mount the web surface, so the browser-based parts of the extension would never become available.

The file defines two plain constants, `NAME` and `VERSION`, then provides one function, `manifest()`, that builds a `Manifest` object. A manifest is like a label on a plug-in box: it says “I am the web extension, version 0.1.0, and here is how to connect me.”

The key detail is the `SurfaceSpec`. A “surface” is a way the outside world can interact with the system. Here, the surface is the web interface. The surface specification points to the web route list and to `resolve_workspace`, which is used to identify which workspace a web request belongs to. The comments also make clear that this extension does not declare credential slots or extra configuration knobs: if it is installed, it is mounted, and user identity comes through the web session cookie rather than a shared bot secret.

#### Function details

##### `manifest`  (lines 14–19)

```
def manifest() -> Manifest
```

**Purpose**: Creates and returns the web extension’s manifest, which is the object the core system reads to understand what this extension contributes. Someone would use it when loading extensions so the web routes can be registered.

**Data flow**: It starts with the file’s fixed name and version, plus imported web-surface details such as the route list, surface name, and workspace-identifying function. It packages those into a surface description, then wraps that in a manifest. The result is a complete description of the web extension that the core can consume.

**Call relations**: During extension discovery, the system calls this function to ask the web extension what it provides. The function builds a `SurfaceSpec` for the web interface and hands that into a `Manifest`, so the core receives one clean object describing how to mount the extension.

*Call graph*: 2 external calls (__init__, __init__).

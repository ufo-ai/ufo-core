# Platform surface and infrastructure manifests  `stage-3.6`

This stage is part of startup and shared setup. It is made of small “manifest” files, which are like labels and instruction cards for plug-in parts of the system. They tell the main UFO application what extra features exist, what version they are, and where to connect them.

The debugger manifest registers the debugger extension and its single web/API surface, so the host knows which route to expose for debugging tools. The Redis hub manifest registers optional Redis-backed hub and terminal pieces. Redis is an external in-memory data store; here it is used as shared backing for communication and terminal state. This manifest also provides factory functions, which are small makers that build the Redis versions when selected in configuration.

The UFO manifest registers the core UFO web surface, including the shell stream route used for live interaction. The web manifest installs the user-facing portal. It declares routes, permissions, conversation storage areas, and a background service that gives chats names based on their first messages. Together, these files make the platform’s visible doors and supporting services discoverable.

## Files in this stage

### Platform extension manifests
Registers the stage’s debugger, Redis infrastructure, UFO shell stream, and web portal extension surfaces with the host system.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This is the debugger extension's registration card. When the larger system discovers extensions, it needs a standard way to ask, “What are you called, what version are you, and what routes do you add?” This file answers that by building a Manifest, which is a small description object the host can read.

The extension exposes one surface, named by SURFACE_DEBUG. A “surface” is the public area where an extension can attach routes, much like putting a labeled counter in a shared building. The actual route definitions come from ROUTES in the debugger surface module, so this file does not define the debugger behavior itself. It only points the host toward where that behavior is.

The important safety detail is the identify setting. It uses resolve_operator_workspace, which means access is tied to recognizing the operator workspace before this debugger surface is mounted or used. In plain terms, the debugger is not advertised as a general public feature; it is attached through the core extension system with an identity check at the boundary.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension's manifest, which is the object the host system reads to learn what this extension provides. Someone would use it during extension loading so the debugger can be registered consistently.

**Data flow**: It reads the fixed extension name and version from this file, plus the debugger surface name and route table imported from the surface module. It wraps those into a SurfaceSpec, then places that surface inside a Manifest. The result is a complete description object; it does not change files, databases, or outside state.

**Call relations**: During extension discovery, the host calls this function to get the debugger's registration details. Inside, it creates a SurfaceSpec to describe the debugger surface, then creates a Manifest to package that surface with the extension name and version.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup/config load`

This file is the front door for the Redis hub extension. In the core system, a hub is the place where live frames are shared, and a terminal transport is how a user’s connected terminal can be reached. The normal versions work inside one running server process. This extension replaces them with Redis-backed versions, so several server instances can share work and still reach the right user connection across pods or machines.

The key idea is simple: if configuration says the hub backend is “redis”, the system calls this manifest to build a RedisStreamHub. If configuration says the terminal backend is “redis”, it builds RedisTerminals. Both use the same Redis address, config.hub.url. That address is required. The builders check it immediately and raise a clear error if it is missing, instead of letting the system fail later in a more confusing place.

The manifest function packages all of this into a Manifest object: the extension’s name, version, the hub backend it offers, and the terminal transport it offers. Think of it like a product label plus installation instructions. Without this file, the Redis classes might exist, but the host application would not know that “redis” is a selectable backend or how to construct it.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed live-frame hub. It is used when the application has selected the Redis hub backend and needs a concrete hub object to send frames through Redis instead of only inside one process.

**Data flow**: It receives a Redis URL, which may be missing. If the URL is missing, it stops immediately with a clear error message explaining that hub.url is required. If the URL is present, it passes that URL into RedisStreamHub and returns the newly created hub.

**Call relations**: The manifest registers this function inside a HubSpec. Later, when the host application chooses the “redis” hub backend, that spec calls this builder. The builder then hands off to RedisStreamHub.__init__ to create the actual Redis-based hub.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport. It lets the system reach a member’s connected terminal even when the request is admitted by a different server instance than the one holding that terminal connection.

**Data flow**: It receives a Redis URL and a blob store. The blob store is shared storage for larger terminal-related data. If the URL is missing, it raises a clear error before anything is built. If the URL is present, it creates RedisTerminals with the Redis URL and blob store, then returns that transport object.

**Call relations**: The manifest registers this function inside a TerminalTransportSpec. When configuration selects the “redis” terminal backend, the application calls this builder. The builder then hands off to RedisTerminals.__init__, which creates the actual transport that uses Redis and the blob store.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension description that the main UFO application can read. It declares the extension’s name and version, and says that this package provides Redis implementations for both the hub and terminal transport.

**Data flow**: It uses the constants in this file, wraps the hub builder in a HubSpec, wraps the terminal builder in a TerminalTransportSpec, and places both into a Manifest object. The result is a single object the host system can inspect to discover what this extension offers.

**Call relations**: This is the function the extension loader calls during startup or configuration loading. It creates HubSpec and TerminalTransportSpec entries that point back to _build_hub and _build_terminal, then returns them inside a Manifest so the wider system can build the selected Redis pieces later.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This is the extension’s calling card. When the main system loads extensions, it needs a small, predictable description of each one: its name, its version, and the routes it wants to expose. This file provides that description for the UFO extension.

The extension declares one “surface,” meaning one public area where the core application can connect incoming traffic to extension code. That surface uses route definitions imported from the UFO surface module, and it uses a workspace-identifying function so the system can tell which workspace an incoming request belongs to.

A useful way to think about this file is like a sign at a service desk: it says “this desk is called ufo, this is its version, and here is the window where customers should line up.” It does not implement the desk’s work itself. The actual route behavior lives elsewhere. This file simply packages the right pieces into a standard Manifest object so the wider platform can install and mount the extension consistently.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the UFO extension’s manifest, which is the standard description the host system reads when loading the extension. It names the extension, gives its version, and declares the single surface that should be mounted.

**Data flow**: It starts with the file’s constants for the extension name and version, plus imported route and workspace-identification pieces. It wraps the route information into a SurfaceSpec, then wraps that surface into a Manifest. The result is a Manifest object that the host can use to register the extension.

**Call relations**: This function is meant to be called by the extension-loading part of the system when it asks, “what does this extension provide?” In response, it creates a SurfaceSpec for the UFO surface and places it inside a Manifest, handing the core application the information it needs to expose the UFO route.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup`

A manifest is like a registration form for an extension. Without this file, the core system would not know that the web portal exists, which web routes to mount, which actions it is allowed to use, or which background work belongs to it.

This manifest describes one web-facing surface: the browser portal. It marks that surface as the home page, so visiting the bare deployment host opens the portal. It also provides an identity function, `resolve_workspace`, which connects an incoming web session to the right workspace. The file does not define credentials or configuration options; the comment explains that the user’s own session cookie carries access, and installing the extension is enough to mount it.

The manifest also declares two conversation slots. These are named places where chat-related state can be stored: one for changes and one for artifacts. Finally, it registers a scheduled job. That job looks for chats whose titles still need to be generated, then runs `summarize_chat_titles` to rewrite the chat’s rail label based on the opening exchange. In short, this file is the web extension’s handshake with the rest of the system: it says what the extension is called, what it exposes, what it may do, and what recurring work it needs.

#### Function details

##### `manifest`  (lines 29–46)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the object the core system reads to learn how this extension should be mounted and run. It names the extension, exposes its web routes, grants its web tools, declares its chat storage slots, and registers the chat-title background job.

**Data flow**: No caller-provided input is needed. The function gathers constants and helper functions imported from the web extension, such as the extension name, route list, workspace resolver, chat slots, and title-summary job settings. It wraps those into a `Manifest` object and returns that object to the core system; it does not write files or change global state itself.

**Call relations**: When the extension is being discovered or loaded, the system calls `manifest` to ask, “What do you provide?” Inside, it creates a `SurfaceSpec` to describe the browser surface, calls `store_key_workspaces` to find workspaces with pending chat-title work, creates a `JobSpec` for that recurring title job, and finally packages everything into a `Manifest` for the core extension loader to use.

*Call graph*: 4 external calls (__init__, __init__, __init__, store_key_workspaces).

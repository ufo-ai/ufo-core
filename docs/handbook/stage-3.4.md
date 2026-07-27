# Web, shell, communication, and live-surface extension manifests  `stage-3.4`

This stage is part of startup and shared setup. It is where add-on features introduce themselves to the UFO service before users can reach them. Each manifest is like an ID card plus a set of mounting instructions. It says what the extension is called, which version it is, and what doors it wants the main system to open.

The web manifest registers the general web routes, so browser-facing pages can be attached. The UFO manifest registers the UFO shell surface, the route used by the shell client to talk to the service. The debugger manifest adds debugging web routes and tells the host how to connect them to the right workspace. The Slack manifest registers Slack routes, declares the private workspace credentials it needs, and points to its setup tools and skills. The Redis hub manifest registers a live-frame hub backend named “redis” and checks that a Redis address is present before it is built. Together, these files let the core load optional user-facing and communication features safely and predictably.

## Files in this stage

### Debug and live backends
Manifests that expose debugging routes and register the Redis live-frame hub backend.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup`

This is the debugger extension’s registration card. In an extension-based system, the main application needs a simple way to ask each add-on, “Who are you, and what do you provide?” This file answers that question for the debugger.

It names the extension as `debugger`, gives it a version, and declares one “surface.” A surface is a set of routes, or reachable entry points, that the host can mount into the larger application. Here, the surface is the debugger surface, using route definitions imported from the debugger’s own `surface` module.

The important safety detail is the `identify` function. The debugger surface is connected to `resolve_operator_workspace`, which means incoming access is tied back to an operator workspace before the surface is used. In plain terms, the extension is not just saying “put these pages online”; it is also saying “only attach them in the right operator context.” That matters because debugger tools are powerful and should not be exposed casually.

Think of this file like the label and access instructions on a special toolbox: it says what the toolbox is called, which tools are inside, and which authorized workshop they belong in.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the object the host system reads to learn what this extension provides. It is used when the application is discovering or loading extensions.

**Data flow**: It starts with the file’s constants, `NAME` and `VERSION`, plus the imported debugger route list and surface name. It wraps the routes in a `SurfaceSpec`, adding `resolve_operator_workspace` as the way to identify the correct operator workspace. It then places that surface inside a `Manifest` object and returns it to the caller.

**Call relations**: When the extension system asks this module for its manifest, `manifest` creates the needed `SurfaceSpec` first, then creates the larger `Manifest` around it. The returned manifest is the handoff point: it tells the host system to mount the debugger routes under the debugger surface, using the operator workspace resolver to decide where that surface applies.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup / config load`

This is the extension’s front door. The core system has a “hub” seam: a place where different live-frame delivery backends can be plugged in. By default, the system may use an in-process hub, which only works inside one running server instance. This file registers an alternative hub that uses Redis Streams, so multiple server instances can share and fan out live frames through Redis.

The file defines three small constants: the extension name, its version, and the backend key users put in configuration: "redis". Its main job is to return a Manifest, which is like a sign-up sheet saying, “I provide a hub backend called redis, and here is how to build it.”

The builder function, _build_hub, receives the Redis URL from configuration. If the URL is missing, it fails immediately with a clear error. That is important because otherwise the system might start successfully and only crash later when it tries to publish its first frame. If the URL is present, the builder creates a RedisStreamHub, which is the actual Redis-backed hub implementation.

#### Function details

##### `_build_hub`  (lines 17–22)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed hub once the system has chosen the "redis" hub backend. It also protects users from a vague later failure by refusing to continue if no Redis URL was configured.

**Data flow**: It receives a URL value, which may be a Redis address such as redis://host:6379/0 or may be missing. If the value is missing, it raises a clear runtime error explaining that hub.url is required. If the value is present, it passes that URL into RedisStreamHub and returns the newly created hub object.

**Call relations**: This function is handed to the hub registration record made by manifest. Later, when the core system wants to build the selected hub backend, this builder is the piece that turns the configured URL into a RedisStreamHub by calling RedisStreamHub.__init__.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 25–30)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the main system. It says the extension is named redis_hub, gives its version, and registers one hub backend named "redis".

**Data flow**: It reads the file’s constants for the extension name, version, and backend name. It creates a HubSpec that connects the backend name "redis" to the _build_hub function, then wraps that specification in a Manifest and returns it.

**Call relations**: During extension discovery or startup setup, the system can call this function to learn what this extension provides. The function builds a HubSpec and a Manifest, and the returned manifest is what lets the core system later call _build_hub when the redis backend is selected.

*Call graph*: 2 external calls (__init__, __init__).


### Communication integration
Manifest that mounts Slack routes and declares the workspace credentials and assets needed for chat integration.

### `extensions/slack/ufo_ext_slack/manifest.py`

`config` · `startup / extension discovery`

Think of this file as the Slack extension’s registration card. The core system does not guess how Slack should work; it asks the extension for a manifest, which is a structured description of what the extension offers and what it needs.

The file names the extension as "slack" and gives it a version. It then describes two credential slots: one for a Slack bot token, and one for a Slack signing secret. A credential slot is a named private place where a workspace can store a secret. The comments explain an important split: normal OAuth installation uses deploy-wide Slack app secrets from environment variables, while a “bring your own app” setup asks the workspace to provide its own token and signing secret.

The manifest also declares a Slack “surface,” meaning the external place where users interact with the system. That surface has routes for incoming Slack events, interactive Slack actions, and the OAuth callback after installation. It connects those routes to functions from the Slack surface module. Finally, it exposes Slack tools and points to a setup skill directory used to guide a user through creating a Slack app manually.

#### Function details

##### `manifest`  (lines 36–67)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Slack extension’s manifest, which is the object the core system reads to know how to install and run Slack support. Someone would use this when loading extensions so the Slack routes, credentials, tools, and setup skill become known to the system.

**Data flow**: It starts with constants and imported Slack functions, such as the route handlers and credential slot names. It packages them into credential descriptions, route descriptions, a surface description, tool listings, and a skill path. The result is a single Manifest object that the rest of the system can read and use.

**Call relations**: When the extension is discovered, the core system calls this function to ask, “What do you provide?” The function creates CredentialSlot entries for the private Slack secrets, SurfaceRoute entries for the HTTP paths Slack will call, a SurfaceSpec tying those routes to Slack posting and workspace identification behavior, and a SkillSpec pointing to the setup instructions. It hands all of that to Manifest so the core can wire the Slack extension into the wider application.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, __init__).


### Shell and web surfaces
Manifests that identify and mount the UFO shell client surface and the general web extension routes.

### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and plug shape on a piece of equipment: it tells the larger UFO system what this extension is and how to attach it. Without this manifest, the core system would not know that the extension exists, what version it is, or which route should be exposed for the UFO terminal client.

The file defines a small manifest for an extension named “ufo”. A manifest is a structured description that the host application can read during extension setup. Here, it says there is one surface, meaning one exposed way for outside users or tools to interact with the extension. That surface uses routes imported from the extension’s surface module, and it uses `resolve_workspace` to identify which workspace a request belongs to.

An important detail is what is not here: there are no extra credential fields and no configuration switch. The comment explains that access is checked using a bearer token secret from the environment variable `UFO_TOKEN_SECRET`, rather than a stored workspace credential. In practice, installing the extension means this surface is mounted and available, much like a web route being registered in a server.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the manifest object that describes this extension to the host system. The host uses it to learn the extension name, version, and the UFO surface it should expose.

**Data flow**: It starts with the fixed extension name and version from this file, plus route and workspace-identification details imported from the surface module. It packages those into a `SurfaceSpec`, which describes one exposed surface, then places that surface inside a `Manifest`. The result is a ready-to-read manifest object; it does not write files or change external state.

**Call relations**: When the host system discovers or loads extensions, it calls `manifest` to ask this file what the UFO extension provides. Inside that call, `manifest` creates a `SurfaceSpec` for the UFO surface and then creates a `Manifest` containing that surface, handing the finished description back to the extension loader.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup / extension discovery`

This file is a manifest, which means it describes an extension to the larger UFO system rather than doing the extension’s work itself. In plain terms, it says: “There is a web extension named `web`, version `0.1.0`, and it exposes one surface called the web surface.” A surface is a place where users or outside systems can interact with UFO. Here, that surface comes with a set of web routes, which are URL paths the application can mount, and an identity function that figures out which workspace a request belongs to.

The important thing is that this extension does not declare secrets or extra settings. The comment explains why: the web session cookie carries the member’s own token, so there is no separate bot credential to configure. Also, installing the extension is enough to make it available, much like plugging in a module that immediately adds its routes.

Without this file, the core system would not know how to discover or mount the web extension. The route code might exist elsewhere, but nothing would announce it to the extension loader.

#### Function details

##### `manifest`  (lines 14–19)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the manifest object that describes the web extension to the UFO core system. Someone uses this when the system is discovering extensions and needs to know what this extension provides.

**Data flow**: It starts with the fixed extension name and version from this file, then combines them with a surface description. That surface description includes the web surface name, the route list imported from the web surface module, and the function used to identify the workspace for a request. The result is a `Manifest` object that the core can read during setup.

**Call relations**: During extension loading, the core calls `manifest` to ask this package what it offers. `manifest` creates a `SurfaceSpec`, which is like a label plus instructions for the web-facing part of the extension, then places it inside a `Manifest`, which is the complete description handed back to the core.

*Call graph*: 2 external calls (__init__, __init__).

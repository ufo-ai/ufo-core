# Surface Extension Registration  `stage-3.2`

This stage is part of startup and shared setup. Before people can use the system through a browser, a shell channel, or operator tools, the host needs to know which “surfaces” exist. A surface is a place where a user or operator can interact with the system, such as a web page, debugger route, or UFO channel.

The three manifest files act like registration cards. The debugger manifest announces the debugger extension, gives its version, and points to the web-facing routes that should be added for debugging. The UFO manifest announces the main UFO extension and the user-facing surface it contributes. The web manifest registers the web portal extension, names the portal surface, and lists the web-only tools that portal code is allowed to use.

Together, these files do not run the surfaces themselves. Instead, they let the host discover them, install them, and expose the right routes and tools in a controlled way.

## Files in this stage

### Extension Manifests
Registers the debugger, UFO shell, and web portal surfaces so the host system can expose each user-facing extension.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `extension load`

This is the debugger extension’s “registration card.” It does not contain the debugger tools themselves. Instead, it describes them to the main UFO platform so the platform knows how to mount them safely.

The file names the extension as `debugger` and gives it a version number. Its main job is to build a `Manifest`, which is a small package of metadata the host can read when loading extensions. Inside that manifest, it declares one `SurfaceSpec`. A surface is a public-facing area of the system, such as a set of routes or pages. Here, the surface is the debugger surface, and it is connected to the debugger’s route table.

The important safety detail is the `identify` function. The surface uses `resolve_operator_workspace`, which means access is tied to recognizing an operator workspace rather than being open everywhere. In everyday terms, this file is like placing a locked service desk inside a building: it says where the desk is, what signs point to it, and which identity check must pass before someone can use it.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the object the host system reads to discover this extension. Someone uses it when the extension is being loaded so the debugger surface and routes can be registered.

**Data flow**: It starts with the file’s fixed name and version values, plus the imported debugger surface name and route list. It wraps the surface name, routes, and operator-workspace identity check into a `SurfaceSpec`, then places that inside a `Manifest`. The result is a complete manifest object that describes the debugger extension to the rest of the system.

**Call relations**: When the extension loader asks this file for its manifest, `manifest` creates the needed `SurfaceSpec` first and then passes it into `Manifest`. It hands the finished manifest back to the host, which can then mount the debugger routes using the declared identity check.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

When the main application loads extensions, it needs a simple answer to three questions: what is this extension called, what version is it, and what does it add to the system? This file provides that answer for the `ufo` extension.

The extension exposes one “surface,” meaning one visible way for a client to interact with the system. Here, that surface is the terminal-facing `ufo` shell client. The file imports the route definitions, the surface name, and the function used to identify the workspace for incoming requests. It then packages those pieces into a `Manifest`, which is the object the core system reads during extension setup.

A useful analogy is a booth at a fair: the manifest is the sign and setup sheet saying “this booth is called ufo, it is version 0.1.0, and visitors should enter through this specific doorway.” Without this file, the core system would not know to mount the UFO surface or which routes belong to it.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest for `ufo`. The host system uses this to learn the extension’s name, version, exposed surface, routes, and workspace-identification function.

**Data flow**: It starts with the constants `NAME` and `VERSION`, plus imported surface details such as `SURFACE_UFO`, `ROUTES`, and `resolve_workspace`. It wraps the surface details into a `SurfaceSpec`, then wraps that into a `Manifest`. The result is a complete description of what this extension contributes to the system.

**Call relations**: During extension loading, the host calls `manifest` to ask this file what should be registered. Inside that call, it creates a `SurfaceSpec` for the UFO client surface and hands that specification to `Manifest.__init__`, so the core system can mount the extension correctly.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup / extension discovery`

A manifest is like a registration card for an extension. Without this file, the core system would not know that the web extension exists, what routes it adds, or how to identify which workspace a web request belongs to.

This manifest says the extension is called the web extension and is version 0.1.0. It also declares a single “surface,” which means a place where users interact with the system. Here, that surface is the web portal. The surface includes the routes the portal serves, plus an identity function that works out the workspace behind a request.

The file also exposes a set of web access tools. These are the actions the web portal is allowed to perform, especially the admin-only chat verbs mentioned in the file comment. Importantly, this extension does not declare separate credential settings. The comment explains why: the web session cookie carries the member’s own token, rather than using a shared bot secret. There is also no configuration switch here; if the extension is installed, the web surface is mounted.

#### Function details

##### `manifest`  (lines 16–22)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the object the core system reads to learn what this extension provides. Someone would use this when loading extensions at startup.

**Data flow**: It starts with constants imported from the web extension: the extension name, its allowed tools, its route list, the web surface name, and the workspace-identification function. It wraps the web route information into a SurfaceSpec, then puts that surface, the version, the name, and the tools into a Manifest. The result is a complete description of the web extension for the core system to consume.

**Call relations**: During extension loading, the core asks this function for the extension’s registration information. The function creates a SurfaceSpec to describe the web-facing routes and then creates a Manifest to package that surface together with the extension name, version, and tools.

*Call graph*: 2 external calls (__init__, __init__).

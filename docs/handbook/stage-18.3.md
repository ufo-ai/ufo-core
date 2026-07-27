# Platform surface and runtime extension manifests  `stage-18.3`

This stage is part of startup and shared platform support. It is made of “manifest” files, which are like registration cards. Each one tells the core system that an extension exists, what it is called, and what doors it wants to open.

The connectors manifest advertises connector tools, their shared object type, and prompt text so the host can offer them to users or agents. The debugger manifest registers debugger web and API access. The ufo manifest exposes the shell-client surface, letting a command-line client talk to the system. The web manifest adds normal web routes so browser-facing pages can be mounted. The page-alerts manifest lists chat tools and a background event listener for page alert activity. The scheduled-tasks manifest registers recurring task objects, a wait tool, a clock-based runner, and scheduling skills. The self-improvement manifest adds a daily job that evaluates and improves prompts using model access. The Redis hub manifest registers an optional Redis-backed hub, so live shared state can move through Redis instead of staying inside one server.

## Files in this stage

### Connector tooling
These manifests advertise user-facing connector capabilities and their prompt/tool surfaces to the host system.

### `extensions/connectors/ufo_ext_connectors/manifest.py`

`config` · `startup / extension load`

This file is the extension’s calling card. When the main application loads extensions, it needs a simple answer to questions like: What is this extension called? What tools does it provide? What extra instructions should be shown to the assistant? This file packages those answers into a Manifest, which is a structured description the host can read.

The connector tools declared here are deliberately generic. They are not tied to one outside service or one broker. Instead, they work across all connectors that other broker extensions register. In everyday terms, this file puts one shared “tool counter” in the workspace, while the individual broker extensions stock that counter with their own services.

The file also reads a Markdown prompt section from disk. That prompt text becomes an instruction block named external_tools, helping the assistant understand how to talk about and use these connector tools. The manifest then combines four pieces: the extension name, its version, the tool list, the connector object type, and the prompt section. The host application can use that single bundle during startup to make the connectors extension available.

#### Function details

##### `manifest`  (lines 21–28)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the connectors extension. The host uses this description to learn which connector tools, objects, and prompt text the extension contributes.

**Data flow**: It reads the constants already prepared in this file: the extension name and version, the shared connector tool list, the connector object definition, and the prompt text loaded from the Markdown file. It wraps the prompt text in a PromptSection, then places everything into a Manifest object. The result is a single package of extension metadata that the rest of the system can consume.

**Call relations**: When the extension is being discovered or loaded, the host calls this function to ask, “What do you provide?” The function creates a PromptSection for the assistant-facing instructions, then creates a Manifest that hands the host the complete connector extension surface in one return value.

*Call graph*: 2 external calls (__init__, __init__).


### Platform surfaces
These manifests register externally visible debugger, shell-client, and web routes that the core can mount at startup.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This is the debugger extension’s identity card and signpost. When the larger UFO system looks for extensions, it needs a simple answer to questions like: “What is this extension called?”, “Which version is it?”, and “What routes should become available if I load it?” Without this file, the debugger code could exist on disk but the host system would not know how to mount it or how users should reach it.

The file defines a fixed name, `debugger`, and a version, `0.1.0`. Its main job is the `manifest` function, which builds a `Manifest` object. A manifest is a small declaration that describes an extension to the host application.

Inside that manifest, the file registers one surface. A surface is a public-facing area of the extension, like a doorway into a specific feature. Here, the surface uses the debugger route table and is identified by `SURFACE_DEBUG`. It also attaches `resolve_operator_workspace` as the identity resolver. In plain terms, that resolver is the check that decides which operator workspace the caller belongs to before the debugger surface is used. This matters because debugger access is sensitive; the extension should only be mounted where the system can confirm the right operator context.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the object the host system reads to learn how to load this extension. It declares the extension name, version, and the single debugger surface with its routes and workspace identity check.

**Data flow**: It starts with constants from this file, plus imported route and surface definitions from the debugger surface module. It creates a `SurfaceSpec`, which describes the debugger-facing entry point and says to use `resolve_operator_workspace` to identify the caller’s operator workspace. It then wraps that surface inside a `Manifest` and returns it to whoever is loading the extension.

**Call relations**: When the extension system asks this module what it provides, this function is the answer. It hands the host a `Manifest` object, built using `Manifest.__init__`, and includes a `SurfaceSpec` built using `SurfaceSpec.__init__` so the host knows which debugger routes to mount and how to authenticate the workspace context.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

A manifest is like a small label and wiring diagram for an extension. Without this file, the core system would not know that the `ufo` extension exists, what it is called, or which route should be mounted so users can reach it.

This manifest declares a single surface, meaning one exposed way for outside users or tools to interact with the extension. Here, that surface is the terminal-facing `ufo` connection used by the shell client. The route list comes from the surface module, and the workspace-identifying function is also supplied there. In plain terms, when someone connects, the system uses `resolve_workspace` to work out which workspace the connection belongs to.

An important detail is what this manifest does not include. It does not define credential slots or extension-specific configuration settings. The comment explains that access is checked using a bearer token, which is a secret token presented by the client, verified against the `UFO_TOKEN_SECRET` environment variable. So installation is enough to mount it, similar to a web route being made available once the service starts.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the core system can read. This is the single place where the `ufo` extension announces its name, version, exposed surface, routes, and workspace-identification function.

**Data flow**: It starts with the file-level constants `NAME` and `VERSION`, plus imported surface details such as `SURFACE_UFO`, `ROUTES`, and `resolve_workspace`. It wraps the surface information into a `SurfaceSpec`, then wraps that into a `Manifest`. The result is a completed manifest object that describes how this extension should be mounted.

**Call relations**: When the extension system asks this module what it provides, `manifest` creates the answer. It calls `SurfaceSpec.__init__` to describe the one exposed `ufo` surface, then calls `Manifest.__init__` to package that surface together with the extension name and version for the core system to use.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup / extension discovery`

This file is the web extension’s manifest, meaning it describes the extension in a form the main UFO system can understand. A manifest is like a sign-up sheet: it says “my name is web, this is my version, and these are the doors I add to the building.” Without this file, the core system would not know how to discover or attach the web extension’s routes.

The important idea here is a “surface.” In this project, a surface is an outside-facing way for people or systems to interact with UFO, such as a web interface. This manifest declares one surface, named by `SURFACE_WEB`. It connects that surface to `ROUTES`, which are the web paths/endpoints the extension exposes. It also provides `resolve_workspace` as the identifying function, which helps the system work out which workspace a web request belongs to.

The file deliberately does not declare credentials or extra configuration. The comment explains why: the web session uses the member’s own session cookie, not a shared bot secret, and installing the extension is enough to mount it. It also does not set up a writeback delivery path; instead, the web extension listens to the central hub through its own streaming route.

#### Function details

##### `manifest`  (lines 14–19)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the object the core system reads to learn how to mount this extension. Someone would use it when the extension is being discovered or loaded.

**Data flow**: It starts with fixed information in this file, such as the extension name and version, and imported web surface details such as the route list and workspace resolver. It wraps those details into a `SurfaceSpec`, which describes the web-facing surface, then places that inside a `Manifest`. The result is a complete manifest object that the core can use.

**Call relations**: During extension loading, the core calls `manifest` to ask this extension what it provides. Inside that call, it creates a `SurfaceSpec` for the web surface and then creates the larger `Manifest` object that carries that surface back to the core.

*Call graph*: 2 external calls (__init__, __init__).


### Event and task services
These manifests expose reactive page-alert tools and recurring job machinery for scheduled and self-improvement work.

### `extensions/page_alerts/ufo_ext_page_alerts/manifest.py`

`config` · `startup`

This file does not perform the page-watching work itself. Instead, it declares what the extension can do, much like a restaurant menu tells you what can be ordered without cooking the meal. The platform needs this declaration so it can expose the right tools in chat and connect the right event to the right code.

The extension is named `page_alerts` and has version `0.1.0`. Its `manifest` function builds a `Manifest`, which is the package of information the UFO platform reads when loading the extension. Inside that package are three chat tools: one to start watching synced workspace pages for a topic, one to list existing watches, and one to cancel a watch. Each tool has a plain description, an input model that defines what information the user must provide, and a handler function that actually does the work.

The file also declares one hook: when a `page_change` event happens, the platform should call `on_page_change`. A hook is a background connection point: instead of waiting for a user to ask, the extension can react when the system notices a changed page. Without this file, the platform would not know these tools or the page-change reaction exist.

#### Function details

##### `manifest`  (lines 21–50)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration that the platform reads when loading page alerts. It says the extension's name and version, lists the chat tools users can call, and connects page-change events to the alerting code.

**Data flow**: It starts with fixed local information: the extension name, version, tool descriptions, input models, and handler functions imported from the alerts module. It wraps each chat action in a `ToolDef`, wraps the page-change reaction in a `HookSpec`, and places them all into a `Manifest`. The result is a single manifest object that the platform can use to register the extension.

**Call relations**: When the extension is loaded, the platform asks for this manifest so it can wire the extension into the wider system. This function creates `ToolDef` objects for the user-facing chat tools, creates a `HookSpec` for the background `page_change` event, and hands all of that to `Manifest` so the platform has one complete registration record.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `startup/config load, then on scheduled job ticks`

This is the extension’s front desk. When the larger UFO system starts or scans extensions, it asks this file for a manifest, which is a clear declaration of what the extension provides. The file names the extension, gives it a version, and registers the pieces that make scheduled tasks work.

The main idea is simple: users or agents can create scheduled tasks, and a background runner periodically checks which workspaces have tasks due. A “workspace” is an isolated area of data and activity. The runner is not triggered by each individual task row directly; instead, it wakes up on a fixed clock schedule and looks for due work. This is like a building caretaker walking the halls every hour rather than every office ringing a bell separately.

The manifest also registers a durable pause tool, which lets an agent pause and resume later in a way the system can remember, and a scheduled-task object type, which lets the generic object commands create, update, list, and delete scheduled tasks. Finally, it points the system at a skill folder named “task-scheduling” and declares that this extension depends on “memory_search” being available.

#### Function details

##### `_run`  (lines 28–29)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job entry function that actually starts the scheduled-task runner when the clock-based job fires. It exists so the manifest can give the job system one simple function to call.

**Data flow**: It receives an ExtensionContext, which is the extension’s access pass to workspace-specific services and stored data. It uses that context to create a ScheduledTaskRunner, then asks the runner to do its work. Nothing is returned; the useful effect is that due scheduled tasks may be found and invoked.

**Call relations**: The manifest gives this function to the JobSpec as the job handler. When the job system decides it is time to run the scheduled-task runner, it calls _run, and _run hands control to ScheduledTaskRunner so the real task-checking work can happen.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–48)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension declaration that the host system reads. It explains which tools, object types, background jobs, skills, and dependencies belong to the scheduled-tasks extension.

**Data flow**: It starts from fixed constants such as the extension name, version, runner job name, clock schedule, and skill folder. It creates a JobSpec for the recurring runner, uses due_task_workspaces to choose which workspaces are candidates for that job, creates SkillSpec entries for the skill folders, and packages everything into a Manifest. The result is a complete description the host can load.

**Call relations**: The extension loader calls manifest when it is discovering what this extension offers. Inside, manifest builds the JobSpec that points back to _run, asks due_task_workspaces for the workspace-selection rule, creates SkillSpec records for the declared skills, and finally returns a Manifest that the rest of the system uses to register the extension.

*Call graph*: 4 external calls (__init__, __init__, __init__, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup registration, then scheduled job execution`

This file is the extension’s front desk. When the larger UFO system loads extensions, it asks each one for a manifest, which is a small declaration of its name, version, and scheduled work. Here, the self-improvement extension declares a cron job. A cron job is work that runs on a clock schedule, like a reminder that rings at a set time, rather than work triggered by a user request or a database change.

The scheduled job is called `eval_cron` and is set to run at midnight according to the schedule string. When the clock fires, the system calls `_tick`. That function first checks that a model is available, because this extension depends on a language model to propose prompt changes, replay behavior, and judge results. If no model has been connected, it stops with a clear error instead of silently doing nothing.

If model access exists, `_tick` wraps it in `ModelAccessLeg`, then builds the three main parts of the improvement run: a proposer that suggests prompt candidates, an evaluator that replays and grades them, and an `ImproveCron` object that coordinates the whole pass. The manifest also limits the job to trajectory workspaces, meaning it runs in the kinds of work areas where recorded behavior can be evaluated.

#### Function details

##### `_tick`  (lines 21–29)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the actual scheduled action for the self-improvement job. It prepares model-powered proposing, replaying, and judging, then starts one improvement run.

**Data flow**: It receives an `ExtensionContext`, which is the job’s bundle of runtime tools and settings. It reads `ctx.model`; if no model is present, it raises an error because the improvement process cannot work without one. If a model is present, it wraps that model access, gives the wrapper to the prompt proposer and evaluator, builds the cron runner, and waits for the run to finish. It does not return a value; its effect is the completed improvement pass or a clear failure.

**Call relations**: The scheduler calls `_tick` when the manifest’s cron job fires. Inside that moment, `_tick` creates the model access wrapper, uses it to create `PromptProposer` and `CandidateEvaluation`, then hands both to `ImproveCron`, which takes over the real improvement workflow.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 32–44)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It says the extension’s name and version, and registers the scheduled evaluation job that should call `_tick`.

**Data flow**: It takes no input. It gathers the fixed constants in this file, asks for the eligible trajectory workspaces, builds a `JobSpec` for the daily evaluation cron, and places that job inside a `Manifest`. The returned manifest is what the host system reads to know how to load and schedule this extension.

**Call relations**: The extension loader calls `manifest` during startup or discovery. `manifest` creates the job description and points that job at `_tick`, so later, when the scheduler reaches the configured time and workspace candidates, `_tick` becomes the function that actually runs.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### Hub backend selection
This manifest registers Redis as a selectable live-frame hub backend for deployments that need shared runtime state.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup/config load`

This is the extension’s sign-up sheet. The core application has a “hub” seam: a replaceable part that distributes live frames. By default, that may happen inside one running server. This file registers an alternative hub that uses Redis Streams, a Redis feature for passing ordered messages between processes. That matters when there is more than one server instance, because each instance needs to see the same live-frame traffic.

The file gives the extension a name, a version, and the backend label `redis`. When the application reads extension manifests during startup, this manifest says: “If the config asks for the Redis hub backend, call this builder function.”

The builder is deliberately strict. If someone selects the Redis backend but forgets to provide `hub.url`, it raises an error immediately. This is like refusing to start a delivery route without the depot address, instead of waiting until the first package is already in hand. If the URL is present, the builder creates a `RedisStreamHub`, which is the actual object that talks to Redis and distributes frames.

#### Function details

##### `_build_hub`  (lines 17–22)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: Creates the Redis-backed hub object from a Redis connection URL. It also protects the system from a half-configured setup by failing immediately if the URL is missing.

**Data flow**: It receives a URL value, which may be a string or may be absent. If the URL is absent, it stops with a clear runtime error explaining that `hub.url` is required. If the URL is present, it passes that URL into `RedisStreamHub` and returns the newly created hub object.

**Call relations**: This function is handed to the manifest as the build step for the `redis` hub backend. When the core system later chooses that backend, it calls this builder, and the builder hands off to `RedisStreamHub.__init__` to create the real Redis-based hub.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 25–30)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that advertises this Redis hub backend to the main application. The manifest is how the extension says what it provides and how the core should construct it.

**Data flow**: It reads the module’s fixed name, version, and backend label. It wraps the backend label and `_build_hub` function into a `HubSpec`, then wraps that specification into a `Manifest`, which it returns to the extension-loading system.

**Call relations**: This is the function the application’s extension loader calls during startup to discover what the extension offers. It creates a `HubSpec` to describe the Redis backend, then creates a `Manifest` that carries that spec back to the core system.

*Call graph*: 2 external calls (__init__, __init__).

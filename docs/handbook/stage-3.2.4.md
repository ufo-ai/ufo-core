# Platform, scheduling, and operations extension manifests  `stage-3.2.4`

This stage is shared start-up support for optional parts of the UFO system. Each file is a manifest, which is like a registration form. When the host application starts, these manifests tell it what extra surfaces, tools, jobs, and backends are available, so the code can be mounted and run at the right time.

The debugger manifest adds a user-facing debug area and a tool for reporting workspace problems to operators. The monitors manifest registers monitor objects, a monitor action, and a clock-driven job that checks probes when they are due. The Redis hub manifest offers Redis, a separate data service, as a hub and terminal transport option. The report digest manifest adds report objects, a writing skill, a scheduled digest writer, and an admin rebuild tool. The scheduled-tasks manifest registers tasks that can wait, repeat, and be used by agents. The self-improvement manifest schedules evaluation work. The UFO manifest exposes the terminal stream used by the shell client. The web manifest adds browser features, chat tools, feature flags, and background jobs.

## Files in this stage

### Debugging and monitoring
Registers operator-facing diagnostics and monitor probes that expose problems and run due checks.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This is the debugger extension's front door. When the main UFO system discovers this extension, it needs a simple summary of what the extension provides and where it should be connected. This file supplies that summary as a manifest, which is like a shipping label for the extension: it says what the package is called, what version it is, what tools are inside, and what screen or web surface should be mounted.

The extension exposes one tool, `report_problem`, which is imported as `REPORT_PROBLEM_TOOL_DEF`. That tool is used to send a workspace fault to operators. It also exposes one surface, named by `SURFACE_DEBUG`, with routes from `ROUTES`. A surface is a mounted user interface or API area. This surface is protected by `resolve_operator_workspace`, an identity resolver that decides whether the requester belongs to the operator workspace. In plain terms, the debug area is not meant for ordinary users; it is only mounted where the system can confirm an operator context.

Without this file, the debugger extension could still contain useful code, but the host system would not know how to discover it, what tool to register, or what routes to make available.

#### Function details

##### `manifest`  (lines 16–24)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension's manifest, which is the object the host system reads to learn what this extension contributes. Someone would use it when registering or loading the extension.

**Data flow**: It starts with fixed values from this file and imported definitions: the extension name, version, report-problem tool definition, debug surface name, debug routes, and the operator identity resolver. It puts those pieces into a `SurfaceSpec`, then places that surface and the tool into a `Manifest`. The result is a complete description of the debugger extension; it does not modify outside state itself.

**Call relations**: When the extension system asks this module what it provides, `manifest` assembles the answer. It calls `SurfaceSpec.__init__` to describe the debug surface and its access check, then calls `Manifest.__init__` to package the extension name, version, tool, and surface into the object the wider system can register.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `startup registration, then recurring scheduled execution`

This file is the extension’s sign-up sheet. When the UFO system loads extensions, it asks each one for a manifest, which is a plain declaration of what the extension contributes. Here, the monitors extension declares its name and version, the monitor tool users or agents can invoke, the monitor object it stores, and a recurring job that runs once per minute.

A monitor is like a recurring alarm with a test attached: on a schedule, it runs a shell probe inside a conversation’s sandbox and records whether the watched condition is changing. The important design choice here is that the runner is time-based, not triggered by every database row change. The job also uses a candidate selector, `due_monitor_workspaces()`, so the system only wakes up workspaces that actually have monitor work ready. That keeps idle workspaces cheap.

The small `_probe` function is the job’s bridge into the real monitor-running code. The `manifest()` function packages everything into a `Manifest` object so the host application can register the extension during startup.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function the scheduled job runs when it is time to check monitors. It creates a `MonitorRunner`, which is the component that knows how to find and probe due monitors, and tells it to run.

**Data flow**: It receives an `ExtensionContext`, which is the extension’s access pass to system services and workspace-specific state. It uses that context to build a `MonitorRunner`, then the runner performs the monitor checks. The function itself returns nothing; its effect is that due monitor probes are executed and their results are recorded by the runner.

**Call relations**: The job declared in `manifest()` points to `_probe` as its handler. When the scheduler fires for a workspace with due monitor work, it calls `_probe`; `_probe` then hands control to `MonitorRunner` by constructing it with the current extension context.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the monitors extension to the UFO host system. It declares the extension’s tool, object type, and recurring monitor runner job in one package.

**Data flow**: It starts from fixed constants such as the extension name, version, job name, and schedule. It asks `due_monitor_workspaces()` for the rule that identifies which workspaces have monitor work waiting, builds a `JobSpec` for the recurring runner, and places that job together with the monitor tool and object into a `Manifest`. The returned `Manifest` is what the host reads to register the extension.

**Call relations**: During extension loading, the host calls `manifest()` to learn what this extension contributes. Inside that declaration, `manifest()` creates a `JobSpec` whose handler is `_probe`, and it uses `due_monitor_workspaces()` so the scheduler only calls `_probe` for workspaces that actually need monitor probing.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### Redis transport backend
Registers Redis-backed hub and terminal transport options for platform connectivity.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup`

This file exists so the core system can discover and use the Redis version of two important services. The first is the hub, which moves live frames between running server instances. The second is the terminal transport, which helps a user’s connected terminal receive work even when the request is accepted by a different server pod. In plain terms, Redis acts like a shared message board that all server instances can read from and write to, instead of each instance only knowing about its own local memory.

The file defines a small manifest, which is a description of what this extension offers. It registers one hub backend called "redis" and one terminal transport backend also called "redis". When the application configuration asks for either of these, the core system calls the matching builder function in this file.

Both builders require the same Redis address, `hub.url`. If that address is missing, they fail immediately with a clear error. This is important because a missing Redis URL would otherwise cause a confusing failure later, when traffic starts flowing. The file does not itself move frames or terminals; it connects names in configuration to the real Redis implementations in `stream_hub` and `stream_terminal`.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed hub when the user selects the Redis hub backend. It makes sure the Redis address is present before building anything, so configuration mistakes are caught early.

**Data flow**: It receives a Redis URL, or `None` if no URL was configured. If the URL is missing, it raises a clear error explaining that `hub.url` is required. If the URL is present, it passes that address into `RedisStreamHub` and returns the new hub object that the rest of the system can use.

**Call relations**: This function is handed to `HubSpec` by `manifest`, so the core system can call it later during startup when `hub.backend` is set to `redis`. Its main handoff is to `RedisStreamHub.__init__`, which builds the actual Redis Streams hub that does the live frame distribution.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport when the user selects the Redis terminal backend. It also connects that transport to the system’s blob store, which is where larger terminal-related payloads can be kept.

**Data flow**: It receives a Redis URL and a `BlobStore`, which is a storage service for larger pieces of data. If the URL is missing, it raises a clear error saying `hub.url` is required. If the URL is present, it creates and returns a `RedisTerminals` object using both the Redis address and the blob store.

**Call relations**: This function is registered by `manifest` inside a `TerminalTransportSpec`, so the core system can call it during startup when `terminal.backend` is set to `redis`. It delegates the real terminal transport setup to `RedisTerminals.__init__`, which provides the cross-pod terminal routing behavior.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension manifest that the core system reads to discover what this Redis extension provides. It packages the extension name, version, hub option, and terminal transport option into one object.

**Data flow**: It reads the constants in this file, such as the extension name, version, and backend names. It builds a `HubSpec` that points to `_build_hub`, builds a `TerminalTransportSpec` that points to `_build_terminal`, and returns a `Manifest` containing both. The result is a compact description the host application can use during setup.

**Call relations**: This is the main discovery point for the file. When the extension is loaded, the core system calls `manifest` to learn which backend names are available. `manifest` creates the specification objects, and those specifications later lead the core system back to `_build_hub` or `_build_terminal` if the user selected the Redis options.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Scheduled automation
Registers scheduled report generation, recurring task execution, and self-improvement evaluation jobs.

### `extensions/report_digest/ufo_ext_report_digest/manifest.py`

`config` · `startup registration, scheduled background runs, and admin tool use`

The report-digest extension creates short digest entries for published reports, so a portal can show a useful summary feed instead of making readers open every report. This file connects that feature to the larger system. Think of it like a notice pinned at the front desk: it says what the extension is called, what it can do, who may ask it to do extra work, and how often its background work should run.

The file declares one shared writing standard as a skill. Both a member’s agent and the background digest job can read that same standard, so report summaries are written consistently. It also defines a scheduled job that wakes up every ten minutes and writes missing digest entries for workspaces that need them. The job does not run if the runtime has not provided the model or blob access it needs, because it must both read published reports and ask a language model to write entries.

The file also exposes an admin-only tool named rebuild_report_digest. It does not rewrite entries immediately. Instead, it marks recent reports as due for rewriting, and the normal scheduled job later processes them in batches. This protects the system from doing a large burst of expensive work all at once.

#### Function details

##### `write_digests`  (lines 38–43)

```
async def write_digests(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job’s entry point. It checks that the runtime gave the extension both a background language model and access to stored member-context blobs, then starts the digest writer.

**Data flow**: It receives an ExtensionContext, which is the bundle of services and workspace information the extension is running with. If the context is missing the model or blob reader, it stops with a clear error because digest writing cannot work without them. Otherwise it builds a DigestWriter with the context, model, and blob access, then runs it; the result is that due report digest entries may be written to storage by the writer.

**Call relations**: The manifest registers this function as the handler for the recurring report_digest job. When the scheduler decides a workspace has undigested reports, it calls this function, and this function hands the real report-reading and entry-writing work to DigestWriter.

*Call graph*: 1 external calls (__init__).


##### `rebuild_report_digest_handler`  (lines 50–73)

```
async def rebuild_report_digest_handler(ctx: ToolContext, args: RebuildReportDigestInput) -> ToolResult
```

**Purpose**: This is the admin tool action for asking the system to write recent report digest entries again. It is meant for cases where the feed entries read badly and the whole recent feed should be regenerated.

**Data flow**: It receives a ToolContext, which describes the tool request and speaker, plus an empty validated input object. It first makes sure the extension context is present, then asks whether the speaker is a workspace admin. Non-admin speakers are rejected. For admins, it runs DigestRebuild, which marks eligible reports from the last seven days as due again. It returns a ToolResult containing either a message that nothing needed rebuilding or a message saying how many reports were queued for rewriting.

**Call relations**: The manifest wires this function into the rebuild_report_digest tool. When a user activates that tool, this handler performs the permission check, asks DigestRebuild to mark reports as due, and returns human-readable text through TextContent and ToolResult. It deliberately does not call the digest writer directly; the scheduled job later performs the actual rewriting.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 76–114)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension manifest, which is the structured description the UFO runtime reads to install and run the extension. It names the extension and lists its skill, tool, job, object type, and data-access needs.

**Data flow**: It takes no input. It creates a Manifest containing constants such as the extension name, version, job name, and schedule. Inside that manifest it describes the writing skill location, the admin rebuild tool and its button-style presentation, the scheduled digest-writing job and its workspace candidates, the report object, and the fact that member-context reading is required. The output is a Manifest object used by the runtime.

**Call relations**: The runtime calls this function when loading the extension. The objects it creates tell the runtime how to expose the rebuild tool, how to schedule write_digests for workspaces selected by owner_candidates and undigested_workspaces, and which report object and skill belong to this extension.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `startup and scheduled job registration`

This file is the extension’s “label on the box.” It declares what the scheduled-tasks extension is called, what version it is, and which features it contributes to the larger application. The key idea is that scheduled work does not run by magic: the host system needs a manifest, which is a formal declaration of tools, object types, background jobs, and skills that should be loaded.

The file registers two recurring jobs. One looks for scheduled tasks that are due to run. The other looks for paused conversations that are due to resume. They run on the same clock schedule, but they are separate jobs because they are separate failure areas. If one gets stuck, the other should still be able to do its work. This is like having two alarm clocks: one for meetings and one for waking someone from a nap, so a problem with one alarm does not silence the other.

The jobs are also careful about where they run. Each job asks for only the workspaces that actually have due work, so workspaces with nothing waiting do not waste dispatcher time. The manifest also loads a task-scheduling skill and declares that this extension depends on memory search and uses a conversation slot for automation-related state.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job entry function for running due scheduled tasks. The scheduler calls it when the scheduled-task runner job fires, and it delegates the real work to `ScheduledTaskRunner`.

**Data flow**: It receives an `ExtensionContext`, which is the extension’s access point to the host system and workspace services. It creates a `ScheduledTaskRunner` using that context, then tells the runner to run. It does not return any meaningful value; its effect is that due scheduled tasks may be picked up and invoked.

**Call relations**: The manifest registers this function as the handler for the scheduled-task runner job. When the host job system fires that job, `_run` acts as the doorway into the scheduled-task runner rather than doing the task-running logic itself.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job entry function for resuming conversations that were paused until a later time. The scheduler calls it when the pause runner job fires, and it delegates the real work to `PauseRunner`.

**Data flow**: It receives an `ExtensionContext` with the services and workspace information the extension needs. It creates a `PauseRunner` with that context, then tells the runner to run. It returns no useful value; its effect is that due pauses may be resumed.

**Call relations**: The manifest registers this function as the handler for the pause runner job. When the host job system fires that job, `_resume` hands control to `PauseRunner`, keeping the manifest file focused on wiring rather than pause-resume details.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the official declaration of everything this extension contributes to the system. The host uses it to discover the extension’s tools, object type, recurring jobs, skills, dependency, and conversation slot.

**Data flow**: It uses constants from this file and imported extension pieces to assemble a `Manifest`. It includes the pause-and-wait tool, the scheduled-task object type, two recurring `JobSpec` entries, the task-scheduling skill path, a dependency on `memory_search`, and the automation conversation slot. The result is a complete manifest object that the host can load.

**Call relations**: This is the main export the host cares about when loading the extension. While building the manifest, it asks the schedule and pause modules for candidate workspace selectors, creates job specifications around `_run` and `_resume`, creates skill specifications for the skill folders, and hands the finished declaration back to the host system.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup and scheduled job execution`

This file is the extension’s sign-up sheet. Without it, the main system would not know the self-improvement extension’s name, version, or scheduled job, so the extension would never be run automatically.

The job is a cron job, meaning it runs because the clock reaches a certain time, not because someone created a proposal or wrote data to storage. When the job runs, `_tick` checks that model access is available. A model here means the language model service the extension needs in order to propose, replay, and judge prompt changes. If no model is connected, the job stops with a clear error instead of silently doing nothing.

If model access is present, the file wraps it in `ModelAccessLeg`, then builds the three main parts of the improvement loop: a proposer that suggests prompt changes, an evaluator that replays past conversations and judges candidates, and an `ImproveCron` runner that ties the process together.

The `manifest` function declares the job itself. It names the job, gives it a schedule, points it at `_tick`, says it should consider trajectory workspaces as candidates, and marks that it needs the deploy model. That last detail matters because replaying an archived transcript can be large, so the job intentionally uses the same model capacity expected in deployment rather than a cheaper background-job model.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the body of the scheduled self-improvement job. It builds the pieces needed to propose prompt changes, replay past examples, judge the results, and run one improvement cycle.

**Data flow**: It receives an `ExtensionContext`, which is the extension’s doorway into shared services such as model access. It first reads `ctx.model`; if no model is available, it raises an error. If a model is present, it wraps that model in `ModelAccessLeg`, uses that wrapper to create a `PromptProposer` and a `CandidateEvaluation`, then gives both to `ImproveCron` and waits for the cron run to finish. It returns no value, but it may cause the self-improvement workflow to create proposals or evaluations through the objects it runs.

**Call relations**: The scheduled job declared by `manifest` points to this function as its handler. When the clock fires, `_tick` assembles the runtime parts: it calls `ModelAccessLeg.__init__` to prepare model use, `PromptProposer.__init__` to create the proposal maker, `CandidateEvaluation.__init__` to create the replay-and-judge evaluator, and `ImproveCron.__init__` to package the whole cycle before running it.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the self-improvement extension to the host system. It gives the system the extension name, version, and the scheduled job it should install.

**Data flow**: It takes no inputs. It uses constants from this file for the extension name, version, job name, and schedule. It asks `trajectory_workspaces()` which workspaces should be considered as candidates, builds a `JobSpec` that points to `_tick`, and returns a `Manifest` containing that job. The returned manifest is the object the wider system reads to register the extension.

**Call relations**: The host system calls `manifest` when discovering or loading extensions. Inside, it calls `trajectory_workspaces()` to define the job’s candidate scope, `JobSpec.__init__` to describe the scheduled evaluation job, and `Manifest.__init__` to package the extension declaration that the host can install.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### User-facing surfaces
Registers terminal and web surfaces, feature flags, chat tools, and background work exposed to users.

### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This is the extension’s signpost. When the larger UFO system loads installed extensions, it needs a small, standard description of each one: its name, its version, and what user-facing entry points it provides. This file supplies that description for the `ufo` extension.

The extension exposes one “surface,” meaning one live area where outside clients can interact with the system. Here, that surface is the terminal wire used by the `ufo` shell client. The manifest points the host to the routes that make up that surface and to `resolve_workspace`, the function used to identify which workspace a connecting user belongs to.

A notable detail is what this file does not define. It does not declare separate credential slots or configuration switches. According to the module comment, authentication is checked against the environment variable `UFO_TOKEN_SECRET`, and simply installing the extension means it is mounted. So this file is less like a settings panel and more like a name tag and map: “I am the UFO extension, version 0.1.0, and this is the live route I provide.”

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the standard manifest object for this extension. The host uses this object to learn the extension’s name, version, and the UFO surface it should mount.

**Data flow**: It starts with the file’s constants, `NAME` and `VERSION`, and imports the surface name, route list, and workspace-identification function from the surface module. It wraps those surface details in a `SurfaceSpec`, then wraps that in a `Manifest`. The result is a complete description of the extension that the rest of the system can read.

**Call relations**: During extension loading, the host calls `manifest` to ask this file what the extension provides. `manifest` creates a `SurfaceSpec` for the UFO terminal surface and hands it to `Manifest`, so the host can later mount the declared routes and use `resolve_workspace` when a client connects.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup and extension registration`

This file is not the portal itself. Instead, it tells the main application what the portal is and what pieces come with it. Think of it like a stall applying to join a market: it lists its name, what entrance it uses, what services it offers, and what chores it will do after hours.

The file gives the extension a name and version, then lists every feature flag the portal may read. A feature flag is an on/off switch controlled outside the code, often per environment. Here, those switches decide whether parts of the portal appear, such as Code, Issues, Metrics, Memory, Skills, or the newer lanes shell.

The `manifest()` function builds the full `Manifest` object. That manifest says the web extension connects to member accounts, exposes admin-only web access tools, contributes conversation slots for changes and artifacts, and owns the browser home route. It also registers two scheduled jobs. One job gives untitled conversations a readable title based on their opening exchange. The other seeds homepages for agent workspaces that do not have one yet.

Without this file, the core system would not know that the web portal exists, which routes belong to it, which flags are valid, or which background jobs should run for it.

#### Function details

##### `manifest`  (lines 67–93)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s official manifest, which is the object the core system reads to discover what this extension provides. Someone would use it when loading extensions so the web portal can be mounted, its tools can be exposed, and its background jobs can be scheduled.

**Data flow**: It starts with constants imported from the web extension and the shared SDK: names, routes, feature flags, tool definitions, job names, schedules, and handler functions. It packages those into a `Manifest`: the extension name and version, whether member accounts are connected, which tools and conversation slots exist, which browser surface owns the home route, which scheduled jobs should run, and which feature flags are valid. The result is a single manifest object; it does not directly run the portal or the jobs itself.

**Call relations**: When the extension system asks this module what the web extension contains, this function assembles the answer. While doing that, it creates a `SurfaceSpec` for the browser-facing portal route, creates two `JobSpec` entries for title summarizing and homepage seeding, asks the jobs SDK for candidate workspaces that need those jobs, and finally hands all of that to `Manifest` so the core system can register the extension in one piece.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).

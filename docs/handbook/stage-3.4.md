# Assistant, Web, and Workflow Extension Manifests  `stage-3.4`

This stage is the system’s set of extension “registration cards.” During startup, the host reads these manifest files to learn what extra abilities are available, how to load them, and when to run background work. The browser, coding, research, debugger, sites, and UFO manifests add user-facing or assistant-facing capabilities: browser delegation, coding helpers with GitHub access, research tools and shared conversation data, a debugger tool and web surface, website-building screens and safety checks, and the UFO extension’s own user surface. The web manifest connects the portal side, declaring routes, permissions, feature flags, side panels, and jobs. The monitors, scheduled-tasks, and self-improvement manifests add ongoing behind-the-scenes work: checking due monitors, running scheduled tasks, and periodically proposing, replaying, and grading improvements. The objectives manifest helps steer active conversations by reminding an agent about unfinished objectives at the start of each turn. Together, these files act like labeled plugs on a power strip: each tells the core system what it can provide, and the core decides how to make those pieces available.

## Files in this stage

### Assistant capabilities
These manifests register specialized assistant-facing subagents, tools, prompts, skills, and debugging or research capabilities.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup`

This file is like the label on a plug-in box: it says what is inside, what it needs, and how the rest of the system should use it. The browser extension gives UFO a way to do web automation, but it does not simply hand every browser-control tool to the main agent. Instead, it defines a dedicated browser subagent, which is a specialized helper agent for browser tasks.

The file imports three main ingredients from nearby modules: the raw browser tools, the delegation tools used by the main agent to ask for browser work, and the browser subagent profile. It also reads a Markdown prompt section from disk. That prompt section becomes part of the main agent’s instructions, explaining when and how to delegate browsing.

The key function, `manifest`, packages these pieces into a `Manifest`. A manifest is the extension’s formal declaration to the host system. It includes the extension name and version, the tools to expose, the subagent to register, the prompt section to add, and a requirement named `cdp_providers`, which means this extension depends on browser-control providers based on CDP, the Chrome DevTools Protocol used to drive browsers programmatically.

Without this file, the browser extension’s pieces might exist in code, but the UFO runtime would not know to load them, advertise them, or connect them into agent turns.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension’s manifest, which is the system-readable description of what this extension offers. It is used when the UFO runtime loads extensions and needs to know which tools, subagents, prompts, and dependencies belong to the browser pack.

**Data flow**: It starts with constants and imported objects: the extension name and version, browser tools, delegation tools, the browser subagent profile, a prompt section read from a Markdown file, and the required `cdp_providers` capability. It combines those into a `Manifest` object, wrapping the prompt text in a `PromptSection`. The result is a single manifest object that the wider system can register and use.

**Call relations**: When the extension is being loaded, the system calls `manifest` to ask, “What do you provide?” This function creates the prompt section and the manifest, then hands that completed declaration back to the loader so the browser tools, delegation tools, and browser subagent can become available in the right places.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup`

This file is like the packing list and instruction card for the coding extension. It does not edit code itself. Instead, it tells the host system what this extension offers and what it needs in order to work.

The main offering is a “coding” child agent: a worker the main agent can send into a repository to inspect files, make edits, run commands, and report back. The file defines what input that child receives, what shape its answer should have, which tools it may use, and which prompt teaches it how to behave. It also defines a stronger fallback worker called “fable_escalation”, used when earlier coding attempts fail and the task needs one more try on a pinned, stronger model.

GitHub access is another important part. Repository work often needs private clone, push, or GitHub API calls. This file declares the credential slots for those tokens, including support for a GitHub App installation and a fallback personal token. The actual secret values are not put directly into the sandbox; instead, the system uses sentinel placeholders and swaps in short-lived credentials at the boundary.

Finally, the manifest exposes a setup tool and route so a workspace admin can connect GitHub. Without this file, the platform would not know that the coding pack exists, how to start its child agents, what skills to load, or how to safely provide GitHub credentials.

#### Function details

##### `github_app_id`  (lines 114–127)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has been configured with a complete GitHub App registration. It protects users from a half-configured setup, where the system might appear to support GitHub App access but silently fall back or fail later.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If none are present, it returns nothing, meaning GitHub App token minting is not enabled. If some are present but others are missing, it raises an error immediately. If all are present, it returns the GitHub App ID.

**Call relations**: This function is used while the module is being loaded to decide whether GitHub App token sources should be created. Its result controls whether the later credential definitions can mint App-based tokens or must rely on the fallback credential path.


##### `manifest`  (lines 197–222)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the object the host system reads to learn what the coding extension provides. It gathers the extension name, version, subagents, skills, credentials, setup tool, and web route into one declaration.

**Data flow**: It starts from constants and objects already defined in the file: prompts, profiles, skill paths, credential slots, and GitHub setup handlers. It packages them into a Manifest object. The result is a structured description of the extension that the host system can register and use.

**Call relations**: The host system calls this function when loading the extension. Inside it, the function creates SkillSpec entries for the coding skill, a ToolDef for the GitHub connection tool, and a RouteSpec for the GitHub installation callback, then hands all of that to Manifest so the extension can be registered as a complete unit.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup / extension discovery`

This file exists so the main UFO system can discover and attach the debugger extension in a controlled way. Think of it like a small sign-in sheet for a plug-in: it says, “I am the debugger extension, here is my version, here is the tool I provide, and here is the screen operators may open.” Without this manifest, the extension’s problem-reporting tool and debugger surface would not be advertised to the rest of the system.

The file defines a name and version, then builds a Manifest object. A manifest is a structured description of an extension. It includes one tool, `REPORT_PROBLEM_TOOL_DEF`, which is the action used to report a workspace problem to operators. It also includes one surface, a web-facing area named by `SURFACE_DEBUG`, with routes supplied by `ROUTES`.

The important safety detail is the `identify=resolve_operator_workspace` setting on the surface. In plain terms, that function is used to decide whether the current caller can be matched to an operator workspace before the debugger surface is made available. So this file is not just listing features; it also helps connect the debugger UI to the right authentication and workspace boundary.

#### Function details

##### `manifest`  (lines 16–24)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the package of information the host system needs to load the extension. Someone would use this when the system is discovering extensions and needs to know what this one contributes.

**Data flow**: It starts with the module’s fixed name and version, plus imported definitions for the report-problem tool and debugger surface routes. It packages those into a Manifest object, including a SurfaceSpec that says what the debugger surface is called, which routes it serves, and how callers are identified. The result is a complete Manifest object returned to the extension loader; it does not change anything else directly.

**Call relations**: During extension loading, the host asks this function for the debugger extension’s description. The function creates the overall Manifest and, inside it, creates a SurfaceSpec for the debugger web surface so the host knows both the tool to register and the surface to mount.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file does not perform web research itself. Instead, it is like a packing list for the research feature. When the system starts or loads extensions, it needs to know what this research pack contributes and what it depends on. This file answers that in one place.

It names the extension, sets its version, reads a web-research prompt section from a Markdown file, points to two skill folders, and gathers together the research tools and research-focused subagent profiles. A subagent profile is a preset description of a helper agent the main agent can delegate work to. A prompt section is reusable instruction text that gets added to the agent's prompt. A conversation slot is shared state attached to the conversation; here it includes a place for research sources.

The important dependency is `search_providers`. The research pack does not own search credentials or choose the search backend itself. It simply says, “I require some search provider to exist.” That means a broken deployment fails early at startup instead of waiting until the first web search request.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension's manifest, which is the object the host system reads to discover what this extension provides. Someone would use it when loading the extension so the research tools, subagents, prompt section, skills, and required search backend are registered together.

**Data flow**: It starts with constants and imported pieces: the extension name and version, research tools, subagent profiles, the web prompt text already read from disk, skill folder paths, and the sources conversation slot. It wraps the web prompt text in a `PromptSection`, wraps each skill folder in a `SkillSpec`, then puts everything into a `Manifest`. The result is a single structured object that describes the whole research pack and states that `search_providers` must be available.

**Call relations**: When the extension system wants to know what this pack contributes, it calls `manifest`. Inside, this function creates the small descriptor objects it needs by calling `PromptSection.__init__` for the web prompt, `SkillSpec.__init__` for each research skill, and `Manifest.__init__` to assemble the final declaration that the wider system can load.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Workflow automation
These manifests wire in objectives, monitors, scheduled work, recurring jobs, object types, and self-improvement evaluation loops.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `startup registration and recurring background runs`

This file is the extension’s front door. It does not contain the monitor logic itself; instead, it declares what pieces the extension contributes to the larger system. Think of it like a registration form handed to the host app at startup: it names the extension, gives its version, lists the tool users can call, lists the stored object type it owns, and sets up a clock-based background job.

The background job is called the monitor runner. It is scheduled with a cron-style schedule, meaning a compact text pattern that says when it should run. Here the pattern makes it run once per minute. When the job fires, it does not blindly scan everything. It uses `due_monitor_workspaces()` to name only the workspaces that currently have monitors ready to be checked, so idle workspaces do not create unnecessary work.

The small `_probe` function is the bridge from the job system into the real monitor runner. It receives an extension context, which is the bundle of services and information the extension needs, creates a `MonitorRunner`, and asks it to run. The actual probing and state updates live elsewhere; this file simply wires those pieces into the platform.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job callback that runs whenever the monitor job fires. It starts the monitor runner for the current extension context so due monitors can be probed.

**Data flow**: It receives an `ExtensionContext`, which is the extension’s access point to workspace data and platform services. It creates a `MonitorRunner` using that context, then awaits its `run` method. It returns nothing, but the runner may perform monitor checks and update stored monitor state elsewhere.

**Call relations**: The job declared in `manifest` points to `_probe` as its handler. When the scheduler decides it is time to check monitors, it calls `_probe`, and `_probe` hands control to `MonitorRunner` to do the real work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the official declaration for the monitors extension. The host application uses it to discover the extension’s name, version, tool, object type, and recurring job.

**Data flow**: It starts from fixed constants such as the extension name, version, job name, and schedule. It calls `due_monitor_workspaces()` to define which workspaces should be considered job candidates, creates a `JobSpec` for the recurring monitor runner, and wraps everything in a `Manifest`. The result is returned to the host so the extension can be registered.

**Call relations**: The host application calls `manifest` while loading extensions. `manifest` wires together the monitor tool, monitor object kind, and job specification; the job specification later calls `_probe` whenever a workspace has monitor work due.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup and user prompt submission`

This file is the front door for the objectives extension. Objectives are durable plans for work that may take more than one agent turn, especially work that can be delegated, paused, retried, or checked later. Without this file, the system might have the tools for objectives, but the agent would not reliably be reminded that an objective exists when a fresh turn begins.

The file does two main things. First, it defines the extension manifest: its name, version, tools, hook, and prompt instructions. A manifest is like a sign-up sheet that tells the host application, “Here is what this extension adds.” The listed tools let an agent plan an objective, run independent steps, record progress, and read the current objective state.

Second, it defines a hook that runs when a user prompt is submitted. A hook is a small piece of code the system calls at a specific moment. This hook looks up whether the current conversation has an objective. If it does, it injects a compact reminder into the agent’s context: the objective directive, how many attempts have happened, how many steps are closed, what still needs doing, and any open questions already asked of the user. This is important because a new turn does not automatically carry all the agent’s previous working memory. The injected “frontier” acts like a checklist clipped to the top of the next shift worker’s clipboard.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function adds the current objective’s unfinished work into the agent’s context at the start of a turn. It helps the agent remember what is still open, what conditions must be checked, and whether it should avoid asking the user the same question again.

**Data flow**: It receives a hook context from the host system. If there is no active turn, it returns nothing. Otherwise, it opens the extension’s stored data, finds the objective linked to the current conversation and workspace, and stops if there is none. When an objective exists, it builds plain text describing the objective, its progress, its open steps, each step’s acceptance conditions, any standing user-facing block, and special warnings for attempted-but-unchecked steps. It then returns an InjectContext containing that text, which means the text will be inserted into the agent’s working context for the turn. It also emits a metric so the system can count these injections and note whether any step is blocked.

**Call relations**: The manifest registers this function as the handler for the user_prompt_submit hook. When that event happens, the host calls it. Inside, it asks agent_current for the active workspace, uses Objectives to read the conversation’s objective view from storage, calls condition_summary to turn each acceptance condition into readable text, and returns InjectContext so the host can place the frontier into the prompt context.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function describes what the objectives extension contributes to the UFO system. The host uses it to discover the extension’s tools, prompt guidance, and the hook that injects objective reminders.

**Data flow**: It takes no input. It gathers fixed information defined in this file: the extension name and version, the objective tools, the prompt section that teaches the agent how to use objectives, and the hook specification for prompt submission. It returns a Manifest object containing all of that registration information.

**Call relations**: The extension loader calls this function when the extension is being loaded. The function creates a HookSpec pointing to _inject_frontier, a PromptSection containing the objective instructions, and a Manifest that hands the complete package back to the host system.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `startup registration, then recurring scheduled job dispatch`

This file is the extension’s “registration form.” Without it, the application would not know that scheduled tasks exist, what tool lets an agent pause and wait, or which background jobs should wake up later to continue work.

The file declares two clock-based jobs. One looks for scheduled tasks that are due. The other looks for paused conversations that are ready to resume. They run separately because they are different kinds of waiting work; if one gets stuck or fails, it should not block the other. Each job also asks for only the workspaces that actually have due rows, so the system does not waste effort checking empty workspaces.

The `manifest()` function packages all of this into a `Manifest`, which is the object the host uses to discover an extension. It includes the extension name and version, the pause-and-wait tool, the scheduled-task object kind, the two recurring jobs, the skill directory for task scheduling, a dependency on memory search, and a conversation slot used for automations.

The small `_run` and `_resume` functions are job entry points. When the scheduler fires, they create the right runner and tell it to do the due work.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the callback used when the scheduled-task job fires. It starts the runner that finds and runs scheduled tasks that are due.

**Data flow**: It receives an `ExtensionContext`, which is the extension’s access point to system services and workspace state. It creates a `ScheduledTaskRunner` with that context, then asks the runner to perform its work. Nothing is returned; the effect is that due scheduled tasks may be executed.

**Call relations**: The `manifest()` function registers `_run` as the handler for the scheduled-task runner job. Later, when the job scheduler fires that job, `_run` creates `ScheduledTaskRunner` and hands control to it.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the callback used when the pause-resume job fires. It starts the runner that resumes conversations whose waiting time has expired.

**Data flow**: It receives an `ExtensionContext` with the services and state needed by the extension. It creates a `PauseRunner` with that context, then asks it to process due pauses. Nothing is returned; the effect is that paused conversations may be resumed.

**Call relations**: The `manifest()` function registers `_resume` as the handler for the pause runner job. When the scheduler fires that job, `_resume` creates `PauseRunner` and hands the actual resume work to it.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest, the single object that tells the host application how to load and use this extension. It is the place where tools, object kinds, background jobs, skills, dependencies, and conversation slots are declared.

**Data flow**: It starts from constants in this file and imported pieces from the scheduled-tasks package. It asks `due_task_workspaces()` and `due_pause_workspaces()` for job candidate selectors, creates two `JobSpec` objects for the recurring jobs, creates `SkillSpec` objects for the skill folders, and wraps everything in a `Manifest`. The returned `Manifest` is what the host reads to install the extension’s capabilities.

**Call relations**: The host calls `manifest()` when discovering the extension. Inside it, the file wires together the due-workspace selectors, the job callbacks `_run` and `_resume`, the skill specification, and the declared tools and objects so the scheduler and agent can use them later.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup registration, then scheduled cron runs`

This file is the extension’s public registration card. Without it, the larger UFO system would not know that the self-improvement extension exists, what version it is, or that it should run a regular evaluation job.

The main idea is a scheduled job, like a calendar reminder. Once per schedule, the system calls `_tick`. That function checks that model access is available, because this extension needs a language model to propose prompt changes, replay old conversations, and judge the results. It wraps the model in `ModelAccessLeg`, which is a shared access layer used by the proposer and evaluator. Then it builds an `ImproveCron` object with two working parts: `PromptProposer`, which suggests possible improvements, and `CandidateEvaluation`, which tests and grades those suggestions. Finally, it runs the cron job.

The `manifest` function returns a `Manifest`, which is the structured description the host reads at startup. It declares one job named `eval_cron`, scheduled by a cron expression. A cron expression is a compact clock-based schedule. The job runs against trajectory workspaces, meaning saved work areas based on previous activity, and it asks for the deploy model rather than a cheaper background model because replaying archived transcripts may need the same capacity as normal deployment.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job body. It runs one round of self-improvement evaluation by setting up model access, creating the proposer and evaluator, and starting the improvement cron.

**Data flow**: It receives an `ExtensionContext`, which carries the workspace and model connection provided by the host. If there is no model connection, it stops with an error because the job cannot work without a language model. If the model exists, it wraps that model, gives the wrapper to the proposer and evaluator, builds the cron runner, and waits for the run to finish. It returns nothing, but the run may create or evaluate improvement candidates through the surrounding extension system.

**Call relations**: The job scheduler calls `_tick` when the manifest-declared cron fires. Inside that moment, `_tick` creates `ModelAccessLeg`, passes it into `PromptProposer` and `CandidateEvaluation`, then hands both parts to `ImproveCron`, which takes over the actual proposer-replay-grader workflow.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function gives the host system the extension’s identity and job schedule. The host uses it to discover the self-improvement extension and register its recurring evaluation job.

**Data flow**: It reads the file’s constants for the extension name, version, job name, and schedule. It asks for the set of trajectory workspaces to run candidates against, builds a `JobSpec` describing the scheduled job, and returns a `Manifest` containing that job. The returned object is the host’s before-and-after bridge: before, this is just Python code; after, the host has a concrete extension definition it can schedule.

**Call relations**: The extension loading system calls `manifest` during startup or registration. `manifest` calls `trajectory_workspaces` to choose the target workspaces, builds a `JobSpec` that points at `_tick` as the handler, and wraps that job inside a `Manifest` for the host to use later when the cron schedule fires.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### Web surfaces
These manifests expose user-facing website, UFO application, and web portal routes, screens, panels, feature flags, jobs, and safety checks.

### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

A manifest is like the contents label on a toolbox: it does not build the website itself, but it tells the system which tools are inside and when to use them. This file declares the "sites" extension and gathers many separate pieces into one package the host app can load.

When the extension is loaded, it offers tools for creating and serving hosted sites, plus delegation tools that let a main agent hand website work to a specialized child agent. It also registers two subagent profiles: one for website building and one for application building. It adds a prompt section loaded from a Markdown file, so the agent knows how to guide users through serving, validating, and returning a hosted link.

The file also connects user-facing parts of the system. It registers a site object type for chat, a sites surface for showing hosted pages, and a conversation slot for site-related state. It points the skill loader at the bundled website-building skill folder.

Finally, it installs two pre-tool-use hooks, which are checks that run before certain application-builder tools are allowed, and a scheduled background job that releases a main agent’s old homepage when appropriate. Without this file, these pieces might exist in the codebase, but the platform would not know to load or connect them.

#### Function details

##### `manifest`  (lines 60–100)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the complete manifest for the sites extension. The host system uses this to discover the extension’s tools, prompts, skills, subagents, surfaces, object types, hooks, conversation state, and scheduled cleanup job.

**Data flow**: It starts with constants and imported building blocks: tool definitions, subagent profiles, prompt text read from disk, skill paths, hook handlers, and background job settings. It packages those pieces into small spec objects where needed, such as a prompt section, skill spec, hook specs, and a job spec. The output is one Manifest object that describes everything the sites extension contributes to the system.

**Call relations**: During extension loading, the platform calls this function to ask, "What do you add?" The function creates the needed PromptSection, SkillSpec, HookSpec, and JobSpec entries, asks unreleased_main_homepage_workspaces for the job’s candidate workspaces, and hands the finished Manifest back to the platform so those pieces can be wired into the running app.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, unreleased_main_homepage_workspaces).


### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup / extension discovery`

This file exists so the main UFO system can discover and mount the `ufo` extension correctly. Without it, the host would not know the extension’s name, version, or which route should be made available for the shell client to use.

The file defines a small manifest, which is a structured description of an extension. It says this extension is called `ufo`, gives it a version number, and declares one surface. A surface is the visible connection point between the extension and the rest of the system, like a service window in a larger building. Here, that surface is the terminal-facing UFO surface used by the shell client.

The surface is built from route information imported from `ufo_ext_ufo.surface`. It also includes `resolve_workspace`, a function used to identify which workspace a connecting user belongs to. The comment at the top explains an important design choice: this extension does not define separate credential slots or configuration switches. If it is installed, it is mounted. Authentication is checked through an environment secret named `UFO_TOKEN_SECRET`, rather than through per-workspace stored credentials.

Overall, this file is small but important: it is the handshake between the extension and the core system.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the manifest object that describes this extension to the UFO host. The host uses it to learn the extension’s name, version, available surface, routes, and workspace-identification function.

**Data flow**: It starts with the fixed name and version defined in this file, then combines them with a `SurfaceSpec` built from imported route and workspace-resolution details. The result is a `Manifest` object that the host can read during extension loading.

**Call relations**: When the system is discovering or loading extensions, it calls `manifest` to ask this file, “What do you provide?” The function creates a `SurfaceSpec` for the UFO surface, then passes that into `Manifest.__init__` so the finished manifest can be handed back to the host.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup / extension registration`

The web portal is one way people use this system, alongside other possible surfaces like Slack or a command-line session. This file explains that web surface to the shared core, so the core knows how to mount it and what work it can do. Without this manifest, the portal would not be advertised as the browser home, its routes would not be connected, its admin-only tools would not be known, and its scheduled cleanup-style jobs would not run.

The file first names the extension and its version. It then declares feature flags, which are on/off switches controlled outside the code. These flags decide whether portal areas like Wiki, Issues, Memory, Skills, the main agent, and the admin screen should appear.

The `manifest` function then builds one `Manifest` object. Think of it like a venue booking sheet: it lists the rooms, the staff permissions, and the recurring tasks. The web surface claims the browser home page and supplies routes plus a way to identify the workspace for a request. It also declares two conversation slots, which are side areas in a conversation for things like changes and artifacts. Finally, it registers two scheduled jobs: one that gives untitled conversations useful titles, and one that seeds missing homepages for agent workspaces. The portal reads member context, meaning it uses the signed-in member’s own session rather than a separate bot credential.

#### Function details

##### `manifest`  (lines 58–83)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the complete description the core system needs in order to install and run the web portal. It declares the portal’s tools, browser routes, visible feature flags, conversation slots, and scheduled background jobs.

**Data flow**: It starts with constants imported from the web extension, such as route definitions, flag names, job names, schedules, and handler functions. It packages these into surface, job, and manifest objects. The result is a single `Manifest` object that the wider system can read to know what the web extension offers and how to activate it.

**Call relations**: When the extension is being registered, this function creates the pieces the core expects. It builds a `SurfaceSpec` so the web routes can be mounted as the home surface, creates two `JobSpec` entries so title summarizing and homepage seeding can run on schedules, asks the jobs helper functions for the right candidate workspaces, and finally hands everything into `Manifest` as the extension’s official description.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).

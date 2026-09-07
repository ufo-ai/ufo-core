# Task automation, monitoring, objectives, and improvement registrations  `stage-4.7`

This stage is the system’s sign-up desk for long-running work. It mostly runs during startup, when the host learns which extensions exist, what tools they add, and which background jobs should run later. These files do not do all the work themselves. They register the parts so the rest of the system can find and use them.

The monitors manifest adds monitor objects, a monitor action, and a clock-based job that checks when monitors are due. The objectives manifest adds planning tools and prompt guidance, and it reminds the agent about unfinished work at the start of each turn. The report digest manifest registers a writing skill, a scheduled digest job, and an admin rebuild tool. The scheduled tasks manifest adds task objects, a pause-and-wait tool, recurring jobs, and a skill for scheduling future work.

The self-improvement package marker labels that extension as installable Python code and explains its purpose: reviewing past activity offline and proposing human-approved prompt changes. Its manifest registers the regular evaluation job that performs that review.

## Files in this stage

### Monitoring and objectives
Registers long-running work visibility through due-monitor checks and objective-planning guidance.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `extension startup and recurring scheduled job execution`

This file is the public declaration for the monitors extension. A monitor is a lasting watch: it runs a shell probe inside a conversation’s sandbox on a schedule, with a required deadline, and keeps its own state such as baselines and streak counts. Without this manifest, the system would not register monitors as a known object, would not expose the monitor tool, and would not schedule the background runner that actually performs checks.

The file gives the extension a name and version, then defines a recurring job called `monitor_runner`. Its schedule string means the job is considered every minute, like a kitchen timer that rings regularly. The job does not scan every workspace blindly. Instead, it asks `due_monitor_workspaces()` for only the workspaces that currently have monitor work due, so idle workspaces do not waste dispatcher effort.

There are two moving parts. `_probe` is the small job callback: when the scheduler decides a workspace needs monitor work, it creates a `MonitorRunner` and tells it to run. `manifest()` packages all the declarations together into a `Manifest`, which is the object the host system reads when loading the extension.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function the scheduled job runs when it is time to check monitors for a workspace. It starts the monitor runner, which does the real probing work.

**Data flow**: It receives an `ExtensionContext`, which is the system-provided bundle of information and services for the current extension run. It uses that context to create a `MonitorRunner`, then asks the runner to perform the monitor checks. It returns nothing; its effect is that due monitor probes are carried out.

**Call relations**: The job declared in `manifest()` uses `_probe` as its handler. When the scheduler fires the monitor runner job, `_probe` is called with the extension context, then it hands control to `MonitorRunner` so the specialized monitor-running code can do the actual work.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension declaration that the host system reads. It says: this extension is named `monitors`, it provides a monitor tool, it provides a monitor object type, and it has a recurring job for checking due monitors.

**Data flow**: It starts from constants in this file, imported monitor definitions, and the workspace-selection helper `due_monitor_workspaces()`. It creates a `JobSpec` describing the scheduled monitor runner job, then places that job together with the tool and object declarations into a `Manifest`. The returned `Manifest` is the complete registration package for the extension.

**Call relations**: The extension loader calls `manifest()` when it needs to discover what this extension contributes. During that build, `manifest()` asks `due_monitor_workspaces()` which workspaces should be considered for scheduled monitor work, creates a `JobSpec` that points to `_probe`, and wraps everything in a `Manifest` for the host system to register.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup registration, then user prompt handling`

This file is the front door for the objectives extension. Its job is to make long-running work survive across separate agent turns. Without it, an agent might delegate work, schedule something, or wait for a later wake-up, then forget the larger goal when the next turn begins.

The file does two main things. First, it defines a prompt section that teaches the agent when to create an objective, how to break it into meaningful steps, and how to judge whether a step is really complete. This is like giving the agent a project notebook rule: do not rely on memory alone; write down durable milestones.

Second, it installs a hook. A hook is code the system calls at a specific moment. Here, the hook runs when a user prompt is submitted. If the current conversation has an active objective, the hook reads that objective from storage and injects a compact status summary into the agent’s context: the objective directive, progress counts, open steps, unmet conditions, and any question already raised with the user. This prevents repeated asking and keeps the next turn oriented.

The manifest function ties these pieces together by naming the extension, exposing its tools, registering the hook, and adding the instruction text.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function gives each new turn a reminder of the active objective, if the conversation has one. It helps the agent see what work is still open, what evidence is needed, and whether it is waiting on the user.

**Data flow**: It receives hook context from the system, including the current turn and extension services. If there is no turn, it returns nothing. Otherwise, it opens a storage transaction, uses the current agent’s workspace to look up the objective for this conversation, and stops if none exists. If an objective is found, it records a metric, builds a plain text summary of the objective frontier, and returns an InjectContext containing that text so it can be added to the agent’s prompt.

**Call relations**: The system calls this function through the hook registered by manifest when a user prompt is submitted. Inside, it asks agent_current for workspace identity, uses Objectives to read the durable objective record, uses condition_summary to turn acceptance conditions into readable lines, emits a metric for observability, and finally hands an InjectContext back to the host system for prompt injection.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the objectives extension to the host system. It declares the extension’s name and version, the tools the agent may call, the prompt guidance the agent should receive, and the hook that keeps objective state visible across turns.

**Data flow**: It takes no input. It packages constants and imported tool definitions into a Manifest object: four objective tools, one user-prompt hook pointing to _inject_frontier, and one prompt section containing the extension’s instructions. The result is a complete registration object the host can load.

**Call relations**: The host system calls this during extension loading. The Manifest it returns causes the objective tools to become available, the prompt section to be included, and the _inject_frontier hook to run later whenever a user prompt is submitted.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Digests and scheduled tasks
Registers scheduled operational work, including report-digest generation and recurring task automation.

### `extensions/report_digest/ufo_ext_report_digest/manifest.py`

`config` · `startup registration, then scheduled job runs and admin tool requests`

This file is the extension’s front desk. It declares the report-digest extension’s name and version, points to the shared writing standard, and registers two ways the system can use that standard. First, a scheduled job runs every ten minutes and looks for reports whose digest entries still need to be written. It uses a background language model and blob access, which is storage access for published report content. Second, an admin-only tool lets a workspace administrator ask for recent digest entries to be written again if they read badly. That tool does not rewrite anything immediately. Instead, it marks reports from the last seven days as due, like putting them back into a work queue, and the scheduled job rewrites them later in small batches. The file also registers the report object type so the portal knows what kind of thing the tool is attached to. A small input model forbids extra arguments, because the rebuild action is intentionally all-or-nothing for the recent report feed, not a per-report tweak.

#### Function details

##### `write_digests`  (lines 38–43)

```
async def write_digests(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the background digest writer for reports that still need entries. It exists so the scheduler has a simple job handler it can call.

**Data flow**: It receives an extension context, which should already contain a background model and access to member-context blobs. It checks that both are present, then builds a DigestWriter with that context, model, and blob reader. The result is not returned to the caller; the important change is that the writer reads due reports and writes their digest entries.

**Call relations**: The manifest registers this function as the scheduled job handler. When the scheduler decides a workspace has undigested reports, it calls this function, which hands the real writing work to DigestWriter.

*Call graph*: 1 external calls (__init__).


##### `rebuild_report_digest_handler`  (lines 50–73)

```
async def rebuild_report_digest_handler(ctx: ToolContext, args: RebuildReportDigestInput) -> ToolResult
```

**Purpose**: Runs when an administrator uses the “Rebuild entries” tool. It marks recent reports so their digest entries will be regenerated by the normal background job.

**Data flow**: It receives a tool context and an empty, strict input object. It checks that the tool was given an extension context, then asks whether the speaking user is a workspace admin. If not, it stops with an admin-only message. If yes, it runs DigestRebuild, which counts and marks eligible recent reports as due. It returns a ToolResult containing either a “nothing to rebuild” message or a message saying how many reports were queued for rewriting.

**Call relations**: This function is connected to the tool declared in manifest. The tool layer calls it when a user activates the rebuild action. It uses ToolContext.require_speaking_admin for the permission check, DigestRebuild for the queueing work, and TextContent plus ToolResult to send a clear message back to the user.

*Call graph*: calls 1 internal fn (require_speaking_admin); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 76–114)

```
def manifest() -> Manifest
```

**Purpose**: Builds the full declaration that the host system reads to install and run this extension. It says what skills, tools, jobs, objects, and permissions the report-digest extension needs.

**Data flow**: It takes no input. It creates a Manifest containing the extension name and version, the shared skill directory, the rebuild tool definition, the scheduled digest-writing job, the report object declaration, and a flag saying the extension needs read access to member context. The returned Manifest is the package of instructions the host uses to wire the extension into the system.

**Call relations**: The host calls this during extension loading. Inside it, SkillSpec points to the writing standard, ToolDef connects the rebuild action to rebuild_report_digest_handler, JobSpec connects the schedule to write_digests, owner_candidates chooses which workspaces should run the job, and the object binding makes the tool appear for the report collection.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, owner_candidates).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension load and recurring job registration`

This is the extension’s front desk. When the application loads extensions, it asks this file for a manifest, which is a plain declaration of what the extension offers and what support it needs. The extension declares one tool that can pause a conversation until later, one object kind for scheduled tasks, and one skill folder that teaches the agent how to schedule work.

The most important part is the two clock-based jobs. One job looks for scheduled tasks that are due. The other looks for paused conversations that are ready to resume. They run on the same repeating schedule, but they are deliberately separate. That means trouble in one area, such as a stuck scheduled-task run, does not block paused conversations from waking up. Think of them as two alarm clocks on the same wall: one rings for planned tasks, the other rings for pauses ending.

The jobs do not scan every workspace blindly. Each job is given a “candidate” finder that names only workspaces with due rows in the relevant table. That keeps idle workspaces cheap. The file also declares that this extension depends on the memory search extension and uses a conversation slot for automation-related state.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job entry function for due scheduled tasks. When the scheduler fires this job, it creates a ScheduledTaskRunner and tells it to perform the scheduled-task work for the current extension context.

**Data flow**: It receives an ExtensionContext, which is the bundle of services and workspace information the extension is allowed to use. It passes that context into ScheduledTaskRunner, then the runner does the actual work of finding and invoking due scheduled conversations. Nothing meaningful is returned; the effect is that due scheduled tasks may be executed.

**Call relations**: The manifest gives this function to a JobSpec as the handler for the scheduled-task runner job. When the platform’s job system decides there is due task work, it calls _run, and _run hands control to ScheduledTaskRunner.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job entry function for paused conversations that are ready to continue. When the scheduler fires this job, it creates a PauseRunner and tells it to resume the due pause work.

**Data flow**: It receives an ExtensionContext with the services and workspace scope for the job. It passes that context into PauseRunner, then the runner resumes conversations whose pause time has arrived. It returns no useful value; its result is the side effect of continuing waiting conversations.

**Call relations**: The manifest gives this function to a separate JobSpec as the handler for pause resumption. The job system calls _resume when due pause work exists, and _resume delegates to PauseRunner so this path stays separate from scheduled-task execution.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension’s official declaration for the host application. It lists the extension name, version, tools, object kinds, recurring jobs, skills, dependency, and conversation slot.

**Data flow**: It starts from constants in this file, such as the extension name, version, schedule string, and skill folder names. It asks due_task_workspaces and due_pause_workspaces for candidate finders, wraps the two job handlers in JobSpec objects, creates SkillSpec entries for the skill folders, and returns one Manifest object containing the whole declaration.

**Call relations**: The host application calls manifest when loading the extension. Inside, manifest creates the two JobSpec entries that later call _run and _resume, creates SkillSpec entries for the task-scheduling skill, and hands the finished Manifest back to the platform so the extension can be installed into the wider system.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### Offline self-improvement
Defines and registers the package for periodic offline review of workspace activity and proposed prompt improvements.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `import time`

This file contains only a short package description. Its practical job is small but important: it tells Python that this directory is an importable package, and it gives readers a quick summary of the extension’s purpose. The extension itself is described as an offline replay evaluation loop. In plain terms, that means it looks back at saved records of what happened in the workspace, rather than acting live during a user request. Those saved records are called trajectories: step-by-step traces of actions and decisions. The extension uses those traces to find possible improvements to prompts, which are the instructions given to an AI system. Importantly, it does not silently change those prompts on its own. It opens governed prompt changes, meaning proposed changes that go through an approval process. A human member must approve them before they take effect. Without this package marker, the surrounding extension code may not be importable in the usual Python way, and without the description, newcomers would have less immediate context for why this extension exists.


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup for registration, then scheduled job execution`

This is the extension’s front desk sign-up sheet. Without it, the larger UFO system would not know the self-improvement extension’s name, version, or scheduled work. The main idea is simple: once on a fixed clock schedule, the extension should try to improve prompts by proposing candidates, replaying old trajectories, and grading the results.

The file declares a cron-style job, which means a task that runs at set times, like an alarm clock. The schedule here is once per day at midnight. Importantly, the job is time-based. It is not triggered by the proposals or stored results it may create, which helps avoid accidental loops.

When the job runs, `_tick` checks that model access is available. A model here means the language model service the extension needs to generate proposals, replay archived conversations, and judge outcomes. It wraps that access in `ModelAccessLeg`, then gives the same wrapped model access to the proposer and evaluator. Finally, it creates an `ImproveCron` object and runs it.

The job also says it needs the deploy model, not a cheaper background model. That matters because replaying an archived transcript can be a large request, and those transcripts were prepared for the deploy model’s context window.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the actual work function for the scheduled self-improvement run. It makes sure language-model access exists, then starts the improvement cycle using a proposer and evaluator.

**Data flow**: It receives an extension context, which contains things like model access and workspace information. If there is no model attached, it stops with an error because the self-improvement job cannot do useful work without one. If a model is present, it wraps that model access, builds a prompt proposer and candidate evaluator around it, gives them to `ImproveCron`, and waits for the cron run to finish.

**Call relations**: The scheduled job created by `manifest` points to `_tick` as its handler, so the host scheduler calls `_tick` when the clock fires. Inside that run, `_tick` creates `ModelAccessLeg` to provide controlled model use, passes it into `PromptProposer` for making candidates, passes it into `CandidateEvaluation` for replaying and judging, then hands both pieces to `ImproveCron` to carry out the full self-improvement pass.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It returns the extension name, version, and the scheduled job the host should register.

**Data flow**: It takes no input. It gathers fixed values from this file, asks `trajectory_workspaces()` which workspaces should be considered as job candidates, builds a `JobSpec` for the daily evaluation cron, and wraps that in a `Manifest`. The result is a structured declaration the host can read during extension loading.

**Call relations**: The host system calls `manifest` when it loads the extension. `manifest` creates the `JobSpec` that names `_tick` as the function to run on the schedule, uses `trajectory_workspaces()` to scope where the job may operate, and returns everything inside a `Manifest` so the scheduler can later call `_tick` at the right time.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).

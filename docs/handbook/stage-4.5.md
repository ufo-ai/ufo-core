# Scheduled and background-work extension manifests  `stage-4.5`

This stage is shared behind-the-scenes support. It does not do the user-facing work itself. Instead, it provides “manifests,” which are registration sheets that tell the main application which background features exist and when they should run. Without these files, the code for the features might be present, but the system would not know to load it or schedule it.

The monitors manifest registers a monitor tool, a monitor object type, and a recurring job that checks whether any monitors are due. It is like adding a watchdog to the system and giving it a timetable.

The scheduled-tasks manifest registers the pieces needed for delayed or repeated user tasks: the scheduled-task object type, a pause-and-wait tool, two background jobs, a skill package, and a storage slot for conversation state. These parts let the system remember future work and wake up to continue it.

The self-improvement manifest registers a daily evaluation job. This job reviews system behavior using the main deployed model, so the evaluation matches real production behavior.

## Files in this stage

### Background Work Manifests
Extension manifests register monitor checks, scheduled-task behavior, and self-improvement evaluation jobs with the host runtime.

### `extensions/monitors/ufo_ext_monitors/manifest.py`

`config` · `extension load and recurring background job scheduling`

This file is the front desk for the monitors extension. A monitor is a lasting watch: it runs a shell check inside a conversation workspace on a schedule, with a deadline, and keeps its own state such as baselines and streak counts. The host system needs a clear declaration of what an extension contributes before it can expose tools, store objects, or start background work. That is what this manifest provides.

The file gives the extension a name and version, then defines a small runner hook called `_probe`. When the scheduled job fires, `_probe` creates a `MonitorRunner`, which is the worker that actually looks for due monitors and probes them.

The `manifest` function then packages everything into a `Manifest`, which is like a registration form handed to the application. It lists the monitor tool, the monitor object kind, and one recurring job. That job is scheduled once per minute using a cron-style schedule string, meaning a compact time pattern. It also supplies `due_monitor_workspaces()` as the candidate selector, so the dispatcher only considers workspaces that currently have monitor work due. This matters because idle workspaces do not waste background-job effort.

#### Function details

##### `_probe`  (lines 28–29)

```
async def _probe(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job's actual callback. When the monitor job fires, it creates a `MonitorRunner` and tells it to run the due monitor checks.

**Data flow**: It receives an `ExtensionContext`, which is the extension's access point to the host system and current workspace. It passes that context into a new `MonitorRunner`, then waits for the runner to finish its monitor-probing work. It returns no value; the useful result is the work the runner performs, such as checking due monitors and updating their stored state.

**Call relations**: The job declared in `manifest` points to `_probe` as its handler. When the scheduler reaches the configured minute mark for a workspace with due monitor work, it calls `_probe`, and `_probe` hands control to `MonitorRunner` by constructing it with the extension context.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–46)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension declaration that the host application reads. It says: this extension is named `monitors`, it provides a monitor tool and object type, and it has a once-per-minute background job.

**Data flow**: It starts from fixed constants such as the extension name, version, job name, and schedule. It asks `due_monitor_workspaces()` for the rule that finds workspaces with monitor work waiting, wraps the runner details in a `JobSpec`, and then places that job together with the tool and object declarations into a `Manifest`. The returned `Manifest` is the final registration package for the extension.

**Call relations**: The host calls `manifest` when loading the extension. Inside, it creates a `JobSpec` for the recurring monitor runner, uses `due_monitor_workspaces()` so the scheduler can avoid idle workspaces, and then creates the `Manifest` that exposes the monitor tool, monitor object kind, and scheduled job to the rest of the system.

*Call graph*: 3 external calls (__init__, __init__, due_monitor_workspaces).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension load and scheduled job ticks`

This file answers the question: “When the scheduled-tasks extension is installed, what should the larger system know about it?” Without it, the extension’s pieces could exist in code but the host would not know to load them, run them on a clock, or expose their tools and skills to the agent.

The file declares the extension name and version, then defines two small job entry functions. One starts the normal scheduled task runner, which looks for tasks whose scheduled time has arrived. The other starts the pause runner, which resumes conversations that were deliberately paused until a later time. These are separate jobs because they are separate kinds of waiting work. If one path gets stuck or fails, it should not block the other.

The main `manifest` function builds a `Manifest`, which is like a packing list handed to the host application. It includes the pause tool, the scheduled-task object kind, two clock-based jobs, the skill folder the agent should learn from, a dependency on memory search, and a conversation slot used to store automation-related state. Each job also supplies a “candidate” query, meaning the dispatcher only considers workspaces that actually have due work, instead of waking every workspace every minute.

#### Function details

##### `_run`  (lines 35–36)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job handler for due scheduled tasks. When the system clock fires this job, it starts the scheduled task runner for the current extension context.

**Data flow**: It receives an `ExtensionContext`, which is the host-provided bundle of services and workspace information the extension needs. It uses that context to create a `ScheduledTaskRunner`, then asks the runner to do its work. Nothing is returned; the useful effect is that due scheduled tasks can be found and invoked.

**Call relations**: The manifest registers this function as the handler for the scheduled-task runner job. When the job system decides a workspace has due scheduled-task rows, it calls this function, which hands control to `ScheduledTaskRunner` to perform the actual task-running logic.

*Call graph*: 1 external calls (__init__).


##### `_resume`  (lines 39–40)

```
async def _resume(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job handler for paused conversations that are ready to continue. When the pause job fires, it starts the pause runner for the current extension context.

**Data flow**: It receives an `ExtensionContext` from the host. It uses that context to create a `PauseRunner`, then asks that runner to resume any pauses that are due. It returns nothing; its effect is to let waiting conversations continue at the right time.

**Call relations**: The manifest registers this function as the handler for the pause runner job. When the job system finds workspaces with due pause rows, it calls this function, which passes the work to `PauseRunner` so pause-specific resume logic stays separate from scheduled-task logic.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 43–66)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension’s manifest, the structured description the host reads to install and run the extension correctly. It is the central place where this extension declares its tools, object types, recurring jobs, skills, dependencies, and conversation storage.

**Data flow**: It starts from constants such as the extension name, version, job names, schedule, and skill folder. It creates two `JobSpec` entries: one for scheduled tasks using `due_task_workspaces()`, and one for pauses using `due_pause_workspaces()`. It also creates `SkillSpec` entries for the listed skill folders, then returns a `Manifest` containing the complete declaration.

**Call relations**: The host calls this function while loading the extension. Inside it, `JobSpec` objects connect clock schedules to `_run` and `_resume`, the candidate functions narrow each job to workspaces with due work, and `SkillSpec` points the host to the task-scheduling skill. The resulting `Manifest` is what lets the rest of the system discover and use this extension.

*Call graph*: 5 external calls (__init__, __init__, __init__, due_pause_workspaces, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup for registration, then scheduled job execution`

This file is the extension’s sign-up sheet. Without it, the larger system would not know that the self-improvement feature exists, what version it is, or that it needs a clock-based job to run.

The job it declares is a cron job, meaning work that runs on a schedule rather than in response to a user action. Here, the schedule is set for a regular evaluation tick. When that tick fires, the extension builds the pieces needed for self-improvement: a prompt proposer, an evaluator that can replay past conversations, and a judge that can grade the result. These all share the same model access wrapper, so model calls go through the extension context in a controlled way.

A key detail is that the job asks for the deploy model. That means it uses the normal production model configuration instead of a cheaper background-job model. The comment explains why: replaying an archived transcript may send a large conversation back to the model, and that transcript was prepared for the deploy model’s context window, meaning the amount of text the model can safely read at once.

In short, this file does not do the self-improvement work itself. It declares when and how that work should be started.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function that runs when the scheduled self-improvement job fires. It checks that model access is available, then assembles the proposer, replay evaluator, and judge needed to run one improvement cycle.

**Data flow**: It receives an extension context, which carries shared services such as model access. If the context has no model, it stops with an error because this job cannot do useful work without asking a model questions. If model access exists, it wraps that model in a `ModelAccessLeg`, gives that wrapper to the prompt proposer and evaluator, builds an `ImproveCron`, and runs it. The visible result is no return value, but the improvement process may read past trajectories, test candidates, and record outcomes through the wider system.

**Call relations**: The scheduler calls this function as the job handler declared by `manifest`. Inside the tick, it creates the supporting objects: `ModelAccessLeg` provides controlled model use, `PromptProposer` suggests candidate prompt changes, `CandidateEvaluation` replays and judges those candidates, and `ImproveCron` ties those steps into one scheduled run.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension declaration that the host system reads. It names the extension, gives its version, and registers the self-improvement evaluation job.

**Data flow**: It takes no inputs. It builds a `JobSpec` describing the scheduled evaluation job: its name, its cron schedule, the `_tick` function to call, the workspaces it may run against, and the fact that it needs the deploy model. It then places that job inside a `Manifest` and returns it to the host system.

**Call relations**: The extension loader calls this function when discovering available extensions. During that setup, it asks `trajectory_workspaces` which candidate workspaces the job should consider, wraps the job details in `JobSpec`, and hands the completed `Manifest` back so the scheduler can later invoke `_tick` at the right time.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).

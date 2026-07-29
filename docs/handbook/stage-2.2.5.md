# Automation, memory, and self-improvement packages  `stage-2.2.5`

This stage is shared behind-the-scenes support for optional extensions. These extensions let the system remember useful facts, run work on a schedule, pause until later, review its own past work, and create new skills while it is running. The package files are like labels on drawers: each __init__.py tells Python that a folder is an importable package and gives a short statement of what that extension is for. The memory package label describes saved facts, recall during user prompts, page-based updates, and a background memory indexing job. The scheduled-tasks package label simply makes that extension loadable. Its manifest is the real instruction sheet: it tells the platform about scheduled task objects, a pause-and-wait tool, a recurring runner job, and agent skills. The self-improvement package label explains that it reviews past workspace activity offline and proposes prompt changes for human approval. Its manifest registers the scheduled evaluation job and defines what happens when it runs. The skill-creation package label introduces runtime agent-made skills.

## Files in this stage

### Memory Extension
Package entry point for the memory extension and its saved-fact recall and indexing behavior.

### `extensions/memory/ufo_ext_memory/__init__.py`

`other` · `cross-cutting`

This is the package entry file for the memory extension. It does not define any functions or run any code itself. Its main job is to identify this folder as a Python package and to document, in one short summary, what the package is about.

The memory extension is described as a way to keep durable facts, meaning information that can survive beyond a single moment or request. It can bring those facts back when a user submits a prompt, using a hook called `user_prompt_submit`. A hook is a planned connection point where the larger system lets an extension react to something that happened. It can also derive memory from page changes through a `page_change` hook, and it includes a job that builds or updates a memory index. An index is like a library catalog: it helps the system find relevant stored information later.

Without this package marker and summary, Python would not treat this directory in the same clear package-oriented way, and newcomers would have less context about the extension’s role.


### Scheduled Automation Extension
Package and manifest files that register scheduled-task automation, pause-and-wait tooling, recurring jobs, and agent skills.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/__init__.py`

`other` · `import/package discovery`

This is an intentionally empty Python package marker. In Python, a directory with an `__init__.py` file can be treated as an importable package, meaning other code can refer to modules inside it using package-style names. Here, that package is `ufo_ext_scheduled_tasks`, which appears to belong to the scheduled-tasks extension.

There is no code to run, no settings to load, and no functions or classes to call. Its job is structural: it gives the extension a clear place in the project’s module tree. Without this file, depending on the Python version and packaging setup, imports or extension discovery could become less reliable. A useful analogy is a labeled folder: the folder may be empty at the front, but the label tells the system, “the scheduled-tasks extension lives here.”


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension loading and recurring scheduled job execution`

This is the extension’s front desk. When UFO loads extensions, it asks this file for a manifest, which is a plain declaration of what the extension contributes to the larger system. Here, the scheduled-tasks extension announces its name and version, exposes a tool that can pause work until later, exposes an object kind called a scheduled task, and registers a recurring job that wakes up on a clock. That job is the part that looks for scheduled tasks that are due and runs them.

The file also points UFO to a skill folder named `task-scheduling`. A skill is supporting guidance or behavior the agent can load before it tries to schedule tasks, like giving a worker the right instruction sheet before starting a job.

One important detail is that the runner job is clock-driven. It fires every minute according to the cron-like schedule string, rather than being triggered directly by the task rows it writes. The job also limits where it runs by using `due_task_workspaces`, so it only considers workspaces that may actually have due tasks. Finally, the manifest declares that this extension depends on `memory_search`, meaning that other extension must be available for this one to work as intended.

#### Function details

##### `_run`  (lines 28–29)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the small job handler that starts the scheduled-task runner when the platform’s clock job fires. It exists so the manifest can give the job system one simple function to call.

**Data flow**: It receives an `ExtensionContext`, which is the extension’s access pass to workspace services and shared platform capabilities. It uses that context to create a `ScheduledTaskRunner`, then asks that runner to do the actual work of finding and firing due scheduled tasks. It returns no value; its effect is whatever task-running work the runner performs.

**Call relations**: The manifest registers `_run` as the handler for the recurring job. When the job system decides this extension should run for a candidate workspace, it calls `_run`; `_run` then hands control to `ScheduledTaskRunner` by constructing it with the current context.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–48)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension’s official registration record. The platform calls it to learn what tools, object types, jobs, skills, and dependencies this extension provides.

**Data flow**: It starts from constants in this file, such as the extension name, version, job name, schedule, and skill folder. It creates a `JobSpec` for the recurring scheduled-task runner, asks `due_task_workspaces` to define which workspaces are candidates for that job, creates `SkillSpec` entries for the listed skills, and packages everything into a `Manifest`. The returned `Manifest` is what the platform uses to load the extension.

**Call relations**: This is the main entry point for the extension loader. While building the manifest, it creates the job description with `JobSpec`, the skill descriptions with `SkillSpec`, and the final extension description with `Manifest`; it also calls `due_task_workspaces` so the registered runner job is aimed only at relevant workspaces.

*Call graph*: 4 external calls (__init__, __init__, __init__, due_task_workspaces).


### Self-Improvement Extension
Package and manifest files that enable offline workspace evaluation and scheduled prompt-improvement proposals.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `import/package discovery`

This file contains only a short package description, but that description is important because it tells readers what this extension is for. The self-improvement extension is not described as something that changes prompts on its own. Instead, it runs an offline replay and evaluation loop: it looks back at recorded trajectories, meaning previous sequences of actions or decisions from the workspace, and uses them as material for improvement. From that review, it opens proposed prompt changes. Those changes are governed, which means they are meant to pass through a controlled approval process rather than being applied automatically. A useful analogy is a coach reviewing game footage: the coach studies what happened, suggests adjustments, and then a responsible person decides whether to adopt them. Without this package marker and description, Python would not treat this directory as a normal importable package in the same way, and newcomers would have less context for why the extension exists.


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `extension startup and scheduled job runs`

This file is the extension’s front desk sign and appointment book. It declares the extension name and version, then registers one scheduled job called an evaluation cron. A cron is a clock-based job: it runs at set times, rather than being triggered by user actions or by data changing.

When the scheduled job runs, `_tick` is called. It first checks that the extension has access to a model, meaning an AI model connection has been wired into the runtime. Without that, the self-improvement loop cannot propose prompt changes, replay behavior, or judge candidates, so it stops with a clear error.

If model access is available, the file wraps it in `ModelAccessLeg`, then builds the three main pieces of the self-improvement loop: a proposer that suggests prompt changes, an evaluator that replays and judges those changes, and an `ImproveCron` runner that coordinates the whole cycle. The manifest also limits the job to relevant trajectory workspaces, which are the stored work areas containing past runs or behavior traces to learn from.

In short, this file does not contain the improvement logic itself. It wires the parts together and tells the larger system when and where to run them.

#### Function details

##### `_tick`  (lines 21–29)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the actual scheduled-job handler. Each time the clock-based evaluation job fires, it starts one self-improvement cycle using the model connection supplied by the extension context.

**Data flow**: It receives an `ExtensionContext`, which is the runtime bundle of services available to the extension. It reads `ctx.model`; if no model is present, it raises an error because the job cannot work without AI model access. If a model is present, it wraps that model in `ModelAccessLeg`, gives that wrapper to a `PromptProposer` and a `CandidateEvaluation`, builds an `ImproveCron`, and awaits its `run` method. The visible result is no returned value, but the self-improvement process has been kicked off and may create proposals, replay candidates, and grade results.

**Call relations**: The scheduled job declared by `manifest` uses `_tick` as its handler. When `_tick` runs, it constructs the helper objects it needs: `ModelAccessLeg` for controlled model use, `PromptProposer` for suggesting changes, `CandidateEvaluation` for testing and judging them, and `ImproveCron` for coordinating the full pass.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 32–44)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension declaration that the host system reads to discover the self-improvement extension. It names the extension, gives its version, and registers the scheduled evaluation job.

**Data flow**: It takes no inputs. It uses the file’s constants for the extension name, version, job name, and schedule. It calls `trajectory_workspaces()` to say which workspaces the job can run against, creates a `JobSpec` that points to `_tick` as the job handler, and packages that job inside a `Manifest`. The output is that `Manifest` object, ready for the host system to load.

**Call relations**: The host system calls `manifest` during extension discovery or startup. The function hands back a `Manifest` containing a `JobSpec`; later, when the job’s schedule matches the clock, the runtime calls `_tick` to perform the self-improvement cycle.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### Skill Creation Extension
Package entry point for runtime agent skill creation and reuse capabilities.

### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This is the package entry file for the skill-creation extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, like a labeled drawer that other code can open. This particular file does not define any functions, classes, or setup code. Its only content is a short package-level note: the package is about skills authored by an agent, represented as objects, and skills that can exist or be used during each turn of execution.

Its value is mostly organizational. Without this file, depending on the Python version and import style, other parts of the project might not reliably recognize `ufo_ext_skill_create` as a package. The comment also gives newcomers and documentation tools a quick clue about the package’s purpose before they open the deeper files where the actual behavior lives.

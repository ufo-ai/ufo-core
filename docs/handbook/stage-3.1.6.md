# Skills and scheduled work manifests  `stage-3.1.6`

This stage is the system’s sign-up sheet for extra abilities and timed work. It mostly runs behind the scenes when the host loads extensions, telling the rest of the system what tools, skills, and scheduled jobs are available.

The documents manifest registers ready-made document skills, so the agent can later create, review, or format Word files, slide decks, spreadsheets, and PDFs. The scheduled-tasks manifest adds the machinery for recurring work: a task object to describe what should repeat, a wait tool for pausing until the right time, a clock-driven runner that checks when jobs are due, and a scheduling skill the agent can use. The self-improvement manifest plugs in a daily evaluation job and defines what should happen when that job runs. The skill-create manifest lets workspace members build and manage their own reusable skills, then makes those saved skills available in later conversations.

Together, these manifests act like labels on drawers in a workshop: they tell the system what equipment exists and when to bring it out.

## Files in this stage

### Document Skill Manifest
Registers the built-in document-focused skills available for creating, reviewing, and styling common office files.

### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup or skill discovery`

This file is like the table of contents for the documents skill pack. The wider system does not automatically know which document skills exist or where their files live, so this manifest gives it that information in one small, predictable place. It names the extension, gives it a version, points to the local skills folder, and lists each skill folder that belongs to the pack. When the system asks this extension what it provides, the manifest function builds a Manifest object containing one SkillSpec for each skill. A SkillSpec is a small description of a loadable skill, mainly saying where that skill’s folder can be found. This matters because the agent loads skills on demand: for example, if it needs to work with a PDF, the loader can find the PDF skill folder through this manifest. Some skills depend on shared foundations, such as common design guidance, but those relationships are described inside the skill folders themselves. This file’s job is simpler: make sure every skill in the documents pack is discoverable.

#### Function details

##### `manifest`  (lines 28–33)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the system-readable list of document skills provided by this package. The loader uses it to discover where each skill lives on disk.

**Data flow**: It starts with the fixed extension name, version, skills folder path, and skill names defined in this file. For each skill name, it creates a SkillSpec pointing to that skill’s folder, then wraps all of those skill descriptions in a Manifest object. The result is a complete description of the documents extension that the rest of the system can read.

**Call relations**: When the extension is being discovered, the system calls manifest to ask what this package contributes. Inside, it creates SkillSpec objects for the individual skill folders and passes them into Manifest.__init__, producing the final manifest that the skill loader can use later.

*Call graph*: 2 external calls (__init__, __init__).


### Scheduled Work Manifests
Defines recurring task support, clock-driven execution, and the daily self-improvement evaluation hook.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/manifest.py`

`config` · `extension load and recurring background job startup`

This is the extension’s sign-up sheet. Without it, the host system would not know that scheduled tasks exist, would not load the task-scheduling instructions, and would not start the background job that checks for work to run.

The file declares a small set of names and paths, then builds a Manifest, which is the package of promises an extension makes to the system. It says: this extension is called scheduled_tasks; it provides a tool that can pause and wait durably; it provides an object kind for scheduled tasks so agents can create, edit, list, and delete them; it provides a recurring job that wakes up on a fixed clock schedule; and it provides a skill folder that teaches agents how to schedule tasks.

The recurring job is important. It does not run because of the task rows themselves. Instead, it wakes up every minute, looks for workspaces that have due tasks, and runs a ScheduledTaskRunner for the current extension context. Think of it like a night watchperson making regular rounds: the tasks are notes on the clipboard, but the watchperson still needs a clock telling them when to check.

#### Function details

##### `_run`  (lines 28–29)

```
async def _run(ctx: ExtensionContext) -> None
```

**Purpose**: This is the job handler that actually starts the scheduled-task runner when the system’s clock-based job fires. It turns the extension context into a ScheduledTaskRunner and asks that runner to do its work.

**Data flow**: It receives an ExtensionContext, which is the bundle of workspace-specific services and permissions the extension needs. It uses that context to create a ScheduledTaskRunner, then awaits the runner’s run step. It returns nothing directly; the visible result is that due scheduled tasks may be claimed and invoked by the runner.

**Call relations**: The manifest gives this function to the JobSpec as the background job’s handler. When the job system decides this extension has candidate workspaces with due tasks, it calls this handler, which hands control to ScheduledTaskRunner.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 32–48)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension manifest, the object the host system reads to discover what this extension contributes. It is the central declaration of the scheduled-tasks extension’s tools, object type, recurring job, skills, and dependency on memory search.

**Data flow**: It reads the constants in this file, such as the extension name, version, job name, schedule string, and skill folder path. It creates a JobSpec for the scheduled-task runner, asks due_task_workspaces for the rule that selects workspaces worth checking, creates SkillSpec entries for the skill folders, and returns a Manifest containing all of these pieces.

**Call relations**: The host system calls this during extension loading. Inside it, JobSpec describes when and where _run should be used, SkillSpec points the system at the task-scheduling skill content, due_task_workspaces narrows the job to relevant workspaces, and Manifest wraps everything into the final declaration the host understands.

*Call graph*: 4 external calls (__init__, __init__, __init__, due_task_workspaces).


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`config` · `startup registration, then scheduled job execution`

This is the extension’s front desk. When the larger system loads extensions, this file provides a manifest: the extension’s name, version, and scheduled work. The scheduled job is a cron job, meaning it runs because the clock reaches a certain time, not because a user request or data write happened. Here, the schedule is set to run once a day at midnight.

When the job runs, `_tick` is called. It first checks that model access has been provided. In plain terms, the self-improvement work needs an AI model to suggest changes, replay behavior, and judge results. If no model is available, it stops with a clear error instead of failing later in a confusing way.

If model access is available, `_tick` wraps it in `ModelAccessLeg`, then builds the three main parts of the improvement loop: a `PromptProposer` that suggests candidate prompt changes, a `CandidateEvaluation` that tests and judges those candidates, and an `ImproveCron` object that coordinates the whole run. The manifest also limits the job to trajectory workspaces, so the scheduled evaluation is aimed at the right kind of stored work.

#### Function details

##### `_tick`  (lines 21–29)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the function that actually runs when the scheduled self-improvement job fires. It prepares model-backed proposer and evaluator pieces, then starts the improvement run.

**Data flow**: It receives an `ExtensionContext`, which carries shared extension resources such as model access. If the context has no model, it raises an error. If a model is present, it wraps that model in `ModelAccessLeg`, uses it to create a `PromptProposer` and a `CandidateEvaluation`, puts those into an `ImproveCron`, and awaits the cron run. The result is no returned value, but the scheduled improvement process is carried out.

**Call relations**: The scheduler calls this function as the job handler declared by `manifest`. Inside, it constructs `ModelAccessLeg` to provide metered model use, passes that into `PromptProposer` for making candidates, passes it into `CandidateEvaluation` for replay and judging, then hands both pieces to `ImproveCron`, which takes over the actual run.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 32–44)

```
def manifest() -> Manifest
```

**Purpose**: This function declares the self-improvement extension to the host system. It says what the extension is called, what version it is, and which scheduled job should be installed.

**Data flow**: It starts from fixed constants: the extension name, version, job name, and cron schedule. It asks `trajectory_workspaces()` for the workspace candidates the job should apply to, builds a `JobSpec` that points to `_tick` as the job’s handler, and wraps that job inside a `Manifest`. The returned manifest is the host system’s description of this extension.

**Call relations**: The extension loading flow calls this function to discover what the extension wants to register. While building that answer, it calls `trajectory_workspaces()` to choose the job’s target workspaces, creates a `JobSpec` for the scheduled evaluation job, and then creates the final `Manifest` that the host system can install.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### User Skill Manifest
Registers the extension that lets workspace members create, manage, persist, and later reload their own skills.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `extension registration, manifest apply, and per-turn skill loading`

This file is the front door for the “skill_create” extension. A skill here means a small bundle of text files, including a required SKILL.md file, that teaches the agent some reusable behavior. Without this file, users could still draft skill files in the workspace, but there would be no supported way to turn those drafts into saved, validated, loadable skills.

The main idea is simple: a user writes files in the temporary conversation workspace, then applies a manifest that says which files belong to the skill. The file contents are copied into durable storage as plain text. This matters because the workspace sandbox is temporary, so saved skills cannot keep pointing back to it. It is like photocopying important notes out of a scratch notebook before the notebook is thrown away.

The file defines the shape of a user skill spec, including three ways to describe a file: inline text, a workspace file reference, or a digest reference that keeps an existing stored file unchanged. The SkillObjects class provides the operations for the skill object kind: listing saved skills, reading metadata, saving updates, deleting skills, and resolving file references safely. It enforces limits on file count and total size, rejects non-text files, and asks the skill parser to validate SKILL.md. Finally, manifest() registers this object kind, the built-in create-skill teaching skill, and a runtime hook that reloads saved user skills into the skill registry on each turn.

#### Function details

##### `_require_ext`  (lines 93–96)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure a tool call has the extension context it needs. The extension context is the object that carries extension-specific state, such as the workspace store.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it stops the operation with an error, because the skill object code cannot safely read or write saved skills without that context.

**Call relations**: The skill object methods call this before touching persistent skill storage. It acts like checking that you have the right key before opening the filing cabinet.

*Call graph*: called by 5 (_files, apply, delete, list, status).


##### `_text`  (lines 99–105)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This function confirms that a skill file is plain UTF-8 text. Skills in this system are text bundles only, so binary files are rejected before saving.

**Data flow**: It receives a skill-relative path and raw bytes. It tries to decode those bytes as text. If decoding works, it returns the decoded string; if not, it raises a clear error naming the file that is not text.

**Call relations**: SkillObjects._resolve calls this after gathering file contents from inline text, stored files, or workspace files. It is the final text-only safety check before the resolved files can be saved.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 115–127)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: This lists saved user-created skills for the current workspace, optionally filtered by a search string. It returns a small page of names and short summaries so callers can browse without loading every file body.

**Data flow**: It receives the tool context, a query string, and a cursor used for paging. It loads all saved skills for the workspace, keeps only those whose name or description contains the query, cuts the result down to one page, and returns rows with the skill name and a shortened description plus a next-page cursor when more results remain.

**Call relations**: When the object system needs to show available skill objects, it calls this method. The method first uses _require_ext to get workspace information, then asks UserSkillStore for saved skills, and finally packages the result as ObjectPage and ObjectRow objects for the object listing API.

*Call graph*: calls 1 internal fn (_require_ext); 3 external calls (__init__, __init__, __init__).


##### `SkillObjects.get`  (lines 129–138)

```
async def get(self, ctx: ToolContext, name: str) -> UserSkillSpec | None
```

**Purpose**: This returns a saved skill’s file list without exposing the full file contents. Instead of sending the text back directly, it returns a checksum for each file so unchanged files can be kept on a later update.

**Data flow**: It receives the tool context and skill name. It loads the stored files; if the skill does not exist, it returns nothing. If it exists, it computes a SHA-256 digest, which is a stable fingerprint, for each file and returns a UserSkillSpec containing FileRef entries with each digest and size.

**Call relations**: The object system calls this when someone asks for the current saved form of a skill. It relies on SkillObjects._files to fetch the raw stored bytes, then converts them into safe references. Later, SkillObjects.apply can accept those references to keep unchanged files without echoing their contents into the conversation.

*Call graph*: calls 1 internal fn (_files); 3 external calls (__init__, __init__, sha256).


##### `SkillObjects.status`  (lines 140–151)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports human-useful facts about a saved skill, such as its description, number of files, total byte size, and last update time. It is meant for checking what exists without retrieving file contents.

**Data flow**: It receives the tool context and skill name. It loads the skill files; if there are none, it returns nothing. Otherwise, it parses the skill content to get the description, asks the store for the update timestamp, counts files and bytes, and returns those facts in a dictionary.

**Call relations**: The object system calls this for a status view of a skill. It uses SkillObjects._files for the stored file data, _require_ext for workspace storage access, UserSkillStore for the timestamp, and parse_skill_content to read the skill metadata from SKILL.md.

*Call graph*: calls 2 internal fn (_files, _require_ext); 2 external calls (__init__, parse_skill_content).


##### `SkillObjects.apply`  (lines 153–165)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None) -> None
```

**Purpose**: This saves a new or updated user skill after checking that it is safe and valid. It is the main write path for turning drafted workspace files into a persistent skill.

**Data flow**: It receives the tool context, skill name, proposed skill spec, and the old spec if one exists. It first rejects specs with too many files. Then it resolves all file values into actual bytes, checks the total size limit, and asks UserSkillStore to save the skill for this workspace while also passing the names of existing built-in or registered skills so the new one cannot shadow them.

**Call relations**: The manifest/object system calls this when a user applies a skill object manifest. This method delegates the complicated file-gathering work to SkillObjects._resolve, uses _require_ext to find the workspace store, and then hands the validated bundle to UserSkillStore.save for persistence and deeper skill validation.

*Call graph*: calls 2 internal fn (_resolve, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 167–169)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This removes a saved user-created skill from the current workspace. After deletion, that skill will no longer be loaded into later turns for this workspace.

**Data flow**: It receives the tool context and skill name. It gets the extension context, identifies the current workspace, and tells UserSkillStore to delete that skill record.

**Call relations**: The object system calls this when a user deletes a skill object. It uses _require_ext for workspace access and UserSkillStore for the actual removal.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._files`  (lines 171–173)

```
async def _files(self, ctx: ToolContext, name: str) -> dict[str, bytes] | None
```

**Purpose**: This is a shared helper that fetches the raw stored files for one saved skill in the current workspace. It keeps the storage lookup in one place so other methods do not repeat it.

**Data flow**: It receives the tool context and skill name. It gets the extension context, asks UserSkillStore for the files belonging to that skill in the current workspace, and returns either a mapping of file paths to bytes or nothing if the skill is not found.

**Call relations**: SkillObjects.get and SkillObjects.status use this to read existing skills. SkillObjects._resolve also uses it when an update wants to keep an existing file by digest instead of resending the file content.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_resolve, get, status); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 175–223)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns a proposed skill spec into actual file bytes ready to validate and save. It understands all three file forms: new inline text, a reference to a workspace file, or a reference to an unchanged stored file.

**Data flow**: It receives the tool context, skill name, and user skill spec. It first loads any existing stored files so FileRef entries can be checked against their SHA-256 fingerprints. It then gathers workspace file references, reads those files inside the sandbox with size and regular-file checks, decodes the returned base64 data, combines all file sources into one resolved mapping, verifies every file is UTF-8 text, and returns the final path-to-bytes bundle.

**Call relations**: SkillObjects.apply calls this before saving a skill. Inside, it calls SkillObjects._files to support unchanged-file references and _text to enforce text-only content. It also uses workspace_path and the sandbox command to safely read files from the conversation workspace rather than from the host system.

*Call graph*: calls 2 internal fn (_files, _text); called by 1 (apply); 6 external calls (b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 250–253)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads the current workspace’s saved user skills so they can be added to the turn’s skill registry. That makes a previously saved skill available to load and mention in prompts on later turns.

**Data flow**: It receives the extension context. It asks UserSkillStore for all saved skills in the current workspace and returns them as runtime skill objects.

**Call relations**: The Manifest created by manifest() registers this function as the runtime_skills provider. Core calls it during turn setup, then merges the returned user skills beside built-in and pack-provided skills.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 256–263)

```
def manifest() -> Manifest
```

**Purpose**: This builds and returns the extension manifest, which tells the host system what this extension provides. It registers the skill object kind, the bundled create-skill teaching skill, and the hook for loading saved user skills at runtime.

**Data flow**: It takes no input. It creates a Manifest with the extension name and version, includes the SKILL_OBJECT definition, points to the bundled create-skill skill directory through SkillSpec, attaches _runtime_skills as the per-turn loader, and returns the finished manifest.

**Call relations**: The extension loader calls this when discovering the extension. The returned Manifest is how the rest of the system learns that this file contributes a new object kind and a runtime skill provider.

*Call graph*: 2 external calls (__init__, __init__).

# Agent skill and content-work extension manifests  `stage-3.4`

This stage is the system’s extension registry. It is mostly used during startup and shared behind the scenes, when UFO needs to discover what extra abilities are available. The small __init__.py files are like nameplates on folders: they mark brief_pipeline, coding, self_improvement, sites, and skill_create as importable Python packages, and sometimes add a short description.

The manifest.py files are the real sign-up sheets. Each one tells the host system what an extension adds and where to find its instructions. The brief pipeline manifest registers three helper agents for brief-making work. Browser adds a browsing subagent, web tools, and delegation prompts. Coding registers software-work child agents and coding guidance. Documents adds document skills and a writing agent. Research declares search tools, research agents, prompts, skills, and its required search backend. Sites registers website-building tools, agents, hooks, object types, surfaces, and background jobs. Skill creation enables members to save, edit, search, and index reusable skills. Self-improvement adds a scheduled evaluation job, like a timer that regularly checks and improves the system.

## Files in this stage

### Brief pipeline agents
Package marker and manifest for the brief-pipeline extension that adds its helper agents and usage instructions.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import time`

This is the package entry file for the brief pipeline extension. In Python, a folder becomes an importable package when it contains an `__init__.py` file. This file does not run any setup code, define any classes, or expose any helper functions. Its only content is a short docstring: “Brief pipeline extension.”

Even though it looks almost empty, it still matters. Without this file, older Python tooling or package-discovery code might not recognize the folder as a package. That could stop other parts of the project from importing modules inside `ufo_ext_brief_pipeline`.

You can think of it like a label on a drawer. The label does not contain the tools, but it tells the system that the drawer exists and what it is generally for. The actual behavior of the brief pipeline extension lives in other files in this package.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `extension discovery / startup`

This file exists so the brief-pipeline extension can be discovered and loaded in a standard way. Think of it like a label on a toolbox: it says what the toolbox is named, what version it is, what tools are inside, and where the instruction sheet lives.

The extension describes a writing workflow made of three typed subagents: one to create an outline, one to turn that outline into a draft, and one to critique the draft. The parent agent then uses the critique to improve the result itself. This file does not run that workflow directly. Instead, it registers the pieces that make the workflow available to the rest of the system.

It imports the three subagent profiles from the pipeline module, sets the extension name and version, and points to a skills folder on disk. The key function, `manifest`, packages all of that into a `Manifest` object. A manifest is a structured description the host system can read when it starts up or scans extensions. Without this file, the extension’s agents and skill would exist in code, but the main system would not know to offer or load them.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the formal description the host system uses to load this extension. It names the extension, gives its version, lists the three subagent profiles, and points to the skill directory.

**Data flow**: It reads the file-level constants for the extension name, version, and skill folder, plus the imported outline, draft, and critic profiles. It wraps the skill folder in a `SkillSpec`, then puts the name, version, subagents, and skill specification into a `Manifest`. The result is a complete manifest object that the larger system can consume.

**Call relations**: When the host system asks this extension what it provides, this function is the answer. Inside, it creates a `SkillSpec` to describe the on-disk skill package, then creates a `Manifest` to bundle that skill together with the three subagent profiles.

*Call graph*: 2 external calls (__init__, __init__).


### Browsing and coding helpers
Extension declarations for browser delegation and software-work child agents.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup / extension load`

This file is like the label and instruction card on a toolbox. It does not do the browser work itself. Instead, it declares what is available when the browser extension is loaded.

The browser extension has two kinds of tools. Some are direct browser or computer-use tools, meant only for the browser subagent. Others are delegation tools, such as tools that let the main agent ask a browser-focused child agent to do web work. This separation matters because the main agent should not directly control the full browser surface. It delegates browser tasks to a specialized subagent instead.

The file also reads a Markdown prompt section from `prompts/browser_section.md`. That text is added to the main agent’s instructions, so the agent knows when and how to use browser delegation.

The `manifest()` function gathers all of this into a `Manifest`: the extension name and version, the tools it contributes, the browser subagent profile, the prompt section, and a required dependency named `cdp_providers`. CDP means Chrome DevTools Protocol, a way to control browsers programmatically. Without this manifest, the system would not know that the browser extension exists or how to connect its tools, prompts, and subagent.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension’s declaration for the UFO system. Someone uses it when loading the extension so the system can discover the browser tools, browser subagent, prompt instructions, and required browser-control support.

**Data flow**: It starts with constants in this file, imported tool lists, the imported browser subagent profile, and prompt text read from disk when the module is loaded. It wraps the prompt text in a `PromptSection`, then places everything into a `Manifest`. The result is a single structured object that tells the host system what this extension contributes.

**Call relations**: When the extension is being registered, this function is the place that packages the browser extension for the rest of the system. Inside that packaging step, it creates a `PromptSection` for the browser instructions and a `Manifest` that contains the tools, subagent, prompt section, and dependency requirement.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means other parts of the project can import code from inside it using names like `ufo_ext_coding.some_module`. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label tells Python that the drawer belongs to the import system. Because the file is empty, it does not set up configuration, expose shortcuts, run startup code, or change behavior when the package is imported. Its main value is structural: without it, some Python environments or tooling might not recognize `extensions/coding/ufo_ext_coding` as an importable package.


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `extension discovery and startup`

This file is like the label and instruction card on a toolbox. It does not edit code itself. Instead, it tells the UFO system what tools and worker profiles are available when a main agent needs help with programming tasks.

The main profile is called `coding`. A parent agent can start it with an objective, and the child agent can explore a repository, read and change files, run shell commands, search, and report back. The child works in the shared workspace; it does not directly deliver files to the end user. That keeps the parent agent in charge of deciding what result is ready to show.

The file also defines a stronger fallback profile called `fable_escalation`. This is meant for hard pull-request blockers after normal coding workers have failed. It uses the same basic input and output shape, but a different prompt and pinned model.

Two small data models, `CodingInput` and `CodingOutput`, define the contract for these child agents: the parent sends an `objective`, and the child returns a freeform `result`. Finally, the `manifest()` function packages all of this into a `Manifest`, including the extension name, version, internet access in the sandbox, subagent profiles, and the path to the coding skill.

#### Function details

##### `manifest`  (lines 105–112)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the system-readable summary of what this coding extension offers. The system uses it to discover the coding subagents, their tools, and the skill files they can load.

**Data flow**: It starts with constants and objects already defined in this file: the extension name and version, the two subagent profiles, the skill folder path, and the skill names. It turns each skill name into a `SkillSpec`, then places those skill specs and the subagent profiles into a `Manifest`. The result is a complete manifest object that the host system can read when loading the extension.

**Call relations**: When the extension is being loaded, the host calls `manifest` to ask, “What do you provide?” Inside that answer, it creates `SkillSpec` entries for the skill paths and a `Manifest` object that gathers the extension’s settings, internet permission, subagents, and skills into one package.

*Call graph*: 2 external calls (__init__, __init__).


### Document and research workflows
Manifests that register document-writing capabilities and research tools, agents, prompts, skills, and search requirements.

### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `extension discovery and startup configuration`

This file is like the packing list for the documents extension. Without it, the wider UFO system would not know that this extension contains skills for making and reviewing Word files, PowerPoint files, spreadsheets, PDFs, themes, and written drafts. It also would not know about the special “writing” subagent, which is a focused helper used for drafting and editing prose.

The file names the extension as `documents`, gives it a version, and points to the folder where its skills live. Each skill is stored as its own folder under `skills/`. The system can later read those folders and make the skills available when needed. Some of these skills build on shared design rules, so documents can still look consistent even when a user does not give detailed style instructions.

The main job happens in `manifest()`. It returns a `Manifest`, which is the structured description the extension loader expects. That manifest includes the extension name, version, the writing subagent profile, and one `SkillSpec` for each skill folder. In plain terms, this file answers: “What does this extension bring with it, and where can the system find each part?”

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the documents extension. The system uses this to discover which document skills and subagents the extension provides.

**Data flow**: It starts with constants in this file: the extension name, version, skills folder, list of skill names, and the writing subagent profile. It turns each skill name into a `SkillSpec` pointing at that skill’s folder, then packages everything into a `Manifest`. The result is a single object that says what this extension contributes.

**Call relations**: When the extension loader asks this file for its manifest, this function creates the answer. It calls `SkillSpec.__init__` once for each listed skill so each folder becomes a loadable skill entry, then calls `Manifest.__init__` to bundle those skill entries together with the extension metadata and the writing subagent profile.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file is like a packing list for the research add-on. It does not run searches itself. Instead, it tells the larger application, “If you enable the research pack, here are the pieces you should load.” Those pieces include web-research tools, special research-focused subagents, a prompt section about web use, skill folders that can be loaded when needed, and a conversation slot for saving source information.

The file also reads a Markdown prompt file called `web_section.md` when the module is loaded. That text becomes the research pack’s web prompt section, which can be added to the agent’s instructions.

One important detail is the `requires=("search_providers",)` setting. The research pack depends on some separate search backend being configured. It does not own search credentials itself. This means the system can fail early during startup if research is enabled but no search provider is available, instead of waiting until the first web search and failing later.

In short, this file is the research extension’s registration form. Without it, the application would not know which research tools, skills, prompts, or subagents belong to the pack, nor that a search provider must be present.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s manifest, which is the object the main system reads to know what this extension offers. Someone uses this when the application is discovering or loading extensions.

**Data flow**: It starts with constants defined in this file and imports from nearby research modules: the extension name and version, available tools, subagent profiles, prompt text, skill folder names, and the sources conversation slot. It packages those pieces into a `Manifest` object, creating small entries for the prompt section and each skill folder along the way. The result is a complete description of the research pack that the rest of the system can load.

**Call relations**: During extension loading, the larger system calls `manifest` to ask this file what should be registered. Inside that answer, it creates a prompt section for the web instructions, creates skill specifications for each research skill directory, and hands everything to the manifest object so the application can add the tools, subagents, skills, and dependency requirement to its startup configuration.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Self-improvement jobs
Package marker and orchestration manifest for scheduled self-improvement evaluation work.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `package import or extension discovery`

This file is mostly a label and a short explanation. In Python, an `__init__.py` file marks a folder as a package, which means other parts of the system can import code from that folder. Here, the file’s only content is a docstring, which is a plain text note stored in the module. That note says this extension supports an offline replay evaluation loop: it looks back at recorded workspace activity, called trajectories, and uses them to suggest prompt changes. Importantly, those changes are not applied automatically. They are opened in a governed approval flow so a human member can review and approve them. In everyday terms, this extension is like a training review process: it studies past work, proposes better instructions for next time, and asks a person before changing the playbook. Without this file, the package may be less clearly documented or, depending on packaging rules, not recognized as a normal Python package.


### `extensions/self_improvement/ufo_ext_self_improvement/manifest.py`

`orchestration` · `startup registration, then scheduled job execution`

This file is the extension’s sign-up sheet. Without it, the larger system would not know the self-improvement extension’s name, version, or scheduled work, so its evaluation loop would never run.

The job it declares is a cron job, meaning it runs on a clock schedule rather than in response to a user action or a database write. When the schedule fires, the job checks that model access is available. A model here means the language model service the extension uses to propose prompt changes, replay old conversations, and judge results. If no model is connected, the job stops with a clear error instead of silently doing incomplete work.

When model access is present, the file wraps it in `ModelAccessLeg`, then builds the three main parts of the self-improvement cycle: a `PromptProposer` to suggest changes, a `CandidateEvaluation` to test and judge them, and an `ImproveCron` object to run the overall sequence. The manifest also marks the job as needing the deploy model, not a cheaper background model, because replaying an archived transcript can be large and must match the context size the transcript was prepared for.

#### Function details

##### `_tick`  (lines 26–34)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job body: it runs one self-improvement evaluation cycle. It makes sure model access exists, then wires together the proposer, evaluator, and cron runner that do the actual work.

**Data flow**: It receives an `ExtensionContext`, which is the job’s bundle of runtime services and workspace information. It reads `ctx.model`; if that is missing, it raises an error because the extension cannot propose or evaluate anything without a language model. If the model is present, it wraps that model in `ModelAccessLeg`, passes the wrapper into `PromptProposer` and `CandidateEvaluation`, gives both to `ImproveCron`, and awaits the cron runner’s `run` method. The visible output is no returned value; the result is that the improvement cycle has been attempted using the context’s model access.

**Call relations**: The job system calls this function when the manifest’s scheduled job fires. Inside that moment, `_tick` creates the model-access wrapper, then hands it to the proposer and evaluator, and finally gives those parts to `ImproveCron`, which takes over the actual proposer, replay, and grading flow.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `manifest`  (lines 37–50)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s declaration to the host system. It says the extension is named `self_improvement`, gives its version, and registers its scheduled evaluation job.

**Data flow**: It takes no input. It uses the file’s constants for the extension name, version, job name, and cron schedule, asks `trajectory_workspaces()` for the candidate workspaces the job may run against, builds a `JobSpec` with `_tick` as the handler, and wraps that job inside a `Manifest`. The output is the `Manifest` object the host can read during extension loading.

**Call relations**: The host system calls `manifest` when it discovers or loads this extension. `manifest` creates the `JobSpec` that points back to `_tick`, so later, when the scheduler reaches the configured time, the host knows exactly which function to run and which workspaces and model type it needs.

*Call graph*: 3 external calls (__init__, __init__, trajectory_workspaces).


### Site creation extension
Package marker and manifest for website-building tools, agents, prompts, hooks, surfaces, objects, and background jobs.

### `extensions/sites/ufo_ext_sites/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `extensions/sites/ufo_ext_sites` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools in other files, and this label lets the rest of the system find them reliably. Because the file is empty, it does not run setup code, create objects, or change settings. Its value is structural: without it, depending on the Python version and import style, code that expects `ufo_ext_sites` to be a regular package might fail to import modules from this directory.


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `extension load and startup`

This file does not build websites itself. Instead, it declares everything the website feature needs so the main system can discover and use it. Think of it like the contents page and setup card for a toolbox: it says which tools are inside, what instructions should be shown to the agent, which specialist helper agents are available, and which safety checks must run before certain actions.

The manifest includes normal site tools, delegation tools for handing work to a website-building subagent, and application-builder tools for designing, editing, reading, writing, testing, and deploying web apps. It also registers the “site” object type and the “sites” surface, which are the parts that let hosted sites appear and be controlled through the user interface.

It loads a prompt section from a Markdown file, so the main agent gets guidance on how to serve, validate, and return hosted website links. It also points to the bundled “website-building” skill directory, which contains reusable website-building instructions and assets.

A key part of this file is its hooks. A hook is a checkpoint that runs before a tool is used. These hooks keep website and application creation on the intended path, enforce the right build phase, limit certain repair reads, and require quality assurance before deployment. Finally, it registers a scheduled cleanup job that releases a homepage page once the chat app becomes the main agent’s active home.

#### Function details

##### `manifest`  (lines 64–123)

```
def manifest() -> Manifest
```

**Purpose**: Creates and returns the complete manifest for the sites extension. The larger system uses this manifest to know what this extension contributes and how those pieces should be wired into the running app.

**Data flow**: It starts from constants and imported pieces: tool definitions, subagent profiles, object and surface definitions, prompt text read from disk, skill folder paths, hook functions, conversation slot information, and scheduled job settings. It packages those pieces into a single Manifest object. The result is a structured declaration that the host system can read to enable website building, website serving, application-building safeguards, and cleanup work.

**Call relations**: When the extension is loaded, the system calls this function to ask, “What do you provide?” The function builds smaller declaration objects for prompt text, skill loading, pre-tool-use checks, and the scheduled homepage release job, then hands the finished Manifest back to the host. It also asks the main-homepage code for candidate workspaces that may need cleanup, so the scheduled job knows where to look later.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, unreleased_main_homepage_workspaces).


### Saved skill management
Package marker and orchestration manifest for creating, saving, editing, searching, and indexing agent-owned skills.

### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This file is the front door label for the `ufo_ext_skill_create` package. In Python, an `__init__.py` file tells the language that a folder should be treated as an importable package. Here, the file does not define any functions or classes. Its only content is a short documentation string explaining the package’s theme: it deals with skills that an agent can author as reusable objects, and skills that exist only for the current interaction or turn. Think of it like the sign on a toolbox drawer. The sign does not contain the tools, but it tells readers and Python itself what kind of tools belong inside. Without this file, depending on the Python setup, importing this folder as a package could be less explicit or fail in older environments, and newcomers would lose a small but useful clue about the package’s purpose.


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `extension startup, object requests, tool calls, and scheduled background indexing`

A “skill” here is a small bundle of text files, led by a required SKILL.md file, that teaches the assistant how to do a task later. This file is the front door for those workspace-owned skills. Without it, member-authored skills could not be exposed as editable objects, loaded during future turns, searched by keyword, or indexed in the background.

The file defines the shape of a saved skill: its files, whether it is pinned, and safe ways to refer to existing content without copying file bodies into normal object views. It also defines SkillObjects, which is the object-store bridge for listing, reading, saving, checking, and deleting skills. Saving is careful: file paths must stay inside the skill’s own folder, files must be text, the bundle cannot be too large, and stale edits are rejected using a generation value, like a version stamp on a shared document.

It also provides a skill_search tool. That tool searches the short “routing cards” for all loadable skills and returns only names and descriptions, not full skill bodies. Finally, it defines a scheduled indexing job. The job finds saved skills whose search index is out of date, turns their descriptions into searchable chunks, and marks them indexed only if the skill did not change meanwhile.

#### Function details

##### `_require_ext`  (lines 119–122)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This is a small safety check that makes sure an ExtensionContext is present before skill storage is used. The ExtensionContext is the runtime information for the current workspace, such as database access and services.

**Data flow**: It receives either an ExtensionContext or nothing. If the context is present, it returns it unchanged; if it is missing, it raises an error instead of letting later code fail in a confusing way.

**Call relations**: The object methods call this at the start of list, get, save, delete, status, and resolve operations. It acts like checking you have the right key before trying to open the workspace’s skill cabinet.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 125–132)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every file path in a skill stays inside that skill’s own folder. It prevents a saved skill from pretending that files elsewhere in the workspace belong to it.

**Data flow**: It takes the skill name and the proposed skill spec. It computes the allowed root folder for that skill, checks each file path against it, and either finishes silently or raises a clear error for an unsafe path.

**Call relations**: SkillObjects.apply calls this before saving anything. It relies on the shared path-safety helpers to do the actual containment check, so the save flow stops early if a file path tries to escape.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_root).


##### `_text`  (lines 135–141)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This makes sure a skill file is normal UTF-8 text. Skills in this system are text bundles, not arbitrary binary attachments.

**Data flow**: It receives a file path and raw bytes. It tries to decode the bytes into text; if decoding works, it returns the text, and if not, it raises an error naming the bad file.

**Call relations**: SkillObjects._resolve uses this after gathering each file’s bytes. This means inline content, kept stored files, and files read from the workspace all pass the same text-only gate.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved workspace skills for tool/object callers. It shows compact rows, not full file contents.

**Data flow**: It receives a tool context and a list query. It checks for the extension context, fetches all saved skill rows, applies the requested paging/filtering through object_page, and returns an ObjectPage.

**Call relations**: This is the object-kind list entry used when the system asks for skill objects. It delegates the storage read to SkillObjects._rows and the paging shape to the shared object_page helper.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 151–162)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same kind of saved-skill list for a signed-in member viewing skills outside an assistant turn. Skills belong to the workspace, so the member sees the workspace set rather than a private personal set.

**Data flow**: It receives an optional extension context, member information, admin status, and a query. It requires the context, reads the saved skill rows, pages them, and returns the page.

**Call relations**: The member portal flow calls this when it needs a browseable list. It uses the same _rows source as SkillObjects.list, keeping turn-time and portal views consistent.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 164–165)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches one saved skill’s object detail. It returns file references by digest and size, not the file bodies themselves.

**Data flow**: It receives a tool context and a skill name. It checks the extension context, asks _skill for the stored detail, and returns either that detail or None if the skill is absent.

**Call relations**: Object-read flows call this when someone asks for a specific skill. It hands the actual loading work to SkillObjects._skill so the same detail-building logic can be reused elsewhere.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 167–185)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This builds the member-portal view of one saved skill, combining the list row with the detailed spec. It still avoids exposing raw file contents.

**Data flow**: It receives an extension context, skill name, member information, and admin status. It finds the matching row, fetches the detailed skill record, and wraps both into a MemberObject; if either piece is missing, it returns None.

**Call relations**: The member-facing detail view calls this after a user chooses a skill. It reuses _rows for the visible summary and _skill for digest-based file references, so the portal shows the same information as object tools.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 187–195)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This turns stored skill records into simple list rows. Each row includes the skill name, a shortened description, and whether it is pinned.

**Data flow**: It receives an ExtensionContext, opens the UserSkillStore for that workspace, reads the listing, and converts each stored item into an ObjectRow tuple.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail all use this as their common list source. It is the small adapter between database-backed skill records and object-list display rows.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 197–212)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the safe detailed object view for one skill. Instead of returning file contents, it returns each file as a SHA-256 digest, which is a stable fingerprint of the bytes, plus its size.

**Data flow**: It receives an ExtensionContext and a skill name. It loads the stored record, computes a digest and size for each stored file, wraps those in a UserSkillSpec, and returns ObjectDetail with timestamps and generation; if no record exists, it returns None.

**Call relations**: SkillObjects.get and SkillObjects.member_detail use this whenever they need one skill’s detail. Its digest output also supports later edits, because callers can send the digest back to keep an unchanged file.

*Call graph*: called by 2 (get, member_detail); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 214–231)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This returns a quick health-style summary of a saved skill, such as description, file count, total bytes, and pinned state. It also checks that the caller is looking at the expected version.

**Data flow**: It receives a tool context, skill name, and expected generation. It loads the record, returns None if missing, raises an error if the generation does not match, and otherwise returns a small dictionary of status fields.

**Call relations**: Object status flows call this after reading or editing a skill. The generation check protects against reporting status for an older version after another writer has changed the skill.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects.apply`  (lines 233–256)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This saves a new or updated workspace skill. It validates size limits, path safety, file contents, and edit version before handing the final files to storage.

**Data flow**: It receives a tool context, skill name, proposed spec, optional old spec, and expected generation. It checks the extension context, rejects too many files, verifies file paths, resolves all file values into bytes, enforces the total byte limit, and saves the result with pinned state and generation.

**Call relations**: Manifest object-apply flows call this when a member creates or edits a skill. It uses _contained_keys for path safety, _resolve to collect actual file bytes, and UserSkillStore.save to persist the final bundle.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 258–269)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This deletes a saved skill, but only if the caller’s generation value is still current. That avoids deleting a skill that someone else just changed.

**Data flow**: It receives a tool context, skill name, and expected generation. It loads the current record, compares generations if the record exists, raises an error on a stale generation, and otherwise asks the store to delete the skill.

**Call relations**: Object-delete flows call this. Like apply and status, it uses _require_ext and UserSkillStore so deletion is tied to the current workspace and protected against stale writes.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 271–319)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns the flexible file values in a submitted skill spec into the actual bytes that will be saved. A file can be inline text, a reference to an existing stored digest, or a request to read a workspace file.

**Data flow**: It receives a tool context, skill name, and spec. It loads currently stored files for digest references, verifies that kept files really match their SHA-256 fingerprints, reads any workspace-source files inside the sandbox, decodes the sandbox’s base64 output back to bytes, checks every file is text, and returns a path-to-bytes dictionary.

**Call relations**: SkillObjects.apply calls this before saving. It is the main translation step between the user-friendly manifest format and the storage format; it calls _text for the final text check and uses the sandbox so workspace file reads happen in a controlled environment.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `skill_search`  (lines 370–387)

```
async def skill_search(ctx: ToolContext, args: SkillSearchInput) -> ToolResult
```

**Purpose**: This is the tool handler that searches loadable skills by keyword. It returns short “name: description” lines that can guide the caller to load the right skill.

**Data flow**: It receives the tool context and search input. It reads all available skill cards, scores each card against the query with a simple word-based scorer, keeps the best positive matches up to the requested limit, and returns either matching lines or a no-match message with the total searched.

**Call relations**: The SKILL_SEARCH_TOOL calls this when a user or agent invokes skill_search. It does not load skill bodies; it only points to likely skills so another flow can load a selected skill by name.

*Call graph*: 3 external calls (__init__, __init__, lexical_score).


##### `_member_cards`  (lines 404–405)

```
async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]
```

**Purpose**: This returns the saved workspace skills as lightweight cards used for routing and discovery. A card is the small name-and-description summary used to decide whether a skill might help.

**Data flow**: It receives an ExtensionContext, opens the UserSkillStore for the workspace, asks for the cards, and returns them as a tuple.

**Call relations**: The manifest registers this with MemberSkillsSpec. When the runtime needs member-authored skill cards, it calls this function through that registration.

*Call graph*: 1 external calls (__init__).


##### `_materialize_skill`  (lines 408–409)

```
async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one saved workspace skill into its runtime form. The runtime form is what the assistant can actually use during a turn.

**Data flow**: It receives an ExtensionContext and a skill name. It asks UserSkillStore to materialize that skill and returns either a RuntimeSkill or None if it cannot be found.

**Call relations**: The manifest registers this as the single-skill materializer. When the runtime decides it should load a named member-authored skill, this function is the bridge from stored files to usable skill.

*Call graph*: 1 external calls (__init__).


##### `_materialize_all_skills`  (lines 412–413)

```
async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads all saved workspace skills into runtime form. It is used when the system needs the full set of member-authored skills available.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore to materialize every saved skill in the workspace and returns them as a tuple.

**Call relations**: The manifest registers this with MemberSkillsSpec. Runtime skill-loading code calls it when it wants the whole workspace skill set rather than one named skill.

*Call graph*: 1 external calls (__init__).


##### `index_skills`  (lines 416–465)

```
async def index_skills(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job keeps saved skill descriptions searchable. It finds skills whose search-index version is missing or stale, indexes their routing text, and records that indexing caught up.

**Data flow**: It receives an ExtensionContext with database, index, and embedding services. It refuses to run if index or embedding support is not configured, queries the database for stale skill rows, tries to index each one, logs per-skill failures, and raises the last failure only if nothing was successfully settled.

**Call relations**: The JobSpec created in manifest points to this handler. For each stale row it calls _index_card, so a single bad skill does not normally stop the whole tick, but a system-wide indexing failure is still visible.

*Call graph*: calls 2 internal fn (transaction, _index_card); 3 external calls (__init__, or_, select).


##### `_index_card`  (lines 468–505)

```
async def _index_card(ctx: ExtensionContext, index: IndexBackend, embed: EmbedClient, chunker: TextChunker, row: sa.Row) -> None
```

**Purpose**: This indexes one skill’s routing card and then carefully marks that exact version as indexed. If the skill changed or vanished during indexing, it avoids marking the wrong version as complete.

**Data flow**: It receives the extension context, index backend, embedding client, text chunker, and a database row. It turns the skill name and shortened description into searchable chunks, upserts them into the index, then updates indexed_digest only if the stored digest still matches; if the row disappeared, it deletes that skill’s index scope.

**Call relations**: index_skills calls this for each stale skill. It hands the text to chunk_embed_upsert for embedding and indexing, then uses a guarded database update so background indexing does not race incorrectly with edits or deletes.

*Call graph*: calls 2 internal fn (transaction, delete); called by 1 (index_skills); 4 external calls (__init__, select, update, chunk_embed_upsert).


##### `_skills_awaiting_index`  (lines 508–518)

```
def _skills_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with skills needing indexing. It is used to decide which workspace owners should receive the scheduled indexing job.

**Data flow**: It takes no runtime input. It returns a SQL query selecting distinct workspace IDs where a saved skill has no indexed digest or an indexed digest that differs from the current digest.

**Call relations**: manifest passes this query builder into owner_candidates when creating the indexing JobSpec. That lets the job scheduler target only workspaces that actually have stale skill index work.

*Call graph*: 2 external calls (or_, select).


##### `manifest`  (lines 521–541)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s registration function. It tells the host system the extension name, tools, object type, built-in authoring skill, member-skill loaders, and background job.

**Data flow**: It takes no input. It constructs and returns a Manifest containing the skill_search tool, the skill object kind, the bundled create-skill skill, callbacks for saved workspace skills, and the scheduled skill-index job.

**Call relations**: The extension loader calls this at startup or extension discovery time. Everything else in the file becomes reachable through the Manifest it returns: object requests go to SkillObjects, tool calls go to skill_search, runtime skill loading goes to the member-skill callbacks, and scheduled indexing goes to index_skills.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).

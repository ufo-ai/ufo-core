# Built-in workspace app and skill bundle registration  `stage-3.1`

This stage is part of startup and behind-the-scenes setup. It is where UFO tells itself which built-in workspace apps, agents, and skills are available before users start working. Most files here are manifests, meaning simple registration cards. They name an app, describe what it does, declare its home-screen skill, and list any setup, permissions, schedules, or helper skills it needs.

The app manifests register the main user-facing workspace tools: Artifacts for shared generated files and hosted sites, Chat for the main conversation agent, Code for pull request review, Issues for issue tracking work, Meetings for meeting support, Metrics for reports, Radar for monitoring signals, and Wiki for shared knowledge. The brief_pipeline manifest adds a chain of helper agent stages for producing briefs, while the documents manifest adds document-writing skills and a writing subagent.

The catalog_skill file is different: it builds a live “model catalog” skill, a table of available AI models, prices, limits, and features. The sample probe is a tiny test button that confirms a sample skill can run.

## Files in this stage

### Model catalog skill
Core built-in skill registration starts with the live model catalog users and agents can consult.

### `core/src/ufo/harness/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a simple but important problem: the list of usable models should not be written by hand in one place while the real runtime uses a different list somewhere else. If that happened, people could choose a model based on stale information, or documentation could claim a model exists when the system cannot actually run it.

Instead, this file turns the live model registry into a readable skill. The registry is the source of truth: it contains each model’s ID, provider, price, knowledge cutoff, context window, reasoning support, and API surface. At startup, this file reads those records and formats them into a Markdown table. Markdown is plain text with simple formatting, like tables and headings, that can be shown to users or loaded as instructions.

The result is a RuntimeSkill named “model-catalog”. A RuntimeSkill is a packaged block of instructions the runtime can load and use. Here, its instructions are not behavior rules but a catalog: a clear comparison sheet for models. An everyday analogy is a restaurant menu printed directly from the kitchen’s current inventory system, rather than typed separately by hand. Because both the menu and the kitchen use the same records, they stay in sync.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This small helper turns a stored price into a human-readable dollar amount per million tokens. A token is a small piece of text used by AI models, and “per million tokens” is a common way to show model pricing.

**Data flow**: It receives an integer price stored in micro-dollars, which are millionths of a dollar. It divides that number by the project’s constant for micro-dollars per dollar, formats the result with two decimal places, and returns a string such as "$1.25".

**Call relations**: When the catalog is being built, model_catalog_skill calls this helper for each model’s input and output prices. The helper keeps the table-building code readable and makes sure prices are displayed consistently.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the complete model catalog skill from the current ModelRegistry. Someone would use it at boot time to produce a trustworthy, readable list of the models this deployment can actually run.

**Data flow**: It receives a ModelRegistry containing model specifications. It sorts the models by ID, turns each one into a row in a Markdown table, formats prices through _per_mtok, wraps the table with a short explanation and skill metadata, and returns a RuntimeSkill object containing the final name, description, instructions, and raw Markdown text.

**Call relations**: This is the main builder in the file. It calls _per_mtok while writing each model row, then hands the finished text to RuntimeSkill so the rest of the system can load the catalog like any other runtime skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### Workspace app manifests
These manifests register the main built-in workspace apps, their agents, homepage skills, setup needs, and scheduled work.

### `extensions/app_artifacts/ufo_ext_app_artifacts/manifest.py`

`config` · `startup / extension discovery`

This file is the registration card for the Artifacts app. It does not build the homepage itself. Instead, it describes the app to the shared app system: its name, version, agent identity, icon, purpose, and the skill package that contains the homepage behavior.

In everyday terms, this is like the label and instruction sheet that comes with an appliance. The system reads it to learn: “There is an app called app_artifacts; create a workspace-visible agent named artifacts; give it this prompt; let it use this homepage skill.”

The long prompt is important because it tells the agent what the Artifacts app is supposed to be: a grid or table of workspace files and hosted sites, newest first, with search, filters, paging, a file viewer, downloads, and links back to conversations. It also tells the agent that if a user asks to change the page, it should load the app-artifacts-home skill and preserve the page’s controls.

Without this file, the extension would not announce itself properly. The system would not know to provision the Artifacts agent or where to find the skill that powers its homepage.

#### Function details

##### `manifest`  (lines 40–46)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the structured description the host system reads to install this app. It names the extension, gives its version, includes the Artifacts agent, and points to the homepage skill.

**Data flow**: It starts from the constants defined earlier in the file, such as the app name, version, prepared agent definition, and skill folder path. It packages those into a Manifest object, including a SkillSpec that points at the app-artifacts-home skill. The result is a complete manifest object that the rest of the system can read.

**Call relations**: When the extension system asks this module what it provides, this function is the answer. Inside, it creates a SkillSpec to describe the homepage skill, then creates a Manifest to bundle that skill together with the Artifacts agent provision.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_chat/ufo_ext_app_chat/manifest.py`

`config` · `startup / extension discovery`

This is the registration card for the Chat app extension. It does not draw the chat screen itself or send messages directly. Instead, it describes the Chat app to the platform so the platform can create it in the right place, with the right name, icon, instructions, and starting skill.

The file defines basic facts such as the extension name, version, and where its skill files live. It then builds an agent description for the workspace’s main chat agent. In plain terms, this says: “Create a visible workspace agent named chat, use the message-circle icon, make it the main agent, allow internet access, and give it these instructions.” Those instructions tell the agent that its home page is the chat conversation screen, including the transcript, message composer, live streamed replies, and starter prompts.

The important link is the `app-chat-home` skill. A skill is a packaged set of behavior or UI guidance that the agent can load when it needs to show or change the page. The `manifest` function wraps all of this into a `Manifest`, which is the standard object the host system reads when discovering extensions. Think of it like a shop sign plus setup sheet: it tells the building manager what this shop is, where to place it, and what staff instructions come with it.

#### Function details

##### `manifest`  (lines 36–42)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of this Chat app extension. The platform uses this description to know that the extension provides a main workspace chat agent and a home-screen skill.

**Data flow**: It starts with the constants defined in this file: the extension name and version, the prebuilt chat agent description, and the path to the `app-chat-home` skill. It packages the skill path into a skill description, combines that with the chat agent, and returns one complete manifest object for the host system to read.

**Call relations**: This function is called when the extension system asks the file, “What do you provide?” It creates the skill entry and the overall manifest, then hands that manifest back so the platform can register the Chat app and make its home skill available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_code/ufo_ext_app_code/manifest.py`

`config` · `startup / extension discovery`

This file is the app’s shipping label and setup sheet. Without it, the system would not know that this extension provides a workspace agent named “code”, what prompt that agent should use, what GitHub connections it needs, or which skill documents should be bundled with it.

The file defines the app’s name, version, agent name, purpose, setup instructions, and the location of its prompt and skills. The prompt is read from a Markdown file and placed into an AgentSpec, which is the recipe for the agent: what it is for, which model settings it uses, whether it can access the internet, how large its sandbox should be, and who can see it.

It also explains an important design choice: the pull-request “babysitting” behavior lives in a skill file, not directly in the prompt. A prompt is copied into an agent record when the agent is first created and may later be edited by a workspace member. A skill, by contrast, is shipped with the extension and can reach existing workspaces on the next deploy. In everyday terms, the prompt is like a personalized notebook page, while a skill is like an updated instruction manual that ships with the product.

Finally, the setup section says the agent needs GitHub connector access, the UFO GitHub App credential, and a source trigger so pull-request changes can wake the agent.

#### Function details

##### `manifest`  (lines 102–111)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the object the platform reads to learn what this app provides. It packages together the app name, version, agent definition, and skill files.

**Data flow**: It reads the module-level constants and prebuilt agent definition already created in the file. It then creates a Manifest object containing one agent and two SkillSpec entries that point to the home skill and babysitting skill folders. The result is a complete description of the extension that the UFO platform can load.

**Call relations**: When the extension system asks this package what it contributes, this function is the handoff point. Inside, it creates SkillSpec objects for the shipped skill paths and passes them, along with the Code app agent, into Manifest.__init__ so the platform receives one structured manifest.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_issues/ufo_ext_app_issues/manifest.py`

`config` · `startup / app registration`

This file tells the larger system how to install and run an Issues app for a workspace. The app is meant to watch one issue tracker, such as GitHub Issues, and help with two jobs: triaging new issues and, when explicitly approved, implementing selected issues.

The file defines the app’s name, version, skill folder, agent name, task names, and the special approval label `ufo:implement`. It then writes the agent’s instructions in human language. Those instructions are important because they set boundaries: triage is always active, but implementation only happens after a member asks for it and marks an issue with the approval label. This prevents the agent from writing code for work nobody approved.

It also defines a schedule for triage. Instead of reacting to every issue change, the app sweeps on a clock. That matters because the app comments on issues, and a comment itself changes the issue. If the app reacted directly to issue changes, it could wake itself up again and again. The schedule avoids that loop.

Finally, the file declares that the agent needs GitHub access, a GitHub app credential, scheduled-task support, and one homepage skill. Without this manifest, the system would not know this app exists or how to set it up.

#### Function details

##### `manifest`  (lines 129–135)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete description of the Issues app for the host system to load. It packages the app name, version, agent definition, and homepage skill into one manifest object.

**Data flow**: It takes no input. It reads the constants already defined in this file, creates a `SkillSpec` pointing to the homepage skill folder, then creates and returns a `Manifest` containing the app’s name, version, agent, and skill list. Nothing external is changed; the output is the manifest object the system can register.

**Call relations**: When the extension system asks this file what it provides, this function is the answer. It builds a `SkillSpec` for the homepage skill and hands that, together with the prebuilt `ISSUES_APP_AGENT`, to `Manifest.__init__`, so the wider platform can install the agent and expose its skill.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_meetings/ufo_ext_app_meetings/manifest.py`

`config` · `startup / extension discovery`

This file tells the platform how to install and present the Meetings app. The app is built around one workspace agent connected to one calendar. Its main active job is to prepare meeting briefs before meetings happen. Two other abilities, follow-ups and decision notes, are described in the agent’s instructions but are not turned on automatically; the member must ask for them first.

The file defines simple names and settings first: the app name and version, where its homepage skill lives, the calendar connector to request, and task names for briefs, follow-ups, and notes. It then writes the agent’s purpose and long operating instructions. Those instructions are important because they tell the agent not only what to do, but also what not to do: for example, it must not quietly perform follow-up or note-taking work unless that feature has been explicitly armed.

The schedule section sets up the briefing task. The key idea is a time window: each run briefs meetings that start before the next scheduled run and have not started yet. Like assigning mail to delivery routes, this prevents the same meeting from being briefed twice.

Finally, the file bundles all of this into an agent provision and exposes a `manifest()` function. Without this file, the platform would not know the Meetings app exists, what connector to ask for, what scheduled task to create, or which homepage skill belongs to it.

#### Function details

##### `manifest`  (lines 111–117)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal app manifest, which is the object the platform reads to discover this extension. It names the app, gives its version, registers the Meetings agent, and points to the app’s homepage skill.

**Data flow**: It reads the constants and prepared objects defined earlier in the file, such as the app name, version, agent setup, and skill folder path. It wraps the homepage skill path in a `SkillSpec`, then places that and the Meetings agent into a `Manifest`. The result is a complete description of the extension that the rest of the system can load.

**Call relations**: When the platform is discovering extensions, it calls `manifest` to ask this file what it provides. The function hands off to the manifest and skill specification constructors to package the already-defined settings into the standard shape the platform expects.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_metrics/ufo_ext_app_metrics/manifest.py`

`config` · `startup / extension discovery`

This file tells the platform how to install and present a workspace agent called "metrics." The agent's main job is to report how engineering delivery is going, such as what shipped, how long changes took, whether fixes followed, and what work is still open. Without this file, the system would not know that the Metrics app exists, what account it needs connected, what schedule to offer, or which homepage skill belongs to it.

Most of the file is configuration written as Python objects. It names the app, points to its skills folder, and defines three possible report areas: engineering, revenue, and support. Engineering is ready from the start. Revenue and support are intentionally dormant until a workspace member asks for them. That distinction matters because the prompt tells the agent not to invent or manually perform work for report sets that have not been formally enabled.

The file also sets up the scheduled engineering report. It offers sensible cadences, such as Monday morning or weekday mornings, instead of very frequent updates that people are unlikely to read. Finally, it declares that setup requires GitHub access, because the engineering delivery report depends on repository data. In everyday terms, this file is like the instruction sheet included with an appliance: it says what the appliance is, what power source it needs, what buttons it has, and what it should do when switched on.

#### Function details

##### `manifest`  (lines 107–113)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete manifest, which is the formal package description the platform reads to learn about this extension. It names the app version, registers the Metrics agent, and points the platform to the app's homepage skill.

**Data flow**: It takes no input from the caller. It reads the constants and prebuilt setup objects defined earlier in the file, wraps the homepage skill path in a SkillSpec, and returns a Manifest object containing the app name, version, agent definition, and skill list.

**Call relations**: When the platform discovers this extension, it calls manifest to ask, "What do you add to the system?" The function builds the final Manifest object and uses SkillSpec to describe the homepage skill path, then hands that complete description back to the extension loader.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_radar/ufo_ext_app_radar/manifest.py`

`config` · `startup / extension discovery`

This file is the Radar app's registration card. Without it, the shared app infrastructure would not know that the Radar app exists, what to call it, what icon to show, which agent to create, or which bundled skill contains its homepage.

The file defines a small set of constants: the app name and version, the folder where its skills live, the name of the homepage skill, and the visible agent name, "radar". It also defines the agent's instruction prompt. That prompt explains the app's job in human terms: show a feed of recent scheduled runs, let each run open into a fuller story, and keep the rebuild control working when members ask for page changes.

The central object is `RADAR_APP_AGENT`, an `AgentProvision`. In plain terms, that is a request to the platform: "please create this workspace-visible agent with this purpose, prompt, model setting, and icon." It also says the agent should not have internet access.

Finally, the `manifest()` function packages everything into a `Manifest`, which is the object the host system reads when it loads the extension. Think of it like a shipping label on a box: it tells the platform what is inside and how to wire it into the workspace.

#### Function details

##### `manifest`  (lines 38–44)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Radar app's extension manifest, which is the platform-readable description of what this extension provides. Someone uses it when the system is discovering extensions and needs to know which agents and skills should be registered.

**Data flow**: It starts with the file's predefined app name, version, agent definition, and skills folder path. It creates a `SkillSpec` pointing to the bundled homepage skill, then creates a `Manifest` containing the app identity, the Radar agent, and that skill. The result is a manifest object that the host platform can read to install or expose the app.

**Call relations**: When the extension is loaded, the surrounding app infrastructure calls `manifest` to ask, "what do you provide?" This function answers by constructing a `SkillSpec` for the homepage skill and a `Manifest` that includes the already-defined Radar agent provision. It hands that finished manifest back to the platform so the app can appear in the workspace.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_wiki/ufo_ext_app_wiki/manifest.py`

`config` · `startup / extension discovery`

This file is the Wiki app’s shipping label. When the platform discovers this extension, it reads this manifest to learn what the app is called, what version it is, which agent should be created, and which skill file supplies the app’s homepage behavior.

The Wiki app is meant to turn a workspace’s shared memory into a readable page. In plain terms, it creates a private “wiki” agent whose job is to present what the workspace knows: team practices, decisions, open work, history, facts, and the people roster. The long prompt in this file is the instruction card for that agent. It tells the agent what the homepage should look like and how to respond when someone asks to change or rebuild it.

The file also sets important boundaries. The app is private, does not get internet access, uses automatic model selection, and is represented with a book icon. Without this file, the platform would not know to provision the Wiki agent or where to find the homepage skill that makes the app usable.

#### Function details

##### `manifest`  (lines 50–56)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension’s manifest, which is the object the platform reads to install the Wiki app. It packages together the app name, version, private Wiki agent, and the homepage skill path.

**Data flow**: It reads the constants defined earlier in the file, such as the app name, version, agent definition, and skill folder path. It creates a skill specification pointing to the Wiki homepage skill, then creates a manifest containing that skill and the Wiki agent. The result is a complete manifest object returned to the platform; it does not change files or external state itself.

**Call relations**: During extension loading, the platform calls this function to ask, “What does this extension provide?” The function hands off to `SkillSpec.__init__` to describe the homepage skill and to `Manifest.__init__` to bundle the whole extension description into the standard form the platform expects.

*Call graph*: 2 external calls (__init__, __init__).


### Workflow extension bundles
These extension manifests add reusable multi-step brief and document workflows plus their supporting agents and skills.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s front desk sign. When the larger system discovers the brief-pipeline extension, it needs a simple answer to: “What is this extension called, what version is it, and what does it add?” This file provides that answer.

The extension adds a small writing pipeline. One subagent creates an outline, a second turns that outline into a draft, and a third critiques the draft. The parent agent stays in charge and uses the critique itself, rather than handing final control to the critic. Think of it like a writing workshop: one person plans, one writes, one reviews, and the main author decides what to do next.

The file imports three predefined subagent profiles from the pipeline module. A profile is a description of how a helper agent should behave. It also points to a skills directory on disk, where the instructions for the parent agent live. The `manifest` function packages all of this into a `Manifest` object, which is the standard “extension declaration” shape understood by UFO. Without this file, the system would not know that this extension exists, which subagents it provides, or where to find the skill instructions.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official declaration for the brief-pipeline extension. The system uses it to learn the extension’s name, version, subagents, and skill files.

**Data flow**: It starts with constants in this file, such as the extension name, version, and path to the skill directory, plus the three imported subagent profiles. It wraps the skill directory in a `SkillSpec`, then puts the name, version, subagents, and skill specification into a `Manifest`. The result is a complete description of what this extension contributes; it does not change anything else by itself.

**Call relations**: This function is called when UFO is loading or inspecting extensions. During that moment, it creates a `SkillSpec` for the skill folder and a `Manifest` for the whole extension, handing that finished declaration back to the extension system so the rest of UFO can wire in the outline, draft, and critic stages.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup / extension discovery`

This file is the documents pack’s signpost. It does not create documents itself. Instead, it describes the tools this extension makes available to the larger UFO system.

The pack includes several “skills,” which are folders containing instructions, scripts, and assets for a specific kind of work. For example, there are skills for Word documents, PowerPoint files, spreadsheets, PDFs, visual themes, document review, and drafting prose. Think of this file like the contents label on a toolbox: it names every tool inside and points to where each one lives.

It also registers a specialized child worker called the `writing` subagent. A subagent is a focused helper that can be given a smaller task, such as drafting or editing text, while already having the right writing workflow loaded.

The key job here is to build and return a `Manifest`, which is the formal description UFO’s extension loader understands. The manifest includes the extension name, version, available subagents, and a list of `SkillSpec` entries. Each `SkillSpec` points to one skill folder under this extension’s `skills/` directory. This lets the main system load these capabilities on demand instead of hard-coding them elsewhere.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds the formal manifest for the documents extension so the UFO system can discover its skills and writing subagent. Someone would use this when loading extensions and asking, “What does this package add?”

**Data flow**: It starts with fixed information in this file: the extension name, version, skills folder, skill names, and writing subagent profile. It turns each skill name into a `SkillSpec`, which points at that skill’s folder on disk. It then packages the name, version, subagent, and skill list into a `Manifest` object and returns it to the caller.

**Call relations**: During extension loading, the larger system calls `manifest` to learn what this documents pack provides. Inside, it creates several `SkillSpec` objects so each skill can be found later, then creates a `Manifest` object that hands the full extension description back to the loader.

*Call graph*: 2 external calls (__init__, __init__).


### Sample skill probe
The sample probe provides a minimal executable health check for bundled skill discovery.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or health check`

This file is deliberately minimal. When Python runs it, it immediately prints the text `sample-skill-probe-ok` to standard output, which usually means the terminal, log, or calling process that launched it. The point is not to perform real skill work, but to provide a clear signal: the file was found, Python could execute it, and the surrounding extension or skill wiring is basically working. You can think of it like pressing a doorbell to confirm the wiring is connected. If the expected message appears, the probe path is alive. If it does not appear, something outside this file may be wrong, such as the skill not being installed, the path being incorrect, or the execution environment failing before the skill code can run. Because it has no functions or configuration, there is no hidden behavior: running the file produces exactly one printed line.

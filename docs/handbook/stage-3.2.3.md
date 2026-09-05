# Agent, skill, and productivity extension manifests  `stage-3.2.3`

This stage is shared behind-the-scenes support for loading extra abilities into the assistant. Each manifest is like a registration card: it tells the host system what an extension offers, what tools it needs, and what smaller specialist agents or skill folders should be made available.

The brief-pipeline manifest adds three helper-agent stages and one skill package for turning a request into a structured brief. The browser manifest registers browser tools, a browser-focused subagent, its prompt text, and outside dependencies. The coding manifest introduces coding workers, the tools they may use, their prompts and models, and their skill folder. The documents manifest makes writing, review, styling, and drafting skills discoverable, along with a document-writing subagent. The objectives manifest adds tools and reminders that keep long-running goals active across conversation turns. The research manifest lists web research tools, research subagents, prompts, saved skills, and shared conversation data. The sites manifest registers website-building tools, prompts, skills, background jobs, hooks, user-facing surfaces, and site-related object types. Together, these files let the runtime assemble the right toolbox before work begins.

## Files in this stage

### Pipeline and browsing manifests
Registers extensions that provide staged brief generation and browser-enabled agent capabilities.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is like the label on a boxed add-on: it names the extension, gives its version, and lists what comes inside. The extension is built around a simple writing pipeline. One subagent makes an outline, a second turns that outline into a draft, and a third critiques the draft. The parent agent then uses that critique to improve the result.

The file does not run the pipeline itself. Instead, it provides a manifest, meaning a small structured description that the larger UFO system can read during extension loading. Without this file, the host would not know that the brief-pipeline extension exists, where its skill instructions live, or which subagent profiles should be made available.

The important pieces are the extension name and version, the path to the skill folder, and the three imported subagent profiles: outline, draft, and critic. The single function, `manifest`, packages those pieces into a `Manifest` object. It also wraps the skill folder path in a `SkillSpec`, which tells the host, “load the skill instructions from here.”

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the host system’s readable description of what this extension provides. Someone uses it when the application is discovering extensions and needs to know this extension’s name, version, subagents, and skill files.

**Data flow**: It starts with constants from this file, such as the extension name, version, and skill folder path, plus the three imported subagent profiles. It wraps the skill folder path in a `SkillSpec`, then places the name, version, subagents, and skill specification into a `Manifest`. The result is a complete manifest object; it does not change any outside state.

**Call relations**: During extension loading, the host calls `manifest` to ask this package what it contributes. `manifest` creates a `SkillSpec` for the skill directory and then creates a `Manifest` that includes that skill plus the outline, draft, and critic subagents, handing the completed description back to the host.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup or extension loading`

This file is like the label and instruction card for the browser extension. Without it, the rest of the system would not know that a browser-capable helper exists, what tools should be made available, or what guidance should be added to the main agent’s prompt.

The file names the extension as "browser" and gives it a version. It also reads a Markdown prompt section from disk. That prompt text is meant to teach the main agent when and how to delegate web-browsing work instead of trying to do it directly.

The important idea is separation of duties. The main agent does not directly hold every low-level browser or computer-use tool. Instead, this manifest exposes delegation tools such as browser tasks, and it registers a dedicated browser subagent profile. When the main agent needs web automation, it can ask the browser subagent to work in a more specialized environment with the full browser tool surface.

The manifest also says this extension requires "cdp_providers". CDP means Chrome DevTools Protocol, a way for software to control and inspect a browser. In plain terms, this file tells the system: “I am the browser pack; here are my tools, my helper agent, my instructions, and the browser-control support I depend on.”

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension’s declaration object. The system uses this declaration to learn what the extension offers and what it needs before browser delegation can work.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the browser tools, the delegation tools, the browser subagent profile, and the prompt text read from the Markdown file. It wraps the prompt text in a PromptSection, then packages everything into a Manifest. The result is a single object describing the complete browser extension.

**Call relations**: When the extension system asks this file what it provides, this function creates the answer. It calls PromptSection.__init__ to turn the browser prompt text into a named prompt section, then calls Manifest.__init__ to bundle that prompt section together with tools, the browser subagent profile, and the required CDP provider support.

*Call graph*: 2 external calls (__init__, __init__).


### Coding and document manifests
Declares capability packs for code-focused workers and document creation, review, styling, and drafting support.

### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension discovery`

This file is mostly a manifest, meaning a structured description of what an extension offers. In plain terms, it tells the host system: “I provide a coding helper child agent, an escalation helper for harder coding blockers, and a coding skill.” Without this file, the system would not know that these workers exist, what they are allowed to do, or how to start them.

The main coding child is designed to work inside a shared workspace. It can read and edit files, run shell commands, search the repo, load coding workflows, run JavaScript snippets, and look up outside information when needed. It receives a freeform objective and returns a freeform result, both wrapped in small Pydantic models. Pydantic is a library that defines and checks the shape of data, like a form that says which fields are allowed.

The file also defines a stronger “fable_escalation” profile. This is like calling a senior specialist after ordinary coding workers have already failed on a pull request blocker. It uses the same input and output shape, and the same tool set, but a different prompt and model.

Finally, the manifest says this extension needs internet access in its sandbox, because builds often download packages or release assets.

#### Function details

##### `manifest`  (lines 105–112)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension’s official Manifest object. The host system calls it to learn the extension name, version, internet needs, available child-agent profiles, and skill locations.

**Data flow**: It starts with constants and prebuilt profile objects defined earlier in the file: the extension name and version, the coding and escalation subagent profiles, and the folder containing skills. It wraps each skill name in a SkillSpec, then places the profiles and skill specs into a Manifest. The output is a complete registration object that the larger system can load.

**Call relations**: During extension loading, the system asks this function for the extension’s registration details. Inside the function, it creates SkillSpec entries for the skill folders and then creates the Manifest that gathers everything together, so the rest of the system can later spawn the named child agents and expose the coding skill.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup or extension discovery`

This file is the documents pack’s “contents label.” It does not create documents itself. Instead, it declares what tools this extension makes available when UFO starts or loads extensions.

The pack offers several skills, each stored as a folder under the local skills directory. These skills cover things like Word documents, PowerPoint decks, spreadsheets, PDFs, document review, visual themes, and writing drafts. Some skills build on others. For example, office and PDF skills rely on shared design foundations, so a document can still look consistent even when the user gives little style direction.

The file also registers a `writing` subagent profile. A subagent is a focused helper agent: in this case, one meant for drafting and editing prose. That lets the larger system hand off writing work to a child agent that already has the right writing workflow loaded.

The central job is done by `manifest()`, which returns a `Manifest` object. Think of it like a menu handed to the extension loader: it lists the extension name, version, subagents, and the skill folders that should be made available.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds the documents extension manifest, which is the formal record of what this extension provides. Someone would use it when the system needs to discover and load the extension’s skills and writing subagent.

**Data flow**: It starts with constants in this file: the extension name, version, skills directory, skill names, and the imported writing profile. It turns each skill name into a full folder path and wraps that path in a `SkillSpec`, which is a small description of a loadable skill. It then packages all of that into a `Manifest` and returns it, without changing files or global state.

**Call relations**: When the extension system asks this file what it contributes, `manifest` creates the answer. It calls `SkillSpec.__init__` once for each listed skill so the loader knows where each skill lives, then calls `Manifest.__init__` to bundle those skill specifications together with the extension metadata and writing subagent profile.

*Call graph*: 2 external calls (__init__, __init__).


### Objective and research manifests
Adds long-running objective continuity and web research tools, agents, prompts, skills, and shared context.

### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `request handling`

This file is the front door for the objectives extension. An objective is durable work that may last beyond one chat turn, such as a multi-step rollout or a task handed to another worker. Without this file, the system could still store objective data elsewhere, but the agent would not automatically be reminded that the objective exists when a new turn begins. That would make long-running work easy to drop.

The file does two things. First, it defines a prompt section: plain instructions telling the agent when to create an objective, what counts as a meaningful step, and why each step needs real acceptance conditions. These conditions are evidence from the outside world, not made-up markers.

Second, it registers a hook, which is code that runs at a specific moment in the system. Here, the hook runs when a user prompt is submitted. If the current conversation has an objective, it builds a compact “frontier” summary and injects it into the agent’s context. The frontier is like a clipboard handed to someone starting their shift: it says what the goal is, which steps are still open, which conditions are unmet, and whether the user has already been asked about a blocker. This keeps the agent oriented across wake-ups, delegated work, and scheduled follow-ups.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook prepares a reminder about the current objective at the start of a turn. It gives the agent the objective’s directive, progress, open steps, unmet conditions, and any already-raised blockers so the agent does not restart blindly or ask the same question twice.

**Data flow**: It receives a hook context from the runtime. If there is no active turn, it returns nothing. If there is a turn, it opens an extension database transaction, looks up the objective for the current conversation and workspace, and stops if none exists. When it finds one, it records a metric saying a frontier was injected and whether any step is blocked. It then turns the objective view into plain text: the objective name, directive, attempt and closure counts, up to twelve frontier steps, each step’s acceptance conditions, blocker notes, and warnings for attempted-but-unchecked steps. The output is an InjectContext containing that text, which means the text will be added to the agent’s working context.

**Call relations**: The system calls this function through the hook registered by manifest when a user prompt is submitted. Inside, it asks agent_current for the current workspace, uses Objectives to read the durable objective state, uses condition_summary to make each acceptance condition readable, and calls emit_metric so observability can count these injections. It hands the finished reminder back to the runtime as an InjectContext.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what the objectives extension contributes. It names the extension, exposes its tools, attaches its turn-start hook, and adds the instructional prompt text that teaches the agent how to use objectives properly.

**Data flow**: It takes no input. It gathers constants and imported tool definitions into a Manifest object: the extension name and version, four objective-related tools, one hook specification for user prompt submission, and one prompt section with the objectives guidance. The result is a complete manifest object that the host can load.

**Call relations**: The extension loader calls this function when it discovers or starts the extension. The function creates a HookSpec that points to _inject_frontier, creates a PromptSection from the long guidance text, and packages everything into a Manifest. After that, the host system can offer the objective tools and run the injection hook at the right time.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file does not perform web searches itself. Instead, it declares what the research pack contains so the larger application can plug those pieces in at startup. Think of it like the label on a toolbox: it names the tools inside, says which instruction cards belong with them, and warns that the toolbox only works if a search engine is available.

The file sets a package name and version, loads a web-related prompt section from a Markdown file, points to a folder of reusable research skills, and names three specific skills the agent can load when needed. It also imports the actual research tools, research subagent profiles, and a conversation slot used for keeping source information.

The central rule is that this extension requires `search_providers`. In plain terms, the research pack depends on some configured search backend. It does not own the credentials or setup for that backend. By declaring this requirement here, the system can fail early during boot if research is enabled without search configured, instead of surprising the user later when the first search is attempted.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s manifest, which is the object the host system reads to discover what this extension contributes. Someone uses it when enabling the research pack so the rest of the system can load its tools, subagents, prompt section, skills, and required search dependency.

**Data flow**: It starts with constants and imported pieces already defined in the file: the extension name and version, the list of research tools, the web prompt text read from disk, the skill folder paths, the source-tracking conversation slot, and the requirement for search providers. It wraps the web prompt text into a prompt section, turns each named skill folder into a skill specification, and places everything into one Manifest object. The output is that single Manifest, ready for the application to consume.

**Call relations**: When the extension system asks this pack what it provides, this function assembles the answer. As part of that assembly it creates the manifest itself, creates the web prompt section that will be added to the agent’s instructions, and creates skill specifications for the on-demand research skills. The resulting manifest is then used by the wider serving/setup flow to make these research features available during conversations.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Site-building manifest
Registers the website-building extension with its tools, prompts, skills, hooks, jobs, surfaces, and objects.

### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

This file does not build websites itself. Instead, it declares everything the rest of the system needs to know so the website features can be discovered and used. Think of it like a store directory: it lists what services are available, where they live, and which rules apply before customers use them.

At load time, it names the extension as `sites`, reads a prompt section from a Markdown file, points to the bundled `website-building` skill folder, and gathers pieces imported from the rest of the sites package. These pieces include tools for creating and serving hosted sites, delegation tools that let a main agent ask a website-building subagent for help, and an object type that lets chat recognize a hosted site as something users can interact with.

The manifest also registers guardrails called hooks. A hook is a function the platform runs at a specific moment, such as just before a tool is used. Here, hooks guide the application builder through the right route, enforce build phases, limit certain repair reads, and require quality checks before deployment.

Finally, it schedules a cleanup-style job related to releasing a main agent’s homepage once ownership changes. Without this manifest, the sites extension’s tools and rules would exist in code but would not be visible to the platform.

#### Function details

##### `manifest`  (lines 64–123)

```
def manifest() -> Manifest
```

**Purpose**: Creates and returns the extension manifest, which is the complete declaration of what the sites package contributes to the platform. The platform uses this to load website tools, prompts, subagents, skills, hooks, surfaces, conversation state, and scheduled jobs.

**Data flow**: It starts with constants and imported extension pieces: names, versions, tool definitions, prompt text, skill paths, hook handlers, and job settings. It packages those into small specification objects, such as prompt, skill, hook, and job descriptions. The result is one `Manifest` object that the host system can read to install and activate the sites extension.

**Call relations**: When the platform asks this extension what it provides, this function builds the answer. As part of that, it creates prompt, skill, hook, job, and manifest specification objects, and asks the main-homepage code which workspaces are candidates for the release job. It does not run the tools or build sites itself; it hands the platform a structured map so those pieces can be wired in at the right times.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, __init__, unreleased_main_homepage_workspaces).

# User-facing extension capability manifests  `stage-4.3`

This stage is part of the system’s startup and discovery work. Each manifest is like a labeled plug on a tool: it tells the UFO host what the extension is called, what it adds, and how the rest of the system may safely use it. The browser manifest registers a browser helper agent, delegation tools, and instructions for when browsing should be handed off. The coding manifest adds coding and review agents, a coding skill, GitHub access details, and a setup tool. The debugger and UFO manifests are smaller cards that expose public web or shell surfaces, meaning places the user interface can connect to. The documents manifest adds writing and document skills plus a specialist writing agent. The objectives manifest supports longer-running goals by storing the current objective and reminding the agent what remains unfinished. The sites manifest wires in website-building tools, prompts, skills, object types, screens, and a site-focused agent. The web manifest exposes portal routes, permissions, background jobs, and conversation storage. Together, these files make user-facing capabilities visible and loadable.

## Files in this stage

### Delegated task agents
Registers browser and coding capabilities that let the main agent hand specialized work to subagents, tools, skills, and prompts.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup`

This file is like the label and instruction card on a plug-in package. The browser extension contains tools for controlling a web browser, but the main agent is not meant to use those low-level browser controls directly. Instead, it gets delegation tools such as browser_task or wide_browse, which let it ask a separate browser subagent to do web work on its behalf.

The file names the extension, gives it a version, loads a prompt section from browser_section.md, and gathers together three important pieces: the browser tools, the delegation tools, and the browser subagent profile. The prompt section is important because it explains to the main agent how and when to delegate browser tasks, rather than trying to browse directly.

The manifest also says this extension requires cdp_providers. CDP means Chrome DevTools Protocol, a way for software to control and inspect a browser. Without that requirement being available, the browser automation pieces would not have the browser connection they need.

In short, this file does not perform browsing itself. It registers the browser capability with the host system so that, during a run, the right tools, prompt text, and subagent are available in the right places.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension's manifest, which is the system-readable description of what this extension provides. It is used so the host system can discover the browser tools, delegation tools, browser subagent, prompt text, and required browser-control support.

**Data flow**: It starts with constants and imported pieces: the extension name and version, browser tool lists, delegation tool lists, the browser subagent profile, and prompt text read from a Markdown file. It packages these into a Manifest object, including a PromptSection object for the prompt text. The output is that completed Manifest, ready for the larger system to load.

**Call relations**: When the extension is being registered, the system calls manifest to ask, 'What do you provide?' Inside, it creates a PromptSection for the browser guidance and a Manifest for the full extension declaration. That returned manifest is then used by the host system to add tools to the turn, register the browser subagent, and check that the needed CDP browser providers exist.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `extension load and startup`

This file is the extension’s packing list and setup sheet. It does not do coding work itself. Instead, it declares the pieces that make repository work possible inside UFO.

First, it defines a “coding” child agent profile. That child can be given an objective, use code-editing tools, run commands, search or fetch web pages, and return a plain result. Think of it as a specialist worker the main agent can call when a task needs hands-on repository work.

Second, it defines a durable “code-review” agent. This agent is meant to watch one pull request conversation, ask coding children to inspect the code from different angles, combine their findings, and publish a GitHub review status.

Third, it describes GitHub access. It declares credential slots, which are named places where tokens or installation records can be stored and safely injected into Git or GitHub API requests. It supports both the preferred GitHub App flow and a fallback personal access token path.

Finally, the manifest function returns one complete Manifest object. That object is what the host system reads to discover this extension’s agents, skills, tools, routes, and credentials.

#### Function details

##### `github_app_id`  (lines 114–127)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has a complete GitHub App registration in environment variables. It returns the App ID when everything needed is present, returns nothing when no App setup exists, and fails loudly if only part of the setup is present.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If all are empty, it treats GitHub App support as not configured and returns null. If some are present but others are missing, it raises an error so the system does not silently run in a half-configured state. If all are present, it returns the App ID.

**Call relations**: This check happens as the module is loaded, before the manifest is built, so the file can decide whether GitHub App token generation should be available. Its result affects the credential sources later declared in the file: a complete registration enables App-based tokens, while no registration leaves that path disabled.


##### `manifest`  (lines 196–221)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension manifest: the single object the UFO host uses to learn what this coding extension offers. Someone would use it when loading the extension so the system can register the coding subagent, code-review agent, skill files, GitHub credentials, tool, and web route.

**Data flow**: It starts from the constants and objects defined earlier in the file, such as the coding profile, review agent, skill path, credential slots, GitHub connection tool, and install callback route. It packages them into a Manifest object. The output is a structured description of the whole extension, with no repository work performed at that moment.

**Call relations**: When the host asks this extension what it contains, this function assembles the answer. Inside that assembly it creates SkillSpec entries for skills, a ToolDef for the connect_github tool, a RouteSpec for the GitHub installation callback, and finally the Manifest that holds them all together.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Debugger surface
Declares the debugger extension and its web surface so the host can discover and mount it.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup`

This file is the debugger extension’s manifest, which means it describes the extension to the larger UFO platform. A manifest is like a label on a plug-in: it says “my name is debugger,” “this is my version,” and “these are the routes I want to expose.” Without this file, the core system would not know how to attach the debugger extension or which requests should be sent to it.

The important piece is the surface definition. A surface is a named area of the system that can receive routes, similar to a doorway into the extension. Here, the debugger registers one surface, using route definitions imported from the debugger’s surface module. It also supplies an identity resolver, `resolve_operator_workspace`, which is used to decide whether an incoming caller belongs to the operator workspace. In plain terms, the debugger is not just opened to anyone; the manifest says how the platform should confirm that the requester is allowed to reach it.

So this file does not implement the debugger itself. Instead, it connects the debugger to the host application in a controlled way: name, version, exposed routes, and the rule used to identify the allowed workspace.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the object the host system reads to discover this extension. Someone uses it when the platform is loading extensions and needs to know what this debugger adds.

**Data flow**: It starts with the file’s constants for the extension name and version, plus imported route and surface information. It wraps the debugger routes into a `SurfaceSpec`, including the workspace identity check, then places that surface inside a `Manifest`. The result is a complete manifest object returned to the caller; it does not change any outside state.

**Call relations**: During extension loading, the platform calls `manifest` to ask this package what it provides. The function creates a surface specification for the debugger routes and then hands that specification into the manifest object, so the wider system can mount the debugger in the right place with the right access check.

*Call graph*: 2 external calls (__init__, __init__).


### Content and continuity capabilities
Adds document work, persistent objectives, and site-building capabilities through skills, prompts, object types, surfaces, and subagent profiles.

### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup or extension discovery`

This file exists so the documents pack can introduce itself to the UFO system in one clear place. Without it, the system would not know that this extension provides skills for working with Word documents, PowerPoint files, spreadsheets, PDFs, visual themes, document review, and writing drafts.

Think of it like a contents label on a toolbox. The toolbox may contain many tools, but the label tells the workshop what is inside and where to find it. Here, the file names the extension, gives it a version, points to the folder where its skills live, and lists the skill folders that should be registered.

It also registers a “writing” subagent profile. A subagent is a smaller helper agent that can be started for a focused job. In this case, the writing helper already knows the writing-drafts skill, so the main agent can hand off drafting or editing work without rebuilding that setup each time.

The main function, manifest, packages all of this into a Manifest object. The rest of the system can then read that object during extension loading and make these skills available on demand.

#### Function details

##### `manifest`  (lines 35–41)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the documents extension’s manifest, which is the structured description of what this extension contributes. Someone uses this when the system is loading extensions and needs to discover the extension’s name, version, subagents, and skill folders.

**Data flow**: It starts with the file’s constants: the extension name, version, skills folder path, list of skill names, and writing subagent profile. It turns each skill name into a SkillSpec pointing at that skill’s folder, then places those skill descriptions and the writing profile into a Manifest. The result is a complete registration object that the wider system can consume.

**Call relations**: During extension discovery, the system calls this function to ask, “What do you provide?” The function creates SkillSpec objects for each listed skill folder, then passes them into Manifest.__init__ along with the extension metadata and writing subagent profile. It hands the finished Manifest back to the loader so those skills and the subagent can be registered.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup and user prompt handling`

This extension solves a memory problem. Some work cannot finish in one reply: it may be delegated, paused, checked later, or resumed after a heartbeat or another worker hands control back. In those moments, the agent may start a fresh turn without its previous short-term context. This file makes sure the durable objective record is brought back into view.

It does two main things. First, it defines the extension manifest: its name, version, tools, hook, and prompt instructions. The tools let the agent plan an objective, run independent steps, record progress on a step, and read the objective state. The prompt section explains when to create objective steps and how to write useful acceptance conditions, meaning concrete checks that prove a step is really done.

Second, it defines a hook that runs when a user prompt is submitted. If the current conversation has an objective, the hook reads that objective from storage and injects a compact “frontier” into the agent’s context. The frontier is like a project dashboard: objective name, directive, attempts, closed step count, open steps, needed conditions, and any already-raised blocker. This prevents the agent from forgetting what is still unresolved or asking the user the same blocked question again.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function adds the current objective’s unfinished work into the agent’s context at the start of a turn. It exists so a resumed conversation does not depend on the agent remembering earlier temporary thoughts.

**Data flow**: It receives a hook context, which includes the current turn and access to the extension’s storage. If there is no turn, or if the conversation has no objective, it returns nothing. Otherwise it opens a storage transaction, looks up the objective for the current conversation and workspace, builds a readable text block showing the objective directive, progress, open steps, needed conditions, blockers, and attempted-but-unchecked steps, records a metric about the injection, and returns that text as an InjectContext to be placed into the agent’s prompt.

**Call relations**: The extension system calls this function when the user_prompt_submit hook fires. Inside that moment, it asks agent_current for the workspace, uses Objectives to read the durable objective record, uses condition_summary to turn acceptance checks into plain text, emits a metric so operators can see that frontier injection happened, and finally hands the assembled reminder back through InjectContext.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the objectives extension to the host system. It tells the system which tools, prompt guidance, and hook belong to this extension.

**Data flow**: It takes no input. It packages fixed extension information: the name and version, four objective-related tools, one hook that runs _inject_frontier when a user prompt is submitted, and one prompt section that teaches the agent how to use objectives. It returns a Manifest object that the host can load.

**Call relations**: The host calls this function when loading the extension. The returned Manifest wires together the objective tools, the prompt instructions, and the HookSpec that points future user prompt submissions to _inject_frontier.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup`

This file does not build websites itself. Instead, it packages up everything the rest of the system needs in order to let an agent create and serve hosted sites. You can think of it like a menu handed to the host application: “Here are the tools I provide, here is the special website-building helper agent, here is the prompt text the main agent should read, and here is how hosted sites should appear to users.”

At import time, the file gives the extension a name and version, reads a Markdown prompt section from disk, and points to the bundled website-building skill folder. The prompt section teaches the agent how to use the site features. The skill folder contains reusable instructions and assets for building websites.

The main function, `manifest`, combines several pieces from nearby modules: site tools, delegation tools, a site object kind, a website-building subagent profile, a user-facing site surface, and a conversation slot. It returns one `Manifest` object, which is the standard format the host system understands. Without this file, the extension’s parts might exist in the codebase, but the main application would not know to load them or make them available to agents.

#### Function details

##### `manifest`  (lines 36–47)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension’s manifest, which is the formal list of features the “sites” package contributes to the host system. It is used when the extension is loaded so the system can register the website tools, prompt text, subagent, surface, object kind, skill, and conversation slot together.

**Data flow**: It reads the module-level constants and imported extension pieces that were prepared earlier, such as the extension name, version, prompt body, tool lists, and skill path. It wraps the prompt text in a `PromptSection`, wraps the skill folder path in a `SkillSpec`, and places all of the extension parts into a `Manifest`. The result is a single manifest object that the rest of the system can consume to make the sites feature available.

**Call relations**: When the host system asks this extension what it provides, `manifest` is the function that answers. Inside that answer, it creates the small wrapper objects for the prompt section and skill specification, then hands all pieces to the `Manifest` constructor so they can be registered as one coherent extension.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Host and web portals
Registers the core UFO shell surface and the broader web portal routes, permissions, jobs, and storage slots.

### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup`

This manifest exists so the core UFO system can discover and mount the `ufo` extension without guessing how it works. When an extension is installed, the host needs to know its name, its version, and what “surface” it exposes. A surface is a visible contact point, like a service window, where clients can connect.

Here, the extension declares one surface named by `SURFACE_UFO`. That surface uses `ROUTES`, which are the web or streaming paths the client can call, and `resolve_workspace`, which is the function used to identify which workspace a connecting user belongs to. The file does not define credentials or user-facing settings. Its own comment explains that authentication is based on a bearer token checked against the `UFO_TOKEN_SECRET` environment variable, rather than a saved workspace credential slot.

Without this file, the extension might exist on disk but the host would not know how to mount it. The shell client would have no declared route to reach, much like having a phone plugged in but no number listed in the directory.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the host uses to register the `ufo` extension. Someone would use this when the system is loading extensions and needs to know what this one exposes.

**Data flow**: It starts with the file’s constants, `NAME` and `VERSION`, and the imported surface details: the surface name, its routes, and the workspace-identification function. It wraps those into a `SurfaceSpec`, then wraps that into a `Manifest`. The result is a structured description of the extension that the host can read and mount.

**Call relations**: During extension loading, the host calls `manifest` to ask this file, “What do you provide?” The function creates a `SurfaceSpec` to describe the live client-facing surface, then passes that into `Manifest.__init__` so the finished manifest can be handed back to the core system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup`

A manifest is like a sign-up sheet for an extension. It tells the core application: “Here is my name, here is what I can do, here is how people reach me, and here are the background chores I need run.” Without this file, the web portal would not be mounted as a browser surface, its admin-only tools would not be registered, and its scheduled upkeep jobs would not be known to the system.

This manifest defines the web extension’s name and version, then builds a `Manifest` object. It registers the web access tools, which are the actions the web surface is allowed to use. It declares two conversation slots, one for changes and one for artifacts, so web conversations have known places to store those kinds of information.

It also declares a web surface: the actual browser-facing part of the extension. The surface includes its route table, a function for identifying which workspace a request belongs to, and a flag saying this surface should act as the home page for the deployment.

Finally, it defines two scheduled jobs. One job summarizes untitled conversations so chat rails can show useful names. The other seeds missing home pages for agent workspaces. The file does not load secrets or configuration knobs; installing the extension is enough to make it available.

#### Function details

##### `manifest`  (lines 34–58)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the core system’s complete description of what this extension contributes. The system uses it to register the web portal, its tools, its storage slots, and its scheduled background jobs.

**Data flow**: It starts with constants and imported functions from the web extension and the UFO SDK. It packages those pieces into surface and job specifications, including candidate finders for conversations that need titles and workspaces that need seeded home pages. It returns one `Manifest` object that the core system can read during extension installation or startup.

**Call relations**: When the extension is discovered, the core calls `manifest` to ask what this extension provides. Inside, it creates a `SurfaceSpec` for the browser portal, creates two `JobSpec` entries for recurring work, and asks the SDK for candidate selectors that find the right conversations or workspaces for those jobs. The finished manifest is then handed back to the core so the web extension can be mounted and scheduled.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).

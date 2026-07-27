# Agent, skill, and workflow extension manifests  `stage-18.2`

This stage is behind-the-scenes setup support. It does not do the agent’s work directly. Instead, each manifest acts like a label on a toolbox, telling the larger system what tools, helper agents, prompts, skills, and outside services are available when an extension is turned on. A subagent is a specialized helper agent for one kind of job.

The brief-pipeline manifest sets up a writing assembly line, with helpers for outlining, drafting, and critiquing, plus a skill package for making briefs. The browser manifest advertises browser tools, a browser-focused subagent, prompt text, and needed external services. The coding manifest registers a coding subagent, its skills and tools, and the GitHub credentials it may safely use. The documents manifest lists skills for creating, editing, reviewing, and styling files such as documents, slides, spreadsheets, PDFs, and themes. The research manifest adds research tools, research subagents, prompts, and optional skills. The sites manifest registers website-building tools, a site-building subagent, prompts, and related skills.

## Files in this stage

### Brief workflow orchestration
Declares the staged brief-building workflow, from outline through drafting to critique, plus its supporting skill package.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “label on the box.” When the host system discovers the brief-pipeline extension, it needs a clear answer to: what is this extension called, what version is it, and what tools or agent roles does it add? Without this file, the system would not know that the extension exists or how to load its parts.

The extension declares three typed subagent profiles: one for making an outline, one for writing a draft, and one for critiquing the result. Think of them like three specialists in a small writing team. The outline specialist plans the structure, the draft specialist turns that plan into prose, and the critic specialist reviews the draft. The parent agent then uses the critique itself rather than handing that final fix-up to another stage.

The file also points to a skill folder on disk. A skill is a packaged instruction or capability that teaches the parent agent how to chain those stages together. The `manifest()` function gathers the name, version, subagent profiles, and skill path into one `Manifest` object, which is the standard format the host system expects during extension loading.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds the official description of the brief-pipeline extension. The host system uses this description to learn the extension’s name, version, available subagents, and skill files.

**Data flow**: It starts with constants in this file, such as the extension name, version, and skill directory, plus the imported outline, draft, and critic profiles. It wraps the skill directory in a `SkillSpec`, then puts the name, version, three subagents, and skill specification into a `Manifest`. The result is a complete manifest object returned to whoever is loading the extension.

**Call relations**: During extension loading, the larger system calls `manifest` to ask what this extension provides. Inside that moment, `manifest` creates a `SkillSpec` for the skill folder and a `Manifest` that packages everything together in the format the host expects.

*Call graph*: 2 external calls (__init__, __init__).


### Interactive tool subagents
Advertises browser and coding assistants, their prompts, tools, skills, and required external credentials or services.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup / extension load`

This file exists so the main UFO system can discover and plug in the browser extension in a predictable way. Think of it like the label on a toolbox: it says the toolbox is named “browser,” gives its version, lists what tools are inside, and explains what other equipment must already be available.

The file brings together three main pieces. First, it includes browser-use tools, which are the low-level abilities for working with a browser. Second, it includes delegation tools, such as tools that let a main agent ask a separate browser-focused helper to do web work. Third, it registers a browser subagent profile, which is the setup for that helper agent.

It also reads a Markdown prompt section from disk. That prompt text is added to the main agent’s instructions so the main agent knows when and how to delegate browser tasks instead of trying to use browser controls directly.

The important design choice is separation: the main agent gets delegation tools, while the browser subagent gets the full browser surface. Without this manifest, the extension might exist on disk, but the system would not know how to load its tools, prompt guidance, subagent profile, or required browser connection support.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension description that the host system uses to load the browser extension. Someone would use this when the system is discovering extensions and needs to know what this one contributes.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the browser tools, delegation tools, browser subagent profile, prompt text read from the Markdown file, and a required dependency named "cdp_providers". It packages those into a PromptSection and then into a Manifest. The result is a single Manifest object that tells the system how to install this extension into an agent run.

**Call relations**: During extension loading, the host can call this function to ask, “What do you provide?” The function creates a prompt section for the browser instructions, then creates the full manifest that includes tools, subagents, prompt text, and requirements. That manifest is then handed back to the host so it can add those pieces to the running system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension load`

This file is the packing list for the coding extension. It tells the main UFO system: “Here is a child agent that can work on code, here are the skills it can learn, here are the tools it may use, and here is how GitHub access should be set up.”

The main feature is a coding subagent. A subagent is a separate worker agent given a focused job, such as exploring a repository, editing files, running tests, and reporting back. The file defines the shape of the request sent to that worker, through `CodingInput`, and the shape of the answer it returns, through `CodingOutput`.

It also declares two GitHub-related credential slots. A credential slot is like a labeled keyhole: the system knows a secret may be needed there, but the secret itself is handled outside the sandbox. One slot records which GitHub App installation belongs to the workspace. The other supplies a GitHub token for Git operations such as clone and push. If this deployment has a GitHub App configured, the token can be minted from that app; otherwise users may need a personal access token.

Finally, `manifest()` bundles all of this into one `Manifest`, including the coding profile, skill folders, the `connect_github` tool, and a web route used when GitHub reports that installation is complete.

#### Function details

##### `github_app_id`  (lines 91–105)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has a complete GitHub App configuration. It prevents a half-configured setup, where the system might appear to support GitHub App authentication but silently fall back or fail later.

**Data flow**: It reads environment variables from the operating system. If the main GitHub App ID is absent, it returns `None`, meaning no app-based GitHub setup is available. If the app ID is present, it checks that the client ID, client secret, and private key are also present; if any are missing, it raises an error. If everything is present, it returns the app ID.

**Call relations**: This function is used while the module is being loaded to decide how the GitHub credential slot should get its token. If it returns an app ID, the credential can come from the GitHub App token source; if it returns `None`, that app-based source is not attached.


##### `manifest`  (lines 134–158)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension manifest, which is the system-readable description of everything this coding extension offers. The host system calls it when loading the extension so it can register the coding subagent, skills, credentials, tool, and GitHub callback route.

**Data flow**: It starts from constants and objects already defined in the file: the extension name and version, the coding subagent profile, the skill directory names, the credential slots, the GitHub connection tool, and the route for GitHub installation callbacks. It wraps those pieces into `SkillSpec`, `ToolDef`, and `RouteSpec` objects, then returns one `Manifest` containing the full extension declaration. It does not perform the GitHub connection itself; it only tells the system what is available and where to send later requests.

**Call relations**: When the extension is loaded, this function is the handoff point to the core UFO system. It calls the manifest-related constructors to package the coding profile, skills, tool, and route. Later, the system can use that package to spawn the coding subagent, expose the `connect_github` tool, and route GitHub installation callbacks to the correct handlers.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Knowledge and document workspaces
Registers document-production skills and research-oriented tools, subagents, prompts, and optional skill folders.

### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup or extension discovery`

This file is like the table of contents for the documents skill pack. The wider system needs a simple way to discover which skills an extension offers and where their files live. Without this manifest, the document skills might exist on disk, but the agent would not know how to find or load them.

The file defines a package name, a version, the folder where the skills are stored, and the exact skill folder names included in the pack. Each skill is expected to live under the local `skills/` directory. Some of those skills build on shared visual guidance, such as `design-foundations`, so loading one skill can also pull in the foundation it depends on.

The main job happens in `manifest()`. It creates a `Manifest`, which is a structured description of this extension, and fills it with one `SkillSpec` per listed skill. A `SkillSpec` is a small record pointing to a skill’s folder. Later, the skill-loading system can read this manifest and make those skills available inside the agent’s working environment.

#### Function details

##### `manifest`  (lines 28–33)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the documents extension. The system uses this to learn the extension’s name, version, and which skill folders it can load.

**Data flow**: It starts with the constants in this file: the extension name, version, root skills folder, and list of skill names. For each skill name, it creates a path to that skill’s folder and wraps it in a `SkillSpec`, which is a small description of one loadable skill. It then packages all of those skill descriptions into a `Manifest` and returns it.

**Call relations**: When the extension system asks this package what it contributes, this function is the answer. It hands the system a `Manifest` built from several `SkillSpec` objects, so the later skill loader can find and load the document-production skills on demand.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file is the “packing list” for the research extension. The extension itself provides web research abilities, but the main system needs a clear way to discover those abilities at startup. Without this file, the system would not know which research tools to expose, which research subagent profiles to register, or which prompt instructions and skill folders belong to this extension.

At the top, the file gives the extension a name and version. It then reads a web-related prompt section from a Markdown file on disk. That prompt text is bundled into the extension so the agent can be taught how to think about web research during a run. It also points to a skills directory and names the research skills that can be loaded when needed.

The central function, `manifest`, builds a `Manifest`, which is like a label on a toolbox: it says what is inside and what the system must have before using it. The manifest includes normal research tools, a wider delegation tool, two research subagent profiles, the web prompt section, and two skill specifications. It also declares that this extension requires `search_providers`, meaning a search backend must be configured. That is important because it makes setup failures happen immediately at boot instead of later, when a user first tries to search the web.

#### Function details

##### `manifest`  (lines 27–36)

```
def manifest() -> Manifest
```

**Purpose**: This function creates the extension manifest: the object that tells the host system everything the research extension contributes. It is used when the system starts up or loads extensions, so the research features can be registered in one consistent bundle.

**Data flow**: It starts with constants and imported objects already defined in the file: the extension name and version, the research tools, the wide research tool, the research subagent profiles, the web prompt text read from disk, and the skill folder names. It wraps the prompt text in a `PromptSection`, turns each named skill folder into a `SkillSpec`, and places everything into a `Manifest`. The result is a single manifest object returned to the caller; it does not change outside state by itself.

**Call relations**: When the extension loader asks this file what it provides, `manifest` builds the answer. In doing so, it calls `PromptSection.__init__` to package the web prompt, `SkillSpec.__init__` to describe each skill folder, and `Manifest.__init__` to assemble the final declaration that the host system can read.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Site-building package
Declares the website-building extension with its tools, subagent, prompt text, and skill package.

### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `extension load / config load`

This file is like the label on a toolbox. It does not build websites itself. Instead, it tells the host system what is inside the sites extension and how to make those pieces available to an agent.

When the extension is loaded, the file provides a manifest, which is a structured declaration of the extension’s name, version, tools, subagents, prompt additions, and skills. The tools let an agent work with sites and delegate website-building work. The subagent profile describes a specialized child agent for website building. The prompt section is read from a Markdown file and added to the agent’s instructions, so the main agent knows important rules such as serving and validating a site before handing it back. The skill points to the bundled “website-building” skill directory, which contains the reusable knowledge and templates for different kinds of website projects.

Without this file, the code and prompt files might still exist on disk, but the host system would not know to load them. The website-building features would not be advertised as part of the extension, so agents could miss the tools, subagent, prompt guidance, or skill content they need.

#### Function details

##### `manifest`  (lines 29–37)

```
def manifest() -> Manifest
```

**Purpose**: Creates and returns the official manifest for the sites extension. The host system uses this to discover the extension’s tools, website-building subagent, prompt section, and skill package.

**Data flow**: It starts with constants defined in this file and objects imported from nearby modules: the extension name and version, the site tools, delegation tools, website-building subagent profile, prompt text loaded from a Markdown file, and the skill directory path. It wraps the prompt text in a PromptSection, wraps the skill path in a SkillSpec, and places everything into a Manifest object. The result is a complete description of what this extension contributes to the system.

**Call relations**: This function is called when the extension system wants to load the sites pack. During that moment, it builds a Manifest by calling the Manifest constructor, and it also creates the PromptSection and SkillSpec objects that are placed inside it. The returned manifest is then used by the host system to add the tools, register the subagent profile, attach the prompt guidance, and make the website-building skill available.

*Call graph*: 3 external calls (__init__, __init__, __init__).

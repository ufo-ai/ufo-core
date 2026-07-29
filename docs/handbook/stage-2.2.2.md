# Agent workflow extension declarations  `stage-2.2.2`

This stage is shared startup support. It does not do the browsing, coding, writing, or research itself. Instead, it tells the main UFO system what extra abilities are available, like labels and menu cards placed on toolboxes before work begins.

Each extension has a small __init__.py file that marks its folder as a Python package, meaning other Python code can import it. These files are mostly front doors: the brief pipeline and browser ones also give a human-readable label, while the coding and research ones simply make the folders importable.

The manifest.py files do the real declaring. The brief pipeline manifest offers a writing workflow made of three subagent stages, moving from outline to draft to critique, plus a skill folder. The browser manifest adds browser and computer-use tools, a browser-focused helper agent, and prompt instructions. The coding manifest declares repository-working tools, skills, a coding subagent, and GitHub credentials. The research manifest registers web research tools, helper agent profiles, prompt text, and optional skills. Together, these files let the host system discover and wire in each extension cleanly.

## Files in this stage

### Brief Pipeline Extension
Package entry and manifest declarations for the outline-to-draft-to-critique brief writing workflow.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import/package discovery`

This is a very small package marker file. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as an importable package. That matters because other parts of the system can then import code from `ufo_ext_brief_pipeline` using normal Python import paths.

Here, the file contains only a docstring: “Brief pipeline extension.” A docstring is a short piece of text attached to a module, like a label on a folder. It helps tools, documentation readers, and developers understand what this package is meant to contain.

Nothing is configured, started, or executed here. The actual brief pipeline behavior lives in other files inside this package. Without this file, depending on the Python version and packaging setup, imports or package discovery for this extension could become less clear or fail in some environments. Think of it like the title page of a binder: it does not contain the work itself, but it identifies the binder and makes it easier for the rest of the system to find what belongs inside.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s introduction card. When the host system discovers the brief-pipeline extension, it needs a clear answer to: “What is your name, what version are you, and what tools or helpers do you add?” Without this file, the extension would not advertise its pipeline to the rest of the system.

The pipeline is built from three typed subagents: one creates an outline, one turns that outline into a draft, and one critiques the draft. Think of it like a small writing assembly line: planner, writer, reviewer. The file imports those three subagent profiles from the pipeline module, then points to a skill directory that teaches the parent agent how to chain the stages together. A “skill” here is a packaged set of instructions or behavior that the agent can load.

The only active piece is the `manifest` function. It returns a `Manifest`, which is the structured description the UFO extension system expects. That manifest includes the extension name, version, subagents, and skill location.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension description that UFO reads when loading this extension. Someone uses it so the system can discover the brief-pipeline name, version, three subagents, and skill package.

**Data flow**: It reads fixed values from this file, such as the extension name, version, and skill folder path, plus the three imported subagent profiles. It wraps the skill folder in a `SkillSpec`, then puts the name, version, subagents, and skill into a `Manifest`. The result is a ready-to-load description of the extension; it does not modify anything else.

**Call relations**: During extension loading, the host calls `manifest` to ask what this extension provides. Inside, it creates a `SkillSpec` for the skill directory and then creates a `Manifest` that bundles that skill together with the outline, draft, and critic subagent profiles.

*Call graph*: 2 external calls (__init__, __init__).


### Browser Extension
Package entry and manifest declarations for browser tools, browser-focused subagents, and related prompt guidance.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `cross-cutting`

This is a small package marker file. In Python, an `__init__.py` file tells the system that the surrounding folder should be treated as an importable package, meaning other parts of the project can refer to it by name. Here, the file does not define any functions or classes. Its main job is to give the package a short human-readable identity: it is the browser tool pack.

The package described here contains tools for working with a sandbox browser, which means a controlled browser environment used by the system rather than a person directly. It also mentions “computer-use tools,” meaning tools that can interact with a browser or computer-like interface, and a “browser subagent profile,” meaning settings or instructions for an assistant component specialized for browser work.

Without this file, depending on the Python setup, importing this folder as a package could be less clear or could fail in some environments. Think of it like a label on a drawer: the drawer holds the real tools elsewhere, but this label tells both Python and human readers what kind of tools live inside.


### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup / extension loading`

This file tells the larger UFO system how to plug in browser automation. Without it, the system would not know that a browser subagent exists, which tools belong to it, or what instructions should be added to the agent’s prompt.

The file gathers three main ingredients. First, it imports the browser tools, which are the low-level abilities for working with a browser. Second, it imports delegation tools, which are the safer front-door commands the main agent uses when it wants browser work done. Third, it imports the browser subagent profile, which describes the child agent that actually gets the full browser surface.

A useful analogy is a theater production. The main agent is the director, but it should not personally run every light and prop. This manifest introduces a specialist crew member, the browser subagent, and gives the director a few clear ways to ask that specialist for help.

The file also reads a markdown prompt section from disk. That text is added to the main agent’s instructions so it learns when and how to delegate browser tasks. Finally, the manifest says this extension requires “cdp_providers”, meaning it depends on browser-control backends that speak Chrome DevTools Protocol, a standard way to control browsers programmatically.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal declaration for the browser extension. The system uses this declaration to learn the extension’s name, version, tools, subagent profile, prompt text, and required browser-control support.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the browser tools, the delegation tools, the browser subagent profile, and the prompt section text read from a markdown file. It wraps the prompt text in a PromptSection object, then places everything into a Manifest object. The result is a complete extension description that the rest of the system can register and use.

**Call relations**: When the extension system asks this file what it provides, this function assembles the answer. It creates a PromptSection for the browser instructions, then creates a Manifest that packages those instructions together with the browser tools, delegation tools, subagent profile, and required dependency.

*Call graph*: 2 external calls (__init__, __init__).


### Coding Extension
Package entry and manifest declarations for repository-oriented coding tools, skills, subagents, and credentials.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a directory with an `__init__.py` file is treated as a package, which means code elsewhere can import modules from it using names like `ufo_ext_coding.some_module`. Think of it like putting a label on a folder so the rest of the system knows it is meant to be opened as part of the program, not just treated as loose files. Because the file is empty, it does not run setup code, expose shortcuts, or change how imports behave. Its value is structural: without it, some Python tools or older import setups might not recognize this directory as an importable package.


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension load`

This file exists so the wider UFO system can load a self-contained coding helper. That helper can inspect a repository, edit files, run commands, review code, and report back. Without this manifest, the host would not know that the coding subagent exists, which tools it is allowed to use, which skills it can load, or how GitHub authentication should work.

The file first names the extension and gathers its building blocks: a prompt for the coding subagent, a list of allowed tools such as shell access and file editing, and two skill folders called “coding” and “code-review.” It defines simple input and output shapes: the caller gives the child agent an objective, and the child returns a text result.

A large part of the file is about GitHub access. It declares two credential slots: one for a GitHub App installation and one for a Git token used by git commands. A credential slot is like a labeled keyhole: the system knows what secret may be inserted there, but the sandbox only sees a safe placeholder, not the real secret. The egress layer swaps that placeholder for the real token when contacting GitHub.

Finally, the manifest exposes one admin tool for connecting GitHub and one web route that receives the GitHub installation callback.

#### Function details

##### `github_app_id`  (lines 92–106)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether this deployment is configured to use a GitHub App. It returns the app id only if the required companion settings are also present, so the system does not pretend GitHub App mode is ready when it is only half configured.

**Data flow**: It reads environment variables from the running process. If there is no GitHub App id, it returns nothing. If there is an app id, it checks that the client id, client secret, and private key are also present; if any are missing, it raises an error explaining what is incomplete. If everything needed is present, it returns the app id string.

**Call relations**: This function is used while the file is being loaded to decide how the GitHub credential slot should get its token. If GitHub App configuration is available, the credential can be sourced from app-generated tokens; otherwise the system falls back to member-provided token behavior.


##### `manifest`  (lines 135–159)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the object the host system reads to discover everything this coding pack offers. It gathers the subagent, skills, credentials, admin tool, and GitHub callback route into one package.

**Data flow**: It takes no input from the caller. It reads the constants and objects defined earlier in this file, then assembles them into a Manifest object. The result tells the host system the extension name and version, which subagent profile to register, which skill directories to load, which credentials may be used, what tool to expose for connecting GitHub, and what route to install for GitHub’s callback.

**Call relations**: When the extension system asks this file what it provides, this function is the answer. In building that answer, it creates SkillSpec entries for the skill folders, a ToolDef for the connect_github admin tool, a RouteSpec for the GitHub installation callback, and finally the Manifest that contains them all.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Research Extension
Package entry and manifest declarations for web research tools, helper agents, prompt text, and optional skills.

### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, other parts of the project might have trouble importing code from `extensions/research/ufo_ext_research` in the expected way. Because the file is empty, it does not set up configuration, expose shortcuts, or run any startup code when the package is imported. Its value is structural: it helps define the project’s module layout and keeps imports predictable.


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup / extension load`

This file is the research extension’s “packing list.” When the system starts and loads extensions, it needs a clear declaration of what each extension adds. This file provides that declaration for the research pack.

It names the extension, gives it a version, reads a web-research prompt section from a Markdown file, points to two research skill folders, and gathers the tools and subagent profiles that make research work. A subagent profile is a prepared role for a helper agent, such as a researcher that can investigate broadly or deeply. A skill is a loadable bundle of instructions or files the agent can use when needed.

The important safety detail is the dependency declaration: this pack requires `search_providers`. In plain terms, the research extension does not bring its own search engine or credentials. It expects the deployed system to choose and configure a search backend separately. By declaring that requirement here, the system can fail during startup if research is enabled without search support, instead of surprising the user later when the first web search fails.

Without this file, the research extension’s parts might exist in the codebase, but the main system would not know to install them into an active run.

#### Function details

##### `manifest`  (lines 27–36)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s manifest, which is the structured description the main system reads to know what this extension adds. Someone uses this function when loading the extension so the tools, prompts, subagents, and skills become available together.

**Data flow**: It starts with constants defined in this file and imports from nearby research modules: the extension name and version, the research tools, the wide-research tool, two subagent profiles, the web prompt text, and the skill folder paths. It wraps the prompt text in a `PromptSection`, wraps each skill folder in a `SkillSpec`, and puts everything into a `Manifest`. The result is one complete manifest object, with a note that a search provider must also be configured.

**Call relations**: During extension loading, the system calls `manifest` to ask, “What does this research pack contribute?” The function creates the smaller declaration objects first, such as the prompt section and skill specs, then hands them to `Manifest.__init__` so the main system receives one package describing everything to register.

*Call graph*: 3 external calls (__init__, __init__, __init__).

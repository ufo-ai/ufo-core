# Delegated Worker and Workflow Extension Packages  `stage-4.1.3`

This stage is shared behind-the-scenes support. It does not run the main app by itself. Instead, it packages optional “extensions,” which are add-on bundles that teach the system how to hand work to specialized helper agents. A helper agent is like a delegated worker: the main agent stays in charge, but can ask a browser, coding, research, or brief-writing worker to do a focused job.

The brief pipeline package has an __init__.py file that simply makes the folder importable and gives it a short description. Its manifest.py is the real setup card: it declares three brief-writing helpers and a skill folder that explains how to use them in sequence. The browser manifest declares a browser subagent, browser tools, and prompt instructions for when browsing should be delegated. The coding package also has a simple __init__.py marker, while its manifest lists coding agents, tools, skills, GitHub access settings, and a web route for repository work. The research manifest adds research tools, agents, prompts, saved skills, and shared conversation data. Together, these files let the host system discover and plug in extra workers.

## Files in this stage

### Brief Pipeline Package
Defines the brief-pipeline extension package and declares its staged helper agents and supporting skill folder.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import time`

This is a very small package-start file. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package, meaning other code can load it by name. Here, the file contains only a short documentation string: “Brief pipeline extension.” That string acts like a label on a folder, telling readers and tools what this package is meant to contain.

There is no executable logic here. It does not set up the pipeline, register commands, read files, or change any state. Its value is structural: without this file, some Python environments or packaging tools might not recognize `ufo_ext_brief_pipeline` as a normal package, which could make importing the brief pipeline extension less reliable. Think of it like a nameplate on an office door: it does not do the work inside the office, but it helps the building know the room exists and what it is for.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s front door. When the larger system looks for installed extensions, it needs a simple answer to questions like: “What is this extension called?”, “What version is it?”, and “What new abilities does it provide?” This file supplies that answer.

The extension is called `brief_pipeline`. Its main idea is a writing workflow with three stages: one helper agent makes an outline, another turns that outline into a draft, and a third critiques the draft. The parent agent then uses that critique itself, rather than handing off the final fix to another agent. You can think of it like an editor’s assembly line: planner, writer, reviewer, then the lead editor decides what to do next.

The file imports the three subagent profiles from the pipeline code and points to a local skills directory. A skill is a packaged instruction set that teaches the parent agent how to chain these stages together. The `manifest()` function bundles all of this into a `Manifest` object, which is the standard shape the host system expects when loading an extension.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration that the host system reads when loading this plugin. It names the extension, gives its version, lists the three subagents it provides, and points to the skill instructions on disk.

**Data flow**: It starts with constants in this file, such as the extension name, version, and skill folder path, plus the imported outline, draft, and critic agent profiles. It wraps the skill folder in a `SkillSpec`, then places the name, version, subagents, and skill into a `Manifest`. The result is a complete description of what this extension contributes; it does not change external state itself.

**Call relations**: The host system calls this function during extension discovery so it can learn what to register. Inside, the function creates a `SkillSpec` for the skill directory and then creates a `Manifest` that gathers the skill and the three imported subagent profiles into one standard package.

*Call graph*: 2 external calls (__init__, __init__).


### Browser Delegation Extension
Declares the browser-focused subagent, tools, and delegation prompt used for delegated web browsing work.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup`

This file is like the label on a plug-in box: it says what is inside and what the rest of the system can use. The browser extension provides two kinds of abilities. First, it defines a browser subagent, which is a specialized child agent meant to operate a web browser. Second, it exposes delegation tools, such as tools that let the main agent ask that browser subagent to do web work, instead of giving the main agent direct browser-control tools.

At load time, the file reads a Markdown prompt section from `prompts/browser_section.md`. That text becomes part of the main agent’s instructions, explaining how and when to delegate browser tasks. The file also imports the browser tool list, the delegation tool list, and the browser subagent profile from nearby modules.

The central piece is the `manifest()` function. It returns a `Manifest`, which is the system’s standard description of an extension: its name, version, available tools, available subagents, prompt sections, and required shared services. Here, the extension says it requires `cdp_providers`, meaning browser automation support based on the Chrome DevTools Protocol, a way for software to control and inspect browsers. Without this file, the browser extension would not be advertised to the system, so its tools, subagent, and prompt guidance would not be registered.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension’s official registration record. The system uses this record to discover the extension’s tools, browser subagent, prompt text, and required browser-control support.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the browser tools, the delegation tools, the browser subagent profile, and the prompt text read from disk earlier. It wraps the prompt text in a `PromptSection`, combines the tool lists, and puts everything into a `Manifest`. The result is a single object that describes everything this extension contributes to the system.

**Call relations**: When the extension is loaded, the system calls `manifest()` to ask, “What do you provide?” Inside, it creates a `PromptSection` for the browser guidance and then creates a `Manifest` containing that section, the tool lists, the browser subagent profile, and the required `cdp_providers` service. That manifest is then handed back to the extension-loading machinery so the browser capabilities can be registered.

*Call graph*: 2 external calls (__init__, __init__).


### Coding Worker Extension
Marks the coding extension package and exposes its repository-work agents, tools, skills, credentials, and route configuration.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this extension area using package-style imports, such as importing modules that live under `ufo_ext_coding`.

There is no setup code, no exported helper, and no hidden side effect here. Its value is structural: it tells Python and readers of the project that the surrounding directory is meant to be one named unit. You can think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer recognizable and usable by the rest of the workshop.

Without this file, some Python environments or tooling might not recognize the directory as a regular package, which could make imports, packaging, or discovery of this coding extension less reliable.


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup and extension registration`

This file is like the packing list and instruction card for the coding extension. It does not itself edit code or talk to GitHub. Instead, it describes the pieces the main system should load so those things can happen safely.

The file defines two child-agent profiles. The normal “coding” profile is for tasks like exploring a repository, changing files, running tests, and reporting back. The “fable_escalation” profile is a stronger fallback for a difficult pull-request blocker after other coding workers have failed. Both profiles say which tools the child may use, such as shell commands, file reading and writing, search, and skill loading.

The file also defines the input and output shapes for those child agents: they receive an objective and return a written result. These shapes are simple data contracts, so the parent and child agree on what is being handed over.

A major part of the file is GitHub access. Repository work often needs private clone, push, or API access. The manifest declares credential slots for either a GitHub App installation or a fallback personal access token. It also sets up safe “sentinel” injection, meaning real secrets are swapped in by the platform rather than copied directly into the sandbox.

Finally, the manifest exposes a “Connect GitHub” setup tool, a callback route for installation, and a workspace fact that records whether GitHub is connected.

#### Function details

##### `github_app_id`  (lines 126–139)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has been configured with a complete GitHub App registration. It prevents a half-configured setup, where users might be asked to connect GitHub but the system could not actually use the App correctly.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If none are present, it returns nothing, meaning this deployment is not using the GitHub App path. If some are present but others are missing, it raises an error so the problem is visible immediately. If all are present, it returns the App ID.

**Call relations**: This runs while the module is being loaded, before the manifest is built. Its result decides whether GitHub App token minting is available for the credential slots declared later in the file.


##### `manifest`  (lines 212–250)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the main object the UFO system reads to learn what the coding extension provides. It gathers the child-agent profiles, skills, credentials, setup tool, route, and workspace fact into one declaration.

**Data flow**: It starts from constants and objects defined earlier in the file: names, version, prompts, credential slots, profiles, and GitHub setup helpers. It creates skill entries from the local skills folder, defines the Connect GitHub tool with its input shape and credential binding, registers the GitHub installation callback route, and adds a fact that can tell the workspace whether the GitHub App is installed. The output is a Manifest object ready for the core system to load.

**Call relations**: The larger extension-loading system calls this function when registering the coding pack. Inside, it creates the manifest and its nested pieces using constructors such as Manifest, SkillSpec, ToolDef, ObjectBinding, ActionPresentation, RouteSpec, and WorkspaceFact. It also calls credential_object_name so the Connect GitHub tool is tied to the correct stored credential object.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, credential_object_name).


### Research Worker Extension
Declares the research extension’s web-research tools, helper agents, prompts, skills, and shared conversation data.

### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file does not perform web searches itself. Instead, it describes everything the research pack brings to the application so the main system can load it safely at startup. Think of it like the packing list on a toolbox: it says which tools are inside, which specialist helpers are available, and what outside equipment is required before the box can be used.

The file names the extension as “research” and gives it a version. It reads a Markdown prompt section from disk, which becomes the “web” guidance added to the agent’s prompt. It also points to two skill folders, “research-assistant” and “research-report”, which can be loaded when needed.

The central `manifest` function builds a `Manifest` object. That object includes the normal research tools, a wider research delegation tool, two research-focused subagent profiles, the web prompt section, the research skills, and a conversation slot for sources found during research. It also declares a requirement named `search_providers`. That is important because this pack depends on some search backend being configured elsewhere. If search is missing, the system can fail early during startup instead of surprising the user later when the first search is attempted.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the structured description the main system uses to install the research pack into a run. Someone would use it when loading extensions so the research tools, prompts, helper agents, and skills become available together.

**Data flow**: It starts with constants defined in this file and imports from nearby research modules: tool definitions, subagent profiles, the prompt text read from `web_section.md`, skill folder paths, and the shared sources slot. It wraps the prompt text in a `PromptSection`, turns each skill folder into a `SkillSpec`, and combines everything into a `Manifest`. The result is a single object that tells the system what this extension provides and that it requires a configured search provider.

**Call relations**: When the extension is discovered during setup, the loader calls `manifest` to ask, “What do you add to the system?” Inside that answer, it creates a `PromptSection` for the web prompt, creates `SkillSpec` entries for the skill directories, and then hands all of those pieces to `Manifest` so the rest of the application can register the research tools, subagents, prompt content, conversation slot, and dependency requirement as one package.

*Call graph*: 3 external calls (__init__, __init__, __init__).

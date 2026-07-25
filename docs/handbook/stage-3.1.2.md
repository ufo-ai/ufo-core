# Agentic extension manifests  `stage-3.1.2`

This stage is shared setup support for the agent system. It does not perform the work itself. Instead, each manifest file acts like a registration card that tells the main application which specialist helpers exist, what tools they can use, and what instructions or skills should be loaded for them. A “subagent” is a smaller expert agent that the main agent can delegate to when a task needs focus.

The brief-pipeline manifest registers a three-step writing workflow: outline, draft, then critique, plus skills used by the parent agent. The browser manifest registers a browser-focused subagent, its web tools, and guidance for when browsing should be handed off. The coding manifest registers the coding subagent, its tools, expected inputs and outputs, prompts, and skill folders. The research manifest loads research tools, research subagents, prompts, and optional skills. The sites manifest does the same for website-building work. Together, these files let the host discover and activate specialized extensions safely and consistently.

## Files in this stage

### Brief generation workflow
Registers the multi-stage brief pipeline that delegates outline, draft, and critique work to specialized subagents.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s calling card. When the larger system discovers this extension, it needs a simple answer to: “What are you named, what version are you, and what capabilities do you add?” Without this file, the brief-pipeline extension would have no standard way to announce its subagents or its skill instructions.

The workflow it declares is a small assembly line for writing briefs. One subagent creates an outline, another turns that outline into a draft, and a third critiques the draft. The parent agent then uses that critique to improve the final result. The file does not run the pipeline itself. Instead, it packages the ingredients the host needs to know about.

It imports three subagent profiles from the pipeline module: outline, draft, and critic. A “profile” here is a typed description of a subagent’s role and behavior. It also points to a skills directory, which contains instructions that teach the parent agent how to chain those stages together. The manifest function then returns a Manifest object, which is like a labeled contents card for the extension: name, version, subagents, and skills.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official declaration for the brief-pipeline extension. The host uses this declaration to learn the extension’s name, version, available subagents, and skill instructions.

**Data flow**: It starts with constants in this file, such as the extension name, version, and path to the skill folder, plus the three imported subagent profiles. It wraps the skill folder path in a SkillSpec, then places that skill spec and the three profiles into a Manifest. The result is a complete Manifest object that the rest of the system can read.

**Call relations**: When the extension needs to describe itself, this function creates the needed objects. It calls SkillSpec.__init__ to describe the skill directory, then calls Manifest.__init__ to bundle that skill together with the outline, draft, and critic subagent profiles.

*Call graph*: 2 external calls (__init__, __init__).


### Browser delegation
Registers the browser-focused subagent, tools, and prompt guidance used for delegated web interaction.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `extension load`

This file is like the label on a toolbox. It does not do the web browsing itself. Instead, it describes what is inside the browser extension so the larger system can plug it in correctly.

At load time, it names the extension as “browser” and gives it a version. It also reads a markdown prompt file, `browser_section.md`, which becomes guidance shown to the main agent. That guidance explains when browser work should be delegated instead of attempted directly.

The important idea is separation of responsibility. The main agent gets delegation tools, such as ways to ask for a browser task, while the browser subagent gets the more direct browser-control tools. This keeps powerful web-automation abilities scoped to the specialist child agent, rather than putting every low-level browser control in the main agent’s hands.

The returned manifest also declares a dependency on `cdp_providers`. CDP means Chrome DevTools Protocol, a browser-control interface. In plain terms, this extension needs something available that can actually connect to and drive a browser.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s official declaration. The system calls this to learn the extension name, version, available tools, browser subagent profile, prompt text, and required browser-control support.

**Data flow**: It starts with constants and imported pieces: the extension name and version, browser tools, delegation tools, the browser subagent profile, and prompt text read from disk. It packages those into a `Manifest` object, wrapping the prompt text in a `PromptSection`. The result is a single object the wider system can read to register this extension.

**Call relations**: When the extension is being loaded, the system asks this function for the manifest. Inside, it creates a prompt section for the browser guidance, then creates the manifest that contains all browser-extension declarations. That manifest is then used by the framework to expose the right tools, register the browser subagent, and check that the needed browser-control provider exists.

*Call graph*: 2 external calls (__init__, __init__).


### Coding delegation
Registers the coding extension with its subagent, tools, prompts, skills, and input-output contract.

### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension discovery`

This file exists so the core system can discover and load a ready-made software-engineering helper. Without it, the system would not know that a “coding” subagent exists, what tools it may use, what prompt should guide it, or which skills should be available on demand.

The file defines a coding subagent profile. A subagent is a child worker started by the main agent for a focused job. Here, that job is repository work: reading files, editing code, running shell commands, searching, loading skills, and handing back artifacts. The profile gives the child a prompt from `subagent_coding.md`, a fixed tool list, and a round limit so it cannot run forever.

It also defines two small data shapes using Pydantic, a library that checks structured data. `CodingInput` says the child receives an `objective`, plus an optional flag for unusually deep work. `CodingOutput` says the child returns a plain `result` string.

Finally, the `manifest()` function packages all of this into a `Manifest`: the extension name and version, the subagent profile, and the skill folders for `coding` and `code-review`. In everyday terms, this is like a product label plus assembly instructions for the coding helper.

#### Function details

##### `manifest`  (lines 63–69)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the coding extension. The core system uses this to learn which subagent and skills this extension offers.

**Data flow**: It starts from constants already defined in the file: the extension name, version, coding subagent profile, skill root folder, and skill names. It turns each skill name into a `SkillSpec`, which points to that skill’s folder, then places those skill specs and the coding subagent profile into a `Manifest`. The result is a single object the rest of the system can read to register the extension.

**Call relations**: When the extension is being loaded, the core system calls `manifest()` to ask, “What do you provide?” This function creates `SkillSpec` entries for the skill folders and passes them into `Manifest`, along with the prepared coding subagent profile, so the core loader can make the subagent and skills available.

*Call graph*: 2 external calls (__init__, __init__).


### Research delegation
Registers the research extension’s tools, subagents, prompts, and optional skill folders for investigation tasks.

### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup/config load`

This file is like the packing list for the research extension. The main application needs a clear list of what an extension adds before it can safely use it. Without this file, the system would not know which web-research tools to expose, which research helper agents to register, what extra prompt instructions to include, or which research skills can be loaded later.

At import time, the file reads a Markdown prompt section from `prompts/web_section.md`. That text becomes the extension’s “web” prompt section, meaning it can be added to the agent’s instructions when web research is available. It also points to a `skills` folder and names two skill packages: `research-assistant` and `research-report`.

The main `manifest()` function builds a `Manifest`, which is a structured declaration of the extension’s contents. It includes normal research tools, a wider research delegation tool, two research subagent profiles, the web prompt section, and the skill specifications.

One important detail is `requires=("search_providers",)`. This says the research pack depends on some configured search backend. The extension does not own the search credentials itself. Instead, it asks the larger deployment to provide search support, so a missing search setup is caught at startup rather than failing later during a user’s first search.

#### Function details

##### `manifest`  (lines 27–36)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s formal declaration. The main system uses this to learn what tools, subagents, prompt text, skills, and required dependencies the research pack brings.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the research tool lists, the research subagent profiles, the loaded web prompt text, and the skill folder names. It wraps the prompt text in a `PromptSection`, turns each named skill folder into a `SkillSpec`, then packages everything into a `Manifest`. The result is a single object the host application can read to install the extension’s capabilities.

**Call relations**: During extension loading, the host asks this function for the research pack’s declaration. Inside, it creates a `PromptSection` for the web instructions, creates `SkillSpec` objects for the two research skills, and then creates the final `Manifest` that hands all of those pieces back to the system.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Site building delegation
Registers the sites extension with website-building tools, prompts, skills, and its subagent profile.

### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup or extension load`

This file answers a simple question: “What does the sites extension bring into the system?” Without it, the rest of the application would not know that this extension can build and serve websites, delegate website work to a specialized child agent, or load the website-building instructions and templates.

Think of it like a packing list for a toolkit. The file names the pack, gives it a version, reads a prompt section from disk, points to the website-building skill folder, and gathers together the tools and subagent profile imported from nearby modules. The prompt section is extra guidance shown to the main agent, so it knows how to serve and check a site before returning it. The skill path tells the skill loader where to find the website-building knowledge and bundled files.

The main work happens in `manifest`, which returns a `Manifest` object. That object is the structured summary the host system can read. It includes normal site tools, delegation tools such as building through a child agent, the website-building subagent profile, the prompt section, and the loadable website-building skill.

#### Function details

##### `manifest`  (lines 29–37)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the sites extension. The larger system uses this manifest to learn what tools, prompts, skills, and subagents the extension provides.

**Data flow**: It reads the constants defined in this file, including the extension name and version, the prompt text loaded from `prompts/sites_section.md`, and the skill folder path. It combines those with imported site tools, delegation tools, and the website-building subagent profile. The result is a `Manifest` object that the host can consume; it does not modify external state itself.

**Call relations**: When the extension is loaded, the host calls `manifest` to ask what this package contributes. Inside, it creates a `PromptSection` for the agent-facing instructions, a `SkillSpec` pointing at the website-building skill, and then wraps everything in a `Manifest` so the host can register those pieces together.

*Call graph*: 3 external calls (__init__, __init__, __init__).

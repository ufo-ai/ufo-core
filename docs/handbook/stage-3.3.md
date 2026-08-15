# Agent skill, subagent, and skill-support extensions  `stage-3.3`

This stage is behind-the-scenes setup for agent abilities. It does not perform the main work itself. Instead, it tells the larger UFO system which extra “extension” packs exist and how to load them. Most files here are manifests, meaning registration sheets that list the agents, tools, prompts, skills, and outside services an extension needs.

The brief pipeline manifest registers three specialist helpers that make a brief in steps: outline, draft, then critique. The browser package file makes the browser extension importable, while its manifest registers browser delegation tools and required browser capability. The coding manifest signs up a software-repository helper, its routes, tools, skills, and GitHub credentials. The documents manifest adds writing, editing, review, and formatting support. The research manifest registers search-based research tools, helper profiles, prompts, skills, and stored conversation data, and requires a search backend. The sites manifest adds website-building tools, prompts, skills, subagents, surfaces, and chat objects. The sample skill probe is a small test switch: running it prints success, proving the skill can be reached.

## Files in this stage

### Brief generation registration
Registers the brief-building workflow, including outlining, drafting, critique subagents, and its supporting skill folder.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This is the extension’s “label on the box.” When the host application discovers the brief-pipeline extension, it needs a simple description of what the extension contains and how to load it. This file provides that description.

The extension is built around a small writing workflow. One subagent makes an outline, a second turns that outline into a draft, and a third reviews the draft like a critic. The parent agent stays in charge and uses the critic’s feedback to improve the final result. An everyday analogy is an editor coordinating three helpers: one plans the article, one writes it, and one reviews it.

The file imports the three subagent profiles from the pipeline code, points to the directory that contains the extension’s skill instructions, and gives the extension a name and version. Its single function, `manifest`, packages all of this into a `Manifest` object. Without this file, the extension might still have useful pieces on disk, but the host system would not know they belong together or how to expose them.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the formal description of the brief-pipeline extension. The host system uses it to learn the extension’s name, version, subagents, and skill location.

**Data flow**: It starts with constants in this file, such as the extension name, version, and skill directory, and with the imported outline, draft, and critic profiles. It wraps the skill directory in a `SkillSpec`, then returns a `Manifest` containing the extension’s identity, its three subagents, and its skill definition. It does not change files or global state; it simply returns a ready-to-use description.

**Call relations**: When the extension is being loaded, the host calls `manifest` to ask, “What do you provide?” Inside, it creates a `SkillSpec` for the skill folder and then creates a `Manifest` that ties that skill together with the three subagent profiles.

*Call graph*: 2 external calls (__init__, __init__).


### Browser extension package
Defines the browser extension package and registers its browser subagent, delegation tools, prompts, and dependency.

### `extensions/browser/ufo_ext_browser/__init__.py`

`other` · `startup/import`

This is a very small package entry file. Its main job is to identify this folder as the home of the browser-related extension code. The short text inside says that this package contains sandbox browser and computer-use tools, plus a browser subagent profile. In plain terms, this is the part of the project meant to let an automated agent use a controlled browser-like environment.

There is no executable logic here. Nothing is configured, started, or changed by this file on its own. Its value is organizational: it gives Python an importable package boundary and gives humans a quick signpost about what belongs in this folder. Without it, depending on the project’s import setup, other code might not be able to refer to this directory as a package, or newcomers would have one less clue about the folder’s purpose.


### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup`

This file is like a label on a toolbox. It does not perform browser automation itself. Instead, it declares what browser-related parts should be made available when this extension is loaded.

The browser extension has two layers. The main agent does not directly receive the low-level browser tools, such as tools that click around or inspect pages. Instead, it gets delegation tools, such as ways to ask a dedicated browser subagent to do the web work. That browser subagent is registered here through its profile, which gives it the right tools and instructions for operating a browser.

The file also reads a Markdown prompt section from disk. That prompt text is added to the main agent’s instructions so the agent knows when and how to delegate browser tasks. The manifest also declares a requirement named `cdp_providers`. CDP means Chrome DevTools Protocol, a way for software to control and observe a browser. Without that provider, the browser subagent would not have the underlying connection it needs.

In short, this file is the extension’s official contract with the host system: what tools it contributes, what subagent it adds, what instructions should be included, and what outside browser-control support must exist.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension’s manifest, which is the package of information the host system uses to load this extension correctly. Someone would use it when the system is discovering extensions and needs to know what this browser extension contributes.

**Data flow**: It reads the constants defined in this file, including the extension name, version, prompt section name, and prompt text already loaded from the Markdown file. It combines imported browser tools, delegation tools, and the browser subagent profile into one `Manifest` object. The result is a complete description of the browser extension, including its required `cdp_providers` capability.

**Call relations**: During extension loading, the host calls `manifest` to ask this file what it offers. The function creates a `PromptSection` for the browser instructions, then passes that along with tools, subagents, and requirements into `Manifest.__init__`. That completed manifest is handed back to the host so the browser extension can be registered.

*Call graph*: 2 external calls (__init__, __init__).


### Specialist agent extensions
Registers major specialist capability packs for coding, document work, research, and website building.

### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension loading`

This file exists so the main system can discover and load the coding helper in a predictable way. The coding helper is a subagent: a separate worker agent that can be given a programming objective, inspect files, edit code, run commands, search, and report back. Without this manifest, the core system would not know that the coding subagent exists, what tools it may use, what prompt shapes its behavior, or what input and output format to expect.

The file first names the extension, its version, the skill folder, the allowed tool names, the subagent prompt, and the round limit. It then defines two small data shapes: `CodingInput`, which carries the task the child should do, and `CodingOutput`, which carries the child’s final report.

A large part of the file is about GitHub credentials. Private repository work needs authentication, but the sandbox should not directly hold real secrets. The manifest describes a credential slot and an injection target: in plain terms, it says “when Git talks to github.com, replace this harmless placeholder with the real token at the safe boundary.” It also supports a GitHub App setup, if the deployment has all required environment variables.

Finally, `manifest()` bundles everything into one `Manifest` object: the subagent, skill, credentials, a `connect_github` tool, and a web route GitHub can call after installation.

#### Function details

##### `github_app_id`  (lines 95–108)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether this deployment has been configured with a GitHub App registration. It returns the app ID only when all required GitHub App settings are present, and it deliberately fails if only some are present, because a half-configured GitHub App would create confusing broken behavior.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If none are set, it returns `None`, meaning the system should not use GitHub App token minting. If some are missing but others are present, it raises an error that names the missing settings. If all are present, it returns the GitHub App ID.

**Call relations**: This check is used while the file builds the Git credential definition. If there is no GitHub App registration, the credential slot expects a member-provided token. If the registration is complete, the credential can instead get tokens from the GitHub App flow.


##### `manifest`  (lines 137–162)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension description that the core system loads. It is the single place that packages the coding subagent, its skill, GitHub credentials, the GitHub connection tool, and the callback route into one object.

**Data flow**: It takes no input from the caller. It uses constants and objects already defined in the file, such as the coding profile, skill path, credential slots, tool handler, and route handler. It returns a `Manifest` object that tells the host system exactly what this extension contributes.

**Call relations**: When the extension is loaded, the host calls this function to learn what to register. Inside, it creates `SkillSpec` entries for the skill folder, a `ToolDef` for the `connect_github` tool, a `RouteSpec` for the GitHub installation callback route, and then hands all of that to `Manifest` so the wider system can wire it in.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup / extension discovery`

This file is the front door for the documents pack. Think of it like a table of contents for a toolbox: it names the toolbox, gives its version, points to the folders where the tools live, and says which helper worker comes with it.

The extension provides several “skills,” which are packaged workflows the agent can load when needed. Some skills focus on file formats, such as Word documents, PowerPoint slides, Excel spreadsheets, and PDFs. Others support shared needs, such as design foundations, document review, theme creation, and writing drafts. The file records the skill folder names and uses the shared skills directory as their base location.

It also includes a `writing` subagent profile. A subagent is a child worker the main agent can start for a focused job. In this case, the writing subagent is prepared to work on drafting and editing prose.

The main job of this file is not to perform document work itself. Instead, it packages the extension’s offerings into a `Manifest`, which is the system’s standard way to announce, “Here is what I provide, and here is where to find it.”

#### Function details

##### `manifest`  (lines 35–41)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the documents extension’s manifest, which is the system-readable summary of this extension’s name, version, subagent, and available skills. The loader uses this so the document tools can be found and loaded on demand.

**Data flow**: It starts with the constants in this file: the extension name, version, skills directory, list of skill names, and writing subagent profile. For each skill name, it creates a `SkillSpec`, which points to that skill’s folder on disk. It then puts those skill specs and the writing subagent profile into a `Manifest` object and returns it.

**Call relations**: When the extension system asks this pack what it provides, `manifest` assembles the answer. It calls `SkillSpec.__init__` to describe each skill folder, then calls `Manifest.__init__` to wrap the extension name, version, subagent, and skills into one object the wider system can consume.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file describes what the “research” pack adds to the larger UFO system. Think of it like the label on a plug-in box: it does not perform searches itself, but it tells the host application what is inside the box and what must already be installed for it to work.

The file names the extension, sets its version, reads a web-research prompt section from a Markdown file, and points to two skill folders that can be loaded when needed. It gathers the research tools, a wider delegation tool, two research-focused subagent profiles, and a conversation slot used for saved source information. A “conversation slot” is a named place where the system can keep structured information across a conversation, such as sources found during web research.

The important safety check is the declared requirement: `requires=("search_providers",)`. The research pack does not own the search service credentials itself. Instead, it depends on whichever search provider the deployment has configured. By declaring that dependency here, the system can fail clearly at startup if research is enabled without search support, instead of waiting until a user asks for research and then breaking mid-task.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the object the main system reads to learn what the research pack contributes. Someone would use this when loading extensions so the research tools, subagents, prompt section, skills, and requirements become visible to the host application.

**Data flow**: It starts with constants and imported pieces already defined in the file: the extension name and version, research tools, subagent profiles, prompt text read from disk, skill folder paths, the sources conversation slot, and the required search provider dependency. It wraps the prompt text in a `PromptSection`, turns each skill folder into a `SkillSpec`, and puts everything into a `Manifest`. The output is that single `Manifest` object; it does not modify outside state by itself.

**Call relations**: When the extension system asks this file what it provides, `manifest` assembles the answer. During that assembly it creates a `PromptSection` for the web prompt text, creates `SkillSpec` objects for the research skills, and finally passes all collected pieces into `Manifest.__init__` so the host can register them during startup.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `startup / extension load`

This file answers a simple question: when the “sites” extension is loaded, what should become available to the agent and to users? Without it, the code for building and serving websites might exist, but the main system would not know to offer those tools, load the website-building instructions, register the child agent profile, or expose hosted sites in the user interface.

Think of it like a packing list for a workshop. The workshop needs tools, instructions, a specialist helper, and a display table. This manifest lists all of those pieces in one place. It gives the extension a name and version, reads a prompt section from a Markdown file, points to the website-building skill folder, and gathers objects imported from nearby modules: site tools, delegation tools, the website-building subagent profile, the hosted-site surface, the site chat object, and a conversation slot.

The important function, `manifest`, returns a `Manifest` object. A manifest is a structured description the host application can read during extension loading. It says: add these tools to the agent, add this prompt text to its instructions, make this skill loadable, register this subagent type, and connect the hosted-site viewing/editing pieces. This file does not build websites itself. It makes sure all the website-building parts are discoverable and connected.

#### Function details

##### `manifest`  (lines 36–47)

```
def manifest() -> Manifest
```

**Purpose**: Creates the complete declaration for the sites extension. The host system calls this so it can learn what website-related abilities, instructions, and user-facing pieces the extension provides.

**Data flow**: It starts with constants and imported extension pieces: the extension name and version, tool lists, the site object, the website-building subagent profile, the hosted-site surface, prompt text read from disk, the skill folder path, and the conversation slot. It wraps the prompt text in a prompt section, wraps the skill path in a skill specification, and returns one `Manifest` object containing all of those parts. It does not change state by itself; it packages information for the rest of the system to consume.

**Call relations**: During extension loading, the larger UFO system asks this function for the sites manifest. Inside that handoff, it creates small manifest-related objects for the prompt section and skill path, then hands the finished manifest back to the loader. The loader can then make the tools, subagent, skill, surface, object kind, and conversation slot available where the agent and chat system need them.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Sample skill probe
Provides a minimal executable probe used to confirm that a sample skill can be discovered and run.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or health check`

This file solves a very small but useful problem: it gives the sample skill a quick “are you alive?” check. Running the file prints the text `sample-skill-probe-ok`. That is like a doorbell: it does not do the work of the whole house, but it proves someone can press it and get a response.

There is no setup, no input, and no hidden decision-making here. The script immediately writes one line to standard output, which is the normal place a command-line program prints text. A surrounding system could run this file during installation, testing, or discovery to confirm that Python can load and execute the sample skill’s probe file.

Without this file, there would be no simple built-in signal that the sample skill is present and runnable. It does not test the skill’s real behavior; it only confirms the most basic execution path.

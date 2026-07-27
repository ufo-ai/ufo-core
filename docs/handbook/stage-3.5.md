# Agent, skill, document, research, and creation extension manifests  `stage-3.5`

This stage is behind-the-scenes setup. It is made of “manifest” files, which are like menu cards that tell the main UFO system what extra abilities are available before work begins. Each manifest names the tools, helper agents, prompts, and skill folders that should be loaded when an extension is turned on.

The browser manifest adds a browser helper agent and the instructions for sending web-browsing tasks to it. The coding manifest adds a coding helper, programming skills, GitHub access, routes, and needed credentials. The documents manifest lists skills for working with Word, PowerPoint, PDFs, spreadsheets, themes, and reviews. The research manifest adds research tools, research-focused helper agents, prompts, and skills. The sites manifest declares website-building tools, prompts, skills, and its site-building helper profile. The brief-pipeline manifest ties several helper agent stages together and adds a skill that teaches a parent agent to run them in sequence. Together, these files let the system discover and assemble specialist capabilities without hard-coding them into the core.

## Files in this stage

### Brief pipeline orchestration
Manifest for coordinating a multi-stage brief workflow through helper agents and a parent-facing sequencing skill.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup or extension discovery`

This is the extension’s “front desk” file. When the larger system discovers the brief-pipeline extension, it needs a simple summary of what the extension adds. Without this file, the system would not know the extension’s name, version, which subagents it provides, or where to find the instructions for using them.

The extension is built around a three-step writing pipeline: first an outline agent, then a draft agent, then a critic agent. The idea is like an assembly line for a short written brief. One worker plans the shape, the next writes the first version, and the last points out what should be improved. The parent agent remains in charge and uses the critic’s feedback itself.

The file imports the three subagent profiles from the pipeline module. A “profile” here is a typed description of a subagent: what kind of helper it is and how it should behave. It also points to a skill directory, which contains the teaching material or instructions for the parent agent. The only function, `manifest`, packages all of this into a `Manifest` object so the host system can load the extension cleanly.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds the official description of the brief-pipeline extension. The system uses this description to learn the extension’s name, version, available subagents, and skill instructions.

**Data flow**: It starts with fixed values in this file: the extension name, version, skill folder path, and the three imported subagent profiles. It wraps the skill folder in a `SkillSpec`, which is a small description of where the skill lives. Then it creates and returns a `Manifest`, which is the complete package of information the host system needs to register the extension.

**Call relations**: This function is called when the extension is being loaded or inspected. Inside it, `SkillSpec.__init__` is used to describe the skill directory, and `Manifest.__init__` is used to assemble the final extension declaration that the rest of the system can consume.

*Call graph*: 2 external calls (__init__, __init__).


### Delegated browsing and coding
Manifests that register specialist subagents, tools, prompts, skills, and integrations for browser and coding work.

### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `startup / extension load`

This file is like the label and instruction card on a toolbox. When the system loads the browser extension, it needs to know three things: what the extension is called, which tools it adds, and how agents should be taught to use those tools safely. The file declares the extension name and version, reads a prompt section from a Markdown file, and builds a Manifest object. A manifest is a compact description of an extension that the host system can load at startup.

The important design choice here is separation. The main agent does not directly receive the full set of low-level browser-control tools. Instead, it gets delegation tools, such as tools that ask a browser subagent to do a web task. The browser subagent gets the fuller browser tool surface. This is like asking a specialist to operate heavy machinery rather than handing the controls to everyone.

The manifest also registers BROWSER_PROFILE, which describes the browser subagent, and declares a dependency on "cdp_providers". CDP means Chrome DevTools Protocol, a way for software to control and inspect a browser. Without this file, the host system would not know that the browser extension exists, which tools to expose, which subagent profile to register, or which prompt guidance to include.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension's manifest, which is the package description the host system uses to load this extension. Someone uses this when they want the system to discover the browser tools, browser subagent, prompt section, and required browser-control support.

**Data flow**: It starts with module-level information: the extension name and version, the browser tools, the delegation tools, the browser subagent profile, the loaded prompt text, and the required dependency name. It wraps the prompt text in a PromptSection, combines the tool lists, and places everything into a Manifest. The result is a single Manifest object that the rest of the system can read to install this extension's capabilities.

**Call relations**: During extension loading, the host calls this function to ask, "What do you provide?" The function creates a PromptSection for the browser instructions and then creates a Manifest that contains that prompt section along with the tools, subagent profile, and dependency requirement. It hands that finished manifest back to the host so the browser extension can become part of the agent's available setup.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and wiring diagram on a plug-in box. Without it, the main UFO system would not know that there is a coding subagent it can start, which tools that child agent may use, where its prompt and skills live, or how GitHub access should be set up.

The file defines a “coding” subagent profile: a child agent meant to explore repositories, edit files, run tests, and return a written result. It also declares two loadable skills, “coding” and “code-review”, so the main agent can learn when to delegate programming work or review pull requests.

A major part of the file is GitHub authentication. It declares a credential slot, which is a named place where secret access can be supplied without putting the real secret directly inside the sandbox. If a deploy has a GitHub App configured, the file uses that app to mint per-turn Git tokens. If not, it falls back to a user-provided GitHub token. The token is injected only when requests go to github.com, using a sentinel value inside the sandbox so the real secret is swapped in later by safer infrastructure.

Finally, the manifest exposes a connect_github tool and a web route that completes GitHub App installation for a workspace.

#### Function details

##### `github_app_id`  (lines 91–105)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has been configured with a GitHub App. It also protects against a half-configured setup, where one required GitHub App setting is present but others are missing.

**Data flow**: It reads environment variables from the operating system. If the main GitHub App ID is absent, it returns no value, meaning the extension should not use GitHub App token minting. If the App ID is present, it checks that the client ID, client secret, and private key are also present; if any are missing, it raises an error instead of letting the system fail later in a confusing way. If everything is present, it returns the App ID.

**Call relations**: This function is used while the file is being loaded to decide how the Git credential slot should get its token. If it returns no App ID, the extension expects a normal GitHub token to be supplied. If it returns an App ID, the credential slot is connected to the GitHub App token source.


##### `manifest`  (lines 134–158)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension manifest, which is the object the core UFO system reads to learn what this coding extension offers.

**Data flow**: It takes no input from the caller. It gathers the constants and objects defined earlier in the file: the extension name and version, the coding subagent profile, the skill folders, GitHub credential slots, the connect_github tool, and the GitHub installation callback route. It packages all of that into one Manifest object and returns it.

**Call relations**: This is the file’s main handoff point to the rest of the system. When the core extension loader asks for the manifest, this function creates SkillSpec objects for the available skills, a ToolDef for the GitHub connection tool, a RouteSpec for the GitHub installation route, and finally wraps them all in a Manifest so the core system can register them.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### Document and research capabilities
Manifests that expose document-processing skills and research-focused tools, agents, prompts, and skills.

### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup or extension discovery`

This file is like a label on a toolbox. It does not do the document work itself. Instead, it tells the UFO system which document-production tools are available in this extension, where to find them on disk, and what version of the extension this is.

The skills live in folders under a local `skills` directory. Each folder contains instructions and supporting files for a specific capability, such as making a DOCX document, creating a PowerPoint deck, reviewing documents, or applying shared design foundations. The larger system can later read this manifest, register those skills, and load a skill only when it is needed.

The important idea is that this file turns a group of folders into a formal extension package. Without it, the skills might still exist on disk, but the agent would not have a clear, standard way to discover them. The `manifest()` function builds a `Manifest` object with the extension name, version, and one `SkillSpec` entry for each skill folder. That gives the loader a clean map of what this extension contributes.

#### Function details

##### `manifest`  (lines 28–33)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the documents extension. A manifest is a small description object that tells the system this extension’s name, version, and the list of skill folders it provides.

**Data flow**: It starts with the fixed extension name, version, skills root folder, and skill names defined in this file. For each skill name, it creates a `SkillSpec`, which points to that skill’s folder. It then puts all of those skill descriptions into one `Manifest` object and returns it to the caller.

**Call relations**: When the extension system needs to know what this package contains, it calls this function. The function hands off each skill path to `SkillSpec.__init__` to describe one loadable skill, then hands the full set of skill descriptions to `Manifest.__init__` so the extension can be registered as a single package.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup / extension load`

This file is the “packing list” for the research extension. When the application starts, it needs to know what an extension adds before it can offer those abilities to an agent. Here, the extension says: “I am called research, this is my version, these are my tools, these are my specialist helper agents, this is the web-research prompt text, and these are the skill folders to load.”

The file reads a prompt section from a Markdown file called `web_section.md`. That text becomes an extra instruction block named `web`, so the agent knows how to behave when doing web research. It also points to two skill directories, `research-assistant` and `research-report`, which are loaded only when needed.

A key detail is the `requires=("search_providers",)` line. The research pack does not own search credentials itself. Instead, it depends on a separately configured search backend. This is like a lamp that does not include its own power supply: the manifest makes sure the house has electricity before anyone tries to switch the lamp on. If research is enabled without a search provider, the system can fail early at startup instead of breaking later during a search.

#### Function details

##### `manifest`  (lines 27–36)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the research extension. The main system uses this description to know which research tools, helper agent profiles, prompt text, and skills should become available.

**Data flow**: It starts with constants defined in this file, such as the extension name, version, loaded web prompt text, skill folder path, and skill names. It combines those with imported research tools and subagent profiles. The result is a `Manifest` object that tells the rest of the system exactly what this extension contributes and that it needs a configured search provider.

**Call relations**: When the extension is discovered during startup, the system calls `manifest` to ask what this package offers. Inside, it creates a prompt section for web research, creates skill specifications for the skill folders, and wraps everything into a manifest object that the host application can register and use.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Site creation capabilities
Manifest for website-building tools, prompts, skills, and the supporting site-focused subagent profile.

### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `extension load`

This file answers a simple question for the host system: “If someone installs the sites extension, what new abilities should become available?” It gathers together the pieces that let an agent build and serve websites. Those pieces include normal tools for working with sites, delegation tools for asking a specialized helper agent to build a website, a website-building subagent profile, a prompt section that teaches the main agent how to use the site workflow, and a loadable skill named “website-building.”

A manifest is like a packing list in a box. The code does not build the website itself. Instead, it lists everything the system should unpack and register when this extension is loaded. It reads a Markdown prompt file from the local prompts folder, points to the skills folder, and names the extension as “sites” with version “0.1.0.”

The important behavior is that the skill path points at the parent website-building skill. The comment explains that a nested webapp skill is discovered along with it, while other folders are bundled supporting files. Without this file, the platform would not know that these website tools, prompts, skills, or the website-building subagent exist.

#### Function details

##### `manifest`  (lines 29–37)

```
def manifest() -> Manifest
```

**Purpose**: Creates the official manifest object for the sites extension. The larger system uses this returned object to register website tools, prompts, skills, and the website-building subagent.

**Data flow**: It starts with constants defined in this file and imports from nearby site-related modules: the extension name and version, tool lists, the website-building profile, the prompt text read from disk, and the path to the website-building skill. It packages those into a Manifest object, wrapping the prompt text in a PromptSection and the skill folder in a SkillSpec. The result is a single structured object that describes everything this extension contributes.

**Call relations**: When the extension loader asks this file what it provides, this function builds the answer. To do that, it creates a PromptSection for the extra instructions, a SkillSpec for the website-building skill folder, and then hands all of those pieces to Manifest so the host system can register them together.

*Call graph*: 3 external calls (__init__, __init__, __init__).

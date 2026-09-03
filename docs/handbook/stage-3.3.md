# Workspace app extension manifests  `stage-3.3`

This stage is the app catalog’s set of registration cards. It is mostly used during startup or installation, when the workspace host scans first-party extensions and decides what apps are available, what agents to create, what setup is needed, and what home screens or background jobs to enable. A manifest is a small configuration file that describes an app to the platform, rather like a label on a plug-in device telling the computer what it is.

Each app has a manifest for that job. Artifacts, Chat, Radar, and Wiki declare their agents and homepage skills, which are the pieces that show or control the app’s main screen. Code adds its packaged skills and GitHub setup needs. Issues and Meetings also describe scheduled work, meaning tasks the system should run on a timer. Metrics registers a workspace-wide reporting agent that can use connected accounts or keys.

The `__init__.py` files for Chat, Code, Issues, Meetings, Metrics, Radar, and Wiki do not run app logic. They simply mark folders as importable Python packages, so the host can find the extension code.

## Files in this stage

### Core workspace apps
Foundational workspace apps register their package boundaries, agents, homepage skills, and install metadata.

### `extensions/app_artifacts/ufo_ext_app_artifacts/manifest.py`

`config` · `startup / extension loading`

This file is like the label and setup card inside a boxed app. The Artifacts app gives a workspace a shared shelf of files and hosted sites, shown newest first, with search, filters, paging, and a viewer for opened files. Without this manifest, the larger system would not know that this extension exists, what agent to create for it, or where to find the skill that powers its homepage.

The file starts by naming the app and its version. It then points to the folder that contains the app’s skills, which are reusable instruction packs the agent can load when it needs to perform a specific job. The main skill here is the homepage skill, `app-artifacts-home`.

Next, it defines the Artifacts app agent. An agent is the workspace-facing assistant identity for this app. Its prompt explains what the homepage should do and reminds the agent to keep the shelf controls and file viewer working when editing the page. The agent is marked as visible to the workspace, uses automatic model selection, and is not allowed internet access.

Finally, the `manifest` function packages all of this into a `Manifest` object. That object is what the shared app infrastructure reads during extension loading.

#### Function details

##### `manifest`  (lines 40–46)

```
def manifest() -> Manifest
```

**Purpose**: Builds the official description of this extension for the host system. Someone would use it when the platform is discovering extensions and needs to know this app’s name, version, agent, and skill files.

**Data flow**: It reads the constants defined earlier in the file, including the app name, version, prepared agent definition, and path to the homepage skill. It wraps the skill path in a `SkillSpec`, then returns a `Manifest` containing the app metadata, the Artifacts agent, and the skill list. Nothing is changed on disk or in the network; the result is a structured description object.

**Call relations**: During extension loading, the platform calls this function to ask, “What do you provide?” The function creates a `SkillSpec` for the homepage skill and hands that, together with the prebuilt agent definition, into `Manifest`. The returned manifest is then used by the app infrastructure to provision the Artifacts workspace agent and make its homepage skill available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_chat/ufo_ext_app_chat/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the system may need to load code from `extensions/app_chat/ufo_ext_app_chat` using normal Python import paths. Without this file, some Python setups or tooling might not recognize the folder as a package, which could make the app chat extension harder or impossible to import reliably. There are no functions, classes, settings, or startup actions here. Its job is more like putting a label on a drawer: the drawer may contain useful tools elsewhere, but this label is what lets the rest of the workshop find it by name.


### `extensions/app_chat/ufo_ext_app_chat/manifest.py`

`config` · `startup / extension discovery`

This file is a small registration card for the workspace chat app. It does not run the chat itself. Instead, it tells the larger system, “there is an app called app_chat, here is its version, here is the agent that represents it, and here is the skill file to load for its home screen.”

The main object described here is the chat agent. Its prompt tells the agent that its job is to be the Chat app for the workspace: show an existing conversation if one is open, show starter prompts if not, keep the message composer working, and stream live replies. The agent is marked as the main visible workspace agent, uses an automatic model choice, has medium reasoning effort, and is allowed to use the internet.

The file also points to a skill named app-chat-home under this extension’s skills folder. A skill is a packaged set of behavior or page instructions that the platform can load when the app needs to show or update part of the interface.

Without this manifest, the platform would not know that the Chat app exists, what agent should power it, or which skill should be loaded for its home page.

#### Function details

##### `manifest`  (lines 36–42)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension’s manifest, which is the structured description the platform reads to register the Chat app. Someone would use it when discovering or loading this extension.

**Data flow**: It starts with the constants defined in this file: the app name, version, chat agent description, and path to the home skill. It wraps the home skill path in a SkillSpec, combines it with the already defined chat agent, and returns a Manifest object. The result is a complete package of metadata that tells the workspace how to install and expose this chat app.

**Call relations**: When the extension system asks this file for its manifest, this function creates the final Manifest object. As part of that, it creates a SkillSpec for the app-chat-home skill so the larger system can later load that skill when the chat home screen is needed.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_code/ufo_ext_app_code/__init__.py`

`other` · `package import`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold useful tools in other files, but this label is what lets the rest of the program find the drawer by name. Because the file is empty, importing `ufo_ext_app_code` does not set up state, load configuration, or run any feature code. Its value is structural: without it, some Python environments or tooling might not recognize this directory as a package, and imports that expect `extensions.app_code.ufo_ext_app_code` to exist could fail.


### `extensions/app_code/ufo_ext_app_code/manifest.py`

`config` · `startup / extension discovery`

This file defines the manifest for the Code app, an app that reviews GitHub pull requests in a workspace. Without this file, the platform would not know that the app exists, what agent to provision, what prompt to give that agent, or which extra instructions to install as skills.

The file starts by naming the app and locating its prompt and skill folders. It reads the main review prompt from `prompts/agent_code.md`, then builds an `AgentProvision`, which is the recipe for creating or adopting the workspace agent named `code`. That recipe says what the agent is for, which model it uses, whether it can access the internet, how large its sandbox should be, and that it should be visible across the workspace.

It also declares setup requirements. The agent needs a GitHub connector, and it expects a source trigger: a wake-up signal from a shared pull-request feed when a pull request changes. A separate “babysit” skill is included for later follow-up work, such as checking pull requests until they merge or close. The comments explain an important design choice: prompts are written once into an agent and may be edited by users, but skills are shipped files, so new behavior can reach existing workspaces through skills more reliably than by changing old prompt text.

#### Function details

##### `manifest`  (lines 99–108)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete app manifest that the platform uses to install or load the Code app. It packages together the app name, version, agent definition, and the skill files that should be made available.

**Data flow**: It takes no input from the caller. It reads the module-level constants already defined in this file, including the app name, version, prepared agent recipe, and skill paths. It then creates a `Manifest` object containing one agent and two `SkillSpec` entries, and returns that object to whoever is loading the extension.

**Call relations**: When the extension system asks this module what it provides, this function is the handoff point. It builds the manifest object and creates skill specifications for the home skill and babysitting skill, so the platform can later provision the agent and expose those shipped skill documents.

*Call graph*: 2 external calls (__init__, __init__).


### Project workflow apps
Workflow-focused apps declare importable packages, required connected accounts, setup needs, and scheduled work.

### `extensions/app_issues/ufo_ext_app_issues/__init__.py`

`other` · `package import`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but this label is what lets the rest of the system find and open it by name.

For this extension, the package name is `ufo_ext_app_issues`. Other files can import modules from it because this file exists. Since it is empty, it does not set up settings, run startup code, define helper functions, or expose a public API directly. Its main value is structural: it makes the extension’s folder part of Python’s module system.

If this file were removed, imports may fail in environments or tooling that still expect traditional Python packages. Even where modern Python can sometimes import folders without it, keeping the file makes the package boundary explicit and predictable.


### `extensions/app_issues/ufo_ext_app_issues/manifest.py`

`config` · `startup / extension discovery`

This file tells the system how to install and present an app that works over an issue tracker such as GitHub Issues. The app has one workspace-visible agent named “issues.” Its main job is to triage open issues: read each new issue, explain what it is asking for, suggest an owner, and write a plan as a comment on the issue itself. That comment is important because it becomes the visible record that the issue has already been triaged, so the app does not keep reacting to its own comments and spending time in a loop.

The file also describes a second feature: implementing approved issues. That work is deliberately gated by the label `ufo:implement`. In plain terms, the label is the written permission slip on the issue. Without it, the app may plan the work but should not write code for it.

Most of the file is configuration: names, prompts, setup instructions, a GitHub connector requirement, and a schedule for periodic triage. The schedule offers hourly and morning options so members can choose how often the app sweeps the backlog. Finally, the file exposes a `manifest()` function, which packages all of this into a `Manifest` object the platform can read when loading the extension.

#### Function details

##### `manifest`  (lines 128–134)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the app manifest, which is the platform-readable description of this extension. Someone would use it when the system is discovering available apps and needs to know what agents and skills this app provides.

**Data flow**: It starts with the constants defined earlier in the file: the app name and version, the prebuilt Issues agent definition, and the path to the home-page skill. It wraps the home skill path in a `SkillSpec`, then puts the name, version, agent, and skill into a `Manifest`. The result is a complete registration object; it does not change external state.

**Call relations**: When the extension is loaded, this function is the handoff point from this file to the wider platform. Inside, it creates a `SkillSpec` for the home skill and a `Manifest` for the whole app, so the platform can later provision the Issues agent and know which skill belongs with it.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_meetings/ufo_ext_app_meetings/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this extension using normal Python import paths, such as importing modules from `ufo_ext_app_meetings`.

There is no code inside this file, so it does not start anything, configure anything, or change data. Its value is structural: it tells Python and project tooling that the meeting app extension is a named package. A simple analogy is a label on a drawer. The label does not contain the tools, but it makes the drawer recognizable and usable by the rest of the workshop.

Without this file, depending on the Python version and packaging setup, imports for this extension might fail or behave differently. Keeping it here makes the package boundary explicit and helps the extension fit cleanly into the larger system.


### `extensions/app_meetings/ufo_ext_app_meetings/manifest.py`

`config` · `startup / app registration`

This file is the Meetings app’s registration card. It does not brief meetings itself. Instead, it describes the app to the wider system: its name, version, agent, skills, calendar connection, and scheduled task. Think of it like the label and setup sheet inside a boxed appliance: it says what the appliance is for, what plug it needs, and what routine it should follow once installed.

The main idea is that one workspace agent owns the whole “meetings” subject. Today, only meeting briefs are turned on by default. The agent is told to look at the workspace calendar and, on each scheduled run, brief meetings that start before the next run and have not started yet. That time window is important because it prevents the same meeting from being briefed twice.

The file also describes two future or optional features: follow-ups and meeting notes. These are not silently done in the background. The prompt tells the agent to wait until a member asks, then connect the right notes account and create the right scheduled task. Finally, the file points to a home-page skill, so the app has a user-facing screen for its status and conversations.

#### Function details

##### `manifest`  (lines 111–117)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the app manifest, which is the platform-readable description of the Meetings app. The platform uses it to learn the app’s name, version, agent setup, and home skill.

**Data flow**: It takes no input from the caller. It reads the constants defined earlier in the file, creates a skill entry pointing at the Meetings home skill, wraps everything into a Manifest object, and returns that object to whoever is loading the extension.

**Call relations**: When the extension system asks this file what it provides, this function is the answer. It creates a SkillSpec for the home-page skill, then creates the Manifest that includes the prebuilt Meetings agent configuration and hands that complete description back to the platform.

*Call graph*: 2 external calls (__init__, __init__).


### Insight and knowledge apps
Reporting, discovery, and wiki apps expose their package markers and catalog manifests for workspace-wide use.

### `extensions/app_metrics/ufo_ext_app_metrics/__init__.py`

`other` · `import/package discovery`

Python uses `__init__.py` files to recognize a folder as an importable package. In this case, the file is empty, which means it does not define any functions, classes, settings, or startup behavior. Its value is structural: it lets code elsewhere refer to this extension package using normal Python import paths, such as importing modules that live inside `ufo_ext_app_metrics`.

A simple analogy is a labeled folder in a filing cabinet. The label does not contain the documents, but without it, people and tools may not know how to find or refer to what is inside. If this file were removed, imports may still work in some modern Python setups, but keeping it makes the package boundary explicit and compatible with tooling that expects traditional Python packages.


### `extensions/app_metrics/ufo_ext_app_metrics/manifest.py`

`config` · `startup and app provisioning`

This file is the app’s setup card and job description. Without it, the platform would not know that the Metrics app exists, what agent to create, what it is allowed to do, which integrations to offer during setup, or which skill powers its home page.

The file gathers the app’s identity, its setup options, its scheduled report, and the agent’s long instructions in one place. The central idea is that one Metrics agent should answer the broad question “how is the team doing?” rather than splitting that answer across many separate apps. It can report six groups of measures, but only for sources the workspace has connected.

Some products are shown directly on the setup screen, such as Stripe, QuickBooks, and Zendesk. Others need workspace-level secrets, such as Datadog and PostHog keys. A longer list of products is mentioned only in the agent’s prompt, so the agent can ask for them when needed without crowding the setup screen.

The file also defines one scheduled task, still named “delivery-report” for compatibility with existing workspaces. That name matters because scheduled tasks are identified by name; changing it could accidentally create a second report instead of updating the existing one. Finally, the `manifest` function packages all of this into the format the host platform expects.

#### Function details

##### `manifest`  (lines 184–190)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the official description of the Metrics app for the host platform. It is what the platform reads to learn the app’s name, version, agent, and home-page skill.

**Data flow**: It takes no input. It uses the constants already defined in the file, builds a skill entry pointing at the app’s home skill folder, and returns a `Manifest` object containing the app name, version, agent setup, and skill list. It does not change outside state; it simply packages the app definition for the platform to consume.

**Call relations**: When the extension is loaded, the host system calls this function to discover what this app provides. Inside, it creates a `SkillSpec` for the home-page skill, then creates and returns the `Manifest` that wraps the full Metrics app configuration.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_radar/ufo_ext_app_radar/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label lets Python find and open it by name. Nothing is run here, no settings are created, and no functions or classes are defined. Its value is structural: without it, some Python versions or tooling may not recognize `ufo_ext_app_radar` as a normal package, which could make imports fail or make the extension harder for the larger system to discover.


### `extensions/app_radar/ufo_ext_app_radar/manifest.py`

`config` · `startup / extension load`

The Radar app is meant to be a workspace homepage for scheduled runs: jobs that ran automatically and produced findings. This file is its registration card. Without it, the wider system would not know that the Radar app exists, what agent name to give it, what prompt to use, or which bundled skill contains its homepage.

The file sets simple constants such as the app name, version, skill folder, and homepage skill name. It then builds an `AgentProvision`, which is the instruction to create a workspace-visible agent named `radar`. That agent is given a clear prompt: show a feed of scheduled run digests, open each run into a fuller story, and use the `app-radar-home` skill when a member asks to change the page. The prompt also limits the agent: it uses the automatic model choice, medium reasoning, and no internet access.

Finally, the `manifest` function packages all of this into a `Manifest`. A manifest is like a shipping label for an extension: it says what is inside the package and how the host platform should make it available.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Radar app’s extension manifest, which is the object the platform reads to discover this app. Someone would use it when loading the extension so the app’s agent and homepage skill can be registered.

**Data flow**: It starts with the constants defined in the file: the app name, version, prebuilt Radar agent definition, and path to the homepage skill. It puts those into a new `Manifest`, including a `SkillSpec` that points to the homepage skill folder. The result is a complete manifest object that describes what this extension provides.

**Call relations**: When the extension system asks this file what it provides, `manifest` is the handoff point. It creates a `SkillSpec` for the homepage skill and passes that, together with the Radar agent provision, into `Manifest` so the host platform can register both pieces together.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_wiki/ufo_ext_app_wiki/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, a named bundle of code. That matters because other parts of the project can refer to this extension as `ufo_ext_app_wiki` and then import modules from inside it.

There is no startup work, configuration, or feature logic here. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized workspace. Without this file, depending on the Python version and packaging setup, imports for this app wiki extension could fail or behave differently.


### `extensions/app_wiki/ufo_ext_app_wiki/manifest.py`

`config` · `startup / extension discovery`

This file is the “shipping label” for the Wiki app extension. Without it, the wider system would not know that this extension exists, what agent it should create, or where to find the skill that powers the app’s homepage.

The Wiki app is described as a private agent for each workspace. Its job is to turn the workspace’s shared memory into a readable page: team facts, decisions, open work, history, and a people roster. The file also says the agent should not use the internet, should use automatic model selection, and should only be visible privately to the right workspace audience.

Most of the file is declarative setup: names, version number, the folder where skills live, the name of the homepage skill, and the long instruction prompt that tells the agent how to behave. The prompt is important because it explains the intended page shape and tells the agent to load the `app-wiki-home` skill when someone asks it to change the page.

At the end, the `manifest` function packages all of this into a `Manifest` object. A manifest is like a contents card in a box: when the platform opens the extension, this card says “create this agent, with this purpose, and include this skill.”

#### Function details

##### `manifest`  (lines 50–56)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the platform-readable description of the Wiki app. The system uses it to learn the extension’s name, version, private wiki agent, and homepage skill.

**Data flow**: It reads the constants defined earlier in the file: the extension name and version, the already-built wiki agent provision, and the path to the homepage skill. It wraps the skill path in a `SkillSpec`, then places the agent and skill into a `Manifest`. The result is a complete manifest object that the host system can load.

**Call relations**: When the extension is discovered, the host calls `manifest` to ask what this package provides. Inside that call, it creates a `SkillSpec` for the homepage skill and a `Manifest` that ties together the metadata, the private wiki agent, and the skill bundle.

*Call graph*: 2 external calls (__init__, __init__).

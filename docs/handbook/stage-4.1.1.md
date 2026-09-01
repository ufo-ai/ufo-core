# Collaboration and Knowledge Workspace App Packages  `stage-4.1.1`

This stage is shared setup for the built-in workspace apps. It is not the main work loop of the apps themselves. Instead, it provides the labels and registration cards the host system reads when a workspace is being assembled, so these apps can appear in the right place with the right abilities.

Each app folder has two simple parts. The __init__.py file is a package marker: it tells Python, the programming language used here, that the folder can be imported by other code. It does not run the app. The manifest.py file is the important “ID card” for the app. It describes how the host should install and show that app.

The Artifacts manifest registers a searchable shelf for shared files and hosted sites. Chat registers the workspace chat home screen and its agent. Meetings declares its account needs, scheduled work, and home screen skill. Radar registers its workspace overview surface. Wiki declares the wiki agent, its permissions, and homepage skill. Together, these files make the collaboration tools discoverable and installable.

## Files in this stage

### Artifacts App Package
Defines the importable Artifacts extension package and its workspace app registration for shared files and hosted sites.

### `extensions/app_artifacts/ufo_ext_app_artifacts/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder that says, “this folder is a package.” That matters because it lets the rest of the project import modules from `extensions/app_artifacts/ufo_ext_app_artifacts` using normal Python import paths. This particular file is empty, so it does not run setup code, expose shortcuts, or create shared objects when the package is imported. Its value is mostly structural: without it, some Python environments or tooling might not recognize the directory as an importable package, which could make extension code harder or impossible to load reliably. Think of it like a blank cover page for a section in a binder: it does not add content, but it tells readers and tools where a section begins.


### `extensions/app_artifacts/ufo_ext_app_artifacts/manifest.py`

`config` · `extension discovery / startup`

This file is the app’s identity card and setup sheet. When the platform discovers this extension, it needs to know the app’s name, version, what agent to create, and where its editable homepage skill lives. Without this file, the Artifacts app would not be advertised to the system, so the workspace would not get its shared file-and-site shelf.

The file defines a few fixed pieces of information: the extension name and version, the folder where its skills are stored, the name of the homepage skill, and the agent name shown to users. It also writes the agent’s instructions in plain text. Those instructions tell the agent that its homepage should act like an “artifacts shelf”: newest shared files and hosted sites first, with search, filters, paging, previewing, downloading, and links back to the relevant conversation.

The main object, `ARTIFACTS_APP_AGENT`, packages that behavior into an `AgentProvision`, meaning “please create this agent for the workspace.” The `manifest` function then returns a `Manifest`, which is the final bundle the host system reads. That bundle includes the agent and the homepage skill path, like a label on a box saying what is inside and how to unpack it.

#### Function details

##### `manifest`  (lines 40–46)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the object the platform reads to know what this app provides. It includes the Artifacts agent and the skill that powers its homepage.

**Data flow**: It starts with the constants already defined in the file: the app name, version, prepared agent definition, and skill folder path. It wraps the homepage skill path in a `SkillSpec`, then places that skill and the agent into a `Manifest`. The result is a complete description of this extension that the rest of the system can load.

**Call relations**: When the platform asks this extension what it contains, this function creates the answer. In doing so, it calls `SkillSpec.__init__` to describe the homepage skill and `Manifest.__init__` to package the extension name, version, agent, and skill into the final object.

*Call graph*: 2 external calls (__init__, __init__).


### Chat App Package
Defines the importable Chat extension package and its manifest for the workspace chat home surface.

### `extensions/app_chat/ufo_ext_app_chat/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python projects, an `__init__.py` file tells Python, “this folder is a package,” meaning its contents can be imported as a named module. Think of it like a label on a drawer: the drawer may contain useful tools elsewhere, but the label is what lets the rest of the system find that drawer by name.

Because this file contains no code, it does not create classes, functions, settings, or side effects when imported. Its value is structural rather than behavioral. Without it, depending on the Python version and packaging setup, imports such as `ufo_ext_app_chat.some_module` might not work reliably, and packaging tools might not recognize this folder as part of the extension.

So this file matters because it helps the app chat extension fit into the larger Python import system, even though it intentionally does nothing at runtime.


### `extensions/app_chat/ufo_ext_app_chat/manifest.py`

`config` · `startup / extension load`

This is a small registration file for the workspace’s main chat experience. Without it, the larger system would not know that this extension provides a chat app, what to call it, which icon to show, what the chat agent is supposed to do, or where to find the skill that draws and controls the chat home page.

Think of it like the label and setup card inside a boxed appliance. It does not run the whole chat app itself. Instead, it says: this extension is named `app_chat`, this is its version, its main agent is called `chat`, and that agent should behave like the workspace’s conversation screen. The prompt explains the agent’s job in human terms: show the current conversation or starter prompts, support sending messages, stream replies, and load the `app-chat-home` skill when the page needs to change.

The file also sets practical details for the agent, such as using an automatic model choice, allowing internet access, making it visible to the whole workspace, and marking it as the main agent. Finally, its `manifest` function packages all of that into a `Manifest` object the host can read during extension loading.

#### Function details

##### `manifest`  (lines 36–42)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official manifest for the chat extension. The host system uses this manifest to discover the extension’s name, version, main chat agent, and the skill file path for the chat home screen.

**Data flow**: It starts with the constants defined in this file, such as the extension name, version, prebuilt chat agent, and skills folder path. It creates a skill entry pointing to the `app-chat-home` skill, combines that with the chat agent, and returns one complete manifest object for the host to load.

**Call relations**: When the extension is being loaded, the host calls this function to ask, “What do you provide?” The function hands off the gathered agent and skill information by constructing the manifest and the skill specification objects, which the rest of the platform can then use to make the chat app available in the workspace.

*Call graph*: 2 external calls (__init__, __init__).


### Meetings App Package
Defines the importable Meetings extension package and its registration for meeting agents, accounts, schedules, and home screen skills.

### `extensions/app_meetings/ufo_ext_app_meetings/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package: a named bundle of code that can be imported from elsewhere. This file is empty, so it does not run setup code, define shortcuts, or expose any special names. Its job is structural rather than behavioral. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized workspace. Without this file, depending on the Python version and import style, code that tries to import modules from `ufo_ext_app_meetings` might not find them reliably or might treat the directory differently than intended.


### `extensions/app_meetings/ufo_ext_app_meetings/manifest.py`

`config` · `startup / app discovery and installation`

This file tells the platform how to install and run the Meetings app. In plain terms, it describes one workspace assistant that works over a calendar. Its main active job is to prepare meeting briefs before meetings happen. Two other possible jobs, follow-ups and meeting notes, are described in the agent’s instructions but are not turned on by default.

The file sets names and constants first, such as the app name, version, calendar connector, notes connector, task names, and the path to the home-screen skill. It then writes the agent’s purpose and prompt. The prompt is important because it tells the agent not just what to do, but also what not to do: for example, it must not quietly perform follow-up or notes work unless that feature has been explicitly enabled.

The schedule section defines when meeting briefs should run. It includes an hourly cadence and morning cadences, so meetings booked or moved during the day can still be caught. The key rule is that each run briefs only meetings that start before the next scheduled run and have not started yet. That time window prevents the same meeting from being briefed twice.

Finally, the file packages all of this into an agent provision and exposes it through `manifest()`, so the platform can discover the app, create the agent, connect the calendar, schedule the briefs task, and load the home-screen skill.

#### Function details

##### `manifest`  (lines 111–117)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the Meetings app’s manifest, which is the platform-readable description of the app. The platform uses it to know the app’s name and version, which agent to create, and which skill files belong to the app.

**Data flow**: It reads the constants and objects already defined in this file, including the app name, version, prepared agent definition, and home skill path. It wraps that information into a `Manifest` object, adding a `SkillSpec` for the home-screen skill. The result is a complete app description that the rest of the system can consume.

**Call relations**: When the platform is discovering or loading extensions, it calls `manifest` to ask this file what it provides. Inside that answer, the function creates the manifest object and the skill specification, then hands the finished package back to the platform so the Meetings app can be installed and run.

*Call graph*: 2 external calls (__init__, __init__).


### Radar App Package
Defines the importable Radar extension package and its manifest for workspace radar metadata, agent setup, and homepage skill.

### `extensions/app_radar/ufo_ext_app_radar/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it has an `__init__.py` file. That means other parts of the system can refer to modules inside `ufo_ext_app_radar` using normal Python import paths. Think of it like putting a label on a folder so the program knows, “this folder belongs to the codebase and can be opened by name.” Because this file is empty, it does not set up configuration, create objects, or run any startup behavior. Its value is structural: without it, depending on the Python version and packaging setup, imports for this extension could fail or behave differently.


### `extensions/app_radar/ufo_ext_app_radar/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and setup card that ships with the Radar app. Without it, the wider UFO system would not know that this extension exists, what it is called, which workspace agent to create, or where to find the app’s homepage behavior.

The Radar app is meant to show a feed of recent scheduled runs: each run has a digest, summary, key points, timing information, and links into the full story. The file captures that purpose in plain text for the agent, including instructions about keeping the feed, story view, and rebuild control working when a workspace member asks for changes.

Most of the file is configuration. It names the extension, sets its version, points to the folder containing its skill files, and builds an `AgentProvision`, which means “please create this agent for the workspace.” The agent is given a prompt, a user-facing purpose, an icon, visibility rules, and limits such as no internet access.

The `manifest()` function packages all of that into a `Manifest`, which is the standard object the platform reads when discovering extensions. In everyday terms, it hands the platform a small form saying: “Here is the Radar app, here is its agent, and here is the skill that powers its homepage.”

#### Function details

##### `manifest`  (lines 38–44)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the object the host system reads to learn what this app provides. It announces the Radar app’s name, version, workspace agent, and homepage skill.

**Data flow**: It starts with constants already defined in the file: the app name, version, prepared Radar agent, and path to the homepage skill. It wraps the skill path in a `SkillSpec`, then places the agent and skill into a `Manifest`. The result is a complete description of the extension that the platform can load.

**Call relations**: When the extension system asks this module what it provides, this function creates the answer. It calls `SkillSpec.__init__` to describe the homepage skill, then `Manifest.__init__` to bundle that skill together with the Radar agent and app metadata.

*Call graph*: 2 external calls (__init__, __init__).


### Wiki App Package
Defines the importable Wiki extension package and its manifest for installing the workspace wiki agent and homepage skill.

### `extensions/app_wiki/ufo_ext_app_wiki/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a directory with an `__init__.py` file is treated as an importable package, which means code elsewhere can refer to modules inside `ufo_ext_app_wiki` by name. Think of it like putting a label on a folder so the rest of the system knows the folder is part of the program, not just loose files on disk.

Because the file is empty, it does not set up configuration, load data, register commands, or run any behavior. Its value is structural: without it, some Python environments or tools might not recognize this directory as a package, and imports for the wiki extension could fail or behave inconsistently. The actual wiki extension behavior lives in other files inside this package.


### `extensions/app_wiki/ufo_ext_app_wiki/manifest.py`

`config` · `startup / extension load`

This file is a manifest, meaning a compact description of what this extension contributes to the larger system. In everyday terms, it is like the card on a library book: it says the app’s name, version, purpose, and what comes inside.

The extension provides a private workspace agent named “wiki.” Its job is to turn the workspace’s shared memory into a readable homepage: team facts, decisions, history, open work, and a people roster. The long prompt in this file tells the agent how to behave and reminds it to use the `app-wiki-home` skill when a member asks it to change the page.

The file also sets important boundaries. The agent is private, uses automatic model selection, has medium reasoning effort, and is not allowed internet access. That matters because the Wiki app is meant to summarize what the workspace already knows, not fetch outside information.

Finally, the `manifest` function packages the agent and its homepage skill into a `Manifest` object. The host application can then read that object during extension loading and know exactly what to provision.

#### Function details

##### `manifest`  (lines 50–56)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest: the official list of what this Wiki app extension adds to the system. Someone would use it when loading extensions so the system can discover the Wiki agent and its homepage skill.

**Data flow**: It starts with constants already defined in the file, such as the extension name, version, prepared Wiki agent definition, and the path to the homepage skill. It wraps the skill path in a `SkillSpec`, then places that skill and the Wiki agent into a `Manifest`. The result is a complete manifest object that the rest of the system can read.

**Call relations**: During extension loading, the surrounding system calls `manifest` to ask, “What do you provide?” This function answers by creating a `SkillSpec` for the shipped homepage skill and a `Manifest` that includes both that skill and the prebuilt Wiki agent provision.

*Call graph*: 2 external calls (__init__, __init__).

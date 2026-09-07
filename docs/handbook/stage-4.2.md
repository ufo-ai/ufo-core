# Built-in conversational and content app registrations  `stage-4.2`

This stage is behind-the-scenes setup for the built-in apps that members see in the workspace. It is like filling out name tags before an event starts, so the platform knows which apps exist and how to show them. Each app folder has an __init__.py file, which simply marks the folder as importable Python code. These files mostly do not run the app; they make sure the rest of the system can find it.

The real registration work happens in the manifest.py files. The Chat manifest introduces the main conversation app, its agent, its purpose, and its home-screen skill. The Artifacts manifest registers the app used for created content and points to the agent and skill behind its homepage. The Radar manifest defines the feed-style Radar app and its agent. The Wiki manifest registers wiki pages and the skill files that support them. The Notification manifest adds more pieces: a notification tool, notification object type, special agent, scheduled inbox-draining job, and delivery action. Together, these manifests let the host system install and present these apps consistently.

## Files in this stage

### Artifacts app registration
Package marker and manifest that register the Artifacts app and its homepage skill.

### `extensions/app_artifacts/ufo_ext_app_artifacts/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a folder often needs an `__init__.py` file to be treated as an importable package. Think of it like putting a label on a drawer: the label does not contain the tools, but it tells Python that the drawer belongs to the project and can be opened by name. Without this file, some Python setups or tools might not recognize `extensions/app_artifacts/ufo_ext_app_artifacts` as a package, which could make imports fail or make packaging tools skip the folder. Because the file is empty, it does not run setup code, expose shortcuts, or change any state when imported. Its value is structural: it supports the surrounding extension by making the package visible and importable.


### `extensions/app_artifacts/ufo_ext_app_artifacts/manifest.py`

`config` · `startup / extension registration`

The Artifacts app is meant to be a shared workspace shelf: a place where people can browse files and hosted sites the workspace has produced. This file does not build that page itself. Instead, it tells the platform how to install and present the app.

It gives the app a name and version, points to the folder where its skills live, and defines one workspace-visible agent called “artifacts.” An agent here means an AI-backed app presence with instructions, a purpose, an icon, and settings such as whether it can use the internet. The prompt explains the app’s job in human terms: keep a homepage showing sites and files newest first, support search and filters, show files in a viewer when possible, and use the `app-artifacts-home` skill when asked to change the page.

The important idea is that this file is the bridge between the extension’s code and the hosting system. Without it, the platform would not know to provision the Artifacts agent or where to find the homepage skill. It is like a library catalog entry: it does not contain the book, but it tells the library what the book is called, where it sits, and how readers should understand it.

#### Function details

##### `manifest`  (lines 41–47)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the structured description the platform reads to register this app. Someone would use it when the extension system asks, “What does this extension provide?”

**Data flow**: It starts with constants already defined in the file: the app name, version, the prepared Artifacts agent, and the path to the homepage skill. It packages those into a `Manifest`, including a `SkillSpec` that points at the `app-artifacts-home` skill folder. The output is a complete manifest object that tells the platform what to install.

**Call relations**: This function is the file’s handoff point to the rest of the system. When the extension loader asks for the extension’s manifest, this function creates the manifest, using `SkillSpec` to describe the skill location and `Manifest` to bundle the app name, version, agent, and skill together.

*Call graph*: 2 external calls (__init__, __init__).


### Chat app registration
Package marker and manifest that register the Chat app, its agent, purpose, and home-screen skill.

### `extensions/app_chat/ufo_ext_app_chat/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, which means other code can refer to modules inside `ufo_ext_app_chat` using normal Python import paths. Think of it like putting a label on a folder so the system knows it is part of the application, not just a random directory. Nothing runs here, and no settings, classes, or functions are created. Its value is structural: without it, some Python environments or tooling might not recognize this directory as a package, which could break imports for the app chat extension.


### `extensions/app_chat/ufo_ext_app_chat/manifest.py`

`config` · `startup / extension discovery`

This is a registration file. Its job is not to run the chat app itself, but to describe it so the larger workspace can load it correctly. Without this file, the platform would not know that there is an app named `app_chat`, what version it is, which agent should represent it, or which skill should be used to show the chat screen.

The file defines a main chat agent named `chat`. Its prompt explains the agent’s role in human terms: it is responsible for the chat screen, including an open conversation, streamed replies, the message composer, or the starter prompts shown when no conversation is open. The prompt also tells the agent that when the page needs to change, it should load the `app-chat-home` skill and keep the core chat features working.

Think of this file like the label and setup card inside a boxed appliance. It does not do the appliance’s work, but it tells the house where to plug it in, what it is for, and which included attachment to use first. The key exported piece is `manifest()`, which packages these details into a `Manifest` object the extension system can read.

#### Function details

##### `manifest`  (lines 36–42)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the Chat app extension. The platform uses this description to discover the app, create its main chat agent, and load the home-screen skill.

**Data flow**: It starts with the constants defined in this file: the app name, version, prepared chat agent, and the folder path for the home skill. It wraps the home skill path in a `SkillSpec`, then places the app name, version, agent, and skill into a `Manifest`. The result is a complete manifest object that the rest of the system can read.

**Call relations**: When the extension system asks this module what it provides, this function is the answer. Inside, it creates a `SkillSpec` for the chat home skill and a `Manifest` that gathers the extension’s name, version, agent, and skills into one package for the platform to load.

*Call graph*: 2 external calls (__init__, __init__).


### Notification app registration
Package marker and manifest that register member-facing notifications, inbox handling, and delivery behavior.

### `extensions/app_notification/ufo_ext_app_notification/__init__.py`

`other` · `cross-cutting`

This package is for an in-app notification system. Its central idea is simple: during any agent turn, an agent may discover something worth telling a member later. Instead of interrupting immediately, the agent can put a message into a shared inbox. A special agent shipped with this extension then reads that inbox and decides which messages are important enough to interrupt the member for.

This file does not contain executable logic. It is the package’s front door: in Python, an `__init__.py` file tells the runtime that this folder is an importable package. Here it also carries a short explanation of the package’s purpose. Without this file, depending on the Python version and packaging setup, other parts of the system might not be able to import this extension cleanly, and newcomers would lose the brief summary of what the notification app is meant to do.

A useful analogy is a reception desk: many people can drop off notes, but one receptionist decides which notes are urgent enough to walk over and deliver right away.


### `extensions/app_notification/ufo_ext_app_notification/manifest.py`

`config` · `startup and scheduled background runs`

This file is the Notification app’s manifest, meaning it describes what the app is and what pieces the wider system should install. Without it, the system would not know that agents can raise notifications, that notifications can be listed as objects, that there is a dedicated Notification agent, or that a regular background job should gather pending notification rows.

The main idea is simple: other agents can use a `notify` tool to place possible alerts into an inbox. A scheduled drain then groups each member’s waiting notifications and feeds them to the Notification agent. That agent decides what is actually worth interrupting the person about, and if needed uses the `deliver` action to send one clear message.

The file also carefully limits power. The Notification agent is given tools for reading, deleting, explaining, and displaying notification objects, plus tools needed for its homepage skill. It is given the `deliver` action, but not the `notify` tool. That matters because the app should deliver notifications, not create more of them itself. The manifest also asks for member-context reading so the app can find its own provisioned agent and read the conversations it may deliver through.

#### Function details

##### `_drain`  (lines 79–80)

```
async def _drain(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry function for emptying the pending notification inbox. It creates an `InboxDrain`, which is the component that does the real work, and asks it to run.

**Data flow**: It receives an `ExtensionContext`, which is the app’s view of the workspace and system services. It passes that context into `InboxDrain`, then awaits the drain’s run process. It returns nothing directly; the effect is that pending inbox rows may be folded into Notification-agent turns.

**Call relations**: The manifest registers this function as the handler for the notification drain job. When the scheduler fires, the host calls `_drain`; `_drain` hands the work to `InboxDrain.__init__` and then to the drain object’s run flow so the file itself stays as a thin connector.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 83–100)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the full declaration of the Notification app for the host system. It says which tools, object kinds, jobs, agents, and skills belong to this extension.

**Data flow**: It reads constants defined in this file and imported app pieces such as the notify tool, deliver action, notification object kind, and untriaged workspace selector. It creates a `JobSpec` for the per-minute drain, a `SkillSpec` for the homepage skill, and a `Manifest` containing all declared app parts. The output is a `Manifest` object the host can load.

**Call relations**: The host calls `manifest` when loading the extension. Inside that setup story, it asks `untriaged_workspaces()` which workspaces are candidates for the background drain, builds the job with `JobSpec.__init__`, builds the homepage skill with `SkillSpec.__init__`, and finally packages everything with `Manifest.__init__` so the shared app infrastructure can install and run it.

*Call graph*: 4 external calls (__init__, __init__, __init__, untriaged_workspaces).


### Radar app registration
Package marker and manifest that register the Radar app, its agent, and feed homepage skill.

### `extensions/app_radar/ufo_ext_app_radar/__init__.py`

`other` · `import setup`

This is an empty package marker file. In Python, a directory often needs an `__init__.py` file to be treated as an importable package, meaning other parts of the project can refer to modules inside `ufo_ext_app_radar` by name. Think of it like putting a label on a folder so the system knows the folder belongs on the library shelf. Because the file has no code, it does not run setup steps, create objects, or change program behavior directly. Its importance is structural: without it, some Python environments or tools might not recognize this directory as a package, which could break imports for the app radar extension.


### `extensions/app_radar/ufo_ext_app_radar/manifest.py`

`config` · `extension load`

This file is the Radar app's registration card. When the larger system loads extensions, it needs to know basic facts: the app's name, version, what agent it should create, what that agent is allowed to do, and which bundled skill should be available. Without this file, the Radar app would not be advertised to the shared apps infrastructure, and the workspace would not get the Radar agent or its homepage behavior.

The file defines a workspace-visible agent named "radar". Its prompt explains the app's job in human terms: show a feed of recent scheduled runs, let each run open into a fuller story, and use the bundled homepage skill when a workspace member asks to change the page. The agent is set to use automatic model selection, medium reasoning effort, and no internet access. That last point matters because it limits the agent to the workspace context rather than letting it browse the web.

It also points to a skill folder, specifically the `app-radar-home` skill under this extension's `skills` directory. You can think of the manifest as the label on a boxed appliance: it says what the appliance is called, what parts come with it, and how the system should plug it in.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Radar app's manifest, which is the object the host system reads to discover this extension. It packages together the app name, version, agent definition, and homepage skill.

**Data flow**: It starts with the constants already defined in the file, such as the app name, version, prepared Radar agent, and skill folder path. It creates a `SkillSpec` pointing at the homepage skill, then creates a `Manifest` containing that skill and the Radar agent. The result is a single manifest object that describes everything this extension contributes.

**Call relations**: When the extension system asks this file for its manifest, this function is the handoff point. It calls the manifest-building classes supplied by the UFO SDK to wrap the Radar agent and homepage skill into the standard shape the rest of the platform expects.

*Call graph*: 2 external calls (__init__, __init__).


### Wiki app registration
Package marker and manifest that register the Wiki app, its agent, and associated skill files.

### `extensions/app_wiki/ufo_ext_app_wiki/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a folder: the label may not contain instructions, but it lets other parts of the system find and refer to what is inside.

For this extension, the file makes the `ufo_ext_app_wiki` package visible to Python's import system. Without it, code that expects to import modules from `extensions/app_wiki/ufo_ext_app_wiki` could fail or behave differently, depending on the Python version and packaging setup.

Because the file is empty, it does not run setup code, expose shortcuts, or define shared variables. Its value is structural: it helps package discovery and keeps the extension laid out in the conventional Python way.


### `extensions/app_wiki/ufo_ext_app_wiki/manifest.py`

`config` · `config load`

This file tells the platform how to install and present the workspace Wiki app. The Wiki app is a private agent named “wiki” whose job is to turn the workspace’s shared memory into a readable home page: team practices, decisions, open work, history, facts, and a people roster. Without this file, the platform would not know to provision that agent or where to find the skill that builds and updates the Wiki page.

Most of the file is simple setup information. It gives the extension a name and version, points to the folder where its skills live, and defines the home skill called `app-wiki-home`. It also writes the agent’s instruction prompt: the guidance that tells the agent what kind of page to maintain and how to respond when a member asks for changes. The agent is marked private, meaning it is intended for workspace admins and members with the right access, not as a public bot. It also has no internet access, so its answers should come from workspace knowledge rather than the web.

The central object is `WIKI_APP_AGENT`, an `AgentProvision`, which is like a work order saying, “create this agent with this name, icon, purpose, and behavior.” The `manifest()` function then packages that agent together with its skill into a `Manifest`, which the extension system can read during loading.

#### Function details

##### `manifest`  (lines 50–56)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete extension manifest: the name and version of the Wiki app, the private Wiki agent to create, and the skill folder the agent should use. The platform calls it when it is discovering or loading extensions.

**Data flow**: It starts with the constants already defined in the file, such as the app name, version, Wiki agent definition, and path to the home skill. It wraps the skill path in a `SkillSpec`, then places that skill and the Wiki agent into a `Manifest`. The result is a structured description of the extension that the rest of the system can consume.

**Call relations**: When the extension loader asks this file what it provides, `manifest` builds the answer. It calls `SkillSpec.__init__` to describe the shipped home-page skill, then calls `Manifest.__init__` to bundle that skill together with the Wiki agent provision. That returned manifest is what lets the shared apps infrastructure provision the Wiki app correctly.

*Call graph*: 2 external calls (__init__, __init__).

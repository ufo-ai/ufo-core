# Built-in work app registrations  `stage-4.3`

This stage is the sign-up desk for several built-in work apps. It is shared behind-the-scenes support, used when the platform discovers what apps exist, installs them in a workspace, and knows how to start their agents. An agent is the automated worker the app creates to do its job.

Each app folder has an __init__.py file. These files are simple package markers: they tell Python, the programming language used here, that the folder can be imported by other code. They do not run the apps themselves.

The real instructions are in the manifest.py files. The Code manifest registers the Code app, including its GitHub setup, prompt, agent, and shipped skill files. The Issues manifest tells the platform how the Issues app is installed, what its agent may do, and what skills it has. The Meetings manifest declares needed accounts and scheduled meeting work. The Metrics manifest defines report scheduling, possible account or key needs, and its home-page skill. Together, these manifests act like labels and instruction cards for the platform’s built-in apps.

## Files in this stage

### Code app registration
Package marker and manifest that make the built-in Code app importable and installable by the platform.

### `extensions/app_code/ufo_ext_app_code/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language, tools, and import system that the surrounding folder should be treated as a package — a named bundle of related code. Think of it like a label on a drawer: the drawer may contain useful files, but the label is what lets the rest of the system refer to the drawer by name.

Because this file is empty, it does not run setup code, expose shortcuts, or change how the extension works. Its value is structural. Without it, some Python environments or packaging tools might not recognize `extensions/app_code/ufo_ext_app_code` as an importable package, which could make extension code harder or impossible to load in the expected way.

There are no functions or classes here. The important behavior comes simply from the file existing at this path.


### `extensions/app_code/ufo_ext_app_code/manifest.py`

`config` · `extension discovery and app provisioning`

This file is the app’s registration card. Without it, the platform would not know that the Code app exists, what it is called, how to provision its agent, or where to find its review and pull-request follow-up instructions.

The app is built around one workspace agent named “code”. Its job is to review pull requests as they change and point out what might break or what is missing. The file loads the main review prompt from `prompts/agent_code.md`, then builds an `AgentProvision`, which is the platform’s recipe for creating or adopting that agent in a workspace. The recipe includes the model to use, the agent’s purpose, its icon, its visibility, and whether it can use the internet or a sandbox.

It also declares that the agent needs a GitHub connector and should be woken by a source trigger, meaning a pull request change can wake the conversation. A second behavior, “babysitting” pull requests until they merge or close, is shipped as a skill file instead of being added to the prompt. That matters because prompts are copied into an agent once and may be edited by members later, while skills remain files provided by the extension and can reach existing workspaces on deploy. In short, this file connects the human-facing Code app idea to the platform machinery that installs it.

#### Function details

##### `manifest`  (lines 99–108)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the platform manifest for this extension. The manifest is the compact description the platform reads to learn the extension’s name, version, agent, and shipped skills.

**Data flow**: It starts with constants already defined in the file: the extension name and version, the prepared Code agent recipe, and the paths to the skill folders. It packages those into a `Manifest` object, creating `SkillSpec` entries for the home skill and babysitting skill. The result is a single manifest object that the platform can read during extension loading.

**Call relations**: When the platform asks this extension what it provides, this function is the handoff point. It calls `SkillSpec.__init__` to describe each skill file, then calls `Manifest.__init__` to bundle the extension metadata, the Code agent provision, and the skills into one object for the platform to consume.

*Call graph*: 2 external calls (__init__, __init__).


### Issues app registration
Package marker and manifest that register the Issues app, its agent permissions, setup, and bundled skills.

### `extensions/app_issues/ufo_ext_app_issues/__init__.py`

`other` · `import/package discovery`

This is an empty Python package file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label mainly makes sure Python knows where the drawer is and can open it by name. Without this file, depending on the Python version and packaging setup, imports from `extensions/app_issues/ufo_ext_app_issues` might fail or behave differently. Because the file is empty, it does not run setup code, expose shortcuts, or change how the extension works at runtime. Its value is structural: it makes the surrounding directory part of the project’s Python module layout.


### `extensions/app_issues/ufo_ext_app_issues/manifest.py`

`config` · `startup/config load`

This file is the app’s registration form. It tells the larger system, in one place, how to create an agent that works with a workspace’s issue tracker, such as GitHub Issues. The agent has two jobs. The first job is triage: regularly look at open issues that do not already have a comment from this app, decide what each issue is asking for, suggest an owner, and post a plan as a comment. The second job is implementation: only after a member approves it, find issues marked with the approval label `ufo:implement`, write the needed change, and open a pull request.

A key idea here is that the app avoids waking itself up forever. On GitHub, posting a comment changes the issue, and a changed issue can wake the app again. So the app uses its own comment as a visible “already triaged” marker. That is like putting a sticky note on a paper form so nobody processes the same form twice.

The file also describes setup: the member must connect the right GitHub account, and the built-in triage task can run on several suggested schedules, such as hourly or weekday mornings. Finally, it points to the app’s home-page skill, which is extra instruction material stored under the skills folder.

#### Function details

##### `manifest`  (lines 128–134)

```
def manifest() -> Manifest
```

**Purpose**: This function gives the UFO platform the finished description of the Issues app. It packages the app name, version, agent definition, and home-page skill into a single manifest object that the platform can read.

**Data flow**: It reads the constants already defined in this file, including the app name, version, prepared agent setup, and path to the home skill. It creates a skill description for the home skill, then creates and returns a Manifest containing that skill and the Issues agent. The function does not modify outside state; it simply builds the app description and hands it back.

**Call relations**: When the extension system needs to discover or load this app, it calls `manifest`. Inside, the function hands the home skill path to `SkillSpec.__init__(ext)` so the platform knows where that skill lives, then hands the app metadata, agent, and skill list to `Manifest.__init__(ext)` so the whole extension can be registered.

*Call graph*: 2 external calls (__init__, __init__).


### Meetings app registration
Package marker and manifest that expose the Meetings app, required accounts, agent, and scheduled work.

### `extensions/app_meetings/ufo_ext_app_meetings/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package — like putting a label on a box so other parts of the program know they can open it and find related code inside. Here, that package is for the `app_meetings` extension. Even though the file has no functions, classes, or settings, it still matters because imports elsewhere may rely on this directory being recognized as a package. Without it, depending on the Python version and how the project loads extensions, the meetings extension might not be found or imported in the expected way. There is no hidden setup work here: importing this package does not change state, register hooks, or start any behavior. It simply provides the package boundary.


### `extensions/app_meetings/ufo_ext_app_meetings/manifest.py`

`config` · `startup and app installation`

This file is like the label and instruction card inside a boxed app. It does not brief meetings itself. Instead, it tells the UFO platform how to set up an agent that will do that work.

The app is built around one workspace agent named “meetings.” Its main active feature is meeting briefs: before meetings happen, the agent looks at the calendar and writes a short preparation note about attendees, earlier decisions, and open items. The file also describes two future or optional features: follow-ups and meeting notes. Those are deliberately not active at first. The prompt tells the agent not to quietly do that work until a member asks for it and the right account is connected.

The setup section asks for only a calendar connection at install time, because briefing meetings needs the calendar. A Google Docs connection is mentioned only for the optional note-taking and follow-up features, so users are not asked to connect more than they need.

The schedule defines when the briefing task can run, including a regular cadence and morning variants. The important idea is that each run briefs only meetings in its own time window, so the same meeting should not be briefed twice. Finally, the manifest exposes a home skill, which is the app’s workspace screen.

#### Function details

##### `manifest`  (lines 111–117)

```
def manifest() -> Manifest
```

**Purpose**: This function hands the platform the complete declaration of the Meetings app. It packages the app name, version, agent setup, and home skill path into a Manifest object the platform can read.

**Data flow**: It takes no inputs from the caller. It reads the constants defined earlier in the file, such as the app name, version, prepared agent definition, and skills folder path. It returns a Manifest object that says: install this app version, create this meetings agent, and make this home skill available.

**Call relations**: When the extension is discovered, the platform calls this function to learn what the extension provides. Inside, it creates a SkillSpec for the home screen skill and a Manifest that includes that skill plus the prebuilt Meetings agent definition.

*Call graph*: 2 external calls (__init__, __init__).


### Metrics app registration
Package marker and manifest that define the Metrics app, its agent, credentials, scheduled reports, and home-page skill.

### `extensions/app_metrics/ufo_ext_app_metrics/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this extension using normal import paths, such as importing modules from `ufo_ext_app_metrics`.

There is no startup logic, configuration, or feature behavior here. Its value is structural: it tells Python and project tooling that the `ufo_ext_app_metrics` directory belongs together as one package. A simple analogy is a label on a folder in a filing cabinet. The label does not contain the documents, but it makes the folder recognizable and usable by the filing system.

Without this file, some environments or tools might not recognize the directory as a package, especially in older Python setups or stricter packaging workflows. The actual app metrics behavior lives in other files inside this package, not here.


### `extensions/app_metrics/ufo_ext_app_metrics/manifest.py`

`config` · `app discovery and provisioning`

This file is the app’s manifest, which is like the label and instruction card on a boxed appliance. It does not calculate metrics itself. Instead, it describes how the Metrics app should be provisioned so the rest of the system can create it correctly.

The app’s job is to give a team one regular report across six areas: delivery, revenue, runway, reliability, product, and support. The file explains which outside services can provide each area’s numbers, such as Stripe for revenue, QuickBooks for runway, Datadog for reliability, and Zendesk for support. Some services are normal connected accounts, where a user grants access. Others need workspace-wide secret keys, so the setup screen asks an admin for credentials instead.

A key idea here is that there is one agent and one scheduled task for all metric sets. That matters because a team should not have six separate apps or six separate reports just to answer one question: “How are we doing?” The file also keeps the scheduled task’s old name, `delivery-report`, so existing workspaces do not accidentally get a duplicate report.

At the end, the file packages all of this into a `Manifest`: the app name and version, the agent to create, its prompt and setup options, its schedule choices, and the home-page skill path.

#### Function details

##### `manifest`  (lines 184–190)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the manifest object that tells the platform how to install and present the Metrics app. The platform uses this as the app’s official description.

**Data flow**: It reads the constants defined earlier in the file, including the app name, version, prepared agent setup, and skill folder path. It creates a `SkillSpec` for the home-page skill, then creates a `Manifest` containing the app metadata, the single Metrics agent, and that skill. The result is a complete manifest object returned to the caller.

**Call relations**: When the extension system asks this module what it provides, this function is the handoff point. It calls `SkillSpec.__init__` to describe the home skill, then calls `Manifest.__init__` to wrap the skill and agent definition into the one object the platform expects.

*Call graph*: 2 external calls (__init__, __init__).

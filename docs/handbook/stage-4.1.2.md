# Engineering and Operations Workspace App Packages  `stage-4.1.2`

This stage is behind-the-scenes setup for three built-in workspace apps: Code, Issues, and Metrics. These apps are not the main work loop by themselves. Instead, they tell the UFO host what tools are available when a workspace is installed or prepared.

Each app package has two simple parts. The __init__.py file is just a marker that tells Python, the programming language used here, “this folder is a package you can import.” It is like putting a label on a drawer so the rest of the system can find it. It does not run app logic.

The manifest.py file is the important registration card. The Code manifest declares the app for repository work, including its agent, skills, and setup needs. The Issues manifest declares an agent for issue tracking, its permissions, schedule, and setup. The Metrics manifest declares an operational metrics agent, its purpose, schedule, setup, and skill files. Together, these manifests let the host discover and install the workspace apps in a consistent way.

## Files in this stage

### Code App Package
Package marker and manifest for the repository-oriented Code workspace app extension.

### `extensions/app_code/ufo_ext_app_code/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that refer to `ufo_ext_app_code` and the Python runtime will recognize the folder as a proper module namespace. Think of it like a label on a filing cabinet drawer: the label does not contain the documents, but it tells the system that the drawer exists and can be opened. Because this file is empty, it does not run setup code, expose convenience imports, or change package behavior. Its main value is structural: without it, some Python tools or older import setups might not reliably recognize this directory as a package.


### `extensions/app_code/ufo_ext_app_code/manifest.py`

`config` · `startup / extension discovery`

This file tells the platform how to install and present the Code app. The app’s job is to review GitHub pull requests as they change, then optionally “babysit” them afterward until they merge or close. Babysitting here means checking back over time, especially because some important pull request events, like failing checks, may not create a fresh source event that would wake the agent automatically.

The file defines the app name and version, loads the agent’s main instructions from a prompt file, and points to two shipped skill folders: a home skill and a babysitting skill. A skill is a packaged instruction file that can be updated with the extension, unlike a prompt that is copied into an agent record once and may then be edited by a workspace member. That distinction matters because existing workspaces would not automatically receive new prompt text, but they can receive updated skills on deploy.

It also defines the workspace agent itself. The agent is named “code”, has a pull-request-review purpose, uses a high-reasoning automatic model, has no internet access, and asks for GitHub-related setup. The setup instructions explain that the workspace must connect GitHub, register and share a pull-request source, add a source trigger, and install the UFO GitHub App so the reviewer can fetch commit data.

#### Function details

##### `manifest`  (lines 102–111)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete registration object for the Code app. The platform can call it to learn what this extension adds: one workspace agent and two packaged skills.

**Data flow**: It starts with the constants already built in the file: the app name, version, prepared agent definition, and paths to the skill folders. It wraps the two skill paths into skill descriptions, combines them with the agent definition, and returns one manifest object that represents the whole extension.

**Call relations**: When the extension system asks this file what it provides, this function builds the answer. It creates the manifest container and the skill entries that point to the shipped skill files, then hands that finished manifest back to the platform so the app can be installed or discovered.

*Call graph*: 2 external calls (__init__, __init__).


### Issues App Package
Package marker and manifest for the issue-tracking workspace app extension.

### `extensions/app_issues/ufo_ext_app_issues/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, an `__init__.py` file is like a nameplate on a folder: it tells Python, and people reading the project, that the folder is meant to be treated as one importable unit. Here, that unit is the `ufo_ext_app_issues` extension package.

Because the file contains no code, importing this package does not run setup steps, create objects, or expose shortcut names. That is important in its own quiet way: it keeps package import predictable and side-effect free. Other files inside this folder can still be imported directly, but this file does not alter how they behave.

Without this file, some Python tools or older Python environments might not recognize the folder as a normal package. That could make imports, packaging, testing, or extension discovery less reliable. So the file exists mainly to support the project’s structure rather than to perform application logic.


### `extensions/app_issues/ufo_ext_app_issues/manifest.py`

`config` · `startup / app registration`

This file is mostly a manifest, which means a structured description that tells the platform how to install and run this app. The app is meant to work over a GitHub issue tracker. Its first job is triage: look at new open issues, explain what each one is asking for, suggest an owner, and post a plan as a comment. Its second job is optional implementation: if a member approves an issue by adding the `ufo:implement` label, the app can write the change and open a pull request.

A key idea in this file is that both jobs belong to one workspace agent, not two separate apps. That matters because both jobs read the same issues and need the same GitHub connection. Splitting them would make members connect the same account twice and would make the system revisit the same backlog in awkward ways.

The file also explains why triage runs on a schedule instead of simply reacting to issue changes. Posting a comment changes the issue, so a reaction-based app could wake itself up again and again because of its own comment. The scheduled sweep avoids that loop by asking a stable question: “Does this issue already have a comment from me?” If yes, it has already been triaged.

At the end, the `manifest` function packages the app name, version, agent setup, GitHub requirements, schedule, and homepage skill into the object the UFO platform expects.

#### Function details

##### `manifest`  (lines 129–135)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the Issues app so the UFO platform can discover it, install it, and provision its agent. Someone would use this when loading extensions or registering available apps.

**Data flow**: It starts from the constants defined in this file: the app name and version, the prebuilt agent description, and the path to the homepage skill. It wraps the homepage skill path in a `SkillSpec`, then places that skill and the Issues agent into a `Manifest`. The result is a single manifest object that says, “Here is this app, here is its agent, and here are its skills.”

**Call relations**: When the platform asks this extension what it provides, this function is the answer. It creates the manifest object and the skill specification object, handing them back to the caller so the rest of the system can set up the Issues agent with its GitHub connection, scheduled triage task, and homepage skill.

*Call graph*: 2 external calls (__init__, __init__).


### Metrics App Package
Package marker and manifest for the operational metrics workspace app extension.

### `extensions/app_metrics/ufo_ext_app_metrics/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker. In Python projects, a folder often needs an `__init__.py` file so Python treats that folder as an importable package. Here, that package is `ufo_ext_app_metrics`, which appears to hold an application metrics extension elsewhere in the same directory tree.

Because the file is empty, it does not define functions, classes, settings, or startup behavior. Its value is structural: it tells Python and developer tools, “this directory is a named module area.” Without it, some import styles or packaging tools might fail to find the extension correctly, especially in environments that still expect explicit package markers.

A useful analogy is a label on a filing cabinet drawer. The label does not contain the documents, but it tells people and systems that the drawer is a recognized place to look. This file plays that role for Python imports.


### `extensions/app_metrics/ufo_ext_app_metrics/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and instruction card inside a boxed app. It does not calculate metrics itself. Instead, it describes a workspace agent named “metrics” that will report how a team is doing.

The main idea is that one app should collect related team health reports in one place. Engineering delivery is ready from the start. Revenue and support are described as future report sets that the agent should only turn on after a workspace member asks for them. That matters because the app should not silently do work or connect accounts for reports nobody requested.

The file sets the app name, version, skill folder, task names, and the long prompt that shapes the agent’s behavior. That prompt tells the agent what to report, what not to do, and how to explain its numbers. It also defines a setup schedule for the engineering delivery report, with suggested cadences such as Monday morning or weekday mornings.

The agent setup says the app needs GitHub access, because the first report is about engineering delivery from repositories. It also points to a home-screen skill, which is the code or instructions used when someone asks to change the Metrics app homepage. Without this file, the host system would not know that this extension exists, what agent to provision, which account connection to request, or which scheduled report to create.

#### Function details

##### `manifest`  (lines 107–113)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the manifest object that the host system reads to discover the Metrics app. It packages the app name, version, agent definition, and homepage skill into one declared extension description.

**Data flow**: It takes no direct input. It reads the constants defined earlier in the file, including the app name, version, prepared agent setup, and path to the home skill. It turns those into a Manifest object and returns it, so the rest of the system can install or register the extension.

**Call relations**: When the extension system asks this file what it provides, this function is the answer. It creates a SkillSpec for the homepage skill path, then places that skill and the prebuilt Metrics agent inside the Manifest that the host can consume.

*Call graph*: 2 external calls (__init__, __init__).

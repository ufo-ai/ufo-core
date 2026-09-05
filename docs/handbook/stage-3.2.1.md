# Built-in app extension manifests  `stage-3.2.1`

This stage is the set of “registration cards” for the built-in workspace apps. It is behind-the-scenes startup support: when the host extension loader scans the system, these manifest files tell it which first-party apps exist, how to install them, and what to show in the workspace.

Each manifest describes one app’s moving parts. Artifacts, Chat, Code, Radar, and Wiki register the agent that users can interact with and the home skill that powers the app’s main page. Issues adds its needed setup information and extra skill files, so the platform knows what must be prepared before use. Meetings and Metrics also declare required accounts or keys, plus scheduled jobs such as meeting-related work or reports. Notification is broader: it exposes tools, a notification object type, a background job, an agent, and a homepage skill.

Together, these files act like labels on drawers in a workshop. The apps may contain the real tools elsewhere, but these manifests tell the platform where each drawer is and what it contains.

## Files in this stage

### Workspace agent manifests
Registration files for core workspace apps that primarily expose user-facing agents and home skills.

### `extensions/app_artifacts/ufo_ext_app_artifacts/manifest.py`

`config` · `startup / extension load`

The Artifacts app is meant to be a shared shelf for a workspace: it shows hosted sites and shared files, newest first, with search, filters, paging, and a viewer for opened files. This file is the app’s registration card. Without it, the larger system would not know that this extension provides an agent named “artifacts,” what that agent is supposed to do, or where to find the homepage skill that keeps the shelf working.

Most of the file is simple setup information. It names the extension, sets its version, points to the folder that contains skills, and writes the instruction prompt for the agent. That prompt is important because it tells the agent how to behave when a workspace member asks it to change the Artifacts page: load the `app-artifacts-home` skill and preserve the page’s controls and viewer.

The file then packages that information into an `AgentProvision`, which is like an installation request for one workspace agent. Finally, the `manifest()` function returns a `Manifest`, which is the complete bundle the host reads when loading this extension.

#### Function details

##### `manifest`  (lines 40–46)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension’s manifest, which is the formal description of what this extension provides. In this case, it says the extension provides one Artifacts agent and one homepage skill.

**Data flow**: It reads the constants defined in this file, including the extension name, version, prepared agent definition, and skill folder path. It uses those values to create a `SkillSpec` for the homepage skill and a `Manifest` containing that skill and the Artifacts agent. The result is a manifest object that the host system can use to load the extension.

**Call relations**: When the extension system needs to discover what this package offers, this function is the place it asks. The function hands off the final packaging work to the SDK objects `SkillSpec` and `Manifest`, which turn the local settings into the structured form the host expects.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_chat/ufo_ext_app_chat/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and setup card for the workspace chat app. It does not run the chat conversation itself. Instead, it declares what the extension is called, what version it is, where its skill files live, and what kind of agent should represent it.

The main item it defines is the chat app agent. In plain terms, an agent is the AI-facing worker for this part of the workspace. This one is named “chat,” marked as the main agent, and given instructions saying that its home page is the chat screen: either an existing conversation with messages and live replies, or a start screen with starter prompts. The prompt also tells it to use the `app-chat-home` skill when it needs to change or load the page.

The file also sets practical details for that agent, such as its icon, purpose, model choice, reasoning level, internet permission, and workspace visibility. Finally, the `manifest()` function packages all of this into a `Manifest`, which is the object the larger system reads to discover and install this extension.

#### Function details

##### `manifest`  (lines 36–42)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest: the small package of information the platform needs in order to register the chat app. Someone would use this when the system is discovering available extensions and needs to know what this extension provides.

**Data flow**: It starts with the constants defined in this file: the extension name, version, prepared chat agent, and the path to the home skill. It wraps the home skill path in a `SkillSpec`, then places that skill and the chat agent into a `Manifest`. The result is a complete description of the chat app extension, ready for the wider system to load.

**Call relations**: During extension loading, the platform calls `manifest` to ask this file what it offers. The function creates a skill description through `SkillSpec` and then hands both the skill and the prebuilt chat agent into `Manifest`, which becomes the object passed back to the loader.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_code/ufo_ext_app_code/manifest.py`

`config` · `startup/config load`

This file tells the larger system how to set up the Code app. The app is a workspace agent that reviews GitHub pull requests and can also “babysit” them afterward, meaning it periodically checks whether reviewed pull requests are still blocked, merged, or closed.

Most of the file is configuration written as Python objects. It names the extension, gives it a version, loads the main agent prompt from a Markdown file, and defines the agent’s purpose in plain terms: review each pull request as it changes and point out what might break or what is missing.

It also explains an important design choice. The ongoing babysitting procedure is shipped as a “skill” rather than baked into the agent prompt. A prompt is copied into an agent record once and can later be edited by a workspace member, so changing the prompt would not reliably reach existing workspaces. A skill is more like a shared instruction document shipped with the app; when the extension updates, the skill can reach places where the app is already installed.

The file also wires in GitHub setup. It says the agent needs the GitHub connector, and it gives setup instructions so the workspace connects the right GitHub account and creates a pull-request source trigger. Without this manifest, the system would not know that this app exists, what agent to create, what prompt to use, or what skills to attach.

#### Function details

##### `manifest`  (lines 99–108)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete manifest object for the Code app. The system calls it to learn the app’s name, version, agent definition, and included skill files.

**Data flow**: It reads the constants already defined in this file, such as the app name, version, agent configuration, and skill folder paths. It packages those values into a Manifest object, with two SkillSpec entries pointing to the home skill and babysitting skill. The result is a single structured description that the host system can use to register the extension.

**Call relations**: When the extension is being loaded, the host asks this function for the app’s manifest. The function creates the Manifest and SkillSpec objects, then hands that finished registration record back to the host so the agent and its skills can be made available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_issues/ufo_ext_app_issues/manifest.py`

`config` · `startup / extension discovery`

This file does not run issue triage itself. Instead, it tells the wider system how to install and start an “issues” agent for a workspace. The agent is designed to work over one issue tracker, such as GitHub, and has two related jobs: triage new issues, and optionally implement approved ones.

The file defines the app’s name, version, skill folder, agent name, task names, and the label that means an issue is approved for implementation. It also writes the agent’s long instruction prompt. That prompt is important because it sets the rules the agent must follow: triage only issues that do not already have a comment from this app, avoid repeating its own work, and only implement issues carrying the approval label. In plain terms, the comment is used like a receipt: if the receipt is already on the issue, the app knows it has already visited.

The setup section says the agent needs a GitHub connection and a scheduled task. The default schedule is for triage, with possible cadences such as hourly or daily. Finally, the manifest function packages all of this into a Manifest object so the platform can discover the app, create the agent, and load its homepage skill.

#### Function details

##### `manifest`  (lines 128–134)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of this extension for the UFO platform. The platform uses this to learn the app’s name, version, agent definition, and bundled skill.

**Data flow**: It takes no input from the caller. It reads the constants already defined in this file, such as the app name, version, prepared agent description, and path to the homepage skill. It then wraps those pieces into a Manifest object and returns it, leaving the rest of the system with a complete registration record for the Issues app.

**Call relations**: When the platform is discovering extensions, it calls this function to ask, “What do you provide?” The function creates a SkillSpec for the homepage skill and places it beside the prebuilt agent provision inside the returned Manifest, so the platform can later create the agent and make the skill available.

*Call graph*: 2 external calls (__init__, __init__).


### Account-backed scheduled apps
Manifests for apps that combine workspace agents with setup requirements and scheduled background work.

### `extensions/app_meetings/ufo_ext_app_meetings/manifest.py`

`config` · `startup`

This file is the app’s registration form. It tells the UFO platform that there is a workspace app called “meetings” whose first job is to brief people before meetings using a connected Google Calendar. It also describes two future features, follow-ups and notes, but makes clear that they should not run until a member explicitly asks for them and connects the needed Google Docs account.

The main object built here is an agent provision: a recipe for creating one workspace-visible agent. That recipe includes the agent’s purpose, its long instruction prompt, its icon, and its setup needs. At setup time, the app asks only for a calendar connection, because meeting briefs are the only feature turned on by default. The file also defines a scheduled task for briefs, with several possible cadences, including hourly and morning runs. The prompt explains an important safety rule: each run should brief only meetings in its own time window, so the same meeting is not briefed twice.

At the end, the `manifest` function packages all of this into a `Manifest`, including the app’s home-screen skill. In everyday terms, this file is like the label, instruction sheet, and installation checklist that lets the platform put the Meetings app on the shelf and know how to activate it.

#### Function details

##### `manifest`  (lines 111–117)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the complete description of the Meetings app for the platform to read. It names the app, gives its version, lists the agent to create, and points to the skill used for the app’s home screen.

**Data flow**: It takes no input from the caller. It reads the constants defined earlier in the file, such as the app name, version, agent recipe, and skill folder path, then builds a `Manifest` object. The result is a packaged app definition that the platform can use during installation or startup.

**Call relations**: When the platform asks this extension what it provides, this function is the answer. Inside, it creates a `SkillSpec` for the home-screen skill and passes that, along with the prebuilt agent provision, into `Manifest` so the wider system can register the app cleanly.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_metrics/ufo_ext_app_metrics/manifest.py`

`config` · `extension discovery and app setup`

This file is the app’s declaration card. It does not calculate metrics itself. Instead, it describes the shape of a workspace agent whose job is to report how a team is doing across six areas: delivery, revenue, runway, reliability, product, and support.

The file makes an important product choice: there is one Metrics app and one scheduled report, not six separate apps or six separate schedules. That keeps setup simple for a workspace member. They connect the accounts they have, choose a reporting cadence, and the app reports the sets it can actually read. If an account or key is missing, the app should say what is missing rather than invent a number.

The setup information is split by how each outside service is reached. Some products, such as Stripe or Zendesk, are connected through normal account grants. Others, such as Datadog and PostHog, need workspace-level secret keys, so the setup asks an admin for those key slots. Other products are mentioned in the agent prompt but are not shown as setup rows, usually because they are alternatives or are obtained only when a member asks.

Finally, the file packages all of this into a manifest. A manifest is like a shipping label for the extension: it tells the platform what to install, what agent to create, and where to find the app’s home-page skill.

#### Function details

##### `manifest`  (lines 184–190)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the manifest object that the platform reads to discover this extension. Someone would use it when installing or loading the Metrics app, so the system knows its name, version, agent, and skill files.

**Data flow**: It reads the constants already defined in this file, such as the app name, version, prepared agent definition, and skills folder. It creates a SkillSpec pointing at the home skill, then creates a Manifest containing the app identity, the Metrics agent, and that skill. The result is a complete description of what this extension contributes to the workspace.

**Call relations**: When the extension system asks this module what it provides, this function is the handoff point. It calls the Manifest constructor to wrap the whole app definition, and it calls the SkillSpec constructor to name the home-page skill that should be included with the app.

*Call graph*: 2 external calls (__init__, __init__).


### Service and knowledge app manifests
Registration files for notification infrastructure and built-in information browsing apps.

### `extensions/app_notification/ufo_ext_app_notification/manifest.py`

`config` · `extension load and scheduled background drain`

The Notification app works like a careful gatekeeper for interruptions. Other agents can raise possible notifications with a `notify` tool, but this app’s own notification agent reviews batches and decides what is important enough to send to a member. This file declares all of that to the shared extension system.

It names the app and version, points to the folder that contains the app’s homepage skill, and defines a scheduled drain job. That job runs once a minute and gathers pending notification rows for each member, so the notification agent can review them as a batch instead of one noisy message at a time.

The file also creates the built-in `notification` agent. Its prompt is strict: deliver only things that change what a member should do today, such as urgent customer, revenue, or production issues, and ignore routine status updates. The agent is given a limited tool allowlist. That matters because permissions are part of the safety design: the app can deliver messages to members, but it cannot itself create notifications with `notify`, and ordinary agents do not get the special `deliver` action.

Finally, `manifest()` packages these declarations into a `Manifest`, which is what the host reads when loading the extension.

#### Function details

##### `_drain`  (lines 79–80)

```
async def _drain(ctx: ExtensionContext) -> None
```

**Purpose**: This is the scheduled job entry for draining notification inboxes. It starts the background process that turns pending notification rows into reviewable batches for the Notification agent.

**Data flow**: It receives an extension context, which is the app’s access point to workspace data and platform services. It builds an `InboxDrain` using that context, runs it, and returns nothing after the drain work is complete. The visible change is in the system state: pending notifications may be collected and prepared for the notification agent to consider.

**Call relations**: The job registration in `manifest` points to this function as the job handler. When the platform’s scheduler reaches the configured time, it calls `_drain`; `_drain` then hands the real work to `InboxDrain`, which performs the inbox-draining behavior.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 83–100)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the full declaration of the Notification extension. The host system uses it to learn what the app is called, what it can do, what background work it runs, and which agent and skill it installs.

**Data flow**: It reads the constants defined in this file and imports pieces from nearby modules, such as the notify tool, deliver action, notification object kind, and workspace candidate finder. It combines them into a `Manifest` object containing tools, objects, permissions, a scheduled job, the notification agent, and the homepage skill. The result is a complete installation recipe for the extension.

**Call relations**: The extension loader calls `manifest` when it needs to register the app. Inside that setup story, `manifest` creates the scheduled job specification, asks `untriaged_workspaces` which workspaces should be candidates for draining, creates the skill specification, and returns the final manifest for the platform to install and run.

*Call graph*: 4 external calls (__init__, __init__, __init__, untriaged_workspaces).


### `extensions/app_radar/ufo_ext_app_radar/manifest.py`

`config` · `startup / extension discovery`

The Radar app is meant to give a workspace a homepage that summarizes recent scheduled runs. In plain terms, it is a dashboard: each scheduled run appears as an item in a feed, and a user can open it to see the fuller story, including files, reports, and conversation.

This file is the app’s shipping label. Without it, the broader system would not know that the Radar app exists, what agent to create for it, what instructions that agent should follow, or where to find the skill that powers its homepage.

The file sets a name and version for the extension, points to the folder that contains its skills, and defines one workspace-visible agent named “radar.” That agent is given a prompt, which is the instruction text telling it what the Radar homepage should do and how to respond when someone asks to change the page. It also declares practical settings, such as using an automatic model choice, medium reasoning effort, no internet access, and workspace-level visibility.

The `manifest` function then packages these pieces into a `Manifest` object. A manifest is like a registration form: it lists the app’s identity, the agent it provides, and the homepage skill that should be made available.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the object the host system reads to discover what this app provides. It includes the app name, version, Radar agent, and the homepage skill path.

**Data flow**: It starts with the constants defined in this file: the app name, version, prebuilt Radar agent description, and the path to the homepage skill. It wraps the skill path in a `SkillSpec`, then puts the agent and skill into a `Manifest`. The result is a complete description of the extension that the surrounding app infrastructure can load.

**Call relations**: When the extension system wants to discover this app, it calls `manifest`. Inside, this function creates a `SkillSpec` for the homepage skill and then creates the final `Manifest`, handing that registration package back to the caller so the Radar app can be provisioned.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_wiki/ufo_ext_app_wiki/manifest.py`

`config` · `startup / extension discovery`

This file is the packaging label and instruction card for the Wiki app extension. The Wiki app is meant to turn a workspace’s shared memory into a readable page: who is in the workspace, what the team knows, decisions made, open work, history, and other facts. Without this manifest, the wider app system would not know that this extension provides a wiki agent or where to find its homepage-building skill.

The file first gives the extension a name and version. It then points to the folder where its skills live, especially the `app-wiki-home` skill, which is the recipe the agent follows when rebuilding or editing the wiki page. It also defines a private agent named `wiki`. “Private” here means the conversation and page are limited to the workspace’s admins and members who are allowed web access, rather than being public.

The long prompt is the agent’s operating instruction. It tells the agent what the homepage should look like and what to do when a member asks for changes. The purpose text is the short human-facing explanation of why the app exists. Finally, the `manifest()` function gathers all of this into a `Manifest` object, which is the format the host system expects when discovering extensions.

#### Function details

##### `manifest`  (lines 50–56)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the system-readable description of the Wiki app. The host uses it to learn the extension’s name, version, private wiki agent, and bundled homepage skill.

**Data flow**: It starts with the constants already defined in the file: the extension name and version, the prepared wiki agent, and the path to the homepage skill. It wraps the skill path in a `SkillSpec`, then places that skill and the wiki agent into a `Manifest`. The result is a complete manifest object returned to whoever is loading the extension.

**Call relations**: When the extension system asks this file what it provides, this function is the answer. It creates a `SkillSpec` so the homepage skill can be registered, then creates the `Manifest` that packages the agent and skill together for the shared apps infrastructure.

*Call graph*: 2 external calls (__init__, __init__).

# Application and Skill Bundle Registration  `stage-3.1`

This stage is the system’s sign-up desk for apps and skill bundles. It runs as shared setup support, before onboarding or a user turn needs to create an agent or load a skill. Each manifest is like a labeled plug that tells the host, “I exist, this is my agent, these are my instructions, and these are the skills or scheduled jobs I bring.”

The app manifests register workspace apps: Artifacts, Chat, Code, Issues, Meetings, Metrics, Radar, and Wiki. They tell the platform how each app should appear, what workspace agent to create, what setup or accounts it needs, and which homepage skill to load. Code focuses on pull-request review, Issues and Meetings can add scheduled work, Metrics reports delivery information, and the others provide their own workspace-facing agents.

The brief-pipeline and documents manifests add reusable writing tools, including specialist subagents and document-making skills. The sample skill probe is a simple load test that prints a success message. The skill-create extension registers user-made workspace skills so later turns can save, find, load, update, or delete them.

## Files in this stage

### Workspace App Manifests
Core workspace app extensions are declared so the platform can install their agents, schedules, setup needs, and homepage skills.

### `extensions/app_artifacts/ufo_ext_app_artifacts/manifest.py`

`config` · `startup`

This file is like a label and instruction card that comes with the Artifacts app. Without it, the shared apps infrastructure would not know that this extension exists, what it should be called, what version it is, or how to set up its workspace agent.

The app’s job is to provide a homepage for workspace artifacts: shared files and hosted sites, shown newest first with search, filters, paging, and a viewer for opened files. The file defines the agent named “artifacts” and gives it a detailed prompt. That prompt is the agent’s job description: keep the artifacts shelf working, show files and sites clearly, and use the `app-artifacts-home` skill when someone asks to change the page.

It also defines basic public details such as the app name, version, icon, purpose, and visibility. “Visibility” here means who can see or use the agent; in this case it is available to the workspace. The skill path points to a folder shipped alongside this file, so the system can load the static homepage-editing skill when needed.

In short, this file does not build the artifact shelf itself. It registers the shelf app with the platform and connects together the agent, its instructions, and its homepage skill.

#### Function details

##### `manifest`  (lines 40–46)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s manifest, which is the package description the host system reads to install or activate the app. It gathers the app name, version, agent definition, and skill location into one object.

**Data flow**: It reads the constants defined earlier in the file, such as the app name, version, prepared agent, and skill folder path. It creates a `SkillSpec` for the homepage skill, then creates and returns a `Manifest` containing that skill and the Artifacts agent. The result is a complete description of what this extension contributes to the system.

**Call relations**: When the extension is loaded, the host system calls `manifest` to ask, “What do you provide?” The function hands off to `SkillSpec.__init__` to describe the homepage skill, then to `Manifest.__init__` to bundle that skill together with the already defined agent provision.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_chat/ufo_ext_app_chat/manifest.py`

`config` · `startup / extension discovery`

This is the registration card for the workspace's main Chat app. It does not run the chat itself. Instead, it describes the app to the UFO platform: its name, version, main agent, icon, prompt, purpose, and the skill folder that contains the chat home behavior.

Think of it like a sign-up form for an app store inside the workspace. The file says: "There is an app called app_chat, it has a main agent named chat, it should use this instruction prompt, and its homepage behavior lives in the app-chat-home skill." The prompt explains the agent's job in human terms: show the current conversation, support the message composer, stream replies, and show starter prompts when no chat is open.

The important object here is the agent provision. It packages the agent's visible identity and operating instructions, including that it is the main workspace-visible chat agent and can use internet access. The `manifest()` function then returns one complete `Manifest`, which is what the host system reads when discovering and loading the extension.

#### Function details

##### `manifest`  (lines 36–42)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension's manifest, which is the platform-readable description of the Chat app. The system uses this to know the app's name, version, main agent, and the skill path for the chat home screen.

**Data flow**: It starts with the constants defined in this file, such as the app name, version, prepared chat agent, and skill folder path. It wraps the home skill path in a `SkillSpec`, then places that skill and the chat agent into a `Manifest`. The result is a complete manifest object that the platform can read to load the extension.

**Call relations**: When the platform discovers this extension, it calls `manifest()` to ask what the extension provides. Inside that call, the function creates a `SkillSpec` for the home skill and a `Manifest` containing both the skill and the already-defined chat agent, then hands that manifest back to the loader.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_code/ufo_ext_app_code/manifest.py`

`config` · `startup / extension discovery`

This file is the app’s registration card. It tells the larger system, “there is an app called app_code, it provides a workspace agent named code, and that agent reviews GitHub pull requests.” The agent’s main instructions are loaded from a prompt file, while extra procedures are shipped as skills, which are reusable instruction files kept with the extension.

A key idea here is that prompts are written into an agent record when the agent is first created, and then users may edit them. Because of that, adding new prompt text later would not reliably reach existing workspaces. Skills solve that problem: they are files delivered by the extension itself, so updated or newly added procedures can arrive on deploy. That is why the pull-request “babysitting” behavior, which keeps watching reviewed pull requests until they merge or close, is packaged as a skill rather than only prompt text.

The file also describes what setup is needed. The agent needs GitHub-related connectors and credentials, plus a source trigger so pull-request changes can wake the conversation. It deliberately uses a scheduled task for babysitting because some important events, like check results or commit statuses, may not move the pull-request source record. In short, this file connects the human-facing app idea to the platform’s installable pieces.

#### Function details

##### `manifest`  (lines 101–110)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the complete manifest object for this extension. The platform uses this to learn the app’s name, version, agent definition, and bundled skills.

**Data flow**: It starts from constants already defined in the file, such as the app name, version, prepared agent description, and skill folder paths. It packages those into a Manifest object, creating SkillSpec entries for the home skill and babysitting skill. The result is a single manifest value that the platform can read during extension loading.

**Call relations**: When the extension system asks this module what it provides, this function is the answer. It hands the platform a Manifest, and inside that it creates SkillSpec records so the platform knows which skill files belong to the app.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_issues/ufo_ext_app_issues/manifest.py`

`config` · `startup / app registration`

This file is the app’s registration form. It does not triage issues or write code itself. Instead, it tells the larger UFO platform how to create an agent that will do those jobs.

The app is built around one workspace agent named “issues.” That agent works over a GitHub issue tracker. Its first feature is triage: on a schedule, it looks for open issues that do not already have a comment from this app, then posts a useful summary, owner suggestion, and plan. The important safeguard is that the agent treats its own comment as the record that an issue has already been triaged. This avoids a loop where commenting on an issue wakes the app again and causes it to comment repeatedly.

The second feature is implementation. It only acts when a member approves an issue by adding the label “ufo:implement.” That label is treated as visible, lasting approval on the issue itself, rather than something hidden in a chat history.

The file also says what setup is required: the member must connect the relevant GitHub account and identify the repositories. Finally, it points to a homepage skill, which is extra behavior used when the agent needs to show or update its app page.

#### Function details

##### `manifest`  (lines 129–135)

```
def manifest() -> Manifest
```

**Purpose**: This function gives the UFO platform the complete description of the Issues app. The platform calls it when loading the extension so it can learn the app’s name, version, agent, and included skill.

**Data flow**: It starts with the constants and objects already defined in this file, such as the app name, version, prepared agent definition, and skills folder path. It wraps the homepage skill path in a SkillSpec, then returns a Manifest object containing the app’s identity, agent to create, and skill to install. Nothing is changed outside the function; the result is a packaged description for the platform to consume.

**Call relations**: When the extension is loaded, this function is the handoff point from this file to the wider manifest system. It creates a SkillSpec so the platform can find the homepage skill, then creates and returns a Manifest so the platform can register the app and provision the Issues agent.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_meetings/ufo_ext_app_meetings/manifest.py`

`config` · `startup / app installation`

This file is the app’s “registration card.” It does not do the meeting work itself. Instead, it tells the UFO platform what the Meetings app is, what it is allowed to do, and how it should be set up for a workspace.

The app is built around one workspace agent named “meetings.” Its main active feature is meeting briefs: before meetings happen, it should look at the calendar and prepare useful context, such as who is attending and what was left open from the last similar meeting. The file also describes two future or optional features: follow-ups and meeting notes. Those are deliberately not turned on at setup. The prompt says the agent must wait until a member asks for them, then connect the needed Google Docs account and create the right scheduled task.

The setup section asks only for a calendar connection at first, using the Google Calendar connector. This matters because a workspace might only want briefing, so it should not have to connect a notes account too early. The schedule defines when the briefing task can run, including a regular cadence and morning variants. Finally, the manifest points to a home-screen skill, which is the app’s page inside the workspace. In short, this file is the blueprint the platform reads to provision the Meetings app safely and consistently.

#### Function details

##### `manifest`  (lines 111–117)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the Meetings app manifest, which is the complete package of information the platform needs to recognize and install the app. It names the app, gives its version, lists the agent to create, and points to the app’s home skill.

**Data flow**: It starts with constants already defined in the file, such as the app name, version, agent definition, and skill folder path. It wraps the home skill path in a SkillSpec, then places that skill and the Meetings agent into a Manifest object. The result is a ready-to-read manifest object for the platform; it does not change external state by itself.

**Call relations**: When the platform asks this extension what it provides, this function is the handoff point. It creates a SkillSpec for the home page skill, then creates the Manifest that contains both that skill and the prebuilt Meetings agent configuration.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_metrics/ufo_ext_app_metrics/manifest.py`

`config` · `startup / extension discovery`

This file is like the label and instruction card inside a boxed app. It does not calculate metrics itself. Instead, it describes what the Metrics app is, what agent should be created for it, what that agent should say and do, what outside account it needs, and what schedule it should offer.

The main agent is named “metrics.” Its job is to report how the team is doing. Engineering delivery reporting is enabled from the start. Revenue and support reporting are described as possible future report sets, but the prompt is careful: the agent should not pretend to run those reports until a member explicitly asks and connects the needed account.

The file also defines the default engineering report schedule. It offers weekday morning-style cadences, especially Monday morning, because these reports are meant to be read as useful team updates rather than constant noise.

Setup is tied to GitHub. The app asks which repositories should be measured and connects the GitHub account that owns them. Finally, the manifest includes one skill, the homepage skill, which is used when the agent needs to show or update its metrics screen. Without this file, the platform would not know that this extension exists, what agent to create, or what setup steps and scheduled task belong to it.

#### Function details

##### `manifest`  (lines 107–113)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest, which is the platform-readable description of the Metrics app. The host system uses it to discover the app name, version, provided agent, and homepage skill.

**Data flow**: It takes no input from the caller. It reads the constants already defined in this file, such as the app name, version, prepared agent definition, and skill path. It packages those into a Manifest object and returns that object to the system.

**Call relations**: When the extension is loaded, the platform calls this function to ask, “What do you provide?” The function creates a SkillSpec for the homepage skill, then hands both that skill and the prebuilt Metrics agent into Manifest so the rest of the system can install and expose the app.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_radar/ufo_ext_app_radar/manifest.py`

`config` · `startup / extension registration`

This file is the Radar app’s registration form. Without it, the larger system would not know that this extension exists, what agent to create for it, or where to find the skill that powers its homepage.

The Radar app is meant to show a digest of recent scheduled runs: what each run found, when it happened, what task caused it, and where to open the fuller story. Think of it like a newsroom front page for automated work: short headlines on the main feed, with links into the full article.

Most of the file is made of clear constants. These give the app its internal name, version, skill folder, public agent name, and a long prompt. The prompt is important because it tells the Radar agent what its homepage should contain and how to respond when a workspace member asks to change that page. It also restricts the agent from internet access and makes it visible across the workspace.

The file then packages these details into an `AgentProvision`, which is the instruction to create an agent, and a `SkillSpec`, which points to the homepage-editing skill. The `manifest()` function returns the final `Manifest`, the bundle the host system reads to wire the extension into the app platform.

#### Function details

##### `manifest`  (lines 38–44)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the Radar app’s manifest, which is the extension’s official description for the host system. It says which agent should be created and which skill folder belongs to this app.

**Data flow**: It reads the constants defined in this file, including the app name, version, prepared Radar agent, and homepage skill path. It wraps the homepage path in a `SkillSpec`, then places the agent and skill into a `Manifest`. The result is a complete object the app platform can use to register the extension.

**Call relations**: When the extension system needs to learn what this package provides, this function is the handoff point. Inside it, the function creates a `SkillSpec` for the homepage skill and then creates the final `Manifest` that carries both the skill and the already-defined Radar agent to the surrounding app infrastructure.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/app_wiki/ufo_ext_app_wiki/manifest.py`

`config` · `startup`

This file is like the label and setup card that ships with the Wiki app. The app’s job is to turn a workspace’s shared knowledge into a readable page: who is in the workspace, how the team works, past decisions, open work, history, and other useful facts. Without this file, the platform would not know that the Wiki app exists, what agent to create for it, or which skill contains its homepage behavior.

The file defines simple constants such as the extension name, version, skill folder, agent name, and the skill used for the homepage. It then writes the agent’s instructions in plain text. Those instructions tell the agent what the Wiki page should look like and what to do when someone asks to change it. It also defines the agent’s purpose, icon, privacy, model choice, and safety-related settings such as not allowing internet access.

The key object is `WIKI_APP_AGENT`, an `AgentProvision`. A provision is a request to create an agent as part of installing or enabling the extension. Here, the agent is private, meaning it is meant for the workspace’s allowed users rather than the public. Finally, the `manifest` function packages the agent and its homepage skill into a `Manifest`, which is the platform’s standard “here is what this extension provides” record.

#### Function details

##### `manifest`  (lines 50–56)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s manifest, which is the formal description the platform reads to install or load the Wiki app. It says the extension’s name and version, includes the private wiki agent, and points to the skill folder for the homepage behavior.

**Data flow**: It starts with the constants already defined in the file: the app name, version, prepared wiki agent, and path to the homepage skill. It wraps the homepage skill path in a `SkillSpec`, then puts that together with the agent inside a `Manifest`. The result is a single manifest object that the larger system can read to know what this extension adds.

**Call relations**: When the extension system asks this file what it provides, `manifest` builds the answer. It calls `SkillSpec.__init__` to describe the homepage skill, then calls `Manifest.__init__` to bundle that skill with the wiki agent and extension metadata.

*Call graph*: 2 external calls (__init__, __init__).


### Document Production Bundles
Specialized writing and briefing extensions register subagents and skills for drafting, critique, and document creation.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup`

This is the extension’s sign-up sheet. When the larger UFO system looks for extensions, it needs a simple way to ask, “What do you add?” This file answers that question for the brief-pipeline extension.

The extension declares its name and version, then points to a skills folder on disk. A skill is a packaged instruction set that teaches the parent agent how to use the extension. In this case, the skill teaches the parent agent to chain three stages together: first an outline stage, then a drafting stage, then a critic stage. The critic does not directly rewrite the work; it gives feedback that the parent agent can apply.

The three stages are described elsewhere as profiles. A profile is like a job description for a helper agent: what role it plays and how it should behave. This file gathers those profiles and the skill path into a Manifest, which is the standard object the host expects from an extension. Without this file, the extension might exist on disk, but the host would not know what subagents or skills it is supposed to load.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration that the host application reads. It says: this extension is called brief_pipeline, this is its version, these are its three helper-agent profiles, and this is where its skill instructions live.

**Data flow**: It starts with fixed values from this file: the extension name, version, and skill folder path. It also uses the imported outline, draft, and critic profiles. It packages all of that into a Manifest object, including a SkillSpec that points to the skill directory, and returns that Manifest to the caller.

**Call relations**: The host extension loader calls this function when it wants to discover what the extension offers. Inside, it creates the skill description and then the full manifest, handing the host a ready-to-load description of the brief writing pipeline.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup / extension load`

This file is like the packing list for the documents extension. Without it, the larger UFO system would not know that this extension contains skills for Word documents, PowerPoint files, PDFs, spreadsheets, document review, themes, shared design rules, and prose drafting.

The file names the extension, gives it a version, points to the folder where its skills live, and lists the skill folders that should be made available. A skill here means a reusable workflow plus supporting files that teach the agent how to do a specific kind of document work. Some of those skills build on shared design foundations, so the system can keep documents visually consistent even when the user gives little style guidance.

It also registers a `writing` subagent profile. A subagent is a smaller, focused helper agent. In this case, it is meant for drafting and editing text, while the format-specific skills take care of the document container, such as DOCX, PPTX, XLSX, or PDF.

The main work happens in `manifest()`, which returns a `Manifest` object. That object is the formal description the extension loader reads during setup, so it can wire these skills and the writing helper into the rest of the system.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal manifest for the documents extension. The system uses this to discover the extension name, version, available document skills, and the bundled writing subagent.

**Data flow**: It starts with the constants in this file: the extension name, version, skills folder, skill names, and writing profile. It turns each skill name into a `SkillSpec`, which points at that skill's folder on disk. It then packages all of that into a `Manifest` object and returns it to the caller.

**Call relations**: When the extension loader asks this file what it contributes, `manifest` creates the answer. To do that, it calls `SkillSpec.__init__` for each listed skill folder, then calls `Manifest.__init__` to bundle those skill specs together with the extension metadata and the writing subagent profile.

*Call graph*: 2 external calls (__init__, __init__).


### Skill Validation and Creation
Sample and member-created skill support verifies skill loading and registers persistent workspace skill management.

### `extensions/sample/skills/sample_skill/probe.py`

`test` · `startup or health check`

This file answers a very simple question: “Can this sample skill’s Python code run at all?” It does not define any classes or functions. It just prints the message `sample-skill-probe-ok` as soon as the file is executed or imported as a script. That makes it useful as a probe, like tapping a microphone and listening for sound. If the surrounding system runs this file and sees the expected text, it knows the sample skill is reachable, Python can execute it, and the basic extension wiring is working. If the message does not appear, the problem is likely outside this file: the file may not have been found, the skill may not have been installed correctly, or the execution environment may be broken. Because the file has no branching, configuration, or dependencies, its behavior is intentionally predictable.


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `startup, object requests, runtime skill loading, scheduled indexing`

A “skill” here is a small bundle of text files, especially a required SKILL.md file, that teaches the agent a reusable behavior. This file is the bridge between those saved skill bundles and the rest of the UFO system. Without it, member-authored skills could not be treated like workspace objects, loaded at runtime, shown in the member portal, or indexed for search.

The file defines the shape of a saved skill: files may be given directly as text, copied from a workspace path, or kept unchanged by referring to their stored SHA-256 digest, which is a fingerprint of the file contents. It enforces safety limits, such as keeping file paths inside the skill folder, requiring text files, limiting the number and total size of files, and refusing stale edits when another writer has changed the skill first.

The SkillObjects class provides the object-style operations: list skills, get details, save changes, delete, and report status. It deliberately avoids returning file contents in object details; instead it returns digests, so large or sensitive file bodies are not echoed into normal context.

The file also supports runtime use. It can produce skill cards, materialize one saved skill or all saved skills, and periodically index skill descriptions so routing or search can find them. Finally, manifest() advertises all of this to the host application.

#### Function details

##### `_require_ext`  (lines 119–122)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure an extension context is present before any skill operation continues. The extension context is the object that tells the code which workspace, database transaction tools, sandbox, index, and other services it is allowed to use.

**Data flow**: It receives either an ExtensionContext or nothing. If the context is missing, it stops the operation with an error; otherwise it returns the same context so later code can safely use it.

**Call relations**: Most methods in SkillObjects call this at the start because they cannot read or write workspace skills without knowing the current extension environment. It is the front-door check before listing, reading, saving, deleting, resolving files, or reporting status.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 125–132)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every file path named in a skill stays inside that skill’s own folder. It prevents a skill from saving files under sneaky paths like ../other-place, which could overwrite or reference things outside the skill.

**Data flow**: It takes the skill name and the proposed skill specification. It computes the skill’s allowed root folder, checks every file key against that root, and either returns silently when all paths are safe or raises a clear error for the first unsafe path.

**Call relations**: SkillObjects.apply calls this before resolving or saving files. It relies on the shared sandbox path-checking helpers to enforce the same containment rules used elsewhere in the system.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_root).


##### `_text`  (lines 135–141)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This confirms that a skill file is plain UTF-8 text. Skills in this feature are text bundles, not arbitrary binary attachments.

**Data flow**: It receives a file path and raw bytes. It tries to decode the bytes into text; if decoding works, it returns the text, and if not, it raises an error naming the offending file.

**Call relations**: SkillObjects._resolve calls this after gathering each file’s bytes, no matter whether the bytes came from stored content, inline text, or a workspace file. It is the final text-only gate before saving.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved workspace skills for normal object-listing tools. It shows lightweight rows rather than full file contents.

**Data flow**: It receives a tool context and a list query such as paging or filtering options. It checks the extension context, reads skill rows, applies the object paging helper, and returns an ObjectPage.

**Call relations**: Object-listing flows call this when a user or agent asks what skill objects exist. It delegates the database-facing work to SkillObjects._rows and the page-shaping work to object_page.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 151–162)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the saved skills as a signed-in member would see them in the portal, outside an agent turn. The important point is that skills belong to the workspace, not to one person or one agent.

**Data flow**: It receives an extension context, member information, an admin flag, and a list query. It uses the workspace context to read the same rows as the normal list operation, then turns them into a paged result.

**Call relations**: Member-facing portal code calls this to show the workspace’s saved skills. It shares the same row-building path as SkillObjects.list, so the portal and tool view stay consistent.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 164–165)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches the detailed saved specification for one skill. It returns file fingerprints and sizes, not the file bodies themselves.

**Data flow**: It receives a tool context and a skill name. It checks the extension context, asks SkillObjects._skill for the stored detail, and returns that detail or null if the skill does not exist.

**Call relations**: Object-get flows call this before editing or inspecting a skill. It hands off to SkillObjects._skill, which knows how to translate stored bytes into digest references.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 167–185)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This returns one skill in the shape needed by the member portal: a display row together with its detailed object information. Like get, it avoids exposing file contents.

**Data flow**: It receives an extension context, a skill name, member information, and an admin flag. It reads all rows to find the matching display row, reads the detailed skill record, and returns both together as a MemberObject; if either piece is missing, it returns null.

**Call relations**: Member portal detail pages call this when someone opens one saved skill. It combines the row data from SkillObjects._rows with the digest-based detail from SkillObjects._skill.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 187–195)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the lightweight list entries for saved skills. Each row contains the skill name, a shortened description, and whether it is pinned.

**Data flow**: It receives an extension context, asks UserSkillStore for the workspace’s skill listing, shortens each description to the configured summary length, and returns a tuple of ObjectRow values.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail use this whenever they need display-ready rows. It is the common adapter between the skill store and object-list views.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 197–212)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This turns one stored skill record into safe object detail. It replaces each file body with a SHA-256 digest and size so callers can refer to unchanged files without receiving their full contents.

**Data flow**: It receives an extension context and a skill name. It asks UserSkillStore for the record; if none exists, it returns null. If found, it computes a digest and size for every stored file, builds a UserSkillSpec with FileRef values, and returns ObjectDetail with timestamps and generation information.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this when they need full metadata for one skill. It sits between raw stored files and the safer public object representation.

*Call graph*: called by 2 (get, member_detail); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 214–231)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports a compact status summary for a saved skill, such as its description, file count, total bytes, and pinned state. It also protects readers from accidentally using stale information.

**Data flow**: It receives a tool context, skill name, and expected generation. It reads the stored record; if missing, it returns null. If the generation does not match, it raises an error. Otherwise it returns a small dictionary of status facts.

**Call relations**: Status-checking object flows call this after reading a skill. It uses the same generation check pattern as edit and delete operations, so callers know whether the skill changed while they were looking.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects.apply`  (lines 233–256)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This saves a new or updated workspace skill. It validates the proposed bundle, resolves file references into actual text bytes, enforces size limits, and writes the result to the store.

**Data flow**: It receives a tool context, skill name, proposed UserSkillSpec, the old spec if any, and an expected generation. It checks the extension context, rejects too many files, checks path containment, resolves all file values into bytes, rejects an oversized bundle, and saves the finished skill with its pinned setting and generation guard.

**Call relations**: Object-apply flows call this when a user or agent creates or edits a skill. It calls _contained_keys for path safety, _resolve for turning references into content, and UserSkillStore.save for the actual persistence.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 258–269)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved skill from the workspace, but only if the caller is not working from a stale generation. That prevents deleting someone else’s newer edit by accident.

**Data flow**: It receives a tool context, skill name, and expected generation. It reads the current record; if the skill exists and its generation differs, it raises an error. Otherwise it asks the store to delete the skill.

**Call relations**: Object-delete flows call this when a user or agent removes a skill. It uses _require_ext for workspace context and UserSkillStore for reading and deleting the stored record.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 271–319)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This converts the flexible file values in a skill specification into the exact bytes that will be saved. It is where inline text, workspace file references, and keep-unchanged digest references all become real stored content.

**Data flow**: It receives a tool context, skill name, and skill spec. It reads already stored files so FileRef entries can keep existing content, verifies their digests, reads any FileFrom paths from the sandboxed workspace using a small Python helper, decodes those results, encodes inline strings, checks every result is text, and returns a path-to-bytes dictionary.

**Call relations**: SkillObjects.apply calls this after basic path checks and before saving. It uses the sandbox to read workspace files safely, _text to reject non-text content, and UserSkillStore to look up existing file bytes for unchanged files.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_member_cards`  (lines 353–354)

```
async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]
```

**Purpose**: This returns the lightweight skill cards shown or considered for member-authored skills. A skill card is the summary information used before loading the full skill.

**Data flow**: It receives an extension context, asks UserSkillStore for the workspace’s cards, and returns them as a tuple.

**Call relations**: The manifest registers this as the member skill card provider. Runtime skill-selection code can call it when it needs to know which saved skills are available.

*Call graph*: 1 external calls (__init__).


##### `_materialize_skill`  (lines 357–358)

```
async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one saved skill into the runtime form the agent can actually use. “Materialize” means turning the stored database version back into a usable RuntimeSkill object.

**Data flow**: It receives an extension context and skill name. It asks UserSkillStore to materialize that skill and returns the RuntimeSkill, or null if the skill cannot be found.

**Call relations**: The manifest registers this as the single-skill loader for member skills. Agent runtime code calls it when a particular saved skill should be loaded for a turn.

*Call graph*: 1 external calls (__init__).


##### `_materialize_all_skills`  (lines 361–362)

```
async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads all saved workspace skills into runtime skill objects. It is used when an agent turn wants the whole saved skill set.

**Data flow**: It receives an extension context, asks UserSkillStore to materialize every saved skill in the workspace, and returns the resulting tuple.

**Call relations**: The manifest registers this as the all-skills loader. Agent runtime code calls it when workspace skills are enabled and the full set needs to be available.

*Call graph*: 1 external calls (__init__).


##### `index_skills`  (lines 365–414)

```
async def index_skills(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job updates the search or routing index for saved skills whose descriptions have changed or have never been indexed. Indexing makes skills findable by meaning, not just by exact name.

**Data flow**: It receives an extension context, checks that both the index backend and embedding client are available, queries the database for stale skill cards, and tries to index each one. It counts successful settlements, logs individual failures, and raises the last error only if every attempted row failed.

**Call relations**: The extension job system calls this on a schedule for workspaces with stale skill indexes. It creates a TextChunker and calls _index_card for each stale row, so one bad skill does not normally block the rest.

*Call graph*: calls 2 internal fn (transaction, _index_card); 3 external calls (__init__, or_, select).


##### `_index_card`  (lines 417–454)

```
async def _index_card(ctx: ExtensionContext, index: IndexBackend, embed: EmbedClient, chunker: TextChunker, row: sa.Row) -> None
```

**Purpose**: This indexes one skill card and then marks it as indexed only if the stored skill has not changed during indexing. That guard avoids claiming that old content is up to date.

**Data flow**: It receives the extension context, index backend, embedding client, text chunker, and one database row. It sends the skill name and shortened description through chunk_embed_upsert, then updates indexed_digest only if the row’s digest still matches. If the skill was deleted meanwhile, it removes that skill’s index scope.

**Call relations**: index_skills calls this for each stale skill. It hands text to the shared indexing helper, then uses a database transaction to settle or clean up based on whether the skill survived unchanged.

*Call graph*: calls 2 internal fn (transaction, delete); called by 1 (index_skills); 4 external calls (__init__, select, update, chunk_embed_upsert).


##### `_skills_awaiting_index`  (lines 457–467)

```
def _skills_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with skills needing indexing. It does not run the query itself; it describes the candidate set for the job scheduler.

**Data flow**: It takes no runtime input. It creates a SQL select statement for distinct workspace IDs where a skill has no indexed digest or its indexed digest differs from the current digest, and returns that statement.

**Call relations**: manifest passes this function to owner_candidates when registering the indexing job. The job system uses it to decide which workspace owners should receive a skill_index run.

*Call graph*: 2 external calls (or_, select).


##### `manifest`  (lines 470–489)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s registration point. It tells the host application what this extension is called, what object type it adds, what built-in authoring skill it provides, how member skills load, and what scheduled job it needs.

**Data flow**: It takes no input. It constructs and returns a Manifest containing the skill object definition, the create-skill built-in skill path, member-skill callbacks, and the scheduled indexing job with its candidate query.

**Call relations**: The host application calls this during extension startup. The returned Manifest wires together the object operations in SkillObjects, the runtime loading helpers, and the scheduled index_skills job so the rest of the system can discover and use them.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).

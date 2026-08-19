# Agent, skill, and domain-workflow extension manifests  `stage-3.2.1`

This stage is shared setup support. It is made of extension “manifests,” which are simple declaration files the UFO system reads when an extension is turned on. A manifest is like a menu or sign-up sheet: it tells the core system which agents, tools, prompts, skills, secrets, and outside services are available.

Each file registers a different work area. The brief-pipeline manifest adds three specialist subagents and a parent skill that runs them in order. The browser manifest adds browser tools, a browser-focused subagent, prompt guidance, and a needed outside capability. The coding manifest registers coding agents, a review agent, GitHub credentials, and a GitHub installation route. The documents manifest makes document, PDF, presentation, spreadsheet, design, review, and drafting skills discoverable, plus a writing subagent. The research manifest adds research tools, helper agents, prompts, skills, and search support. The sites manifest adds website-building tools, prompts, skills, subagents, surfaces, and chat objects. The skill-create manifest lets users save, inspect, and reload their own skills later.

## Files in this stage

### Pipeline and browser manifests
These manifests introduce multi-agent brief orchestration and the browser capabilities that agents can use as an external operating surface.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s calling card. When the UFO system discovers the brief-pipeline extension, it needs a simple answer to: “What do you add?” This file provides that answer in a standard shape called a manifest, which is like a label on a toolbox listing what tools are inside.

The extension defines a name and version, then points to a skills folder on disk. That folder contains the instructions or behavior package for the parent agent. It also imports three ready-made subagent profiles: one for making an outline, one for writing a draft, and one for criticizing the draft. Together, these form a small assembly line: outline first, draft second, critique third. The parent agent is then expected to use the critique to improve the final result itself.

Without this file, the extension might still contain useful code and skill files, but the host system would not have a clear, standard way to discover them and wire them in. The manifest function packages the extension’s identity, subagents, and skill path into one object the rest of the system can read.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension description that the UFO system can load. It says the extension is named “brief_pipeline,” gives its version, lists the three subagent roles, and points to the skill files on disk.

**Data flow**: It starts with constants in this file: the extension name, version, and skill folder path. It also uses the imported outline, draft, and critic profiles. It wraps the skill folder in a SkillSpec object, then puts the name, version, subagent profiles, and skill spec into a Manifest object. The result is a single structured description of what this extension provides.

**Call relations**: When the extension system needs to know what this package contributes, it calls this function. The function creates a SkillSpec to describe the skill directory, then creates a Manifest to bundle that skill together with the three subagent profiles. That manifest is handed back to the host system so it can make the brief-writing pipeline available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/browser/ufo_ext_browser/manifest.py`

`config` · `extension load`

This file is like the label and contents list on a plug-in box. When the browser extension is loaded, the system needs to know its name, version, available tools, special browser subagent, and extra instructions for the main agent. Without this file, the rest of the system would not know how to offer browser automation as a delegated capability.

The file gathers pieces defined elsewhere: browser tools, delegation tools, and the browser subagent profile. The delegation tools are the safe front door the main agent uses to ask for browser work, while the browser subagent is the worker that can use the fuller browser-control surface. It also reads a Markdown prompt section from `prompts/browser_section.md`; this text teaches the main agent when and how to delegate browser tasks.

The central `manifest()` function returns a `Manifest`, which is the extension's registration form. It includes the extension name and version, the combined tool list, the browser subagent profile, the prompt section, and a requirement named `cdp_providers`. CDP means Chrome DevTools Protocol, a way for software to control and inspect a browser. In plain terms, this extension depends on browser-control providers being available before it can do useful work.

#### Function details

##### `manifest`  (lines 23–31)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the browser extension's registration object. The system uses this to discover the browser tools, the browser subagent, the prompt instructions, and the required browser-control support.

**Data flow**: It starts with constants and imported pieces already prepared by the file: the extension name and version, browser tools, delegation tools, the browser subagent profile, and the prompt text read from disk. It wraps the prompt text in a `PromptSection`, places everything into a `Manifest`, and returns that manifest to whoever is loading the extension.

**Call relations**: When the extension loader asks this file what it provides, this function packages the answer. Inside that packaging step, it creates a prompt section for the main agent's instructions and then creates the manifest that the rest of the system can register and use.

*Call graph*: 2 external calls (__init__, __init__).


### Domain workflow manifests
These extension declarations register specialized agents, tools, prompts, skills, secrets, and surfaces for coding, documents, research, and site-building workflows.

### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `extension load / startup`

This file is mostly a manifest: a structured description of everything the coding pack contributes. Without it, the platform would not know that there is a `coding` child agent for repository work, a stronger `fable_escalation` child agent for hard pull-request blockers, or a durable `code-review` agent that can watch and review pull requests.

The file starts by naming the pack, loading prompt text from nearby Markdown files, and listing the tools a coding child may use, such as reading files, editing files, running shell commands, and searching. It then defines small input and output shapes for coding tasks, so callers and child agents agree on the basic request and response format.

A large part of the file is about GitHub access. Repository work needs authentication, but the sandbox should not directly hold raw long-lived secrets. So the manifest declares credential slots: named places where GitHub installation tokens or fallback personal tokens can be supplied and safely injected into Git or API requests. Think of these slots like locked mailboxes: the agent can use the delivered token for the right destination, but the system controls how it is passed along.

At the end, `manifest()` packages all of this into one `Manifest` object. That object tells the host system which subagents to register, which skill folders to load, which credentials to prepare, which tool to expose for connecting GitHub, and which web route receives the GitHub App installation callback.

#### Function details

##### `github_app_id`  (lines 125–138)

```
def github_app_id() -> str | None
```

**Purpose**: This function checks whether the deployment has been configured with a complete GitHub App registration. It prevents a half-configured setup, where users might be told GitHub App access exists even though some required secret or identifier is missing.

**Data flow**: It reads four environment variables: the GitHub App ID, client ID, client secret, and private key. If none are set, it returns `None`, meaning this deployment will not use GitHub App token minting. If some are set but others are missing, it raises an error explaining which values must be added. If all are present, it returns the GitHub App ID.

**Call relations**: This check runs while the module is being loaded, before the manifest is built. Its result decides whether GitHub App token sources are created for Git and API credentials, or whether those credential slots rely on fallback behavior instead.


##### `manifest`  (lines 217–242)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension manifest that the platform reads to install and expose the coding pack. It is the single place where the pack’s agents, skills, credentials, tool, and GitHub callback route are gathered together.

**Data flow**: It takes no caller-provided input. It uses constants and objects already defined in the file: the coding subagent profiles, the code-review agent, the skill directory, GitHub credential slots, the `connect_github` tool definition, and the GitHub installation route. It returns a `Manifest` object containing all of those pieces in the format the host system expects.

**Call relations**: The platform calls this when loading the extension. Inside, it creates the small wrapper objects needed by the platform, including skill specifications, a tool definition for connecting GitHub, and a route specification for the GitHub App install callback, then hands them back as one complete manifest.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `extension discovery and skill loading`

This file is the “packing list” for the documents extension. It names the extension, gives it a version, points to the folder where its skills live, and lists every skill that should be available to the system. A skill here means a reusable workflow, stored in its own folder, that the agent can load when it needs to do a specific kind of work, such as editing a Word document, reviewing a PDF, building a slide deck, or drafting prose.

The file also connects in a writing subagent. A subagent is a focused helper agent with its own profile; in this case, it is meant for drafting and editing text. That matters because document work is often two jobs at once: shaping the file format and improving the words inside it. This manifest lets those parts be loaded together when needed.

The central idea is simple: when the extension loader asks, “What do you provide?”, the `manifest` function returns a structured answer. It builds a `Manifest` object containing the extension name, version, subagent profile, and a list of `SkillSpec` entries pointing at the skill folders on disk. This is like handing the system a menu that says what document services are available and where to find each recipe.

#### Function details

##### `manifest`  (lines 35–41)

```
def manifest() -> Manifest
```

**Purpose**: This function builds and returns the extension’s manifest, which is the system-readable description of what the documents pack contributes. The extension loader would use it to learn the pack’s name, version, available skills, and bundled writing subagent.

**Data flow**: It starts with constants already defined in the file: the extension name, version, the root folder for skills, the list of skill folder names, and the writing subagent profile. It turns each skill name into a full path under the skills folder, wraps each path in a `SkillSpec`, and places those skill descriptions plus the writing profile into a `Manifest`. The result is a complete manifest object ready for the rest of the system to read.

**Call relations**: When the system discovers this extension, it calls `manifest` to ask what the extension offers. Inside, `manifest` creates `SkillSpec` objects for each skill folder, then hands all of them to `Manifest.__init__` so the extension’s offerings are packaged in the standard form the loader expects.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file answers a simple question: “What does the research pack add to the system?” It gathers the pieces that make web research work and packages them into one Manifest, which is a structured description the host application can read at startup.

The file names the extension as “research” and gives it a version. It loads a web prompt section from a Markdown file, points to two skill folders, and imports the research tools, source-tracking conversation slot, and research subagent profiles defined elsewhere. A subagent profile is a prepared role for a helper agent, like assigning a specialist librarian to a hard research task.

The most important behavior is the declared requirement: requires=("search_providers",). This means the research pack does not bring its own search engine credentials or backend. Instead, it insists that the deployment has already configured some search provider. If not, the system should fail early at boot, rather than waiting until a user asks for web research and then breaking mid-task.

Without this file, the application would not know that the research extension contributes search tools, web-specific prompt instructions, research skills, source storage, or specialist research subagents.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the formal description of the research extension. The host uses this description to add the extension’s tools, subagents, prompt section, skills, conversation slot, and dependency on a search provider.

**Data flow**: It starts with constants and imported pieces: the extension name and version, research tools, research subagent profiles, the already-read web prompt text, skill folder paths, and the source-tracking slot. It wraps the prompt text in a PromptSection, turns each skill folder name into a SkillSpec, and then places everything into a Manifest. The result is one complete object describing what the research pack contributes and what it needs before it can run.

**Call relations**: When the extension system asks this file what it provides, this function assembles the answer. It hands the gathered pieces to Manifest, using PromptSection for the web prompt text and SkillSpec for each loadable skill folder, so the rest of the application can register the research features in a consistent way.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/sites/ufo_ext_sites/manifest.py`

`config` · `extension load`

This file answers a simple question: “When the sites extension is loaded, what should become available?” Without it, the rest of the system might contain website-building code, but the main app would not know to offer those tools, load the website-building instructions, register the hosted-site view, or allow chat to refer to site objects.

Think of it like a packing list for a toolbox. The file gathers together pieces defined elsewhere: tools that create and publish sites, delegation tools that can spawn a child agent focused on website building, a subagent profile for that child agent, a surface that displays a hosted site, an object kind for site references in chat, and a conversation slot for remembering site-related state.

It also reads a prompt section from a Markdown file. That text teaches the main agent how to use the sites feature: build, validate, serve, and return the hosted link. Finally, it points to the bundled website-building skill folder, so the skill loader can bring in the design guidance and templates needed for site projects.

The only function, `manifest`, packages all of these pieces into one `Manifest` object. The wider system can then load this single object instead of discovering each part by hand.

#### Function details

##### `manifest`  (lines 36–47)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the sites extension’s manifest, which is the formal list of everything the extension contributes to the system. It is used when the extension is loaded so the platform can register the website tools, prompts, skills, subagent, surface, object kind, and conversation slot.

**Data flow**: It starts with constants and imported pieces: the extension name and version, the prompt text read from disk, the skill folder path, and the tools and definitions imported from other files. It wraps the prompt text in a `PromptSection`, wraps the skill path in a `SkillSpec`, and places all of the pieces into a `Manifest`. The result is a single manifest object that the rest of the system can consume.

**Call relations**: When the extension loader asks this file what it provides, `manifest` gathers the pieces and hands them to `Manifest.__init__`. As part of that assembly, it creates a prompt section through `PromptSection.__init__` and a skill reference through `SkillSpec.__init__`, so the main system can later load the instructions and skill files along with the sites tools.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Skill authoring manifest
This manifest defines the user-authored skill extension, including persistent skill objects, inspection, runtime loading, and the built-in authoring skill.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `extension registration and object request handling`

This file is the bridge between user-authored skill files and the larger UFO extension system. A “skill” here is a small bundle of text files, always including SKILL.md, that can be saved under an agent and loaded in later turns. The important boundary is ownership: skills belong to the bound agent, not to an individual member, so every member working through that agent sees the same saved skill set.

The file defines the shape of a saved skill manifest. File contents may be given directly, copied from the workspace, or kept unchanged by referring to a stored SHA-256 digest, which is a fingerprint of the old file content. This avoids putting full saved file bodies back into context when someone only wants to preserve them.

When a skill is applied, the code checks that there are not too many files, that all paths stay inside the skill’s own mount folder, that the total size is small enough, and that every file is UTF-8 text. These checks matter because saved skill files are later written back into a workspace; without path containment, a malicious file name could act like a durable write outside its own folder.

The SkillObjects class supplies the object operations: list, get, status, apply, and delete. At the bottom, manifest() registers this object kind, the bundled “create-skill” authoring skill, and a loader for saved runtime skills.

#### Function details

##### `_require_ext`  (lines 99–102)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This is a small safety check that makes sure the operation has an ExtensionContext, which is the extension’s access to the current agent and storage. If the context is missing, it stops immediately with a clear internal error.

**Data flow**: It receives either an ExtensionContext or nothing. If a context is present, it returns it unchanged; if it is missing, it raises an error instead of letting later code fail in a confusing way.

**Call relations**: All the object operations call this before touching agent-scoped skill storage. It is the gatekeeper that turns the optional context carried by tool calls into the required context used by the store and other helpers.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 105–115)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every file path in a skill stays inside that skill’s own folder. It prevents a saved skill from using a path like “../other-file” to write outside its allowed area when the skill is later mounted.

**Data flow**: It receives the skill name and the proposed skill spec. It computes the skill’s mount root, tests each file key against that root, and either finishes silently or raises a ValueError naming the unsafe path.

**Call relations**: SkillObjects.apply calls this before saving anything. It relies on skill_mount_root to find the safe folder and contained_relative to enforce the “do not climb out” rule.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_mount_root).


##### `_text`  (lines 118–124)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This confirms that a skill file is plain UTF-8 text. Skills in this system are text bundles, so binary files are rejected before they are saved.

**Data flow**: It receives a file path and raw bytes. It tries to decode those bytes as text; success returns the decoded string, while failure raises a ValueError that explains which skill file is not text.

**Call relations**: SkillObjects._resolve calls this for every resolved file before returning the final bytes to be saved. It is the last content-type check in the apply path.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 131–132)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved skills for the current agent. It is used when a caller wants to browse available member-authored skill objects without reading file details.

**Data flow**: It receives a tool context and a list query such as paging or filtering information. It extracts the extension context, builds simple rows for the agent’s saved skills, and wraps those rows into an ObjectPage result.

**Call relations**: The object system calls this for normal object listing during a turn. It delegates to _rows to get the raw skill rows and to object_page to shape them according to the query.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 134–146)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same kind of saved-skill list for a signed-in member outside a normal turn. Even though a member is named, the skills are still scoped to the bound agent, not to that member.

**Data flow**: It receives an optional extension context, member information, admin status, and a list query. It requires the extension context, gathers rows for the bound agent, and returns them as a paged object list.

**Call relations**: The member-facing portal path calls this when it needs a page of skill objects. It uses the same _rows helper as SkillObjects.list, keeping turn-time and portal-time listings consistent.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 148–149)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches the detail view for one saved skill in the current tool context. The detail includes file fingerprints and sizes, not the full file contents.

**Data flow**: It receives a tool context and a skill name. It extracts the extension context, asks _skill for that named skill, and returns either the ObjectDetail or null if the skill does not exist.

**Call relations**: The object system calls this when a caller asks for one skill by name. It hands off the real lookup and detail construction to _skill.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 151–170)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This fetches one saved skill for the member-facing portal. It first confirms that the skill appears in the list view, then returns its row plus detail together.

**Data flow**: It receives an optional extension context, a skill name, member information, and admin status. It finds the matching row for the bound agent; if none exists it returns null. If the row exists, it loads the skill detail and packages the row and detail into a MemberObject.

**Call relations**: The portal calls this for a single skill page. It uses _rows as the source of truth for whether the skill is visible, then uses _skill to attach digest-based file details.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 172–176)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the simple list entries shown for saved skills. Each row contains the skill name and a short summary made from the skill description.

**Data flow**: It receives an ExtensionContext. It opens the UserSkillStore for that agent, loads all saved skills, trims each description to the configured maximum length, and returns a tuple of ObjectRow values.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail all use this helper so every list-style view is based on the same saved-skill index.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 178–201)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the detail record for one saved skill without exposing the actual file bodies. It gives callers stable fingerprints they can use to keep unchanged files on a later update.

**Data flow**: It receives an ExtensionContext and a skill name. It loads the stored files and timestamps; if either is missing, it returns null. Otherwise it creates a UserSkillSpec where each file value is a FileRef containing a SHA-256 digest and size, then adds creation time, update time, and a link showing which agent the skill belongs to.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this when they need detail for one skill. It talks to UserSkillStore for stored data, uses hashing to create file references, and asks the ExtensionContext for the agent name used in the scoped_to link.

*Call graph*: calls 1 internal fn (agent_name); called by 2 (get, member_detail); 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 203–217)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This gives a compact health/status summary for a saved skill. It reports the skill description, file count, and total byte size.

**Data flow**: It receives a tool context, skill name, and optional expected generation value. It loads the saved files for the current agent; if none exist, it returns null. If files exist, it parses the skill content to get the description and returns a small dictionary of status values.

**Call relations**: The object system calls this when it wants lightweight status rather than full detail. It uses UserSkillStore to read the files and parse_skill_content to understand SKILL.md.

*Call graph*: calls 1 internal fn (_require_ext); 2 external calls (__init__, parse_skill_content).


##### `SkillObjects.apply`  (lines 219–236)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a saved skill. It is the main write path, and it enforces size limits, path safety, text-only content, and validation before storing the files.

**Data flow**: It receives a tool context, object name, new skill spec, optional old spec, and optional expected generation. It checks the number of files, confirms paths stay inside the skill, resolves file bodies from inline text, workspace sources, or previous digests, checks total size, and saves the final bytes into the agent’s UserSkillStore along with the currently loaded built-in skill names.

**Call relations**: The object system calls this when a manifest is applied. It uses _contained_keys for path safety, _resolve for turning references into bytes, and UserSkillStore.save for persistence.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 238–246)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved skill for the current agent. It is used when a caller wants the skill to stop being available in later turns.

**Data flow**: It receives a tool context, skill name, and optional expected generation. It extracts the extension context and asks UserSkillStore to delete the named skill; it does not return a content result.

**Call relations**: The object system calls this for delete operations. It uses _require_ext for agent scope and then hands the actual removal to the store.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 248–296)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns the flexible file values in a skill spec into actual bytes ready to save. It supports three cases: new inline text, copied workspace files, and unchanged stored files referred to by digest.

**Data flow**: It receives a tool context, skill name, and skill spec. It first loads any existing stored files so FileRef entries can be checked against real content. It then collects workspace file references, asks the sandbox to read those files safely, decodes their base64 output, combines kept, copied, and inline files into one dictionary, checks each file is UTF-8 text, and returns path-to-bytes content.

**Call relations**: SkillObjects.apply calls this during create or update. It uses UserSkillStore for old content, the sandbox for reading workspace paths, hashing for digest checks, JSON and base64 for passing file data through the sandbox command, and _text for the final text-only validation.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 324–326)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads the current agent’s saved skills so they can be added to the runtime skill registry. In plain terms, it makes previously saved skills available for use in a later turn.

**Data flow**: It receives an ExtensionContext. It opens the UserSkillStore for that agent, loads all saved runtime skills, and returns them as a tuple.

**Call relations**: The Manifest returned by manifest() registers this as the runtime skill loader. The host system calls it when building the set of skills available for the bound agent.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 329–336)

```
def manifest() -> Manifest
```

**Purpose**: This describes the extension to the UFO host system. It says the extension’s name and version, what object kind it adds, what bundled authoring skill it ships, and how to load saved runtime skills.

**Data flow**: It takes no input. It creates a Manifest containing the skill object definition, a SkillSpec pointing at the bundled create-skill directory, and the _runtime_skills callback, then returns that Manifest.

**Call relations**: The extension loader calls this at registration time. The returned Manifest is how the rest of the system discovers SKILL_OBJECT, the bundled create-skill workflow, and the saved-skill runtime loader.

*Call graph*: 2 external calls (__init__, __init__).

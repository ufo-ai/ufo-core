# Skill and toolbox preparation  `stage-8.2`

This stage prepares the agent’s “skills” and tools before the main work begins, and also supports them while work is running. A skill is a packaged ability, usually stored as files, that the agent can copy into its workspace and use. The runtime code reads skill folders, understands which skills depend on others, and copies the needed files into the agent’s sandbox, which is its safe working area.

The tool registry is the catalog of callable tools. It gives each tool a name, describes its inputs so the AI model knows how to ask for it, and routes each request to the right code. The extension context builds a safer toolbox for extensions and background jobs, so they can do approved tasks without direct access to databases, secrets, or other workspaces.

Several pieces add skills to this system. The model catalog skill turns the live model list into a readable table of available AI models, costs, and features. The skill_create extension lets users save, inspect, update, delete, and reload their own skills, while its store enforces name rules, limits, and collision checks. The sample probe is a simple health check that confirms a sample skill can run.

## Files in this stage

### Skill sources
Built-in and extension-backed entrypoints define the skills that can be advertised or authored by users.

### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a simple but important problem: people need to know which models are available, but that list must not become stale or misleading. Instead of keeping a separate hand-written document, this code creates the catalog directly from the same model records the rest of the system uses for routing, pricing, and prompting. In everyday terms, it is like printing the restaurant menu straight from the kitchen’s current ingredient list, rather than from an old PDF.

The main function, `model_catalog_skill`, receives a `ModelRegistry`, which is the live collection of model descriptions known to the system. It sorts the models by their id, then turns each model into a row in a Markdown table. The table includes the provider, knowledge cutoff, context window, input and output price, whether reasoning is supported, and the API surface. Prices are stored internally as tiny money units, so `_per_mtok` converts them into normal dollar strings per million tokens.

Finally, the file wraps the table in a `RuntimeSkill`. A runtime skill is a piece of instruction-like content the system can load and use during operation. Because this skill is generated at boot from real registry data, it should match what the system can actually run.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This small helper turns an internal price value into a human-readable dollar amount. It is used so the model catalog can show prices in a familiar form, such as dollars per million tokens.

**Data flow**: It receives a price stored as micro-dollars, which are millionths of a dollar. It divides that number by the project’s constant for micro-dollars per dollar, formats the result with two decimal places, and returns a string like `$1.25`.

**Call relations**: When `model_catalog_skill` is building the catalog table, it calls `_per_mtok` for each model’s input and output price. `_per_mtok` does only the formatting step, then hands the readable price text back for inclusion in the table row.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the complete model-catalog runtime skill. Someone would use it at boot time to generate a trustworthy model list from the actual registry, rather than from separate documentation.

**Data flow**: It receives a `ModelRegistry`, which contains model specifications. It reads each specification, sorts the models by id, builds a Markdown table with their key facts, converts prices using `_per_mtok`, wraps the table with skill metadata, and returns a `RuntimeSkill` containing the final instructions and raw Markdown.

**Call relations**: This is the main builder in the file. As it assembles each table row, it calls `_per_mtok` to make prices readable. At the end, it calls `RuntimeSkill.__init__` to package the generated catalog text into the form the rest of the runtime can load as a skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `extension startup, object operations, and runtime skill loading`

This file makes user-written skills behave like named objects that can live beyond the current workspace. A skill is a small bundle of text files, especially a required SKILL.md file, that can later be loaded and used by the agent. Without this file, the system would not know how to expose saved skills through the manifest API or how to add them back into the agent’s available skills on later turns.

The file defines the shape of a saved skill request. A file can be given directly as text, copied in from the temporary workspace, or referred to by a stored SHA-256 digest, which is a content fingerprint. That digest lets a user say “keep this existing file unchanged” without putting the full file text back into the conversation.

The central piece is SkillObjects. It provides the object operations: list saved skills, get safe metadata about one, report its status, apply a new version, and delete it. When applying a skill, it reads any workspace files through the sandbox, checks file count and total size limits, confirms every file is UTF-8 text, and then asks UserSkillStore to save it. This is like packing a folder into a labeled box: the file verifies the label, contents, and weight before putting the box on the shelf.

At the bottom, the manifest function registers this object kind and the built-in authoring skill, and tells the host how to load saved skills at runtime.

#### Function details

##### `_require_ext`  (lines 92–95)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has the extension context it needs. The extension context is the information that ties the operation to this specific installed extension and agent.

**Data flow**: It receives a ToolContext. If that context contains an extension context, it returns it unchanged. If not, it stops the operation with a clear runtime error, because the rest of the code cannot safely find the right skill store without it.

**Call relations**: The SkillObjects methods call this before they touch saved skills. It is the small gatekeeper that ensures list, get, apply, delete, and file lookup are all working in the right extension scope.

*Call graph*: called by 5 (_files, apply, delete, get, list).


##### `_text`  (lines 98–104)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This helper checks that a skill file is real text, specifically UTF-8 text. Skills are meant to bundle readable text files, not arbitrary binary data such as images or compiled files.

**Data flow**: It receives a skill-relative path and raw bytes. It tries to decode the bytes into text. If decoding works, it returns the text; if not, it raises a friendly error naming the file that is not valid text.

**Call relations**: SkillObjects._resolve calls this after it has gathered file contents from inline text, stored references, or workspace files. It is the final text-only check before the files are considered ready to save.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 111–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This method returns a paged list of the saved user skills for the current agent. It shows each skill by name with a short description summary.

**Data flow**: It receives the tool context and a list query such as paging information. It gets the extension context, loads all saved skills from UserSkillStore, turns each one into a small row with a name and shortened description, and returns a page of rows.

**Call relations**: When the object system needs to show available skill objects, it calls this method. The method asks UserSkillStore for the saved skills and hands the rows to object_page so the result follows the object API’s paging rules.

*Call graph*: calls 1 internal fn (_require_ext); 3 external calls (__init__, __init__, object_page).


##### `SkillObjects.get`  (lines 119–137)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This method returns safe details about one saved skill without dumping its full file contents into the conversation. It describes each stored file by its content fingerprint and size.

**Data flow**: It receives a context and a skill name. It loads the skill’s files, looks up creation and update times, computes a SHA-256 digest and byte size for each file, and returns an ObjectDetail containing a UserSkillSpec made of FileRef entries. If the skill or timestamps are missing, it returns nothing.

**Call relations**: The object system calls this when someone asks to inspect a skill object. It relies on SkillObjects._files for the stored bytes, UserSkillStore for timestamps, and FileRef values so a later apply can keep unchanged files by sending the digest back.

*Call graph*: calls 2 internal fn (_files, _require_ext); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 139–153)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This method gives a quick health-style summary of one saved skill. It reports what the skill says it does, how many files it has, and how large it is.

**Data flow**: It receives a context, a skill name, and an optional expected generation value. It loads the skill files, parses the SKILL.md content to find the description, counts files and bytes, and returns those facts as a small dictionary. If the skill does not exist, it returns nothing.

**Call relations**: The object system can call this after or around object changes to show the current state. It uses SkillObjects._files to fetch stored content and parse_skill_content to understand the skill’s declared description.

*Call graph*: calls 1 internal fn (_files); 1 external calls (parse_skill_content).


##### `SkillObjects.apply`  (lines 155–171)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method saves a new or updated version of a user-authored skill. It enforces the limits that keep a skill small, text-only, and safe to persist.

**Data flow**: It receives a context, skill name, desired spec, optional old spec, and optional expected generation. It checks the number of files, resolves all file values into actual bytes, checks the total byte size, and then saves the result through UserSkillStore along with the currently known built-in skill names so a user skill cannot replace one.

**Call relations**: The object system calls this when a manifest is applied for a skill. It delegates the complicated file gathering to SkillObjects._resolve, then hands the verified bundle to UserSkillStore for persistence.

*Call graph*: calls 2 internal fn (_resolve, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 173–181)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This method removes a saved user skill by name. It is the delete operation for the skill object kind.

**Data flow**: It receives a context, the skill name, and an optional expected generation value. It gets the extension context and tells UserSkillStore to delete that skill. It does not return a value.

**Call relations**: The object system calls this when a user deletes a skill object. It is a direct handoff from the object API to UserSkillStore, scoped through the extension context.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._files`  (lines 183–185)

```
async def _files(self, ctx: ToolContext, name: str) -> dict[str, bytes] | None
```

**Purpose**: This private helper loads the raw stored files for a named skill. It keeps the repeated store lookup in one place.

**Data flow**: It receives a context and skill name. It extracts the extension context, opens the UserSkillStore for that extension, and asks for the skill’s files. It returns a mapping from skill-relative file paths to bytes, or nothing if the skill is not found.

**Call relations**: SkillObjects.get, SkillObjects.status, and SkillObjects._resolve all use this helper when they need the existing stored contents. It is the shared doorway from object operations into saved file data.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_resolve, get, status); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 187–235)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This method turns a requested skill spec into a complete set of file bytes ready to save. It understands three ways of providing a file: inline text, a workspace path, or a stored digest that means “keep the old copy.”

**Data flow**: It receives a context, skill name, and UserSkillSpec. It first loads any existing stored files so digest references can be checked. It verifies that every FileRef really matches the stored content. It then reads any FileFrom workspace files inside the sandbox, using a small Python program that checks each path is a regular file and that the total read size stays within the limit. Finally, it combines kept files, copied files, and inline strings into one path-to-bytes mapping, checking every file is UTF-8 text before returning it.

**Call relations**: SkillObjects.apply calls this before saving. This method pulls existing files through SkillObjects._files, reads workspace files through the sandbox, decodes sandbox output from JSON and base64, and uses _text as the final text validation step.

*Call graph*: calls 2 internal fn (_files, _text); called by 1 (apply); 6 external calls (b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 262–264)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This function loads the saved user skills that should become available to the agent during a turn. It is how persisted skills re-enter the runtime skill list.

**Data flow**: It receives an ExtensionContext. It opens UserSkillStore for that context and returns all saved skills as runtime skill objects.

**Call relations**: The manifest registers this function as the runtime_skills provider. When the host is building the agent’s skill registry for a turn, it calls this function so saved user skills can be mounted alongside other skills.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 267–274)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It gives the extension its name, version, object kind, built-in authoring skill, and saved-skill loader.

**Data flow**: It takes no input. It constructs and returns a Manifest containing the skill_create extension metadata, the SKILL_OBJECT definition, the create-skill skill directory, and the _runtime_skills callback.

**Call relations**: The extension host calls this when loading the extension. The returned Manifest is the package label and instruction sheet that tells the host what objects this extension adds and what skills it contributes.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and cross-turn persistence`

A user-created skill is like a small folder of files that teaches an agent a reusable behavior. This file is the safe storage layer for those folders. It records them in a database table keyed by workspace, agent, and skill name, so each agent sees only its own saved skills.

Before a skill is saved, the store checks that its name is a safe lowercase “slug” such as `summarize-email`. This matters because the name is used as both a registry key and a stored identifier; unsafe names with slashes, dots, spaces, or uppercase letters could confuse later file or lookup logic. It also parses the skill content to make sure the files form a valid runtime skill, refuses to overwrite a core or pack skill unless this agent already owns that user skill, and enforces a maximum of 100 saved skills per agent.

The actual file bytes are stored as base64 text, which is a way to turn arbitrary bytes into database-safe characters. The store also creates a SHA-256 digest, a fingerprint of the saved content, and keeps created and updated timestamps. When loading skills, it is deliberately forgiving: if one stored skill is corrupt, it logs a warning and continues loading the rest instead of breaking the whole agent.

#### Function details

##### `UserSkillStore.save`  (lines 69–126)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves one skill for the current workspace and agent, either by updating an existing saved skill or inserting a new one. It is the main gatekeeper that checks the name, validates the skill files, prevents clashes with built-in skills, and enforces the per-agent skill limit.

**Data flow**: It receives a skill name, a set of file paths mapped to raw bytes, and the names already present in the skill registry. It reads the current workspace and agent, checks whether the name is allowed, parses the files into a runtime skill, checks whether this agent already owns that name, and counts existing user skills if this would be a new one. It then base64-encodes the files, stores them as JSON text, computes a content fingerprint, writes the record inside a database transaction, and returns the parsed runtime skill.

**Call relations**: This is called when something wants to create or re-save a user-authored skill. During that flow it asks `UserSkillStore._owns` whether the skill is already this agent’s property, and may ask `UserSkillStore._count` whether the agent has room for another skill. After those checks, it hands the validated content to the database as either an update or an insert.

*Call graph*: calls 2 internal fn (_count, _owns); 10 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, agent_current, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 128–162)

```
async def load_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved user skill belonging to the current agent. It turns stored database text back into usable runtime skills, while skipping any broken saved skill instead of failing the whole load.

**Data flow**: It reads the current workspace and agent, queries the database for all matching skill names and stored content, and processes them in name order. For each row, it validates the stored JSON shape, base64-decodes each file back into bytes, and parses those files into a runtime skill. The result is a tuple of usable skills; corrupt entries are left out and recorded in the log.

**Call relations**: This function is used when the agent needs to rebuild its available user-created skills from persistent storage. It hands each decoded skill folder to `parse_skill_content`, which performs the final conversion into the runtime form the rest of the system can use.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 164–180)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original files for one saved skill owned by the current agent. It is useful when the system needs the raw contents, for example to inspect, edit, export, or display a skill.

**Data flow**: It receives a skill name, reads the current workspace and agent, and looks up that exact record in the database. If no record exists, it returns `None`. If it finds one, it validates the stored JSON and base64-decodes each saved file back into bytes, returning a dictionary of file paths to file contents.

**Call relations**: This sits beside the higher-level loading path. Unlike `UserSkillStore.load_all`, it does not parse the files into a runtime skill; it gives callers the saved file bundle directly.

*Call graph*: 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 182–191)

```
async def delete(self, name: str) -> None
```

**Purpose**: Removes one saved user skill for the current agent. It is the cleanup path when an agent no longer needs a user-authored skill.

**Data flow**: It receives a skill name, reads the current workspace and agent, and deletes the matching database row if one exists. It does not return a value; after it runs, that skill is no longer stored for that agent.

**Call relations**: This is used by flows that remove user-created skills. It talks directly to the database and does not need to parse skill content, because deleting only depends on the workspace, agent, and skill name.

*Call graph*: 2 external calls (delete, agent_current).


##### `UserSkillStore.timestamps`  (lines 193–205)

```
async def timestamps(self, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Returns when a saved skill was first created and when it was last updated. This helps callers show history or decide whether a saved skill has changed.

**Data flow**: It receives a skill name, reads the current workspace and agent, and queries the database for the matching row’s creation and update times. If the skill is not found, it returns `None`; otherwise it returns the two timestamps as a pair.

**Call relations**: This is a small lookup used by callers that need metadata rather than skill contents. It stays separate from `UserSkillStore.files` and `UserSkillStore.load_all` so those heavier decoding and parsing steps are not done just to read dates.

*Call graph*: 2 external calls (select, agent_current).


##### `UserSkillStore._count`  (lines 207–219)

```
async def _count(self) -> int
```

**Purpose**: Counts how many user-created skills the current agent already has. It exists to enforce the maximum number of saved skills before adding a new one.

**Data flow**: It reads the current workspace and agent, asks the database to count matching rows in the user skill table, and returns that number. It does not change any stored data.

**Call relations**: `UserSkillStore.save` calls this only when saving a new skill name. If the count has already reached the configured cap, the save flow stops before writing anything.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


##### `UserSkillStore._owns`  (lines 221–233)

```
async def _owns(self, name: str) -> bool
```

**Purpose**: Checks whether the current agent already has a saved skill with a given name. This is important because re-saving your own skill is allowed, but taking over a built-in or pack-provided skill name is not.

**Data flow**: It receives a skill name, reads the current workspace and agent, and looks for a matching database row. It returns `true` if the row exists and `false` if it does not.

**Call relations**: `UserSkillStore.save` calls this early in the save process. The answer controls two later decisions: whether a registry-name collision is forbidden, and whether the save should count as adding a new skill toward the agent’s limit.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


### Toolbox boundaries
The extension execution context and tool registry describe which safe capabilities are exposed to handlers and models.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting during extension handlers, background jobs, and turn-related work`

Extensions need useful powers: save small bits of state, read declared credentials, call a model, register synced content, inspect conversation transcripts, write files for a conversation, or propose an agent prompt change. But giving them direct access to the whole database or every secret would be like handing a guest the master key to the building. This file instead creates scoped handles: small doorways that only open to the current workspace and only for the capabilities the extension declared.

The main object is ExtensionContext. It is the single bundle passed to handlers. Inside it are pieces such as ScopedStore for extension-owned key-value data, CredentialAccess for named secret slots, ModelAccess for metered language-model calls, TrajectoryCorpus for read-only conversation transcripts, and source/page helpers for content sync. Most methods find the active workspace through ws_current(), which means the caller does not pass a workspace id around; the runtime binds one before the handler runs.

The important safety pattern is repeated throughout: check the declared permission, use the current workspace, perform a narrow database query or service call, and fail loudly if the capability was not wired. This makes core jobs and extensions travel through the same path, so internal code is held to the same boundaries as outside extension code.

#### Function details

##### `ScopedStore.workspace_id`  (lines 76–77)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently bound to the running task. This lets the store automatically stay inside the right workspace without asking callers to pass an id.

**Data flow**: It reads the ambient workspace from ws_current() → takes its workspace_id → returns that UUID. It does not change anything.

**Call relations**: ScopedStore methods use this property whenever they read or write extension state, so each database operation is tied to the workspace that the runtime has already selected.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 79–90)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved value from this extension’s private key-value area in the current workspace. It is used when an extension needs durable state, such as cached setup information, without seeing anyone else’s data.

**Data flow**: A string key goes in → the method opens a workspace-scoped transaction and looks for a row matching the current workspace, this extension name, and that key → it returns the stored JSON-like value, or None if no row exists.

**Call relations**: Browserbase’s provider uses this to retrieve its stored context. Internally, this method relies on the shared workspace transaction helper so the read follows the same workspace boundary as the rest of the system.

*Call graph*: called by 1 (_context); 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 92–113)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one value in this extension’s private store for the current workspace. It gives extensions a simple durable memory without exposing the database directly.

**Data flow**: A key and JSON-like value go in → the method first tries to update an existing row for this workspace and extension → if nothing was updated, it inserts a new row → nothing is returned, but the stored value is changed.

**Call relations**: Browserbase’s provider uses this to persist its context. The method wraps the update-or-insert work in a workspace transaction so callers do not handle database details themselves.

*Call graph*: called by 1 (_context); 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.delete`  (lines 115–123)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension’s private store in the current workspace. It is for clearing extension state without touching other keys or workspaces.

**Data flow**: A key goes in → the method opens a workspace-scoped transaction and deletes only the row matching the current workspace, extension name, and key → nothing is returned.

**Call relations**: This is the cleanup companion to get and put. It uses the same scoped database path, so deletion is limited in the same way reads and writes are.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 125–138)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists saved key-value pairs for this extension in the current workspace, optionally limited by a key prefix. It is useful when an extension stores related entries under names that share a beginning.

**Data flow**: An optional prefix goes in → the method queries rows for the current workspace and extension whose keys start with that prefix → it returns an ordered tuple of key and value pairs.

**Call relations**: The web extension’s audience code uses this to find stored audience grants. Like the other store methods, it uses the workspace transaction helper so the listing cannot wander into another workspace.

*Call graph*: called by 2 (_granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 152–153)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the id of the workspace whose credentials this access object may read. It keeps credential operations tied to the workspace currently running.

**Data flow**: It reads the ambient workspace from ws_current() → extracts its workspace_id → returns that UUID. No credentials are read here.

**Call relations**: Credential methods use this workspace identity when sealing provider installations and when asking the current workspace for secrets.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 155–161)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches a secret value from a declared credential slot. A slot is a named place for a secret, and this method refuses to read slots the extension did not declare.

**Data flow**: A slot name goes in → the method checks that the slot is in the declared set → if not, it raises UndeclaredCredentialSlot before touching secrets → otherwise it asks the current workspace to resolve the credential and returns the secret string.

**Call relations**: Extensions call this when they need an API key or similar secret. It hands the actual lookup to the workspace object, so fallback rules such as workspace-provided key versus platform default stay centralized.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 163–168)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current value matches an expected value. This compare-and-swap pattern helps avoid overwriting a secret that changed since the caller last saw it.

**Data flow**: A slot name, expected old value, and new plaintext value go in → the method checks the slot was declared → it asks the current workspace to rotate the credential → it returns true or false depending on whether the replacement happened.

**Call relations**: This is used after an outside provider rotates a credential. It delegates the actual secret update to the workspace layer and keeps the same declared-slot gate as CredentialAccess.get.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 170–183)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores a provider installation as a sealed credential value for a declared slot. The stored value is not just a raw installation id; it is protected so it only opens for this workspace and slot.

**Data flow**: A slot name and installation id go in → the method checks the slot was declared → it creates a sealed token using the installation-sealing key, current workspace id, slot, and installation id → it stores that sealed value as the workspace credential.

**Call relations**: This method connects extension credential access with the provider-installation system. It calls the credential sealing helpers before handing the final protected value to the current workspace.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 216–217)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id whose conversation transcripts may be read. It keeps transcript reads limited to the workspace currently bound to the job.

**Data flow**: It reads ws_current() → takes the workspace_id → returns it. It does not read any transcripts by itself.

**Call relations**: TrajectoryCorpus.trajectories uses this property to build its database query for recent conversations.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 219–270)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Builds a read-only sample of recent conversation transcripts for evaluation or analysis jobs. It skips missing or broken transcripts instead of failing the whole job.

**Data flow**: No direct input goes in beyond the corpus limit and blob store held on the object → it finds recent conversations in the current workspace that have turns, fetches each transcript blob, decodes it, computes the current agent prompt digest, and wraps the result as Trajectory objects → it returns a tuple of successfully decoded trajectories.

**Call relations**: ExtensionContext.trajectories delegates here when a handler asks for the corpus. This method combines database metadata, blob storage, transcript decoding, and governance prompt digesting into one safe read-only view.

*Call graph*: 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 288–291)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s agent-visible workspace. This lets an off-turn job prepare a file that the agent can later see under /workspace.

**Data flow**: A conversation id, relative path, and byte content go in → the method asks the conversation sandbox service to place the bytes at that path → it returns the /workspace path visible to the agent.

**Call relations**: ExtensionContext includes ConversationFiles only when sandbox support is wired. This method is a narrow wrapper around the sandbox, exposing file writing but not arbitrary command execution.


##### `ConversationFiles.prune`  (lines 293–299)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a conversation workspace prefix, keeping only a chosen number of newest-looking names. This prevents unattended file writers from growing a conversation workspace forever.

**Data flow**: A conversation id, relative prefix, and keep count go in → the method asks the sandbox service to remove extra files under that prefix → nothing is returned.

**Call relations**: This is the cleanup partner to ConversationFiles.write. It keeps the extension-facing surface small by delegating only this pruning operation to the sandbox layer.


##### `trajectory_workspaces`  (lines 302–324)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Defines which workspaces are candidates for jobs that read trajectory data. In plain terms, it selects workspaces that actually have at least one conversation with at least one turn.

**Data flow**: No caller data goes in → it creates a candidate query function for workspaces with turn-bearing conversations → it passes that function to owner_candidates and returns the resulting WorkspaceCandidates object.

**Call relations**: A trajectory-reading extension can declare this candidate source. The dispatcher later binds each candidate workspace, after which TrajectoryCorpus reads under normal workspace scope.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 310–322)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used by trajectory_workspaces to find eligible workspaces. It looks for workspaces that have a conversation, and that conversation has a turn.

**Data flow**: No runtime input goes in → it constructs a SQL select with nested existence checks → it returns a query that yields workspace ids.

**Call relations**: This helper is created inside trajectory_workspaces and handed to owner_candidates. It keeps the exact database knowledge about conversations and turns in core code, not in extensions.

*Call graph*: 2 external calls (exists, select).


##### `TurnInvoker.invoke`  (lines 331–333)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Describes the method an internal turn invoker must provide. It starts a turn in a conversation without consuming a member’s normal pause or quota gate.

**Data flow**: A conversation id, agent id, message, and idempotency key go in → an implementation should admit or reuse the matching turn → it returns the created or reused turn id.

**Call relations**: ExtensionContext.invoke calls this protocol method when an invoker has been wired. This file only states the shape; the real turn-running code lives elsewhere.


##### `ModelResolver.auto_model`  (lines 343–343)

```
def auto_model(self) -> str
```

**Purpose**: Describes how to ask for the deployment’s default model name. ModelAccess uses this so background handlers call the same default model the system expects.

**Data flow**: No input goes in → an implementation returns a model identifier string → nothing is changed.

**Call relations**: ModelAccess.model and ModelAccess.turn read this property before making model calls. The protocol keeps this file from importing the full model registry directly.


##### `ModelResolver.pricing`  (lines 346–346)

```
def pricing(self) -> Pricing
```

**Purpose**: Describes how to get the price table used for billing model usage. Pricing tells the system how token counts turn into billable cost.

**Data flow**: No input goes in → an implementation returns a Pricing object → ModelAccess later uses it when recording usage.

**Call relations**: ModelAccess.turn reads this property after a model stream reports token usage. This lets the billing step stay connected to the same resolver that chose the model client.


##### `ModelResolver.client_for`  (lines 348–348)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Describes how to obtain a model client for a named model. A model client is the object that actually talks to the language-model provider.

**Data flow**: A model name goes in → an implementation chooses the right provider client, usually using the current workspace’s credential rules → it returns a ModelClient.

**Call relations**: ModelAccess.turn calls this before streaming a completion. The protocol boundary lets the context expose model access without depending directly on the registry implementation.


##### `ModelResolver.key_slot_for`  (lines 350–350)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Describes how to find which credential slot supplies a model’s key, if any. This is used to label usage as using a workspace-provided key or not.

**Data flow**: A model name goes in → an implementation returns a credential slot name or None → no state changes.

**Call relations**: context_for stores this function on ExtensionContext when a model resolver is present. pending_usage_exports later uses it to label exported billing records.


##### `ModelAccess.model`  (lines 366–368)

```
def model(self) -> str
```

**Purpose**: Returns the default model that this access object will call and bill. It makes clear that handlers do not pick an arbitrary model through this seam.

**Data flow**: It reads the resolver’s auto_model property → returns that model name string → nothing is changed.

**Call relations**: Handlers can inspect this property before calling complete or turn. The actual model call path in ModelAccess.turn uses the same resolver value.


##### `ModelAccess.complete`  (lines 370–377)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one language-model request and returns only the assistant’s text. It is the simple helper for callers that do not want to inspect tool calls or structured assistant blocks.

**Data flow**: A ModelRequest goes in → the method calls ModelAccess.turn to get a full assistant Message → if the content is plain text it returns it, otherwise it joins all text blocks and returns that combined string.

**Call relations**: The memory extension’s summarizer uses this for text summarization. It relies on ModelAccess.turn for streaming, tool-call collection, and billing, then narrows the result to text.

*Call graph*: calls 1 internal fn (turn); called by 1 (_summarize).


##### `ModelAccess.turn`  (lines 379–427)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one metered language-model turn and returns the full assistant message, including any tool calls. It ensures the model call is billed to the current workspace.

**Data flow**: A ModelRequest goes in → the method replaces its model with the deployment default, gets the matching client, streams response events, gathers text chunks, tool-call JSON pieces, and usage counts → it records the total usage inside a billable event → it returns an assistant Message containing text and, if present, tool-use blocks.

**Call relations**: ModelAccess.complete calls this when it wants a text-only result. This method sits between extension handlers and the model provider, so handlers do not bypass workspace credentials or metering.

*Call graph*: called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, model_copy, loads, ws_current).


##### `ExtensionContext.pending_usage_exports`  (lines 493–515)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns this extension’s pending billing usage exports for the current workspace. Before reading, it mints any new export records that are ready, so consumers see a stable batch.

**Data flow**: A time floor and limit go in → the method requires key_slot_for to be wired, opens a workspace transaction, creates export intents for settled usage after the floor rules, then reads up to the requested limit → it returns UsageExport records.

**Call relations**: External billing-export handlers call this before sending usage elsewhere. It hands off ledger-specific work to accounting helpers while keeping the export keyed by this extension name.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 517–526)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an outside receiver has accepted them. This prevents successfully delivered records from being sent again.

**Data flow**: A tuple of UsageExport records goes in → if it is empty the method stops immediately → otherwise it opens a workspace transaction and asks accounting code to acknowledge those records → nothing is returned.

**Call relations**: This is the second half of the export flow started by pending_usage_exports. If a caller does not acknowledge, the same frozen exports can be read again for safe retry.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 529–541)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Opens a database transaction for extension-owned tables and certain SDK-supported core operations. It is powerful and intentionally warns that the caller must still scope its own SQL correctly.

**Data flow**: No direct input goes in → the method opens a workspace transaction and yields the raw async database connection to the caller → on normal exit it commits through the transaction manager, and on error it rolls back.

**Call relations**: The web extension’s audience code uses this to perform its own database work. Unlike ScopedStore, this hands over a broad connection, so it is for trusted extension code that follows the SDK rules.

*Call graph*: called by 2 (_gate, web_audience); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 543–552)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Starts an internal agent turn from an extension or job. It fails clearly if no turn invoker was provided.

**Data flow**: A conversation id, agent id, message, and idempotency key go in → the method checks that an invoker exists → it delegates to that invoker → it returns the resulting turn id.

**Call relations**: This method is the safe doorway from an extension context into the turn-running system. The actual admission checks and idempotency behavior are performed by the wired TurnInvoker implementation.


##### `ExtensionContext.page_states`  (lines 554–569)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads the current visibility subject and revision number for selected live pages in this workspace. A revision is a version counter that helps callers notice changes.

**Data flow**: A tuple of page ids goes in → if it is empty, an empty dictionary comes out → otherwise it queries non-tombstoned pages in the current workspace and builds PageState values → it returns a dictionary keyed by page id.

**Call relations**: This supports extensions that need to compare known pages with current page state. It uses the same workspace transaction path as other page and source operations.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 571–682)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Registers a content-sync source for the current workspace, such as a connected account, feed, or folder that a backend can poll. Re-registering the same authority returns the same source, but changing its owner or visibility without removing it first is rejected.

**Data flow**: A backend name, typed config model, subject, owner member id, and optional connection id go in → the config is converted to JSON data and a stable source id is computed → if a member connection is involved, the method verifies that connection belongs to this workspace, owner, provider, and account → it revives, returns, or inserts the source row as appropriate → it returns the source id.

**Call relations**: Sample and YC extensions use this during setup to declare sources for the sync driver. The method bridges extension setup code with core source tables while enforcing workspace and connection ownership checks.

*Call graph*: called by 2 (_setup, setup_sources); 7 external calls (now, model_dump, insert, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.sources`  (lines 684–724)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered sources in the current workspace, optionally for one backend. Removed sources are left out.

**Data flow**: An optional backend filter goes in → the method queries non-removed source rows for this workspace, ordered predictably → it converts each row to a SourceRecord → it returns a tuple of those records.

**Call relations**: The sources extension uses this to build bindings from extension-visible source state. It is the read-side companion to register_source and remove_source.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 726–771)

```
async def source_pages(self, subjects: frozenset[str] | None=None) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages in the current workspace, optionally limited to visibility subjects the caller is allowed to read. The page body itself is not returned, only its blob reference.

**Data flow**: An optional set of subjects goes in → the method queries non-tombstoned page rows in this workspace, applying the subject filter if present → it converts rows to PageRecord objects → it returns them as a tuple.

**Call relations**: This gives extensions a sanctioned read over core page metadata. It pairs with forget_page when a caller needs to inspect pages and then remove selected ones.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 773–789)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. Tombstoning means the row remains as a record, but downstream systems treat it as removed and clean up derived index data.

**Data flow**: A page id goes in → the method updates that page only if it belongs to the current workspace and is not already tombstoned → if no row matches, it raises ValueError → otherwise it records the tombstone and update time.

**Call relations**: This is the write-side companion to source_pages. The page-change pipeline later notices the tombstone and removes indexed chunks or other derived data.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 791–822)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones its live pages in one transaction. The source row stays around so existing page references still point to something, but the sync driver will not claim it again.

**Data flow**: A source id goes in → the method marks that live source as removed for the current workspace → if no live source matches, it raises ValueError → it then tombstones all live pages belonging to that source → nothing is returned.

**Call relations**: This is the removal path for sources created by register_source. By updating source and pages together, it lets the normal page-change cleanup flow clear derived index state.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 824–850)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the visibility subject for one or more live sources and their live pages together. A subject is the disclosure label used to decide who may see synced content.

**Data flow**: A tuple of source ids and a new subject go in → the method updates matching live source rows in the current workspace → if none match, it raises ValueError → it updates live pages from those sources with the same subject and a fresh timestamp → nothing is returned.

**Call relations**: This supports permission or audience changes for already-registered sources. Updating pages with a new timestamp causes downstream replay or indexing code to treat the subject change like a real page edit.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 852–858)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Submits a governed proposal to change an agent, rather than changing the agent directly. This adds an approval and safety layer around prompt edits.

**Data flow**: An AgentChange goes in → the method creates a Governance object for the current workspace and extension → it asks governance to open the proposal → it returns a ProposalRef identifying that proposal.

**Call relations**: The sample extension calls this during its tick flow. The method hands the actual approval workflow to Governance while stamping the proposal with the extension that suggested it.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 860–865)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns the workspace’s trajectory corpus through the context. It fails clearly if transcript reading was not wired for this handler.

**Data flow**: No direct input goes in → the method checks that corpus is present → if missing, it raises RuntimeError → otherwise it asks TrajectoryCorpus to load trajectories and returns them.

**Call relations**: The sample extension uses this when it wants conversation examples. This wrapper keeps handlers from mistaking an unwired corpus for an empty corpus.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 868–902)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: BlobStore | None=None, sandboxes: Conversa
```

**Purpose**: Builds the ExtensionContext object that a handler receives. It assembles only the capabilities that were declared or wired for this extension.

**Data flow**: The extension name, declared credential slots, optional services such as index, embedder, page feed, blob store, sandboxes, invoker, model resolver, scheduler, surfaces, and audience go in → the function creates scoped access objects around those services → it returns one ExtensionContext bundle.

**Call relations**: This is the factory used to give both extensions and core jobs the same shape of context. Optional inputs decide which doors exist: for example, a blob store creates a TrajectoryCorpus, sandboxes create ConversationFiles, and a model resolver creates ModelAccess.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/tools/registry.py`

`data_model` · `startup and request handling`

A “tool” here is something the AI model can ask the system to run, such as reading a page, searching, or calling an external service. This file gives each tool a clear record, called `ToolDef`, that says its name, what it does, what input shape it expects, and which async handler actually runs it. It also records safety labels: for example, `untrusted` means the tool output may contain attacker-controlled text, and `side_effecting` means the tool can make an outside change, like writing data or sending a request.

The file also defines `ToolRegistry`, which is the frozen catalog of all available tools. Think of it like a restaurant menu plus the kitchen routing slip: the model sees the menu descriptions and input schemas, while the engine uses the registry to find the right kitchen station when an order comes in.

One important detail is the reserved `requested_by` input field. Every tool schema is automatically given this field so a tool call can point back to the message that authorized it. The registry refuses tools that already define their own `requested_by`, because that would clash with this shared safety mechanism. It also refuses duplicate tool names, since two tools with the same name would make dispatch ambiguous.

#### Function details

##### `ToolDef.schema`  (lines 43–55)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the public tool description that can be sent to the model. It combines the tool’s name and human description with the JSON input schema produced from its Pydantic input model, then adds the shared `requested_by` field used for authority tracking.

**Data flow**: It starts with a `ToolDef`, reads its input model, and asks that model for a JSON schema, which is a machine-readable description of valid input. It then adds a required-looking shared property named `requested_by` to that schema and returns a `ToolSchema` containing the final name, description, and input schema. The original tool definition is not changed.

**Call relations**: This is used when the registry needs to present all available tools to the model. It hands the finished schema to `ToolSchema.__init__`, which packages the information into the format expected by the model interface.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 62–71)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the tool catalog is safe and unambiguous immediately after it is created. It prevents duplicate tool names and prevents tool input models from using the reserved `requested_by` field.

**Data flow**: It receives a newly created `ToolRegistry` with a tuple of tool definitions. It reads all tool names to find repeats, and it inspects each tool’s input model fields to detect any use of `requested_by`. If either problem exists, it raises a `ValueError`; otherwise, the registry is left ready to use.

**Call relations**: This runs automatically as part of constructing a `ToolRegistry`. It protects later flows, such as schema generation and tool dispatch, from confusing or unsafe definitions before the engine starts relying on the registry.


##### `ToolRegistry.schemas`  (lines 73–74)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the model-facing descriptions for every tool in the registry. The engine can use this when it needs to tell the model what tools are available and what inputs each one accepts.

**Data flow**: It starts with the registry’s tuple of `ToolDef` objects. For each tool, it calls that tool’s `schema()` method, collecting the resulting `ToolSchema` objects into a new tuple. Nothing in the registry is modified.

**Call relations**: This is the batch version of `ToolDef.schema`: rather than building one tool description, it builds the full menu of tool descriptions that can be placed on the wire to the model.


##### `ToolRegistry.get`  (lines 76–80)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the tool definition with a given name so the engine can run the correct handler. It fails loudly if the model or caller asks for a tool that is not in the catalog.

**Data flow**: It takes a tool name as text, scans the registry’s stored tool definitions, and returns the matching `ToolDef` when it finds one. If no tool has that name, it raises a `KeyError` saying the tool is unknown.

**Call relations**: During tool dispatch, `core/src/ufo/loop/engine._dispatch_segments` calls this to turn a requested tool name into the actual tool definition and handler. This is the point where a model’s tool request is matched against the approved registry rather than being trusted blindly.

*Call graph*: called by 1 (_dispatch_segments).


### Skill runtime checks
The runtime loads and prepares skill folders, while the sample probe verifies that an extension-provided skill can execute.

### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during request handling`

A skill is a folder that teaches the agent a reusable workflow. Its main file, SKILL.md, has a small metadata header plus the instructions the agent should read. This file turns those folders into Python objects, checks that their names and metadata make sense, and builds a registry: a lookup table of all skills available during a run.

The important idea is that loading a skill does two different things. First, it puts the skill’s written instructions into the model’s context, like handing someone a recipe card. Second, it mounts the skill’s files into the sandbox under `.skills/<skill-name>/`, like putting the recipe’s tools and ingredients on a shelf where the agent can reach them. If a skill depends on another skill, the registry pulls in that dependency too, but only through the explicit `depends` list. Nested folders are just a naming convention unless they declare that dependency.

The file also avoids wasting context space. `LoadedSkills` remembers which skill instructions are already visible to the model, so repeated loads still refresh the files in the sandbox but do not paste the same instructions again. This matters because model context is limited and repeated instructions would crowd out useful conversation history.

#### Function details

##### `RuntimeSkill.mounted_files`  (lines 59–60)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Builds the full set of files that should be copied into the sandbox for one skill. It includes the original SKILL.md exactly as written, plus any extra files bundled with the skill.

**Data flow**: It reads the skill’s stored raw SKILL.md text and its saved asset files. It turns SKILL.md into bytes and combines it with the asset file map. The result is a dictionary from relative file path to file contents.

**Call relations**: When `mount_skill` is ready to copy a skill into the sandbox, it calls this method to know exactly which files to write. This keeps mounting focused on writing files, while the skill object decides what belongs to the skill.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 62–63)

```
def mount_root(self) -> str
```

**Purpose**: Computes the sandbox folder where this skill should be placed. This gives every skill a predictable home under the workspace’s `.skills` directory.

**Data flow**: It reads the skill’s registry name and joins it with the shared skills mount directory. The result is a sandbox path such as `.skills/some-skill` or `.skills/parent/child`.

**Call relations**: Before `mount_skill` writes any files, it calls this method to find the destination root. The returned path is then combined with each mounted file’s relative path.

*Call graph*: called by 1 (mount_skill).


##### `LoadedSkill.prompt_body`  (lines 75–85)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the text that should be shown to the model for one loaded skill. It labels whether the skill was directly requested or was pulled in because another skill depends on it.

**Data flow**: It reads the wrapped `RuntimeSkill` and the optional `dependency_of` name. It builds a heading, adds a dependency note if needed, and appends the skill’s instruction body. The output is one text block for the model context.

**Call relations**: This is used as the per-skill building block for the larger loaded skill context. `loaded_context` gathers these blocks for every skill whose instructions are not already in context.


##### `LoadedSkills.reseed`  (lines 101–118)

```
def reseed(self, loads: Iterable[tuple[LoadedSkill, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the memory of which skill instructions are currently visible to the model. This prevents the system from pasting the same skill workflow repeatedly while still knowing which skills the agent originally asked for.

**Data flow**: It receives previous loaded skill groups and optional preloaded skills. It first clears the old tracking state, then records every skill whose workflow is present. Skills directly requested by the agent are also recorded in `asked_for`; preloaded skills count as visible but not agent-requested. The object’s two sets are updated in place.

**Call relations**: It calls `LoadedSkills.reset` before rebuilding the state. It is used when the system derives the tracker from the actual conversation window or prompt contents, rather than blindly trusting old in-memory state.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 120–125)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of skills the agent explicitly asked for, then clears the tracker. This is useful at a boundary where the detailed skill instructions may be dropped but the system wants to remember what should be reloaded later.

**Data flow**: It reads the `asked_for` set, sorts it into a stable tuple, clears both tracking sets, and returns the tuple of names.

**Call relations**: It calls `LoadedSkills.reset` after taking the names. It fits into flows such as compaction or summary handoff, where the system keeps only the skill names needed to rebuild context.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 127–129)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered loaded-skill state. It is the shared cleanup step for rebuilding or draining the tracker.

**Data flow**: It takes the current `in_context` and `asked_for` sets and empties both. It returns nothing, but the `LoadedSkills` object is changed in place.

**Call relations**: Both `LoadedSkills.reseed` and `LoadedSkills.drain` call this method so they do not duplicate the clearing logic. It is the small reset switch for the skill-context tracker.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 132–138)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a SKILL.md file into its metadata header and its instruction body. The metadata is used for loading decisions, while the body is what the model reads as instructions.

**Data flow**: It receives the full SKILL.md text. It checks that the text starts with a YAML frontmatter fence, finds the closing fence, and splits the text into metadata and body. If the format is missing or unfinished, it raises a clear error.

**Call relations**: `parse_skill_content` calls this before reading the metadata with YAML. This function is the format gatekeeper that makes malformed skill files fail early.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 141–146)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate subfolders that are themselves skills. A subfolder counts as a child skill only if it contains its own SKILL.md.

**Data flow**: It receives a skill directory path, looks through its direct children, keeps only directories containing SKILL.md, sorts them, and returns the list of paths.

**Call relations**: `parse_skill` uses this to avoid treating child-skill files as part of the parent’s asset bundle. `discover_skills` uses it to recursively register child skills under path-like names.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 149–182)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory collection of skill files into a validated `RuntimeSkill`. This lets skills loaded from bytes be checked the same way as skills read from disk.

**Data flow**: It receives a directory name, a mapping of relative file paths to bytes, and optional registry and parent names. It reads SKILL.md, splits its frontmatter from its body, parses the metadata as YAML, checks that the declared skill name matches the folder name, collects non-SKILL.md files as assets, and returns a `RuntimeSkill`. If required data is missing or wrong, it raises an error.

**Call relations**: `parse_skill` gathers files from disk and hands them here for the actual validation and object creation. This function calls `_split_frontmatter`, `yaml.safe_load`, and constructs the `RuntimeSkill` that later registries and mounting code use.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 185–194)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads a skill directory from disk and parses it into a `RuntimeSkill`. It makes sure the parent skill does not accidentally absorb files that belong to nested child skills.

**Data flow**: It receives a filesystem path and optional registry and parent names. It finds child skill directories, walks the directory tree, reads files that belong to this skill, excludes child-skill subtrees, and passes the collected bytes to `parse_skill_content`. The output is one validated `RuntimeSkill`.

**Call relations**: `discover_skills` calls this for each skill folder it encounters. It delegates child detection to `_child_skill_dirs` and validation to `parse_skill_content`.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 197–215)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning them in one flat lookup map. This lets the rest of the system ask for skills by name without walking folders again.

**Data flow**: It receives a skill directory and optional naming context. It parses the current directory as one skill, stores it by registry name, then finds each immediate child skill directory and recursively discovers it under a name like `parent/child`. The result is a dictionary from skill name to `RuntimeSkill`.

**Call relations**: `_load_core_skills` calls this for each core skill directory. Inside its recursion, it calls `parse_skill` for the current folder and `_child_skill_dirs` to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 218–225)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the built-in skills that ship with the core project. These are available without any external skill pack.

**Data flow**: It receives the root directory containing core skill folders. It lists visible directories, skips hidden or private-looking ones, discovers skills inside each, and merges them into one dictionary keyed by skill name.

**Call relations**: This function is run when the module is imported to create the core skill registry data. It calls `discover_skills` so top-level core skills and their children are all included.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 242–247)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up one skill by name and gives a helpful error if it does not exist. This keeps unknown skill requests from failing silently.

**Data flow**: It receives a skill name and checks the registry’s `by_name` dictionary. If found, it returns the matching `RuntimeSkill`. If not, it builds a readable list of available names and raises a `ValueError`.

**Call relations**: `SkillRegistry.closure` and its helper `SkillRegistry.closure.add` call this whenever they need to turn a requested name or dependency name into a skill object.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 249–272)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Figures out the full set of skills that should be loaded for one request. It includes the skills the agent named directly and every dependency they pull in, without duplicates.

**Data flow**: It receives one or more requested skill names. It first creates direct `LoadedSkill` entries for each unique requested name. Then it walks each requested skill’s dependencies, adding missing dependencies with a note about which skill pulled them. It returns an ordered tuple of `LoadedSkill` entries.

**Call relations**: The engine’s `_loaded_skill_closures` calls this when preparing skill loads. This method uses `SkillRegistry.named` for lookups and creates `LoadedSkill` wrappers; its nested `add` helper performs the dependency walk safely.

*Call graph*: calls 1 internal fn (named); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 262–267)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill and then adds that dependency’s own dependencies. It is written to avoid loading the same skill twice, even if dependencies form a cycle.

**Data flow**: It receives a `RuntimeSkill` and the name of the skill that depended on it. If the skill is already in the loaded map, it stops. Otherwise it records a `LoadedSkill` marked as a dependency, then looks up and adds each dependency named by that skill. The shared loaded map grows in place.

**Call relations**: This helper lives inside `SkillRegistry.closure` and is called during dependency expansion. It calls `SkillRegistry.named` for each dependency name and creates `LoadedSkill` entries that `closure` later returns.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 274–282)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Produces the public list of loadable top-level skills and their descriptions. This is the catalog shown to the model so it knows what skills it can ask for.

**Data flow**: It reads all skills in registry order, keeps only those without a parent, and returns pairs of skill name and description. Child skills are left out of this top-level index.

**Call relations**: The system prompt can render this result as the skill index. Child skills are meant to be reached through parent instructions or explicit dependency rules, not advertised as separate top-level catalog entries here.


##### `SkillRegistry.merged_with`  (lines 284–296)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a registry that includes saved user skills after the core and pack skills. It refuses user skills that try to reuse an existing name, so user-controlled content cannot replace trusted built-in or pack skills.

**Data flow**: It copies the current registry dictionary, then examines each user skill. If the name is new, it adds the user skill. If the name already exists, it logs a refusal and skips it. It returns a new `SkillRegistry` with the merged names.

**Call relations**: This is used when a workspace’s saved user skills need to be added to the active registry. It calls the logging system when a shadowing attempt is refused and constructs a new `SkillRegistry` for the result.

*Call graph*: 2 external calls (__init__, log).


##### `_mounted_tree`  (lines 302–320)

```
def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Creates a compact text tree showing all files mounted for a skill load. This tells the model where the skill files are without repeating long path prefixes for every file.

**Data flow**: It receives the loaded skill entries. It asks each skill for its mounted file paths, combines skill names with file paths, sorts them, and builds an indented directory tree under the shared `.skills` mount directory. The output is a human-readable string.

**Call relations**: `loaded_context` calls this at the end of the context block. The tree complements the written skill instructions by showing the file locations the agent can use in the sandbox.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 323–337)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the complete text shown to the model for one skill load. It includes new skill instructions, a note for instructions already present, and a tree of all mounted files.

**Data flow**: It receives the loaded skill entries and a collection of skill names already in context. It turns not-yet-present skills into prompt blocks, collects repeated skills into one short note, appends the mounted file tree, and returns one combined string.

**Call relations**: This is shared by normal skill loading and subagent preloading so skills look the same in both situations. It calls `_mounted_tree` to add the final file-location summary.

*Call graph*: calls 1 internal fn (_mounted_tree).


##### `mount_skill`  (lines 340–343)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Copies one skill’s files into the sandbox workspace. This makes SKILL.md and any bundled assets available to the agent as real files.

**Data flow**: It receives a sandbox session and a `RuntimeSkill`. It computes the skill’s mount root, gets all files that should be mounted, and writes each file into the sandbox at the matching path. It returns nothing, but the sandbox filesystem changes.

**Call relations**: When a skill load is being applied, this function performs the file-writing part. It calls `RuntimeSkill.mount_root`, `RuntimeSkill.mounted_files`, and the sandbox session’s `write_file` method to place the files where the model was told they would be.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files).


### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or health check`

This file acts like a quick “is the lightswitch working?” test for the sample skill. It does not define any reusable code or perform any deeper setup. Its whole job is to print the text `sample-skill-probe-ok` when Python runs the file. That message can be used by a larger tool, installer, or test process to confirm that the sample skill’s probe file is present, readable, and executable. Without this file, anything expecting a simple probe signal for the sample skill would have no easy confirmation that this part of the extension is wired up correctly. The important behavior is that the print happens immediately when the file is run or imported, because the statement is at the top level rather than inside a function.

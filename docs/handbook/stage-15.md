# Specialized Agent Workflows and Durable Skills  `stage-15`

This stage is shared support for “bigger than one turn” work. It lets the main agent call focused helper agents, keep useful skills, and manage long-running or multi-step jobs. The subagent core defines a default helper profile and the machinery to start, message, wait for, or cancel child agents. Extensions then add specialists: browser agents for web tasks, research agents for search and page fetching, website agents for building and publishing sites, and a brief pipeline that passes work from outline to draft to critique.

Several tools wrap these helpers so the main agent can delegate cleanly. Browser delegation runs one or many browsing jobs. Wide research runs many research jobs and saves one combined JSON result. Website delegation sends a build request to the site builder, while site tools run servers and publish safe links.

Other files give the agent durable working memory and scratch space. Skill creation saves user-written skills for later turns, with checks for names and ownership. Todos keep visible checklists. Scheduled tasks pause, wait, or recur. The REPL extension provides Python and JavaScript scratchpads. A sample skill probe simply proves a skill file can run.

## Files in this stage

### Durable Skill Management
User-authored skills are created, persisted, and probed so agents can reuse member-provided capabilities across turns.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `extension load, object requests, and runtime skill loading`

This file solves a practical problem: a skill written during a conversation should not disappear when the temporary workspace goes away. It describes how such skills are stored as named objects, how their files are checked, and how they are made available again later.

A saved skill is treated like a small folder of text files. It must include SKILL.md, and it may include extra bundled text files. The file supports three ways to submit file content: give the text directly, point to a workspace file with {from: ...}, or keep an already-stored file by sending back its sha256 digest. A sha256 digest is a fingerprint of the file contents; it lets the system confirm “this is the same file” without copying the whole body into the conversation.

The main worker is SkillObjects. It lists saved skills, returns safe metadata, reports status, applies changes, and deletes skills. Applying a skill resolves all file references into bytes, confirms every file is UTF-8 text, enforces file count and total size limits, and then saves it through UserSkillStore. When reading workspace files, it runs a small Python program inside the sandbox and returns contents as base64 text, which is a safe way to move raw bytes through JSON.

At the bottom, the file registers the object kind, the built-in authoring skill, and the runtime loader with the extension system.

#### Function details

##### `_require_ext`  (lines 92–95)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure a tool request really has the extension context attached. The extension context is the piece of information needed to find this agent’s saved skill store.

**Data flow**: It receives a ToolContext. If the context contains an ExtensionContext, it returns that. If it is missing, it stops immediately with a runtime error, because the rest of the skill storage code cannot work without it.

**Call relations**: The object operations call this before touching storage. Listing, getting, applying, deleting, and the shared file lookup all rely on it so they do not accidentally run without knowing which extension and agent they belong to.

*Call graph*: called by 5 (_files, apply, delete, get, list).


##### `_text`  (lines 98–104)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This checks that a stored skill file is plain UTF-8 text, not an image, archive, or other binary file. Skills are meant to be readable text bundles.

**Data flow**: It receives a skill-relative file path and that file’s raw bytes. It tries to decode the bytes as text. If decoding works, it returns the text; if not, it raises a clear error naming the file that is not valid text.

**Call relations**: SkillObjects._resolve calls this after gathering each file’s bytes, right before accepting the resolved skill. It acts like a final quality check before SkillObjects.apply saves anything.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 111–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of the saved skills for the current agent. It gives each skill’s name and a short summary so a user or tool can browse what exists.

**Data flow**: It receives the current tool context and a list query, such as paging information. It gets the extension context, loads all saved skills from UserSkillStore, turns each one into a small row with a name and shortened description, and returns an ObjectPage containing the requested slice.

**Call relations**: When the object system needs to show available skill objects, it calls this method. The method first uses _require_ext to confirm storage can be reached, then asks UserSkillStore for the skills, and finally hands the rows to object_page to shape the response.

*Call graph*: calls 1 internal fn (_require_ext); 3 external calls (__init__, __init__, object_page).


##### `SkillObjects.get`  (lines 119–137)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This returns details for one saved skill without exposing the full file contents. Instead of echoing files into the context, it returns fingerprints and sizes for each file.

**Data flow**: It receives a context and skill name. It loads the skill files, and if none exist it returns None. It also loads creation and update timestamps. For each file, it calculates a sha256 digest and records the size, then wraps that information in a UserSkillSpec inside an ObjectDetail.

**Call relations**: The object system calls this when someone asks to inspect a named skill object. It delegates file loading to SkillObjects._files, uses _require_ext so it can read timestamps from UserSkillStore, and builds FileRef values that can later be sent back to SkillObjects.apply to keep unchanged files.

*Call graph*: calls 2 internal fn (_files, _require_ext); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 139–153)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This gives a quick health summary for a saved skill. It reports the parsed description, number of files, and total byte size.

**Data flow**: It receives a context, a skill name, and an expected generation value that this implementation does not use. It loads the saved files. If the skill is missing, it returns None. Otherwise it parses the skill content to get the description, counts the files, sums their sizes, and returns those facts as a small dictionary.

**Call relations**: The object system calls this when it needs a lightweight status view rather than full object details. It uses SkillObjects._files to retrieve the saved bytes, then hands those bytes to parse_skill_content so the SKILL.md metadata is interpreted consistently with the runtime skill loader.

*Call graph*: calls 1 internal fn (_files); 1 external calls (parse_skill_content).


##### `SkillObjects.apply`  (lines 155–171)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a saved skill. It turns the submitted specification into actual file bytes, validates limits, and persists the result for future turns.

**Data flow**: It receives the context, target skill name, desired UserSkillSpec, the old spec if any, and an expected generation value that this code does not use. It rejects specs with too many files, resolves inline text, workspace references, and kept-file digests into bytes, checks the total size, and saves the completed file map through UserSkillStore along with the current built-in skill names.

**Call relations**: The object system calls this when a user applies a manifest for a skill object. It relies on _require_ext to find the correct store and on SkillObjects._resolve to do the careful reference-to-content conversion before UserSkillStore.save performs the persistence and validation against built-in skill shadowing.

*Call graph*: calls 2 internal fn (_resolve, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 173–181)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved skill for the current agent. After deletion, that skill will no longer be available as one of the agent’s persisted runtime skills.

**Data flow**: It receives the context, skill name, and an expected generation value that this implementation does not use. It gets the extension context and asks UserSkillStore to delete the saved record for that name. It returns no content.

**Call relations**: The object system calls this when a user deletes a skill object. The method uses _require_ext to identify the correct agent-owned store, then hands the deletion request to UserSkillStore.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._files`  (lines 183–185)

```
async def _files(self, ctx: ToolContext, name: str) -> dict[str, bytes] | None
```

**Purpose**: This is the shared helper for loading the raw stored files of one skill. It keeps the storage lookup in one place so get, status, and resolve all read saved content the same way.

**Data flow**: It receives a context and skill name. It extracts the extension context, opens the UserSkillStore for that extension, and asks for the files. It returns a dictionary of skill-relative paths to raw bytes, or None if the skill is not saved.

**Call relations**: SkillObjects.get and SkillObjects.status call this when they need to inspect a saved skill. SkillObjects._resolve also calls it when an update wants to keep an unchanged file by digest.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_resolve, get, status); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 187–235)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns a user-submitted skill spec into the exact bytes that should be saved. It is the careful middle step that proves kept files match their fingerprints, reads workspace files, accepts inline text, and rejects non-text files.

**Data flow**: It receives the context, skill name, and UserSkillSpec. First it loads any currently stored files. For each FileRef, it checks that the stored file exists and that its sha256 digest matches. For each FileFrom, it converts the workspace-relative path into a sandbox path, runs a sandbox Python reader, and decodes the returned base64 contents. For inline strings, it encodes the text. Each resulting file is checked as UTF-8 text, then the function returns a complete path-to-bytes dictionary.

**Call relations**: SkillObjects.apply calls this before saving a skill. Inside, it uses SkillObjects._files to get old content, workspace_path to map workspace references, the sandbox command to read referenced files, and _text to enforce the text-only rule.

*Call graph*: calls 2 internal fn (_files, _text); called by 1 (apply); 6 external calls (b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 262–264)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads the current agent’s saved skills so they can be added to the runtime skill registry. In plain terms, it makes previously saved skills available for use again.

**Data flow**: It receives an ExtensionContext. It opens the UserSkillStore for that context, loads all saved runtime skills, and returns them as a tuple-like collection expected by the extension system.

**Call relations**: The manifest registers this as the runtime skill provider. When the extension system is assembling the skills available for a turn, it calls this function and receives the saved skills from UserSkillStore.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 267–274)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest, which is the extension’s registration card. It tells the host system the extension name, version, object type, built-in authoring skill, and how to load saved runtime skills.

**Data flow**: It takes no inputs. It creates a Manifest containing the skill_create name and version, the SKILL_OBJECT object definition, a SkillSpec pointing to the bundled create-skill authoring skill directory, and the _runtime_skills loader. It returns that Manifest to the extension host.

**Call relations**: The extension loader calls this when it discovers the package. The returned Manifest is what connects the object operations in SkillObjects, the bundled authoring workflow skill, and the saved-skill runtime loader into the larger system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and cross-turn skill persistence`

This file is the storage cabinet for skills that a user or agent creates at runtime. A “skill” is saved as a small directory of files, such as a SKILL.md file plus any extra assets. Because the database stores text more easily than raw file bytes, each file is turned into base64 text, which is a safe text form of binary data, then packed into JSON.

The main class, UserSkillStore, always works inside the current workspace and agent, found through agent_current(). That prevents one agent’s saved skills from leaking into another agent’s view. When saving, it first checks that the skill name is a safe lowercase slug, like “summarize-report”. It refuses names with slashes, dots, spaces, uppercase letters, or other risky characters. It also prevents a user skill from replacing a built-in skill unless this agent already owns that saved name. Finally, it enforces a maximum number of saved skills per agent.

The file can save, load, fetch files for, delete, and read timestamps for saved skills. Loading is intentionally forgiving: if one stored skill is corrupt, it logs a warning and continues loading the rest, so one bad record does not break the whole agent.

#### Function details

##### `UserSkillStore.save`  (lines 69–126)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves one user-created skill for the current agent, after checking that it is safe and allowed. It returns the parsed runtime version of the skill so the caller can use it immediately.

**Data flow**: It receives a skill name, a mapping of file paths to file bytes, and the names already present in the skill registry. It reads the current workspace and agent, validates the name, parses the files into a RuntimeSkill, checks whether this agent already owns that name, checks the per-agent skill limit if this is a new skill, converts file bytes to base64 text, calculates a SHA-256 digest as a content fingerprint, then updates an existing database row or inserts a new one. The database is changed, and the parsed RuntimeSkill comes back to the caller.

**Call relations**: This is the central write path for this store. During the save story it calls UserSkillStore._owns to tell whether this is replacing one of the agent’s own skills, and UserSkillStore._count to enforce the maximum only when adding a new skill. It also hands the files to parse_skill_content before persisting, so invalid skill contents are rejected before they become stored data.

*Call graph*: calls 2 internal fn (_count, _owns); 10 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, agent_current, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 128–162)

```
async def load_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved skill that belongs to the current agent. It is used when the agent needs its user-authored skills restored into the runtime skill list.

**Data flow**: It reads the current workspace and agent, selects all matching saved skill records from the database, and walks through them in name order. For each row, it validates the stored JSON, decodes each base64 file back into bytes, and parses those files into a RuntimeSkill. Good skills are collected into a tuple; corrupt or unreadable skills are skipped after writing a warning log.

**Call relations**: No in-file caller is shown, but this is the store’s bulk read path. It depends on agent_current for the correct scope, SQL selection to fetch rows, base64 decoding to recover file bytes, and parse_skill_content to turn stored files back into usable runtime skills.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 164–180)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the raw files for one saved skill owned by the current agent. This is useful when another part of the system needs to inspect, edit, or display the saved skill contents rather than run the parsed skill.

**Data flow**: It receives a skill name and reads the current workspace and agent. It looks for one matching database row. If no row exists, it returns None. If it finds one, it validates the stored JSON, decodes each base64 file body back into bytes, and returns a dictionary from relative file path to file bytes.

**Call relations**: No in-file caller is shown, but this is the single-skill file retrieval path. It uses agent_current to avoid crossing workspace or agent boundaries, then relies on SQL selection and base64 decoding to rebuild the saved files.

*Call graph*: 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 182–191)

```
async def delete(self, name: str) -> None
```

**Purpose**: Removes one saved user skill for the current agent. It lets the system clean up skills the agent no longer wants or needs.

**Data flow**: It receives a skill name and reads the current workspace and agent. It sends a database delete for the row with that workspace, agent, and name. It returns nothing; the visible effect is that the stored skill is gone if it existed.

**Call relations**: No in-file caller is shown, but this is the removal path for user skills. It uses agent_current to target only the current agent’s record, then hands the actual deletion to SQLAlchemy’s delete operation inside a transaction.

*Call graph*: 2 external calls (delete, agent_current).


##### `UserSkillStore.timestamps`  (lines 193–205)

```
async def timestamps(self, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Returns when a saved skill was first created and last updated. This helps callers show history or decide whether a saved skill has changed.

**Data flow**: It receives a skill name and reads the current workspace and agent. It queries the database for the matching row’s created_at and updated_at values. If the skill is not found, it returns None; otherwise it returns the two datetime values.

**Call relations**: No in-file caller is shown, but this is the metadata lookup path. It uses agent_current for scope and a SQL select to fetch only the two timestamp fields instead of loading the whole saved skill.

*Call graph*: 2 external calls (select, agent_current).


##### `UserSkillStore._count`  (lines 207–219)

```
async def _count(self) -> int
```

**Purpose**: Counts how many user skills the current agent has saved. It exists mainly to enforce the per-agent maximum before adding another skill.

**Data flow**: It reads the current workspace and agent, asks the database to count matching rows in the user_skill table, and returns that count as an integer. It does not change stored data.

**Call relations**: UserSkillStore.save calls this only when the skill being saved is new to the agent. The count then decides whether saving would exceed the configured cap and should be refused.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


##### `UserSkillStore._owns`  (lines 221–233)

```
async def _owns(self, name: str) -> bool
```

**Purpose**: Checks whether the current agent already has a saved skill with a given name. This matters because re-saving your own skill is allowed, but creating a new skill that shadows a built-in skill is not.

**Data flow**: It receives a skill name, reads the current workspace and agent, and queries the database for a matching saved skill row. It returns true if such a row exists and false otherwise. It does not change stored data.

**Call relations**: UserSkillStore.save calls this near the start of the save process. The answer controls two later choices: whether a registry-name collision is allowed as an update to an existing user skill, and whether the save should count as adding a new skill toward the limit.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or health check`

This file acts like a very small health check. It does not load settings, call other code, or perform any real skill behavior. Its whole job is to say “sample-skill-probe-ok” when Python runs it. That makes it useful for tests, installers, or discovery tools that need a quick way to confirm that the sample skill can be reached and executed. Think of it like ringing a doorbell: the printed message is the chime that proves someone pressed the right button and the wiring works. Without this file, any process expecting this exact probe output would have no simple confirmation that the sample skill’s probe path is working.


### Subagent Foundations
Core helper-agent profiles and orchestration support general delegation and fixed multi-step writing pipelines.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent setup and dispatch`

This file is like the job description for the system’s standard helper agent. When the main agent delegates a self-contained task and no special subagent type is requested, this profile is used as the safe default.

The file gives the profile a name, `general_purpose`, and defines which tools the helper may use. It can read and write files, search, edit, run shell commands, load skills, and use some optional extension tools if they are installed. It is deliberately not allowed to do certain higher-level actions: it cannot ask the human user questions, create more subagents, control sibling subagents, or approve account connections. That keeps the helper focused and prevents delegation chains or permission decisions from spreading to child agents.

It also defines two small data shapes using Pydantic, a validation library that checks whether data has the expected fields. `GeneralPurposeInput` expects a task string, and `GeneralPurposeOutput` returns a result string.

The prompt text tells the helper how to behave: work independently, load useful skills first, avoid repeated failing attempts, save useful artifacts in the shared workspace, and report back briefly. Finally, all of this is bundled into `GENERAL_PURPOSE_PROFILE`, then exposed as the one built-in core subagent profile.


### `core/src/ufo/loop/subagents.py`

`orchestration` · `request handling`

A subagent is like sending a specialist to do a side task while the main agent keeps its own place. This file defines the registry of available specialist profiles, then provides the workflow for spawning one as a child turn in the database and queue system. Without this file, the main agent could not safely delegate work, reconnect to delegated work after a retry, or trust that the child returned data in the expected shape.

The flow starts with a SubagentProfile, which names the subagent, its instructions, allowed tools, input shape, and output shape. SubagentRegistry keeps these profiles unique and finds them by name. subagent_system_prompt builds the instructions the child model will see. It fills in the skill list, adds any preloaded skill text, adds shared citation and formatting rules, and ends with a strict rule: the child must finish by calling the finish tool with structured output.

Subagents is bound to a parent turn. Its spawn method validates the requested input, creates or reuses a child conversation and turn, enqueues that turn for execution, and either returns the child turn id immediately or waits until the child finishes. Background subagents can later be waited on, cancelled, or sent follow-up messages. The file is careful about retry safety: with a deduplication key, repeating the same spawn or message reconnects to the already-created turn instead of creating a duplicate.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 79–83)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the registry does not contain two subagent profiles with the same name. This prevents an ambiguous situation where asking for one profile name could mean two different sets of instructions and tools.

**Data flow**: It reads the profile names stored in the registry → looks for any name that appears more than once → either leaves the registry unchanged or raises an error listing the duplicate names.

**Call relations**: This runs automatically when a SubagentRegistry is created. It protects later lookups, especially Subagents.spawn, from choosing the wrong profile.


##### `SubagentRegistry.get`  (lines 85–92)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Finds a subagent profile by name. If the name is not registered, it fails clearly and says which profile names are valid.

**Data flow**: It takes a requested name → scans the stored profiles → returns the matching profile, or raises UnknownSubagentProfile with a helpful message.

**Call relations**: Subagents.spawn uses this before creating a child turn, and _untrusted_output uses it when reporting background results. The clear failure keeps unknown or removed subagent names from silently running with the wrong behavior.

*Call graph*: 1 external calls (__init__).


##### `subagent_system_prompt`  (lines 95–127)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full instruction text for a subagent. It combines the profile’s own prompt, the available skill list, optional preloaded skill instructions, shared citation rules, and the final requirement to return structured output through the finish tool.

**Data flow**: It receives a profile, a list of skills, and optional preloaded skill bodies → fills the skill-index placeholder, checks that no prompt placeholders were left unresolved, checks that preloaded text is not too large, and appends the required output rules → returns one complete system prompt string.

**Call relations**: This is used when a subagent turn is prepared for model execution. It calls the prompt-rendering helpers to build the skill index and find unresolved slots, and it calls loaded_context to turn preloaded skills into prompt text.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 141–142)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Returns a copy of the Subagents helper that acts on behalf of a specific audience member. This is used when a request needs to be tied to the person who asked for it, rather than only to the parent turn.

**Data flow**: It takes an optional requester member id → copies the current Subagents object with that id filled in → returns the new copy without changing the original.

**Call relations**: Callers use this before spawning, messaging, or checking child turns when the operation should be associated with a specific member. It relies on dataclasses.replace to make the safe copy.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 145–146)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Chooses which member id should be treated as responsible for subagent actions. A directly authorized requester wins; otherwise it falls back to the member recorded on the parent turn.

**Data flow**: It reads requester_member_id and the parent turn’s on_behalf_of_member_id → returns the requester id if present, otherwise the parent’s member id.

**Call relations**: Spawn, message, and child-validation code use this value to stamp new turns and to prevent one member’s deduplication key or child turn from being reused by another member.


##### `Subagents.spawn`  (lines 148–192)

```
async def spawn(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: Starts a child subagent turn, either in the foreground where the parent waits for the result, or in the background where the parent receives the child turn id immediately. It validates both the input sent to the child and, for foreground runs, the output returned by the child.

**Data flow**: It receives a profile name, input payload, background flag, and optional deduplication key → looks up the profile, validates the payload against the profile’s input model, chooses a child conversation id, creates or reuses the child turn, enqueues it if needed, and then either returns the child id or waits for completion → returns a SpawnResult containing the child id and, when foreground, the validated output.

**Call relations**: This is the main entry point for starting subagents. It uses SubagentRegistry.get to resolve the requested profile, _admit to record the child in durable storage, _enqueue to submit it to the turn queue, and _await_terminal when the parent wants the finished result.

*Call graph*: calls 3 internal fn (_admit, _await_terminal, _enqueue); 5 external calls (__init__, __init__, turn_id_for, uuid4, uuid5).


##### `Subagents.wait`  (lines 194–213)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits for one or more background subagents to finish and reports their final status and text. This lets a parent come back later to collect results from children it started earlier.

**Data flow**: It receives child turn ids → verifies each one really belongs to this parent, waits until each has a terminal result, marks whether that child’s output should be treated as untrusted, and builds status objects → returns a tuple of SubagentStatus values.

**Call relations**: This completes the background-spawn loop after spawn has returned early. It calls _require_child to enforce ownership, _await_terminal to poll for completion, and _untrusted_output to preserve the profile’s trust rules in the returned status.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 215–232)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a child subagent turn that this parent started, then reports the child’s resulting status. It refuses to cancel arbitrary turn ids, which protects unrelated work.

**Data flow**: It receives a child turn id → verifies it is this parent’s child, asks the shared cancellation routine to cancel it, reads the turn’s stored status and terminal frame from the database, and extracts final text if present → returns a SubagentStatus.

**Call relations**: Callers use this when a background child is no longer needed. It shares ownership checking with wait and message through _require_child, and delegates the actual durable cancellation work to cancel_one_turn.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 234–336)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child subagent by creating the next turn in that child’s own conversation. This continues the same subagent thread instead of starting a fresh specialist from scratch.

**Data flow**: It receives a child turn id, message text, and deduplication key → verifies the child belongs to this parent, locks the child conversation, finds an existing follow-up with the same key or creates the next sequence-numbered turn, checks whether earlier queued work must run first, and enqueues the follow-up when appropriate → returns the follow-up turn id and status.

**Call relations**: This is used after a background spawn when the parent wants to give the child more information or a next instruction. It calls _require_child for safety and _enqueue only when the new follow-up is ready to be dispatched.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._untrusted_output`  (lines 338–344)

```
def _untrusted_output(self, profile: str) -> bool
```

**Purpose**: Decides whether a child’s output should be treated as untrusted content. If the profile is missing, it chooses the safer answer and marks the output untrusted.

**Data flow**: It receives a profile name → tries to find that profile in the registry → returns the profile’s untrusted_output setting, or returns true if the profile no longer exists.

**Call relations**: Subagents.wait uses this when packaging background child results. The conservative fallback prevents removed or unknown profiles from being treated as safe by accident.

*Call graph*: called by 1 (wait).


##### `Subagents._require_child`  (lines 346–363)

```
async def _require_child(self, turn_id: UUID) -> str
```

**Purpose**: Checks that a turn id belongs to a subagent spawned by this exact parent turn and acting member. This is a guardrail against reading, cancelling, or messaging someone else’s work.

**Data flow**: It receives a turn id → reads that turn’s parent id, subagent profile, and on-behalf-of member id from the database → returns the subagent profile if all checks match, or raises an error if the turn is missing or does not belong here.

**Call relations**: wait, cancel, and message all call this before touching a child turn. It is the shared safety check for all operations on existing background subagents.

*Call graph*: called by 3 (cancel, message, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 365–437)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str) -> bool
```

**Purpose**: Records a newly spawned child conversation and its first turn in the database, or reconnects to the existing one if this spawn is being retried. This makes spawning durable and safe after crashes or repeated tool execution.

**Data flow**: It receives the child conversation id, child turn id, profile name, and serialized input → inserts the child conversation and queued first turn if they do not already exist, checks that the existing or new turn belongs to the same acting member, and marks it as ready for dispatch if still queued → returns true when the caller should enqueue it, false when it has already moved past the queued state.

**Call relations**: Subagents.spawn calls this before queueing work. It prepares the database records that _enqueue will submit to the workflow queue, and it uses the current trace information so the child’s work can be connected back to the parent in observability tools.

*Call graph*: called by 1 (spawn); 5 external calls (select, update, audience_member, workspace_tx, current_traceparent).


##### `Subagents._enqueue`  (lines 439–468)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Submits a child or follow-up turn to the durable turn queue for execution. If queue submission fails, it clears the dispatch marker so another recovery path can try again later.

**Data flow**: It receives a turn id and conversation id → builds queue options including workflow name, workflow id, partition key, and app version → asks the DBOS client to enqueue the turn; if cancellation or another error happens, it updates the database to show the turn was not successfully dispatched, and logs non-cancellation errors.

**Call relations**: Subagents.spawn calls this for first child turns, and Subagents.message calls it for follow-up turns. It is the bridge between database admission and actual queued execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 470–480)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a turn has a terminal frame, meaning it has finished, failed, or otherwise reached its final recorded outcome. It polls the database at a short interval.

**Data flow**: It receives a turn id → repeatedly reads the turn’s terminal field from the database → if the terminal field is still empty, sleeps briefly and checks again; once present, converts it into a TerminalFrame and returns it.

**Call relations**: Foreground spawn uses this to wait for a child’s final answer, and wait uses it to collect background child results. It is the common “watch until done” loop for subagent turns.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / pipeline setup`

This file is the recipe card for a simple three-step writing pipeline. The parent agent is expected to run the steps in order: ask one subagent for an outline, pass that outline to another subagent to write a draft, then pass the draft to a final subagent for review. Each subagent is “toolless,” meaning it cannot call extra tools or start more agents itself; it only reads its prompt and input, then returns a structured answer. That keeps the chain predictable, like an assembly line where each worker does one job and hands the result to the next station.

The file uses Pydantic models, which are Python classes that describe and check structured data. For example, the outline stage receives a topic and audience, and must return text under an `outline` field. These models make the handoff between stages explicit, so later code can rely on fields being present instead of guessing from free-form text.

At the bottom, the file builds three `SubagentProfile` objects. Each profile names the stage, loads the matching instruction prompt from the local `prompts` folder, says that no tools are allowed, sets the expected input and output models, and limits the stage to four rounds. Without this file, the brief pipeline would not know what subagents exist, what prompts they should use, or what shape their inputs and outputs must have.


### Browser and Research Delegation
Specialized browsing and research workflows hand web tasks to focused subagents backed by safe host-side search and fetch tools.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file exists so the main agent does not have to drive a browser directly. Instead, it gives a clear job to a specialized browser subagent, waits for the result, and returns a clean summary. Think of it like a project manager sending work to a web researcher: the manager gives the task, the researcher uses their own browser workspace, and the manager receives the finished notes.

The single-task tool, `browser_task`, starts a fresh browser session with a URL, a task description, and a friendly task name. It runs the browser agent in the background, waits only up to the requested time limit, and cancels the work if it takes too long. This matters because broken websites or stuck automation loops should not freeze the parent agent forever.

The batch tool, `wide_browse`, reads a workspace file containing URLs or site names, removes blank lines and duplicates, and sends each item to the browser agent. It limits how many browser jobs run at once, so the system does not overload itself. It can also attach an output JSON schema, meaning a description of the expected result shape, so each browser job knows what kind of structured data to return. Finally, it writes all collected results to `wide_browse.json` in the workspace and returns both the rows and the output filename.

#### Function details

##### `_browser_task`  (lines 93–122)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job through the browser subagent. It is used when the main agent needs a website visited, a form filled, information extracted, or another multi-step browser action completed without controlling the browser itself.

**Data flow**: It receives the current tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, timeout, and user-facing description. It spawns a browser subagent with the URL and task details, then waits for that subagent to finish within the allowed time. If the job times out or is cancelled, it returns an error-style tool result saying the browser task was cancelled; if the subagent finishes successfully, it parses the browser result text into a validated browser result and returns it as text.

**Call relations**: This is the handler behind the `browser_task` tool definition. When that tool is called, `_browser_task` uses the shared tool context to spawn a child browser turn. It relies on the browser subagent to do the actual web work, then converts the finished subagent response into the parent tool result. It also creates `TextContent` and `ToolResult` objects for the final response.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 125–138)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. It is used by the batch browsing tool to get the URLs or site names that should be visited.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for a shell command, asks the sandbox to run `cat` on that file, and checks whether reading succeeded. It then trims each line, skips empty lines, removes duplicates while keeping the original order, and returns the resulting list of strings.

**Call relations**: This is a helper for `_wide_browse`. Before `_wide_browse` can start browser jobs, it calls `_read_lines` to turn the user-provided entities file into a trustworthy list of work items. `_read_lines` hands that cleaned list back to `_wide_browse`, which then fans it out to browser subagents.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 141–168)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs browser automation over many URLs or site names and gathers the results into one JSON output file. It is used when the agent needs the same kind of information collected from many websites or entities.

**Data flow**: It receives the current tool context and a `WideBrowseInput` with an entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates the entities, rejects the job if there are too many, reads the optional output schema, and creates a semaphore, which is a gate that limits how many jobs may run at the same time. It then launches one visit task per entity, waits for all of them to finish, writes the collected rows to `wide_browse.json`, and returns a tool result containing the rows and the output filename.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It first calls `_read_lines` to prepare the list of entities, then uses its nested `visit` function for each browser job. It uses `asyncio.gather` to wait for all visits, `json.dumps` to format the results, and returns the final data through `TextContent` and `ToolResult`.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 149–162)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser subtask for one entity inside a wider batch browse. It turns a single URL or site name into a specific browser prompt, sends it to the browser subagent, and packages that one result as a row.

**Data flow**: It receives one entity string from the surrounding `_wide_browse` function. It waits for permission from the shared semaphore so only a limited number of browser jobs run at once, fills `{entity}` in the prompt template, optionally appends the requested JSON schema, and spawns a browser subagent for that one entity. It returns a dictionary containing the entity and the serialized browser output, or an empty string if no output was returned.

**Call relations**: This helper lives inside `_wide_browse` because it depends on that function’s prompt template, schema text, semaphore, and tool context. `_wide_browse` starts many `visit` calls at once through `asyncio.gather`; each `visit` hands one browser task to the browser subagent and gives its row back to the batch collector.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup / subagent registration`

This file is like an ID card and rule sheet for a browser-focused child agent. The larger system can hand a web task to this subagent instead of letting the main agent do everything itself. That keeps browser work contained: the subagent gets browser tools, a few file tools for saving notes or screenshots, and its own prompt explaining how it should work.

The file starts by loading a Markdown prompt from `prompts/subagent_browser.md`. That prompt is the browser subagent’s operating instructions. It then builds the list of tools the subagent may use: all browser tools from the browser extension, plus simple workspace tools such as `read`, `write`, `edit`, and `search_web`.

Two small data models describe the conversation boundary. `BrowserTask` says what the parent agent can send in: the task text, an optional starting URL, and an optional task name. `BrowserResult` says what comes back: a result string. These models are made with Pydantic, a library that checks data has the expected shape.

Finally, `BROWSER_PROFILE` combines the name, prompt, tools, input model, and output model into a `SubagentProfile`. The `untrusted_output=True` setting is important: it warns the rest of the system to treat the subagent’s returned text carefully, because web pages may contain misleading or hostile content.


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file solves the problem of doing the same research task over a long list of companies, people, topics, or other entities. Instead of asking one research agent to work through the list one by one, it fans the work out to several research subagents in parallel, like giving a stack of names to a small team instead of one person.

The tool expects an input file with one entity per line. It reads that file safely through the sandbox, removes blank lines and duplicates, and refuses lists that are too large. It also optionally reads an output schema file, which is a template describing the shape of the answer the subagents should return.

For each entity, the tool fills `{entity}` into a prompt template, adds the schema instructions if present, and spawns a child research run using the shared research profile. A semaphore, which is a simple limit on how many tasks may run at the same time, keeps the fan-out from becoming too large. Each child gets a deterministic deduplication key based on the parent call and the entity name. That matters for crash recovery: if the parent run is retried, already-started or completed children can be reused instead of duplicated.

When all children finish, their outputs are collected into `wide_research.json` in the workspace, and the tool returns both the rows and the output filename.

#### Function details

##### `_read_lines`  (lines 42–55)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads an entity list file from the sandbox and turns it into a clean list of unique, non-empty lines. It protects the shell command by quoting the path before running `cat`.

**Data flow**: It receives a tool context and a file path. It asks the sandbox to read the file, checks whether that command failed, then walks through the file line by line. Blank lines are dropped, repeated entries are skipped, and the remaining entries come out as a list of strings.

**Call relations**: This is the first helper used by `_wide_research`. Before any subagents are spawned, `_wide_research` calls this function to turn the user-provided entities file into the exact set of research targets.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 58–85)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: Runs the main wide research workflow. It reads the inputs, starts several research subagents in parallel, gathers their answers, writes a combined JSON file, and returns a tool result pointing to that file.

**Data flow**: It receives the tool context and structured user arguments: the entities file, prompt template, optional schema file, and description. It reads and validates the entity list, reads the schema text if available, creates a limit for parallel work, then launches one `visit` task per entity. After all visits finish, it writes the collected rows to `wide_research.json` and returns those rows plus the output filename as text content inside a tool result.

**Call relations**: This is the handler attached to the `WIDE_RESEARCH_TOOL` definition, so the tool system calls it when someone invokes `wide_research`. It calls `_read_lines` to prepare the target list, uses `asyncio.gather` to wait for all per-entity visits, and uses the tool context to spawn the actual research subagents and write the final file.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_research.visit`  (lines 66–79)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Researches one entity as part of the larger batch. It builds that entity's prompt, asks a research subagent to do the work, and returns one row for the final output table.

**Data flow**: It receives one entity name from the surrounding `_wide_research` run. It waits for permission from the semaphore so only a limited number of visits run at once, fills the entity into the prompt template, appends schema instructions if there is a schema, and spawns a research subagent with a stable deduplication key. It returns a dictionary containing the entity name and the subagent's serialized result text, or an empty string if there was no output.

**Call relations**: This inner function is created and used only inside `_wide_research`. `_wide_research` starts one `visit` for each entity and then gathers all of their returned rows into the final JSON file.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / agent profile registration`

This file is like a job description and toolkit list for research assistants inside the larger agent system. Instead of letting every part of the system browse the web or use files in an ad hoc way, it creates two named subagent profiles: `research` for focused research tasks, and `deep_research` for more involved work that may need many steps and sources.

Each profile has a prompt, which is the written instruction sheet the subagent follows. Those prompts are loaded from nearby Markdown files. Both profiles use the same set of allowed tools: web search, URL fetching, a vertical search tool, a browser task tool, external tool access, file tools, memory search, and spreadsheet support. This matters because it draws a boundary around what a research subagent can do. For example, it can ask the browser subagent to perform browser work through `browser_task`, but it does not get the raw browser controls directly.

The file also defines simple input and output shapes using Pydantic models. A caller gives the subagent an `objective`, and the subagent returns a `result`. Finally, it builds `SubagentProfile` objects that the rest of the system can register or call when it wants research done. The deep version is almost the same, but gets a much larger round limit so it can keep working longer.


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `tool invocation during a model turn`

This file is the bridge between an agent asking for outside information and the configured search backend that can provide it. Without it, the agent would not have a standard way to say “search the web for this,” “read this URL,” or “find images/videos/papers/products,” and it would be easier to leak details like search API keys into the wrong place.

The file defines three tool inputs using Pydantic models, which are data shapes that check arguments before a tool runs. `SearchWebInput` accepts up to five short search queries plus optional filters. `FetchUrlInput` accepts a public HTTP or HTTPS URL and optional instructions for extracting information. `SearchVerticalInput` asks for a specific kind of result, such as images or shopping listings.

The actual tool functions all start by finding the search provider for the current turn. If there is no provider, they fail clearly instead of pretending search worked. Web search runs each query separately, gathers all hits, and turns them into JSON text for the model. URL fetching first checks whether the provider supports page fetching; if not, it returns a helpful error telling the agent to use other tools. When a page is fetched, the response includes a strong provenance warning: the page came through the provider’s crawler session, not the user’s workspace session. Like borrowing someone else’s browser, any logged-in context belongs to that crawler, not to you.

#### Function details

##### `_provider`  (lines 120–123)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This small helper gets the search provider for the current tool call. It exists so every research tool fails in the same clear way when no search backend has been configured.

**Data flow**: It receives the current `ToolContext`, which is the tool call’s surrounding information. It reads `ctx.search_provider`; if one is present, it returns it, and if it is missing, it raises an error saying no search provider is configured for this turn.

**Call relations**: The three tool handlers call this first before doing any search or fetch work. `_search_web`, `_fetch_url`, and `_search_vertical` all rely on it as the gatekeeper that either hands them a usable provider or stops the operation early.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 126–140)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This helper turns search results into a JSON string the model can read consistently. It keeps the output format the same for normal web search and specialized vertical search.

**Data flow**: It receives a list of search hits and an optional direct answer from the provider. It copies each hit’s main fields, such as URL, title, snippet text, publication date, and highlights, into plain dictionaries, adds the optional answer if there is one, and returns the whole package as JSON text.

**Call relations**: After `_search_web` or `_search_vertical` gets results from the provider, they call this helper to package those results. The helper hands back text that those functions wrap in `TextContent` and return as a `ToolResult`.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 143–158)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It runs one or more ordinary web searches and returns a combined list of result links and snippets.

**Data flow**: It receives the tool context and validated search arguments, including the list of queries, optional recency filter, and optional allowed domains. It gets the configured provider, sends each query to that provider with a default result count, collects all returned hits, keeps the first provider-supplied answer if one appears, converts everything to JSON, and returns it as tool text.

**Call relations**: When the agent calls the public `search_web` tool, this function is the worker that runs. It first asks `_provider` for the backend, then builds `SearchQuery` requests for the provider, then passes the gathered results to `_results_json` before returning a `ToolResult`.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


##### `_fetch_url`  (lines 161–180)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It asks the search provider’s crawler to read a public web page and returns the page text, optionally with a summary.

**Data flow**: It receives the tool context and validated fetch arguments, including the URL, optional prompt, optional maximum length, and cache-bypass flag. It gets the provider, checks whether that provider can fetch pages, and if not returns an error message. If fetching is supported, it sends a `FetchRequest`, then returns JSON containing the final URL, page text, a crawler provenance warning, and a summary if the provider supplied one.

**Call relations**: When the agent calls `fetch_url`, this function coordinates the whole operation. It uses `_provider` to find the backend, hands a `FetchRequest` to that backend, and wraps the fetched page into `TextContent` and `ToolResult`. Unlike the search functions, it formats its own JSON because fetched pages have different fields and must include the crawler provenance warning.

*Call graph*: calls 1 internal fn (_provider); 4 external calls (__init__, __init__, __init__, dumps).


##### `_search_vertical`  (lines 183–190)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It searches within a specific kind of content, such as images, people, academic papers, videos, or shopping listings.

**Data flow**: It receives the tool context and validated arguments containing a vertical name and a short query. It gets the provider, sends a search request that includes the chosen vertical, converts the returned hits and optional answer into JSON, and returns that JSON as tool text.

**Call relations**: When the agent calls `search_vertical`, this function performs the specialized search. It follows the same pattern as `_search_web`: get the provider with `_provider`, call the provider with a `SearchQuery`, format results through `_results_json`, then return a `ToolResult`.

*Call graph*: calls 2 internal fn (_provider, _results_json); 3 external calls (__init__, __init__, __init__).


### Scratchpads and Conversation State
REPL scratchpads, scheduled waits, and todo checklists give agents durable working memory and workflow control during conversations.

### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `startup for registration, then active during REPL tool calls`

This file gives the agent a safe, repeatable way to experiment with code while solving tasks. A REPL is an interactive coding session; here it is made persistent by saving every successful block of code into a workspace file. The next run replays that saved code plus the new code, so variables and imports can carry forward like notes left on a desk. If a run fails, its code is not saved, which prevents broken definitions from poisoning later attempts.

There are two tools. `js_repl` runs JavaScript with Node.js, mainly for browser automation and visual website testing. It prepares a small helper called `emitImage`, so JavaScript code can return screenshots or generated images back to the agent. It also creates links to globally installed Node packages so normal imports, such as installed browser tools, can work. `xlsx_repl` runs Python for Excel work with `openpyxl`; if the code sets a variable named `result`, the tool prints it as JSON.

All execution happens through `ctx.sandbox`, meaning code runs in the controlled container rather than on the host machine. The file packages stdout, stderr, exit status, and any emitted images into tool results, then exposes everything through a `manifest()` that the extension system can load.

#### Function details

##### `global_modules_link`  (lines 54–72)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because ES modules do not automatically search some global package locations, so bare imports could fail without these links.

**Data flow**: It receives a tuple of possible global package directories. It turns those paths into one shell command that creates a local `.repl/node_modules` folder, removes an old whole-folder symlink if present, and adds per-package symlinks for packages it finds. The output is the command text; it does not run the command itself.

**Call relations**: `js_repl` asks this helper for the setup command just before running Node.js. If that command fails in the sandbox, `js_repl` stops and reports that linking global modules failed, because imports may not work correctly.

*Call graph*: called by 1 (js_repl).


##### `_candidate_source`  (lines 162–168)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full code file that should be executed for a REPL run. It combines previously successful code with the new code, or starts fresh when the caller asks for a reset.

**Data flow**: It receives the sandbox context, the saved REPL state path, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state, it returns just the new code plus a final newline. Otherwise, it reads the old saved code and appends the new code. The result is the complete candidate source text to run next.

**Call relations**: Both `js_repl` and `xlsx_repl` call this before execution. They treat its output as a trial version of the REPL state: if the run succeeds, they save it; if the run fails, they leave the old state untouched.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 171–182)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Turns raw process output into the standard tool response format used by the host system. It keeps text output and optional images together, and marks the result as an error when the process exit code is nonzero.

**Data flow**: It receives stdout, stderr, an exit code, and optionally image objects. It creates a JSON text block containing those three process fields, attaches any images after it, and returns a `ToolResult`. The `is_error` flag is set based on whether the exit code indicates failure.

**Call relations**: `js_repl` and `xlsx_repl` both call this at the end of a run. They hand it the sandbox command results, and for JavaScript also pass images collected by `_emitted_images`.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_emitted_images`  (lines 190–201)

```
async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code chose to send back through `emitImage`. It turns the saved image records into image response objects the tool system can display.

**Data flow**: It checks whether the JavaScript image log file exists in the sandbox. If not, it returns an empty tuple. If it exists, it reads the file, looks at the most recent allowed lines, validates each line as image JSON, skips malformed lines, and returns image content objects containing media type and base64 data.

**Call relations**: `js_repl` calls this after the Node.js process finishes. The images it returns are passed into `_repl_result`, so they appear alongside stdout, stderr, and the exit code.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 204–216)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a persistent JavaScript session inside the sandbox. It is used when the agent needs to automate browsers, test websites or games, run Node.js code, or return generated images.

**Data flow**: It receives the tool context and validated JavaScript input. First it asks `_candidate_source` for the full code to try. It writes a temporary `.mjs` run file containing the image-emitting prelude plus that code, clears any old emitted-image log, and runs the Node package-linking command from `global_modules_link`. Then it executes Node.js with a timeout. If the process exits successfully, it saves the candidate code as the new persistent state. Finally it reads any emitted images and returns a formatted tool result.

**Call relations**: This is the handler registered for the JavaScript tool in `manifest`. During a tool call, it coordinates the helper functions: `_candidate_source` prepares the replayable code, `global_modules_link` prepares imports, `_emitted_images` collects visual output, and `_repl_result` packages the final answer.

*Call graph*: calls 4 internal fn (_candidate_source, _emitted_images, _repl_result, global_modules_link); 1 external calls (quote).


##### `xlsx_repl`  (lines 219–227)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a persistent Python session for spreadsheet work, especially Excel files through `openpyxl`. It lets the agent build up workbook-related state across calls and return a simple JSON result when needed.

**Data flow**: It receives the tool context and validated Python input. It asks `_candidate_source` for the full code to run, writes that code to a temporary Python file, and adds a footer that prints `result` as JSON if the user code defined it. It runs Python in the sandbox with a timeout. If the run succeeds, it saves the candidate source as the new persistent state. It returns stdout, stderr, and exit code as a tool result.

**Call relations**: This is the handler registered for the spreadsheet REPL tool in `manifest`. It shares the same persistence and result-packaging helpers as `js_repl`, but does not collect images or link Node.js packages.

*Call graph*: calls 2 internal fn (_candidate_source, _repl_result); 1 external calls (quote).


##### `manifest`  (lines 230–250)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It names the extension, lists its tools, connects each tool to its input model and handler function, advertises related data skills, and requests sandbox internet access.

**Data flow**: It takes no input. It constructs tool definitions for the JavaScript and spreadsheet REPLs, creates skill specifications for the bundled data skills, and returns a `Manifest` object containing all of that registration information.

**Call relations**: The extension system calls this when loading the package. The returned manifest is what makes `js_repl`, `xlsx_repl`, and the bundled skills visible and usable later during agent work.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py`

`domain_logic` · `request handling and scheduled workflow pauses`

This file is the bridge between plain user actions like “run this every weekday” and the system’s scheduler, which is the part that stores future work and wakes it up later. Without it, scheduled tasks would not behave like normal workspace objects that can be listed, inspected, updated, or deleted, and workflows could not safely pause across time.

A scheduled task is treated as a private object owned by the member who created it. The task stores a cron schedule, which is a compact text pattern for repeated times, plus the prompt to run each time. The file carefully separates content from administration: the creator can change the prompt and description, while an admin can change timing, expiry, pause state, or delete the task without reading or editing private content.

The `ScheduledTaskSpec` model describes the allowed shape of a scheduled task. `ScheduledTaskObjects` supplies the object-store behavior: list rows, show details, report status, create or update tasks, and cancel them. It also hides private content from people who should not see it.

The `pause_and_wait` tool is a related but separate workflow feature. It creates a one-time internal wake-up row, not a visible scheduled task. Think of it like leaving a sealed note with an alarm clock: if a person replies first, the workflow resumes from that; otherwise the alarm wakes it later.

#### Function details

##### `ScheduledTaskSpec.validate_utc_expiry`  (lines 82–85)

```
def validate_utc_expiry(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Checks that an expiry time, if provided, is written as a UTC timestamp. UTC is the shared world clock the scheduler expects, so this prevents confusing local-time deadlines.

**Data flow**: It receives the proposed `expires_at` value from the scheduled task specification. If there is no value, it leaves it alone. If there is a value, it checks that the timestamp has timezone information and that its offset is exactly zero, meaning UTC; otherwise it rejects it with a clear error. The accepted timestamp is returned unchanged.

**Call relations**: This runs automatically when a `ScheduledTaskSpec` is built or validated. Later, creation and update logic can trust that any expiry time it receives is already in the scheduler’s expected time format.

*Call graph*: 2 external calls (utcoffset, timedelta).


##### `_require_scheduler`  (lines 106–109)

```
def _require_scheduler(ctx: ToolContext) -> ScheduleStore
```

**Purpose**: Fetches the schedule store from the current tool context, and fails early if scheduled-task support is not available. It is the shared doorway to the durable scheduler.

**Data flow**: It receives a `ToolContext`, which is the object carrying information about the current turn and extension services. It looks for `ctx.ext.scheduler`. If the scheduler is missing, it raises an error explaining that this extension needs the scheduled-task context and store. If present, it returns the schedule store.

**Call relations**: All code that needs stored schedules calls this helper first: listing, finding, creating, updating, deleting, checking status, and pausing workflows. This keeps the rest of the file from repeating the same safety check.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _find, _owned_rows, _status, pause_and_wait).


##### `_summary`  (lines 112–113)

```
def _summary(task: ScheduledTask) -> str
```

**Purpose**: Builds the short one-line label shown for a scheduled task in listings. It combines the schedule with either the task description or, if no description exists, the prompt.

**Data flow**: It receives a stored scheduled task. It creates text in the form `schedule — description-or-prompt`, then trims it to the configured maximum length so listings stay compact. The result is a short summary string.

**Call relations**: The listing method uses this when the current viewer is allowed to see the task’s content. If the viewer is not allowed to see private content, the listing uses a safer generic summary instead.

*Call graph*: called by 1 (_owned_rows).


##### `ScheduledTaskObjects._admin_can_apply`  (lines 133–136)

```
def _admin_can_apply(self, _old: ScheduledTaskSpec, spec: ScheduledTaskSpec) -> bool
```

**Purpose**: Decides whether an admin is allowed to apply a proposed update to someone else’s scheduled task. Admins may adjust timing and lifecycle settings, but not the private prompt or description.

**Data flow**: It receives the old specification and the proposed new specification. It checks which fields were actually supplied in the update. If the update includes `prompt` or `description`, it returns false; otherwise it returns true.

**Call relations**: This supports the broader object permission flow supplied by the member-owned object base class. It encodes the file’s main privacy rule: administrators can operate the clock, but they cannot rewrite what another member’s task will say or do.


##### `ScheduledTaskObjects._owned_rows`  (lines 138–154)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Produces the list entries for scheduled tasks visible through the object system. Each row includes the task name, a safe summary, and ownership information.

**Data flow**: It receives the current tool context. It gets the scheduler, reads all visible non-internal scheduled tasks from the store, and turns each task into an owned object row. If the current member may see the task content, it uses the real summary; otherwise it shows the schedule with a private-task note. It returns all rows as a tuple.

**Call relations**: The object listing flow calls this when someone lists scheduled-task objects. It relies on `_require_scheduler` to reach storage, `_content_visible` to protect private content, and `_summary` to produce readable labels.

*Call graph*: calls 3 internal fn (_content_visible, _require_scheduler, _summary); 2 external calls (__init__, __init__).


##### `ScheduledTaskObjects._detail`  (lines 156–179)

```
async def _detail(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> ObjectDetail[ScheduledTaskSpec] | None
```

**Purpose**: Builds the full object detail view for one scheduled task, including its editable specification and a link back to the conversation where it reports.

**Data flow**: It receives the current context, the task name, and the expected owner record. It looks up the task by name, checks that the stored generation still matches the requested owner, and returns nothing if the task changed or disappeared. If it matches, it creates a `ScheduledTaskSpec` from the stored task, adds creation and update times, adds a link to the reporting conversation, and marks whether the private spec should be visible.

**Call relations**: The object get/detail flow calls this after a task row has been selected. It uses `_find` to locate the stored task and `_content_visible` to decide whether the viewer is allowed to see the prompt and description.

*Call graph*: calls 2 internal fn (_content_visible, _find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ScheduledTaskObjects._status`  (lines 181–212)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports runtime information for a scheduled task, such as whether it is paused, when it will run next, when it last ran, and a small excerpt of the last response if the viewer is allowed to see it.

**Data flow**: It receives the context, task name, and expected owner record. It finds the task, checks the stored generation, asks the scheduler to inspect the task, and returns nothing if the task cannot be found or inspected. Otherwise it builds a status dictionary with pause state, next run time, last run time, expiry, and last-run details. Private response text is only included when `_content_visible` allows it.

**Call relations**: The object status flow calls this when a user asks how a scheduled task is doing. It combines object identity checks from `_find`, live scheduler inspection through `_require_scheduler`, and privacy checks through `_content_visible`.

*Call graph*: calls 3 internal fn (_content_visible, _find, _require_scheduler).


##### `ScheduledTaskObjects._apply_owned`  (lines 214–265)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ScheduledTaskSpec, old: ScheduledTaskSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new scheduled task or updates an existing one. It validates the schedule, enforces ownership rules, preserves the original conversation and creator, and recalculates the next run time.

**Data flow**: It receives the current context, object name, desired specification, any old specification, and the expected owner. First it validates any supplied cron schedule. It requires an acting member, because tasks run on behalf of a member. For a new task, it rejects conflicts, requires both schedule and prompt, computes the next firing time, and creates the stored task tied to the current conversation. For an existing task, it verifies that the task has not changed unexpectedly, checks whether the current speaker is the creator or an admin, keeps omitted fields unchanged, recalculates the next run time, and sends the update to the scheduler.

**Call relations**: The object apply/update flow calls this when a manifest is applied. It uses `_find` to detect the current stored task, `_require_scheduler` to write to storage, cron validation helpers to check and schedule time, and `speaker_is_admin` when deciding whether a non-creator may make administrative changes.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_scheduler); 4 external calls (__init__, now, next_fire, validate_cron).


##### `ScheduledTaskObjects._delete_owned`  (lines 267–271)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Cancels an existing scheduled task after confirming that the requested object still matches the stored task. This prevents deleting the wrong task if it changed while someone was editing.

**Data flow**: It receives the current context, task name, and expected owner record. It finds the task, checks that its stored generation matches the owner’s generation, and raises an error if it does not. If it matches, it asks the scheduler to cancel the task.

**Call relations**: The object delete flow calls this when a scheduled task is removed. It uses `_find` for the safety check and `_require_scheduler` to perform the cancellation in the schedule store.

*Call graph*: calls 2 internal fn (_find, _require_scheduler).


##### `ScheduledTaskObjects._find`  (lines 273–276)

```
async def _find(self, ctx: ToolContext, name: str) -> ScheduledTask | None
```

**Purpose**: Looks up a scheduled task by name in the scheduler’s list. It is the small shared search helper used before reading, updating, or deleting a task.

**Data flow**: It receives the current context and a task name. It asks the scheduler for the current list of scheduled tasks, scans that list for a matching name, and returns the first match. If no task has that name, it returns nothing.

**Call relations**: Detail, status, apply, and delete all call this before acting on a task. It centralizes the name lookup so each higher-level operation can focus on its own permission and consistency rules.

*Call graph*: calls 1 internal fn (_require_scheduler); called by 4 (_apply_owned, _delete_owned, _detail, _status).


##### `ScheduledTaskObjects._content_visible`  (lines 278–281)

```
def _content_visible(self, ctx: ToolContext, task: ScheduledTask) -> bool
```

**Purpose**: Decides whether the current viewer is allowed to see a task’s private content. Content is visible to the creator, and also for older or system-created tasks with no creator recorded.

**Data flow**: It receives the current context and a stored task. It compares the task’s creator member ID with the acting member ID in the context. It returns true if the task has no recorded creator or if the creator is the current acting member; otherwise it returns false.

**Call relations**: Listing, detail, and status call this before showing prompts, descriptions, or response excerpts. It is the file’s simple privacy filter, used anywhere private task content might otherwise leak.

*Call graph*: called by 3 (_detail, _owned_rows, _status).


##### `pause_and_wait`  (lines 317–353)

```
async def pause_and_wait(ctx: ToolContext, args: PauseAndWaitInput) -> ToolResult
```

**Purpose**: Pauses the current workflow until either a member sends a newer message or a durable timer expires. It gives the agent a ready-made reply to send now, plus instructions for the later resumed turn.

**Data flow**: It receives the current tool context and pause request arguments: the message to show now, how long to wait, next-step instructions, a reason, optional metadata, and a user-facing description. It computes the timer’s wake-up time, writes a one-time internal pause row into the scheduler, and packages the resume instructions as JSON. If a newer member message has already arrived, it returns a result telling the agent not to arm the timer path and to let that message resume the workflow. Otherwise it returns a result telling the agent that the workflow is awaiting the timer, including the resume time and saved instructions.

**Call relations**: The `PAUSE_AND_WAIT_TOOL` definition uses this as its handler when the tool is invoked. It calls `_require_scheduler` to record the pause and returns a `ToolResult` containing text directives that guide the agent’s current turn and the later resume behavior.

*Call graph*: calls 1 internal fn (_require_scheduler); 5 external calls (__init__, __init__, now, timedelta, dumps).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling across conversation turns`

This file solves a simple but important problem: long requests can have many steps, and both the user and the agent need a clear way to see what has been planned, what is being worked on, and what is finished. Think of it like a clipboard checklist beside a workbench. The agent writes the whole checklist at the start, then checks items off as work continues.

The file defines the shape of todo data: each task has text and a status such as “pending,” “in_progress,” or “completed.” A todo board has a title and a list of those tasks. The two main tools are `update_todo_list`, which creates or replaces the whole board, and `update_todo_status`, which changes the status of one or more existing tasks.

The checklist is stored using a key based on the conversation ID. That means each conversation gets its own board, instead of all users or conversations sharing one list. When a status update comes in, the file first reads the saved board, checks that it exists, checks that the requested task number is valid, applies the changes, and saves the board again.

The `manifest` function advertises this extension to the host system: it names the tools, describes when to use them, says what input they expect, and includes a prompt section that teaches the agent how to use the checklist.

#### Function details

##### `_require_ext`  (lines 82–85)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the todo tools are running with an extension context, which is the object that gives them access to the extension’s storage. Without it, the tools would have nowhere reliable to save or read the checklist.

**Data flow**: It receives the current tool context. It checks whether that context includes extension-specific data. If it does, it returns that extension context; if not, it stops the operation by raising an error.

**Call relations**: Both `update_todo_list` and `update_todo_status` call this at the start of their work. It acts like a gatekeeper before either tool tries to use the stored todo board.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 88–89)

```
def _board_key(ctx: ToolContext) -> str
```

**Purpose**: This helper builds the storage key used to save and find the todo board for the current conversation. It keeps one conversation’s checklist separate from another’s.

**Data flow**: It receives the tool context, reads the conversation ID from the current turn, adds the todo key prefix, and returns a single storage key string.

**Call relations**: `update_todo_list` uses this key when saving a new board, and `update_todo_status` uses the same kind of key when reading and saving changes. This is what lets both tools talk about the same checklist within one conversation.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_result`  (lines 92–93)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns the current todo board into the standard result format returned by a tool. It makes sure the agent receives the latest checklist after every create or update operation.

**Data flow**: It receives a `TodoBoard`, converts it into JSON text, wraps that text as tool content, and returns a `ToolResult` containing it.

**Call relations**: After `update_todo_list` creates a board, and after `update_todo_status` changes one, they both call this helper to send the updated board back to the caller.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 96–98)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension’s storage and turns it back into a validated `TodoBoard` object. If no board has been saved yet, it reports that by returning nothing.

**Data flow**: It receives the extension context and a storage key. It asks the extension store for the saved value at that key. If the store has no value, it returns `None`; otherwise, it validates the saved data as a todo board and returns it.

**Call relations**: `update_todo_status` calls this before changing task statuses. That status tool depends on this helper because it must start from the existing checklist rather than inventing a new one.

*Call graph*: called by 1 (update_todo_status).


##### `update_todo_list`  (lines 101–105)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or fully replaces the todo checklist for the current conversation. An agent uses it when starting or revising a multi-step plan.

**Data flow**: It receives the tool context and the requested list details: a title, a complete set of tasks, and a short user-facing description. It first confirms extension storage is available, builds a `TodoBoard`, saves that board under the current conversation’s storage key, and returns the saved board as JSON text in a tool result.

**Call relations**: This is one of the extension’s public tools, exposed by `manifest`. Inside its flow, it calls `_require_ext` to get storage access, `_board_key` to choose where to save the board, and `_board_result` to return the current checklist to the agent.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 108–119)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of existing todo items, such as marking a task as started or completed. It protects the checklist from invalid updates, such as changing a task before a list exists or using a task number that is outside the list.

**Data flow**: It receives the tool context and one or more status updates, each with a 1-based task number and a new status. It gets extension storage, builds the conversation-specific key, reads the saved board, rejects the request if there is no board, checks each task number, updates the matching task statuses, saves the revised board, and returns the updated checklist.

**Call relations**: This is the second public tool exposed by `manifest`. It relies on `_require_ext` for storage access, `_board_key` to find the right conversation’s board, `_read_board` to load the current checklist, and `_board_result` to show the caller the checklist after the updates.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `manifest`  (lines 122–141)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the todo extension to the host system. It tells the system the extension’s name, version, available tools, expected inputs, tool descriptions, and the prompt text that guides the agent’s behavior.

**Data flow**: It takes no input. It creates tool definitions for creating a todo list and updating todo statuses, attaches their input models and handler functions, adds the todo prompt section, and returns a `Manifest` object.

**Call relations**: The host system calls this when loading the extension. The manifest is the bridge between the code in this file and the larger platform: it is how `update_todo_list` and `update_todo_status` become available as usable tools.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Website Building Workflow
Website-building delegation routes a user request to a specialized site agent and the supporting tools that build, serve, and publish the result.

### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `tool invocation during a website build request`

This file exists so the main agent does not have to do a whole website build by itself. Instead, it can package the request into a clear objective and delegate it to a specialized “website_building” subagent. Think of it like a project manager giving a complete brief to a specialist builder: the specialist works separately, but in the same workshop, so the finished files and hosted site remain available to the original conversation.

The file defines the public tool name and description shown to the agent, plus a typed input shape called `BuildWebsiteInput`. That input requires a self-contained objective because the child agent starts without the parent conversation’s history. It can also include a friendly task name, skills to preload before the child starts, and an option to give the child more working rounds for larger builds.

The important behavior is that the tool is marked as side-effecting, meaning it can change the outside world of the conversation by creating files, starting a hosted site, or continuing a previously started child task. It uses an idempotency key, which is a repeat-safe identifier, so if the same tool call is retried after a crash, the system reconnects to the same spawned build instead of accidentally starting a duplicate one.

#### Function details

##### `_build_website`  (lines 57–64)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the actual tool handler that starts or reconnects to the website-building subagent. It takes the user’s build request, sends it to the child agent, and returns the child’s summary back to the main agent.

**Data flow**: It receives the current tool context and a `BuildWebsiteInput` object. It turns the input into plain data, leaving out empty fields and the `user_description` field because that is for the activity timeline rather than the child’s work brief. It then asks the context to spawn the `website_building` child, using the current idempotency key so retries point to the same child run. When the child finishes or reports back, the function converts the child output to JSON text if there is any output, wraps that text in `TextContent`, and returns it inside a `ToolResult`.

**Call relations**: The tool system calls this function when the agent invokes the registered `build_website` tool. Inside, it hands the real work to `ToolContext.spawn`, which creates or resumes the specialized website-building child. After the child returns, `_build_website` packages the result into the standard tool response objects so the rest of the agent system can read it like any other tool result.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup and website-building task execution`

This file is like a job description and tool belt for a website-building assistant that works inside a larger conversation. The main assistant can hand off a website task to this subagent, and this profile keeps that work focused: the child agent gets website-building instructions, file-editing tools, build and local preview tools, browser-style checking tools, spreadsheet and JavaScript helpers, and optional web research tools.

The file also sets limits and boundaries. The subagent can build and test a site, but it cannot publish a full backend app or directly share a file with the user. That matters because the child agent is temporary and works inside the parent assistant’s workspace. The parent assistant stays responsible for final delivery and publishing choices.

Two small data shapes describe the handoff. `WebsiteBuildingTask` says what the parent can ask for: the objective, an optional task name, optional skills to preload, and whether to use extended context. `WebsiteBuildingResult` says the child must report back with a plain result string. Finally, `WEBSITE_BUILDING_PROFILE` bundles the name, prompt, allowed tools, input and output shapes, and maximum number of rounds into one object the wider system can register and run.


### `extensions/sites/ufo_ext_sites/tools.py`

`orchestration` · `request handling`

This file is the bridge between an agent saying “build or publish this website” and the sandbox actually doing it. The sandbox is the isolated container where project files live and commands run, so these tools can safely build code, start servers, and check results without touching the outside machine directly.

There are four main tools. `website` runs a build command and returns the files it produced. `start_server` starts any server command in the background, first clearing the chosen port and then waiting until something is listening there. This avoids the common problem where a tool says “server started” before the server is really usable. `deploy_website` serves a static folder, such as one containing `index.html`, and registers that live port as a hosted site with a permanent URL. `publish_website` does the same for a fuller web app, optionally installing dependencies and optionally running a backend command.

The important safety step is that hosting permission is checked before replacing anything on the shared serving port. That way, if the system refuses a publish because of ownership or visibility rules, it does not accidentally knock down an existing site first. Once the server is proven live, the file registers the port so members can open the hosted link. In short, this file acts like a careful stage manager: build the set, turn on the lights, verify the audience can see it, then hand out the ticket.

#### Function details

##### `StartServerInput.validate_port`  (lines 91–94)

```
def validate_port(self) -> 'StartServerInput'
```

**Purpose**: This checks that a requested server port is a real TCP port number. It prevents impossible or unsafe port values from reaching the server-starting code.

**Data flow**: It receives the parsed `StartServerInput` data. If `port` is missing, it leaves it alone so the default can be used later; if a port is present, it must be between 1 and 65535. The same input object comes out unchanged when valid, or an error is raised when invalid.

**Call relations**: This runs as part of Pydantic validation, meaning it is applied when tool arguments for `start_server` are being turned into a `StartServerInput` object. By catching bad ports early, it keeps `start_server` and `_serve` from trying to run shell commands with a nonsensical port.


##### `_json_result`  (lines 126–127)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: This small helper turns a Python dictionary into the standard tool response format. It lets the public tools return structured information, such as URLs and file lists, in a consistent way.

**Data flow**: It takes a dictionary, converts it to a JSON string, wraps that string as text content, and then wraps the content in a tool result. The output is a `ToolResult` ready to send back to the caller.

**Call relations**: `website`, `start_server`, `deploy_website`, and `publish_website` all finish by calling this helper. They each gather their own result data first, then hand it to `_json_result` so the final response has the same shape no matter which website tool was used.

*Call graph*: called by 4 (deploy_website, publish_website, start_server, website); 3 external calls (__init__, __init__, dumps).


##### `_serve`  (lines 130–167)

```
async def _serve(ctx: ToolContext, command: str, project: str, port: int, log: str) -> dict[str, object]
```

**Purpose**: This starts a server command inside the sandbox and waits until the chosen port is actually accepting connections. Someone uses it when they need a running server, not just a background process that might still be starting or might have failed.

**Data flow**: It receives the tool context, a shell command, a project directory, a port, and a log file path. It first frees the port by killing old processes that may be using it, starts the command with output sent to the log file, and repeatedly tries to connect to the port until it is ready or times out. It returns the sandbox-local URL, port, and log path; if startup fails, it reads the log tail and raises an error.

**Call relations**: `start_server` uses this directly for scratch servers. `deploy_website` and `publish_website` use it after their hosting checks, so they only register a hosted link after `_serve` has proved that a real server is reachable.

*Call graph*: called by 3 (deploy_website, publish_website, start_server); 1 external calls (quote).


##### `_refuse_before_serving`  (lines 170–209)

```
async def _refuse_before_serving(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> str
```

**Purpose**: This asks the hosting system whether a site is allowed to be registered before the code touches the shared serving port. It protects existing sites from being taken down when a new deploy would later be refused.

**Data flow**: It receives the tool context, the requested site name, the port that will be used, and optional visibility. It checks that extension state exists, that an acting member owns the action, and that changing visibility is only done when there is a live speaker. It normalizes the site name, builds the would-be URL, asks `HostedSites` whether registration would be refused, and returns the safe normalized name if everything passes.

**Call relations**: `deploy_website` and `publish_website` call this before `_serve`. If this function refuses the action, they stop before killing anything on the port; if it passes, they continue to start the server and later call `_host`, which repeats the checks at the moment of writing.

*Call graph*: called by 2 (deploy_website, publish_website); 3 external calls (__init__, site_name, site_url).


##### `_host`  (lines 212–249)

```
async def _host(ctx: ToolContext, raw_name: str, port: int, visibility: Visibility | None) -> dict[str, object]
```

**Purpose**: This records that a sandbox port should be available as a hosted site with a stable public-facing link. It is the step that turns “a server is running on a port” into “there is a named site members can open.”

**Data flow**: It receives the context, a raw or normalized site name, the live port, and optional visibility. It verifies owner and visibility rules, normalizes the name, builds the hosted URL for the sandbox conversation, and asks `HostedSites` to register the port. It returns the site name, visibility, internal site object name, and public site URL.

**Call relations**: `deploy_website` and `publish_website` call this only after `_serve` has confirmed the server is live. It uses `site_url` to describe where the site will be opened, `HostedSites` to store the registration, and `site_object_name` to return the system’s internal reference for the hosted site.

*Call graph*: called by 2 (deploy_website, publish_website); 4 external calls (__init__, site_object_name, site_name, site_url).


##### `website`  (lines 252–261)

```
async def website(ctx: ToolContext, args: WebsiteInput) -> ToolResult
```

**Purpose**: This tool runs a website build command in the sandbox and reports what files are present afterward. It is useful when the agent needs to compile or generate the site before serving or publishing it.

**Data flow**: It receives a build command, an optional project path, and a plain-language description. It chooses the project directory, runs the command there with a long build timeout, and raises an error if the build fails. On success, it lists the directory contents and returns the project path plus the file names as JSON.

**Call relations**: This is a standalone build step. It uses shell quoting for the project directory before running sandbox commands, then hands the final dictionary to `_json_result` so the caller receives the build result in the same tool-response format as the other site tools.

*Call graph*: calls 1 internal fn (_json_result); 1 external calls (quote).


##### `start_server`  (lines 264–268)

```
async def start_server(ctx: ToolContext, args: StartServerInput) -> ToolResult
```

**Purpose**: This tool starts a background server in the sandbox without publishing it as a hosted deliverable. It is meant for temporary testing, such as letting browser tools inspect a local development server.

**Data flow**: It receives a server command, project directory, optional port, optional log file, and description. It fills in defaults for the port and log file when needed, asks `_serve` to clear the port, launch the server, and wait for readiness, then returns the local URL, port, log path, and project path as JSON.

**Call relations**: `start_server` is a thin public wrapper around `_serve`. Unlike `deploy_website` and `publish_website`, it does not call `_host`, because a scratch server should be reachable inside the sandbox but should not become a permanent hosted site.

*Call graph*: calls 2 internal fn (_json_result, _serve).


##### `deploy_website`  (lines 271–277)

```
async def deploy_website(ctx: ToolContext, args: DeployWebsiteInput) -> ToolResult
```

**Purpose**: This tool serves a finished static website folder and gives it a stable hosted link. It is for cases where the built output already exists and only needs to be made available.

**Data flow**: It receives the static output directory, requested site name, entry file, optional visibility, and description. It first checks whether hosting would be allowed, then starts Python’s built-in static file server on the standard app port, waits for that port to be ready, registers the live port as a hosted site, and returns both serving details and hosted-link details as JSON.

**Call relations**: Its flow is deliberately ordered: `_refuse_before_serving` checks permissions before any server replacement, `_serve` starts and verifies the server, `_host` records the hosted site, and `_json_result` formats the final answer. This makes redeploys reliable while avoiding damage when a deploy is not allowed.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve).


##### `publish_website`  (lines 280–294)

```
async def publish_website(ctx: ToolContext, args: PublishWebsiteInput) -> ToolResult
```

**Purpose**: This tool publishes a web app as a hosted site, with optional dependency installation and optional backend startup. It covers both simple static builds and apps that need a custom run command.

**Data flow**: It receives the app project path, built output path, app name, optional visibility, optional run and install commands, and description. It checks hosting permission first, runs the install command if one was provided, chooses either the custom run command or a static file server, chooses the correct working directory, starts and verifies the server, registers the hosted site, and returns the combined serving and hosting information as JSON.

**Call relations**: `publish_website` uses the same safety pattern as `deploy_website`: `_refuse_before_serving` first, `_serve` after setup, `_host` once the port is live, and `_json_result` at the end. It also uses shell quoting when running the optional install step so the project path is passed safely to the sandbox shell.

*Call graph*: calls 4 internal fn (_host, _json_result, _refuse_before_serving, _serve); 1 external calls (quote).

## 📊 State Registers Touched

- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-hosted-site-state` — The durable records for generated hosted sites, including names, ports, owners, conversations, sharing, and viewing permissions.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-extension-kv-store` — The generic per-workspace extension JSON store used by add-ons to persist small feature-specific state outside core tables.
- `reg-todo-checklist-state` — The durable visible todo/checklist state that agents update during long-running work and reuse across turns.
- `reg-repl-scratchpad-state` — The Python and JavaScript REPL scratchpad sessions, files, and execution state kept for agent experimentation across tool calls or turns.
- `reg-turn-cleanup-callback-state` — The per-turn registry of cleanup callbacks and borrowed-resource finalizers that tools add during execution and completion/teardown later drains.
- `reg-search-provider-registry` — The live registry of web-search, page-fetch, embedding/search provider backends and their capabilities used by research, recall, and indexing code.

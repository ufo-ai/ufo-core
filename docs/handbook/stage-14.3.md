# Agent scratchpad and skill-authoring extension support  `stage-14.3`

This stage is shared support that helps an agent keep useful tools and scratch work across a runtime turn. It is not the main reasoning loop itself. Instead, it adds small “extensions,” meaning optional add-on packages, that the agent can call when it needs a workspace or wants to save a new skill.

The skill creation package starts with an __init__.py file that simply marks the folder as a Python package and identifies it as support for agent-authored skills. Its manifest defines the skill_create extension: it tells the system how to save, list, inspect, delete, and reload skills that an agent has written. The store file does the actual filing work. It keeps each saved skill tied to the current workspace and agent, like labeled drawers, so different agents do not mix up or overwrite each other’s tools.

The REPL extension adds two persistent scratchpads, one for JavaScript/Node.js and one for Python Excel work. These act like reusable notebooks during a session and advertise related data-analysis skills when needed.

## Files in this stage

### Agent-authored skill storage
Defines the skill-creation package and the mechanisms for saving, listing, inspecting, deleting, and reloading agent-scoped skills.

### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import time`

This is the package entry file for `ufo_ext_skill_create`. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Think of it like a label on a drawer: it does not contain the tools itself, but it tells the system what kind of tools are inside.

Here, the only content is a short documentation string. It says this package deals with “agent-owned authored skills as objects” and “per-turn runtime skills.” In plain terms, this extension is meant to support skills that an agent can create, own, and use, plus skills that are available temporarily during a single step or turn of execution.

There is no executable logic in this file. Nothing is initialized, no settings are loaded, and no functions or classes are defined here. Its value is structural and explanatory: without it, imports may not recognize this directory as a package in some Python setups, and newcomers would have less immediate context about what the extension is for.


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `extension setup, object requests, and turn startup`

A “skill” here is a small bundle of text files, always including a SKILL.md file, that a user can create during a conversation and then reuse in later turns. This file is the bridge between that human idea and the system’s object/runtime machinery. Without it, authored skills would not appear in the object list, could not be saved safely, and would not be loaded back into the agent’s skill registry.

The file defines the shape of a saved skill specification. File content can be supplied directly, read from the current workspace, or referred to by a stored SHA-256 digest, which is a fingerprint used to keep an unchanged file without sending its whole content again. It also defines safety checks: file paths must stay inside the skill’s own folder, files must be UTF-8 text, there is a maximum file count, and the whole skill has a size limit.

The main worker is SkillObjects. It lists saved skills, returns safe details without echoing file bodies, applies updates by resolving all file references into bytes, and deletes skills. At the bottom, manifest() advertises this extension to the host system, including its built-in authoring skill and the saved runtime skills that should be available on future turns.

#### Function details

##### `_require_ext`  (lines 99–102)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the caller supplied an ExtensionContext, which is the object that says which agent and extension storage are being used. It turns a missing context into a clear error instead of letting later code fail in a confusing way.

**Data flow**: It receives either an ExtensionContext or nothing. If the context is present, it returns it unchanged; if it is missing, it raises a RuntimeError explaining that this skill kind was called without the context it needs.

**Call relations**: Most SkillObjects methods call this first, because every read or write must be tied to the current agent’s extension storage. It is the doorway that prevents list, get, apply, delete, status, and resolve operations from running without knowing their scope.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 105–115)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every saved file path stays inside the skill’s own mounted folder. It prevents a malicious or mistaken skill from saving a path like “../../other-file” and later writing outside its allowed area.

**Data flow**: It receives the skill name and the proposed skill spec. It computes the proper mount root for that skill, checks each file path against it, and either finishes silently or raises a ValueError naming the unsafe path.

**Call relations**: SkillObjects.apply calls this before saving anything. It relies on the sandbox path-containment helper and the skill mount-root helper, acting like a fence check before the new file list becomes durable storage.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_mount_root).


##### `_text`  (lines 118–124)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This verifies that a skill file is plain UTF-8 text. Skills in this system are text bundles, so binary files are rejected early with a clear message.

**Data flow**: It receives a path and raw bytes. It tries to decode the bytes as text; on success it returns the decoded string, and on failure it raises a ValueError saying that the named skill file is not text.

**Call relations**: SkillObjects._resolve calls this after it has gathered file bytes from direct content, workspace files, or stored digests. It is the final content-type check before resolved files are allowed into the saved skill map.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 131–132)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved skills visible to the current turn. It is used when the object system needs summary rows rather than full file details.

**Data flow**: It receives a tool context and a list query. It extracts the extension context, loads skill rows for the current agent, applies the query’s paging rules, and returns an ObjectPage containing the matching rows.

**Call relations**: This is a public object-list entry for turn-time tools. It asks _require_ext for scope, asks _rows to build the raw summaries, and hands those rows to object_page so the wider object API gets the standard page format.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 134–146)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the saved skills page for a signed-in member outside a live turn, such as in a portal. The important rule is that skills belong to the bound agent, not to the individual member.

**Data flow**: It receives an optional extension context, member information, admin status, and a query. It uses the extension context to load the agent’s saved skill rows, pages them, and returns the resulting ObjectPage.

**Call relations**: This mirrors SkillObjects.list for member-facing views. It still goes through _require_ext and _rows, then uses object_page, so portal reads and turn reads see the same agent-scoped saved skill set.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 148–149)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches the safe detail view for one saved skill by name. The detail includes file fingerprints and sizes, not the actual file contents.

**Data flow**: It receives a tool context and a skill name. It gets the extension context, asks _skill for the stored detail, and returns that detail or None if the skill does not exist.

**Call relations**: This is the normal object-detail path during a turn. It delegates almost all work to _skill, after _require_ext confirms which agent’s storage should be searched.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 151–170)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This returns a portal-friendly view of one saved skill, combining its list row with its detail. It avoids exposing file bodies and only returns a skill if it also appears in the visible row list.

**Data flow**: It receives an optional extension context, a skill name, and member/admin information. It loads the visible rows, looks for the requested name, loads the safe detail if the row exists, and returns a MemberObject combining both; otherwise it returns None.

**Call relations**: This is the member-facing counterpart to get. It uses _rows as the visibility gate, then _skill for the digest-based detail, and wraps both into MemberObject for the portal object API.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 172–176)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the lightweight summary rows used in skill lists. Each row contains the skill name and a shortened description.

**Data flow**: It receives an ExtensionContext. It loads all saved runtime skills for that agent from UserSkillStore, turns each one into an ObjectRow, trims long descriptions to the configured maximum, and returns the rows as a tuple.

**Call relations**: SkillObjects.list, member_page, and member_detail all use this helper when they need the visible index of saved skills. It is the shared source for list-style views.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 178–201)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the safe detailed representation of a saved skill. It deliberately replaces each file’s content with a SHA-256 digest and size so large or sensitive file text is not echoed back into normal object context.

**Data flow**: It receives an ExtensionContext and a skill name. It reads the stored files and timestamps; if either is missing, it returns None. Otherwise it creates a UserSkillSpec whose files are FileRef records, adds creation/update times and a link to the owning agent, and returns an ObjectDetail.

**Call relations**: SkillObjects.get and member_detail call this when they need detail for one skill. It talks to UserSkillStore for stored data and to the extension context for the agent name used in the scoped_to link.

*Call graph*: calls 1 internal fn (agent_name); called by 2 (get, member_detail); 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 203–217)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports quick health and size information for a saved skill. It is useful for confirming what was saved without loading or displaying all file contents.

**Data flow**: It receives a tool context, skill name, and optional generation expectation. It loads the stored files for the current agent; if none exist, it returns None. Otherwise it parses SKILL.md to get the description, counts files, sums their byte sizes, and returns those facts in a dictionary.

**Call relations**: The object system can call this after or around object operations to show current state. It depends on _require_ext for scope, UserSkillStore for bytes, and parse_skill_content for the skill description.

*Call graph*: calls 1 internal fn (_require_ext); 2 external calls (__init__, parse_skill_content).


##### `SkillObjects.apply`  (lines 219–236)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a saved skill. It enforces the file-count limit, path safety, total-size limit, and then saves the fully resolved text files into the agent’s skill store.

**Data flow**: It receives a tool context, object name, proposed spec, optional old spec, and optional generation expectation. It checks the context, rejects too many files, verifies all paths stay inside the skill, resolves direct content/workspace references/stored digests into bytes, checks total size, and writes the result to UserSkillStore along with the currently known built-in skill names.

**Call relations**: This is the central save path for the skill object kind. It calls _contained_keys before trusting paths, _resolve to gather actual file bytes, and UserSkillStore.save to persist the validated bundle.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 238–246)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved skill for the current agent. After deletion, that authored skill will no longer be listed or loaded as a runtime skill.

**Data flow**: It receives a tool context, skill name, and optional generation expectation. It gets the extension context and asks UserSkillStore to delete that named skill from the agent-scoped store.

**Call relations**: This is the delete path exposed by the object kind. It uses _require_ext to identify the correct agent scope, then hands the actual removal to UserSkillStore.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 248–296)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns a user’s mixed file specification into actual bytes ready to save. It supports three ways to specify each file: inline text, a workspace file path, or a stored digest that means “keep the existing content.”

**Data flow**: It receives a tool context, skill name, and proposed spec. It loads existing stored files, verifies any FileRef digest matches the saved content, reads any FileFrom workspace files inside the sandbox using a small Python script, decodes those file bodies from base64, converts inline strings to bytes, checks every result is UTF-8 text, and returns a path-to-bytes dictionary.

**Call relations**: SkillObjects.apply calls this after basic path checks and before saving. Inside, it uses _require_ext for storage scope, UserSkillStore for existing files, the sandbox to read workspace sources safely, hashing to verify kept files, JSON/base64 for moving bytes through command output, and _text for the final text-only rule.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 324–326)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads the current agent’s saved skills so they can become runtime skills for a turn. In other words, it makes previously authored skills available to the agent again.

**Data flow**: It receives an ExtensionContext. It creates a UserSkillStore for that agent scope, loads all saved skills, and returns them as a tuple of RuntimeSkill objects.

**Call relations**: The manifest registers this as the extension’s runtime skill provider. During turn setup, the host can call it to add saved user skills to the skill registry.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 329–336)

```
def manifest() -> Manifest
```

**Purpose**: This tells the host system what the skill_create extension provides. It names the extension, declares its object kind, includes the built-in create-skill authoring skill, and points to the loader for saved runtime skills.

**Data flow**: It takes no inputs. It constructs and returns a Manifest containing the extension name, version, the SKILL_OBJECT definition, a SkillSpec pointing at the bundled authoring skill directory, and the _runtime_skills callback.

**Call relations**: This is the file’s registration point. The host calls it when loading extensions, and the returned Manifest wires together the object behavior implemented by SkillObjects with the built-in and user-saved skills.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and skill loading`

This file is the small “filing cabinet” for user-authored skills. A skill is stored as a set of files, such as a main SKILL.md file plus any extra assets. The store checks that a skill has a safe name, turns its file bytes into text that can be stored in the database, and later turns that text back into usable skill files.

The database table records the workspace, the agent, the skill name, a fingerprint of the content, the saved content itself, and creation/update times. The workspace and agent act like labels on a drawer: every lookup only opens the drawer for the current agent in the current workspace.

Before saving, the store refuses unsafe names, refuses attempts to overwrite built-in skills unless this agent already owns that saved skill, and enforces a limit of 100 user skills per agent. It also parses the files into a RuntimeSkill, which is the form the rest of the system can run. File bytes are base64-encoded, meaning raw bytes are converted into safe text for database storage. When loading, corrupt saved skills are skipped and logged rather than crashing the whole skill-loading process.

#### Function details

##### `UserSkillStore.save`  (lines 69–126)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves one user-created skill for the current agent, after checking that it is safe and allowed. It either updates an existing saved skill with the same name or inserts a new one.

**Data flow**: It receives a skill name, a mapping of file paths to raw file bytes, and the set of skill names already present in the main registry. It reads the current workspace and agent, validates the name, parses the files into a runnable skill, checks whether the agent already owns this name, and checks the per-agent skill limit when adding a new skill. It then base64-encodes the files, stores them as JSON text, computes a SHA-256 digest, and writes the result into the database. The output is the parsed RuntimeSkill that the rest of the system can use.

**Call relations**: This is the main write path for the store. During the save flow it calls _owns to decide whether the save is an update or a new skill, and _count to enforce the maximum number of saved skills. It also relies on parse_skill_content to prove the files form a valid skill before anything is persisted.

*Call graph*: calls 2 internal fn (_count, _owns); 10 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, agent_current, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 128–162)

```
async def load_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved skill owned by the current agent and returns them in runnable form. If one saved skill is broken or unreadable, it skips that one and keeps loading the others.

**Data flow**: It reads the current workspace and agent, fetches all matching skill names and saved content from the database, and processes them in name order. For each row, it validates the stored JSON shape, base64-decodes each file back into bytes, and parses the files into a RuntimeSkill. It returns a tuple of successfully loaded RuntimeSkill objects, while logging any corrupt entries instead of raising an error for the whole batch.

**Call relations**: This is the bulk read path used when the system needs to make an agent’s saved skills available. It hands each decoded skill directory to parse_skill_content so the rest of the runtime receives normal RuntimeSkill objects, not raw database records.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 164–180)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original files for one saved skill owned by the current agent. It is useful when another part of the system needs the skill’s stored contents rather than the parsed runtime version.

**Data flow**: It receives a skill name, reads the current workspace and agent, and looks up that exact saved skill in the database. If no row exists, it returns None. If a row exists, it validates the stored JSON, base64-decodes each file, and returns a dictionary from file path to raw bytes.

**Call relations**: This is a focused read helper for one skill. Unlike load_all, it does not parse the result into a RuntimeSkill; it gives callers the saved files directly after decoding them from the database-safe text format.

*Call graph*: 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 182–191)

```
async def delete(self, name: str) -> None
```

**Purpose**: Deletes one saved skill for the current agent. If the skill does not exist, it simply leaves the database unchanged.

**Data flow**: It receives a skill name, reads the current workspace and agent, and sends a delete command to the database for the row matching that workspace, agent, and name. It returns nothing; the change is the removal of the matching saved skill record, if present.

**Call relations**: This is the remove path for user-authored skills. It uses the same current-agent scope as the other methods, so deletion is limited to the active agent’s own saved skill drawer.

*Call graph*: 2 external calls (delete, agent_current).


##### `UserSkillStore.timestamps`  (lines 193–205)

```
async def timestamps(self, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Returns when a saved skill was first created and when it was last updated. This supports features that need to show or compare skill history without loading the whole skill content.

**Data flow**: It receives a skill name, reads the current workspace and agent, and selects only the creation and update timestamps for that one skill. If the skill is missing, it returns None. If found, it returns the two datetime values as a pair.

**Call relations**: This is a small metadata lookup alongside the larger content-loading methods. It uses the same scoped database access pattern, but only asks for timing information rather than file contents.

*Call graph*: 2 external calls (select, agent_current).


##### `UserSkillStore._count`  (lines 207–219)

```
async def _count(self) -> int
```

**Purpose**: Counts how many user-created skills the current agent already has. It exists to enforce the maximum saved-skill limit before adding another new skill.

**Data flow**: It reads the current workspace and agent, asks the database to count rows in the user_skill table for that scope, and returns the count as an integer.

**Call relations**: This is an internal helper used by UserSkillStore.save. Save calls it only when the requested name is not already owned, because replacing an existing skill should not increase the number of saved skills.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


##### `UserSkillStore._owns`  (lines 221–233)

```
async def _owns(self, name: str) -> bool
```

**Purpose**: Checks whether the current agent already has a saved skill with a given name. This matters because updating your own saved skill is allowed, but newly shadowing a built-in skill is not.

**Data flow**: It receives a skill name, reads the current workspace and agent, and looks for a matching row in the database. It returns true if such a row exists and false otherwise.

**Call relations**: This is an internal helper used by UserSkillStore.save. Save uses its answer to decide whether the operation is an update, whether a registry name collision should be rejected, and whether the skill-count limit needs to be checked.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


### Persistent REPL scratchpads
Provides persistent JavaScript and Python Excel scratchpads and advertises related data-analysis skills for runtime use.

### `extensions/repl/ufo_ext_repl/manifest.py`

`entrypoint` · `tool registration and tool execution`

This file is the entry point for the persistent REPL extension. A REPL is an interactive coding workspace: the agent can run a bit of code, keep the successful definitions and imports, and build on them in the next call. Without this file, the agent would have to start from zero every time it wanted to test a website with Node.js or inspect an Excel file with Python.

The main idea is simple: each run is written into a sandboxed workspace file. If the new code succeeds, it is saved as part of the continuing session. If it fails, it is not saved, so a bad experiment does not poison later runs. A reset option deletes the saved state and starts fresh.

The JavaScript tool runs Node.js as an ES module, which allows modern JavaScript features such as top-level await. It also prepares links to globally installed Node packages so imports like Playwright can work. JavaScript code can call emitImage to return images, which is useful for browser screenshots or visual checks.

The Excel tool runs Python with openpyxl available for spreadsheet work. If the code sets a variable named result, a small footer prints it as JSON so the agent can read structured output. Both tools run inside the project sandbox, so file access and network access stay within the system’s safety boundaries.

#### Function details

##### `_meter_run`  (lines 57–73)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for a REPL run, including which tool ran and what exit code it returned. This helps operators tell the difference between agent code failing, an interpreter missing, and other runtime problems.

**Data flow**: It receives the tool context, the tool name, and the process exit code. It turns the current subagent profile into a metric label, folds unusual exit codes into a generic “other” bucket, and sends the count to the monitoring system. It does not return a value; its effect is the emitted metric.

**Call relations**: Both js_repl and xlsx_repl call this after the sandboxed interpreter finishes. It hands the run information to the observability helpers emit_metric and turn_profile so failures are visible outside the tool result itself.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 76–94)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because ES modules do not normally use NODE_PATH, so bare imports may fail unless packages are linked into the local node_modules folder.

**Data flow**: It takes a tuple of possible global package roots, or uses the defaults. It returns a shell script string that creates the REPL’s node_modules directory, removes an outdated whole-directory symlink if present, and adds per-package symlinks for packages found in those roots. It only produces the command; it does not run it itself.

**Call relations**: js_repl calls this just before running Node.js. The returned command is passed to the sandbox shell so package resolution is prepared before the user’s JavaScript code executes.

*Call graph*: called by 1 (js_repl).


##### `_candidate_source`  (lines 184–190)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be run for the next REPL call. It combines the previous successful session code with the new code, unless the user asked to reset.

**Data flow**: It receives the sandbox context, the path of the saved REPL state file, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state, it returns just the new code plus a trailing newline. If saved state exists, it reads that file and appends the new code. The returned string is the candidate session that will be tested.

**Call relations**: Both js_repl and xlsx_repl use this before they run an interpreter. Those callers later save the candidate source only if the interpreter exits successfully, which is how failed experiments are kept out of future REPL state.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 193–204)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Packages interpreter output into the standard tool result format. It gives the caller stdout, stderr, and the exit code as JSON text, and can include images for JavaScript runs.

**Data flow**: It receives stdout, stderr, an exit code, and optionally image objects. It creates a JSON text block containing the three process details, adds any images after that text block, and marks the whole tool result as an error when the exit code is not zero. It returns a ToolResult object.

**Call relations**: js_repl and xlsx_repl call this at the end of a run. It uses TextContent and ToolResult to translate raw process output into the response shape expected by the wider tool system.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_emitted_images`  (lines 212–223)

```
async def _emitted_images(ctx: ToolContext) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code chose to return through emitImage. This lets browser automation or visualization code send screenshots or generated graphics back inline with the normal text output.

**Data flow**: It receives the tool context and checks whether the JavaScript image log file exists in the sandbox. If the file is missing, it returns an empty tuple. If present, it reads the file, looks at the latest image entries up to the configured limit, validates each JSON line, skips malformed lines, and converts valid entries into ImageContent objects. It returns the collected images.

**Call relations**: Only js_repl calls this, after Node.js finishes. The images it returns are passed into _repl_result so they appear alongside stdout, stderr, and the exit code.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 226–239)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a persistent JavaScript/Node.js session inside the sandbox. It is meant for tasks like browser automation, website testing, game interaction, and producing images from JavaScript code.

**Data flow**: It receives the tool context and validated JavaScript input. It builds the candidate session source, writes a temporary run file that includes the emitImage setup plus the session code, clears any old emitted-image log, links global Node packages, and runs Node.js with a timeout. It records a metric, saves the candidate source as the new session state only if Node exits successfully, then returns stdout, stderr, the exit code, and any emitted images.

**Call relations**: This is the handler registered for the js_repl tool in manifest. During a run it relies on _candidate_source for persistence, global_modules_link for import support, _meter_run for monitoring, _emitted_images for image output, and _repl_result for the final tool response.

*Call graph*: calls 5 internal fn (_candidate_source, _emitted_images, _meter_run, _repl_result, global_modules_link); 1 external calls (quote).


##### `xlsx_repl`  (lines 242–251)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a persistent Python session for Excel spreadsheet work, especially using openpyxl. It lets the agent load workbooks, keep variables between calls, and return structured values through a variable named result.

**Data flow**: It receives the tool context and validated Python input. It builds the candidate session source, writes a temporary Python run file with an added footer that prints result as JSON when present, and runs python3 with a timeout. It records a metric, saves the candidate source as the new session state only when the run succeeds, and returns stdout, stderr, and the exit code.

**Call relations**: This is the handler registered for the xlsx_repl tool in manifest. It shares the same persistence pattern as js_repl through _candidate_source, reports run status through _meter_run, and formats the final answer through _repl_result.

*Call graph*: calls 3 internal fn (_candidate_source, _meter_run, _repl_result); 1 external calls (quote).


##### `manifest`  (lines 254–274)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, tools, skill folders, and sandbox internet setting. This is how the rest of the application discovers and offers the REPL tools.

**Data flow**: It takes no input. It creates two ToolDef entries, one for JavaScript and one for Excel/Python, pointing each to its input model and handler function. It also creates SkillSpec entries for the packaged data skills and returns a Manifest object with sandbox internet access enabled.

**Call relations**: The extension loader calls this when registering the extension. The returned manifest connects user-facing tool names to js_repl and xlsx_repl, and makes the skill directories available for later loading.

*Call graph*: 3 external calls (__init__, __init__, __init__).

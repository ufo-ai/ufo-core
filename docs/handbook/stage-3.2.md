# Skill and user-authored skill loading  `stage-3.2`

This stage is behind-the-scenes setup for reusable “skills,” which are small folders of instructions and helper files the agent can use later during a work turn. The empty __init__.py file simply tells Python that the built-in skills folder is importable, like putting a label on a drawer. The runtime.py file does the main loading work: it defines what counts as a skill, reads skill folders, checks their names and contents, sorts them when one skill depends on another, and prepares their instructions and files so the agent can copy them into its safe working area. The sample probe.py is a tiny test tool for a sample skill; it prints a known success message so people or automated checks can confirm the skill can run. The skill_create extension adds workspace-authored skills. Its manifest.py connects save, view, update, delete, and load operations to the larger system. Its store.py is the gatekeeper: it keeps these user skills private to one workspace, validates them, uses safe names, and prevents them from overwriting built-in skills.

## Files in this stage

### Skill package foundation
The core skill package is established and the runtime defines how reusable skill folders are discovered, validated, dependency-ordered, and exposed.

### `core/src/ufo/skills/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that a folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may hold useful tools, but the label itself does not do the work. Here, the drawer is `ufo.skills`, which likely contains modules that define or support “skills” elsewhere in the project. Without this file, some Python setups or tools might not recognize the folder as a package, which could make imports less reliable. Because the file is empty, it does not run setup code, expose shortcuts, or change behavior when the package is imported. Its value is structural: it helps organize the codebase and gives the rest of the system a stable namespace for skill-related code.


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during conversation turns`

A skill is a small package of help for the agent: a folder with a `SKILL.md` file for instructions, plus optional extra files such as templates or scripts. This file is the bridge between those folders on disk and what the running agent sees during a conversation.

First, it knows how to parse `SKILL.md`: the top section is YAML frontmatter, which is structured metadata like the skill name, description, and dependencies; the rest is the plain instruction text the agent will read. It checks that the declared name matches the folder name, so a skill cannot quietly pretend to be something else.

Second, it can discover nested skills. A child skill lives inside another skill’s folder, but nesting is only naming and placement. If the child needs the parent’s files, it must explicitly list the parent as a dependency.

Third, `SkillRegistry` is the lookup table of all available skills. When a skill is loaded, the registry expands it into a closure: the requested skill plus every dependency it needs, without duplicates or infinite loops.

Finally, the file prepares the actual context shown to the agent and mounts the skill files under `.skills/` in the sandbox. Think of it like checking out a toolbox: the agent gets the instructions, and the files are placed on the workbench at predictable paths.

#### Function details

##### `RuntimeSkill.mounted_files`  (lines 59–60)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Builds the complete set of files that should be copied into the sandbox for one skill. It always includes the original `SKILL.md`, plus any bundled asset files.

**Data flow**: It reads the skill’s saved raw `SKILL.md` text and its stored asset files. It turns the markdown into bytes, combines it with the asset file bytes, and returns a dictionary from relative file path to file content.

**Call relations**: When `mount_skill` is ready to place a skill into the sandbox, it calls this method to learn exactly which files must be written. This keeps the mounting step simple: it only has to write the paths and bytes it is given.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 62–63)

```
def mount_root(self) -> str
```

**Purpose**: Returns the sandbox directory where this skill’s files should live. This gives every skill a predictable home under the shared `.skills` folder.

**Data flow**: It reads the skill’s registry name and joins it to the fixed skills mount directory. The result is a string path such as the skill’s root folder inside the workspace.

**Call relations**: When `mount_skill` writes files, it first calls this method to find the correct root path. Nested skill names naturally become nested mount paths because the name can contain slashes.

*Call graph*: called by 1 (mount_skill).


##### `LoadedSkill.prompt_body`  (lines 75–85)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the text block that will be shown to the agent for one loaded skill. It labels whether the skill was requested directly or was pulled in as a dependency, then includes the skill’s instructions.

**Data flow**: It reads the wrapped `RuntimeSkill` and the optional `dependency_of` name. It builds a header, adds a dependency note if needed, appends the skill instruction body, and returns that text.

**Call relations**: This is the per-skill building block used when creating the full loaded context. It does not include asset file contents; those are mounted separately and shown later through a file tree.


##### `LoadedSkills.reseed`  (lines 101–118)

```
def reseed(self, loads: Iterable[tuple[LoadedSkill, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Refreshes the record of which skill instructions are already in the model’s current context. This prevents the system from repeating long instructions that the agent has already seen.

**Data flow**: It receives past skill-load closures and optional preloaded skills. It clears the old tracking state, adds every loaded skill name to `in_context`, marks directly requested skills in `asked_for`, and also records preloaded skills as present but not user-requested.

**Call relations**: It calls `LoadedSkills.reset` before rebuilding the record from scratch. This is important because the tracker is meant to match the current conversation window, not simply accumulate history forever.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 120–125)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of skills the agent explicitly asked for, then clears the tracker. This is used when the system needs to carry only the important reload hints across a boundary where full instruction text may be dropped.

**Data flow**: It reads the `asked_for` set, sorts it into a stable tuple, clears both tracking sets, and returns the tuple of names.

**Call relations**: It calls `LoadedSkills.reset` after collecting the names. Its output can later be used to reload those direct skills, which will bring their dependencies back through the registry.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 127–129)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered loaded-skill state. It is the shared cleanup step for rebuilding or draining the tracker.

**Data flow**: It takes the current `in_context` and `asked_for` sets and empties both. It returns nothing; the change is made to the tracker object itself.

**Call relations**: Both `LoadedSkills.reseed` and `LoadedSkills.drain` call this function when they need a clean slate. It is deliberately small so both operations clear state in exactly the same way.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 132–138)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a `SKILL.md` file into its metadata section and its instruction body. It also enforces that the file starts and ends its metadata block in the expected format.

**Data flow**: It receives the full text of `SKILL.md`. It checks for the opening `---` fence, finds the closing fence, and returns two strings: the YAML metadata text and the markdown body. If either fence is missing, it raises an error.

**Call relations**: `parse_skill_content` calls this before reading the skill’s name, description, and dependencies. This makes malformed skill files fail early with a clear message.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 141–146)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate subfolders that are themselves skills. A subfolder counts as a child skill only if it contains its own `SKILL.md` file.

**Data flow**: It receives a skill directory path, looks at its direct children on disk, filters for directories containing `SKILL.md`, sorts them, and returns the matching paths.

**Call relations**: `parse_skill` uses this to avoid treating child skill files as part of the parent’s asset bundle. `discover_skills` uses it to recursively register child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 149–182)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory set of skill files into a validated `RuntimeSkill` object. This lets the project parse skills the same way whether they came from disk, storage, or a sandbox export.

**Data flow**: It receives a claimed directory name, a mapping of file paths to bytes, and optional registry naming information. It reads `SKILL.md`, splits and parses the YAML metadata, checks that the frontmatter name matches the directory name, collects non-`SKILL.md` files as assets, and returns a `RuntimeSkill` containing metadata, instructions, dependencies, assets, and the original markdown.

**Call relations**: `parse_skill` calls this after reading files from disk. It relies on `_split_frontmatter` for the markdown structure and `yaml.safe_load` to read the metadata safely.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 185–194)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill folder from disk and parses it into a `RuntimeSkill`. It excludes nested child-skill folders so the parent skill does not accidentally absorb the child’s files.

**Data flow**: It receives a directory path and optional registry naming information. It finds child skill directories, reads all ordinary files outside those child subtrees into memory, and passes those bytes to `parse_skill_content`. The result is one validated runtime skill.

**Call relations**: `discover_skills` calls this for each folder it is registering. It uses `_child_skill_dirs` to separate parent assets from child skill packages.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 197–215)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning a flat lookup map by registry name. This makes nested folders easy to load by names such as `parent/child`.

**Data flow**: It receives a skill directory and optional parent naming information. It parses the current directory as one skill, then finds child skill directories and recursively discovers each child with a path-style name. It returns a dictionary from skill name to `RuntimeSkill`.

**Call relations**: `_load_core_skills` calls this while building the built-in skill set. Inside its recursion, it calls `parse_skill` for the current folder and `_child_skill_dirs` to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 218–225)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the project’s built-in skills from the core skills directory. This creates the default skill collection available before user or pack-provided skills are added.

**Data flow**: It receives a root directory, scans its visible child directories, discovers skills under each one, and combines them into a dictionary keyed by skill name.

**Call relations**: This runs when the module is imported to build `CORE_SKILLS_BY_NAME` and the core registry. It delegates the actual recursive parsing to `discover_skills`.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 242–247)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up one skill by name and gives a helpful error if it does not exist. This is the registry’s safe front door for skill lookup.

**Data flow**: It receives a skill name, checks the registry dictionary, and returns the matching `RuntimeSkill`. If the name is missing, it builds a message listing available skills and raises a `ValueError`.

**Call relations**: `SkillRegistry.closure` and its inner dependency-walking helper call this whenever they need to turn a name into the actual skill object. The clear error helps callers diagnose misspelled or unavailable skills.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 249–272)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Expands requested skill names into the full set of skills that must be loaded, including dependencies. It keeps direct requests first, avoids duplicates, and is safe even if dependencies form a cycle.

**Data flow**: It receives one or more skill names. It first records each requested skill as directly loaded, then walks each requested skill’s dependencies, adding each missing dependency once and remembering which skill pulled it in. It returns an ordered tuple of `LoadedSkill` entries.

**Call relations**: The engine’s skill-loading flow calls this when it needs to know what one load request really includes. It uses `SkillRegistry.named` for lookups and creates `LoadedSkill` objects that later drive prompt text and file mounting.

*Call graph*: calls 1 internal fn (named); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 262–267)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill to a closure and then walks that dependency’s own dependencies. It is the small recursive worker inside `SkillRegistry.closure`.

**Data flow**: It receives a `RuntimeSkill` and the name of the skill that depends on it. If the skill is already loaded, it stops. Otherwise it records the skill with its dependency label, looks up each dependency by name, and repeats the process.

**Call relations**: This helper is called only from `SkillRegistry.closure`. It calls `SkillRegistry.named` to resolve dependency names and creates `LoadedSkill` entries so the final context can explain why each dependency appeared.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 274–282)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Returns the public list of top-level skills that can be shown in the system prompt. Child skills are intentionally left out because they are reached through their parent skill’s instructions.

**Data flow**: It reads the registry’s skills in registration order, keeps only skills without a parent, and returns tuples of skill name and description.

**Call relations**: This is used wherever the agent needs a compact catalog of available skills. It does not load anything; it only describes what the agent may ask to load.


##### `SkillRegistry.merged_with`  (lines 284–296)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that adds saved user skills after the base skills. It refuses to let user-controlled skills replace core or pack skills with the same name.

**Data flow**: It copies the current registry dictionary, then examines each user skill. If the name is unused, it adds the user skill; if the name collides, it logs that the shadowing attempt was refused and skips it. It returns a new `SkillRegistry`.

**Call relations**: This is used when combining the trusted base registry with workspace-specific user skills. It calls the logging system when rejecting a collision, preserving the rule that built-in or pack skills cannot be silently overwritten.

*Call graph*: 2 external calls (__init__, log).


##### `_mounted_tree`  (lines 302–320)

```
def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Builds a compact text tree showing every file mounted for a skill load. This gives the agent a readable map of where the skill files were placed.

**Data flow**: It receives the loaded skill entries, asks each skill for its mounted files, sorts the combined paths, and formats directories and filenames as an indented tree under the `.skills` mount directory. It returns that tree as text.

**Call relations**: `loaded_context` calls this after preparing the instruction blocks. The tree is shown once for the whole load closure, so repeated directory prefixes are not wasted in the prompt.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 323–337)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Creates the full text that a skill load contributes to the model’s context. It includes new skill instructions, a note for already-seen skills, and a file tree for everything mounted.

**Data flow**: It receives the closure of loaded skills and a collection of skill names already in context. It builds prompt blocks only for skills not already present, adds a short note naming repeated skills, appends the mounted-file tree, and returns the combined text.

**Call relations**: This is shared by normal skill loading and subagent preloading so skills look the same in both cases. It calls `_mounted_tree` to describe the files that will be available in the sandbox.

*Call graph*: calls 1 internal fn (_mounted_tree).


##### `mount_skill`  (lines 340–343)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Copies one skill’s files into the sandbox workspace so the agent can read and use them. This is the actual file-writing step of skill loading.

**Data flow**: It receives a sandbox session and a runtime skill. It asks the skill for its mount root and its files, then writes each file’s bytes into the sandbox at the corresponding path. It returns nothing after the writes complete.

**Call relations**: After a registry closure decides which skills are needed, this function is used to place each skill’s files where the agent can access them. It calls `RuntimeSkill.mount_root`, `RuntimeSkill.mounted_files`, and the sandbox session’s `write_file` method.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files).


### Sample skill probe
A minimal sample skill probe provides a simple execution check for skill availability.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `probe or health-check time`

This file acts like a simple “is it alive?” check for the sample skill. It does not define any reusable code or perform any real skill work. Instead, Python runs the one line in the file immediately, and that line prints `sample-skill-probe-ok` to the screen or calling process. The value of this file is its predictability: if something launches this probe and sees that exact message, it knows the file was found, Python could run it, and the basic sample-skill probe path is working. Without it, there would be no very small, unambiguous signal that the sample skill can be reached and executed. Think of it like pressing a doorbell to confirm the wiring works; it does not open the door, but it proves the connection is live.


### Workspace-authored skill storage
The skill creation extension wires workspace-authored skills into the system and persists them with validation, safe naming, and workspace isolation.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `startup registration, object operations, and per-turn skill loading`

A skill here is a small bundle of text files, with a required SKILL.md file that names and describes it. This file defines the public shape of that bundle, registers it as a workspace object called “skill,” and tells the main system how to bring saved skills back on later turns. Without it, users could write skill files in the temporary workspace, but they would not have a safe way to persist them or make them available to load later.

The main idea is safety and repeatability. When a skill is saved, any file reference like {from: "path"} is read from the conversation workspace and turned into stored text. The saved object never points back at the temporary sandbox. The file also enforces limits: only a bounded number of files, a bounded total size, and UTF-8 text only. It also avoids leaking full file bodies when someone asks for the object; instead, it returns a digest, which is like a fingerprint, so unchanged files can be kept without pasting their content back into the request.

The SkillObjects class is the object interface: list, get, status, apply, and delete. At startup, manifest() registers this object kind, the teaching skill that explains how to create skills, and a runtime provider that adds saved user skills into the turn’s skill registry.

#### Function details

##### `_require_ext`  (lines 100–103)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure a tool call has the extension context it needs. The extension context is the object that gives access to this extension’s storage and workspace identity.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it unchanged. If not, it stops immediately with an error, because the skill object cannot safely read or write storage without that information.

**Call relations**: The object methods use this before touching the user skill store. It is the safety check at the doorway for listing, reading, saving, deleting, and loading stored skill files.

*Call graph*: called by 5 (_files, apply, delete, get, list).


##### `_text`  (lines 106–112)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This checks that a skill file is real text, not binary data. Skills are intentionally limited to UTF-8 text files so they can be parsed, reviewed, and stored safely.

**Data flow**: It receives a skill-relative path and raw bytes. It tries to decode the bytes as text. If decoding works, it returns the text; if not, it raises a clear error naming the file that is not valid text.

**Call relations**: SkillObjects._resolve calls this after gathering each file’s bytes. That means every inline file, workspace-sourced file, and kept stored file passes the same text-only gate before saving.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 122–128)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the saved user-created skills for the current workspace. It gives a short row for each skill, including its name and a trimmed description.

**Data flow**: It receives the tool context and a list query. It gets the extension context, loads all stored skills for the workspace, turns each one into a list row, and then applies the query’s paging rules before returning the page.

**Call relations**: The object system calls this when someone asks to list skill objects. It relies on UserSkillStore for the actual saved skills and hands the rows to the shared object paging helper so listing behaves like other object kinds.

*Call graph*: calls 1 internal fn (_require_ext); 3 external calls (__init__, __init__, object_page).


##### `SkillObjects.get`  (lines 130–148)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This returns the stored record for one saved skill, but deliberately does not return the file contents. Instead, it returns a fingerprint and size for each file so callers can keep unchanged files without copying their bodies into the request.

**Data flow**: It receives a tool context and skill name. It loads the skill’s stored files and timestamps. If either is missing, it returns nothing. Otherwise, it computes a SHA-256 digest, a standard content fingerprint, for each file and returns those references in an object detail record.

**Call relations**: The object system calls this when a caller wants to inspect one skill object. It uses SkillObjects._files to fetch bytes, uses the store again for timestamps, and builds FileRef values that can later be passed back to SkillObjects.apply to keep files unchanged.

*Call graph*: calls 2 internal fn (_files, _require_ext); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 150–158)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This gives a quick health-style summary of one saved skill. It reports the skill description, file count, and total stored byte size.

**Data flow**: It receives a tool context and skill name. It loads the stored files; if the skill is missing, it returns nothing. If present, it parses the skill content to read its description, counts the files, totals their sizes, and returns those facts as a small dictionary.

**Call relations**: The object system calls this when it needs a lightweight status view. It reuses SkillObjects._files for storage access and the shared skill parser so the description is interpreted the same way it is when skills are loaded.

*Call graph*: calls 1 internal fn (_files); 1 external calls (parse_skill_content).


##### `SkillObjects.apply`  (lines 160–172)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None) -> None
```

**Purpose**: This creates or updates a saved user skill. It turns the requested file specification into actual text bytes, checks size limits, and then saves the validated skill for the current workspace.

**Data flow**: It receives the tool context, the skill name, the new specification, and the previous specification if any. It rejects specs with too many files, resolves inline text, workspace file references, and unchanged-file fingerprints into complete file contents, checks the total byte limit, and writes the result to the user skill store.

**Call relations**: The object system calls this when a manifest is applied. It delegates the tricky file-gathering work to SkillObjects._resolve, then hands the complete bundle to UserSkillStore.save, along with the names of already-known skills so the store can prevent a user skill from replacing a built-in one.

*Call graph*: calls 2 internal fn (_resolve, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 174–176)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This removes a saved user-created skill from the current workspace. It is the delete operation for the skill object kind.

**Data flow**: It receives the tool context and skill name. It gets the extension context, opens the user skill store, and asks it to delete that skill under the current workspace id. It returns no content after the deletion request completes.

**Call relations**: The object system calls this when a skill object is deleted. It is a thin bridge from the object API to UserSkillStore.delete.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._files`  (lines 178–180)

```
async def _files(self, ctx: ToolContext, name: str) -> dict[str, bytes] | None
```

**Purpose**: This is the shared helper for loading the raw stored files of one user skill. It keeps the storage lookup in one place for get, status, and update resolution.

**Data flow**: It receives the tool context and skill name. It gets the extension context, opens the user skill store for the current workspace, and returns the saved file map if it exists, or nothing if it does not.

**Call relations**: SkillObjects.get, SkillObjects.status, and SkillObjects._resolve call this whenever they need the actual saved bytes. It hides the repeated store lookup so those methods can focus on their own jobs.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_resolve, get, status); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 182–230)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns a user’s requested skill file specification into a complete set of file bytes ready to save. It understands three ways to provide a file: inline text, a workspace file reference, or a digest that keeps an already-stored file unchanged.

**Data flow**: It receives the tool context, skill name, and requested spec. First it loads any existing stored files so FileRef entries can be checked against their SHA-256 fingerprints. Then it gathers all {from: ...} workspace paths, reads them inside the sandbox, decodes the returned base64 content, and combines those bytes with inline strings and kept files. Finally, it verifies every file is UTF-8 text and returns the full path-to-bytes map.

**Call relations**: SkillObjects.apply calls this before saving. This function is the careful middle step between a compact manifest and a safe stored skill: it calls SkillObjects._files to verify kept files, uses the sandbox to read workspace files, and calls _text so non-text files are rejected before UserSkillStore.save ever sees them.

*Call graph*: calls 2 internal fn (_files, _text); called by 1 (apply); 6 external calls (b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 257–260)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This supplies the current workspace’s saved user skills to the main system at turn time. It is what makes a saved skill show up later as something loadable.

**Data flow**: It receives the extension context. It opens the user skill store for the current workspace and returns all saved runtime skill records as a tuple.

**Call relations**: The Manifest created by manifest() registers this as the runtime skills provider. On later turns, the core system calls it and merges these saved skills beside built-in and pack-provided skills.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 263–270)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s registration function. It tells the host system this extension’s name, version, object kind, teaching skill, and runtime skill provider.

**Data flow**: It takes no inputs. It builds and returns a Manifest containing the skill object definition, the bundled create-skill teaching skill path, and the function used to load saved user skills each turn.

**Call relations**: The extension loader calls this at startup or extension discovery time. The returned Manifest is the package label and instruction sheet the host uses to wire this file’s object operations and runtime skill loading into the rest of the system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and turn startup`

A “skill” here is a small folder of files, usually led by `SKILL.md`, that teaches the agent a reusable behavior. This file is the storage cupboard for skills created by users inside one workspace. Without it, a skill made during one turn would disappear later, or worse, could leak into another workspace or pretend to be a built-in skill.

The main class, `UserSkillStore`, works through the extension context, which gives it a database transaction. A transaction is a safe database session where related reads and writes happen together. When a skill is saved, the store first checks that the name is a simple lowercase slug, like `write-summary`, not a path or strange string. It then parses the skill using the same parser used for normal packaged skills, so user-created skills must meet the same rules. It also refuses names already used by core or pack skills, because a user skill must not “shadow” or replace trusted system skills.

For storage, the skill folder is turned into a JSON object whose file contents are base64 text. Base64 is a way to store raw bytes safely inside text. A SHA-256 digest, like a fingerprint, records the exact saved bundle. On each turn, `load_all` reads the workspace’s saved skills, decodes them, parses them again, and returns only the ones that still load. If one saved skill is corrupt, it is logged and skipped instead of breaking the whole workspace.

#### Function details

##### `UserSkillStore.save`  (lines 86–147)

```
async def save(self, workspace_id: UUID, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves a user-authored skill for one workspace after checking that it is safe and valid. It is used when the agent or user has produced a skill folder that should survive future turns.

**Data flow**: It receives a workspace ID, the desired skill name, a map of file paths to file bytes, and the names of skills already available this turn. It checks the name, parses the files into a runtime skill, checks whether the workspace already owns that name, refuses collisions with built-in or pack skills, and enforces the workspace skill limit for new names. It then base64-encodes the files, stores the bundle as JSON, creates a SHA-256 fingerprint for it, and either updates the existing database row or inserts a new one. It returns the parsed skill that was just saved.

**Call relations**: This is the main write path for the store. During saving it asks `_owns` whether this workspace already has the skill name, and asks `_count` how many saved skills the workspace has before allowing a new one. It hands the raw files to the shared skill parser so saved user skills follow the same rules as other skills, then writes the final bundle through the extension database transaction.

*Call graph*: calls 2 internal fn (_count, _owns); 9 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 149–186)

```
async def load_all(self, workspace_id: UUID) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved user skill for a workspace so the runtime can include them in the skill registry for the current turn. It keeps one broken saved skill from stopping all other skills from working.

**Data flow**: It receives a workspace ID and reads all saved skill names and stored content for that workspace from the database. For each row, it validates the stored JSON shape, decodes each base64 file back into bytes, and parses the files into a runtime skill. Valid skills are collected and returned as a tuple; invalid or corrupt stored skills are logged and skipped.

**Call relations**: This is the read path used when the system is preparing the workspace’s available skills for a turn. It relies on the same parser used by saving, so loaded skills are checked again before use. If parsing fails, it does not pass the error upward; it logs the problem and continues so the rest of the workspace can still run.

*Call graph*: 3 external calls (b64decode, select, parse_skill_content).


##### `UserSkillStore.files`  (lines 188–203)

```
async def files(self, workspace_id: UUID, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original file bundle for one saved skill. This is useful when something needs to inspect, edit, or display the saved skill’s actual files rather than just load it as a runtime skill.

**Data flow**: It receives a workspace ID and skill name, then looks up that exact saved skill in the database. If no row exists, it returns `None`. If a row exists, it validates the stored JSON, decodes each base64 file body back into bytes, and returns a dictionary from file path to file bytes.

**Call relations**: This function is a focused lookup helper for callers that need one skill’s stored files. Unlike `load_all`, it does not parse the files into a runtime skill; it simply reconstructs the saved folder contents from storage.

*Call graph*: 2 external calls (b64decode, select).


##### `UserSkillStore.delete`  (lines 205–212)

```
async def delete(self, workspace_id: UUID, name: str) -> None
```

**Purpose**: Removes one saved user skill from a workspace. It is used when a user or tool wants a skill to stop being available in future turns.

**Data flow**: It receives a workspace ID and skill name. It opens a database transaction and deletes the row matching both values. It does not return a value, and deleting a missing skill simply leaves the database unchanged.

**Call relations**: This is the cleanup path for the store. Other save and load operations use the same workspace-and-name key, so deleting that row means later calls such as `load_all`, `files`, or `timestamps` will no longer find that skill.

*Call graph*: 1 external calls (delete).


##### `UserSkillStore.timestamps`  (lines 214–224)

```
async def timestamps(self, workspace_id: UUID, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Looks up when a saved skill was first created and when it was last updated. This is useful for showing history or deciding whether a saved skill has changed.

**Data flow**: It receives a workspace ID and skill name, then asks the database for that row’s creation and update times. If the skill is missing, it returns `None`. If present, it returns the two timestamps as a pair.

**Call relations**: This is a small read helper alongside `files`. It uses the same workspace-scoped lookup as the rest of the store, so it only reports dates for a skill owned by that workspace.

*Call graph*: 1 external calls (select).


##### `UserSkillStore._count`  (lines 226–234)

```
async def _count(self, workspace_id: UUID) -> int
```

**Purpose**: Counts how many user skills are currently saved in one workspace. It exists to enforce the limit that keeps each turn from becoming slow because too many saved skills must be loaded and parsed.

**Data flow**: It receives a workspace ID, queries the database for the number of rows belonging to that workspace, and returns that number as an integer. It does not change stored data.

**Call relations**: `save` calls this before adding a brand-new skill. If the count is already at the maximum, `save` refuses the new skill instead of letting the workspace grow without bound.

*Call graph*: called by 1 (save); 1 external calls (select).


##### `UserSkillStore._owns`  (lines 236–246)

```
async def _owns(self, workspace_id: UUID, name: str) -> bool
```

**Purpose**: Checks whether a workspace already has a saved skill with a given name. This helps distinguish between updating the workspace’s own skill and trying to override a core or pack skill.

**Data flow**: It receives a workspace ID and skill name, searches the database for a matching row, and returns `true` if one exists or `false` if not. It only reads data.

**Call relations**: `save` calls this early in the save process. If the workspace already owns the name, saving is treated as an update. If it does not own the name and that name is already in the current registry, `save` treats it as a collision with a trusted skill and refuses it.

*Call graph*: called by 1 (save); 1 external calls (select).

# Built-in and extension skill loading  `stage-8.1`

This stage is shared behind-the-scenes support used during a turn, when the agent needs reusable abilities called “skills.” A skill is a small bundle of instructions and optional helper files, like a recipe card with the tools needed to follow it. Some skills come with the core system, some come from extensions such as document, research, sweep, brief, sample, or scheduling features, and some are created by users.

The package marker file simply makes the core skills folder importable by Python, so the rest of the system can find it. The runtime file does the real loading work: it defines the shape of a skill, reads skill folders, follows any “this skill needs that skill too” links, and copies allowed files into a protected workspace. The model catalog file builds a live skill that lists available AI models, costs, and capabilities. The user skill store saves custom skills per workspace and agent, checks names, blocks replacing built-in skills, and keeps the registry from growing without limits.

## Files in this stage

### Skill runtime foundation
Package markers and runtime primitives define how skills are imported, loaded, resolved, and exposed safely inside a workspace.

### `core/src/ufo/skills/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.skills` using normal Python import paths.

Because the file is empty, it does not set up shared objects, run startup code, or re-export anything from the package. Its value is structural: it gives the project a clear place for skill-related code to live. You can think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer recognizable and usable by the rest of the workshop.

If this file were removed, behavior would depend on the Python version and packaging setup. Modern Python can sometimes import folders without `__init__.py`, but keeping this file makes the package boundary explicit and avoids surprises in tools, packaging, or older import behavior.


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during a conversation`

A skill is a small package of guidance for the agent: a folder with a `SKILL.md` file plus optional extra files. The `SKILL.md` starts with YAML frontmatter, which is structured metadata such as the skill name, description, and dependencies, followed by the human-readable instructions the agent should see.

This file is the bridge between those folders and the running agent. First, it can parse a skill from disk or from already-loaded bytes. It checks that the declared name matches the folder name, separates metadata from instructions, and keeps the original `SKILL.md` text exactly as written.

It also understands nested child skills. A child skill can live inside a parent folder and gets a name like `parent/child`, but nesting alone does not automatically load the parent. If one skill needs another, it must say so through `depends`.

At runtime, `SkillRegistry` acts like the skill catalog. Given one or more requested names, it finds those skills plus all their dependencies, only once, and in a safe order. `loaded_context` then builds the text shown to the model: new workflows are included, already-seen workflows are summarized, and a small file tree tells the agent where loaded files live. `load_skills` resolves bundled files already under `$UFO_HOME/skills` and installs materialized user files into the same tree.

#### Function details

##### `skill_root`  (lines 44–47)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the `$UFO_HOME/skills` path where one skill’s files live.

**Data flow**: It takes a skill name as text, combines it with the shared skills directory, and returns `$UFO_HOME/skills/example-skill`. It does not touch the filesystem.

**Call relations**: A `RuntimeSkill` asks this helper for its location through `RuntimeSkill.root`. `install_skill` uses that location when tests write one skill directly.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 66–67)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Returns all files that should be copied into the sandbox for this skill. This includes the original `SKILL.md` plus any bundled asset files.

**Data flow**: It reads the skill’s stored raw `SKILL.md` text and its stored asset file list, turns the markdown into bytes, and returns a dictionary from file path to file contents. The result is ready to be written into the sandbox.

**Call relations**: The bundle builder, loader, and direct test installer use this method so all paths receive the same file set and preserve `SKILL.md` exactly.

*Call graph*: called by 1 (install_skill).


##### `RuntimeSkill.root`  (lines 69–70)

```
def root(self) -> str
```

**Purpose**: Reports the sandbox folder where this particular skill lives.

**Data flow**: It reads the skill’s `name`, passes that name to `skill_root`, and returns the resulting path string. It does not change the skill or the sandbox.

**Call relations**: Load and direct-install paths call this before resolving files. Internally, this method delegates path construction to `skill_root`.

*Call graph*: calls 1 internal fn (skill_root).


##### `LoadedSkill.prompt_body`  (lines 82–92)

```
def prompt_body(self) -> str
```

**Purpose**: Builds the text block that one loaded skill contributes to the model’s context. It labels whether the skill was directly requested or came along as a dependency, then includes the skill’s instructions.

**Data flow**: It reads the `LoadedSkill`’s skill name, optional `dependency_of` marker, and instruction text. It produces a markdown block with a heading and the workflow body; it does not include asset file contents.

**Call relations**: This is the per-skill building block used when loaded skills are turned into prompt text. It makes sure the model can tell the difference between a skill it asked for and one loaded because another skill needed it.


##### `LoadedSkills.reseed`  (lines 108–125)

```
def reseed(self, loads: Iterable[tuple[LoadedSkill, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker of which skill workflows are already visible to the model. This prevents repeated loads from pasting the same instructions into the conversation again.

**Data flow**: It receives previous loaded skill groups and optional preloaded skills, clears the old tracking state, then records every skill currently in context. Skills directly requested by the agent are also recorded separately as `asked_for`; preloaded skills are counted as visible but not as requested.

**Call relations**: It starts by calling `LoadedSkills.reset` so the tracker reflects the current conversation window rather than stale history. Other parts of the system can then use this tracker to decide whether a future load should repeat full instructions or just mention that they are already present.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 127–132)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the skill names the agent explicitly asked for, then clears the tracker. This is useful when a boundary, such as context compaction, needs to remember what to reload later.

**Data flow**: It reads the `asked_for` set, sorts it into a stable tuple of names, clears both tracking sets, and returns the names. After it runs, the object no longer claims any skills are in context.

**Call relations**: It calls `LoadedSkills.reset` after collecting the names. The returned names can be carried forward so the agent can reload the same requested skills and regain their dependencies.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 134–136)

```
def reset(self) -> None
```

**Purpose**: Clears all memory of which skills are in the model context and which were directly requested. It is the simple reset button for the loaded-skill tracker.

**Data flow**: It takes no new input beyond the existing object. It empties the `in_context` and `asked_for` sets, leaving both blank.

**Call relations**: `LoadedSkills.reseed` uses it before rebuilding state from known loads, and `LoadedSkills.drain` uses it after exporting the requested skill names.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 139–145)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a `SKILL.md` file into its metadata section and its instruction body. It also checks that the file uses the required frontmatter format.

**Data flow**: It takes the full markdown text, verifies that it starts with the `---` fence, finds the closing fence, and returns two strings: the YAML metadata and the remaining body. If the fences are missing or incomplete, it raises an error instead of guessing.

**Call relations**: `parse_skill_content` calls this before reading the YAML metadata. This keeps malformed skill files from being accepted silently.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 148–153)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child folders that are themselves skills. A folder counts as a child skill only if it contains its own `SKILL.md`.

**Data flow**: It reads the entries directly inside a skill directory, keeps only directories with a `SKILL.md` file, sorts them, and returns their paths. It does not look through every nested level itself.

**Call relations**: `parse_skill` uses this to exclude child skill folders from the parent’s asset files. `discover_skills` uses it to recurse into each child and register it separately.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 156–189)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory collection of skill files into a validated `RuntimeSkill`. This is used when the files have already been gathered as bytes, not necessarily read directly from disk at this moment.

**Data flow**: It receives a claimed directory name, a mapping of file paths to bytes, and optional registry naming information. It finds and decodes `SKILL.md`, splits frontmatter from body, parses the YAML metadata, checks that the metadata name matches the folder name, gathers non-`SKILL.md` assets, and returns a `RuntimeSkill` with description, instructions, dependencies, parent, files, and original markdown.

**Call relations**: `parse_skill` calls this after collecting files from a real directory. It relies on `_split_frontmatter`, YAML parsing, and `RuntimeSkill` construction to turn raw files into the standard runtime object.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 192–201)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads a skill folder from disk and parses it into a `RuntimeSkill`. It treats nested child skills as separate packages, not as ordinary files belonging to the parent.

**Data flow**: It receives a filesystem path to a skill directory. It first finds child skill directories, then walks the parent directory for files while skipping those child subtrees, reads each file as bytes, and passes the collected files to `parse_skill_content`. The output is one parsed skill for that directory.

**Call relations**: `discover_skills` calls this for each skill directory it registers. It uses `_child_skill_dirs` to avoid mixing parent and child files, then hands parsing to `parse_skill_content`.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 204–222)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Finds a skill and all of its nested child skills, then returns them in one flat lookup table. This lets the registry address child skills by names like `parent/child`.

**Data flow**: It receives a skill directory plus optional registry and parent names. It parses the current directory, stores it by its registry name, finds immediate child skill directories, recursively discovers each child with a path-style name, and returns one dictionary of all discovered skills.

**Call relations**: `_load_core_skills` uses this while building the built-in skill set at import/startup time. Inside the recursion, it calls `parse_skill` for the current folder and `_child_skill_dirs` to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 225–232)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the built-in skills that ship with the project. These become the core skill catalog available before packs or user-defined skills are added.

**Data flow**: It receives the root directory containing core skills, lists visible skill directories, discovers each one and its children, and combines them into a dictionary keyed by skill name. The result is used to create the module-level core skill registry.

**Call relations**: This runs when the module is imported to populate `CORE_SKILLS_BY_NAME` and related constants. It calls `discover_skills` for each core skill directory.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 249–254)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up one skill by name and gives a clear error if it is not available. This is the registry’s front desk: ask for a name, get the matching skill or a list of valid choices.

**Data flow**: It reads the registry’s `by_name` dictionary using the requested name. If found, it returns the `RuntimeSkill`; if missing, it builds an error message listing available skill names and raises `ValueError`.

**Call relations**: `SkillRegistry.closure` and its inner dependency-walking helper call this whenever they need to turn a requested or dependent skill name into the actual skill object.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 256–279)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Computes the full set of skills needed for a load request: the directly requested skills plus all dependencies they pull in. It also records which skills were requested directly and which arrived because another skill depended on them.

**Data flow**: It receives one or more skill names. It first creates direct `LoadedSkill` entries for the unique requested names, then walks each skill’s `depends` list, adding each dependency once and labeling it with the skill that pulled it. It returns the ordered tuple of `LoadedSkill` entries.

**Call relations**: The engine’s skill-loading flow calls this to decide what must be mounted and shown to the model. It uses `SkillRegistry.named` for lookups and the nested `SkillRegistry.closure.add` helper for recursive dependency walking.

*Call graph*: calls 1 internal fn (named); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 269–274)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill and then adds that dependency’s dependencies. It is careful not to add the same skill twice, which also prevents dependency cycles from causing endless recursion.

**Data flow**: It receives a `RuntimeSkill` and the name of the skill that depended on it. If the skill is already in the loaded set, it stops; otherwise it stores a `LoadedSkill` marked as a dependency, looks up each dependency named by that skill, and repeats the process.

**Call relations**: This helper lives inside `SkillRegistry.closure` and is used only during closure calculation. It calls `SkillRegistry.named` to resolve dependency names and creates `LoadedSkill` records for newly discovered dependencies.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 281–289)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the list of skills that should appear in the public skill index shown to the model. It includes only top-level skills, not child skills.

**Data flow**: It reads the registry’s skills in registration order, keeps those without a parent, and returns pairs of skill name and description. It does not include instructions or files.

**Call relations**: This supports the system prompt’s skill catalog. Child skills are intentionally left out because the parent skill’s instructions are expected to explain when and how to use them.


##### `SkillRegistry.merged_with`  (lines 291–303)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes saved user skills after the base skills. It refuses to let a user skill replace a core or pack skill with the same name.

**Data flow**: It copies the current registry’s name-to-skill dictionary, then examines each user skill. If the name is new, it adds the skill; if the name already exists, it logs that the shadowing attempt was refused and skips it. It returns a new `SkillRegistry`.

**Call relations**: This is used when a workspace’s saved skills are added to the active catalog. It calls the logging function when a user skill collides and constructs a fresh registry for the merged result.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 309–327)

```
def _loaded_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Creates a compact tree of all files resolved for a skill load.

**Data flow**: It receives the loaded skill entries, sorts their file paths, and formats them as an indented tree under `$UFO_HOME/skills`.

**Call relations**: `loaded_context` calls this after building workflow instruction blocks. The tree covers the whole closure at once, so dependencies and requested skills appear together in one file map.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 330–344)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text that a skill load contributes to the model. It includes new skill instructions, notes any instructions already present, and appends the loaded-file tree.

**Data flow**: It receives loaded skill entries and a set of skill names already in context. For each new skill, it includes the prompt body; for each repeated skill, it adds its name to an “already loaded” note. It then appends `Loaded files:` followed by the tree from `_loaded_tree` and returns the final text.

**Call relations**: This is shared by normal skill loading and subagent preloading so skills look the same in both places. It calls `_loaded_tree` to describe the resolved files.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `install_skill`  (lines 347–355)

```
async def install_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Writes one skill’s files into the sandboxed workspace under that skill’s own `$UFO_HOME/skills/<name>/` folder. The containment check is important because skill file paths can come from user-controlled saved data.

**Data flow**: It receives a sandbox session and a `RuntimeSkill`. It gets the skill’s root and files, checks each file path so it stays inside that root, and writes the bytes into the sandbox. The sandbox filesystem changes; the function returns nothing.

**Call relations**: This test helper calls `RuntimeSkill.root`, `RuntimeSkill.all_files`, the containment helper, and the sandbox’s `write_file` method.

*Call graph*: calls 3 internal fn (write_file, root, all_files); 1 external calls (contained_relative).


### Built-in catalog skill
The model catalog skill uses the runtime to expose the live model registry as reusable built-in skill content.

### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a documentation problem: the list of available models must match what the system can actually run. If people wrote that list by hand, it could become stale when a model is added, removed, repriced, or changed. Instead, this file builds the catalog directly from the same model records the runtime uses for routing requests, pricing usage, and choosing prompts.

At startup, the system can call `model_catalog_skill` with a `ModelRegistry`, which is the live collection of known model descriptions. The function walks through every registered model, sorts them by model id, and renders a Markdown table. Each row shows useful facts: provider, knowledge cutoff, context window, input and output price, whether reasoning is supported, and what API surface the model uses. Think of it like printing a restaurant menu from the kitchen’s actual inventory system instead of from a separate paper note.

The result is wrapped as a `RuntimeSkill`, meaning it can be loaded and read like other skills in the system. The raw skill text also includes a small front matter header with the skill name and description, so it looks like a normal skill document while still being generated from live data.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This small helper turns a price stored in micro-dollars into a readable dollar amount. It is used so the catalog can show prices like `$1.25` instead of an internal integer unit.

**Data flow**: It receives an integer price measured in micro-USD per million tokens. It divides that by the number of micro-USD in one USD, formats the result with two decimal places, and returns a string with a dollar sign.

**Call relations**: When `model_catalog_skill` is building each model’s table row, it calls `_per_mtok` for the input price and again for the output price. The formatted strings are then placed into the catalog table.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function builds the actual model-catalog skill from the live model registry. Someone would use it during boot so users can inspect the models this deployment really supports.

**Data flow**: It receives a `ModelRegistry`, which contains the current model specifications. It reads each model’s id, provider, knowledge cutoff, context window, pricing, reasoning support, and API surface. It formats those facts into a Markdown table, combines that table with a name and description, and returns a `RuntimeSkill` containing both the readable instructions and the raw skill document.

**Call relations**: This is the main builder in the file. As it prepares the table, it hands price values to `_per_mtok` so they become human-readable dollar amounts. At the end, it creates a `RuntimeSkill`, which lets the generated catalog behave like a normal skill elsewhere in the runtime.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### User skill storage
User-created skill storage persists per-agent skills while validating names, avoiding built-in overrides, and enforcing limits.

### `extensions/skill_create/ufo_ext_skill_create/store.py`

`io_transport` · `request handling and cross-cutting persistence`

A “skill” here is a small bundle of files, such as a SKILL.md file plus any assets, that an agent can save and use later. This file is the safe storage layer for those bundles. Without it, user-authored skills could disappear between runs, collide with built-in skills, or be saved under unsafe names like paths with slashes.

The file defines the database table used to keep each saved skill. A skill is keyed by workspace, agent, and name, which means two agents can have different skills with the same name without mixing them up. The actual files are stored as JSON, with each file’s bytes converted to base64 text. Base64 is a common way to turn raw bytes into plain text so they can fit safely inside a database text column.

The main class, UserSkillStore, always works in the “current agent” scope. Think of it like a labeled drawer: every save, load, delete, and count operation opens only the drawer for the current workspace and agent. When saving, it checks the name, parses the skill to make sure it is usable, refuses to shadow core or pack skills, enforces the maximum number of saved skills, then inserts or updates the database row. When loading, it skips corrupt saved content and logs a warning instead of breaking all skill loading.

#### Function details

##### `UserSkillStore.save`  (lines 69–126)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves one user-created skill for the current workspace and agent. It checks that the name is safe, that the skill content can be parsed, that it does not illegally replace a built-in skill, and that the agent has not exceeded the saved-skill limit.

**Data flow**: It receives a skill name, a mapping of file paths to raw file bytes, and the set of names already present in the active skill registry. It reads the current workspace and agent, validates the name, parses the files into a runtime skill, checks whether this agent already owns that name, and counts existing user skills if needed. It then turns the files into base64 text, wraps them in the StoredSkill format, computes a sha256 digest as a fingerprint, and writes the row to the database by updating an existing skill or inserting a new one. It returns the parsed RuntimeSkill that can be registered or used immediately.

**Call relations**: This is the main write path for the store. During a save request, it calls UserSkillStore._owns to decide whether the operation is a new skill or an update, and UserSkillStore._count to enforce the per-agent cap only when adding a new skill. It also hands the incoming file bundle to parse_skill_content before saving, so bad skill content is rejected before it becomes persistent data.

*Call graph*: calls 2 internal fn (_count, _owns); 10 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, agent_current, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 128–162)

```
async def load_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved user skill belonging to the current workspace and agent. It is used when the system needs to rebuild the agent’s available user-authored skills from the database.

**Data flow**: It reads the current workspace and agent, queries the database for all matching saved skill names and stored content, and processes them in name order. For each row, it validates the stored JSON shape, decodes each base64 file back into bytes, and parses the file bundle into a RuntimeSkill. If one saved skill is corrupt, it logs a warning and continues with the rest. It returns a tuple of successfully loaded RuntimeSkill objects.

**Call relations**: This is the bulk read path. It relies on the same StoredSkill format that UserSkillStore.save writes, and it passes each decoded bundle to parse_skill_content so the rest of the system receives normal runtime skill objects rather than raw database rows.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 164–180)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original files for one saved user skill, if that skill exists for the current workspace and agent. This is useful when the system needs the stored bundle itself, not just the parsed runtime skill.

**Data flow**: It receives a skill name and reads the current workspace and agent. It looks up that exact database row. If no row exists, it returns None. If a row exists, it validates the stored JSON, decodes each base64 string back into raw bytes, and returns a dictionary from file path to file bytes.

**Call relations**: This is a focused read path for one skill. It reads the data written by UserSkillStore.save, but unlike UserSkillStore.load_all it does not parse the files into a RuntimeSkill; it hands back the raw saved files for callers that need to inspect, export, or reuse the bundle.

*Call graph*: 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 182–191)

```
async def delete(self, name: str) -> None
```

**Purpose**: Deletes one saved user skill for the current workspace and agent. It lets an agent remove a skill from its own persistent drawer without touching other agents’ skills.

**Data flow**: It receives a skill name and reads the current workspace and agent. It sends a delete command to the database for the row matching that workspace, agent, and name. It returns nothing; after it finishes, the matching saved skill is gone if it existed.

**Call relations**: This is the removal path for the store. It uses the same scoping rule as save and load operations, so deletion is limited to the current agent’s saved skills and cannot remove a core skill or another agent’s skill.

*Call graph*: 2 external calls (delete, agent_current).


##### `UserSkillStore.timestamps`  (lines 193–205)

```
async def timestamps(self, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Looks up when a saved skill was first created and last updated. This gives callers simple history information without loading the whole skill content.

**Data flow**: It receives a skill name and reads the current workspace and agent. It queries only the created_at and updated_at fields for that saved skill. If no matching row exists, it returns None. Otherwise, it returns the two datetime values as a pair.

**Call relations**: This is a lightweight metadata read. It fits alongside UserSkillStore.files and UserSkillStore.load_all by using the same agent-scoped lookup, but it avoids decoding or parsing skill files because callers only need timing information.

*Call graph*: 2 external calls (select, agent_current).


##### `UserSkillStore._count`  (lines 207–219)

```
async def _count(self) -> int
```

**Purpose**: Counts how many user-created skills the current workspace and agent already have saved. It exists to help enforce the maximum saved-skill limit.

**Data flow**: It reads the current workspace and agent, queries the database for the number of rows matching that pair, and returns that number as an integer. It does not change any stored data.

**Call relations**: This helper is called by UserSkillStore.save when the requested name is not already owned by the agent. In that moment, save needs to know whether adding one more skill would exceed the allowed cap, and _count supplies that answer.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


##### `UserSkillStore._owns`  (lines 221–233)

```
async def _owns(self, name: str) -> bool
```

**Purpose**: Checks whether the current workspace and agent already have a saved skill with a given name. It helps distinguish updating an existing user skill from trying to add or override something new.

**Data flow**: It receives a skill name and reads the current workspace and agent. It searches the database for a row with that workspace, agent, and name. It returns true if such a row exists and false otherwise.

**Call relations**: This helper is called by UserSkillStore.save before collision and quota checks. If the agent already owns the name, save can treat the operation as a re-save; if not, save must reject names that belong to core or pack skills and may need to call UserSkillStore._count before adding a new row.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).

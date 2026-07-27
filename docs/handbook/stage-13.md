# Skills, user-created skills, and subagent orchestration  `stage-13`

This stage is shared support for giving the agent extra abilities and extra helpers. A “skill” is a reusable folder of instructions and files. The skills package marker makes that code importable, while the runtime reads skill folders, checks which other skills they need, and copies the right files into the sandbox workspace, which is the safe working area for a run. The model catalog skill builds a live reference table of available AI models. The sample skill probe is a simple test that proves a skill can run.

User-made skills are handled by the skill creation extension. Its manifest exposes create, list, inspect, update, and delete actions. Its store saves skills per workspace, checks they are valid and limited, and keeps them separate from built-in skills.

The other half is delegation. Subagent profiles describe specialized child agents, and the subagents loop starts them, waits for them, messages them, or cancels them. Browser, research, and website extensions define focused helper agents plus tools for handing work to them. The brief pipeline defines outline, draft, and critique writing stages.

## Files in this stage

### Skill runtime and catalogs
Core skill package support defines importability, runtime loading, dependency handling, sandbox copying, built-in catalog generation, and a sample probe.

### `core/src/ufo/skills/__init__.py`

`other` · `import time`

This is an empty package file. In Python, a file named `__init__.py` tells the language that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain many useful tools, but this label is what lets the rest of the program refer to the drawer by name. Here, the drawer is `ufo.skills`. Without this file, depending on the Python version and packaging setup, imports from this folder could be less reliable or fail in some environments. There are no functions, classes, or settings here. Its value is structural: it helps organize the codebase and makes the skills area available to the rest of the system.


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during a run`

A skill is a folder that teaches the agent a reusable workflow. Its main file, `SKILL.md`, starts with YAML frontmatter, which is a small metadata block, followed by markdown instructions. The folder may also contain extra files, such as templates or examples. This file reads those folders, checks that they are shaped correctly, records their metadata, and later mounts their files into the sandbox under `.skills/` so the agent can refer to them safely.

The file also solves an important dependency problem. A skill can say it depends on another skill. When one skill is loaded, the registry follows those dependency links and returns the full set needed, without loading the same skill twice and without getting stuck in cycles. Child skills can be nested in folders, but nesting is only a naming and mounting convention; a child does not automatically pull in its parent unless it declares that dependency.

At startup, core skills are discovered from disk and placed in a `SkillRegistry`. Later, active packs or user-saved skills can be combined with that registry. When a skill is used, the code builds two things: prompt text containing the selected workflows, and a compact file tree showing what was mounted. Without this file, skills would be loose folders with no reliable parsing, naming, dependency loading, or safe sandbox placement.

#### Function details

##### `RuntimeSkill.mounted_files`  (lines 53–54)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Returns the complete set of files that should appear in the sandbox for this skill. It includes the original `SKILL.md` file exactly as written, plus any bundled asset files.

**Data flow**: It reads the skill’s stored raw `SKILL.md` text and its asset file list. It turns the markdown text into bytes and combines it with the asset bytes. The result is a dictionary from relative file path to file content.

**Call relations**: When `mount_skill` is ready to copy a skill into the sandbox, it asks this method for the files to write. This keeps the mounting code simple: it does not need to know which file is the workflow file and which files are assets.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 56–57)

```
def mount_root(self) -> str
```

**Purpose**: Builds the sandbox folder path where this skill should be mounted. This gives every skill a predictable home under the workspace’s `.skills` directory.

**Data flow**: It reads the skill’s registry name and combines it with the fixed skills mount directory. The output is a string path such as a workspace `.skills/<skill-name>` location.

**Call relations**: When `mount_skill` writes files, it first calls this method to decide the root folder. The returned path is then combined with each file name from `RuntimeSkill.mounted_files`.

*Call graph*: called by 1 (mount_skill).


##### `LoadedSkill.prompt_body`  (lines 69–75)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the prompt text contribution for one loaded skill. It labels the skill and says whether it was directly requested or was pulled in as a dependency, then includes the skill’s instructions.

**Data flow**: It reads the loaded skill’s name, instructions, and optional dependency source. It formats a short header and appends the markdown workflow. The result is plain text ready to be inserted into the model’s context.

**Call relations**: This is the per-skill text building block used when loaded skills are turned into a combined context. It deliberately does not include asset file contents; those are made available through mounted paths instead.


##### `_split_frontmatter`  (lines 78–84)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a `SKILL.md` file into its metadata block and its instruction body. It also rejects files that do not have the required opening and closing frontmatter fences.

**Data flow**: It receives the full `SKILL.md` text. It checks that the text begins with the expected `---` marker, finds the matching closing marker, and splits the file into metadata text and body text. It returns those two parts, or raises an error if the format is invalid.

**Call relations**: `parse_skill_content` calls this first because it cannot understand a skill until the metadata and workflow text are separated. This small helper is the gatekeeper for the expected `SKILL.md` shape.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 87–92)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate subfolders that are themselves skills. A subfolder counts as a child skill only if it directly contains its own `SKILL.md` file.

**Data flow**: It receives a folder path, looks through that folder’s direct children, and keeps only directories with a `SKILL.md` inside. It returns those child directories in sorted order.

**Call relations**: `parse_skill` uses this to avoid treating child skill folders as ordinary asset files of the parent. `discover_skills` uses it to recursively register those child skills under nested names.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 95–128)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory collection of skill files into a validated `RuntimeSkill`. This is used when skill files are already available as bytes rather than being read directly from disk.

**Data flow**: It receives a claimed directory name, a mapping of file paths to bytes, and optional registry naming information. It finds and decodes `SKILL.md`, splits its frontmatter from its body, parses the YAML metadata, checks that the declared name matches the folder name, gathers the non-`SKILL.md` assets, and returns a `RuntimeSkill` object. If required pieces are missing or inconsistent, it raises an error.

**Call relations**: `parse_skill` calls this after reading files from a real directory. It relies on `_split_frontmatter` for the markdown structure and on YAML parsing for the metadata.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 131–140)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill folder from disk and converts it into a `RuntimeSkill`. It includes normal bundled files but excludes nested child skill folders, because those children are registered separately.

**Data flow**: It receives a filesystem path to a skill directory. It first finds child skill directories, then reads all files under the skill directory except files inside those child skill subtrees. It passes the collected bytes to `parse_skill_content` and returns the parsed skill.

**Call relations**: `discover_skills` calls this for each skill directory it is registering. It uses `_child_skill_dirs` to keep parent assets separate from child skill assets.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 143–161)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, then returns them as one flat lookup table. This lets the rest of the system find skills by names like `parent/child` without walking folders again.

**Data flow**: It receives a skill directory and optional registry name and parent name. It parses the current directory as a skill, stores it under its registry name, then finds child skill directories and recursively discovers each one under a nested name. The output is a dictionary from skill name to `RuntimeSkill`.

**Call relations**: `_load_core_skills` uses this while building the built-in skill collection. Inside its own recursion, it calls `parse_skill` for the current folder and `_child_skill_dirs` to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 164–171)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the project’s built-in skills from the core skills folder. This gives the system a ready-made set of base skills before packs or user skills are added.

**Data flow**: It receives the root folder where core skills live. It scans direct child directories, ignoring hidden or private-looking names, discovers skills in each directory, and merges them into one dictionary keyed by skill name. The result becomes the core skill table.

**Call relations**: This runs when the module is imported to build the core skill constants. It calls `discover_skills` for each eligible core skill directory.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 188–193)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a skill by name and gives a clear error if it is not available. This protects callers from silently continuing with a misspelled or missing skill.

**Data flow**: It receives a skill name and checks the registry’s name-to-skill dictionary. If found, it returns the matching `RuntimeSkill`. If not, it raises an error that includes the available names.

**Call relations**: `SkillRegistry.closure` and its inner dependency-walking helper call this whenever they need to turn a requested or depended-on name into an actual skill object.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 195–218)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Computes the full set of skills needed for a load request: the directly requested skills first, then all of their dependencies. It avoids duplicates and is safe even if dependencies form a cycle.

**Data flow**: It receives one or more skill names. It first records each directly requested skill as direct, not as a dependency. Then it walks each requested skill’s `depends` list, adding missing dependencies and remembering which skill pulled them in. It returns an ordered tuple of `LoadedSkill` objects.

**Call relations**: This is the registry’s main loading decision point. It calls `SkillRegistry.named` to resolve names and creates `LoadedSkill` records. Its inner helper, `SkillRegistry.closure.add`, performs the recursive dependency walk.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 208–213)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill and then follows that skill’s own dependencies. It is the recursive worker inside `SkillRegistry.closure`.

**Data flow**: It receives a resolved skill and the name of the skill that depended on it. If the skill has already been recorded, it stops. Otherwise it records a `LoadedSkill` with the dependency source, reads that skill’s dependency names, resolves each one, and repeats the process. It changes the surrounding `loaded` collection and returns nothing directly.

**Call relations**: `SkillRegistry.closure` calls this while walking dependency chains. The helper calls `SkillRegistry.named` for each dependency name and creates `LoadedSkill` entries for newly included skills.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 220–228)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the public list of loadable top-level skills and their descriptions. This is the catalog shown to the agent or prompt so it knows what it can ask to load.

**Data flow**: It reads the registry’s skills in registration order. It keeps only skills with no parent, then returns pairs of skill name and description. Child skills are left out because they are meant to be found through parent skill instructions.

**Call relations**: This method is used by prompt-building code that needs the skill index. It does not resolve dependencies or mount files; it only produces the visible catalog.


##### `SkillRegistry.merged_with`  (lines 230–242)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that adds user-saved skills after the existing core and pack skills. It refuses to let a user skill replace an existing skill with the same name.

**Data flow**: It receives a tuple of user skills. It copies the current registry, then checks each user skill name. If the name is unused, it adds the skill; if it would collide, it logs that the shadowing was refused and skips it. It returns a new `SkillRegistry`.

**Call relations**: This is used when a workspace’s saved user skills need to be layered onto the base registry. It calls the logging facility when a user skill is dropped and constructs a fresh registry for the combined result.

*Call graph*: 2 external calls (__init__, log).


##### `_mounted_tree`  (lines 248–266)

```
def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Creates a compact text tree of all files mounted for a loaded skill set. This helps the agent see where files are available without repeating long paths over and over.

**Data flow**: It receives the loaded skills. It gathers every mounted file path under each skill name, sorts them, breaks each path into directory and file pieces, and writes an indented tree. The output is a plain text listing rooted at the `.skills` mount directory.

**Call relations**: `loaded_context` calls this after assembling workflow text. The tree describes the files that `mount_skill` will or has written into the sandbox.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 269–274)

```
def loaded_context(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Builds the full prompt text for a skill load. It combines the workflows for all loaded skills and appends a file tree showing what was mounted.

**Data flow**: It receives the ordered tuple of `LoadedSkill` entries. It turns each one into its prompt contribution, separates them with visible dividers, asks `_mounted_tree` for the mounted file listing, and returns one combined string.

**Call relations**: This is shared by normal skill loading and subagent preloading so both present skills in the same way. It hands off the file-listing part to `_mounted_tree`.

*Call graph*: calls 1 internal fn (_mounted_tree).


##### `mount_skill`  (lines 277–280)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Copies one skill’s files into the sandbox workspace. This is what makes the skill’s `SKILL.md` and assets reachable by path during a conversation.

**Data flow**: It receives a sandbox session and a `RuntimeSkill`. It asks the skill for its mount root, asks for all files that should be mounted, and writes each file’s bytes into the sandbox at the matching path. It returns nothing, but it changes the sandbox filesystem.

**Call relations**: This is the I/O step after the registry has decided which skills are loaded. It calls `RuntimeSkill.mount_root` and `RuntimeSkill.mounted_files`, then hands each file to `SandboxSession.write_file` to perform the actual write.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files).


### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a documentation problem that can easily become dangerous: the list of available models must match what the system actually runs. Instead of keeping a hand-written model list somewhere, this file generates the catalog directly from the live model registry, which is the system’s source of truth for model IDs, prices, context limits, reasoning support, and API shape. Think of it like printing a restaurant menu straight from the kitchen’s inventory system, rather than copying it by hand.

The main function, `model_catalog_skill`, takes a `ModelRegistry`, reads every registered model specification, sorts them by model ID, and turns them into a Markdown table. That table includes each model’s provider, knowledge cutoff, context window, input and output price per million tokens, whether reasoning is supported, and API surface. It then wraps this table in a `RuntimeSkill`, which is the format the rest of the system uses for skills loaded at runtime.

A small helper, `_per_mtok`, formats stored price values into readable dollar amounts. The important behavior is that the skill is generated at boot from the same model records used for routing and pricing, so the displayed catalog should not drift away from real runtime behavior.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns a model price stored in tiny accounting units into a human-readable dollar string per million tokens. It exists so the generated catalog shows prices as normal money, like `$1.25`, instead of raw internal numbers.

**Data flow**: It receives an integer price measured in micro-USD, which means millionths of a US dollar. It divides that number by the project’s `MICRO_USD_PER_USD` conversion constant, formats the result with two decimal places, and returns a string with a dollar sign.

**Call relations**: `model_catalog_skill` calls this helper while building each row of the model table. It uses it twice per model: once for the input-token price and once for the output-token price.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the complete runtime skill that describes all models available in this deployment. Someone would use it during startup to give the system a reliable, built-in reference page for choosing and comparing models.

**Data flow**: It receives a `ModelRegistry`, which contains the current model specifications. It reads those specifications, sorts them by model ID, builds a Markdown table with model facts, formats prices through `_per_mtok`, and combines the table with a skill name and description. It returns a `RuntimeSkill` containing the generated instructions and the raw Markdown skill text.

**Call relations**: At boot, this function is called beside the model registry so the catalog is generated from the same records the runtime uses. While building the table it hands price values to `_per_mtok` for readable formatting, then passes the finished name, description, instructions, and raw Markdown into `RuntimeSkill.__init__` to produce the skill object the rest of the system can load.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or diagnostics`

This file acts like a very small “can you hear me?” test for the sample skill. It does not define any reusable functions or classes. It simply prints the text `sample-skill-probe-ok` as soon as Python runs the file. That matters because a larger system can execute this probe and look for that exact message to confirm that the sample skill’s files are present, readable, and runnable. Think of it like tapping a microphone before a presentation: the message itself is not the main feature, but it proves the connection works. Without this file, any setup or diagnostic step that expects a quick confirmation from the sample skill would have no simple signal to check.


### Workspace skill creation
The skill creation extension exposes user-managed skill operations and persists safe workspace-scoped skill packages.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `extension registration, manifest apply, object requests, and per-turn skill loading`

This file is the front door for the “skill_create” extension. A skill here means a small bundle of text files, led by a required SKILL.md file, that teaches the agent a reusable behavior. Without this file, user-authored skills would not be exposed as workspace objects, could not be saved through manifests, and would not be added back into the agent’s skill list on later turns.

The file defines the shape of a saved skill: each file can be given directly as text, copied from a file in the conversation workspace, or kept unchanged by referring to its stored SHA-256 digest, which is a fingerprint of the file contents. When a manifest is applied, workspace file references are read from the sandbox, checked to be normal text files, size-limited, and converted into stored inline bytes. This is important because the saved skill must not depend on temporary sandbox paths.

The main worker is SkillObjects, which supplies the object operations: list, get, status, apply, and delete. It uses UserSkillStore as the storage layer. The file also registers the built-in create-skill teaching skill and provides a runtime hook that reloads saved workspace skills into the turn’s skill registry.

#### Function details

##### `_require_ext`  (lines 100–103)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has the extension context it needs. The extension context is the object that gives access to this extension’s workspace-specific storage and settings.

**Data flow**: It receives a ToolContext. If the context contains an extension context, it returns it. If not, it raises an error, because the skill object code cannot safely read or write saved skills without knowing which extension and workspace it belongs to.

**Call relations**: The object methods call this before touching UserSkillStore. It is the small guard at the doorway for list, get, apply, delete, and the shared file-loading helper.

*Call graph*: called by 5 (_files, apply, delete, get, list).


##### `_text`  (lines 106–112)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This helper verifies that a skill file is plain UTF-8 text. User skills are intentionally text-only, so binary files such as images or compiled programs are rejected.

**Data flow**: It receives a skill-relative path and raw bytes. It tries to decode the bytes as text. If decoding works, it returns the decoded string; if not, it raises a clear error naming the file that is not valid text.

**Call relations**: SkillObjects._resolve calls this after gathering each proposed file’s bytes. It acts as the final text check before the files are accepted for saving.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 122–128)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of saved user skills for the current workspace. Each row includes the skill name and a short summary taken from its description.

**Data flow**: It receives the tool context and a list query, gets the extension context, loads all saved skills for the workspace, turns them into display rows, and then applies the requested paging. The output is an ObjectPage suitable for showing a list of skill objects.

**Call relations**: This is called when the object system needs to show available objects of kind skill. It relies on _require_ext for the workspace context, UserSkillStore for the saved skills, and object_page to shape the rows into the requested page.

*Call graph*: calls 1 internal fn (_require_ext); 3 external calls (__init__, __init__, object_page).


##### `SkillObjects.get`  (lines 130–148)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This returns the stored description of one saved skill without dumping the full file contents into the conversation. Instead, it returns each file as a digest reference that can be reused to keep the file unchanged.

**Data flow**: It receives the tool context and a skill name. It loads that skill’s stored files, fetches its creation and update timestamps, and builds a UserSkillSpec where each file is represented by its SHA-256 fingerprint and size. If the skill or timestamps are missing, it returns null.

**Call relations**: This is used when the object system needs details for one skill object. It calls SkillObjects._files to retrieve content, uses _require_ext to reach the store for timestamps, and wraps the result in ObjectDetail for the caller.

*Call graph*: calls 2 internal fn (_files, _require_ext); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 150–158)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This gives a compact health summary for one saved skill. It reports the parsed description, number of files, and total byte size.

**Data flow**: It receives the tool context and skill name, loads the stored files, and returns null if the skill does not exist. If it exists, it parses the skill content to read the description, counts the files, sums their sizes, and returns those values in a small dictionary.

**Call relations**: This is called when the object system wants a quick status view rather than the full object detail. It depends on SkillObjects._files for stored bytes and parse_skill_content for understanding the SKILL.md metadata.

*Call graph*: calls 1 internal fn (_files); 1 external calls (parse_skill_content).


##### `SkillObjects.apply`  (lines 160–172)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None) -> None
```

**Purpose**: This saves or updates a user-authored skill from a manifest. It enforces limits, resolves file references, validates the final bundle through the store, and prevents user skills from replacing built-in skills.

**Data flow**: It receives the tool context, skill name, new skill spec, and optionally the old spec. It checks that the skill does not contain too many files, resolves all file bodies into bytes, checks the total size, and then asks UserSkillStore to save the result for the current workspace. Saving also receives the existing skill names so the store can reject shadowing built-ins.

**Call relations**: This is the main write path for skill objects. The object system calls it when a manifest is applied. It calls _require_ext for workspace storage, SkillObjects._resolve to turn mixed file references into real content, and UserSkillStore.save to persist the validated skill.

*Call graph*: calls 2 internal fn (_resolve, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 174–176)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This removes a saved user skill from the current workspace. After deletion, that skill will no longer be loaded on later turns.

**Data flow**: It receives the tool context and skill name, gets the extension context, and asks UserSkillStore to delete that name from the workspace’s saved skills. It does not return a value.

**Call relations**: The object system calls this for delete operations on skill objects. It uses _require_ext to find the right workspace and UserSkillStore.delete to perform the removal.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._files`  (lines 178–180)

```
async def _files(self, ctx: ToolContext, name: str) -> dict[str, bytes] | None
```

**Purpose**: This shared helper loads the raw stored files for one saved skill. It keeps the repeated “find the extension context, then ask the store” pattern in one place.

**Data flow**: It receives the tool context and skill name. It gets the extension context, asks UserSkillStore for that skill’s files in the current workspace, and returns either a mapping of file paths to bytes or null if the skill is not found.

**Call relations**: SkillObjects.get, SkillObjects.status, and SkillObjects._resolve all call this when they need the current stored content. It is the read-side bridge between the object methods and UserSkillStore.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_resolve, get, status); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 182–230)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns a proposed skill spec into the exact bytes that should be saved. It supports three cases: new inline text, content copied from the workspace, and existing stored files kept by digest.

**Data flow**: It receives the tool context, skill name, and proposed UserSkillSpec. First it loads existing files so digest references can be checked. Then it verifies kept files really match their stored SHA-256 fingerprints. Next it collects workspace file references, reads those files inside the sandbox with size and regular-file checks, decodes their base64 output, and combines everything into one path-to-bytes mapping. Finally it verifies every file is UTF-8 text and returns the resolved mapping.

**Call relations**: SkillObjects.apply calls this before saving. It calls SkillObjects._files for existing content, workspace_path to translate workspace-relative references, the sandbox shell to read referenced files safely, and _text to reject non-text files.

*Call graph*: calls 2 internal fn (_files, _text); called by 1 (apply); 6 external calls (b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 257–260)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This supplies the current workspace’s saved user skills to the agent at the start of a turn. It is what makes a previously saved skill loadable later.

**Data flow**: It receives the extension context, uses it to open UserSkillStore, loads all saved skills for the workspace, and returns them as runtime skill objects. Nothing is modified.

**Call relations**: The Manifest registers this as the runtime_skills provider. Core code calls it when building the turn’s skill registry, and its returned skills are merged beside built-in and pack-provided skills.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 263–270)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest that tells the host system what this extension contributes. It names the extension, registers the skill object kind, includes the create-skill teaching skill, and connects the runtime skill provider.

**Data flow**: It takes no input. It creates a Manifest containing the extension name and version, the SKILL_OBJECT object kind, a SkillSpec pointing to the bundled create-skill files, and the _runtime_skills callback. The returned Manifest is what the host loads.

**Call relations**: The extension loader calls this during registration. It ties together the file’s pieces: the object API implemented by SkillObjects, the bundled authoring skill, and the per-turn loader for saved user skills.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `during skill creation, skill loading each turn, and skill cleanup`

A “skill” here is a small folder of files, usually including a SKILL.md instruction file plus any extra assets. This file is the storage layer for skills that a user creates inside a workspace. Without it, a skill written by the agent or user would disappear after the current temporary environment ended, or worse, could accidentally collide with a built-in skill or leak across workspaces.

The main class, UserSkillStore, works like a careful librarian. When saving a skill, it first checks that the name is a simple lowercase slug, such as “summarize-notes”. It then parses the files with the same parser used for normal skills, so user skills must follow the same rules. It refuses names that would overwrite core or pack-provided skills. It also caps each workspace at 100 saved skills, because every saved skill is loaded and parsed on each turn.

For storage, each file’s raw bytes are converted to base64 text, which is a safe way to put binary data into a text database column. The whole bundle is stored in the extension’s user_skill table, keyed by workspace and skill name. Loading reverses that process: read the bundle, decode the files, parse them into runtime skills, and skip broken ones with a warning so one bad saved skill does not break the whole workspace.

#### Function details

##### `UserSkillStore.save`  (lines 86–147)

```
async def save(self, workspace_id: UUID, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves a user-authored skill for a specific workspace after checking that it is valid and allowed. It prevents unsafe names, prevents user skills from replacing built-in skills, and keeps the workspace under the saved-skill limit.

**Data flow**: It receives a workspace ID, a skill name, a map of file paths to file bytes, and the names already present in the skill registry. It validates the name, parses the files into a usable RuntimeSkill, checks whether this workspace already owns that name, and checks the workspace skill count if this would be a new skill. Then it turns each file into base64 text, stores the resulting JSON bundle in the database, records a sha256 digest as a change fingerprint, and either updates the existing row or inserts a new one. The output is the parsed RuntimeSkill, ready for the caller to use.

**Call relations**: This is the main write path for user-created skills. In its flow it calls UserSkillStore._owns to tell whether the name is already this workspace’s own saved skill, and UserSkillStore._count to enforce the workspace limit before adding a new one. It also hands the file bundle to the shared skill parser so saved user skills are judged by the same rules as other skills.

*Call graph*: calls 2 internal fn (_count, _owns); 9 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 149–186)

```
async def load_all(self, workspace_id: UUID) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved user skill for one workspace and turns them back into runtime skills for the current turn. It is strict about workspace boundaries, so skills saved in one workspace do not appear in another.

**Data flow**: It receives a workspace ID. It reads that workspace’s saved skill rows from the database, ordered by name. For each row, it validates the stored JSON shape, decodes each base64 file back into bytes, and parses those files into a RuntimeSkill. If one saved skill is corrupt or no longer accepted by the parser, it logs a warning and skips that skill instead of failing the whole load. The output is a tuple of successfully loaded RuntimeSkill objects.

**Call relations**: This is the read path used when the runtime needs to merge saved user skills into the turn’s available skill registry. It relies on the shared parse_skill_content routine for final validation, just as saving does, and it deliberately keeps going when a single stored skill fails so the rest of the workspace can still operate.

*Call graph*: 3 external calls (b64decode, select, parse_skill_content).


##### `UserSkillStore.files`  (lines 188–203)

```
async def files(self, workspace_id: UUID, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original file contents for one saved skill in one workspace. This is useful when something needs to inspect, edit, or display the stored skill files rather than just load the parsed runtime form.

**Data flow**: It receives a workspace ID and a skill name. It looks for the matching database row. If none exists, it returns None. If it finds one, it validates the stored JSON, decodes each base64 string back into bytes, and returns a dictionary from relative file path to raw file bytes.

**Call relations**: This function is a focused lookup beside the broader UserSkillStore.load_all path. Instead of loading every skill into the runtime registry, it retrieves one skill’s stored bundle and stops there.

*Call graph*: 2 external calls (b64decode, select).


##### `UserSkillStore.delete`  (lines 205–212)

```
async def delete(self, workspace_id: UUID, name: str) -> None
```

**Purpose**: Removes one saved user skill from one workspace. It gives the workspace a way to clean up old or unwanted skills.

**Data flow**: It receives a workspace ID and a skill name. It sends a delete command to the database for exactly that workspace-and-name pair. It returns nothing; the lasting effect is that the row is gone if it existed.

**Call relations**: This is the cleanup path for the store. It does not call the parser or decoding logic because it only needs to remove the saved record, not understand its contents.

*Call graph*: 1 external calls (delete).


##### `UserSkillStore.timestamps`  (lines 214–224)

```
async def timestamps(self, workspace_id: UUID, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Reads when a saved skill was first created and when it was last updated. This can support user-facing information such as showing when a skill was last changed.

**Data flow**: It receives a workspace ID and a skill name. It queries the database for the created_at and updated_at fields for that exact saved skill. If the row is missing, it returns None. If it exists, it returns the two datetime values as a pair.

**Call relations**: This is a small metadata lookup alongside the main save, load, and delete operations. It reads only the timing columns and does not touch the stored file bundle.

*Call graph*: 1 external calls (select).


##### `UserSkillStore._count`  (lines 226–234)

```
async def _count(self, workspace_id: UUID) -> int
```

**Purpose**: Counts how many user-created skills a workspace currently has. It exists so saving a new skill can enforce the maximum number of saved skills per workspace.

**Data flow**: It receives a workspace ID. It asks the database to count rows in the user_skill table for that workspace. It returns that count as an integer.

**Call relations**: UserSkillStore.save calls this only when the skill name is not already owned by the workspace. That lets save distinguish between updating an existing skill, which is allowed, and adding another new skill, which may hit the workspace limit.

*Call graph*: called by 1 (save); 1 external calls (select).


##### `UserSkillStore._owns`  (lines 236–246)

```
async def _owns(self, workspace_id: UUID, name: str) -> bool
```

**Purpose**: Checks whether a workspace already has a saved user skill with a given name. This matters because re-saving your own skill is allowed, but taking the name of a core or pack skill is not.

**Data flow**: It receives a workspace ID and a skill name. It searches the database for a row with that exact workspace-and-name pair. It returns true if such a row exists and false otherwise.

**Call relations**: UserSkillStore.save calls this before deciding whether a name collision is acceptable. If the name is already in the broader registry but this workspace does not own it, save treats it as a collision with a core or pack skill and refuses the save.

*Call graph*: called by 1 (save); 1 external calls (select).


### Core subagent orchestration
Built-in subagent defaults and turn-control helpers let parent agents start, message, await, and cancel bounded child-agent work.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent setup`

This file is like the default job description for a temporary assistant. When the main agent delegates a self-contained task but does not name a special kind of subagent, the system uses this “general_purpose” profile.

The profile gives the subagent clear boundaries. It can read, write, edit, search files, run shell commands, load skills, share files, and use some optional extension tools if those extensions are installed. But it cannot ask the user questions, create more subagents, talk to sibling subagents, cancel them, wait for them, or approve account connections. In plain terms: it is allowed to work independently, but not to take over coordination or user-facing decisions.

The file also defines two small data shapes using Pydantic, a library that checks data has the expected fields. `GeneralPurposeInput` says the subagent receives a `task`. `GeneralPurposeOutput` says it returns a `result`.

Most of the file is the prompt text that becomes the subagent’s operating instructions. It tells the subagent to make reasonable assumptions, load relevant skills, avoid repeated failed attempts, use the shared `/workspace` folder for handoffs, and finish with a concise summary. Finally, the file packages all of this into `GENERAL_PURPOSE_PROFILE` and exposes it as the core built-in subagent profile.


### `core/src/ufo/loop/subagents.py`

`orchestration` · `during a parent turn, when it spawns or controls subagents`

A subagent is like asking a specialist assistant to do a contained piece of work while the main assistant keeps its own place. This file defines the registry of available specialist profiles, builds the child agent's system prompt, and provides the `Subagents` object that a parent turn uses to create and control child turns.

A profile says three important things: what instructions the child should see, which tools it may use, and what input and output shapes are allowed. Before a child is launched, this file checks that the requested profile exists and that the payload matches the profile's input model. It then creates a separate conversation and turn for the child in the database, links it back to the parent turn, and enqueues it on the durable turn queue. That separate queue partition matters: it lets the parent wait for the child without both being stuck behind each other.

Foreground spawns wait until the child finishes, then validate the child's final text against the profile's output model. Background spawns return the child turn id immediately, so the parent can later wait, send a follow-up message, or cancel it. The file is careful about repeat runs too: with a deduplication key, the same child can be reattached instead of accidentally created and billed twice.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 77–81)

```
def __post_init__(self) -> None
```

**Purpose**: This checks the list of subagent profiles as soon as the registry is created. Its job is to make sure no two profiles have the same name, because duplicate names would make a spawn request ambiguous.

**Data flow**: It reads the profile names already stored in the registry → counts which names appear more than once → either leaves the registry usable or raises an error listing the duplicates.

**Call relations**: This runs automatically after `SubagentRegistry` is built. It protects later lookups in `SubagentRegistry.get`, so spawning code can rely on a profile name meaning exactly one profile.


##### `SubagentRegistry.get`  (lines 83–90)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: This finds the subagent profile with a requested name. If the name is not registered, it gives a clear error that also lists the valid choices.

**Data flow**: It takes a profile name → searches the registry's stored profiles → returns the matching profile, or raises `UnknownSubagentProfile` with a helpful message if none match.

**Call relations**: The spawn flow uses this before launching a child, and trust checks use it when reporting background results. When the lookup fails, it hands control to the `UnknownSubagentProfile` error so callers do not continue with an unknown specialist.

*Call graph*: 1 external calls (__init__).


##### `subagent_system_prompt`  (lines 93–125)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: This builds the full instruction text shown to a child subagent. It combines the profile's own instructions, the available skill list, any preloaded skill material, shared citation and formatting rules, and the final instruction to return output through the `finish` tool.

**Data flow**: It receives a profile, an optional list of skills, and optional preloaded skill bodies → fills the skill-index slot, checks that no template placeholders were left behind, checks that preloaded text is not too large, and appends the shared output rules → returns one complete prompt string for the child agent.

**Call relations**: This function is used when the system prepares a subagent's model call. It calls `render_skill_index` to turn skill names and descriptions into prompt text, uses `PROMPT_VAR_RE.findall` to catch unfilled prompt slots, and calls `loaded_context` to insert preloaded skill instructions safely.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.spawn`  (lines 137–181)

```
async def spawn(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: This starts a child subagent turn. It can either wait for the child to finish and return its validated output, or launch it in the background and immediately return the child's turn id.

**Data flow**: It receives a profile name, input payload, background flag, and optional deduplication key → looks up the profile, validates the input, chooses or derives a child conversation id, creates the child turn, and enqueues it → returns a `SpawnResult` containing the child turn id and, for foreground work, the validated final output.

**Call relations**: This is the main public entry for spawning. It relies on `_admit` to write the child conversation and turn, `_enqueue` to put that turn on the durable queue, and `_await_terminal` to poll for completion when running in the foreground. It uses `turn_id_for`, `uuid4`, and `uuid5` to make child ids, and wraps unsafe validation failures in `UntrustedContentError` when the profile marks the child output as untrusted.

*Call graph*: calls 3 internal fn (_admit, _await_terminal, _enqueue); 5 external calls (__init__, __init__, turn_id_for, uuid4, uuid5).


##### `Subagents.wait`  (lines 183–202)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: This waits for one or more background subagents to finish and reports their final status. A parent uses it after launching background work when it is ready to collect the results.

**Data flow**: It receives child turn ids → first confirms each one really belongs to this parent turn, then waits for each terminal result → returns a tuple of `SubagentStatus` objects with the turn id, status, final text, and whether the result should be treated as untrusted.

**Call relations**: This is the collection step for background spawns. It calls `_require_child` to prevent one parent from reading another parent's child, `_await_terminal` to wait for completion, and `_untrusted_output` to decide how cautiously the returned text should be treated.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 204–221)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: This cancels a child subagent turn that this parent spawned. It is used when the parent no longer needs the child's work or wants to stop it before it finishes.

**Data flow**: It receives a child turn id → verifies that the turn belongs to this parent, asks the shared cancellation helper to cancel it, then reads the turn's current status and terminal text from the database → returns a `SubagentStatus` describing the result.

**Call relations**: Before doing anything destructive, it calls `_require_child` to enforce ownership. It then delegates the real cancellation to `cancel_one_turn`, reads the updated database state through `workspace_tx` and `sqlalchemy.select`, and uses `TerminalFrame.model_validate` to read terminal text when one exists.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 223–297)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: This sends a follow-up message to a background child subagent. Instead of starting over, it creates the next turn in the child's existing conversation so the child can continue with its previous context.

**Data flow**: It receives a child turn id and message text → confirms the child belongs to this parent, locks the child's conversation, calculates the next sequence number, inserts a new queued turn with the message as inbound text, and enqueues it if nothing earlier is already waiting → returns a `SubagentStatus` for the newly queued follow-up turn.

**Call relations**: This is the continuation path after a background spawn. It calls `_require_child` for safety, uses database operations through `workspace_tx` to insert and update the follow-up turn, and calls `_enqueue` only when the new turn is ready to be dispatched without jumping ahead of older queued work.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._untrusted_output`  (lines 299–305)

```
def _untrusted_output(self, profile: str) -> bool
```

**Purpose**: This decides whether output from a subagent profile should be treated as untrusted. It is deliberately cautious: if the profile can no longer be found, it marks the output as untrusted rather than assuming it is safe.

**Data flow**: It receives a profile name → tries to find that profile in the registry → returns the profile's `untrusted_output` flag, or returns `true` if the profile is unknown.

**Call relations**: `Subagents.wait` calls this while building background status reports. It uses the registry lookup as the source of truth, and falls back to the safer answer if that lookup raises `UnknownSubagentProfile`.

*Call graph*: called by 1 (wait).


##### `Subagents._require_child`  (lines 307–318)

```
async def _require_child(self, turn_id: UUID) -> str
```

**Purpose**: This checks that a turn id belongs to a subagent spawned by the current parent turn. It prevents accidental or unauthorized control of some other turn.

**Data flow**: It receives a turn id → reads that turn's parent id and subagent profile from the database → returns the profile name if the parent matches, or raises an error if the turn is missing or belongs elsewhere.

**Call relations**: The public control methods `wait`, `cancel`, and `message` call this before reading, stopping, or extending a child turn. It uses `workspace_tx` and `sqlalchemy.select` to make the ownership check against stored turn records.

*Call graph*: called by 3 (cancel, message, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 320–389)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str) -> bool
```

**Purpose**: This creates the database records for a child subagent conversation and its first turn. It is built to be safe to retry, so the same deduplicated spawn does not create duplicate children.

**Data flow**: It receives a child conversation id, child turn id, profile name, and inbound JSON text → inserts the child conversation and queued first turn if they do not already exist, records the parent link and tracing information, and marks the turn as dispatch-ready → returns `true` if the turn should be enqueued now, or `false` if it is no longer queued.

**Call relations**: `Subagents.spawn` calls this before queueing any child work. It uses database transactions through `workspace_tx`, SQL insert/select/update operations, and `current_traceparent` so observability traces can connect the child work back to the parent.

*Call graph*: called by 1 (spawn); 4 external calls (select, update, workspace_tx, current_traceparent).


##### `Subagents._enqueue`  (lines 391–420)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This asks the durable workflow queue to run a queued child or follow-up turn. If queueing fails, it clears the dispatch marker so another part of the system can retry later.

**Data flow**: It receives a turn id and conversation id → builds queue options including the queue name, workflow name, workflow id, and partition key → submits the work to DBOS; if cancellation or an error happens, it updates the database to show the turn is not currently dispatched.

**Call relations**: `Subagents.spawn` uses this for a new child turn, and `Subagents.message` uses it for a follow-up turn. It talks to the DBOS client for actual queue submission, uses `workspace_tx` and SQL updates for rollback bookkeeping, and logs deferred enqueue failures with `log`.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 422–432)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: This waits until a turn has a terminal frame, meaning the turn has reached its final recorded result. It is a simple polling loop used when the parent needs the child's answer.

**Data flow**: It receives a turn id → repeatedly reads the turn's terminal field from the database → once the terminal field is present, validates it as a `TerminalFrame` and returns it; otherwise it sleeps briefly and checks again.

**Call relations**: Foreground `spawn` calls this to wait for a child before returning output, and `wait` calls it to collect background children. It uses `workspace_tx` and `sqlalchemy.select` for each database read, `asyncio.sleep` between checks, and `TerminalFrame.model_validate` to turn stored terminal data into a typed result.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


### Brief writing pipeline
The brief pipeline extension packages staged outline, draft, and critique worker definitions for structured writing delegation.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `import time`

This is the package entry file for the brief pipeline extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. That means other parts of the project can refer to this extension by its package name, rather than by raw file paths.

Here, the file only contains a short documentation string: “Brief pipeline extension.” It does not define functions, classes, settings, or startup behavior. Its main value is structural. It is like a label on a folder in a filing cabinet: it does not do the work inside the folder, but it makes the folder recognizable and usable by the rest of the system.

Without this file, depending on the project’s Python version and import setup, the extension package might be harder or impossible to import in the expected way. So while it is tiny, it helps the project keep a clean package layout.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load / pipeline setup`

This file is the recipe card for a small writing pipeline. The pipeline works like an assembly line: one subagent makes an outline, the next turns that outline into a draft, and the last reviews the draft. Each subagent is “toolless,” meaning it cannot call extra tools or start other agents. That keeps the process simple and predictable: the parent agent is the one that moves work from one stage to the next.

The file first names the three stages and sets a shared round limit, which caps how long each subagent can spend working. It then points to the folder containing the prompt files. Those prompt files are the written instructions each subagent follows.

The small Pydantic models, such as `BriefRequest`, `BriefOutline`, and `BriefDraft`, define the shape of the messages passed between stages. Pydantic is a validation library: it helps make sure the data has the expected fields before it is used. For example, the outline stage expects a topic and audience, and it must return an outline.

Finally, the file builds three `SubagentProfile` objects. Each profile combines a name, prompt text, allowed tools, input type, output type, and round limit. Without this file, the brief pipeline would not know what its stages are or how information should safely move between them.


### Specialized subagent extensions
Browser, research, and website extensions define delegation tools and matching specialized subagent profiles for focused parallel or isolated work.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is the bridge between a general agent and a specialized browser agent. Instead of giving the parent agent direct control of a browser, it asks a separate “browser” subagent to do the web work and report back. That keeps browser automation isolated, like hiring a courier for a trip rather than giving them the keys to your whole office.

There are two main tools. `browser_task` starts one fresh browser session for a self-contained task, such as visiting a site, clicking through pages, or extracting information. The session has a time limit. If the website hangs or the browser agent gets stuck, this file cancels the child task so the parent does not wait forever.

`wide_browse` is for batch work. It reads a workspace file containing URLs or site names, removes blank lines and duplicates, then launches several browser subagents at once, up to a safe limit. Each subagent gets a prompt built from a template. If an output JSON schema is provided, it is appended to the task so each browser run knows the desired result shape. When all runs finish, the collected results are written to `wide_browse.json` and also returned to the caller.

A key detail is repeat safety. Batch browser runs use deterministic deduplication keys, so if a parent run is recovered after a crash, it can reconnect to already-started child work instead of accidentally launching duplicates.

#### Function details

##### `_browser_task`  (lines 86–111)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation job by spawning a browser subagent. It waits for that subagent to finish, enforces the requested time limit, and returns the browser agent’s final summary.

**Data flow**: It receives a tool context and a `BrowserTaskInput` containing the starting URL, task instructions, display name, and timeout. It checks that subagent control is available, starts a fresh browser child turn, then waits for that child within the timeout window. If time runs out, it cancels the child and returns an error message. If the child finishes normally, it reads the child’s text as a `BrowserResult`, converts it back to JSON, and returns that JSON as tool output.

**Call relations**: This is the handler behind the `browser_task` tool definition. When a caller asks for one browser session, this function uses `ToolContext.spawn` to start the browser subagent, uses `asyncio.timeout` to protect the parent from waiting too long, validates the returned browser result with `BrowserResult.model_validate_json`, and packages the final text with `TextContent` and `ToolResult`.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 114–125)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique entries. It is used so batch browsing has one clear item to visit per line.

**Data flow**: It receives the tool context and a file path. It asks the sandbox to run `cat` on that path, checks whether reading succeeded, then walks through the file line by line. Blank lines are ignored, surrounding spaces are removed, and duplicate entries are skipped. The result is an ordered list of unique strings.

**Call relations**: `_wide_browse` calls this first to get the URLs or site names it should process. This helper keeps the file-reading and cleanup step separate, so the batch logic can work with a ready-to-use list instead of raw file text.

*Call graph*: called by 1 (_wide_browse); 1 external calls (dumps).


##### `_wide_browse`  (lines 128–155)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs the same kind of browser task across many URLs or site names in parallel. It collects all child results into a JSON file and returns a summary pointing to that file.

**Data flow**: It receives a tool context and a `WideBrowseInput` containing an entities file, a prompt template, an optional schema file path, and a user-facing description. It reads and deduplicates the entities, rejects the request if there are too many, reads the schema file if available, then starts browser visits with a concurrency limit so only a fixed number run at once. After all visits finish, it writes the list of results to `wide_browse.json` in the sandbox and returns JSON containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It calls `_read_lines` to prepare the batch list, creates an `asyncio.Semaphore` to cap parallel work, uses `asyncio.gather` to wait for all per-entity visits, then wraps the final response in `TextContent` and `ToolResult`.

*Call graph*: calls 1 internal fn (_read_lines); 5 external calls (__init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 136–149)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser subtask for one entity in a larger batch. It turns a single URL or site name into a browser prompt, sends it to the browser subagent, and formats that one result row.

**Data flow**: It receives one entity string from the batch. It waits for a slot in the semaphore, substitutes that entity into the prompt template, appends the output schema text if one was read successfully, and spawns a browser subagent with a deduplication key based on the parent idempotency key and the entity. It returns a small dictionary containing the entity and the browser subagent’s JSON output, or an empty string if there was no output.

**Call relations**: This nested helper is used inside `_wide_browse` for each entity. `_wide_browse` launches many `visit` calls together with `asyncio.gather`; each `visit` performs the actual handoff to the browser subagent and gives one finished row back to the batch collector.


### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup and subagent setup`

This file is like an ID card and instruction packet for a browser-focused helper agent. The main agent can hand off web automation work to this subagent, such as opening pages, gathering information, or saving findings. Without this file, the system would not know how to create that browser helper, what tools it is allowed to use, or what kind of request and response format to expect.

The file first loads a written prompt from `prompts/subagent_browser.md`. That prompt contains the working instructions the browser subagent follows. It then builds a tool list from the browser extension’s browser tools and adds a few shared workspace tools: reading, writing, editing files, and web search. This lets the subagent both browse the web and leave useful notes or screenshots where the parent agent can inspect them later.

Two small data models describe the conversation boundary. `BrowserTask` is the request sent into the subagent: the task text, plus an optional starting URL and optional task name. `BrowserResult` is the response coming back: a plain result string. Finally, `BROWSER_PROFILE` packages all of this into a `SubagentProfile`, which is the system’s standard description of a subagent. It is marked as producing untrusted output, meaning the parent system should treat its returned content carefully rather than blindly trusting it.


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file solves the problem of doing the same research task repeatedly for many targets, such as a list of companies, people, or topics. Instead of asking one research agent to work through the whole list slowly, it fans the work out to several child research agents in parallel, like giving eight helpers their own names from a shared checklist.

The flow is simple. First, the tool reads an entities file from the sandbox, trims blank space, removes duplicate lines, and keeps the original order of the first occurrence. It refuses very large batches, with a hard limit of 128 entities, to avoid runaway work. It then optionally reads an output schema file, which is a description of the shape the final data should follow.

For each entity, it builds a research prompt by replacing `{entity}` in the prompt template. If a schema was found, it appends that schema to the prompt. It then starts a research subagent under the shared research profile. A semaphore, which is a simple limit on how many tasks may run at once, keeps the fan-out to eight concurrent child agents.

The tool is marked as side-effecting because it creates child work and writes a file. It uses a stable deduplication key for each entity, so if the parent run is retried after a crash, already-finished child results can be reused instead of repeated. Finally, it writes all rows to `wide_research.json` and returns both the rows and the output file name.

#### Function details

##### `_read_lines`  (lines 41–52)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads a text file from the sandbox and turns it into a clean list of unique entity names. It exists so the main research tool starts from a reliable checklist: no blank entries and no repeated work for duplicate lines.

**Data flow**: It receives a tool context and a file path. It asks the sandbox to run `cat` on that path, using JSON quoting so the path is safely placed in the shell command. If the read fails, it raises an error with the sandbox's message. If it succeeds, it reads each output line, trims whitespace, skips empty lines, removes duplicates while keeping first-seen order, and returns the resulting list of strings.

**Call relations**: The main `_wide_research` function calls this at the start of a batch run. `_read_lines` hands back the cleaned entity list that decides how many child research agents will be spawned.

*Call graph*: called by 1 (_wide_research); 1 external calls (dumps).


##### `_wide_research`  (lines 55–82)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the actual worker behind the `wide_research` tool. It takes the user's batch research request, splits it into one research job per entity, runs those jobs in parallel with a safe concurrency limit, writes the combined results to a JSON file, and returns a summary to the caller.

**Data flow**: It receives the tool context and validated input fields: the entity file path, a prompt template, an optional schema file path, and a user description. It first reads and deduplicates the entity list. It rejects the request if there are more than 128 entities. It then reads the schema file if available, creates a semaphore to cap parallel child agents, and launches one nested `visit` task per entity. After all visits finish, it writes the list of result rows to `wide_research.json` in the sandbox. It returns a `ToolResult` containing text JSON with the rows and the output file name.

**Call relations**: This function is connected to the exported `WIDE_RESEARCH_TOOL` as its handler, so it runs when that tool is invoked. It calls `_read_lines` to prepare the entity list, uses `asyncio.gather` to wait for all per-entity `visit` tasks, and wraps the final answer in `TextContent` and `ToolResult` so the surrounding tool system can deliver it back to the caller.

*Call graph*: calls 1 internal fn (_read_lines); 5 external calls (__init__, __init__, Semaphore, gather, dumps).


##### `_wide_research.visit`  (lines 63–76)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This nested helper performs the research step for one entity. It builds that entity's prompt, starts one research subagent, and turns the subagent's answer into a row for the final results file.

**Data flow**: It receives one entity string from the batch. Before doing work, it waits for a semaphore slot so no more than the configured number of child agents run at once. It replaces `{entity}` in the prompt template with the actual entity name, appends the requested output schema if one was read, and spawns a research subagent with that objective. The subagent is given a deterministic deduplication key based on the parent call's idempotency key and the entity name. It returns a dictionary containing the entity and the subagent output as JSON text, or an empty string if there was no output.

**Call relations**: `_wide_research` creates one `visit` task for each cleaned entity and passes all of them to `asyncio.gather`. Each `visit` hands its objective to `ctx.spawn`, which delegates the real research work to the research profile, then returns one row that `_wide_research` later collects and writes to the output file.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup/config load`

This file is like a job description sheet for research helpers inside the larger agent system. Instead of letting every part of the system invent its own research setup, it creates two named profiles that can be reused consistently: `research` for focused research tasks, and `deep_research` for longer, multi-source investigations.

Each profile is given a prompt, which is the written instruction set the subagent follows. Those prompts are loaded from nearby Markdown files. The profiles also receive a fixed list of tools they are allowed to use, such as web search, URL fetching, browser tasks, file reading and writing, shell commands, memory search, and spreadsheet work. This matters because it keeps research subagents powerful enough to gather and organize information, but scoped so they do not accidentally use tools meant for other agents.

The file also defines simple input and output shapes using Pydantic models, which are data checks that make sure the subagent receives an `objective` and returns a `result`. Finally, it builds two `SubagentProfile` objects. The deeper profile uses the same tool set and model, but raises the maximum number of back-and-forth work rounds to 200, giving it more time for difficult research.


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file is like a front desk for website-building work. When the main agent is asked to make a website, web app, dashboard, or small web game, it does not do all of that work directly here. Instead, this file defines a clean way to pass the whole job to a specialized subagent whose job is to build, serve, and check the site in the sandbox.

The important idea is that the child agent starts fresh. It does not automatically know the earlier conversation, so the `objective` must include all the details: what to build, what pages or sections it needs, what it should look like, and how it should behave. The input model also lets the caller give the build a friendly task name, preload helpful skills before the child starts, and allow more working rounds for unusually large builds.

The file then registers one tool definition, `DELEGATION_TOOLS`, so the wider system can offer `build_website` as a callable tool. When used, the tool calls `ctx.spawn`, which creates the website-building child session through the system’s normal spawning path. That keeps the child limited to the profile’s approved tools instead of giving it uncontrolled access. The child’s final output is turned into text and returned as the tool result.

#### Function details

##### `_build_website`  (lines 47–50)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the actual tool action behind `build_website`. It starts a fresh website-building child session, passes along the caller’s build instructions, and returns the child agent’s summary as text.

**Data flow**: It receives a tool context, which is the system object that can start child agents, and a `BuildWebsiteInput` object containing the website objective and optional settings. It removes any empty optional fields, sends the remaining data to the `website_building` subagent, waits for that subagent to finish, converts the child’s output to JSON text if there is output, and wraps that text in a tool result for the caller.

**Call relations**: This function is connected to the `build_website` tool definition as its handler, so it runs when the main agent chooses that tool. Its main handoff is to `ToolContext.spawn`, which creates the specialized website-building child agent. After the child returns, `_build_website` packages the response using `TextContent` and `ToolResult` so the rest of the tool system can read it in the normal format.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup`

This file is like a job description and equipment list for a helper whose only job is to build and serve websites. The wider system already knows how to run subagents, but it needs a clear profile for each kind of subagent: what it is called, what prompt it should follow, what tools it may use, and what kind of task and result it should exchange with the rest of the system.

Here, the file loads a website-building prompt from a nearby Markdown file. That prompt contains the actual working instructions for the subagent. It then names the allowed tools: basic file tools for reading and editing project files, site-specific tools for building and serving pages, a JavaScript REPL for testing a running page, and optional web research tools if those are installed.

The file also defines two small data shapes using Pydantic, a library that checks that data has the expected fields. `WebsiteBuildingTask` describes what the parent process sends in, such as the objective and optional skill-loading hints. `WebsiteBuildingResult` describes what the subagent sends back: a plain result string.

Finally, all of these pieces are bundled into `WEBSITE_BUILDING_PROFILE`, which the main subagent machinery can use directly. Without this file, the system would not have a clean, reusable way to launch the website-building worker with the right rules and tools.

## 📊 State Registers Touched

- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-tool-catalog` — The shared catalog of tools the model may call, including their names, schemas, handlers, and safety properties.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-skill-inventory` — The built-in and user-created skill folders, metadata, dependencies, and workspace-specific skill records.
- `reg-subagent-tree` — The shared parent-child work structure for delegated agents, including child turns, messages, waits, and cancellations.

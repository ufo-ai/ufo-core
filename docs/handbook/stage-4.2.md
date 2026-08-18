# Agent skills, subagents, and workflow profiles  `stage-4.2`

This stage is the system’s “capability shelf.” It is mostly behind-the-scenes setup used before and during the main work loop, so an agent knows what special skills it can use and what child agents it can call for focused jobs. The skill runtime defines what a skill is, reads skill folders, works out dependencies, and copies needed files into the safe workspace where the agent runs. The model catalog skill is built from the live model registry, so users can ask which AI models are really available. The skills package file simply makes these modules importable.

Several files describe subagent profiles, which are recipes for smaller helper agents. There is a fallback general-purpose subagent, plus specialized helpers for browser use, writing, research, and website building. Each profile sets the helper’s instructions, allowed tools, input, output, model, and limits. Extension manifests announce extra bundles, such as the brief pipeline and research tools. The skill creation extension adds user-made skills, while its store safely saves, loads, updates, and deletes them without overwriting built-ins or exceeding limits.

## Files in this stage

### Skill runtime foundations
Core skill packaging and runtime logic establish how built-in skills are exposed and materialized for agents.

### `core/src/ufo/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a simple but important problem: users need to know what models are available, what they cost, and what their limits are. If that information were written by hand, it could easily become stale. A model might be added, renamed, repriced, or given a different context window, while the help text still says the old thing.

Instead, this file turns the live model registry into a readable skill. The model registry is the system’s source of truth for model facts: model id, provider, knowledge cutoff, context window, price, reasoning support, and API surface. At startup, `model_catalog_skill` reads those records, sorts them by model id, and renders them into a Markdown table. Markdown is plain text with simple formatting, often used for documentation.

The result is wrapped in a `RuntimeSkill`, which is a skill the system can load and show to users or agents. Think of it like printing a menu from the restaurant’s actual kitchen inventory instead of from an old paper copy. The key behavior is that the catalog is not separate documentation; it is generated from the same records the runtime uses to make real decisions.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns an internal price value into a user-friendly dollar string. It is used so model prices can be displayed clearly as dollars per million tokens.

**Data flow**: It receives a price stored as micro-dollars, which are millionths of a dollar. It divides that number by the constant that represents one dollar in micro-dollars, formats the result with two decimal places, and returns text like `$1.25`.

**Call relations**: `model_catalog_skill` calls this helper while building each row of the model table. The helper does only the price formatting, so the larger catalog-building function can stay focused on assembling the full skill text.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the model catalog skill from the current model registry. Someone would use it during boot so the system can offer a trustworthy list of available models and their facts.

**Data flow**: It receives a `ModelRegistry`, which contains the registered model specifications. It reads each model’s facts, sorts the models by id, formats them into a Markdown table, adds a short explanation and skill metadata, then returns a `RuntimeSkill` containing the finished catalog text.

**Call relations**: At startup, this function is called with the live registry. As it builds the price columns, it hands each input and output price to `_per_mtok` for readable formatting. When the body and metadata are ready, it creates a `RuntimeSkill`, which is the object the rest of the skill runtime can load and present.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `core/src/ufo/skills/__init__.py`

`other` · `import time`

This file is intentionally empty, but it still has a useful job. In Python, an `__init__.py` file tells the language, “treat this folder as a package,” meaning its contents can be imported using a dotted name like `ufo.skills.something`. Think of it like a label on a drawer: the drawer may contain many tools, and the label lets the rest of the system find them in an organized way. Without this file, depending on the Python version and project setup, imports from the `ufo.skills` folder could become less predictable or fail in environments that expect traditional packages. Because it contains no code, it does not define behavior, load data, or run setup work. Its value is structural: it gives the codebase a clear place for modules related to “skills,” while keeping the package entry point simple and side-effect free.


### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading`

A skill is a folder that teaches the agent a reusable workflow. The folder must contain a SKILL.md file with a small YAML frontmatter section, which is structured metadata, followed by the instructions the agent should read. It may also contain extra files, such as examples or scripts. This file turns those folders into RuntimeSkill objects, builds a registry of all available skills, and decides what happens when the agent asks to load one. The important idea is that loading a skill has two parts: the instruction text is shown to the model, and the skill’s files are copied into a safe area of the sandbox under .skills/<name>/. Dependencies are resolved through an explicit depends list, not by folder nesting. Nested child skills get names like parent/child, but they are only pulled in if something depends on them. The file also avoids repeating instructions already present in the conversation. If a skill is loaded again, its files are still re-mounted, but its workflow text is replaced with a short “already loaded” note. Without this file, the system would not have a safe, consistent way to discover skills, prevent name confusion, load dependencies, or give the agent access to the right files.

#### Function details

##### `skill_mount_root`  (lines 44–47)

```
def skill_mount_root(name: str) -> str
```

**Purpose**: Builds the workspace path where one skill’s files should live. It gives every skill its own home under the shared .skills area.

**Data flow**: It takes a skill name as text, joins it with the fixed skills mount directory, and returns a path string like the destination shelf for that skill’s files.

**Call relations**: RuntimeSkill.mount_root calls this when code needs the mount location for a particular skill, keeping the path rule in one small place.

*Call graph*: called by 1 (mount_root).


##### `RuntimeSkill.mounted_files`  (lines 66–67)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Collects all files that should be written into the sandbox for this skill. It includes the original SKILL.md plus any bundled asset files.

**Data flow**: It reads the skill’s stored raw SKILL.md text and asset file bytes, turns SKILL.md back into bytes, and returns a dictionary from relative file path to file contents.

**Call relations**: mount_skill calls this before writing files into the sandbox, so the mounting step gets one complete package of everything the skill should expose.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 69–70)

```
def mount_root(self) -> str
```

**Purpose**: Returns the sandbox folder where this specific skill should be mounted. It is the per-skill version of the general mount-root rule.

**Data flow**: It reads the RuntimeSkill name, passes that name to skill_mount_root, and returns the resulting workspace path.

**Call relations**: mount_skill calls this to decide where to write the skill’s files. It hands off the actual path-building to skill_mount_root so all mount paths stay consistent.

*Call graph*: calls 1 internal fn (skill_mount_root); called by 1 (mount_skill).


##### `LoadedSkill.prompt_body`  (lines 82–92)

```
def prompt_body(self) -> str
```

**Purpose**: Builds the text block that is shown to the model for one loaded skill. The block names the skill and then includes only its workflow instructions.

**Data flow**: It reads the loaded skill, checks whether it was loaded directly or pulled in as a dependency, adds the right header, and returns a single text block containing the header and instructions.

**Call relations**: This method is the per-skill text builder used when a load result is turned into model-visible context. It does not include bundled file contents; those are reached through the mounted file paths instead.


##### `LoadedSkills.reseed`  (lines 108–125)

```
def reseed(self, loads: Iterable[tuple[LoadedSkill, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker that remembers which skill instructions are already in the model’s context. This prevents paying the cost of repeating the same workflow text.

**Data flow**: It receives previous loaded skill groups and optional preloaded skills, clears the old sets, records every skill currently in context, and separately records which skills the agent directly asked for.

**Call relations**: It calls LoadedSkills.reset first so the tracker matches the current conversation window instead of stale history. This is used around conversation-window changes, where the system must know what instructions are still visible.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 127–132)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the skills the agent explicitly asked for, then clears the tracker. This is useful at a boundary where old workflow text may be dropped but the system still wants to remember what to reload later.

**Data flow**: It reads the asked_for set, sorts it into a stable tuple, clears both tracking sets, and returns the saved names.

**Call relations**: It calls LoadedSkills.reset after taking the names. In the bigger flow, it acts like emptying a mailbox: it hands over the important requested skill names and leaves the tracker clean.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 134–136)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered loaded-skill state. It is the simple reset button for the in-context and asked-for sets.

**Data flow**: It takes the current LoadedSkills object, empties its in_context set and its asked_for set, and returns nothing.

**Call relations**: LoadedSkills.reseed calls it before rebuilding from known loads, and LoadedSkills.drain calls it after handing off remembered requested skill names.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 139–145)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates the metadata at the top of SKILL.md from the instruction body below it. It also rejects files that do not use the expected frontmatter format.

**Data flow**: It takes the full SKILL.md text, checks that it starts with the frontmatter fence, finds the closing fence, and returns two strings: metadata and body. If the fences are missing, it raises a clear error.

**Call relations**: parse_skill_content calls this as the first parsing step, before turning the metadata into fields such as name, description, and dependencies.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 148–153)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate subfolders that are themselves skills. A subfolder counts as a child skill only if it contains its own SKILL.md file.

**Data flow**: It reads the entries inside one skill directory, filters for directories with a SKILL.md file, sorts them, and returns the matching paths.

**Call relations**: parse_skill calls it so parent skills do not accidentally absorb child skill files, and discover_skills calls it to recursively register those child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 156–189)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory collection of skill files into a RuntimeSkill object. This lets skills be validated the same way whether they came from disk or from saved sandbox data.

**Data flow**: It receives a claimed directory name, a mapping of file paths to bytes, and optional registry naming information. It reads SKILL.md, splits and parses the YAML frontmatter, checks that the frontmatter name matches the folder name, gathers non-SKILL.md assets, and returns a RuntimeSkill.

**Call relations**: parse_skill calls this after reading files from disk. It relies on _split_frontmatter for the SKILL.md structure and yaml.safe_load for the frontmatter metadata.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 192–201)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill folder from disk and parses it into a RuntimeSkill. It treats nested child skill folders as separate skills, not as ordinary assets of the parent.

**Data flow**: It receives a filesystem path, finds child skill directories, reads all regular files except those inside child skill folders, and passes the resulting file map to parse_skill_content.

**Call relations**: discover_skills calls this for each skill directory it visits. This function bridges the real filesystem and the in-memory parser.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 204–222)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning them in one flat lookup map. This turns a folder tree into registry entries with names such as parent/child.

**Data flow**: It receives a skill directory plus optional registry and parent names, parses the current skill, then walks each immediate child skill directory and merges the child results into one dictionary.

**Call relations**: _load_core_skills calls this while collecting built-in skills. It uses parse_skill for the current folder and _child_skill_dirs to find children to recurse into.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 225–232)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the project’s built-in skills from the core skills directory. This creates the base skill set available before any packs or user skills are added.

**Data flow**: It receives a root directory, lists visible child directories, discovers skills in each one, and returns a dictionary keyed by skill name.

**Call relations**: This runs when the module builds CORE_SKILLS_BY_NAME. It calls discover_skills for each core skill folder so nested built-in skills are included too.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 249–254)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up one skill by name and gives a helpful error if it is missing. This keeps unknown skill requests from failing silently.

**Data flow**: It receives a skill name, checks the registry dictionary, and returns the matching RuntimeSkill. If the name is not present, it raises an error that also lists available names.

**Call relations**: SkillRegistry.closure and its nested add function call this whenever they need to resolve a requested skill or a dependency name into the actual skill object.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 256–279)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Figures out the full set of skills needed for a load request, including dependencies. A “closure” means the requested skills plus everything they depend on, with each skill included once.

**Data flow**: It receives one or more requested skill names, creates direct LoadedSkill entries for those names, then walks each depends list and adds dependency LoadedSkill entries without duplicating names or looping forever on cycles. It returns the ordered tuple of loaded entries.

**Call relations**: core/src/ufo/loop/engine._loaded_skill_closures calls this when the runtime needs to expand requested skill loads. It calls SkillRegistry.named to resolve names and uses LoadedSkill objects to remember whether a skill was direct or pulled by another skill.

*Call graph*: calls 1 internal fn (named); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 269–274)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill and then adds that dependency’s own dependencies. It is the recursive helper inside SkillRegistry.closure.

**Data flow**: It receives a RuntimeSkill and the name of the skill that pulled it in. If the skill is already loaded, it stops; otherwise it records the skill as a dependency and walks its depends list.

**Call relations**: SkillRegistry.closure uses this helper while expanding dependency chains. The helper calls SkillRegistry.named to resolve each dependency name before continuing the walk.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 281–289)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the public list of loadable top-level skills and their descriptions. This is what can be shown to the agent as the available skill catalog.

**Data flow**: It reads the registry in its stored order, keeps only skills that have no parent, and returns name-description pairs.

**Call relations**: core/src/ufo/serve._mount_shared_surfaces calls this when preparing shared surfaces such as the system prompt area that needs the skill index. Child skills are intentionally left out because they are reached through parent instructions.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.merged_with`  (lines 291–303)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that adds saved user skills after the base core and pack skills. It refuses user skills that try to reuse an existing name.

**Data flow**: It copies the current registry dictionary, checks each user skill, logs and skips any name collision, adds non-colliding user skills, and returns a new SkillRegistry.

**Call relations**: This function protects the registry before user-controlled skills are included. It calls the logging system when a user skill is refused and constructs a fresh SkillRegistry for the merged result.

*Call graph*: 2 external calls (__init__, log).


##### `_mounted_tree`  (lines 309–327)

```
def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Creates a compact tree-shaped text listing of all files mounted by a skill load. This shows the agent where files are without repeating long path prefixes over and over.

**Data flow**: It receives loaded skills, collects every mounted file path under each skill name, sorts them, breaks paths into parts, and returns an indented text tree rooted at the .skills directory.

**Call relations**: loaded_context calls this at the end of building the model-visible load result, so the workflows and the file map are presented together.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 330–344)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text that a skill load contributes to the model’s context. It includes new workflow instructions, a note for already-seen workflows, and a tree of mounted files.

**Data flow**: It receives the loaded skill entries and a collection of skill names already in context. It renders instruction blocks only for new skills, adds one note for repeated skills, appends the mounted-file tree, and returns the combined text.

**Call relations**: This is the final text-rendering step shared by normal skill loading and preloaded subagent skills. It calls _mounted_tree so the file locations are always shown even when instruction text is not repeated.

*Call graph*: calls 1 internal fn (_mounted_tree).


##### `mount_skill`  (lines 347–355)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Writes one skill’s files into the sandbox workspace under that skill’s safe mount folder. This makes the skill’s assets available to the agent as files.

**Data flow**: It receives a SandboxSession and a RuntimeSkill, asks the skill for its mount root and file contents, checks each file path is contained inside the skill’s own mount area, and writes each file into the sandbox.

**Call relations**: This is the file-writing side of skill loading. It calls RuntimeSkill.mount_root and RuntimeSkill.mounted_files to know where and what to write, uses contained_relative to prevent path escape, and then hands each safe write to SandboxSession.write_file.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files); 1 external calls (contained_relative).


### User-created skill lifecycle
The skill creation extension declares and implements persistent member-authored skill management.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`domain_logic` · `extension load, object operations, and skill loading during turns`

This file is the bridge between “a user wrote a skill” and “the agent can use that skill later.” A skill here is a small bundle of text files, including a required SKILL.md file, saved under the currently bound agent. That agent ownership matters: the saved skills are not private to one member; they are available to anyone acting through that agent.

The file defines the shape of a saved skill request. File contents can be provided directly, read from a workspace file, or kept unchanged by referring to a stored file’s SHA-256 digest, which is a stable fingerprint of its bytes. It deliberately avoids returning saved file bodies in normal object reads, so large or sensitive skill text is not casually copied into conversation context.

Before saving, it checks several safety rules. File paths must stay inside the skill’s own mounted folder, like making sure a delivery driver only drops boxes inside the assigned room. Files must be UTF-8 text, not binary data. The total number and size of files are capped. Workspace file references are read inside the sandbox, which is the controlled execution area.

At the bottom, the file registers the object kind, the built-in “create-skill” helper skill, and the runtime loader that makes saved skills available on later turns.

#### Function details

##### `_require_ext`  (lines 99–102)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This function makes sure an ExtensionContext is present before any skill object operation continues. The ExtensionContext is the system’s handle for the current extension run, including which agent is being used.

**Data flow**: It receives either an ExtensionContext or nothing. If it receives one, it returns it unchanged; if it receives nothing, it stops the operation with a clear runtime error.

**Call relations**: Most public SkillObjects operations call this at the start, because they cannot safely read or write agent-owned skills without knowing the current extension context.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 105–115)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This function checks that every file path in a skill stays inside that skill’s own folder. It prevents a saved skill from later writing files somewhere outside its allowed area.

**Data flow**: It receives the skill name and the proposed skill spec. It computes the mount folder for that skill, checks each file path against it, and either returns silently or raises a ValueError if a path tries to escape.

**Call relations**: SkillObjects.apply calls this before saving. It relies on skill_mount_root to know the allowed folder and contained_relative to enforce that the path does not climb out of it.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_mount_root).


##### `_text`  (lines 118–124)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This function verifies that a skill file is plain UTF-8 text. Skills in this system are text bundles, so binary files are rejected.

**Data flow**: It receives a path and raw bytes. It tries to decode the bytes as text; success returns the decoded string, while failure raises a ValueError naming the problem file.

**Call relations**: SkillObjects._resolve calls this after it has gathered each file’s bytes, so bad file contents are caught before the skill is saved.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 131–132)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of saved skills for the current agent during a tool or object call. It is used when the caller wants the skill names and short summaries, not full file details.

**Data flow**: It receives the tool context and a list query such as paging options. It checks the extension context, gathers rows for saved skills, passes them through the object paging helper, and returns an ObjectPage.

**Call relations**: This is one of the object-store entry points used by the platform. It hands the real row-building work to SkillObjects._rows and then formats the result with object_page.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 134–146)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same kind of saved-skill list for portal-style member access outside a live turn. The member information is accepted, but the skills are still scoped to the bound agent, not to that individual member.

**Data flow**: It receives an optional extension context, member details, admin flag, and a list query. It requires the extension context, loads the agent’s skill rows, paginates them, and returns the page.

**Call relations**: The portal calls this when a signed-in member browses skills. Like SkillObjects.list, it depends on SkillObjects._rows and object_page, with _require_ext guarding that an agent context exists.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 148–149)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches the detail view for one saved skill during a tool or object call. The detail describes the files by digest and size rather than returning their full contents.

**Data flow**: It receives the tool context and a skill name. It checks the extension context, asks SkillObjects._skill for the stored detail, and returns that detail or None if the skill is not found.

**Call relations**: The platform calls this when someone asks for one skill object. It delegates all stored-file inspection and detail construction to SkillObjects._skill.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 151–170)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This returns one saved skill for portal-style member access, including both its list row and its detail record. It only returns a detail if the skill also appears in the normal row listing.

**Data flow**: It receives an extension context, skill name, member details, and admin flag. It loads the agent’s rows, looks for the named skill, then loads its detail; if either part is missing, it returns None. Otherwise it returns a MemberObject containing both row and detail.

**Call relations**: The portal uses this when a member opens a specific skill. It calls SkillObjects._rows first to decide whether the skill should be visible, then SkillObjects._skill to build the digest-based detail.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 172–176)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the lightweight list entries for all saved skills owned by the current agent. Each row contains the skill name and a shortened description.

**Data flow**: It receives an ExtensionContext. It loads all saved runtime skills from UserSkillStore, turns each one into an ObjectRow, trims descriptions to the configured summary length, and returns the rows as a tuple.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail use this as their shared way to decide what saved skills are visible in list form.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 178–201)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the detailed object record for one saved skill without exposing the actual file bodies. It gives callers stable file fingerprints so unchanged files can be kept on a later update.

**Data flow**: It receives an ExtensionContext and a skill name. It loads the skill’s stored files and timestamps; if either is missing, it returns None. Otherwise it creates a UserSkillSpec where each file is represented by a FileRef containing a SHA-256 digest and byte size, adds creation and update times, and links the skill to the owning agent.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this when they need the detail view. It talks to UserSkillStore for saved data and asks the ExtensionContext for the agent name used in the scoped_to link.

*Call graph*: calls 1 internal fn (agent_name); called by 2 (get, member_detail); 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 203–217)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This returns a compact health/status summary for one saved skill. It answers what the skill says it is, how many files it has, and how many bytes those files use.

**Data flow**: It receives the tool context, skill name, and an expected generation value. It loads the skill files for the current agent; if none exist, it returns None. Otherwise it parses the skill content to get the description, counts files and bytes, and returns those values in a dictionary.

**Call relations**: The object system can call this to report the current state of a skill. It uses UserSkillStore to fetch files and parse_skill_content to understand the SKILL.md metadata.

*Call graph*: calls 1 internal fn (_require_ext); 2 external calls (__init__, parse_skill_content).


##### `SkillObjects.apply`  (lines 219–236)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a saved skill for the current agent. It enforces limits and safety checks before writing anything to storage.

**Data flow**: It receives the tool context, skill name, new spec, optional old spec, and an expected generation value. It checks that there are not too many files, confirms all paths stay inside the skill folder, resolves file bodies from inline text, workspace references, or stored digests, checks the total byte limit, and saves the final bytes to UserSkillStore.

**Call relations**: The platform calls this when a manifest is applied to save a skill. It uses _require_ext for context, _contained_keys for path safety, SkillObjects._resolve for turning the spec into real bytes, and UserSkillStore.save for persistence.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 238–246)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved skill for the current agent. It is the delete operation for the skill object kind.

**Data flow**: It receives the tool context, skill name, and an expected generation value. It checks the extension context, then asks UserSkillStore to delete the named skill.

**Call relations**: The platform calls this when a skill object is deleted. It only needs _require_ext to identify the current agent scope, then hands the deletion to UserSkillStore.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 248–296)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns a user’s skill spec into the exact bytes that should be saved. It supports three ways to provide each file: new inline text, a workspace file reference, or a digest saying to keep an already stored file unchanged.

**Data flow**: It receives the tool context, skill name, and spec. It loads any existing stored files, verifies FileRef digests against those stored bytes, reads any FileFrom workspace files inside the sandbox, decodes the sandbox’s base64 results back into bytes, converts inline strings to bytes, verifies each file is UTF-8 text, and returns a path-to-bytes dictionary ready to save.

**Call relations**: SkillObjects.apply calls this during create or update. This function coordinates with UserSkillStore for old files, the sandbox for workspace reads, hashlib for digest checks, JSON and base64 for safe data transfer, and _text for final text validation.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `_runtime_skills`  (lines 324–326)

```
async def _runtime_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads the current agent’s saved skills so they can be added to the runtime skill registry. In plain terms, it makes previously saved user skills available for use in a turn.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore to load all saved skills for that context and returns them as RuntimeSkill objects.

**Call relations**: The manifest registers this as the runtime skill loader. When the host prepares skills for an agent, it calls this function to pull saved skills into the active set.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 329–336)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s declaration to the host system. It says the extension’s name and version, what object kind it adds, what built-in helper skill it ships, and how to load saved runtime skills.

**Data flow**: It takes no input. It creates and returns a Manifest containing the skill_create extension metadata, the skill object definition, the bundled create-skill SkillSpec, and the runtime skill loader.

**Call relations**: The host calls this when discovering or loading the extension. The returned Manifest is the top-level wiring that connects SKILL_OBJECT, the bundled authoring skill directory, and _runtime_skills to the rest of the system.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and cross-cutting skill persistence`

A “skill” here is a small bundle of files that an agent can use later, such as a SKILL.md file plus any supporting assets. This file is the agent’s long-term notebook for those skills: it turns the files into database records, and later turns those records back into runnable skills.

The store always works inside the current workspace and agent, taken from the surrounding execution context. That means one agent cannot accidentally read or change another agent’s saved skills. Before saving, it checks that the skill name is a safe lowercase slug, like `summarize-email`, not a path or strange filename. This matters because the name is both a registry key and a stored identifier. It also refuses to save a new user skill with the same name as a core or packaged skill, unless this agent already owns that user skill. This prevents user-created skills from secretly replacing trusted built-in ones.

For storage, the file contents are converted to base64 text, which is a safe way to put arbitrary bytes into a text database column. A hash digest is stored too, like a fingerprint of the saved content. When loading, the store decodes the files and asks the skill parser to rebuild runtime-ready skills. If one saved skill is corrupt, it logs a warning and skips that one instead of breaking all skill loading.

#### Function details

##### `UserSkillStore.save`  (lines 69–126)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str]) -> RuntimeSkill
```

**Purpose**: Saves one user-authored skill for the current agent, either creating it or replacing the existing saved copy. It also enforces the important safety rules: valid name, no stealing a built-in skill name, and no exceeding the per-agent skill limit.

**Data flow**: It receives a skill name, a set of file paths mapped to file bytes, and the names already present in the skill registry. It reads the current workspace and agent, checks the name, parses the files to make sure they form a valid runtime skill, checks whether this agent already owns that name, and checks the saved-skill count if this would be a new skill. Then it base64-encodes the files, stores them as JSON text, computes a SHA-256 digest as a content fingerprint, and writes the record inside a database transaction. It returns the parsed RuntimeSkill, so the caller can immediately use the skill that was saved.

**Call relations**: This is the main write path for the store. During saving it calls UserSkillStore._owns to decide whether the name is already this agent’s saved skill, and UserSkillStore._count to enforce the limit for new skills. It hands the raw files to parse_skill_content so invalid skill bundles fail before anything is persisted, and it raises clear errors when the caller asks for an unsafe or disallowed save.

*Call graph*: calls 2 internal fn (_count, _owns); 10 external calls (__init__, __init__, __init__, __init__, b64encode, sha256, insert, update, agent_current, parse_skill_content).


##### `UserSkillStore.load_all`  (lines 128–162)

```
async def load_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Loads every saved skill owned by the current agent and turns them back into runtime-ready skills. It is used when the system needs to make the agent’s saved skills available again after they were persisted.

**Data flow**: It reads the current workspace and agent, queries the database for all matching saved skill names and stored content, and processes them in name order. For each row, it validates the stored JSON shape, base64-decodes each saved file back into bytes, and parses the bundle into a RuntimeSkill. If one row is malformed or cannot be parsed, it logs a warning with the workspace, agent, skill name, and error, then continues with the remaining rows. The output is a tuple of successfully loaded RuntimeSkill objects.

**Call relations**: This is the main read-all path for the store. It calls the shared skill parser after reconstructing each file bundle from database text. Unlike save, it does not stop everything when one saved skill is bad; it isolates that failure so the rest of the agent’s skills can still load.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 164–180)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: Fetches the original files for one saved skill owned by the current agent. This is useful when a caller needs the stored source files rather than the parsed runtime skill.

**Data flow**: It receives a skill name, reads the current workspace and agent, and asks the database for the stored content for exactly that skill. If no row exists, it returns None. If a row is found, it validates the stored JSON and base64-decodes each file’s text back into bytes. The output is a dictionary from relative file path to file bytes.

**Call relations**: This is a focused read path for one skill’s raw contents. It uses the same stored format that UserSkillStore.save writes, but it does not call the skill parser because its job is to return files exactly as stored.

*Call graph*: 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 182–191)

```
async def delete(self, name: str) -> None
```

**Purpose**: Deletes one saved user skill for the current agent. It removes the database record if it exists and does nothing visible if it does not.

**Data flow**: It receives a skill name, reads the current workspace and agent, and runs a database delete limited to that workspace, that agent, and that name. It does not return a value. The changed state is that the matching saved skill row is gone, if one was present.

**Call relations**: This is the removal path that complements UserSkillStore.save and the read methods. It relies on the current agent scope so deletion cannot cross into another agent’s saved skills.

*Call graph*: 2 external calls (delete, agent_current).


##### `UserSkillStore.timestamps`  (lines 193–205)

```
async def timestamps(self, name: str) -> tuple[datetime, datetime] | None
```

**Purpose**: Looks up when one saved skill was first created and when it was last updated. This lets callers show history or decide whether a saved skill has changed.

**Data flow**: It receives a skill name, reads the current workspace and agent, and queries the database for the created_at and updated_at fields for that skill. If the skill is not found, it returns None. If it is found, it returns the two datetime values as a pair.

**Call relations**: This is a small metadata read path. It uses the same workspace-and-agent boundary as the rest of the store, and it reads the timestamps maintained when UserSkillStore.save inserts or updates a skill.

*Call graph*: 2 external calls (select, agent_current).


##### `UserSkillStore._count`  (lines 207–219)

```
async def _count(self) -> int
```

**Purpose**: Counts how many user-created skills the current agent has saved. It exists so saving a new skill can enforce the maximum allowed number of saved skills.

**Data flow**: It reads the current workspace and agent, queries the user_skill table for rows matching that scope, and asks the database for the count. It returns that count as an integer and does not change stored data.

**Call relations**: This is an internal helper used by UserSkillStore.save. Save calls it only when the requested name is not already owned by the agent, because the limit applies to adding a new skill rather than updating an existing one.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


##### `UserSkillStore._owns`  (lines 221–233)

```
async def _owns(self, name: str) -> bool
```

**Purpose**: Checks whether the current agent already has a saved user skill with a given name. This helps distinguish updating one of the agent’s own skills from trying to create a new one or collide with a built-in skill.

**Data flow**: It receives a skill name, reads the current workspace and agent, and queries the database for a matching row. If a row exists, it returns true. If not, it returns false. It only reads data and does not modify anything.

**Call relations**: This is an internal helper used by UserSkillStore.save near the start of the save process. Save uses its answer to decide whether a registry-name collision is allowed and whether the per-agent skill count needs to be checked.

*Call graph*: called by 1 (save); 2 external calls (select, agent_current).


### General workflow profiles
Fallback and multi-stage workflow profiles define reusable child-agent behavior beyond a single specialized task.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent setup and spawn dispatch`

When the main agent delegates work to a child agent, the system needs a clear job description for that child. This file provides the default one: a “general_purpose” subagent. Think of it like a standard work order for an assistant: it says what tools the assistant may use, what rules it must follow, and how it should report back.

The profile is deliberately limited. The subagent can read and edit files, run shell commands, search, fetch web pages, load skills, and use some optional extension tools if they exist. But it cannot ask the user questions, create more subagents, message or cancel sibling subagents, or approve account connections. That keeps delegated work focused and prevents a child agent from taking over coordination decisions that belong to the parent.

The prompt text gives practical behavior rules. The subagent should work independently, make reasonable assumptions, avoid repeating failed actions, load relevant skills first, use proper Office file formats for formal documents, and save useful artifacts in the shared workspace. The file then wraps these rules, tool names, and input/output data contracts into a SubagentProfile object. Finally, it exposes that profile as the core set of subagent profiles, which other extension-provided profiles can build on or supplement.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s “label on the box.” When the UFO system discovers or loads the brief-pipeline extension, it needs a clear answer to: what is this extension called, what version is it, and what parts does it add?

The extension adds a simple writing workflow: first an outline agent plans the brief, then a draft agent writes it, then a critic agent reviews it. The parent agent is also given a skill folder that explains how to chain those stages together. In plain terms, it is like setting up an assembly line: one worker sketches the plan, another turns it into a draft, and a reviewer points out what to improve. The parent agent remains in charge of applying the feedback.

The file does not run the pipeline itself. Instead, it builds a Manifest, which is a compact declaration the host system can read. That manifest includes the extension name, its version, the three subagent profiles imported from the pipeline module, and the path to the skill instructions stored under the extension’s skills directory. Without this file, the host system would not know how to register this extension or which agents and skill instructions belong to it.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension description that the host system reads when loading this extension. It packages the extension’s name, version, three subagent profiles, and skill folder into one manifest object.

**Data flow**: It starts with constants from this file, such as the extension name, version, and skill directory path, plus the imported outline, draft, and critic profiles. It wraps the skill directory in a SkillSpec, then places that and the three profiles into a Manifest. The result is a ready-to-read declaration of what this extension provides.

**Call relations**: When the extension system asks this module what it contributes, this function creates the answer. It calls SkillSpec.__init__ to describe the skill folder, then calls Manifest.__init__ to bundle that skill together with the three subagent profiles so the host can register them.

*Call graph*: 2 external calls (__init__, __init__).


### Specialized subagent profiles
Extension-provided child-agent profiles configure focused workers for browsing, writing, research, and website building.

### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `startup and subagent spawning`

This file is like an ID badge and job description for a browser-focused helper agent. The main agent can send work to this helper when a task needs web browsing, such as opening pages, searching, saving screenshots, or gathering information from websites.

The file first loads a long instruction prompt from `subagent_browser.md`. That prompt teaches the browser subagent how to work. It then builds the list of tools the subagent is allowed to use: the browser tool set, plus a few shared file tools such as reading and writing files, editing, and web search. This matters because a subagent should not automatically receive every tool in the system; it gets only the tools needed for its role.

Two small data shapes are defined with Pydantic, a library that checks structured data. `BrowserTask` describes what the parent agent can send in: the task text, an optional starting URL, an optional task name, and whether the child gets extended context. `BrowserResult` describes what comes back: a freeform result string.

Finally, `BROWSER_PROFILE` packages all of this into a `SubagentProfile`. That profile is what the rest of the system can register or spawn. One important detail is that `extended_context` defaults to true, because browser sessions often need enough room to complete multi-step page work without stopping halfway.


### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `startup or extension load, when the writing subagent profile is registered for later use`

This file is like a job description and tool belt for a specialized writing assistant. The project can spawn child agents for focused tasks; this one is designed to work on prose drafts, not code, research, or file-format construction. Without this profile, the parent system would not have a clear, reusable way to start a writing-focused child with the right instructions and limits.

The file first names the profile, model, default skill, and tools. The default skill is `writing-drafts`, which is preloaded so the child starts with the draft workflow already available instead of needing to fetch it during its first turn. The allowed tools are deliberately narrow: reading, writing, editing, searching files, and loading skills. It does not get shell access, a programming REPL, web tools, or file-sharing tools. In plain terms, it can work with draft files in the workspace, but it cannot run programs, browse the internet, or act like a second coding assistant.

It also reads a Markdown prompt file from the nearby `prompts` folder. That prompt becomes the child agent’s standing instructions. Two small Pydantic models describe the shape of messages going in and coming out: a writing task has an `objective` and optional preloaded skills, and the result is a freeform `result`. Finally, everything is bundled into `WRITING_PROFILE`, the object other parts of the system use when they want to launch this writing subagent.


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup/config load`

This file acts like a packing list for the research add-on. When the larger system starts up and loads extensions, it needs a clear answer to questions like: “What tools does this extension add?”, “Which specialist agents can it create?”, and “What extra instructions should be included in prompts?” This file provides that answer in one place.

It names the extension as “research” and gives it a version. It loads a web-research prompt section from a Markdown file, points to two on-demand skill folders, and gathers together the research tools and subagent profiles imported from other parts of the extension. It also declares a conversation slot for sources, which is a shared place where research results can record where information came from.

One important detail is the requirement for “search_providers”. The research pack does not contain its own search credentials or backend. Instead, it expects the deployment to provide a search service. This is like plugging a lamp into a house’s wiring: the lamp declares that it needs electricity, but it does not generate power itself. By declaring this requirement here, the system can fail early during startup if search is missing, instead of surprising the user later when the first research request fails.

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s manifest, which is the structured description the host system reads to install this pack. Someone would use it when loading the extension so the system knows exactly what research capabilities to add.

**Data flow**: It reads constants and imported objects already prepared in this file: the extension name and version, research tools, subagent profiles, prompt text, skill folder paths, the sources conversation slot, and the required search-provider dependency. It wraps the prompt text in a PromptSection, turns each skill folder into a SkillSpec, and puts everything into a Manifest. The result is a single Manifest object that the rest of the system can consume.

**Call relations**: During extension loading, the host asks this function for the research pack’s declaration. The function then creates the smaller pieces needed by the declaration, such as the prompt section and skill specifications, and hands them to Manifest so the host can register tools, subagents, skills, prompt content, conversation slots, and dependency requirements together.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / agent profile registration`

This file is like a job description and toolbox list for two specialist research helpers: `research` and `deep_research`. A main agent can delegate a research task to one of these child agents instead of doing every search and source check itself.

The file names the two profiles, chooses the language model they run on, and lists the tools they are allowed to use. Those tools include web search, fetching web pages, browser tasks, external tools, file reading and writing, shell commands, memory search, and spreadsheet-style work. By keeping this list here, the project can give research agents enough power to gather and organize information while still keeping their scope clear.

It also loads the instruction text for each agent from prompt files on disk. The normal `research` profile uses the standard research prompt. The `deep_research` profile uses a deeper research prompt and gets a much larger round limit, meaning it can keep working through more back-and-forth steps when the task needs many sources or careful synthesis.

Two small Pydantic models describe the shape of the conversation with these agents: each research task comes in as an `objective`, and the answer comes back as a `result`. Pydantic is a validation library that helps ensure data has the expected fields. Without this file, the system would not have these named research subagents available for delegation.


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup`

This file is like a job description and tool belt for a child worker whose only job is building websites. The main system can hand a website task to this subagent, and this file makes sure the subagent starts with the right instructions, the right limits, and the right tools.

It reads a Markdown prompt from the nearby prompts folder. That prompt contains the website-building workflow the subagent should follow. It then defines which tools the subagent can use: basic file tools for reading and editing, build and local website tools, JavaScript and spreadsheet REPL tools for testing or inspecting behavior, and optional web research tools if they are installed. A REPL is an interactive scratchpad where code can be run step by step.

One important choice is that the subagent is not allowed to use `publish_website`. Publishing a full app is left to the parent conversation, because the child subagent works inside the parent’s sandbox and should report back rather than directly deliver the final hosted site. It also does not use `share_file`, because there is no separate member to send files to; the parent can read the shared workspace afterward.

The two small Pydantic models describe the shape of the task sent in and the result sent back. Pydantic is a library that checks data has the expected fields. Finally, all of this is bundled into a `SubagentProfile` so the wider system can launch this website-building helper consistently.

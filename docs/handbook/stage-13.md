# Subagents, skills, and structured long-running work  `stage-13`

This stage is shared support for work that is too large, specialized, or long-running for one agent turn. It gives the main agent a way to call helper agents, load reusable “skills” as saved instructions and files, and keep track of goals across many turns.

The skills files define how skills are stored, read, selected, and loaded without repeating the same guidance. The model catalog skill is built from the live model list, while skill_create stores user-made skills safely, and the web community bridge can fetch public skills without trusting bad or oversized responses. Subagent profiles describe helper workers: general assistants, writers, website builders, homepage publishers, and research agents. The subagents runtime starts these child turns, checks their expected input and output shapes, waits when needed, and returns results. Brief pipeline config splits writing into outline, draft, and critique steps. Website and research delegation tools hand off focused jobs and collect summaries or JSON results. Objectives storage and tools record plans, steps, evidence, blocks, and delegated work so progress survives across turns and is checked against real workspace facts.

## Files in this stage

### Skill runtime and sources
These files define how skills are represented, loaded, selected, created, cataloged, and discovered from community sources.

### `core/src/ufo/runtime/skills/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to the `ufo.runtime.skills` package and to modules inside it.

There is no setup code, no exported shortcut names, and no runtime logic in this file. Its value is structural: it makes the folder part of the project’s module layout. A simple analogy is a blank label on a filing cabinet drawer. The label does not contain the documents, but it tells the system that the drawer exists and can be opened.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.runtime.skills` might fail or behave differently. Keeping it present makes the package boundary explicit and predictable.


### `core/src/ufo/runtime/skills/runtime.py`

`domain_logic` · `startup and request handling`

A skill is a folder that teaches the agent how to do something. Its main file, SKILL.md, has a small YAML frontmatter block at the top, which is structured metadata, followed by the actual instructions the agent should read. This file is the central machinery for turning those folders into usable runtime objects.

It solves several practical problems. First, it validates skills so a broken or wrongly named SKILL.md fails early. Second, it discovers nested child skills while keeping their files separate from the parent. Third, it builds a registry, like a library catalog, so a skill can be found by name and its declared dependencies can be loaded with it. Fourth, it remembers which skill instructions are already in the conversation, so repeated loads do not waste space by pasting the same workflow again.

The file also prepares skills for two destinations: the model’s prompt and the sandbox filesystem. For the prompt, it creates readable blocks with headers such as “Skill: sandbox” and a tree of loaded files. For the sandbox, it encodes file contents and asks the sandbox to place them under $UFO_HOME/skills. System skills can also be packed into a deterministic ZIP bundle so different parts of the system agree on exactly which built-in skill files are being used.

#### Function details

##### `skill_root`  (lines 52–54)

```
def skill_root(name: str) -> str
```

**Purpose**: Builds the stable filesystem location where one skill should appear inside the runtime environment. Someone uses it when they need the path for a named skill under $UFO_HOME/skills.

**Data flow**: It takes a skill name as text → joins it onto the shared skills root path → returns a string such as $UFO_HOME/skills/sandbox.

**Call relations**: RuntimeSkill.root calls this when code needs the path for a particular RuntimeSkill. It is the small shared rule that keeps all skill paths formatted the same way.

*Call graph*: called by 1 (root).


##### `RuntimeSkill.all_files`  (lines 92–93)

```
def all_files(self) -> dict[str, bytes]
```

**Purpose**: Returns every file that belongs to a skill, including SKILL.md and any extra bundled assets. This gives later code one complete package to hash, list, or install.

**Data flow**: It reads the skill’s saved raw SKILL.md text and its stored asset files → turns SKILL.md into bytes and combines it with the asset mapping → returns a dictionary from file path to file bytes.

**Call relations**: RuntimeSkill.content_digest uses it to calculate a fingerprint of the whole skill. _wire_skill uses it to prepare every file for transfer into the sandbox.

*Call graph*: called by 2 (content_digest, _wire_skill).


##### `RuntimeSkill.root`  (lines 95–96)

```
def root(self) -> str
```

**Purpose**: Returns the runtime folder path for this specific skill. It is a convenience method so callers do not have to know how skill paths are assembled.

**Data flow**: It reads the RuntimeSkill name → passes that name to skill_root → returns the resulting $UFO_HOME/skills/... path.

**Call relations**: It is called by _wire_skill while preparing file paths for sandbox installation. It delegates the actual path format to skill_root.

*Call graph*: calls 1 internal fn (skill_root); called by 1 (_wire_skill).


##### `RuntimeSkill.card`  (lines 98–106)

```
def card(self) -> SkillCard
```

**Purpose**: Creates the lightweight catalog version of a skill. The card contains routing information, like name, description, dependencies, and target agents, but not the full instruction body.

**Data flow**: It reads selected fields from a RuntimeSkill → copies them into a SkillCard → returns that card for searching and dependency resolution.

**Call relations**: The registry uses cards when it needs to reason about which skills are loadable without reading or carrying full skill content. This keeps selection and dependency walking lighter than loading complete files.

*Call graph*: 1 external calls (__init__).


##### `RuntimeSkill.content_digest`  (lines 108–114)

```
def content_digest(self) -> str
```

**Purpose**: Computes a stable fingerprint for a skill’s full contents. This lets the system tell whether two skill packages are exactly the same, even across caches, bundles, or sandbox images.

**Data flow**: It gathers all skill files through all_files → sorts them so order is predictable → hashes each path and each file’s bytes using SHA-256, a standard one-way fingerprint algorithm → returns a string beginning with sha256:.

**Call relations**: SystemSkillBundle.from_skills uses this to detect duplicate skill names with different contents and to build manifests. _wire_skill uses it when sending a skill to the sandbox.

*Call graph*: calls 1 internal fn (all_files); called by 1 (_wire_skill); 1 external calls (sha256).


##### `SystemSkillBundle.from_skills`  (lines 126–151)

```
def from_skills(cls, skills: Iterable[RuntimeSkill]) -> 'SystemSkillBundle'
```

**Purpose**: Builds one immutable bundle of system skills as a ZIP archive plus a manifest. This is used when the server, runtime queue, or sandbox template need the exact same built-in skills.

**Data flow**: It receives RuntimeSkill objects → checks that no name is reused for different content → builds a compact JSON manifest describing each skill’s digest and files → hashes that manifest to name the whole bundle → writes the manifest and every skill file into a deterministic ZIP archive → returns a SystemSkillBundle containing the digest, archive bytes, and manifest bytes.

**Call relations**: Startup and serving code call this when preparing shared skill surfaces or sandbox images. It uses _write to place each file into the archive in a repeatable way.

*Call graph*: called by 4 (init_runtime, _mount_shared_surfaces, run, system_skill_bundle); 4 external calls (sha256, BytesIO, dumps, ZipFile).


##### `SystemSkillBundle._write`  (lines 154–157)

```
def _write(archive: zipfile.ZipFile, path: str, content: bytes) -> None
```

**Purpose**: Writes one file into a ZIP archive with fixed metadata. Fixed metadata matters because otherwise ZIP files can differ just because timestamps or permissions changed.

**Data flow**: It receives an open ZIP archive, a path, and bytes → creates a ZIP entry with a fixed old timestamp and normal file permissions → writes the bytes at that path.

**Call relations**: SystemSkillBundle.from_skills calls this for the manifest and each skill file. It is a helper that makes the bundle reproducible.

*Call graph*: 2 external calls (writestr, ZipInfo).


##### `LoadedSkill.prompt_body`  (lines 170–180)

```
def prompt_body(self) -> str
```

**Purpose**: Turns one loaded skill into the text that should be pasted into the model’s context. It labels whether the skill was directly requested or came along as a dependency.

**Data flow**: It reads the loaded skill’s name, dependency marker, and instruction body → builds a markdown header and appends the instructions → returns that prompt text.

**Call relations**: loaded_context uses this for every loaded skill whose instructions are not already present in the conversation. It is the bridge from parsed skill object to model-readable guidance.


##### `LoadedSkills.reseed`  (lines 206–225)

```
def reseed(self, loads: Iterable[tuple[LoadedRef, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the tracker of which skill instructions are already in the model’s context. This prevents duplicate instruction blocks after previous loads, transcript replay, or context compaction.

**Data flow**: It receives previously resolved loads and optionally preloaded skills → clears the old tracker → records every skill name as currently in context → records only directly requested skills as asked_for → also marks preloaded skills as present but not user-requested.

**Call relations**: It calls reset before rebuilding. Runtime code can use this after reconstructing conversation state so future load_skill calls know what not to repeat.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 227–232)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of skills the agent explicitly asked for, then clears the tracker. This is useful at a boundary where full instruction text is being dropped but the system wants to remember what can be reloaded later.

**Data flow**: It reads the asked_for set → sorts it into a tuple → resets both tracking sets → returns the sorted names.

**Call relations**: It calls reset after collecting the names. It is the counterpart to reseed: one saves the important names, the other rebuilds state from known loads.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 234–236)

```
def reset(self) -> None
```

**Purpose**: Clears all memory of loaded and directly requested skills from this tracker. It gives the tracker a clean slate.

**Data flow**: It takes no outside data → empties in_context and asked_for in place → returns nothing.

**Call relations**: LoadedSkills.reseed calls it before rebuilding from known loads. LoadedSkills.drain calls it after handing out the remembered asked-for names.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 239–245)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Separates a SKILL.md file into metadata and instruction body. It also enforces that the file starts and ends its YAML frontmatter block correctly.

**Data flow**: It receives the full SKILL.md text → checks that it begins with the frontmatter fence --- → finds the closing fence → returns the metadata text and the remaining body text, or raises an error if the format is invalid.

**Call relations**: parse_skill_content calls this before interpreting the YAML metadata. It is the first validation gate for every skill file.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 248–253)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child folders that are themselves skills. A child skill is recognized by having its own SKILL.md file.

**Data flow**: It receives a directory path → looks at its direct children → keeps only child directories containing SKILL.md → returns them sorted.

**Call relations**: parse_skill uses this to exclude child skill subtrees from the parent’s asset files. discover_skills uses it to recursively register child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 256–297)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Parses a skill from in-memory files and validates its metadata. This is used when the files may come from disk or from stored bytes, but should be checked the same way.

**Data flow**: It receives a claimed directory name, a mapping of file paths to bytes, and optional registry and parent names → reads and decodes SKILL.md → splits frontmatter from body → parses YAML metadata → checks that the declared name matches the directory and that options such as agents and indexed have valid types → separates asset files from SKILL.md → returns a RuntimeSkill.

**Call relations**: parse_skill calls this after reading files from disk. It calls _split_frontmatter and creates the RuntimeSkill that the registry and loader use later.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 300–309)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads a skill folder from disk and turns it into a RuntimeSkill. It keeps child skill folders out of the parent’s file bundle so each skill owns its own files.

**Data flow**: It receives a filesystem path and optional registry and parent names → finds immediate child skill directories → walks the parent folder for files while skipping those child subtrees → reads file bytes → passes the collected files to parse_skill_content → returns the parsed RuntimeSkill.

**Call relations**: discover_skills calls this for each skill directory it visits. It depends on _child_skill_dirs for correct parent-child separation and parse_skill_content for validation.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 312–330)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning a flat name-to-skill map. Flat names such as parent/child let the registry look up every skill by one unique name.

**Data flow**: It receives a skill directory and optional names → parses the current directory as one skill → adds it to a dictionary → finds immediate child skill directories → recursively discovers each child under a path-style name → returns the combined dictionary.

**Call relations**: _load_core_skills calls this when loading built-in skills. It calls parse_skill for the current folder and _child_skill_dirs to find children.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 333–340)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads all built-in skills shipped beside this runtime file. These core skills are available at startup before member-created skills are added.

**Data flow**: It receives a root directory → selects visible child directories as skill folders → discovers each folder’s skill tree → merges them into one dictionary keyed by skill name → returns that dictionary.

**Call relations**: This runs when the module initializes CORE_SKILLS_BY_NAME. It calls discover_skills for each core skill directory.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.__post_init__`  (lines 367–369)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the default set of bundled skill names when none was provided. This marks the current deploy-tier skills as the static skills that can be assumed to exist in system bundles.

**Data flow**: It checks bundled_names after the dataclass is created → if missing, sets it to all names currently in by_name → returns nothing.

**Call relations**: It runs automatically whenever a SkillRegistry is constructed. Later bundled_skills, materialize, and load_skills rely on this bundled name set.


##### `SkillRegistry.named`  (lines 371–375)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up a deploy-controlled skill by exact name. If the name is unknown, it raises a helpful error instead of returning nothing silently.

**Data flow**: It receives a skill name → checks the by_name dictionary → returns the RuntimeSkill if found → otherwise asks _unknown to build an error with possible suggestions.

**Call relations**: Callers use this when they need the full RuntimeSkill from the deploy tier. It uses _unknown to keep error messages consistent.

*Call graph*: calls 1 internal fn (_unknown).


##### `SkillRegistry._unknown`  (lines 377–380)

```
def _unknown(self, name: str) -> ValueError
```

**Purpose**: Creates a clear error for an unknown skill name, including close spelling suggestions when possible. This helps users recover from typos.

**Data flow**: It receives the missing name → gathers all known names → finds close text matches → builds and returns a ValueError.

**Call relations**: SkillRegistry.named and SkillRegistry._card call this when lookup fails. It calls known_names so suggestions include both deploy and member skills.

*Call graph*: calls 1 internal fn (known_names); called by 2 (_card, named); 1 external calls (get_close_matches).


##### `SkillRegistry._card`  (lines 382–389)

```
def _card(self, name: str) -> SkillCard
```

**Purpose**: Finds the routing card for a skill name, whether the skill is deploy-controlled or member-saved. The card is enough information to resolve dependencies without loading full files.

**Data flow**: It receives a name → first checks deploy skills and converts one to a card if found → otherwise checks member_cards → returns the SkillCard or raises an unknown-skill error.

**Call relations**: SkillRegistry.closure and its nested add function call this while walking requested skills and dependencies. It uses _unknown when neither tier contains the name.

*Call graph*: calls 1 internal fn (_unknown); called by 2 (closure, add).


##### `SkillRegistry.known_names`  (lines 391–394)

```
def known_names(self) -> frozenset[str]
```

**Purpose**: Returns every skill name that this registry can resolve. This is the combined namespace of deploy skills and member skill cards.

**Data flow**: It reads keys from by_name and member_cards → unions them into one frozen set → returns that set.

**Call relations**: SkillRegistry._unknown calls this to produce useful suggestions. Save and validation paths can also use the same idea to check whether a name is already taken.

*Call graph*: called by 1 (_unknown).


##### `SkillRegistry.all_cards`  (lines 396–401)

```
def all_cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: Returns every loadable skill as a lightweight routing card. This is useful for search or selection because it avoids carrying full instruction bodies.

**Data flow**: It converts each deploy RuntimeSkill into a SkillCard → appends existing member_cards → returns all cards as one tuple.

**Call relations**: Search code can score these cards when deciding which skill might match a request. It relies on RuntimeSkill.card for deploy skills.


##### `SkillRegistry.bundled_skills`  (lines 403–406)

```
def bundled_skills(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: Returns the deploy skills that are part of the static system bundle. These are the skills that can be mounted or cached as known built-in content.

**Data flow**: It reads bundled_names → filters by_name to skills whose names are in that set → returns those RuntimeSkill objects as a tuple.

**Call relations**: Serving setup calls this when mounting shared surfaces. It depends on __post_init__ having set a sensible bundled_names default.

*Call graph*: called by 1 (_mount_shared_surfaces).


##### `SkillRegistry.closure`  (lines 408–432)

```
def closure(self, *names: str) -> tuple[LoadedRef, ...]
```

**Purpose**: Resolves requested skill names into the full ordered load list, including dependencies. A dependency is another skill that must come along because the skill declared it needs it.

**Data flow**: It receives one or more requested names → creates direct LoadedRef entries for each unique requested name → walks each card’s depends list recursively → adds each dependency once with the name of the skill that pulled it → returns the ordered tuple of LoadedRef objects.

**Call relations**: The runtime engine calls this when working out what a load_skill request really includes. It calls _card for lookups and uses the nested add helper to walk dependencies safely.

*Call graph*: calls 1 internal fn (_card); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 422–427)

```
def add(card: SkillCard, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency and then follows that dependency’s own dependencies. It prevents repeats, which also keeps dependency cycles from causing infinite recursion.

**Data flow**: It receives a SkillCard and the name of the skill that depended on it → if the card is already recorded, it stops → otherwise stores a LoadedRef marked as a dependency → looks up and adds each of that card’s dependencies.

**Call relations**: This helper lives inside SkillRegistry.closure and is called while resolving transitive dependencies. It calls _card whenever it needs the next dependency’s routing card.

*Call graph*: calls 1 internal fn (_card); 1 external calls (__init__).


##### `SkillRegistry.materialize`  (lines 434–456)

```
async def materialize(self, refs: Sequence[LoadedRef]) -> tuple[LoadedSkill, ...]
```

**Purpose**: Turns resolved routing references into full loaded skills with instruction text and files. This is where member skill bytes are actually fetched.

**Data flow**: It receives a sequence of LoadedRef objects → for each name, uses by_name if it is a deploy skill or calls the async materializer if it is a member skill → checks that the returned skill still exists and has the expected name → wraps it as a LoadedSkill with dependency and bundled information → returns all LoadedSkill objects in order.

**Call relations**: After closure decides what should load, materialize obtains the real RuntimeSkill bodies. It hands back LoadedSkill objects used by prompt rendering and sandbox installation.

*Call graph*: 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 458–467)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill index shown to the agent in prompts. It includes top-level deploy skills and child skills that explicitly asked to be indexed.

**Data flow**: It reads deploy skills in registry order → keeps skills with no parent, plus child skills whose indexed flag is true → returns tuples of name and description.

**Call relations**: Prompt-building code calls this for the {{skill_index}} slot and selection prompt. Member skills are deliberately excluded so saved member content cannot change the stable system prompt.

*Call graph*: called by 2 (_prompt_skill_index, prompt_index).


##### `SkillRegistry.merged_with`  (lines 469–490)

```
def merged_with(self, generated: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that includes generated deploy-controlled skills, such as skills created for the current runtime setup. Existing deploy names win if there is a collision.

**Data flow**: It copies the current deploy skill map → tries to append each generated skill, logging and skipping any duplicate name → copies member cards → removes member cards that now collide with deploy names, also logging → returns a new SkillRegistry with the merged data.

**Call relations**: Runtime setup can call this when adding generated skills to a base registry. It uses logging to record refused shadowing, meaning one skill trying to take another skill’s name.

*Call graph*: 2 external calls (__init__, log).


##### `SkillRegistry.with_member`  (lines 492–511)

```
def with_member(self, cards: Sequence[SkillCard], materialize: SkillMaterializer) -> 'SkillRegistry'
```

**Purpose**: Creates a registry view that includes the bound agent’s saved member skills. Member skills may not replace deploy skills with the same name.

**Data flow**: It receives member SkillCards and a materializer function → filters out cards whose names collide with deploy skills, logging those refusals → stores the remaining member cards → returns a new SkillRegistry that can resolve both tiers.

**Call relations**: Turn setup uses this when attaching an agent’s saved skills to the stable deploy registry. Later closure can resolve the cards, and materialize can call the provided materializer to fetch full member skill files.

*Call graph*: 2 external calls (__init__, log).


##### `_loaded_tree`  (lines 517–535)

```
def _loaded_tree(loaded: Sequence[LoadedSkill]) -> str
```

**Purpose**: Creates a compact directory tree listing every file loaded for a group of skills. This tells the agent where files are available without pasting the files’ contents.

**Data flow**: It receives LoadedSkill objects → gathers every skill file path under its skill name → sorts paths → emits directories once and files as indented lines under $UFO_HOME/skills → returns the tree as text.

**Call relations**: loaded_context calls this after building instruction blocks. It is shared for the whole load closure so common prefixes are not repeated again and again.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 538–551)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the full text that one skill load contributes to the model’s context. It includes new instruction bodies, a note for already-present skills, and a file tree.

**Data flow**: It receives loaded skills and a set of skill names already in context → creates prompt bodies only for skills not already present → collects repeated names into one short note → appends the loaded file tree → returns the final markdown text.

**Call relations**: load_skill-style runtime flows and subagent preloading use this after materialize. It calls _loaded_tree to describe available files while avoiding duplicate instruction injection.

*Call graph*: calls 1 internal fn (_loaded_tree).


##### `_wire_skill`  (lines 554–561)

```
def _wire_skill(skill: RuntimeSkill) -> dict[str, object]
```

**Purpose**: Prepares one RuntimeSkill for transfer into the sandbox. It packages file contents in a safe, serializable form the sandbox API can accept.

**Data flow**: It receives a RuntimeSkill → gathers all files → checks and normalizes each file path under the skill root → base64-encodes each file’s bytes, which turns binary data into safe text → includes the skill content digest → returns a dictionary describing the skill.

**Call relations**: install_skill and load_skills call this before asking the sandbox to load user-provided or non-bundled skill files. It uses RuntimeSkill.all_files, root, and content_digest.

*Call graph*: calls 3 internal fn (all_files, content_digest, root); called by 2 (install_skill, load_skills); 2 external calls (urlsafe_b64encode, contained_relative).


##### `install_skill`  (lines 564–568)

```
async def install_skill(sandbox: Sandbox, skill: RuntimeSkill) -> None
```

**Purpose**: Installs one materialized skill into the sandbox’s $UFO_HOME/skills area. This is useful when a single skill must be made available to runtime code.

**Data flow**: It receives a Sandbox object and a RuntimeSkill → wires the skill into the sandbox load format → calls sandbox.load_skills with it as a user skill → checks that the sandbox returned a path for that skill → raises an error if the install did not appear to happen.

**Call relations**: It hands the actual filesystem placement to Sandbox.load_skills. _wire_skill prepares the data sent to that sandbox call.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


##### `load_skills`  (lines 571–578)

```
async def load_skills(sandbox: Sandbox, loaded: Sequence[LoadedSkill]) -> None
```

**Purpose**: Installs a whole resolved set of loaded skills into the sandbox. It separates bundled system skills from user-supplied skill files so the sandbox can load each efficiently and safely.

**Data flow**: It receives a Sandbox and LoadedSkill entries → builds a deploy map of bundled skill names to known digests → builds a user map for non-bundled skills using _wire_skill → calls sandbox.load_skills with both groups → verifies every requested skill got a root path back → raises an error if any are missing.

**Call relations**: After the registry has resolved and materialized skills, this function makes their files available inside the sandbox. It calls _wire_skill for skills that must be sent with file contents and delegates installation to Sandbox.load_skills.

*Call graph*: calls 2 internal fn (load_skills, _wire_skill).


### `core/src/ufo/runtime/skills/selection.py`

`domain_logic` · `per agent turn, while building the prompt/message`

Agents can have many saved skills, each like a small card with a name and description. The model needs to know these skills exist, but the prompt has limited room. This file is the rulebook for deciding where those cards go and how much detail to show.

If the saved-skill list is small, it is folded directly into the main system prompt, beside built-in skills. If it is too large, it is instead placed into a separate `<saved_skills>` block in the turn message. That block has its own size limit.

The file uses a ladder of choices. First, pinned skills are favored, because they are meant to stay visible. If the full catalog fits, every skill gets a full line with its description. If not, the file picks a small set of skills whose names or descriptions match the current query, gives those full descriptions, and still lists the remaining skills by name. If even that is too big, it drops lines from the end and adds a note saying how many were omitted and that `skill_search` can find them.

Everything here is pure calculation: no network, no disk, no hidden state. Given the same cards and query, it always produces the same visibility decision.

#### Function details

##### `_query_terms`  (lines 35–42)

```
def _query_terms(query: str) -> tuple[str, ...]
```

**Purpose**: Turns a user query into a clean list of searchable words. It removes punctuation, ignores very short words, lowercases the text in a language-safe way, and keeps only the first occurrence of each term.

**Data flow**: Input is a query string. The function looks only at the first allowed chunk of that string, splits it into word-like pieces, drops tiny pieces, removes duplicates while keeping order, and returns the remaining terms as a tuple.

**Call relations**: This is the shared preparation step for search-like matching. `lexical_score` uses it before scoring one card, and `select_top_k` uses it once before ranking many cards.

*Call graph*: called by 2 (lexical_score, select_top_k).


##### `_term_hits`  (lines 45–47)

```
def _term_hits(terms: Sequence[str], card: SkillCard) -> int
```

**Purpose**: Counts how many prepared query terms appear in a single skill card. It checks both the skill name and its description.

**Data flow**: Input is a sequence of already-cleaned terms and one `SkillCard`. The function combines the card name and description, lowercases them, counts how many terms appear anywhere in that combined text, and returns that count as an integer.

**Call relations**: `lexical_score` calls this after `_query_terms` has cleaned the query. It is the small scoring step that says how well one card matches the query words.

*Call graph*: called by 1 (lexical_score).


##### `lexical_score`  (lines 50–55)

```
def lexical_score(query: str, card: SkillCard) -> int
```

**Purpose**: Gives one skill card a simple relevance score for a query. The score is the number of distinct query words found in the card’s name or description.

**Data flow**: Input is a query string and one `SkillCard`. The function turns the query into terms with `_query_terms`, counts matches with `_term_hits`, and returns the match count.

**Call relations**: This is the public single-card scoring helper. Internally it delegates query cleanup to `_query_terms` and card matching to `_term_hits`, matching the same idea used by the bulk selector.

*Call graph*: calls 2 internal fn (_query_terms, _term_hits).


##### `select_top_k`  (lines 58–66)

```
def select_top_k(query: str, cards: Sequence[SkillCard]) -> tuple[SkillCard, ...]
```

**Purpose**: Chooses the best-matching unpinned skill cards for the current query. It is used when there are too many saved skills to show every description.

**Data flow**: Input is a query string and a sequence of cards. The function cleans the query once, removes pinned cards from consideration, sorts the remaining cards by how many query terms they match, keeps original order for ties, and returns up to the configured top count.

**Call relations**: `member_visibility` calls this only after it has decided the full catalog is too large. The selected cards are the ones that keep full descriptions in the saved-skills block.

*Call graph*: calls 1 internal fn (_query_terms); called by 1 (member_visibility).


##### `skill_line`  (lines 69–71)

```
def skill_line(card: SkillCard) -> str
```

**Purpose**: Formats one skill card as a prompt-friendly line. It includes the skill name and description, but cuts the line off at a fixed length so one long description cannot take over the prompt.

**Data flow**: Input is one `SkillCard`. The function builds text like `- name: description`, trims it to the maximum allowed line length, and returns that string.

**Call relations**: This is the common card-to-text formatter. It is used by prompt folding, catalog size checks, prompt index creation, and full member visibility rendering so all those paths measure and show cards consistently.

*Call graph*: called by 4 (catalog_fits, folds_into_prompt, member_visibility, prompt_index).


##### `folds_into_prompt`  (lines 74–78)

```
def folds_into_prompt(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Decides whether the member’s saved skills are small enough to go directly into the main system prompt. This keeps small skill collections simple and avoids a separate saved-skills block when it is not needed.

**Data flow**: Input is a sequence of cards. The function formats each card with `skill_line`, measures the combined text using `_joined_size`, compares it with the prompt-fold budget, and returns `true` or `false`.

**Call relations**: `prompt_index` calls this before deciding whether to mix member skills into the normal prompt index or leave them out for later rendering in the turn message.

*Call graph*: calls 2 internal fn (_joined_size, skill_line); called by 1 (prompt_index).


##### `prompt_index`  (lines 81–93)

```
def prompt_index(registry: SkillRegistry) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the skill list that will be inserted into the system prompt for a turn. It always includes deployed skills, and it also includes member-saved skills when that member list is small enough.

**Data flow**: Input is a `SkillRegistry`, which contains deployed skills and member-saved cards. The function reads the member cards, checks whether they fold into the prompt, and returns either only the registry’s deploy index or that index plus formatted member card entries.

**Call relations**: This sits at the prompt-building boundary. It asks `SkillRegistry.index()` for the deploy-side skills, uses `folds_into_prompt` to decide whether member cards join them, and uses `skill_line` so the text matches the size calculation.

*Call graph*: calls 3 internal fn (index, folds_into_prompt, skill_line).


##### `catalog_fits`  (lines 96–99)

```
def catalog_fits(cards: Sequence[SkillCard]) -> bool
```

**Purpose**: Checks whether all saved skills can fit in the separate saved-skills block with full descriptions. It answers the question: can we show the whole catalog without ranking or trimming?

**Data flow**: Input is a sequence of cards. The function formats each card with `skill_line`, measures the full wrapped block with `_block_size`, compares it with the member-block budget, and returns `true` or `false`.

**Call relations**: This is a standalone version of one decision also made inside `member_visibility`. It uses the same formatter and size calculation so callers get the same answer the rendering path would use.

*Call graph*: calls 2 internal fn (_block_size, skill_line).


##### `member_visibility`  (lines 113–146)

```
def member_visibility(query: str, cards: Sequence[SkillCard]) -> MemberVisibility
```

**Purpose**: Makes the complete saved-skill visibility decision for one turn. It says whether skills fold into the prompt, whether the full catalog fits the separate block, and what block text should be shown if needed.

**Data flow**: Inputs are the current query and the member skill cards. The function formats each card once, measures whether the cards fit in the system prompt and in the saved-skills block, then either returns no block, a full-catalog block, or a trimmed block with pinned skills, top query matches, bare skill names, and possibly a dropped-count note. The output is a `MemberVisibility` value containing the decisions and final text.

**Call relations**: `member_block` calls this when it only needs the rendered text. Inside, this function uses `_joined_size` and `_block_size` for budget checks, `select_top_k` for query-based choices when the catalog is too large, `_render` to wrap the final lines, and `skill_line` to keep formatting consistent.

*Call graph*: calls 5 internal fn (_block_size, _joined_size, _render, select_top_k, skill_line); called by 1 (member_block); 1 external calls (__init__).


##### `member_block`  (lines 149–157)

```
def member_block(query: str, cards: Sequence[SkillCard]) -> str
```

**Purpose**: Returns just the `<saved_skills>` text block for a turn. If the saved skills are small enough to appear in the system prompt, this returns an empty string.

**Data flow**: Inputs are the current query and the member skill cards. The function asks `member_visibility` for the full decision, takes the `block` field from that result, and returns it.

**Call relations**: This is the simple convenience entry for callers that do not need the extra visibility flags. It delegates the real decision-making to `member_visibility`.

*Call graph*: calls 1 internal fn (member_visibility).


##### `_joined_size`  (lines 163–164)

```
def _joined_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how many characters a list of lines would take if joined with newline characters. This is used for budget checks before text is actually placed into a prompt.

**Data flow**: Input is a sequence of strings. The function adds each line length plus the newline space between lines, handles the empty case as zero, and returns the total character count.

**Call relations**: `folds_into_prompt`, `_block_size`, and `member_visibility` use this helper so they all count multi-line text the same way.

*Call graph*: called by 3 (_block_size, folds_into_prompt, member_visibility).


##### `_block_size`  (lines 167–168)

```
def _block_size(lines: Sequence[str]) -> int
```

**Purpose**: Measures how large a saved-skills block would be after adding its opening and closing tags. It prevents code from forgetting that the wrapper text also consumes prompt space.

**Data flow**: Input is a sequence of already-rendered lines. The function measures the joined body with `_joined_size`, adds the fixed wrapper size and the extra newline when there are lines, and returns the total character count.

**Call relations**: `catalog_fits` and `member_visibility` call this when deciding whether the whole catalog can fit inside the saved-skills block.

*Call graph*: calls 1 internal fn (_joined_size); called by 2 (catalog_fits, member_visibility).


##### `_render`  (lines 171–172)

```
def _render(lines: tuple[str, ...]) -> str
```

**Purpose**: Builds the final saved-skills block text from prepared lines. It wraps the lines between `<saved_skills>` and `</saved_skills>` tags.

**Data flow**: Input is a tuple of text lines. The function places the opening tag first, then the lines, then the closing tag, joins everything with newlines, and returns the finished string.

**Call relations**: `member_visibility` calls this at the end of the rendering path, after it has already decided which lines fit and in what order.

*Call graph*: called by 1 (member_visibility).


### `core/src/ufo/harness/models/catalog_skill.py`

`domain_logic` · `startup`

This file solves a simple but important problem: people need a trustworthy list of available models, but hand-written lists go stale. Instead of keeping a separate document, this file turns the live model registry into a readable skill. The registry is the system’s source of truth for model facts such as provider, price, context window, knowledge cutoff, reasoning support, and API surface.

At startup, the system can call `model_catalog_skill` with the current `ModelRegistry`. The function sorts all registered model records by model id, formats each one as a row in a Markdown table, and wraps the result in a `RuntimeSkill`. A runtime skill is a piece of instruction text the system can load and show or use during operation.

The small helper `_per_mtok` formats prices. Prices are stored internally as tiny units called micro-dollars, so this helper turns them into ordinary dollar amounts per million tokens. A token is a small chunk of text used for AI model billing and limits.

The key idea is “one source of truth.” Like printing a restaurant menu directly from the kitchen’s inventory system, the catalog cannot accidentally advertise dishes the kitchen no longer serves.

#### Function details

##### `_per_mtok`  (lines 18–19)

```
def _per_mtok(micro_usd_per_mtok: int) -> str
```

**Purpose**: This helper turns an internal price number into a human-readable dollar string. It is used so the model catalog shows prices in a format people can quickly understand.

**Data flow**: It receives a price stored as micro-dollars per million tokens. It divides that number by the constant that represents one US dollar in micro-dollars, formats the result with two decimal places, and returns a string such as `$1.25`.

**Call relations**: When `model_catalog_skill` is building each table row, it calls `_per_mtok` for both the input-token price and the output-token price. `_per_mtok` does only the price display conversion, then hands the formatted text back so the catalog row can include it.

*Call graph*: called by 1 (model_catalog_skill).


##### `model_catalog_skill`  (lines 22–50)

```
def model_catalog_skill(registry: ModelRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the complete model-catalog skill from the current model registry. Someone would use it at boot time to give the runtime a built-in, accurate guide to the models this deployment can run.

**Data flow**: It receives a `ModelRegistry`, which contains the registered model specifications. It reads those specifications, sorts them by model id, turns each one into a Markdown table row, adds explanatory text and skill metadata, and returns a `RuntimeSkill` containing the finished catalog as instructions and raw Markdown.

**Call relations**: This is the main builder in the file. As it formats the catalog, it calls `_per_mtok` to make prices readable. At the end, it creates a `RuntimeSkill`, handing over the skill name, description, rendered instructions, and raw Markdown so the runtime can load the catalog like any other skill.

*Call graph*: calls 1 internal fn (_per_mtok); 1 external calls (__init__).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and cross-turn persistence`

A “skill” here is a small bundle of files, including the main skill instructions, that can be used by agents in a workspace. This file is the workspace’s skill cabinet: each saved skill gets one database row, keyed by workspace and name, and the file contents are stored as base64 text so they can fit safely in a database column.

The important job is not just storage. The store protects shared work. Each skill has a generation, which is like a ticket number printed when you read the skill. To edit it, you must bring back the same ticket. If someone else saved or deleted the skill first, your ticket is stale and the save is refused instead of silently overwriting their work.

The file also keeps user skills from impersonating built-in skills, rejects unsafe names, enforces caps on total saved skills and pinned skills, and keeps routing information such as description, dependencies, agent targets, and pin state alongside the saved content. Listing paths are tolerant of some bad old rows, so one broken skill does not hide the rest. Direct reads by name are stricter, because silently pretending a corrupt skill is missing would be misleading.

Deleting a skill also cleans up its search index data when an index service is available, taking care to leave recoverable states if a crash happens partway through.

#### Function details

##### `_save_lock_key`  (lines 54–56)

```
def _save_lock_key(workspace_id: UUID) -> int
```

**Purpose**: This helper turns a workspace ID into a stable number that PostgreSQL can use as a per-workspace lock key. The lock makes concurrent saves in the same workspace line up instead of racing each other.

**Data flow**: It receives a workspace UUID, converts it to text, hashes that text with SHA-256, takes the first eight bytes of the hash, and turns those bytes into a signed integer. The result is not meant to be secret; it is just a repeatable lock number for that workspace.

**Call relations**: UserSkillStore.save calls this before writing. The returned key is handed to PostgreSQL’s advisory transaction lock so that all saves for the same workspace pass through the same narrow doorway one at a time.

*Call graph*: called by 1 (save); 1 external calls (sha256).


##### `UserSkillStore.save`  (lines 123–238)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str], pinned: bool=False, generation: UUID | None=None) -> RuntimeSkill
```

**Purpose**: This saves a new or edited workspace skill after checking that it is safe, valid, and allowed. It prevents accidental overwrites by requiring the caller to provide the generation they previously read when editing an existing skill.

**Data flow**: It receives a skill name, a map of file paths to file bytes, the names already owned by built-in or pack skills, a pin setting, and optionally the generation seen by the caller. It validates the name, parses the files to prove they form a usable skill, encodes the files into JSON-safe base64 text, computes a content digest, and writes both the content and routing details to the database. Before inserting or updating, it checks whether the row already exists, whether the supplied generation still matches, whether the name collides with a built-in skill, and whether workspace limits would be exceeded. It returns the parsed RuntimeSkill if the save succeeds, and changes the database row by creating it or replacing its content and generation.

**Call relations**: This is the main write path for the store. It calls _save_lock_key to serialize concurrent PostgreSQL saves, _count when adding a new skill to enforce the total cap, and _pinned_count when pinning to enforce the pinned cap. It raises specific errors such as InvalidSkillName, StaleSkillGeneration, SkillCollidesWithCoreSkill, TooManyUserSkills, and PinnedSkillLimit so callers can explain the refusal clearly.

*Call graph*: calls 3 internal fn (_count, _pinned_count, _save_lock_key); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, b64encode, sha256, dumps, cast (+6 more)).


##### `UserSkillStore.cards`  (lines 240–281)

```
async def cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: This returns lightweight routing cards for all saved skills in the current workspace. Agents can use these cards to know what skills exist and when they may apply, without loading every skill’s full file bundle.

**Data flow**: It reads the current workspace ID, queries the database for each skill’s name, description, dependencies, target agents, and pin state, then turns those rows into SkillCard objects. It skips rows with an empty description and logs a warning, because those rows cannot route usefully. The result is a tuple of SkillCard objects ordered by skill name.

**Call relations**: This is a read path used when the system needs the catalog of available workspace skills. Unlike materialize or files, it does not decode stored file content, so a corrupt bundle can still leave a usable card unless the description is missing.

*Call graph*: 4 external calls (__init__, loads, select, agent_current).


##### `UserSkillStore.listing`  (lines 283–307)

```
async def listing(self) -> tuple[SkillListing, ...]
```

**Purpose**: This returns the simple list shown to users or tools when they ask what workspace skills are saved. It includes only the name, description, and whether the skill is pinned.

**Data flow**: It reads the current workspace ID, fetches each matching skill row’s name, description, and pin state from the database, filters out rows with no description, and wraps the rest as SkillListing objects. The output is a tuple ordered by skill name.

**Call relations**: This is the user-facing summary path. It follows the same empty-description skip rule as UserSkillStore.cards, so the visible list and routing catalog stay consistent.

*Call graph*: 3 external calls (__init__, select, agent_current).


##### `UserSkillStore.record`  (lines 309–340)

```
async def record(self, name: str) -> SkillRecord | None
```

**Purpose**: This retrieves one saved skill in full detail for viewing or editing. It includes the original files, description, generation ticket, pin state, and timestamps.

**Data flow**: It receives a skill name and reads the current workspace ID. It queries the database for that exact workspace and skill name. If no row exists, it returns None. If a row exists, it validates the stored JSON, decodes each base64 file back into bytes, and returns a SkillRecord containing the files and metadata. If the stored content is corrupt, validation or decoding raises an error instead of hiding the problem.

**Call relations**: This is the detailed read path that pairs with UserSkillStore.save. A caller can read a SkillRecord, take its generation, and later pass that generation back to save to make a safe edit.

*Call graph*: 4 external calls (__init__, b64decode, select, agent_current).


##### `UserSkillStore.materialize`  (lines 342–349)

```
async def materialize(self, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one named saved skill as a runtime-ready skill object. It is used when the system needs to actually use the skill, not just list it.

**Data flow**: It receives a skill name, asks UserSkillStore.files for the stored file bytes, and returns None if the skill is not saved. If files are found, it parses those files into a RuntimeSkill, which checks the skill content and extracts its usable metadata and instructions.

**Call relations**: This function builds on UserSkillStore.files to avoid duplicating database and decoding work. It then hands the decoded bundle to parse_skill_content, making it the direct path from stored bytes to usable skill.

*Call graph*: calls 1 internal fn (files); 1 external calls (parse_skill_content).


##### `UserSkillStore.materialize_all`  (lines 351–379)

```
async def materialize_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads every saved skill in the current workspace as runtime-ready skill objects. It is designed to be tolerant: one broken saved bundle is logged and skipped instead of preventing all other skills from loading.

**Data flow**: It reads the current workspace ID, fetches every saved skill name and stored content from the database, then processes each row. For each one, it validates the stored JSON, decodes base64 file contents back into bytes, and parses the files into a RuntimeSkill. If any row fails during validation, decoding, or parsing, it logs the error and continues with the next row. The result is a tuple of successfully loaded RuntimeSkill objects.

**Call relations**: This is the bulk loading path. It uses the same parsing step as UserSkillStore.materialize, but it reads all rows at once and deliberately keeps going after individual failures so a single bad skill does not disable the whole workspace skill set.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 381–396)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: This returns the raw files for one saved workspace skill. It is useful when another part of the system wants the stored bundle but does not yet need it parsed as a runtime skill.

**Data flow**: It receives a skill name, reads the current workspace ID, and fetches the stored content column for that skill. If no row exists, it returns None. If found, it validates the stored JSON and decodes each base64 string back into file bytes, returning a dictionary from relative file path to bytes.

**Call relations**: UserSkillStore.materialize calls this first, then parses the returned files. This function is the small reusable doorway from database storage format back to normal file bytes.

*Call graph*: called by 1 (materialize); 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 398–420)

```
async def delete(self, name: str) -> None
```

**Purpose**: This deletes one saved skill from the current workspace and, when search indexing is available, removes that skill’s indexed data too. It is careful about the order of operations so a crash is easier to recover from.

**Data flow**: It receives a skill name and reads the current workspace ID. If an index service exists, it first marks the skill’s indexed digest as missing in the database, then asks the index to delete the scope for that skill. After that, it deletes the skill row from the database. The database loses the saved skill, and the search index is pruned when possible.

**Call relations**: This is the removal path for saved skills. It uses IndexScope to identify the indexed material belonging to the skill, and it performs the index-related cleanup before the final database delete so a partial failure leaves a state that later indexing work can repair.

*Call graph*: 4 external calls (__init__, delete, update, agent_current).


##### `UserSkillStore._count`  (lines 422–430)

```
async def _count(self, connection: AsyncConnection) -> int
```

**Purpose**: This counts how many user-created skills are saved in the current workspace. It is used to stop a workspace from growing beyond the configured maximum number of saved skills.

**Data flow**: It receives an already-open database connection, reads the current workspace ID, runs a count query over the user_skill table for that workspace, and returns the count as an integer. It does not change any data.

**Call relations**: UserSkillStore.save calls this only when creating a new skill. Because it runs inside the same transaction as the save, the count check and the insert are kept together.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


##### `UserSkillStore._pinned_count`  (lines 432–444)

```
async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int
```

**Purpose**: This counts how many other skills are pinned in the current workspace. It is used to enforce the limit on pinned user-created skills.

**Data flow**: It receives an open database connection and the name of a skill to exclude from the count. It reads the current workspace ID, counts rows in that workspace where pinned is true and the name is not the excluded name, and returns that number. It does not change any data.

**Call relations**: UserSkillStore.save calls this when a save would pin a skill that was not already pinned. Excluding the skill being saved lets an already-pinned skill be re-saved without counting itself as a new pin.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The Community tab needs to show skills from skills.sh without overwhelming either the user or the outside service. This file does that job. Think of it like a careful librarian: it asks the outside catalog for a short list, keeps recent answers on the desk for reuse, and only goes back to the shelves when needed.

There are two main public reads. `CommunitySkills.listing` returns up to 24 skills, either from the directory’s leaderboard when there is no search text, or from the directory’s search API when there is. Each skill row includes only the name, source repository, and install count, because the public listing does not safely provide descriptions. `CommunitySkills.fetch` downloads one selected skill’s `SKILL.md` file and reads its front matter, which is the metadata block at the top of the Markdown file, to get the name and description.

The file is deliberately defensive. It limits how much data it will read, turns rate-limit and server errors into user-friendly messages, ignores entries with missing or suspicious source names, and caches results. Listings are cached for 15 minutes; fetched documents stay cached for the life of the process. Without this file, the portal would either have no Community skill browser or would need to expose users directly to brittle outside-service failures.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: This turns an HTTP failure code from skills.sh into a clear `CommunityUnavailable` error. It gives a special, human-readable explanation for rate limiting, instead of showing a bare number.

**Data flow**: It receives a numeric response code. If the code means “too many requests,” it creates an error explaining that the directory limit has been reached; otherwise it creates an error saying which code the directory returned. The result is an exception object ready to be raised by the caller.

**Call relations**: The low-level download helper and the search helper call this when skills.sh does not answer with success. It centralizes the wording so listing and document fetch failures are reported consistently.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: This returns the list of community skills shown in the portal. With no search text it returns the popular leaderboard; with search text it asks the directory for matching skills.

**Data flow**: It receives a query string and first checks the in-memory listing cache. If a fresh cached list exists, it returns that immediately. Otherwise it opens an HTTP client, gets either the popular list or the search results, trims the list to the display limit, stores it with the current time, and returns it.

**Call relations**: This is one of the main entry points used by the Community narrowing in the web portal. It calls `_client` to make a configured web client, then calls `_popular` or `_search` depending on whether the user typed a query.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: This fetches the full document for one selected community skill, so the Install view can show its description and instructions. It returns nothing if the document is missing or not in the expected format.

**Data flow**: It receives a source repository such as `owner/repo` and a skill name. It checks the document cache using those values, downloads the directory’s JSON response if needed, looks for the `SKILL.md` file inside it, parses that Markdown file, stores the parsed result or `None` in the cache, and returns the result.

**Call relations**: This is the second main public read provided by the file. It uses `_client` and `_body` to download from skills.sh, then hands the `SKILL.md` contents to `_parse` so the raw document becomes a `CommunityDocument` the portal can display.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used for requests to the skill directory. It also applies the timeout, redirect behavior, and optional test transport.

**Data flow**: It receives a timeout value. It builds and returns an asynchronous HTTP client configured to stop waiting after that timeout, follow redirects, and use the injected transport if tests supplied one.

**Call relations**: `listing` and `fetch` call this before making outside web requests. This keeps the setup for HTTP access in one place, so tests can swap in fake network behavior without changing the higher-level logic.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: This reads the skills.sh leaderboard page and turns it into a sorted list of popular community skills. It is used when the user opens the Community list without searching.

**Data flow**: It downloads the leaderboard page body, scans the returned text for embedded JSON-like skill entries, parses each usable entry, filters out invalid ones, removes duplicates by source and name, and returns the skills sorted by install count from highest to lowest. If no usable listing is found, it raises a clear unavailable error.

**Call relations**: `listing` calls this when the query is empty. It relies on `_body` for the protected download and `_entry` to turn each raw directory entry into a clean `CommunitySkill` object.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: This asks the public skills.sh search endpoint for skills matching the user’s query. It returns clean skill rows sorted by popularity.

**Data flow**: It receives an HTTP client and the search text. It sends a GET request with the query and limit, checks that the response succeeded, reads the JSON list of skills, converts each raw entry into a `CommunitySkill` when possible, drops invalid entries, sorts the rest by install count, and returns them.

**Call relations**: `listing` calls this when the user has typed a search query. If the directory rejects the request, it calls `_refusal`; otherwise it uses `_entry` to normalize each result.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: This converts one raw skill record from skills.sh into the portal’s small, trusted `CommunitySkill` shape. It rejects records that are not usable or have an invalid source repository name.

**Data flow**: It receives an arbitrary object from parsed directory data. If the object is not a dictionary, or if it lacks a skill name or a source shaped like `owner/repo`, it returns `None`. Otherwise it creates and returns a `CommunitySkill` with name, source, and install count.

**Call relations**: Both `_popular` and `_search` call this while cleaning directory results. It acts as the gatekeeper between messy outside data and the portal’s predictable internal skill rows.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: This safely downloads a response body from skills.sh. It prevents runaway reads by stopping if the directory sends more data than this file is willing to accept.

**Data flow**: It receives an HTTP client, a URL, a maximum byte count, and optional headers. It streams the response in chunks, checks the status code, counts the bytes as they arrive, raises an error if the response is too large, and finally returns the full downloaded bytes.

**Call relations**: `_popular` uses this to read the leaderboard page, and `fetch` uses it to download a skill document response. When the server returns an error status, it hands the status code to `_refusal` so the user-facing message stays consistent.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: This reads a downloaded `SKILL.md` Markdown document and extracts the information the Install screen needs. It only accepts documents that have valid front matter with both a name and description.

**Data flow**: It receives the full Markdown document as text. It looks for a metadata block at the top, parses that block as YAML, checks for required `name` and `description` fields, and returns a `CommunityDocument` containing those fields, the remaining instructions, and the original document. If anything is missing or unreadable, it returns `None`.

**Call relations**: `fetch` calls this after it finds the `SKILL.md` file in the directory’s download response. This is the final step that turns a raw community file into display-ready detail for the portal.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### Core subagent execution
These files provide the default subagent profile and the runtime machinery for spawning, validating, waiting on, and returning results from child agents.

### `core/src/ufo/runtime/profiles.py`

`config` · `subagent setup and task delegation`

This file is the system’s fallback recipe for creating a focused child agent. A subagent is like asking a coworker to take one clearly bounded task while the main agent keeps overall control. Without this file, the core runtime would not have a default helper profile to use when a task is delegated without naming a more specific extension-provided profile.

The profile is called `general_purpose`. It is meant to work in the same `/workspace` as the parent agent, solve as much as it can independently, and report back concisely. Its prompt tells it not to ask the user for clarification, not to keep repeating a blocked action, and to load relevant skills before doing specialized work. It also reminds the subagent that formal document outputs should be real Office files, such as `.docx`, `.pptx`, or `.xlsx`, rather than Markdown.

The file also defines the tools this helper is allowed to use. It can read, write, edit, search files, run shell commands, load skills, and share files. It may also use optional extension tools, such as web search or spreadsheet tools, if those extensions are installed. Important powers are deliberately left out: this subagent cannot ask the user, spawn more subagents, message or cancel sibling agents, or grant account access. In short, it is designed to be useful but contained.


### `core/src/ufo/runtime/subagents.py`

`orchestration` · `request handling`

This file is the control room for “subagents”: helper agents that a running turn can ask to do work. Think of a parent agent asking a specialist coworker to investigate something. The coworker may answer right away, or may keep working in the background and send the result back later.

A spawn target can be either a named profile, which is a predefined prompt plus tool list and input/output contract, or a workspace agent, which is a real agent row owned in the workspace. The file first resolves the name, checks that the caller is allowed to use it, validates the payload against the target’s expected input, and writes a new child conversation and turn into the database. It then places that turn on the express queue so it starts even when normal capacity is tight.

For foreground spawns, it waits for the child to finish and checks that the final answer matches the declared output contract. For background spawns, it returns the child turn id immediately and later posts a structured result back into the parent conversation. It also supports cancelling, sending follow-up messages to a child, avoiding duplicate spawns during crash recovery, and detaching a foreground wait if a new member message arrives. Without this file, child-agent work would be unsafe, hard to resume, easy to duplicate, and results could reach the parent without clear validation or ownership checks.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 145–149)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the configured subagent profiles do not reuse the same name. This matters because a spawn name must point to one clear target.

**Data flow**: It reads the registry’s tuple of profiles, collects their names, and looks for repeats. If every name is unique, nothing changes; if any name appears more than once, it raises an error before the registry can be used.

**Call relations**: This runs automatically when a SubagentRegistry is created. It protects later lookups, such as get and find, from having to guess which duplicate profile was meant.


##### `SubagentRegistry.get`  (lines 151–157)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Returns a profile by name, and gives a clear error if the name is not registered. It is the strict lookup path for code that requires the profile to exist.

**Data flow**: It receives a profile name, asks find for the matching profile, and either returns that profile or raises an UnknownSubagentProfile error that includes the available names.

**Call relations**: It builds on SubagentRegistry.find for the actual search. The queue profile resolver calls it when setting up a turn that names a subagent profile, so missing profiles fail loudly instead of turning into confusing runtime behavior.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 159–160)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Looks for a profile by name and returns nothing if it is absent. This is useful when absence is allowed and the caller wants to decide what to do next.

**Data flow**: It receives a name, scans the stored profiles in order, and returns the first profile with that name. If none match, it returns null.

**Call relations**: SubagentRegistry.get uses this for strict lookup. Other parts of this file also use the same idea when they need to treat missing profiles as untrusted or invalid rather than immediately crash.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 174–182)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Figures out whether a spawn target has its own pinned model. A model is the AI engine choice; this helper tells billing and admission which model should be considered for profile targets.

**Data flow**: It receives either a subagent profile or an agent target. For a profile, it returns the model named by that profile; for a workspace agent target, it returns null because the agent’s own row is used elsewhere.

**Call relations**: Subagents.spawn calls this while admitting a child turn. It helps pass the correct model information into balance checking without mixing up profile children and workspace-agent children.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 185–221)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the full instruction text that a profile-based child agent receives. It combines the profile’s prompt, a skill list, optional preloaded skill instructions, shared output rules, and the requirement to finish with a structured answer.

**Data flow**: It takes a profile, a list of available skills, and optional loaded skills. It fills the skill-index slot, checks for any unfilled prompt variables, optionally appends preloaded skill text if it is not too large, and returns one complete system prompt string.

**Call relations**: This helper is used by the subagent setup path outside this file when preparing a profile child to run. It calls the prompt-rendering and skill-loading helpers so the final prompt is complete before any model sees it.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 248–257)

```
def authorize(self, authority: ExecutionAuthority) -> 'Subagents'
```

**Purpose**: Creates a copy of the Subagents controller using the authority of the current tool call. Authority means who the action is being done for, such as a member or the workspace.

**Data flow**: It receives an ExecutionAuthority. If it is member-specific, it returns a copy bound to that member authority; otherwise it returns a copy using the parent turn’s authority.

**Call relations**: Tool code can call this before spawning so all later checks in spawn, message, cancel, and result use the right requester. It uses dataclasses.replace to keep the object immutable while changing only the authority field.

*Call graph*: 1 external calls (replace).


##### `Subagents.spawn`  (lines 259–413)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False, model: str | N
```

**Purpose**: Starts a child turn, either as a foreground helper whose answer is returned now or as a background helper whose result arrives later. This is the main “spawn a subagent” operation.

**Data flow**: It receives a target name, payload, and options such as background mode, deduplication key, delivery behavior, and model choice. It builds or inherits runtime settings, resolves the target, validates input, admits the child rows in the database, enqueues the workflow, optionally waits for the terminal result, validates the output, and returns a SpawnResult.

**Call relations**: This is the central caller of most helpers in the file: it uses _child_runtime_config, _existing_agent_spawn, _resolve, _validated, _admit, _enqueue, _await_terminal, _await_terminal_or_detach, and _target_model. If waiting fails, it cancels the child through the shared cancellation helper so the system does not leave abandoned work behind.

*Call graph*: calls 11 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _child_runtime_config, _enqueue, _existing_agent_spawn, _resolve, _validated, _target_model, own_account (+1 more)); 11 external calls (__init__, __init__, sha256, dumps, cancel_one_turn, input_contract, output_contract, ws_current, turn_id_for, uuid4 (+1 more)).


##### `Subagents.result`  (lines 415–452)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the finished result of a child that this conversation spawned. It is for safely retrieving a child’s terminal state and validated output after the child has already ended.

**Data flow**: It receives a child turn id, checks that the turn belongs to this spawning conversation, loads the child’s conversation, agent, and terminal data from the database, chooses the correct output contract, validates the terminal text when possible, and returns a SpawnResult.

**Call relations**: It relies on _require_child to enforce ownership, _agent_output_schema for agent-child contracts, and _untrusted_output to mark whether the result should be treated carefully. It is the read-side companion to background delivery and foreground spawn waiting.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, workspace_tx, output_contract).


##### `Subagents.wait`  (lines 454–474)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits until several child turns finish and reports their final status and text. It is meant for tool flows that explicitly need to block for child completion.

**Data flow**: It receives a tuple of child turn ids, verifies that each is a valid child, waits for each terminal result, and returns a tuple of SubagentStatus objects containing status, text, and trust information.

**Call relations**: It calls _require_child before waiting so a caller cannot wait on someone else’s child. It then uses _await_terminal for completion and _untrusted_output to label the returned text.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 476–493)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a child turn that this parent conversation spawned. It gives the caller a final status view after the cancellation attempt.

**Data flow**: It receives a child turn id, verifies that it is allowed, asks the shared cancellation system to cancel the turn, reloads the turn’s status and terminal text from the database, and returns a SubagentStatus.

**Call relations**: It starts with _require_child to enforce the parent-child and member boundary. It then hands off the actual cancellation to cancel_one_turn, the shared primitive used elsewhere in the runtime.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents.message`  (lines 495–557)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to an existing child spawn. This lets the parent answer a child’s question or correct it while preserving the child’s own conversation and contract.

**Data flow**: It receives a child turn id, message text, and deduplication key. It verifies the child, confirms the profile still exists when needed, checks billing for the continued work, invokes the child conversation with the message, reads the admitted turn’s status, and returns that status.

**Call relations**: It uses _require_child for access control, _profile_model and _require_balance for billing checks, and the injected TurnInvoker to admit the follow-up message. It is the continuation path for spawned work after the first child turn.

*Call graph*: calls 3 internal fn (_profile_model, _require_balance, _require_child); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `Subagents._resolve`  (lines 559–582)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns the user’s target string into either a subagent profile or a workspace agent. It also catches unclear names, such as a bare name that matches both kinds.

**Data flow**: It receives a target string, checks for prefixes like profile: or agent:, searches the profile registry and workspace agent table, and returns the matching target object. If nothing matches or the name is ambiguous, it raises an error with helpful available options.

**Call relations**: Subagents.spawn calls this before admitting any new child. It delegates profile listing to _profile_names and agent lookup/listing to _agent_target and _agent_names.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._validated`  (lines 584–597)

```
def _validated(self, target: str, contract: Contract, payload: dict[str, Any]) -> str
```

**Purpose**: Checks that the payload sent to a spawn matches the target’s input contract. A contract is the declared shape of acceptable input.

**Data flow**: It receives the target name, contract, and payload dictionary. It validates the payload, converts the clean version to JSON for storage, or raises SpawnPayloadRejected with readable field-level faults and expected keys.

**Call relations**: Subagents.spawn uses this after resolving the target and before admission. It turns low-level validation errors into errors the calling model or user can repair.

*Call graph*: calls 1 internal fn (__init__); called by 1 (spawn); 2 external calls (model_validate, payload_keys).


##### `Subagents._existing_agent_spawn`  (lines 599–638)

```
async def _existing_agent_spawn(self, turn_id: UUID, target: str) -> tuple[AgentTarget, str] | None
```

**Purpose**: Reconnects a deduplicated agent spawn to an already-admitted child turn when a spawning step is replayed. This prevents crash recovery from creating a duplicate child.

**Data flow**: It receives the deterministic child turn id and requested target name. It loads any existing turn and agent row, verifies it belongs to the same parent and same member request, and returns the original AgentTarget plus stored inbound JSON, or null if no matching agent spawn exists.

**Call relations**: Subagents.spawn calls this when a deduplication key was supplied. If it returns a replay target, spawn skips fresh resolution and input validation and reuses the already accepted request.

*Call graph*: called by 1 (spawn); 4 external calls (__init__, select, workspace_tx, authority_member_id).


##### `Subagents._profile_names`  (lines 640–641)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the registered subagent profile names in sorted order. This is mainly used to make errors helpful.

**Data flow**: It reads the registry’s profiles, extracts each name, sorts them, and returns them as a tuple.

**Call relations**: _resolve uses it when reporting unknown targets. _require_agent_spawn also uses it when an agent target has disappeared and the error needs to show what can still be spawned.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `Subagents._agent_names`  (lines 643–655)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Lists the active workspace agent names that can be considered as spawn targets. Archived agents are left out.

**Data flow**: It opens a workspace database transaction, selects non-archived agents in the parent’s workspace ordered by name, and returns their names as a tuple.

**Call relations**: _resolve calls this when it needs to explain an unknown target. It complements _profile_names so the caller can see both spawn namespaces.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 657–680)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up one active workspace agent by name and packages the facts needed to spawn it. Those facts include its id and input/output schemas.

**Data flow**: It receives an agent name, queries the workspace database for a non-archived agent with that name in the parent workspace, and returns an AgentTarget if found. If no row matches, it returns null.

**Call relations**: _resolve calls this while deciding whether a target string names a workspace agent. The returned AgentTarget is later used by spawn and _admit to admit the child under that agent.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 682–688)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Loads the output schema for a workspace agent by id. The output schema defines what a finished agent child is expected to return.

**Data flow**: It receives an agent id, queries the agent table, and returns that agent’s output schema, which may be null if the agent uses the default contract.

**Call relations**: Subagents.result calls this when reading the result of an agent child, because agent children do not use a subagent profile contract.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._untrusted_output`  (lines 690–699)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child’s output should be treated as untrusted content. Untrusted means the parent should not treat the text as safe instructions just because it came from a child.

**Data flow**: It receives either a profile name or null for an agent child. Agent children are always marked untrusted; profile children are marked according to their registered profile, and missing profiles are treated as untrusted.

**Call relations**: Subagents.result and Subagents.wait call this when returning child information. It keeps safety conservative if the profile registry changed after the child was created.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 701–737)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Verifies that a turn id belongs to a child spawned by this conversation and by the same requester. It prevents one member or conversation from controlling another’s child turn.

**Data flow**: It receives a turn id, loads its parent and requester information, checks whether it was spawned by the current parent turn or a sibling turn in the same conversation, and compares the stored member authority. It returns the child’s profile name, or null for an agent child, if allowed; otherwise it raises an error.

**Call relations**: The public child operations result, wait, cancel, and message all call this first. It is the file’s main ownership gate for existing spawned turns.

*Call graph*: called by 4 (cancel, message, result, wait); 3 external calls (select, workspace_tx, authority_member_id).


##### `Subagents._profile_model`  (lines 739–745)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a named profile, if any. It is used when continuing an existing child so billing is based on the model that child would use.

**Data flow**: It receives a profile name or null, searches the registry, and returns that profile’s model value if found. If the profile is absent or null, it returns null.

**Call relations**: Subagents.message calls this before checking balance for a follow-up. It deliberately does not raise for missing profiles, because a live child should not become impossible to bill-check just because the registry changed.

*Call graph*: called by 1 (message).


##### `Subagents._child_runtime_config`  (lines 747–769)

```
def _child_runtime_config(self, model: str | None) -> TurnRuntimeConfig | None
```

**Purpose**: Builds the runtime settings for a child turn, especially when the caller asks to pin a specific model. It also refuses model choices that would conflict with the parent’s already pinned model.

**Data flow**: It receives an optional model id and reads the parent turn’s runtime config. If no model is requested, it returns the inherited config; if a parent model is already pinned, it raises; if the requested model is unknown to this deployment, it raises; otherwise it returns a new TurnRuntimeConfig with that model set.

**Call relations**: Subagents.spawn calls this before target resolution and admission. Its result is later stored by _admit and used by future turn setup and billing.

*Call graph*: calls 2 internal fn (pinned_tree, unknown); called by 1 (spawn); 1 external calls (model_validate).


##### `Subagents._require_balance`  (lines 771–790)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Checks that the workspace has enough prepaid balance or billing permission to start new work. It prevents child turns from being admitted for free when funds are exhausted.

**Data flow**: It receives a database connection, an optional model id, and optionally the agent id that will run the child. It asks BalanceGate whether the work is allowed; if the gate rejects it, it raises BalanceExhausted.

**Call relations**: _admit calls this for newly admitted child work, and Subagents.message calls it before admitting a follow-up. It uses the chosen child model and agent, not blindly the parent’s, so billing follows the actual work.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 792–920)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, request_fingerprint: str, delivers_result: bool=False, name:
```

**Purpose**: Writes the child conversation and first child turn into the database, or reconnects to an existing deduplicated one. Admission is the durable record that the child exists.

**Data flow**: It receives the child ids, agent/profile details, validated inbound JSON, delivery settings, runtime config, and billing model. Inside a transaction it inserts conversation and turn rows if absent, checks that any existing row matches the same request and requester, verifies permissions for agent targets, checks balance for new work, stamps the turn as ready for dispatch, and returns whether it should be enqueued.

**Call relations**: Subagents.spawn calls this after validation and before _enqueue. It calls _require_agent_spawn for workspace-agent permission checks and _require_balance for billing, and it is the key idempotent step that makes replay safe.

*Call graph*: calls 2 internal fn (_require_agent_spawn, _require_balance); called by 1 (spawn); 8 external calls (model_dump, select, update, workspace_tx, current_traceparent, authority_member_id, conversation_name, audience_member).


##### `Subagents._require_agent_spawn`  (lines 922–973)

```
async def _require_agent_spawn(self, connection: AsyncConnection, target: AgentTarget, inbound: str) -> None
```

**Purpose**: Checks that the requester is allowed to spawn a workspace agent and that the stored inbound payload still fits that agent’s input schema. This protects agent ownership and admin-only access.

**Data flow**: It receives a database connection, an AgentTarget, and inbound JSON. It reloads and locks the agent row, rejects missing or archived agents, checks whether the requester owns the agent or is an admin, and validates the inbound JSON against the agent’s current input contract.

**Call relations**: _admit calls this only for new agent-child admissions. If the target is gone, it uses _profile_names and live agent names to raise an UnknownSpawnTarget-style error.

*Call graph*: calls 2 internal fn (_profile_names, __init__); called by 1 (_admit); 5 external calls (execute, select, authority_member_id, member_is_admin, input_contract).


##### `Subagents._enqueue`  (lines 975–1003)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Places an admitted child turn onto the DBOS express queue so the worker system will run it. DBOS is the workflow engine used here for durable background execution.

**Data flow**: It receives the child turn id and conversation id, builds enqueue options, and asks the DBOS client to enqueue the workflow. If enqueueing is cancelled or fails, it clears the dispatch timestamp so another dispatcher can try later; non-cancel failures are logged instead of crashing the caller.

**Call relations**: Subagents.spawn calls this only when _admit says the child is still queued and needs dispatch. It is the bridge from database admission to actual execution.

*Call graph*: called by 1 (spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 1005–1048)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has committed its terminal result. A terminal is the final recorded ending of a turn, such as done, failed, or cancelled.

**Data flow**: It receives a child turn id, tries to retrieve and wait on the DBOS workflow, repeatedly checks the database for a terminal or parked state, follows newer running attempts if the workflow id changed, and returns the TerminalFrame once it exists.

**Call relations**: Subagents.spawn uses this for foreground waits, Subagents.wait uses it for explicit waiting, and _await_terminal_or_detach uses it as one side of its race. It calls _terminal_or_park and _running_attempt to reconcile workflow state with database state.

*Call graph*: calls 2 internal fn (_running_attempt, _terminal_or_park); called by 3 (_await_terminal_or_detach, spawn, wait); 1 external calls (sleep).


##### `Subagents._running_attempt`  (lines 1050–1056)

```
async def _running_attempt(self, turn_id: UUID) -> str | None
```

**Purpose**: Reads the workflow attempt id currently running for a turn, if any. This helps the waiter follow a restarted or retried child workflow.

**Data flow**: It receives a turn id, queries the turn row, and returns the running_attempt value from the database.

**Call relations**: _await_terminal calls this when the workflow it was watching is missing or ended without a terminal. It lets waiting continue on the actual active attempt instead of failing too early.

*Call graph*: called by 1 (_await_terminal); 2 external calls (select, workspace_tx).


##### `Subagents._await_terminal_or_detach`  (lines 1058–1114)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child result, but stops waiting if a new member message arrives for the parent conversation. In that case the child keeps running in the background and will deliver its own result later.

**Data flow**: It receives a child turn id. It starts one task waiting for the child terminal and another listening to the parent’s hub subscription for new arrivals. If the terminal wins, it returns the terminal; if a valid member arrival wins and _detach succeeds, it returns null; it cleans up both tasks before leaving.

**Call relations**: Subagents.spawn calls this when detach_on_arrival is requested. It uses _await_terminal for normal completion, _detach for the database handoff to background delivery, and the hub to avoid polling while waiting for parent messages.

*Call graph*: calls 2 internal fn (_await_terminal, _detach); called by 1 (spawn); 5 external calls (create_task, ensure_future, gather, wait, log).


##### `Subagents._terminal_or_park`  (lines 1116–1132)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Checks whether a child has a terminal result, and handles the special parked state. Parked means the turn stopped waiting on a spend limit without a final terminal.

**Data flow**: It receives a turn id, loads terminal and status from the database, returns a TerminalFrame if present, cancels and raises SubagentParked if the status is parked, or returns null if the child is still running or queued.

**Call relations**: _await_terminal calls this repeatedly while reconciling DBOS workflow state. It uses cancel_one_turn for parked children so the parent does not wait forever or leave costly abandoned work.

*Call graph*: called by 1 (_await_terminal); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents._detach`  (lines 1134–1165)

```
async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool
```

**Purpose**: Marks a still-running child so its result will be delivered later instead of returned inline. It only does this if the interrupting arrival is truly an unconsumed member message for the parent turn.

**Data flow**: It receives a child turn id and an arrival id. In one database update, it checks that the child is unfinished, has no existing delivery request, and that the arrival row belongs to the parent conversation as a member admission; if the update changes one row, it returns true, otherwise false.

**Call relations**: _await_terminal_or_detach calls this when the hub reports an arrival. Its guarded update decides the race between “child finished first” and “parent got a new member message first.”

*Call graph*: called by 1 (_await_terminal_or_detach); 4 external calls (exists, select, update, workspace_tx).


##### `SubagentResult.deliver`  (lines 1194–1244)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished child’s result back into the parent conversation when the child was marked for background delivery. This turns completed child work into an ordinary arrival the parent can read.

**Data flow**: It receives a child Turn. If delivery is not pending, it does nothing; if the child lacks a terminal, it raises. Otherwise it loads the parent turn information and, for agent children, the child agent row, builds the delivery body, invokes the parent conversation with a deduplicated key, and then marks the child delivery as delivered.

**Call relations**: This is the delivery-side counterpart to background spawn and detach. It calls _body to create the structured message and uses the injected TurnInvoker to admit that message into the parent conversation before stamping the child delivered.

*Call graph*: calls 1 internal fn (_body); 4 external calls (model_validate, select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1246–1275)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the structured text envelope that carries a child’s result to the parent. The envelope names the target, the spawn id, and the result status so the parent can understand where it came from.

**Data flow**: It receives the child turn and, for agent children, the child agent row. It chooses the correct target label and output contract, asks _payload for the safe payload and status, wraps untrusted content with a protective wall when needed, escapes any closing envelope text inside the payload, and returns the final message string.

**Call relations**: SubagentResult.deliver calls this before invoking the parent conversation. It calls _payload for validation and output shaping, output_contract for agent-child schemas, and wall to prevent untrusted result text from being read as direct instructions.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (wall, output_contract).


##### `SubagentResult._payload`  (lines 1277–1296)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child terminal into the payload text and status used in a delivered spawn result. It validates successful answers and converts failures or questions into explicit result states.

**Data flow**: It receives an output contract, or null if no contract is available, and a TerminalFrame. If the child did not finish successfully, it returns diagnostic text and that status; if the child asked a question, it returns the question JSON and a question status; if the contract is missing or validation fails, it returns an invalid-result message; otherwise it returns the validated output JSON and done.

**Call relations**: SubagentResult._body calls this while building the delivery envelope. It is the last check that keeps malformed child output from being delivered as if it were a trustworthy answer.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### Writing and website workers
These files configure structured writing pipelines and specialized child agents for document drafting and website-building delegation.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `startup / extension load`

This file is the blueprint for a simple writing assembly line. The parent agent can ask one subagent to make an outline, pass that outline to another subagent to write a draft, and then pass the draft to a third subagent for critique. Each stage is deliberately narrow: it cannot use tools and cannot create more subagents, so the process stays predictable and shallow.

The file first names the three profiles and points to the folder that contains their prompt text. A prompt is the written instruction that tells a language-model agent how to behave. It then defines small Pydantic models, which are structured data shapes that check whether inputs and outputs have the expected fields. For example, the outline stage receives a topic and audience, and returns an outline. The draft stage receives the topic plus outline, and returns a draft. The critic receives the draft, and returns a verdict plus optional improvements.

At the bottom, the file creates three SubagentProfile objects. These are like job descriptions for the subagents: each one includes a name, prompt, allowed tools, input shape, output shape, and maximum number of rounds. Without this file, the brief pipeline would not have a shared contract for what each stage should receive or produce, so the parent agent could not reliably connect the stages together.


### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is like an ID badge and job description for a specialized writing helper. In this system, a parent assistant can start a smaller “subagent” to do one focused job. Here, that job is writing and revising text, especially drafts that follow the project’s `writing-drafts` workflow.

The file names the profile `writing`, pins it to the `gpt-5.6-terra` model, and loads its detailed instructions from a nearby prompt file. It also decides which tools the child can use. The writing child may read, write, edit, search files, and load a skill. It may not run shell commands, use a programming REPL, browse the web, or share files directly. That matters because this subagent is meant to be a prose worker, not a second coding agent or an independent researcher. Its handoff is the workspace: it saves draft files for the parent to inspect.

Two small data shapes describe the conversation boundary. `WritingTask` says what the parent sends in: a freeform objective, plus skills to preload. By default, it preloads `writing-drafts`, so the child starts with the right workflow already available. `WritingResult` says what comes back: a freeform result string. Finally, `WRITING_PROFILE` packages all of this into a `SubagentProfile`, which the wider system can use when it wants to spawn this writing-focused child.


### `extensions/sites/ufo_ext_sites/application_homepage.py`

`config` · `subagent setup and result validation during homepage design/build`

This file is a safety wrapper and job description for an “application homepage builder” subagent. A subagent is a smaller AI worker started by the main system to do one focused task. Here, that task is to create a homepage for an application: first a visual design, then a deployed web page.

The file sets constants such as the builder’s name, model, skill, workspace paths, and round limit. The long phase contract tells the child agent where each phase must stop. In the design phase, it should produce a wireframe and report the design path. In the build phase, it must create or reuse the design, build the page, deploy it, and return the hosted site information. If it truly cannot continue, it must explain the blocker.

Two Pydantic models are the gatekeepers. Pydantic is a validation library that checks whether data has the expected shape. `ApplicationBuildTask` describes the input given to the child agent: the objective and whether this is a design or build phase. `ApplicationBuildResult` describes the output the child must give back.

The important protection is that the result must include proof for its status. A deployed result without a URL is like saying “the package was delivered” without giving an address or tracking number. The validator catches that early, so the main system does not bind an empty or broken homepage to an application.

#### Function details

##### `ApplicationBuildResult.status_carries_its_evidence`  (lines 77–86)

```
def status_carries_its_evidence(self) -> 'ApplicationBuildResult'
```

**Purpose**: This validation method makes sure the child agent’s final answer includes the evidence needed for the status it claims. A design result must include a design file path, a deployed result must include site identifiers and a URL, and a blocked result must include an explanation.

**Data flow**: It starts with an `ApplicationBuildResult` object that already has a status and optional fields. It looks up which fields are required for that status, checks whether any are empty, and either raises a validation error naming the missing fields or returns the unchanged result as valid.

**Call relations**: This method is run automatically by Pydantic after an `ApplicationBuildResult` is created or parsed. It sits between the child agent’s answer and the parent system, preventing incomplete claims from moving forward into the homepage-binding flow.


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup`

This file is like a job description and toolbox list for a specialist assistant whose only job is building websites. The main agent can hand off a website-building task to this subagent, and this profile explains what the subagent is allowed to do and how it should report back.

The file loads a dedicated prompt from a Markdown file. That prompt contains the website-building instructions the subagent follows. It then chooses the tools the subagent may use: basic file tools such as reading and writing files, shell access, site build and local serving tools, JavaScript and spreadsheet REPLs, and optional web research tools. A REPL is an interactive scratchpad where code can be run piece by piece. Some tools are deliberately excluded. For example, publishing a full website with a backend is left to the parent agent, because the subagent works inside the parent turn’s sandbox and should not make final delivery decisions on its own.

The file also defines the shape of the task sent in and the result sent back using Pydantic models, which are structured data classes that validate fields. Finally, it creates a SubagentProfile object that bundles the name, prompt, allowed tools, input and output formats, and maximum number of rounds into one reusable profile.


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file exists so the main agent does not have to build a website directly when there is a more focused helper available. Think of it like a project manager handing a full brief to a web designer: the manager stays in charge of the overall conversation, but the specialist does the build-and-check work.

The file defines the shape of the tool input with `BuildWebsiteInput`. The most important field is `objective`, which must include all needed instructions because the child agent starts without the parent conversation’s history. Optional fields let the caller give the build a friendly name, preload useful skills so the child starts prepared, or give the child more working time for large builds.

The actual tool function, `_build_website`, uses the tool context to spawn the `website_building` profile. A profile is a prepared agent setup with its own tools and instructions. The child works in the same sandbox filesystem, so files it creates remain available afterward, but it has its own conversation transcript. The tool is marked as side-effecting because it can create files, run a site, and register a deployed result. It also uses an idempotency key, which means if the same tool call is retried after a crash, the system can reconnect to the already-started child instead of accidentally starting a duplicate build.

#### Function details

##### `_build_website`  (lines 56–63)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the worker behind the `build_website` tool. It sends the caller’s website brief to the specialized website-building subagent, waits for that subagent’s result, and wraps the result as tool output.

**Data flow**: It receives a tool context, which contains runtime information such as the idempotency key, and a validated `BuildWebsiteInput` object, which contains the website objective and optional build settings. It turns the input into a plain data payload, leaving out empty optional values, then asks the context to spawn the website-building profile with that payload. When the child finishes, it takes the child’s output, converts it to JSON text if there is output, or uses an empty string if there is none. It returns that text inside a `ToolResult`, which is the standard response format for a tool call.

**Call relations**: This function is registered as the handler for the `build_website` tool in `DELEGATION_TOOLS`. When an agent calls that tool, the tool runtime calls `_build_website`. `_build_website` then hands the work to `ToolContext.spawn`, which starts or reconnects to the child website-building run, and finally packages the child’s summary with `TextContent` and `ToolResult` so the parent agent can read it.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### Objective tracking
These files persist long-running objectives and expose tools for planning, checking, reading, and delegating objective work across turns.

### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `startup/import`

This is the package entry file for the objectives extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, meaning other parts of the project can refer to it by name and load modules from it. Here, the file contains only a short docstring: “The objectives extension.” That acts like a label on a folder, helping readers and documentation tools understand what this package is about.

Nothing is executed here, and no functions, classes, or settings are defined. Its main value is structural: without it, depending on the Python version and packaging setup, the extension might not be recognized or documented as a normal package. Think of it like a sign on a drawer: it does not contain the tools itself, but it tells people and the system what kind of tools are stored inside.


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `cross-cutting: active whenever objectives are planned, read, attempted, blocked, or checked`

An objective here is a piece of work with named steps. Each step may have acceptance conditions, such as “this file exists” or “this command succeeds.” This file stores those plans and records every later event against them: a step was tried, a step was blocked, or the extension checked whether the step’s conditions now hold.

The important idea is that events are appended, not rewritten. Like a lab notebook, the record keeps what was attempted and when. That matters because a later turn may wake up with fresh memory and must rebuild the situation from durable storage. It also prevents a worker from quietly changing the success conditions after discovering the work is harder than expected.

The file has three main layers. First, it declares database tables for objectives, steps, events, and checks. Second, it defines small data shapes such as conditions, step plans, step views, and objective views. These turn raw database rows into meaningful snapshots. Third, the Objectives class provides the read and write operations: find an objective, create or revise a plan, append an event, save condition-check results, and rebuild a full view.

A key behavior is that a step’s state is derived from its history and latest check. A claimed attempt alone is not enough to close a checked step; the extension’s own condition verdicts decide that.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This property answers the simple question: has anyone recorded that this step was tried? It is used so the system can distinguish untouched work from work that has been attempted but not yet proven complete.

**Data flow**: It reads the step’s stored event list. If any event has the kind used for a completed attempt, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: Other step and objective calculations rely on this as a basic fact about the step’s history. For example, the state calculation uses it before deciding whether a step is pending, attempted, done, or unmet.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This property finds whether the step is currently blocked by an unanswered question or obstacle. It only treats the latest event as an open block, because a later attempt or event means the old block is no longer the current situation.

**Data flow**: It looks at the last event in the step’s event history. If that event is a block, it returns that event; if there are no events or the latest event is not a block, it returns nothing.

**Call relations**: The record-writing path uses this to avoid writing the same block again and again. The runnable-step calculation also uses it so the engine does not dispatch work that is waiting on an open block.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This property turns a step’s events, acceptance conditions, and latest verdicts into a clear status such as pending, done, blocked, attempted, or unmet. It is the central rule for deciding what still needs work.

**Data flow**: It reads the step’s event history, conditions, and condition verdicts. A latest block becomes blocked; no attempt becomes pending; an attempted step with no conditions becomes done; an attempted step with conditions waits for matching verdicts; if all verdicts pass it becomes done, otherwise unmet. It returns the status text and does not write anything.

**Call relations**: Objective-level views use this property to count confirmed steps and to build the frontier of unfinished work. This keeps the rest of the extension from trusting worker claims without the extension’s own checks.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This property counts how many recorded attempts have been made across all steps in the objective. It gives a quick signal of how much effort has been spent.

**Data flow**: It reads every step’s events and counts events whose kind means an attempt was made. It returns that count without changing the objective.

**Call relations**: This is part of the summary picture an objective can give to callers. It pairs with confirmed to show whether repeated attempts are actually turning into completed steps.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This property counts how many steps are truly complete according to the step-state rules. It reports confirmed progress, not just claimed activity.

**Data flow**: It asks each step for its current state and counts the steps whose state is done. It returns that number and does not modify anything.

**Call relations**: This uses StepView.state, so it inherits the rule that checked conditions must actually pass before a conditional step counts as complete.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This property identifies which unfinished steps may be started in parallel right now. It only includes steps that the original plan marked as independent, have not yet been attempted, and are not waiting on a block.

**Data flow**: It starts from the objective’s frontier, meaning unfinished steps. It filters that list to independent steps with no recorded attempt and no open block, then returns them as a tuple.

**Call relations**: Dispatch code can use this as the safe fan-out list: these are the steps the plan said do not depend on earlier work. It relies on frontier, StepView.attempted, and StepView.open_block to avoid sending the wrong work.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This property lists the steps that are not yet done. It is the objective’s current work queue in snapshot form.

**Data flow**: It checks each step’s state and keeps only steps whose state is not done. It returns those steps without changing the stored record.

**Call relations**: Runnable builds on this list to choose parallelizable work. Other callers can use it to see what remains after completed steps are filtered out.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This helper turns a machine-readable acceptance condition into a short human-readable phrase. It is useful when showing or explaining what must be true for a step to count as complete.

**Data flow**: It receives one condition object. Depending on whether the condition is a file existence check, a file text check, or a command success check, it formats the relevant path, text, or command into a sentence-like string.

**Call relations**: It sits beside the condition data models as a display helper. It does not read the database or affect objective state.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This method finds one objective by conversation and name within the current workspace. The conversation filter matters because different agents or subagents may reuse names without meaning the same objective.

**Data flow**: It receives a conversation id and objective name, then queries the objective table for a matching row in this workspace. If no row exists, it returns nothing; if one exists, it asks _view to build the full ObjectiveView with steps, events, and checks.

**Call relations**: Objectives.plan calls this first to decide whether it is creating a new objective or revising an existing one. When a row is found, named hands off to _view so callers get the full readable snapshot rather than a bare database row.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This method fetches the most recently created objective for a conversation in the current workspace. It is useful when the caller knows the conversation but not the objective name.

**Data flow**: It receives a conversation id and queries the objective table for matching objectives, newest first, taking only one. If it finds none it returns nothing; if it finds one it calls _view to assemble the full objective snapshot.

**Call relations**: This is another entry point into the stored objective record. Like named, it relies on _view to turn database rows into the higher-level ObjectiveView used by the rest of the extension.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This method creates a new objective plan or revises an existing one. Its most important rule is that acceptance conditions for already-attempted steps stay frozen, so workers cannot make success easier after the fact.

**Data flow**: It receives the conversation id, objective name, directive, and planned steps. It looks for an existing objective; if none exists, it inserts one. If one exists, it updates the directive, preserves conditions for attempted steps, and removes old unstarted steps that are no longer in the plan. It then updates or inserts each planned step and finally returns a freshly read ObjectiveView.

**Call relations**: This is the main planning write path. It calls named to inspect the current record and later to return the finished view; in between it writes objective and step rows using database insert, update, and delete operations.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This method appends an event saying that a step was attempted or blocked. It deliberately avoids recording the exact same still-open block twice, which prevents repeated wake-ups from flooding the history with the same question.

**Data flow**: It receives a step view, event kind, actor turn id, and evidence text. It trims the evidence to the maximum stored length, checks whether the same block is already open, and either returns false without writing or inserts a new event row and returns true.

**Call relations**: This is the event-writing path used after work is tried or an obstacle is raised. It relies on StepView.open_block to recognize duplicate blocks and writes to the event table when there is genuinely new history to preserve.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This method records the extension’s own verdicts about whether a step’s acceptance conditions currently hold. It appends a new check instead of replacing old ones, so the history can show that something passed or failed at different times.

**Data flow**: It receives a step view, a tuple of condition verdicts, and the actor turn id. It converts each verdict into stored JSON containing the condition, whether it held, and a detail message, then inserts a new check row. It returns nothing.

**Call relations**: Condition evaluation code can call this after checking files or commands. Later, _view reads the latest check back so StepView.state can decide whether an attempted conditional step is done or unmet.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This private method rebuilds a complete ObjectiveView from database rows. It is the bridge between raw storage and the convenient in-memory picture used by the rest of the extension.

**Data flow**: It receives an objective database row. It queries that objective’s steps, then the events and checks for those steps. It groups events by step, keeps the latest check per step, parses stored condition data and verdict data, and returns an ObjectiveView containing StepView objects in plan order.

**Call relations**: named and on_conversation call this after finding an objective row. _view calls _conditions and _verdicts to turn stored JSON into typed condition and verdict objects, then constructs the view objects that expose properties such as state, frontier, and runnable.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This helper parses stored acceptance-condition data back into condition objects the code can reason about. It protects the system from silently accepting unknown condition kinds.

**Data flow**: It receives a payload, usually JSON read from the database. If the payload is not a list, it returns an empty tuple. For each list item, it looks at the kind field, validates the matching condition type, and returns all parsed conditions as a tuple; unknown kinds raise an error.

**Call relations**: Objectives._view uses this when rebuilding each step’s acceptance conditions. _verdicts also uses it to parse the condition embedded inside each stored verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This helper parses stored condition-check results into ConditionVerdict objects. These verdicts are what let a step’s state be based on the extension’s observation rather than a worker’s claim.

**Data flow**: It receives a payload, usually JSON from the latest check row. If it is not a list, it returns an empty tuple. For each dictionary item, it parses the embedded condition, converts the holds value to true or false, converts the detail to text, and returns the resulting verdicts.

**Call relations**: Objectives._view calls this when a step has a saved check. It uses _conditions for the condition part and then creates ConditionVerdict objects that StepView.state can later interpret.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `request handling and cross-turn objective tracking`

This file is the control panel for the objectives extension. An objective is a named piece of work that may last across several turns, with steps and acceptance checks that say what must be true before each step can close. Without this file, agents could write down plans, but they would not have the tool behavior that keeps those plans honest: checking files, text, and commands against the actual sandbox before calling work complete.

The main idea is simple: saying “I did it” is not the same as proving “it is true.” When an agent records a completed step, the file re-runs that step’s conditions. These conditions can ask whether a file exists, whether a file contains exact text, or whether a command succeeds. If a check fails, the step stays unmet and the tool reports what failed.

It also protects against weak plans. For example, if a step says it will create a file, but that file already exists when the plan is written, that condition would prove nothing. The plan is refused so the agent must name better evidence.

Finally, the file can fan out independent steps to background subagents, like handing separate errands to different helpers, and it can render the whole objective as readable text.

#### Function details

##### `_require_ext`  (lines 104–107)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool is running inside the objectives extension. The tools need that extension context to open database transactions and save or read objective state.

**Data flow**: It receives the current tool context. If the context includes an extension object, it returns that object. If not, it stops immediately with an error, because continuing would mean trying to use storage that is not available.

**Call relations**: The main tool handlers call this at the start of their work. It is the doorway they pass through before planning, recording, reading, or dispatching objective steps.

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 110–125)

```
def render(view: ObjectiveView) -> str
```

**Purpose**: This turns an objective view into plain text that an agent or person can read. It summarizes the objective, each step, what each step still needs, recent evidence, and any failed checks.

**Data flow**: It receives an objective view from storage. It reads the objective name, directive, attempt counts, step states, acceptance conditions, verdicts, and recent events. It returns one formatted text block, ready to place in a tool result.

**Call relations**: After a plan is saved, a step is checked, or an objective is reread, the surrounding tool calls this function to present the current state. It relies on the store’s condition summary helper to describe checks in human terms.

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 128–132)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This checks all acceptance conditions for one step. It answers the practical question: does the real workspace currently satisfy what this step said would prove completion?

**Data flow**: It receives the tool context and a step view. For each condition attached to the step, it asks `_verdict` to run the actual check. It returns a tuple of verdicts, one per condition, saying which checks held and which did not.

**Call relations**: The record and read tools use this when they need fresh truth instead of stale memory. It delegates the detailed checking of each individual condition to `_verdict`.

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 135–159)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

**Purpose**: This runs one acceptance condition and turns the result into a clear yes-or-no verdict. It is the place where abstract conditions become real sandbox commands.

**Data flow**: It receives a tool context, a condition, and a phase label such as planning or recording. It converts the condition into a shell command: test whether a file exists, search for exact text in a file, or run a supplied command. It runs that command in the sandbox with a timeout, records a metric about the result, and returns a condition verdict with a helpful detail message.

**Call relations**: The bulk checker `evaluate` calls this for every condition on a step. The planning tool also calls it to reject file-based conditions that are already true before work starts. When a check fails, it asks `_unmet` to explain the failure.

*Call graph*: calls 1 internal fn (_unmet); called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `_unmet`  (lines 162–177)

```
def _unmet(condition: Condition, result: ExecResult) -> str
```

**Purpose**: This explains why a condition did not pass in terms useful for the next attempt. Instead of only saying “false,” it includes whether the command exited, timed out, and what the command said near the end.

**Data flow**: It receives the condition that was tested and the sandbox execution result. It builds a short failure message from the condition summary, the exit status or timeout, and the tail of standard output or standard error. It returns that message as text.

**Call relations**: `_verdict` calls this only when a condition fails. Its output later appears in rendered objective views so the agent can see what still needs fixing.

*Call graph*: called by 1 (_verdict); 1 external calls (condition_summary).


##### `plan_objective`  (lines 180–235)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

**Purpose**: This records or revises an objective plan. Before saving, it refuses file-based proof that is already true, because that would let a step close without proving the work changed anything.

**Data flow**: It receives the tool context and a proposed objective name, directive, and ordered steps. It loads any existing objective with the same name, compares proposed conditions with already recorded steps, and tests new file-based conditions that are supposed to describe produced state. If any such condition is already true, it returns an error explaining the weak evidence. Otherwise it stores the plan and returns a readable rendering of it.

**Call relations**: This is the handler behind the `plan_objective` tool definition. It uses `_require_ext` to get storage access, `_verdict` to test suspicious plan conditions, the objectives store to save the plan, and `render` to show the saved result.

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 238–286)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

**Purpose**: This records that a step was attempted or that it is blocked. For completed attempts, it does not simply trust the claim; it re-checks the step’s acceptance conditions before showing whether the step really closes.

**Data flow**: It receives the tool context and the objective name, step title, record kind, and evidence text. It loads the objective, finds the named step, and writes the event. If the step is marked blocked, it records that state and may warn if the same block was already recorded. If the step is marked did, it evaluates the step’s conditions, saves the check results, refreshes the objective, and returns a rendered view with the new verdicts.

**Call relations**: This is the handler behind the `record_step` tool definition. It calls `_require_ext` for extension storage, uses the objectives store to find and update the step, calls `evaluate` for real-world checks, uses `_with_verdicts` to attach fresh verdicts to the displayed view, and finishes with `render`.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 289–338)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

**Purpose**: This starts every currently runnable independent step in background subagents. It lets separate pieces of work happen at the same time instead of forcing one turn to do them one by one.

**Data flow**: It receives the tool context, an objective name, and a subagent profile. It loads the objective and asks which steps are ready to run independently. If none are ready, it returns a message saying so. Otherwise it spawns a background child turn for each step, using a deduplication key so the same step reconnects instead of running twice, records a dispatch metric, and returns the list of started subagents.

**Call relations**: This is the handler behind the `run_independent_steps` tool definition. It uses `_require_ext` and the objectives store to read the plan, calls the tool context’s spawn feature to start subagents, and calls `_dispatch_stopped` if one spawn fails after others have already started.

*Call graph*: calls 2 internal fn (_dispatch_stopped, _require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `_dispatch_stopped`  (lines 341–364)

```
def _dispatch_stopped(step: str, dispatched: list[tuple[str, str]], error: Exception) -> ToolFailure
```

**Purpose**: This builds a careful failure report when dispatching independent steps stops part-way through. It makes clear which subagents are already running so their later results are not mistaken for surprises.

**Data flow**: It receives the step that failed to start, the list of already dispatched steps and subagent turn IDs, and the error that stopped dispatch. It turns those into a tool failure with a summary and applied effects describing the children that are still running. The output is a structured failure object that can be returned as a tool result.

**Call relations**: `run_independent_steps` calls this when spawning a child subagent raises an error. The helper packages the partial success into a clear report and explains that re-running the tool is safe because already-started steps use deduplication keys.

*Call graph*: called by 1 (run_independent_steps); 2 external calls (__init__, __init__).


##### `read_objective`  (lines 367–386)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

**Purpose**: This reads an objective and returns its current state. For attempted steps with acceptance checks, it refreshes those checks first so the displayed state reflects the real workspace now.

**Data flow**: It receives the tool context and objective name. It loads the objective from storage. If it does not exist, it returns an error. For each attempted step that has conditions, it evaluates the conditions again, saves the fresh check results, updates the view for display, and returns the rendered objective text.

**Call relations**: This is the handler behind the `read_objective` tool definition. It uses `_require_ext` and the objectives store to load and update state, calls `evaluate` to avoid showing stale check results, uses `_with_verdicts` to place fresh verdicts into the view, and then calls `render`.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 389–399)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

**Purpose**: This creates a copy of an objective view with updated verdicts for one named step. It is a display helper: it lets the tool show fresh check results without rebuilding the whole objective by hand.

**Data flow**: It receives an objective view, a step title, and a tuple of verdicts. It walks through the steps, replaces only the matching step with a copy that contains the new verdicts, and returns a copied objective view with that updated step.

**Call relations**: The record and read tools call this after evaluating conditions. It bridges the fresh check results from `evaluate` into the view that `render` will turn into user-readable text.

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### Research delegation
These files define research worker profiles and the wide-research orchestration tool for parallel, retry-safe investigation.

### `extensions/research/ufo_ext_research/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. You can think of it like a label on a folder saying, “code in here belongs together and can be referred to by this package name.”

Because the file has no code, it does not create objects, start processes, change settings, or run any setup steps. Its value is structural: it makes the `extensions/research/ufo_ext_research` directory usable as a named place for research extension code. Without it, depending on the Python version and import style, other parts of the project might not be able to reliably import modules from this folder using normal package paths.

If future maintainers need package-wide setup, shared exports, or version metadata for this research extension, this file is the conventional place to put that. For now, it simply keeps the package boundary clear.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / agent profile registration`

This file is like a job description and equipment list for research-focused child agents. A child agent is a smaller worker that the main assistant can delegate a specific task to, instead of doing everything itself.

It defines two profiles. The first, called `research`, is meant for focused research tasks. The second, called `deep_research`, is meant for harder work that may need more time, more sources, and more back-and-forth steps. Both profiles use the same set of research tools: web search, page fetching, vertical search, browser tasks, external tools, file reading and writing, command-line access, memory search, and spreadsheet-style work. This keeps these agents useful for investigation while still limiting them to tools appropriate for research.

The file also loads each agent’s detailed instructions from nearby prompt files. These prompts are the written rules the agent follows while working.

To make delegation predictable, the file defines simple input and output shapes using Pydantic models. The input is an `objective`, meaning the task the parent wants researched. The output is a `result`, described using the shared subagent result standard so parent agents receive answers in a consistent format.

Without this file, the system would not know how to create or configure these research subagents: which model to run, how long they may work, what tools they can use, or what kind of task and result format they expect.


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file solves a practical batch-work problem: if a user wants the same kind of research done for many companies, people, or topics, doing them one by one is slow and fragile. `wide_research` turns that into a controlled parallel job. It reads an entity list from a workspace file, removes blanks and duplicates, checks the batch is not too large, reads the requested output schema, then starts several research subagents at once.

Each subagent gets a tailored prompt where `{entity}` is replaced with the current entity. The child is told to write its full JSON result to a private temporary file. The parent then reads that file and stores either the parsed result or a clear error for that one entity.

A key idea here is safe recovery. The tool uses an idempotency key, meaning a stable identifier for this exact tool call, like a receipt number. From that it builds repeatable child keys and file names. If the parent is restarted, it can reconnect to earlier child work and reuse rows already saved in a recovery aggregate file. This is like keeping a checklist on the counter while several assistants work: if the room loses power, you can resume from the checked-off items instead of starting over.

The file also treats individual failures as row-level failures. One bad entity should not erase useful results from the rest of the batch.

#### Function details

##### `_read_lines`  (lines 66–79)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads the user-provided entity file and turns it into a clean list of unique entity names. It is used so the batch research work starts from a predictable, duplicate-free set of targets.

**Data flow**: It receives the tool context and a file path. It asks the sandbox to run `cat` on that safely quoted path, then reads the file line by line. Blank lines are ignored, repeated names are skipped, and the remaining names come out as a list in their original order. If the file cannot be read, it raises an error instead of starting a broken batch.

**Call relations**: The main `_wide_research` setup function calls this first, before any child research work is started. It relies on shell quoting so the file path is treated as a path, not as a shell command.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_WideResearch.run`  (lines 98–134)

```
async def run(self) -> ToolResult
```

**Purpose**: This is the main driver for an already-prepared wide research batch. It launches visits for all entities, gathers their row results, writes the final output file, and returns a tool response pointing to that output.

**Data flow**: It starts with a prepared `_WideResearch` object containing the context, entities, recovery paths, output schema, and shared bookkeeping. It registers cleanup for temporary result files, runs `_visit` for every entity in parallel, and converts normal per-entity exceptions into error rows instead of failing the whole batch. It then writes a recovery aggregate and the final `wide_research.json` file, and returns JSON content that includes the rows and output file name.

**Call relations**: After `_wide_research` finishes setup, it creates `_WideResearch` and calls `run`. During the run, each entity is handed to `_visit`; once all rows are collected, `run` calls `_install_recovery` so the durable recovery record matches the final result.

*Call graph*: calls 2 internal fn (_install_recovery, _visit); 6 external calls (__init__, __init__, __init__, __init__, gather, dumps).


##### `_WideResearch._remove_result_files`  (lines 136–143)

```
async def _remove_result_files(self) -> None
```

**Purpose**: This cleanup helper removes the private per-entity JSON files after they have been folded into the aggregate output. It prevents temporary workspace clutter from building up.

**Data flow**: It reads the set of result file paths that have been persisted. If the set is empty, it does nothing. Otherwise it builds a safely quoted `rm -f` command and asks the sandbox to delete those files. If deletion fails, it raises an operating-system style error with the sandbox's message.

**Call relations**: `run` registers this function with the context cleanup system, so it is meant to be called later by the tool runtime during cleanup. It does not take part in producing rows; it tidies up files created by `_visit` after their contents have been saved.

*Call graph*: 1 external calls (quote).


##### `_WideResearch._install_recovery`  (lines 145–155)

```
async def _install_recovery(self, rows: tuple[WideResearchRow, ...]) -> None
```

**Purpose**: This writes the current batch progress to the recovery file in a careful, replace-all-at-once way. That lets a later retry resume from completed rows without trusting a half-written file.

**Data flow**: It receives the rows known so far. It wraps them in the standard wide research file shape, writes that JSON to a temporary staging path, then moves the staging file into the real recovery path. The move is used so readers see either the old complete file or the new complete file, not a partial write.

**Call relations**: `_save_row` calls this whenever one entity finishes, so progress is saved incrementally. `run` also calls it at the end with the complete row set before writing the user-facing output file.

*Call graph*: called by 2 (_save_row, run); 3 external calls (__init__, dumps, quote).


##### `_WideResearch._save_row`  (lines 157–167)

```
async def _save_row(self, row: WideResearchRow) -> None
```

**Purpose**: This records one finished entity row and immediately updates the recovery file. It is the checkpoint step that makes crash recovery useful.

**Data flow**: It receives a `WideResearchRow`, waits for a lock so two parallel tasks do not edit the shared aggregate at the same time, stores the row by entity name, and rebuilds the saved progress in the original entity order. It then calls `_install_recovery` and marks that entity's private result file as safe to clean up later.

**Call relations**: Each `_visit` calls this after it has either read a valid result or created an error row. Because many `_visit` calls can run at once, `_save_row` acts like a single clerk updating the shared checklist one item at a time.

*Call graph*: calls 1 internal fn (_install_recovery); called by 1 (_visit).


##### `_WideResearch._visit`  (lines 169–216)

```
async def _visit(self, entity: str) -> WideResearchRow
```

**Purpose**: This performs the research workflow for one entity. It either reuses a recovered row, starts or reconnects to a research subagent, reads that child’s JSON file, and turns the outcome into one row.

**Data flow**: It takes an entity name from the batch. First it waits on a semaphore, which is a counter that limits how many entities run at once. If the entity was already recovered, it returns that saved row. Otherwise it builds a stable child id from the parent idempotency key and the entity, creates a prompt telling the research subagent what to write and where, spawns that child, then reads the expected result file. A missing file becomes an error row, invalid JSON becomes an error row, and valid JSON becomes a result row. In each new case, it saves the row through `_save_row` before returning it.

**Call relations**: `run` starts one `_visit` task per entity. `_visit` is the point where this wide batch hands actual research off to the research profile subagent, then brings the answer back into the parent aggregate.

*Call graph*: calls 1 internal fn (_save_row); called by 1 (run); 4 external calls (__init__, model_validate, loads, quote).


##### `_wide_research`  (lines 219–290)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the tool handler called when someone invokes `wide_research`. It validates the request, prepares recovery and parallelism settings, and then starts the batch runner.

**Data flow**: It receives the tool context and parsed user input. It requires an idempotency key, reads and deduplicates the entity file, rejects batches above the configured maximum, creates a stable call id, deletes old recovery files from other turns, and tries to load any matching recovery file for this call. It then reads the output schema file; if that fails, it returns a structured tool failure before any child work starts. Finally it builds per-entity result paths, creates the semaphore and lock, constructs `_WideResearch`, and returns whatever its `run` method returns.

**Call relations**: This function is connected to the `ToolDef` as the handler for the public `wide_research` tool. It is the bridge between the tool runtime and the `_WideResearch` batch object: first it does setup and safety checks, then it delegates the real fan-out work to `_WideResearch.run`.

*Call graph*: calls 1 internal fn (_read_lines); 8 external calls (__init__, __init__, __init__, Lock, Semaphore, sha256, loads, quote).

## 📊 State Registers Touched

- `reg-model-catalog` — The live menu of AI models, their capabilities, providers, and calling rules.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-skill-library` — The stored and packaged reusable skill instructions and files available to agents.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-environment-documents` — The saved per-agent run environment describing prompts, tools, skills, files, and model overrides.
- `reg-subagent-delivery-state` — The parent-child task links and owed-result records used when agents spawn helper agents.
- `reg-object-store-and-journal` — The shared workspace object records and change history for agents, tasks, memories, sites, and related items.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-monitor-objective-state` — The saved monitors, objectives, plans, steps, evidence, and blocks that survive across turns.

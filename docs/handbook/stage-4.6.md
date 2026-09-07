# Agent skill, authoring, document, coding, and research registrations  `stage-4.6`

This stage is part of the system’s behind-the-scenes setup. It does not do the writing, coding, or research itself. Instead, it registers the add-on packs that make those abilities available, like putting labeled tools into a workshop before anyone starts working.

Each manifest file is a registration sheet. The brief-pipeline manifest tells the host about three helper agents and the instructions for using them together to build briefs. The coding manifest declares repository-editing helpers, the tools they may use, their prompts, models, and skill folder, so code changes happen in a controlled way. The documents manifest makes document, PDF, theme, review, and drafting skills visible, along with a writing helper agent. The research manifest registers research tools, specialist agents, prompts, skills, and the required search backend. The skill-create manifest adds the ability for workspace members to save, reload, search, and index their own skills.

The two __init__.py files are package markers. They let Python recognize those extension folders as importable packages and provide a short description of what they contain.

## Files in this stage

### Brief pipeline package
Package marker and manifest registration for the coordinated brief-building extension and its subagent stages.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/__init__.py`

`other` · `startup/import time`

This is a very small package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, meaning other code can refer to it by name. Here, the only content is a short text description: “Brief pipeline extension.” That description does not run any logic, but it tells readers and tools what this package is meant to represent.

Think of this file like the label on a folder in a filing cabinet. The real documents may live in other files inside the folder, but this label lets the rest of the project recognize the folder as a meaningful unit. Without this file, depending on the Python version and packaging setup, the extension might not be discovered or imported in the expected way.


### `extensions/brief_pipeline/ufo_ext_brief_pipeline/manifest.py`

`config` · `startup or extension discovery`

This file is like the label and packing list on a plug-in box. When the larger UFO system discovers this extension, it needs to know its name, version, what helper agents it adds, and where to find the skill files that describe how to use them. Without this file, the extension’s outline, draft, and critic stages would exist in code, but the host would not know to load them as part of one usable package.

The extension describes a writing pipeline. First, an outline subagent prepares structure. Then a draft subagent turns that outline into prose. Finally, a critic subagent reviews the draft. The parent agent stays in charge and uses the critique itself rather than handing everything over permanently.

The file defines a stable extension name, a version, and the path to the skill directory on disk. Its only function, `manifest`, packages those pieces into a `Manifest` object. A manifest is a small declaration object: it does not run the pipeline itself, but it tells the system what is available so the system can wire the extension into normal operation.

#### Function details

##### `manifest`  (lines 15–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension declaration for the brief-pipeline feature. The host system uses this declaration to learn the extension’s name, version, available subagents, and skill folder.

**Data flow**: It starts with constants from this file, such as the extension name, version, and skill directory path, plus the three imported subagent profiles. It wraps the skill directory in a `SkillSpec`, then puts the name, version, subagent profiles, and skill specification into a `Manifest`. The result is a ready-to-read description of what this extension contributes; it does not change outside state.

**Call relations**: When the UFO extension loader asks this module what it offers, this function creates the answer. It uses `SkillSpec` to describe the skill files on disk, then hands that along to `Manifest`, which becomes the single object the rest of the system can inspect to register the brief-pipeline extension.

*Call graph*: 2 external calls (__init__, __init__).


### Work-product extension manifests
Registrations for coding, document-writing, and research extensions that expose specialized tools, skills, prompts, and subagents.

### `extensions/coding/ufo_ext_coding/manifest.py`

`config` · `extension load`

This file is like a job description plus access badge for specialized coding workers. The main system can ask for a child worker with `spawn("coding", {"objective": ...})`; this manifest says what that worker is allowed to do and how it should behave. The coding worker gets tools for reading and changing files, running shell commands, searching the repository, loading workflow instructions, testing JavaScript, and looking up external information. It does not directly send finished files to the user; it works inside the shared workspace, and the parent agent decides what to present.

The file also defines a stronger fallback worker called `fable_escalation`. That profile uses the same input and tools, but a different pinned model and prompt, intended for difficult pull-request blockers after normal coding workers have failed.

Two small data shapes, `CodingInput` and `CodingOutput`, describe the conversation contract: the caller gives an objective, optionally asks for a larger work budget, and the worker returns a freeform result. Finally, the `manifest` function packages all of this into a `Manifest` object so the extension loader can discover the extension, enable internet access in the sandbox, register the subagents, and load the `coding` skill from disk.

#### Function details

##### `manifest`  (lines 105–112)

```
def manifest() -> Manifest
```

**Purpose**: This function builds the extension’s official registration record. The system uses it to learn that this extension is named `coding`, what version it is, that its sandbox may use the internet, which child-agent profiles it offers, and which skill directories should be loaded.

**Data flow**: It reads the constants already defined in the file: the extension name and version, the two prepared subagent profiles, the skill root folder, and the skill names. It turns each skill name into a `SkillSpec`, then places those skill specs and the subagent profiles into a new `Manifest`. The result is a single object that describes the whole extension to the rest of the system.

**Call relations**: When the extension system asks this file what it provides, `manifest` is the handoff point. It creates `SkillSpec` entries for the skill folders and wraps them, along with the prepared coding and escalation profiles, inside a `Manifest` object that the core extension loader can consume.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/documents/ufo_ext_documents/manifest.py`

`config` · `startup`

Think of this file as the packing list for the documents extension. The actual skills live in folders under the local `skills/` directory, but the wider UFO system needs a clear list of what is inside the package before it can use them. This file provides that list.

It names the extension `documents`, gives it a version, points to the skills folder, and lists the skill folder names that belong to the extension. These include skills for Word documents, PowerPoint decks, spreadsheets, PDFs, visual design foundations, theme creation, document review, and prose drafting.

It also includes a `writing` subagent profile. A subagent is a focused helper agent with its own instructions and tools. Here, the writing subagent is meant to draft or edit the words that go inside documents, while the format-specific skills take care of the artifact itself.

The important behavior is that `manifest()` packages all of this into a standard `Manifest` object. The UFO loader can then read that object and register the extension’s skills and subagent. In everyday terms, this file does not write documents itself; it labels the tools in the toolbox so the rest of the system can find and load them when needed.

#### Function details

##### `manifest`  (lines 37–43)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the official description of the documents extension. The system uses this to discover the extension’s name, version, bundled skills, and writing subagent.

**Data flow**: It starts with constants in this file: the extension name, version, skills directory, skill names, and imported writing profile. It turns each skill name into a `SkillSpec`, which is a small description pointing at that skill’s folder. It then puts the name, version, subagent profile, and skill descriptions into a `Manifest` object and returns it to the caller.

**Call relations**: When the extension loader asks this package what it contains, this function is the answer. It creates `SkillSpec` entries for each local skill folder, then hands those entries to `Manifest` so the broader UFO system can register the skills and make the writing subagent available.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/research/ufo_ext_research/manifest.py`

`config` · `startup`

This file is the research pack’s “contents label.” When the larger application starts, it needs to know what an extension contributes before it can let an agent use it. This manifest answers that question for the research extension.

It gathers together several pieces: web research tools, a wide-research delegation tool, two research subagent profiles, a prompt section named “web,” skill folders that can be loaded when needed, and a conversation slot for storing source information. The prompt section is read from a Markdown file next to this code, so the agent can be given consistent instructions about web research without hard-coding that text inside the main application.

A key detail is the declared requirement: `search_providers`. In plain terms, this extension does not bring its own search engine or credentials. It expects the deployment to provide one. By declaring that requirement here, the system can fail early at startup if research is enabled without search being configured, instead of surprising the user later when the first search is attempted.

Think of this file like a packing list handed to the host application: “Here are the tools, helpers, instructions, and dependencies I need in order to provide research features.”

#### Function details

##### `manifest`  (lines 28–38)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the research extension’s manifest, which is the object the host system reads to discover what this extension provides. Someone would use it when loading extensions so the research tools, subagents, prompt section, skills, and requirements become visible to the rest of the system.

**Data flow**: It starts with constants and imported objects already defined in the file: the extension name and version, the tool list, subagent profiles, prompt text, skill folder names, source-storage slot, and required search-provider capability. It wraps the prompt text in a `PromptSection`, turns each skill folder into a `SkillSpec`, and then places everything into a `Manifest`. The result is a single manifest object that describes the whole research pack; it does not perform a search or contact any outside service itself.

**Call relations**: During extension loading, the wider system calls `manifest` to ask this file what it contributes. Inside that answer, it creates a `PromptSection` for the web-research instructions, creates `SkillSpec` entries for the research skill folders, and hands all of that to `Manifest` so the host can register the tools, subagents, prompt material, conversation slot, and dependency requirement together.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Skill creation package
Package marker and orchestration manifest for saving, loading, searching, and indexing user-authored skills.

### `extensions/skill_create/ufo_ext_skill_create/__init__.py`

`other` · `import/package discovery`

This is a small package marker file. In Python, an `__init__.py` file is what lets a folder be imported as a package, meaning other parts of the project can refer to this extension by name. Here, the file does not define any code, functions, or classes. Its main value is informational: the docstring says this package is about skills created or owned by an agent, plus skills that exist temporarily during a single runtime turn.

In plain terms, this folder is likely meant to group code for teaching or giving the agent new abilities. Think of it like the label on a toolbox drawer: the drawer may contain many tools elsewhere, but this label tells you what kind of tools belong inside. Without this file, depending on the Python version and import setup, the folder might be less clearly recognized as a package, and newcomers would lose a quick clue about the folder’s purpose.


### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `startup, object requests, skill loading, scheduled indexing`

A “skill” here is a small bundle of text files, led by SKILL.md, that teaches an agent how to do something later. This file is the front door for those workspace-owned skills. Without it, members could not create persistent skills, the portal could not show them, agents could not load them at runtime, and saved skills would not be searchable.

The file defines the shape of a saved skill: files can be supplied directly, copied from a workspace path, or kept unchanged by referring to their stored SHA-256 digest, which is a fingerprint of the file contents. It also defines SkillObjects, the object API used to list, read, save, and delete skills. When saving, it checks that file paths stay inside the skill folder, that the bundle is not too large, that referenced files really exist, and that all files are UTF-8 text.

The file also adds a keyword search tool that ranks all loadable skills by their short “routing cards,” not by exposing full file bodies. Finally, it registers a scheduled indexing job. That job embeds skill descriptions into a search index and carefully avoids marking a skill indexed if it changed while indexing was happening.

#### Function details

##### `_require_ext`  (lines 119–122)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This is a safety check that makes sure the extension context is present before skill code touches workspace storage or services. The extension context is the bundle of runtime information, such as the current workspace and connected backends.

**Data flow**: It receives either an ExtensionContext or nothing. If the context exists, it returns it unchanged; if it is missing, it raises an error instead of letting later code fail in a confusing way.

**Call relations**: The SkillObjects methods call this before listing, reading, saving, deleting, or resolving skills. It acts like checking you have the right key before opening any workspace-owned cabinet.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 125–132)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every file path in a skill stays inside that skill’s own folder. It prevents a skill from pretending that outside files belong to it.

**Data flow**: It receives the skill name and the proposed skill specification. For each file path, it compares the path with the allowed skill root; if any path tries to escape, it turns that into a clear validation error. It returns nothing when all paths are safe.

**Call relations**: SkillObjects.apply calls this before saving. It relies on the shared sandbox path-checking helpers so saved skill bundles cannot smuggle in unrelated workspace paths.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_root).


##### `_text`  (lines 135–141)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This makes sure a skill file is plain UTF-8 text. Skills in this system can bundle text files, not arbitrary binary files like images or executables.

**Data flow**: It receives a file path and raw bytes. It tries to decode the bytes as text; if decoding works, it returns the text, and if not, it raises a friendly validation error naming the file.

**Call relations**: SkillObjects._resolve calls this after it has gathered each file’s bytes. It is the last content check before the files are considered ready to save.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of saved workspace skills for normal object listing. It is used when a tool or UI wants the collection view rather than one full skill.

**Data flow**: It receives a tool context and a list query such as paging or filtering information. It gets the extension context, builds the current skill rows, and passes them through the shared object paging helper. It returns an ObjectPage.

**Call relations**: The object system calls this when someone lists objects of kind skill. It delegates the actual row-building to SkillObjects._rows and the paging format to object_page.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 151–162)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same kind of saved-skill listing for a signed-in member outside an agent turn, such as in a portal. Skills are workspace-owned, so the member identity does not narrow the set here.

**Data flow**: It receives an extension context, member information, admin status, and a list query. It requires the extension context, reads the saved skill rows for the workspace, applies paging, and returns an ObjectPage.

**Call relations**: Portal-style member views call this to show saved skills. Like SkillObjects.list, it uses SkillObjects._rows for the rows and object_page for the final page shape.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 164–165)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This retrieves the detail record for one saved skill. It returns safe metadata and file fingerprints rather than dumping file contents into the response.

**Data flow**: It receives a tool context and a skill name. It checks for the extension context, asks for that skill’s stored detail, and returns an ObjectDetail if found or null if not found.

**Call relations**: The object system calls this for object_get-style reads. It delegates the storage-facing work to SkillObjects._skill.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 167–185)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This gives a portal member both the list-row view and the detail view for one saved skill. It still keeps file bodies out of the response.

**Data flow**: It receives the extension context, skill name, member information, and admin status. It finds the matching row, then fetches the detailed spec; if either is missing, it returns null. If both exist, it returns a MemberObject containing both pieces.

**Call relations**: Member-facing pages call this when opening one skill. It combines SkillObjects._rows and SkillObjects._skill so the portal can show summary information beside the safe detailed spec.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 187–195)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the compact list entries for saved skills. Each row contains the name, a shortened description, and whether the skill is pinned.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore for the workspace’s skill listing, turns each stored item into an ObjectRow, trims the summary to the configured maximum length, and returns all rows as a tuple.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail use this whenever they need the collection-style view. It is the bridge from stored skill records to display rows.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 197–212)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the safe detailed object view for one saved skill. Instead of returning file contents, it returns each file’s digest and size so callers can keep unchanged files on later edits.

**Data flow**: It receives an ExtensionContext and a skill name. It reads the stored record; if none exists, it returns null. If found, it computes a SHA-256 digest and byte size for each file, wraps those in FileRef values, and returns an ObjectDetail with timestamps and generation information.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this for detail reads. It uses UserSkillStore for storage and FileRef/UserSkillSpec/ObjectDetail to shape the public response.

*Call graph*: called by 2 (get, member_detail); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 214–231)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This returns quick status facts about a saved skill, such as its description, file count, total bytes, and pinned flag. It also protects readers from seeing stale data by checking the expected generation.

**Data flow**: It receives a tool context, skill name, and expected generation value. It reads the stored record; if missing, it returns null. If the record exists but the generation does not match, it raises an error. Otherwise it returns a small dictionary of status values.

**Call relations**: The object workflow calls this after or around reads when it needs lightweight state. It uses UserSkillStore directly after checking the extension context.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects.apply`  (lines 233–256)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This saves a new or updated workspace skill. It enforces limits and safety rules before anything is written.

**Data flow**: It receives a tool context, skill name, proposed spec, previous spec, and expected generation. It checks the file count, verifies paths stay inside the skill, resolves file contents from inline text, workspace files, or stored digests, checks total size, and writes the result to UserSkillStore. It returns nothing on success.

**Call relations**: The manifest/object system calls this when a user applies a skill object change. It calls _contained_keys for path safety, _resolve for turning references into bytes, and then hands the final bundle to UserSkillStore.save.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 258–269)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved workspace skill, but only if the caller is not working from an outdated generation. That prevents accidental deletion after someone else has edited the skill.

**Data flow**: It receives a tool context, skill name, and expected generation. It reads the current record; if it exists and the generation differs, it raises an error. Then it asks the store to delete the skill.

**Call relations**: The object system calls this for delete operations. It uses UserSkillStore both to check the current generation and to perform the deletion.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 271–319)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns the mixed file values in a skill spec into actual bytes ready to save. It supports three cases: new inline text, copying from a workspace file, or keeping an already stored file by digest.

**Data flow**: It receives a tool context, skill name, and spec. It loads existing stored files for FileRef values, verifies their hashes, reads FileFrom paths through the sandbox using a small Python helper, decodes those results from base64, encodes inline strings, checks that every file is UTF-8 text, and returns a path-to-bytes dictionary.

**Call relations**: SkillObjects.apply calls this before saving. It talks to UserSkillStore for existing files, uses the sandbox to safely read workspace paths, and calls _text to reject non-text content.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `skill_search`  (lines 370–387)

```
async def skill_search(ctx: ToolContext, args: SkillSearchInput) -> ToolResult
```

**Purpose**: This is the tool action that searches loadable skills by keyword. It returns only short name-and-description lines, so it helps find a skill without exposing full skill bodies.

**Data flow**: It receives a tool context and search input containing a query and limit. It gets all skill cards from the current skill set, scores each card against the query with a lexical keyword scorer, keeps the best positive matches up to the limit, and returns them as text. If nothing matches, it returns a message saying how many skills were searched.

**Call relations**: The SKILL_SEARCH_TOOL uses this as its handler. It depends on the runtime skill collection in the tool context and the shared lexical_score function used elsewhere for routing skills.

*Call graph*: 3 external calls (__init__, __init__, lexical_score).


##### `_member_cards`  (lines 404–405)

```
async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]
```

**Purpose**: This returns the saved workspace skills as lightweight cards that can be considered for loading. A card is the short public face of a skill: mainly its name and description.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore for the workspace’s cards and returns them as a tuple.

**Call relations**: The manifest registers this with MemberSkillsSpec. The runtime calls it when it needs to know which member-authored skills are available.

*Call graph*: 1 external calls (__init__).


##### `_materialize_skill`  (lines 408–409)

```
async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one saved skill into its runtime form. Materializing means turning the stored files into the object the agent can actually use during a turn.

**Data flow**: It receives an ExtensionContext and a skill name. It asks UserSkillStore to materialize that one skill and returns a RuntimeSkill if it exists, or null if it does not.

**Call relations**: The manifest registers this with MemberSkillsSpec. The runtime calls it when a specific saved skill needs to be loaded.

*Call graph*: 1 external calls (__init__).


##### `_materialize_all_skills`  (lines 412–413)

```
async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads all saved workspace skills into runtime form. It is used when the runtime needs the whole workspace skill set rather than one named skill.

**Data flow**: It receives an ExtensionContext. It asks UserSkillStore to materialize every saved skill for the workspace and returns the resulting tuple.

**Call relations**: The manifest registers this with MemberSkillsSpec. The runtime calls it when preparing the full set of member-authored skills.

*Call graph*: 1 external calls (__init__).


##### `index_skills`  (lines 416–465)

```
async def index_skills(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job keeps saved skill descriptions searchable. It finds skills whose stored search-index digest is missing or out of date, embeds their routing text, and records them as indexed only if they have not changed meanwhile.

**Data flow**: It receives an ExtensionContext with database, index, and embedding backends. It verifies those backends exist, queries the database for stale skill cards, then tries to index each one. It counts successful settlements, logs per-skill failures, and raises an error if every attempted card failed.

**Call relations**: The JobSpec registered in manifest calls this on a schedule for workspaces that need indexing. It creates a TextChunker, reads stale rows through a transaction, and calls _index_card for each row.

*Call graph*: calls 2 internal fn (transaction, _index_card); 3 external calls (__init__, or_, select).


##### `_index_card`  (lines 468–505)

```
async def _index_card(ctx: ExtensionContext, index: IndexBackend, embed: EmbedClient, chunker: TextChunker, row: sa.Row) -> None
```

**Purpose**: This indexes one skill’s searchable card and then safely marks that exact version as indexed. It is careful about races, meaning cases where a skill is edited or deleted while indexing is happening.

**Data flow**: It receives the extension context, index backend, embedding client, text chunker, and a database row for one skill. It sends the skill name and shortened description through chunk-and-embed upsert. Then it updates indexed_digest only if the database still has the same digest. If the row disappeared, it deletes that skill’s index scope.

**Call relations**: index_skills calls this for each stale skill. It hands text to chunk_embed_upsert for indexing, uses a database transaction to settle the digest, and uses IndexBackend.delete to clean up if the skill was deleted mid-run.

*Call graph*: calls 2 internal fn (transaction, delete); called by 1 (index_skills); 4 external calls (__init__, select, update, chunk_embed_upsert).


##### `_skills_awaiting_index`  (lines 508–518)

```
def _skills_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query used to find workspaces with skills that need indexing. It does not run the query itself; it returns the query shape.

**Data flow**: It takes no inputs. It creates a SQL query selecting distinct workspace IDs where a skill has no indexed digest or its indexed digest differs from the current digest. The output is a selectable query object.

**Call relations**: manifest passes this query builder to owner_candidates for the indexing job. That lets the job scheduler choose only workspaces with stale skill index work.

*Call graph*: 2 external calls (or_, select).


##### `manifest`  (lines 521–541)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s registration function. It tells UFO what this extension is called and what tools, object types, built-in authoring skill, member-skill loaders, and scheduled jobs it provides.

**Data flow**: It takes no inputs. It constructs a Manifest containing the skill search tool, the saved-skill object kind, the create-skill built-in skill path, member skill callbacks, and the indexing job definition. It returns that Manifest to the extension loader.

**Call relations**: The extension system calls this at startup. It ties together the rest of the file: SkillObjects for object operations, skill_search for the search tool, member materialization callbacks for runtime loading, and index_skills for scheduled indexing.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).

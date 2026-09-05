# Connected accounts, hosted sites, and user-created skills  `stage-13.1.5`

This stage is shared support for workspace assets that people create or connect while using the system. It makes these assets behave like normal workspace objects, so they can be found, inspected, shared, protected, or removed in a consistent way.

The skill creation manifest is the front door for member-authored skills. A skill is a reusable instruction or capability saved by a workspace member. The manifest tells the system that skills can be created, edited, searched, loaded, indexed, and deleted. The skill store is the filing cabinet behind that door. It saves and retrieves skill content, lists available skills, and blocks unsafe cases such as bad names, duplicate names, accidental overwrites, or too many saved items.

The connectors object file does a similar job for third-party accounts, such as external services someone has linked. It separates the account connection from an agent’s permission to use it. The sites object file brings hosted websites into the same object system, so they can be listed, shared, made public or private, inspected, and deleted.

## Files in this stage

### User-created skills
Defines member-authored reusable skills as first-class workspace assets and provides the storage layer that safely persists and loads them.

### `extensions/skill_create/ufo_ext_skill_create/manifest.py`

`orchestration` · `extension startup, object operations, tool calls, and scheduled indexing`

A “skill” here is a saved bundle of text files, led by a SKILL.md file, that teaches the system how to do something later. This file is the front door for that feature. It tells the host application what object type exists, what tool is available, what built-in authoring skill should be loaded, and what background job keeps saved skills searchable.

The main class, SkillObjects, acts like a clerk at a records desk. It can show the saved skill list, fetch one skill’s details, save a new version, delete a skill, and report its status. It is careful not to echo full file contents back into normal object listings. Instead, it returns a SHA-256 digest, which is like a fingerprint for file content, so callers can keep unchanged files without sending the whole body again.

When saving, the file checks that paths stay inside the skill’s own folder, that all content is text, that the bundle is not too large, and that a stale edit does not overwrite someone else’s newer change. It can also read file content from the workspace sandbox, but only after validating and copying it into storage.

The file also adds keyword search across loadable skills and a scheduled indexing job. That job turns skill descriptions into searchable chunks so users can find relevant skills later.

#### Function details

##### `_require_ext`  (lines 119–122)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure an ExtensionContext is present before the code tries to use workspace services such as storage, database access, or indexing. It turns a missing context into a clear runtime error instead of a confusing later failure.

**Data flow**: It receives an optional extension context. If the value is present, it returns that same context unchanged. If it is missing, it stops the operation by raising an error.

**Call relations**: The object methods call this at the start of list, get, save, delete, status, and portal-facing reads. It is the shared checkpoint that ensures those flows are really running inside a workspace extension.

*Call graph*: called by 8 (_resolve, apply, delete, get, list, member_detail, member_page, status).


##### `_contained_keys`  (lines 125–132)

```
def _contained_keys(name: str, spec: UserSkillSpec) -> None
```

**Purpose**: This checks that every file path in a proposed skill stays inside that skill’s own folder. It prevents a saved skill from smuggling in paths that point outside its allowed area, like using “../” to escape a folder.

**Data flow**: It receives the skill name and the proposed skill specification. It computes the allowed root for that skill, then checks each file key against that root. It returns nothing when all paths are safe, and raises a clear error when a path is outside the skill.

**Call relations**: SkillObjects.apply calls this before saving. It relies on skill_root to find the skill’s allowed directory and contained_relative to enforce the boundary.

*Call graph*: called by 1 (apply); 2 external calls (contained_relative, skill_root).


##### `_text`  (lines 135–141)

```
def _text(path: str, content: bytes) -> str
```

**Purpose**: This makes sure a stored skill file is UTF-8 text, not binary data such as an image or archive. Skills are meant to bundle readable text files only.

**Data flow**: It receives a path and raw bytes. It tries to decode the bytes as text. If decoding works, it returns the decoded string; if not, it raises an error that names the bad file.

**Call relations**: SkillObjects._resolve calls this after it has gathered file bytes from inline text, saved content, or workspace files. It is the final text-only check before the bytes are accepted for saving.

*Call graph*: called by 1 (_resolve).


##### `SkillObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of saved workspace skills for normal object listing. It gives callers names, short summaries, and visible fields such as whether a skill is pinned.

**Data flow**: It receives a tool context and a list query. It checks that the extension context exists, reads all skill rows, and then applies paging and filtering rules through object_page. The result is an ObjectPage ready to show to the caller.

**Call relations**: This is called when the object system lists skill objects during a tool turn. It delegates the actual row-building to SkillObjects._rows and hands the rows to the shared object_page helper.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.member_page`  (lines 151–162)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the same kind of saved-skill listing for a signed-in member using the portal outside an agent turn. It reinforces that skills belong to the workspace, not to one person or one agent.

**Data flow**: It receives an extension context, member information, admin status, and a query. It requires the extension context, loads the workspace’s saved skill rows, and returns a paged ObjectPage. The member_id and admin inputs are part of the portal interface, but this listing is workspace-wide.

**Call relations**: The member-facing object API calls this when the portal needs a page of skills. It uses SkillObjects._rows for the shared row format and object_page for paging.

*Call graph*: calls 2 internal fn (_rows, _require_ext); 1 external calls (object_page).


##### `SkillObjects.get`  (lines 164–165)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This fetches the detail for one saved skill during a tool turn. It returns metadata and file fingerprints, not the raw file bodies.

**Data flow**: It receives a tool context and a skill name. It verifies the extension context, asks SkillObjects._skill for the stored detail, and returns either that detail or None if no such skill exists.

**Call relations**: The object system calls this when a caller asks for one skill object. It hands the real lookup work to SkillObjects._skill.

*Call graph*: calls 2 internal fn (_skill, _require_ext).


##### `SkillObjects.member_detail`  (lines 167–185)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[UserSkillSpec] | None
```

**Purpose**: This returns one saved skill in the shape the member portal expects: a list row plus object detail. Like get, it keeps file content out of the response and uses digests instead.

**Data flow**: It receives an extension context, skill name, member information, and admin status. It loads the workspace rows, finds the matching row, then loads the detailed skill record. If both exist, it combines them into a MemberObject; otherwise it returns None.

**Call relations**: The portal calls this when a signed-in member opens a saved skill. It reuses SkillObjects._rows for the list-side information and SkillObjects._skill for the detail-side information.

*Call graph*: calls 3 internal fn (_rows, _skill, _require_ext); 1 external calls (__init__).


##### `SkillObjects._rows`  (lines 187–195)

```
async def _rows(self, ext: ExtensionContext) -> tuple[ObjectRow, ...]
```

**Purpose**: This builds the lightweight list entries for all saved skills in the workspace. Each row includes the skill name, a shortened description, and whether it is pinned.

**Data flow**: It receives an extension context. It asks UserSkillStore for the saved skill listing, converts each stored listing item into an ObjectRow, and returns all rows as a tuple.

**Call relations**: SkillObjects.list, SkillObjects.member_page, and SkillObjects.member_detail all use this so normal tool views and portal views show the same skill list information.

*Call graph*: called by 3 (list, member_detail, member_page); 2 external calls (__init__, __init__).


##### `SkillObjects._skill`  (lines 197–212)

```
async def _skill(self, ext: ExtensionContext, name: str) -> ObjectDetail[UserSkillSpec] | None
```

**Purpose**: This builds the detailed object view for one saved skill without exposing the actual file contents. It replaces each file body with a SHA-256 digest and byte size.

**Data flow**: It receives an extension context and a skill name. It asks UserSkillStore for the saved record. If none exists, it returns None. If found, it creates a UserSkillSpec containing FileRef entries for each stored file, plus pinned status and object timestamps/generation.

**Call relations**: SkillObjects.get and SkillObjects.member_detail call this when they need the detailed view of a skill. It is the place where stored bytes are deliberately converted into safe file references.

*Call graph*: called by 2 (get, member_detail); 5 external calls (__init__, __init__, __init__, __init__, sha256).


##### `SkillObjects.status`  (lines 214–231)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports a compact status summary for a saved skill, such as its description, file count, total size, and pinned flag. It also checks that the caller is looking at the expected version.

**Data flow**: It receives a tool context, a skill name, and an expected generation identifier. It loads the record from UserSkillStore. If the skill is missing, it returns None. If the generation does not match, it raises an error. Otherwise it returns a small dictionary of status values.

**Call relations**: The object system can call this after or during object operations to confirm what exists. It uses _require_ext to access the workspace store and uses the generation check to avoid reporting stale information as current.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects.apply`  (lines 233–256)

```
async def apply(self, ctx: ToolContext, name: str, spec: UserSkillSpec, old: UserSkillSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This saves a new or updated skill into the workspace. It enforces size limits, path safety, text-only files, and generation checks so one member’s edit does not accidentally overwrite another’s.

**Data flow**: It receives a tool context, skill name, new specification, optional old specification, and expected generation. It checks the file count, verifies paths stay inside the skill, resolves all file values into bytes, checks total size, and then asks UserSkillStore to save the skill with its pinned setting and generation guard.

**Call relations**: The manifest object system calls this when a caller applies a skill object change. It uses _contained_keys for path safety, _resolve to turn references into real content, and UserSkillStore.save to persist the final skill.

*Call graph*: calls 3 internal fn (_resolve, _contained_keys, _require_ext); 1 external calls (__init__).


##### `SkillObjects.delete`  (lines 258–269)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This removes a saved skill from the workspace, but only if the caller’s expected version still matches. That protects against deleting a skill that someone else changed after the caller last read it.

**Data flow**: It receives a tool context, skill name, and expected generation. It loads the current record. If the skill exists and its generation differs from the expected one, it raises an error. Otherwise it asks UserSkillStore to delete the skill.

**Call relations**: The object system calls this for delete operations. It uses _require_ext to get workspace access and UserSkillStore for both the version check and the actual deletion.

*Call graph*: calls 1 internal fn (_require_ext); 1 external calls (__init__).


##### `SkillObjects._resolve`  (lines 271–319)

```
async def _resolve(self, ctx: ToolContext, name: str, spec: UserSkillSpec) -> dict[str, bytes]
```

**Purpose**: This turns the mixed file values in a skill specification into the actual bytes that will be saved. A file can be inline text, a reference to a workspace file, or a digest reference meaning “keep the already stored content.”

**Data flow**: It receives a tool context, skill name, and skill specification. It first loads existing stored files so FileRef values can be verified by digest. It then reads any FileFrom paths from the workspace sandbox using a small Python helper, decodes the returned base64 content, and combines those bytes with inline strings and kept files. Each final file is checked as text, and the result is a dictionary from skill-relative path to bytes.

**Call relations**: SkillObjects.apply calls this before saving. It uses UserSkillStore to retrieve old file bodies, the sandbox to safely read workspace files, hashlib to verify digests, base64 and JSON to transfer file bytes, and _text to reject non-text files.

*Call graph*: calls 2 internal fn (_require_ext, _text); called by 1 (apply); 7 external calls (__init__, b64decode, sha256, dumps, loads, quote, workspace_path).


##### `skill_search`  (lines 370–387)

```
async def skill_search(ctx: ToolContext, args: SkillSearchInput) -> ToolResult
```

**Purpose**: This tool searches all currently loadable skills by keyword and returns matching name-and-description lines. It helps a caller find a skill even when that skill is not already visible in a shorter list.

**Data flow**: It receives a tool context and search input containing a query and result limit. It gets every skill card from the current skill set, scores each one with a simple word-based matcher, keeps the highest scoring matches, and returns either formatted lines or a “no matches” message with the total searched.

**Call relations**: SKILL_SEARCH_TOOL uses this as its handler when the skill_search tool is invoked. It depends on the same lexical scorer used elsewhere for skill routing, and returns ToolResult content rather than loading skill bodies.

*Call graph*: 3 external calls (__init__, __init__, lexical_score).


##### `_member_cards`  (lines 404–405)

```
async def _member_cards(ctx: ExtensionContext) -> tuple[SkillCard, ...]
```

**Purpose**: This returns the routing cards for member-authored skills in the workspace. A routing card is the small name-and-description summary the system uses to decide what skills might be relevant.

**Data flow**: It receives an extension context. It asks UserSkillStore for the saved skill cards and returns them as a tuple.

**Call relations**: The manifest registers this with MemberSkillsSpec so the runtime can include workspace-authored skills when building the set of available skill cards.

*Call graph*: 1 external calls (__init__).


##### `_materialize_skill`  (lines 408–409)

```
async def _materialize_skill(ctx: ExtensionContext, name: str) -> RuntimeSkill | None
```

**Purpose**: This loads one saved workspace skill into the runtime form the agent can actually use. “Materialize” here means turning stored files into a RuntimeSkill object.

**Data flow**: It receives an extension context and a skill name. It asks UserSkillStore to materialize that skill and returns either the RuntimeSkill or None if it cannot be loaded.

**Call relations**: The manifest registers this callback with MemberSkillsSpec. The runtime calls it when a particular workspace skill needs to be loaded for a turn.

*Call graph*: 1 external calls (__init__).


##### `_materialize_all_skills`  (lines 412–413)

```
async def _materialize_all_skills(ctx: ExtensionContext) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads all saved workspace skills into runtime form. It is used when the runtime wants the whole workspace-authored skill set, not just one named skill.

**Data flow**: It receives an extension context. It asks UserSkillStore to materialize every saved skill and returns the resulting tuple of RuntimeSkill objects.

**Call relations**: The manifest registers this with MemberSkillsSpec so the runtime has a single callback for loading all member-authored skills when needed.

*Call graph*: 1 external calls (__init__).


##### `index_skills`  (lines 416–465)

```
async def index_skills(ctx: ExtensionContext) -> None
```

**Purpose**: This scheduled job keeps saved skills searchable by updating the index for any skill whose description has changed or has never been indexed. An index is like a catalog: it lets search find relevant text quickly.

**Data flow**: It receives an extension context. It checks that both the search index backend and embedding client are configured, then queries the database for skills with stale index digests. For each stale row, it calls _index_card. It logs individual failures and continues, but if every attempted row failed, it raises the last error so the job is reported as failed.

**Call relations**: The manifest registers this as the skill_index job. It opens a database transaction to find stale work, creates a TextChunker for splitting text, and calls _index_card for each skill that needs indexing.

*Call graph*: calls 2 internal fn (transaction, _index_card); 3 external calls (__init__, or_, select).


##### `_index_card`  (lines 468–505)

```
async def _index_card(ctx: ExtensionContext, index: IndexBackend, embed: EmbedClient, chunker: TextChunker, row: sa.Row) -> None
```

**Purpose**: This indexes one skill’s routing card and then marks that exact version as indexed. It is careful not to mark a newer changed skill as indexed by accident.

**Data flow**: It receives the extension context, index backend, embedding client, text chunker, and one database row. It builds searchable text from the skill name and description, sends it through chunk_embed_upsert, then updates the database only if the skill’s digest still matches the row that was indexed. If the skill was deleted during indexing, it removes that skill’s index scope.

**Call relations**: index_skills calls this for each stale skill. It hands text to chunk_embed_upsert for embedding and storage, uses a guarded database update to settle indexed_digest, and calls the index backend’s delete method if the row disappeared mid-process.

*Call graph*: calls 2 internal fn (transaction, delete); called by 1 (index_skills); 4 external calls (__init__, select, update, chunk_embed_upsert).


##### `_skills_awaiting_index`  (lines 508–518)

```
def _skills_awaiting_index() -> sa.Select[tuple[UUID]]
```

**Purpose**: This builds the database query that finds workspaces with skills needing indexing. It identifies workspace owners where at least one saved skill is unindexed or has changed since it was indexed.

**Data flow**: It takes no runtime input. It returns a SQLAlchemy Select object that asks for distinct workspace IDs from the user_skill table where indexed_digest is missing or differs from the current digest.

**Call relations**: manifest passes this query builder to owner_candidates when defining the scheduled job. That lets the job system decide which workspaces should run index_skills.

*Call graph*: 2 external calls (or_, select).


##### `manifest`  (lines 521–541)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension’s registration form. It tells the host application the extension name and version, what tool and object kind it provides, what built-in authoring skill it ships, how to load member skills, and what job to schedule.

**Data flow**: It takes no input. It creates and returns a Manifest containing the skill_search tool, the skill object definition, the bundled create-skill SkillSpec, member-skill callbacks, and the scheduled indexing JobSpec.

**Call relations**: The host calls this when loading the extension. The returned Manifest wires together the rest of this file: SKILL_SEARCH_TOOL for searching, SKILL_OBJECT for saved skill CRUD operations, member skill callbacks for runtime loading, and index_skills for background indexing.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling and skill loading`

A “skill” here is a small bundle of files, including the text that describes what the skill does. This file stores those bundles in a database, one row per skill name in a workspace. Without it, user-created skills would not survive across turns, two edits could overwrite each other by accident, and a user skill could pretend to be a built-in skill by reusing its name.

The main piece is UserSkillStore. It always works in the current workspace, found from the running agent context. Saving is careful: it checks that the name is a safe lowercase slug, parses the files to make sure they really form a valid skill, encodes the files as text for the database, and records extra “routing card” information such as description, dependencies, agent names, and whether the skill is pinned. It also uses a generation value, like a numbered ticket on a document, so an edit is accepted only if it was based on the current version.

The file also supports lightweight views. cards() and listing() read only summary information. record() and files() read the full stored bundle. materialize() turns stored files back into a runnable skill. materialize_all() does that for every skill, but skips broken rows so one damaged skill does not hide the rest. delete() also cleans up search index data when available.

#### Function details

##### `_save_lock_key`  (lines 54–56)

```
def _save_lock_key(workspace_id: UUID) -> int
```

**Purpose**: This helper turns a workspace ID into a stable database lock number. The lock lets saves for the same workspace line up one at a time, like people taking turns at a counter.

**Data flow**: It takes a workspace UUID, turns it into text, hashes it with SHA-256, takes the first part of that hash, and converts it into an integer. The result is a repeatable lock key: the same workspace gets the same key, while different workspaces are very unlikely to share one.

**Call relations**: UserSkillStore.save calls this before writing to the database. The save flow uses the key when PostgreSQL is available so competing saves in the same workspace cannot slip past version and limit checks at the same time.

*Call graph*: called by 1 (save); 1 external calls (sha256).


##### `UserSkillStore.save`  (lines 123–238)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str], pinned: bool=False, generation: UUID | None=None) -> RuntimeSkill
```

**Purpose**: This saves a new or edited workspace skill. It protects the workspace from unsafe names, invalid skill files, name collisions with built-in skills, too many skills, too many pinned skills, and stale edits based on an old version.

**Data flow**: It receives a skill name, a map of file paths to bytes, the names already owned by core or pack skills, a pinned flag, and optionally the generation that the caller previously read. It checks the name, parses the files into a runtime skill, encodes the files for storage, computes a digest to identify the exact content, and opens a database transaction. Inside that transaction it compares the caller’s generation with the stored generation, checks workspace limits, then either inserts a new row or updates the existing row with a fresh generation. It returns the parsed RuntimeSkill; it also changes the database if the save is accepted.

**Call relations**: This is the central write path for user skills. It calls _save_lock_key to serialize PostgreSQL saves per workspace, _count to enforce the total skill cap, and _pinned_count to enforce the pinned skill cap. It also constructs StoredSkill for the saved bundle and raises the custom errors in this file when a caller tries an unsafe or stale save.

*Call graph*: calls 3 internal fn (_count, _pinned_count, _save_lock_key); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, b64encode, sha256, dumps, cast (+6 more)).


##### `UserSkillStore.cards`  (lines 240–281)

```
async def cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: This returns compact skill cards for every saved skill in the current workspace. These cards are used when the system needs to know what skills exist and how they should be routed, without loading every file in every skill.

**Data flow**: It reads the current workspace ID, queries the database for each skill’s name, description, dependencies, agent list, and pinned state, then turns each valid row into a SkillCard. Dependency and agent fields are stored as JSON text, so it decodes them before building the card. Rows with an empty description are skipped and logged because they cannot describe a useful route.

**Call relations**: Callers use this when they need a fast summary rather than the full skill contents. Inside the flow it asks agent_current for the workspace, uses SQLAlchemy to read the database, uses json.loads to unpack stored lists, and hands back SkillCard objects for the rest of the system to route with.

*Call graph*: 4 external calls (__init__, loads, select, agent_current).


##### `UserSkillStore.listing`  (lines 283–307)

```
async def listing(self) -> tuple[SkillListing, ...]
```

**Purpose**: This returns the simple list shown to someone browsing saved skills: name, description, and whether each skill is pinned. It avoids loading the full file bundles.

**Data flow**: It gets the current workspace ID, reads matching database rows ordered by name, and builds one SkillListing object for each row that has a description. The output is a tuple of these listing objects. It does not change the database.

**Call relations**: This is the lightweight display path for workspace skills. It relies on agent_current to know which workspace to show and SQLAlchemy to fetch the rows, then hands the results into SkillListing objects for presentation by higher-level code.

*Call graph*: 3 external calls (__init__, select, agent_current).


##### `UserSkillStore.record`  (lines 309–340)

```
async def record(self, name: str) -> SkillRecord | None
```

**Purpose**: This fetches the full saved record for one named skill. It is used when a caller needs the actual files plus metadata such as the generation needed for safe editing.

**Data flow**: It receives a skill name, reads the current workspace ID, and looks up that one row in the database. If there is no row, it returns None. If there is a row, it validates the stored JSON bundle, decodes each base64 text value back into raw file bytes, and returns a SkillRecord containing the files, description, generation, pinned flag, and timestamps.

**Call relations**: This is the detailed read path that pairs with UserSkillStore.save: callers can read a record, keep its generation, and send that generation back when saving an edit. It uses SQLAlchemy for the lookup, StoredSkill validation for the stored bundle, base64 decoding for the file bytes, and SkillRecord to package the result.

*Call graph*: 4 external calls (__init__, b64decode, select, agent_current).


##### `UserSkillStore.materialize`  (lines 342–349)

```
async def materialize(self, name: str) -> RuntimeSkill | None
```

**Purpose**: This turns one saved skill back into a RuntimeSkill that the system can actually use. If the named skill does not exist, it returns None.

**Data flow**: It receives a skill name and asks files() for that skill’s stored file bytes. If files() finds nothing, materialize() returns None. Otherwise it passes the files into parse_skill_content, which checks and interprets the bundle, and returns the resulting RuntimeSkill.

**Call relations**: This function is a small bridge between storage and runtime use. It delegates the database reading and base64 decoding to UserSkillStore.files, then hands the recovered files to parse_skill_content so the skill becomes usable by the skill system.

*Call graph*: calls 1 internal fn (files); 1 external calls (parse_skill_content).


##### `UserSkillStore.materialize_all`  (lines 351–379)

```
async def materialize_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This loads every saved workspace skill into runtime form. It is tolerant of damage: if one stored skill is corrupt, it logs that problem and continues loading the others.

**Data flow**: It reads the current workspace ID, fetches every saved skill name and stored content bundle, and loops through the rows. For each row it validates the JSON bundle, decodes base64 file contents back into bytes, and parses the files into a RuntimeSkill. Good skills are collected into the returned tuple. Broken rows are not returned; they are logged instead.

**Call relations**: This is the bulk loading path. It uses agent_current and SQLAlchemy to read all rows for the workspace, then uses StoredSkill validation, base64 decoding, and parse_skill_content for each skill. Unlike materialize(), it catches failures so one bad saved bundle does not stop the rest from loading.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 381–396)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: This returns the raw files for one saved workspace skill. It is useful when code wants the bundle itself rather than a parsed RuntimeSkill.

**Data flow**: It receives a skill name, finds the current workspace, and queries the database for that skill’s stored content. If no row exists, it returns None. If a row exists, it validates the stored JSON and decodes each base64 string into bytes, returning a dictionary from file path to file content.

**Call relations**: UserSkillStore.materialize calls this as its first step. In that story, files() does the storage-specific work, and materialize() then parses the returned bytes into a usable skill.

*Call graph*: called by 1 (materialize); 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 398–420)

```
async def delete(self, name: str) -> None
```

**Purpose**: This removes one saved skill from the workspace. If a search index exists, it also removes the skill’s indexed material so old search chunks do not linger after the skill is gone.

**Data flow**: It receives a skill name and reads the current workspace ID. If indexing is available, it first marks the row as needing no current indexed digest, then asks the index to delete the scope for this skill. After that, it deletes the database row. The database and index are changed; nothing is returned.

**Call relations**: This is the cleanup path for a skill. It uses agent_current to target the workspace, SQLAlchemy update and delete operations for the database, and IndexScope to tell the external index exactly which skill-owned material to remove.

*Call graph*: 4 external calls (__init__, delete, update, agent_current).


##### `UserSkillStore._count`  (lines 422–430)

```
async def _count(self, connection: AsyncConnection) -> int
```

**Purpose**: This counts how many user skills are currently saved in the workspace. save() uses it to stop a workspace from growing beyond the configured skill limit.

**Data flow**: It receives an open database connection, reads the current workspace ID, runs a count query over the user_skill table for that workspace, and returns the integer count. It only reads data; it does not change anything.

**Call relations**: UserSkillStore.save calls this while it is already inside the write transaction. That placement matters because the limit check and the eventual insert happen as one protected sequence rather than as separate, race-prone steps.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


##### `UserSkillStore._pinned_count`  (lines 432–444)

```
async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int
```

**Purpose**: This counts how many other skills are pinned in the workspace. save() uses it when a caller tries to pin a skill, so the workspace does not exceed the pinned-skill limit.

**Data flow**: It receives an open database connection and the name to exclude from the count. It reads the current workspace ID, counts rows in that workspace where pinned is true and the name is not the excluded name, and returns that number. The exclusion lets an already pinned skill be saved again without counting itself as a new pin.

**Call relations**: UserSkillStore.save calls this during the same protected save transaction, but only when a save would add a new pin. The result decides whether save() can continue or must raise PinnedSkillLimit.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


### Connected and hosted assets
Exposes third-party account connections and hosted websites through the shared workspace object system for inspection, sharing, permissioning, and deletion.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling`

A connected account is not just a setting. It represents consent with an outside provider, such as a service account, and it may be usable by one or more agents. This file turns that idea into two object types. A `connection` is the member-owned account link itself. A `connector_grant` is one agent's permission to use that link. The split matters because revoking one agent's access should not always disconnect the whole account for everyone.

The file reads connection and grant summaries from the grants layer, then presents them as normal workspace objects with names, short summaries, details, status fields, owners, and links to related objects. Think of it like a library card system: the connection is the library membership, while a grant is a permission slip for one helper to borrow books with it.

It also enforces safety rules. New provider connections cannot be created by editing these objects, because connecting an account requires a separate third-party consent flow. Deleting a connection disconnects it everywhere. Deleting a grant only removes one agent's access. Updating a grant is narrowly allowed for changing whether it is shared or private, and only with the right speaker and ownership/admin checks supplied by the surrounding object framework.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the provider name, such as the outside service the account belongs to. It is a type-level promise rather than working code.

**Data flow**: An object that claims to be an account summary is expected to already contain provider information → this property describes how the rest of the file can read it → callers can treat different summary shapes alike as long as they provide a provider string.

**Call relations**: It supports `_named`, which needs a provider and account ID from each row so it can build stable object names for both connections and grants.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This protocol property says that any account summary used here must expose the provider's account identifier. It lets the naming helper work with both connection summaries and grant summaries.

**Data flow**: An account summary object already has an account ID → this property describes the required way to read it → naming code can combine it with the provider to identify the account.

**Call relations**: It is part of the small shared contract used by `_named`, which is then used when listing connection objects and connector grant objects.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper turns a group of account-like summary rows into a dictionary keyed by the standard object name for each account. It gives connections and grants consistent names based on provider and account ID.

**Data flow**: It receives summary rows that each have a provider and account ID → it asks `account_object_name` to make the official name for each row → it returns a lookup table from that name to the original row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` use it while building object listings, so the two object types name accounts in the same way.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–86)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list view for connection objects a member can read. Each row tells the object system the connection's name, who owns it, and a human-readable summary.

**Data flow**: It reads current connection summaries from the grants layer → it gives each connection a standard account-based name, summary text, and generated owner record → it returns rows the workspace object system can show in listings.

**Call relations**: The member-readable object framework calls this when it needs to list `connection` objects. It relies on `_named` for consistent names and on `connection_summaries` for the live connection data.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 88–106)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the detailed object record for one connected account. It is used when someone asks to inspect a specific `connection` object.

**Data flow**: It receives an object name and owner record, especially the stored generation ID → it looks through current connection summaries for the matching connection → it returns provider/account details plus creation and update times, or nothing if the connection no longer exists.

**Call relations**: The object framework calls this after a connection has been selected from the object list. It reads from `connection_summaries` and packages the result as an `ObjectDetail` using `ConnectionSpec`.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 108–123)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces live status information for a connected account, such as owner, sharing state, host, and which agents are using it. It is meant for status-style inspection rather than editing.

**Data flow**: It receives the current tool context, object name, and owner generation ID → it finds the matching connection summary → it returns a plain dictionary of status fields, or nothing if the connection is gone.

**Call relations**: The object/tooling layer calls this when it needs runtime status for a `connection`. It reads directly from `connection_summaries` and does not change anything.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 125–133)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses attempts to create or change a connection through the generic object apply path. A real connection requires a third-party consent flow, so users must use `connect_account` instead.

**Data flow**: It receives the requested connection spec and any old object state → instead of applying changes, it raises a clear 'not supported' error → no connection data is created or changed.

**Call relations**: The object framework calls this when someone tries to apply a `connection` object. It stops that flow immediately with `VerbNotSupported`, pointing users toward the safer account-connection path.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 135–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account, which also removes it from every agent that depended on it. It requires an available grants service and a real speaking member, because disconnecting an account is a sensitive action.

**Data flow**: It receives the tool context, object name, and owner generation ID → it checks that grant operations are available and that a member is speaking → it asks the grants layer to disconnect the connection as that member, and raises an error if the connection changed during the attempt.

**Call relations**: The object framework calls this when a permitted user deletes a `connection` object. It hands the actual disconnect work to `ctx.grants.disconnect`, while this method enforces the local preconditions and reports conflicts.

*Call graph*: 1 external calls (__init__).


##### `ConnectorGrantObjects._admin_can_apply`  (lines 156–157)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This defines the one grant update that an admin is allowed to perform without being the connection owner: making a shared grant private. It prevents admins from using the same path to broaden access.

**Data flow**: It receives the old grant spec and the requested new spec → it checks whether the old grant was shared and the only requested change is `shared` becoming false → it returns true for that narrow case and false otherwise.

**Call relations**: The member-readable object framework uses this permission hook when deciding whether an admin may apply an update to a `connector_grant`. It uses `model_copy` to compare the requested change safely against the old spec.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 159–176)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This builds the list view for connector grant objects a member can read. Each row represents one agent's access to one connected account.

**Data flow**: It reads grant summaries from the grants layer → it creates a standard name, summary text, and generated owner record for each grant, including whether the grant is shared → it returns rows the object system can list.

**Call relations**: The object framework calls this when listing `connector_grant` objects. It mirrors `ConnectionObjects._member_rows`, but reads `grant_summaries` because it is listing agent access edges rather than base account connections.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 178–219)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the detailed object record for one agent's grant to use a connected account. It also adds links that explain what the grant is scoped to and, when private, which connection it opens.

**Data flow**: It receives an object name and owner generation ID → it finds the matching grant summary → it builds a spec with provider, account ID, and shared/private state, plus timestamps and relationship links → it returns that detail, or nothing if the grant no longer exists.

**Call relations**: The object framework calls this when someone inspects a `connector_grant`. It reads from `grant_summaries`, links the grant to its agent, and for private grants links back to the underlying `connection` object using `account_object_name`.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 221–236)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces live status information for one connector grant, such as owner, host, agent, and shared/private state. It gives a quick operational view without changing access.

**Data flow**: It receives context, object name, and owner generation ID → it finds the matching grant summary → it returns a plain dictionary of status values, or nothing if the grant no longer exists.

**Call relations**: The object/tooling layer calls this when it needs status for a `connector_grant`. It reads from `grant_summaries` and leaves the grant unchanged.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 238–288)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This is the main editing path for connector grants. It can attach an existing connection to an agent, or change a grant between shared and private, but it refuses attempts to create a brand-new provider connection here.

**Data flow**: It receives the requested grant spec, the old spec if one exists, and the owner record if one exists → for a new grant, it checks for a grants service and speaking member, then asks the grants layer to attach an existing connection to the current conversation's agent → for an existing grant, it reloads the current grant, verifies that only the `shared` setting is changing, and asks the grants layer to update that sharing flag → it returns nothing on success and raises clear errors for missing credentials, missing speaker, unavailable connections, unsupported changes, or race-like changes.

**Call relations**: The object framework calls this when applying a `connector_grant` create or update. It hands real access changes to `ctx.grants.attach` or `ctx.grants.set_shared`, while using `grant_summaries`, `ConnectorGrantSpec`, and `model_copy` to verify that the request is still valid and narrowly scoped.

*Call graph*: 5 external calls (__init__, __init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 290–300)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent's access to a connected account without disconnecting the account itself. It is the focused delete action for a `connector_grant`.

**Data flow**: It receives the tool context, object name, and owner generation ID → it checks that grant operations are available and that a member is speaking → it asks the grants layer to revoke that one grant as the speaking member, and raises an error if the grant changed during revocation.

**Call relations**: The object framework calls this when a permitted user deletes a `connector_grant`. It delegates the actual revocation to `ctx.grants.revoke`, leaving the underlying connection and other agents' grants intact.

*Call graph*: 1 external calls (__init__).


### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling for site object listing, reading, visibility changes, and deletion`

When someone deploys a website, the system needs more than a running port. It needs a stable workspace object so people can find the site later, see who owns it, open its link, change who can view it, or unhost it. This file defines that object kind: `site`.

A site object name is made from the site’s own name plus a short fingerprint of the conversation that created it. This matters because two different conversations can both deploy a site called `dashboard`, and they must not collide.

The main class, `SiteObjects`, teaches the object system how to read hosted sites from the site store, turn them into list rows, return detailed information, expose status such as URL and source files, change visibility, and delete the hosted registration. Visibility means who may open the site: only the creator and admins, the workspace, or the public internet. A special case exists for a site used as an agent’s homepage. In that case, the site follows the agent’s visibility instead of its own, like a name badge pinned to the agent rather than a separate shared document.

Without this file, deployed sites might still run, but they would not behave like first-class workspace objects. Chat, object reads, permission changes, previews, and unhosting would not have one consistent place to go.

#### Function details

##### `site_object_name`  (lines 81–85)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the unique object name for a hosted site. It combines the human site name with a short digest, or fingerprint, of the conversation ID so two conversations can safely use the same site name.

**Data flow**: It receives a conversation ID and a site name. It turns the conversation ID into a SHA-256 hash, keeps a short prefix of that hash, and appends it to the site name with a dash. The result is a stable object name such as `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the naming rule used whenever site rows are gathered or conversation-specific grants are returned. `_named` uses it to build a lookup table, `member_conversation_rows` uses it when reporting visible sites, and `site_name_from_object` uses it to verify that an object name really matches the expected conversation.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 88–94)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from a site object name, but only if that object name belongs to the given conversation. This prevents accidentally treating a similarly named object from another conversation as the same site.

**Data flow**: It receives a conversation ID and an object name. It computes the expected digest suffix for that conversation, checks whether the object name ends with it, removes the suffix, and then rebuilds the full object name to confirm it matches exactly. It returns the plain site name if valid, or `None` if not.

**Call relations**: It relies on the same naming rule as `site_object_name`. It is a helper for code that needs to turn an object-style name back into a hosted site name safely.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 97–98)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of hosted site records into a dictionary keyed by their object names. This makes later lookup by workspace object name quick and consistent.

**Data flow**: It receives hosted site records. For each site, it computes the official object name from the site’s conversation ID and site name, then stores the site under that name. It returns a dictionary from object name to hosted site record.

**Call relations**: `_member_rows` uses this when preparing all listable site rows, and `_find` uses it to locate one site by object name. It depends on `site_object_name` so all code follows the same naming convention.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 101–104)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that an extension context is available and returns it. The extension context is the bundle of workspace-specific services this file needs, such as storage, transaction access, URLs, and agent visibility information.

**Data flow**: It receives an optional extension context. If the context is missing, it raises an error because site objects cannot be read without workspace state. If present, it returns the context unchanged.

**Call relations**: Most operations call this before touching workspace data. `_sites` uses it to build a site-store wrapper, while methods such as `_member_rows`, `_member_object`, `_status`, `member_conversation_rows`, and `_apply_owned` use it to read workspace-level information.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 107–109)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates access to the hosted-sites registry for the current workspace. The registry is where deployed sites are recorded, updated, and unregistered.

**Data flow**: It receives an optional extension context, first confirms it with `_workspace`, then uses the workspace ID and current transaction from that context to construct a `HostedSites` store object. It returns that store wrapper.

**Call relations**: This is the doorway from the object layer into the hosted-site storage layer. Listing, finding, visibility changes, conversation grants, and deletion all call it when they need the current set of hosted sites or need to update one.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 112–118)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides which visibility setting actually controls who can see a site. For ordinary sites it uses the site’s own visibility; for an agent homepage it uses the agent’s visibility instead.

**Data flow**: It receives a hosted site and a mapping from agent IDs to their visibility levels. If the site is not bound to an agent homepage, it returns the site’s stored visibility. If it is bound to an agent, it looks up that agent’s level and converts it into a site visibility value.

**Call relations**: `_member_rows`, `_member_object`, and `_apply_owned` call this so they all agree about what the current access level really is. It delegates the conversion of an agent level to `visibility_level` from the store module.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 121–122)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates a short human-readable summary for a site row. It gives readers the site name, visibility, and sandbox port at a glance.

**Data flow**: It receives a hosted site and the visibility that should be shown. It formats those pieces into one string and returns it.

**Call relations**: `_member_rows` uses this while building rows for object listings. It keeps the display wording in one small place.

*Call graph*: called by 1 (_member_rows).


##### `_preview_url`  (lines 125–132)

```
def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None
```

**Purpose**: Returns a signed link to the site’s saved preview image, if one exists. A signed link is a temporary or permission-bearing URL that lets the portal show the image without creating a separate public route.

**Data flow**: It receives the workspace context and a hosted site. If the site has no saved preview blob or size, it returns `None`. Otherwise, it asks the extension context to create an image preview URL and returns that URL.

**Call relations**: `_member_rows` calls this when adding optional preview information to list rows. It hands off URL creation to `ExtensionContext.image_preview_url`, which knows how stored image blobs are served.

*Call graph*: calls 1 internal fn (image_preview_url); called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 153–154)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make on someone else’s site: narrowing it to private. This protects creators from admins making their site more widely visible.

**Data flow**: It receives the old site spec and the requested new spec. It returns `true` only when the old visibility is not already private and the new visibility is private; otherwise it returns `false`.

**Call relations**: The parent object framework uses this permission hook when deciding whether an admin may apply a change. The actual visibility update later happens through `_apply_owned` if the framework allows the request.


##### `SiteObjects._listed`  (lines 156–157)

```
def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool
```

**Purpose**: Decides whether a row should appear in a normal listing. Homepage-bound sites are hidden unless the user explicitly asks for homepage bindings.

**Data flow**: It receives a prepared object row and the list query. If the row has no homepage-agent field, it is listed normally. If it does have that field, it is listed only when the query includes a `homepage_agent` filter.

**Call relations**: The object-listing machinery calls this after rows are prepared. It enforces the rule that an agent homepage is discovered through the agent binding, not by browsing ordinary shared sites.


##### `SiteObjects.list`  (lines 159–169)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Adds a convenient shortcut for agents listing their own homepage site. If the query says `homepage_agent` is `mine`, this method replaces it with the current turn’s agent ID.

**Data flow**: It receives the tool context and a list query. If the query filter asks for `homepage_agent: mine`, it copies the query with that filter changed to the current agent’s ID. It then passes the possibly changed query to the parent listing method and returns the resulting page.

**Call relations**: This is called by the object system when site objects are listed. Its special work happens before handing off to the generic member-readable listing flow, which will call row-building and filtering hooks such as `_member_rows` and `_listed`.

*Call graph*: 1 external calls (replace).


##### `SiteObjects._member_rows`  (lines 171–217)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the site rows a member can read in workspace object listings. Each row includes ownership, visibility, useful fields like URL and preview, and enough metadata for filtering and display.

**Data flow**: It receives the extension context and the current member ID, if any. It reads all hosted sites, computes their object names, fetches agent visibilities and owner email addresses, and turns each hosted site into an `OwnedRow`. The output is a tuple of rows containing summaries, owner information, timestamps, visibility, conversation ID, site URL when available, homepage binding when present, and preview URL when available.

**Call relations**: The generic object-listing flow calls this to get raw rows for `site` objects. It pulls data through `_sites` and `_workspace`, names records with `_named`, computes real access with `effective_visibility`, formats display text with `_summary`, and adds previews through `_preview_url`.

*Call graph*: calls 6 internal fn (_named, _preview_url, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 219–244)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects from a specific conversation are visible to a member. This lets conversation views know which deployed sites should be surfaced alongside the conversation.

**Data flow**: It receives a conversation ID, member ID, admin flag, and limit. It reads agent visibilities, asks the hosted-site store for sites visible in that conversation, and turns each one into a conversation object grant containing the object name, generation, and a flag saying the content is visible.

**Call relations**: Conversation-object discovery calls this when it needs site grants for one conversation. It uses `_workspace` for agent visibility, `_sites` for the visibility-aware site query, and `site_object_name` to return names in the same format used elsewhere.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 246–269)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Returns the detailed object view for one site that a member is allowed to read. The detail includes the site’s effective visibility and a link back to the conversation that created it.

**Data flow**: It receives the object name, owner information, and current member ID. It finds the hosted site, returns `None` if it no longer exists, otherwise computes the effective visibility and builds an `ObjectDetail` with a `SiteSpec`, creation and update times, and a `created_in` link to the conversation object.

**Call relations**: The object-read flow calls this after access checks have identified a readable site object. It depends on `_find` to locate the site, `_workspace` to read agent visibility, and `effective_visibility` to show the correct access level, especially for agent homepages.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 271–298)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Builds the operational status for a hosted site, including its live URL, port, creator, deploy generation, homepage binding, and optional source files. This is the information a user or agent needs before editing or redeploying the site.

**Data flow**: It receives the tool context, object name, and owner information. It finds the site; if missing, it returns `None`. It then builds a status dictionary with identity and hosting details. If the site has a stored source manifest, it materializes those source files into the sandbox and adds the destination path and file list to the status.

**Call relations**: The object status flow calls this when someone asks for details beyond the normal object spec. It uses `_find` for lookup, `_workspace` and `site_url` to build the public link, and `materialize_source` when editable deployed source needs to be placed into the sandbox.

*Call graph*: calls 2 internal fn (_find, _workspace); 2 external calls (materialize_source, site_url).


##### `SiteObjects._apply_owned`  (lines 300–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies an allowed visibility change to an existing hosted site. It refuses creation through the object API because sites must be created by deployment, and it refuses direct visibility changes for agent homepage sites because those follow the agent.

**Data flow**: It receives the tool context, object name, requested site spec, old spec, and owner. If there is no old object or owner, it raises an error explaining that sites are created by deployment. It finds the current site, checks its effective visibility, returns early if nothing changes, rejects homepage-bound changes, then writes the new visibility to the hosted-site store. If the site is being made public and has a preview image but no share card yet, it draws a share card from the stored screenshot.

**Call relations**: The object-apply flow calls this after permission checks. It uses `_find` to make sure the site still exists, `_workspace` and `effective_visibility` to compare the real current level, `_sites` to persist the change, and `draw_from_stored_shot` to prepare public-link preview artwork when needed.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 2 external calls (__init__, draw_from_stored_shot).


##### `SiteObjects._delete_owned`  (lines 338–342)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts a site by removing its registration from the hosted-site store. After this, the permanent site link should stop resolving.

**Data flow**: It receives the tool context, object name, and owner. It finds the site, raises an error if it disappeared during deletion, and then unregisters that site using its conversation ID and site name. It does not return a value; the lasting change is the removal from the registry.

**Call relations**: The object-delete flow calls this after confirming the requester is allowed to unhost the site. It uses `_find` for lookup and `_sites` to perform the unregister operation.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 344–345)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by its workspace object name. It centralizes the conversion from stored site records to object-name lookup.

**Data flow**: It receives the extension context and an object name. It reads all hosted sites from the store, builds a name-to-site dictionary with `_named`, and returns the matching hosted site if present. If no matching site exists, it returns `None`.

**Call relations**: Single-object operations call this before reading details, status, applying visibility, or deleting. It sits on top of `_sites` for storage access and `_named` for the official object naming rule.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).

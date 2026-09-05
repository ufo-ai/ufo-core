# Knowledge source, memory, and page objects  `stage-13.1.4`

This stage is shared behind-the-scenes support for knowledge that comes from outside the conversation. It defines the “objects” users and agents can see, open, or manage when the system works with saved memory, synced documents, and external sources.

The gbrain object file is the control panel for markdown knowledge sources. A source can be a GitHub repository or a server folder. Users can register it, inspect it, resync it, share it, or remove it, and its markdown files become searchable memory.

The memory object file exposes saved memory items and member profiles as read-only records. “Read-only” means other parts of the system can list and open them, but not casually change them. It also checks permissions so people only see what they are allowed to see.

The pages file turns synced documents into workspace page objects. Users can list pages and read a limited-size copy, while admins can forget pages. Nobody creates or edits these pages manually.

The tools file registers outside provider accounts and streams, and lets conversations subscribe so they wake up when synced pages change.

## Files in this stage

### Gbrain source management
Registers and administers gbrain markdown repositories or folders as searchable synced knowledge sources.

### `extensions/gbrain/ufo_ext_gbrain/objects.py`

`domain_logic` · `object request handling for source list/get/apply/status/delete/resync`

This file is the rulebook for a gbrain source. A gbrain source is an origin for markdown pages: either a GitHub repository, optionally on a named branch, or a local folder configured by the operator. The important idea is that the source’s name is not chosen freely. It is derived from the origin itself, like a library sticker made from a book’s ISBN, so the same repository or folder always maps to the same object name.

The file defines the user-facing spec, checks that it names exactly one origin, and translates that spec into the lower-level source records that the sync system polls. It also decides who may do what. A source is private to the member who registered it unless it is marked shared. The registering member can later make a private source shared, but a shared source cannot be quietly made private again; it must be deleted and recreated. Deletion and forced resync are limited to the registrar or an admin.

GitHub sources can be applied by users. Local folder roots are different: they read the server’s filesystem, so they are operator-controlled and must come from deployment configuration, not chat. The file also exposes list, detail, and status views, including sync timing and error counts.

#### Function details

##### `gbrain_source_name`  (lines 58–65)

```
def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str
```

**Purpose**: Builds the official object name for a gbrain source from its origin. This prevents two people from giving different names to the same repository, branch, or folder.

**Data flow**: It receives a repository name, branch name, and folder root, any of which may be absent depending on the source type. It serializes those values in a stable order, hashes them, keeps a short digest, and returns a name like `gbrain-xxxxxxxx`.

**Call relations**: The origin-building helper uses this when turning a user spec into a concrete source registration. The registered-source wrapper also uses it to recover the same name when listing or looking up existing rows.

*Call graph*: called by 2 (name, _origin); 2 external calls (sha256, dumps).


##### `GbrainSpec.validate_origin`  (lines 98–103)

```
def validate_origin(self) -> 'GbrainSpec'
```

**Purpose**: Checks that a requested gbrain source describes one clear origin. It protects the system from confusing specs, such as naming both a GitHub repository and a local folder.

**Data flow**: It reads the spec fields after they have been parsed. If exactly one of `repo` or `root` is set, and `branch` is only used with `repo`, the same spec continues onward; otherwise validation stops with a clear error.

**Call relations**: This is run by the data model whenever a `GbrainSpec` is created or validated. Later code can rely on the spec having one valid shape instead of repeating these checks everywhere.


##### `_origin`  (lines 113–127)

```
def _origin(spec: GbrainSpec) -> _Origin
```

**Purpose**: Turns a validated user spec into the internal source identity: which backend will sync it, what config that backend needs, and what object name it must use.

**Data flow**: It receives a `GbrainSpec`. For a folder root, it builds folder-backend config and a name based on the root. For a GitHub repo, it builds git-backend config and a name based on repo and branch. It returns these pieces together.

**Call relations**: The main apply flow calls this to check whether a source already exists and to enforce the derived name. The owned-apply step calls it again when it is ready to actually register or update the source.

*Call graph*: calls 1 internal fn (gbrain_source_name); called by 2 (_apply_owned, apply); 3 external calls (__init__, __init__, __init__).


##### `_identity`  (lines 130–131)

```
def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]
```

**Purpose**: Extracts the fields that define whether two specs mean the same registered source state. It is used to tell a harmless repeat submission from a real change.

**Data flow**: It receives a `GbrainSpec` and returns a small tuple containing repo, branch, root, and shared/private setting. Nothing is changed; the result is just an easy comparison key.

**Call relations**: The apply method uses this to detect a no-op reapply. The resync path uses it to make sure a resync request is not secretly trying to change the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `_Registered.name`  (lines 148–149)

```
def name(self) -> str
```

**Purpose**: Returns the object-system name for an already registered source. It keeps stored source rows aligned with the same naming rule used for new applies.

**Data flow**: It reads the registered row’s repo, branch, and root values. It feeds them into the name-making helper and returns the resulting `gbrain-...` name.

**Call relations**: Existing rows are wrapped as `_Registered` objects, and list or lookup code relies on this property when matching a requested object name to a stored source.

*Call graph*: calls 1 internal fn (gbrain_source_name).


##### `_Registered.spec`  (lines 151–157)

```
def spec(self) -> GbrainSpec
```

**Purpose**: Reconstructs the public spec for a stored source so callers can read back what is registered. It hides internal database details and returns the same shape users apply.

**Data flow**: It reads the stored repo, branch, root, and subject. It converts the subject into a simple shared/private boolean, then returns a `GbrainSpec` with `resync` left at its normal false value.

**Call relations**: The object-detail hook calls this after it finds a registered source by name. This is how get/read operations produce the user-facing source spec.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Registered.summary`  (lines 159–163)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a registered source. This helps list views show something meaningful without dumping the full spec.

**Data flow**: It checks whether the source is a server directory or GitHub repository. It formats that origin as plain text, includes the branch when present, trims it to the maximum summary length, and returns the text.

**Call relations**: The member-row listing hook uses this summary when building rows for the object system. Error messages in apply also use it to explain which private source is already taken.


##### `_require_ext`  (lines 166–169)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the gbrain object code has its extension context, which is the connection to the source registry and sync actions. Without that context, the code cannot read or change sources.

**Data flow**: It receives an optional extension context. If one is present, it returns it unchanged; if not, it raises a runtime error explaining that dispatch was incorrectly set up.

**Call relations**: Lookup, listing, granting, resyncing, applying, and deleting all pass through this guard before using extension services. It is a small safety checkpoint before touching the real source system.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resync, _registered_named).


##### `_registered_from_ext`  (lines 172–197)

```
async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]
```

**Purpose**: Loads all currently registered gbrain sources from the extension context and converts them into a uniform in-memory form. It ignores unrelated source backends.

**Data flow**: It asks the extension context for source records. For git records, it validates git config and extracts repo and branch. For folder records, it validates folder config and extracts root. It wraps each accepted record as `_Registered` and returns them as a tuple.

**Call relations**: The list hook uses this to show all gbrain sources. The name lookup helper uses it to search existing rows before get, apply, status, resync, grant, or delete work continues.

*Call graph*: calls 1 internal fn (sources); called by 2 (_member_rows, _registered_named); 3 external calls (__init__, model_validate, model_validate).


##### `_registered_named`  (lines 200–204)

```
async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None
```

**Purpose**: Finds one registered gbrain source by its derived object name. It is the common lookup path for operations that target a single source.

**Data flow**: It receives an optional extension context and a name. It first requires a real context, loads all registered gbrain sources, compares their derived names to the requested name, and returns the matching `_Registered` row or `None`.

**Call relations**: Apply uses it to detect existing registrations. Detail, status, grant, resync, apply-owned, and delete-owned use it to fetch the exact stored source before acting.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, apply).


##### `GbrainObjects.apply`  (lines 222–253)

```
async def apply(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Implements the top-level apply behavior for gbrain source objects. It separates three cases: forced resync, harmless reapply, and real registration or sharing changes.

**Data flow**: It receives the tool context, requested name, new spec, any visible old spec, and an expected generation value. If `resync` is true, it routes to the resync path. If the spec is identical to what is already visible, it grants the current agent access without re-registering. Otherwise it checks for private conflicts and then hands the real mutation to the base object apply flow.

**Call relations**: This is the main entry for apply requests on this object kind. It calls `_resync`, `_grant_settled`, `_identity`, `_origin`, and `_registered_named`, and it asks the tool context whether the speaker is an admin when deciding whether a private existing source blocks the request.

*Call graph*: calls 6 internal fn (speaker_is_admin, _grant_settled, _resync, _identity, _origin, _registered_named); 3 external calls (__init__, authority_member_id, subject_shared).


##### `GbrainObjects._grant_settled`  (lines 255–270)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to a source when an apply request turns out to be an identical no-op. This prevents a confusing situation where the user sees success but the agent was not actually connected to the feed.

**Data flow**: It reads the speaking member, the object owner, and the registered source row. If there is no speaker, no owner, or the speaker is not allowed to use that owner’s source, it does nothing. Otherwise it asks the extension context to grant the source to the current agent.

**Call relations**: The main apply method calls this only for reapplying an unchanged visible source. It then uses `_registered_named` to find the stored source and `_require_ext` before calling the extension grant operation.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); called by 1 (apply).


##### `GbrainObjects._resync`  (lines 272–292)

```
async def _resync(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing source without changing its definition. It treats resync as an action, not as stored state.

**Data flow**: It receives the requested name, spec, and old spec. It first verifies the request exactly matches the current source apart from the resync flag. It then checks visibility and authority: the registrar or an admin may resync. If allowed, it finds the registered source and asks the extension context to schedule that source for syncing now.

**Call relations**: The main apply method sends resync requests here. This method uses `_identity` to reject mixed edit-and-resync attempts, asks the context about admin status, looks up the source with `_registered_named`, and then hands the source id to the extension sync scheduler.

*Call graph*: calls 4 internal fn (speaker_is_admin, _identity, _registered_named, _require_ext); called by 1 (apply); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `GbrainObjects._member_rows`  (lines 294–307)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows used when listing gbrain source objects visible through the member-readable object system. Each row contains the object name, a short summary, and ownership information.

**Data flow**: It receives the extension context and an optional member id. It loads all registered gbrain sources, turns each into an `OwnedRow`, and marks the owner as shared or tied to a member based on the stored subject. It returns the completed tuple of rows.

**Call relations**: The surrounding object framework calls this listing hook when it needs the available objects. It relies on `_registered_from_ext` to read source rows and `_require_ext` to ensure the extension services are available.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `GbrainObjects._member_object`  (lines 309–324)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[GbrainSpec] | None
```

**Purpose**: Builds the detailed read view for one gbrain source. It returns the public spec plus creation and update times.

**Data flow**: It receives an extension context, object name, owner information, and optional member id. It looks up the registered source by name. If found, it converts the row back into a `GbrainSpec` and packages it with timestamps; if not, it returns `None`.

**Call relations**: The object framework uses this hook after ownership and visibility have been decided. It depends on `_registered_named` to find the stored source and on `_Registered.spec` indirectly to create the readable spec.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (__init__).


##### `GbrainObjects._status`  (lines 326–340)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Provides live operational status for a gbrain source, such as when it will sync next and whether recent syncs have failed. This is separate from the source’s desired configuration.

**Data flow**: It receives the tool context, object name, and owner. It looks up the registered row. If found, it returns a dictionary with shared/private state, next sync time, and consecutive error count; for private owned sources, it also includes the owner member id.

**Call relations**: The object/status flow calls this when it needs runtime details. It uses `_registered_named` for lookup and the subject-sharing helper to decide what ownership information is safe and useful to report.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (subject_shared).


##### `GbrainObjects._apply_owned`  (lines 342–380)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the actual source registration or allowed ownership change after the broader object system has passed control to this kind. It is where GitHub sources are created, shared, and granted to agents.

**Data flow**: It receives the context, name, spec, old spec, and owner. It requires a speaking member, refuses local folder roots, derives the correct origin and name, and rejects mismatched names. If no source exists, it registers a new one as private or shared. If it exists, it refuses unsharing, optionally flips private to shared, and grants the source to the current agent.

**Call relations**: The base apply flow calls this hook for real owned mutations. It calls `_origin` to derive backend config, `_registered_named` to see what exists, `_require_ext` to reach extension operations, and subject helpers to choose private-member or shared ownership.

*Call graph*: calls 3 internal fn (_origin, _registered_named, _require_ext); 4 external calls (__init__, __init__, member_subject, subject_shared).


##### `GbrainObjects._delete_owned`  (lines 382–386)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a registered gbrain source once the object system has established that the caller is allowed to delete it. Removing the source also lets the downstream page-tombstone pipeline clean up synced pages.

**Data flow**: It receives the context, object name, and owner. It looks up the registered source. If none exists, it reports an unknown object; otherwise it asks the extension context to remove that source by id.

**Call relations**: The base delete flow calls this hook after applying the registrar-or-admin delete rules. It uses `_registered_named` to find the source and `_require_ext` before calling the extension removal operation.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); 1 external calls (__init__).


### Read-only memory records
Exposes stored memory items and member profiles through permission-checked read-only object types.

### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the public “object shelf” for the memory extension. One shelf holds individual memories: facts, decisions, preferences, events, and similar notes that were recorded earlier. The other shelf holds lightweight people profiles: what the workspace currently knows about each member’s role and focus. Without this file, tools could search memory IDs but would not have a safe, standard way to open them, list them, or show them in the portal.

The important rule throughout the file is visibility. A memory is only returned if its subject, meaning its audience label, is one the reader is allowed to read. If a memory came from a synced page, the page must still be readable and still match the same revision; otherwise the memory is hidden. This prevents old derived facts from leaking after the source page is no longer visible.

Memory records are deliberately read-only here. New memories are written through a separate `memory_update` path, and old memories are not deleted directly. Instead, consolidation can mark one memory as replaced by another, and this file exposes a `superseded_by` link so stale references can still lead readers to the newer statement.

Profiles are also read-only. They are written by a background “People pass” from shared workspace facts. A reader either has access to the shared workspace subject and can see the people band, or they cannot see it at all.

#### Function details

##### `_require_ext`  (lines 91–94)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure object operations were given the memory extension context they need. The context is the bundle that knows the workspace, database connection, and helper services.

**Data flow**: It receives an optional extension context. If the context is present, it passes it through unchanged; if it is missing, it stops immediately with a runtime error so later code does not fail in a confusing way.

**Call relations**: All public memory and profile read methods call this before doing real work. It is the front-door check that ensures `MemoryObjects` and `ProfileObjects` can safely continue to database reads and page visibility checks.

*Call graph*: called by 8 (get, list, member_detail, member_page, get, list, member_detail, member_page).


##### `_stamp`  (lines 97–101)

```
def _stamp(written: datetime) -> str
```

**Purpose**: This turns a stored date and time into one consistent text format. It matters because different databases may store timezone information differently.

**Data flow**: It receives a `datetime`, normalizes it with `_aware` so it clearly represents an actual moment in time, and returns an ISO-8601 string, which is a standard machine-readable time spelling.

**Call relations**: Listing rows for both memories and profiles call this through `_row` and `_profile_row`. It keeps the `written` field stable for callers no matter which database engine is underneath.

*Call graph*: called by 2 (_profile_row, _row); 1 external calls (_aware).


##### `_row`  (lines 104–128)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str, written: datetime | None, page_id: UUID | None, pages: Mapping[UUID, PageState]) -> ObjectRow
```

**Purpose**: This builds the compact row shown when memory items are listed. It gives callers a readable preview plus useful fields such as subject, kind, written time, and source page information.

**Data flow**: It receives the memory ID, body text, visibility subject, classification fields, write time, optional source page ID, and any readable page states. It trims long text to safe listing sizes, adds source page title and stream when known, and returns an `ObjectRow` for display or filtering.

**Call relations**: `MemoryObjects._page` uses this for each listed memory, and `MemoryObjects.member_detail` uses it when wrapping a detailed memory for the member portal. It calls `_stamp` for dates and `clip_to_word` so previews do not cut words awkwardly.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_page, member_detail); 2 external calls (__init__, clip_to_word).


##### `_member_reader`  (lines 131–139)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: This creates the reading identity used when a signed-in member views memory outside an active conversation turn. It answers the question: “If this member opens the portal, whose memory are they allowed to read?”

**Data flow**: It receives a member ID. It looks up the current agent, builds that member’s conversation audience, turns the audience into readable subject labels, and returns a `SourceReader` containing the agent, requesting member, and allowed subjects.

**Call relations**: `MemoryObjects.member_page` and `MemoryObjects.member_detail` use this before reading memories for portal-style member views. It recreates the same kind of audience information that a live tool call would normally carry in its context.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists live memory items for the current tool caller. “Live” means not superseded and not retired.

**Data flow**: It receives a tool context and a list query. It extracts the extension context and the caller’s reader identity, then asks `_page` to fetch visible memory rows and shape them into a paged result.

**Call relations**: This is the standard object-list entry point for the `memory` kind during tool use. It delegates the real database filtering and visibility checks to `MemoryObjects._page`.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 151–164)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists memory items for a signed-in member viewing outside an active turn, such as through a portal page. It does not give admins extra access to another member’s private memories.

**Data flow**: It receives an optional extension context, a member ID, an admin flag, and a list query. It builds that member’s reader identity with `_member_reader`, then asks `_page` to return only memories visible to that reader.

**Call relations**: Portal-style member views call this instead of `MemoryObjects.list`. It shares the same listing engine, `_page`, so turn-time and portal-time memory views obey the same visibility rules.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 166–200)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: This opens one memory item for a signed-in member outside an active turn and returns both the detailed body and the listing-style row beside it. It still allows a superseded item to be opened, so old references can point to the replacement.

**Data flow**: It receives an extension context, memory name, member ID, and admin flag. It builds the member’s reader identity, loads the item with `_item`, finds any source page link, fetches readable page state for that page, and returns a `MemberObject` containing both a row preview and full detail. If the memory is missing or hidden, it returns nothing.

**Call relations**: This is the portal-detail counterpart to `MemoryObjects.get`. It depends on `_item` for the full memory record and `_row` for the display row.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 2 external calls (__init__, UUID).


##### `MemoryObjects.get`  (lines 202–203)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by name for the current tool caller. It is how a reference returned by memory search becomes the full stored memory text and metadata.

**Data flow**: It receives a tool context and memory name. It checks for an extension context, gets the caller’s reader identity, and asks `_item` to load the visible item. The result is either an `ObjectDetail` or nothing if the ID is invalid, missing, or not visible.

**Call relations**: This is the standard object-get entry point for the `memory` kind during tool use. It leaves the careful ID parsing, database lookup, source-page check, and link building to `MemoryObjects._item`.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 205–262)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the main listing engine for memory items. It fetches recent live memories from the database, then removes any whose source page is no longer readable or no longer matches the memory’s recorded source revision.

**Data flow**: It receives the extension context, a reader identity, and a list query. It reads the reader’s allowed subjects, queries the memory table for matching non-superseded, non-retired rows, loads readable source page states, converts allowed rows into `ObjectRow` values, and wraps them in an `ObjectPage` using the requested paging and filtering rules.

**Call relations**: `MemoryObjects.list` and `MemoryObjects.member_page` both rely on this shared worker. It talks to the extension transaction for database access, asks the context which cited pages are readable, uses `_row` to shape each result, and finishes with `object_page`.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 264–330)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the main detail loader for a single memory. It opens a memory by UUID, checks the reader may see it, and includes links to its source page or replacement memory when relevant.

**Data flow**: It receives the extension context, reader identity, and memory name. It first parses the name as a UUID; if that fails, it returns nothing. It then reads the matching memory row for the workspace and allowed subjects. If the memory came from a page, it confirms that page is still readable, has the same subject, and is the same revision. Finally it builds a `MemorySpec`, timestamps, and links, then returns an `ObjectDetail`.

**Call relations**: `MemoryObjects.get` and `MemoryObjects.member_detail` call this whenever a single memory must be opened. It uses database queries for the row, page-state reads for source visibility, and object link models to connect old memories to pages or replacement memories.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 332–339)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports that there is no separate status information for memory objects. Returning nothing tells callers there is no pending object operation to track here.

**Data flow**: It receives the tool context, object name, and optional expected generation value. It does not read or change anything and always returns `None`.

**Call relations**: It exists to satisfy the object-store interface used by the wider system. Unlike editable object kinds, memory objects do not have an apply workflow whose status needs reporting.


##### `MemoryObjects.apply`  (lines 341–350)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or edit memory objects through the generic object apply path. Memories must be recorded through `memory_update` instead.

**Data flow**: It receives the tool context, object name, proposed memory spec, optional old spec, and optional generation check. Rather than writing anything, it raises `VerbNotSupported` with an explanation.

**Call relations**: The wider object system may call this when a caller tries to apply changes to a `memory` object. This method blocks that route and points callers back to the proper memory-writing tool.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 352–359)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete memory objects directly. Old memories are ended by being superseded or dropped from recall, not by a per-item delete operation here.

**Data flow**: It receives the tool context, object name, and optional generation check. It does not touch the database and raises `VerbNotSupported` with a message explaining that memories are not deleted this way.

**Call relations**: The generic object system may call this for a delete request on the `memory` kind. This method protects the memory store’s append-and-consolidate model by preventing direct deletion.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.list`  (lines 410–411)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists member profile rows for the current tool caller. A profile row says what the workspace currently knows about a person’s role and focus.

**Data flow**: It receives a tool context and list query. It checks the extension context, uses the context’s readable subjects, and asks `_page` to return profile rows only if the caller can read shared workspace facts.

**Call relations**: This is the standard object-list entry point for the `profile` kind during tool use. It delegates the shared-subject gate and database query to `ProfileObjects._page`.

*Call graph*: calls 2 internal fn (_page, _require_ext).


##### `ProfileObjects.member_page`  (lines 413–426)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists people profiles for a signed-in member outside an active turn. Admin status does not widen the result because profiles are based only on shared workspace facts.

**Data flow**: It receives an optional extension context, member ID, admin flag, and query. It computes the subjects that member’s conversation audience can read, then asks `_page` to return the visible profile page.

**Call relations**: Portal-style member views call this instead of `ProfileObjects.list`. It uses the same `_page` worker as tool-time listing so the People band follows the same shared-facts rule everywhere.

*Call graph*: calls 2 internal fn (_page, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects.get`  (lines 428–430)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None
```

**Purpose**: This opens one member profile by member ID for the current tool caller. It returns the role and current focus if the caller can read shared workspace facts.

**Data flow**: It receives a tool context and profile name. It checks the extension context, calls `_entry` with the caller’s readable subjects, and returns only the detail part of the found member object. If access is denied, the ID is invalid, or no row exists, it returns nothing.

**Call relations**: This is the standard object-get entry point for the `profile` kind. It relies on `ProfileObjects._entry` to enforce the shared-subject rule and load the database row.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 432–442)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ProfileSpec] | None
```

**Purpose**: This opens one profile for a signed-in member viewing outside an active turn. It returns both the listing row and the detailed profile when visible.

**Data flow**: It receives an extension context, profile name, member ID, and admin flag. It computes the member’s readable subjects from their conversation audience and passes those to `_entry`. The output is a `MemberObject` or nothing.

**Call relations**: This is the portal-detail counterpart to `ProfileObjects.get`. It uses `_entry` as the shared detail loader so portal reads and tool reads obey the same access rule.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects._page`  (lines 444–463)

```
async def _page(self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the main listing engine for profile rows. It only shows the People band to readers who can read the workspace-shared subject.

**Data flow**: It receives the extension context, a set of readable subjects, and a list query. If the shared subject is absent, it returns an empty page immediately. Otherwise it queries recent profile rows for the workspace, turns each one into an `ObjectRow`, and wraps them in an `ObjectPage`.

**Call relations**: `ProfileObjects.list` and `ProfileObjects.member_page` call this for all profile listing. It uses the extension transaction for database access, `_profile_row` for display rows, and `object_page` for the final paged response.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `ProfileObjects._entry`  (lines 465–498)

```
async def _entry(self, ext: ExtensionContext, subjects: frozenset[str], name: str) -> MemberObject[ProfileSpec] | None
```

**Purpose**: This is the main detail loader for a single member profile. It checks whether the reader may see shared people information, then loads the profile by member ID.

**Data flow**: It receives the extension context, readable subjects, and profile name. If the shared subject is missing, it returns nothing. It parses the name as a UUID, queries the profile table for that member in the workspace, normalizes the written time, and returns a `MemberObject` containing both row and detail. Missing or invalid IDs return nothing.

**Call relations**: `ProfileObjects.get` and `ProfileObjects.member_detail` call this whenever one profile must be opened. It uses `_profile_row` for the row view and builds a `ProfileSpec` and `ObjectDetail` for the full view.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, select, _aware, UUID).


##### `ProfileObjects.status`  (lines 500–507)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports that profile objects have no separate status to check. Profiles are generated by the People pass, not edited through an apply job here.

**Data flow**: It receives the tool context, profile name, and optional expected generation value. It does not read or change anything and always returns `None`.

**Call relations**: It exists because the object-store interface includes a status operation. For profile objects, there is no user-started write operation whose status would be meaningful.


##### `ProfileObjects.apply`  (lines 509–518)

```
async def apply(self, ctx: ToolContext, name: str, spec: ProfileSpec, old: ProfileSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or edit profiles through the generic object apply path. Profiles are written by the People pass from shared facts.

**Data flow**: It receives the tool context, profile name, proposed profile spec, optional old spec, and optional generation check. It writes nothing and raises `VerbNotSupported` with the refusal message.

**Call relations**: The wider object system may call this when a caller tries to apply changes to a `profile` object. This method keeps profile writing centralized in the background People pass.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 520–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete profiles through the generic object delete path. The People band reflects generated shared facts rather than manual profile records.

**Data flow**: It receives the tool context, profile name, and optional generation check. It makes no database changes and raises `VerbNotSupported` with the same explanation used for profile writes.

**Call relations**: The generic object system may call this for a delete request on the `profile` kind. This method blocks manual deletion so generated profile data stays controlled by the People pass.

*Call graph*: 1 external calls (__init__).


##### `_profile_row`  (lines 530–540)

```
def _profile_row(row: sa.Row) -> ObjectRow
```

**Purpose**: This builds the compact row shown when member profiles are listed. It gives callers a readable summary plus fields for member ID, role, focus, and written time.

**Data flow**: It receives a database row from the profile table. It combines the role and focus into a short summary, trims it cleanly, formats the written time with `_stamp`, and returns an `ObjectRow`.

**Call relations**: `ProfileObjects._page` uses this for each listed profile, and `ProfileObjects._entry` uses it when returning a single profile with both row and detail. It is the profile equivalent of the memory `_row` helper.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_entry, _page); 2 external calls (__init__, clip_to_word).


### Synced source pages and subscriptions
Presents synced documents as read-only pages and manages external source objects plus conversation subscriptions to source updates.

### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `object request handling`

A page here is not something a user writes directly. It is one document brought in by the content sync driver from an outside source, such as an issue tracker or another connected service. This file gives those synced documents a standard object interface so the rest of the system can browse them in a safe, predictable way.

The main idea is “read and forget.” Users can list visible pages and get one page by its UUID-shaped name. Getting a page reads its metadata from the source-page records and reads the body from blob storage, which is storage for larger byte content. The body is capped at 65,536 UTF-8 bytes so a single object read cannot accidentally pull in a huge document. If the page changes while it is being read, the file checks the latest visible page state before returning it; if the saved details no longer match, it returns nothing rather than serving a stale or mismatched body.

Pages can only be produced by the sync driver. Create and update requests are refused with a clear message. Delete means “forget this synced page”: only a workspace admin may do it, and it tombstones the page so the existing change pipeline can clean up related index data. The file also builds links back to the source binding that synced the page, when that source can be identified.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small safety check makes sure the page object code is running with its extension context. That context is needed to read sources, read synced pages, and forget pages.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it stops the operation with a runtime error, because the page code would not know where to fetch page data from.

**Call relations**: PageObjects._pages uses it before reading source and page records. PageObjects.get uses it before checking the current readable page state. PageObjects.delete uses it before asking the extension to forget a page.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This turns a page timestamp into one consistent UTC time string. It accepts either the provider's original timestamp or, if that is missing, the local row timestamp.

**Data flow**: It receives an optional timestamp string from the outside provider and a datetime from the local page row. If the provider value is missing, it uses the row time and adds UTC if needed. If the provider value exists, it parses it and rejects it if it has no timezone. The output is an ISO-formatted UTC timestamp with microsecond precision.

**Call relations**: _Page.spec uses it when building the full page details returned by get. _Page.fields uses it when building the lighter metadata returned by list. Internally it relies on Python datetime parsing and timezone adjustment.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This gives the page its object name. The name is simply the page row's UUID converted to text.

**Data flow**: It reads the _Page object's id field. It converts that UUID into a string and returns it, without changing anything.

**Call relations**: PageObjects.list uses this value when making rows for listing pages. PageObjects._find compares this value with the requested name when looking up one page.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds a link from a page back to the source object that synced it, when that source name is known. The link helps readers understand where the page came from.

**Data flow**: It reads the page's source_name. If there is no source name, it returns no links. If there is one, it creates an ObjectLink with relation synced_by pointing to the corresponding source object reference.

**Call relations**: PageObjects.get includes these links in the ObjectDetail it returns. The method creates ObjectRef and ObjectLink objects so the broader object system can show the relationship in a standard way.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This builds the full public shape of a page, including metadata and the page body. It is what a successful get request exposes to the caller.

**Data flow**: It receives the already-read body text and a flag saying whether the body had to be cut short. It combines those with the _Page metadata, normalizes the created and updated timestamps, and returns a PageSpec object.

**Call relations**: PageObjects.get calls this after reading the page body and confirming the page record still matches the current readable state. It depends on _page_timestamp to keep time values consistent.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short human-readable label for a page in list results. It gives the title, source provider, stream, and visibility subject in one compact line.

**Data flow**: It reads the page title, backend, stream, and subject. It formats them into a short string and trims it to the configured maximum length.

**Call relations**: PageObjects.list uses this summary when building each ObjectRow. It gives list views a useful preview without needing to fetch the full page body.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This builds the searchable and sortable metadata shown in page list results. It includes only the fields the object kind advertises for list filtering and ordering.

**Data flow**: It reads the page's source id, backend, stream, title, and timestamp values. It converts the UUID and timestamps into stable text forms, then returns them as a dictionary.

**Call relations**: PageObjects.list uses these fields when creating each listed ObjectRow. It calls _page_timestamp so list metadata uses the same time formatting as full page details.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the pages the caller is allowed to see as a paged list. It is used for browsing synced pages without loading their full body text.

**Data flow**: It receives the tool context and a list query containing things like filtering, ordering, or paging instructions. It fetches visible pages through _pages, turns each one into an ObjectRow with a name, summary, and fields, then passes those rows to object_page to apply the query and return an ObjectPage.

**Call relations**: The object system calls this when someone lists page objects. It hands the actual page lookup to PageObjects._pages, then hands the finished rows to the shared object_page helper for standard list behavior.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This returns the full detail for one visible page, including a bounded copy of its body. It also protects against returning a body that no longer matches the current page record.

**Data flow**: It receives a context and a page name. It finds the matching page, streams the page body bytes from blob storage up to one byte beyond the limit, decodes a safe UTF-8 string, and notes whether truncation happened. Then it rereads the current visible page state and compares key fields such as subject, revision, digest, and body reference. If the page is missing or changed, it returns None. Otherwise it returns an ObjectDetail with the PageSpec, timestamps, and source link.

**Call relations**: The object system calls this when someone asks for one page by name. It uses PageObjects._find to locate the page, _require_ext to reach extension services, ToolContext.source_reader to respect the reader's source access, and ObjectDetail to return the result in the standard object format.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This says that page objects have no separate apply/status workflow. A status request for a page always has no answer.

**Data flow**: It receives the context, object name, and optional expected generation. It ignores them and returns None, making no changes.

**Call relations**: This fits the object interface, but pages are synced records rather than user-applied resources. Since there is no create-or-update process to track, it does not hand off to any other code.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update a page. Pages must come from the content sync driver, not from manual object writes.

**Data flow**: It receives the context, page name, desired spec, old spec, and optional generation check. Instead of saving anything, it raises VerbNotSupported with an explanation that pages are synced.

**Call relations**: The object system may call this for create or update operations on page objects. It immediately stops that path by constructing a VerbNotSupported error, because the only allowed writer is the sync driver outside this object API.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This lets a workspace admin forget one synced page. Forgetting tombstones the page so downstream cleanup can remove derived index state.

**Data flow**: It receives a context, page name, and optional expected generation. It first asks whether the speaker is an admin. If not, it raises AdminRequired. If the speaker is an admin, it finds the named page; if no page exists, it raises a ValueError. If found, it calls the extension context to forget that page id.

**Call relations**: The object system calls this for delete requests on page objects. It uses ToolContext.speaker_is_admin for the permission gate, PageObjects._find to locate the page, and _require_ext to reach forget_page on the extension context.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This searches the caller-visible page list for one page with the requested object name. It is the shared lookup step for get and delete.

**Data flow**: It receives a context and a name string. It fetches all visible pages through _pages, compares each page's name to the requested name, and returns the first match or None.

**Call relations**: PageObjects.get calls it before reading a page body. PageObjects.delete calls it before forgetting a page. It relies on PageObjects._pages so the search uses the same visibility rules as listing.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This builds the internal _Page objects that represent the synced pages the caller is allowed to see. It also attaches source-provider information and, when possible, a source object name for linking.

**Data flow**: It receives the tool context. It gets the extension context, fetches registered sources, builds lookup tables from source id to backend and source object name, then asks for source pages visible to the current source reader. For each page record, it combines the page fields with the source information and returns a tuple of _Page objects.

**Call relations**: PageObjects.list uses this to produce browse rows, and PageObjects._find uses it to search for one page. It calls ToolContext.source_reader so page visibility follows the reader's grants, validates connector source config where needed, and uses binding_name to create source names that _Page.links can later point to.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hooks`

This file is the public control panel for the source-sync extension. A “source” here means one provider binding: for example, one connected account plus the selected streams of data to keep syncing. Without this file, agents could not safely create, inspect, resync, share, or delete those bindings through the object tools, and conversations would not know how to be alerted when shared source data changes.

The file turns low-level source rows into easier object-shaped things. Several rows, one per stream, are grouped into one binding with a derived name. That name is important: it prevents two different names from secretly pointing at the same account and provider. Applying a source validates the provider, stream names, tenant URL, account or workspace credential, sharing choice, and optional backfill window before writing anything.

It also defines “source triggers.” A trigger is a standing subscription from one conversation to one shared source. When page changes arrive, the hook filters them to shared pages the trigger’s agent may read, writes a small change log when file storage is available, and wakes either the current conversation or a stable per-page conversation. In everyday terms, sources are the mailboxes being watched, and triggers are the sticky notes saying who should be told when new mail arrives.

#### Function details

##### `_Binding.name`  (lines 220–221)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name comes from the provider, account, and tenant URL so the same binding cannot be registered under several arbitrary names.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared naming helper, and returns the resulting stable name string.

**Call relations**: Other code uses this property whenever it needs to compare, list, or address a binding. It delegates the actual naming rule to the shared source naming helper so this file follows the same naming convention as the rest of the system.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 224–225)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding first came into existence. Because a binding is made of several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads every stream’s creation timestamp, chooses the earliest one, and returns that timestamp as the binding’s creation time.

**Call relations**: The object detail path uses this value when showing a source object. It turns many per-stream dates into one date a reader can understand for the whole binding.


##### `_Binding.updated_at`  (lines 228–229)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports the latest time any stream in the binding changed. This tells users when the binding as a whole was last modified.

**Data flow**: It reads every stream’s update timestamp, chooses the newest one, and returns that timestamp.

**Call relations**: The object detail path uses this value to present one update time for the source object, even though the underlying storage keeps separate rows per stream.


##### `_Binding.links`  (lines 231–244)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Describes what credential or connection the source uses for access. This helps object readers see what the binding depends on without exposing more than they should.

**Data flow**: It reads the binding’s account and sharing state. For direct workspace credentials it returns a link to the credential slot; for private connected accounts it returns a link to the connection object; for shared connected-account sources it returns no connection link.

**Call relations**: Source object detail calls this when building links for the object. It uses object-reference helpers and credential/account naming helpers to point at the right related object.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 246–254)

```
def spec(self) -> SourceSpec
```

**Purpose**: Reconstructs the user-facing source specification from stored stream rows. This is what object_get can show back to the agent or user.

**Data flow**: It reads the binding’s provider, streams, account, URL, sharing subject, and backfill setting, converts internal values like the direct-account marker into public fields, and returns a SourceSpec.

**Call relations**: Source object detail and apply comparisons rely on this to turn storage state back into the same shape that users submit. It uses the shared-subject helper to decide whether the source is public to the workspace.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 256–258)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a binding. It names the provider, account, and streams in a compact form.

**Data flow**: It joins the stream names, combines them with the provider and account, trims the text to the configured maximum length, and returns the summary string.

**Call relations**: Listing code and alert messages use this to describe a source without dumping its full specification. The alert builder calls it when explaining which watched source changed.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 267–270)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure an ExtensionContext is present before source code tries to use runtime services. The extension context is the object that gives access to stored sources, files, credentials, and other system services.

**Data flow**: It receives a possible context. If it is missing, it raises a runtime error; otherwise it returns the context unchanged.

**Call relations**: Many source and trigger operations call this at their boundary before touching storage or runtime services. It acts like a guardrail so programming mistakes fail loudly instead of causing confusing missing-attribute errors later.

*Call graph*: called by 10 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 273–276)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool turn has a connector registry. The registry is the catalog that knows which external account connectors are available.

**Data flow**: It reads the connector registry from the tool context. If none is present, it raises a runtime error; otherwise it returns the registry.

**Call relations**: Account resolution calls this before deciding whether a provider should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 279–314)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds source bindings from the lower-level stored source rows. Storage keeps one row per stream, but users think in terms of one binding with several streams.

**Data flow**: It reads all source records from the extension context, ignores records for providers not owned by this extension, validates each row’s config, groups rows by provider/account/base URL, and returns sorted _Binding objects.

**Call relations**: Listing, lookup, trigger listing, and page-change handling all call this first when they need the current set of bindings. It is the bridge between raw sync rows and object-level source bindings.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 317–327)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one binding by its derived object name. Both source operations and trigger operations start here when a user names a source.

**Data flow**: It requires an extension context, rebuilds all bindings, compares each binding’s name to the requested name, and returns the matching binding or None.

**Call relations**: Source get, status, apply, delete, resync, settled grants, and trigger creation all use this helper so name lookup is consistent everywhere.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 330–331)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the source-trigger store after confirming the extension context exists. The trigger store is where standing subscriptions are saved.

**Data flow**: It checks the extension context, builds a SourceTriggerStore around it, and returns that store.

**Call relations**: Source deletion, trigger creation, trigger deletion, trigger lookup, trigger listing, and page-change delivery all use this helper before reading or changing trigger rows.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 334–343)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides how far back a stream should sync when a backfill window is involved. It combines what the user requested with what the stream declares by default.

**Data flow**: It receives a requested backfill value and a stream’s declared default. If the user gave a number, that number wins; if the user gave nothing, the stream default is used; if the user asked for all history, it returns None to mean no cutoff.

**Call relations**: Source registration and window-widening both call this so first-time syncs and later backfill changes use exactly the same rule.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 346–356)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the parts of a SourceSpec that define whether two source submissions mean the same binding state. This excludes one-off actions like resync.

**Data flow**: It reads provider, sorted streams, account ID, base URL, sharing setting, and backfill setting from the spec and returns them as a comparable tuple.

**Call relations**: The apply path uses this to detect no-op reapplications, and the resync path uses it to reject requests that try to resync and edit the binding at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 385–406)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Customizes source apply behavior for two special cases: resyncs and identical reapplications. It keeps ordinary creation or edits on the standard object path.

**Data flow**: It receives the tool context, object name, submitted spec, old visible spec, and expected generation. If resync is requested it schedules a resync; if the submitted identity matches the old one it grants the agent access to the already-settled source; otherwise it passes the request to the base object apply logic.

**Call relations**: This is the first source-specific step when the object framework applies a source manifest. It hands off to _resync, _grant_settled, or the inherited apply flow depending on what the submit is trying to do.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 408–429)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Gives the current agent access to an already-existing source when the submitted spec is identical. This matters because no new stream row is registered in a no-op apply, so no automatic grant would happen otherwise.

**Data flow**: It reads the speaking member, source owner, and binding. If the speaker is allowed to receive the feed, it grants each stream’s source ID to the current agent.

**Call relations**: SourceObjects.apply calls this for identical reapplications. It uses _binding_named to find the stored streams and the extension context to write grants.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 431–455)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules all streams in a source binding to sync immediately. It is an action, not a change to the saved source specification.

**Data flow**: It verifies that the submitted spec matches the current one, checks that the caller can see and is allowed to spend the source, finds the binding, and asks the extension context to schedule sync for all its stream IDs.

**Call relations**: SourceObjects.apply calls this when resync is true. It uses the same identity comparison as apply, looks up the binding by name, and relies on the tool context for admin checks.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `SourceObjects._member_rows`  (lines 457–470)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when sources are listed. Each row gives the object name, a short summary, and ownership/sharing information.

**Data flow**: It rebuilds all bindings from the extension context, converts each one into an OwnedRow with an ObjectOwner, and returns the tuple of rows.

**Call relations**: The member-readable object framework calls this when listing sources. It depends on _bindings_from_ext for the grouped binding view.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 472–488)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed object response for one source. It turns the internal binding back into its public spec plus timestamps and links.

**Data flow**: It looks up the named binding. If found, it returns an ObjectDetail containing the binding’s spec, creation time, update time, and access links; if not found, it returns None.

**Call relations**: The object framework calls this after visibility has been decided. It relies on _binding_named and the _Binding helper properties to present the source cleanly.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 490–516)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports operational status for a source, such as when each stream will sync next and whether it has recent errors. This is separate from the desired configuration.

**Data flow**: It looks up the binding, then builds a dictionary with sharing state and per-stream sync timing, error, parking, and backfill details. For private sources it may also include the owner member ID.

**Call relations**: The source object status path calls this when a user asks how a source is doing. It uses _binding_named to connect the object name to live stream rows.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 518–622)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real work of creating or changing a source after ownership checks pass. It validates the request, registers new stream rows, grants access, updates sharing or backfill windows, and removes streams that were left out.

**Data flow**: It takes the submitted source spec and current state, checks for a speaking member, validates provider and stream names, validates tenant URL, resolves the account or credential, checks the derived name, compares held streams, widens backfill if needed, flips private to shared if requested, registers new streams, grants existing streams to the agent, and removes dropped streams.

**Call relations**: The base object apply flow calls this for authorized source mutations. It coordinates helpers for URL validation, account resolution, binding lookup, backfill calculation, and window widening, then writes through the extension context.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 8 external calls (__init__, __init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 624–698)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Safely expands a source’s backfill window farther into the past. It refuses narrowing because that could leave old synced pages behind with no clean way to remove them.

**Data flow**: It receives the binding, kept streams, stream defaults, account, URL, and requested backfill. For each kept windowed stream it computes the new cutoff date from the original anchor date, rejects any move that would make the cutoff later, then rewrites source configs and marks changed streams for refetch.

**Call relations**: SourceObjects._apply_owned calls this before making writes when the backfill request changes. It uses effective_days so widening follows the same meaning as initial registration.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 700–707)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding by removing every stream row in it. It also removes triggers that watched that binding.

**Data flow**: It looks up the binding by name, raises an unknown-object error if missing, removes each stream source ID through the extension context, then asks the trigger store to remove triggers for that binding.

**Call relations**: The base object delete flow calls this after permission checks. It combines source-row cleanup with trigger cleanup so no subscription remains pointing at a deleted source.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 709–782)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which account or credential a source should use to authenticate. This keeps the user from having to choose between internal paths with a flag.

**Data flow**: It reads connector registry information, active connected accounts, connection ownership, declared credential slots, and fallback capabilities. It returns a resolved account handle plus an optional connection ID, or raises a clear error telling the user to connect an account or add a credential.

**Call relations**: SourceObjects._apply_owned calls this before registering streams. It uses the connector registry and tool context account APIs to ensure the source will run with a credential the speaking member is allowed to use.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 785–789)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Builds the official object name for a source trigger. A trigger is defined by the source it watches and the conversation that owns it.

**Data flow**: It receives a source binding name and conversation ID, combines them into one deterministic string, and returns that name.

**Call relations**: Trigger listing, trigger lookup, and trigger apply all use this rule. This prevents duplicate or misleading trigger names for the same source-conversation pair.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 820–854)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when source triggers are listed. It shows who owns each trigger, which source it watches, how it delivers, and where it came from.

**Data flow**: It reads reported triggers from the trigger store, rebuilds current source bindings for summaries, fetches creator emails, computes each trigger name, and returns OwnedRow entries with useful fields such as conversation, source, delivery, origin, owner email, and whether it is mine.

**Call relations**: The member-readable object framework calls this for trigger lists. It pulls from both trigger storage and source binding reconstruction so listings remain readable even if a source has changed.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 856–891)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed object response for one source trigger. It shows the trigger spec and links to the watched source and, for current delivery, the reporting conversation.

**Data flow**: It finds the trigger by name and checks it matches the expected generation. If valid, it returns an ObjectDetail with the source/delivery spec, timestamps, and object links; otherwise it returns None.

**Call relations**: The object framework calls this after finding a visible trigger row. It relies on _find to locate the trigger and then creates links that help users navigate what the trigger watches and where it reports.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 893–907)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports readable status fields for one source trigger. This gives the same practical details as listing, focused on one trigger.

**Data flow**: It finds the trigger, verifies its generation, fetches the creator’s email, compares the creator with the current authority, and returns conversation, source, delivery, origin, owner email, and mine fields.

**Call relations**: The trigger object status path calls this. It uses _find for lookup and authority helpers to say whether the current caller created the trigger.

*Call graph*: calls 1 internal fn (_find); 2 external calls (authority_member_id, owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 909–946)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new trigger for the current conversation to watch a shared source. Existing triggers cannot be edited into different triggers; they must be deleted and recreated.

**Data flow**: It computes the expected trigger name from the submitted source and current conversation. It rejects wrong names, returns quietly for an identical existing trigger, refuses edits, checks that the source is watchable, creates the trigger row, then rechecks that the source still exists and cleans up if it disappeared.

**Call relations**: The base object apply flow calls this for authorized trigger mutations. It uses _watchable before creation, the trigger store to create the row, and _binding_named after creation to guard against a race with source deletion.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 2 external calls (__init__, authority_member_id).


##### `SourceTriggerObjects._watchable`  (lines 948–961)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the requested source can be watched by a trigger. Only visible shared sources are allowed.

**Data flow**: It asks the source object store for the named source. If it is missing, it raises an unknown-object error; if it is private, it raises a clear refusal; otherwise it returns successfully.

**Call relations**: SourceTriggerObjects._apply_owned calls this before creating a trigger. It deliberately relies on the source object’s own visibility rules so guessing a private source name does not reveal hidden information.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 963–967)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes one source trigger, while protecting against deleting a row that changed underneath the caller.

**Data flow**: It finds the trigger by name, checks that its stored generation matches the owner generation supplied by the object framework, and removes that exact trigger from the trigger store.

**Call relations**: The base object delete flow calls this after permission checks. It uses _find for safe lookup and _require_triggers to perform the actual removal.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 969–977)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds one listed trigger by its derived object name. This centralizes trigger lookup so get, status, and delete use the same naming rule.

**Data flow**: It reads reported triggers from the trigger store, derives each trigger’s name from its binding and conversation, and returns the matching listed trigger or None.

**Call relations**: Trigger detail, status, and delete operations call this. It uses trigger_name so lookup matches listing and apply naming exactly.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 980–1030)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes the conversations whose triggers care about those sources. It is the event hook that turns source sync results into agent notifications.

**Data flow**: It receives a hook payload, verifies it is a page-change batch, maps changed source IDs back to bindings, groups changes by binding, finds triggers for each binding, filters to shared changes the trigger’s agent may read, and fires each authorized trigger while ignoring archived agents.

**Call relations**: The runtime calls this on page-change events. It rebuilds bindings with _bindings_from_ext, reads trigger subscriptions through _require_triggers, and hands each deliverable batch to _fire_trigger.

*Call graph*: calls 3 internal fn (_bindings_from_ext, _fire_trigger, _require_triggers); 2 external calls (__init__, suppress).


##### `_fire_trigger`  (lines 1033–1091)

```
async def _fire_trigger(ext: ExtensionContext, binding: _Binding, trigger: SourceTrigger, audience: Audience, authorized: list[PageChange]) -> None
```

**Purpose**: Delivers one trigger’s authorized page changes to an agent conversation. It supports either waking the current conversation once per batch or opening a stable conversation per changed page.

**Data flow**: It receives the extension context, binding, trigger, audience, and authorized changes. For current delivery it writes one change log and invokes the trigger’s conversation; for per-page delivery it opens or reuses one conversation per page, writes a per-page change log, and invokes the agent there.

**Call relations**: on_page_change calls this after filtering changes. It uses _write_change_log to store details and _alert_message to create the short message sent through the extension context’s invoke API.

*Call graph*: calls 4 internal fn (invoke, open_conversation, _alert_message, _write_change_log); called by 1 (on_page_change); 2 external calls (conversation_audience, authority_from_member_id).


##### `_write_change_log`  (lines 1094–1129)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the full list of changed pages to a runtime file, one JSON record per line. This keeps alert messages short while still giving the agent exact page references to inspect.

**Data flow**: It receives a conversation ID, binding, timestamp label, and page changes. If file storage is unavailable it returns None; otherwise it serializes each change with page ref, stream, title, disposition, and as-of time, writes the file, prunes older runtime files in that directory, and returns the path.

**Call relations**: _fire_trigger calls this before invoking the agent. It uses _disposition to label each change as added, updated, or removed.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_fire_trigger); 1 external calls (dumps).


##### `_disposition`  (lines 1132–1138)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Classifies what happened to one changed page: added, updated, or removed. This makes alerts and change logs easier to read.

**Data flow**: It reads the page-change flags and timestamps. Tombstones become removed; otherwise a change whose created time equals its changed time becomes added; all other non-tombstone changes become updated.

**Call relations**: _write_change_log uses this for each JSON line, and _stream_counts uses it when summarizing changes in the alert message.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1141–1155)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes changes by stream, such as how many pages were added, updated, or removed. This gives the alert a compact overview.

**Data flow**: It loops through page changes, classifies each with _disposition, counts them by stream and disposition, and returns a readable semicolon-separated summary.

**Call relations**: _alert_message calls this to explain what changed without naming every page when there are many changes.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1158–1177)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Creates the message sent to an agent when a watched source changes. It balances brevity with enough detail for the agent to fetch the changed pages.

**Data flow**: It receives the binding, changed pages, and optional change-log path. For small batches it names each page directly; for larger batches it points to the log file when available or tells the agent how to list pages; then it adds stream counts and a prompt to explain what matters.

**Call relations**: _fire_trigger calls this immediately before invoking the agent. It uses the binding summary, _stream_counts, and _page_reference to build a useful notification.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (_fire_trigger).


##### `_page_reference`  (lines 1180–1183)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference the agent can pass to object_get. It also includes a short title so the reference is recognizable.

**Data flow**: It reads the page ID and title from a PageChange, trims the title or substitutes a fallback label, and returns a string like a page object reference with the label in parentheses.

**Call relations**: _alert_message calls this when a change batch is small enough to list pages directly in the alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1186–1226)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes the tenant API URL for providers that need one. This prevents unsafe or malformed URLs from being stored and later used by sync jobs.

**Data flow**: It reads the provider’s connector settings and submitted base URL. Fixed-host providers must not receive an override; tenant-host providers must provide an HTTPS URL matching that provider’s allowed host and path shape, with no username, password, port, query, or fragment. It returns a normalized URL or None.

**Call relations**: SourceObjects._apply_owned calls this before account resolution and registration. It uses URL parsing plus the provider-specific rules defined in this file to produce clear validation errors.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).

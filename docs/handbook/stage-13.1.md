# Object system and permission-safe mutations  `stage-13.1`

This stage is shared behind-the-scenes support for the whole workspace. It is the system’s rulebook for “objects,” meaning named records such as agents, members, pages, reports, skills, connected accounts, and hosted sites. Before anything can be shown or changed, this layer checks what kind of object it is, who may see it, what actions are allowed, and whether the change should be recorded first.

The central objects file is the main gate. It lets extensions register object kinds, lists and reads objects, routes create/update/delete requests, blocks changes to read-only records, and logs planned mutations before they happen. The object name file makes sure every object identity has a safe, predictable kind and name, like checking an address before delivering mail. The object views file turns internal actions into safe descriptions for the AI model or user interface, including what inputs each action accepts.

The sub-stages plug real object families into this gate: workspace and member records, agents and prompt approvals, work items like monitors and reports, knowledge pages and memory, plus connected accounts, sites, and user-created skills. Together they make many different assets behave consistently and safely.

## Sub-stages

- [Built-in workspace and host object kinds](stage-13.1.1.md) `stage-13.1.1` — 5 files
- [Agent objects and prompt governance](stage-13.1.2.md) `stage-13.1.2` — 2 files
- [Workflow, notification, monitor, and report objects](stage-13.1.3.md) `stage-13.1.3` — 5 files
- [Knowledge source, memory, and page objects](stage-13.1.4.md) `stage-13.1.4` — 4 files
- [Connected accounts, hosted sites, and user-created skills](stage-13.1.5.md) `stage-13.1.5` — 4 files

## Files in this stage

### Object identity and actions
Defines the workspace object system, its safe object identities, and the filtered action descriptions exposed to models and users.

### `core/src/ufo/runtime/objects.py`

`domain_logic` · `startup validation and request handling`

A workspace object is a durable item with a kind, a name, and a spec, where the spec is structured data validated by that kind. This file is like the front desk for all such objects. Extensions bring their own storage and rules, but core checks the envelope first: the kind must exist, the name must be valid, the spec must be safe to show back in transcripts, and the requested verb must be allowed.

The file also supplies common behavior that object kinds should not have to rewrite. It provides listing with search, filters, sorting, and cursors. It provides ownership gates for member-owned objects, so private rows stay hidden and shared rows can be seen. It supports generation checks, which are like matching a ticket number before changing a record, to avoid overwriting something that changed after it was read.

At runtime, `ObjectVerbs` exposes tools such as `object_list`, `object_get`, `object_apply`, and `object_delete`. These tools resolve the kind, bind the call to the extension that owns it, optionally target another agent when permitted, call the kind's store, and return plain JSON or YAML. Before create, update, or delete, the file writes an object-change journal entry, so failed writes and retried writes have a clear audit trail.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 195–200)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved list cursor has the right kind of value for its sort category. This prevents a caller from resuming a paged list with a malformed or inconsistent cursor.

**Data flow**: It reads the cursor's rank and value inside the cursor object. If the pair makes sense, it returns the cursor unchanged; otherwise it raises an error explaining that the cursor value does not match its rank.

**Call relations**: This is used automatically when `_ObjectCursor` is built or decoded during `object_page`. It protects the paging flow before the cursor is trusted.


##### `object_page`  (lines 203–286)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared listing rules for object rows: search, exact filters, sorting, and page size. Object kinds can hand it simple rows and get consistent list behavior without reimplementing it.

**Data flow**: It receives rows and a list query. It verifies that row fields are declared and do not collide with reserved names, filters and searches the rows, sorts them using `_sortable`, cuts the result to one page, and returns an `ObjectPage` with an optional cursor for the next page.

**Call relations**: Member-owned listing paths call this after their visibility checks. It hands off sortable value decisions to `_sortable` and creates `_ObjectCursor` tokens when there are more rows to fetch.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 231–236)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of one named field on a listing row. It treats `name` and `summary` as built-in fields and everything else as a custom row field.

**Data flow**: It receives a row and a field name. It returns the row's name, summary, or the matching custom field value, using `None` when a custom field is absent.

**Call relations**: This helper lives inside `object_page`, where search, filter, and sort logic repeatedly need the same field lookup rule.


##### `_sortable`  (lines 289–302)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a value into a form that can be safely sorted across object listing rows. It rejects complex values because lists and dictionaries do not have one obvious order.

**Data flow**: It receives a JSON-like value and the field name being sorted. It returns a rank plus a simple value for `None`, booleans, numbers, and strings; otherwise it raises an error naming the bad field.

**Call relations**: `object_page` calls this while ordering rows and while creating or comparing page cursors.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 317–317)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the contract for a kind-specific store to list lightweight object rows. A concrete object kind implements this to expose searchable summaries without returning full specs.

**Data flow**: It receives a tool context and an `ObjectListQuery`. An implementation reads its own storage and returns an `ObjectPage`.

**Call relations**: `ObjectVerbs._list` calls this after resolving the object kind and binding the call to the kind's extension context.


##### `ObjectStore.get`  (lines 319–319)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the contract for reading one full object detail by name. A concrete store uses it to return the spec, timestamps, links, visibility flag, and optional generation.

**Data flow**: It receives a context and object name. An implementation reads storage and returns an `ObjectDetail`, or `None` if the object is not present or not readable.

**Call relations**: `ObjectVerbs._get`, `_apply`, `_delete`, and `action_target` rely on this read before showing, changing, deleting, or targeting an object.


##### `ObjectStore.status`  (lines 321–327)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines the contract for reading live state beside an object's saved spec. Status is extra current information, such as sync state or next scheduled time, not part of the authored spec.

**Data flow**: It receives a context, object name, and expected generation. An implementation checks the object is still current when needed and returns a JSON-like status dictionary or `None`.

**Call relations**: `ObjectVerbs._get` and `ObjectVerbs.action_target` call this after `get` so the displayed or targeted object is still safe to use.


##### `ObjectStore.apply`  (lines 329–337)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for creating or updating an object after core has validated the manifest and spec. The store decides the domain-specific write behavior.

**Data flow**: It receives a context, name, validated spec, optional old spec, and expected generation. An implementation writes or refuses the change, returning nothing on success.

**Call relations**: `ObjectVerbs._apply` calls this after journaling the intended change; member-owned base classes also funnel writes through their ownership checks before reaching kind-specific code.


##### `ObjectStore.delete`  (lines 339–345)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for deleting an object by name. The concrete store performs the actual removal or refuses it for domain reasons.

**Data flow**: It receives a context, name, and expected generation. An implementation deletes the row or raises an error, returning nothing on success.

**Call relations**: `ObjectVerbs._delete` calls this after first reading the object and journaling the intended deletion.


##### `owner_emails`  (lines 364–379)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for a set of member-owner IDs in one database query. Listing code can use this to show owner information without doing one query per row.

**Data flow**: It receives owner IDs, ignores `None`, queries the workspace member table for matching IDs, and returns a mapping from member ID to email address.

**Call relations**: Object kinds can call this while building rows for listings. It uses `workspace_tx` for database access and SQLAlchemy to build the query.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 424–432)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-owned objects while applying the shared visibility rules first. It makes sure users only see rows that are shared, owned by them, or visible because they are admins.

**Data flow**: It reads the acting member and admin status from the context, asks the subclass for owned rows, filters invisible or unlisted rows, converts them to public `ObjectRow`s, and sends them through `object_page`.

**Call relations**: `ObjectStore.list` implementations can inherit this flow. It calls subclass-provided `_owned_rows`, shared gate helpers, and then `object_page` for search and pagination.

*Call graph*: calls 5 internal fn (_listed, _owned_rows, _visible, object_page, speaker_is_admin); 2 external calls (__init__, authority_member_id).


##### `MemberOwnedObjects.get`  (lines 434–445)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the acting user is allowed to see it. Hidden objects look the same as missing objects.

**Data flow**: It finds the row owner, checks visibility using the acting member and admin status, then asks the subclass for the detail. If the owner carries a generation, it copies that generation into the returned detail.

**Call relations**: The main object read path can call stores built from this base. It relies on `_owner` and `_detail`, which subclasses provide through row data and storage reads.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 2 external calls (replace, authority_member_id).


##### `MemberOwnedObjects.status`  (lines 447–471)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object while protecting against stale reads and permission leaks. It checks both before and after the status read that the same row is still current and visible.

**Data flow**: It receives a name and expected generation. It finds the owner, checks generation, verifies visibility, asks the subclass for status, re-reads the owner, checks generation and visibility again, and returns the status or `None`.

**Call relations**: `ObjectVerbs._get` uses store status after reading detail. This method calls `_owner`, `_status`, `_visible`, and `_require_current_generation` to keep the status matched to the same object.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 2 external calls (__init__, authority_member_id).


##### `MemberOwnedObjects.apply`  (lines 473–499)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin exceptions, speaker requirements, and generation checks. It is the common write gate for member-owned kinds.

**Data flow**: It receives the new spec, optional old spec, and expected generation. It finds any current owner, checks visibility and freshness, checks whether a live speaker is required, verifies ownership or allowed admin behavior, then delegates the actual write to `_apply_owned`.

**Call relations**: `ObjectVerbs._apply` reaches this through the kind's store. This method centralizes permission decisions before handing off to the subclass's domain-specific write code.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `MemberOwnedObjects.delete`  (lines 501–519)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the actor may delete it. It hides invisible rows and requires owner or admin authority.

**Data flow**: It receives a name and expected generation. It finds the owner, checks freshness, refuses missing or invisible rows, checks speaker and ownership/admin requirements, then calls `_delete_owned` to perform the deletion.

**Call relations**: `ObjectVerbs._delete` reaches this through the store after journaling. The subclass supplies the actual delete operation.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `MemberOwnedObjects._owned`  (lines 521–525)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the direct owner of a row. Admin-only rows, which have no member owner, are never considered owned by an ordinary acting member.

**Data flow**: It receives an owner record and an acting member ID. It returns `true` only when the owner has a member ID and it equals the acting member ID.

**Call relations**: `_visible`, `apply`, and `delete` use this as their basic ownership test.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 527–528)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Decides whether a row can be seen by the current actor. A row is visible if it is shared, owned by the actor, or the actor is an admin.

**Data flow**: It receives an owner, acting member ID, and admin flag. It combines shared status, `_owned`, and admin status into one yes-or-no result.

**Call relations**: All member-owned read and write paths call this before exposing or acting on an existing row.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._listed`  (lines 530–538)

```
def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool
```

**Purpose**: Lets a subclass hide some otherwise visible rows from browsing while still allowing them to be addressed by name. By default, every visible row is listed.

**Data flow**: It receives an owned row and the current query. The base implementation simply returns `true`.

**Call relations**: `MemberOwnedObjects.list` calls this after visibility checks. Subclasses can override it when their kind has rows that should not appear in normal listings.

*Call graph*: called by 1 (list).


##### `MemberOwnedObjects._admin_can_apply`  (lines 540–541)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass allow a limited admin update to someone else's row. By default, admins cannot update member-owned rows unless they own them or the row is admin-only.

**Data flow**: It receives the old and new specs. The base implementation returns `false`, meaning no special admin edit is allowed.

**Call relations**: `MemberOwnedObjects.apply` calls this when deciding whether a non-owner admin may update an existing member-owned object.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 543–560)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Refuses an operation if the object under a name has changed since it was read. This avoids changing or displaying live state for the wrong generation of a generated row.

**Data flow**: It receives a name, current owner, expected generation, and action word. It compares the current generation with the expected one, and raises an error if they do not match.

**Call relations**: `status`, `apply`, and `delete` call this before sensitive reads or writes.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 562–563)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for one object name by scanning the subclass's owned rows. It is a shared helper for member-owned gates.

**Data flow**: It receives a context and object name. It asks `_owned_rows` for rows and returns the owner for the first matching name, or `None` if no row matches.

**Call relations**: `get`, `status`, `apply`, and `delete` call this before deciding visibility or ownership. Subclasses provide `_owned_rows`.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 565–566)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook where a member-owned kind supplies its known rows with owners. Subclasses must implement it because core does not know each extension's storage.

**Data flow**: It receives a tool context. A subclass returns owned rows; the base method raises `NotImplementedError`.

**Call relations**: `list` and `_owner` call this. It is the data source behind the shared member-owned gate.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 568–571)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook where a member-owned kind reads the full detail for a visible object. Subclasses implement the storage-specific read.

**Data flow**: It receives a context, object name, and owner. A subclass returns object detail or `None`; the base method raises `NotImplementedError`.

**Call relations**: `get` calls this only after ownership and visibility checks pass.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 573–576)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Abstract hook where a member-owned kind reads live status for an object. The base class surrounds it with visibility and generation checks.

**Data flow**: It receives a context, object name, and owner. A subclass returns a JSON-like status dictionary or `None`; the base method raises `NotImplementedError`.

**Call relations**: `status` calls this between its pre-read and post-read safety checks.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 578–586)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Abstract hook where a member-owned kind performs the actual create or update after core's gate allows it. Subclasses put their domain write here.

**Data flow**: It receives context, name, new spec, old spec if any, and owner if any. A subclass writes the change; the base method raises `NotImplementedError`.

**Call relations**: `apply` calls this only after name freshness, visibility, speaker, ownership, and admin rules are satisfied.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 588–589)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Abstract hook where a member-owned kind performs the actual deletion after core's gate allows it. Subclasses implement the storage-specific removal.

**Data flow**: It receives context, name, and owner. A subclass deletes the row; the base method raises `NotImplementedError`.

**Call relations**: `delete` calls this after all shared member-owned deletion checks pass.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 611–618)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the contract for reading one object from the member portal, outside an agent turn. Kinds implement this only if they can safely answer portal reads.

**Data flow**: It receives an extension context, object name, member ID, and admin flag. An implementation returns a `MemberObject` or `None`.

**Call relations**: Portal routes can depend on this protocol to know whether a kind supports member-facing detail pages.


##### `MemberListable.member_page`  (lines 626–633)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the contract for listing objects in the member portal. It extends member detail reading with a paged index view.

**Data flow**: It receives an extension context, member ID, admin flag, and list query. An implementation returns an `ObjectPage`.

**Call relations**: Portal index routes use this protocol for kinds that opt into member-facing listing.


##### `ConversationMemberListable.member_conversation_rows`  (lines 645–653)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines the contract for listing object grants related to a conversation for one member. It is used by kinds that can show conversation-scoped object access.

**Data flow**: It receives an extension context, conversation ID, member ID, admin flag, and limit. An implementation returns grant rows with names, generations, and content visibility.

**Call relations**: Conversation-facing code can call this protocol without knowing the storage details of each object kind.


##### `MemberReadableObjects.member_page`  (lines 666–679)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Implements member-portal listing for member-owned objects using the same visibility rules as turn-time listing. This keeps portal and tool behavior aligned.

**Data flow**: It receives an extension context, member ID, admin flag, and query. It asks `_member_rows` for rows, filters visible and listed rows, converts them to `ObjectRow`s, and returns `object_page` output.

**Call relations**: This is the portal-side counterpart to `MemberOwnedObjects.list`. It delegates row production to `_member_rows` and shared pagination to `object_page`.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 681–699)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Implements member-portal detail reading for member-owned objects. It returns both the list row and the full detail when the member may see the object.

**Data flow**: It receives an extension context, name, member ID, and admin flag. It finds the matching member row, checks visibility, asks `_member_object` for detail, and returns a `MemberObject` or `None`.

**Call relations**: Portal detail routes use this path. It shares the same subclass hooks that the turn-side `_owned_rows` and `_detail` adapt to.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 701–702)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Adapts portal-style member rows into the turn-time member-owned store interface. This prevents separate row logic for portal reads and tool reads.

**Data flow**: It receives a tool context, extracts the acting member ID, and calls `_member_rows` with the context's extension.

**Call relations**: `MemberOwnedObjects.list` and `_owner` call this through inheritance. The real data comes from the subclass's `_member_rows`.

*Call graph*: calls 1 internal fn (_member_rows); 1 external calls (authority_member_id).


##### `MemberReadableObjects._detail`  (lines 704–709)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Adapts portal-style object detail reading into the turn-time member-owned store interface. It keeps detail reads consistent across portal and tool paths.

**Data flow**: It receives a tool context, name, and owner. It extracts the acting member ID and calls `_member_object` with the context's extension.

**Call relations**: `MemberOwnedObjects.get` calls this through inheritance. The subclass supplies `_member_object`.

*Call graph*: calls 1 internal fn (_member_object); 1 external calls (authority_member_id).


##### `MemberReadableObjects._member_rows`  (lines 711–714)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook where a member-readable kind supplies rows for one member. Subclasses implement it using their own extension storage.

**Data flow**: It receives an extension context and optional member ID. A subclass returns owned rows; the base method raises `NotImplementedError`.

**Call relations**: `member_page`, `member_detail`, and `_owned_rows` call this as their row source.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 716–724)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook where a member-readable kind supplies full detail for one object. Subclasses implement it using their own storage and privacy rules.

**Data flow**: It receives an extension context, object name, owner, and optional member ID. A subclass returns detail or `None`; the base method raises `NotImplementedError`.

**Call relations**: `member_detail` and `_detail` call this after row lookup and visibility checks.

*Call graph*: called by 2 (_detail, member_detail).


##### `action_registry`  (lines 777–822)

```
def action_registry(bound: tuple[BoundAction, ...], kinds: Mapping[str, BoundKind]) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Validates and indexes object actions declared by core or extensions. It is a startup safety check that catches bad action names, duplicate bindings, unsafe input models, and actions targeting unknown kinds.

**Data flow**: It receives bound actions and the known object kinds. It checks each action's binding, names, reserved fields, tool declaration, and input model, then returns a nested map from kind to action name to bound action.

**Call relations**: This runs after manifests are collected. It calls `validate_tool_declaration` and `_validate_spec_model` before actions are exposed through `ObjectVerbs`.

*Call graph*: calls 1 internal fn (_validate_spec_model); 2 external calls (fullmatch, validate_tool_declaration).


##### `object_registry`  (lines 825–848)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Validates and indexes all object kinds for a deployment. It prevents two extensions from claiming the same kind name and rejects unsafe spec models before serving starts.

**Data flow**: It receives bound kinds. It checks kind-name grammar, duplicate names, declared agent-target verbs, and spec model safety, then returns a map from kind name to bound kind.

**Call relations**: This is the boot gate for `ObjectVerbs`. It calls `_validate_spec_model` so object specs are safe to store, render, and echo.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 851–870)

```
def _validate_spec_model(label: str, spec_model: type[BaseModel]) -> None
```

**Purpose**: Checks that a Pydantic spec model is safe for workspace objects. Specs are shown back to users, so this rejects unknown extra fields, secret-bearing fields, and models that cannot become JSON.

**Data flow**: It receives a label and model class. It walks reachable nested models, checks their configuration and fields, asks Pydantic for a JSON schema, and raises a clear error if anything is unsafe.

**Call relations**: `object_registry` uses this for object specs, and `action_registry` uses it for action input models. It depends on `_reachable_models` and `_annotation_types` to inspect nested types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 2 (action_registry, object_registry).


##### `_reachable_models`  (lines 873–887)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds a Pydantic model and any nested Pydantic models used inside its fields. This lets validation cover the whole spec shape, not just the top level.

**Data flow**: It receives a model class, walks through field annotations, follows nested model types, avoids repeats, and returns all discovered models.

**Call relations**: `_validate_spec_model` calls this before checking model configuration and secret fields. It uses `_annotation_types` to unpack complex type annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 890–897)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a type annotation into the concrete pieces inside it. For example, it can look through containers or unions to find nested model or secret types.

**Data flow**: It receives an annotation. If it has no type arguments, it returns the annotation itself; otherwise it recursively returns all nested argument types.

**Call relations**: `_reachable_models` and `_validate_spec_model` call this while inspecting Pydantic fields.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectGetInput.validate_ref`  (lines 923–926)

```
def validate_ref(cls, value: str) -> str
```

**Purpose**: Validates that a non-empty object reference uses the canonical object reference format. Empty references are allowed because they mean “this turn's agent.”

**Data flow**: It receives the `ref` string from tool input. If it is non-empty, it parses it as an `ObjectRef`; then it returns the original string.

**Call relations**: Pydantic runs this when `ObjectGetInput` is created. `ObjectVerbs._get` can then trust the reference format or handle the empty-ref case.

*Call graph*: calls 1 internal fn (parse).


##### `ObjectVerbs.tools`  (lines 1003–1089)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the tool definitions that expose object operations to the runtime. These are the public verbs for listing, reading, explaining, applying, deleting, and invoking object actions.

**Data flow**: It reads the `ObjectVerbs` instance and creates `ToolDef` objects with names, descriptions, input models, handlers, and safety flags. It returns them as a tuple.

**Call relations**: The tool registry calls this to publish object tools. Each tool points back to a handler method such as `_list`, `_get`, `_apply`, or `_delete`.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 1091–1158)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the `object_list` tool. It either returns the catalog of registered object kinds or lists instances of one chosen kind.

**Data flow**: It receives tool context and list input. With no kind, it validates that no instance-only filters were supplied and returns kind descriptions; with a kind, it resolves the kind, checks any agent target, calls the store's list method, and returns rows, actions, cursors, and target information as JSON.

**Call relations**: This is called by the `object_list` `ToolDef`. It uses `_resolve`, `_target`, `_bound_ctx`, `_action_views`, `_instance_action_discoveries`, and `_json_result` to assemble the response.

*Call graph*: calls 7 internal fn (_action_views, _bound_ctx, _granted_actions, _instance_action_discoveries, _resolve, _target, _json_result); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._get`  (lines 1160–1224)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the `object_get` tool. It reads one object's full spec, live status, links, timestamps, generation, and available actions.

**Data flow**: It receives context and get input. It parses the ref or resolves an empty ref to the current agent, resolves the kind, checks any agent target, reads detail and status from the store, renders links and actions, and returns YAML text.

**Call relations**: This is called by the `object_get` `ToolDef`. It uses `_resolve`, `_bound_ctx`, `_target`, and `_action_views`, and it calls the kind store's `get` and `status` methods.

*Call graph*: calls 5 internal fn (parse, _action_views, _bound_ctx, _resolve, _target); 8 external calls (__init__, __init__, __init__, __init__, select, workspace_tx, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1226–1241)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the `object_explain` tool. It tells a caller how to author and use one object kind before creating or updating it.

**Data flow**: It receives context and a kind name. It resolves the kind and returns its description, guidance, allowed agent-target verbs, name rule, spec JSON schema, and action views.

**Call relations**: This is called by the `object_explain` `ToolDef`. It uses `_resolve`, `_action_views`, and `_json_result`.

*Call graph*: calls 3 internal fn (_action_views, _resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1243–1308)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the `object_apply` tool for creating or updating objects from a YAML manifest. It validates the manifest and spec, journals the intended change, and then asks the kind's store to write it.

**Data flow**: It receives context and apply input. It parses the YAML envelope, resolves the kind, checks any agent target, validates the object name and spec, reads any existing object, records a change journal row, calls store `apply`, withdraws the journal if the store fails, and returns a created-or-updated result.

**Call relations**: This is called by the `object_apply` `ToolDef`. It relies on `_parse_envelope`, `_resolve`, `_target`, `_bound_ctx`, `_journal_object_change`, `_withdraw_object_change`, and `_json_result`.

*Call graph*: calls 7 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _parse_envelope, _withdraw_object_change); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1310–1345)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the `object_delete` tool. It deletes one object and returns the deleted spec when the caller was allowed to see it.

**Data flow**: It receives context and delete input. It resolves the kind, checks any agent target, reads the current object, journals the delete, calls store `delete` with the observed generation, withdraws the journal if deletion fails, and returns a JSON result with the old spec.

**Call relations**: This is called by the `object_delete` `ToolDef`. It uses `_resolve`, `_bound_ctx`, `_target`, `_journal_object_change`, `_withdraw_object_change`, and `_json_result`.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _withdraw_object_change); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._object_action`  (lines 1347–1348)

```
async def _object_action(self, ctx: ToolContext, args: ObjectActionInput) -> ToolResult
```

**Purpose**: Marks the schema entry for `object_action`, but it is not meant to run directly. The engine dispatches object actions through a separate resolved path.

**Data flow**: It receives context and action input, then immediately raises a runtime error saying this path should not be reached.

**Call relations**: The `object_action` `ToolDef` points here for exposure, but real action dispatch uses engine resolution and `ObjectVerbs.action_target` instead.


##### `ObjectVerbs._granted_actions`  (lines 1350–1358)

```
def _granted_actions(self, ctx: ToolContext, kind: str) -> dict[str, BoundAction]
```

**Purpose**: Filters a kind's registered actions down to the actions the current turn is allowed to see. This keeps action discovery tied to granted canonical action IDs.

**Data flow**: It receives context and kind name. It looks up actions for the kind, keeps only those whose canonical IDs are in `ctx.granted_actions`, and returns them by short name.

**Call relations**: `_list`, `_action_views`, and `_instance_action_discoveries` call this before showing actions to the caller.

*Call graph*: called by 3 (_action_views, _instance_action_discoveries, _list).


##### `ObjectVerbs._action_views`  (lines 1360–1390)

```
def _action_views(self, ctx: ToolContext, kind: str, binding: str, *, name: str | None=None, agent: str | None=None, generation: UUID | None=None) -> list[JsonValue]
```

**Purpose**: Builds ready-to-call action templates for one kind and binding. These templates show the caller exactly how to invoke available collection or instance actions.

**Data flow**: It receives context, kind, binding type, and optional instance name, agent, and generation. It filters granted actions by binding, fixed target name, and agent support, then renders each with `action_view`.

**Call relations**: `_list`, `_get`, and `_explain` call this when including available actions in responses. It first narrows actions through `_granted_actions`.

*Call graph*: calls 1 internal fn (_granted_actions); called by 3 (_explain, _get, _list); 1 external calls (action_view).


##### `ObjectVerbs._instance_action_discoveries`  (lines 1392–1414)

```
def _instance_action_discoveries(self, ctx: ToolContext, kind: str, *, agent: str | None) -> list[JsonValue]
```

**Purpose**: Builds action summaries for instance actions when listing a kind. This lets a caller know which actions may appear on individual objects before reading one.

**Data flow**: It receives context, kind, and optional agent name. It filters granted instance actions, skips non-agent-targetable actions for agent-targeted views, and returns names, descriptions, input schemas, and optional fixed target names.

**Call relations**: `_list` calls this to include `instance_actions` beside a kind's object rows. It uses `_granted_actions` for permission filtering.

*Call graph*: calls 1 internal fn (_granted_actions); called by 1 (_list); 1 external calls (__init__).


##### `ObjectVerbs.action_target`  (lines 1416–1460)

```
async def action_target(self, ctx: ToolContext, action: ToolDef, wire: ObjectActionInput) -> ObjectActionTarget
```

**Purpose**: Resolves the object or collection that an object action will act on before the action handler runs. It checks agent targeting and, for instance actions, verifies that the target object exists and can be read.

**Data flow**: It receives context, the chosen action definition, and the wire input. It checks the action binding, resolves any agent target, optionally reads the target object's detail and status through the owning kind, and returns an `ObjectActionTarget` carrying live and expected generation information.

**Call relations**: The engine uses this during object-action dispatch. It calls `_agent_gate`, `_resolve`, `_bound_ctx`, and the kind store's `get` and `status` for instance actions.

*Call graph*: calls 3 internal fn (_agent_gate, _bound_ctx, _resolve); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1462–1467)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Finds the registered object kind for a kind name. If the kind is unknown, it raises an error listing what is registered.

**Data flow**: It receives a kind string, looks it up in the registry, and returns the bound kind or raises `UnknownKind`.

**Call relations**: All main object flows call this before dispatching: list, get, explain, apply, delete, and action targeting.

*Call graph*: called by 6 (_apply, _delete, _explain, _get, _list, action_target); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1469–1470)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension context that owns a kind. This ensures the store handler runs with its own extension capabilities and data.

**Data flow**: It receives the current tool context and a bound kind. It returns a copy of the context with `ext` replaced by the bound kind's extension context.

**Call relations**: `_list`, `_get`, `_apply`, `_delete`, and `action_target` call this before invoking a kind's store.

*Call graph*: called by 5 (_apply, _delete, _get, _list, action_target); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1472–1485)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Resolves an optional agent target for object CRUD calls and checks whether the kind allows that verb to target another agent. If no agent name is supplied, it means the current agent.

**Data flow**: It receives context, bound kind, target agent name, and the requested verb set. It returns `None` for the current agent, raises if the kind rejects targeting for that verb, or calls `_agent_gate` to resolve the named agent.

**Call relations**: `_list`, `_get`, `_apply`, and `_delete` use this before entering an agent-scoped object operation.

*Call graph*: calls 1 internal fn (_agent_gate); called by 4 (_apply, _delete, _get, _list).


##### `ObjectVerbs._agent_gate`  (lines 1487–1536)

```
async def _agent_gate(self, ctx: ToolContext, name: str) -> ObjectAgent | None
```

**Purpose**: Checks whether this turn may target another agent and resolves that agent's ID and stable name. It enforces that only the workspace main agent, on an exact live member request, can target a different visible agent.

**Data flow**: It receives context and an agent name. It reads the current agent from the database, returns `None` if the target is the current agent, checks main-agent and speaker rules, checks member admin status, looks up a visible unarchived target agent, and returns an `ObjectAgent`.

**Call relations**: `_target` and `action_target` call this whenever a caller names an agent. It uses workspace database transactions and `member_is_admin` for the visibility gate.

*Call graph*: called by 2 (_target, action_target); 6 external calls (__init__, __init__, or_, select, workspace_tx, member_is_admin).


##### `_journal_object_change`  (lines 1539–1593)

```
async def _journal_object_change(ctx: ToolContext, kind: str, name: str, verb: Literal['create', 'update', 'delete'], before: BaseModel | None, after: BaseModel | None, agent_id: UUID) -> UUID | None
```

**Purpose**: Writes an audit record before an object create, update, or delete is attempted. This makes mutations traceable and supports safe retries using an idempotency key.

**Data flow**: It receives context, kind, name, verb, before spec, after spec, and agent ID. It derives or creates a change ID, checks if that journal row already exists, inserts the change with JSON specs and caller information if new, and returns the new ID or `None` if it already existed.

**Call relations**: `ObjectVerbs._apply` and `_delete` call this before store mutation. If the later store call fails and this attempt inserted the row, they call `_withdraw_object_change`.

*Call graph*: called by 2 (_apply, _delete); 7 external calls (dumps, model_dump, insert, select, workspace_tx, uuid4, uuid5).


##### `_withdraw_object_change`  (lines 1596–1603)

```
async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None
```

**Purpose**: Removes a journal row that was written for a mutation attempt that did not succeed. This keeps the change log from claiming a write happened when the store refused it.

**Data flow**: It receives context and change ID. It deletes the matching change row for the current workspace.

**Call relations**: `ObjectVerbs._apply` and `_delete` call this in their error path after a failed store mutation.

*Call graph*: called by 2 (_apply, _delete); 2 external calls (delete, workspace_tx).


##### `_parse_envelope`  (lines 1606–1632)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]
```

**Purpose**: Parses and validates the YAML manifest used by `object_apply`. It makes sure the manifest has exactly the expected top-level shape before spec validation starts.

**Data flow**: It receives manifest text. It checks byte size, parses YAML, requires `kind`, `name`, and `spec` plus optional `generation`, checks types, parses generation as a UUID when present, and returns kind, name, spec mapping, and generation.

**Call relations**: `ObjectVerbs._apply` calls this first, before resolving the kind or validating the spec model.

*Call graph*: called by 1 (_apply); 3 external calls (__init__, UUID, safe_load).


##### `_json_result`  (lines 1635–1636)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a JSON text tool result. It is a small helper for object tools that return machine-readable JSON.

**Data flow**: It receives a payload mapping, converts it to a JSON string, wraps that string in `TextContent`, and returns a `ToolResult`.

**Call relations**: `_list`, `_explain`, `_apply`, and `_delete` use this to format their responses consistently.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/runtime/object_name.py`

`data_model` · `cross-cutting`

The system needs a single shared rule for naming objects. Without this file, different parts of the code could accept different spellings, and an object might be saved under a name that later cannot be read, linked to, or understood. This file acts like the naming office for the runtime: it says what forms are allowed and rejects anything outside those rules.

There are two main ideas here. A “kind” is the category of object, such as an agent. It must look like a simple lowercase identifier. A “name” is the object’s own name inside that category. It can contain lowercase letters, numbers, and hyphens, with a length limit. There is also one special reserved name shape for archived agent objects.

The standalone validate_object_name function checks caller-supplied object names before they are written. The ObjectRef model then packages a kind, a name, and optionally an agent name into one frozen value, meaning it cannot be changed after creation. This is useful because an object reference should behave like an address on an envelope: once written, other code can trust it. The class also knows how to turn itself into the common text form “kind/name” and how to parse that text form back into a checked ObjectRef.

#### Function details

##### `validate_object_name`  (lines 25–33)

```
def validate_object_name(name: str) -> None
```

**Purpose**: This function checks whether a proposed object name follows the shared object-name rules. It is used before saving a caller-provided name, so bad names are refused at the point where they enter the system.

**Data flow**: It receives a text name. It checks the name length and whether the characters match the allowed pattern. If the name is valid, nothing is returned and processing can continue; if it is invalid, it raises an InvalidName error explaining the rule that was broken.

**Call relations**: This is the direct gatekeeper for raw object names. When some write path wants to persist an object under a user- or caller-supplied name, it can call this function before the name becomes part of stored state.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 49–52)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: This validator makes sure the kind part of an object reference is written in the allowed form. It protects the system from object categories that cannot be safely compared, stored, or rendered.

**Data flow**: It receives the proposed kind string while an ObjectRef is being built. It checks that the kind starts with a lowercase letter and then uses only lowercase letters, numbers, or underscores. If the kind passes, the same value is kept; if not, ObjectRef creation fails with an error.

**Call relations**: This runs automatically as part of creating an ObjectRef. Any code that constructs an ObjectRef, including parsing from text, gets this check without needing to call it by hand.


##### `ObjectRef.validate_name`  (lines 56–65)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: This validator checks the name part of an object reference. It allows normal object names and also allows a special reserved archived-name form used for agent objects.

**Data flow**: It receives the proposed name string while an ObjectRef is being built. It checks the maximum length and then accepts either the normal object-name pattern or the reserved archived-name pattern. If accepted, the name is kept; otherwise, ObjectRef creation fails with a clear validation error.

**Call relations**: This runs automatically whenever an ObjectRef is created. It works together with ObjectRef.validate_reserved_name_kind, which adds the extra rule that reserved archived names may only belong to the agent kind.


##### `ObjectRef.validate_reserved_name_kind`  (lines 68–71)

```
def validate_reserved_name_kind(self) -> 'ObjectRef'
```

**Purpose**: This validator enforces the special rule for archived object names. A reserved archived-looking name is only valid when the object kind is agent.

**Data flow**: It receives the already-built ObjectRef data after the individual fields have been checked. It looks at the kind and name together. If the name has the reserved archived shape but the kind is not agent, creation fails; otherwise, the ObjectRef is returned unchanged.

**Call relations**: This is the final consistency check during ObjectRef creation. It depends on the earlier field checks having confirmed that the kind and name are each individually well-formed.


##### `ObjectRef.__str__`  (lines 73–74)

```
def __str__(self) -> str
```

**Purpose**: This turns an ObjectRef into its standard readable text form. The result is the compact spelling used when people or other code need to display the reference as “kind/name”.

**Data flow**: It reads the ObjectRef’s kind and name fields. It joins them with a slash. The returned string does not include the optional agent field.

**Call relations**: This is used whenever Python needs the string form of an ObjectRef. It complements ObjectRef.parse, which performs the opposite move by turning the “kind/name” spelling back into a checked ObjectRef.


##### `ObjectRef.parse`  (lines 77–82)

```
def parse(cls, value: str) -> 'ObjectRef'
```

**Purpose**: This class method reads the standard “kind/name” text spelling and turns it into an ObjectRef. It is useful when object references arrive as strings from inputs or wire-style data.

**Data flow**: It receives a text value. It splits the value on the slash and requires exactly two pieces: kind and name. It then builds an ObjectRef from those pieces, which triggers the normal validation rules; the result is a trusted ObjectRef object, or an error if the text is malformed.

**Call relations**: This function is called by object-runtime code when a reference is supplied as text, such as during object-get input validation and object retrieval. After parsing, it hands the rest of the flow a structured ObjectRef instead of a loose string.

*Call graph*: called by 2 (validate_ref, _get).


### `core/src/ufo/runtime/object_views.py`

`domain_logic` · `model discovery and portal/request handling`

An action in the runtime may be rich internal data: it has a name, description, input model, binding rules, presentation settings, and a prepared way to call it. This file creates a simpler public “view” of that action. The view is like a menu card: it tells someone what the action is called, what it does, what input it accepts, and includes a pre-filled call template so the action can be invoked correctly later.

The central data shape is `ActionView`, a Pydantic model. Pydantic is a library that validates and shapes data. Here it is configured to be frozen, meaning it cannot be changed after creation, and to reject unexpected fields. That helps keep action descriptions predictable when shared between the runtime, model discovery, and portal controls.

The helper functions filter actions carefully. Some actions are meant only for internal profiles, while others have presentation metadata that says they should appear as buttons or controls. The file also gathers the action IDs that an embedded app page is allowed to call. In short, this file is a gate and translator: it turns internal capabilities into clear, limited, user-facing action descriptions.

#### Function details

##### `action_view`  (lines 27–53)

```
def action_view(kind: str, bound: 'BoundAction', *, name: str | None=None, agent: str | None=None, generation: UUID | None=None, presented: bool=False) -> ActionView
```

**Purpose**: Builds one `ActionView` from a bound action. Someone uses it when they need a clean, shareable description of an action plus a ready-made call template for invoking that action later.

**Data flow**: It receives the action kind, a bound action, and optional details such as object name, agent name, generation ID, and whether presentation details should be included. It builds a `call` dictionary with the action identity and any provided context, adds an empty input placeholder, reads the action description and input schema, and returns a validated `ActionView`. If presentation is requested, it also copies user-facing label and confirmation text.

**Call relations**: `presented_action_views` calls this when it has found an action that should be shown. After collecting the needed fields, this function hands them to `ActionView` creation so the rest of the system receives one consistent view object.

*Call graph*: called by 1 (presented_action_views); 1 external calls (__init__).


##### `presented`  (lines 56–58)

```
def presented(bound: 'BoundAction') -> bool
```

**Purpose**: Answers a simple visibility question: should this action appear as a portal control? It is used to keep hidden or profile-only actions out of user-facing views.

**Data flow**: It receives a bound action, checks whether the action has presentation information, and checks that it is not marked as profile-only. It returns `true` only when both conditions make the action suitable to show.

**Call relations**: `presented_action_views` uses this as one of its filters before building visible action views. `frame_admissible_ids` also uses it before allowing an action to be callable from an embedded app page.

*Call graph*: called by 2 (frame_admissible_ids, presented_action_views).


##### `presented_action_views`  (lines 61–77)

```
def presented_action_views(actions: 'Mapping[str, Mapping[str, BoundAction]]', kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the list of visible action views for one target, such as one object or named binding. It filters out actions that do not belong to the requested binding or should not be shown.

**Data flow**: It receives a nested collection of actions, the kind of action target to look under, the required binding, and optional object name and generation ID. It sorts the matching actions by their short names, keeps only actions that are actually bound to the requested binding and name, requires them to be presentable, and converts each survivor into an `ActionView`. It returns the resulting views as an ordered tuple.

**Call relations**: This function is the main collector in this file. It asks `presented` whether each candidate action is allowed to appear, then calls `action_view` to turn each approved action into the public shape consumed by model discovery or portal controls.

*Call graph*: calls 2 internal fn (action_view, presented).


##### `frame_admissible_ids`  (lines 80–97)

```
def frame_admissible_ids(tools: 'Iterable[ToolDef]', actions: 'Mapping[str, Mapping[str, BoundAction]]') -> tuple[str, ...]
```

**Purpose**: Finds the canonical action IDs that an embedded app page is allowed to call. This is a safety boundary: only actions explicitly marked for frame use are included.

**Data flow**: It receives global tool definitions and object-bound actions. From the tools, it keeps unbound tools that have presentation settings and are marked as usable from a frame. From the bound actions, it keeps presented actions whose presentation also allows frame use, then extracts their canonical IDs. It combines both groups, removes duplicates, sorts them, and returns the IDs as a tuple.

**Call relations**: When deciding whether bound actions belong in the allowed frame list, this function calls `presented` first. That means frame access follows the same visibility rule used elsewhere: an action must be intentionally presentable and not profile-only before it can be exposed.

*Call graph*: calls 1 internal fn (presented).

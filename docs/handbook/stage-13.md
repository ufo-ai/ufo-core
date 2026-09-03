# Workspace object system and domain object handling  `stage-13`

This stage is shared support used whenever the workspace needs to show or change its “objects,” which are durable named records such as agents, members, artifacts, connectors, sites, or todos. It is not a startup or shutdown step. It is the behind-the-scenes switchboard for list, read, explain, create, update, delete, and action requests.

The central switchboard is objects.py. It checks that each request has the right shape, finds the correct built-in or extension handler, and applies safety rules such as visibility, workspace membership, admin rights, and the agent being acted for. object_name.py gives every record a standard address like kind/name, so all parts speak the same language. listings.py keeps long lists paged cleanly. object_scope.py carries the current agent target safely during dispatch. object_views.py turns available actions into safe descriptions for the model or user interface.

The built-in object stage covers core workspace records such as members, agents, conversations, artifacts, credentials, and extensions. The extension-owned object stage lets add-ons expose their own records through the same rules. The package marker files simply make these object modules importable.

## Sub-stages

- [Built-in workspace, member, agent, conversation, artifact, and credential objects](stage-13.1.md) `stage-13.1` — 9 files
- [Extension-owned objects](stage-13.2.md) `stage-13.2` — 13 files

## Files in this stage

### Object dispatch gateway
The central object API validates, routes, and enforces rules for workspace object operations.

### `core/src/ufo/runtime/objects.py`

`domain_logic` · `startup validation and request handling`

Workspace objects are like labeled files in a shared filing cabinet. Each object has a kind, a name, and a spec, which is the structured content of the object. Extensions can add new kinds of objects, but this file makes sure they all follow the same public rules: names must be valid, specs must be safe to show back to users, listings must sort and page consistently, and edits must be recorded before they happen.

The file has two main jobs. First, it defines the contracts that object kinds must implement, such as how to list rows, read details, apply changes, and delete records. It also includes reusable permission gates for member-owned objects, so each extension does not have to reinvent rules like “owners can edit their own private rows” or “admins can see shared workspace rows.”

Second, it exposes the actual tool verbs used at runtime: object_list, object_get, object_explain, object_apply, object_delete, and object_action. These verbs look up the requested kind, bind the call to the owning extension’s context, optionally target another agent when allowed, validate inputs, journal mutations for audit and recovery, and then call the kind’s store. Without this file, object kinds would behave inconsistently, unsafe specs could leak into transcripts, and writes would be harder to audit or recover.

#### Function details

##### `_ObjectCursor.validate_rank`  (lines 195–200)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a saved list cursor contains the right kind of value for its sort category. This prevents a page token from claiming, for example, that a text value should be treated like a number.

**Data flow**: It reads the cursor’s rank and value after the cursor model has been built. If the pair makes sense, it returns the cursor unchanged; if not, it raises an error so the cursor is rejected.

**Call relations**: This validation runs automatically when an object list cursor is decoded inside object_page. It protects the paging flow before object_page uses the cursor to skip already-seen rows.


##### `object_page`  (lines 203–286)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the shared listing rules for object rows: search, exact filters, sorting, and page-sized results. Object kinds can hand it lightweight rows and get back a consistent page for users.

**Data flow**: It receives rows and a query. It checks that fields are declared and do not collide with reserved names, filters rows by search text and exact matches, sorts them using _sortable, applies any cursor, and returns an ObjectPage with rows and possibly a next cursor.

**Call relations**: MemberOwnedObjects.list and MemberReadableObjects.member_page call this after they have applied their visibility rules. object_page delegates sortable-value decisions to _sortable and creates _ObjectCursor tokens when more rows remain.

*Call graph*: calls 1 internal fn (_sortable); called by 2 (list, member_page); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 231–236)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up the value of one named listing field on one row. It gives object_page a single way to read built-in fields like name and summary as well as custom row fields.

**Data flow**: It receives a row and a field name. It returns row.name, row.summary, or the matching value from row.fields, with missing custom fields coming back as null-like None.

**Call relations**: This helper lives inside object_page and is used while filtering, searching, sorting, and building cursors. It keeps the surrounding listing code from repeating the same field lookup rules.


##### `_sortable`  (lines 289–302)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Turns a listing field value into a safe sorting key. It only allows simple values that can be ordered predictably: missing values, booleans, numbers, and strings.

**Data flow**: It receives a field value and the field’s name. It returns a pair containing a rank and a normalized value; if the value is a complex object such as a list or mapping, it raises an error.

**Call relations**: object_page calls this while sorting rows and while comparing cursor boundaries. It is the small rulebook that makes object ordering stable across all object kinds.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 317–317)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the contract for listing one kind’s objects. A store implementation uses it to return one lightweight page of rows without full object specs.

**Data flow**: It receives a tool context and an ObjectListQuery. An implementation reads its backing storage, applies or delegates listing rules, and returns an ObjectPage.

**Call relations**: ObjectVerbs._list calls this through the registered kind’s store. Concrete extensions provide the real implementation behind this protocol method.


##### `ObjectStore.get`  (lines 319–319)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the contract for reading one object’s full detail. It is used when the system needs the spec, timestamps, links, visibility flag, and optional generation fence for a named object.

**Data flow**: It receives a tool context and an object name. An implementation looks up the named row and returns ObjectDetail, or None if the object does not exist or is not visible through that store’s rules.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, ObjectVerbs._delete, and ObjectVerbs.action_target rely on this method before showing, changing, deleting, or targeting an object.


##### `ObjectStore.status`  (lines 321–327)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Defines the contract for reading live, kind-specific status beside an object’s saved spec. Status is information like current state or last sync time, not the authored spec itself.

**Data flow**: It receives a context, name, and an expected generation. An implementation checks that the object is still the same version if it uses generations, then returns a JSON-like status mapping or None.

**Call relations**: ObjectVerbs._get and ObjectVerbs.action_target call this after get. This lets the kind re-check safety before live status is displayed or an action target is accepted.


##### `ObjectStore.apply`  (lines 329–337)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for creating or updating an object after core has validated the envelope and spec. The store performs the domain-specific write.

**Data flow**: It receives a context, name, validated spec, old spec if there was one, and an expected generation. An implementation writes the change or raises a clear refusal.

**Call relations**: ObjectVerbs._apply calls this after parsing YAML, validating the spec, checking create-only rules, and recording the intended change in the journal.


##### `ObjectStore.delete`  (lines 339–345)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Defines the contract for deleting a named object. The store decides how deletion affects its own tables.

**Data flow**: It receives a context, name, and expected generation. An implementation removes the object or raises an error if deletion is unsupported, forbidden, stale, or impossible.

**Call relations**: ObjectVerbs._delete calls this after reading the object and journaling the delete. Concrete object kinds supply the actual deletion behavior.


##### `owner_emails`  (lines 364–379)

```
async def owner_emails(owners: Iterable[UUID | None]) -> dict[UUID | None, str]
```

**Purpose**: Looks up email addresses for a set of member owner IDs in one database query. Listings can use this to show who owns each row without querying once per row.

**Data flow**: It receives owner IDs, ignores empty owner values, reads matching members from the workspace database, and returns a map from member ID to email. If there are no real IDs, it returns an empty map.

**Call relations**: Object kinds can call this while building their listing rows. It uses workspace_tx for a database transaction and SQLAlchemy to select member records.

*Call graph*: 2 external calls (select, workspace_tx).


##### `MemberOwnedObjects.list`  (lines 424–432)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists member-owned objects while enforcing who is allowed to see them. It keeps private rows out of listings for people who should not know they exist.

**Data flow**: It reads whether the speaker is an admin and which member is acting, asks the subclass for owned rows, filters them through visibility and listing rules, converts them to ObjectRow values, and sends them to object_page.

**Call relations**: ObjectVerbs._list can reach this through a kind’s store. It calls subclass-provided _owned_rows, shared gate helpers like _visible and _listed, and then object_page for common paging.

*Call graph*: calls 5 internal fn (_listed, _owned_rows, _visible, object_page, speaker_is_admin); 2 external calls (__init__, authority_member_id).


##### `MemberOwnedObjects.get`  (lines 434–445)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads one member-owned object only if the acting member may see it. Invisible objects are returned as not found, which avoids revealing private names.

**Data flow**: It finds the object’s owner, checks visibility using the acting member and admin status, asks the subclass for full detail, and adds a generation value when the owner is generation-tracked.

**Call relations**: ObjectVerbs._get can call this through the store interface. It relies on _owner, _visible, and _detail, with dataclasses.replace used to attach generation information when needed.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_admin); 2 external calls (replace, authority_member_id).


##### `MemberOwnedObjects.status`  (lines 447–471)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reads live status for a member-owned object while guarding against stale reads and visibility changes. It makes sure status is not shown for a different version of a row than the one just read.

**Data flow**: It reads the owner, checks the expected generation, verifies visibility, asks the subclass for status, then reads the owner again and repeats generation and visibility checks before returning the status.

**Call relations**: ObjectVerbs._get and action-target checks can reach this through the store interface. It calls _owner, _require_current_generation, _visible, and subclass _status.

*Call graph*: calls 5 internal fn (_owner, _require_current_generation, _status, _visible, speaker_is_admin); 2 external calls (__init__, authority_member_id).


##### `MemberOwnedObjects.apply`  (lines 473–499)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership, admin exceptions, speaker requirements, and generation fences. It is the shared edit gate for this family of object kinds.

**Data flow**: It receives a validated spec and optional old spec. It reads the current owner, checks freshness, visibility, whether a live speaker is required, and whether the actor owns or may admin-edit the row, then passes the write to _apply_owned.

**Call relations**: ObjectVerbs._apply can call this through the store interface. It coordinates helper checks such as _owned, _visible, _admin_can_apply, and _require_current_generation before handing off to the subclass’s write method.

*Call graph*: calls 7 internal fn (_admin_can_apply, _apply_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `MemberOwnedObjects.delete`  (lines 501–519)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a member-owned object only when the caller can see it and has the right to remove it. It treats invisible rows as not found and can require a live speaker for sensitive deletions.

**Data flow**: It reads the owner, checks generation freshness, verifies the row exists and is visible, enforces any speaker requirement, checks owner or admin rights, then calls _delete_owned.

**Call relations**: ObjectVerbs._delete can call this through the store interface. It uses _owner, _visible, _owned, _require_current_generation, and the subclass deletion hook.

*Call graph*: calls 6 internal fn (_delete_owned, _owned, _owner, _require_current_generation, _visible, speaker_is_admin); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `MemberOwnedObjects._owned`  (lines 521–525)

```
def _owned(self, owner: OwnerT, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the actual member owner of a row. Admin-only rows have no member owner, so this deliberately returns false for them.

**Data flow**: It receives an owner and an acting member ID. It returns true only when the row has a non-empty member_id and it matches the acting member.

**Call relations**: _visible calls this to decide read access. apply and delete call it to decide whether a non-admin actor may change the row.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 527–528)

```
def _visible(self, owner: OwnerT, acting: UUID | None, is_admin: bool) -> bool
```

**Purpose**: Answers whether a row may be seen by the actor. A row is visible if it is shared, owned by the actor, or the actor is an admin.

**Data flow**: It receives owner information, the acting member ID, and an admin flag. It combines shared status, _owned, and admin status into one boolean answer.

**Call relations**: The list, get, status, apply, and delete gates all call this before revealing or changing member-owned rows.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._listed`  (lines 530–538)

```
def _listed(self, row: OwnedRow[OwnerT], query: ObjectListQuery) -> bool
```

**Purpose**: Lets a kind hide some otherwise visible rows from broad listings while still allowing direct access by name. The default is to list every visible row.

**Data flow**: It receives an owned row and the listing query. The base implementation returns true without changing anything.

**Call relations**: MemberOwnedObjects.list calls this after visibility checks. Subclasses can override it when a visible object should not appear in browsing results.

*Call graph*: called by 1 (list).


##### `MemberOwnedObjects._admin_can_apply`  (lines 540–541)

```
def _admin_can_apply(self, old: SpecT, spec: SpecT) -> bool
```

**Purpose**: Lets a subclass allow a limited admin edit to someone else’s row. The base version denies this special exception.

**Data flow**: It receives the old spec and new spec. It returns false unless a subclass overrides it with a more specific allowed-change rule.

**Call relations**: MemberOwnedObjects.apply calls this when a workspace admin is editing a row they do not own. It gives subclasses a narrow escape hatch without weakening the default gate.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._require_current_generation`  (lines 543–560)

```
def _require_current_generation(self, name: str, owner: OwnerT | None, expected_generation: UUID | None, action: str) -> None
```

**Purpose**: Refuses an operation if the object under a name has changed since the caller’s earlier read. This is like checking that a document’s version number still matches before saving over it.

**Data flow**: It receives the name, current owner, expected generation, and action wording. If the current generation differs from the expected one, or an unexpected generation is supplied for an unfenced row, it raises an error; otherwise it returns nothing.

**Call relations**: MemberOwnedObjects.status, apply, and delete call this around reads and writes. It prevents stale status display and accidental overwrite or deletion of a replaced generated row.

*Call graph*: called by 3 (apply, delete, status).


##### `MemberOwnedObjects._owner`  (lines 562–563)

```
async def _owner(self, ctx: ToolContext, name: str) -> OwnerT | None
```

**Purpose**: Finds the owner record for a named member-owned object. It is a small lookup built on the subclass’s row listing.

**Data flow**: It receives a context and name, asks _owned_rows for all known rows, searches by name, and returns the matching owner or None.

**Call relations**: get, status, apply, and delete call this before deciding visibility and permissions. It depends on _owned_rows, which subclasses must implement.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 565–566)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook for subclasses to provide the rows that exist for this kind. The base class cannot know where each extension stores its objects.

**Data flow**: It receives a tool context. A subclass should read its own storage and return OwnedRow entries; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.list calls this directly, and _owner calls it for name lookups. Concrete object kinds must provide it for the shared ownership gate to work.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 568–571)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook for subclasses to read the full detail of one visible owned object. The base gate handles permission checks; the subclass supplies the data.

**Data flow**: It receives a context, name, and owner. A subclass should return ObjectDetail or None; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.get calls this after confirming the row is visible. Concrete stores implement it using their own tables or data source.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 573–576)

```
async def _status(self, ctx: ToolContext, name: str, owner: OwnerT) -> dict[str, JsonValue] | None
```

**Purpose**: Abstract hook for subclasses to read live status for one owned object. Status is separate from the saved spec.

**Data flow**: It receives a context, name, and owner. A subclass should return a JSON-like status mapping or None; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.status calls this between generation and visibility checks. Concrete kinds implement the actual live-state lookup.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 578–586)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: OwnerT | None) -> None
```

**Purpose**: Abstract hook for subclasses to perform the actual create or update once the shared ownership gate has approved it.

**Data flow**: It receives the context, name, new spec, old spec if present, and current owner if any. A subclass writes the change to its own storage; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this only after validation, visibility, ownership, speaker, and generation checks have passed.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 588–589)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: OwnerT) -> None
```

**Purpose**: Abstract hook for subclasses to perform the actual deletion once the shared ownership gate has approved it.

**Data flow**: It receives the context, name, and owner. A subclass removes or marks the row deleted in its own storage; the base implementation raises NotImplementedError.

**Call relations**: MemberOwnedObjects.delete calls this after checking existence, visibility, ownership or admin rights, and generation freshness.

*Call graph*: called by 1 (delete).


##### `MemberReadable.member_detail`  (lines 611–618)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject | None
```

**Purpose**: Defines the opt-in contract for object kinds that can be read by a signed-in member outside a chat turn. It supports portal-style detail pages.

**Data flow**: It receives an optional extension context, object name, member ID, and admin flag. An implementation returns a MemberObject if visible, or None if absent or hidden.

**Call relations**: Portal or member-facing routes can call this on kinds that implement the protocol. Kinds that cannot answer outside a turn simply do not implement it.


##### `MemberListable.member_page`  (lines 626–633)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the opt-in contract for object kinds that can provide a member-facing page of objects. It is the listing companion to MemberReadable.member_detail.

**Data flow**: It receives an optional extension context, member ID, admin flag, and ObjectListQuery. An implementation returns an ObjectPage of visible rows.

**Call relations**: Member-facing index routes can call this on kinds that implement MemberListable. It extends the MemberReadable contract with browsing support.


##### `ConversationMemberListable.member_conversation_rows`  (lines 645–653)

```
async def member_conversation_rows(self, ext: 'ExtensionContext | None', conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Defines a contract for listing object grants related to a conversation for one member. A grant says which object name and generation are tied to that conversation and whether content is visible.

**Data flow**: It receives an optional extension context, conversation ID, member ID, admin flag, and limit. An implementation returns a tuple of ConversationObjectGrant entries.

**Call relations**: Conversation-facing code can call this on kinds that implement the protocol. This file only defines the shape of that interaction.


##### `MemberReadableObjects.member_page`  (lines 666–679)

```
async def member_page(self, ext: 'ExtensionContext | None', *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Provides a ready-made member-facing listing for member-owned object kinds. It uses the same visibility and listing rules as the turn-side object list.

**Data flow**: It receives an extension context, member ID, admin flag, and query. It asks _member_rows for candidate rows, filters visible and listed rows, converts them to ObjectRow, and returns object_page’s paged result.

**Call relations**: This implements the MemberListable contract for subclasses. It calls _member_rows and object_page, keeping portal listings aligned with runtime listings.

*Call graph*: calls 2 internal fn (_member_rows, object_page); 1 external calls (__init__).


##### `MemberReadableObjects.member_detail`  (lines 681–699)

```
async def member_detail(self, ext: 'ExtensionContext | None', name: str, *, member_id: UUID, admin: bool) -> MemberObject[SpecT] | None
```

**Purpose**: Provides a ready-made member-facing detail read for member-owned object kinds. It returns both the lightweight row and full object detail when the member may see it.

**Data flow**: It receives an extension context, name, member ID, and admin flag. It finds the row from _member_rows, checks visibility, asks _member_object for detail, and wraps both pieces in MemberObject.

**Call relations**: This implements MemberReadable for subclasses. It calls _member_rows and _member_object so the same subclass data source serves both portal and turn-side reads.

*Call graph*: calls 2 internal fn (_member_object, _member_rows); 2 external calls (__init__, __init__).


##### `MemberReadableObjects._owned_rows`  (lines 701–702)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Adapts the member-facing row hook to the turn-side owned-object gate. It lets one subclass method feed both access paths.

**Data flow**: It receives a tool context, extracts the acting member from the context authority, passes the extension context and member ID to _member_rows, and returns those rows.

**Call relations**: MemberOwnedObjects.list and _owner can call this through inheritance. It delegates to _member_rows, which subclasses must implement.

*Call graph*: calls 1 internal fn (_member_rows); 1 external calls (authority_member_id).


##### `MemberReadableObjects._detail`  (lines 704–709)

```
async def _detail(self, ctx: ToolContext, name: str, owner: OwnerT) -> ObjectDetail[SpecT] | None
```

**Purpose**: Adapts the member-facing object-detail hook to the turn-side get path. It keeps the portal and tool views from drifting apart.

**Data flow**: It receives a tool context, object name, and owner. It extracts the acting member, then calls _member_object with the extension context and returns its ObjectDetail result.

**Call relations**: MemberOwnedObjects.get calls this through inheritance. It delegates the actual read to _member_object.

*Call graph*: calls 1 internal fn (_member_object); 1 external calls (authority_member_id).


##### `MemberReadableObjects._member_rows`  (lines 711–714)

```
async def _member_rows(self, ext: 'ExtensionContext | None', *, member_id: UUID | None) -> tuple[OwnedRow[OwnerT], ...]
```

**Purpose**: Abstract hook for subclasses to provide rows visible from a member-oriented data source. The base class does not know each kind’s storage layout.

**Data flow**: It receives an optional extension context and member ID. A subclass returns OwnedRow entries; the base implementation raises NotImplementedError.

**Call relations**: member_page, member_detail, and _owned_rows all call this. Implementing it is the main way a subclass supplies listing data.

*Call graph*: called by 3 (_owned_rows, member_detail, member_page).


##### `MemberReadableObjects._member_object`  (lines 716–724)

```
async def _member_object(self, ext: 'ExtensionContext | None', name: str, owner: OwnerT, *, member_id: UUID | None) -> ObjectDetail[SpecT] | None
```

**Purpose**: Abstract hook for subclasses to provide full object detail for the member-facing and turn-side read paths.

**Data flow**: It receives an optional extension context, name, owner, and member ID. A subclass returns ObjectDetail or None; the base implementation raises NotImplementedError.

**Call relations**: member_detail and _detail call this after visibility or ownership context has been established. Concrete kinds implement the storage read here.

*Call graph*: called by 2 (_detail, member_detail).


##### `action_registry`  (lines 777–822)

```
def action_registry(bound: tuple[BoundAction, ...], kinds: Mapping[str, BoundKind]) -> dict[str, dict[str, BoundAction]]
```

**Purpose**: Validates and indexes object actions contributed by core or extensions. Actions are extra operations beyond basic create, read, update, and delete.

**Data flow**: It receives bound action declarations and the registered kinds. It checks names, target kinds, collisions, reserved input fields, tool declaration rules, and input-model safety, then returns a nested mapping by kind and action name.

**Call relations**: Startup code uses this after manifests are collected. It calls validate_tool_declaration and _validate_spec_model so bad action definitions fail before the system serves requests.

*Call graph*: calls 1 internal fn (_validate_spec_model); 2 external calls (fullmatch, validate_tool_declaration).


##### `object_registry`  (lines 825–848)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Validates and indexes all object kinds for one deployment. It is the boot-time checkpoint that prevents unsafe or conflicting kinds from being registered.

**Data flow**: It receives bound kind declarations. It checks kind-name grammar, duplicate names, valid agent-target verbs, and spec-model safety, then returns a mapping from kind name to BoundKind.

**Call relations**: Startup code uses this before ObjectVerbs can dispatch requests. It calls _validate_spec_model to ensure specs are safe to store, render, and echo.

*Call graph*: calls 1 internal fn (_validate_spec_model); 1 external calls (fullmatch).


##### `_validate_spec_model`  (lines 851–870)

```
def _validate_spec_model(label: str, spec_model: type[BaseModel]) -> None
```

**Purpose**: Checks that a Pydantic spec model is safe for workspace objects. Specs are shown back to users, so they must not accept unknown fields, contain secrets, or use non-JSON shapes.

**Data flow**: It receives a label and a model class. It walks reachable nested models, checks extra-field settings and secret-bearing field types, then asks Pydantic to build a JSON schema; any failure becomes a clear ValueError.

**Call relations**: object_registry uses this for object specs, and action_registry uses it for action input models. It relies on _reachable_models and _annotation_types to inspect nested model types.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 2 (action_registry, object_registry).


##### `_reachable_models`  (lines 873–887)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all Pydantic models nested inside another model’s field types. This lets validation rules apply to the whole spec shape, not only the top-level class.

**Data flow**: It receives a model class, walks its fields and any nested model annotations, avoids repeats, and returns all discovered model classes.

**Call relations**: _validate_spec_model calls this before checking model configuration and fields. It uses _annotation_types to unpack nested type annotations.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 890–897)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Flattens a Python type annotation into the concrete pieces inside it. For example, it can look through containers or unions to find nested model or secret types.

**Data flow**: It receives an annotation object. If there are no type arguments, it returns the annotation itself; otherwise it recursively expands each argument and returns the flattened tuple.

**Call relations**: _reachable_models uses this to discover nested Pydantic models, and _validate_spec_model uses it to detect secret-bearing field types.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectGetInput.validate_ref`  (lines 923–926)

```
def validate_ref(cls, value: str) -> str
```

**Purpose**: Validates that a non-empty object reference has the canonical kind/name shape. Empty is allowed because object_get can use it to mean “this turn’s agent.”

**Data flow**: It receives the ref string. If it is non-empty, it parses it as an ObjectRef and raises if invalid; then it returns the original string.

**Call relations**: Pydantic runs this validator when building ObjectGetInput. ObjectVerbs._get can then trust that a provided ref is syntactically valid before parsing it again for use.

*Call graph*: calls 1 internal fn (parse).


##### `ObjectVerbs.tools`  (lines 1003–1089)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the public tool definitions for the object system. These definitions describe what each tool does, what input shape it accepts, and which handler runs.

**Data flow**: It reads the ObjectVerbs instance and returns six ToolDef objects for listing, getting, explaining, applying, deleting, and invoking object actions.

**Call relations**: The tool registry or runtime calls this to expose object verbs to the engine. Each ToolDef points back to methods such as _list, _get, _apply, and _delete.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 1091–1158)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements object_list. It either returns the catalog of registered kinds or lists instances of one kind with search, filters, sorting, paging, and action discovery.

**Data flow**: It receives a tool context and list input. With no kind, it checks that no instance-only narrowing was supplied and returns kind descriptions. With a kind, it resolves the kind, checks any agent target, calls the store’s list method, adds refs and action metadata, and returns JSON.

**Call relations**: The object_list ToolDef points here. It uses _resolve, _target, _bound_ctx, _action_views, _instance_action_discoveries, and _json_result to turn a user request into a store call and a response.

*Call graph*: calls 7 internal fn (_action_views, _bound_ctx, _granted_actions, _instance_action_discoveries, _resolve, _target, _json_result); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._get`  (lines 1160–1224)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements object_get. It reads one object’s full spec, live status, links, timestamps, actions, and optional generation value.

**Data flow**: It receives a context and get input. It parses the ref or finds the current agent when the ref is empty, resolves the kind, checks any agent target, calls store.get and store.status, renders links and actions, and returns YAML text.

**Call relations**: The object_get ToolDef points here. It coordinates ObjectRef parsing, _resolve, _target, _bound_ctx, store reads, and _action_views.

*Call graph*: calls 5 internal fn (parse, _action_views, _bound_ctx, _resolve, _target); 8 external calls (__init__, __init__, __init__, __init__, select, workspace_tx, object_agent, safe_dump).


##### `ObjectVerbs._explain`  (lines 1226–1241)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements object_explain. It tells a caller how to author or use objects of one kind.

**Data flow**: It receives a context and kind name, resolves the kind, and returns JSON containing the description, guidance, allowed agent-target verbs, name rule, spec schema, and available actions.

**Call relations**: The object_explain ToolDef points here. It uses _resolve and _action_views, and it is often the preparatory step before object_apply.

*Call graph*: calls 3 internal fn (_action_views, _resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 1243–1308)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements object_apply. It creates or updates an object from a YAML manifest after validation and before/after journaling.

**Data flow**: It receives a context and apply input, parses the YAML envelope, resolves the kind, checks any agent target, validates the object name and spec, reads any existing object, journals the intended change, calls store.apply, withdraws the journal row if this attempt fails, and returns a created or updated result.

**Call relations**: The object_apply ToolDef points here. It calls _parse_envelope, _resolve, _target, _bound_ctx, _journal_object_change, _withdraw_object_change, and _json_result before and after delegating to the kind’s store.

*Call graph*: calls 7 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _parse_envelope, _withdraw_object_change); 4 external calls (__init__, __init__, validate_object_name, object_agent).


##### `ObjectVerbs._delete`  (lines 1310–1345)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements object_delete. It removes one object after first reading and journaling what will be deleted.

**Data flow**: It receives a context and delete input, resolves the kind, checks any agent target, reads the old object, records the delete in the journal, calls store.delete with the old generation, withdraws this attempt’s journal row on failure, and returns the deleted spec when visible.

**Call relations**: The object_delete ToolDef points here. It uses _resolve, _target, _bound_ctx, _journal_object_change, _withdraw_object_change, and _json_result around the store deletion.

*Call graph*: calls 6 internal fn (_bound_ctx, _resolve, _target, _journal_object_change, _json_result, _withdraw_object_change); 2 external calls (__init__, object_agent).


##### `ObjectVerbs._object_action`  (lines 1347–1348)

```
async def _object_action(self, ctx: ToolContext, args: ObjectActionInput) -> ToolResult
```

**Purpose**: Exists only as the schema-facing handler for object_action. The real action dispatch is performed by the engine before this method should run.

**Data flow**: It receives a context and action input but immediately raises RuntimeError. Nothing is returned in normal operation.

**Call relations**: ObjectVerbs.tools exposes this as the handler for the object_action ToolDef, but the surrounding engine resolves and dispatches object actions elsewhere. Reaching this method signals a wiring mistake.


##### `ObjectVerbs._granted_actions`  (lines 1350–1358)

```
def _granted_actions(self, ctx: ToolContext, kind: str) -> dict[str, BoundAction]
```

**Purpose**: Filters a kind’s registered actions down to the ones granted to the current turn. It prevents ungranted actions from being advertised.

**Data flow**: It receives a context and kind name. It reads the action registry for that kind, compares each action’s canonical ID with ctx.granted_actions, and returns only the allowed actions.

**Call relations**: _list, _action_views, and _instance_action_discoveries call this before showing action information. It is the action-discovery permission filter.

*Call graph*: called by 3 (_action_views, _instance_action_discoveries, _list).


##### `ObjectVerbs._action_views`  (lines 1360–1390)

```
def _action_views(self, ctx: ToolContext, kind: str, binding: str, *, name: str | None=None, agent: str | None=None, generation: UUID | None=None) -> list[JsonValue]
```

**Purpose**: Builds ready-to-call action templates for actions available on a kind or a specific object instance. These templates are what callers see beside object list, get, and explain results.

**Data flow**: It receives a context, kind, binding type, and optional instance name, agent, and generation. It filters granted actions by binding, target name, and agent-target support, then turns each into a JSON-ready action view.

**Call relations**: _list, _get, and _explain call this when including action information in responses. It uses _granted_actions and action_view to produce consistent templates.

*Call graph*: calls 1 internal fn (_granted_actions); called by 3 (_explain, _get, _list); 1 external calls (action_view).


##### `ObjectVerbs._instance_action_discoveries`  (lines 1392–1414)

```
def _instance_action_discoveries(self, ctx: ToolContext, kind: str, *, agent: str | None) -> list[JsonValue]
```

**Purpose**: Builds a lightweight catalog of instance actions for a kind, including each action’s input schema. This lets a listing say what actions may appear on individual rows.

**Data flow**: It receives a context, kind, and optional agent name. It filters granted instance actions, skips actions that cannot target the named agent when needed, and returns JSON-ready discovery records.

**Call relations**: ObjectVerbs._list calls this when listing instances of a kind. It uses _granted_actions and InstanceActionDiscovery to describe actions without binding them to a specific row unless declared.

*Call graph*: calls 1 internal fn (_granted_actions); called by 1 (_list); 1 external calls (__init__).


##### `ObjectVerbs.action_target`  (lines 1416–1460)

```
async def action_target(self, ctx: ToolContext, action: ToolDef, wire: ObjectActionInput) -> ObjectActionTarget
```

**Purpose**: Resolves what an object action is going to act on before the action handler runs. It checks the target agent, object existence, visibility, live status, and generation information.

**Data flow**: It receives a context, an action definition, and the wire input. It validates that the action is bound, checks agent-target rules, handles collection actions directly, or reads the target instance through the kind owner’s store and returns an ObjectActionTarget.

**Call relations**: The engine calls this during object_action dispatch. It uses _agent_gate, _resolve, _bound_ctx, and object_agent so the target is checked by the owning kind, not merely by the action contributor.

*Call graph*: calls 3 internal fn (_agent_gate, _bound_ctx, _resolve); 3 external calls (__init__, __init__, object_agent).


##### `ObjectVerbs._resolve`  (lines 1462–1467)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up a registered object kind by name and gives a clear error if it does not exist. This is the central kind-name lookup.

**Data flow**: It receives a kind string, reads the registry, and returns the matching BoundKind. If missing, it raises UnknownKind with the registered names.

**Call relations**: _list, _get, _explain, _apply, _delete, and action_target call this before dispatching to a kind’s store.

*Call graph*: called by 6 (_apply, _delete, _explain, _get, _list, action_target); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 1469–1470)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool context to the extension context that owns a kind. This lets extension stores run with their own workspace-scoped capabilities.

**Data flow**: It receives the current ToolContext and a BoundKind. It returns a copy of the context with ext replaced by the bound kind’s extension context.

**Call relations**: _list, _get, _apply, _delete, and action_target call this before invoking a store owned by a specific extension.

*Call graph*: called by 5 (_apply, _delete, _get, _list, action_target); 1 external calls (replace).


##### `ObjectVerbs._target`  (lines 1472–1485)

```
async def _target(self, ctx: ToolContext, bound: BoundKind, name: str, verbs: frozenset[AgentTargetVerb]) -> ObjectAgent | None
```

**Purpose**: Checks whether a request is allowed to target another agent for the requested object verb. If no agent name is supplied, it keeps the call in the current agent scope.

**Data flow**: It receives a context, bound kind, target agent name, and allowed verbs. With an empty name it returns None; otherwise it verifies the kind allows an agent target for one of those verbs and calls _agent_gate to resolve the agent.

**Call relations**: _list, _get, _apply, and _delete call this before entering an object_agent scope. It delegates the detailed permission and lookup work to _agent_gate.

*Call graph*: calls 1 internal fn (_agent_gate); called by 4 (_apply, _delete, _get, _list).


##### `ObjectVerbs._agent_gate`  (lines 1487–1536)

```
async def _agent_gate(self, ctx: ToolContext, name: str) -> ObjectAgent | None
```

**Purpose**: Resolves and authorizes cross-agent targeting. It makes sure only the workspace main agent, on behalf of an exact live member, can address another visible agent.

**Data flow**: It receives a context and agent name. It reads the current agent, allows same-agent targeting as no special target, rejects subagents or speakerless calls, checks member admin status, looks up a visible active target agent, and returns an ObjectAgent.

**Call relations**: _target and action_target call this whenever a request names another agent. It uses workspace_tx, SQLAlchemy queries, and member_is_admin to enforce workspace and visibility rules.

*Call graph*: called by 2 (_target, action_target); 6 external calls (__init__, __init__, or_, select, workspace_tx, member_is_admin).


##### `_journal_object_change`  (lines 1539–1593)

```
async def _journal_object_change(ctx: ToolContext, kind: str, name: str, verb: Literal['create', 'update', 'delete'], before: BaseModel | None, after: BaseModel | None, agent_id: UUID) -> UUID | None
```

**Purpose**: Records a create, update, or delete before the actual mutation runs. This gives the system an audit trail and helps crash recovery know what write was intended.

**Data flow**: It receives context, object identity, verb, before and after specs, and target agent ID. It creates a deterministic change ID when there is an idempotency key, skips insertion if that change already exists, otherwise writes an object_change row and returns the new change ID.

**Call relations**: ObjectVerbs._apply and ObjectVerbs._delete call this before store writes. If the later store operation fails, those callers may call _withdraw_object_change for rows inserted by this attempt.

*Call graph*: called by 2 (_apply, _delete); 7 external calls (dumps, model_dump, insert, select, workspace_tx, uuid4, uuid5).


##### `_withdraw_object_change`  (lines 1596–1603)

```
async def _withdraw_object_change(ctx: ToolContext, change_id: UUID) -> None
```

**Purpose**: Removes a journal row that was written by the current attempt when the matching mutation failed. It keeps the audit log from claiming a failed write happened.

**Data flow**: It receives the context and change ID, opens a workspace transaction, and deletes that object_change row within the same workspace.

**Call relations**: ObjectVerbs._apply and ObjectVerbs._delete call this in exception paths after _journal_object_change returned a newly inserted row.

*Call graph*: called by 2 (_apply, _delete); 2 external calls (delete, workspace_tx).


##### `_parse_envelope`  (lines 1606–1632)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object], UUID | None]
```

**Purpose**: Parses and validates the YAML manifest used by object_apply. It enforces the public envelope shape before the object kind sees the spec.

**Data flow**: It receives manifest text, rejects oversized input, parses YAML, requires exactly kind, name, spec, and optional generation, checks types, converts generation to a UUID when present, and returns the parsed pieces.

**Call relations**: ObjectVerbs._apply calls this as its first step. Later _apply validates the object name and the spec against the resolved kind’s model.

*Call graph*: called by 1 (_apply); 3 external calls (__init__, UUID, safe_load).


##### `_json_result`  (lines 1635–1636)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a JSON-serializable payload into the standard tool result format. It is a small helper for tools that return JSON text.

**Data flow**: It receives a mapping, serializes it with json.dumps, puts the text in TextContent, and returns a ToolResult containing that content.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete call this when their response is plain JSON rather than YAML detail text.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### Kind package namespaces
Package markers make host and runtime kind modules importable by the object system and extensions.

### `core/src/ufo/host/kinds/__init__.py`

`other` · `import time`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the surrounding `kinds` folder is an importable package. Think of it like a label on a drawer. The drawer may contain useful tools in other files, and the label lets the rest of the program find them using normal Python import paths.

Because the file has no code, it does not create objects, run setup steps, or change program behavior directly. What would break without it depends on the Python packaging style used by the project. In many codebases, this file makes imports more predictable and keeps the folder clearly defined as part of the `ufo.host` module tree.

Its presence matters mostly for organization. It gives the project a stable place to group different “host kind” implementations or definitions, even though the actual work lives in neighboring files rather than here.


### `core/src/ufo/runtime/kinds/__init__.py`

`other` · `import time`

This is an empty package file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as a package, meaning other code can import modules from it using a dotted name such as `ufo.runtime.kinds.something`. Think of it like a label on a drawer: it does not contain the tools, but it lets the rest of the workshop know the drawer exists and can be opened. Nothing would run from this file directly, and it does not create classes, functions, or settings. Its value is structural: it helps organize the runtime “kinds” area of the codebase and keeps imports predictable.


### Runtime object helpers
Shared helpers define paging, object identities, scoped agent targets, and safe action views.

### `core/src/ufo/runtime/listings.py`

`domain_logic` · `request handling`

This file solves a common listing problem: how to show “next page” and “previous page” links when the underlying data may change while someone is reading it. Instead of using an offset like “start at row 50,” it uses keyset paging, which means each page starts from a real row position: the row’s creation time plus its unique id. This is like using a bookmark placed between two books on a shelf, rather than counting fifty books from the left every time the shelf changes.

All listings using this helper sort the same way: newest first, with the id used to break ties when two rows have the same creation time. A ListingCursor is the bookmark. It can be turned into a compact string for a web link, then decoded later when the user clicks that link. If the string is broken or fake, the code raises MalformedCursor instead of silently showing the wrong page.

The two main helpers split the work. page_query changes a database query so it fetches the right slice of rows, plus one extra row to detect whether another page exists. page_of then turns those rows into a ListingPage: the visible rows plus optional cursors for older and newer directions. Together, they keep different listing screens consistent and avoid duplicating tricky paging logic.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a listing position into a single text token that can travel in a link or query string. It records whether the link should move toward newer or older rows, plus the row’s creation time and id.

**Data flow**: It starts with a ListingCursor containing a timestamp, an item id, and a direction flag. It converts the direction into the word "newer" or "older", converts the timestamp to standard text, joins the pieces with a separator, and returns that string. It does not change anything else.

**Call relations**: This is used when a page needs to expose navigation controls. The cursor produced here is the counterpart to ListingCursor.decode, which later reads the token back when a client follows the link.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor token from outside the system and turns it back into a trusted ListingCursor. It protects the listing code from bad or made-up cursor strings by rejecting anything that does not name a valid position.

**Data flow**: It receives a text token, splits it into direction, timestamp, and item id, then checks that the direction is allowed, the timestamp can be parsed as a date-time, and the id is a valid UUID. If everything is valid, it returns a ListingCursor. If not, it raises MalformedCursor so the caller can report a client error instead of guessing.

**Call relations**: The web memory surface calls this when a request includes a cursor from a paging link. After this function validates the token, the resulting cursor can be passed into page_query and page_of to fetch and shape the requested page.

*Call graph*: called by 1 (workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query so it fetches exactly the rows needed for one page of a listing. It applies the shared newest-first ordering, moves to the correct side of a cursor if one is present, and asks for one extra row to learn whether another page exists.

**Data flow**: It receives a database select query, an optional cursor, a page size, and the two database columns that define row position: creation time and id. If there is no cursor, it orders from newest to oldest and limits the result. If there is a cursor, it adds a condition to fetch only rows older or newer than that cursor, using both timestamp and id as the comparison point. It returns a modified query ready to run against the database.

**Call relations**: Listing code calls this before reading rows from the database. Its result is meant to be paired with page_of, which interprets the returned rows and builds the page envelope with navigation cursors.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns the raw rows fetched for a page into a ListingPage that callers can return to users. It decides which rows are visible and whether older or newer navigation links should exist.

**Data flow**: It receives the fetched rows, the cursor used to reach them, the requested page size, a render function that turns each source row into the public row shape, and a position function that extracts each row’s timestamp and id. It checks whether there is an extra row beyond the limit, trims the page to the requested size, reverses rows when needed so the final display remains newest-first, creates boundary cursors from the first and last visible rows, and returns a ListingPage. If there are no visible rows, it returns an empty page with no cursors.

**Call relations**: This runs after page_query has fetched rows. It completes the paging flow by translating database results into the page object used by listing surfaces, and it calls its inner helper page_of.at to make the cursor objects for the page boundaries.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper builds a ListingCursor for one row on the page. It is used to mark the row positions that the older and newer controls should point from.

**Data flow**: It receives one source row and a direction flag. It asks the supplied position function for that row’s creation time and id, then returns a ListingCursor containing those values and the requested direction.

**Call relations**: This helper lives inside page_of because it depends on the position function passed to page_of. page_of calls it when it needs to create the older cursor from the last visible row and the newer cursor from the first visible row.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/object_name.py`

`data_model` · `cross-cutting`

This file is the system’s rulebook for object names. Many parts of the runtime need to create, store, pass around, or display references to objects. If each part invented its own naming rules, links could break, saved rows could become unreadable, or two objects could appear to have the same identity. This file prevents that by defining one shared grammar.

There are two main ideas here. A “kind” is the category of object, such as `agent`; it must look like a lowercase programming-style name. A “name” is the object’s own identifier inside that kind; it uses lowercase letters, numbers, and hyphens, with a length limit. There is also one reserved archived-name format, allowed only for agent objects.

`validate_object_name` is a lightweight check for caller-supplied object names before they are persisted. `ObjectRef` is the stronger, portable identity object: it stores a kind, a name, and optionally an agent name for references that cross an agent boundary. Because it is a Pydantic model, validation runs when an `ObjectRef` is created. Think of it like a passport check: before a reference can travel through the system, its fields must match the official format.

#### Function details

##### `validate_object_name`  (lines 25–33)

```
def validate_object_name(name: str) -> None
```

**Purpose**: Checks whether a plain object name is allowed before the system saves something under that name. It rejects names that are too long or do not match the shared object-name pattern.

**Data flow**: It receives a text name. It compares the name against the maximum length and the allowed character pattern. If the name is valid, nothing is returned and the caller can continue; if not, it raises `InvalidName`, which stops the write early with a clear explanation.

**Call relations**: This is the standalone name gate for places that accept a caller-provided name. When a bad name is found, it creates an `InvalidName` error so the problem is reported at the moment the name is supplied, rather than later when another part of the system tries to read or display it.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 49–52)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks the `kind` part of an object reference. A kind must be a lowercase identifier-like word so object categories have one predictable spelling.

**Data flow**: It receives the proposed `kind` value while an `ObjectRef` is being built. It tests the text against the kind-name pattern. If it matches, the same value is passed through; if not, object creation fails with a validation error.

**Call relations**: This is called automatically by Pydantic, the validation library used by `ObjectRef`, during model creation. It works before the reference is accepted, so later code can trust that `ref.kind` has the expected shape.


##### `ObjectRef.validate_name`  (lines 56–65)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks the `name` part of an object reference. It accepts normal object names, and also accepts a special archived-agent name format used internally.

**Data flow**: It receives the proposed `name` value while an `ObjectRef` is being built. It checks the length, then checks whether the value matches either the normal object-name pattern or the reserved archived-name pattern. A valid name is returned unchanged; an invalid one causes object creation to fail with a detailed error.

**Call relations**: This runs automatically as part of creating an `ObjectRef`. It pairs with the later whole-object check, `ObjectRef.validate_reserved_name_kind`, because matching the reserved archived format is not enough by itself; that special name must also belong to the right kind.


##### `ObjectRef.validate_reserved_name_kind`  (lines 68–71)

```
def validate_reserved_name_kind(self) -> 'ObjectRef'
```

**Purpose**: Makes sure the special archived-name format is used only for agent objects. This protects the reserved namespace from accidentally being used by unrelated object kinds.

**Data flow**: It receives the fully built `ObjectRef` after the individual fields have been checked. It looks at both `kind` and `name` together. If the name has the reserved archived form but the kind is not `agent`, it raises a validation error; otherwise it returns the same reference.

**Call relations**: Pydantic calls this after field validation, when both `kind` and `name` are available. It completes the validation story started by `ObjectRef.validate_name` by enforcing the rule that depends on more than one field.


##### `ObjectRef.__str__`  (lines 73–74)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into its standard human-readable and wire-friendly spelling, `kind/name`. This gives the system one consistent way to display or serialize a reference as text.

**Data flow**: It reads the reference’s `kind` and `name` fields. It joins them with a slash. The result is a string like `agent/alice`; the object itself is not changed.

**Call relations**: This is used whenever Python needs the text form of an `ObjectRef`. It mirrors the format expected by `ObjectRef.parse`, so references can round-trip between structured data and plain text.


##### `ObjectRef.parse`  (lines 77–82)

```
def parse(cls, value: str) -> 'ObjectRef'
```

**Purpose**: Builds an `ObjectRef` from the standard text spelling `<kind>/<name>`. It is used when another part of the runtime receives a reference as text and needs the safer structured form.

**Data flow**: It receives a string. It splits the string on `/` and requires exactly two pieces. Those pieces become the `kind` and `name` fields of a new `ObjectRef`, which then goes through the normal validation rules before being returned.

**Call relations**: This is called when object-related code accepts a text reference, including `core/src/ufo/runtime/objects.ObjectGetInput.validate_ref` and `core/src/ufo/runtime/objects.ObjectVerbs._get`. In that flow, outside text is first parsed and validated here, then the object runtime can safely use the resulting structured reference.

*Call graph*: called by 2 (validate_ref, _get).


### `core/src/ufo/runtime/object_scope.py`

`util` · `request handling / cross-cutting runtime context`

Object actions in this system can be run in a particular agent context. Most of the time, the current agent is enough. But some object actions are dispatched to a specific agent target chosen by the engine before the handler runs. This file provides the small piece of runtime state that remembers that choice while the handler is running.

It defines two frozen data shapes. `ObjectAgent` is the selected agent, with an id and name. `ObjectActionTarget` is the full target that dispatch resolved: the object kind, optional instance name, optional agent, and generation ids used to understand which version of the object lookup was seen. These fields are kept out of the handler input model; instead, runtime context exposes them when needed.

The central mechanism is a `ContextVar`, which is like a task-local pocket: each running task can have its own value without interfering with other tasks. The `object_agent` context manager temporarily places an `ObjectAgent` in that pocket. When the block ends, it restores the old value, even if something fails. `object_agent_id` then answers the question, “Which agent id should this object action use?” It returns the object-selected agent id if one is set; otherwise it falls back to the normal current agent.

#### Function details

##### `object_agent`  (lines 44–52)

```
def object_agent(target: ObjectAgent | None) -> Iterator[None]
```

**Purpose**: Temporarily sets the object-selected agent for the current task while a block of code runs. This is useful when an object handler must be audited or executed as a specific agent chosen during dispatch.

**Data flow**: It receives either an `ObjectAgent` or `None`. If the input is `None`, it simply runs the wrapped block without changing anything. If an agent is provided, it stores that agent in the task-local context before the block runs, then restores the previous value afterward. Nothing is returned; the change is only active inside the `with` block.

**Call relations**: Callers use this as a short-lived wrapper around work that should see a particular object agent. It does not hand work off to other project functions itself; its main job is to prepare the context so later calls, especially `object_agent_id`, can read the right agent.


##### `object_agent_id`  (lines 55–57)

```
def object_agent_id() -> UUID
```

**Purpose**: Returns the agent id that object-related code should use right now. It prefers the object-selected agent if one was set, and otherwise uses the normal current agent.

**Data flow**: It reads the task-local object agent value. If that value exists, it returns that agent’s UUID. If no object-specific agent is set, it asks `agent_current()` for the current general agent and returns that agent’s id.

**Call relations**: This is the read side of the context set by `object_agent`. When code needs an agent id during object handling, this function checks the object-specific context first; if there is no override, it calls `ufo.runtime.agent_scope.agent_current` to fall back to the broader runtime agent context.

*Call graph*: 1 external calls (agent_current).


### `core/src/ufo/runtime/object_views.py`

`domain_logic` · `model discovery and portal/control rendering`

The runtime has actions attached to tools or objects, but the outside world should not see the raw internal objects. This file creates a clean “view” of an action: its name, description, expected input shape, and a ready-made call template that already says which kind of thing the action belongs to. Think of it like printing a menu item for a restaurant customer: the kitchen has complex machinery, but the customer sees a clear name, description, and how to order it.

The central data shape is ActionView, a frozen Pydantic model. Pydantic is a library that makes structured data records and checks that their fields have the expected types. “Frozen” means the view cannot be changed after it is made, which helps keep discovery and UI display predictable.

The helper functions then build and filter these views. Some actions are meant only for the model’s internal profile and should not become portal buttons. Others have presentation details, such as a label or confirmation message, and can be shown as user-facing controls. The file also produces the set of action IDs that an embedded app page may call, combining visible global tools and visible object-bound actions in a sorted, duplicate-free list.

#### Function details

##### `action_view`  (lines 27–53)

```
def action_view(kind: str, bound: 'BoundAction', *, name: str | None=None, agent: str | None=None, generation: UUID | None=None, presented: bool=False) -> ActionView
```

**Purpose**: Builds one public-facing ActionView from an internal bound action. It prepares both the action’s description and a partially filled call template so a model or portal can later invoke the action without needing to know the runtime’s internal objects.

**Data flow**: It receives the object kind, a bound action, and optional context such as object name, agent, generation ID, and whether portal presentation details should be included. It reads the action’s name, description, input model schema, and possibly its label or confirmation text. It returns an immutable ActionView whose call field already contains the action kind, action name, optional context, and an empty input object waiting for real arguments.

**Call relations**: presented_action_views calls this after it has decided an action is eligible to be shown. action_view is the final packaging step: it takes a filtered internal action and hands back the simple record that the rest of the system can expose.

*Call graph*: called by 1 (presented_action_views); 1 external calls (__init__).


##### `presented`  (lines 56–58)

```
def presented(bound: 'BoundAction') -> bool
```

**Purpose**: Answers the yes-or-no question: should this bound action be visible as a portal control? An action counts only if it has presentation information and is not marked as profile-only.

**Data flow**: It receives a bound action and reads two pieces of information from it: whether presentation details exist, and whether the action is restricted to profile use only. It returns true when the action can be displayed as a user-facing control, and false otherwise.

**Call relations**: presented_action_views uses this to filter the actions that become ActionView records. frame_admissible_ids uses the same check before allowing an object-bound action to be callable from an embedded frame, so the visibility rule stays consistent.

*Call graph*: called by 2 (frame_admissible_ids, presented_action_views).


##### `presented_action_views`  (lines 61–77)

```
def presented_action_views(actions: 'Mapping[str, Mapping[str, BoundAction]]', kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Creates the ordered list of visible action views for one target object or binding. This is what a portal-style interface can use to know which buttons or controls should appear for that target.

**Data flow**: It receives the full action collection, the object kind to look under, the binding that must match, and optional object name and generation ID. It looks up actions for that kind, sorts them by their short names for stable output, filters out actions that are unbound, bound to a different target, tied to a different object name, or not presentable, and then turns each remaining action into an ActionView. The result is a tuple of ready-to-display action descriptions.

**Call relations**: This function is the main collector for portal-presented actions in this file. During that collection it calls presented to enforce the visibility rule, then calls action_view to package each approved action into the public shape.

*Call graph*: calls 2 internal fn (action_view, presented).


##### `frame_admissible_ids`  (lines 80–97)

```
def frame_admissible_ids(tools: 'Iterable[ToolDef]', actions: 'Mapping[str, Mapping[str, BoundAction]]') -> tuple[str, ...]
```

**Purpose**: Builds the list of action IDs that an embedded app page is allowed to call. This is a safety and clarity boundary: only actions explicitly marked for frame use are included.

**Data flow**: It receives all tool definitions and all bound actions. From the tools, it keeps global tools that are not object-bound, have presentation information, and are marked as usable from a frame. From the bound actions, it keeps presentable actions whose presentation also allows frame use, then takes their canonical IDs. It removes duplicates, sorts the IDs, and returns them as a tuple of strings.

**Call relations**: This function applies the same presented check used for portal controls, so frame-callable object actions must also be valid visible actions. It does not build full ActionView records; it returns only the canonical IDs needed by embedded pages to know what they may call.

*Call graph*: calls 1 internal fn (presented).

## 📊 State Registers Touched

- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-auth-sessions` — The sign-in state and signed tokens that prove who a web, surface, or API request belongs to.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-source-index-memory` — The saved external pages, search chunks, embeddings, memories, and recall indexes used as workspace knowledge.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
- `reg-agent-provisioning` — The saved provenance, setup needs, policies, and ownership for agents that are shipped by extensions or created in workspaces.
- `reg-objective-state` — The durable goal/objective records holding plans, steps, evidence, blockers, and progress used by objective tools and background follow-up.
- `reg-transcript-access-audit` — The durable audit trail recording privileged reads of private transcripts for later security review.
- `reg-credential-fulfillment` — Durable one-time markers that a requested credential/setup slot has been fulfilled for a workspace.

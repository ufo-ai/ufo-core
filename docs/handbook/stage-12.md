# External connector tools, provider APIs, and source objects  `stage-12`

This stage is part of the agent’s main work during a turn. It is the set of “adapters” that let the agent use outside services without directly holding private tokens. The core object system gives extensions a common way to expose durable named things, such as connected accounts, the workspace agent, conversations, sources, and synced pages. Some are editable, like the agent’s settings by the owner; others are read-only, like conversation records and synced pages.

The connector layer is the switchboard. It finds available provider tools, sends calls through a broker, and keeps secrets away from agent code. The connector tools let the agent list connected accounts, inspect or revoke them, call services like GitHub or Gmail, and move files between workspace storage and providers. MCP support adds another route for tool discovery from configured tool servers.

Composio and Pipedream provide two broker backends: their clients create login links and run actions, their brokers describe and execute tools, and their proxy helpers forward HTTP requests safely. Source and page tools track synced external content and notify conversations about changes. Exa supplies web search, and the YC bridge safely wraps the Y Combinator command-line tool. Package files simply make these extension folders importable.

## Files in this stage

### Shared external API contracts
Core contracts define the object, connector, and search abstractions that external integrations plug into.

### `core/src/ufo/objects.py`

`domain_logic` · `startup and request handling`

This file is the shared “front desk” for workspace objects. An object is a named YAML document with three parts: what kind it is, what its name is, and its spec, meaning the structured settings for that object. Extensions can register new kinds of objects, but this file makes sure they all follow the same basic rules: kind names and object names must use safe formats, specs must be valid JSON-like data, and secrets are not allowed because specs may be shown back to users and included in transcripts.

The main flow is simple. At startup, object_registry checks all registered object kinds and rejects unsafe or conflicting ones. During tool use, ObjectVerbs exposes five user-facing actions: list, get, explain, apply, and delete. It finds the requested kind, switches into that kind’s extension context, validates input, then calls that kind’s ObjectStore. The store is where the real storage-specific work happens.

The file also includes shared list behavior, such as searching, filtering, sorting, and paging. MemberOwnedObjects adds a reusable privacy gate for objects owned by members: a row is visible if it is shared, belongs to the acting member, or the speaker is the workspace owner. Without this file, every extension would need to reinvent object validation, permissions, paging, and tool wiring, which would make behavior inconsistent and easier to get wrong.

#### Function details

##### `ObjectRef.validate_kind`  (lines 67–70)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid kind name. This keeps references predictable and prevents odd or unsafe names from becoming part of the object address.

**Data flow**: A kind string goes in. The function compares it with the allowed kind-name pattern. It returns the same string if it is valid, or raises an error if it is not.

**Call relations**: Pydantic, the data validation library, calls this when an ObjectRef is built. It protects every later use of that reference, including links, search hits, and object_get addresses.


##### `ObjectRef.validate_name`  (lines 74–80)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that an object reference uses a valid object name. Names must be short, lowercase, and simple enough to be safely shown and typed.

**Data flow**: A name string goes in. The function checks both its length and its allowed characters. It returns the name unchanged when valid, or raises an error with the naming rule when invalid.

**Call relations**: Pydantic calls this during ObjectRef creation. It works alongside ObjectRef.validate_kind so every reference has a clean kind/name shape.


##### `ObjectRef.__str__`  (lines 82–83)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into its human-readable address, such as kind/name. This is the canonical short form used when showing or passing around a reference.

**Data flow**: An ObjectRef already holding a kind and name goes in. The function joins those two pieces with a slash. The output is a plain string.

**Call relations**: This is used whenever Python needs a string form of an ObjectRef. It complements the validators by displaying the already-checked identity in a standard way.


##### `_ObjectCursor.validate_rank`  (lines 180–185)

```
def validate_rank(self) -> '_ObjectCursor'
```

**Purpose**: Checks that a list paging cursor stores a value that matches its declared sort type. This prevents a bad cursor from confusing pagination.

**Data flow**: A cursor object goes in after its fields have been loaded. The function compares its rank with the kind of value it carries, such as number or string. It returns the cursor if the pairing makes sense, or raises an error if it does not.

**Call relations**: Pydantic calls this when object_page reads a cursor sent back by a caller. It makes sure object_page can safely use the cursor as a boundary for the next page.


##### `object_page`  (lines 188–267)

```
def object_page(rows: tuple[ObjectRow, ...], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Applies the common list behavior for objects: search, exact filters, sorting, and page-sized results. Stores can hand it lightweight rows and get consistent listing behavior for free.

**Data flow**: A tuple of ObjectRow entries and an ObjectListQuery go in. The function checks that row fields are declared, filters rows by search text and exact matches, sorts them, applies any cursor, and returns an ObjectPage with up to 50 rows plus an optional next cursor.

**Call relations**: MemberOwnedObjects.list calls this after it has removed rows the caller is not allowed to see. Inside, object_page uses _sortable to compare values and _ObjectCursor to read or create continuation tokens.

*Call graph*: calls 1 internal fn (_sortable); called by 1 (list); 2 external calls (__init__, __init__).


##### `object_page.value`  (lines 212–217)

```
def value(row: ObjectRow, name: str) -> JsonValue
```

**Purpose**: Looks up one sortable or filterable value from a listing row. It gives object_page one simple way to read built-in fields and custom row fields.

**Data flow**: An ObjectRow and a field name go in. If the field is name or summary, it reads that direct attribute; otherwise it looks in the row’s extra fields. The result is the value used for searching, filtering, or ordering.

**Call relations**: This helper lives inside object_page because only that listing workflow needs it. object_page calls it repeatedly while matching filters and building sort keys.


##### `_sortable`  (lines 270–283)

```
def _sortable(value: JsonValue, field_name: str) -> tuple[_SortRank, str | int | float]
```

**Purpose**: Converts a list field value into a safe sort key. It makes mixed values sort in a predictable order instead of letting Python compare unlike things directly.

**Data flow**: A JSON-like value and the field name go in. The function classifies None, booleans, numbers, and strings into ranked sortable forms. It returns that rank and value, or raises an error if the value is a complex object or list that cannot be ordered.

**Call relations**: object_page calls this while sorting rows and while comparing rows against a paging cursor. It is the small rulebook that keeps object ordering stable.

*Call graph*: called by 1 (object_page).


##### `ObjectStore.list`  (lines 294–294)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Defines the required list operation for each object kind’s storage layer. A concrete store uses it to return lightweight rows visible within the provided context.

**Data flow**: A tool context and list query go in. The concrete implementation reads its own backing data and returns an ObjectPage. This protocol method itself only states the contract.

**Call relations**: ObjectVerbs._list calls the store’s list method after resolving the kind and binding the extension context. Member-owned stores may inherit MemberOwnedObjects.list as their implementation.


##### `ObjectStore.get`  (lines 296–296)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Defines the required read operation for one object. A concrete store uses it to return the object’s full spec, timestamps, and links, or nothing if it does not exist or is hidden.

**Data flow**: A tool context and object name go in. The concrete store checks its data and returns an ObjectDetail or None. This protocol method only describes what implementers must provide.

**Call relations**: ObjectVerbs._get, ObjectVerbs._apply, and ObjectVerbs._delete call this before reading, updating, or deleting. MemberOwnedObjects.get can provide the shared visibility check for member-owned kinds.


##### `ObjectStore.status`  (lines 298–298)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Defines how a kind can provide live, changing status for an object. Status is separate from the saved spec, like a package’s tracking state beside its shipping label.

**Data flow**: A tool context and object name go in. The concrete store returns a JSON-like dictionary with current status, or None if there is no visible status. This protocol method only defines the expected shape.

**Call relations**: ObjectVerbs._get calls status after it has loaded the object detail, so the full read includes both saved configuration and current state.


##### `ObjectStore.apply`  (lines 300–300)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None
```

**Purpose**: Defines the create-or-update operation for a kind. The concrete store decides what creating or updating means for its own tables and may refuse if the mutation is unsupported.

**Data flow**: A context, name, validated spec, and optional old spec go in. The concrete implementation writes or updates its own storage, or raises a clear error. This protocol method itself has no body.

**Call relations**: ObjectVerbs._apply validates the manifest and current object first, then hands the validated spec to the store’s apply method. MemberOwnedObjects.apply adds ownership checks before calling a subclass’s mutation hook.


##### `ObjectStore.delete`  (lines 302–302)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Defines the delete operation for a kind. The concrete store removes or deactivates the object in whatever way is correct for that kind.

**Data flow**: A context and object name go in. The concrete implementation changes its backing storage or raises an error if deletion is not allowed. This protocol method only states the requirement.

**Call relations**: ObjectVerbs._delete calls this after first reading the old object so it can echo the deleted spec. MemberOwnedObjects.delete can provide shared member ownership checks before deletion.


##### `MemberOwnedObjects.list`  (lines 343–351)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists only the member-owned rows the caller is allowed to see. It centralizes privacy rules so each member-owned kind does not have to rewrite them.

**Data flow**: A tool context and list query go in. The function checks whether the speaker is the workspace owner, reads all owned rows from the subclass, keeps only visible ones, turns them into ObjectRow entries, and returns a paged result from object_page.

**Call relations**: A member-owned object store uses this as its ObjectStore.list implementation. It calls the subclass hook _owned_rows for raw data, _visible for the privacy decision, and object_page for shared search, filtering, sorting, and paging.

*Call graph*: calls 4 internal fn (_owned_rows, _visible, object_page, speaker_is_owner); 1 external calls (__init__).


##### `MemberOwnedObjects.get`  (lines 353–359)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: Reads a member-owned object only if the caller may see it. Hidden objects look the same as missing objects, which avoids revealing private names.

**Data flow**: A context and object name go in. The function finds the row owner, checks visibility using the acting member and owner status, then either returns None or asks the subclass for full detail.

**Call relations**: ObjectVerbs._get can call this through the store interface. The function relies on _owner to find ownership, _visible to gate access, and _detail to fetch the actual object after access is allowed.

*Call graph*: calls 4 internal fn (_detail, _owner, _visible, speaker_is_owner).


##### `MemberOwnedObjects.status`  (lines 361–367)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status for a member-owned object only when the caller is allowed to see that object. It applies the same privacy behavior as get.

**Data flow**: A context and name go in. The function finds the owner, checks whether the row is visible, and returns None if not. If visible, it asks the subclass for the status dictionary.

**Call relations**: ObjectVerbs._get asks the store for status as part of rendering a full object. This method uses _owner, _visible, and _status so status cannot leak information about hidden objects.

*Call graph*: calls 4 internal fn (_owner, _status, _visible, speaker_is_owner).


##### `MemberOwnedObjects.apply`  (lines 369–379)

```
async def apply(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None) -> None
```

**Purpose**: Creates or updates a member-owned object while enforcing ownership rules. It allows visible rows to be changed only by their owning member or by the workspace owner.

**Data flow**: A context, name, new spec, and old spec go in. The function checks whether a row already exists, whether it is visible, whether the actor owns it or is the workspace owner, and whether a live speaker is required. If all checks pass, it passes the work to _apply_owned.

**Call relations**: ObjectVerbs._apply reaches this through the store interface after validating the YAML manifest and spec. This method calls _owner, _visible, _owned, and speaker_is_owner before handing off to the subclass’s _apply_owned hook.

*Call graph*: calls 5 internal fn (_apply_owned, _owned, _owner, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects.delete`  (lines 381–392)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Deletes a member-owned object only when the caller has the right to remove it. Like apply, it hides invisible rows by reporting them as not found.

**Data flow**: A context and name go in. The function finds the owner, rejects missing or invisible rows, checks whether the actor owns the row or is the workspace owner, checks any live-speaker requirement, and then calls _delete_owned.

**Call relations**: ObjectVerbs._delete reaches this through the store interface after reading the old object. This method uses _owner, _visible, _owned, and speaker_is_owner before handing deletion to the subclass hook.

*Call graph*: calls 5 internal fn (_delete_owned, _owned, _owner, _visible, speaker_is_owner); 2 external calls (__init__, __init__).


##### `MemberOwnedObjects._owned`  (lines 394–398)

```
def _owned(self, owner: ObjectOwner, acting: UUID | None) -> bool
```

**Purpose**: Answers whether the acting member is the member-owner of a row. Owner-only rows have no member owner, so only a workspace owner can change them through the separate owner check.

**Data flow**: An ObjectOwner and an acting member ID go in. The function compares the row’s member_id with the acting member ID, while treating None as not member-owned. It returns true or false.

**Call relations**: MemberOwnedObjects._visible uses this to decide visibility, and MemberOwnedObjects.apply and delete use it to decide whether a non-owner speaker may mutate a visible row.

*Call graph*: called by 3 (_visible, apply, delete).


##### `MemberOwnedObjects._visible`  (lines 400–401)

```
def _visible(self, owner: ObjectOwner, acting: UUID | None, is_owner: bool) -> bool
```

**Purpose**: Decides whether a member-owned row should be visible to the current actor. A row is visible if it is shared, owned by the actor, or viewed by the workspace owner.

**Data flow**: An owner record, acting member ID, and workspace-owner flag go in. The function combines the shared flag, _owned result, and owner flag. It returns a simple true or false.

**Call relations**: All member-owned read and write paths call this before exposing or changing a row. It calls _owned for the member-ownership part of the rule.

*Call graph*: calls 1 internal fn (_owned); called by 5 (apply, delete, get, list, status).


##### `MemberOwnedObjects._owner`  (lines 403–404)

```
async def _owner(self, ctx: ToolContext, name: str) -> ObjectOwner | None
```

**Purpose**: Finds the ownership record for one named row. This lets the shared gate check access before fetching full details or making changes.

**Data flow**: A context and object name go in. The function asks the subclass for all owned rows, searches for the matching name, and returns its ObjectOwner or None if not found.

**Call relations**: MemberOwnedObjects.get, status, apply, and delete call this first. It depends on the subclass-provided _owned_rows hook.

*Call graph*: calls 1 internal fn (_owned_rows); called by 4 (apply, delete, get, status).


##### `MemberOwnedObjects._owned_rows`  (lines 406–407)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: A required subclass hook that must return the lightweight rows and owners for this kind. The base class needs this information to enforce visibility and ownership.

**Data flow**: A context goes in. A subclass is expected to return a tuple of OwnedRow values. The base implementation raises NotImplementedError because it does not know any kind’s storage.

**Call relations**: MemberOwnedObjects.list calls this to build visible listings, and _owner calls it to find the owner for a specific name. Concrete member-owned stores must implement it.

*Call graph*: called by 2 (_owner, list).


##### `MemberOwnedObjects._detail`  (lines 409–410)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[SpecT] | None
```

**Purpose**: A required subclass hook that returns the full saved object detail after access has already been allowed. It keeps storage-specific reading outside the shared permission code.

**Data flow**: A context and name go in. A subclass should return ObjectDetail or None from its own storage. The base version raises NotImplementedError.

**Call relations**: MemberOwnedObjects.get calls this only after _owner and _visible say the caller may see the row. Concrete stores supply the actual read logic.

*Call graph*: called by 1 (get).


##### `MemberOwnedObjects._status`  (lines 412–413)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: A required subclass hook that returns live status for a visible object. It lets each kind define status in its own terms.

**Data flow**: A context and name go in. A subclass should return a JSON-like status dictionary or None. The base version raises NotImplementedError.

**Call relations**: MemberOwnedObjects.status calls this only after the shared visibility gate passes. Concrete stores implement the kind-specific status lookup.

*Call graph*: called by 1 (status).


##### `MemberOwnedObjects._apply_owned`  (lines 415–423)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SpecT, old: SpecT | None, owner: ObjectOwner | None) -> None
```

**Purpose**: A required subclass hook that performs the actual create or update after the shared ownership checks have passed. This separates “may this caller do it?” from “how does this kind store it?”

**Data flow**: A context, name, validated spec, optional old spec, and optional owner record go in. The subclass writes the change using its own storage rules. The base version raises NotImplementedError.

**Call relations**: MemberOwnedObjects.apply calls this at the end of the guarded mutation path. Concrete stores implement it to do the domain-specific write.

*Call graph*: called by 1 (apply).


##### `MemberOwnedObjects._delete_owned`  (lines 425–426)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: A required subclass hook that performs the actual deletion after access checks pass. The base class handles the gate; the subclass handles the data change.

**Data flow**: A context, name, and owner record go in. The subclass removes or changes the row in its own storage. The base version raises NotImplementedError.

**Call relations**: MemberOwnedObjects.delete calls this only after confirming the row exists, is visible, and may be deleted by the caller. Concrete stores implement the real deletion.

*Call graph*: called by 1 (delete).


##### `object_registry`  (lines 456–473)

```
def object_registry(bound: tuple[BoundKind, ...]) -> dict[str, BoundKind]
```

**Purpose**: Builds the lookup table of all registered object kinds and rejects unsafe registrations at startup. It is the boot-time checkpoint for the object system.

**Data flow**: A tuple of BoundKind entries goes in. The function checks each kind name, detects duplicate kind names, validates each spec model, and returns a dictionary keyed by kind name. It raises errors before serving if anything is unsafe.

**Call relations**: Startup code uses this to create the registry that ObjectVerbs later reads. It calls _validate_spec_model for the deeper safety checks on each kind’s spec.

*Call graph*: calls 1 internal fn (_validate_spec_model).


##### `_validate_spec_model`  (lines 476–499)

```
def _validate_spec_model(owner: str, kind: ObjectKind) -> None
```

**Purpose**: Checks that a kind’s spec model is safe to store, render, and echo back to users. It forbids surprise extra keys, secret fields, and data that cannot be represented as JSON.

**Data flow**: An owner label and ObjectKind go in. The function checks declared list fields against spec fields, walks nested models, inspects field types, and asks Pydantic for a JSON schema. It returns nothing if valid or raises a detailed startup error.

**Call relations**: object_registry calls this for every registered kind. It uses _reachable_models to inspect nested Pydantic models and _annotation_types to look inside type annotations.

*Call graph*: calls 2 internal fn (_annotation_types, _reachable_models); called by 1 (object_registry).


##### `_reachable_models`  (lines 502–516)

```
def _reachable_models(model: type[BaseModel]) -> tuple[type[BaseModel], ...]
```

**Purpose**: Finds all Pydantic models nested inside a spec model. This matters because safety rules must apply to nested objects, not just the top-level spec.

**Data flow**: A Pydantic model class goes in. The function walks its fields, follows annotations that point to other Pydantic models, avoids repeats, and returns all discovered model classes.

**Call relations**: _validate_spec_model calls this before checking extra-key settings and secret fields. It uses _annotation_types to unwrap container and union-style type hints.

*Call graph*: calls 1 internal fn (_annotation_types); called by 1 (_validate_spec_model).


##### `_annotation_types`  (lines 519–526)

```
def _annotation_types(annotation: object) -> tuple[object, ...]
```

**Purpose**: Breaks a type annotation into the concrete pieces inside it. For example, it can look through a list or optional type to find the actual inner type.

**Data flow**: A type annotation goes in. The function asks Python typing for its arguments, recursively flattens nested arguments, and returns a tuple of discovered annotation parts.

**Call relations**: _validate_spec_model and _reachable_models call this when inspecting model fields. It relies on typing.get_args, the standard helper for reading parameterized type hints.

*Call graph*: called by 2 (_reachable_models, _validate_spec_model); 1 external calls (get_args).


##### `ObjectVerbs.tools`  (lines 564–627)

```
def tools(self) -> tuple[ToolDef, ...]
```

**Purpose**: Creates the five tool definitions that expose workspace objects to the rest of the system: list, get, explain, apply, and delete. These are the public controls for the object feature.

**Data flow**: An ObjectVerbs instance with a registry goes in. The function builds ToolDef objects with names, descriptions, input models, handlers, and safety flags. It returns them as a tuple.

**Call relations**: Tool registration code calls this to make object actions available. Each ToolDef points back to one of ObjectVerbs’ private handler methods for the actual request.

*Call graph*: 1 external calls (__init__).


##### `ObjectVerbs._list`  (lines 629–655)

```
async def _list(self, ctx: ToolContext, args: ObjectListInput) -> ToolResult
```

**Purpose**: Implements the object_list tool. With no kind, it lists available object kinds; with a kind, it lists matching objects of that kind.

**Data flow**: A tool context and ObjectListInput go in. If no kind is provided, it returns kind names and descriptions. If a kind is provided, it resolves the kind, binds the correct extension context, builds an ObjectListQuery, calls the store, and returns JSON with rows and maybe a next cursor.

**Call relations**: The object_list ToolDef created by ObjectVerbs.tools calls this. It uses _resolve to find the kind, _bound_ctx to switch context, and _json_result to format the response.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _json_result); 1 external calls (__init__).


##### `ObjectVerbs._get`  (lines 657–672)

```
async def _get(self, ctx: ToolContext, args: ObjectGetInput) -> ToolResult
```

**Purpose**: Implements the object_get tool. It reads one object’s saved spec, live status, links, and timestamps.

**Data flow**: A context plus kind and name go in. The function resolves the kind, binds the extension context, asks the store for detail, raises UnknownObject if missing, asks for status, and returns a YAML text block with the full rendered object.

**Call relations**: The object_get ToolDef calls this. It depends on _resolve and _bound_ctx before handing the read to the kind’s store, then uses yaml.safe_dump and ToolResult text content for output.

*Call graph*: calls 2 internal fn (_bound_ctx, _resolve); 4 external calls (__init__, __init__, __init__, safe_dump).


##### `ObjectVerbs._explain`  (lines 674–686)

```
async def _explain(self, ctx: ToolContext, args: ObjectExplainInput) -> ToolResult
```

**Purpose**: Implements the object_explain tool. It tells a caller how to author objects of a kind before they try to create one.

**Data flow**: A context and kind name go in. The function resolves the kind and returns JSON containing the description, guidance, name rule, and generated JSON schema for the spec. It does not change storage.

**Call relations**: The object_explain ToolDef calls this. It uses _resolve to find the registered kind and _json_result to send a machine-readable explanation back.

*Call graph*: calls 2 internal fn (_resolve, _json_result).


##### `ObjectVerbs._apply`  (lines 688–709)

```
async def _apply(self, ctx: ToolContext, args: ObjectApplyInput) -> ToolResult
```

**Purpose**: Implements the object_apply tool, which creates or updates an object from a YAML manifest. It validates the outer document and the kind-specific spec before any store mutation runs.

**Data flow**: A context and manifest string go in. The function parses the YAML envelope, resolves the kind, validates the name, validates the spec with the kind’s model, reads any existing object, calls the store’s apply method with the old spec if present, and returns JSON saying created or updated.

**Call relations**: The object_apply ToolDef calls this. It uses _parse_envelope, _validate_name, _resolve, _bound_ctx, and _json_result, then hands the validated mutation to the kind’s store.

*Call graph*: calls 5 internal fn (_bound_ctx, _resolve, _json_result, _parse_envelope, _validate_name); 1 external calls (__init__).


##### `ObjectVerbs._delete`  (lines 711–725)

```
async def _delete(self, ctx: ToolContext, args: ObjectDeleteInput) -> ToolResult
```

**Purpose**: Implements the object_delete tool. It deletes one object and returns enough of the old spec to help undo an accidental delete when the kind supports re-creation.

**Data flow**: A context plus kind and name go in. The function resolves the kind, binds the context, reads the old object, raises UnknownObject if absent, calls the store’s delete method, and returns JSON with deleted true and the old spec.

**Call relations**: The object_delete ToolDef calls this. It uses _resolve and _bound_ctx before delegating to the store, and _json_result to format the final response.

*Call graph*: calls 3 internal fn (_bound_ctx, _resolve, _json_result); 1 external calls (__init__).


##### `ObjectVerbs._resolve`  (lines 727–732)

```
def _resolve(self, kind: str) -> BoundKind
```

**Purpose**: Looks up a requested object kind in the registry. It gives all tool handlers a single, consistent unknown-kind error.

**Data flow**: A kind name goes in. The function checks the registry mapping and returns the matching BoundKind. If none exists, it raises UnknownKind and includes the registered kind names in the message.

**Call relations**: ObjectVerbs._list, _get, _explain, _apply, and _delete all call this before touching a store. It is the shared dispatch gate for object kinds.

*Call graph*: called by 5 (_apply, _delete, _explain, _get, _list); 1 external calls (__init__).


##### `ObjectVerbs._bound_ctx`  (lines 734–735)

```
def _bound_ctx(self, ctx: ToolContext, bound: BoundKind) -> ToolContext
```

**Purpose**: Rebinds a tool call to the extension context that owns the selected kind. This makes the same object verb run inside the right extension’s workspace view.

**Data flow**: The current ToolContext and a BoundKind go in. The function copies the context while replacing its extension context with the bound kind’s context. The new ToolContext comes out.

**Call relations**: ObjectVerbs._list, _get, _apply, and _delete call this after _resolve. The returned context is passed to the store method so the store works under its own extension.

*Call graph*: called by 4 (_apply, _delete, _get, _list); 1 external calls (replace).


##### `_parse_envelope`  (lines 738–756)

```
def _parse_envelope(manifest: str) -> tuple[str, str, Mapping[str, object]]
```

**Purpose**: Parses and validates the outer YAML document used by object_apply. It enforces the exact three-key shape: kind, name, and spec.

**Data flow**: A manifest string goes in. The function checks its byte size, safely loads YAML, confirms it is a mapping with exactly the required keys, checks kind and name are strings, checks spec is a mapping, and returns those three pieces. Invalid input raises InvalidManifest.

**Call relations**: ObjectVerbs._apply calls this before resolving a kind or validating the spec. It uses yaml.safe_load so the manifest is treated as data, not executable YAML behavior.

*Call graph*: called by 1 (_apply); 2 external calls (__init__, safe_load).


##### `_validate_name`  (lines 759–764)

```
def _validate_name(name: str) -> None
```

**Purpose**: Checks that a new or updated object name follows the shared naming rule. This gives every object kind the same safe address format.

**Data flow**: A name string goes in. The function checks the maximum length and allowed pattern. It returns nothing on success or raises InvalidName with the rule on failure.

**Call relations**: ObjectVerbs._apply calls this after parsing the manifest and before validating or writing the spec. ObjectRef has similar validation for references.

*Call graph*: called by 1 (_apply); 1 external calls (__init__).


##### `_json_result`  (lines 767–768)

```
def _json_result(payload: Mapping[str, object]) -> ToolResult
```

**Purpose**: Wraps a Python mapping as a JSON tool response. It is a small helper that keeps JSON-returning object tools formatted the same way.

**Data flow**: A mapping payload goes in. The function converts it to a JSON string, wraps that text in TextContent, then wraps it in a ToolResult. The ToolResult comes out.

**Call relations**: ObjectVerbs._list, _explain, _apply, and _delete call this for their JSON responses. ObjectVerbs._get is the exception because it returns a YAML rendering of a full object.

*Call graph*: called by 4 (_apply, _delete, _explain, _list); 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/connectors.py`

`domain_logic` · `cross-cutting`

Connectors let the product talk to outside services such as Gmail, GitHub, or other provider APIs. The hard part is doing that without casually passing private tokens around. This file sets up two safe “seams.” One seam is for feed sync jobs, which need a credential to read provider data. The other seam is for dynamic connector tools, where a broker runs a provider action while keeping the real account token on the server side.

The main idea is simple: code that needs access asks for a Credential, but that credential may be a safe request transport, a bearer token, or provider-specific headers. Its printed form always hides secrets. Brokers expose provider tools, schemas, execution, file upload/download references, search, and credential lookup. Files are passed as references such as URLs or staged upload instructions, not as raw bytes through this process.

ConnectorRegistry is the practical hub. It knows the explicitly installed connectors, an optional open resolver that can claim many provider names, and an optional fallback authentication backend for direct member-provided keys. When a sync source uses a brokered account, the registry sends it to the broker. When it uses the special direct account marker, it sends it to the fallback. Without this file, each connector would need its own ad hoc rules for secrets, tool lookup, account routing, and file transfer, which would be both unsafe and hard to extend.

#### Function details

##### `Credential.__repr__`  (lines 52–61)

```
def __repr__(self) -> str
```

**Purpose**: Returns a safe text version of a Credential for debugging. It deliberately hides any token, header value, or transport details so an accidental log line does not leak a secret.

**Data flow**: It reads which authentication path is present on the Credential: transport, bearer token, custom headers, or nothing. It then returns a short string that says only the kind of credential, with the sensitive contents replaced by “redacted.” The Credential itself is not changed.

**Call relations**: This is used implicitly whenever Python needs to display a Credential, such as in logs, exceptions, or debugging tools. It supports the wider connector flow by making the credential object safer to pass around inside the process.


##### `AuthProxy.credential`  (lines 81–81)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines the promise that an authentication backend must fulfill: given a workspace, provider, and account handle, return the right Credential for a sync job. It is a protocol method, meaning real backends implement it elsewhere.

**Data flow**: The caller supplies a workspace ID, a provider name, and an account identifier. The implementing backend uses those to find or construct one safe authentication method, then returns a Credential. The method may read secrets from a protected store or return a broker transport that keeps the secret elsewhere.

**Call relations**: ConnectorRegistry.credential calls this on the fallback authentication backend when a source uses direct credentials or when no broker resolves the provider. Sync runners rely on this contract without needing to know which backend actually stores or injects the secret.


##### `stale_grant_guidance`  (lines 89–96)

```
def stale_grant_guidance(provider: str) -> str
```

**Purpose**: Builds a clear user-facing explanation for a brokered account grant that the current broker no longer recognizes. It tells the agent or member that retrying will not help and that the account must be reconnected.

**Data flow**: It takes a provider name, inserts it into a fixed explanatory message, and returns that message as text. It does not inspect any broker state or change anything.

**Call relations**: Broker implementations can call this when an account ID refers to an old or unknown grant. The returned guidance is meant to be attached to broker failures so the surrounding tool flow can tell the user what action fixes the problem.


##### `ConnectorBroker.tools`  (lines 166–168)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Defines how a broker lists provider tools that may match a search query. A real broker uses this to let the system discover what actions are available for a connected service.

**Data flow**: The caller provides a workspace ID, provider name, and search text. The broker implementation searches its catalog in the context of that workspace and returns matching BrokerTool records. This protocol method itself contains no implementation.

**Call relations**: Dynamic connector discovery code calls this through a ConnectorBroker obtained from ConnectorRegistry.entry. Broker extensions implement it for their own catalog APIs.


##### `ConnectorBroker.schema`  (lines 170–170)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Defines how to fetch the detailed input shape for one broker tool. The input shape tells the agent what arguments it can safely build for that tool.

**Data flow**: The caller gives a workspace ID, provider name, and tool slug. The broker implementation returns a BrokerTool with its input schema filled in, or raises UnknownBrokerTool if that slug does not exist for the provider.

**Call relations**: Tool-description flows call this after selecting a provider and slug through the registry. It supports the step where the agent learns exactly how to call a connector tool before execution.


##### `ConnectorBroker.execute`  (lines 172–180)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Defines how a broker runs one provider tool under a connected account. The broker, not the sandbox or agent, injects the real provider credential.

**Data flow**: The caller sends the workspace ID, provider, tool slug, argument data, broker account ID, and an optional idempotency key, which is a repeat-safe request marker. The broker implementation performs the provider action and returns a dictionary response. The secret token remains inside the broker’s own system.

**Call relations**: Dynamic connector tool execution calls this after the registry has routed the provider to its broker. Any file arguments should already have been staged through stage_upload when needed, and file results can later be interpreted through file_outputs.


##### `ConnectorBroker.file_outputs`  (lines 182–182)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Defines how a broker identifies files produced by a tool execution response. It turns broker-specific response data into standard BrokerFile references.

**Data flow**: The caller passes the raw response dictionary from a broker execution. The implementation looks for produced file references and returns a tuple of BrokerFile objects containing names and download URLs. It does not transfer the file bytes itself.

**Call relations**: After ConnectorBroker.execute returns, surrounding tool code can call this to find downloadable outputs. The sandbox then fetches those files directly from the broker’s file store rather than routing the bytes through the main server.


##### `ConnectorBroker.stage_upload`  (lines 184–192)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Defines how to prepare a workspace file so a broker tool can use it as input. It creates a place where the sandbox can upload the file directly to the broker’s storage.

**Data flow**: The caller gives workspace, provider, tool slug, filename, MIME type, and an MD5 checksum, which is a content fingerprint. The broker implementation returns a StagedUpload containing a PUT URL if bytes must be uploaded, the required content type, and the argument value to pass to the tool. It may return no PUT URL when the broker already has the same bytes.

**Call relations**: Tool execution code calls this before ConnectorBroker.execute when a tool argument points at a workspace file. The sandbox performs the actual upload, so this protocol keeps large file bytes out of the serve process.


##### `ConnectorBroker.search`  (lines 194–194)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Defines a richer semantic search for broker tools. Besides matching tools, it may return a suggested plan, helpful guidance, and warnings about pitfalls.

**Data flow**: The caller provides a workspace ID, provider name, and natural-language query. The broker implementation returns a BrokerSearch containing matching BrokerTool entries and optional explanatory lists. No state is changed by the protocol method itself.

**Call relations**: Connector discovery or planning flows can call this through the broker selected by ConnectorRegistry.entry. Brokers that have their own router or recommendation system can expose that extra knowledge here.


##### `ConnectorBroker.credential`  (lines 196–196)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Defines how a broker provides a feed-sync Credential for one of its granted accounts. This lets sync jobs authenticate provider requests without learning the underlying token.

**Data flow**: The caller supplies the workspace ID, provider name, and broker account handle. The broker implementation confirms the account belongs to the workspace and returns a Credential, often a transport that forwards requests through the broker. The real provider secret stays broker-side.

**Call relations**: ConnectorRegistry.credential calls this when a sync source uses a brokered account rather than the special direct account marker. Broker extensions implement the method for their own account and credential systems.


##### `RequestForwarder.forward`  (lines 215–217)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: Defines how an intercepted provider HTTP request is sent through a broker under a granted account. It is used when a command-line tool in the sandbox sends a sentinel value instead of a real token.

**Data flow**: The caller passes an account ID, HTTP method, URL, headers, and request body bytes. The implementation asks the broker to perform that request with the real credential injected server-side, then returns a ForwardedResponse containing status, headers, and body. The sandbox never sees the real credential.

**Call relations**: CliCredential holds a RequestForwarder for provider CLI authentication. The egress proxy calls this when it recognizes a request that should be broker-forwarded instead of sent directly.


##### `ConnectorResolver.transfer_hosts`  (lines 263–263)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: Defines which broker file-storage hosts are allowed for transfers in an open connector namespace. These hosts are needed so sandbox file upload and download traffic can pass through the egress rules safely.

**Data flow**: A resolver implementation returns a tuple of host names. The property takes no input and does not change anything. The returned hosts are used as extra allowed destinations for grants served by that resolver.

**Call relations**: A ConnectorResolver is attached to ConnectorRegistry when a broker can serve many provider slugs without listing them all. Egress and grant setup code can read this property to know which transfer hosts to permit.


##### `ConnectorResolver.entry`  (lines 265–265)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Defines how an open resolver creates a ConnectorEntry for any provider slug it claims. This lets one broker serve providers that were not explicitly registered one by one.

**Data flow**: The caller provides a provider name. The resolver implementation returns a ConnectorEntry that names the provider, gives a label, and points to the shared broker. It does not prove here that the broker can actually serve the provider; broker calls fail later if it cannot.

**Call relations**: ConnectorRegistry.entry calls this when a provider is not in the fixed entries map but a resolver exists. ConnectorRegistry.credential also uses it to route brokered credentials for unregistered provider slugs.


##### `ConnectorResolver.catalog`  (lines 267–267)

```
async def catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Defines how to search a broker’s live catalog of connectable services. This is how discovery can show providers beyond the closed set of installed entries.

**Data flow**: The caller gives search text and a maximum number of results. The resolver implementation queries its broker’s service catalog and returns CatalogEntry records with provider slugs and labels. It does not modify registry entries.

**Call relations**: ConnectorRegistry.search_catalog calls this when an open resolver is installed. Discovery tools use the registry method so they do not need to know whether results came from fixed connectors or a live broker catalog.


##### `ConnectorRegistry.entry`  (lines 286–292)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: Finds the ConnectorEntry that should serve a provider. It first checks explicitly installed connectors, then asks the open resolver, and fails clearly if neither can serve the provider.

**Data flow**: It receives a provider name and looks it up in the registry’s entries mapping. If found, it returns that entry. If not found but a resolver exists, it asks the resolver to build an entry. If there is no match and no resolver, it raises a KeyError.

**Call relations**: Dynamic connector flows call this before searching, describing, or executing provider tools so they can reach the right broker. It hands off to ConnectorResolver.entry only for provider names outside the explicit registry.


##### `ConnectorRegistry.search_catalog`  (lines 294–299)

```
async def search_catalog(self, query: str, limit: int) -> tuple[CatalogEntry, ...]
```

**Purpose**: Returns live catalog search results from the open resolver, or an empty result when no resolver is installed. This keeps discovery code simple and safe.

**Data flow**: It receives a query and result limit. If the registry has no resolver, it returns an empty tuple. If a resolver exists, it forwards the query and limit to resolver.catalog and returns that answer.

**Call relations**: Connector discovery calls this to append open-namespace catalog results to known registered connectors. It delegates the real search to ConnectorResolver.catalog when that capability is available.


##### `ConnectorRegistry.credential`  (lines 301–314)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Routes a feed-sync credential request to the correct place. Brokered accounts go to their broker, while the special direct account marker goes to the fallback authentication backend.

**Data flow**: It receives a workspace ID, provider name, and account handle. If the account is not DIRECT_ACCOUNT, it first tries the provider’s registered broker, then the resolver’s broker. If the account is direct, or no broker path applies, it tries the fallback AuthProxy. It returns a Credential, or raises a RuntimeError if no broker or fallback can resolve it.

**Call relations**: The sync runner uses ConnectorRegistry as its AuthProxy. This method calls ConnectorBroker.credential for broker grants, ConnectorResolver.entry when an open namespace may own the provider, and AuthProxy.credential on the fallback for direct credentials.


### `core/src/ufo/search.py`

`data_model` · `startup and request handling`

This file is a boundary, or “seam,” between the core application and any real web search service. The core code does not include a built-in search engine and does not keep search API keys. Instead, an extension provides a SearchProvider, which is like a plug-in adapter for a specific search backend.

The file defines small, frozen data objects for the information that moves across this boundary. A SearchQuery describes what to search for. SearchResults contains ranked SearchHit items and may include a direct answer if the backend can produce one. A FetchRequest asks for the text of one web page, and FetchedPage is the extracted result.

The SearchProvider protocol describes what any search backend must be able to do: say whether it supports page fetching, run a search, and optionally fetch a page. A protocol is a promise about shape: any object with these methods can be used as a search provider. If a provider does not support fetching, asking it to fetch should fail clearly with SearchUnsupported. In normal use, the research fetch tool checks supports_fetch first, so users should not hit that error path.

This matters because it keeps sensitive keys and backend-specific details outside the sandbox and outside core logic, while still giving tools a consistent way to search the web.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 87–87)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether this search provider can fetch and extract the contents of a specific web page. It is a safety check so tools do not ask a provider to do something it cannot do.

**Data flow**: The caller asks the provider for this value. The provider returns true if page fetching is available, or false if it only supports search results. Nothing is changed; it is simply a capability signal.

**Call relations**: During a research turn, tools use the selected provider through the turn context. The fetch tool checks this property before trying to fetch a URL, so unsupported providers are skipped or rejected cleanly before reaching the fetch call.


##### `SearchProvider.search`  (lines 89–89)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This asynchronous method runs a web search using the chosen backend. Someone uses it when they have a SearchQuery and need structured search results without caring which external service provides them.

**Data flow**: A SearchQuery goes in, containing the search text, desired result count, and optional filters such as recency, allowed domains, or category. The provider sends that request to its backend in its own way, then returns SearchResults with ranked hits and possibly a direct answer.

**Call relations**: The research tools call this through the selected provider for the current turn. The concrete backend implementation does the outside-network work, while core code only depends on this common method and receives the normalized SearchResults back.


##### `SearchProvider.fetch`  (lines 91–91)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This asynchronous method fetches and extracts text from one URL when the provider supports that feature. It is used after a search result or user-provided link needs to be read more fully.

**Data flow**: A FetchRequest goes in, containing the URL and optional instructions such as an extraction prompt, a maximum character count, or whether to bypass cache. If supported, the provider returns a FetchedPage with the page text and possibly a summary. If fetching is not supported and a caller bypasses the capability check, the provider should raise SearchUnsupported.

**Call relations**: The fetch URL tool is expected to look at supports_fetch before calling this method. When fetching is allowed, the concrete provider contacts its backend and hands the extracted page back to the tool in the shared FetchedPage shape.


### `core/src/ufo/agents.py`

`domain_logic` · `object request handling`

This file turns the workspace agent into a normal workspace object, so it can be listed, inspected, and updated through the same object system as other things. The important rule is that there is exactly one agent per workspace. It is created when the workspace is initialized, so this file refuses attempts to create another one or delete the existing one.

The editable part of the agent is small: which model it should use, and whether it may use public internet access from the sandbox. Those two fields live in `AgentSpec`, the shape of the settings a user can apply. The system prompt is deliberately not editable here. Prompt changes go through a separate governance proposal path, which is like requiring a formal change request instead of letting someone rewrite it directly. This file only shows the current prompt and a digest, a short fingerprint used to prove which prompt a proposal refers to.

`AgentObjects` is the store behind the object kind. It reads rows from the `agent` database table for list, get, and status views. When applying changes, it first checks that the object already exists, then checks that the speaker is the workspace owner, and only then updates the model and internet policy. Finally, `AGENT_OBJECT` registers all of this with the broader object framework.

#### Function details

##### `_effective_model`  (lines 40–45)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: This helper reports the real model the agent is using when the stored setting says `auto`. `auto` means "follow the deployment's configured model," so readers need to see the concrete model currently in effect without changing the saved setting.

**Data flow**: It receives the current tool context and the model value stored in the database. If the stored value is the special `auto` value, it reads the already-resolved model from `ctx.agent.model`; otherwise it returns the stored value unchanged. Nothing is written back, so a read does not accidentally turn `auto` into a fixed model name.

**Call relations**: `AgentObjects.list` uses this when building short summaries, and `AgentObjects.status` uses it when showing the live state. In both cases, it sits between the database value and the user-facing output so people see what the agent will actually run.

*Call graph*: called by 2 (list, status).


##### `AgentObjects.list`  (lines 71–96)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of agent objects in the current workspace. In practice there is normally one row, but it still uses the common object-listing shape so the agent fits into the rest of the object system.

**Data flow**: It opens a workspace database transaction, selects the agent name, stored model, and internet-access flag for the current workspace, and sorts by name. It then turns each database row into an `ObjectRow` with a readable summary, using `_effective_model` to show the real model if the stored value is `auto`. The rows and the caller's list query are passed to `object_page`, which returns the final page of results.

**Call relations**: This is called when the object framework needs to list objects of kind `agent`. It asks the database for the raw facts, calls `_effective_model` to make the model understandable to readers, then hands the formatted rows to the shared paging helper.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 98–111)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: This returns the editable specification for one named agent object. It is the read side for settings that can later be applied back, namely the model and public-internet policy.

**Data flow**: It receives a context and an agent name, then asks `_row` to fetch that agent's database row in the current workspace. If no row exists, it returns `None`. If a row is found, it builds an `AgentSpec` from the stored model and internet-access flag, wraps it with creation and update timestamps in an `ObjectDetail`, and returns that detail.

**Call relations**: The object framework uses this when someone asks for the definition of a specific `agent` object. It relies on `_row` for the database lookup, then packages the result in the standard detail format used by object reads.

*Call graph*: calls 1 internal fn (_row); 2 external calls (__init__, __init__).


##### `AgentObjects.status`  (lines 113–121)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This returns the live, read-only status of one agent, including fields that are not part of the editable spec. Most importantly, it exposes the current prompt and a digest of that prompt so governance proposals can safely refer to exactly what they are changing.

**Data flow**: It receives a context and an agent name, then fetches the matching row with `_row`. If there is no row, it returns `None`. Otherwise it builds a plain dictionary containing the prompt, the prompt digest, and the effective model name after resolving `auto` if needed.

**Call relations**: This is used when callers want the agent's current state rather than just its editable settings. It calls `_row` for the stored data, `_effective_model` for the model shown to users, and `prompt_digest` to create the prompt fingerprint used by the governance flow.

*Call graph*: calls 2 internal fn (_row, _effective_model); 1 external calls (prompt_digest).


##### `AgentObjects.apply`  (lines 123–142)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None) -> None
```

**Purpose**: This updates the existing workspace agent's model and public-internet policy. It refuses to create a new agent and refuses edits from anyone who is not the workspace owner.

**Data flow**: It receives the context, object name, desired `AgentSpec`, and the old spec if one exists. If there is no old spec, it raises an error because agents cannot be created through apply. It then asks the context whether the speaker is the owner; if not, it raises an owner-required error. If the checks pass, it opens a workspace database transaction and updates the matching agent row with the new model, the new internet-access flag, and a fresh update timestamp.

**Call relations**: This is called by the object system when someone applies a new desired state for the `agent` object. It performs the policy checks itself, then hands the actual write to the database through SQLAlchemy inside the workspace transaction.

*Call graph*: calls 1 internal fn (speaker_is_owner); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects.delete`  (lines 144–145)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This always refuses deletion of the workspace agent. The project currently requires one agent per workspace, so deleting it is not a supported operation.

**Data flow**: It receives the context and agent name, but does not read or change the database. It immediately raises a `VerbNotSupported` error explaining that the agent cannot be deleted.

**Call relations**: The object framework calls this if someone tries to delete an object of kind `agent`. Instead of handing off to any storage logic, it stops the flow with a clear unsupported-operation error.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 147–162)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: This private helper fetches the database row for one named agent in the current workspace. It keeps the repeated lookup logic in one place for the read-style methods.

**Data flow**: It receives an agent name, opens a workspace database transaction, and selects the prompt, model, internet-access flag, creation time, and update time from the `agent` table for the current workspace and name. It returns the single matching row, or `None` if there is no match.

**Call relations**: `AgentObjects.get` and `AgentObjects.status` both call this before building their user-facing responses. It is the shared doorway from those methods into the `agent` table.

*Call graph*: called by 2 (get, status); 3 external calls (select, workspace_tx, ws_current).


### `core/src/ufo/conversations.py`

`domain_logic` · `request handling`

A conversation in this project is treated like a signpost, not like editable content. Artifacts and scheduled tasks can point back to the conversation they came from or report into, and this file explains how those links are resolved. It exposes only safe metadata: the chat surface, the optional member the conversation belongs to, and creation/update times. It deliberately does not expose the actual messages.

The main class, ConversationObjects, acts like a read-only window onto rows in the workspace database. When someone lists or gets conversations, the file applies a visibility rule first. Shared conversations, with no member attached, are visible to everyone. Private member-bound conversations are visible only to that same audience member. This is like a notice board where public notices are visible to all, but personal envelopes can only be opened by their owner.

The file also defines ConversationSpec, the small shape of conversation data returned to callers, and registers CONVERSATION_OBJECT so the wider object system knows this kind exists. Any mutation request is rejected with a clear message: conversations are made by chat surfaces and later closed by retention, not authored through this object interface.

#### Function details

##### `ConversationObjects.list`  (lines 50–59)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the conversations the current caller is allowed to see, as a paged list. It gives each row a readable summary and includes the chat surface as a list field.

**Data flow**: It receives a tool context, which includes who the caller is, and a list query, which says how the caller wants the results shaped. It asks for the visible database rows, turns each row into an ObjectRow with an id, summary, and surface field, then passes those rows through the shared paging helper. The output is an ObjectPage containing only allowed conversations.

**Call relations**: This is the public list path for the conversation object. It relies on _visible_rows to fetch only permitted database rows, then hands the formatted rows to object_page so the wider object system gets the standard paged response.

*Call graph*: calls 1 internal fn (_visible_rows); 2 external calls (__init__, object_page).


##### `ConversationObjects.get`  (lines 61–72)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ConversationSpec] | None
```

**Purpose**: Looks up one conversation by its id and returns its safe metadata if the caller may see it. If the id is invalid or the conversation is hidden from this caller, it returns nothing.

**Data flow**: It receives the caller context and a conversation name, which is expected to be a UUID-style id. It asks _find to locate a matching visible database row. If no row is found, it returns None. If a row is found, it builds a ConversationSpec from the row’s surface and member id, then wraps that spec with the row’s creation and update timestamps in an ObjectDetail.

**Call relations**: This is the single-item read path for conversation links. It delegates the permission-aware database lookup to _find, then converts the raw row into the object detail shape expected by the object framework.

*Call graph*: calls 1 internal fn (_find); 2 external calls (__init__, __init__).


##### `ConversationObjects.status`  (lines 74–75)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Reports that conversations have no separate live status in this object interface. The metadata returned by get is all this object kind exposes.

**Data flow**: It receives the caller context and conversation name, but does not read the database or inspect the name. It always returns None, meaning there is no status payload to show.

**Call relations**: The wider object system may ask object kinds for status information. For conversations, this method intentionally stops the flow there because transcripts or runtime state are not exposed through this object kind.


##### `ConversationObjects.apply`  (lines 77–80)

```
async def apply(self, ctx: ToolContext, name: str, spec: ConversationSpec, old: ConversationSpec | None) -> None
```

**Purpose**: Rejects attempts to create or update a conversation through the object system. This protects the rule that conversations are created by chat surfaces, not by object authors.

**Data flow**: It receives the caller context, object name, desired conversation spec, and any previous spec. Instead of writing anything, it raises a VerbNotSupported error with the standard explanation. Nothing in the database is changed.

**Call relations**: When the object framework tries to apply a desired conversation state, this method acts as a locked door. It hands back a clear refusal rather than passing control to any database-writing code.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects.delete`  (lines 82–83)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Rejects attempts to delete a conversation through the object system. Conversation lifetime is controlled elsewhere, such as by retention rules.

**Data flow**: It receives the caller context and conversation name. It does not look up the row and does not remove anything. It raises a VerbNotSupported error explaining that conversations are surface-made rather than authored here.

**Call relations**: When the object framework asks this store to delete a conversation object, this method ends the request immediately with a refusal. That keeps conversation removal out of this read-only link-target interface.

*Call graph*: 1 external calls (__init__).


##### `ConversationObjects._find`  (lines 85–95)

```
async def _find(self, ctx: ToolContext, name: str) -> sa.Row | None
```

**Purpose**: Finds one visible conversation row by id. It is careful to treat bad ids as simply not found, rather than as database errors.

**Data flow**: It receives the caller context and a name string. First it tries to turn the name into a UUID, the standard id format used for conversation rows. If that fails, it returns None. If the id is valid, it opens a workspace database transaction, builds the normal visibility-filtered query, adds the id condition, and returns the single matching row or None.

**Call relations**: The get method calls this when someone asks for one conversation. _find uses _visible so that lookup and permission filtering are done together, meaning a caller cannot learn about private conversations they should not see.

*Call graph*: calls 1 internal fn (_visible); called by 1 (get); 2 external calls (workspace_tx, UUID).


##### `ConversationObjects._visible_rows`  (lines 97–100)

```
async def _visible_rows(self, ctx: ToolContext) -> tuple[sa.Row, ...]
```

**Purpose**: Fetches all conversation rows visible to the current caller. It is the database-reading helper behind listing conversations.

**Data flow**: It receives the caller context, opens a workspace database transaction, runs the visibility-filtered select query, collects all returned rows, and turns them into an immutable tuple. The output is the set of database rows that list can safely display.

**Call relations**: The list method calls this before formatting rows for the object system. _visible_rows delegates the actual permission rule and column choice to _visible, then performs the database read.

*Call graph*: calls 1 internal fn (_visible); called by 1 (list); 1 external calls (workspace_tx).


##### `ConversationObjects._visible`  (lines 102–121)

```
def _visible(self, ctx: ToolContext) -> sa.Select
```

**Purpose**: Builds the database query that defines which conversations this caller may see. This is the central visibility rule for the file.

**Data flow**: It reads the audience member id from the tool context and the current workspace id from workspace state. If there is no audience member, it allows only shared conversations. If there is an audience member, it allows shared conversations plus that member’s own private conversations. It returns a SQLAlchemy Select object, which is a database query description, selecting only the id, surface, member id, and timestamps for matching rows.

**Call relations**: _find and _visible_rows both call this before touching the database. That means both single-item reads and list reads use the same visibility rule and the same limited set of safe metadata columns.

*Call graph*: called by 2 (_find, _visible_rows); 3 external calls (or_, select, ws_current).


### Connector account surfaces
Connector extensions expose connected accounts as objects and provide agent-facing tools for discovering and using external services.

### `extensions/connectors/ufo_ext_connectors/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That matters because other parts of the project may need to import modules from `extensions/connectors/ufo_ext_connectors` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain the useful tools, but the label tells Python that the drawer is part of the organized system. Since this file contains no code, it does not run setup steps, expose shortcut imports, or change any behavior. Its value is structural: without it, depending on the Python version and import style, code may fail to recognize this directory as a package or may not import it in the expected way.


### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `request handling for connector object list, read, update, and delete operations`

A connected account is an OAuth grant: permission for this agent to use an account from an outside provider, such as a service account connected through a chat flow. This file turns those grants into visible objects so the rest of the system can treat them like named things. Without it, a user could connect an account, but the object system would not have a clear way to show that account, describe who owns it, change whether it is shared, or revoke this agent’s access.

The important rule is that accounts are not created here. Creation must go through `connect_account`, because that path involves a third party and secret credentials. This file only reflects grants that already exist. It names each grant from its provider and account id, cleaned into a safe object-style name. If two accounts would produce the same name, it adds a short hash, like adding a small ticket number when two people have the same name.

`ConnectorObjects` plugs into the project’s member-owned object framework. It supplies rows for listing, details for inspection, status fields for auditing, and two allowed changes. Applying an update can only flip `shared`, which decides whether other workspace members can use the account in their turns. Deleting revokes this agent’s binding to the grant. The underlying account token is held by the grant system, so removing the grant row is what makes the account stop resolving for this agent’s tools, syncs, and proxy rules.

#### Function details

##### `_slug`  (lines 47–48)

```
def _slug(raw: str) -> str
```

**Purpose**: Turns a provider name or account id into a simple lowercase name part that is safe to use in an object name. It removes awkward characters by replacing groups of non-letters and non-numbers with hyphens.

**Data flow**: It receives a raw string, lowercases it, replaces anything outside `a-z` and `0-9` with hyphens, and trims hyphens from the ends. The result is a compact name fragment such as `google-work-account`.

**Call relations**: `ConnectorObjects._named` calls this when it is building stable object names for grant rows. This keeps naming consistent before any connector object is listed, read, shared, or revoked.

*Call graph*: called by 1 (_named); 1 external calls (sub).


##### `ConnectorObjects._owned_rows`  (lines 65–76)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: Builds the short list entries shown when someone lists connector objects. Each row says which provider account it is and whether it is private or shared, along with who owns it.

**Data flow**: It reads the current grant summaries through `_named`, then turns each named grant into an `OwnedRow`. The output is a tuple of rows containing the object name, a human-readable summary, and ownership information based on the grantor and sharing flag.

**Call relations**: The member-owned object framework calls this when it needs the visible inventory of connector objects. It relies on `_named` to translate raw grant records into object names, then hands rows back to the common object layer so visibility and ownership rules can be applied.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, __init__).


##### `ConnectorObjects._detail`  (lines 78–88)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[ConnectorSpec] | None
```

**Purpose**: Returns the full stored description for one connector object. This is what lets a reader inspect which provider and account id the object represents, and whether it is shared.

**Data flow**: It receives a context and an object name, looks up the matching grant through `_named`, and returns nothing if the name is unknown. If found, it builds an `ObjectDetail` containing a `ConnectorSpec` plus the grant’s creation and update times.

**Call relations**: The object system calls this when a user asks to read or inspect a specific connector object. It uses `_named` for the lookup and then hands back a structured detail record that the shared object machinery can present.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, __init__).


##### `ConnectorObjects._status`  (lines 90–99)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Provides audit-style status information for a connector object. It shows who granted it, which host and agent it belongs to, and whether it is shared.

**Data flow**: It receives a context and object name, looks up the grant through `_named`, and returns nothing if there is no match. If the grant exists, it returns a plain dictionary with the grantor member id, host, agent, and sharing state.

**Call relations**: The object framework calls this when it needs status separate from the main editable spec. It depends on `_named` for the same name-to-grant lookup used by listing, detail, sharing, and revocation.

*Call graph*: calls 1 internal fn (_named).


##### `ConnectorObjects._apply_owned`  (lines 101–118)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorSpec, old: ConnectorSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Applies the only allowed edit to a connector object: changing whether the connected account is shared. It refuses attempts to create a connector this way or to change the provider or account id.

**Data flow**: It receives the requested new spec, the previous spec, and ownership information. If there was no previous object, or if anything except `shared` changed, it raises a refusal telling the caller to use `connect_account`. If the sharing value is unchanged, it does nothing. If sharing really changed, it finds the grant and asks the grant store to update the shared flag for this workspace, agent, provider, and account.

**Call relations**: The member-owned object framework calls this after its permission checks allow a grantor or workspace owner to mutate the object. This function then enforces the connector-specific rule that only sharing may change, and hands the actual update to `ctx.grants.set_shared`.

*Call graph*: calls 1 internal fn (_named); 2 external calls (__init__, model_copy).


##### `ConnectorObjects._delete_owned`  (lines 120–126)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Revokes this agent’s connection to a provider account. In practical terms, deleting the connector object makes the account stop being available to this agent’s tools and syncs.

**Data flow**: It receives the context, object name, and owner information. It looks up the named grant, checks that the grant store is available, and tells that store to revoke the grant for the current workspace, agent, provider, and account id. It does not return a value; the lasting change is that the grant row is removed.

**Call relations**: The common owned-object layer calls this after confirming the requester is allowed to delete, meaning the grantor or a workspace owner. This function uses `_named` to find the exact grant and then hands the revocation to `ctx.grants.revoke`.

*Call graph*: calls 1 internal fn (_named).


##### `ConnectorObjects._named`  (lines 128–142)

```
async def _named(self, ctx: ToolContext) -> dict[str, GrantSummary]
```

**Purpose**: Builds the map from user-facing connector object names to the grant records they represent. This is the central lookup used by every read, update, and delete operation in this file.

**Data flow**: It reads all grant summaries for the current workspace and agent. For each grant, it creates a plain name from the provider and account id using `_slug`. If a plain name is unique, it uses it directly. If several grants collapse to the same plain name, it adds a short SHA-256 digest based on the account id so each object still has its own name. The result is a dictionary from object name to grant summary.

**Call relations**: All the connector object operations call this first so they are working from the same naming rules. It calls `grant_summaries` to fetch the raw grant records, `_slug` to make readable name pieces, and `hashlib.sha256` only when it needs to disambiguate duplicate-looking names.

*Call graph*: calls 1 internal fn (_slug); called by 5 (_apply_owned, _delete_owned, _detail, _owned_rows, _status); 2 external calls (sha256, grant_summaries).


### `extensions/connectors/ufo_ext_connectors/tools.py`

`domain_logic` · `request handling`

A connector broker is a server that knows how to talk to many outside services. Instead of giving the agent thousands of fixed tools, this file gives it a searchable front door: find a connector, discover that connector's real tools, then call one of them. Think of it like a hotel concierge: the agent asks what services exist, asks for the menu for one service, then asks the concierge to place the order.

The file also protects the main system from messy real-world tool results. If a connector needs a file from the workspace, the file is not copied through the main server. The sandbox hashes it, uploads it to a broker-provided URL, and replaces the argument with the broker's file reference. If a connector returns files, the sandbox downloads them into a connector_files folder in the workspace.

Some providers put file contents directly inside JSON as base64, which is text that represents raw bytes. Left untouched, this is huge and often unreadable. This file decodes marked base64 fields: small UTF-8 text is kept inline, while large or binary data is written to the workspace and replaced with a file reference. Finally, it shrinks repeated JSON objects by keeping the first copy and replacing later identical copies with same_as pointers. This keeps useful results in context instead of forcing the model to open a separate file.

#### Function details

##### `list_external_tools`  (lines 139–162)

```
async def list_external_tools(ctx: ToolContext, args: ListExternalToolsInput) -> ToolResult
```

**Purpose**: Searches for available external connectors, such as GitHub or Slack, by keyword or exact source ID. Someone uses this first when they need to know which outside services the current turn can reach.

**Data flow**: It receives the current tool context and search queries. It reads the turn's connector registry, matches providers already known locally, also asks the broker catalog for matching connector rows, removes duplicates, and returns JSON text containing source IDs and labels.

**Call relations**: This is one of the public connector tools exposed to the agent. It starts by calling _registry to get the live connector list, uses asyncio.gather to search catalog entries in parallel, and finishes through _json_result so the answer is packaged as a normal tool result.

*Call graph*: calls 2 internal fn (_json_result, _registry); 1 external calls (gather).


##### `describe_external_tools`  (lines 165–187)

```
async def describe_external_tools(ctx: ToolContext, args: DescribeExternalToolsInput) -> ToolResult
```

**Purpose**: Shows the real tools available inside one connector, and can fetch the input schema for exact tool names. This prevents the agent from guessing tool names or arguments before it calls an external service.

**Data flow**: It receives a connector source ID, optional exact tool names, and an optional discovery query. It looks up the connector entry, asks the broker for schemas for requested names, records names the broker does not recognize, optionally searches for available tools, and returns JSON with schemas, available tools, and unresolved names.

**Call relations**: This is normally called after list_external_tools and before call_external_tool. It gets the registry through _registry, formats schemas with _tool_json, builds fallback search text with _discovery_query when guessed names fail, and wraps the final response with _json_result.

*Call graph*: calls 4 internal fn (_discovery_query, _json_result, _registry, _tool_json).


##### `call_external_tool`  (lines 190–194)

```
async def call_external_tool(ctx: ToolContext, args: CallExternalToolInput) -> ToolResult
```

**Purpose**: Runs one real external connector tool through its broker. It is the point where a discovered tool is actually executed using the connected account for the current agent.

**Data flow**: It receives the connector ID, tool name, optional account ID, and arguments. It finds the connector entry, resolves which connected account to use, creates a _ConnectorCall helper, and returns the helper's serialized result as tool text.

**Call relations**: This is the public execution tool. It depends on _registry to find the broker and on ToolContext.connector_account to select the authenticated account, then hands the detailed work to _ConnectorCall.run.

*Call graph*: calls 2 internal fn (connector_account, _registry); 3 external calls (__init__, __init__, __init__).


##### `_ConnectorCall.run`  (lines 222–235)

```
async def run(self, arguments: dict[str, JsonValue], account_id: str) -> str
```

**Purpose**: Carries out one connector tool call from start to finish. It prepares input files, runs the broker-side tool, saves output files, translates embedded base64 content, and shrinks repeated data.

**Data flow**: It receives raw tool arguments and an account ID. It recursively stages any workspace-file arguments, sends the cleaned arguments to the broker's execute API, fetches any returned files into the workspace, rewrites base64-heavy response fields into readable text or file references, adds a workspace_files list when needed, and returns a JSON string.

**Call relations**: call_external_tool creates the _ConnectorCall and invokes this method. Inside the method, _staged_value handles input file references, _fetched_files pulls produced files back, _translated_node cleans the response tree, and _deduped runs in a worker thread so the main event loop is not blocked by JSON shrinking.

*Call graph*: calls 3 internal fn (_fetched_files, _staged_value, _translated_node); 1 external calls (to_thread).


##### `_ConnectorCall._staged_value`  (lines 237–252)

```
async def _staged_value(self, value: object) -> object
```

**Purpose**: Walks through an argument value and replaces any workspace file marker with a broker-ready file reference. This lets connector tools receive files without the main server copying file bytes itself.

**Data flow**: It receives any nested value from the arguments. If the value is exactly a dictionary like {"workspace_file": "..."}, it validates the path and stages that file; if it is a dictionary or list, it repeats the same process inside it; otherwise it leaves the value unchanged.

**Call relations**: _ConnectorCall.run calls this for every top-level argument before broker execution. When it finds a file marker, it hands off to _stage_file to do the actual hashing and upload.

*Call graph*: calls 1 internal fn (_stage_file); called by 1 (run).


##### `_ConnectorCall._stage_file`  (lines 254–285)

```
async def _stage_file(self, path: str) -> dict[str, object]
```

**Purpose**: Uploads one workspace file to the broker's file store, or skips the upload if the broker already has the same file. It returns the special argument value the broker expects for that staged file.

**Data flow**: It receives a workspace path. It scopes that path to the workspace, asks the sandbox to compute the file's MD5 hash and size, rejects unreadable or too-large files, guesses a content type from the filename, asks the broker for an upload location, and uses sandbox curl to PUT the bytes if needed. The output is a broker-provided argument dictionary.

**Call relations**: _staged_value calls this when it sees a workspace_file argument. It talks to the sandbox for local file work and to the broker for upload staging, keeping raw file bytes out of the serve process.

*Call graph*: called by 1 (_staged_value); 4 external calls (guess_type, PurePosixPath, quote, workspace_path).


##### `_ConnectorCall._fetched_files`  (lines 287–306)

```
async def _fetched_files(self, files: tuple[BrokerFile, ...]) -> list[dict[str, str]]
```

**Purpose**: Downloads files produced by a connector tool into the workspace. It gives each fetched file a fresh folder so one result cannot overwrite another.

**Data flow**: It receives broker file records, each with a name and download URL. For each one it sanitizes the filename, builds a unique path under /workspace/connector_files, asks the sandbox to download the file with curl, and returns a list of file names and workspace paths.

**Call relations**: _ConnectorCall.run calls this after broker execution, using the broker's file_outputs view of the response. The returned list is later added to the tool result under workspace_files.

*Call graph*: called by 1 (run); 3 external calls (PurePosixPath, quote, uuid4).


##### `_ConnectorCall._translated_node`  (lines 308–368)

```
async def _translated_node(self, node: Mapping[str, object], depth: int=0) -> dict[str, object]
```

**Purpose**: Cleans one JSON object from a connector result by decoding fields that the provider explicitly marked as base64. This turns unreadable encoded blobs into either plain text or workspace file references.

**Data flow**: It receives a mapping from the connector response and a recursion depth. It first translates child values, then checks for marker fields such as encoding: base64 and content-like fields such as content or data. Valid base64 is decoded; small UTF-8 text replaces the encoded string, while large or binary bytes are written to the workspace. Marker fields are updated only when all marked content was successfully translated.

**Call relations**: _ConnectorCall.run starts response translation here, and _translated calls it again for nested dictionaries. It relies on _decoded_base64 to safely decode marked strings, _translated_bytes to choose inline text versus file output, and _translated for recursive walking.

*Call graph*: calls 3 internal fn (_translated, _translated_bytes, _decoded_base64); called by 2 (_translated, run); 1 external calls (guess_type).


##### `_ConnectorCall._translated`  (lines 370–389)

```
async def _translated(self, value: object, depth: int) -> object
```

**Purpose**: Walks any value inside a connector result and applies the same cleanup rules throughout the tree. It also recognizes data URLs, which are strings that directly contain base64-encoded bytes.

**Data flow**: It receives a value and the current nesting depth. Dictionaries are sent to _translated_node, lists are translated item by item, short enough strings starting with data: are tested as data URLs, and everything else is returned unchanged. Very deeply nested data is left alone instead of risking a recursion failure.

**Call relations**: _translated_node uses this to process children before handling the current object. It loops back to _translated_node for nested objects and hands possible data URLs to _translated_data_url.

*Call graph*: calls 2 internal fn (_translated_data_url, _translated_node); called by 1 (_translated_node).


##### `_ConnectorCall._translated_data_url`  (lines 391–403)

```
async def _translated_data_url(self, value: str) -> object
```

**Purpose**: Decodes one data URL when it really is a base64 data URL. This catches provider results that put a whole small file into a single string rather than a marked content field.

**Data flow**: It receives a string beginning with data:. It checks the expected data URL pattern, decodes the base64 payload if valid, chooses a filename extension from the declared media type when possible, and returns either decoded text or a workspace file reference. If the string is not a valid base64 data URL, it returns the original string.

**Call relations**: _translated calls this for candidate data URL strings. This function uses _decoded_base64 for safe decoding and _translated_bytes to decide whether the bytes stay inline or are offloaded to a file.

*Call graph*: calls 2 internal fn (_translated_bytes, _decoded_base64); called by 1 (_translated); 1 external calls (guess_extension).


##### `_ConnectorCall._translated_bytes`  (lines 405–414)

```
async def _translated_bytes(self, decoded: bytes, text: str | None, name: str, mimetype: str) -> object
```

**Purpose**: Decides how decoded bytes should appear in the final tool result. Small readable text stays in the JSON; large text and binary data become files in the workspace.

**Data flow**: It receives raw bytes, optional decoded text, a filename, and a media type. If the bytes are valid UTF-8 text and the text is under the inline size limit, it returns the text. Otherwise it writes the bytes out through _offloaded and returns that file reference.

**Call relations**: _translated_node and _translated_data_url call this after base64 has been decoded. It hands only the file-writing case to _offloaded.

*Call graph*: calls 1 internal fn (_offloaded); called by 2 (_translated_data_url, _translated_node).


##### `_ConnectorCall._offloaded`  (lines 416–448)

```
async def _offloaded(self, name: str, mimetype: str, data: bytes) -> dict[str, object]
```

**Purpose**: Writes decoded result bytes into the workspace and returns a small reference object. This keeps big or binary payloads out of the model's text context while still making the data available.

**Data flow**: It receives a suggested name, media type, and byte content. It sanitizes the filename, builds a content-addressed path using a SHA-256 hash of the bytes, writes to a temporary part file through the sandbox, atomically renames that part file into place, and returns name, workspace_path, mimetype, and byte count.

**Call relations**: _translated_bytes calls this whenever decoded content should not be kept inline. It uses sandbox file writing rather than a broker download because these bytes already came back inside the broker response.

*Call graph*: called by 1 (_translated_bytes); 4 external calls (sha256, PurePosixPath, quote, uuid4).


##### `_ConnectorCall._deduped`  (lines 450–508)

```
def _deduped(self, payload: dict[str, object]) -> str
```

**Purpose**: Turns a connector result into JSON text, while replacing repeated large objects with same_as pointers to the first copy. This reduces bulky repeated response data without deleting facts.

**Data flow**: It receives the final payload dictionary. It serializes it once, skips deduplication if the result is too large, too structurally dense, or already contains same_as, and otherwise walks the payload to find identical objects. The output is a JSON string, either original or condensed.

**Call relations**: _ConnectorCall.run calls this at the end in a worker thread. It uses _condensed to inspect and rewrite repeated structures, and _escaped to build JSON Pointer paths that identify the first occurrence.

*Call graph*: calls 2 internal fn (_condensed, _escaped); 1 external calls (dumps).


##### `_ConnectorCall._condensed`  (lines 510–586)

```
def _condensed(self, value: object, pointer: str, depth: int, first: dict[bytes, str]) -> tuple[object, bytes, int]
```

**Purpose**: Examines one node in a JSON-like result and decides whether repeated objects should be replaced by a pointer. It preserves the first full copy and only replaces later identical objects that are large enough to be worth pointing to.

**Data flow**: It receives a value, its JSON Pointer path, current depth, and a table of first-seen object digests. It recursively computes stable hashes for dictionaries, lists, strings, and other values, tracks each object's original size, records the first large object with a given hash, and returns either the original-shaped value or a {"same_as": "..."} reference, plus its digest and size.

**Call relations**: _deduped calls this while building the condensed payload. The method calls itself recursively, uses _escaped when extending paths through object keys, and uses SHA-256 hashing so identity checks are based on structure and content.

*Call graph*: calls 1 internal fn (_escaped); called by 1 (_deduped); 1 external calls (sha256).


##### `_escaped`  (lines 589–592)

```
def _escaped(token: str) -> str
```

**Purpose**: Escapes one key so it can safely appear inside a JSON Pointer path. JSON Pointer is a standard way to point to a location inside a JSON document.

**Data flow**: It receives a key string. It replaces ~ with ~0 and / with ~1, then returns the escaped token so a key containing those characters still points to the correct place.

**Call relations**: _deduped and _condensed use this when they create same_as paths. Without it, a provider key containing a slash or tilde could make the pointer name the wrong object.

*Call graph*: called by 2 (_condensed, _deduped).


##### `_decoded_base64`  (lines 595–619)

```
def _decoded_base64(value: object) -> tuple[bytes, str | None] | None
```

**Purpose**: Safely decodes a value that a provider claimed is base64. If the claim is wrong, it leaves the value alone by returning None.

**Data flow**: It receives any value. It only accepts strings below the decode size limit, removes whitespace, strictly decodes base64, then tries to decode the bytes as UTF-8 text. The output is either decoded bytes plus optional text, or None if the input was not valid base64.

**Call relations**: _translated_node and _translated_data_url call this before changing provider output. Its strict checks are what prevent ordinary IDs, hashes, or mislabeled strings from being silently corrupted.

*Call graph*: called by 2 (_translated_data_url, _translated_node); 1 external calls (b64decode).


##### `search_connector_tools`  (lines 622–633)

```
async def search_connector_tools(ctx: ToolContext, args: SearchConnectorToolsInput) -> ToolResult
```

**Purpose**: Performs richer tool discovery inside one connector using a natural-language goal. It returns matching tools plus broker-provided advice such as suggested plans, guidance, and pitfalls.

**Data flow**: It receives a connector source ID and query. It looks up the connector entry, asks the broker's search API for matching tools in the current workspace, formats each tool's schema, and returns JSON with the connector ID, tools, plan, guidance, and pitfalls.

**Call relations**: This is a public discovery tool alongside describe_external_tools. It uses _registry to find the broker, _tool_json to format returned tools, and _json_result to package the answer.

*Call graph*: calls 3 internal fn (_json_result, _registry, _tool_json).


##### `_registry`  (lines 636–639)

```
def _registry(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Fetches the connector registry for the current turn and fails clearly if it is missing. The registry is the live list of connector providers and brokers available right now.

**Data flow**: It receives the tool context. If the context has a connector registry, it returns it; otherwise it raises an error explaining that connector tools were dispatched without one.

**Call relations**: All public connector tools call this first: list_external_tools, describe_external_tools, call_external_tool, and search_connector_tools. It is the shared guardrail that keeps these tools tied to the current turn's connector setup.

*Call graph*: called by 4 (call_external_tool, describe_external_tools, list_external_tools, search_connector_tools).


##### `_tool_json`  (lines 642–643)

```
def _tool_json(tool: BrokerTool) -> dict[str, object]
```

**Purpose**: Converts a broker tool object into the small JSON shape returned to the agent. It exposes the tool's slug, description, and input schema.

**Data flow**: It receives a BrokerTool. It reads the slug, description, and input_schema fields and returns them in a plain dictionary.

**Call relations**: describe_external_tools uses this when returning exact schemas, and search_connector_tools uses it for search results. It keeps the outward format consistent across both discovery paths.

*Call graph*: called by 2 (describe_external_tools, search_connector_tools).


##### `_discovery_query`  (lines 646–653)

```
def _discovery_query(explicit: str, unresolved: list[str]) -> str
```

**Purpose**: Builds a useful catalog search query when exact tool names did not resolve. It turns guessed slugs into ordinary search words so the broker can suggest real tools.

**Data flow**: It receives an explicit query and a list of unresolved tool names. If the explicit query is present, it returns that. Otherwise it lowercases the unresolved names, replaces punctuation with spaces, removes duplicate words while keeping order, and returns the resulting search phrase.

**Call relations**: describe_external_tools calls this when it needs to search for alternatives after a requested tool name is missing, or when no exact tool names were provided. It uses a regular expression to split slug-like names into words.

*Call graph*: called by 1 (describe_external_tools); 1 external calls (sub).


##### `_json_result`  (lines 656–657)

```
def _json_result(payload: dict[str, object]) -> ToolResult
```

**Purpose**: Wraps a dictionary as a standard text tool result. It is the small final step used by discovery-style connector tools.

**Data flow**: It receives a payload dictionary. It serializes the dictionary to JSON text, puts that text into a TextContent object, then returns a ToolResult containing it.

**Call relations**: list_external_tools, describe_external_tools, and search_connector_tools call this when they are ready to return. call_external_tool builds its result separately because _ConnectorCall.run already returns serialized text after extra cleanup.

*Call graph*: called by 3 (describe_external_tools, list_external_tools, search_connector_tools); 3 external calls (__init__, __init__, dumps).


### `extensions/mcp/ufo_ext_mcp.py`

`domain_logic` · `request handling`

This file is the bridge between the agent and external MCP servers chosen by a workspace. Instead of hard-coding every possible outside tool, it gives the agent two stable tools: one to ask an MCP server what tools it offers, and one to call a chosen tool. Think of it like a hotel concierge: first it asks a partner service for its menu, then it places an order using the exact item name and required details.

The file also defines how MCP servers are configured. A workspace stores a JSON map of server names to server URLs and optional auth tokens in a credential slot called `mcp_servers`. Before any call is made, the URL is checked to make sure it is HTTP or HTTPS, and the requested server name must exist.

When listing tools, the file connects to the selected MCP server, asks for its catalog, and returns each tool’s name, description, input shape, and whether it appears safe to repeat. When calling a tool, it checks that the request is not larger than 1 MiB, sends the call, and then returns either structured JSON from the server or joined text. Responses are also capped at 1 MiB. This matters because MCP servers are external and their output is untrusted, so the code keeps clear size limits and marks the exposed tools as untrusted content.

#### Function details

##### `McpServer._http_url`  (lines 70–73)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: This validates that a configured MCP server URL starts with `http://` or `https://`. It prevents the extension from trying to connect to unsupported or surprising kinds of addresses.

**Data flow**: It receives a URL string from the workspace’s MCP server configuration. It checks the string against the allowed URL pattern; if it matches, the same URL is kept, and if it does not, validation fails with a clear error.

**Call relations**: This is used automatically when an `McpServer` configuration object is built. That happens when the code reads the workspace’s `mcp_servers` credential before listing or calling tools, so bad server addresses are rejected before any network connection is attempted.


##### `mcp_client`  (lines 95–101)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: This creates a ready-to-use client for talking to one MCP server over HTTP. If the workspace supplied an auth token, it attaches it as a Bearer token in the request headers.

**Data flow**: It takes an `McpServer` object containing a URL and optional auth string. It builds HTTP headers when auth is present, creates a streamable HTTP transport for that URL, wraps it in a FastMCP client, and returns that client with a timeout.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` has found the right configured server. The FastMCP client it returns is then responsible for the MCP handshake and the actual `tools/list` or `tools/call` request.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 104–116)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: This looks up a named MCP server from the workspace’s private credential configuration. It is the safety gate that makes sure tool calls only go to servers the workspace has explicitly configured.

**Data flow**: It receives the current tool context and the requested server name. It reads the `mcp_servers` credential, parses and validates it as named server settings, then returns the matching `McpServer`; if the extension context is missing or the name is unknown, it raises an error.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` both call this before making any MCP request. It hands them the trusted, validated server settings they need before `mcp_client` can open a connection.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 119–135)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: This is the implementation of the public `list_mcp_tools` tool. It asks a configured MCP server for its available tool catalog so the agent can choose exact tool names and argument shapes instead of guessing.

**Data flow**: It receives the tool context and an input object containing a server name. It resolves that server, opens an MCP client, asks the server for its tools, converts each tool into a simple JSON-friendly record, and returns those records as a tool result.

**Call relations**: This is one of the handlers registered by `manifest`. In the normal flow, the agent should use it before `_call_mcp_tool`; it relies on `_server` for configuration, `mcp_client` for the connection, and `_json_result` to package the returned catalog.

*Call graph*: calls 3 internal fn (_json_result, _server, mcp_client).


##### `_call_mcp_tool`  (lines 138–150)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: This is the implementation of the public `call_mcp_tool` tool. It invokes one exact tool on one configured MCP server and turns the server’s response into a result the agent can read.

**Data flow**: It receives the tool context plus the server name, tool name, and JSON arguments. It resolves the server, checks that the serialized arguments are no larger than the request limit, sends the call, and then returns either an error result, structured JSON, or a JSON wrapper around plain text. It also enforces the response size limit before returning text.

**Call relations**: This handler is registered by `manifest` as the companion to `list_mcp_tools`. It first uses `_server` and `mcp_client` to reach the right MCP server, uses `_joined_text` when the server returns text blocks, uses `_bounded` to enforce size limits, and uses `_json_result` when returning normal JSON-shaped results.

*Call graph*: calls 5 internal fn (_bounded, _joined_text, _json_result, _server, mcp_client); 4 external calls (__init__, __init__, __init__, dumps).


##### `_joined_text`  (lines 153–154)

```
def _joined_text(content: list[object]) -> str
```

**Purpose**: This pulls plain text out of an MCP response that may contain several content blocks. It gives the caller one readable string instead of a mixed list of response pieces.

**Data flow**: It receives a list of content objects from an MCP result. It keeps only the blocks that are MCP text content, takes their text, joins them with newline characters, and returns the combined string.

**Call relations**: `_call_mcp_tool` uses this when a remote MCP tool fails or when the result does not contain structured JSON. It helps turn the server’s raw content blocks into a simple message for the final tool result.

*Call graph*: called by 1 (_call_mcp_tool).


##### `_bounded`  (lines 157–160)

```
def _bounded(text: str) -> str
```

**Purpose**: This enforces the maximum allowed response size. It fails loudly instead of quietly cutting off data, which avoids giving the agent incomplete or misleading output.

**Data flow**: It receives a text string, measures its size after encoding it as bytes, and compares that size with the configured 1 MiB limit. If the text is small enough it returns the same text; if it is too large it raises `McpError`.

**Call relations**: `_call_mcp_tool` uses this for error text from MCP calls, and `_json_result` uses it for JSON responses. It is the shared checkpoint that keeps oversized external responses from passing through.

*Call graph*: called by 2 (_call_mcp_tool, _json_result); 1 external calls (__init__).


##### `_json_result`  (lines 163–164)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: This turns a Python dictionary into the standard text-based `ToolResult` format used by the extension. It is the common wrapper for successful JSON-shaped answers.

**Data flow**: It receives a dictionary payload. It serializes that payload to JSON text, checks the serialized text with `_bounded`, wraps it in `TextContent`, and returns a `ToolResult` containing that text.

**Call relations**: `_list_mcp_tools` uses this to return the discovered tool catalog, and `_call_mcp_tool` uses it to return structured results or plain text wrapped as JSON. It centralizes response formatting and size checking.

*Call graph*: calls 1 internal fn (_bounded); called by 2 (_call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 167–198)

```
def manifest() -> Manifest
```

**Purpose**: This tells the host system what this extension provides: its name, version, tools, input models, handlers, and required credential slot. Without it, the platform would not know how to expose the MCP listing and calling tools.

**Data flow**: It takes no input. It builds a `Manifest` containing two tool definitions, marks their outputs as untrusted, and declares the `mcp_servers` credential slot where workspace MCP server settings are stored. It returns that manifest to the extension loader.

**Call relations**: This is the registration point for the whole file. It connects the user-facing tool names to `_list_mcp_tools` and `_call_mcp_tool`, and it tells the wider system that credentials are needed before those handlers can reach configured MCP servers.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Composio provider integration
Composio modules resolve dynamic toolkits, broker tool execution, proxy provider requests, and handle Composio-specific client transport.

### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, which means code elsewhere can refer to modules inside `extensions/composio/ufo_ext_composio` using normal Python import paths. Think of it like a label on a drawer: the drawer may hold useful tools in other files, and this label lets the rest of the system find that drawer reliably. Because the file is empty, it does not set up state, define shortcuts, run startup code, or expose any public names directly. Its value is structural: without it, some Python environments or tooling may not recognize this directory as a package, which could make imports fail or make package discovery less predictable.


### `extensions/composio/ufo_ext_composio/resolver.py`

`domain_logic` · `connector discovery and connection setup`

Composio offers a large catalog of outside tools and services. Instead of making this project keep a separate connector definition for every possible service, this file creates an “open namespace”: a way to accept any Composio toolkit by its slug, which is the short text name Composio uses for that toolkit. Think of it like a hotel front desk that can route guests to hundreds of rooms without printing a separate instruction sheet for each room.

The main piece is ComposioResolver. It is small and mostly stateless: it keeps only a shared ConnectorBroker, which is the part that later runs the actual connector work. When someone asks whether a provider name is valid, the resolver asks Composio’s live catalog. When someone needs connection details, it builds an OAuthProvider description, meaning a description of an OAuth sign-in flow where the user grants access without sharing a password. Here the provider host is blank because the account token stays with Composio and tools run through Composio’s side.

It can also make a ConnectorEntry, which is the registry-facing record that says “this provider name should use this broker.” Finally, it can search Composio’s toolkit catalog and return simple catalog entries for discovery. The transfer host list is important because it tells the sandbox which Composio file-storage hosts are allowed when tool inputs or outputs include files.

#### Function details

##### `ComposioResolver.transfer_hosts`  (lines 32–33)

```
def transfer_hosts(self) -> tuple[str, ...]
```

**Purpose**: This property returns the Composio file-transfer hosts that are allowed for brokered tool runs. It matters because files produced or consumed by Composio tools still need to pass through approved sandbox routes.

**Data flow**: It takes no direct input beyond the resolver object. It reads the shared Composio transfer-host constant and returns it as a tuple of host names, without changing anything.

**Call relations**: When the connector system needs to know which outside file hosts are safe for this resolver, it asks this property. The answer is handed back directly from the Composio client constants, so all Composio-backed toolkits share the same allowed file-transfer locations.


##### `ComposioResolver.claims`  (lines 35–36)

```
async def claims(self, provider: str) -> bool
```

**Purpose**: This function answers the question: “Can Composio broker this provider name?” It checks the provider slug against Composio’s current toolkit catalog instead of relying on a hard-coded local list.

**Data flow**: It receives a provider name as text. It creates or retrieves a Composio client, asks whether that toolkit is connectable, and returns true if Composio recognizes it as connectable and false otherwise. It does not store the result on the resolver.

**Call relations**: During connector lookup, the wider registry can ask this resolver whether it claims an unregistered provider name. This function calls the Composio client to make that live decision, so the resolver only accepts names that Composio says it can actually connect.

*Call graph*: 1 external calls (composio_client).


##### `ComposioResolver.descriptor`  (lines 38–39)

```
def descriptor(self, provider: str) -> OAuthProvider
```

**Purpose**: This function builds the connection description used for a validated Composio provider. It returns an OAuth provider object that tells the rest of the system how this provider should be treated during sign-in.

**Data flow**: It receives the provider slug. It places that slug into a ComposioOAuthProvider object and deliberately uses an empty host, because the real account token and execution stay with Composio rather than a separate provider host. The result is a provider descriptor; nothing else is changed.

**Call relations**: After a provider has been accepted as a Composio-backed toolkit, the connection flow can ask for its descriptor. This function hands off to ComposioOAuthProvider to create the object the rest of the connector framework understands.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.entry`  (lines 41–44)

```
def entry(self, provider: str) -> ConnectorEntry
```

**Purpose**: This function creates the registry entry for a Composio-backed provider. It turns a raw provider slug into a human-readable label and ties it to the shared Composio broker.

**Data flow**: It receives a provider slug such as a lower-case name with underscores. It converts that into a title-style label for display, combines it with the original provider name and this resolver’s broker, and returns a ConnectorEntry. It does not contact Composio or change stored state.

**Call relations**: Once the system has decided that this resolver owns a provider name, it needs an entry that says which broker should run it. This function creates that entry and points it at the resolver’s shared broker, so many different Composio toolkit names can all use the same execution path.

*Call graph*: 1 external calls (__init__).


##### `ComposioResolver.catalog`  (lines 46–48)

```
async def catalog(self, query: str, limit: int=TOOL_SEARCH_LIMIT) -> tuple[CatalogEntry, ...]
```

**Purpose**: This function searches Composio’s toolkit catalog and returns results the connector discovery system can show to users. It helps users find services they can actually connect through Composio.

**Data flow**: It receives a search query and an optional maximum number of results. It asks the Composio client for matching toolkits, then turns each returned slug and label into a CatalogEntry. The output is a tuple of catalog entries; the resolver itself is not modified.

**Call relations**: When a discovery tool or user interface searches for connectable services, this function is the bridge to Composio’s live catalog. It calls the Composio client for the raw matches, wraps each match in the project’s CatalogEntry shape, and returns them to the caller for display or selection.

*Call graph*: 2 external calls (__init__, composio_client).


### `extensions/composio/ufo_ext_composio/broker.py`

`io_transport` · `request handling`

Think of this file as a front desk for all Composio-backed connectors. The rest of the project asks for plain connector actions, such as “what tools are available?”, “what inputs does this tool need?”, or “run this tool for this workspace.” This broker translates those requests into Composio API calls and translates Composio’s answers back into UFO’s own connector shapes.

A key safety idea here is that the broker works on behalf of a workspace-specific Composio user. When it executes a tool or creates a credential, it checks that the connected account belongs to that workspace’s broker user. That prevents a “confused deputy” problem, where one user might accidentally or maliciously cause the system to use someone else’s account.

The file also smooths over failure cases. If a tool slug is wrong, it tries to return a better error that includes real available tool names. If an account is stale or no longer known to this broker, it tells the agent to ask the member to reconnect instead of suggesting unrelated tool names.

Files are treated carefully too. Tool results may contain file objects hidden anywhere in nested response data, and uploads are staged through Composio so the system can pass files to tools without holding provider secrets directly.

#### Function details

##### `ComposioBroker.tools`  (lines 48–50)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Composio tools for a provider that match a search query, then presents them in UFO’s standard broker-tool format. This is used when the system needs to discover what actions are available for a connector.

**Data flow**: It receives a workspace id, provider name, and search text. It asks the current Composio client for matching tools, then passes the raw Composio response through a converter that keeps only useful fields like slug and short description. It returns a tuple of broker tool records.

**Call relations**: When the connector layer needs tool discovery, it calls this method. The method gets a fresh Composio client for that call, then hands the raw listing to _discovered_tools so the rest of the system does not need to understand Composio’s response shape.

*Call graph*: calls 1 internal fn (_discovered_tools); 1 external calls (composio_client).


##### `ComposioBroker.schema`  (lines 52–65)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Fetches the input schema for one specific Composio tool. A schema is a machine-readable description of what arguments the tool accepts, like a form definition.

**Data flow**: It receives the workspace id, provider, and tool slug. It asks Composio for that tool’s schema, rewrites file-upload inputs into UFO’s workspace-file format, and returns a BrokerTool containing the slug, description, and input schema. If Composio says the tool does not exist, it raises an UnknownBrokerTool error.

**Call relations**: This is called after a tool has been chosen and the system needs to know how to call it correctly. It relies on the Composio client to fetch the schema and on workspace_file_schema to adapt Composio’s file-upload vocabulary into the connector system’s vocabulary.

*Call graph*: 4 external calls (__init__, __init__, composio_client, workspace_file_schema).


##### `ComposioBroker.execute`  (lines 67–90)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a selected Composio tool for a workspace and connected account. This is the main path for turning a requested connector action into an actual server-side Composio execution.

**Data flow**: It receives the workspace id, provider, tool slug, tool arguments, connected account id, and optional idempotency key, which helps avoid duplicate effects if the same request is retried. It builds the workspace’s Composio broker-user id, sends the execution request, and returns the response dictionary. If Composio reports a stale account, it changes the error into reconnect guidance; if the slug is missing, it tries to improve the error with real tool names.

**Call relations**: The connector runtime calls this when it is time to run a tool. It gets a current Composio client, calls Composio’s execute API, and uses _stale_account, _reconnect_error, and _slug_miss to turn confusing Composio failures into messages the agent can act on.

*Call graph*: calls 3 internal fn (_slug_miss, _reconnect_error, _stale_account); 1 external calls (composio_client).


##### `ComposioBroker.file_outputs`  (lines 92–97)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Finds files produced by a tool execution response. This matters because Composio can return file references nested inside a larger result, not just at the top level.

**Data flow**: It receives the full response dictionary from a tool run. It walks through the response recursively, looking for objects that look like Composio file outputs with a name and download URL. It returns those files as BrokerFile records.

**Call relations**: After execute returns a response, higher-level code can call this to extract downloadable files. It delegates the recursive search to _collect_files, keeping this public method simple.

*Call graph*: calls 1 internal fn (_collect_files).


##### `ComposioBroker.stage_upload`  (lines 99–115)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates an upload slot in Composio’s file store for a file that will be passed into a tool. In everyday terms, it asks Composio for a temporary place to put the file before the tool runs.

**Data flow**: It receives the workspace id, provider, tool slug, filename, file type, and MD5 checksum. It asks Composio to create an upload target, then returns a StagedUpload containing the URL to upload to, the content type to use, and the argument object that should later be passed to the tool.

**Call relations**: This is used before executing tools that need file inputs. It calls the Composio client to reserve the upload location, then packages the result in the standard staged-upload shape expected by UFO’s connector flow.

*Call graph*: 2 external calls (__init__, composio_client).


##### `ComposioBroker.search`  (lines 117–120)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches Composio’s Tool Router for tools related to a user query. This is broader discovery than simply listing tools by provider and helps the agent find a useful action.

**Data flow**: It receives the workspace id, provider, and search query. It gets a current Composio client and passes all of that to the Composio search helper. It returns a BrokerSearch result from that helper.

**Call relations**: When the agent needs guided tool search, this method is the broker’s entry point. It does not transform the result itself; it hands the work to search_connector_tools with a fresh Composio client.

*Call graph*: 2 external calls (composio_client, search_connector_tools).


##### `ComposioBroker.credential`  (lines 122–138)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a safe credential object for proxying provider HTTP requests through Composio. It confirms the account belongs to this workspace’s broker user, but it does not hand provider tokens back to UFO.

**Data flow**: It receives the workspace id, provider, and connected account id. It asks Composio to confirm that this account is connected for the workspace’s broker user. If the account is missing, it raises an error telling the user to reconnect. If valid, it returns a Credential whose transport sends HTTP through Composio’s proxy-execute path using the Composio API key.

**Call relations**: Code that needs provider-style HTTP access calls this instead of asking for raw secrets. The method verifies ownership through the Composio client, uses _reconnect_error for stale grants, and wraps ComposioProxyTransport in a Credential for the rest of the SDK.

*Call graph*: calls 1 internal fn (_reconnect_error); 4 external calls (__init__, __init__, AsyncHTTPTransport, composio_client).


##### `ComposioBroker._slug_miss`  (lines 140–161)

```
async def _slug_miss(self, client: composio.ComposioClient, provider: str, slug: str, error: composio.ComposioError) -> composio.ComposioError
```

**Purpose**: Improves the error message when a tool slug is not found during execution. Instead of only saying “not found,” it tries to include real tool slugs from that provider so the next attempt can be better.

**Data flow**: It receives a Composio client, provider, missing slug, and original error. It turns the bad slug into search words, asks Composio for likely tools, and if needed falls back to listing tools without a query. If it finds tools, it returns a new ComposioError with the original message plus available tool names; otherwise it returns the original error.

**Call relations**: ComposioBroker.execute calls this only after Composio reports a not-found error for execution. It uses _discovered_tools to convert Composio listings into clean tool names before building the clearer error.

*Call graph*: calls 2 internal fn (_discovered_tools, list_tools); called by 1 (execute); 2 external calls (sub, ComposioError).


##### `_collect_files`  (lines 164–173)

```
def _collect_files(value: object, found: list[BrokerFile]) -> None
```

**Purpose**: Recursively searches any nested response data for Composio file objects. This is the helper that makes file extraction work even when files are buried inside lists or dictionaries.

**Data flow**: It receives any value and a list that is collecting found files. If the value looks like a Composio file object with a non-empty URL, it adds a BrokerFile to the list. If the value is a dictionary or list, it searches each contained value; otherwise it leaves it alone.

**Call relations**: ComposioBroker.file_outputs starts the collection process and passes in the tool response. _collect_files does the walking and creates BrokerFile records whenever it finds a matching file shape.

*Call graph*: called by 1 (file_outputs); 1 external calls (__init__).


##### `_stale_account`  (lines 176–187)

```
def _stale_account(error: composio.ComposioError, account_id: str) -> bool
```

**Purpose**: Decides whether a Composio execution error probably means the connected account is no longer valid for this broker. This helps the system tell the user to reconnect instead of treating the problem as a bad tool name.

**Data flow**: It receives a Composio error and the connected account id that was used. It lowercases the error body and looks for narrow signs of a missing connected account, either Composio’s own “connected account not found” wording or the exact account id with “not found.” It returns true if the error matches that stale-account pattern, otherwise false.

**Call relations**: ComposioBroker.execute calls this when Composio rejects a tool run. If it returns true, execute uses _reconnect_error to produce reconnect guidance and avoids the slug-miss path.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 190–191)

```
def _reconnect_error(error: composio.ComposioError, provider: str) -> composio.ComposioError
```

**Purpose**: Adds clear reconnect instructions to a Composio error. It turns a low-level missing-account failure into something the agent can explain to the user.

**Data flow**: It receives the original Composio error and provider name. It keeps the original status and body, appends stale-grant guidance for that provider, and returns a new ComposioError with the combined message.

**Call relations**: ComposioBroker.execute uses this when a tool run points to a stale connected account. ComposioBroker.credential uses it when account verification fails. In both cases, it relies on stale_grant_guidance to produce the user-facing reconnect advice.

*Call graph*: called by 2 (credential, execute); 2 external calls (stale_grant_guidance, ComposioError).


##### `_discovered_tools`  (lines 194–216)

```
def _discovered_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Converts Composio’s raw tool-list response into UFO’s simple BrokerTool records. It filters out malformed entries and keeps descriptions short enough for discovery displays.

**Data flow**: It receives a dictionary from Composio, looks for an items list, and walks through each item. For each valid item, it chooses a slug from the slug or name field, trims the description to a fixed cap, and creates a BrokerTool. It returns all valid tools as a tuple.

**Call relations**: ComposioBroker.tools uses this for normal discovery results. ComposioBroker._slug_miss also uses it when building a helpful error message after a missing tool slug.

*Call graph*: called by 2 (_slug_miss, tools); 1 external calls (__init__).


### `extensions/composio/ufo_ext_composio/client.py`

`io_transport` · `request handling and connector tool discovery/execution`

Composio acts like a broker between this project and many outside services. Instead of this project storing a separate secret token for every service, Composio keeps those tokens and lets the agent ask Composio to search or run tools on the user’s behalf. This file is the main doorway to that broker.

The file defines which toolkits are allowed, which are deliberately blocked, and how to decide whether a toolkit is useful enough to offer to users. A toolkit must support Composio-managed sign-in, have at least one tool, and not be on the hand-written ban list.

The `ComposioClient` class wraps Composio’s web API. It can create an OAuth consent link, confirm that a connected account belongs to the expected workspace user, list tools, fetch a tool schema, execute a tool, and create upload slots for files. It also opens Tool Router sessions, which are used for smarter search over available tools.

A few helper functions turn Composio’s raw replies into safer local shapes. For example, file-upload fields are rewritten so the agent only sees a simple `/workspace` file path, not Composio’s internal storage details. Without this file, the connector system would not know how to discover, authorize, or run Composio-backed tools safely.

#### Function details

##### `connectable`  (lines 120–144)

```
def connectable(slug: str, toolkit: Mapping[str, object]) -> bool
```

**Purpose**: Decides whether a Composio toolkit should be offered to users by this deployment. It keeps out banned services, services with no usable managed sign-in, and services with no tools.

**Data flow**: It receives a toolkit slug and a catalog record from Composio. It checks the slug against the local ban list, then reads the record to see whether Composio has managed authentication schemes and a positive tool count. It returns `True` only when all checks pass; otherwise it returns `False`.

**Call relations**: This is the gatekeeper used when checking one toolkit and when listing many toolkits. `ComposioClient.connectable_toolkit` uses it after fetching a toolkit detail page, and `ComposioClient.list_toolkits` uses it while filtering search results.

*Call graph*: called by 2 (connectable_toolkit, list_toolkits).


##### `ComposioError.__init__`  (lines 151–154)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Builds a clear exception for a failed or unusable Composio response. It preserves both the HTTP status code and the response body so callers can report what went wrong.

**Data flow**: It receives a numeric status and a text body. It formats them into a readable error message, stores both values on the exception, and returns a normal Python exception object ready to be raised.

**Call relations**: Many client methods raise this when Composio refuses a request or returns data in a shape the code cannot safely use. It is also raised by `_body`, the shared response parser, so low-level HTTP failures become the same project-specific error.

*Call graph*: called by 6 (_auth_config, connect_link, connected_account, create_upload, tool_router_session, _body).


##### `ComposioClient.connect_link`  (lines 171–180)

```
async def connect_link(self, toolkit: str, user_id: str, callback_url: str) -> str
```

**Purpose**: Creates the sign-in link a user opens to connect an outside service through Composio. This is the start of the OAuth flow, meaning the standard browser-based consent process for granting access.

**Data flow**: It receives a toolkit name, a broker user id, and a callback URL. It first finds or creates an authentication configuration, then asks Composio for a connected-account link. It returns the redirect URL that should be shown or sent to the user, and raises an error if Composio does not provide one.

**Call relations**: This method starts by calling `_auth_config` so the sign-in flow has credentials to ride on. It then sends the request through `_post`; if the response is malformed, it raises `ComposioError` rather than letting the caller continue with a broken link.

*Call graph*: calls 3 internal fn (_auth_config, _post, __init__).


##### `ComposioClient.connected_account`  (lines 182–206)

```
async def connected_account(self, account_id: str, expected_user_id: str, expected_toolkit: str) -> OAuthAccount
```

**Purpose**: Confirms that a connected account is real, active, owned by the expected workspace user, and tied to the expected toolkit. This prevents one user or connector from reusing another account id.

**Data flow**: It receives a connected account id, the expected user id, and the expected toolkit slug. It fetches the account from Composio, checks ownership, active status, and toolkit identity, then returns an `OAuthAccount` containing the account id. If any check fails, it raises an error.

**Call relations**: This method relies on `_get` to read Composio’s account record. It is part of the safety check after OAuth completes: only after these checks does the project treat the account id as a valid grant.

*Call graph*: calls 2 internal fn (_get, __init__); 1 external calls (__init__).


##### `ComposioClient.list_tools`  (lines 208–214)

```
async def list_tools(self, toolkit: str, query: str='', limit: int=TOOL_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Asks Composio for tools belonging to one toolkit, optionally narrowed by a search query. This lets the connector layer discover what actions are available instead of hard-coding them.

**Data flow**: It receives a toolkit slug, an optional query, and a limit. It turns those into URL query parameters and sends a GET request. It returns Composio’s response as a dictionary.

**Call relations**: It delegates the web request to `_get`. The broker layer calls it when it needs to resolve or recover from a tool slug that was not already known.

*Call graph*: calls 1 internal fn (_get); called by 1 (_slug_miss).


##### `ComposioClient.tool_schema`  (lines 216–217)

```
async def tool_schema(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed description and input format for one Composio tool. A schema is a structured description of what arguments a tool accepts.

**Data flow**: It receives a tool slug, builds the matching Composio API path, and fetches the record. It returns the parsed response dictionary.

**Call relations**: It is a thin, named wrapper around `_get`. Other parts of the connector system can use it when they need exact instructions for calling a specific tool.

*Call graph*: calls 1 internal fn (_get).


##### `ComposioClient.connectable_toolkit`  (lines 219–236)

```
async def connectable_toolkit(self, slug: str) -> str | None
```

**Purpose**: Checks whether a single user-supplied toolkit slug names a toolkit this deployment is willing and able to broker. It also protects the API path from unsafe characters such as slashes.

**Data flow**: It receives a slug. If the slug does not look like a simple toolkit identifier, it returns `None` without making a web request. Otherwise it fetches the toolkit from Composio, returns `None` for a not-found result or a non-connectable toolkit, and returns the toolkit’s display name when it is acceptable.

**Call relations**: This method combines the low-level fetch from `_get` with the local policy in `connectable`. It is used when the resolver decides whether this Composio extension can claim a requested connector name.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.list_toolkits`  (lines 238–256)

```
async def list_toolkits(self, query: str, limit: int) -> tuple[tuple[str, str], ...]
```

**Purpose**: Searches Composio’s catalog for toolkits and returns only the ones this deployment can actually offer. This powers discovery of the open connector set.

**Data flow**: It receives a search string and a result limit. It asks Composio for matching toolkits, walks through the returned items, filters out malformed or non-connectable entries, and returns a tuple of `(slug, label)` pairs.

**Call relations**: It uses `_get` for the catalog request and `connectable` for the local allow-or-block decision. It is the bulk-search companion to `connectable_toolkit`.

*Call graph*: calls 2 internal fn (_get, connectable).


##### `ComposioClient.execute_tool`  (lines 258–272)

```
async def execute_tool(self, slug: str, arguments: Mapping[str, object], user_id: str, connected_account_id: str | None=None, idempotency_key: str | None=None) -> dict[str, object]
```

**Purpose**: Runs a Composio tool on Composio’s server, optionally for a specific connected account. This is where an already-authorized connector action actually happens.

**Data flow**: It receives a tool slug, argument values, a broker user id, and optionally a connected account id and idempotency key. It builds the execute request, rejects it if the JSON payload is larger than the configured safety limit, adds an idempotency header when provided, and returns Composio’s execution response.

**Call relations**: It sends the request through `_post`. Unlike search, execution goes directly to Composio’s execute API so Composio can inject the stored account token without this project ever seeing it.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `ComposioClient.create_upload`  (lines 274–298)

```
async def create_upload(self, toolkit: str, slug: str, filename: str, mimetype: str, md5: str) -> 'ComposioUpload'
```

**Purpose**: Asks Composio for a temporary place to upload a file that a tool will later use. This keeps file-transfer details out of the agent’s prompt and out of the regular tool arguments.

**Data flow**: It receives the toolkit, tool slug, filename, MIME type, and MD5 hash of the file. It posts those details to Composio, checks that a storage key came back, and returns a `ComposioUpload` with that key and, when needed, a presigned PUT URL for uploading the bytes.

**Call relations**: It uses `_post` for the upload request and raises `ComposioError` if Composio’s reply is missing required fields. The returned object tells the sandbox where to PUT the file and what key to pass to the tool.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `ComposioClient.tool_router_session`  (lines 300–311)

```
async def tool_router_session(self, user_id: str, toolkits: list[str]) -> ToolRouterSession
```

**Purpose**: Opens a Composio Tool Router session for semantic tool search. Semantic search means searching by intended use, not just by exact tool name.

**Data flow**: It receives a broker user id and a list of enabled toolkits. It asks Composio to create a router session, reads the session id and MCP URL from the response, and returns them as a `ToolRouterSession`. If either value is missing, it raises an error.

**Call relations**: This method is called by `search_connector_tools` when no cached session exists yet. It uses `_post` to create the remote session, and the returned URL is later used for the actual search call.

*Call graph*: calls 2 internal fn (_post, __init__); called by 1 (search_connector_tools); 1 external calls (__init__).


##### `ComposioClient._auth_config`  (lines 313–331)

```
async def _auth_config(self, toolkit: str) -> str
```

**Purpose**: Finds the authentication configuration to use for a toolkit, creating a Composio-managed one if none exists. This ensures the consent link has a valid setup behind it.

**Data flow**: It receives a toolkit slug. It first asks Composio for an existing auth config and extracts an id if possible. If none exists, it posts a request to create a managed auth config, checks that the new response contains an id, and returns that id.

**Call relations**: This private helper is called by `connect_link` before creating a user-facing consent URL. It uses `_get`, `_post`, and `_auth_config_id`, and raises `ComposioError` if creation succeeds but the response cannot be trusted.

*Call graph*: calls 4 internal fn (_get, _post, __init__, _auth_config_id); called by 1 (connect_link).


##### `ComposioClient._get`  (lines 333–335)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a GET request to Composio and parses the response in the project’s standard way. GET is the usual web method for reading data.

**Data flow**: It receives an API path and optional query parameters. It opens an HTTP client, sends the request, passes the response to `_body`, and returns the parsed dictionary.

**Call relations**: All read-style client methods call this helper instead of repeating HTTP setup and response checking themselves. It gets its configured HTTP client from `_http`.

*Call graph*: calls 2 internal fn (_http, _body); called by 6 (_auth_config, connectable_toolkit, connected_account, list_toolkits, list_tools, tool_schema).


##### `ComposioClient._post`  (lines 337–341)

```
async def _post(self, path: str, body: dict[str, object], headers: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Sends a POST request to Composio and parses the response in the project’s standard way. POST is the usual web method for creating something or triggering an action.

**Data flow**: It receives an API path, a JSON body, and optional headers. It opens an HTTP client, sends the JSON request, passes the response to `_body`, and returns the parsed dictionary.

**Call relations**: All write or action-style client methods call this helper, including link creation, auth config creation, upload setup, tool execution, and Tool Router session creation. It gets its configured HTTP client from `_http`.

*Call graph*: calls 2 internal fn (_http, _body); called by 5 (_auth_config, connect_link, create_upload, execute_tool, tool_router_session).


##### `ComposioClient._http`  (lines 343–349)

```
def _http(self) -> httpx.AsyncClient
```

**Purpose**: Builds the configured asynchronous HTTP client used for Composio API calls. An asynchronous client lets the program wait for network replies without blocking other work.

**Data flow**: It reads the client’s API key and optional test transport, then creates an `httpx.AsyncClient` with Composio’s base URL, API key header, timeout, and transport. It returns that client to be used inside a request block.

**Call relations**: `_get` and `_post` call this every time they make a request. Centralizing it keeps the API key, timeout, base URL, and test transport behavior consistent.

*Call graph*: called by 2 (_get, _post); 1 external calls (AsyncClient).


##### `_body`  (lines 352–360)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns a raw HTTP response from Composio into a safe Python dictionary or raises a clear error. It prevents callers from accidentally treating an error page or unexpected JSON value as valid data.

**Data flow**: It receives an HTTP response. If the status code means failure, it raises `ComposioError`; if the body is empty, it returns an empty dictionary; otherwise it parses JSON and verifies that the result is an object/dictionary.

**Call relations**: `ComposioClient._get` and `ComposioClient._post` both pass every response through this function. This makes response validation one shared rule for the whole client.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_get, _post); 1 external calls (json).


##### `workspace_file_schema`  (lines 363–389)

```
def workspace_file_schema(value: object) -> object
```

**Purpose**: Rewrites Composio tool input schemas so file uploads are shown to the agent as simple workspace file paths. This hides broker storage details the model should not invent or manipulate.

**Data flow**: It receives any schema-like value. When it finds a dictionary marked as file-uploadable, it replaces that piece with an object requiring `workspace_file`; for dictionaries and lists, it recursively rewrites their children; for other values, it leaves them unchanged.

**Call relations**: `_search_result` calls this while preparing tool descriptions for the dynamic connector tools. The result is a friendlier and safer schema for the agent to follow.

*Call graph*: called by 1 (_search_result).


##### `_auth_config_id`  (lines 392–399)

```
def _auth_config_id(payload: dict[str, object]) -> str | None
```

**Purpose**: Extracts the first authentication configuration id from a Composio list response. It is a small helper for the “use existing config if available” path.

**Data flow**: It receives a parsed response dictionary. It looks for an `items` list, scans for the first dictionary with a string `id`, and returns that id. If the shape does not match, it returns `None`.

**Call relations**: `ComposioClient._auth_config` calls this after listing auth configs. A found id means no new auth config needs to be created.

*Call graph*: called by 1 (_auth_config).


##### `composio_client`  (lines 402–409)

```
def composio_client() -> ComposioClient
```

**Purpose**: Creates the default `ComposioClient` for this deployment using the API key from the environment. It fails loudly if the key is missing because connector OAuth cannot work without it.

**Data flow**: It reads `COMPOSIO_API_KEY` from environment variables. If the value is missing or empty, it raises a runtime error; otherwise it returns a new `ComposioClient` using that key.

**Call relations**: Other startup or request code can call this when it needs the real Composio client. It is the bridge between deployment configuration and the API wrapper class.

*Call graph*: 1 external calls (__init__).


##### `_dict`  (lines 416–417)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. This keeps parsing code simple when Composio responses may omit fields or use unexpected shapes.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: `_search_result` uses this repeatedly while walking nested Tool Router search results. It avoids crashes when optional nested fields are missing or malformed.

*Call graph*: called by 1 (_search_result).


##### `_str_tuple`  (lines 420–423)

```
def _str_tuple(value: object) -> tuple[str, ...]
```

**Purpose**: Safely extracts a tuple of non-empty strings from a list. It filters away missing, empty, or non-string values from external data.

**Data flow**: It receives any value. If the value is not a list, it returns an empty tuple; if it is a list, it keeps only entries that are non-empty strings and returns them as a tuple.

**Call relations**: `_search_result` uses this for tool slugs, plan steps, guidance, and pitfalls returned by the Tool Router. This gives the rest of the code clean string sequences.

*Call graph*: called by 1 (_search_result).


##### `_search_result`  (lines 426–460)

```
def _search_result(result: dict[str, object]) -> BrokerSearch
```

**Purpose**: Converts a raw Tool Router search result into the project’s `BrokerSearch` format. That format contains matching tools plus practical advice such as plan steps, guidance, and pitfalls.

**Data flow**: It receives a result dictionary from Composio’s Tool Router. It reads nested search results and tool schemas, deduplicates tool slugs, rewrites file-upload schemas with `workspace_file_schema`, builds `BrokerTool` entries, gathers plan and warning text, and returns a `BrokerSearch` object.

**Call relations**: `search_connector_tools` calls this after the remote MCP tool search finishes. It is the translation layer between Composio’s search response and what the dynamic connector tools show to the agent.

*Call graph*: calls 3 internal fn (_dict, _str_tuple, workspace_file_schema); called by 1 (search_connector_tools); 2 external calls (__init__, __init__).


##### `search_connector_tools`  (lines 463–486)

```
async def search_connector_tools(client: ComposioClient, workspace_id: UUID, connector: str, query: str) -> BrokerSearch
```

**Purpose**: Searches for useful Composio tools for a connector using Composio’s Tool Router. It is designed for natural-language discovery, such as finding the right tool for a user’s task.

**Data flow**: It receives a Composio client, workspace id, connector slug, and query. It builds the broker user id, reuses or creates a cached Tool Router session for that user and connector, calls the remote search tool through MCP, then converts the result into `BrokerSearch`.

**Call relations**: If no cached session exists, it calls `ComposioClient.tool_router_session` while protected by a lock so concurrent searches do not create duplicate sessions. It then hands the search call to `mcp_session.mcp_call_tool` and passes the answer to `_search_result` for cleanup.

*Call graph*: calls 2 internal fn (tool_router_session, _search_result); 1 external calls (mcp_call_tool).


### `extensions/composio/ufo_ext_composio/proxy.py`

`io_transport` · `request handling`

Composio keeps provider credentials, such as API tokens, on its own side. That is safer, but it creates a practical problem: connectors in this system still want to make normal HTTP calls to services like calendars, CRMs, or file providers. This file is the adapter that makes both things true at once.

The main piece, ComposioProxyTransport, acts like a mail forwarding office. A connector gives it a normal provider request: method, URL, query parameters, headers, and body. The transport repackages those details into a POST request to Composio's proxy endpoint. It includes the connected account ID, so Composio knows which stored credential to use. It deliberately skips headers that should not be forwarded, such as Authorization and Content-Length, because Composio and the HTTP layer must control those.

When Composio replies, the file rebuilds an httpx.Response, which is the response object expected by the rest of the code. It preserves useful provider headers so things like pagination still work. If the provider response is binary or too large, Composio may store it elsewhere and return a temporary download URL; this file turns that into a redirect response instead of pulling huge bytes through the shared proxy.

ComposioRequestForwarder uses the same machinery for one-off forwarded CLI requests. It adds size and time limits so a slow or huge backend response cannot tie up the proxy process forever.

#### Function details

##### `ComposioProxyTransport.handle_async_request`  (lines 63–102)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the main request translator. It takes a normal provider HTTP request, rewrites it as a Composio proxy-execute call, then returns a response shaped like the provider answered directly.

**Data flow**: It receives an httpx request containing a method, URL, headers, query parameters, timeout settings, and optional body. It reads the body, builds a JSON payload for Composio, copies safe query and header values into that payload, and sends a POST request to Composio's proxy endpoint through the inner transport. It then reads Composio's response with a size limit if one is configured. If Composio itself reports an error, that error response is passed back. Otherwise, the Composio payload is converted into a provider-style HTTP response.

**Call relations**: This is the method httpx calls when the transport is used for an outgoing provider request. During its work it relies on _read_bounded to safely collect the broker response, and on _provider_response to turn Composio's wrapped response format back into the shape the connector expects.

*Call graph*: calls 2 internal fn (_provider_response, _read_bounded); 4 external calls (Request, aread, Response, loads).


##### `ComposioProxyTransport._read_bounded`  (lines 104–119)

```
async def _read_bounded(self, response: httpx.Response) -> bytes
```

**Purpose**: This reads the full response body from Composio while optionally enforcing a maximum size. It protects shared proxy processes from buffering an unexpectedly huge response in memory.

**Data flow**: It receives an httpx response from the Composio broker. If no maximum size is set, it simply reads and returns all bytes. If a maximum is set, it reads the response chunk by chunk, adds each chunk to a buffer, and checks the growing size. If the body grows beyond the limit, it closes the response and raises a Composio error instead of continuing to store more bytes.

**Call relations**: handle_async_request calls this right after Composio replies. Its output is the raw response body that handle_async_request either returns as an error response or decodes as JSON for _provider_response.

*Call graph*: called by 1 (handle_async_request); 4 external calls (aclose, aiter_bytes, aread, ComposioError).


##### `ComposioProxyTransport._provider_response`  (lines 121–165)

```
def _provider_response(self, payload: dict[str, Any], request: httpx.Request) -> httpx.Response
```

**Purpose**: This rebuilds the provider's HTTP response from Composio's proxy response format. It makes the rest of the system feel as if it talked to the provider directly.

**Data flow**: It receives a decoded Composio payload plus the original request. It unwraps nested data envelopes until it reaches the provider status, headers, and body. It removes headers that describe body transfer details, because those may no longer be true after proxying. If Composio reports binary data stored at a temporary URL, it returns a redirect response pointing to that URL. Otherwise, it turns JSON-like data, text, empty data, or other simple values into response bytes and returns an httpx response with the provider status and headers.

**Call relations**: handle_async_request calls this after it has successfully read and decoded Composio's response. This function is the final step that hands a normal-looking provider response back to the connector or caller.

*Call graph*: called by 1 (handle_async_request); 4 external calls (Response, dumps, cast, ComposioError).


##### `ComposioProxyTransport.aclose`  (lines 167–168)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport used to talk to Composio. It is used to release network resources when the proxy transport is no longer needed.

**Data flow**: It has no input besides the transport object itself. It asks the inner transport to close any open connections or related resources. It returns nothing.

**Call relations**: Code that creates a ComposioProxyTransport can call this during cleanup. ComposioRequestForwarder.forward does so after each forwarded request, ensuring the temporary transport does not leave connections open.


##### `ComposioRequestForwarder.forward`  (lines 185–213)

```
async def forward(self, account_id: str, method: str, url: str, headers: Mapping[str, str], body: bytes) -> ForwardedResponse
```

**Purpose**: This forwards one provider request through Composio for a CLI or broker-style call. It wraps the proxy transport with safety limits for response size and total time.

**Data flow**: It receives a connected account ID, HTTP method, URL, headers, and body bytes. It gets the current Composio client and API key, creates a ComposioProxyTransport for that account, builds an httpx request with a timeout, and sends it through the transport. It reads the returned response body. If the whole operation takes too long, it raises a Composio timeout error. In all cases it closes the transport before finishing. On success, it returns a ForwardedResponse containing the status code, headers, and body bytes.

**Call relations**: This is the one-shot forwarding path used when the proxy needs to execute a request on behalf of a Composio-granted credential. It constructs and drives ComposioProxyTransport directly, then packages the result into the ForwardedResponse type expected by the broader forwarding system.

*Call graph*: 8 external calls (__init__, __init__, timeout, AsyncHTTPTransport, Request, Timeout, ComposioError, composio_client).


### `extensions/composio/ufo_ext_composio/mcp_session.py`

`io_transport` · `request handling`

This file is a small bridge between this project and Composio’s Tool Router, which is reached through MCP, the Model Context Protocol: a standard way for an app to talk to external tools. In plain terms, it opens a short-lived web conversation with a Composio endpoint, asks one named tool a question, and translates the reply into a simple shape the rest of the code can use.

The important thing is that this is for searching available tools, especially Composio’s `COMPOSIO_SEARCH_TOOLS` tool. Actual tool execution happens somewhere else through Composio’s execute API, so billing, permissions, and grants stay attached to that official execution path. This file is like a receptionist who asks the directory where something is, not the worker who performs the job.

The function opens a streamable HTTP session, calls the requested MCP tool with the given arguments and headers, then closes the session automatically. MCP results can come back in a few forms, so it checks them in a friendly order: first already-parsed data, then structured content, then a text block that might contain JSON. If none of those produce a dictionary, it wraps the remaining value in a dictionary so callers still get a predictable result.

#### Function details

##### `mcp_call_tool`  (lines 18–42)

```
async def mcp_call_tool(endpoint: str, tool: str, arguments: dict[str, Any], headers: dict[str, str], timeout_seconds: float) -> dict[str, object]
```

**Purpose**: This function calls one MCP tool at a given HTTP endpoint and returns the result as a plain dictionary. It exists so the rest of the project does not need to know the details of opening an MCP session or decoding the different result formats MCP may return.

**Data flow**: It receives an endpoint URL, a tool name, the tool arguments, HTTP headers, and a timeout. It opens a streamable HTTP connection using those details, sends the tool call, waits for the answer, and then examines the result. If the result already contains dictionary-like data, it returns that. If the answer is structured content, it returns that. If the answer is text, it tries to read the text as JSON; if that works, it returns the parsed dictionary or wraps the parsed value under `result`, and if it is not JSON, it returns the raw text under `text`. If nothing else fits, it wraps the raw data under `result`.

**Call relations**: When some higher-level Composio flow needs to ask the Tool Router a search-style question, it calls this function rather than dealing with MCP networking itself. Inside, this function creates a FastMCP `Client` using a `StreamableHttpTransport`, uses that client to make the remote call, and uses `json.loads` only when the returned MCP content is a text block that may contain JSON.

*Call graph*: 3 external calls (Client, StreamableHttpTransport, loads).


### Pipedream provider integration
Pipedream modules broker catalog actions, manage connected-account client calls, and proxy provider HTTP requests without exposing tokens.

### `extensions/pipedream/ufo_ext_pipedream/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a folder can contain an `__init__.py` file to signal that the folder is meant to be imported as a package, rather than treated as just a plain directory. Think of it like a label on a binder: the label does not contain the documents, but it tells Python that the binder belongs on the shelf and can be opened by name.

For this extension, the file makes the `extensions/pipedream/ufo_ext_pipedream` directory importable as `ufo_ext_pipedream`. Other code can then refer to modules inside this package using normal Python import paths. Because the file is empty, it does not run setup code, expose shortcuts, or change package behavior when imported.

Without this file, depending on the Python version and packaging setup, imports for this extension could be less reliable or fail in environments that expect a traditional package marker.


### `extensions/pipedream/ufo_ext_pipedream/broker.py`

`orchestration` · `request handling for connector discovery, execution, credentials, and file results`

Pipedream offers many ready-made actions for apps such as Gmail or Slack. UFO needs a single, predictable way to ask, “What actions are available?”, “What inputs does this action need?”, and “Run this action using this member’s connected account.” This file provides that shared doorway through `PipedreamBroker`.

The broker is deliberately stateless. Instead of keeping a long-lived connection, each method asks for the current Pipedream client when it runs. That matters for tests and for avoiding stale network settings. The broker first translates UFO’s provider name into Pipedream’s app name, then talks to Pipedream’s action catalog or run API.

A key job here is hiding Pipedream details from the rest of UFO. Pipedream action definitions include internal fields, including the connected-account slot. The broker removes those from the schema shown to the agent, because the agent should provide only real user inputs; the broker binds the account itself. When execution fails because an old account grant is no longer valid, the broker turns that into a clearer “please reconnect” style error. It also turns action-created files into downloadable URLs. One important limitation is that Pipedream file inputs must be URLs, so this broker refuses staged uploads and tells callers to share a workspace file instead.

#### Function details

##### `PipedreamBroker.tools`  (lines 59–67)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Finds Pipedream actions for one provider and turns them into UFO broker tools. It also avoids a frustrating dead end: if a specific search phrase returns nothing, it falls back to the app’s general top actions.

**Data flow**: It receives a workspace id, provider name, and search text. It looks up the provider’s Pipedream app, asks the Pipedream client for matching actions, converts the response into simple tool records, and returns those records. If the query had words but no matches, it repeats the lookup without the query before returning.

**Call relations**: This is the main catalog lookup used directly by callers and indirectly by `PipedreamBroker.search`. It relies on `_spec` to translate the provider into a Pipedream app and `_listed_tools` to simplify Pipedream’s raw action list.

*Call graph*: calls 2 internal fn (_listed_tools, _spec); called by 1 (search); 1 external calls (pipedream_client).


##### `PipedreamBroker.schema`  (lines 69–75)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Builds the input description for one Pipedream action so an agent knows what values it may provide. It leaves out internal Pipedream fields and the account field, because the broker fills those in later.

**Data flow**: It receives a workspace id, provider name, and action slug. It fetches the action definition, extracts its configurable properties, turns those properties into a JSON-style input schema, and returns a `BrokerTool` containing the slug, description, and schema.

**Call relations**: When a caller needs details for a chosen action, this method asks `_definition` for the raw Pipedream definition, uses `_props` to read its configurable inputs, `_input_schema` to make a safe public schema, and `_str` to safely read text.

*Call graph*: calls 4 internal fn (_definition, _input_schema, _props, _str); 1 external calls (__init__).


##### `PipedreamBroker.execute`  (lines 77–112)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Runs a Pipedream action using a specific connected account. It verifies that the account belongs to the expected app, binds that account into the action input, sends the run to Pipedream, and turns common account problems into clearer reconnect guidance.

**Data flow**: It receives the workspace, provider, action slug, user-supplied arguments, connected account id, and an optional idempotency key. It fetches the action definition, copies the arguments, inserts the account binding into the right Pipedream app slot, checks the account belongs to this workspace and app, and calls Pipedream’s server-side run API. It returns the response dictionary if the action succeeds; otherwise it raises a Pipedream error, sometimes enhanced with reconnect instructions.

**Call relations**: This is the main execution path after an action has been selected. It uses `_definition` to understand the action, `_app_slot` to know where to place the account, `_spec` to confirm the provider app, `_key_miss` to make unknown-action errors more helpful, and `_stale_account` plus `_reconnect_error` to explain stale grants.

*Call graph*: calls 7 internal fn (_definition, _key_miss, _app_slot, _reconnect_error, _spec, _stale_account, __init__); 2 external calls (dumps, pipedream_client).


##### `PipedreamBroker.file_outputs`  (lines 114–132)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts files created by a Pipedream action and presents them as downloadable broker files. This lets the sandbox fetch files that the action wrote during its run.

**Data flow**: It receives a Pipedream run response. It looks inside the response’s exported file-stash upload list, skips malformed entries, takes each valid download URL, derives a filename from the local path when possible, and returns a tuple of `BrokerFile` objects.

**Call relations**: This is used after action execution, when the system needs to expose files produced by Pipedream. It does not call back into Pipedream; it only interprets the response format and packages file names and URLs for the rest of UFO.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `PipedreamBroker.stage_upload`  (lines 134–146)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Rejects staged uploads for Pipedream actions. Pipedream expects file inputs as URLs, so callers must share the workspace file and pass its download link instead.

**Data flow**: It receives details that would normally describe an upload to prepare, such as filename, MIME type, and checksum. It does not create anything; it immediately raises an error explaining the supported URL-based flow.

**Call relations**: This protects the broader connector interface from using the wrong upload style with Pipedream. Instead of handing work to a staging service, it tells the caller to use the workspace file sharing path.


##### `PipedreamBroker.search`  (lines 148–149)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Wraps the tool lookup result in a search response object. Pipedream has no separate planning or routing step here, so search is simply catalog lookup.

**Data flow**: It receives a workspace id, provider, and query. It asks `PipedreamBroker.tools` for matching tools and returns a `BrokerSearch` containing those tools.

**Call relations**: This method is the search-shaped entry point for callers that expect a `BrokerSearch`. It delegates the real lookup to `PipedreamBroker.tools` and only packages the answer.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `PipedreamBroker.credential`  (lines 151–172)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Creates a credential object that can send HTTP requests through Pipedream’s Connect proxy for a verified account. This allows code to make provider API calls using the member’s connected Pipedream account.

**Data flow**: It receives a workspace id, provider, and account id. It checks that the account exists in that workspace, confirms it belongs to the provider’s Pipedream app, builds a proxy transport around the Pipedream client’s transport, and returns a `Credential`. If the account is missing, it raises a reconnect-style error.

**Call relations**: This is used when the system needs a live API transport rather than running a prebuilt Pipedream action. It uses `_spec` to know the expected app and `_reconnect_error` when a missing account likely means the member must reconnect.

*Call graph*: calls 3 internal fn (_reconnect_error, _spec, __init__); 4 external calls (__init__, __init__, AsyncHTTPTransport, pipedream_client).


##### `PipedreamBroker._definition`  (lines 174–182)

```
async def _definition(self, slug: str) -> dict[str, object]
```

**Purpose**: Fetches the raw Pipedream definition for an action slug. It turns a Pipedream “not found” response into UFO’s `UnknownBrokerTool` signal.

**Data flow**: It receives an action slug. It asks the current Pipedream client for that action’s definition, unwraps the `data` object if Pipedream returned one, and returns a dictionary definition. If Pipedream says the action does not exist, it raises `UnknownBrokerTool` instead.

**Call relations**: `PipedreamBroker.schema` calls this before building an input schema, and `PipedreamBroker.execute` calls it before binding an account and running the action. It is the shared doorway from a slug to the action’s full metadata.

*Call graph*: called by 2 (execute, schema); 2 external calls (__init__, pipedream_client).


##### `PipedreamBroker._key_miss`  (lines 184–198)

```
async def _key_miss(self, client: pipedream.PipedreamClient, provider: str, slug: str) -> PipedreamError
```

**Purpose**: Creates a more useful error when execution names an unknown action. Instead of only saying “not found,” it tries to include the real available action keys for that app.

**Data flow**: It receives a Pipedream client, provider, and missing slug. It finds the provider’s Pipedream app, tries to list that app’s actions, gathers their slugs, and returns a `PipedreamError` explaining the missing slug and, when possible, naming available alternatives.

**Call relations**: `PipedreamBroker.execute` uses this after `_definition` reports an unknown tool. It calls `_spec` to identify the app and `_listed_tools` to turn the catalog response into readable tool names.

*Call graph*: calls 4 internal fn (_listed_tools, _spec, list_actions, __init__); called by 1 (execute).


##### `_stale_account`  (lines 201–208)

```
def _stale_account(error: PipedreamError, account_id: str) -> bool
```

**Purpose**: Checks whether a Pipedream error looks like it was caused by an old or missing connected account grant. It is intentionally narrow so unrelated provider errors are not mislabeled as reconnect problems.

**Data flow**: It receives a Pipedream error and an account id. It lowercases the error body and looks for Pipedream’s own “external user not found” wording, or for the account id together with “not found.” It returns `true` only when the message matches those stale-account patterns.

**Call relations**: `PipedreamBroker.execute` uses this after run failures and action-level errors. If this function says the grant is stale, execution passes the error through `_reconnect_error` so the agent gets next-step guidance.

*Call graph*: called by 1 (execute).


##### `_reconnect_error`  (lines 211–212)

```
def _reconnect_error(error: PipedreamError, provider: str) -> PipedreamError
```

**Purpose**: Adds human guidance to an error when the likely fix is reconnecting the provider account. It preserves the original status but expands the message.

**Data flow**: It receives a Pipedream error and provider name. It appends standard stale-grant guidance for that provider to the original error body and returns a new `PipedreamError` with the same status code.

**Call relations**: `PipedreamBroker.execute` uses this when a run appears to reference a stale account, and `PipedreamBroker.credential` uses it when the requested account is missing. It relies on the connector SDK’s reconnect-guidance helper for the wording.

*Call graph*: calls 1 internal fn (__init__); called by 2 (credential, execute); 1 external calls (stale_grant_guidance).


##### `_spec`  (lines 215–219)

```
def _spec(provider: str) -> ConnectorSpec
```

**Purpose**: Looks up UFO’s registered Pipedream connector specification for a provider name. This is how the broker knows which Pipedream app slug belongs to a provider.

**Data flow**: It receives a provider string. It checks the registered Pipedream connector map and returns the matching connector spec. If no provider is registered, it raises a key error so the mistake is visible immediately.

**Call relations**: Several broker paths call this before talking to Pipedream: tool lookup, execution, credential creation, and helpful unknown-key errors. It is the small translation step between UFO provider names and Pipedream app names.

*Call graph*: called by 4 (_key_miss, credential, execute, tools).


##### `_listed_tools`  (lines 222–234)

```
def _listed_tools(listed: dict[str, object]) -> tuple[BrokerTool, ...]
```

**Purpose**: Turns Pipedream’s raw action-list response into simple broker tool records. It keeps only usable action keys and short descriptions.

**Data flow**: It receives a dictionary response from Pipedream. It reads the `data` list, skips anything malformed or missing a non-empty key, converts each valid item into a `BrokerTool`, and returns all tools as a tuple.

**Call relations**: `PipedreamBroker.tools` uses this for normal catalog search results, and `PipedreamBroker._key_miss` uses it to name available alternatives in an error. It uses `_str` so missing or non-text descriptions become harmless empty strings.

*Call graph*: calls 1 internal fn (_str); called by 2 (_key_miss, tools); 1 external calls (__init__).


##### `_props`  (lines 237–239)

```
def _props(definition: dict[str, object]) -> list[dict[str, object]]
```

**Purpose**: Extracts the configurable property definitions from a Pipedream action definition. These properties are the raw ingredients for both public input schemas and account binding.

**Data flow**: It receives an action definition dictionary. It reads `configurable_props`, keeps only entries that are dictionaries, and returns them as a list. If the field is absent or not a list, it returns an empty list.

**Call relations**: `PipedreamBroker.schema` uses this before building the public schema, and `_app_slot` uses it to find the special connected-account slot. It keeps later code from having to repeatedly check for malformed property data.

*Call graph*: called by 2 (schema, _app_slot).


##### `_app_slot`  (lines 242–249)

```
def _app_slot(definition: dict[str, object], slug: str) -> str
```

**Purpose**: Finds the special Pipedream input field where the connected account must be placed. Without this slot, the broker cannot safely run the action for a member’s account.

**Data flow**: It receives an action definition and slug. It scans the action’s configurable properties for the property whose type is Pipedream’s app/account type, then returns that property’s name. If none exists, it raises a Pipedream error explaining that the action cannot bind the account.

**Call relations**: `PipedreamBroker.execute` calls this just before running an action, so it knows exactly where to insert the `authProvisionId`. It uses `_props` to read the action’s property list safely.

*Call graph*: calls 2 internal fn (_props, __init__); called by 1 (execute).


##### `_input_schema`  (lines 252–275)

```
def _input_schema(props: list[dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the public input schema for an action from Pipedream’s configurable properties. It removes internal fields so the agent sees only values it should actually supply.

**Data flow**: It receives a list of property dictionaries. For each valid property, it skips the app/account slot and Pipedream service-only fields, maps Pipedream’s type names to JSON schema types, copies a description or label when available, and records required fields. It returns a dictionary shaped like a JSON schema object.

**Call relations**: `PipedreamBroker.schema` calls this after fetching an action definition. It uses `_str` to safely normalize property types and acts as the filter between Pipedream’s full component metadata and UFO’s agent-facing schema.

*Call graph*: calls 1 internal fn (_str); called by 1 (schema).


##### `_str`  (lines 278–279)

```
def _str(value: object) -> str
```

**Purpose**: Safely turns a value into a string only when it already is one. It prevents unexpected non-string data from leaking into descriptions or type names.

**Data flow**: It receives any value. If the value is a string, it returns it unchanged; otherwise it returns an empty string.

**Call relations**: `PipedreamBroker.schema`, `_listed_tools`, and `_input_schema` use this as a small guardrail when reading Pipedream data. It keeps those callers simple and avoids treating numbers, objects, or missing values as text.

*Call graph*: called by 3 (schema, _input_schema, _listed_tools).


### `extensions/pipedream/ufo_ext_pipedream/client.py`

`io_transport` · `connector consent, account verification, and connector action execution`

This file is the bridge between this project and Pipedream Connect. Pipedream acts like a secure middle office: the user signs in to Gmail or another provider through Pipedream, Pipedream keeps the real access token, and this app only stores a connected-account id. That matters because a leaked account id is much less dangerous than a leaked provider token.

The file defines which Pipedream-backed connectors this extension supports. Right now the allowlist contains Gmail, including a special option for using this deployment’s own Google OAuth app when Pipedream’s shared one is not enough.

The main class, PipedreamClient, talks to Pipedream’s REST API using httpx, an asynchronous HTTP library. Before normal API calls, it gets a short-lived Pipedream access token using the deployment’s client id and secret, then caches that token until it is close to expiring. It can mint a Connect Link for browser consent, look up the account created by that consent, verify that the account belongs to the expected user or workspace, search and describe available actions, and run an action on Pipedream’s servers.

A key safety theme is ownership checking. The project-level Pipedream token can read accounts across the project, so this client refuses to use an account unless its recorded external user matches the expected workspace or connection. Without those checks, one workspace could accidentally or maliciously run actions using another workspace’s connected account.

#### Function details

##### `PipedreamError.__init__`  (lines 79–82)

```
def __init__(self, status: int, body: str) -> None
```

**Purpose**: Creates a clear error for failed or unusable Pipedream API results. It keeps both the status code and the response text so callers can report what went wrong instead of silently treating a bad response as an empty result.

**Data flow**: It receives a numeric status and a text body. It turns them into a readable exception message, then stores the original status and body on the error object for later inspection.

**Call relations**: Client methods use this when Pipedream rejects a request, returns missing fields, or reports an account problem. The broker layer also raises it when connector setup or execution cannot safely continue.

*Call graph*: called by 12 (_key_miss, credential, execute, _app_slot, _reconnect_error, access_token, connect_token, newest_account, workspace_account, _account (+2 more)).


##### `PipedreamClient.access_token`  (lines 120–141)

```
async def access_token(self) -> str
```

**Purpose**: Gets the Pipedream access token that authenticates this deployment to Pipedream’s API. It reuses a cached token when it is still fresh, which avoids asking Pipedream for a new token on every call.

**Data flow**: It reads the client id from the client object and checks the process-wide token cache. If a usable token is present, it returns it. Otherwise it posts the client id and secret to Pipedream’s OAuth token endpoint, validates that an access token came back, records its expiry time, stores it in the cache, and returns the token.

**Call relations**: The private request helpers call this before authenticated GET and POST requests. It uses the low-level HTTP client builder and response parser, and raises PipedreamError if the token grant response is not usable.

*Call graph*: calls 3 internal fn (_http, __init__, _body); called by 2 (_get, _post); 1 external calls (monotonic).


##### `PipedreamClient.connect_token`  (lines 143–158)

```
async def connect_token(self, external_user_id: str, success_redirect_uri: str, error_redirect_uri: str) -> ConnectToken
```

**Purpose**: Creates a short-lived Pipedream Connect token and hosted consent link for a specific external user. This is what lets the user open a browser page and connect their provider account.

**Data flow**: It receives the external user id plus success and error return URLs. It sends those to Pipedream, expects back both a token and a connect-link URL, and returns them as a ConnectToken object. If either value is missing, it raises an error.

**Call relations**: This is used during the start of an OAuth-style connection flow. It delegates the actual HTTP POST to _post, which adds authentication and parses the response.

*Call graph*: calls 2 internal fn (_post, __init__); 1 external calls (__init__).


##### `PipedreamClient.connected_account`  (lines 160–167)

```
async def connected_account(self, account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Reads one connected account and confirms it belongs to the expected external user. This prevents the app from binding or using someone else’s Pipedream account by mistake.

**Data flow**: It receives an account id and expected external user id. It fetches the account from Pipedream, unwraps the response shape if needed, then checks that the account is healthy and owned by that exact external user. It returns a ConnectedAccount summary if the check passes.

**Call relations**: This is part of the safety gate after an account id is known. It uses _get to fetch from Pipedream, _dict to tolerate response wrapping, and _owned_account to enforce ownership.

*Call graph*: calls 3 internal fn (_get, _dict, _owned_account).


##### `PipedreamClient.workspace_account`  (lines 169–179)

```
async def workspace_account(self, account_id: str, workspace_id: UUID) -> ConnectedAccount
```

**Purpose**: Reads a connected account and confirms it belongs to the given workspace. This is the workspace-level version of the ownership guard.

**Data flow**: It receives an account id and workspace UUID. It fetches the account, converts the response to a normal account summary, then checks whether the account’s external user id fits the workspace’s allowed naming pattern. If it does, the ConnectedAccount is returned; otherwise a forbidden error is raised.

**Call relations**: This is used before workspace-scoped execution is allowed. It combines _get for the remote read, _account for basic account validation, and _workspace_owns_external_user for the workspace ownership rule.

*Call graph*: calls 5 internal fn (_get, __init__, _account, _dict, _workspace_owns_external_user).


##### `PipedreamClient.newest_account`  (lines 181–195)

```
async def newest_account(self, external_user_id: str, app: str) -> ConnectedAccount
```

**Purpose**: Finds the most recently created connected account for one external user and one Pipedream app. This is useful right after the user finishes the hosted consent page, when the system needs to identify the account that was just connected.

**Data flow**: It receives an external user id and app slug. It asks Pipedream for matching accounts, keeps only dictionary-shaped records, chooses the record with the latest creation timestamp, validates that it has an id, and then confirms the account belongs to the expected user before returning it.

**Call relations**: This follows the Connect Link return path. It uses _get to list accounts, _dict to normalize records, and _owned_account to apply the same ownership guard used for direct account lookup.

*Call graph*: calls 4 internal fn (_get, __init__, _dict, _owned_account).


##### `PipedreamClient.list_actions`  (lines 197–203)

```
async def list_actions(self, app: str, query: str='', limit: int=ACTION_SEARCH_LIMIT) -> dict[str, object]
```

**Purpose**: Searches Pipedream’s catalog of runnable actions for a given app, such as Gmail actions. It gives the dynamic tool layer a way to discover what a connected provider can do.

**Data flow**: It receives an app slug, an optional search phrase, and a limit. It builds query parameters, includes the search phrase only when present, sends a GET request to Pipedream, and returns the response dictionary.

**Call relations**: The broker calls this when it needs to resolve or suggest an action key. The method itself relies on _get for authentication, HTTP transport, and response parsing.

*Call graph*: calls 1 internal fn (_get); called by 1 (_key_miss).


##### `PipedreamClient.action_definition`  (lines 205–206)

```
async def action_definition(self, key: str) -> dict[str, object]
```

**Purpose**: Fetches the detailed definition for one Pipedream action component. This tells the caller what inputs the action expects and how it is described.

**Data flow**: It receives an action key. It asks Pipedream for that component’s definition and returns the response dictionary.

**Call relations**: This is a catalog lookup helper. It hands the network work to _get, which obtains the access token and parses the HTTP response.

*Call graph*: calls 1 internal fn (_get).


##### `PipedreamClient.run_action`  (lines 208–226)

```
async def run_action(self, key: str, external_user_id: str, configured_props: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one Pipedream action on Pipedream’s servers for a specific external user. It also asks Pipedream to use a fresh file stash so files produced by the action can be returned as reachable URLs rather than unreadable temporary paths.

**Data flow**: It receives an action key, an external user id, and configured action inputs. It builds the run request, adds a new stash id, checks that the JSON request is not larger than the allowed size, posts it to Pipedream, and returns the action result. If the request is too large, it raises ValueError before sending anything.

**Call relations**: This is the execution step after an action has been selected and configured. It uses _post for the authenticated API call, while the broker layer is responsible for choosing when to run it.

*Call graph*: calls 1 internal fn (_post); 1 external calls (dumps).


##### `PipedreamClient._get`  (lines 228–231)

```
async def _get(self, path: str, params: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Performs an authenticated GET request to Pipedream and returns a parsed response object. It centralizes the repeated steps needed for read-style API calls.

**Data flow**: It receives an API path and optional query parameters. It gets an access token, opens an HTTP client with that token, sends the GET request, parses the response body, and returns a dictionary.

**Call relations**: Higher-level methods such as account lookup, account listing, action search, and action definition all pass through this helper. It calls access_token, _http, and _body so those details stay out of the public methods.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 5 (action_definition, connected_account, list_actions, newest_account, workspace_account).


##### `PipedreamClient._post`  (lines 233–236)

```
async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]
```

**Purpose**: Performs an authenticated POST request to Pipedream and returns a parsed response object. It centralizes the repeated steps needed for create, token, and run-style API calls.

**Data flow**: It receives an API path and a JSON-ready body dictionary. It gets an access token, opens an HTTP client with that token, posts the JSON body, parses the response, and returns a dictionary.

**Call relations**: connect_token and run_action use this to send data to Pipedream. Like _get, it keeps authentication, client creation, and response parsing in one place.

*Call graph*: calls 3 internal fn (_http, access_token, _body); called by 2 (connect_token, run_action).


##### `PipedreamClient._http`  (lines 238–251)

```
def _http(self, token: str | None=None) -> httpx.AsyncClient
```

**Purpose**: Builds a short-lived asynchronous HTTP client for Pipedream API calls. When given a token, it adds the authorization header and the Pipedream environment header.

**Data flow**: It receives an optional access token. If a token is present, it prepares headers with the bearer token and environment; if not, it uses no special headers. It returns an httpx AsyncClient configured with the Pipedream base URL, timeout, optional test transport, and headers.

**Call relations**: access_token uses this without a token for the OAuth token request. _get and _post use it with a token for normal authenticated Connect API calls.

*Call graph*: called by 3 (_get, _post, access_token); 1 external calls (AsyncClient).


##### `_dict`  (lines 254–255)

```
def _dict(value: object) -> dict[str, object]
```

**Purpose**: Safely treats a value as a dictionary only when it really is one. It prevents later code from crashing or trusting an unexpected response shape.

**Data flow**: It receives any value. If the value is a dictionary, it returns it unchanged; otherwise it returns an empty dictionary.

**Call relations**: Account-reading methods and _account use this when Pipedream responses may contain nested objects. It is a small guardrail before ownership and health checks run.

*Call graph*: called by 4 (connected_account, newest_account, workspace_account, _account).


##### `_owned_account`  (lines 258–271)

```
def _owned_account(record: dict[str, object], account_id: str, external_user_id: str) -> ConnectedAccount
```

**Purpose**: Confirms that a connected account belongs to the exact external user expected by the caller. This is an important security check because the project token can see more than one user’s accounts.

**Data flow**: It receives a Pipedream account record, the account id being checked, and the expected external user id. It first converts the record into a ConnectedAccount using _account, then compares the recorded owner with the expected owner. It returns the account if they match, or raises a forbidden error if they do not.

**Call relations**: connected_account and newest_account call this after fetching account records. It builds on _account’s basic validation and adds the stricter same-user ownership rule.

*Call graph*: calls 2 internal fn (__init__, _account); called by 2 (connected_account, newest_account).


##### `_account`  (lines 274–286)

```
def _account(record: dict[str, object], account_id: str) -> ConnectedAccount
```

**Purpose**: Turns a raw Pipedream account record into the project’s small ConnectedAccount summary, while rejecting records that are missing an owner or marked unhealthy.

**Data flow**: It receives a dictionary from Pipedream and the account id that was requested. It reads the external owner, checks that the account is not unhealthy, extracts the app slug when present, and returns a ConnectedAccount. If the owner is missing or the account is unhealthy, it raises PipedreamError.

**Call relations**: workspace_account uses this directly before checking workspace ownership. _owned_account also uses it before checking exact external-user ownership.

*Call graph*: calls 2 internal fn (__init__, _dict); called by 2 (workspace_account, _owned_account); 1 external calls (__init__).


##### `workspace_user_prefix`  (lines 289–290)

```
def workspace_user_prefix(workspace_id: UUID) -> str
```

**Purpose**: Builds the standard prefix used for Pipedream external user ids that belong to a workspace. The prefix is the common beginning that later checks use to recognize workspace-owned connection users.

**Data flow**: It receives a workspace UUID. It converts the UUID to its compact hexadecimal form and returns a string beginning with the project’s external-user prefix followed by that workspace marker.

**Call relations**: _workspace_owns_external_user uses this to test ownership, and connection_user_id uses it when creating a new external user id for a connection flow.

*Call graph*: called by 2 (_workspace_owns_external_user, connection_user_id).


##### `_workspace_owns_external_user`  (lines 293–302)

```
def _workspace_owns_external_user(workspace_id: UUID, external_user_id: str) -> bool
```

**Purpose**: Checks whether a Pipedream external user id belongs to a given workspace. It accepts both an older direct workspace id form and the newer workspace-plus-connection-id form.

**Data flow**: It receives a workspace UUID and an external user id string. It first checks the legacy exact form, then checks for the standard workspace prefix. If the prefix matches, it verifies that the remaining connection id is exactly 32 lowercase hexadecimal characters. It returns true or false.

**Call relations**: workspace_account calls this after reading an account from Pipedream. This function decides whether that account is allowed to be used inside the requested workspace.

*Call graph*: calls 1 internal fn (workspace_user_prefix); called by 1 (workspace_account).


##### `connection_user_id`  (lines 305–307)

```
def connection_user_id(workspace_id: UUID, state: str) -> str
```

**Purpose**: Creates a stable Pipedream external user id for a workspace connection attempt. It uses the connection state value to derive a short id without storing the original state in the user id.

**Data flow**: It receives a workspace UUID and a state string. It hashes the state with SHA-256, takes the first 32 hexadecimal characters, appends them to the workspace user prefix, and returns the complete external user id.

**Call relations**: This is used when starting or correlating a connection flow. It shares the same prefix format that _workspace_owns_external_user later recognizes during account verification.

*Call graph*: calls 1 internal fn (workspace_user_prefix); 1 external calls (sha256).


##### `_body`  (lines 310–318)

```
def _body(response: httpx.Response) -> dict[str, object]
```

**Purpose**: Turns an HTTP response from Pipedream into a dictionary, or raises a clear error if the response failed or has the wrong shape. This keeps all callers from having to repeat the same response checks.

**Data flow**: It receives an httpx response. If the status code is an error, it raises PipedreamError with the status and text. If the body is empty, it returns an empty dictionary. Otherwise it parses JSON and returns it only if it is a dictionary; non-dictionary JSON is rejected.

**Call relations**: access_token, _get, and _post all call this immediately after receiving an HTTP response. It is the common gate between raw network data and the rest of the client’s logic.

*Call graph*: calls 1 internal fn (__init__); called by 3 (_get, _post, access_token); 1 external calls (json).


##### `pipedream_client`  (lines 321–339)

```
def pipedream_client() -> PipedreamClient
```

**Purpose**: Builds the default PipedreamClient from environment variables. It makes startup or connector use fail loudly if the deployment is missing the credentials needed to talk to Pipedream.

**Data flow**: It reads the Pipedream client id, client secret, project id, and optional environment from process environment variables. If any required value is missing, it raises RuntimeError. Otherwise it returns a configured PipedreamClient.

**Call relations**: Other parts of the extension call this when they need a real Pipedream client for consent or execution. The function is the boundary between deployment configuration and the reusable client class.

*Call graph*: 1 external calls (__init__).


### `extensions/pipedream/ufo_ext_pipedream/proxy.py`

`io_transport` · `request handling and teardown`

Pipedream keeps provider credentials on its own servers, so this project cannot simply attach a Gmail, Slack, or other provider token to outgoing requests. This file solves that by acting like a forwarding booth. A connector can make an ordinary HTTP request to the provider it wants, but this transport repackages that request and sends it to Pipedream's proxy endpoint instead. Pipedream then adds the real credential on the server side and forwards the call to the provider.

The main class, `PipedreamProxyTransport`, plugs into `httpx`, an HTTP client library. When a request comes in, it first asks the Pipedream client for a Pipedream access token. It reads the request body, copies safe headers, and rewrites those headers with Pipedream's required `x-pd-proxy-` prefix. It deliberately drops transport-level headers such as `host`, `content-length`, and `authorization`, because forwarding those could be wrong or unsafe.

The original provider URL is encoded into the Pipedream proxy URL, along with the external user and account id. The rewritten request is then handed to an inner transport that actually sends it over the network. The important behavior is that the provider's status code, response body, and headers come back unchanged, so normal connector logic like pagination or error handling still works.

#### Function details

##### `PipedreamProxyTransport.handle_async_request`  (lines 54–73)

```
async def handle_async_request(self, request: httpx.Request) -> httpx.Response
```

**Purpose**: This is the core forwarding step. It takes an ordinary HTTP request meant for a provider, wraps it as a Pipedream Connect Proxy request, and sends that instead so Pipedream can inject the real provider credential.

**Data flow**: It starts with an incoming `httpx.Request`, which includes the provider URL, method, headers, and body. It asks the Pipedream client for an access token, reads the body, keeps only headers that are safe to forward, adds Pipedream's required header prefix, and base64-url-encodes the original provider URL so it can fit inside the proxy path. It then builds a new request to Pipedream's proxy endpoint with the external user id and account id in the query string, sends it through the inner transport, and returns the resulting response unchanged.

**Call relations**: This method is called by `httpx` whenever the client using this transport sends a request. Inside that flow, it relies on `request.aread` to collect the original body, `base64.urlsafe_b64encode` to safely place the provider URL inside the proxy path, and `httpx.URL` and `httpx.Request` to build the new Pipedream-bound request. It then hands the finished request to the inner transport, which performs the actual network work.

*Call graph*: 4 external calls (urlsafe_b64encode, Request, aread, URL).


##### `PipedreamProxyTransport.aclose`  (lines 75–76)

```
async def aclose(self) -> None
```

**Purpose**: This closes the underlying HTTP transport when the proxy transport is no longer needed. It is the cleanup step that helps release network resources cleanly.

**Data flow**: It takes no new request data. It simply passes the close signal down to the inner transport, and the result is that any resources owned by that inner transport, such as open connections, can be shut down.

**Call relations**: This is called during cleanup by code that is finished using the `httpx` client or transport. Rather than doing its own separate cleanup, it delegates directly to the wrapped inner transport because that is the object that owns the actual connection machinery.


### Sources and web search
Source and search extensions model external content providers, synced pages, and Exa-backed web search/page retrieval.

### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hook handling`

A source is the project’s way to say, “sync these kinds of content from this provider account.” This file turns lower-level source rows into one understandable object per provider binding, where a binding means the provider, the account or workspace credential, and sometimes a tenant URL. Without this file, users could not register sources through the normal object commands, inspect them, share them, delete them, or subscribe to change alerts.

The file does three main jobs. First, it reconstructs source objects from stored sync rows. Each stream is stored separately, but users see one source object containing all selected streams. Second, it validates new source registrations. It checks that the provider exists, the requested streams exist, the account or credential is usable, and any tenant URL is in a safe expected shape. Source names are not chosen freely; they are derived from the binding, like a label printed from the account details. If the user applies the right content under the wrong name, the code refuses and tells them the exact name to use.

Third, it controls changes. Most source changes require the original registering member or workspace owner. But subscribing is different: any member who can see the source may add or remove only their own conversation id. When synced pages change, the page-change hook finds subscribers and starts an alert turn for each one, mentioning only shared pages the subscriber is allowed to know about.

#### Function details

##### `_Binding.name`  (lines 149–150)

```
def name(self) -> str
```

**Purpose**: This property gives a source binding its official object name. The name is derived from the provider, account, and tenant URL, so two people cannot accidentally give the same binding different names.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared name-making helper, and returns the resulting string. It does not change anything.

**Call relations**: Other parts of this file use this property whenever they need to compare, list, group, or alert about a source object. It relies on the registry’s binding-name rule so naming stays consistent across the extension.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 153–154)

```
def created_at(self) -> datetime
```

**Purpose**: This property reports when the overall binding first existed. Because a binding can contain several stream rows, it uses the earliest stream creation time.

**Data flow**: It reads all stream creation timestamps inside the binding and returns the smallest one. No stored data is changed.

**Call relations**: It is used when building detailed object information, so object readers see one sensible creation time for the whole source rather than separate times for each stream.


##### `_Binding.updated_at`  (lines 157–158)

```
def updated_at(self) -> datetime
```

**Purpose**: This property reports the most recent update time for the binding. It treats the binding as updated when any of its streams was last updated.

**Data flow**: It reads all stream update timestamps and returns the latest one. It does not write anything.

**Call relations**: It supports the object detail view, where the source object needs a single updated-at timestamp even though it is built from multiple stream rows.


##### `_Binding.spec`  (lines 160–168)

```
def spec(self, subscribers: tuple[str, ...]=()) -> SourceSpec
```

**Purpose**: This converts an internal binding into the public source specification shown to users and tools. It is the bridge from stored sync rows back to the object manifest people can read or re-apply.

**Data flow**: It reads the binding’s provider, streams, account, URL, sharing subject, and optional subscriber list. It then creates and returns a SourceSpec with direct workspace credentials shown as an empty account id.

**Call relations**: Detail views and update checks use this to compare what exists with what the caller is applying. It calls the SourceSpec constructor to produce the public shape.

*Call graph*: 1 external calls (__init__).


##### `_Binding.summary`  (lines 170–172)

```
def summary(self) -> str
```

**Purpose**: This makes a short human-readable label for a source binding. It helps lists and alerts explain what changed without dumping the full technical spec.

**Data flow**: It joins the stream names, combines them with the provider and account, trims the text to the configured maximum length, and returns that string.

**Call relations**: Source listing uses this summary for object rows, and alert messages call it to describe the subscribed source in plain text.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 175–178)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This checks that the current tool call has the extension context it needs. The extension context is the object that can read and write extension-owned data such as source rows and subscriber storage.

**Data flow**: It receives a tool context, reads its ext field, and returns it if present. If it is missing, it raises an error because source operations cannot safely continue.

**Call relations**: Nearly every source operation calls this before touching extension data. It acts like a guard at the door, catching programming mistakes where source objects were dispatched without the required extension services.

*Call graph*: called by 7 (_apply_owned, _bindings, _delete_owned, _detail, _resolved_account, _status, apply).


##### `_require_connectors`  (lines 181–184)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: This checks that the current turn has a connector registry available. The connector registry is the catalog that knows how provider accounts and credentials are reached.

**Data flow**: It receives a tool context, reads its connectors field, and returns it if present. If absent, it raises an error instead of guessing.

**Call relations**: Account resolution calls this when deciding whether a provider should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 187–216)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: This rebuilds user-facing source bindings from the lower-level source rows stored by the extension. It gathers separate stream rows into one binding per provider, account, and URL.

**Data flow**: It asks the extension context for all registered sources, ignores records for providers this extension does not know, validates each record’s connector config, and groups rows by binding identity. It returns a tuple of _Binding objects whose streams are sorted by name.

**Call relations**: The object store uses this through SourceObjects._bindings to list and find sources. The page-change hook also uses it to connect changed page source ids back to the binding that owns them.

*Call graph*: calls 1 internal fn (sources); called by 2 (_bindings, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_subscribers_map`  (lines 219–231)

```
async def _subscribers_map(ext: ExtensionContext, name: str) -> dict[str, str]
```

**Purpose**: This reads the stored subscriptions for one source. A subscription maps a conversation id to the agent id that should be invoked when the source changes.

**Data flow**: It reads a key from the extension store using the source name. If the stored value is a valid string-to-string map, it returns a copy; if there is no value, it returns an empty map; if the stored shape is wrong, it raises an error.

**Call relations**: Detail, status, subscription editing, and change alerts all call this. It is the single reader for the subscriber list, so all those flows interpret the stored data the same way.

*Call graph*: called by 4 (_detail, _edit_subscribers, _status, on_page_change).


##### `_store_subscribers`  (lines 234–240)

```
async def _store_subscribers(ext: ExtensionContext, name: str, mapping: dict[str, str]) -> None
```

**Purpose**: This saves the subscription map for a source, or removes the storage entry when no subscribers remain. That keeps the extension store clean and avoids stale empty records.

**Data flow**: It receives an extension context, a source name, and a conversation-to-agent map. If the map has entries, it writes them under the source’s subscriber key; if it is empty, it deletes that key.

**Call relations**: Subscription editing calls this after changing the caller’s entry. Deletion also calls it to clear all subscriptions when a source is removed.

*Call graph*: called by 2 (_delete_owned, _edit_subscribers).


##### `_binding_identity`  (lines 243–244)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool]
```

**Purpose**: This extracts the parts of a SourceSpec that define what the source actually is. It intentionally ignores subscribers, because subscriptions are allowed to change without recreating the source.

**Data flow**: It reads provider, streams, account id, base URL, and sharing flag from the spec, sorts the streams for stable comparison, and returns them as a tuple.

**Call relations**: SourceObjects.apply uses this to detect a subscribers-only edit. If the identity is unchanged, the apply can follow the looser subscription path instead of the stricter owner-gated source mutation path.

*Call graph*: called by 1 (apply).


##### `_self_only_change`  (lines 247–254)

```
def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None
```

**Purpose**: This enforces the rule that a conversation may only subscribe or unsubscribe itself. It stops one conversation from silently changing another conversation’s alerts.

**Data flow**: It compares the old and new subscriber id sets, removes the caller’s own id from the differences, and checks whether anything else changed. If another id changed, it raises a clear error; otherwise it returns normally.

**Call relations**: SourceObjects.apply calls this before editing subscribers. It is the safety check that lets subscription edits be visibility-gated rather than owner-gated.

*Call graph*: called by 1 (apply).


##### `SourceObjects.apply`  (lines 276–291)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: This is the main entry for applying a source object spec. It separates a simple subscription toggle from a real source registration or mutation.

**Data flow**: It receives the current tool context, object name, desired spec, and any visible old spec. If the source identity is unchanged, it verifies only the caller’s subscriber id changed and saves that subscription change. Otherwise it hands the operation to the base object logic, which applies the normal ownership rules.

**Call relations**: This method is called by the object system when someone uses the apply verb for a source. It calls _binding_identity and _self_only_change for the subscription path, then _edit_subscribers to save it; all other changes continue through the inherited MemberOwnedObjects flow.

*Call graph*: calls 4 internal fn (_edit_subscribers, _binding_identity, _require_ext, _self_only_change).


##### `SourceObjects._edit_subscribers`  (lines 293–304)

```
async def _edit_subscribers(self, ext: ExtensionContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID) -> None
```

**Purpose**: This updates the stored subscriber list after the self-only rule has already approved the change. It records which agent should be used when re-entering that conversation for alerts.

**Data flow**: It loads the current subscriber map, checks whether the caller’s conversation id is in the desired subscriber list, and either stores the caller’s agent id or removes the caller’s entry. It then writes the updated map back, or deletes it if empty.

**Call relations**: SourceObjects.apply calls this for subscription-only applies. It uses _subscribers_map and _store_subscribers so it preserves every other subscriber unchanged.

*Call graph*: calls 2 internal fn (_store_subscribers, _subscribers_map); called by 1 (apply).


##### `SourceObjects._owned_rows`  (lines 306–317)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow, ...]
```

**Purpose**: This builds the compact rows used when listing source objects. Each row gives the source name, a short summary, and ownership information.

**Data flow**: It loads all bindings visible through the extension data, then turns each binding into an OwnedRow with an ObjectOwner that says whether it is shared and who registered it. It returns all rows as a tuple.

**Call relations**: The base object machinery calls this when it needs to list source objects and apply member visibility rules. It depends on _bindings, which reconstructs bindings from stored source rows.

*Call graph*: calls 1 internal fn (_bindings); 2 external calls (__init__, __init__).


##### `SourceObjects._detail`  (lines 319–328)

```
async def _detail(self, ctx: ToolContext, name: str) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: This builds the full detail view for one source object. It includes the source spec, timestamps, and the current subscriber ids.

**Data flow**: It looks up the binding by name. If none exists, it returns nothing. Otherwise it reads subscribers from the extension store, converts the binding into a SourceSpec with those subscribers included, and returns an ObjectDetail with created and updated times.

**Call relations**: The object system calls this for object_get or similar detail requests. It calls _find to locate the binding and _subscribers_map so the returned manifest shows the alert subscriptions.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 330–351)

```
async def _status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This builds live status information for a source, such as whether the caller is subscribed and when each stream will sync next. Status is separate from the spec because it describes current state, not desired configuration.

**Data flow**: It finds the binding by name, reads the caller’s conversation id, loads the subscriber map, and creates a dictionary with sharing state, the caller’s subscriber id, whether the caller is subscribed, and per-stream sync status. For private sources with a known owner, it also includes the owner member id.

**Call relations**: The object system calls this when it needs status for a source object. It uses _find and _subscribers_map to combine stored binding data with caller-specific subscription information.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map).


##### `SourceObjects._apply_owned`  (lines 353–421)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: This performs the real work of registering, sharing, or refusing changes to a source after ownership checks have passed. It protects the system from invalid providers, unsafe URLs, missing credentials, and unsupported stream names.

**Data flow**: It receives the desired source spec and first verifies there is a speaking member. It rejects subscribers on first registration, checks the provider catalog, checks requested streams, validates the tenant URL, resolves the account or credential, and verifies the object name matches the derived binding name. If the binding already exists, it allows only a permitted move from private to shared; other identity changes are refused. If it is new, it registers one stored source row per stream with the right private or shared subject.

**Call relations**: The inherited apply flow calls this for owner-gated source changes. It calls _resolved_account, _validated_base_url, _find, and extension methods such as registering sources or changing their subject.

*Call graph*: calls 4 internal fn (_find, _resolved_account, _require_ext, _validated_base_url); 6 external calls (__init__, __init__, __init__, member_subject, get, binding_name).


##### `SourceObjects._delete_owned`  (lines 423–430)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: This removes an existing source binding and clears its subscriber list. Deleting the binding removes all of its stream rows.

**Data flow**: It gets the extension context, finds the binding by name, and raises an unknown-object error if it is missing. For each stream in the binding, it asks the extension to remove that source row, then stores an empty subscriber map to erase subscriptions.

**Call relations**: The base object system calls this after delete permission has been checked. It uses _find to locate the binding and _store_subscribers to clean up alert state.

*Call graph*: calls 3 internal fn (_find, _require_ext, _store_subscribers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 432–487)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> str
```

**Purpose**: This decides which credential path a source should use: a connected provider account or the workspace’s direct credential. It turns a user’s spec into the exact account handle stored with the source.

**Data flow**: It reads the extension context, connector registry, declared credentials, connected accounts, and the spec’s account_id. If the provider is brokered or has connected accounts, it requires a valid connected account and may ask the caller to choose among several. If direct credentials are allowed instead, it verifies the workspace credential exists and returns the special direct-account marker. If neither path works, it raises an explanatory error.

**Call relations**: SourceObjects._apply_owned calls this before registration so every stored source has a valid authentication choice. It calls _require_ext, _require_connectors, and the tool context’s connector account lookup.

*Call graph*: calls 3 internal fn (connector_accounts, _require_connectors, _require_ext); called by 1 (_apply_owned).


##### `SourceObjects._find`  (lines 489–492)

```
async def _find(self, ctx: ToolContext, name: str) -> _Binding | None
```

**Purpose**: This looks up one reconstructed source binding by its object name. It is the local search helper used whenever a single source is needed.

**Data flow**: It loads the current binding list and returns the first binding whose derived name matches the requested name. If no binding matches, it returns nothing.

**Call relations**: Apply, delete, detail, and status paths all call this before acting on one source. It delegates the reconstruction work to _bindings.

*Call graph*: calls 1 internal fn (_bindings); called by 4 (_apply_owned, _delete_owned, _detail, _status).


##### `SourceObjects._bindings`  (lines 494–495)

```
async def _bindings(self, ctx: ToolContext) -> tuple[_Binding, ...]
```

**Purpose**: This returns all current source bindings for the extension context. It is a small wrapper that makes sure the required extension context exists first.

**Data flow**: It checks the tool context for an extension context, then asks _bindings_from_ext to reconstruct bindings from stored source rows. It returns those bindings unchanged.

**Call relations**: Listing and finding sources call this. It centralizes the context check so callers do not each need to repeat the same extension-context requirement.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 2 (_find, _owned_rows).


##### `on_page_change`  (lines 498–537)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook sends alerts when pages synced from subscribed sources change. It makes sure subscribers hear only about shared pages, so private content is not leaked.

**Data flow**: It receives a hook context and expects a page-change batch. It rebuilds bindings, maps changed source ids to their binding, groups changes by binding, reads subscribers for each binding, filters to shared changes only, builds an alert message, and invokes the stored conversation and agent once per subscriber with an idempotency key to avoid duplicate alerts on replay. It returns no special outcome.

**Call relations**: The manifest hook system calls this when synced pages change. It calls _bindings_from_ext to understand which source each changed page came from, _subscribers_map to find who asked for alerts, and _alert_message to write the message sent into each subscribed conversation.

*Call graph*: calls 3 internal fn (_alert_message, _bindings_from_ext, _subscribers_map); 1 external calls (UUID).


##### `_alert_message`  (lines 540–551)

```
def _alert_message(binding: _Binding, changes: list[PageChange]) -> str
```

**Purpose**: This writes the text of a source-change alert. The message tells the agent which source changed, how many synced pages changed, and which page objects to inspect.

**Data flow**: It receives a binding and a list of page changes, turns up to a fixed number of changes into readable page references, counts extra and removed pages, and returns a single instruction-style message string.

**Call relations**: on_page_change calls this before invoking subscribed conversations. It uses _Binding.summary for a short source description and _page_reference for each changed page label.

*Call graph*: calls 2 internal fn (summary, _page_reference); called by 1 (on_page_change).


##### `_page_reference`  (lines 554–560)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: This formats one changed page as an object reference that an alerted agent can fetch. It adds a short label from the page body so the reference is easier to understand.

**Data flow**: It receives a PageChange. If the page is not removed and has body text, it takes the first line, strips leading heading markers, and trims it; otherwise it uses a fallback label. It returns text like a page object id plus the label.

**Call relations**: _alert_message calls this while building the list of changed pages. It keeps alert wording readable without needing to include full page contents.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 563–597)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: This checks and normalizes tenant API URLs for providers that need them. It prevents unsafe or unexpected URLs from being stored as source configuration.

**Data flow**: It receives a provider and optional base URL. If the connector has a fixed host, it rejects any override. Otherwise it looks up the provider’s safe URL rule, requires a URL when needed, parses it, rejects usernames, passwords, ports, query strings, fragments, non-HTTPS schemes, and host or path shapes that do not match the provider rule. It returns a normalized HTTPS URL without a trailing slash, or no URL for fixed-host providers.

**Call relations**: SourceObjects._apply_owned calls this before account resolution and registration. It uses urlsplit to safely inspect the URL and raises clear errors that include an example of the required shape.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here is not something a user writes by hand. It is a document that the content sync system has already copied in from a registered source. This file gives those synced documents a safe object interface: people can browse them, read them, and, if they own the workspace, delete them in the special sense of marking them forgotten. Without this file, synced source content would exist in storage but would not have a clear, permission-aware way to appear as workspace objects.

The main public object is `PAGE_OBJECT`, which tells the object system that pages are named by their stored row id, can be listed with certain searchable fields, and use `PageObjects` as their behavior. `PageObjects` is the small service behind the object: `list` gathers visible pages and turns them into rows, `get` reads one page and its body from blob storage, and `delete` asks the extension to forget the page. Create and update are refused because pages are produced by the sync driver, not authored through this interface.

Visibility is important. A caller can always see shared pages, and may also see pages private to their own audience member identity. The page body is deliberately capped at 65,536 UTF-8 bytes, like showing a safe preview rather than handing over an unbounded file.

#### Function details

##### `_require_ext`  (lines 61–64)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool call has an extension context attached. The extension context is the object that knows how to read source pages and forget them.

**Data flow**: It receives a `ToolContext`. If the context contains an extension context, it returns it. If not, it stops the operation with a runtime error, because the page object cannot work without that connection to the extension’s storage and services.

**Call relations**: When `PageObjects._pages` needs to read synced pages, it calls this first. `PageObjects.delete` also calls it before asking the extension to forget a page.

*Call graph*: called by 2 (_pages, delete).


##### `_audience_subjects`  (lines 67–73)

```
def _audience_subjects(ctx: ToolContext) -> frozenset[str]
```

**Purpose**: This decides which visibility groups the current caller may read. It prevents one workspace member from seeing another member’s private synced pages.

**Data flow**: It reads the audience member id from the `ToolContext`. If there is no member id, it returns only the shared subject. If there is a member id, it returns both the shared subject and that member’s private subject.

**Call relations**: This is used by `PageObjects._pages` just before asking the extension for page records. It calls `member_subject` to turn a member id into the standard private visibility label.

*Call graph*: called by 1 (_pages); 1 external calls (member_subject).


##### `_page_timestamp`  (lines 76–86)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This normalizes a page timestamp into a consistent UTC time string. It accepts either the provider’s own timestamp or, when that is missing, the row’s stored timestamp.

**Data flow**: It receives an optional timestamp string from the source provider and a fallback `datetime` from the local row. If the provider value is present, it parses it and requires that it include a timezone. It then converts the chosen time to UTC and returns an ISO-formatted string with microseconds.

**Call relations**: `_Page.spec` uses this when building the full page object returned by `get`. `_Page.fields` uses it when preparing the smaller set of fields shown in list results.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 106–107)

```
def name(self) -> str
```

**Purpose**: This gives a page its object name. The name is simply the page row’s unique id written as text.

**Data flow**: It reads the `_Page` instance’s UUID id and converts it to a string. Nothing else changes.

**Call relations**: The listing and lookup flow relies on this property so object names match the stored page ids.


##### `_Page.links`  (lines 109–117)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This describes the source object that synced the page, when that source can be named. It lets readers see that a page was produced by a particular registered source.

**Data flow**: It reads the page’s `source_name`. If there is no source name, it returns no links. If there is one, it builds an `ObjectLink` pointing to the source object with relation `synced_by`.

**Call relations**: `PageObjects.get` includes these links in the detailed object response. Internally this function creates an `ObjectRef` for the source and wraps it in an `ObjectLink`.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 119–132)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This builds the full public description of a page, including metadata and the bounded body text. It is what callers receive when they read a page by name.

**Data flow**: It receives the already-read body text and a flag saying whether the body was cut short. It combines those with the page’s stored metadata, normalizes the creation and update times, and returns a `PageSpec` object.

**Call relations**: `PageObjects.get` calls this after it has found the page and read the body from blob storage. This function calls `_page_timestamp` so the timestamps in the response are consistent.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 134–135)

```
def summary(self) -> str
```

**Purpose**: This makes a short human-readable summary for list views. It helps someone recognize a page without opening the full body.

**Data flow**: It combines the title, source provider, stream, and visibility subject into one short string. It then trims that string to the configured maximum length.

**Call relations**: `PageObjects.list` uses this when turning pages into `ObjectRow` entries for the object listing.


##### `_Page.fields`  (lines 137–145)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This prepares the searchable and sortable metadata shown in page list results. It avoids including the full body, which belongs only in the detailed read.

**Data flow**: It reads the page’s source id, provider, stream, title, and timestamps. It normalizes the timestamps and returns a dictionary of simple JSON-compatible values.

**Call relations**: `PageObjects.list` calls this while building each list row. It calls `_page_timestamp` for the created and updated time values.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 156–161)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of synced pages the caller is allowed to see. It is used when someone browses available source pages.

**Data flow**: It receives a tool context and a list query containing things like filters, ordering, or page size. It asks `_pages` for all visible pages, converts each one into an `ObjectRow` with a name, summary, and fields, then passes those rows and the query to `object_page` to produce the final page of results.

**Call relations**: This is one of the main object operations exposed through `PAGE_OBJECT`. It depends on `PageObjects._pages` for permission-aware page retrieval, then hands the rows to the shared object paging helper.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 163–193)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This reads one synced page by name and returns its full detail, including a bounded copy of the body. It is the safe “open this page” operation.

**Data flow**: It receives a context and page name. It finds the visible page, opens the body reference from blob storage, and reads at most 65,537 bytes so it can tell whether the 65,536-byte response limit was exceeded. It decodes the bounded bytes as UTF-8, carefully avoiding a broken final character when truncation cuts through multibyte text, then returns an `ObjectDetail` with the page spec, timestamps, and links. If no visible page matches, it returns `None`.

**Call relations**: This is called by the object system when a caller requests a page detail. It uses `PageObjects._find` to locate the page, then uses `_Page.spec` and `_Page.links` to shape the final response.

*Call graph*: calls 1 internal fn (_find); 1 external calls (__init__).


##### `PageObjects.status`  (lines 195–196)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for pages. A synced page is either present and readable through `get`, or not found.

**Data flow**: It receives the context and page name but does not inspect them. It always returns `None`, meaning there is no extra status payload.

**Call relations**: The object interface includes a status operation, but this page kind does not need one, so the method is a deliberate no-op.


##### `PageObjects.apply`  (lines 198–201)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None) -> None
```

**Purpose**: This refuses attempts to create or update pages through the object interface. Pages must come from the content sync driver instead.

**Data flow**: It receives the context, name, desired spec, and optional old spec. It ignores the requested change and raises `VerbNotSupported` with a message explaining that pages are synced, not authored here.

**Call relations**: The object system calls `apply` for create or update-style operations. For this object kind, the method acts as a guardrail so only source registration and syncing can produce page content.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 203–209)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This lets the workspace owner forget one synced page. Forgetting tombstones the page so the existing page-change pipeline can clean up derived index data.

**Data flow**: It receives a context and page name. First it asks whether the speaker is the workspace owner; if not, it raises `OwnerRequired`. Then it finds the visible page by name; if no page exists, it raises a value error. Finally, it gets the extension context and calls `forget_page` with the page id.

**Call relations**: The object system calls this for delete requests. It uses `ToolContext.speaker_is_owner` for the owner gate, `PageObjects._find` to locate the target page, and `_require_ext` before handing the final forget request to the extension.

*Call graph*: calls 3 internal fn (speaker_is_owner, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 211–212)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This searches the caller’s visible pages for one object name. It is the shared lookup step used before reading or forgetting a page.

**Data flow**: It receives a context and name. It asks `_pages` for all pages the caller may see, compares each page’s object name to the requested name, and returns the first match. If none match, it returns `None`.

**Call relations**: `PageObjects.get` uses this before reading the body. `PageObjects.delete` uses it before forgetting the page.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 214–241)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This collects the live synced pages visible to the current caller and enriches them with source information. It is the main internal bridge between raw extension records and the `_Page` helper objects used by listing and reading.

**Data flow**: It receives a tool context. It first requires an extension context, reads all registered sources, builds a map from source ids to provider names, and, when possible, builds object names for those source bindings. It then asks the extension for source page records limited to the caller’s allowed visibility subjects, and converts each record into a `_Page` with metadata, body reference, timestamps, and optional source link name.

**Call relations**: `PageObjects.list` calls this to build list rows, and `PageObjects._find` calls it to locate a single page. It uses `_audience_subjects` to enforce visibility, `_require_ext` to reach extension services, `ConnectorSourceConfig.model_validate` to read connector configuration, and `binding_name` to connect a page back to its source object.

*Call graph*: calls 2 internal fn (_audience_subjects, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/exa/ufo_ext_exa.py`

`io_transport` · `request handling`

This file is an adapter between the project’s general search interface and Exa, an external search API. Think of it like a travel adapter: the rest of the system speaks in its own standard shapes, such as “search query,” “search result,” and “fetched page,” while Exa expects particular web request bodies and returns its own response format. This file converts between the two.

The main class, ExaSearchProvider, runs on the host side of the application, not inside the sandbox. That matters because it reads the user’s Exa API key through CredentialAccess and sends it directly to api.exa.ai. The key is not injected into sandboxed tool code.

When a search is requested, the provider builds an Exa /search request, including options like result count, allowed domains, recency, or a special vertical such as academic or people search. It sends the request over HTTP, checks that Exa did not return an error, then turns each returned item into the project’s SearchHit format.

When page content is requested, it calls Exa’s /contents endpoint for one URL and returns a FetchedPage with text and an optional summary. If Exa gives a bad status code or a response without a usable results list, this file raises ExaError instead of quietly pretending there were no results.

#### Function details

##### `ExaSearchProvider.search`  (lines 54–56)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a web search through Exa and returns the results in the project’s standard search-result format. A caller uses this when it has a SearchQuery and wants matching web pages without dealing with Exa’s API directly.

**Data flow**: It receives a SearchQuery with the search text and options. It turns that query into an Exa request body, sends it to Exa, checks the returned payload for a results list, converts each result item into a SearchHit, and returns a SearchResults object containing those hits.

**Call relations**: This is the main search entry point for this provider. It relies on _search_body to translate the project’s query into Exa’s request shape, _post to do the authenticated HTTP call, _results to validate and extract Exa’s results list, and _hit to convert each Exa item into the project’s common result shape.

*Call graph*: calls 4 internal fn (_hit, _post, _search_body, _results); 1 external calls (__init__).


##### `ExaSearchProvider.fetch`  (lines 58–74)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches readable content for a single URL through Exa. A caller uses this when it already has a page address and wants the page text, plus an optional summary.

**Data flow**: It receives a FetchRequest containing a URL, optional maximum text length, optional summary prompt, and a force-refresh flag. It builds an Exa /contents request, caps the amount of text requested, asks Exa for the content, extracts the first result if present, and returns a FetchedPage with the URL, text, and optional summary.

**Call relations**: This is the provider’s page-reading path. It hands the prepared request to _post for the HTTP call, uses _results to pull out Exa’s returned items, and uses _opt_str to safely keep the summary only when Exa actually returned a string.

*Call graph*: calls 3 internal fn (_post, _opt_str, _results); 1 external calls (__init__).


##### `ExaSearchProvider._search_body`  (lines 77–91)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the exact JSON body that Exa expects for a search request. It hides Exa-specific request details from the rest of the provider.

**Data flow**: It receives a SearchQuery. For a normal web search, it asks Exa for text snippets and highlights, optionally limits results to allowed domains, and optionally adds a start date for recent results. For a vertical search, it asks for shorter text and may map project terms like academic or people into Exa categories. It returns a dictionary ready to send as JSON.

**Call relations**: ExaSearchProvider.search calls this before sending anything to Exa. The date calculation is only used when the caller asks for recent results, such as the past day, week, or month.

*Call graph*: called by 1 (search); 2 external calls (now, timedelta).


##### `ExaSearchProvider._hit`  (lines 94–104)

```
def _hit(item: dict[str, object]) -> SearchHit
```

**Purpose**: Converts one Exa result item into the project’s SearchHit format. This keeps Exa’s response details from leaking into the rest of the system.

**Data flow**: It receives one dictionary from Exa’s results list. It reads fields such as URL, title, page text, published date, and highlights, replacing missing text-like fields with empty strings where needed. It keeps highlights only if they are strings, then returns a SearchHit.

**Call relations**: ExaSearchProvider.search uses this for every valid result item extracted by _results. It calls _opt_str for the published date so non-string values are treated as absent instead of being passed along incorrectly.

*Call graph*: calls 1 internal fn (_opt_str); called by 1 (search); 1 external calls (__init__).


##### `ExaSearchProvider._post`  (lines 106–114)

```
async def _post(self, path: str, body: dict[str, Json]) -> object
```

**Purpose**: Sends one authenticated HTTP POST request to Exa and returns the decoded JSON response. It is the single place where the Exa API key is read and attached to outgoing requests.

**Data flow**: It receives an API path, such as /search or /contents, and a JSON-ready request body. It reads the Exa API key from credentials, creates an async HTTP client pointed at api.exa.ai, posts the body with the key in the x-api-key header, and returns the response JSON. If Exa returns an error status, it raises ExaError with the status and response text.

**Call relations**: Both ExaSearchProvider.search and ExaSearchProvider.fetch call this when they are ready to contact Exa. It is also where tests can inject a custom HTTP transport, so tests can fake Exa responses without calling the real network.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_results`  (lines 117–121)

```
def _results(payload: object) -> list[dict[str, object]]
```

**Purpose**: Pulls the results list out of an Exa response and checks that it is shaped as expected. This prevents malformed API responses from being mistaken for an empty search.

**Data flow**: It receives the decoded response payload from Exa. If the payload is a dictionary with a results field that is a list, it keeps only the list items that are dictionaries and returns them. If there is no proper results list, it raises ExaError.

**Call relations**: Both search and fetch use this immediately after _post returns. It acts as a gatekeeper before the rest of the code tries to interpret individual result items.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `_opt_str`  (lines 124–125)

```
def _opt_str(value: object) -> str | None
```

**Purpose**: Returns a value only if it is actually a string. It is a small safety helper for optional text fields from Exa.

**Data flow**: It receives any value. If the value is a string, it returns that string; otherwise it returns None. It does not change anything else.

**Call relations**: ExaSearchProvider._hit uses this for published dates, and ExaSearchProvider.fetch uses it for summaries. In both cases, it keeps unexpected non-text values from being treated as valid text.

*Call graph*: called by 2 (_hit, fetch).


##### `manifest`  (lines 128–141)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system: its name, version, required credential, and the search backend it provides. Without this, the system would not know how to select or construct the Exa search provider.

**Data flow**: It takes no input. It creates a Manifest containing one credential slot for the user-provided Exa API key and one search provider specification named exa. That specification says how to build an ExaSearchProvider when the host supplies credential access.

**Call relations**: The extension-loading part of the system calls this to discover what the file offers. The manifest connects the configured search provider name to a factory that creates ExaSearchProvider for real search and fetch requests.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Specialized service bridge
The YC bridge safely exposes the Y Combinator command-line interface to UFO tools.

### `extensions/yc/ufo_ext_yc/cli.py`

`io_transport` · `request handling`

This file exists because YC data is reached through two outside boundaries: YC’s web login service and the local `yc` command-line program. Without this layer, other parts of the system would have to know how to store tokens, launch the CLI, limit output size, clean up temporary files, and recover when login is still pending or has expired.

There are two main paths. The authorization path, centered on `YcAuth`, starts or completes a “device authorization” login flow. That is the familiar pattern where a program shows a code and URL, and the user finishes login in a browser. The file stores pending authorization state in the extension store, seals secret material through the credential system, and saves finished credentials only after YC returns tokens.

The command path, centered on `YcCli` and `YcRead`, runs the installed `yc` program with the saved credentials. To avoid leaking secrets into the real machine account, it creates a temporary home folder, writes credentials there, runs the CLI with tight time and size limits, then deletes the folder. If the CLI refreshes credentials while running, this file carefully writes the newer credentials back to secure storage.

In short, this file is like a guarded reception desk: it checks who may log in, keeps passwords in the safe, passes approved requests to the YC CLI, and brings back only bounded text output.

#### Function details

##### `YcDeviceAuthorization.validate_verification_url`  (lines 59–63)

```
def validate_verification_url(self) -> 'YcDeviceAuthorization'
```

**Purpose**: This validation step makes sure YC’s device-login response sends the user to the real Y Combinator account website. It prevents a bad or unexpected response from directing the user to some other host.

**Data flow**: It receives a parsed device authorization record with one or two verification URLs. It chooses the complete URL when present, otherwise the basic URL, checks that its host is `account.ycombinator.com`, and returns the same record if it is safe; otherwise it rejects the record with an error.

**Call relations**: This is run automatically when a YC device authorization response is parsed into a `YcDeviceAuthorization`. In the larger login flow, `YcAuth._start` depends on this check before showing the verification URL and user code to the user.


##### `YcRunner.run`  (lines 93–93)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This is a small contract for anything that can run YC commands. It lets `YcRead` depend on the idea of a runner without caring whether the runner is the real CLI or a test double.

**Data flow**: It is defined as taking command arguments and a session name, then producing text output. The protocol itself does not perform work; an implementing object supplies the actual before-to-after behavior.

**Call relations**: `YcRead.run` calls a runner through this shape. In normal use, the runner is `YcCli`, which turns those arguments into an actual subprocess call to the installed `yc` program.


##### `YcAuth.run`  (lines 101–116)

```
async def run(self, action: Literal['start', 'complete'], session: str) -> YcAuthResult
```

**Purpose**: This is the front door for YC login actions. It checks that the request is allowed and then routes the request to either start login or finish login.

**Data flow**: It receives an action, either `start` or `complete`, plus a session label. Before doing anything, it reads the tool context to confirm there is an extension context, the speaker is acting in their private audience, credential storage is configured, the YC credential slot exists, and the speaker owns the workspace. If all checks pass, it returns a structured login status from `_start` or `_complete`.

**Call relations**: The public tool function `yc_auth` creates a `YcAuth` object and calls this method. This method then hands the real work to `YcAuth._start` for beginning browser-based login or `YcAuth._complete` for exchanging the finished browser approval for stored credentials.

*Call graph*: calls 2 internal fn (_complete, _start).


##### `YcAuth._start`  (lines 118–176)

```
async def _start(self, session: str) -> YcAuthResult
```

**Purpose**: This begins the YC device-login flow and returns the URL and code the user must visit. It also avoids creating duplicate pending logins when the same request is repeated.

**Data flow**: It reads the extension store to see whether a pending authorization already exists for this request. If one does, it reopens the sealed pending data and returns the same verification URL and user code, unless credentials have already changed, in which case it reports that YC is connected. If no matching pending authorization exists, it asks YC’s authorization endpoint for a device code, checks the response size and host, seals the pending secret through the credential system, stores a small pointer to it, and returns `authorization_required` with the user-facing login details.

**Call relations**: This is called by `YcAuth.run` when the action is `start`. It uses `_credential_digest` to notice whether credentials have changed, `_headers` to identify the CLI session to YC, and `_bound` to reject oversized web responses before parsing them.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 5 external calls (__init__, __init__, __init__, __init__, time).


##### `YcAuth._complete`  (lines 178–230)

```
async def _complete(self, session: str) -> YcAuthResult
```

**Purpose**: This finishes the YC device-login flow after the user has approved the code in their browser. It either stores the resulting credentials, says login is still pending, or explains why the flow must be restarted.

**Data flow**: It first reads the stored pending authorization. If none exists, it checks whether valid credentials are already stored and returns `connected` if so; otherwise it errors. If pending data exists, it reopens the sealed device code, checks whether it expired, then asks YC’s token endpoint for real credentials. A successful response is validated and stored securely. A still-waiting response becomes a `pending` result. Expired or failed responses clear the pending marker and raise a clear error.

**Call relations**: This is called by `YcAuth.run` when the action is `complete`. It shares helper work with `_start`: `_credential_digest` checks whether credentials changed elsewhere, `_headers` builds YC request headers, and `_bound` protects the process from unexpectedly large web responses.

*Call graph*: calls 3 internal fn (_bound, _credential_digest, _headers); called by 1 (run); 4 external calls (__init__, __init__, loads, time).


##### `YcAuth._credential_digest`  (lines 232–240)

```
async def _credential_digest(self) -> str | None
```

**Purpose**: This creates a fingerprint of the currently stored YC credentials, if any. The fingerprint lets the login flow tell whether credentials changed without storing or comparing the secret text directly in the pending-login record.

**Data flow**: It tries to read the YC credential slot from secure credential storage. If the slot is unset, it returns nothing. If credentials exist, it validates their shape, hashes the raw credential text with SHA-256, and returns the hash string.

**Call relations**: `YcAuth._start` and `YcAuth._complete` call this when deciding whether a pending login is stale or whether the system is already connected. It keeps those methods from putting raw tokens into the extension store.

*Call graph*: called by 2 (_complete, _start); 1 external calls (sha256).


##### `YcAuth._headers`  (lines 242–247)

```
def _headers(self, session: str) -> dict[str, str]
```

**Purpose**: This builds the standard HTTP headers sent to YC’s authorization service. The headers identify the YC CLI version and tie the request to the current UFO conversation session.

**Data flow**: It receives a session string. It combines that with the fixed CLI version and returns a small dictionary of header names and values.

**Call relations**: `YcAuth._start` uses these headers when requesting a device code, and `YcAuth._complete` uses them when exchanging that code for tokens. This keeps both YC web calls labeled in the same way.

*Call graph*: called by 2 (_complete, _start).


##### `YcAuth._bound`  (lines 249–251)

```
def _bound(self, response: httpx.Response) -> None
```

**Purpose**: This protects the process from accepting an unexpectedly huge authentication response. It is a simple size guard for data returned by YC’s web service.

**Data flow**: It receives an HTTP response. It checks the number of bytes in the response body. If the body is within the allowed limit, nothing changes; if it is too large, it raises a YC CLI error.

**Call relations**: `YcAuth._start` and `YcAuth._complete` call this immediately after receiving HTTP responses. That means oversized data is stopped before JSON parsing or credential processing begins.

*Call graph*: called by 2 (_complete, _start); 1 external calls (__init__).


##### `_read_bounded`  (lines 254–262)

```
async def _read_bounded(stream: asyncio.StreamReader, limit: int) -> bytes
```

**Purpose**: This reads text coming from the YC command-line program while enforcing a maximum size. It prevents a runaway command from filling memory with unlimited output.

**Data flow**: It receives an asynchronous stream, such as standard output or standard error from a subprocess, and a byte limit. It reads the stream in chunks, counts the bytes, collects the chunks, and returns the combined bytes. If the total grows past the limit, it raises a YC CLI error instead of returning data.

**Call relations**: `YcCli._execute` starts this helper twice: once for normal output and once for error output. Those bounded readers run while the subprocess is active so the process cannot block on full output pipes.

*Call graph*: called by 1 (_execute); 2 external calls (__init__, read).


##### `YcCli.run`  (lines 270–280)

```
async def run(self, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This runs one YC CLI command using securely stored credentials. It sets up a temporary private environment for the command, cleans it up afterward, and saves any credential refresh the CLI performs.

**Data flow**: It reads the YC credentials from secure storage and validates them. It creates a temporary home folder containing those credentials, calls `_execute` to run the requested command, then calls `_persist_refresh` so refreshed tokens are not lost. Finally, it deletes the temporary folder and returns the command’s text output.

**Call relations**: `YcRead.run` calls this through the `YcRunner` interface in normal tool use. Inside, this method ties together `_prepare_home`, `_execute`, and `_persist_refresh` so callers do not need to know about temporary files or credential rotation.

*Call graph*: calls 2 internal fn (_execute, _persist_refresh); 1 external calls (to_thread).


##### `YcCli._prepare_home`  (lines 282–289)

```
def _prepare_home(self, raw: str) -> Path
```

**Purpose**: This creates a private temporary home directory for the YC CLI and writes the credential file where the CLI expects to find it. It keeps the real host user’s home directory untouched.

**Data flow**: It receives raw credential JSON text. It creates a new temporary directory with owner-only permissions, creates a `.yc/credentials.json` file inside it, writes the credentials there with restrictive permissions, and returns the path to the temporary home.

**Call relations**: `YcCli.run` uses this before launching the CLI. The returned folder is later passed to `_execute` as the command’s `HOME` and is deleted by `YcCli.run` after the command finishes.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `YcCli._execute`  (lines 291–332)

```
async def _execute(self, home: Path, args: tuple[str, ...], session: str) -> str
```

**Purpose**: This actually launches the installed `yc` program and captures its output safely. It enforces a timeout, limits output size, and turns command failures into clear errors.

**Data flow**: It receives a temporary home path, command arguments, and a session label. It builds a small environment with that home directory, the system path, and the session value, then starts the `yc` subprocess. It reads standard output and standard error through `_read_bounded`, waits no longer than the configured timeout, kills the process if it times out or output is too large, and returns decoded standard output when the command succeeds. If the executable is missing or the command exits with an error code, it raises a YC CLI error.

**Call relations**: `YcCli.run` calls this after creating the temporary credential home. This method delegates stream reading to `_read_bounded`, and its result is handed back up to `YcCli.run`, which then persists refreshed credentials and cleans up.

*Call graph*: calls 1 internal fn (_read_bounded); called by 1 (run); 5 external calls (__init__, create_subprocess_exec, create_task, gather, timeout).


##### `YcCli._persist_refresh`  (lines 334–353)

```
async def _persist_refresh(self, home: Path, expected: str) -> None
```

**Purpose**: This saves new YC credentials if the CLI refreshed them while running. It is careful not to overwrite someone else’s newer credential update by accident.

**Data flow**: It reads the credential file from the temporary home and validates it. If it is unchanged, it does nothing. If it changed, it tries to rotate the stored credential from the expected old value to the refreshed value. If another update happened at the same time, it rereads the current stored value and compares creation times, retrying only when the refreshed value is newer. If the conflict cannot be reconciled, it raises an error.

**Call relations**: `YcCli.run` calls this after `_execute`, even when the command path is being cleaned up. It is the bridge from the CLI’s temporary credential file back to the system’s secure credential store.

*Call graph*: called by 1 (run); 2 external calls (__init__, to_thread).


##### `YcReadInput.validate_action`  (lines 366–373)

```
def validate_action(self) -> 'YcReadInput'
```

**Purpose**: This checks that a YC read request contains the fields required for its chosen action. It catches unclear or impossible requests before they become CLI commands.

**Data flow**: It receives a parsed `YcReadInput` object. It verifies that `ask` and `search` have a query, `skills_read` has a skill name, and `entity` is used only with search. If the request is valid, it returns the same object; otherwise it raises a validation error.

**Call relations**: This runs automatically when tool input is validated. It protects `YcRead.run`, which can then translate the request into command arguments without rechecking every invalid combination.


##### `YcRead.run`  (lines 380–395)

```
async def run(self, args: YcReadInput, session: str) -> str
```

**Purpose**: This converts high-level YC read actions into concrete `yc` command-line arguments. It is the translator between tool-friendly requests like “search” or “read this skill” and the CLI’s exact command words.

**Data flow**: It receives a validated `YcReadInput` and a session string. Based on the action, it builds the right tuple of command arguments: agent ask, search, skills list, skills read, or tools context. It then sends those arguments to the configured runner and returns the runner’s text output.

**Call relations**: `yc_read` creates a `YcRead` with a real `YcCli` runner and calls this method. This method does not launch the subprocess itself; it hands the prepared command to the runner so command execution stays centralized in `YcCli`.


##### `yc_read`  (lines 398–404)

```
async def yc_read(ctx: ToolContext, args: YcReadInput) -> ToolResult
```

**Purpose**: This is the tool-facing function for reading YC information. It accepts a tool request, runs the matching YC CLI command, and packages the text result for the UFO tool system.

**Data flow**: It receives the current tool context and validated read input. It checks that the YC extension context exists, builds a `YcCli` using the extension’s credential access, wraps it in `YcRead`, and runs the request with a session name based on the conversation id. It returns a `ToolResult` containing the CLI output as text.

**Call relations**: This is the public boundary called by the tool runtime for YC read actions. It hands translation to `YcRead.run` and command execution to `YcCli`, then wraps the returned output in `TextContent` and `ToolResult` for the rest of the system.

*Call graph*: 4 external calls (__init__, __init__, __init__, __init__).


##### `yc_auth`  (lines 407–414)

```
async def yc_auth(ctx: ToolContext, args: YcAuthInput) -> ToolResult
```

**Purpose**: This is the tool-facing function for connecting a workspace to YC. It starts or completes the browser-based authorization flow and returns a small JSON status message to the user interface.

**Data flow**: It receives the tool context and an authorization input saying `start` or `complete`. It checks that the YC extension context exists, opens an HTTP client with a timeout, creates `YcAuth`, and runs the requested action with a conversation-based session name. It turns the resulting status object into JSON text inside a `ToolResult`.

**Call relations**: This is the public boundary called by the tool runtime for YC authorization actions. It delegates the permission checks and login flow to `YcAuth.run`, while it provides the HTTP client and packages the final result as tool output.

*Call graph*: 4 external calls (__init__, __init__, __init__, AsyncClient).

## 📊 State Registers Touched

- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-mcp-server-connections` — The configured MCP tool-server connections used to discover and call extra provider tools.
- `reg-source-page-sync-state` — The saved sources, pages, sync cursors, deletion markers, and retry state for imported external content.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-oauth-handshake-state` — Temporary signed or stored state for account-connection callbacks, hosted consent links, and GitHub App installation flows before a durable grant exists.
- `reg-extension-request-context-envelope` — The request-time workspace, member, conversation, turn, capability, and cleanup context passed from core into extension handlers and tools.
- `reg-web-search-fetch-backend` — The configured web-search and page-fetch provider backend, client settings, and availability used by research, browsing, source, and SDK search calls.

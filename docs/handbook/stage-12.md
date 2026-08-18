# Source synchronization, page ingestion, indexing, and retrieval  `stage-12`

This stage is the system’s intake and search pipeline. It runs mostly in the main background work loop, after a user connects an outside account or registers a source. First, connected.py, tools.py, and triggers.py create source feeds, let users request syncing, and record which conversations should wake up when shared content changes. The connector layer defines the common rules for all outside readers, including cursors, which are saved bookmarks that let a sync resume where it stopped.

Provider-specific connectors then talk to services like Google, Slack, GitHub, or Jira and turn their different API results into one standard record shape. backend.py adapts that stream into normal source runs, while sync.py stores current page bodies, metadata, deletions, and change events. pages.py lets users view those synced pages safely as read-only objects.

After pages arrive, indexing.py cuts long text into smaller chunks and prepares them for search. The default index stores and searches those chunks, while memory extraction can turn page changes into durable facts for later recall. Together, these parts move content from external systems into searchable pages, indexes, and agent memory.

## Sub-stages

- [Provider-specific source connectors](stage-12.1.md) `stage-12.1` — 49 files
- [Memory extraction and searchable recall](stage-12.2.md) `stage-12.2` — 7 files

## Files in this stage

### Source feed registration
Creates connector-backed source feeds, exposes source registration tools, and records wake-up triggers for conversations interested in source changes.

### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection hook and background retry`

When a member connects an outside account, the system records that connection, but the member also needs source rows so the system knows which streams of data to sync. This file closes that gap. It turns a successful connection into one private source per important provider stream, without asking the member to do a second setup step.

The main idea is simple: a connector describes the provider’s core streams, called canonical streams. For example, these are the main collections the connector exists to read, not every possible side list exposed by the provider’s API. When a connection is recorded, this file looks up the connector, checks those canonical streams, and registers any missing source rows for the member who owns the connection.

It is careful not to undo user choices. If a source row already exists, it leaves it alone. If the member previously removed that source, it does not recreate it, because that would erase the meaning of the removal. If an existing binding contains shared workspace content, this code does not add more streams into that shared area; it only creates private member-owned rows.

There is also a scheduled retry function. It is not the normal producer. It is a safety net for cases where the hook failed, the process crashed, or a connection appeared through another path. Providers that require a tenant-specific base URL are skipped, because only the member can supply that URL.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs right after a connection has been recorded. Its job is to immediately create the private feeds that belong to that new connection, before the connect flow finishes responding to the member.

**Data flow**: It receives a hook context containing an event payload. If the payload is a connection-recorded event, it takes the connection ID from it, builds a ConnectedSources helper using the extension context, and asks it to register sources for just that connection. If the payload is the wrong kind, it raises an error instead of silently doing the wrong thing. It returns no special outcome.

**Call relations**: This function is called by the extension hook system when a connection-recorded event fires. It hands the real work to ConnectedSources.register, using the connection ID as a filter so only the newly created connection is considered.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the safety-net job for source creation. It scans existing main-agent connections and creates any canonical source rows that should exist but do not.

**Data flow**: It receives an extension context. From that, it builds a ConnectedSources helper and asks it to register sources without naming a specific connection. Because there is no connection ID filter, the helper checks all eligible connections. The function itself returns nothing; its effect is the creation of missing source rows.

**Call relations**: This function is meant to be run later, such as on a scheduled tick or retry pass. It uses the same ConnectedSources.register path as the connect-time hook, so the immediate path and retry path follow the same rules about existing, removed, private, and unsupported sources.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method decides which connections are eligible for automatic source creation. It can work on one specific connection, or on every main-agent connection during a retry run.

**Data flow**: It first reads the currently live source rows from the extension context. Then it asks for the connections held by the main agent, which is the system actor allowed to sync data for connected accounts. For each connection, it optionally filters by the requested connection ID, looks up the matching connector class, skips unknown connectors and connectors that need a member-supplied base URL, and then passes eligible connections to _register. It does not directly return data; it causes missing source rows to be registered through the lower-level method.

**Call relations**: The hook on_connection_recorded calls this with a connection ID so it only touches the connection that just landed. The retry job calls it without a connection ID so it checks all main-agent connections. For each usable connector found in CONNECTORS, it delegates the detailed per-stream work to ConnectedSources._register.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates the missing source rows for the canonical streams of one connected account. It also protects existing rows and past removals so automatic registration does not override a member’s choices.

**Data flow**: It receives one connection, one connector instance, and the list of live source records already known. It builds the member subject for the connection owner, finds any existing source rows tied to this connection, and stops if those rows belong to someone else. If there is already a bound row, it reads that row’s source configuration to reuse its requested backfill window. It then checks each stream exposed by the connector, keeps only canonical streams, calculates how far back the first sync should reach, builds a source configuration, and computes the source ID that row would have. If that ID is not already live, it is considered fresh. Before registering fresh rows, it asks which of them were previously removed. Finally, it registers only the fresh rows that were not removed, making them private to the connection owner and tied to the original connection.

**Call relations**: ConnectedSources.register calls this after it has chosen an eligible connection and connector. Inside, this method relies on the connector’s stream list to know what should exist, uses effective_days to combine an existing backfill request with the stream’s own limit, and uses the extension context to compute source IDs, check removed rows, and register the final source records.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object apply/list/get/delete and page-change hook handling`

This file makes external content sync feel like normal workspace objects. A “source” is one binding to an outside provider, such as a provider account plus an optional tenant URL, with one or more named streams to sync. The file validates that the provider exists, the streams are real, the URL is safe, and the member has the right connected account or workspace credential. It then registers one internal source row per stream. Re-applying a source is treated as the whole desired state: streams left out are removed, which also removes the pages they synced.

It also protects ownership. Sources start private to the member who registered them unless explicitly shared. Private sources can later become shared by that member, but shared sources cannot be made private in place. Deleting or forcing a resync is limited to the registrar or an admin.

The second half defines “source_trigger” objects. A trigger is a standing request for one conversation to be woken when a shared source changes. Triggers can either wake the current conversation with a batch summary or open one stable conversation per changed page. When page changes arrive, this file filters them to shared pages the target agent may read, writes a small change log file when possible, and sends a clear alert message.

#### Function details

##### `_Binding.name`  (lines 200–201)

```
def name(self) -> str
```

**Purpose**: Returns the official object name for a source binding. The name is derived from the provider, account, and tenant URL so the same binding cannot be registered under many different names.

**Data flow**: It reads the binding’s provider, account, and base URL, feeds them into the shared naming helper, and returns the resulting stable name string.

**Call relations**: Other parts of this file compare user-supplied object names against this derived name when listing, finding, applying, deleting, or waking triggers for a source.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 204–205)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding first came into existence. Because a binding is made of several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads all stream creation timestamps inside the binding, chooses the earliest one, and returns that timestamp.

**Call relations**: It is used when building object details for a source, so readers see one creation time for the whole binding instead of one per stream.


##### `_Binding.updated_at`  (lines 208–209)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports the most recent update time for the binding. Since each stream has its own row, the binding’s update time is the newest update among them.

**Data flow**: It reads all stream update timestamps, chooses the latest one, and returns that timestamp.

**Call relations**: It is used when source object details are shown, giving the caller a single “last changed” time for the grouped binding.


##### `_Binding.links`  (lines 211–224)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Builds the object links that explain what the source uses for authentication. For example, it may point to a workspace credential slot or to a connected account object.

**Data flow**: It reads the binding’s account and sharing state. For direct credential-based sources it returns a link to a credential object; for private connected-account sources it returns a link to the connection object; for shared connected-account sources it returns no connection link so private account details are not exposed.

**Call relations**: Source object detail calls this so user interfaces can show useful related objects without leaking connection information for shared sources.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 226–234)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns the internal binding rows back into the public source specification users see. This is the readable manifest form of the source.

**Data flow**: It reads provider, stream names, account, base URL, sharing state, and backfill setting from the binding, converts internal values like the direct-account marker into user-facing fields, and returns a SourceSpec.

**Call relations**: Object get and resync checks use this to compare what is stored with what the caller is applying.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 236–238)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a source binding. It names the provider, account, and streams without exposing the full internal details.

**Data flow**: It joins the stream names, combines them with provider and account, trims the result to the summary length limit, and returns the text.

**Call relations**: Listings and alert messages use this summary so people can quickly understand which source changed.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 247–250)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure the extension context is present before code tries to read or change source data. The extension context is the object that gives access to the system’s source, file, credential, and hook services.

**Data flow**: It receives a possible extension context. If one is present, it returns it; if missing, it raises a runtime error because this file cannot work without it.

**Call relations**: Most source and trigger operations call this near the start, so failures are clear when the object system was invoked without the needed extension services.

*Call graph*: called by 9 (_apply_owned, _delete_owned, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 253–256)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool call has a connector registry. The connector registry is the catalog of available external-account connection methods.

**Data flow**: It reads the connector registry from the tool context. If it exists, it returns it; if not, it raises a runtime error.

**Call relations**: Account resolution uses this before deciding whether a provider should use a connected account, a broker, or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 259–292)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds user-facing source bindings from the lower-level source rows stored by the sync system. This is like grouping individual train tickets back into one trip itinerary.

**Data flow**: It asks the extension context for all source rows, ignores rows from unknown providers, reads each row’s connector config, groups rows by provider, account, and base URL, records owner and backfill information, and returns sorted _Binding objects.

**Call relations**: Listing sources, finding one source, listing triggers with source summaries, and processing page changes all start from this grouped view.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 295–305)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one registered source binding by its official derived name. Both source operations and trigger operations need this lookup.

**Data flow**: It requires a valid extension context, rebuilds all bindings, scans for the one whose derived name matches the requested name, and returns that binding or None.

**Call relations**: Source get, status, apply, resync, and delete use it to locate the binding. Trigger creation also uses it to confirm that the watched source still exists.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 6 (_apply_owned, _delete_owned, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 308–309)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the trigger store, which is where source-trigger rows are kept. It ensures the needed extension context is present first.

**Data flow**: It receives a possible extension context, verifies it, wraps it in a SourceTriggerStore, and returns that store object.

**Call relations**: Trigger listing, creation, deletion, lookup, source deletion cleanup, and page-change wakeups all use this helper to reach the trigger storage layer.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 312–321)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides how far back a stream should sync when a source is first registered or widened. It combines the user’s request with the stream’s own default window.

**Data flow**: It takes a requested backfill value and a stream-declared default. A number wins, no request uses the stream default, and “all” or a stream with no window becomes None, meaning no cutoff date.

**Call relations**: Source registration uses it to set the first cutoff date. Window-widening uses it to compare the old and new reach of kept streams.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 324–334)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Builds a compact fingerprint of the parts of a source specification that define the binding’s intended state. It is used to tell whether an apply is a no-op or whether a resync request is trying to change something else.

**Data flow**: It reads provider, sorted streams, account ID, base URL, shared flag, and backfill setting from a SourceSpec and returns them as a tuple.

**Call relations**: SourceObjects.apply uses it to skip identical re-applies. SourceObjects._resync uses it to reject resync requests that also try to edit the source.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 363–382)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Receives a source apply request and decides whether it is a resync, a harmless repeat, or a real change. It keeps resync as an action separate from changing source state.

**Data flow**: It takes the tool context, object name, new spec, optional old spec, and expected generation. If resync is requested, it delegates to _resync; if the visible old spec is identical, it returns without writing; otherwise it passes the request to the base object flow.

**Call relations**: This is the public apply entry for source objects. It hands true registration and edits onward to the inherited apply machinery, which later calls SourceObjects._apply_owned after ownership checks.

*Call graph*: calls 2 internal fn (_resync, _binding_identity).


##### `SourceObjects._resync`  (lines 384–408)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing source without changing its configuration. This lets a registrar or admin say “run it now” without re-registering it.

**Data flow**: It checks that the submitted spec matches the current visible spec, checks that the caller can see the source and is either its owner or an admin, finds the binding, collects its stream source IDs, and asks the extension context to schedule those streams for syncing.

**Call relations**: SourceObjects.apply calls this when spec.resync is true. It relies on _binding_identity, _binding_named, and the base ownership helpers so resync cannot be used by someone who merely sees a shared source.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._member_rows`  (lines 410–423)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when someone lists source objects. Each row represents one grouped binding, not each individual stream row.

**Data flow**: It rebuilds bindings from extension source rows, turns each binding into an OwnedRow with name, summary, owner member, and shared flag, and returns the tuple of rows.

**Call relations**: The inherited object store calls this during source listing and visibility filtering.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 425–441)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view of one source object. It turns the internal binding into the public spec plus timestamps and links.

**Data flow**: It looks up the named binding. If missing, it returns None; otherwise it returns an ObjectDetail containing the binding’s SourceSpec, creation time, update time, and authentication links.

**Call relations**: The object system calls this after it has determined that the caller may read the source.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 443–465)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live sync status for a source. This includes when each stream will sync next, how many errors it has had in a row, and any backfill cutoff.

**Data flow**: It finds the binding, then builds a dictionary with the shared flag and per-stream status fields. For private sources it also includes the owner member ID when known.

**Call relations**: The object status path calls this to give users operational information beyond the static manifest.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 467–562)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real work of registering or editing a source after ownership rules have passed. It validates the provider, stream names, URL, authentication path, sharing change, backfill change, added streams, and dropped streams.

**Data flow**: It receives the desired source spec and current owner state. It verifies a speaking member exists, checks provider and stream support, validates base URL shape, resolves the account or credential, checks the derived object name, loads any existing binding, widens windows or shares existing rows when allowed, registers new stream rows with cutoff dates, and removes rows for streams no longer named.

**Call relations**: The base object framework calls this for real source changes. It delegates URL checking to _validated_base_url, account choice to _resolved_account, window math to _widen_window and effective_days, and existing binding lookup to _binding_named.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 7 external calls (__init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 564–638)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Safely expands a live source’s backfill window farther into the past. It refuses to narrow the window because that could leave already-synced pages stranded and never cleaned up.

**Data flow**: It receives the existing binding, the kept streams, provider window defaults, account information, base URL, and new request. For each kept stream it calculates the new cutoff from the original registration anchor, rejects requests that move the cutoff later, builds updated connector configs, and asks the extension context to rewindow and refetch streams whose cutoff changed.

**Call relations**: SourceObjects._apply_owned calls this before making other writes when a submitted spec changes backfill_days. It uses effective_days for consistent cutoff rules.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 640–647)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding and cleans up triggers that watched it. Deleting the stream rows also causes their synced pages to be removed through the broader page cleanup pipeline.

**Data flow**: It finds the binding, removes each stream source row through the extension context, then asks the trigger store to remove all triggers for that binding name.

**Call relations**: The base object system calls this after delete permission has been checked. It uses _binding_named for the binding and _require_triggers for trigger cleanup.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 649–722)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which account or credential a source will use to authenticate with the external provider. It prevents unclear or unsafe choices, such as using another member’s connection.

**Data flow**: It reads connector registry information, active connected accounts, connection ownership, declared credential slots, and optional account_id from the spec. It returns a _ResolvedAccount containing the account handle and optional connection ID, or raises a clear error explaining what the user must connect or configure.

**Call relations**: SourceObjects._apply_owned calls this before registering rows so every stream in the binding uses the same validated authentication path.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 725–729)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Creates the official name for a source trigger from the source binding and conversation ID. This makes a trigger exactly the pair it watches and reports to.

**Data flow**: It takes a binding name and conversation UUID, appends the conversation’s hexadecimal ID, and returns the combined string.

**Call relations**: Trigger listing, lookup, and creation all use this same rule, so applying a trigger under the wrong name can be refused with the exact correct name.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 760–794)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when someone lists source triggers. The rows explain which source is watched, where the trigger reports, who created it, and whether it belongs to the current member.

**Data flow**: It reads reported triggers from the trigger store, rebuilds bindings for summaries, looks up creator emails, then returns OwnedRows with generated owners and fields such as conversation, source, delivery, origin, owner email, and mine.

**Call relations**: The inherited object listing flow calls this. It combines trigger-store data with source summaries so lists are understandable to users.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 796–831)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view of one source trigger. It shows the trigger spec and links to the watched source and, for current-conversation delivery, the conversation it reports to.

**Data flow**: It finds the trigger by name and checks that its stored generation matches the owner. If valid, it returns an ObjectDetail with source, delivery, timestamps, and object links; otherwise it returns None.

**Call relations**: The object system calls this after visibility checks. It relies on _find so the detail matches the exact trigger row currently stored.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 833–847)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns status-like metadata for a source trigger. This is mostly readable context: source, conversation, delivery type, origin, owner email, and whether it is mine.

**Data flow**: It finds the trigger, verifies its generation, looks up the creator’s email, and returns a dictionary of trigger details or None if the row no longer matches.

**Call relations**: The object status path calls this to show trigger information without requiring the caller to inspect the full object detail.

*Call graph*: calls 1 internal fn (_find); 1 external calls (owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 849–886)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new source trigger for the current conversation, or treats an identical existing trigger as a no-op. It refuses edits because a trigger’s identity is the source-conversation pair.

**Data flow**: It computes the expected name from the requested source and current conversation. It rejects wrong names, rejects attempts to change an existing trigger, checks that the source is visible and shared, creates the trigger row, then rechecks the source still exists and cleans up if it vanished during creation.

**Call relations**: The base object framework calls this for trigger applies. It delegates watchability checks to _watchable, naming to trigger_name, storage to the trigger store, and final source existence to _binding_named.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 1 external calls (__init__).


##### `SourceTriggerObjects._watchable`  (lines 888–901)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the caller may create a trigger on a source. Only shared sources can be watched, because private source pages would not reach the conversation being woken.

**Data flow**: It asks the source object store for the named source using the caller’s normal visibility rules. If the source is missing it raises UnknownObject; if it is private it raises a clear error; otherwise it returns successfully.

**Call relations**: SourceTriggerObjects._apply_owned calls this before creating a trigger, so guessed private source names are not exposed and unusable triggers are not stored.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 903–907)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a stored source trigger after confirming it is still the same row the caller meant to delete. This protects against deleting a trigger that changed underneath the request.

**Data flow**: It finds the trigger by name, compares its ID to the owner generation, raises an error if it changed, and otherwise removes it from the trigger store.

**Call relations**: The base object system calls this after delete permission checks. It uses _find for the current row and _require_triggers for the actual removal.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 909–917)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Looks up one reported trigger by its derived object name. It is the trigger equivalent of finding a binding by name.

**Data flow**: It reads all reported triggers from the trigger store, computes each trigger’s derived name, and returns the matching ListedTrigger or None.

**Call relations**: Trigger detail, status, and deletion all call this so they operate on the same name rule used during listing and creation.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 920–1000)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes the conversations that asked to be notified. It is the hook that turns background sync changes into agent alerts.

**Data flow**: It receives a hook payload, confirms it is a page-change batch, maps changed source IDs back to source bindings, groups changes by binding, loads triggers for each binding, filters to shared changes the trigger’s agent may read, writes change logs when possible, and invokes the appropriate conversation or per-page conversation with an alert message.

**Call relations**: The manifest hook system calls this on page-change events. It uses _bindings_from_ext to understand source ownership, _require_triggers to find watchers, _write_change_log to store detailed change lists, and _alert_message to build the message sent to agents.

*Call graph*: calls 4 internal fn (_alert_message, _bindings_from_ext, _require_triggers, _write_change_log); 1 external calls (__init__).


##### `_write_change_log`  (lines 1003–1036)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the detailed list of changed pages into a conversation file as JSON lines, when file storage is available. This keeps large change details out of the alert message while still making them easy for the agent to inspect.

**Data flow**: It receives the extension context, conversation ID, binding, batch stamp, and changes. It converts each change into one JSON line with page reference, stream, title, change type, and timestamp, writes the file under a source-specific directory, prunes old files, and returns the written path or None if files are unavailable.

**Call relations**: on_page_change calls this before invoking an agent. The returned path is included in _alert_message when there are too many changed pages to name directly.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (on_page_change); 1 external calls (dumps).


##### `_disposition`  (lines 1039–1045)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Classifies a page change as added, updated, or removed. This gives alerts and logs simple everyday labels.

**Data flow**: It reads a PageChange. If it is a tombstone it returns removed; otherwise it compares creation time with change time and returns added for first-time rows or updated for later rewrites.

**Call relations**: _write_change_log uses it for each log line, and _stream_counts uses it to summarize counts by stream.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1048–1062)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes a batch of changes by stream and change type. For example, it can say that one stream had three added pages and two updated pages.

**Data flow**: It receives a list of page changes, counts each change’s disposition within its stream, and returns a compact text summary sorted by stream.

**Call relations**: _alert_message calls this to give the agent a quick overview before pointing to exact pages or a change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1065–1084)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to an agent when a watched source changes. The message explains what changed and tells the agent how to inspect the changed pages.

**Data flow**: It receives the binding, authorized changes, and optional log path. For small batches it names each page directly; for larger batches it points to the log file when available; otherwise it tells the agent how to list pages. It combines that detail with stream counts and the binding summary.

**Call relations**: on_page_change calls this right before invoking a conversation. It uses _stream_counts, _page_reference, and _Binding.summary to make the alert useful and readable.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (on_page_change).


##### `_page_reference`  (lines 1087–1091)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference with a short readable label. This helps the alerted agent know what to fetch next.

**Data flow**: It reads the page ID and title from a PageChange, trims the title to a safe length or uses a fallback label, and returns text like a page object reference plus title.

**Call relations**: _alert_message uses this when a batch is small enough to list individual changed pages directly.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1094–1128)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes a provider tenant URL. This prevents unsafe or wrongly shaped URLs from being stored for providers that need customer-specific API hosts.

**Data flow**: It reads the provider’s connector definition and the submitted base URL. Providers with fixed hosts must not receive a URL. Providers needing a tenant URL must match a known HTTPS-only host and path pattern, with no username, password, port, query, or fragment. It returns a normalized HTTPS URL or None.

**Call relations**: SourceObjects._apply_owned calls this before account resolution and registration, so invalid tenant URLs are rejected before any source rows are written.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling and source-change alerting`

A source trigger is like a standing delivery instruction: “when this source changes, send the update to this conversation.” This file defines the database table for those instructions and a small store, `SourceTriggerStore`, that is the only intended way to read or change them.

The important safety rule is that every database query is explicitly limited to the current workspace. The database connection itself is not automatically scoped, so each query adds `workspace_id` by hand. Without that, one workspace could accidentally see or change another workspace’s triggers.

The file also protects agent ownership. When a trigger is created, it checks that the target conversation belongs to the agent that is currently running. This stops one agent from creating a trigger that would later wake another agent’s conversation.

There are two main read paths. `waking` is used when a source changes; it finds every trigger in the workspace for that source binding, even across different agents. `list_reported` is used for member-facing screens; it lists this agent’s triggers and joins in live conversation facts such as who can see the conversation and what label should be shown. That label is read fresh so renamed channels do not display stale names.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a stored date and time has a timezone. If the database gives back a time without timezone information, it marks it as UTC, which is the project’s shared reference time.

**Data flow**: It receives a `datetime` value. If the value already says what timezone it belongs to, it is returned unchanged. If it has no timezone, the function creates an equivalent value tagged as UTC and returns that.

**Call relations**: It is used by `_trigger` while turning database rows into `SourceTrigger` objects. That keeps every trigger object consistent before it is returned by create, waking, or listing reads.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This helper turns one database row into a `SourceTrigger` object that the rest of the code can use safely. It also rejects unknown delivery modes instead of letting bad database data move further through the system.

**Data flow**: It receives a row from the `source_trigger` table. It checks that the `delivery` value is one of the two known choices, copies the row fields into a `SourceTrigger`, and passes stored times through `_utc` so they always have timezone information. The result is a plain Python value object representing one trigger.

**Call relations**: `SourceTriggerStore.create`, `SourceTriggerStore.waking`, and `SourceTriggerStore.list_reported` all call this after reading rows from the database. It is the shared translation step between SQL results and the in-memory trigger objects used by the extension.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives quick access to the workspace that this store is allowed to operate in. It exists because every query in this file must be manually limited to that workspace.

**Data flow**: It reads `workspace_id` from the store’s `ExtensionContext` and returns it. It does not change anything.

**Call relations**: The store’s database methods use this value when they create, delete, or select trigger rows. It is the small guardrail that keeps each operation tied to the current workspace.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This creates a new trigger saying that one conversation wants updates from one source binding. It refuses unsafe or duplicate requests, so a conversation cannot be wired to the wrong agent and cannot watch the same binding twice.

**Data flow**: It receives a conversation id, a source binding name, a delivery mode, and optionally the member who requested it. It finds the currently running agent, checks that the conversation belongs to that agent, then inserts a new row with a fresh id and timestamps. If another request already created the same trigger, it returns no row and raises a clear error. On success, it converts the inserted row into a `SourceTrigger` and returns it.

**Call relations**: This is a public store method used by higher-level subscription code when a user or agent asks to watch a source. Inside, it calls `object_agent_id` to identify the current agent, `uuid4` to make the trigger id, and `_trigger` to return the new database row as a usable object.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller expected. That protects against deleting the wrong row if something changed between reading and removing it.

**Data flow**: It receives the expected `SourceTrigger`. It checks that the trigger still belongs to the currently running agent, then deletes a row matching the workspace, trigger id, agent id, conversation id, and binding. If no row matched, it raises an error saying the trigger changed while removal was attempted. It returns nothing when the delete succeeds.

**Call relations**: This is called by higher-level unsubscribe or cleanup code that already has a trigger object. It calls `object_agent_id` to confirm the current executor and uses SQLAlchemy’s delete operation to remove the matching database row.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This removes every trigger in the current workspace for one source binding. It is used when the source itself is removed, because no conversation should keep waiting on a source that no longer exists.

**Data flow**: It receives a binding string. It deletes all rows in the current workspace whose binding matches that string. It does not return the deleted rows and does not care which agents created them, because the binding belongs to the workspace as a whole.

**Call relations**: Source-removal code calls this when a binding disappears. The method hands the actual deletion to SQLAlchemy’s delete operation and relies on the workspace filter to avoid touching other workspaces.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds all triggers that should wake up when a particular source binding changes. It is the read path for the alert sweep that fans a source update out to interested conversations.

**Data flow**: It receives a binding string. It selects all matching rows in the current workspace, ordered by creation time and id for stable processing. Each row is converted with `_trigger`, and the function returns a tuple of `SourceTrigger` objects.

**Call relations**: Alerting code calls this after a source reports new data. The method uses SQLAlchemy’s select operation to read matching rows and `_trigger` to turn those rows into the objects the alerting code can deliver to.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This lists triggers for member-facing views, along with the visibility and label of the conversation where each trigger fires. It answers not just “what triggers exist?” but also “what should this member-facing surface show about them?”

**Data flow**: It optionally receives a conversation id to narrow the list. It selects triggers in the current workspace for the currently running agent, optionally limited to that conversation, then converts rows into `SourceTrigger` objects. If any triggers were found, it asks the context for live conversation facts, such as audience and surface label. It returns `ListedTrigger` objects only for triggers whose conversations still exist in those facts.

**Call relations**: Member-facing listing code calls this when it needs to show watched sources. The method calls `object_agent_id` to limit the list to this agent, uses SQLAlchemy’s select operation to fetch rows, uses `_trigger` to build trigger objects, and wraps each surviving trigger in `ListedTrigger` with the live conversation details.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).


### Connector ingestion and synchronization
Defines connector contracts, adapts streamed provider records into source sync runs, and maintains synced page state for downstream consumers.

### `core/src/ufo/sources/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as a package, which means code elsewhere can import modules inside it using names like `ufo.sources.something`. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python that the drawer belongs to the organized system of modules. Because the file has no code, it does not create objects, run setup steps, or change program behavior at runtime beyond making imports possible and clear. Without it, depending on the Python version and project layout, imports from `ufo.sources` could be less predictable or fail in some packaging situations.


### `core/src/ufo/sources/connector.py`

`domain_logic` · `sync run`

A connector is the system’s adapter for an outside service, such as email, chat, documents, or repositories. This file says what every connector must provide: a list of streams it can read, a way to fetch records page by page, and a way to turn one record into readable text for recall. Without this shared contract, each provider would speak its own shape of data and the rest of the system would not know how to sync it safely.

The file also defines small value objects that describe streams, pages, pagination styles, and per-partition bounds. A “partition” is one slice of a source, like one repository or one chat channel. The important worker here is `PartitionWalk`. It keeps a JSON cursor map for many partitions at once, like a bookmark for each shelf in a library. It supports streams that arrive oldest-first, newest-first, or with no usable ordering field.

The tricky part is safe resuming. A sync run may stop halfway through. `PartitionWalk` records enough information after each page so the next run can continue from the right place. For newest-first feeds, it keeps both the newest seen item and the current downward window, so newly inserted records do not cause older records to be skipped. The default `Connector.render` turns a raw record into a simple titled page; richer connectors can override it to produce nicer human-readable content.

#### Function details

##### `PartitionWalk.stream`  (lines 233–340)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main walking loop for streams split across many partitions, such as many repositories or channels. It yields pages of records while updating a cursor that says exactly where each partition has been read up to.

**Data flow**: It starts with an incoming cursor string, decodes it into a per-partition bookmark map, then asks the connector for the list of partitions. For each partition, it chooses the right bounds to request next: start fresh, continue downward through a backfill window, or fetch only newer records. As pages arrive, it updates the checkpoint, yields `StreamPage` objects with records, deletes, and the next cursor, and finally cleans up cursors for partitions that disappeared or for unordered streams that completed.

**Call relations**: This function relies on `PartitionWalk._decode` at the start to understand the saved cursor and on `PartitionWalk._encode` whenever it needs to hand back a new cursor. It calls the supplied partition iterator and page factory, wraps their results into `StreamPage` objects, and uses `PartitionBound` to tell the connector what slice of a partition to fetch next.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 343–372)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This turns the stored cursor text back into the partition bookmark map that `PartitionWalk.stream` can use. It treats unknown cursor shapes as empty, but raises an error if a cursor that looks like this walk’s own format is malformed.

**Data flow**: It receives a cursor string or nothing. If the value is missing, invalid JSON, or not a JSON object, it returns an empty map so the walk starts over. If it is a JSON object, it converts each partition entry into either a plain watermark string or a validated in-progress window with `high` and `until`, then returns that map.

**Call relations**: `PartitionWalk.stream` calls this before doing any partition work. Its output decides whether each partition is skipped, resumed from a watermark, resumed inside a backfill window, or read from scratch.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 375–380)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This turns the current partition bookmark map into a JSON string that can be stored as the next cursor. It is how the walk leaves a durable trail for the next sync run.

**Data flow**: It receives a map from partition names to either watermark strings or in-progress window objects. It converts window objects into plain dictionaries, serializes the whole map as sorted JSON, and returns that string.

**Call relations**: `PartitionWalk.stream` calls this after pages and checkpoint changes so every yielded `StreamPage` can carry the latest resume position. The encoded cursor is what lets a later run continue without guessing.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 394–395)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method tells the system which collections, or streams, a connector knows how to sync. A concrete connector implements it to advertise things like messages, documents, issues, or users.

**Data flow**: There are no input records to transform. A connector implementation returns a list of `StreamSpec` objects, where each spec describes one source-side collection, its ID field, cursor field, deletion behavior, and related sync settings.

**Call relations**: During registration, `ConnectedSources._register` calls this method to discover what the connector can provide. The returned stream definitions then guide later sync runs, including which stream to fetch and how to interpret its records.

*Call graph*: called by 1 (_register).


##### `Connector.fetch_page`  (lines 398–413)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None, backfill_after: datetime | None) -> AsyncIterator[list[dict[str, Any]]
```

**Purpose**: This abstract method is the connector’s required record-fetching hook. A concrete connector implements it to contact its outside service and yield records in pages from a given cursor position.

**Data flow**: It receives a stream description, an optional cursor, a resolved credential, a base URL, an optional current user ID to exclude where useful, and an optional backfill floor. The implementation uses those inputs to request provider data and yields either simple lists of records or richer `StreamPage` objects that can also report deletes and a provider cursor.

**Call relations**: This method is the handoff point between the shared sync machinery and provider-specific code. The base class only defines the promise; each real connector supplies the service-specific requests, pagination, filtering, and cursor translation.


##### `Connector.render`  (lines 415–434)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This turns one raw provider record into a title and body that the system can store as readable recall content. It gives every connector a useful default, while document-like connectors can override it for better prose.

**Data flow**: It receives a record dictionary and the stream it came from. It looks for a human-friendly title field such as `title`, `name`, or `subject`; if none exists, it falls back to the record’s primary key. It returns a title plus a body containing a heading and the record serialized as sorted JSON, and it raises an error if it cannot find any usable title or identity.

**Call relations**: The sync adapter uses this kind of rendering after records are fetched, so raw service data can become recallable page text. Internally this default implementation uses JSON serialization to preserve the complete record in a predictable body format.

*Call graph*: 1 external calls (dumps).


### `core/src/ufo/sources/backend.py`

`orchestration` · `source sync run`

A connector knows how to talk to an outside service, such as a ticketing or code hosting tool, and read one named stream of records from it. The rest of the source system does not want to know those connector details. It wants a tidy result: pages to store, records to delete, a cursor for next time, and whether this was a full snapshot. This file is the adapter between those two worlds.

The main piece is `ConnectorBackend`. For each run, it finds the right stream, asks the authentication proxy for a credential, calls the connector, and converts each provider record into a `Page`. A `Page` is the internal shape used later for recall/search. If one record cannot be represented as a page, the file warns and skips only that record instead of failing the whole run.

It also decides how syncing resumes. If the connector provides a next cursor, that cursor is saved. If not, this file creates its own small resume note saying: start again from the original cursor, skip records already consumed, and keep the best timestamp watermark seen so far. This is like putting a bookmark in a long list even when the book itself has no page numbers.

Incremental streams are capped so one run cannot grow forever. Full snapshot streams are not capped, because deleting missing records safely requires seeing the entire collection.

#### Function details

##### `binding_name`  (lines 99–109)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Builds a stable, human-safe object name for a connector binding. The name is based on the provider, account, and optional tenant URL, so the same connection gets the same name wherever the system refers to it.

**Data flow**: It receives a provider name, an account identifier, and an optional base URL. It packages those values in a consistent order, hashes them to a short digest, replaces underscores in the provider name with dashes, and returns a compact name such as a provider prefix plus a short fingerprint.

**Call relations**: This is a standalone naming helper. Other parts of the source system can use it when they need a predictable name for the same connector account, without exposing the full account or URL details.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 147–243)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one connector stream and returns the core sync result for that run. It is the main bridge from provider records to internal pages, cursors, deletions, and snapshot status.

**Data flow**: It receives a source row config, the previously saved cursor, and authentication context. It gets a credential through the auth proxy, finds the requested stream, resolves the base URL, decodes any saved backfill envelope, then reads pages from the connector. Each record is either skipped because it was already consumed in a previous capped slice, converted into a `Page`, or dropped with a warning if invalid. It collects provider-reported deletes, updates the watermark from cursor fields, and returns a `SyncResult` containing stored pages, a next cursor, delete IDs, and whether this run was a full snapshot.

**Call relations**: This is called when the sync driver wants this source row refreshed. During the run it asks `_stream` to find the connector stream, `_decode_cursor` to understand any adapter-made resume state, `_page` to turn records into internal pages, and `_max_str` to advance timestamp-like watermarks. If the run hits the incremental cap, it returns early with either the connector cursor or its own backfill envelope so the next run can continue.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 245–249)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with the requested name inside the connector. A stream definition tells the backend what records exist, how they are keyed, and whether missing records should be treated as deletes.

**Data flow**: It receives a stream name, loops through the connector's declared streams, and returns the matching stream specification. If no stream matches, it raises an error so the sync does not silently read the wrong thing.

**Call relations**: `fetch` calls this near the start of a run. Once `_stream` returns the matching stream specification, the rest of the fetch flow uses that specification to call the connector, build page IDs, read timestamps, and decide snapshot behavior.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 252–269)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes whether a saved cursor is one of this adapter's own backfill resume notes. If it is not, the cursor is treated as connector-owned data and left alone.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, if the string is not JSON, or if the JSON object does not contain the reserved `ufo_backfill` key, it returns nothing. If the reserved key is present, it validates the stored origin, skip count, and watermark, then returns them as a structured envelope. If the reserved envelope is malformed, it raises an error because only this adapter is supposed to write it.

**Call relations**: `fetch` calls this for incremental streams before reading records. The result tells `fetch` whether to pass the cursor straight to the connector or to re-drive an earlier connector cursor and skip records that were already landed in a previous capped run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 271–312)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page | None
```

**Purpose**: Turns one provider record into the internal `Page` shape used by the source system. It also makes sure a bad single record does not block all later records in the same stream.

**Data flow**: It receives the stream specification and one raw record. It builds a stable record reference, asks the connector to render a title and body, extracts created and updated timestamps, and tries to construct a `Page`. If the page is valid, it returns it. If validation fails, it writes a warning with the source reference and validation problem, then returns nothing so the caller can keep syncing the rest.

**Call relations**: `fetch` calls this for each record that should be landed. `_page` delegates record identity to `_record_ref` and timestamp cleanup to `_record_timestamp`, then hands the resulting page back to `fetch` for inclusion in the final `SyncResult`.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 3 external calls (__init__, warn, validation_fault).


##### `_record_timestamp`  (lines 315–346)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Reads and normalizes a timestamp field from a provider record. It accepts common timestamp forms and warns when a connector points to a value that cannot be used as a page time.

**Data flow**: It receives a record, an optional field name, and labels for the connector and stream. If no field is configured or the value is missing, it returns nothing. Otherwise it reads the value, including nested fields when needed, and tries to normalize strings or integer timestamps into the system's standard timestamp format. If the value cannot be normalized, it warns and returns nothing.

**Call relations**: `_page` calls this twice for each record, once for the created time and once for the updated time. Its output becomes optional timestamp fields on the internal `Page`; warnings help connector authors find bad timestamp mappings without stopping the sync.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 349–353)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Chooses the stable ID used to refer to one provider record inside a stream. This lets later updates, refetches, and deletions point to the same internal page.

**Data flow**: It receives a stream specification and one record. If the configured primary key value is a string or integer, it returns that value as text. If the record does not have a usable primary key, it hashes the whole record in a consistent order and returns that hash as a fallback ID.

**Call relations**: `_page` uses this when creating the page source reference. That reference is also the shape expected by delete handling, so `_record_ref` helps keep stored pages and delete notices aligned.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 356–361)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the greatest string value seen so far, usually for a cursor or timestamp-like watermark. This is a small helper for advancing progress only when a newer value appears.

**Data flow**: It receives the current saved string and a candidate value. If the candidate is not a string, it leaves the current value unchanged. If there is no current value or the candidate sorts later than it, it returns the candidate; otherwise it returns the current value.

**Call relations**: `fetch` calls this while reading records from an incremental stream that has a cursor field. The returned watermark is saved into the next cursor path, either directly at the end of a stream or inside the adapter's backfill envelope when a capped run must pause.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/sync.py`

`domain_logic` · `startup registration, periodic sync loop, and downstream page-change replay`

This file is the bridge between outside content and the system’s internal page store. A “source backend” is anything that knows how to read a collection of documents, such as a local folder or an external service connector. The sync driver periodically finds sources that are due, claims them so two workers do not sync the same source at once, asks the right backend to fetch documents, writes changed document bodies into blob storage, and updates page rows in the database. It uses content digests, like fingerprints, so unchanged pages are skipped instead of rewritten. It also treats removals carefully: a full snapshot can mark anything missing as deleted, while an incremental fetch only deletes the specific items it names. Failures are recorded and retried with a growing delay, while intentional skips, such as missing permissions, do not count as errors. The file also defines a page feed used by indexers. That feed reads page changes in database revision order and returns page bodies, so an indexer can safely resume after a cursor. In everyday terms, this file is both the librarian that keeps the shelf current and the checkout log that tells another worker what changed next.

#### Function details

##### `SourceRowConfig.requested_fields`  (lines 73–76)

```
def requested_fields(cls) -> frozenset[str]
```

**Purpose**: This class method tells the system which non-identity settings must still match when the same source is registered again. It separates fields chosen by the caller from fields that are filled in later by the backend or resolver.

**Data flow**: It reads the class-level sets of non-identity fields and resolved fields. It subtracts the resolved ones, then returns the remaining field names as a frozen set.

**Call relations**: This supports source registration and identity decisions for typed backend configs. Backends declare these sets so the core code does not have to guess which config keys matter.


##### `normalize_page_timestamp`  (lines 79–99)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: This function turns provider timestamps into one consistent UTC format. It accepts either numeric Unix-style times or ISO date strings, and rejects ambiguous timestamps that do not say what timezone they are in.

**Data flow**: A timestamp string goes in. The function parses it as seconds or milliseconds if it is numeric, or as an ISO timestamp otherwise, checks that the time can be anchored to a timezone, converts it to UTC, and returns a normalized string with microsecond precision.

**Call relations**: Page.normalize_timestamp calls this when a fetched page is validated. That means backend authors can pass common timestamp formats, while the rest of the system stores one predictable shape.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 115–116)

```
def digest(self) -> str
```

**Purpose**: This property creates a stable fingerprint of a page body. The sync driver uses it to tell whether a document’s contents really changed.

**Data flow**: It reads the page body text, encodes it as bytes, runs SHA-256 hashing on it, and returns a string prefixed with `sha256:`.

**Call relations**: SyncDriver._commit compares this digest with the digest already stored for the page. If the fingerprint is the same, the body does not need to be written again.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 120–123)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: This validator cleans up a page’s created or updated timestamp while the Page model is being built. It lets missing timestamps stay missing, but standardizes present ones.

**Data flow**: A timestamp value or null goes in. Null comes back unchanged; a string is passed through normalize_page_timestamp and comes back as a UTC ISO timestamp string.

**Call relations**: Pydantic calls this while constructing Page objects returned by source backends. It is the local gate that keeps page metadata timestamps consistent.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 155–157)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This constructor records the reason a source stream was skipped without treating it as a true failure. Backends use it for cases like a missing permission, disabled feature, or plan limit.

**Data flow**: A human-readable reason goes in. The exception is initialized with that message and also stores it on `reason` so the sync driver can log it clearly.

**Call relations**: Many connector backends raise this during pagination or stream setup. SyncDriver.run catches it, logs a skipped run, releases the claim, and schedules the next normal attempt without deleting existing pages.

*Call graph*: called by 48 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `validation_fault`  (lines 160–166)

```
def validation_fault(error: ValidationError) -> str
```

**Purpose**: This function turns a validation error into a safe summary for logs. It names which fields failed and why, without including the rejected values.

**Data flow**: A Pydantic ValidationError goes in. The function reads its error list, formats each location and rule type, joins them with semicolons, and returns that text.

**Call relations**: SyncDriver._report_failed uses this when bad provider data or bad source config fails model validation. It helps operators diagnose shape problems without leaking sensitive payloads.

*Call graph*: called by 1 (_report_failed); 1 external calls (errors).


##### `StreamFault.__init__`  (lines 176–178)

```
def __init__(self, reason: str) -> None
```

**Purpose**: This constructor records a backend-authored explanation for provider data that could not be read safely. It is for real stream failures, not intentional skips.

**Data flow**: A reason string goes in. The exception is initialized with that message and stores the same value on `reason` for later reporting.

**Call relations**: Connector code can raise this when a provider response has an unexpected shape. SyncDriver.run treats it as a failed sync and SyncDriver._report_failed includes the sanitized reason.

*Call graph*: called by 1 (_sheet_value_records).


##### `SourceBackend.config_model`  (lines 218–218)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: This protocol property says that every source backend must declare the typed configuration model it expects. That keeps source settings structured instead of being an unchecked dictionary.

**Data flow**: There is no runtime transformation here; an implementing backend returns a Pydantic model class. The sync driver later uses that model to validate the database config before fetching.

**Call relations**: SyncDriver._fetch relies on this contract before calling SourceBackend.fetch. FolderSource and extension backends provide concrete versions.


##### `SourceBackend.fetch`  (lines 220–220)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This protocol method defines the central promise of a source backend: given config, a cursor, and workspace auth context, return the pages currently available or changed. It is the seam where core sync code hands control to folder, connector, or other source implementations.

**Data flow**: Validated source config, the previous cursor, and SourceAuth go in. The backend contacts or reads its source, then returns a SyncResult containing pages, a next cursor, delete notices, and whether the fetch was a full snapshot.

**Call relations**: SyncDriver._fetch calls this after choosing the backend and preparing auth. FolderSource.fetch is the built-in implementation, and external source extensions implement the same shape.


##### `FolderSource.fetch`  (lines 234–245)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This built-in backend reads every file in a local directory and turns each file into a page. It provides a simple source type for local content without needing an external connector.

**Data flow**: A SourceConfig with a root folder goes in, along with an unused cursor and auth. The folder is read in a worker thread, each file becomes a Page with its relative path as the key and title, and the function returns a SyncResult marked as a full snapshot.

**Call relations**: SyncDriver._fetch calls this when the source row’s backend is `folder`. It hands off the actual disk scanning to FolderSource._read, then packages the results for the normal commit path.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 248–255)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: This helper scans a local folder and reads the text contents of every file under it. It deliberately fails if the root folder is missing, so a temporary mount problem does not look like an empty folder and delete everything.

**Data flow**: A Path goes in. The function checks that it is a directory, walks all files below it in sorted order, reads each file as UTF-8 bytes, and returns pairs of relative file path and text.

**Call relations**: FolderSource.fetch calls this through asyncio.to_thread so blocking disk reads do not stall the event loop. Its output becomes Page objects for the sync driver.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 258–278)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None, non_identity_keys: frozenset[str]=frozenset()) -> UUID
```

**Purpose**: This function creates a repeatable database ID for a source row. The same workspace, backend, and identity-defining config produce the same UUID, so restarting the app does not create duplicate sources.

**Data flow**: Workspace ID, backend name, config, optional connection ID, and keys to ignore go in. The function removes non-identity config fields, serializes the remaining identity in sorted order, includes the connection generation if present, and returns a UUID derived from that text.

**Call relations**: register_sources calls this while bootstrapping configured sources. Backends influence the identity by declaring which config fields should not be part of it.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 281–284)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: This function creates a repeatable ID for one page within one source. It lets updates, refetches, and delete notices all point to the same page row.

**Data flow**: A source ID and the page’s source-specific reference go in. The function combines them into a stable string and returns a UUID derived from it.

**Call relations**: SyncDriver._commit uses this for every fetched page and delete reference. That keeps the commit code from depending on database-generated page IDs.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 287–352)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: This startup function makes sure each source listed in configuration has a corresponding database row and is granted to the main agent. It prevents configured sources from needing to be manually inserted.

**Data flow**: A tuple of configured source entries goes in. The function opens a workspace transaction, finds the workspace and main agent, computes each source’s stable ID, skips removed rows, inserts missing source rows, and adds a grant if needed.

**Call relations**: This runs separately from the polling sync loop, usually at boot. It uses source_row_id for stable identity and writes rows that SyncDriver later claims and syncs.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 370–381)

```
def _rescheduled(claimed: ClaimedSource, when: datetime) -> sa.Case[datetime]
```

**Purpose**: This helper decides what `next_sync_at` should become when a claimed sync finishes. It protects a resync request made during the run from being accidentally pushed into the future.

**Data flow**: A claimed source and a proposed future time go in. It returns a SQL expression: use the proposed time only if the row’s current schedule was not changed after the claim began; otherwise keep the newer requested schedule.

**Call relations**: SyncDriver._write, SyncDriver._release, and SyncDriver._skip use this when freeing a source claim. It keeps normal completion, failure backoff, and skipped runs from overwriting a fresh request.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 384–389)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: This helper builds safe metric tags that identify which provider and stream a sync result belongs to. Tags are short labels used for logs and counters.

**Data flow**: A claimed source goes in. The function reads the backend name and the `stream` config value if it is a string, then returns them in a dictionary.

**Call relations**: SyncDriver.run uses it when logging skips, and SyncDriver._report_ok and SyncDriver._report_failed use it for success and failure telemetry. It delegates config lookup to _config_value.

*Call graph*: calls 1 internal fn (_config_value); called by 3 (_report_failed, _report_ok, run).


##### `_config_value`  (lines 392–394)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: This helper safely reads a string value from a source’s config. If the key is missing or not a string, it returns an empty string instead of passing along an unexpected type.

**Data flow**: A claimed source and config key go in. The function looks up the key, checks whether the value is a string, and returns either that string or an empty string.

**Call relations**: _stream_tags uses this for the stream tag. SyncDriver._report_ok and SyncDriver._report_failed also use it for account IDs in logs.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `SyncDriver.candidate_workspaces`  (lines 428–448)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This method finds which workspaces have at least one source ready to sync. It lets a dispatcher avoid opening workspace-specific work for tenants that have nothing due.

**Data flow**: It reads the current time, opens an owner-level database transaction, searches source rows whose schedule has arrived and whose claim is free or expired, and returns distinct workspace IDs.

**Call relations**: A higher-level scheduler can call this before binding and running the driver per workspace. It is a lightweight precheck before SyncDriver.run does actual claiming and syncing.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 450–469)

```
async def run(self) -> None
```

**Purpose**: This is the main per-workspace sync loop. It claims due sources, fetches their documents, commits changes, and handles skipped or failed streams without stopping the whole batch.

**Data flow**: It creates a unique claim token, asks _claim_due for sources it owns for this run, and processes each one. Successful fetches flow through _fetch and _commit; skipped streams go to _skip; failures are classified, logged with _report_failed, given a retry time by _error_backoff, and released by _release.

**Call relations**: This method ties together nearly all of SyncDriver’s private helpers. It is called by the system’s scheduled job runner after a workspace has been selected.

*Call graph*: calls 8 internal fn (_claim_due, _commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); 4 external calls (suppress, now, log, uuid4).


##### `SyncDriver._claim_due`  (lines 471–521)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: This method reserves a small batch of due source rows for one worker. The claim acts like a temporary “I’m working on this” note so concurrent workers do not duplicate the same sync.

**Data flow**: A claim token goes in. The method finds due, unremoved, unclaimed-or-expired sources, optionally locks them in PostgreSQL, writes the claim and lease expiry to the database, and returns ClaimedSource objects with the data needed for fetching.

**Call relations**: SyncDriver.run calls this first. The returned claim is later checked by _write, _release, and _skip before they update the source row.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 523–542)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: This method prepares and calls the correct backend for one claimed source. It validates the stored config and builds the authentication context without the core code directly holding provider tokens.

**Data flow**: A ClaimedSource goes in. The method finds the backend by name, validates the source config with that backend’s model, resolves optional identity information, binds optional source credentials, creates SourceAuth, and returns the backend’s SyncResult.

**Call relations**: SyncDriver.run calls this after claiming a source. It hands off to the backend’s fetch method, which may be FolderSource.fetch or an extension connector implementation.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 544–585)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: This method compares fetched pages with what is already stored and prepares only the needed writes. It avoids rewriting unchanged bodies and tracks metadata-only changes separately.

**Data flow**: A claimed source and SyncResult go in. It loads prior pages, computes stable page IDs, compares digests and browse metadata, writes new body blobs for changed content, builds lists of changed pages, metadata updates, fetched IDs, and delete IDs, then calls _write and reports success.

**Call relations**: SyncDriver.run calls this after _fetch succeeds. It uses _prior_pages to know the old state, page_id_for for stable IDs, _write for the database transaction, and _report_ok for telemetry.

*Call graph*: calls 4 internal fn (_prior_pages, _report_ok, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 587–619)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: This method reads the current stored state for all pages belonging to a source. The sync driver needs this snapshot to decide what actually changed.

**Data flow**: A source ID goes in. The method queries page rows for that source and returns a dictionary keyed by page ID, with each value containing digest, tombstone status, and browse metadata.

**Call relations**: SyncDriver._commit calls this before processing fetched pages. Its result is the comparison table used to skip unchanged content or notice metadata-only updates.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 621–743)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: This method performs the database side of a successful sync. It upserts changed pages, updates metadata, tombstones deleted pages, and releases the source claim with the next normal sync time.

**Data flow**: A claimed source, next cursor, changed pages, metadata updates, fetched IDs, delete IDs, and snapshot flag go in. Inside one workspace transaction it verifies the claim, writes changed page rows, applies metadata-only updates, marks explicit or snapshot-missing pages as tombstoned, updates live page subjects if needed, updates the source cursor and schedule, clears the claim, and returns how many pages were tombstoned.

**Call relations**: SyncDriver._commit calls this after deciding what changed. It uses _rescheduled so a newer resync request is not lost, and its page writes create the revisions later read by CorePageFeed.pages_changed_since.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 745–757)

```
def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int) -> None
```

**Purpose**: This method logs a successful source sync with useful counts. It records how many pages were fetched, written, and tombstoned.

**Data flow**: A source plus fetched, written, and tombstoned counts go in. The method builds provider, stream, and account labels, then emits a structured success log while suppressing logging errors.

**Call relations**: SyncDriver._commit calls this after _write completes. It uses _stream_tags and _config_value to keep telemetry consistent with failure reporting.

*Call graph*: calls 2 internal fn (_config_value, _stream_tags); called by 1 (_commit); 2 external calls (suppress, log).


##### `SyncDriver._error_backoff`  (lines 759–767)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: This method computes how long to wait before retrying a failing source. The wait doubles after repeated failures, up to a maximum, so a broken provider is not hammered every minute.

**Data flow**: A claimed source and the current time go in. It increments the consecutive error count, calculates the capped backoff delay, and returns both the new count and the next retry timestamp.

**Call relations**: SyncDriver.run calls this when _fetch or _commit raises an exception. The result is passed to _report_failed for logging and to _release for database rescheduling.

*Call graph*: called by 1 (run); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 769–816)

```
def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This method records a failed sync in logs and metrics while avoiding sensitive data. It gives operators enough context to find the failing provider stream without copying provider payloads or credentials into telemetry.

**Data flow**: A source, exception, cursor-reset flag, error count, and next retry time go in. The method classifies the exception, builds a sanitized provider fault for HTTP errors, StreamFaults, or validation errors, logs a structured failure event, and emits a failure counter metric.

**Call relations**: SyncDriver.run calls this on any real failure. It uses validation_fault for Pydantic validation errors, _stream_tags and _config_value for labels, and then run continues by calling _release.

*Call graph*: calls 3 internal fn (_config_value, _stream_tags, validation_fault); called by 1 (run); 4 external calls (suppress, isoformat, emit_metric, log_error).


##### `SyncDriver._release`  (lines 818–844)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: This method frees a source claim after a failed sync and schedules the retry. It also clears a bad cursor when the provider says the old cursor has expired.

**Data flow**: A claimed source, cursor-reset flag, error count, and retry time go in. The method updates the source row by restoring or clearing the cursor, setting the next sync time with _rescheduled, storing the error count, clearing claim fields, and updating the timestamp.

**Call relations**: SyncDriver.run calls this after _report_failed. It is the failure-path counterpart to _write, which releases the claim after success.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 846–869)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: This method frees a source claim after a backend says the stream should be skipped rather than failed. It keeps existing pages untouched and schedules the next normal attempt.

**Data flow**: A claimed source goes in. The method computes the normal next sync time, updates the source row to reset error count, clear the claim, and preserve the cursor, using _rescheduled to avoid overwriting newer requests.

**Call relations**: SyncDriver.run calls this when it catches StreamSkipped. This path deliberately does not call _commit, so no snapshot deletion can happen for an unreadable stream.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 909–909)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This protocol method defines how an indexer asks for page changes after a cursor. It is the contract that lets downstream systems replay source changes safely.

**Data flow**: A cursor and requested limit go in. An implementation returns a PageBatch containing ordered PageChange items and the next cursor to resume from.

**Call relations**: CorePageFeed.pages_changed_since provides the core implementation. Extensions read through this interface instead of querying page tables and blob storage directly.


##### `page_cursor`  (lines 912–921)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: This function parses the page feed cursor format. The cursor identifies a position in the ordered stream using a revision number and page ID.

**Data flow**: An object goes in. The function requires it to be a string shaped like `revision|uuid`, validates the revision and UUID, and returns them as an integer and UUID object; invalid input raises ValueError.

**Call relations**: CorePageFeed.pages_changed_since calls this when a caller provides a cursor. The parsed values become the database filter for “changes after this exact point.”

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 933–991)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: This method returns a bounded batch of page changes for an indexer, including page bodies for live pages and empty bodies for tombstones. It lets an indexer resume reliably from its last cursor.

**Data flow**: A cursor and limit go in. The method builds a query ordered by revision and page ID, applies the cursor filter if present, caps the limit, reads matching page rows, fetches each live body from blob storage, converts rows into PageChange objects, and returns them in a PageBatch with the next cursor.

**Call relations**: This is the concrete PageFeed used by extension contexts. It depends on page_cursor for resume parsing and reads the rows written by SyncDriver._write.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).


### Synced page access
Exposes synchronized source pages as read-only workspace objects that users can list and inspect while admins can forget stale pages.

### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling for page object operations`

A “page” here is one document that was copied into the workspace by a content sync process from an outside source, such as an issue tracker or repository. This file is the public object-facing layer for those pages. It is like a library catalogue desk: people can browse the catalogue and read an item, but they cannot rewrite the book because the book came from somewhere else.

The file defines what a page looks like through PageSpec: source information, stream, title, timestamps, visibility subject, content digest, blob reference, and a bounded body preview. It also defines an internal _Page wrapper that turns database-style page records into object names, summaries, fields, links back to the source that synced them, and full specs.

PageObjects is the main store for the page object kind. Listing asks the extension context for pages the current reader is allowed to see, joins them with their source metadata, and returns rows suitable for filtering and ordering. Getting a page reads the body from blob storage, but only up to 65,536 UTF-8 bytes, then re-checks that the page has not changed while it was being read. Create and update are refused because pages are produced only by the sync driver. Delete means “forget”: it tombstones the synced page and is allowed only for workspace admins, so derived index data can be cleaned up safely.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool request has an ExtensionContext attached. Page operations need that context to read sources, read pages, and forget pages.

**Data flow**: It receives a ToolContext. If the context contains an extension context, it returns it. If not, it stops the operation with a RuntimeError, because the page object code cannot safely continue without access to the extension’s page and source services.

**Call relations**: PageObjects._pages calls it before loading source and page records, PageObjects.get calls it before re-checking page state, and PageObjects.delete calls it before forgetting a page. In each case it acts as the doorway from the generic tool request into this extension’s storage and permissions layer.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This function turns a page timestamp into a consistent UTC text value. It accepts either a timestamp supplied by the outside provider or, if that is missing, the local row timestamp.

**Data flow**: It takes a possible provider timestamp string and a database row datetime. If the provider timestamp is missing, it uses the row datetime and adds UTC if needed. If the provider timestamp is present, it parses it and rejects it if it is invalid or has no timezone. The result is always an ISO-formatted UTC timestamp with microseconds.

**Call relations**: _Page.spec uses it when building the full page details, and _Page.fields uses it when building listable fields. This keeps timestamps shown in lists and detailed reads in the same format.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the object name for a page. In this system, a page is named by its UUID, which is the stable row id owned by the sync driver.

**Data flow**: It reads the _Page id value and converts it to text. Nothing else changes. The output is the name callers use when listing, getting, or deleting the page object.

**Call relations**: PageObjects.list uses this name when returning page rows, and PageObjects._find compares it with the requested name. It is the bridge between the internal UUID and the object API’s string names.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds a link from a page back to the source binding that synced it, when that source can be named. The link helps readers understand where the page came from.

**Data flow**: It looks at the page’s source_name. If there is no source name, it returns no links. If there is one, it returns a single ObjectLink with relation “synced_by” pointing at the corresponding source object.

**Call relations**: PageObjects.get includes these links in the ObjectDetail it returns. The function hands off to ObjectLink and ObjectRef to express the relationship in the standard object format.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This builds the full public description of a page, including its metadata and the body text that was read from blob storage. It is the final shape returned by object_get.

**Data flow**: It receives body text and a flag saying whether that body was cut short. It combines those with the _Page’s source, stream, title, timestamps, visibility subject, digest, and blob reference. It normalizes timestamps through _page_timestamp and returns a PageSpec.

**Call relations**: PageObjects.get calls this after reading the body and verifying that the page is still current. The PageSpec it returns becomes the spec inside the ObjectDetail sent back to the caller.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short human-readable one-line summary for a page. It is meant for list results, where people need a quick clue about what each page is.

**Data flow**: It reads the page title, source backend, stream, and visibility subject. It formats them into one sentence-like string and trims it to the configured summary length. The page itself is not changed.

**Call relations**: PageObjects.list uses this summary when creating ObjectRow entries. It helps the list view show useful context without fetching the full page body.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This returns the searchable and sortable metadata for a page list row. These fields let callers filter or order pages without reading every page body.

**Data flow**: It reads the page’s source id, backend name, stream, title, and timestamps. It normalizes the timestamps through _page_timestamp and returns them in a plain dictionary of JSON-friendly values.

**Call relations**: PageObjects.list places these fields into each ObjectRow. PAGE_OBJECT also declares these as list fields, so this function supplies the data behind that advertised filtering and ordering surface.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a page of synced pages that the current caller is allowed to see. It is used when someone browses page objects rather than opening a specific page.

**Data flow**: It receives the request context and a list query containing things like filters, ordering, or pagination. It asks _pages for all visible page wrappers, turns each into an ObjectRow with name, summary, and fields, then passes those rows through object_page to apply the query shape. The result is an ObjectPage.

**Call relations**: This is one of the main methods used by the object system for the page kind. It relies on _pages to do the permission-aware loading, then hands the rows to the shared object_page helper so page listing behaves like other object kinds.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This reads one synced page by name and returns its full details, including a safely bounded body. It protects callers from huge blobs and protects correctness by checking that the page did not change while it was being read.

**Data flow**: It receives the request context and page name. It finds the visible page, opens the page body from blob storage, reads at most 65,537 bytes so it can tell whether the 65,536-byte limit was exceeded, and decodes a valid UTF-8 body. It then asks the extension for the current readable state of that page. If the page disappeared or changed during the read, it returns None. Otherwise it returns an ObjectDetail containing the PageSpec, creation and update times, and source link.

**Call relations**: The object system calls this when a user asks for one page. It first uses _find, then uses _require_ext and the ToolContext’s source_reader to re-check access and freshness. It calls _Page.spec and _Page.links indirectly through the page wrapper to produce the final object detail.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for page objects. Pages are read-only projections from synced data, so there is no apply progress or lifecycle status to expose here.

**Data flow**: It receives the context, object name, and optional expected generation, but does not read or change anything. It always returns None.

**Call relations**: The object framework can ask any object store for status. For this page kind, the answer is deliberately empty because create and update are not supported and synced pages do not have a user-driven status flow.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update a page object. Pages are authored by the external source and landed by the sync driver, not written by users through this object API.

**Data flow**: It receives a target name, a desired PageSpec, an optional old PageSpec, and an optional generation check. Instead of storing anything, it raises VerbNotSupported with a message explaining that pages come from syncing a registered source.

**Call relations**: The object framework calls apply for create or update-style operations. This implementation stops that path immediately, ensuring all page content continues to come only through the content-sync driver.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This forgets one synced page, but only if the speaker is a workspace admin. Forgetting tombstones the page so the existing page-change pipeline can remove derived index state cleanly.

**Data flow**: It receives the request context and page name. It first asks whether the speaker is an admin. If not, it raises AdminRequired. If the user is an admin, it finds the page by name. If no page exists, it raises a ValueError. If found, it calls the extension context to forget that page id.

**Call relations**: The object system calls this for delete operations on page objects. It uses _find to resolve the name into a page and _require_ext to reach forget_page. The permission check comes before the lookup so non-admins cannot use delete as a probing tool.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This looks up one visible page by its object name. It is a helper for operations that need a single page rather than a list.

**Data flow**: It receives the request context and a name string. It asks _pages for the tuple of pages visible to the current reader, scans for the first one whose UUID-based name matches, and returns that _Page. If none match, it returns None.

**Call relations**: PageObjects.get uses it before reading a body, and PageObjects.delete uses it before forgetting a page. It depends on _pages, so the lookup automatically respects the same visibility rules as listing.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This loads the synced pages visible to the current reader and enriches them with source information. It is the shared data-gathering step behind listing and finding pages.

**Data flow**: It receives the request context, gets the ExtensionContext, loads all sources, and builds lookup tables from source id to backend name and, when possible, to the source object name. It then asks for source pages using the current source_reader, which represents the caller’s allowed visibility. Each returned record is wrapped as a _Page with page metadata, source metadata, timestamps, digest, body reference, and optional source name. The result is a tuple of _Page objects.

**Call relations**: PageObjects.list calls this to build list rows, and PageObjects._find calls it to resolve a single name. It calls ConnectorSourceConfig.model_validate and binding_name so page details can link back to known connector source objects when the source configuration is valid.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### Search indexing and retrieval
Chunks synced text, embeds or keyword-indexes it through the common indexing contract, and serves searches through the default backend.

### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting search indexing and retrieval`

This is the project’s default memory search engine. It solves a practical problem: once the system has broken documents or other owned content into searchable chunks, those chunks need somewhere to live and a reliable way to find them again. Without this file, the default setup would have no built-in way to index or retrieve chunked text.

The file supports two database worlds. In Postgres, it uses database-native full-text search and pgvector, a Postgres extension for comparing numeric meaning-vectors. In SQLite, it uses FTS5, SQLite’s full-text search table, and does vector comparison in Python by scanning rows and calculating cosine similarity. Think of it like one search desk with two sets of tools: a heavy-duty library catalog for production-like Postgres, and a smaller desktop card catalog for local SQLite use.

The main class, DefaultIndex, receives a transaction opener from the surrounding system. Each operation opens a workspace-scoped database transaction, checks which database type it is talking to, and chooses the right SQL. It can insert or update chunks, remove all chunks for an owner, check whether an owner has chunks, prune old chunks after re-chunking, and run lexical or vector searches. The rest of the project sees neutral Chunk, Hit, and IndexScope objects, while database-specific details such as halfvec, tsvector, and FTS5 stay hidden inside this file.

#### Function details

##### `pgvector_literal`  (lines 31–32)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text form expected by pgvector, the Postgres vector-search extension. It is used when embeddings need to be sent to Postgres SQL.

**Data flow**: It receives a tuple such as several floating-point embedding values → converts each value to a plain float string → returns one bracketed string like a vector literal that Postgres can cast into its vector type.

**Call relations**: DefaultIndex.upsert uses this before storing an embedding in Postgres, and DefaultIndex.vector uses it before asking Postgres to compare a query embedding against stored embeddings.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 35–43)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Compares two numeric vectors and returns how similar their directions are. This is used for SQLite vector search, where the database does not provide the same vector-search machinery as Postgres.

**Data flow**: It receives two equal-length tuples of numbers → computes each vector’s length with square roots, then compares them by their dot product → returns a score between directions, or 0.0 if either vector has no length.

**Call relations**: DefaultIndex.vector calls this after reading candidate SQLite rows, so it can rank chunks by semantic similarity in Python.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 46–47)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Converts an embedding from Python numbers into compact raw bytes for SQLite storage. SQLite stores the vector as a blob because it has no special vector column type here.

**Data flow**: It receives a tuple of floating-point numbers → packs them as 32-bit floats in a byte string → returns bytes ready to save in the SQLite chunk table.

**Call relations**: DefaultIndex.upsert calls this only on the SQLite path, just before writing a chunk with an embedding to the database.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 50–51)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Converts a stored SQLite embedding blob back into Python numbers. This lets the code compare saved vectors with a new query vector.

**Data flow**: It receives raw bytes from the SQLite database → interprets every four bytes as one 32-bit float → returns a tuple of floating-point values.

**Call relations**: DefaultIndex.vector calls this for each SQLite row that has an embedding, then passes the result into cosine to score similarity.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 54–63)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object, which is the common search-result shape returned to the rest of the system. It hides the small differences between SQL result rows and the project’s neutral search result type.

**Data flow**: It receives a database row plus a score → copies the chunk identity, owner information, subject, order, text, and score into a Hit → returns that Hit to the search method.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both call this after they have scored rows, so both search styles return the same kind of result object.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 171–208)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing chunks with the same digest. This is how the search index stays current when content is chunked or re-chunked.

**Data flow**: It receives a tuple of Chunk objects → if the tuple is empty, it does nothing; otherwise it opens a database transaction, checks whether the connection is Postgres or SQLite, and writes each chunk with its text and optional embedding → the database ends up with current chunk rows, and on SQLite the matching full-text-search row is also refreshed.

**Call relations**: When storing embeddings, it hands Postgres embeddings through pgvector_literal and SQLite embeddings through pack_embedding. It is one of the main write paths used by the default index backend after chunks have been prepared elsewhere.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 210–217)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every indexed chunk belonging to one owner. This is used when an owner’s indexed content should disappear completely.

**Data flow**: It receives an IndexScope, which identifies an owner by kind and id → opens a transaction and deletes matching rows → in Postgres it removes rows from the chunk table, while in SQLite it first removes matching full-text-search rows and then removes the chunk rows.

**Call relations**: DefaultIndex.prune calls this when there is no keep-set, meaning no chunks should remain for that owner. It also serves as the full-scope removal operation for the backend.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 219–222)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the index already contains any chunks for a given owner. This helps other code decide whether indexing work is needed or whether stored search data exists.

**Data flow**: It receives an IndexScope with owner kind and owner id → opens a transaction and asks the chunk table for one matching row → returns true if a row exists, otherwise false.

**Call relations**: This method stands as a small read check inside the DefaultIndex backend. It does not call local helpers, and other parts of the indexing flow can use it before deciding what work to do next.


##### `DefaultIndex.prune`  (lines 224–238)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks for an owner while keeping a specified set of current chunk digests. This is important after content is re-chunked, because otherwise stale chunks would still appear in search results.

**Data flow**: It receives an IndexScope and a frozen set of chunk digests to keep → if the keep-set is empty, it deletes the whole scope; otherwise it opens a transaction and removes only chunks not in the keep-set → the index is left with only the owner’s current chunks.

**Call relations**: When there is nothing to keep, it hands off to DefaultIndex.delete. Otherwise it runs database-specific prune SQL, including full-text-search cleanup on SQLite before deleting chunk rows.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 240–276)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by matching words in their text. This is the classic keyword-search path, useful when the user’s query contains terms that should appear in the result text.

**Data flow**: It receives a query string, allowed subjects, an owner kind, and a maximum result count → returns nothing if there are no subjects, or if the cleaned query is empty; otherwise it opens a transaction and runs the database’s full-text search → converts each matching row into a Hit with a relevance score.

**Call relations**: After the database returns scored rows, it calls _hit to turn each row into the project’s common result object. On Postgres it uses Postgres text-search scoring, while on SQLite it builds a safe FTS5 match query from the query terms.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 278–311)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by embedding similarity, meaning it looks for text whose stored numeric meaning-vector is close to the query vector. This supports semantic search, where wording can differ but meaning is similar.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit → returns nothing if the embedding or subjects are empty; otherwise it opens a transaction and chooses the database-specific search path → returns the best matching chunks as Hit objects ordered by similarity.

**Call relations**: On Postgres it sends the query through pgvector_literal and lets the database score nearby vectors, then calls _hit for results. On SQLite it reads candidate rows, uses unpack_embedding to restore each stored vector, uses cosine to score it in Python, sorts the scores, and then calls _hit for the top results.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 314–324)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the larger system and registers the default index backend. This is how the core system learns that the backend named "default" can be created from this file.

**Data flow**: It takes no input → creates an IndexBackendSpec whose factory builds a DefaultIndex using the system-provided transaction opener → wraps that spec in a Manifest with the extension name and version, then returns it.

**Call relations**: When the extension system reads this module, this function provides the registration object. It constructs the Manifest and IndexBackendSpec objects that connect the name "default" to DefaultIndex.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/indexing.py`

`domain_logic` · `indexing and re-indexing`

Search works best when long notes or pages are split into smaller, meaningful pieces. This file provides that shared indexing recipe without tying it to any particular database or search engine. Think of it like a food-prep station: it cuts the raw text into portions, labels each portion, and hands the prepared pieces to another system that stores and searches them.

The file defines small value objects such as Chunk, Hit, and IndexScope. A Chunk is one searchable piece of text. A Hit is a search result. An IndexScope identifies all chunks that belong to one owner, such as one memory item or one page.

It also defines two plug-in contracts. IndexBackend is the storage/search side: it knows how to save chunks, delete them, prune old ones, and run keyword or vector searches. EmbedClient is the embedding side: it turns text into a vector, which is a list of numbers that represents meaning for similarity search.

The main workflow is chunk_embed_upsert. It chunks one body of text, embeds each chunk, saves the embedded chunks, then removes any older chunks for the same owner that no longer belong. That last pruning step matters: without it, edited or emptied text could leave stale search results behind.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the required method a search backend must provide to save chunks. “Upsert” means save this item whether it is new or already exists, updating the old copy if needed.

**Data flow**: It receives a group of Chunk objects, each holding text, ownership details, and usually an embedding. The backend implementation stores or refreshes those chunks in its own index. Nothing is returned, but the searchable index changes.

**Call relations**: chunk_embed_upsert calls this after text has been split and embedded. This file only states that the method must exist; a real backend elsewhere supplies the database or search-engine behavior.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the required method a search backend must provide to remove all chunks for one owner. It is used when an indexed item should disappear from search completely.

**Data flow**: It receives an IndexScope, which says which owner kind and owner id to delete. The backend removes matching stored chunks. It returns nothing, but the index is changed by deletion.

**Call relations**: This method is part of the backend contract. It is not called inside this file, but other indexing or cleanup code can call it when an owner is removed.


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the required method a search backend must provide to remove old chunks that no longer match the current text. It prevents search results from showing text that has already been edited away.

**Data flow**: It receives an IndexScope naming one owner and a set of chunk digests to keep. The backend deletes that owner’s chunks whose digests are not in the keep set. It returns nothing, but stale index entries are removed.

**Call relations**: chunk_embed_upsert calls this after saving the current chunks. If the body is empty, the keep set is empty, so pruning removes all chunks for that owner.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the required method a search backend must provide to answer whether an owner already has indexed chunks. It lets callers decide whether indexing work is needed or whether an item is already searchable.

**Data flow**: It receives an IndexScope identifying one owner. The backend checks its stored index and returns true or false. It does not change the index.

**Call relations**: This method is part of the backend contract. It is not used inside this file, but higher-level indexing code can call it before deciding what to index.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the required method a search backend must provide for keyword-style search. Lexical search means matching the actual words in the query against stored text.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches matching chunks and returns Hit objects, each describing a found chunk and its score.

**Call relations**: This method is part of the search backend contract. Code outside this file calls it when it wants word-based retrieval rather than meaning-based vector retrieval.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the required method a search backend must provide for similarity search using embeddings. An embedding is a list of numbers that represents the meaning of text, so this search can find related wording rather than exact word matches.

**Data flow**: It receives a query embedding, a set of allowed subjects, an owner kind, and a limit. The backend compares that embedding with stored chunk embeddings and returns the best Hit objects. It does not modify stored data.

**Call relations**: This method is part of the backend contract. Search orchestration code elsewhere calls it after producing an embedding for the user’s query.


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the required method an embedding provider must offer. It turns one or more pieces of text into numeric vectors that can be used for meaning-based search.

**Data flow**: It receives a tuple of text strings. The implementation sends or computes those texts through an embedding model and returns one vector per input text, in the same order.

**Call relations**: chunk_embed_upsert calls this after TextChunker has produced chunks. This file defines the shape of the call, while an extension or service supplies the actual embedding model.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared indexing workflow for one body of text. It creates chunks, embeds them, saves them, and removes old chunks that no longer belong.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner information, a subject, and the raw body text. First it asks the chunker to split the text into Chunk objects. If there are chunks, it asks the embedding client for vectors and copies each vector into its matching chunk, then sends those chunks to the index backend. Finally it tells the backend to keep only the current chunk digests for that owner, which removes stale chunks. It returns nothing, but the index is brought into line with the current body text.

**Call relations**: This function ties together the three main parts of the file. It calls TextChunker through chunker.chunk, hands chunk text to EmbedClient.embed, saves the results through IndexBackend.upsert, and finishes by calling IndexBackend.prune with an IndexScope for the owner.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This is the public method that turns one text body into labeled Chunk objects. It gives each piece enough identity information to be stored, searched, and traced back to its owner.

**Data flow**: It receives raw text plus owner kind, owner id, and subject. It asks _slices to produce the actual text pieces, numbers them in order, creates a stable digest for each piece with _digest, and returns a tuple of Chunk objects without embeddings.

**Call relations**: chunk_embed_upsert uses this as the first step of indexing. Internally it depends on _slices to decide the piece boundaries and _digest to create a unique content-based identifier for each piece.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This internal method decides how to cut raw text into index-sized text pieces. It keeps small text as one piece, splits large text into readable parts, adds some overlap, and enforces a character limit.

**Data flow**: It receives raw text. If the text is blank, it returns no pieces. If it is already short enough by word count, it trims it and applies the character cap. Otherwise it recursively splits by natural delimiters, merges small neighboring pieces, adds context overlap between chunks, then character-caps each final piece. It returns a list of strings.

**Call relations**: TextChunker.chunk calls this to get the text for each Chunk. It coordinates several helper methods: _count_words, _recursive_split, _greedy_merge, _apply_overlap, and _cap_by_chars.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This internal method estimates how large a piece of text is. It supports both space-separated languages and CJK text, where Chinese, Japanese, and Korean often do not use spaces between words.

**Data flow**: It receives text. It removes whitespace to see whether anything remains. If much of the text is CJK characters, it treats non-whitespace characters as the size measure. Otherwise it counts runs of non-space text like ordinary words. It returns an integer size estimate.

**Call relations**: _slices uses this to decide whether text is short enough. _recursive_split uses it to decide whether a piece still needs more splitting. _greedy_merge uses it to decide whether two neighboring pieces can safely be joined.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This internal method enforces the hard maximum character length for a chunk. It is a safety net for unusually long text that word-based splitting did not make small enough.

**Data flow**: It receives one text string. If it fits within max_chars, it returns that text as a one-item list, unless it is empty. If it is too long, it cuts it into overlapping character windows so neighboring pieces still share a little context. It returns a list of non-empty strings.

**Call relations**: _slices calls this for both short bodies and final overlapped pieces. It is the last guardrail before Chunk objects are created.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This internal method breaks large text using increasingly smaller natural boundaries. It tries paragraphs first, then lines, then sentences, then punctuation, and finally plain whitespace.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split the text on that level’s delimiters. If splitting does not help, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece further. It returns a list of smaller strings.

**Call relations**: _slices calls this when the whole text is too large. It calls _split_at_delimiters to cut on natural separators, _count_words to test piece size, and _split_on_whitespace as the fallback when no delimiter level remains.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This internal helper cuts text at the earliest matching delimiter from a given set. A delimiter is a boundary marker such as a paragraph break, period, comma, or similar punctuation.

**Data flow**: It receives text and a tuple of delimiters. It repeatedly finds the earliest next delimiter, takes everything through that delimiter as one piece, and continues with the rest. It drops pieces that are only whitespace and returns the remaining list.

**Call relations**: _recursive_split calls this while trying each delimiter level. It supplies the raw cut points before _recursive_split decides whether any piece needs further splitting.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This internal fallback splits text when natural punctuation boundaries are not enough or do not exist. It prevents very long unbroken text from becoming one oversized chunk.

**Data flow**: It receives text. If normal word runs are available, it groups them into blocks of target_words. If there are no usable word runs, or there is one very long run, it cuts the raw text into fixed-size character pieces based on the target size. It returns non-empty strings.

**Call relations**: _recursive_split calls this only after delimiter-based splitting has run out of options. It is the last-resort cutter.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This internal method joins neighboring pieces when they are too small on their own. It makes chunks closer to the target size so the search index is not filled with tiny fragments.

**Data flow**: It receives a list of text pieces. It walks through them in order, adding the next piece to the current one if the combined size is still within a generous limit. When adding another piece would make it too large, it saves the current piece and starts a new one. It returns the merged list.

**Call relations**: _slices calls this after recursive splitting. It relies on _count_words to decide whether a combined piece is still an acceptable size.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This internal method adds a little context from the end of each chunk to the start of the next one. This helps search because an idea that crosses a boundary is less likely to be lost.

**Data flow**: It receives a list of chunk strings. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise it keeps the first chunk as-is, then prefixes each later chunk with trailing context from the previous chunk. It returns the overlapped list.

**Call relations**: _slices calls this after pieces have been split and merged. It uses _trailing_context to choose the text to carry forward and itertools.pairwise to look at each previous/current pair.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This internal helper chooses the overlap text taken from the end of a chunk. It tries to include useful recent words without starting awkwardly in the middle of an earlier sentence when it can avoid it.

**Data flow**: It receives one chunk of text. If the chunk is not longer than the requested overlap, it returns an empty string because copying the whole chunk would be too much. Otherwise it takes the last overlap_words word runs. If it finds a sentence boundary early enough in that trailing text, it drops the older sentence fragment and returns the newer part. If not, it returns the full trailing text.

**Call relations**: _apply_overlap calls this for each previous chunk before prefixing the next chunk. It is the small decision-maker that makes overlap more sentence-aware.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This internal method creates a stable identifier for a chunk. The identifier changes when the owner, subject, order, or text changes, which lets the system tell current chunks from stale ones.

**Data flow**: It receives owner kind, owner id, subject, ordinal number, and chunk text. It joins those fields with a separator, hashes the result with SHA-256, and returns the digest string with a sha256 prefix. It does not change any outside state.

**Call relations**: TextChunker.chunk calls this once for every slice of text. chunk_embed_upsert later uses these digests when asking the backend to prune old chunks and keep only the current ones.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).

## 📊 State Registers Touched

- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-page-index` — The stored pages, revisions, chunks, embeddings, and search indexes used to find synced knowledge later.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-untrusted-content-taint` — Trust/taint markers attached to external content as it moves through retrieval, prompts, tools, and rendering so prompt-injection safety checks can be enforced.
- `reg-listing-cursors` — Opaque pagination and browsing cursor state used to resume stable listings across objects, pages, memory, artifacts, usage records, and portal panels.

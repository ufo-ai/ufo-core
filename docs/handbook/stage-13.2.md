# Extension-owned objects  `stage-13.2`

This stage is shared behind-the-scenes support for “extension-owned objects.” These are workspace items that belong to add-on features, but still appear through the same object system users and agents already use. It is like giving many different tools the same kind of label, shelf, and access rules.

The source files define outside content to sync, synced read-only pages, and triggers that wake conversations when watched content changes. Connector objects represent linked third-party accounts and guard actions like sharing, revoking, or disconnecting them. GBrain source objects register places where Markdown knowledge pages come from. Memory objects expose stored memories and member profiles for reading, while leaving updates to memory jobs.

Other files make monitors visible as stoppable watches, report digests visible as read-only records of scheduled runs, and hosted sites manageable through list, share, publish, privatize, or unhost actions. The skill store saves and protects user-created skills. The todo extension manages per-conversation checklists. Small package files simply make extension modules importable. Together, these parts let many extensions plug into one common workspace experience.

## Files in this stage

### Source synchronization objects
Defines shared source registrations, synced page objects, and conversation wake-up subscriptions for source changes.

### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hook handling`

This file is the control desk for external content feeds. A “source” is one connection to an outside provider, such as a provider account plus selected streams of content. Without this file, users could not register those feeds, change which streams are synced, delete them safely, or ask to be notified when their synced pages change.

The file turns lower-level source rows into readable object-style records. It groups one row per stream into a single binding, gives that binding a stable name derived from its provider/account/URL, validates that the requested provider, stream names, account, and tenant URL are allowed, and then adds or removes stream rows so the stored source exactly matches the submitted spec. It also enforces important safety rules: private sources belong to the registering member, shared sources can be watched by conversations, and backfill windows can be widened but not quietly narrowed because narrowing could leave old synced pages stranded.

The second half defines source triggers. A trigger is like a standing alarm: when synced pages change, it wakes either the current conversation with a batch summary or one stable conversation per changed page. The hook at the end listens for page-change batches, filters out anything the trigger’s agent is not allowed to read, writes a small change log file when useful, and sends a plain instruction message to the agent.

#### Function details

##### `_Binding.name`  (lines 220–221)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name is derived from the provider, account, and tenant URL so the same real-world connection always has the same name.

**Data flow**: It reads the binding’s provider, account, and base URL, passes them to the shared name-making helper, and returns the resulting string. It does not change anything.

**Call relations**: Other parts of this file compare submitted object names against this derived name so duplicate or wrongly named sources are refused instead of silently creating confusing records.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 224–225)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the binding effectively began. Because a binding is made from several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads the creation time from every stream in the binding and returns the earliest one. Nothing is written or changed.

**Call relations**: The source object detail uses this value when showing a source to a member, so the grouped binding has one sensible creation date.


##### `_Binding.updated_at`  (lines 228–229)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports the most recent update time for the binding. Since any stream can change, the binding is considered updated when its newest stream row was updated.

**Data flow**: It reads all stream update times and returns the latest one. It has no side effects.

**Call relations**: The source object detail uses this value so a user sees one updated timestamp for the whole binding, even though it is stored as several stream rows.


##### `_Binding.links`  (lines 231–244)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Describes what credential or connection the source uses for access. This helps readers understand what outside account or workspace key powers the sync.

**Data flow**: It looks at whether the binding uses a direct workspace credential, a connected account, or is shared. It returns object links to the credential or connection when it is appropriate to show them, and returns no links for shared brokered bindings where the private connection should not be exposed.

**Call relations**: Source object details call this when presenting a source. It hands back links that the object system can display as relationships such as “access_to”.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 246–254)

```
def spec(self) -> SourceSpec
```

**Purpose**: Rebuilds the public source specification from the internal grouped rows. This is what users see when they get the source object.

**Data flow**: It reads the binding’s provider, stream names, account, base URL, sharing status, and backfill setting, converts internal values such as the direct-account marker into user-facing fields, and returns a SourceSpec.

**Call relations**: Source object retrieval and comparison use this to turn stored source rows back into the same shape that apply accepts.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 256–258)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a binding. It names the provider, account, and streams without showing the full detailed spec.

**Data flow**: It joins the stream names, combines them with provider and account, trims the result to the maximum summary length, and returns that text.

**Call relations**: Listings and alert messages use this summary so users and agents can quickly recognize which source changed.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 267–270)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure an ExtensionContext is present before source work continues. The extension context is the gateway to stored sources, files, credentials, and conversation operations.

**Data flow**: It receives an optional context. If it is present, it returns it; if it is missing, it raises a runtime error because the caller cannot safely proceed.

**Call relations**: Many source and trigger operations call this at their start. It acts like checking that the toolbox is actually on the workbench before using any tools.

*Call graph*: called by 10 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 273–276)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool call has a connector registry available. The registry says which outside providers and account connections can be used.

**Data flow**: It reads the connector registry from the ToolContext. If it exists, it returns it; otherwise it raises a runtime error.

**Call relations**: Account resolution calls this before deciding whether a provider should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 279–314)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Turns raw per-stream source rows into grouped source bindings. This gives the rest of the file one object per provider/account/URL instead of one record per stream.

**Data flow**: It asks the extension context for all source records, ignores records from providers this extension does not know, validates each record’s connector config, groups rows by provider, account, and base URL, wraps each row as a stream, and returns sorted binding objects.

**Call relations**: Listing, lookup, trigger listing, and page-change handling all start here when they need the current set of registered source bindings.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 317–327)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one source binding by its derived object name. Both source operations and trigger operations use this lookup.

**Data flow**: It requires the extension context, rebuilds all bindings from stored rows, scans for the binding whose name matches the requested name, and returns that binding or None.

**Call relations**: Apply, delete, status, object get, resync, grant, and trigger creation call this whenever they need to confirm that a named source still exists.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 330–331)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Builds the trigger store for the current extension context. The trigger store is the small storage layer for source-trigger rows.

**Data flow**: It requires a valid extension context, creates a SourceTriggerStore around it, and returns that store.

**Call relations**: Trigger listing, creation, deletion, source deletion cleanup, and page-change wakeups all call this before reading or writing trigger records.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 334–343)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Decides how far back a stream should initially sync. It combines the member’s request with the stream’s own default window.

**Data flow**: It receives a requested backfill value and a stream-declared default. A numeric request wins, no request uses the stream default, and “all” or a stream with no window becomes None, meaning no date cutoff.

**Call relations**: Source registration uses it to set the first cutoff date, and window widening uses it to compare old and new reach.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 346–356)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the fields that define whether two source specs mean the same binding state. It deliberately ignores one-shot actions like resync.

**Data flow**: It reads provider, sorted streams, account ID, base URL, sharing flag, and backfill setting from a SourceSpec, and returns them as a tuple suitable for comparison.

**Call relations**: The apply path uses this to detect no-op re-applies, and the resync path uses it to reject requests that try to resync and edit the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 385–406)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Handles the top-level apply action for source objects. It separates special cases like resync and identical re-apply before falling back to the normal owned-object apply flow.

**Data flow**: It receives the tool context, object name, new spec, old visible spec, and expected generation. If resync is requested, it schedules a sync; if the spec is identical, it grants the caller access to the already-settled source; otherwise it passes the request to the base class for permission checks and actual mutation.

**Call relations**: This is the first source-specific method called when a user applies a source manifest. It delegates to _resync, _grant_settled, or the inherited apply machinery depending on the request.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 408–429)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Grants the current agent access to an already existing source when the submitted spec exactly matches it. This prevents a no-op re-apply from looking successful while leaving the agent without the feed it asked for.

**Data flow**: It reads the speaking member, the source owner, and the named binding. If the speaker is allowed to use that private or shared binding, it grants each stream’s source ID to the current agent.

**Call relations**: SourceObjects.apply calls this only for identical re-applies. It uses _binding_named to find the streams and the extension context to record the grants.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 431–455)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing binding without changing its configuration. This is for “run it now” requests, not edits.

**Data flow**: It compares the submitted spec with the visible old spec, checks that the source exists, verifies the caller is the registrar or an admin, then sends all stream source IDs to the extension context’s sync scheduler.

**Call relations**: SourceObjects.apply calls this when the spec has resync set. It refuses mixed edit-and-resync requests so no half-action can happen.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `SourceObjects._member_rows`  (lines 457–470)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when listing source objects. Each listed row represents one grouped binding with ownership and sharing information.

**Data flow**: It rebuilds all bindings, turns each into an OwnedRow containing the derived name, short summary, owner member ID, and shared flag, and returns the tuple of rows.

**Call relations**: The member-readable object framework calls this during list operations. The base class then applies visibility rules for members and admins.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 472–488)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed view for one source object. It returns the stored spec, timestamps, and access links for the named binding.

**Data flow**: It looks up the binding by name. If found, it converts it to a SourceSpec, adds created and updated times plus credential/connection links, and returns an ObjectDetail; if missing, it returns None.

**Call relations**: The object framework calls this during get operations after ownership has been resolved.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 490–516)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports operational status for a source, such as next sync time and recent error state per stream. This is the health view of the binding.

**Data flow**: It looks up the binding, then builds a dictionary containing whether it is shared and, for each stream, next sync time, error count, parked state, parked reason, and backfill cutoff. For private sources it also includes the owner member ID.

**Call relations**: The object system calls this when status information is requested. It depends on _binding_named for the current grouped source rows.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 518–622)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Creates or updates a source after ownership checks have passed. It validates the provider, streams, tenant URL, account choice, sharing changes, backfill rules, and then adds, grants, updates, or removes stream rows.

**Data flow**: It receives the desired source spec and current owner state. It requires a speaking member, checks the provider catalog and stream names, validates the base URL, resolves the account or credential path, checks that the submitted name is the derived name, compares with any existing binding, widens windows or flips private to shared when allowed, registers missing streams, grants existing streams to the agent, and removes dropped streams.

**Call relations**: The inherited apply flow calls this for real source changes. It hands account decisions to _resolved_account, URL checking to _validated_base_url, backfill comparison to _widen_window, and storage writes to the extension context.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 8 external calls (__init__, __init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 624–698)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Changes a source’s backfill window only when the new window reaches farther back in time. This protects already-synced pages from being left in an unclear state.

**Data flow**: It receives the binding, the kept streams, provider-declared windows, account details, and the new request. For each kept windowed stream it reconstructs the original anchor date, computes the new cutoff, refuses any request that would move the cutoff later, and then asks the extension context to rewrite source configs and refetch streams whose cutoff changed.

**Call relations**: SourceObjects._apply_owned calls this before any stream rows are written when a backfill setting changes. It uses effective_days so default windows and explicit requests are judged consistently.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 700–707)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding that the caller is allowed to remove. Deleting removes every stream row and also removes triggers that were watching the binding.

**Data flow**: It looks up the binding by name. If found, it removes each stream source ID through the extension context, then asks the trigger store to remove triggers for that binding; if not found, it raises an unknown-object error.

**Call relations**: The owned-object framework calls this after delete permissions are checked. It connects source cleanup with trigger cleanup so no alarm remains for a deleted source.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 709–782)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which real authentication path a source should use: a connected account or the workspace’s direct credential. This keeps source registration tied to actual available access rather than a user-chosen flag.

**Data flow**: It reads the connector registry, active connector accounts, connection ownership, declared credentials, and fallback capability. It returns an account handle plus optional connection ID, or raises a clear error telling the user to connect an account, choose among accounts, set a credential, or remove an invalid account ID.

**Call relations**: SourceObjects._apply_owned calls this before registering rows. The returned account value is stored in each ConnectorSourceConfig and later drives how sync jobs authenticate.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 785–789)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Builds the official object name for a source trigger. A trigger is uniquely identified by the source it watches and the conversation that owns it.

**Data flow**: It receives a binding name and a conversation ID, joins them into one deterministic string, and returns it.

**Call relations**: Trigger apply, find, and list operations all use this one naming rule so the same source-conversation pair cannot be filed under multiple names.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 820–854)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when listing source triggers. Each row explains which source wakes which conversation and who created the trigger.

**Data flow**: It reads reported triggers from the trigger store, rebuilds source bindings for friendly summaries, fetches creator email addresses, computes each trigger’s derived name, and returns rows with owner, sharing, conversation, source, delivery mode, origin, owner email, and whether it belongs to the current member.

**Call relations**: The member-readable object framework calls this for trigger list requests. It combines trigger-store data with source summaries so lists are understandable.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 856–891)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed view for one source trigger. It shows what source is watched, how delivery works, and what objects the trigger relates to.

**Data flow**: It finds the listed trigger by name and verifies its generation. If it still matches, it returns a SourceTriggerSpec with timestamps and links to the watched source, and for current-delivery triggers, the reporting conversation.

**Call relations**: The object framework calls this during get operations. It uses _find to avoid returning stale details if the trigger changed.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 893–907)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Reports status-like metadata for a source trigger. This includes the owning conversation, watched source, delivery mode, origin, owner email, and whether it is the caller’s trigger.

**Data flow**: It finds the trigger, verifies its generation, fetches the creator email, compares the creator with the current authority member, and returns a dictionary of readable fields or None if the trigger is stale or missing.

**Call relations**: The object system calls this for status requests. It mirrors the list fields so users can inspect one trigger in more detail.

*Call graph*: calls 1 internal fn (_find); 2 external calls (authority_member_id, owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 909–946)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a source trigger for the current conversation. Triggers are immutable: to change one, the user must delete it and create another.

**Data flow**: It computes the expected trigger name from the requested source and current conversation. If the name is wrong, it refuses with the right one; if an identical trigger already exists, it does nothing; otherwise it checks that the source is shared and visible, writes the trigger row, and then rechecks that the source still exists.

**Call relations**: The owned-object framework calls this after permission checks. It delegates watchability checks to _watchable, writes through the trigger store, and cleans up if the source disappeared during creation.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 2 external calls (__init__, authority_member_id).


##### `SourceTriggerObjects._watchable`  (lines 948–961)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether a source can be watched by a trigger. Only visible shared sources can be watched, because private source pages would not be readable by the conversation wakeup path.

**Data flow**: It asks the source object store to get the named source under the current context. If no source is visible, it raises unknown-object; if the source is private, it raises a clear error; otherwise it returns successfully.

**Call relations**: SourceTriggerObjects._apply_owned calls this before creating a trigger, so trigger creation uses the same visibility rules as normal source reads.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 963–967)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a source trigger if the stored trigger still matches the owner generation being deleted. This protects against deleting a trigger that changed underneath the request.

**Data flow**: It finds the trigger by name, checks that its ID matches the owner generation, and then removes that trigger through the trigger store. If the stored row no longer matches, it raises an error.

**Call relations**: The owned-object framework calls this after delete permissions are checked. It uses _find for the current row and _require_triggers for the actual removal.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 969–977)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds one listed source trigger by its derived object name. It is the shared lookup helper for trigger get, status, and delete.

**Data flow**: It reads all reported triggers from the trigger store, computes each derived trigger name, and returns the matching listed trigger or None.

**Call relations**: Trigger object detail, status, and delete all call this so they agree on how a trigger name maps back to a stored trigger row.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 980–1030)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes the relevant source triggers. It is the bridge between the sync engine and conversations.

**Data flow**: It receives a hook payload, confirms it is a page-change batch, maps source IDs back to bindings, groups changes by binding, finds triggers watching each binding, filters to shared pages, checks which source IDs each trigger’s agent may read, and fires the trigger for authorized changes. It returns None after processing.

**Call relations**: The manifest hook system calls this on page-change events. It hands each authorized trigger delivery to _fire_trigger and suppresses archived-agent failures so one unavailable agent does not stop the whole hook.

*Call graph*: calls 3 internal fn (_bindings_from_ext, _fire_trigger, _require_triggers); 2 external calls (__init__, suppress).


##### `_fire_trigger`  (lines 1033–1091)

```
async def _fire_trigger(ext: ExtensionContext, binding: _Binding, trigger: SourceTrigger, audience: Audience, authorized: list[PageChange]) -> None
```

**Purpose**: Delivers one trigger’s authorized changes to the right conversation or conversations. It turns raw page changes into agent invocations.

**Data flow**: It receives the extension context, binding, trigger, conversation audience, and authorized changes. For current delivery it writes one change log, builds one alert message, and invokes the owning conversation. For per-page delivery it opens or reuses a stable conversation for each page, writes a one-page log, and invokes the agent there.

**Call relations**: on_page_change calls this after filtering changes for visibility. It delegates log writing to _write_change_log and alert text to _alert_message, then uses the extension context to invoke agents.

*Call graph*: calls 4 internal fn (invoke, open_conversation, _alert_message, _write_change_log); called by 1 (on_page_change); 2 external calls (conversation_audience, authority_from_member_id).


##### `_write_change_log`  (lines 1094–1129)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the changed-page details to a runtime file when file storage is available. This keeps large change lists out of the chat message while still making them available to the agent.

**Data flow**: It receives a conversation ID, binding, latest/revision label, and page changes. It converts each change into one JSON line with page reference, stream, title, change type, and timestamp, writes the file under a source-specific directory, prunes old runtime files for that directory, and returns the path; if files are unavailable, it returns None.

**Call relations**: _fire_trigger calls this before invoking an agent. _alert_message then mentions the returned path when there are too many changed pages to list inline.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_fire_trigger); 1 external calls (dumps).


##### `_disposition`  (lines 1132–1138)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Classifies a page change as added, updated, or removed. This gives alerts and logs plain words instead of raw timestamp/tombstone details.

**Data flow**: It reads a PageChange. If it is a tombstone, it returns removed; otherwise it compares creation and change times to return added for first-time pages or updated for later revisions.

**Call relations**: The change log writer and stream-count summary both call this so they describe changes consistently.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1141–1155)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes how many pages changed per stream and per change type. This creates the compact count line in trigger alerts.

**Data flow**: It receives a list of page changes, groups them by stream, counts added, updated, and removed changes using _disposition, and returns a readable string such as counts per stream.

**Call relations**: _alert_message calls this to describe the batch before giving details or pointing to the change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1158–1177)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to an agent when a watched source changes. The message tells the agent what changed and how to inspect the pages.

**Data flow**: It receives a binding, changes, and an optional log path. It builds either inline page references for small batches, a file-reading instruction for large logged batches, or an object-list fallback, then combines that with stream counts and the binding summary into one instruction string.

**Call relations**: _fire_trigger calls this immediately before invoking an agent. It uses _stream_counts, _page_reference, and the binding summary to make the alert useful.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (_fire_trigger).


##### `_page_reference`  (lines 1180–1183)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference an agent can pass to object_get. It includes a short title so the reference is recognizable.

**Data flow**: It reads the page ID and title from a PageChange, trims the title or uses an untitled fallback, and returns a string like a page object reference plus label.

**Call relations**: _alert_message uses this when the batch is small enough to list changed pages directly in the alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1186–1226)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes a provider tenant API URL. This prevents unsafe or wrongly shaped URLs from being stored in source configs.

**Data flow**: It receives a provider and optional base URL. For providers with a fixed API host, it refuses overrides. For tenant-specific providers, it requires a URL, parses it, checks HTTPS, no credentials, no port, no query or fragment, and provider-specific host/path patterns, then returns a normalized URL without a trailing slash.

**Call relations**: SourceObjects._apply_owned calls this before resolving the account or writing source rows, so invalid tenant URLs are rejected before any source state changes.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here is one document brought in by the content sync system from an outside source, such as an issue tracker or repository provider. This file turns those stored page rows into workspace objects that tools can browse. Without it, synced content would exist in the backend, but users would not have the standard object interface for listing it, opening it, or safely forgetting it.

The file defines the shape of a page response with `PageSpec`: source information, title, timestamps, visibility subject, digest, blob reference, and a page body. The body is deliberately limited to 65,536 UTF-8 bytes so a single object read cannot return an unbounded amount of text.

The private `_Page` dataclass is a cleaned-up view of a database page row plus its source name. It knows how to present itself as a row in a list, a detailed spec, a short summary, and a link back to the source that synced it.

`PageObjects` is the main object-store implementation. Listing gathers readable pages and formats them. Getting a page reads the body from blob storage, trims it safely, then checks the page state again before returning it. That second check is important: it avoids returning content if the page changed, disappeared, or became unreadable while the blob was being read. Create and update are rejected. Delete means “forget this synced page,” and only workspace admins may do it.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool call has an extension context attached. The extension context is the object that knows how to read sources, pages, and page state for this extension.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it unchanged. If not, it stops the operation with a runtime error, because page objects cannot work without that extension-specific access.

**Call relations**: The page listing, page reading, and admin forget flows call this before touching extension data. It acts like a front-desk check: before any page-specific work happens, it confirms the request is in the right building.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This helper turns a page timestamp into a consistent UTC timestamp string. It accepts either a provider-supplied timestamp or falls back to the local row timestamp when the provider did not give one.

**Data flow**: It receives an optional timestamp string from the source provider and a stored database timestamp. If the provider value is missing, it uses the stored value and adds UTC if needed. If a provider value exists, it parses it, requires it to include a timezone, converts it to UTC, and returns an ISO-formatted string with microseconds.

**Call relations**: _Page.spec and _Page.fields use this whenever they show created or updated times. That keeps list views and detailed page reads speaking the same time format.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the page its object name. For pages, the name is simply the page UUID written as text.

**Data flow**: It reads the page’s UUID from the `_Page` instance and returns the string version. It does not change anything.

**Call relations**: Listing uses this name for each object row, and lookup compares requested names against this value. It is the bridge between an internal UUID and the user-facing object name.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds a link from the page back to the source binding that synced it, when that source name is known. The link helps readers understand where the page came from.

**Data flow**: It checks the page’s `source_name`. If there is no known source name, it returns no links. If there is one, it creates a single object link with relation `synced_by` pointing to the source object.

**Call relations**: Page detail reads include these links in the returned object detail. It hands off to the SDK’s object reference and link types so other parts of the object system can display or follow the relationship.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This turns an internal page record into the detailed page data returned to callers. It combines metadata from the stored row with the already-read body text.

**Data flow**: It receives the page body text and a flag saying whether the body was shortened. It reads the page’s source, stream, title, timestamps, subject, digest, and blob reference; normalizes the timestamps; then returns a `PageSpec` containing all of that information.

**Call relations**: The get flow calls this after it has safely read the body from blob storage and rechecked that the page is still current. It uses `_page_timestamp` so detailed reads use the same timestamp rules as list rows.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short, human-readable one-line description for a page in list results. It includes the title, source provider, stream, and visibility subject.

**Data flow**: It reads the page’s title, backend, stream, and subject, formats them into one sentence-like string, and cuts the result down to the configured maximum length. It does not change the page.

**Call relations**: The list flow uses this when building each row. It is the small label a user sees before deciding whether to open the full page.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This prepares the searchable and sortable metadata shown with a page in list results. It includes only the fields the object kind advertises for filtering and ordering.

**Data flow**: It reads source id, provider name, stream, title, and timestamps from the page. It normalizes the timestamps and returns a dictionary of plain JSON-friendly values.

**Call relations**: The list flow attaches these fields to each object row. It relies on `_page_timestamp` so the list metadata uses the same UTC timestamp format as page details.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns the pages the caller is allowed to see as a paged object list. It is used when someone browses synced pages rather than opening one page in full.

**Data flow**: It receives the tool context and a list query, gathers the readable pages, turns each one into a row with a name, summary, and metadata fields, and then applies the standard object paging/filtering helper to produce an `ObjectPage` result.

**Call relations**: This is the public list operation for the page object kind. It asks `_pages` for the raw visible page views, then hands the rows to the SDK’s `object_page` helper so pages behave like other workspace object lists.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This opens one synced page and returns its detailed metadata plus a bounded copy of its body. It protects callers from huge bodies and protects correctness by checking that the page did not change while being read.

**Data flow**: It receives a tool context and page name. First it finds the matching visible page. If none exists, it returns nothing. Then it reads bytes from the page body blob, keeping at most 65,537 bytes so it can tell whether truncation happened. It decodes up to 65,536 UTF-8 bytes, carefully avoiding a broken final character if the cut lands mid-character. After reading the blob, it asks the extension for the current readable state of that page. If the subject, revision, digest, or blob reference no longer matches, it returns nothing. Otherwise it returns an object detail containing the page spec, timestamps, and source link.

**Call relations**: This is the public read operation for a single page. It starts with `_find`, uses the tool context’s blob reader for the body, calls `_require_ext` to recheck current page state, and finally uses `_Page.spec` and `_Page.links` to shape the response.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for page objects. Pages are synced records, not user-authored resources with an apply progress state.

**Data flow**: It receives the context, name, and expected generation, but does not inspect them. It always returns `None`, meaning there is no status payload to show.

**Call relations**: The object framework may ask any object kind for status. For pages, this method closes that loop by saying there is nothing extra to report.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This rejects attempts to create or update pages through the object interface. Pages must be produced by the content sync driver, not manually authored.

**Data flow**: It receives the requested name, desired page spec, optional old spec, and generation information. Instead of writing anything, it raises a `VerbNotSupported` error with a message explaining that synced pages come from registered sources.

**Call relations**: The object framework calls this for create or update style operations. This implementation deliberately stops that path so the sync driver remains the only writer of page content.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This lets a workspace admin forget a synced page. Forgetting tombstones the page so the existing page-change pipeline can clean up derived data such as indexes.

**Data flow**: It receives the tool context and page name. It first asks whether the speaker is a workspace admin. If not, it raises an admin-required error. If the speaker is allowed, it finds the named page; if the page does not exist, it raises a value error. Then it asks the extension context to forget that page id.

**Call relations**: This is the public delete operation for page objects. It uses `_find` to resolve the user-facing name to an internal page id, `_require_ext` to reach extension storage operations, and then hands the actual tombstone work to `forget_page`.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This helper finds one visible page by its object name. It keeps the name lookup logic in one place for both reads and deletes.

**Data flow**: It receives a context and a page name. It gathers the currently visible pages, compares each page’s name to the requested name, and returns the first match. If no page matches, it returns `None`.

**Call relations**: The get and delete flows call this before doing their main work. It depends on `_pages`, so lookup automatically respects the same visibility rules as listing.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This gathers the live pages the current caller may read and enriches them with source information. It is the shared source of truth for listing and name lookup.

**Data flow**: It receives the tool context, gets the extension context, reads all configured sources, and builds maps from source id to provider name and object binding name. It then asks for pages readable by the current source reader, and converts each stored page record into an internal `_Page` object with metadata, body reference, timestamps, and optional link target back to the source.

**Call relations**: Listing calls this to build the page table, and `_find` calls it to search by name for get and delete. It calls the extension’s source and page accessors, validates connector source configuration when building source names, and uses `_Page` as the common format for later steps.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).


### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling, alert sweep, and source cleanup`

A source trigger is a standing request to notify a conversation when a particular shared source changes. Without this file, the system could fetch sources, but it would not remember who asked to be alerted when those sources update.

The file defines the database table for these triggers and a small store, `SourceTriggerStore`, that is always tied to one workspace. A workspace is the project or shared area the user is working in. Because the database connection itself is not automatically limited to one workspace, every query in this file explicitly includes the workspace ID. That is a safety rail: it stops one workspace’s subscriptions from leaking into another.

The store can create a trigger, remove one trigger, remove all triggers for a deleted source binding, find all conversations that should wake when a binding changes, and list triggers for member-facing screens. It also checks an important rule before creating or removing: a trigger must wake a conversation owned by the currently running agent. In plain terms, one assistant is not allowed to secretly attach alerts to another assistant’s conversation.

For member-facing lists, the file does not store display information such as a channel label on the trigger row. Instead, it reads the current conversation facts when listing. That means if a channel is renamed, the listing shows the new name rather than stale text.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This small helper makes sure a date and time value has a timezone attached. If the database returns a time without timezone information, it treats it as UTC, the common reference timezone.

**Data flow**: It receives a `datetime` value. If the value already says what timezone it belongs to, it returns it unchanged. If it has no timezone, it adds UTC and returns the corrected value.

**Call relations**: It is used by `_trigger` while turning database rows into in-memory trigger objects, so callers always receive trigger timestamps in a consistent form.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This converts one database row into a `SourceTrigger` object that the rest of the code can use safely. It also rejects unknown delivery modes instead of letting bad stored data travel further into the system.

**Data flow**: It receives a row read from the `source_trigger` table. It checks that the row’s delivery value is either `current` or `per_page`, converts the stored timestamps through `_utc`, and returns a `SourceTrigger` containing the row’s important fields.

**Call relations**: It sits between raw database reads and the rest of the feature. `SourceTriggerStore.create`, `SourceTriggerStore.waking`, and `SourceTriggerStore.list_reported` call it after reading rows, so those methods hand back clear Python objects rather than database-shaped data.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives the workspace ID attached to the store’s extension context. It is used so every database operation stays inside the current workspace.

**Data flow**: It reads `workspace_id` from `self.ctx` and returns that ID. It does not change anything.

**Call relations**: The store’s create, remove, list, and wake-up queries rely on this value when they add workspace filters to their database statements.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This creates a new trigger saying that one conversation wants updates for one source binding. It prevents duplicate watches and prevents an agent from creating a trigger for a conversation it does not own.

**Data flow**: It receives a conversation ID, a source binding name, a delivery style, and optionally the member who requested it. It finds the currently executing agent, checks that the conversation belongs to that agent, inserts a new database row with a fresh ID and timestamps, and returns the new row as a `SourceTrigger`. If the same conversation is already watching the binding, it raises a clear error instead of exposing a low-level database conflict.

**Call relations**: This is called when user or agent activity asks to start watching a source. It calls `object_agent_id` to identify the current agent, uses `uuid4` for the new trigger ID, and hands the inserted row to `_trigger` so the caller receives a clean trigger object.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller thinks it is removing. That protects against deleting the wrong subscription if something changed meanwhile.

**Data flow**: It receives the expected `SourceTrigger`. It checks that the trigger belongs to the currently executing agent, then deletes the database row only when the workspace, trigger ID, agent, conversation, and binding all match. If no row is deleted, it reports that the trigger changed or disappeared.

**Call relations**: This is used when a specific watch is being turned off. It calls `object_agent_id` to confirm the current agent and uses a SQL delete statement to remove the matching row.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This deletes every trigger in the current workspace for one source binding. It is what the system uses when a source binding itself is removed, because no conversation can keep watching a source that no longer exists.

**Data flow**: It receives a binding name. It deletes all rows in the current workspace whose binding matches that name. It returns nothing and does not care which agent originally created each trigger.

**Call relations**: This is used during source cleanup. Unlike removing one trigger, it works workspace-wide because a shared source can have subscribers from multiple agents.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds every trigger that should fire when a particular source binding changes. It is the alert sweep’s way of asking, “who needs to be woken up for this update?”

**Data flow**: It receives a binding name. It reads all trigger rows in the current workspace for that binding, ordered by creation time and ID for stable results, converts each row with `_trigger`, and returns them as a tuple.

**Call relations**: This is called when source updates are being processed. It uses a SQL select statement to read matching rows and `_trigger` to turn those rows into `SourceTrigger` objects that the alert logic can act on.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This prepares the list of triggers that can be shown on member-facing screens for the current agent. It includes not only the trigger, but also the audience and display label of the conversation where the trigger fires.

**Data flow**: It optionally receives a conversation ID to narrow the list. It reads matching trigger rows for the current workspace and current agent, converts them into `SourceTrigger` objects, asks the context for current conversation facts, and returns `ListedTrigger` objects for conversations that still exist. If there are no triggers, it returns an empty tuple.

**Call relations**: This is used when the system needs to report existing watches to a user. It calls `object_agent_id` so the list is limited to the current agent, uses a SQL select statement to read trigger rows, uses `_trigger` for conversion, and builds `ListedTrigger` entries with live conversation facts so labels stay up to date.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).


### Connected source registries
Exposes connected accounts and GBrain source registries as governed workspace objects.

### `extensions/connectors/ufo_ext_connectors/objects.py`

`domain_logic` · `object listing, inspection, and edit/delete request handling`

This file is the bridge between “accounts a member connected” and the workspace object interface that agents and the portal can read. Think of it like a library catalog for connected accounts: one kind of record describes the account itself, and another kind describes an agent’s permission slip to use that account.

There are two object kinds here. A `connection` is the real member-owned link to an outside provider account, such as a hosted service account. It can be inspected and disconnected, but it cannot be created by simply applying an object, because connecting requires a third-party consent flow. A `connector_grant` is one agent’s access to one existing connection. Creating a grant attaches an already-connected account to the current conversation’s agent. Updating a grant can only change whether it is shared or private; it cannot secretly switch the provider or account.

The file reads current connection and grant summaries from the grants system, turns them into object rows with stable names, and returns detailed object records when asked. When something is deleted or changed, it calls the grants service to do the real work. Important safety checks require a speaking member, meaning a real workspace member must be acting, and several failure paths detect if the underlying connection changed while the operation was in progress.

#### Function details

##### `_AccountSummary.provider`  (lines 51–51)

```
def provider(self) -> str
```

**Purpose**: This is part of a small shape definition for anything that can be summarized as a connected account. It says such a summary must expose the provider name, such as the outside service the account belongs to.

**Data flow**: An account-like summary object is expected to already have provider information → this property represents reading that provider value → callers can use the provider together with the account id to build a stable object name.

**Call relations**: The helper `_named` relies on this property when it is given rows from connection or grant summaries. It does not do work by itself; it defines what those rows must be able to provide.


##### `_AccountSummary.account_id`  (lines 54–54)

```
def account_id(self) -> str
```

**Purpose**: This is the second required piece of the account summary shape. It represents the account identifier at the outside provider.

**Data flow**: An account-like summary object is expected to already have an account id → this property represents reading that id → callers combine it with the provider name to identify the account in the object system.

**Call relations**: The helper `_named` uses this alongside `_AccountSummary.provider` for both connection rows and connector grant rows. It is a contract for compatible summary objects, not an operation that changes data.


##### `_named`  (lines 57–58)

```
def _named(rows: tuple[SummaryT, ...]) -> dict[str, SummaryT]
```

**Purpose**: This helper turns a group of account summaries into a dictionary keyed by the object name used in the workspace. It gives connections and grants a consistent name based on provider and account id.

**Data flow**: It receives a tuple of summary rows, each with a provider and account id → for each row it asks `account_object_name` to build the standard name → it returns a dictionary where that name points back to the original row.

**Call relations**: Both `ConnectionObjects._member_rows` and `ConnectorGrantObjects._member_rows` call this before presenting rows to the object system. It centralizes naming so the two object views do not invent different names for the same account.

*Call graph*: called by 2 (_member_rows, _member_rows); 1 external calls (account_object_name).


##### `ConnectionObjects._member_rows`  (lines 69–86)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists the connected provider accounts that can be shown as `connection` objects. Each row tells the object system the account name, a human-readable summary, and who owns it.

**Data flow**: It asks the grants system for current connection summaries → gives those summaries stable names with `_named` → wraps each one as an owned object row with owner member id, privacy information, and a generation id used to recognize that exact connection later → returns all rows as a tuple.

**Call relations**: The surrounding object framework calls this when it needs to list connection objects for a member or workspace view. It hands back `OwnedRow` entries that later allow `_member_object`, `_status`, or `_delete_owned` to work against the right connection.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._member_object`  (lines 88–106)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectionSpec] | None
```

**Purpose**: This returns the detailed object record for one connected account. It is used when someone wants to inspect a specific `connection` object rather than just see it in a list.

**Data flow**: It receives an object name and stored owner metadata, especially the generation id → reloads connection summaries and looks for the row with that id → if found, builds a `ConnectionSpec` with provider and account id plus timestamps → returns an `ObjectDetail`, or returns nothing if the connection no longer exists.

**Call relations**: After `_member_rows` has exposed a connection, the object system can call this to open it. It depends on the same grants summary source so the detail view reflects the current connected account.

*Call graph*: 3 external calls (__init__, __init__, connection_summaries).


##### `ConnectionObjects._status`  (lines 108–123)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces extra live status information for a connection, such as who owns it, whether it is shared, where it is hosted, and which agents use it. It is meant for status displays rather than changing anything.

**Data flow**: It receives the object owner metadata → reloads connection summaries and finds the matching connection by generation id → converts selected fields into simple JSON-friendly values → returns that status dictionary, or nothing if the connection disappeared.

**Call relations**: The object tooling calls this when it wants current status for a connection object. It reads from `connection_summaries` and does not hand off to any mutating operation.

*Call graph*: 1 external calls (connection_summaries).


##### `ConnectionObjects._apply_owned`  (lines 125–133)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectionSpec, old: ConnectionSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This deliberately refuses to create or edit a `connection` object through normal object apply. The reason is that connecting an outside account requires a third-party consent process, so it must go through the dedicated `connect_account` path.

**Data flow**: It receives the requested connection spec and any existing object information → ignores the requested change because this route is not allowed → raises `VerbNotSupported` with a message explaining that the connect-account flow must be used.

**Call relations**: The object framework calls this when someone tries to apply a `connection` object. Instead of passing work to the grants service, it stops the request immediately to preserve the consent boundary.

*Call graph*: 1 external calls (__init__).


##### `ConnectionObjects._delete_owned`  (lines 135–145)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This disconnects a connected account when an authorized member deletes the `connection` object. Deleting the connection removes the underlying account connection, not just one agent’s access.

**Data flow**: It receives the tool context, object name, and owner metadata → checks that the grants service is available and that a real speaking member is present → asks the grants service to disconnect the connection identified by the generation id → finishes if successful, or raises an error if the connection changed or could not be disconnected.

**Call relations**: The object framework calls this during deletion of a `connection` object after ownership/admin gates have been checked. It hands the actual disconnect operation to `ctx.grants.disconnect`, using the speaking member as the actor.

*Call graph*: 1 external calls (__init__).


##### `ConnectorGrantObjects._admin_can_apply`  (lines 156–157)

```
def _admin_can_apply(self, old: ConnectorGrantSpec, spec: ConnectorGrantSpec) -> bool
```

**Purpose**: This says exactly when a workspace admin is allowed to update a connector grant. Admins may make a shared grant private, but they may not otherwise change the account or grant details.

**Data flow**: It receives the old grant spec and the requested new spec → builds the only allowed admin-changed version of the old spec, with `shared` set to false → returns true only if the old grant was shared and the requested spec matches that one safe change.

**Call relations**: The member-readable object framework uses this as part of its permission decision before calling the grant apply path. It supports the rule described by the share gate: owners can share, while admins can only reduce exposure by making access private.

*Call graph*: 1 external calls (model_copy).


##### `ConnectorGrantObjects._member_rows`  (lines 159–176)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: This lists the agent access grants that can be shown as `connector_grant` objects. Each row represents one agent’s permission to use a connected provider account.

**Data flow**: It asks the grants system for current grant summaries → uses `_named` to create stable account-based names → wraps each grant as an owned row showing provider, account id, owner email, and shared/private state → returns the rows to the object system.

**Call relations**: The object framework calls this when listing connector grants. The rows it returns are later used by `_member_object`, `_status`, `_apply_owned`, and `_delete_owned` to inspect or change a particular grant.

*Call graph*: calls 1 internal fn (_named); 3 external calls (__init__, __init__, grant_summaries).


##### `ConnectorGrantObjects._member_object`  (lines 178–219)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[ConnectorGrantSpec] | None
```

**Purpose**: This returns the detailed object record for one agent’s access grant. It also adds links that explain what the grant is connected to: the agent that holds it and, when private, the underlying connection it opens.

**Data flow**: It receives the object name and owner metadata → reloads grant summaries and finds the row with the matching generation id → builds a `ConnectorGrantSpec` containing provider, account id, and shared state → adds timestamps and object links, including a `scoped_to` agent link and sometimes an `access_to` connection link → returns the detail record, or nothing if the grant no longer exists.

**Call relations**: The object framework calls this after a grant row has been selected for inspection. It uses `account_object_name` to point private grants back to the related `connection` object, so readers can navigate from an agent’s permission slip to the account behind it.

*Call graph*: 6 external calls (__init__, __init__, __init__, __init__, account_object_name, grant_summaries).


##### `ConnectorGrantObjects._status`  (lines 221–236)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: This produces live status information for a connector grant, such as the owner, host, agent name, and whether the grant is shared. It is a read-only status view.

**Data flow**: It receives the tool context, object name, and owner metadata → reloads grant summaries and finds the matching grant by generation id → converts selected fields into a JSON-friendly dictionary → returns that dictionary, or nothing if the grant has disappeared.

**Call relations**: The object tooling calls this when it needs current status for a grant object. It reads from `grant_summaries` and does not change the grant.

*Call graph*: 1 external calls (grant_summaries).


##### `ConnectorGrantObjects._apply_owned`  (lines 238–288)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: ConnectorGrantSpec, old: ConnectorGrantSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: This is the main edit path for `connector_grant` objects. It can attach an existing connection to an agent, or change whether an existing grant is shared, while blocking attempts to create a new underlying third-party connection or swap the account behind a grant.

**Data flow**: For a brand-new grant, it receives a spec with provider, account id, and shared flag → checks that grants are available and a speaking member exists → asks the grants service to attach the existing connection to the current conversation’s agent → errors if that connection is not available. For an existing grant, it reloads the current grant, verifies the requested change does not alter provider or account id, and if only the shared flag changed, asks the grants service to update that flag.

**Call relations**: The object framework calls this when a `connector_grant` object is created or updated. It hands real changes to `ctx.grants.attach` for new grants and `ctx.grants.set_shared` for disclosure changes, while using `grant_summaries` to guard against stale or unsafe edits.

*Call graph*: 5 external calls (__init__, __init__, __init__, model_copy, grant_summaries).


##### `ConnectorGrantObjects._delete_owned`  (lines 290–300)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: This revokes one agent’s access to a connected account when a `connector_grant` object is deleted. It leaves the underlying connection, and any other agents’ grants, in place.

**Data flow**: It receives the tool context, object name, and owner metadata → checks that the grants service exists and a speaking member is acting → asks the grants service to revoke the grant identified by the generation id → finishes if successful, or raises an error if the grant changed while revocation was attempted.

**Call relations**: The object framework calls this during deletion of a connector grant after the revoke permission gate is satisfied. It delegates the actual revoke action to `ctx.grants.revoke`, using the speaking member as the accountable actor.

*Call graph*: 1 external calls (__init__).


### `extensions/gbrain/ufo_ext_gbrain/__init__.py`

`other` · `import time`

This file does not contain any code of its own. Its job is structural: in Python, an `__init__.py` file tells the runtime that a folder should be treated as an importable package. You can think of it like a label on a drawer. The drawer may contain useful tools in other files, and this label lets the rest of the project find them using normal Python import paths. Without this file, some Python environments or packaging tools might not recognize `extensions/gbrain/ufo_ext_gbrain` as a proper package, which could make the GBrain extension harder or impossible to import. Because it is empty, it does not run setup logic, define public shortcuts, or change behavior at import time. Its value is simply making the package boundary clear and reliable.


### `extensions/gbrain/ufo_ext_gbrain/objects.py`

`domain_logic` · `request handling for object verbs; also reflects sources registered at startup`

A gbrain source is like a subscription to a pile of Markdown files. Once registered, the system can sync those files into searchable memory. This file is the rulebook for treating those subscriptions as first-class objects: how they are named, what a valid source looks like, who can see it, who can change it, and how it connects to the lower-level sync system.

The most important rule is that the object name is not chosen freely. It is calculated from the origin itself: repository, branch, or folder path. That means the same GitHub repo always gets the same `gbrain-...` name, and changing the repo or branch creates a different source. This prevents two people from accidentally giving different names to the same feed.

The file also separates two kinds of origins. GitHub repositories may be applied by users. Local server folders are more sensitive because they read the host machine's filesystem, so they can only come from operator configuration at startup, not from chat or normal object apply calls.

Visibility matters too. A source starts private to the member who registered it unless it is marked shared. The registrar can make it shared later, but shared sources cannot be made private again except by deleting and recreating them. Resync and delete are protected actions: they require the registrar or an admin.

#### Function details

##### `gbrain_source_name`  (lines 58–65)

```
def gbrain_source_name(repo: str | None, branch: str | None, root: str | None) -> str
```

**Purpose**: Builds the official object name for a gbrain source from its real origin. This keeps naming consistent, so the same repo, branch, or folder path always maps to the same object name.

**Data flow**: It receives a repository name, branch name, and folder root, any of which may be absent depending on the source type. It packs those values into a stable JSON string, hashes that string, keeps the first few hexadecimal characters, and returns a name like `gbrain-1a2b3c4d`.

**Call relations**: The origin-building code uses this when preparing a new source, and `_Registered.name` uses it when turning an existing database row back into its object name. It is the shared naming rule that keeps new requests and stored rows speaking the same language.

*Call graph*: called by 2 (name, _origin); 2 external calls (sha256, dumps).


##### `GbrainSpec.validate_origin`  (lines 98–103)

```
def validate_origin(self) -> 'GbrainSpec'
```

**Purpose**: Checks that a requested gbrain source describes exactly one origin. A source must be either a GitHub repository or a local folder, not both and not neither.

**Data flow**: It reads the fields on a `GbrainSpec`. If both `repo` and `root` are set, or both are missing, it rejects the spec. If a branch is given without a repository, it rejects that too. Otherwise it returns the validated spec unchanged.

**Call relations**: This runs as part of Pydantic model validation, before the object handlers act on the spec. It protects later code such as `_origin` from having to guess what kind of source the user meant.


##### `_origin`  (lines 113–127)

```
def _origin(spec: GbrainSpec) -> _Origin
```

**Purpose**: Turns a validated gbrain spec into the concrete sync backend and backend-specific settings the system needs. It also calculates the official object name for that origin.

**Data flow**: It receives a `GbrainSpec`. If the spec names a local folder root, it creates folder backend configuration and a name based on that root. If the spec names a GitHub repo, it creates Git backend configuration and a name based on repo and branch. It returns one `_Origin` bundle containing backend, config, and name.

**Call relations**: The main apply path calls this to compare the requested name with the required derived name and to register new sources. `GbrainObjects.apply` also uses it when checking whether an already-private source is taken by someone else.

*Call graph*: calls 1 internal fn (gbrain_source_name); called by 2 (_apply_owned, apply); 3 external calls (__init__, __init__, __init__).


##### `_identity`  (lines 130–131)

```
def _identity(spec: GbrainSpec) -> tuple[str | None, str | None, str | None, bool]
```

**Purpose**: Extracts the parts of a spec that define whether it is the same source state for object-apply purposes. This is used to tell a harmless repeat apply from a real change.

**Data flow**: It receives a `GbrainSpec` and returns a tuple containing repository, branch, root, and shared flag. It deliberately leaves out `resync`, because resync is an action request, not stored source state.

**Call relations**: `GbrainObjects.apply` uses this to detect re-applying the same source. `_resync` uses it to make sure a resync request is not secretly trying to change the source at the same time.

*Call graph*: called by 2 (_resync, apply).


##### `_Registered.name`  (lines 148–149)

```
def name(self) -> str
```

**Purpose**: Gives an existing registered source its official object name. It recalculates the name from the stored origin rather than trusting a separate stored label.

**Data flow**: It reads the registered row's repo, branch, and root values. It passes them through the shared naming function and returns the resulting `gbrain-...` name.

**Call relations**: Code that lists or looks up registered sources relies on this property. Because it calls `gbrain_source_name`, stored records and newly submitted specs follow the exact same naming rule.

*Call graph*: calls 1 internal fn (gbrain_source_name).


##### `_Registered.spec`  (lines 151–157)

```
def spec(self) -> GbrainSpec
```

**Purpose**: Converts a stored source row back into the public spec shape shown to users and tools. This is how the object system reads back what is currently registered.

**Data flow**: It reads the stored repository, branch, root, and subject. It turns the subject into a simple `shared` boolean and creates a `GbrainSpec`. The returned spec does not preserve resync as true, because resync is only a one-time action.

**Call relations**: `GbrainObjects._member_object` returns this spec when someone asks for an object detail. It bridges the lower-level source record format and the higher-level object manifest format.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Registered.summary`  (lines 159–163)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable description for a registered source. This helps lists show something meaningful without dumping the full stored record.

**Data flow**: It reads whether the row is a folder source or a GitHub source. For folders, it returns text like `server directory ...`; for GitHub, it returns text like `github repository owner/repo` or `owner/repo@branch`. The result is trimmed to a maximum length.

**Call relations**: `GbrainObjects._member_rows` uses this when producing object list rows. Error messages in the apply path also use it to explain which private source is already registered.


##### `_require_ext`  (lines 166–169)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Ensures the gbrain object code has an extension context, which is the gateway to the real source registry. Without it, this file cannot list, register, grant, resync, or remove sources.

**Data flow**: It receives an optional extension context. If the value is missing, it raises a runtime error with a clear message. If present, it returns the context unchanged.

**Call relations**: Most operations call this before touching source storage through the extension API. It is a small safety check used by lookup, listing, apply, resync, grant, and delete paths.

*Call graph*: called by 6 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resync, _registered_named).


##### `_registered_from_ext`  (lines 172–197)

```
async def _registered_from_ext(ext: ExtensionContext) -> tuple[_Registered, ...]
```

**Purpose**: Reads all registered gbrain sources from the extension context and converts them into this file's simpler `_Registered` shape. It filters out sources that belong to other backends.

**Data flow**: It asks the extension context for source records. For each record, it checks whether the backend is the gbrain Git backend or folder backend. It validates the backend-specific config, extracts repo/branch/root, copies ownership and sync status fields, and returns a tuple of `_Registered` entries.

**Call relations**: Listing uses this to show all gbrain object rows. Name lookup uses it as its source of truth, then searches the returned entries for a matching derived object name.

*Call graph*: calls 1 internal fn (sources); called by 2 (_member_rows, _registered_named); 3 external calls (__init__, model_validate, model_validate).


##### `_registered_named`  (lines 200–204)

```
async def _registered_named(ext: ExtensionContext | None, name: str) -> _Registered | None
```

**Purpose**: Finds one registered gbrain source by its object name. It is the common lookup helper for operations that need to act on a specific source.

**Data flow**: It receives an optional extension context and a name. It first requires a real context, then loads all registered gbrain sources, compares each source's derived name to the requested name, and returns the matching `_Registered` row or `None`.

**Call relations**: Apply, detail read, status read, grant, resync, and delete all call this when they need to connect an object name to the underlying source row. It sits between object-level names and extension-level source IDs.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, apply).


##### `GbrainObjects.apply`  (lines 222–253)

```
async def apply(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Implements the high-level apply behavior for `gbrain_source` objects. It decides whether the request is a resync, a no-op reapply, a new registration, or a sharing change, then applies the right permission checks.

**Data flow**: It receives the tool context, requested object name, desired spec, any visible old spec, and an expected generation value. If `resync` is true, it sends the request to `_resync`. If the submitted spec matches the visible old spec, it grants the already-settled source to the current agent. If this is a new visible object but the same origin is privately registered by someone else, it refuses. Otherwise it delegates to the base object apply flow.

**Call relations**: This is the front door for apply requests. It calls `_resync`, `_grant_settled`, `_identity`, `_origin`, and `_registered_named` for gbrain-specific rules, then hands normal mutations to the parent `MemberReadableObjects` machinery.

*Call graph*: calls 6 internal fn (speaker_is_admin, _grant_settled, _resync, _identity, _origin, _registered_named); 3 external calls (__init__, authority_member_id, subject_shared).


##### `GbrainObjects._grant_settled`  (lines 255–270)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Gives the current agent access to an already-registered source when the user re-applies the identical spec. This avoids a confusing case where the object appears unchanged but the agent still lacks the feed.

**Data flow**: It reads the speaking member, the object's owner information, and the registered source row. If there is no speaker, no owner, or the speaker is not allowed to use the source, it does nothing. Otherwise it calls the extension context to grant that source to the current agent.

**Call relations**: `GbrainObjects.apply` calls this only for identical re-applies. It then hands off to the extension context's grant operation, using `_registered_named` to find the source ID and `_require_ext` to ensure the extension API is available.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); called by 1 (apply).


##### `GbrainObjects._resync`  (lines 272–292)

```
async def _resync(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing source without changing its settings. It exists because resync is an action, not a stored configuration change.

**Data flow**: It receives the context, object name, submitted spec, and old spec. It first checks that the submitted spec matches the current source except for the `resync` flag. It then checks that the source is visible and that the caller is either the owner or an admin. Finally it looks up the registered source ID and asks the extension context to schedule a sync now.

**Call relations**: `GbrainObjects.apply` sends resync requests here. This helper uses `_identity` for the no-change check, `_registered_named` to find the stored row, permission helpers from the base object class, and the extension context to trigger the actual sync scheduling.

*Call graph*: calls 4 internal fn (speaker_is_admin, _identity, _registered_named, _require_ext); called by 1 (apply); 4 external calls (__init__, __init__, __init__, authority_member_id).


##### `GbrainObjects._member_rows`  (lines 294–307)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the list entries for gbrain sources that the object system can then filter or display for a member. Each row contains the object name, a short summary, and ownership information.

**Data flow**: It receives an extension context and an optional member ID. It loads all registered gbrain sources, converts each into an `OwnedRow`, marks whether it is shared, records its owner member if any, and returns all rows as a tuple.

**Call relations**: The base `MemberReadableObjects` class calls this as part of list and visibility logic. It relies on `_registered_from_ext` to read the source registry and on `_Registered.summary` and `_Registered.name` to present each row.

*Call graph*: calls 2 internal fn (_registered_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `GbrainObjects._member_object`  (lines 309–324)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[GbrainSpec] | None
```

**Purpose**: Builds the detailed object view for one registered gbrain source. It returns the user-facing spec plus creation and update timestamps.

**Data flow**: It receives an extension context, object name, owner information, and optional member ID. It looks up the registered source by name. If none exists, it returns `None`; otherwise it creates an `ObjectDetail` containing the source spec and timestamps.

**Call relations**: The base object system calls this after it has decided an object may be read. It uses `_registered_named` for lookup and `_Registered.spec` to translate the stored row into the public manifest form.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (__init__).


##### `GbrainObjects._status`  (lines 326–340)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns operational status for a gbrain source, such as when it will next sync and how many sync errors have happened in a row. This is separate from the desired spec because it describes runtime health.

**Data flow**: It receives the tool context, object name, and owner. It looks up the registered source. If missing, it returns `None`. Otherwise it returns a dictionary with shared status, next sync time, consecutive error count, and, for private owned sources, the owner member ID.

**Call relations**: The object framework calls this when status information is requested. It uses `_registered_named` to find the source and the subject helper to turn the stored subject into a plain shared/not-shared value.

*Call graph*: calls 1 internal fn (_registered_named); 1 external calls (subject_shared).


##### `GbrainObjects._apply_owned`  (lines 342–380)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: GbrainSpec, old: GbrainSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the actual create or update after the higher-level object framework has passed ownership checks. It registers new GitHub sources, flips private sources to shared, and grants the source to the current agent.

**Data flow**: It receives the context, object name, desired spec, old spec, and owner. It requires a speaking member, refuses local folder roots, derives the required name from the origin, and rejects mismatched names. If the source is new, it registers it with either a private member subject or shared subject. If it already exists, it refuses shared-to-private changes, optionally changes private-to-shared, and grants the source to the agent.

**Call relations**: The parent apply flow calls this after `GbrainObjects.apply` has handled special cases and general permissions. This method is where object intent turns into extension-context calls such as register, set shared subject, and grant.

*Call graph*: calls 3 internal fn (_origin, _registered_named, _require_ext); 4 external calls (__init__, __init__, member_subject, subject_shared).


##### `GbrainObjects._delete_owned`  (lines 382–386)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Removes a registered gbrain source after delete permission has been approved. Removing the source lets the sync system tombstone, or mark as deleted, the pages that came from it.

**Data flow**: It receives the tool context, object name, and owner. It looks up the registered source by name. If no such row exists, it raises an unknown-object error. Otherwise it asks the extension context to remove the source by its source ID.

**Call relations**: The base object delete flow calls this once it has checked that the caller is allowed to delete. It uses `_registered_named` to find the underlying source row and `_require_ext` to call the extension removal API.

*Call graph*: calls 2 internal fn (_registered_named, _require_ext); 1 external calls (__init__).


### Memory and profile records
Makes memory items and member profiles readable through the object system while keeping writes owned by memory jobs.

### `extensions/memory/ufo_ext_memory/objects.py`

`domain_logic` · `request handling`

This file is the doorway between the system’s generic “objects” interface and the memory extension’s database tables. A memory item is a saved fact or note that can be opened by its durable id. A profile is a short “People” entry for a workspace member, such as their role and current focus.

The file’s main job is to answer: “What is this reader allowed to see?” Memory can include private or shared subjects, so every list or read checks the caller’s audience before returning anything. If a memory was distilled from a page, the file also checks that the reader can still read that exact page revision. This matters because a memory should not leak information from a page the reader cannot access.

Memory listings show only live items. Older items that were replaced are hidden from lists, but they can still be opened by id and will point to the newer item. This is like keeping an old filing-card number working, but adding a note that says “see the new card.”

Profiles are simpler. They are written from shared workspace facts, so only readers who carry the shared workspace subject can see the People band at all. Both memory and profile objects reject direct apply and delete operations. Writing happens through memory-specific background or tool paths, not through this object interface.

#### Function details

##### `_require_ext`  (lines 91–94)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the memory object code was given its extension context, which is the object that knows the workspace, database, and helper services. Without it, the object methods cannot safely read memory data.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it stops the request with a clear runtime error instead of letting later database code fail in a confusing way.

**Call relations**: The public memory and profile methods call this first before they move on to listing or reading entries. It acts like checking that you have the right key before trying to open the filing cabinet.

*Call graph*: called by 8 (get, list, member_detail, member_page, get, list, member_detail, member_page).


##### `_stamp`  (lines 97–101)

```
def _stamp(written: datetime) -> str
```

**Purpose**: This turns a stored date and time into one consistent text format for object rows. It hides database differences, especially the fact that some databases store timezone information differently.

**Data flow**: It receives a datetime from storage, normalizes it through the memory store’s timezone helper, and returns an ISO-8601 string, the common timestamp format used on the wire.

**Call relations**: _row and _profile_row use this when they build list rows. That means both memory items and profile entries present their written time in the same readable form.

*Call graph*: called by 2 (_profile_row, _row); 1 external calls (_aware).


##### `_row`  (lines 104–128)

```
def _row(name: str, body: str, subject: str, item_class: str, memory_kind: str, written: datetime | None, page_id: UUID | None, pages: Mapping[UUID, PageState]) -> ObjectRow
```

**Purpose**: This builds the lightweight row shown when memory items are listed. It includes a short summary, selected fields, and page-origin information when the memory came from a synced page.

**Data flow**: It receives the memory id, body text, visibility subject, classification fields, written time, optional source page id, and readable page states. It clips long text to list-friendly lengths, adds timestamps and source page details, and returns an ObjectRow.

**Call relations**: MemoryObjects._page uses it for each listed memory, and MemoryObjects.member_detail uses it when wrapping one memory for portal-style member reads. It relies on _stamp for time formatting and clip_to_word to keep row text small.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_page, member_detail); 2 external calls (__init__, clip_to_word).


##### `_member_reader`  (lines 131–139)

```
def _member_reader(member_id: UUID) -> SourceReader
```

**Purpose**: This creates the reading identity used when a signed-in member views memory outside an active conversation turn. It says which agent is reading, which member requested it, and which visibility subjects count for that member.

**Data flow**: It receives a member id. It asks the current agent for its id, builds that member’s conversation audience, converts that audience into subjects, and returns a SourceReader containing all of that.

**Call relations**: MemoryObjects.member_page and MemoryObjects.member_detail use this before reading memory for the member portal. It mirrors the audience rules used during a normal conversation turn.

*Call graph*: called by 2 (member_detail, member_page); 4 external calls (__init__, audience_subjects, conversation_audience, agent_current).


##### `MemoryObjects.list`  (lines 148–149)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists live memory items visible to the current tool request. It is the normal object-list entry point for the memory kind.

**Data flow**: It receives a tool context and list query. It gets the extension context, asks the tool context who the reader is, and passes both into the shared paging routine. The result is an ObjectPage of visible memory rows.

**Call relations**: The object system calls this when someone lists memory objects during a tool request. It delegates the real database work and visibility checks to MemoryObjects._page.

*Call graph*: calls 3 internal fn (source_reader, _page, _require_ext).


##### `MemoryObjects.member_page`  (lines 151–164)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists live memory items as a signed-in member would see them in a portal-style view, outside an active tool turn. Admin status does not widen private memory access here.

**Data flow**: It receives an optional extension context, a member id, an admin flag, and a list query. It builds that member’s reader identity, then asks MemoryObjects._page to fetch the visible rows. It returns an ObjectPage.

**Call relations**: Portal or member-facing code calls this instead of MemoryObjects.list when there is no ToolContext. It uses _member_reader to recreate the member’s normal audience and _page to do the actual listing.

*Call graph*: calls 3 internal fn (_page, _member_reader, _require_ext).


##### `MemoryObjects.member_detail`  (lines 166–200)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[MemorySpec] | None
```

**Purpose**: This opens one memory item as a signed-in member would see it and also returns the list-style row beside the full detail. It is useful for member portal detail pages.

**Data flow**: It receives an extension context, memory name, member id, and admin flag. It builds the member reader, fetches the memory detail, looks for a source-page link, reads that page state if needed, builds a row, and returns a MemberObject. If the item is not visible or the name is invalid, it returns nothing.

**Call relations**: This is the member-facing counterpart to MemoryObjects.get. It relies on MemoryObjects._item for the careful visibility-checked detail read, then uses _row to produce the row summary shown next to that detail.

*Call graph*: calls 4 internal fn (_item, _member_reader, _require_ext, _row); 2 external calls (__init__, UUID).


##### `MemoryObjects.get`  (lines 202–203)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This opens one memory item by id for the current tool request. It can return superseded memory items too, so old references can still be followed.

**Data flow**: It receives a tool context and a memory name. It gets the extension context and current reader, then asks MemoryObjects._item to parse the id, check permissions, and build the full detail. The result is either an ObjectDetail or nothing.

**Call relations**: The object system calls this when an object_get request targets a memory ref. It hands off all real lookup and link-building work to MemoryObjects._item.

*Call graph*: calls 3 internal fn (source_reader, _item, _require_ext).


##### `MemoryObjects._page`  (lines 205–262)

```
async def _page(self, ext: ExtensionContext, reader: SourceReader, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the main memory listing worker. It finds the newest live memory rows the reader may see, then removes any page-derived rows whose original page is no longer readable in the right revision.

**Data flow**: It receives the extension context, a reader, and a list query. It reads the reader’s allowed subjects, queries the memory table for non-superseded and non-retired rows in those subjects, loads source page states for page-derived memories, filters out unsafe rows, turns the rest into ObjectRows, and returns a paged object result.

**Call relations**: MemoryObjects.list and MemoryObjects.member_page both delegate to this function. It talks to the database through the extension transaction, checks source pages through readable_page_states, formats each result with _row, and wraps the final rows with object_page.

*Call graph*: calls 3 internal fn (readable_page_states, transaction, _row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `MemoryObjects._item`  (lines 264–330)

```
async def _item(self, ext: ExtensionContext, reader: SourceReader, name: str) -> ObjectDetail[MemorySpec] | None
```

**Purpose**: This is the careful single-memory reader. It opens a memory by UUID, verifies that the reader is allowed to see it, and includes links to its source page or replacement memory when relevant.

**Data flow**: It receives the extension context, reader, and memory name. It parses the name as a UUID, queries the memory table for that workspace and allowed subjects, checks the source page if the memory came from one, builds provenance links, and returns an ObjectDetail with a MemorySpec. Invalid names, missing rows, or failed visibility checks return nothing.

**Call relations**: MemoryObjects.get and MemoryObjects.member_detail call this whenever one memory item must be opened. It is where the stale-reference behavior is preserved: superseded items can still be returned, with a link to the replacement.

*Call graph*: calls 2 internal fn (readable_page_states, transaction); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, select, UUID).


##### `MemoryObjects.status`  (lines 332–339)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no special write status for memory objects. Since memory objects are read-only through this interface, there is no editable status to return.

**Data flow**: It receives the request context, object name, and optional expected generation. It ignores them and returns nothing.

**Call relations**: The object framework may ask for status as part of its general protocol. This memory implementation answers with no status because apply and delete are not supported.


##### `MemoryObjects.apply`  (lines 341–350)

```
async def apply(self, ctx: ToolContext, name: str, spec: MemorySpec, old: MemorySpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update a memory item through the generic object apply path. Memories must be written through the memory_update tool path instead.

**Data flow**: It receives the context, name, proposed memory spec, optional old spec, and optional generation. Instead of changing storage, it raises a VerbNotSupported error with an explanation.

**Call relations**: The object framework calls this if someone tries to apply a memory object. The function deliberately stops the flow so all memory writes stay on the intended memory_update route.

*Call graph*: 1 external calls (__init__).


##### `MemoryObjects.delete`  (lines 352–359)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete memory items through the generic object path. Memory items are ended by superseding or retirement logic, not by direct deletion here.

**Data flow**: It receives the context, name, and optional expected generation. It does not touch the database and raises a VerbNotSupported error explaining that memories cannot be deleted this way.

**Call relations**: The object framework calls this for delete requests on memory objects. It protects the memory store from ad hoc cleanup that would leave derived search/index data inconsistent.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.list`  (lines 410–411)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists member profile rows visible to the current tool request. Profiles describe what shared workspace facts say about people.

**Data flow**: It receives a tool context and list query. It checks for the extension context, takes the context’s readable subjects, and passes them to ProfileObjects._page. The result is an ObjectPage of profile rows or an empty page.

**Call relations**: The object system calls this when listing profile objects during a tool request. It delegates the shared-subject rule and database query to ProfileObjects._page.

*Call graph*: calls 2 internal fn (_page, _require_ext).


##### `ProfileObjects.member_page`  (lines 413–426)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists the People band as a signed-in member would see it outside an active tool turn. Admin status does not change the result because profiles come only from shared facts.

**Data flow**: It receives an extension context, member id, admin flag, and list query. It computes the member’s conversation subjects and asks ProfileObjects._page to return the visible profile rows.

**Call relations**: Member-facing code calls this for portal-style profile lists. It builds the same audience subjects a conversation would use, then lets _page enforce that the shared workspace subject must be present.

*Call graph*: calls 2 internal fn (_page, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects.get`  (lines 428–430)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ProfileSpec] | None
```

**Purpose**: This opens one profile by member id for the current tool request. It returns the detailed role and focus if the reader is allowed to see shared workspace facts.

**Data flow**: It receives a tool context and profile name. It checks the extension context, calls ProfileObjects._entry with the readable subjects, and returns only the detail part of the found MemberObject. If nothing is found, it returns nothing.

**Call relations**: The object system calls this when object_get targets a profile. It leaves the permission check, UUID parsing, and database lookup to ProfileObjects._entry.

*Call graph*: calls 2 internal fn (_entry, _require_ext).


##### `ProfileObjects.member_detail`  (lines 432–442)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ProfileSpec] | None
```

**Purpose**: This opens one profile for a signed-in member outside an active tool turn. It returns both the row summary and full detail when visible.

**Data flow**: It receives an extension context, profile name, member id, and admin flag. It computes the member’s audience subjects and asks ProfileObjects._entry to fetch the matching member profile. The result is a MemberObject or nothing.

**Call relations**: Portal or member-facing code uses this when showing a person’s profile detail. It is the member-view counterpart to ProfileObjects.get.

*Call graph*: calls 2 internal fn (_entry, _require_ext); 2 external calls (audience_subjects, conversation_audience).


##### `ProfileObjects._page`  (lines 444–463)

```
async def _page(self, ext: ExtensionContext, subjects: frozenset[str], query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This is the main profile listing worker. It returns all recent profile rows only when the reader has access to the workspace-shared subject.

**Data flow**: It receives the extension context, a set of readable subjects, and a list query. If the shared subject is missing, it returns an empty page. Otherwise it queries the profile table for the workspace, orders newest first, formats rows with _profile_row, and returns a paged result.

**Call relations**: ProfileObjects.list and ProfileObjects.member_page both call this. It is the place where the People band’s simple visibility rule is enforced before any database results are exposed.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (list, member_page); 2 external calls (select, object_page).


##### `ProfileObjects._entry`  (lines 465–498)

```
async def _entry(self, ext: ExtensionContext, subjects: frozenset[str], name: str) -> MemberObject[ProfileSpec] | None
```

**Purpose**: This reads one member profile by member UUID, but only for readers who can see shared workspace facts. It builds both the list row and the full detail object.

**Data flow**: It receives the extension context, readable subjects, and profile name. It first checks for the shared subject, then parses the name as a UUID, queries the profile table for that member in the workspace, normalizes the written time, and returns a MemberObject with ProfileSpec detail. If any check fails, it returns nothing.

**Call relations**: ProfileObjects.get and ProfileObjects.member_detail call this for single-profile reads. It uses _profile_row so the detail response and list response describe the profile consistently.

*Call graph*: calls 2 internal fn (transaction, _profile_row); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, select, _aware, UUID).


##### `ProfileObjects.status`  (lines 500–507)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no special write status for profile objects. Profiles are read-only through this object interface.

**Data flow**: It receives the request context, profile name, and optional expected generation. It does not read or change anything and returns nothing.

**Call relations**: The generic object protocol may call status, but this implementation has no editable profile state to report because profile writes happen elsewhere.


##### `ProfileObjects.apply`  (lines 509–518)

```
async def apply(self, ctx: ToolContext, name: str, spec: ProfileSpec, old: ProfileSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or edit a profile through the generic object apply path. Profiles are written by the People pass, which derives them from shared workspace facts.

**Data flow**: It receives the context, profile name, proposed profile spec, optional old spec, and optional generation. It raises a VerbNotSupported error and makes no storage change.

**Call relations**: The object framework calls this for profile apply requests. The refusal keeps profile data flowing only from the background People pass rather than manual object edits.

*Call graph*: 1 external calls (__init__).


##### `ProfileObjects.delete`  (lines 520–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete a profile through the generic object path. Profile contents are controlled by the People pass, not direct user deletion here.

**Data flow**: It receives the context, profile name, and optional expected generation. It does not change the database and raises a VerbNotSupported error.

**Call relations**: The object framework calls this for delete requests on profiles. It closes that route so profile lifecycle stays tied to the shared facts and People pass.

*Call graph*: 1 external calls (__init__).


##### `_profile_row`  (lines 530–540)

```
def _profile_row(row: sa.Row) -> ObjectRow
```

**Purpose**: This builds the lightweight row shown in profile listings. It gives each member profile a readable summary plus fields that can be displayed or filtered.

**Data flow**: It receives a database row containing member id, role, focus, and written time. It clips a combined role-and-focus summary, formats the written timestamp, and returns an ObjectRow named by the member id.

**Call relations**: ProfileObjects._page uses it for every listed profile, and ProfileObjects._entry uses it when returning one profile with detail. It keeps profile row formatting in one place.

*Call graph*: calls 1 internal fn (_stamp); called by 2 (_entry, _page); 2 external calls (__init__, clip_to_word).


### Monitoring and digest results
Covers long-running monitor objects and the read-only report records produced by scheduled radar runs.

### `extensions/monitors/ufo_ext_monitors/monitor_kind.py`

`domain_logic` · `request handling`

A monitor is like a reminder with a sensor attached. When it is armed elsewhere in chat, the system first runs a command and saves that first output as the baseline. Later probes compare against that baseline. This file does not arm new monitors, because arming needs that live first probe; instead it exposes already-armed monitors through the project’s object interface.

The file defines the shape of a monitor’s public settings with MonitorSpec: the command to run, how often to check, the deadline, and the reason for watching. MonitorObjects then connects those monitor records to the object system. For listing, it fetches all armed monitors from MonitorStore, adds owner information, and reports useful fields such as the next probe time, deadline, conversation, and whether the current member owns it. For getting one monitor, it returns the saved spec and a link back to the conversation where the monitor reports.

It also provides a status view: when it was armed, how many probes ran, how many were quiet or failed, how many were skipped, and a shortened baseline excerpt. Applying a monitor manifest is deliberately rejected with a helpful message. Deleting a monitor disarms it, but only after checking that the monitor record is still the same one the caller meant to stop.

#### Function details

##### `_owner`  (lines 53–58)

```
def _owner(row: Monitor) -> GeneratedObjectOwner
```

**Purpose**: Builds the ownership record that tells the object system who created a monitor and who it is shared with. This matters because monitor visibility follows the audience of the conversation it watches.

**Data flow**: It takes a monitor row from storage → reads the creator member id, audience, and monitor id → returns a GeneratedObjectOwner containing the creator, the sharing setting converted into object-system form, and the monitor id as the generation marker.

**Call relations**: When MonitorObjects._member_rows prepares the list of visible monitors, it calls _owner for each stored monitor. _owner delegates the audience conversion to subject_shared, then hands back the ownership facts that get attached to each listed row.

*Call graph*: called by 1 (_member_rows); 2 external calls (__init__, subject_shared).


##### `_require_ext`  (lines 61–64)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Checks that the monitor extension context is available before code tries to talk to monitor storage. It turns a missing setup problem into a clear error instead of letting later code fail mysteriously.

**Data flow**: It receives an optional ExtensionContext → if one is present, it returns it unchanged → if it is missing, it raises a RuntimeError explaining that this monitor kind needs the monitors extension context.

**Call relations**: Storage-facing methods call _require_ext before creating a MonitorStore. It is used by listing, finding, and deleting so those flows all fail early and clearly if the extension was not wired in.

*Call graph*: called by 3 (_delete_owned, _find, _member_rows).


##### `MonitorObjects._member_rows`  (lines 78–97)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Creates the rows shown when someone lists monitors. Each row is a compact summary of one armed watch, with enough information to recognize it and sort or filter it.

**Data flow**: It receives the extension context and the current member id → loads all armed monitors from MonitorStore → looks up the creators’ email addresses → turns each monitor into an OwnedRow with its name, short summary, owner details, conversation id, next probe time, deadline, owner email, and whether it belongs to the current member → returns all rows as a tuple.

**Call relations**: This is called by the broader object-listing machinery for the monitor kind. Inside that flow it first requires the extension context, then asks MonitorStore for active monitors, calls owner_emails to make the listing human-readable, and uses _owner to attach visibility and ownership information to each row.

*Call graph*: calls 2 internal fn (_owner, _require_ext); 3 external calls (__init__, __init__, owner_emails).


##### `MonitorObjects._member_object`  (lines 99–125)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[MonitorSpec] | None
```

**Purpose**: Builds the detailed view for one monitor when someone asks to inspect it. It returns the monitor’s main settings and points back to the conversation it reports into.

**Data flow**: It receives the extension context, requested name, expected owner information, and current member id → finds a monitor with that name → checks that its stored id still matches the requested owner generation → if anything does not match, returns nothing → otherwise returns an ObjectDetail containing the command, interval, deadline, reason, timestamps, and a link to the watched conversation.

**Call relations**: This is used by the object-get path for monitors. It relies on _find to locate the current armed monitor, then packages the result with MonitorSpec, ObjectDetail, ObjectLink, and ObjectRef so the object system can present it in a standard form.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `MonitorObjects._status`  (lines 127–142)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns the live status counters for one monitor. This gives a reader the operational story: when it last checked, whether checks are failing, and what baseline the next probe is compared with.

**Data flow**: It receives the tool context, monitor name, and expected owner information → finds the monitor in storage → verifies the stored id still matches the requested generation → if not found or changed, returns nothing → otherwise returns a dictionary with dates, probe counters, skipped count, and a shortened baseline string.

**Call relations**: The monitor object system calls this when it needs status information beside the saved monitor spec. It uses _find for the storage lookup and then hands back plain JSON-like values that can be shown to tools or users.

*Call graph*: calls 1 internal fn (_find).


##### `MonitorObjects._apply_owned`  (lines 144–152)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: MonitorSpec, old: MonitorSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Refuses to create or update monitors through the generic object apply operation. This is intentional because arming a monitor must happen in chat, where the first probe can run and seed the baseline.

**Data flow**: It receives the usual apply inputs: tool context, monitor name, desired spec, any old spec, and owner → ignores them for creation purposes → raises VerbNotSupported with an explanation of the correct arming path.

**Call relations**: The generic object mutation flow would call this if someone tried to apply a monitor manifest. Instead of modifying storage, it stops the flow immediately and returns the shared refusal message that points users toward the monitor action.

*Call graph*: 1 external calls (__init__).


##### `MonitorObjects._delete_owned`  (lines 154–159)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Stops an armed monitor. Deleting the object is the way this system disarms the watch so it will not probe or fire again.

**Data flow**: It receives the tool context, monitor name, and expected owner information → finds the named armed monitor → checks that its id still matches the requested generation → if it changed or disappeared, raises an error → otherwise asks MonitorStore to disarm it → if disarming fails because the record changed, raises the same kind of error.

**Call relations**: The object-delete flow calls this after the broader permission gate has allowed deletion. It uses _find to confirm the exact monitor, requires the extension context before opening MonitorStore, and then hands the row to MonitorStore.disarm to perform the actual stop.

*Call graph*: calls 2 internal fn (_find, _require_ext); 1 external calls (__init__).


##### `MonitorObjects._find`  (lines 161–165)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> Monitor | None
```

**Purpose**: Looks up an armed monitor by its object name. It is a small shared helper so get, status, and delete all search storage in the same way.

**Data flow**: It receives the extension context and a monitor name → checks that the extension context exists → loads all armed monitors from MonitorStore → returns the first row whose name matches → returns nothing if no armed monitor has that name.

**Call relations**: MonitorObjects._member_object, MonitorObjects._status, and MonitorObjects._delete_owned all call _find before doing their work. _find centralizes the storage lookup, so those higher-level flows can focus on packaging details, reporting status, or disarming the monitor.

*Call graph*: calls 1 internal fn (_require_ext); called by 3 (_delete_owned, _member_object, _status); 1 external calls (__init__).


### `extensions/report_digest/ufo_ext_report_digest/__init__.py`

`other` · `import setup`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label is what lets the rest of the program refer to the drawer by name. Without this file, some Python environments or packaging setups might not recognize `ufo_ext_report_digest` as a proper package, which could make imports fail. There are no functions, classes, settings, or startup actions here. Its value is structural: it helps connect the report digest extension’s files into the larger project.


### `extensions/report_digest/ufo_ext_report_digest/objects.py`

`domain_logic` · `request handling`

This file turns scheduled radar runs into a browsable object type called `report`. Think of it like a read-only filing cabinet: each drawer entry is one scheduled run, named by its turn ID, with a short summary and details about the conversation, task, status, digest entry, and shared files.

The important rule is that reports are created by running scheduled tasks, not by editing this object type. So listing and reading are allowed, but create, update, delete, and status-changing actions are refused. Without this file, users and tools would not have a standard object interface for finding past report runs or seeing the digest output and artifacts attached to them.

The file also protects access. It asks who the current member is, then reads only scheduled runs that member is allowed to see. Even an admin view does not widen the underlying audience rules for a run. After fetching runs, it enriches them in two ways: it looks up digest entries written by the report writer job, and it looks up the names of scheduled tasks that fired the runs. Finally it shapes each run into an `ObjectRow` or `ObjectDetail`, adding signed artifact links so files can be opened safely.

#### Function details

##### `ReportObjects.list`  (lines 66–76)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists report objects visible to the current tool user. It is used when a caller wants a page of recent report runs rather than one specific report.

**Data flow**: It starts with the tool context and list query. It reads the current authority to find the member ID; if there is no member, it returns an empty page. If there is a member, it extracts the extension context, keeps the current agent and read-audience limits, and asks `_page` to build the result page.

**Call relations**: This is the public listing path for the object store. It relies on `_ext` to find the extension services, then hands the real fetch-and-format work to `ReportObjects._page`. If there is no member identity, it uses `object_page` to return a harmless empty result.

*Call graph*: calls 2 internal fn (_page, _ext); 2 external calls (authority_member_id, object_page).


##### `ReportObjects.get`  (lines 78–88)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ReportSpec] | None
```

**Purpose**: Fetches one report object by name for the current tool user. The name is expected to be the scheduled run’s turn ID.

**Data flow**: It receives the tool context and a report name. It checks whether the authority belongs to a member; without a member it returns nothing. Otherwise it turns the context into an extension context, asks `_one` to find and build the report detail, and returns only the detail part if a report exists.

**Call relations**: This is the public single-object read path. It uses `_ext` for access to extension services and delegates the lookup and row construction to `ReportObjects._one`, which performs the ID parsing and scheduled-run read.

*Call graph*: calls 2 internal fn (_one, _ext); 1 external calls (authority_member_id).


##### `ReportObjects.member_page`  (lines 90–98)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists reports for a given member, usually for member-oriented views rather than a live tool call. It still uses the same report-building path as normal listing.

**Data flow**: It receives an extension context or carrier, the member ID, an admin flag, and a list query. It converts the carrier into an extension context, uses the object agent ID for this member-level view, and asks `_page` to fetch and format the reports.

**Call relations**: This is an alternate entry into the same paging machinery used by `ReportObjects.list`. It calls `_ext` and then `ReportObjects._page`; the provided admin flag is accepted by the interface but does not change the read-widening behavior in this file.

*Call graph*: calls 2 internal fn (_page, _ext); 1 external calls (object_agent_id).


##### `ReportObjects.member_detail`  (lines 100–108)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ReportSpec] | None
```

**Purpose**: Reads one report for a given member in member-oriented views. It returns the row plus full object detail when the named run exists and is visible.

**Data flow**: It receives an extension context or carrier, a report name, the member ID, and an admin flag. It resolves the extension context, passes the name and member ID to `_one`, and returns the resulting member object or nothing.

**Call relations**: This mirrors `ReportObjects.get` but for member-level callers. It uses `_ext` to get extension services and then relies on `ReportObjects._one` for the actual lookup and detail building.

*Call graph*: calls 2 internal fn (_one, _ext).


##### `ReportObjects.status`  (lines 110–117)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports do not have a separate live status operation, so this always says there is no status document. The run’s status is already included in the report fields.

**Data flow**: It receives the tool context, report name, and optional expected generation value. It does not read or change anything and returns `None`.

**Call relations**: This satisfies the object-store interface but intentionally does not connect to the rest of the report flow. Listing and detail reads expose status through `_row` instead.


##### `ReportObjects.apply`  (lines 119–128)

```
async def apply(self, ctx: ToolContext, name: str, spec: ReportSpec, old: ReportSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update a report object. Reports exist only because a scheduled task ran, so editing them through this object interface would give a false picture of how they are produced.

**Data flow**: It receives the context, report name, desired spec, previous spec, and optional generation check. Instead of saving anything, it raises a `VerbNotSupported` error with a message explaining that reports come from scheduled runs.

**Call relations**: This is called by the object system when someone tries an apply-style write. It stops that path immediately and does not hand off to any storage or formatting code.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects.delete`  (lines 130–137)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to delete a report object through this interface. Removing the object directly is not allowed because the object is a view of a scheduled run record.

**Data flow**: It receives the context, report name, and optional generation check. It does not remove data; it raises a `VerbNotSupported` error explaining that reports come from scheduled task runs.

**Call relations**: This is the delete-side guard for the object store. Like `ReportObjects.apply`, it blocks the write request before any lower-level report code is reached.

*Call graph*: 1 external calls (__init__).


##### `ReportObjects._page`  (lines 139–152)

```
async def _page(self, ext: ExtensionContext, member_id: UUID, *, agent_id: UUID, query: ObjectListQuery, subjects: frozenset[str] | None=None) -> ObjectPage
```

**Purpose**: Builds one page of report rows for a member. It fetches recent scheduled runs, enriches them, and applies the requested page shape.

**Data flow**: It receives an extension context, member ID, agent ID, list query, and optional audience subjects. It asks the extension context for up to 200 scheduled runs that match those limits. Then it turns those runs into rows with `_rows` and wraps them into an `ObjectPage` using the query settings.

**Call relations**: Both `ReportObjects.list` and `ReportObjects.member_page` call this after they have identified the member and extension context. `_page` is the bridge between raw scheduled-run records from the extension context and the object-page format expected by callers.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (list, member_page); 1 external calls (object_page).


##### `ReportObjects._one`  (lines 154–186)

```
async def _one(self, ext: ExtensionContext, name: str, *, member_id: UUID, subjects: frozenset[str] | None=None) -> MemberObject[ReportSpec] | None
```

**Purpose**: Finds and builds one detailed report object from a report name. It treats the name as a turn ID, because each report is named after the scheduled run turn that produced it.

**Data flow**: It receives an extension context, report name, member ID, and optional audience subjects. It first tries to parse the name as a UUID; if that fails, there can be no matching run. It then asks for the one scheduled run with that turn ID. If found, it builds a row through `_rows`, creates an empty `ReportSpec`, adds creation/update times from the run time, and links the report back to its conversation.

**Call relations**: `ReportObjects.get` and `ReportObjects.member_detail` use this for single-report reads. It calls the scheduled-run service for the raw record, uses `ReportObjects._rows` for the shared enrichment logic, and then wraps the result as a `MemberObject` with `ObjectDetail` metadata.

*Call graph*: calls 2 internal fn (scheduled_runs, _rows); called by 2 (get, member_detail); 6 external calls (__init__, __init__, __init__, __init__, __init__, UUID).


##### `ReportObjects._rows`  (lines 188–203)

```
async def _rows(self, ext: ExtensionContext, runs: tuple[ScheduledRun, ...]) -> tuple[ObjectRow, ...]
```

**Purpose**: Converts scheduled runs into object rows, adding digest text and task names along the way. This keeps list and detail views consistent.

**Data flow**: It receives a tuple of scheduled runs. It collects their turn IDs and asks `_entries` for any digest entries already written for them. It also extracts scheduled task IDs from run idempotency keys and asks `_task_names` for human-readable task names. Finally it calls `_row` once per run and returns all rows as a tuple.

**Call relations**: `ReportObjects._page` and `ReportObjects._one` both call this so they do not duplicate enrichment work. `_rows` coordinates the database lookups done by `_entries` and `_task_names`, then hands each fully supplied run to `ReportObjects._row` for final shaping.

*Call graph*: calls 3 internal fn (_entries, _row, _task_names); called by 2 (_one, _page); 1 external calls (scheduled_fire_task_id).


##### `ReportObjects._row`  (lines 205–247)

```
def _row(self, ext: ExtensionContext, run: ScheduledRun, entry: dict[str, JsonValue] | None, tasks: dict[UUID, str]) -> ObjectRow
```

**Purpose**: Turns one scheduled run into one object-list row. This is where the user-facing fields and summary text are assembled.

**Data flow**: It receives an extension context, one scheduled run, an optional digest entry, and a map of task IDs to task names. It derives the firing task ID when possible, chooses a summary from the digest title or from task/status/time, copies run metadata into fields, hides successful terminal text by making it empty, and converts each artifact into file information with signed download and preview links. It returns an `ObjectRow`.

**Call relations**: `ReportObjects._rows` calls this after gathering all supporting information. `_row` depends on extension context link helpers to create safe artifact URLs, and its output is later placed into pages or detailed member objects.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 1 (_rows); 2 external calls (__init__, scheduled_fire_task_id).


##### `ReportObjects._entries`  (lines 249–277)

```
async def _entries(self, ext: ExtensionContext, turn_ids: tuple[UUID, ...]) -> dict[UUID, dict[str, JsonValue]]
```

**Purpose**: Looks up digest entries for a group of scheduled runs. These entries contain the title, summary, and points written by the report-digest writer job.

**Data flow**: It receives an extension context and turn IDs. If there are no turn IDs, it returns an empty map. Otherwise it opens a database transaction, selects matching rows for the current workspace from the report digest entry table, and returns a dictionary keyed by turn ID with cleaned entry data.

**Call relations**: `ReportObjects._rows` calls this before building rows. The result is passed into `_row`, where a digest title can become the row summary and the full entry is included in the report fields.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `ReportObjects._task_names`  (lines 279–297)

```
async def _task_names(self, ext: ExtensionContext, task_ids: tuple[UUID, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up the object names of scheduled tasks that fired the visible runs. This lets a report say which scheduled task produced it, when that task still exists.

**Data flow**: It receives an extension context and task IDs. If there are no task IDs, it returns an empty map. Otherwise it opens a database transaction, selects matching scheduled-task IDs and names in the current workspace, and returns a dictionary from task ID to task name. Deleted or missing tasks simply do not appear in the result.

**Call relations**: `ReportObjects._rows` calls this after extracting task IDs from run idempotency keys. The resulting name map is passed to `_row`, which uses it in the row summary and `task` field.

*Call graph*: calls 1 internal fn (transaction); called by 1 (_rows); 1 external calls (select).


##### `_ext`  (lines 300–305)

```
def _ext(carrier: ToolContext | ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Finds the `ExtensionContext`, which is the object that gives this file access to extension services such as scheduled runs, database transactions, and artifact links.

**Data flow**: It receives either an extension context directly, a tool context that may contain one, or `None`. If it is already an extension context, it returns it. If it is a tool context with an extension context attached, it returns that. If neither is true, it raises a runtime error because report objects cannot work without extension services.

**Call relations**: `ReportObjects.list`, `ReportObjects.get`, `ReportObjects.member_page`, and `ReportObjects.member_detail` call this at the edge of their flows. It prevents the rest of the file from having to care whether the caller supplied a tool context or an extension context directly.

*Call graph*: called by 4 (get, list, member_detail, member_page).


### Hosted and user-authored assets
Exposes hosted sites, saved user-created skills, and conversation todo lists as extension-owned workspace assets.

### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed site is not just a running port. It also needs a stable name, a link, ownership, and rules about who may see or change it. This file provides that bridge. It turns each registered hosted site into a “site” object named from the site name plus a short digest of the conversation that created it, so two conversations can both have a site called “dashboard” without colliding.

The file reads site records from the workspace site store, wraps them in object-list rows, and adds useful fields such as the creator email, conversation id, hosted URL, preview image URL, and deploy generation. It also enforces the sharing rules. The creator can see their own site. Workspace admins can see all sites. Other members can see sites once they are shared beyond private. A workspace admin may narrow a site to private, but may not widen it.

There is one special case: a site can be bound as an agent’s homepage. Then the site’s own visibility is ignored while bound; the agent’s visibility decides who can see it. Such homepage sites are hidden from normal browsing unless the caller asks for the homepage binding. Delete means “unhost”: the registry entry is removed and the public link stops resolving.

#### Function details

##### `site_object_name`  (lines 81–85)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the public object name for a hosted site. It combines the human site name with a short fingerprint of the conversation id, preventing two conversations from fighting over the same object name.

**Data flow**: It receives a conversation id and a site name. It hashes the conversation id, keeps a short prefix of that hash, and appends it to the site name with a dash. The result is a stable object name such as a site name plus a conversation-specific suffix.

**Call relations**: This is the naming rule used wherever sites are exposed as objects. Listing code uses it through _named, conversation grant code uses it when reporting visible sites, and site_name_from_object uses it to check whether an object name really belongs to a conversation.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 88–94)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from an object name for a particular conversation. It is a safety check that the suffix on the object name matches the conversation being considered.

**Data flow**: It receives a conversation id and an object name. It recomputes the expected conversation digest, checks whether the object name ends with that suffix, removes it, and verifies that rebuilding the object name gives the same string. It returns the plain site name if valid, otherwise None.

**Call relations**: It relies on site_object_name so parsing and creating names stay consistent. This keeps callers from accidentally treating a site from one conversation as if it belonged to another.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 97–98)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a group of hosted site records into a lookup table keyed by their object names. This lets later code find a site by the name used in the object system.

**Data flow**: It receives an iterable of hosted site records. For each site, it computes the object name from the site’s conversation id and site name. It returns a dictionary from object name to the original site record.

**Call relations**: It is used by _member_rows to prepare list output and by _find to search for one named site. It delegates the exact naming rule to site_object_name.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 101–104)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Ensures the site object code has an extension context, which is the workspace-scoped bundle of store access, transaction state, and helper services. Without it, this object kind cannot read the workspace site registry.

**Data flow**: It receives an optional extension context. If the context is missing, it raises an error explaining that this object kind must read through that context. If present, it returns the context unchanged.

**Call relations**: Most operations call this before touching workspace-specific information. _sites uses it to open the hosted-site store, and methods such as _member_rows, _member_object, _status, _apply_owned, and member_conversation_rows use it to read workspace data.

*Call graph*: called by 6 (_apply_owned, _member_object, _member_rows, _status, member_conversation_rows, _sites).


##### `_sites`  (lines 107–109)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates the workspace-specific access object for hosted sites. This is the doorway from object operations into the stored registry of deployed sites.

**Data flow**: It receives an optional extension context, verifies it through _workspace, then uses the workspace id and current transaction from that context to construct a HostedSites store helper. The result is an object that can list, update, or unregister hosted sites.

**Call relations**: Read paths call it to fetch all sites or conversation-visible sites. Change paths call it to set visibility or unregister a site. It is the shared store access helper for _member_rows, member_conversation_rows, _find, _apply_owned, and _delete_owned.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `effective_visibility`  (lines 112–118)

```
def effective_visibility(site: HostedSite, agents: Mapping[UUID, str]) -> Visibility
```

**Purpose**: Decides which visibility rule actually applies to a site. For normal sites it is the site’s own setting; for an agent homepage, it follows the agent’s visibility instead.

**Data flow**: It receives a hosted site and a mapping from agent ids to their visibility levels. If the site is not bound to an agent homepage, it returns the site’s visibility. If it is bound, it looks up the bound agent and converts that agent level into the site visibility value used by this subsystem.

**Call relations**: List, detail, and update code call this whenever they need the real audience for a site. _apply_owned uses it to detect no-op changes and to reject direct changes to homepage-bound sites.

*Call graph*: called by 3 (_apply_owned, _member_object, _member_rows); 1 external calls (visibility_level).


##### `_summary`  (lines 121–122)

```
def _summary(site: HostedSite, visibility: Visibility) -> str
```

**Purpose**: Creates a short human-readable label for a listed site. It gives the site name, current visibility, and sandbox port in one line.

**Data flow**: It receives a hosted site and the visibility that should be shown. It formats those values into a compact string. The returned text becomes the row summary in object listings.

**Call relations**: _member_rows calls this while building each object-list row, so people scanning a list can quickly tell what the site is and how it is shared.

*Call graph*: called by 1 (_member_rows).


##### `_preview_url`  (lines 125–132)

```
def _preview_url(scoped: ExtensionContext, site: HostedSite) -> str | None
```

**Purpose**: Returns a signed link to the screenshot captured during the site’s last deployment, when such a screenshot exists. This lets the object list show a preview without inventing a separate serving route.

**Data flow**: It receives the workspace context and a hosted site. If the site has no stored preview blob or size, it returns None. Otherwise it asks the context to create an image preview URL for that stored blob and returns that URL.

**Call relations**: _member_rows calls this while building list rows. When it returns a URL, that URL is added as preview_url so clients can show a visual card for the site.

*Call graph*: calls 1 internal fn (image_preview_url); called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 153–154)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin may make to someone else’s site: making a non-private site private. It prevents admins from widening access to a site they did not deploy.

**Data flow**: It receives the old site spec and the requested new spec. It checks whether the old visibility was not private and the requested visibility is private. It returns true only for that narrowing change.

**Call relations**: This is a policy hook used by the shared object framework during apply operations. It works alongside _apply_owned, which performs the actual visibility update once the framework has decided the caller is allowed to proceed.


##### `SiteObjects._listed`  (lines 156–157)

```
def _listed(self, row: OwnedRow[GeneratedObjectOwner], query: ObjectListQuery) -> bool
```

**Purpose**: Controls whether a site row should appear in normal listings. It hides agent-homepage sites unless the listing explicitly asks for homepage bindings.

**Data flow**: It receives a prepared object row and the list query. If the row has no homepage_agent field, it returns true. If the row is a homepage-bound site, it returns true only when the query includes a homepage_agent filter.

**Call relations**: This is another hook for the object listing machinery. It keeps regular site browsing focused on workspace-shared sites, while still allowing targeted reads for an agent’s homepage.


##### `SiteObjects.list`  (lines 159–169)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Adds a convenient shortcut for agents listing their own homepage site. A caller can filter homepage_agent as "mine" instead of knowing its own agent id.

**Data flow**: It receives the tool context and an object-list query. If the query asks for homepage_agent equal to "mine", it replaces that filter value with the current turn’s agent id. It then passes the adjusted query to the parent listing implementation and returns the resulting page.

**Call relations**: This method sits at the start of list requests for site objects. After translating the viewer-relative "mine" value, it hands the normal listing work back to the shared MemberReadableObjects flow, which will later use hooks such as _member_rows and _listed.

*Call graph*: 1 external calls (replace).


##### `SiteObjects._member_rows`  (lines 171–217)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the complete set of site rows that the object system can filter and show to a member. Each row includes ownership, sharing state, links, preview information, and other useful facts.

**Data flow**: It receives the extension context and the member id of the viewer, if any. It reads all hosted sites from the workspace, reads agent visibility settings, gathers creator email addresses, computes each site’s effective visibility, and builds OwnedRow objects. The output is a tuple of rows ready for the object-list system to filter and display.

**Call relations**: The listing flow calls this to obtain raw site rows. It uses _sites to read the registry, _named to key rows by object name, effective_visibility for sharing rules, _summary for row text, _preview_url for screenshot links, and site_url to include a reachable hosted URL when a public base URL exists.

*Call graph*: calls 6 internal fn (_named, _preview_url, _sites, _summary, _workspace, effective_visibility); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 219–244)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects from one conversation are visible enough to be mentioned in that conversation’s object view. It is the bridge between conversation visibility and hosted-site visibility.

**Data flow**: It receives a conversation id, member id, admin flag, and limit. It reads agent visibility settings, asks the hosted-site store for sites in that conversation visible to the member, and turns each visible site into a ConversationObjectGrant. The result tells the conversation object system which site names and generations can be shown.

**Call relations**: Conversation-level object discovery calls this when it needs grants for site objects. It uses _workspace to read agent visibility, _sites to query visible conversation sites, and site_object_name to report names in the same format used everywhere else.

*Call graph*: calls 3 internal fn (_sites, _workspace, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 246–269)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object view for one site when a member asks for it. It returns the current visible spec and a link back to the conversation that created the site.

**Data flow**: It receives the extension context, object name, owner information, and viewer member id. It looks up the hosted site by name. If not found, it returns None. If found, it computes effective visibility and returns an ObjectDetail containing a SiteSpec, timestamps, and a created_in link to the source conversation.

**Call relations**: The object-get flow calls this after access checks have identified a readable row. It relies on _find to locate the site, _workspace and effective_visibility to show the correct sharing level, and object-link types to connect the site back to its conversation.

*Call graph*: calls 3 internal fn (_find, _workspace, effective_visibility); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 271–298)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Produces operational details for a hosted site, including its public URL and, when available, a materialized copy of its deployed source files. This is what lets a caller inspect and edit a deployed static site.

**Data flow**: It receives the tool context, object name, and owner information. It finds the site; if missing, it returns None. It builds a status dictionary with the site name, port, creator, URL, deploy generation, homepage binding, and empty source fields. If the site has a stored source manifest, it materializes those files into the sandbox and adds the destination path and file list.

**Call relations**: The object status flow calls this when a caller asks for more than the basic object detail. It uses _find for lookup, _workspace for workspace id, site_url for the hosted link, and materialize_source to copy stored source into the sandbox for editing.

*Call graph*: calls 2 internal fn (_find, _workspace); 2 external calls (materialize_source, site_url).


##### `SiteObjects._apply_owned`  (lines 300–336)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies an allowed visibility change to an existing site. It refuses creation through the object API because sites must come from deployment, and it refuses direct changes to a site that is currently an agent homepage.

**Data flow**: It receives the tool context, object name, requested spec, previous spec, and owner. If there is no existing object, it raises an unsupported-verb error. It finds the current site, compares the requested visibility with the effective current visibility, and returns early if nothing changes. If the site is homepage-bound, it raises an error telling the caller to change the agent instead. Otherwise it updates the site store. If the site is becoming public and has a preview image but no share card, it draws a share card from that stored screenshot.

**Call relations**: The object apply flow calls this after permission checks. It uses _find and _sites to locate and update the registry, effective_visibility to understand the current gate, and draw_from_stored_shot to prepare a public-facing preview card when a site first becomes public.

*Call graph*: calls 4 internal fn (_find, _sites, _workspace, effective_visibility); 2 external calls (__init__, draw_from_stored_shot).


##### `SiteObjects._delete_owned`  (lines 338–342)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts an existing site. In practical terms, deleting the object removes the site’s registry entry so its permanent link no longer resolves.

**Data flow**: It receives the tool context, object name, and owner. It finds the site by name. If the site disappeared during the operation, it raises an error. Otherwise it asks the hosted-site store to unregister that conversation/name pair. The output is no return value, but the store is changed.

**Call relations**: The object delete flow calls this after confirming the caller is allowed to unhost the site. It uses _find for safety and _sites to perform the actual unregister operation.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 344–345)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by its object-system name. It is the common search helper for detail, status, visibility changes, and deletion.

**Data flow**: It receives the extension context and object name. It reads all hosted sites through _sites, converts them into a name-to-site dictionary with _named, and returns the matching HostedSite if present. If no match exists, it returns None.

**Call relations**: _member_object, _status, _apply_owned, and _delete_owned all call this before working on a specific site. By centralizing lookup, they all use the same object naming rule and store view.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### `extensions/skill_create/ufo_ext_skill_create/store.py`

`domain_logic` · `request handling for workspace skill save, load, list, and delete operations`

A “skill” here is a small bundle of files, usually including instructions in SKILL.md, that agents can load and use later. This file makes those user-authored skills durable: they survive across chat turns and temporary sandboxes because they are stored in a database table, one row per workspace and skill name.

The main class, UserSkillStore, is the doorway for all skill storage actions. When saving, it first checks that the skill name is a safe lowercase slug, then parses the skill files to make sure the content is valid. It stores the file bytes as base64 text, which is a safe way to put binary data into a text database column. It also records summary information, such as the description, dependencies, allowed agents, whether the skill is pinned, and a digest, which is like a fingerprint of the stored content.

A key idea is the generation value. This is a unique version marker that changes on every save. If two writers read the same skill and both try to save changes, the second save is rejected instead of silently overwriting the first. This is like requiring a claim ticket before editing a shared document.

The file also supports lightweight listings, full record reads, parsing stored skills back into runtime objects, and deletion. Deletion also cleans up search index data when an index exists, so old skill content does not linger after the skill is gone.

#### Function details

##### `_save_lock_key`  (lines 54–56)

```
def _save_lock_key(workspace_id: UUID) -> int
```

**Purpose**: This helper turns a workspace ID into a stable number used as a database lock key. The lock helps serialize skill saves inside one workspace so two saves cannot race past limits or overwrite checks.

**Data flow**: It receives a workspace UUID, converts it to text, hashes that text with SHA-256, takes the first eight bytes of the hash, and turns those bytes into a signed integer. The result is a repeatable lock number for that workspace.

**Call relations**: UserSkillStore.save calls this before writing to the database. On PostgreSQL, save uses the returned number to ask the database for a transaction-level advisory lock, which is a lock chosen by the application rather than tied to one specific row.

*Call graph*: called by 1 (save); 1 external calls (sha256).


##### `UserSkillStore.save`  (lines 123–238)

```
async def save(self, name: str, files: Mapping[str, bytes], registry_names: frozenset[str], pinned: bool=False, generation: UUID | None=None) -> RuntimeSkill
```

**Purpose**: This function validates and saves one workspace skill. It protects existing work by requiring the caller to provide the version it previously read, and it rejects unsafe names, collisions with built-in skills, too many saved skills, or too many pinned skills.

**Data flow**: It takes a skill name, a mapping of file paths to bytes, the set of already-reserved registry skill names, a pinned flag, and an optional generation value. It checks the name, parses the skill files, encodes the files for storage, computes a content fingerprint, and opens a database transaction. Inside that transaction it compares the supplied generation with the row currently stored, checks workspace caps, then inserts a new row or updates the existing one with a fresh generation. It returns the parsed RuntimeSkill object and changes the database.

**Call relations**: This is the central write path for the store. It uses _save_lock_key to prevent concurrent saves in the same workspace from slipping past checks, _count to enforce the total skill cap, and _pinned_count to enforce the pinned-skill cap. It also relies on parse_skill_content to make sure the same content that is stored is also what creates the skill’s routing information.

*Call graph*: calls 3 internal fn (_count, _pinned_count, _save_lock_key); 16 external calls (__init__, __init__, __init__, __init__, __init__, __init__, b64encode, sha256, dumps, cast (+6 more)).


##### `UserSkillStore.cards`  (lines 240–281)

```
async def cards(self) -> tuple[SkillCard, ...]
```

**Purpose**: This function returns a compact routing card for every saved skill in the current workspace. A routing card is the small summary an agent can use to decide whether a skill is relevant, without loading the full skill files.

**Data flow**: It reads the current workspace ID, queries the database for each skill’s name, description, dependencies, allowed agents, and pinned flag, then turns those rows into SkillCard objects. If a row has no description, it skips that row and logs a warning, because such a card would not be useful for routing.

**Call relations**: This is used when the system needs summaries of available user skills rather than the full stored bundles. Unlike materialize or files, it does not decode or parse the full content, so a corrupt stored bundle can still have its card returned as long as its card columns are usable.

*Call graph*: 4 external calls (__init__, loads, select, agent_current).


##### `UserSkillStore.listing`  (lines 283–307)

```
async def listing(self) -> tuple[SkillListing, ...]
```

**Purpose**: This function returns the simple list shown for saved user skills: name, description, and whether each skill is pinned. It is meant for display rather than for running the skills.

**Data flow**: It reads the current workspace ID, queries the database for saved skills in name order, and creates SkillListing objects from rows that have a non-empty description. Rows with empty descriptions are left out, matching the behavior of cards.

**Call relations**: This is the lightweight display path for the object listing. It uses the database card-like columns and does not load the stored file bundle, so it stays fast and avoids failing just because one skill’s stored content is damaged.

*Call graph*: 3 external calls (__init__, select, agent_current).


##### `UserSkillStore.record`  (lines 309–340)

```
async def record(self, name: str) -> SkillRecord | None
```

**Purpose**: This function reads one saved skill in full, including its files, description, version generation, pin state, and timestamps. It is used when a caller needs the exact stored object, for example before editing it.

**Data flow**: It receives a skill name and looks for that name in the current workspace. If no row exists, it returns None. If a row exists, it validates the stored JSON bundle, decodes each base64 file back into bytes, and returns a SkillRecord containing the decoded files and metadata.

**Call relations**: This is the full-detail read path. Its generation value is important because UserSkillStore.save expects callers editing an existing skill to send back the generation they read here, preventing accidental overwrites.

*Call graph*: 4 external calls (__init__, b64decode, select, agent_current).


##### `UserSkillStore.materialize`  (lines 342–349)

```
async def materialize(self, name: str) -> RuntimeSkill | None
```

**Purpose**: This function loads one saved skill and turns it back into a RuntimeSkill, the form the agent can actually use. It returns None if the skill name is not saved in the workspace.

**Data flow**: It receives a skill name, asks files for that skill’s decoded file bundle, and stops with None if no files are found. Otherwise it parses those files with parse_skill_content and returns the resulting RuntimeSkill.

**Call relations**: This is the named load path for execution. It delegates the database and decoding work to UserSkillStore.files, then hands the decoded bundle to the skill parser so the runtime receives a validated skill object.

*Call graph*: calls 1 internal fn (files); 1 external calls (parse_skill_content).


##### `UserSkillStore.materialize_all`  (lines 351–379)

```
async def materialize_all(self) -> tuple[RuntimeSkill, ...]
```

**Purpose**: This function loads every saved skill in the workspace and turns each valid one into a RuntimeSkill. If one saved bundle is corrupt, it logs the problem and keeps loading the rest.

**Data flow**: It reads all skill names and stored content for the current workspace from the database. For each row, it validates the stored JSON, decodes base64 file contents into bytes, parses the skill files, and adds the resulting RuntimeSkill to the output. If any row fails during this process, that row is skipped and a warning is logged. The result is a tuple of successfully loaded skills.

**Call relations**: This is the bulk load path. It is more tolerant than materialize: loading one named skill fails loudly if it is corrupt, but loading all skills avoids letting one bad row hide every other usable workspace skill.

*Call graph*: 4 external calls (b64decode, select, agent_current, parse_skill_content).


##### `UserSkillStore.files`  (lines 381–396)

```
async def files(self, name: str) -> dict[str, bytes] | None
```

**Purpose**: This function returns the raw files for one saved workspace skill. It is useful when another part of the system wants the stored bytes rather than a parsed RuntimeSkill.

**Data flow**: It receives a skill name, looks up the stored content for that name in the current workspace, and returns None if no row exists. If the row exists, it validates the stored JSON and decodes each base64 string back into bytes, returning a dictionary from file path to file bytes.

**Call relations**: UserSkillStore.materialize calls this as its first step. files does the database lookup and byte decoding, while materialize takes those bytes and parses them into a runnable skill.

*Call graph*: called by 1 (materialize); 3 external calls (b64decode, select, agent_current).


##### `UserSkillStore.delete`  (lines 398–420)

```
async def delete(self, name: str) -> None
```

**Purpose**: This function deletes one saved skill from the workspace. If a search index is present, it also removes that skill’s indexed content so deleted skill text does not remain searchable.

**Data flow**: It receives a skill name and reads the current workspace ID. If indexing exists, it first marks the skill row as needing no indexed digest, commits that change, and asks the index to delete the scope for this skill. Then it deletes the skill row from the database. The output is no returned value, but the database and possibly the index are changed.

**Call relations**: This is the removal path for stored skills. Its order is deliberate: first make any existing row look stale to the indexing system, then prune the index, then delete the row. That way, if a crash happens midway, the system is left in a state that a later index job can repair rather than with hidden orphaned index chunks.

*Call graph*: 4 external calls (__init__, delete, update, agent_current).


##### `UserSkillStore._count`  (lines 422–430)

```
async def _count(self, connection: AsyncConnection) -> int
```

**Purpose**: This helper counts how many user-created skills are currently saved in the workspace. It is used to enforce the maximum number of saved skills per workspace.

**Data flow**: It receives an open asynchronous database connection, reads the current workspace ID, runs a count query over the user_skill table for that workspace, and returns the integer count.

**Call relations**: UserSkillStore.save calls this while saving a new skill. Because save calls it inside the same transaction used for the insert, the count check and the actual write happen as one protected operation.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


##### `UserSkillStore._pinned_count`  (lines 432–444)

```
async def _pinned_count(self, connection: AsyncConnection, excluding: str) -> int
```

**Purpose**: This helper counts how many pinned user skills a workspace has, excluding one named skill. It lets an already-pinned skill be re-saved without counting against itself.

**Data flow**: It receives an open database connection and the name to exclude. It reads the current workspace ID, counts rows in that workspace where pinned is true and the name is different from the excluded name, and returns the integer count.

**Call relations**: UserSkillStore.save calls this when a save asks for a skill to be pinned. The result tells save whether adding or keeping this pin would exceed the workspace’s pinned-skill limit.

*Call graph*: called by 1 (save); 3 external calls (execute, select, agent_current).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation display`

This file solves a simple but important problem: long requests often have several steps, and both the user and the agent need a shared sense of what is planned and what is already done. It acts like a small clipboard that stays attached to one conversation.

The file defines the shape of a todo item, a whole todo board, and the two tools the agent can call. One tool, `update_todo_list`, creates or replaces the whole checklist. The other, `update_todo_status`, changes the status of individual tasks, such as from `pending` to `in_progress` or `completed`.

The checklist is not just kept in the agent's short-term prompt text. It is saved in the extension's scoped store, keyed by the conversation ID. That means a later turn in the same conversation can read the same board back. This is like putting the checklist in a labeled folder rather than scribbling it on a disposable note.

The file also exposes the checklist as a conversation slot, which is a structured piece of conversation-side data the rest of the system can ask for. When displayed, long titles, long task descriptions, or too many tasks are trimmed to safe limits, and the result says whether anything was truncated. Without this file, the agent could still talk about plans, but it would not have a durable, validated progress board that tools and the UI can rely on.

#### Function details

##### `_require_ext`  (lines 87–90)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a todo tool was called with the extension context it needs. The extension context is the object that gives access to the extension's saved store.

**Data flow**: It receives a tool call context. If that context contains an extension object, it returns it. If not, it stops the call with an error, because the todo tools cannot read or write their saved checklist without it.

**Call relations**: Before either todo-changing tool does real work, it calls this helper. `update_todo_list` and `update_todo_status` both depend on the returned extension context so they can write to, or read from, the scoped store.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 93–94)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper builds the storage name used for one conversation's todo board. It keeps todo lists from different conversations separated.

**Data flow**: It receives a conversation ID and turns it into a string key by adding the todo prefix in front of it. The result is the exact key used when reading from or writing to the extension store.

**Call relations**: Whenever code needs to find the board for a conversation, it asks this helper for the key. The create, update, summary, and read paths all use it so they agree on where the checklist lives.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 97–98)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper packages the current todo board into the standard result format returned by a tool. It ensures the caller sees the latest checklist after a change.

**Data flow**: It receives a validated todo board, converts it to JSON text, wraps that text in a text content object, and then wraps that in a tool result. The output is ready to send back to the model or tool caller.

**Call relations**: After `update_todo_list` creates a board, or `update_todo_status` changes one, they both call this helper. It is the final step that turns internal checklist data into the tool response.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 101–103)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store. It also validates the saved data before the rest of the code uses it.

**Data flow**: It receives the extension context and a storage key. It asks the store for the raw saved value. If nothing is saved, it returns `None`; otherwise it turns the saved value back into a `TodoBoard` object with the expected fields.

**Call relations**: `update_todo_status` uses this before changing task statuses. The conversation slot functions also use it when they need to summarize or display the current checklist.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 106–110)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or replaces the full todo checklist for the current conversation. The agent uses it at the start of multi-step work, or when the plan changes enough that the whole list should be revised.

**Data flow**: It receives the tool context and the requested title and tasks. It checks that the extension store is available, builds a new todo board from the input, saves that board under the current conversation's key, and returns the saved board as JSON text. Any previous board for that conversation is replaced.

**Call relations**: This is one of the two public tools registered by `manifest`. It relies on `_require_ext` for store access, `_board_key` to choose the right conversation-specific storage key, and `_board_result` to return the current board to the caller.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 113–124)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that updates the status of existing tasks in the current checklist. The agent uses it to mark work as started or finished as progress happens.

**Data flow**: It receives the tool context and one or more status updates. It checks for the extension store, finds the current conversation's board, and refuses to continue if no list exists yet. For each update, it treats the task number as 1-based, checks that the number is inside the list, changes that task's status, saves the revised board, and returns the updated board as JSON text.

**Call relations**: This is the second public tool registered by `manifest`. It depends on `_read_board` to load the current checklist, `_board_key` to find it, `_require_ext` to access the store, and `_board_result` to show the caller the new state after saving.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 127–129)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick summary of whether a conversation has tasks, and how many. It is used when the system only needs a small overview rather than the full checklist.

**Data flow**: It receives a conversation slot context, reads the board for that conversation, and returns nothing if there is no board. If a board exists, it returns the number of tasks on it.

**Call relations**: It is attached to the `TASKS_SLOT` provider as the summary reader. The conversation slot system calls it when it wants a lightweight count, and it uses `_board_key` and `_read_board` to find the saved board.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 132–162)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This function prepares the saved checklist for display or structured conversation use. It returns a safe, limited version of the board, including counts and whether any content was shortened.

**Data flow**: It receives a conversation slot context and reads the board for that conversation. If there is no board, it returns an empty task payload. If there is a board, it trims the title, limits the number of tasks, trims long descriptions, counts completed tasks, notes whether anything was truncated, and returns a structured task payload.

**Call relations**: It is attached to the `TASKS_SLOT` provider as the full reader. The conversation slot system calls it when it needs the actual task data, and it relies on `_board_key` and `_read_board` before building the display-friendly task objects.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 175–197)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the todo extension to the host system. It tells the system what the extension is called, which tools it offers, what prompt instructions it adds, and what conversation slot it provides.

**Data flow**: It takes no input. It builds and returns a manifest containing the extension name and version, two tool definitions, one prompt section loaded from the nearby markdown file, and the tasks conversation slot.

**Call relations**: The host system calls this when loading the extension. Through the returned manifest, `update_todo_list` and `update_todo_status` become callable tools, and the task slot becomes available for summaries and display.

*Call graph*: 3 external calls (__init__, __init__, __init__).

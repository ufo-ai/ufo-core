# Source Registration, Connected Feeds, and Triggers  `stage-16.3`

This stage is shared behind-the-scenes support for bringing outside content into the system and letting conversations react when that content changes. A “source” here means a registered stream of content, such as a feed from a connected provider. The tools file defines the user-facing pieces: agents can register a provider stream to be synced, look up what sources exist, remove them, and set a trigger, meaning a request to wake a conversation when a shared source updates.

The connected-account file acts like an automatic setup helper. When a member links an external account, it creates the private feed records for that account’s main streams right away, so the member does not need to configure them separately. If that first setup is interrupted or incomplete, it also offers a retry path to fill in the missing feed rows later.

The triggers file is the memory for wake-up requests. It stores which conversations are watching which shared sources, and provides safe ways to add, remove, find, and list those rules inside one workspace.

## Files in this stage

### Source feeds and triggers
User-facing source registration, connected-account feed setup, and shared-source wake-up rules are defined together for source management.

### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object requests and page-change hooks`

This file is the control desk for source syncing. A “source” here means a saved connection to an outside provider, such as a CRM or helpdesk, plus the particular streams of content to import. Without this file, users could not create those saved sync bindings through the object tools, safely change which streams are synced, or set up conversations that react when synced pages change.

The file turns lower-level sync rows into two understandable object kinds. The first, `source`, groups one row per stream into a single binding named from its provider, account, and tenant URL. That name is important: it prevents duplicate bindings that secretly point at the same account. Applying a source validates the provider, streams, account access, optional tenant URL, sharing choice, and backfill window. Adding a stream registers a new row; dropping a stream removes that row and its synced pages.

The second object kind, `source_trigger`, is like a standing alarm bell. It watches one shared source from one conversation. When page changes arrive, the file filters out private or unreadable changes, writes a small change log when possible, and wakes the right conversation or per-page conversation. The result is a safe bridge between background syncing and agents that need to tell people what changed.

#### Function details

##### `_Binding.name`  (lines 218–219)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. The name is derived from the provider, account, and tenant URL so the same real binding always has the same name.

**Data flow**: It reads the binding’s provider, account, and base URL → passes them to the shared naming helper → returns the stable source name.

**Call relations**: Other code uses this property whenever it needs to compare, list, or report a binding by name. It relies on the common `binding_name` rule so source objects and trigger objects agree about names.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 222–223)

```
def created_at(self) -> datetime
```

**Purpose**: Reports when the whole binding first came into existence. Since a binding is made of several stream rows, it uses the oldest stream creation time.

**Data flow**: It reads all stream creation timestamps → finds the earliest one → returns that as the binding’s creation time.

**Call relations**: Source object detail uses this to show a single created time for a multi-stream binding.


##### `_Binding.updated_at`  (lines 226–227)

```
def updated_at(self) -> datetime
```

**Purpose**: Reports when any part of the binding was last changed. It uses the newest update time among the streams.

**Data flow**: It reads all stream update timestamps → finds the latest one → returns that as the binding’s update time.

**Call relations**: Source object detail uses this to show whether any stream in the binding has changed recently.


##### `_Binding.links`  (lines 229–242)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: Describes what authentication object this source depends on. In plain terms, it points either to a workspace credential slot or to a connected account, unless the source is shared and the private connection should not be exposed.

**Data flow**: It reads the binding’s account and sharing subject → decides whether the source uses a direct credential, a private connection, or no visible link → returns object links for the readable dependency.

**Call relations**: Source object reads call this when building object details, so users can see what grants access to the synced provider without leaking private connection details for shared sources.

*Call graph*: 4 external calls (__init__, __init__, credential_object_name, account_object_name).


##### `_Binding.spec`  (lines 244–252)

```
def spec(self) -> SourceSpec
```

**Purpose**: Turns an internal binding back into the public source specification users see. This is the readable form of the saved source.

**Data flow**: It reads provider, streams, account, base URL, sharing subject, and backfill setting → converts internal values like the direct-account marker into user-facing fields → returns a `SourceSpec`.

**Call relations**: Source get operations use this to show the current source manifest, and apply logic compares it with new requests to decide what changed.

*Call graph*: 2 external calls (__init__, subject_shared).


##### `_Binding.summary`  (lines 254–256)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a binding. It names the provider, account, and streams in one compact sentence.

**Data flow**: It reads the provider, account, and stream names → joins the streams into text → returns a shortened summary string.

**Call relations**: Listings and alert messages use this summary so people see useful context instead of only an opaque derived name.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 265–268)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Makes sure an extension context is present before source code tries to use it. The extension context is the object that gives access to stored sources, files, credentials, and other runtime services.

**Data flow**: It receives an optional context → if it is missing, raises a clear runtime error → otherwise returns the context unchanged.

**Call relations**: Most source and trigger operations call this before touching extension services. It catches wiring mistakes early instead of failing later with confusing missing-value errors.

*Call graph*: called by 10 (_apply_owned, _delete_owned, _grant_settled, _member_rows, _resolved_account, _resync, _widen_window, _member_rows, _binding_named, _require_triggers).


##### `_require_connectors`  (lines 271–274)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Makes sure the current tool turn has a connector registry. The registry is the catalog that knows which providers can be connected and how.

**Data flow**: It receives the tool context → checks whether connector information is attached → returns it or raises a clear runtime error.

**Call relations**: `SourceObjects._resolved_account` calls this while deciding whether a source should use a connected account or a workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 277–312)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Rebuilds user-level source bindings from the lower-level stored source rows. Each stream is stored separately, so this function groups rows that belong to the same provider account and tenant URL.

**Data flow**: It asks the extension context for all source rows → ignores rows from unknown backends → reads each row’s connector configuration → groups streams into `_Binding` objects → returns the bindings sorted internally by stream name.

**Call relations**: Listing sources, finding one source by name, listing triggers with source summaries, and handling page-change hooks all start here because they need the grouped binding view.

*Call graph*: calls 1 internal fn (sources); called by 4 (_member_rows, _member_rows, _binding_named, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_binding_named`  (lines 315–325)

```
async def _binding_named(ext: ExtensionContext | None, name: str) -> _Binding | None
```

**Purpose**: Finds one registered source binding by its derived object name. It is the shared lookup used by both source operations and trigger operations.

**Data flow**: It receives an extension context and a name → rebuilds all bindings → returns the first binding whose derived name matches, or `None` if there is no match.

**Call relations**: Source reads, writes, deletes, resyncs, grants, and trigger creation call this whenever they need to confirm that a named source really exists.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 7 (_apply_owned, _delete_owned, _grant_settled, _member_object, _resync, _status, _apply_owned).


##### `_require_triggers`  (lines 328–329)

```
def _require_triggers(ext: ExtensionContext | None) -> SourceTriggerStore
```

**Purpose**: Creates access to the trigger store after confirming the extension context exists. The trigger store is where source-trigger rows are saved.

**Data flow**: It receives an optional extension context → verifies it is present → wraps it in a `SourceTriggerStore` → returns that store.

**Call relations**: Trigger listing, creation, lookup, deletion, source deletion cleanup, and page-change delivery all call this to work with saved triggers.

*Call graph*: calls 1 internal fn (_require_ext); called by 6 (_delete_owned, _apply_owned, _delete_owned, _find, _member_rows, on_page_change); 1 external calls (__init__).


##### `effective_days`  (lines 332–341)

```
def effective_days(request: int | Literal['all'] | None, declared: int | None) -> int | None
```

**Purpose**: Figures out how far back a stream should sync when a backfill window is involved. It combines what the user requested with what the stream itself declares as its default window.

**Data flow**: It receives a user request and a stream-declared number of days → returns the user’s number when one was given, the stream default when no request was given, or `None` when the result means all history or no cutoff.

**Call relations**: Source registration and window-widening both call this so they calculate backfill cutoffs by the same rule.

*Call graph*: called by 2 (_apply_owned, _widen_window).


##### `_binding_identity`  (lines 344–354)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool, int | Literal['all'] | None]
```

**Purpose**: Extracts the parts of a source spec that define whether it is the same binding state. This is used to tell a no-op reapply from a real change.

**Data flow**: It receives a source spec → normalizes the stream list by sorting it → returns a tuple of provider, streams, account, URL, sharing, and backfill request.

**Call relations**: `SourceObjects.apply` uses this to skip unnecessary work for identical applies, and `_resync` uses it to ensure a resync request is not secretly changing the source.

*Call graph*: called by 2 (_resync, apply).


##### `SourceObjects.apply`  (lines 383–404)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Controls the top-level apply behavior for source objects. It separates three cases: resync now, identical reapply, or a real create/update.

**Data flow**: It receives the tool context, object name, requested spec, old visible spec, and expected generation → if `resync` is set it schedules sync only → if the spec is identical it grants access to the agent → otherwise it hands off to the base object apply flow.

**Call relations**: This is called by the object system when someone applies a `source`. It delegates the actual special cases to `_resync` and `_grant_settled`, and lets the parent class enforce normal ownership rules for real mutations.

*Call graph*: calls 3 internal fn (_grant_settled, _resync, _binding_identity).


##### `SourceObjects._grant_settled`  (lines 406–427)

```
async def _grant_settled(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Gives the current agent access to an already-existing source when the agent reapplies the exact same spec. This matters because no new source row is created in that case, so the usual registration-time grant would not happen.

**Data flow**: It reads the speaking member, source owner, and binding streams → checks that the speaker is allowed to receive the feed → grants each stream source to the current agent → returns nothing.

**Call relations**: `SourceObjects.apply` calls this for identical reapplications. It uses `_binding_named` to find the streams and the extension context to record the grants.

*Call graph*: calls 2 internal fn (_binding_named, _require_ext); called by 1 (apply).


##### `SourceObjects._resync`  (lines 429–453)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync run for all streams in an existing binding. It is an action, not a change to the saved source definition.

**Data flow**: It checks that the requested spec matches the current spec except for `resync` → verifies the caller can see and spend the source authority as owner or admin → finds the binding → asks the extension context to schedule syncs for its stream IDs.

**Call relations**: `SourceObjects.apply` calls this when `resync` is true. It uses `_binding_identity` to reject mixed edit-and-resync requests and `_binding_named` to find the stream rows to wake.

*Call graph*: calls 4 internal fn (speaker_is_admin, _binding_identity, _binding_named, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._member_rows`  (lines 455–468)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the rows shown when sources are listed. Each row includes the source name, a short summary, and who owns or shares it.

**Data flow**: It rebuilds all bindings from the extension context → converts each binding into an owned listing row → returns the collection of rows.

**Call relations**: The object framework calls this during source listing. The base class then applies the member/admin visibility rules around these rows.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); 3 external calls (__init__, __init__, subject_shared).


##### `SourceObjects._member_object`  (lines 470–486)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: ObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the detailed object view for one source. It returns the saved spec, timestamps, and authentication links.

**Data flow**: It receives a name and owner information → looks up the binding by name → if found, converts it to object detail with spec, created time, updated time, and links → otherwise returns `None`.

**Call relations**: The object framework calls this during source get/explain flows after ownership has been considered. It relies on `_binding_named` for the actual lookup.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 488–514)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live operational status for a source, such as next sync time and recent error information. This is the health readout for the binding’s streams.

**Data flow**: It looks up the binding → reads each stream’s next sync time, error count, parked state, and backfill cutoff → returns a dictionary suitable for status output, including owner ID for private sources when available.

**Call relations**: The object system calls this when status is requested for a source. It uses `_binding_named` and the binding’s sharing subject to shape what status to report.

*Call graph*: calls 1 internal fn (_binding_named); 1 external calls (subject_shared).


##### `SourceObjects._apply_owned`  (lines 516–620)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Performs the real create or update work for a source once ownership rules allow it. It validates the request, registers new stream rows, grants them to the agent, widens backfill windows when allowed, shares private sources when allowed, and removes dropped streams.

**Data flow**: It receives the requested source spec and current owner state → checks for a speaking member, known provider, valid streams, valid backfill use, safe base URL, and usable account credentials → compares with any existing binding → applies safe changes → registers missing streams, grants existing streams to the agent, and removes streams left out of the new spec.

**Call relations**: The base object apply flow calls this for real source mutations. It coordinates `_validated_base_url`, `_resolved_account`, `_binding_named`, `effective_days`, and `_widen_window`, then writes changes through the extension context.

*Call graph*: calls 6 internal fn (_resolved_account, _widen_window, _binding_named, _require_ext, _validated_base_url, effective_days); 7 external calls (__init__, __init__, now, timedelta, binding_name, member_subject, get).


##### `SourceObjects._widen_window`  (lines 622–696)

```
async def _widen_window(self, ctx: ToolContext, binding: _Binding, *, kept: tuple[_Stream, ...], declared: dict[str, int | None], windowed: frozenset[str], account: str, base_url: str | None, request:
```

**Purpose**: Changes a binding’s backfill cutoff only when the new request reaches farther back in time. This avoids leaving old synced pages stranded outside a newly narrowed window.

**Data flow**: It receives the current binding, kept streams, stream backfill defaults, and the new request → calculates each stream’s old anchor date and new cutoff → rejects narrowing → updates stored connector configs and marks widened streams for refetch.

**Call relations**: `SourceObjects._apply_owned` calls this when a source reapply changes `backfill_days`. It uses `effective_days` so widening follows the same rules as first registration.

*Call graph*: calls 2 internal fn (_require_ext, effective_days); called by 1 (_apply_owned); 3 external calls (__init__, __init__, timedelta).


##### `SourceObjects._delete_owned`  (lines 698–705)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes a source binding that the caller is allowed to remove. Deleting removes every stream row and clears triggers that watched the binding.

**Data flow**: It looks up the binding by name → raises an unknown-object error if missing → removes each stream source through the extension context → removes trigger records for that binding.

**Call relations**: The object framework calls this after delete permissions pass. It uses `_binding_named` for lookup and `_require_triggers` to clean up alarms tied to the deleted source.

*Call graph*: calls 3 internal fn (_binding_named, _require_ext, _require_triggers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 707–780)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Decides which account or credential a source will use to authenticate with its provider. It chooses between connected accounts and direct workspace credentials without making the user supply a separate mode flag.

**Data flow**: It reads the requested provider and account ID, connector registry, active account grants, connection ownership, and credential slots → validates that exactly the right kind of authentication is available → returns the account handle and optional connection ID to store with the source.

**Call relations**: `SourceObjects._apply_owned` calls this before registration. The returned account becomes part of the connector config and therefore part of the binding’s identity.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `trigger_name`  (lines 783–787)

```
def trigger_name(binding: str, conversation_id: UUID) -> str
```

**Purpose**: Builds the official object name for a source trigger. A trigger is uniquely defined by the source it watches and the conversation that owns it.

**Data flow**: It receives a binding name and conversation ID → combines them into one stable string → returns the trigger object name.

**Call relations**: Trigger listing, lookup, and creation all use this same naming rule so applying a trigger under the wrong name can be rejected with the correct name.

*Call graph*: called by 3 (_apply_owned, _find, _member_rows).


##### `SourceTriggerObjects._member_rows`  (lines 818–852)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds rows shown when source triggers are listed. Each row explains which source is watched, which conversation owns the trigger, how delivery works, and who created it.

**Data flow**: It reads reported trigger records → rebuilds source bindings for summaries → looks up creator email addresses → returns owned listing rows with fields such as source, delivery, conversation, origin, owner email, and whether it belongs to the current member.

**Call relations**: The object framework calls this during trigger listing. It combines trigger-store data with source summaries from `_bindings_from_ext` and names each row with `trigger_name`.

*Call graph*: calls 4 internal fn (_bindings_from_ext, _require_ext, _require_triggers, trigger_name); 4 external calls (__init__, __init__, owner_emails, subject_shared).


##### `SourceTriggerObjects._member_object`  (lines 854–889)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SourceTriggerSpec] | None
```

**Purpose**: Builds the detailed object view for one source trigger. It shows what source is watched, how delivery happens, and links to related objects.

**Data flow**: It receives a trigger name and expected owner generation → finds the matching trigger → verifies it has not changed → returns a detail object with the trigger spec and links to the watched source and, for current-conversation delivery, the reporting conversation.

**Call relations**: The object framework calls this for trigger get operations. It depends on `_find` to locate the stored trigger safely.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SourceTriggerObjects._status`  (lines 891–905)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns status-style metadata for a source trigger. This includes the source, delivery mode, owning conversation, origin, creator email, and whether it is the current member’s trigger.

**Data flow**: It finds the trigger by name and generation → looks up the creator email → returns a dictionary of readable trigger facts, or `None` if the trigger no longer matches.

**Call relations**: The object framework calls this when showing trigger status. It uses `_find` for lookup and owner email lookup for human-friendly display.

*Call graph*: calls 1 internal fn (_find); 1 external calls (owner_emails).


##### `SourceTriggerObjects._apply_owned`  (lines 907–944)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceTriggerSpec, old: SourceTriggerSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Creates a new trigger for the current conversation, or treats an exact reapply as a no-op. It refuses attempts to rename or edit an existing trigger because the trigger’s identity is the source-conversation pair.

**Data flow**: It receives the requested trigger spec → checks that the object name matches the source and current conversation → if an identical trigger already exists, returns → otherwise checks the source is watchable, writes the trigger record, and rechecks that the source still exists.

**Call relations**: The base object apply flow calls this for trigger creation. It calls `_watchable` before writing, `_require_triggers` to create the row, and `_binding_named` afterward to handle a race where the source was deleted during creation.

*Call graph*: calls 4 internal fn (_watchable, _binding_named, _require_triggers, trigger_name); 1 external calls (__init__).


##### `SourceTriggerObjects._watchable`  (lines 946–959)

```
async def _watchable(self, ctx: ToolContext, source: str) -> None
```

**Purpose**: Checks whether the requested source can be watched by a trigger. Only visible shared sources are allowed, because private source pages would not be delivered to shared conversations.

**Data flow**: It asks the source object store for the named source as the current caller → if it cannot be seen, raises unknown-object → if it is private, raises a clear validation error → otherwise returns successfully.

**Call relations**: `SourceTriggerObjects._apply_owned` calls this before creating a trigger, so invalid or private sources never get a trigger row.

*Call graph*: called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceTriggerObjects._delete_owned`  (lines 961–965)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Deletes a trigger after confirming it is still the same stored trigger the caller meant to delete. This avoids deleting a different row if something changed mid-operation.

**Data flow**: It receives a trigger name and owner generation → finds the current trigger → verifies its generation matches → removes it from the trigger store.

**Call relations**: The object framework calls this after delete permissions pass. It uses `_find` for a safe lookup and `_require_triggers` to remove the saved trigger.

*Call graph*: calls 2 internal fn (_find, _require_triggers).


##### `SourceTriggerObjects._find`  (lines 967–975)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> ListedTrigger | None
```

**Purpose**: Finds one listed trigger by its derived object name. It is the private lookup helper for trigger detail, status, and deletion.

**Data flow**: It reads all reported triggers from the trigger store → derives each trigger’s object name → returns the matching row or `None`.

**Call relations**: Trigger get, status, and delete operations call this so they all use the same name-matching rule from `trigger_name`.

*Call graph*: calls 2 internal fn (_require_triggers, trigger_name); called by 3 (_delete_owned, _member_object, _status).


##### `on_page_change`  (lines 978–1019)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Responds when synced pages change and wakes any triggers that care about those changes. It is the bridge from background sync events to agent conversations.

**Data flow**: It receives a hook payload → confirms it is a page-change batch → maps changed source IDs back to bindings → groups changes by binding → finds triggers for each binding → keeps only shared changes readable by the trigger’s agent → fires each eligible trigger.

**Call relations**: The platform calls this hook after page changes. It uses `_bindings_from_ext` to understand which binding each source row belongs to, `_require_triggers` to find interested triggers, and `_fire_trigger` to deliver notifications.

*Call graph*: calls 3 internal fn (_bindings_from_ext, _fire_trigger, _require_triggers); 2 external calls (__init__, suppress).


##### `_fire_trigger`  (lines 1022–1057)

```
async def _fire_trigger(ext: ExtensionContext, binding: _Binding, trigger: SourceTrigger, authorized: list[PageChange]) -> None
```

**Purpose**: Delivers one trigger notification for authorized page changes. Depending on the trigger’s delivery mode, it either wakes the current conversation once or opens/reuses one conversation per changed page.

**Data flow**: It receives the extension context, binding, trigger, and readable changes → writes a change log when possible → builds an alert message → invokes the target conversation with an idempotency key so retries do not duplicate work.

**Call relations**: `on_page_change` calls this after filtering changes for sharing and readability. It hands log writing to `_write_change_log` and alert text creation to `_alert_message`.

*Call graph*: calls 4 internal fn (invoke, open_conversation, _alert_message, _write_change_log); called by 1 (on_page_change).


##### `_write_change_log`  (lines 1060–1095)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the list of changed pages to a small JSON-lines file for the woken conversation. This keeps bulky change details out of the chat message while still making them available to the agent.

**Data flow**: It receives a conversation, binding, timestamp-like label, and changes → if runtime files are unavailable, returns `None` → otherwise writes one JSON object per changed page and prunes older runtime files in that directory → returns the file path.

**Call relations**: `_fire_trigger` calls this before invoking the agent. The alert message can then point the agent to the file when there are many changed pages.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_fire_trigger); 1 external calls (dumps).


##### `_disposition`  (lines 1098–1104)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Classifies a page change as added, updated, or removed. This gives alerts and logs simple words instead of raw timestamp and tombstone details.

**Data flow**: It receives one page change → returns `removed` if it is a tombstone, `added` if the created and changed timestamps match, otherwise `updated`.

**Call relations**: `_write_change_log` uses this for each JSON-line entry, and `_stream_counts` uses it to summarize changes by stream.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 1107–1121)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Summarizes how many pages were added, updated, or removed in each stream. It creates the compact count sentence used in trigger alerts.

**Data flow**: It receives a list of page changes → counts dispositions per stream → formats those counts into readable text → returns the summary string.

**Call relations**: `_alert_message` calls this to explain the size and shape of a change batch before pointing to page references or a change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 1124–1143)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to an agent when a watched source changes. The message tells the agent what changed and how to inspect the affected pages.

**Data flow**: It receives the binding, changes, and optional log path → chooses a detail style based on how many pages changed and whether a log file exists → combines the source summary, stream counts, and next-step instructions → returns the alert text.

**Call relations**: `_fire_trigger` calls this right before invoking a conversation. It uses `_stream_counts`, `_page_reference`, and the binding summary to make the notification useful.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (_fire_trigger).


##### `_page_reference`  (lines 1146–1150)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as an object reference the agent can read. It includes a short title label so the reference is easier to recognize.

**Data flow**: It receives a page change → chooses the page title or a fallback phrase → trims the label to a safe length → returns a `page/<id> (label)` style string.

**Call relations**: `_alert_message` calls this when there are only a few changed pages and they can be named directly in the alert.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 1153–1193)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes the tenant API URL for providers that need one. This prevents unsafe or wrongly shaped URLs from being stored in source bindings.

**Data flow**: It receives a provider and optional base URL → checks whether the connector has a fixed API host or needs a tenant-specific URL → validates scheme, host, path, port, credentials, query, and fragment against provider rules → returns a normalized URL or `None`.

**Call relations**: `SourceObjects._apply_owned` calls this before account resolution and registration. It protects the sync system from accepting arbitrary URLs while giving users provider-specific examples when validation fails.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### `extensions/sources/ufo_ext_sources/connected.py`

`domain_logic` · `connection hook and background retry`

When someone connects an outside service, the system records permission to use that account. But useful feeds are stored separately, one row per stream of content. This file closes that gap: after a connection is recorded, it creates source rows for the connector’s canonical streams, meaning the core streams the connector exists to import.

It works like a hotel check-in desk that also prepares the guest’s key cards automatically. The connection is the booking; the source rows are the key cards for each main area the guest should access.

There are two paths. The first runs immediately when the connection is recorded, before the user-facing connection flow finishes. The second is a retry job that periodically checks all main-agent connections and creates only the rows that are still missing. The retry job is not meant to duplicate work; it is a safety net for crashes, failed hooks, or older connections.

The file is careful not to undo user choices. If a matching source row already exists, it leaves it alone. If the member previously removed that source, it does not recreate it, because doing so would erase the fact that the member removed it. It also skips providers that need a tenant-specific URL, because only the member can supply that URL. Newly created rows are private to the connection owner and granted to the main agent, not automatically shared with a workspace.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This is the hook that runs right after a connection has been recorded. Its job is to start source creation for that one newly connected account, so the member immediately gets the provider’s main feeds.

**Data flow**: It receives a hook context that should contain a connection-recorded payload. It reads the connection id from that payload, builds a ConnectedSources helper using the extension context, and asks it to register sources for that specific connection. If the payload is not the expected kind, it raises an error instead of silently doing the wrong work. It returns no special outcome.

**Call relations**: The connection system calls this when it publishes a connection-recorded event. This function creates a ConnectedSources object and hands the work to that object, so the hook itself stays small while the registration rules live in one reusable place.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the repair job for source rows that should have been created during connection setup but were missed. It checks connected accounts and fills in only the missing canonical streams.

**Data flow**: It receives an extension context, builds a ConnectedSources helper from it, and runs registration without naming a single connection. That means the helper scans all relevant main-agent connections and creates any missing source rows it is allowed to create. It does not return a value.

**Call relations**: A scheduled or background job calls this later, outside the immediate connection flow. Like the hook, it creates a ConnectedSources object, but it uses the broad retry mode instead of focusing on one just-created connection.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method finds the connected accounts that may need feed rows and decides which ones are eligible for automatic registration. It can work on one connection, for the immediate hook, or on all main-agent connections, for the retry job.

**Data flow**: It first asks the extension context for the currently live source rows, so it can avoid duplicating existing rows. Then it looks through the main agent’s connections. If a specific connection id was supplied, it ignores all others. For each remaining connection, it looks up the matching connector class. If there is no connector, or if the connector has no fixed base URL because it needs a tenant-specific URL, it skips that connection. For eligible connectors, it creates a connector instance and passes the connection, connector, and live source list to _register.

**Call relations**: Both on_connection_recorded and retry_connected_sources use this method through a ConnectedSources instance. It gathers the broad facts, such as existing sources and known main-agent connections, then delegates the per-connection stream decisions to ConnectedSources._register.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates the missing source rows for one connected account’s canonical streams. It protects existing rows, respects removals, keeps rows private to the owner, and calculates how far back each first sync should look.

**Data flow**: It receives one main-agent connection, its connector, and the already live source records. It turns the owner member id into the member subject used for private ownership. It finds existing rows already bound to this connection; if any belong to a different subject, it stops, because it must not add private rows into a differently shared binding. If there is an existing bound row, it reads its source configuration to reuse the requested backfill setting. Then it walks through the connector’s streams and keeps only canonical ones. For each stream, it calculates the effective backfill window, builds a ConnectorSourceConfig, derives the source id that row would have, and records it as fresh only if that id is not already live. Before registering, it asks which of those possible source ids were previously removed. It then registers each fresh, not-removed source for the provider, owner member, and connection.

**Call relations**: ConnectedSources.register calls this after it has chosen an eligible connection and connector. Inside, this method asks the connector for its streams, uses ConnectorSourceConfig to describe each source row, uses effective_days to combine requested and stream-specific backfill limits, and finally hands new rows to the extension context’s source registration path.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling, alert sweep, and source removal`

A “source trigger” is like a standing subscription: one conversation says, “when this source binding changes, tell me.” This file keeps those subscriptions in a database table and wraps all access in SourceTriggerStore, so the rest of the extension does not have to write raw database queries.

The important safety rule is workspace isolation. The database connection supplied by ExtensionContext is not automatically limited to one workspace, so every query in this file explicitly checks workspace_id. Without that, one workspace could accidentally see or delete another workspace’s triggers.

Each trigger says which conversation owns it, which agent should run when it fires, which source binding it watches, how updates should be delivered, and who created it. Delivery can be “current,” meaning send updates into the existing conversation, or “per_page,” meaning split changes into stable page-specific agent conversations.

The store also protects against mismatches. When creating or removing a trigger, it checks that the currently executing agent is the same agent tied to the conversation or trigger. Listing for members does one extra step: it asks the wider system for conversation facts such as audience and visible channel label, because visibility depends on where the trigger fires, not just on the trigger row itself.

#### Function details

##### `_utc`  (lines 85–86)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a stored date and time has timezone information. If the database gave back a plain timestamp with no timezone attached, it treats it as UTC, the common reference timezone.

**Data flow**: It receives a datetime value. If that value already says what timezone it belongs to, it returns it unchanged; if not, it adds UTC as the timezone. The output is always a datetime that can be compared and displayed more safely.

**Call relations**: _trigger calls this when turning database rows into SourceTrigger objects, so every trigger returned by the store has consistent time values.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 89–104)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This helper turns one database row into a SourceTrigger object that the rest of the code can use. It also checks that the delivery mode stored in the database is one of the two modes this code understands.

**Data flow**: It receives a row from the source_trigger table. It reads the row’s fields, rejects an unknown delivery value, normalizes created_at and updated_at through _utc, and returns a SourceTrigger data object. If the delivery value is unexpected, it raises an error instead of silently producing a bad trigger.

**Call relations**: SourceTriggerStore.create uses it after inserting a new row, SourceTriggerStore.waking uses it when finding triggers to fire, and SourceTriggerStore.list_reported uses it before adding member-facing conversation details.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 1 external calls (__init__).


##### `SourceTriggerStore.workspace_id`  (lines 114–115)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives the workspace identifier from the store’s ExtensionContext. It is a small convenience that keeps every database query tied to the current workspace.

**Data flow**: It reads ctx.workspace_id from the store and returns that UUID. It does not change anything.

**Call relations**: The store’s database methods use this value when creating, deleting, or selecting trigger rows, because the transaction itself is not automatically scoped to a workspace.


##### `SourceTriggerStore.create`  (lines 117–166)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None) -> SourceTrigger
```

**Purpose**: This creates a new trigger saying that one conversation should be notified when a particular source binding changes. It refuses to create the trigger if the conversation belongs to a different agent, or if the same conversation already watches the same binding.

**Data flow**: It receives a conversation id, a source binding name, a delivery mode, and optionally the member who asked for it. It gets the current agent id, checks that the conversation is owned by that agent, inserts a new row with a fresh UUID and timestamps, and returns the new SourceTrigger. If another request already created the same trigger, it returns a clear ValueError instead of exposing a low-level database conflict.

**Call relations**: This is used when a user or agent asks to start watching a source. It calls object_agent_id to learn which agent is currently executing, uuid4 to make a new trigger id, and _trigger to convert the inserted database row into the object returned to the caller.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 168–183)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller expected. That protects against deleting the wrong row if something changed between reading and removing it.

**Data flow**: It receives the SourceTrigger the caller intends to delete. It checks that the trigger’s agent is still the current executing agent, then deletes the row only when workspace id, trigger id, agent id, conversation id, and binding all match. It returns nothing on success; if no row was deleted, it raises an error saying the trigger changed while being removed.

**Call relations**: This is used when stopping one conversation’s subscription. It relies on object_agent_id for the current agent check and uses a SQL delete statement to remove the database row.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 185–194)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This deletes every trigger in the current workspace for one source binding. It is used when the source itself is removed, because no conversation should keep waiting on a source that no longer exists.

**Data flow**: It receives a binding name. It deletes all source_trigger rows in the current workspace with that binding and returns nothing. It does not limit by agent, because a workspace source may have been watched by conversations owned by several agents.

**Call relations**: This belongs to the source cleanup flow. When a binding disappears, this method performs the broad workspace-wide cleanup with a database delete statement.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.waking`  (lines 196–209)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds all triggers that should wake up for a changed source binding. It is the read path used by the alert sweep when a source has new data to deliver.

**Data flow**: It receives a binding name. It selects all matching rows in the current workspace, ordered by creation time and id for stable processing, turns each row into a SourceTrigger with _trigger, and returns them as a tuple. It includes triggers for all agents in the workspace because the source is shared at the workspace level.

**Call relations**: The alerting flow calls this when it needs to know which conversations to notify for a source. It builds the database query with sqlalchemy.select and hands each row to _trigger before returning the list of wake-up rules.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 211–245)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This lists triggers that are suitable to show to a member, including the audience and visible label of the conversation where each trigger fires. It can list all current-agent triggers or only those for one conversation.

**Data flow**: It optionally receives a conversation id. It selects trigger rows for the current workspace and current agent, optionally narrows them to the given conversation, converts rows into SourceTrigger objects, then asks the context for conversation facts. It returns ListedTrigger objects that pair each trigger with its conversation audience and surface label, skipping triggers whose conversation no longer exists.

**Call relations**: Member-facing screens use this when they need to show what sources are being watched and decide what the member is allowed to see. It calls object_agent_id to stay within the current agent’s namespace, sqlalchemy.select to read rows, _trigger to build trigger objects, and ListedTrigger to attach the live conversation details.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).

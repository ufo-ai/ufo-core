# Source Synchronization, Indexing, and Memory Recall  `stage-14`

This stage is the system’s intake and memory pipeline. It runs mostly during the main background work after a user connects outside services. Its job is to read information from tools like Google, Slack, GitHub, Salesforce, and many others, turn that information into standard “pages,” notice changes and deletions, and make the content searchable and usable later in conversations.

The concrete source connectors are the adapters for each outside service. They know how to call that service’s web API, which is a structured way for software to ask another system for data. The REST helper provides common plumbing for these calls, including retries and page-by-page fetching. The backend converts connector results into UFO’s shared sync format. The sync layer stores page bodies, cursors that mark progress, and a replay feed so indexers can catch up safely.

The tools file lets agents manage sources, resync them, and alert subscribed conversations when pages change. The pages file exposes synced pages for reading. Finally, the memory and search backends split text into chunks, embed it for meaning-based search, store it in an index, and recall or clean memories in background jobs.

## Sub-stages

- [Concrete Source Connectors](stage-14.1.md) `stage-14.1` — 49 files
- [Memory and Search Backends](stage-14.2.md) `stage-14.2` — 8 files

## Files in this stage

### Source Management Tools
Agent-facing tools let users administer external sources, trigger resyncs, manage subscriptions, and notify conversations about changed synced pages.

### `extensions/sources/ufo_ext_sources/tools.py`

`domain_logic` · `object-tool request handling and page-change hook execution`

A “source” here is a saved connection between the workspace and an outside provider, such as one account plus a set of streams to sync. Think of it like subscribing the workspace to selected feeds from a service. This file turns those saved feeds into normal object-tool items, so an agent can list them, get their details, apply a new one, remove one, or ask for an immediate resync.

The file is careful about identity and permissions. A source’s name is not chosen by the user; it is derived from the provider, account, and tenant URL. That prevents two names from pointing at the same real connection. Changing the provider, account, URL, or streams is treated as replacing the source, not editing it in place. Private sources belong to the member who registered them. Shared sources are visible to the workspace, but only the registrar can make a private source shared. Deletion and resync require the registrar or an admin.

The unusual part is subscriptions. Any conversation that can see a source may add or remove only its own conversation id from the source’s subscriber list. When synced pages change, the hook groups changes by source, checks who subscribed, filters out anything private or unreadable, writes a change log file when useful, and invokes each subscribed conversation with a summary.

#### Function details

##### `_Binding.name`  (lines 167–168)

```
def name(self) -> str
```

**Purpose**: Builds the official object name for a source binding. This matters because source names are derived from the real connection details, not chosen freely.

**Data flow**: It reads the binding’s provider, account, and base URL → passes them through the shared naming helper → returns the stable source name.

**Call relations**: Other methods use this property when presenting bindings or matching a requested name. It delegates the exact naming rule to the shared source helper so this file stays consistent with the rest of the system.

*Call graph*: 1 external calls (binding_name).


##### `_Binding.created_at`  (lines 171–172)

```
def created_at(self) -> datetime
```

**Purpose**: Finds when the binding first came into existence. A binding can contain several stream rows, so it uses the oldest stream creation time.

**Data flow**: It reads all stream creation timestamps inside the binding → chooses the earliest one → returns that timestamp as the binding’s created time.

**Call relations**: The detail view uses this value to show a single creation time for the whole source, even though the database stores one row per stream.


##### `_Binding.updated_at`  (lines 175–176)

```
def updated_at(self) -> datetime
```

**Purpose**: Finds when the binding was most recently changed. Since a binding has one row per stream, the newest stream update time represents the whole binding.

**Data flow**: It reads all stream update timestamps → chooses the latest one → returns that timestamp.

**Call relations**: The detail view uses this to report freshness for the grouped source object.


##### `_Binding.spec`  (lines 178–186)

```
def spec(self, subscribers: tuple[str, ...]=()) -> SourceSpec
```

**Purpose**: Turns an internal binding into the public source specification shown to agents. This is how stored source rows become something an object tool can display or re-apply.

**Data flow**: It reads the binding’s provider, stream names, account, base URL, sharing state, and optional subscribers → converts direct-account internals back to an empty account id for display → returns a SourceSpec object.

**Call relations**: Detail and apply logic use this conversion when comparing what already exists with what the caller wants. It creates the public model object used by the source object kind.

*Call graph*: 1 external calls (__init__).


##### `_Binding.summary`  (lines 188–190)

```
def summary(self) -> str
```

**Purpose**: Creates a short human-readable label for a source binding. It helps listings and alerts describe the source without dumping the full configuration.

**Data flow**: It joins the stream names and combines them with provider and account → trims the result to a fixed length → returns that short summary string.

**Call relations**: Alerts call this when telling a subscribed conversation which source changed. Object listings also use the same kind of summary through owned rows.

*Call graph*: called by 1 (_alert_message).


##### `_require_ext`  (lines 199–202)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Checks that the current tool call has the extension context it needs. Without that context, this file cannot read or change registered sources.

**Data flow**: It receives the tool context → reads its extension-context field → returns it if present, or raises an error if missing.

**Call relations**: Most SourceObjects methods call this before touching source rows, subscriber storage, credentials, files, or sync scheduling. It acts like a guard at the door.

*Call graph*: called by 8 (_apply_owned, _bindings, _delete_owned, _detail, _edit_subscribers, _resolved_account, _resync, _status).


##### `_require_connectors`  (lines 205–208)

```
def _require_connectors(ctx: ToolContext) -> ConnectorRegistry
```

**Purpose**: Checks that the current turn has access to the connector registry, which knows about connected accounts and connector backends.

**Data flow**: It receives the tool context → reads its connector-registry field → returns it if present, or raises an error if missing.

**Call relations**: Account resolution calls this while deciding whether a provider should use a connected account or a direct workspace credential.

*Call graph*: called by 1 (_resolved_account).


##### `_bindings_from_ext`  (lines 211–240)

```
async def _bindings_from_ext(ext: ExtensionContext) -> tuple[_Binding, ...]
```

**Purpose**: Reconstructs source objects from the lower-level stored source rows. The storage has one row per stream, but users see one binding per provider/account/URL.

**Data flow**: It asks the extension context for all source rows → ignores rows for providers this extension does not know → validates each row’s connector config → groups rows with the same provider, account, and base URL → returns one binding object per group, with its streams sorted by name.

**Call relations**: Listing, finding, and the page-change hook rely on this function to translate raw source rows into the object-level view. It creates the _Stream and _Binding objects that the rest of the file works with.

*Call graph*: calls 1 internal fn (sources); called by 2 (_bindings, on_page_change); 3 external calls (__init__, __init__, model_validate).


##### `_subscribers_map`  (lines 243–255)

```
async def _subscribers_map(ext: ExtensionContext, name: str) -> dict[str, str]
```

**Purpose**: Reads the saved subscribers for one source. The map remembers both the conversation id and the agent id, so alerts can return to the same conversation and agent.

**Data flow**: It reads a stored value under the source’s subscriber key → accepts it only if it is a string-to-string map → returns a normal dictionary, an empty dictionary if nothing is stored, or raises an error if the saved shape is bad.

**Call relations**: Detail, status, subscription editing, and page-change alerts all call this to know who is subscribed to a source.

*Call graph*: called by 4 (_detail, _edit_subscribers, _status, on_page_change).


##### `_store_subscribers`  (lines 258–264)

```
async def _store_subscribers(ext: ExtensionContext, name: str, mapping: dict[str, str]) -> None
```

**Purpose**: Saves or clears the subscriber list for a source. Empty subscriber lists are removed from storage instead of being kept as empty records.

**Data flow**: It receives a source name and a conversation-to-agent map → writes the map if it has entries → deletes the stored key if the map is empty → returns nothing.

**Call relations**: Subscription edits call this after changing the map, and deletion calls it to remove subscriber state when the source is removed.

*Call graph*: called by 2 (_delete_owned, _edit_subscribers).


##### `_binding_identity`  (lines 267–268)

```
def _binding_identity(spec: SourceSpec) -> tuple[str, tuple[str, ...], str, str, bool]
```

**Purpose**: Extracts the parts of a source spec that define what source it really is. This lets the code tell a harmless subscriber edit apart from a real source replacement.

**Data flow**: It reads provider, sorted streams, account id, base URL, and shared flag from the spec → packages them into a tuple → returns that tuple for comparison.

**Call relations**: Apply and resync use this comparison before deciding whether to allow a special action, such as resyncing or changing only subscribers.

*Call graph*: called by 2 (_resync, apply).


##### `_self_only_change`  (lines 271–278)

```
def _self_only_change(old: tuple[str, ...], new: tuple[str, ...], caller: str) -> None
```

**Purpose**: Enforces the rule that a conversation may only subscribe or unsubscribe itself. This prevents one conversation from silently changing another conversation’s alerts.

**Data flow**: It compares the old subscriber ids with the new subscriber ids → finds which ids changed → allows the change only if the only changed id is the caller’s own id, otherwise raises a clear error.

**Call relations**: SourceObjects.apply calls this during subscriber-only applies, before saving the updated subscriber map.

*Call graph*: called by 1 (apply).


##### `SourceObjects.apply`  (lines 303–331)

```
async def apply(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Decides what kind of source apply request is being made: resync, subscriber edit, or normal create/update. It routes each case through the right permission rules.

**Data flow**: It receives the requested name, desired spec, old visible spec if any, and generation information → if resync is requested, it hands off to the resync path → if the source identity is unchanged, it treats the request as a subscriber edit and checks self-only changes → otherwise it falls back to the base member-owned object apply flow.

**Call relations**: This is the main entry point for applying source objects. It calls the resync helper for action-only resyncs, the subscriber helper for alert subscriptions, and the inherited object machinery for ordinary source creation or ownership-gated updates.

*Call graph*: calls 4 internal fn (_edit_subscribers, _resync, _binding_identity, _self_only_change); 1 external calls (__init__).


##### `SourceObjects._resync`  (lines 333–357)

```
async def _resync(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None) -> None
```

**Purpose**: Schedules an immediate sync for an existing source without changing its saved configuration. It protects credentials by allowing only the registrar or an admin to trigger connector traffic.

**Data flow**: It checks that the requested spec exactly matches the existing source identity → checks that the caller can see the source and is either its owner or an admin → finds the binding → sends all stream row ids to the extension context’s sync scheduler → returns nothing.

**Call relations**: SourceObjects.apply calls this when a spec sets resync. It uses identity comparison, owner lookup, visibility checks, and the extension context before handing the stream ids to the sync scheduler.

*Call graph*: calls 4 internal fn (speaker_is_admin, _find, _binding_identity, _require_ext); called by 1 (apply); 3 external calls (__init__, __init__, __init__).


##### `SourceObjects._edit_subscribers`  (lines 359–381)

```
async def _edit_subscribers(self, ctx: ToolContext, name: str, desired: tuple[str, ...], caller: str, agent: UUID) -> None
```

**Purpose**: Adds or removes the caller’s conversation from a source’s subscriber list. It also records the current agent id so future alerts reopen the right agent in that conversation.

**Data flow**: It reads the current subscriber map → if the caller’s id is in the desired list, stores the caller with the agent id; otherwise removes the caller → writes the map back → rechecks that the source still exists, clearing the map and raising an unknown-object error if it disappeared.

**Call relations**: SourceObjects.apply calls this after it has already verified that only the caller’s own subscription changed. The page-change hook later uses the stored map to send alerts.

*Call graph*: calls 4 internal fn (_find, _require_ext, _store_subscribers, _subscribers_map); called by 1 (apply); 1 external calls (__init__).


##### `SourceObjects._owned_rows`  (lines 383–394)

```
async def _owned_rows(self, ctx: ToolContext) -> tuple[OwnedRow[ObjectOwner], ...]
```

**Purpose**: Builds the lightweight rows used when listing source objects. Each row says the source name, a short summary, and who owns or shares it.

**Data flow**: It reads all bindings → for each binding, builds an owner record from its owner member id and shared/private subject → returns a tuple of listing rows.

**Call relations**: The member-owned object framework calls this when it needs to list visible source objects. It depends on the binding reconstruction helper to hide the one-row-per-stream storage detail.

*Call graph*: calls 1 internal fn (_bindings); 2 external calls (__init__, __init__).


##### `SourceObjects._detail`  (lines 396–407)

```
async def _detail(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> ObjectDetail[SourceSpec] | None
```

**Purpose**: Builds the full object detail for one source. This includes the public spec, timestamps, and the current subscriber ids.

**Data flow**: It looks up the binding by name → if not found, returns nothing → reads the subscriber map → converts the binding into a SourceSpec with sorted subscriber ids → returns an ObjectDetail with created and updated timestamps.

**Call relations**: The object framework calls this for object_get-style detail. It combines binding lookup with subscriber storage so the caller sees both the source configuration and alert subscriptions.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map); 1 external calls (__init__).


##### `SourceObjects._status`  (lines 409–432)

```
async def _status(self, ctx: ToolContext, name: str, _owner: ObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Builds live status information for one source. This tells the caller whether the source is shared, whether this conversation is subscribed, and when each stream will sync next.

**Data flow**: It finds the binding → reads subscribers → builds a dictionary containing sharing state, the caller’s subscriber id, subscribed/not-subscribed state, and per-stream sync/error data → includes owner member id for private owned sources → returns the status dictionary, or nothing if the source is missing.

**Call relations**: The object framework calls this alongside object details. Its subscriber_id value is what callers are expected to add or remove in the subscribers field.

*Call graph*: calls 3 internal fn (_find, _require_ext, _subscribers_map).


##### `SourceObjects._apply_owned`  (lines 434–510)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SourceSpec, old: SourceSpec | None, owner: ObjectOwner | None) -> None
```

**Purpose**: Creates a new source binding or applies the few allowed ownership-gated changes to an existing one. It validates the provider, stream names, tenant URL, account choice, source name, and sharing rules before writing anything.

**Data flow**: It receives the desired source spec → confirms there is a speaking member → rejects subscribers on first creation → checks that the provider and streams exist → validates the base URL → resolves which account or credential to use → derives the required source name and refuses mismatches → if the binding already exists, allows only a private-to-shared flip where permitted; otherwise registers one source row per stream with the extension context.

**Call relations**: The base member-owned apply flow calls this after permission checks. It relies on account resolution and URL validation, then calls the extension context to register source streams or mark existing streams shared.

*Call graph*: calls 4 internal fn (_find, _resolved_account, _require_ext, _validated_base_url); 6 external calls (__init__, __init__, __init__, binding_name, member_subject, get).


##### `SourceObjects._delete_owned`  (lines 512–519)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: ObjectOwner) -> None
```

**Purpose**: Deletes all stream rows that make up a source binding and clears its subscribers. This removes the registered sync source as one object, even though it is stored as several stream rows.

**Data flow**: It finds the binding by name → if missing, raises an unknown-object error → asks the extension context to remove each stream source id → clears the subscriber map → returns nothing.

**Call relations**: The member-owned object framework calls this after registrar-or-admin delete permissions pass. It hands actual removal to the extension context and cleans up alert state.

*Call graph*: calls 3 internal fn (_find, _require_ext, _store_subscribers); 1 external calls (__init__).


##### `SourceObjects._resolved_account`  (lines 521–594)

```
async def _resolved_account(self, ctx: ToolContext, spec: SourceSpec) -> _ResolvedAccount
```

**Purpose**: Chooses how the source will authenticate to its provider. It decides between a connected account grant and a direct workspace credential, and gives clear errors when the user must connect or configure something first.

**Data flow**: It reads the connector registry, available connected accounts, the requested account id, connection ownership, declared credentials, and fallback backend support → selects a connected account when required or available → otherwise selects the direct account only when a suitable workspace credential exists → returns the chosen account handle and optional connection id, or raises a user-facing error explaining what is missing.

**Call relations**: SourceObjects._apply_owned calls this before registering source rows. The returned account id is saved into the connector source config, so future sync runs replay the same authentication choice.

*Call graph*: calls 4 internal fn (connector_accounts, connector_connection, _require_connectors, _require_ext); called by 1 (_apply_owned); 1 external calls (__init__).


##### `SourceObjects._find`  (lines 596–599)

```
async def _find(self, ctx: ToolContext, name: str) -> _Binding | None
```

**Purpose**: Looks up one binding by its derived source name. It hides the lower-level work of rebuilding bindings first.

**Data flow**: It reads all reconstructed bindings → compares each binding’s name with the requested name → returns the first matching binding or nothing.

**Call relations**: Create/update, delete, detail, status, subscriber editing, and resync paths all call this whenever they need the current binding behind a source object name.

*Call graph*: calls 1 internal fn (_bindings); called by 6 (_apply_owned, _delete_owned, _detail, _edit_subscribers, _resync, _status).


##### `SourceObjects._bindings`  (lines 601–602)

```
async def _bindings(self, ctx: ToolContext) -> tuple[_Binding, ...]
```

**Purpose**: Returns all source bindings visible to this source-object store before higher-level filtering is applied. It is a thin bridge from tool context to extension storage.

**Data flow**: It checks that the tool context has an extension context → asks the binding reconstruction helper to read and group source rows → returns the tuple of bindings.

**Call relations**: Listing uses this directly, and name lookup uses it indirectly through SourceObjects._find.

*Call graph*: calls 2 internal fn (_bindings_from_ext, _require_ext); called by 2 (_find, _owned_rows).


##### `on_page_change`  (lines 605–666)

```
async def on_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Sends change alerts to conversations subscribed to sources whose synced pages changed. It carefully filters the alert so subscribers only hear about shared pages their agent can read.

**Data flow**: It receives a hook payload → verifies it is a page-change batch → reconstructs current bindings → groups changed pages by binding → reads subscribers for each changed binding → keeps only shared changes → checks each subscriber agent’s readable source ids → writes a change log for authorized changes → invokes the subscribed conversation with an alert message and an idempotency key so replays do not double-alert.

**Call relations**: The manifest hook system calls this when synced pages change. It uses binding reconstruction, subscriber storage, change-log writing, and alert-message building before handing the final message to the extension invocation system.

*Call graph*: calls 4 internal fn (_alert_message, _bindings_from_ext, _subscribers_map, _write_change_log); 2 external calls (__init__, UUID).


##### `_write_change_log`  (lines 669–702)

```
async def _write_change_log(ext: ExtensionContext, conversation_id: UUID, binding: _Binding, latest: str, changes: list[PageChange]) -> str | None
```

**Purpose**: Writes the detailed list of changed pages into the subscribed conversation’s workspace files. This keeps large change details out of the alert message while still making them available to the agent.

**Data flow**: It receives the extension context, conversation id, binding, latest-change timestamp, and page changes → if file storage is unavailable, returns nothing → creates one JSON line per changed page with page reference, stream, title, change type, and timestamp → writes the file under a per-source change-log directory → prunes old files in that directory → returns the written path.

**Call relations**: on_page_change calls this before sending an alert when there are authorized changes. It uses _disposition to label each changed page as added, updated, or removed.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (on_page_change); 1 external calls (dumps).


##### `_disposition`  (lines 705–711)

```
def _disposition(change: PageChange) -> str
```

**Purpose**: Labels a page change as added, updated, or removed. This makes sync events understandable in alerts and logs.

**Data flow**: It reads the page-change flags and timestamps → returns removed for tombstones, added when creation time equals the change time, and updated otherwise.

**Call relations**: _write_change_log uses this for each JSON-line entry, and _stream_counts uses it when summarizing counts for the alert.

*Call graph*: called by 2 (_stream_counts, _write_change_log).


##### `_stream_counts`  (lines 714–728)

```
def _stream_counts(changes: list[PageChange]) -> str
```

**Purpose**: Creates a compact per-stream summary of what changed. For example, it can say that one stream had added pages while another had removed pages.

**Data flow**: It receives a list of page changes → counts added, updated, and removed changes separately for each stream → formats those counts into a readable string → returns that string.

**Call relations**: _alert_message calls this so the alert starts with a quick overview before pointing to exact pages or a change log.

*Call graph*: calls 1 internal fn (_disposition); called by 1 (_alert_message); 1 external calls (defaultdict).


##### `_alert_message`  (lines 731–750)

```
def _alert_message(binding: _Binding, changes: list[PageChange], log_path: str | None) -> str
```

**Purpose**: Builds the message sent to a subscribed conversation when a source changes. It balances brevity with enough direction for the agent to investigate.

**Data flow**: It receives the binding, authorized page changes, and optional change-log path → if there are only a few changes, names the pages directly → if there are many and a log exists, points to the log file → otherwise tells the agent how to list recent pages → combines that detail with the source summary and stream counts → returns the final message text.

**Call relations**: on_page_change calls this just before invoking a subscribed conversation. It uses the binding summary, page references, and stream-count summary to make the alert useful.

*Call graph*: calls 3 internal fn (summary, _page_reference, _stream_counts); called by 1 (on_page_change).


##### `_page_reference`  (lines 753–757)

```
def _page_reference(change: PageChange) -> str
```

**Purpose**: Formats one changed page as a readable object reference. This gives the alerted agent a direct object name it can fetch.

**Data flow**: It reads the page id and title from a page change → trims the title to a safe length, or uses a fallback for untitled pages → returns a string like a page object reference plus label.

**Call relations**: _alert_message uses this when the alert names each changed page directly.

*Call graph*: called by 1 (_alert_message).


##### `_validated_base_url`  (lines 760–794)

```
def _validated_base_url(provider: str, base_url: str | None) -> str | None
```

**Purpose**: Checks and normalizes tenant API URLs for providers that need a customer-specific URL. This prevents unsafe or wrongly shaped URLs from being saved as long-running sync targets.

**Data flow**: It receives a provider and optional base URL → checks whether the connector has a fixed API host or requires a tenant URL → validates scheme, host, path, credentials, port, query, and fragment against the provider’s rule → returns a normalized HTTPS URL or nothing for fixed-host providers → raises a clear error with an example when invalid.

**Call relations**: SourceObjects._apply_owned calls this before source registration. Its result becomes part of the binding identity and is saved into each stream’s connector source config.

*Call graph*: called by 1 (_apply_owned); 1 external calls (urlsplit).


### Connector Ingestion
Connector support code fetches provider records from external APIs and adapts those records into the system’s standard sync pipeline.

### `core/src/ufo/sources/backend.py`

`orchestration` · `sync run`

Connectors fetch data from outside services in their own streaming style: page after page, sometimes with a cursor that says where to resume next time. The core source system wants one clear result for each run: the pages found, the cursor to save, whether missing records should be deleted, and any provider-reported deletions. This file translates between those worlds.

The main class, ConnectorBackend, runs exactly one connector stream for one configured account. It first asks the authentication proxy for a Credential, so secrets stay behind the broker or local credential store and are not sent to agents or sandboxes. It then finds the requested stream, checks the base URL, fetches records, turns each record into a Page, and returns a SyncResult.

A key concern is avoiding endless or huge sync runs. Incremental streams are capped at a fixed number of records per run. If the connector gives a real checkpoint cursor, the backend saves it. If not, this file creates its own small “backfill envelope” cursor that says: start from the old origin again, skip records already consumed, and keep the best watermark seen. This is like bookmarking a long queue by saying, “start at the entrance, but ignore the first 5,000 people next time.” Full snapshot streams are not capped, because stopping early would make deletion detection unsafe.

#### Function details

##### `binding_name`  (lines 87–97)

```
def binding_name(provider: str, account: str, base_url: str | None) -> str
```

**Purpose**: Creates a stable, human-usable name for a connector binding based on the provider, account, and optional tenant URL. This lets the same external account be referred to consistently in different parts of the system.

**Data flow**: It receives a provider name, an account name, and possibly a base URL. It packages those values into sorted JSON, hashes them to get a short unique fingerprint, replaces underscores in the provider name with dashes, and returns a name like provider-fingerprint.

**Call relations**: This is a standalone naming helper. It relies on JSON serialization and hashing so the same inputs always produce the same binding name, while small input differences produce different names.

*Call graph*: 2 external calls (sha256, dumps).


##### `ConnectorBackend.fetch`  (lines 122–215)

```
async def fetch(self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Runs one sync pass for one connector stream and returns the core system’s SyncResult. It is the central bridge that turns external provider records into UFO pages, saved cursors, and deletion information.

**Data flow**: It receives source configuration, the previously saved cursor, and source authentication context. It asks the auth proxy for a credential, chooses the requested stream, resolves the base URL, decodes any internal backfill cursor, fetches provider pages, skips already-consumed records if needed, converts records into Page objects, gathers deletes, advances watermarks, and finally returns a SyncResult. It may also save a special resume cursor when an incremental run hits the record cap before the stream is finished.

**Call relations**: This is the function the source runner calls when it needs data from a connector. During the run it asks _stream to find the stream definition, _decode_cursor to understand any stored backfill state, _page to turn each provider record into a page, and _max_str to advance a string watermark. If a capped run cannot naturally advance, it logs a warning and returns a cursor that lets the next run make positional progress.

*Call graph*: calls 4 internal fn (_decode_cursor, _page, _stream, _max_str); 4 external calls (__init__, __init__, dumps, warn).


##### `ConnectorBackend._stream`  (lines 217–221)

```
def _stream(self, name: str) -> StreamSpec
```

**Purpose**: Finds the connector stream definition with the requested name. This protects the sync run from silently using the wrong stream.

**Data flow**: It receives a stream name, checks the connector’s available streams one by one, and returns the matching StreamSpec. If no stream matches, it raises an error explaining that the connector does not have that stream.

**Call relations**: ConnectorBackend.fetch calls this near the start of a run so it knows which provider stream to fetch, which primary key to use, which cursor field to read, and whether the stream is a full snapshot or incremental.

*Call graph*: called by 1 (fetch).


##### `ConnectorBackend._decode_cursor`  (lines 224–241)

```
def _decode_cursor(cursor: str | None) -> '_BackfillEnvelope | None'
```

**Purpose**: Recognizes and reads the backend’s own special backfill cursor format. Ordinary connector cursors are left untouched so connectors can use their own cursor formats safely.

**Data flow**: It receives the saved cursor string. If the cursor is missing, not JSON, not a JSON object, or does not contain the reserved ufo_backfill key, it returns nothing. If the reserved key is present, it validates the stored origin, skip count, and watermark, then returns them as a _BackfillEnvelope. If that reserved envelope is malformed, it raises an error because this backend is supposed to be its only writer.

**Call relations**: ConnectorBackend.fetch calls this only for incremental streams. The result tells fetch whether it should resume normally from the connector cursor or re-drive an older cursor and skip records already landed in a previous capped run.

*Call graph*: called by 1 (fetch); 1 external calls (loads).


##### `ConnectorBackend._page`  (lines 243–268)

```
def _page(self, stream: StreamSpec, record: dict[str, Any]) -> Page
```

**Purpose**: Turns one raw provider record into the core Page shape that UFO can store and recall. It gives the page a stable reference, rendered text, title, stream name, and timestamps.

**Data flow**: It receives a stream definition and one record dictionary. It derives a stable record reference, asks the connector to render the record into a title and body, extracts and normalizes created and updated timestamps, and returns a Page object with those fields.

**Call relations**: ConnectorBackend.fetch calls this for every record that should be included in the sync result. Inside, it uses _record_ref to decide the page identity and _record_timestamp to safely parse timestamp fields before constructing the Page.

*Call graph*: calls 2 internal fn (_record_ref, _record_timestamp); called by 1 (fetch); 1 external calls (__init__).


##### `_record_timestamp`  (lines 271–302)

```
def _record_timestamp(record: dict[str, Any], field: str | None, *, connector: str, stream: str) -> str | None
```

**Purpose**: Extracts a timestamp from a provider record and normalizes it into the format expected by source pages. If the timestamp is absent or malformed, it returns nothing instead of crashing the whole record conversion.

**Data flow**: It receives a record, the configured timestamp field name, and labels for the connector and stream. If there is no field configured or the value is missing, it returns None. If the value is a string or non-boolean integer, it tries to normalize it. If normalization fails, or the value is the wrong type, it logs a warning and returns None.

**Call relations**: ConnectorBackend._page calls this once for the created-at field and once for the updated-at field. It may use get_path when the configured field points into nested data, and it uses normalize_page_timestamp to convert accepted values into the system’s standard timestamp form.

*Call graph*: called by 1 (_page); 3 external calls (warn, get_path, normalize_page_timestamp).


##### `_record_ref`  (lines 305–309)

```
def _record_ref(stream: StreamSpec, record: dict[str, Any]) -> str
```

**Purpose**: Chooses a stable identifier for a provider record so updates and deletes point to the same page over time. It prefers the stream’s declared primary key, and falls back to a hash of the whole record when necessary.

**Data flow**: It receives the stream definition and record. If the record’s primary key value is a string or integer, it returns that value as text. Otherwise it serializes the full record in a stable order, hashes it, and returns the hash as a fallback identifier.

**Call relations**: ConnectorBackend._page calls this while building the Page source reference. That same reference style is important because fetched records, later updates, and provider deletion notices need to meet at the same stored page identity.

*Call graph*: called by 1 (_page); 2 external calls (sha256, dumps).


##### `_max_str`  (lines 312–317)

```
def _max_str(current: str | None, value: Any) -> str | None
```

**Purpose**: Keeps the greatest string value seen so far, used as a simple watermark for incremental syncs. A watermark is a saved marker that says “next time, start after the latest value I have processed.”

**Data flow**: It receives the current saved string and a candidate value from a record. If the candidate is not a string, it leaves the current value unchanged. If there is no current value, or the candidate sorts later than it, it returns the candidate; otherwise it returns the current value.

**Call relations**: ConnectorBackend.fetch calls this while reading records from a stream that has a cursor field. The updated watermark may become the next saved cursor, or be stored inside the backend’s backfill envelope when a capped run must continue later.

*Call graph*: called by 1 (fetch).


### `core/src/ufo/sources/rest.py`

`io_transport` · `active during source data fetching`

Many services expose data through REST APIs, which are web endpoints that return data in response to HTTP requests. Each provider has small differences, but the hard parts repeat: add the right authentication, fetch pages without blocking the rest of the system, wait and retry when the service is busy, and stop safely if pagination goes wrong. This file is the reusable toolkit for that job.

The main class, RestConnector, is a base class that real connectors inherit from. A provider-specific connector supplies things like its base web address and the list of streams it can read. RestConnector then opens an asynchronous HTTP client, asks the connector how to paginate, validates each page, flattens records if needed, and yields clean record batches to the sync engine.

The file supports several common pagination styles. Some APIs put the next page in a Link header, some return a cursor token in the response body, some use offset and limit numbers, and some use Microsoft/OData-style next links. It also includes guardrails: it refuses endless page loops, detects repeated cursors, caps retry waits, and includes response bodies in error messages so failures are easier to diagnose. Without this file, every REST connector would need to reimplement the same fragile network and pagination behavior.

#### Function details

##### `get_path`  (lines 44–53)

```
def get_path(data: Mapping[str, Any], path: str, default: Any=None) -> Any
```

**Purpose**: Reads a nested value from a dictionary using a dotted path such as "data.items.next". It is useful when API responses wrap useful fields inside several layers.

**Data flow**: It receives a mapping, a dotted path, and an optional fallback value. It walks through the mapping one part at a time; if any step is missing or no longer dictionary-like, it returns the fallback. If the whole path exists, it returns the value found there.

**Call relations**: Pagination helpers use this when they need to find records, cursor tokens, continuation flags, or server-reported page sizes inside an API response. records_at also uses it to locate a nested list before turning it into records.

*Call graph*: called by 3 (_get_cursor_pages, _get_offset_pages, records_at).


##### `list_or_empty`  (lines 56–60)

```
def list_or_empty(value: Any) -> list[dict[str, Any]]
```

**Purpose**: Turns a value into a safe list of record dictionaries, or returns an empty list if the value is not a list. This prevents unexpected response shapes from leaking into the sync pipeline.

**Data flow**: It receives any value. If the value is a list, it keeps only the items that are dictionaries. If it is not a list, it returns an empty list.

**Call relations**: It is the small safety filter used by records_at, by raw response parsing for link-header pagination, and by the OData page reader when it pulls records from the response's "value" field.

*Call graph*: called by 3 (_get_odata_pages, _response_list, records_at).


##### `dict_or_empty`  (lines 63–66)

```
def dict_or_empty(value: Any) -> dict[str, Any]
```

**Purpose**: Returns a value only if it is a dictionary-shaped record; otherwise it returns an empty dictionary. It is a helper for connectors that need to pull a single nested object out of a larger page record.

**Data flow**: It receives any value. If that value is a dictionary, it passes it through unchanged. If not, it returns an empty dictionary.

**Call relations**: This helper is not used elsewhere in this file, but it is available to provider-specific connector code that imports these REST utilities.


##### `records_at`  (lines 69–74)

```
def records_at(data: Any, path: str | None) -> list[dict[str, Any]]
```

**Purpose**: Finds the list of records in an API response, optionally at a nested path. It gives pagination code one consistent way to say, "the records are over here."

**Data flow**: It receives response data and an optional path. If no path is given, it treats the data itself as the list. If a path is given, it uses get_path to look inside the response, then uses list_or_empty so the result is always a list of dictionaries.

**Call relations**: The cursor, offset, page-number, and link-header pagination flows call this after each HTTP response arrives. It is the bridge between provider-specific response envelopes and the common page-yielding code.

*Call graph*: calls 2 internal fn (get_path, list_or_empty); called by 4 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages, parse).


##### `_int_or_none`  (lines 77–82)

```
def _int_or_none(value: Any) -> int | None
```

**Purpose**: Safely converts a value into an integer only when it is clearly an integer. It avoids guessing with messy values.

**Data flow**: It receives any value. If the value is already an integer, it returns it. If it is a string made only of decimal digits, it converts and returns it. Otherwise it returns None.

**Call relations**: Offset-based pagination uses this when an API reports the actual page size it applied. If the reported size is usable, the next offset advances by that amount.

*Call graph*: called by 1 (_get_offset_pages).


##### `with_context`  (lines 85–88)

```
def with_context(records: Iterable[dict[str, Any]], **context: Any) -> list[dict[str, Any]]
```

**Purpose**: Copies records and adds extra identifying information to each one, such as a parent ID or site ID. This helps later code know where each record came from.

**Data flow**: It receives an iterable of record dictionaries plus named context fields. It creates a new list where each record is copied and the context fields are added. The original records are not changed.

**Call relations**: This helper is not called inside this file, but it is available for connector-specific fan-out flows where child records need to remember their parent or partition.


##### `next_link`  (lines 91–96)

```
def next_link(headers: httpx.Headers) -> str | None
```

**Purpose**: Extracts the "next page" URL from an HTTP Link header. This supports APIs that advertise pagination links in headers rather than in the JSON body.

**Data flow**: It receives response headers. It looks for a Link header and searches it for a relation marked as next. If found, it returns the URL; otherwise it returns None.

**Call relations**: The link-header pagination loop calls this after each page. If it returns another URL, the loop fetches that next page; if it returns None, the pagination ends.

*Call graph*: called by 1 (_get_link_header_pages); 1 external calls (get).


##### `_is_retryable`  (lines 99–104)

```
def _is_retryable(error: BaseException) -> bool
```

**Purpose**: Decides whether a failed request is worth trying again. It treats network transport problems and common temporary server or rate-limit responses as retryable.

**Data flow**: It receives an exception. Network transport errors return true. HTTP errors return true only for configured temporary status codes such as 429 or 5xx errors. Other errors return false.

**Call relations**: The shared sender, RestConnector._send, asks this helper after a request fails. A true answer allows another attempt; a false answer makes the error bubble up immediately.

*Call graph*: called by 1 (_send).


##### `_retry_wait`  (lines 107–124)

```
def _retry_wait(error: BaseException, delay: float) -> float
```

**Purpose**: Chooses how long to wait before retrying a failed request. For rate limits, it honors a usable Retry-After header, but caps it so one request cannot sleep for too long.

**Data flow**: It receives the error and the caller's current backoff delay. If the error is not a 429 rate-limit response, it returns the given delay. If there is a valid finite non-negative Retry-After number, it returns that number capped to the maximum allowed wait.

**Call relations**: RestConnector._send calls this before sleeping between retry attempts. It works with _is_retryable to make retries polite without letting bad headers stall the whole fetch.

*Call graph*: called by 1 (_send); 1 external calls (isfinite).


##### `_raise_for_status`  (lines 127–138)

```
def _raise_for_status(response: httpx.Response) -> None
```

**Purpose**: Turns an unsuccessful HTTP response into an exception whose message includes a short piece of the response body. This makes API errors much easier to understand.

**Data flow**: It receives an HTTP response. If the response is successful, it does nothing. If not, it reads a capped amount of response text and raises an HTTPStatusError containing the status, request, URL, and body snippet.

**Call relations**: RestConnector._send calls this immediately after each HTTP response. Successful responses continue to parsing; failed responses enter the retry-or-raise path.

*Call graph*: called by 1 (_send); 1 external calls (HTTPStatusError).


##### `_json_or_empty`  (lines 141–145)

```
def _json_or_empty(response: httpx.Response) -> dict[str, Any]
```

**Purpose**: Reads a JSON object from a response, while treating empty responses as an empty dictionary. This is useful for APIs that return no body for some successful calls.

**Data flow**: It receives an HTTP response. If the status is 204 or the body is empty, it returns {}. Otherwise it parses the response JSON and returns it as a dictionary.

**Call relations**: RestConnector._get and RestConnector._post use this after the shared request-and-retry logic has returned a successful response.

*Call graph*: called by 2 (_get, _post); 1 external calls (json).


##### `_response_list`  (lines 148–151)

```
def _response_list(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Reads a top-level JSON list of records from a response. Empty or wrongly shaped responses become an empty list.

**Data flow**: It receives an HTTP response. If the response has no content, it returns an empty list. Otherwise it parses JSON and passes the result through list_or_empty so only dictionary records remain.

**Call relations**: Link-header pagination uses this as its default parser when the API returns records as the whole response body.

*Call graph*: calls 1 internal fn (list_or_empty); called by 1 (_get_link_header_pages); 1 external calls (json).


##### `_bound_pages`  (lines 154–160)

```
def _bound_pages(who: str, pages: int) -> None
```

**Purpose**: Stops a pagination loop if it has fetched too many pages without ending. This is a safety fuse against buggy or hostile APIs that never stop saying there is another page.

**Data flow**: It receives a label for the loop and the number of pages fetched so far. If the count is over the maximum, it raises a RuntimeError. Otherwise it lets the loop continue.

**Call relations**: Every built-in pagination loop calls this near the start of each page fetch. It protects link, cursor, OData, offset, and page-number pagination from running forever.

*Call graph*: called by 5 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages, _get_offset_pages, _get_page_number_pages).


##### `_bound_cursor`  (lines 163–169)

```
def _bound_cursor(who: str, token: str, seen: set[str]) -> None
```

**Purpose**: Stops cursor-based pagination if the next-page marker repeats. A repeated marker means the provider is not advancing and the connector would fetch the same page again and again.

**Data flow**: It receives a loop label, the next cursor or next URL, and a set of values already seen. If the token is already in the set, it raises an error. Otherwise it records the token as seen.

**Call relations**: The link-header, cursor-token, and OData pagination loops call this whenever a continuation value appears. It catches endless loops earlier than the general page-count limit.

*Call graph*: called by 3 (_get_cursor_pages, _get_link_header_pages, _get_odata_pages).


##### `RestConnector.streams`  (lines 178–179)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: Returns the stream definitions declared by a concrete REST connector. A stream is one collection of records the connector knows how to read.

**Data flow**: It reads the class-level streams_list and returns a new list copy. Returning a copy means callers can inspect or work with the list without directly mutating the class setting.

**Call relations**: The wider connector framework asks connectors what streams they expose. This method supplies that answer for subclasses that simply fill in streams_list.


##### `RestConnector._make_client`  (lines 181–198)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the asynchronous HTTP client used to talk to one provider account. It applies the base URL, timeouts, JSON headers, and the credential style chosen for that account.

**Data flow**: It receives a base URL and a Credential. If the credential contains a custom transport, it builds a client that sends requests through that transport. Otherwise it adds a bearer token or custom auth headers. If no usable authentication is present, it raises an error instead of making anonymous requests.

**Call relations**: RestConnector.fetch_page calls this at the start of a fetch. All later GET and POST helpers use the client it creates, so authentication and timeouts are consistent for the whole page run.

*Call graph*: called by 1 (fetch_page); 2 external calls (AsyncClient, Timeout).


##### `RestConnector._get`  (lines 200–203)

```
async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a GET request and returns the response body as a JSON dictionary, with empty responses represented as {}. It is the common helper for read endpoints that return object-shaped JSON.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It asks _get_raw to send the request with retry behavior, then passes the successful response to _json_or_empty. The caller receives a dictionary.

**Call relations**: Cursor, offset, and page-number pagination call this for ordinary JSON-body pages. It keeps those pagination loops focused on page logic rather than network and parsing details.

*Call graph*: calls 2 internal fn (_get_raw, _json_or_empty); called by 3 (_get_cursor_pages, _get_offset_pages, _get_page_number_pages).


##### `RestConnector._get_raw`  (lines 205–209)

```
async def _get_raw(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a GET request and returns the full HTTP response. This is needed when pagination information lives in headers or when callers need more than the parsed body.

**Data flow**: It receives an HTTP client, path, and optional query parameters. It wraps client.get in the shared _send retry envelope. The result is a successful httpx.Response or an exception if the request cannot succeed.

**Call relations**: RestConnector._get uses it before parsing JSON. Link-header and OData pagination also call it directly because they need headers or absolute next-link behavior.

*Call graph*: calls 1 internal fn (_send); called by 3 (_get, _get_link_header_pages, _get_odata_pages).


##### `RestConnector._post`  (lines 211–216)

```
async def _post(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> dict[str, Any]
```

**Purpose**: Performs a POST request for APIs that expose read-style endpoints through POST. It keeps the connector's read-only design while supporting providers such as search endpoints.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the POST through _send, then parses a successful response with _json_or_empty. The caller receives a dictionary.

**Call relations**: This file does not call it directly, but subclasses can use it in custom pagination methods. It shares the same retry and error behavior as the GET path.

*Call graph*: calls 2 internal fn (_send, _json_or_empty).


##### `RestConnector._post_raw`  (lines 218–224)

```
async def _post_raw(self, client: httpx.AsyncClient, path: str, *, json: dict[str, Any] | None=None) -> httpx.Response
```

**Purpose**: Performs a POST request and returns the raw response. It is for read endpoints whose response is not a normal JSON object, such as a top-level list or streamed batch format.

**Data flow**: It receives an HTTP client, path, and optional JSON body. It sends the POST through _send and returns the successful response unchanged.

**Call relations**: Provider-specific connectors can call this from custom pagination code when they need direct access to the response body or headers. It still benefits from the shared retry wrapper.

*Call graph*: calls 1 internal fn (_send).


##### `RestConnector._send`  (lines 226–242)

```
async def _send(self, request: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response
```

**Purpose**: Runs one HTTP request with consistent retry behavior. It retries temporary network, rate-limit, and server failures, while letting permanent failures surface clearly.

**Data flow**: It receives a no-argument function that starts an HTTP request. It tries the request, checks the status, and returns the response if successful. On retryable failures, it sleeps for a Retry-After or exponential backoff delay and tries again until attempts are exhausted. Final failure is raised to the caller.

**Call relations**: _get_raw, _post, and _post_raw all route through this method. It is the shared envelope that makes every REST read behave the same under flaky networks or temporary API problems.

*Call graph*: calls 3 internal fn (_is_retryable, _raise_for_status, _retry_wait); called by 3 (_get_raw, _post, _post_raw); 1 external calls (sleep).


##### `RestConnector.fetch_page`  (lines 244–274)

```
async def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main page-fetching entry for REST connectors. It opens the client, runs pagination, validates each page, flattens records, and yields clean pages to the sync engine.

**Data flow**: It receives a stream, an optional cursor, credentials, an optional base URL override, and optional current-user information. It chooses a base URL, builds an HTTP client, gets page data from paginate_source, skips empty pages, validates the shape, flattens each record, and yields either a list of records or a StreamPage with records, deletes, and next cursor preserved.

**Call relations**: The broader source framework calls this when it wants data for a stream. fetch_page then calls _make_client and paginate_source, and uses _validate_page and flatten before handing pages back upstream.

*Call graph*: calls 4 internal fn (_make_client, _validate_page, flatten, paginate_source); 1 external calls (__init__).


##### `RestConnector.paginate_source`  (lines 276–284)

```
def paginate_source(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Provides a small hook between fetch_page and paginate. It exists so subclasses can include extra source context, such as the authenticated user's ID, before normal pagination begins.

**Data flow**: It receives the HTTP client, stream, cursor, and optional self user ID. The default implementation ignores self_user_id and simply calls paginate with the cursor. It returns the asynchronous page iterator from paginate.

**Call relations**: fetch_page calls this instead of calling paginate directly. By default it hands off to RestConnector.paginate, but subclasses can override it when pagination depends on more account context.

*Call graph*: calls 1 internal fn (paginate); called by 1 (fetch_page).


##### `RestConnector.paginate`  (lines 286–298)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Chooses the default pagination behavior for a stream. If the stream declares a supported pagination strategy, this method runs it; otherwise it tells subclass authors they must implement custom pagination.

**Data flow**: It receives an HTTP client, stream, and optional cursor. It inspects the stream's pagination settings. If no usable strategy is declared, it raises NotImplementedError. Otherwise it yields pages produced by paginate_from_strategy.

**Call relations**: paginate_source calls this in the normal flow. It delegates strategy-specific work to paginate_from_strategy, keeping this method as the simple decision point.

*Call graph*: calls 1 internal fn (paginate_from_strategy); called by 1 (paginate_source).


##### `RestConnector.paginate_from_strategy`  (lines 300–362)

```
async def paginate_from_strategy(self, stream: StreamSpec, *, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Runs one of the built-in pagination loops described by a stream's Pagination settings. It lets many connectors declare pagination in data rather than writing custom code.

**Data flow**: It receives a stream and HTTP client. It reads the stream's pagination strategy, path, record path, cursor names, page-size settings, and extra parameters. Depending on the strategy, it calls the cursor, link-header, or offset pagination helper and yields each list of records.

**Call relations**: RestConnector.paginate calls this for streams with declared pagination. It hands work to _get_cursor_pages, _get_link_header_pages, or _get_offset_pages, and may call _strategy_path if the stream did not explicitly name its request path.

*Call graph*: calls 4 internal fn (_get_cursor_pages, _get_link_header_pages, _get_offset_pages, _strategy_path); called by 1 (paginate).


##### `RestConnector.paginate_from_strategy.parse`  (lines 332–334)

```
def parse(response: httpx.Response) -> list[dict[str, Any]]
```

**Purpose**: Parses records out of a link-header pagination response when the records are nested inside the JSON body. It is a tiny local adapter for APIs that do not return a top-level list.

**Data flow**: It receives an HTTP response. It parses the body as JSON when content exists, then uses records_at with the configured record path. It returns a list of dictionary records.

**Call relations**: paginate_from_strategy creates this parser only for next-link pagination with a record_path. It passes the parser to _get_link_header_pages so that helper can stay generic.

*Call graph*: calls 1 internal fn (records_at); 1 external calls (json).


##### `RestConnector._get_link_header_pages`  (lines 364–392)

```
async def _get_link_header_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None, page_size_param: str | None='per_page', page_size: int | None=None, parse_records: C
```

**Purpose**: Fetches pages for APIs that put the next-page URL in the HTTP Link header. This is common in REST APIs that follow web linking conventions.

**Data flow**: It receives a client, initial path, optional parameters, optional page-size settings, and an optional response parser. It fetches the first page, yields parsed records, reads the next link from headers, checks that the link is not repeating, and keeps fetching until no next link remains.

**Call relations**: paginate_from_strategy calls this for the next_link strategy. It uses _get_raw for requests, _response_list or a custom parser for records, next_link for continuation, and the pagination guard helpers for safety.

*Call graph*: calls 5 internal fn (_get_raw, _bound_cursor, _bound_pages, _response_list, next_link); called by 1 (paginate_from_strategy).


##### `RestConnector._get_cursor_pages`  (lines 394–426)

```
async def _get_cursor_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, next_cursor_path: str, params: dict[str, Any] | None=None, cursor_param: str='cursor', page_size_pa
```

**Purpose**: Fetches pages for APIs that return a next cursor token in the response body. A cursor is like a bookmark the server gives back for the next request.

**Data flow**: It receives a client, path, record path, cursor path, query parameter names, page size, and extra parameters. It sends a request, extracts records, yields them, reads the next cursor, and repeats with that cursor added to the query. It stops when no valid cursor is returned.

**Call relations**: paginate_from_strategy calls this for the next_cursor strategy. It uses _get to fetch JSON, records_at to find records, get_path to find the next cursor, and guard helpers to prevent runaway loops.

*Call graph*: calls 5 internal fn (_get, _bound_cursor, _bound_pages, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_odata_pages`  (lines 428–454)

```
async def _get_odata_pages(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Microsoft Graph or OData-style pages, where records live under "value" and the next-page URL appears as "@odata.nextLink". OData is a common API format used by Microsoft services.

**Data flow**: It receives a client, path, and optional first-request parameters. It fetches the current URL, reads records from the "value" field, yields them, then follows "@odata.nextLink" if present. Only the first request uses the original parameters because later next links already include their own query information.

**Call relations**: This helper is available for subclasses that need OData pagination. It uses _get_raw for requests, list_or_empty for record safety, and the same page and cursor bounds used by the built-in strategy helpers.

*Call graph*: calls 4 internal fn (_get_raw, _bound_cursor, _bound_pages, list_or_empty).


##### `RestConnector._get_offset_pages`  (lines 456–496)

```
async def _get_offset_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, limit: int, params: dict[str, Any] | None=None, limit_param: str='limit', offset_param: str='offset
```

**Purpose**: Fetches pages for APIs that use offset and limit numbers. The offset says where to start, and the limit says how many records to ask for.

**Data flow**: It receives a client, path, record path, limit, parameter names, optional extra parameters, and optional response fields that say whether more pages exist or what limit the server actually used. It requests a page, yields records, decides whether to stop, then advances the offset for the next request.

**Call relations**: paginate_from_strategy calls this for the offset_limit strategy. It uses _get for JSON responses, records_at and get_path to inspect the response, _int_or_none for server-reported sizes, and _bound_pages as the loop safety fuse.

*Call graph*: calls 5 internal fn (_get, _bound_pages, _int_or_none, get_path, records_at); called by 1 (paginate_from_strategy).


##### `RestConnector._get_page_number_pages`  (lines 498–527)

```
async def _get_page_number_pages(self, client: httpx.AsyncClient, path: str, *, records_path: str | None, page_size: int, params: dict[str, Any] | None=None, page_param: str='page', page_size_param: s
```

**Purpose**: Fetches pages for APIs that use page numbers instead of cursors or offsets. It keeps asking for page 1, page 2, page 3, and so on until the final short page appears.

**Data flow**: It receives a client, path, record path, page size, optional parameters, page parameter names, and a starting page number. It requests the current page, yields any records, stops when fewer records than the page size are returned, or increments the page number and continues.

**Call relations**: This helper is not selected by paginate_from_strategy in this file, but subclasses can call it from custom paginate methods. It shares the same _get, records_at, and page-bound safety behavior as the other pagination loops.

*Call graph*: calls 3 internal fn (_get, _bound_pages, records_at).


##### `RestConnector._strategy_path`  (lines 529–535)

```
def _strategy_path(self, stream: StreamSpec) -> str
```

**Purpose**: Resolves the request path for a stream when the stream's pagination settings did not include one. The base version raises an error because only provider-specific connectors know their own path mapping.

**Data flow**: It receives a stream. Instead of guessing a path, it raises NotImplementedError with a message explaining that either the pagination path must be set or a subclass must override this method.

**Call relations**: paginate_from_strategy calls this only when a Pagination object lacks a path. Subclasses with central per-stream path tables can override it to supply the missing path.

*Call graph*: called by 1 (paginate_from_strategy).


##### `RestConnector.flatten`  (lines 537–540)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns one raw API record into the flat dictionary shape the sync writer expects. The default does nothing because many APIs already return flat enough records.

**Data flow**: It receives a record and the stream it belongs to. The base implementation returns the same record unchanged. Subclasses can override it to pull nested fields upward or reshape records.

**Call relations**: fetch_page calls this for every validated record before yielding the page. It is the last record-shaping step in the REST connector flow.

*Call graph*: called by 1 (fetch_page).


##### `RestConnector._validate_page`  (lines 542–553)

```
def _validate_page(self, page: Any, stream: StreamSpec) -> None
```

**Purpose**: Checks that pagination produced a list of dictionary records. It fails early if connector code yields the wrong shape.

**Data flow**: It receives a page value and stream. If the page is not a list, it raises a TypeError. If any item in the list is not a dictionary, it raises a TypeError naming the bad kind of item. Otherwise it changes nothing.

**Call relations**: fetch_page calls this before flattening and yielding records. It protects downstream sync code from receiving malformed pages from either built-in pagination or custom subclass pagination.

*Call graph*: called by 1 (fetch_page).


### Synced Page Storage
The sync layer persists external content as pages with cursors, deletions, and replayable change feeds, while page APIs expose the resulting read-only content.

### `core/src/ufo/sources/sync.py`

`orchestration` · `background sync polling and downstream indexing replay`

This file solves a common problem: outside systems change over time, and UFO needs a dependable copy of their documents without losing track of what is new, changed, or deleted. Think of it like a mailroom. Source backends go out and collect mail from different places. The sync driver checks which mailboxes are due, brings in the mail, stores the heavy contents in a blob store, and updates a database card for each document.

A backend is the plug-in point for each source type. The built-in `FolderSource` reads local files, while other connectors can be added elsewhere. Each fetched document becomes a `Page`, with a stable source reference, body text, title, stream name, and optional timestamps. The driver uses a content digest, which is a fingerprint of the body, to avoid rewriting pages that did not change.

The file also handles deletions carefully. A full snapshot source can say, “this is everything I have,” so missing old pages are marked as tombstones. An incremental source only names explicit deletes. This avoids wiping pages just because a partial fetch did not mention them.

Finally, `CorePageFeed` lets downstream indexers replay page changes in order using a cursor. That means an indexer can stop, restart, and continue from the exact next change instead of starting over.

#### Function details

##### `normalize_page_timestamp`  (lines 58–78)

```
def normalize_page_timestamp(value: str) -> str
```

**Purpose**: Turns a page timestamp from a source into one standard UTC text format. This matters because different providers may send dates as Unix numbers, ISO strings, or dates with a trailing `Z`, and the rest of the system needs one reliable shape.

**Data flow**: It receives a timestamp string. If the string is numeric, it treats it as seconds or milliseconds since the Unix epoch; otherwise it parses it as an ISO-style date and requires a timezone unless it is a plain calendar date. It returns the same moment as a UTC ISO timestamp with microseconds, or raises an error if the input is unsafe or unclear.

**Call relations**: It is used by `Page.normalize_timestamp` when a `Page` is created or validated. That keeps page timestamps consistent before the sync driver stores them.

*Call graph*: called by 1 (normalize_timestamp); 2 external calls (fromisoformat, fromtimestamp).


##### `Page.digest`  (lines 94–95)

```
def digest(self) -> str
```

**Purpose**: Creates a stable fingerprint for a page’s body text. The sync driver uses this fingerprint to tell whether the document content actually changed.

**Data flow**: It reads the page’s `body`, encodes it as bytes, runs SHA-256 over it, and returns a string beginning with `sha256:`. It does not change the page.

**Call relations**: The sync commit path reads this property when comparing fetched pages with existing database rows. If the digest matches, the driver can skip rewriting the body.

*Call graph*: 1 external calls (sha256).


##### `Page.normalize_timestamp`  (lines 99–102)

```
def normalize_timestamp(cls, value: str | None) -> str | None
```

**Purpose**: Normalizes optional creation and update timestamps on a page. It keeps provider-specific date formats from leaking into the database.

**Data flow**: It receives either `None` or a timestamp string during page validation. `None` passes through unchanged; a string is sent to `normalize_page_timestamp` and replaced with the normalized UTC version.

**Call relations**: It is part of the `Page` model validation flow. Any backend that creates `Page` objects benefits from this cleanup automatically.

*Call graph*: calls 1 internal fn (normalize_page_timestamp).


##### `StreamSkipped.__init__`  (lines 134–136)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Stores the human-readable reason why a source stream was intentionally skipped. This is used when a provider refuses access for a non-fatal reason, such as a missing permission or plan limit.

**Data flow**: It receives a reason string, passes it to the base error type, and also saves it as `reason` on the exception object. The exception can then be logged without treating the run as a real data failure.

**Call relations**: Connector code raises `StreamSkipped` when a stream should not be synced right now. `SyncDriver.run` catches it, logs a skipped run, and calls `_skip` instead of failing the source.

*Call graph*: called by 49 (paginate, paginate, paginate, paginate, _org_stream, paginate, paginate, paginate, paginate, paginate (+15 more)).


##### `SourceBackend.config_model`  (lines 176–176)

```
def config_model(self) -> type[ConfigT]
```

**Purpose**: Defines the typed configuration model a source backend expects. This prevents the core sync code from treating source settings as an unstructured bag of values.

**Data flow**: A backend exposes a model class. The sync driver reads that class and uses it to validate the source row’s stored configuration before fetching.

**Call relations**: This is part of the `SourceBackend` protocol, meaning every backend must provide it. `SyncDriver._fetch` relies on it before calling the backend’s fetch method.


##### `SourceBackend.fetch`  (lines 178–178)

```
async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Defines how a backend retrieves documents from its source. Each connector implements this to return pages, a new cursor, and any deletion information.

**Data flow**: It receives validated backend configuration, the previous cursor if any, and source authentication context. It returns a `SyncResult` describing fetched pages, where to resume next time, and how deletes should be interpreted.

**Call relations**: This is the main contract between the core sync driver and each source backend. `SyncDriver._fetch` calls it after preparing config and authentication.


##### `FolderSource.fetch`  (lines 192–203)

```
async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Reads a local folder and turns every file into a page. It is the built-in source backend for syncing plain files from disk.

**Data flow**: It receives a folder source config, ignores the cursor and auth, and reads the configured root directory in a worker thread so file I/O does not block the event loop. It wraps each file’s relative path and text into a `Page`, then returns a full snapshot `SyncResult`.

**Call relations**: The sync driver calls this through the `SourceBackend.fetch` interface when a source row uses the folder backend. It delegates the actual disk scan to `FolderSource._read`.

*Call graph*: 4 external calls (__init__, __init__, to_thread, Path).


##### `FolderSource._read`  (lines 206–213)

```
def _read(root: Path) -> tuple[tuple[str, str], ...]
```

**Purpose**: Scans a folder and reads all files as UTF-8 text. It is the low-level disk-reading helper for the folder backend.

**Data flow**: It receives a root path. If the path is not a directory, it raises `FileNotFoundError`; otherwise it walks all files below the root, sorts them, reads their bytes, decodes them as UTF-8, and returns pairs of relative path and text.

**Call relations**: `FolderSource.fetch` runs this helper in a background thread. The returned file entries are then converted into `Page` objects.

*Call graph*: 2 external calls (is_dir, rglob).


##### `source_row_id`  (lines 216–228)

```
def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object], *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Builds a repeatable database ID for a source from its workspace, backend, config, and optional connection generation. This lets the app restart and register the same configured source without creating duplicates.

**Data flow**: It receives the workspace ID, backend name, config mapping, and optionally a connection ID. It serializes the config in sorted order, combines the pieces into a string, and turns that into a UUID using a namespace-based UUID function.

**Call relations**: `register_sources` uses this before inserting configured sources. Because the ID is deterministic, the registration process can safely run again at startup.

*Call graph*: called by 1 (register_sources); 2 external calls (dumps, uuid5).


##### `page_id_for`  (lines 231–234)

```
def page_id_for(source_id: UUID, source_ref: str) -> UUID
```

**Purpose**: Builds a repeatable page ID for one document inside one source. This keeps updates, re-fetches, and deletes pointed at the same database row.

**Data flow**: It receives a source ID and the source’s own stable document reference. It combines them into a namespace-based UUID and returns that UUID.

**Call relations**: `SyncDriver._commit` uses this for every fetched page and every explicit delete reference. That makes page writes and tombstones line up with earlier versions of the same document.

*Call graph*: called by 1 (_commit); 1 external calls (uuid5).


##### `register_sources`  (lines 237–302)

```
async def register_sources(configured: tuple[SourceEntry, ...]) -> None
```

**Purpose**: Creates database rows for sources listed in configuration at startup. It makes sure configured sources exist and are granted to the main agent without duplicating old rows.

**Data flow**: It receives configured source entries. If there are none, it exits. Otherwise it opens a workspace transaction, finds the workspace and main agent, computes each source’s stable ID, skips sources that were previously removed, inserts new source rows, and adds a grant for the main agent.

**Call relations**: This runs outside the periodic sync polling loop, usually during boot. It uses `source_row_id` to get stable IDs and database insert helpers to make registration idempotent.

*Call graph*: calls 1 internal fn (source_row_id); 4 external calls (now, insert, select, workspace_tx).


##### `_rescheduled`  (lines 320–331)

```
def _rescheduled(claimed: ClaimedSource, when: datetime) -> sa.Case[datetime]
```

**Purpose**: Decides what `next_sync_at` should become when a claimed sync finishes. It protects resync requests that arrived while the sync was already running.

**Data flow**: It receives the claimed source and a proposed next time. It returns a database expression: use the proposed time only if no newer request appeared after the claim started; otherwise keep the newer existing `next_sync_at`.

**Call relations**: `SyncDriver._write`, `_release`, and `_skip` all use this when freeing a source claim. It prevents a finishing run from accidentally postponing a fresh sync request.

*Call graph*: called by 3 (_release, _skip, _write); 1 external calls (case).


##### `_stream_tags`  (lines 334–339)

```
def _stream_tags(source: ClaimedSource) -> dict[str, str]
```

**Purpose**: Builds small labels that identify which provider stream a sync log or metric belongs to. These labels make monitoring useful without creating unbounded metric names.

**Data flow**: It receives a claimed source, reads the backend name and the `stream` value from the source config, and returns them in a dictionary. Missing or non-string stream values become an empty string.

**Call relations**: `SyncDriver.run`, `_report_ok`, and `_report_failed` use these tags when recording skipped, successful, or failed syncs. It calls `_config_value` to read config safely.

*Call graph*: calls 1 internal fn (_config_value); called by 3 (_report_failed, _report_ok, run).


##### `_config_value`  (lines 342–344)

```
def _config_value(source: ClaimedSource, key: str) -> str
```

**Purpose**: Safely reads one string value from a source config. It avoids logging or tagging unexpected non-string values.

**Data flow**: It receives a claimed source and a config key. It looks up the key in the source config and returns the value only if it is a string; otherwise it returns an empty string.

**Call relations**: `_stream_tags`, `SyncDriver._report_ok`, and `SyncDriver._report_failed` use it to pull fields such as stream and account for logs and metrics.

*Call graph*: called by 3 (_report_failed, _report_ok, _stream_tags).


##### `SyncDriver.candidate_workspaces`  (lines 378–398)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one source ready to sync. This lets the scheduler avoid opening work for quiet workspaces.

**Data flow**: It reads the current time, opens an owner-level database transaction, and selects distinct workspace IDs for sources that are due, not removed, and not actively claimed unless the claim expired. It returns those workspace IDs as a tuple.

**Call relations**: A higher-level dispatcher can call this before running workspace-bound sync jobs. It is a lightweight pre-check that tells the system where `SyncDriver.run` is worth invoking.

*Call graph*: 4 external calls (now, or_, select, owner_tx).


##### `SyncDriver.run`  (lines 400–419)

```
async def run(self) -> None
```

**Purpose**: Runs one sync pass for due sources in the current workspace. It claims sources, fetches their pages, commits changes, and handles skipped or failed streams without blocking other sources.

**Data flow**: It creates a unique claim token, asks `_claim_due` for due sources, then processes each one. For a normal source it calls `_fetch` and `_commit`; for `StreamSkipped` it logs and calls `_skip`; for any other error it calculates backoff, reports the failure, and releases the claim.

**Call relations**: This is the main driver method used during the background sync tick. It coordinates nearly every helper in this file: claiming, fetching, writing, success logs, failure logs, backoff, release, and skip behavior.

*Call graph*: calls 8 internal fn (_claim_due, _commit, _error_backoff, _fetch, _release, _report_failed, _skip, _stream_tags); 4 external calls (suppress, now, log, uuid4).


##### `SyncDriver._claim_due`  (lines 421–471)

```
async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]
```

**Purpose**: Reserves a batch of due source rows so this worker can sync them without another worker doing the same work. The claim is like putting a temporary “I’m working on this” note on each source.

**Data flow**: It receives a claim token, reads due sources from the database, optionally uses row locking on PostgreSQL, and writes the claim token plus an expiry time onto the selected rows. It returns `ClaimedSource` objects containing the source details needed for the run.

**Call relations**: `SyncDriver.run` calls this at the start of a pass. The returned claim token is later checked by `_write`, `_release`, and `_skip` so only the worker that claimed the source can finish it.

*Call graph*: called by 1 (run); 7 external calls (__init__, now, timedelta, or_, select, update, workspace_tx).


##### `SyncDriver._fetch`  (lines 473–492)

```
async def _fetch(self, source: ClaimedSource) -> SyncResult
```

**Purpose**: Prepares and calls the right backend for one claimed source. It turns stored config into typed config and supplies authentication context without the core code holding provider tokens directly.

**Data flow**: It receives a claimed source. It finds the backend by name, validates the source config using that backend’s config model, optionally resolves the product’s own external user identity, builds `SourceAuth`, and awaits the backend’s `fetch`. It returns the backend’s `SyncResult`.

**Call relations**: `SyncDriver.run` calls this after claiming a source. It hands control to the backend implementation through `SourceBackend.fetch`, then gives the result back to the driver for committing.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `SyncDriver._commit`  (lines 494–535)

```
async def _commit(self, source: ClaimedSource, result: SyncResult) -> None
```

**Purpose**: Compares fetched pages with what is already stored and prepares the minimum set of database and blob-store changes. It avoids rewriting unchanged document bodies.

**Data flow**: It receives a claimed source and a sync result. It reads prior pages, assigns stable page IDs, checks each page’s digest and metadata, writes changed bodies to the blob store, records changed pages and metadata-only updates, converts delete references into page IDs, and calls `_write`. It then logs a successful sync summary.

**Call relations**: `SyncDriver.run` calls this after `_fetch` succeeds. It uses `_prior_pages`, `page_id_for`, `_write`, and `_report_ok` to turn backend output into durable stored changes.

*Call graph*: calls 4 internal fn (_prior_pages, _report_ok, _write, page_id_for); called by 1 (run); 2 external calls (__init__, __init__).


##### `SyncDriver._prior_pages`  (lines 537–569)

```
async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]
```

**Purpose**: Loads the current database state for all pages belonging to one source. This gives `_commit` something to compare the new fetch against.

**Data flow**: It receives a source ID, reads existing page rows in a workspace transaction, and returns a dictionary keyed by page ID. Each value contains the stored digest, tombstone flag, and browse metadata.

**Call relations**: `SyncDriver._commit` calls this before examining fetched pages. The comparison decides whether a page body changed, only metadata changed, or nothing changed.

*Call graph*: called by 1 (_commit); 3 external calls (__init__, select, workspace_tx).


##### `SyncDriver._write`  (lines 571–693)

```
async def _write(self, source: ClaimedSource, next_cursor: str | None, changed: list[ChangedPage], metadata: list[PageBrowse], fetched: list[UUID], deleted: list[UUID], snapshot: bool) -> int
```

**Purpose**: Writes one sync result into the database and frees the source claim on success. It inserts or updates changed pages, applies metadata-only updates, marks deleted pages as tombstones, and schedules the next sync.

**Data flow**: It receives the claimed source, next cursor, lists of changed pages, metadata-only pages, fetched page IDs, explicit deletes, and whether the fetch was a full snapshot. Inside a transaction it verifies the source is still valid and still claimed by this worker, writes page changes, tombstones explicit deletes and snapshot-missing pages, updates page subject if needed, and updates the source row with cursor, next sync time, zero errors, and no claim. It returns how many pages were tombstoned.

**Call relations**: `SyncDriver._commit` calls this after blob bodies have been stored. It uses `_rescheduled` when setting the next sync time so a newer resync request is not overwritten.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (_commit); 6 external calls (now, timedelta, insert, select, update, workspace_tx).


##### `SyncDriver._report_ok`  (lines 695–707)

```
def _report_ok(self, source: ClaimedSource, fetched: int, written: int, tombstoned: int) -> None
```

**Purpose**: Records a successful source sync in logs. The event includes how many pages were fetched, written, and tombstoned.

**Data flow**: It receives the source and count values. It builds provider, stream, and account labels, then logs a `source_sync.ok` event. Logging errors are suppressed so monitoring problems do not break a successful sync.

**Call relations**: `SyncDriver._commit` calls this after `_write` completes. It uses `_stream_tags` and `_config_value` to attach readable context to the log.

*Call graph*: calls 2 internal fn (_config_value, _stream_tags); called by 1 (_commit); 2 external calls (suppress, log).


##### `SyncDriver._error_backoff`  (lines 709–717)

```
def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]
```

**Purpose**: Calculates how long to wait before retrying a failing source. Repeated failures wait longer, up to a cap, so the system does not hammer a broken provider.

**Data flow**: It receives the source and the current time. It increments the consecutive error count, doubles the retry delay based on that count, caps the delay, and returns the new error count plus the next retry time.

**Call relations**: `SyncDriver.run` calls this when fetch or commit raises an error. The returned values are passed to `_report_failed` and `_release`.

*Call graph*: called by 1 (run); 1 external calls (timedelta).


##### `SyncDriver._report_failed`  (lines 719–758)

```
def _report_failed(self, source: ClaimedSource, error: Exception, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Records a failed sync as both a searchable log event and a metric. It is careful not to leak sensitive provider response text into logs.

**Data flow**: It receives the source, exception, whether the cursor will be reset, the new error count, and the next retry time. It extracts safe labels, names the error class, adds a bounded provider fault summary for HTTP status errors, logs the failure, and emits a failure metric. Each reporting step is protected so telemetry errors do not replace the original failure.

**Call relations**: `SyncDriver.run` calls this after `_error_backoff` when a source fails. It uses `_stream_tags` and `_config_value` to describe which provider stream failed.

*Call graph*: calls 2 internal fn (_config_value, _stream_tags); called by 1 (run); 4 external calls (suppress, isoformat, emit_metric, log_error).


##### `SyncDriver._release`  (lines 760–786)

```
async def _release(self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime) -> None
```

**Purpose**: Frees a source claim after a real failure and schedules its retry. It also clears the stored cursor when the backend says the old cursor expired.

**Data flow**: It receives the source, whether to reset the cursor, the new error count, and the retry time. It updates the source row to clear the claim, set the cursor to `None` or keep the old one, store the error count, and set `next_sync_at` through `_rescheduled`.

**Call relations**: `SyncDriver.run` calls this after reporting a failure. It is the failure-path counterpart to the successful claim clearing done in `_write`.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 2 external calls (update, workspace_tx).


##### `SyncDriver._skip`  (lines 788–811)

```
async def _skip(self, source: ClaimedSource) -> None
```

**Purpose**: Frees a source claim after a stream was intentionally skipped rather than failed. Existing pages are left alone and the source is retried at the normal interval.

**Data flow**: It receives the claimed source, gets the current time, and updates the source row to clear the claim, reset consecutive errors, and set the next sync time through `_rescheduled`. It does not change cursor or pages.

**Call relations**: `SyncDriver.run` calls this when a backend raises `StreamSkipped`. This keeps missing permissions or plan gates from triggering delete detection or failure backoff.

*Call graph*: calls 1 internal fn (_rescheduled); called by 1 (run); 4 external calls (now, timedelta, update, workspace_tx).


##### `PageFeed.pages_changed_since`  (lines 851–851)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Defines the interface an indexer uses to read changed pages in order. The cursor lets a reader resume from where it last stopped.

**Data flow**: It receives an optional cursor and a requested limit. An implementation returns a `PageBatch` containing page changes and the next cursor, if there are changes.

**Call relations**: This protocol is implemented by `CorePageFeed.pages_changed_since`. Extensions can depend on the protocol instead of knowing the database and blob-store details.


##### `page_cursor`  (lines 854–863)

```
def page_cursor(cursor: object) -> tuple[int, UUID]
```

**Purpose**: Parses a page feed cursor into its two ordering parts: revision number and page ID. This makes sure invalid cursors are rejected before they reach the database query.

**Data flow**: It receives an object that should be a string shaped like `revision|uuid`. It checks the type and separator, verifies the revision is decimal, parses the UUID, and returns the pair. Invalid input raises `ValueError`.

**Call relations**: `CorePageFeed.pages_changed_since` calls this when a caller provides a cursor. The parsed values become the starting point for the ordered page-change query.

*Call graph*: called by 1 (pages_changed_since); 1 external calls (UUID).


##### `CorePageFeed.pages_changed_since`  (lines 875–933)

```
async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch
```

**Purpose**: Reads changed pages from the database in a stable order and inlines each page body for the indexer. It is the core implementation of the page replay feed.

**Data flow**: It receives an optional cursor and a limit. It builds a query ordered by revision and page ID, caps the batch size, applies the cursor if present, and reads rows from the database. For each row, it loads the body from the blob store unless the page is tombstoned, computes the best `as_of` time from provider timestamps or ingestion time, builds `PageChange` objects, and returns them with a next cursor based on the last row.

**Call relations**: Indexing code calls this through the `PageFeed` interface to catch up after sync writes. It uses `page_cursor` to resume safely and the blob store to attach document bodies to non-deleted page changes.

*Call graph*: calls 1 internal fn (page_cursor); 7 external calls (__init__, __init__, fromisoformat, and_, or_, select, workspace_tx).


### `extensions/sources/ufo_ext_sources/pages.py`

`domain_logic` · `request handling`

A “page” here means one document that the system copied in from a registered external source, such as an issue tracker or another content provider. Users do not create or edit these pages by hand. They are produced by the sync driver, which is the background process that brings external content into the workspace.

This file turns those synced database rows into workspace objects that tools can browse. Listing a page shows safe metadata like its title, source, stream, and timestamps. Getting a page also reads its body from blob storage, which is a separate place used for larger chunks of data. To avoid returning too much data at once, the body is capped at 65,536 UTF-8 bytes and marked as truncated if there is more.

The file is careful about permissions and freshness. It only shows pages the current reader is allowed to see. After reading the body, it checks that the page record still points to the same subject, revision, digest, and blob reference; if the page changed during the read, it returns nothing rather than serving mismatched metadata and body content. This is like checking that a library book’s catalog card still matches the book you pulled from the shelf.

Create and update are deliberately rejected because synced pages belong to the source-sync pipeline. Delete means “forget this page,” and only admins may do it, so the rest of the indexing pipeline can clean up derived state.

#### Function details

##### `_require_ext`  (lines 62–65)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure the tool call has an extension context attached. The extension context is the object that gives access to source pages, source definitions, and page-forgetting operations.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it. If not, it raises an error because page objects cannot work without that connection to the extension’s data.

**Call relations**: Page listing, page reading, and page deletion all call this before touching extension-owned source data. It acts as a small safety gate so those flows fail clearly if they were dispatched without the required extension state.

*Call graph*: called by 3 (_pages, delete, get).


##### `_page_timestamp`  (lines 68–78)

```
def _page_timestamp(provider_value: str | None, row_value: datetime) -> str
```

**Purpose**: This function turns a page timestamp into a consistent UTC timestamp string. It accepts either a timestamp from the external provider or, if that is missing, the local row timestamp.

**Data flow**: It receives an optional provider timestamp string and a database row timestamp. If the provider value is present, it parses it and requires that it include a timezone. If it is missing, it uses the row timestamp and adds UTC if needed. It returns an ISO-formatted UTC timestamp with microsecond precision.

**Call relations**: _Page.spec and _Page.fields call this when preparing page details or list fields. That keeps displayed timestamps consistent no matter which source originally provided them.

*Call graph*: called by 2 (fields, spec); 2 external calls (fromisoformat, replace).


##### `_Page.name`  (lines 99–100)

```
def name(self) -> str
```

**Purpose**: This property gives the workspace object name for a page. The name is simply the page row’s UUID written as text.

**Data flow**: It reads the page’s internal UUID and converts it to a string. Nothing else changes.

**Call relations**: PageObjects.list uses this name when building list rows, and PageObjects._find compares this name with the requested object name when someone asks for a specific page.


##### `_Page.links`  (lines 102–110)

```
def links(self) -> tuple[ObjectLink, ...]
```

**Purpose**: This builds a link from a synced page back to the source object that produced it, when that source can be named. The link helps readers understand where the page came from.

**Data flow**: It reads the page’s stored source name. If there is no source name, it returns no links. If there is one, it creates a single link saying the page was “synced_by” that source.

**Call relations**: PageObjects.get includes these links in the returned object detail. The function packages the relationship so callers can navigate from a page to its source.

*Call graph*: 2 external calls (__init__, __init__).


##### `_Page.spec`  (lines 112–125)

```
def spec(self, body: str, body_truncated: bool) -> PageSpec
```

**Purpose**: This creates the full public description of a page, including its metadata and the bounded body text. It is the shape returned when someone reads a page object.

**Data flow**: It receives the already-read body text and a flag saying whether that body was truncated. It combines those with the page’s stored fields, converts timestamps through _page_timestamp, and returns a PageSpec object.

**Call relations**: PageObjects.get calls this after it has safely read the body and rechecked that the page did not change. The result becomes the main spec inside the ObjectDetail returned to the caller.

*Call graph*: calls 1 internal fn (_page_timestamp); 1 external calls (__init__).


##### `_Page.summary`  (lines 127–128)

```
def summary(self) -> str
```

**Purpose**: This creates a short human-readable summary for list views. It shows the title, source provider, stream, and visibility subject.

**Data flow**: It reads the page title, backend, stream, and subject, formats them into one line, and cuts the text to the configured maximum length. It does not change the page.

**Call relations**: PageObjects.list uses this when building each row shown in a page listing. It gives users a quick preview without fetching the full page body.


##### `_Page.fields`  (lines 130–138)

```
def fields(self) -> dict[str, JsonValue]
```

**Purpose**: This prepares the searchable and sortable metadata fields shown in page lists. These are the fields callers can filter or order by.

**Data flow**: It reads the page’s source ID, source backend, stream, title, and timestamps. It converts timestamps into consistent UTC strings and returns a dictionary of simple values.

**Call relations**: PageObjects.list puts these fields into each ObjectRow. The object kind definition later declares these same fields as list fields, so listing tools know what can be filtered or ordered.

*Call graph*: calls 1 internal fn (_page_timestamp).


##### `PageObjects.list`  (lines 149–154)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of synced pages visible to the current caller. It is used when someone wants to browse available synced content without reading every page body.

**Data flow**: It receives a tool context and a list query. It loads visible pages through _pages, turns each one into a row with a name, summary, and metadata fields, then passes those rows through object_page so the query’s paging, filtering, or ordering rules can be applied. It returns an ObjectPage.

**Call relations**: This is the list operation for the PAGE_OBJECT kind. It relies on _pages to collect permission-aware page records, then hands the rows to the SDK’s object_page helper to produce the standard list response.

*Call graph*: calls 1 internal fn (_pages); 2 external calls (__init__, object_page).


##### `PageObjects.get`  (lines 156–201)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[PageSpec] | None
```

**Purpose**: This returns the full detail for one synced page, including a bounded copy of the body text. It protects callers from stale reads and overly large bodies.

**Data flow**: It receives a tool context and page name. It finds the matching visible page, reads bytes from the blob reference up to one byte past the limit, trims to the maximum allowed size, and decodes UTF-8 safely. Then it asks the extension for the current readable page state and compares it with the page metadata it started with. If the page disappeared or changed during the read, it returns None. Otherwise it returns ObjectDetail with the PageSpec, creation and update times, and source links.

**Call relations**: This is the get operation for the PAGE_OBJECT kind. It calls _find to locate the page, uses the context’s blob reader to fetch the body, calls _require_ext to recheck current page state through the extension, and finally uses _Page.spec and _Page.links to build the returned object detail.

*Call graph*: calls 3 internal fn (source_reader, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects.status`  (lines 203–210)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports no separate status for page objects. Synced pages are read-only objects and do not have an apply operation whose progress needs tracking here.

**Data flow**: It receives the usual status inputs, including the context, name, and expected generation. It ignores them and returns None.

**Call relations**: This fills the standard object-kind interface. When the object system asks for page status, this method answers that there is no status information to provide.


##### `PageObjects.apply`  (lines 212–221)

```
async def apply(self, ctx: ToolContext, name: str, spec: PageSpec, old: PageSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This rejects attempts to create or update a page through the object interface. Pages must come from the content-sync driver, not from manual edits.

**Data flow**: It receives the desired page spec and optional old spec, along with context and generation information. Instead of writing anything, it raises VerbNotSupported with an explanation that pages are synced, not authored.

**Call relations**: This is the create/update hook required by the object interface. If a caller tries to apply a page object, this method stops the request before any page data can be changed.

*Call graph*: 1 external calls (__init__).


##### `PageObjects.delete`  (lines 223–235)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This forgets one synced page, but only if the caller is a workspace admin. Forgetting tombstones the page so the existing page-change pipeline can clean up related index data.

**Data flow**: It receives a context and page name. It first asks whether the speaker is an admin. If not, it raises AdminRequired. If the caller is an admin, it finds the page by name. If there is no such visible page, it raises an error. Otherwise it calls the extension’s forget_page operation with the page ID.

**Call relations**: This is the delete operation for the PAGE_OBJECT kind. It uses _find to resolve the requested name to a page row, _require_ext to reach the extension API, and then hands the page ID to forget_page so deletion goes through the normal synced-page cleanup path.

*Call graph*: calls 3 internal fn (speaker_is_admin, _find, _require_ext); 1 external calls (__init__).


##### `PageObjects._find`  (lines 237–238)

```
async def _find(self, ctx: ToolContext, name: str) -> _Page | None
```

**Purpose**: This looks up one visible page by its object name. It is a small helper used by read and delete operations.

**Data flow**: It receives a context and a name string. It loads all currently visible pages through _pages, searches for the first page whose UUID string matches the name, and returns that page or None.

**Call relations**: PageObjects.get calls this before reading a page body, and PageObjects.delete calls it before forgetting a page. It centralizes the name-matching rule so both operations use the same view of visible pages.

*Call graph*: calls 1 internal fn (_pages); called by 2 (delete, get).


##### `PageObjects._pages`  (lines 240–268)

```
async def _pages(self, ctx: ToolContext) -> tuple[_Page, ...]
```

**Purpose**: This gathers the set of live synced pages the current caller is allowed to read and wraps them in the local _Page helper type. It also attaches source provider names where possible.

**Data flow**: It receives a tool context, gets the extension context, loads source definitions, and builds lookup tables from source ID to backend name and source object name. Then it asks the extension for source pages using the current reader’s permissions. For each returned record, it creates a _Page containing page metadata, body reference, timestamps, source details, and visibility information. It returns all of them as a tuple.

**Call relations**: PageObjects.list uses this to build browse rows, and PageObjects._find uses it to search for one page. It calls the context’s source_reader so the extension only returns pages the caller is allowed to see, and it uses connector configuration plus binding_name to connect pages back to their source objects.

*Call graph*: calls 2 internal fn (source_reader, _require_ext); called by 2 (_find, list); 3 external calls (__init__, model_validate, binding_name).

## 📊 State Registers Touched

- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-source-page-sync-state` — The source records, page bodies, cursors, revisions, deletion markers, and replay feed used to keep external content synchronized.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-background-job-state` — The durable and in-memory background job registry, candidate queue, claims, retries, and worker progress for non-turn jobs such as sync, billing, evaluation, and cleanup.
- `reg-extension-kv-store` — The generic per-workspace extension JSON store used by add-ons to persist small feature-specific state outside core tables.
- `reg-search-provider-registry` — The live registry of web-search, page-fetch, embedding/search provider backends and their capabilities used by research, recall, and indexing code.
- `reg-source-connector-registry` — The registered source backend implementations, credential requirements, sync hooks, and capability metadata used to instantiate external content synchronization.
- `reg-page-alert-subscription-state` — The saved routing/subscription state that decides which conversations or agents should be alerted when synced source pages change.
- `reg-web-metadata-store` — Persistent web metadata/cache records captured by web/search/browser-related extensions for later lookup, indexing, or display.

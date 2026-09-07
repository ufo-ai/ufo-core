# Source extension account plumbing and sync metadata  `stage-14.2.4`

This stage is shared support for source extensions, the parts of the system that read outside services like Google or GitHub. It helps both when an account is first connected and later when background sync jobs keep data up to date. The connected-account piece creates private feed rows for an account’s main streams right after connection, and can try again later if that setup failed. The direct-auth piece supports a simpler path where a member stores their own API key; it turns that key into a bearer credential, meaning a token the sync worker can present to the outside service. The Google helper separates “you do not have permission” from “the service is temporarily out of quota,” so the system knows whether to skip one stream or retry the run. Resource recognition matches URLs and synced records to the same real-world item, such as a pull request. Triggers store notification rules for changed shared sources. Watermarks act like bookmarks, remembering the latest record already read.

## Files in this stage

### Account access plumbing
Creates source feed rows for connected accounts and prepares direct API-key credentials for sync jobs.

### `extensions/sources/ufo_ext_sources/connected.py`

`orchestration` · `connection hook and scheduled retry`

When a member connects an outside account, the system records that the member has granted access to that account. But the system also needs separate source rows, one per feed or stream, so the account's actual content can be synced. This file closes that gap: it turns a connection into the provider's canonical streams, meaning the core streams the connector says are central to that provider.

There are two ways this happens. The hook `on_connection_recorded` runs right after a connection is recorded, so the member does not have to take a second action. The job `retry_connected_sources` runs later and fills in any rows that were missed because a hook failed, a process died, or a grant appeared through another route.

The careful part is that it does not blindly recreate everything. It first looks at existing sources. If a source already exists, it leaves it alone. If the member previously removed that source, it respects the removal and does not recreate it. It also avoids providers that need a tenant-specific URL, because only the member can supply that information. Any new rows it creates are private to the member who owns the connection, and they are attached to the existing connection rather than creating a separate binding.

#### Function details

##### `on_connection_recorded`  (lines 51–58)

```
async def on_connection_recorded(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs when the system has just recorded a new account connection. It immediately asks `ConnectedSources` to create the default private feeds for that one connection before the connect flow finishes.

**Data flow**: It receives a hook context containing an event payload and access to the extension runtime. If the payload is a `ConnectionRecorded` event, it takes the connection ID from that payload and passes it to a new `ConnectedSources` helper. If the payload is not the expected kind, it raises an error because this hook was wired to the wrong event. It returns no special outcome after the registration attempt.

**Call relations**: This is the immediate path. The connection flow calls this hook after committing a connection, and this function creates `ConnectedSources` with the extension context so the helper can do the real source-registration work for that specific connection.

*Call graph*: 1 external calls (__init__).


##### `retry_connected_sources`  (lines 61–63)

```
async def retry_connected_sources(ctx: ExtensionContext) -> None
```

**Purpose**: This is the repair path for connected-account feeds. It scans all main-agent connections and creates any canonical source rows that should exist but do not.

**Data flow**: It receives the extension context, builds a `ConnectedSources` helper from it, and calls registration without naming a single connection. That means the helper checks all eligible connections instead of only one. The function itself returns nothing; its effect is any missing source rows created in the extension backend.

**Call relations**: This is used later, outside the original connect flow, such as by a scheduled job. It hands off to the same `ConnectedSources.register` logic used by the connection hook, which keeps the immediate path and retry path consistent.

*Call graph*: 1 external calls (__init__).


##### `ConnectedSources.register`  (lines 74–82)

```
async def register(self, connection_id: UUID | None=None) -> None
```

**Purpose**: This method decides which account connections should get automatic source rows. It filters to known providers with usable base URLs, optionally narrows to one connection, and then delegates the detailed per-stream work.

**Data flow**: It first reads the current live source records from the extension. Then it walks through the connections held by the main agent. If a specific connection ID was supplied, it ignores all others. For each remaining connection, it looks up the provider's connector class in the connector registry. Unknown providers and tenant-specific providers without a base URL are skipped. Eligible connections are passed to `_register` along with the connector instance and the already-read live source list.

**Call relations**: Both `on_connection_recorded` and `retry_connected_sources` use this method through `ConnectedSources`. It is the dispatcher: it gathers the shared facts once, checks which connections are candidates, and calls `ConnectedSources._register` to decide exactly which stream rows need to be created.

*Call graph*: calls 1 internal fn (_register); 2 external calls (main_agent_connections, get).


##### `ConnectedSources._register`  (lines 84–129)

```
async def _register(self, connection: MainAgentConnection, connector: Connector, live: tuple[SourceRecord, ...]) -> None
```

**Purpose**: This method creates missing private source rows for the canonical streams of one connected account. It also protects existing choices: it does not overwrite shared sources, does not recreate removed sources, and preserves the backfill request from an existing binding.

**Data flow**: It receives one connection, its connector, and the current live source records. It builds the member subject for the connection owner, finds existing source rows already bound to this connection, and stops if any of those rows belong to a different subject, because that means the binding is not purely private to this member. If there is an existing bound row, it reads its source configuration to reuse its requested backfill window. Then it walks the connector's streams, keeps only canonical streams, calculates how far back each first sync should reach, and builds a source configuration for each stream. It computes the stable source ID that row would have, skips IDs already live, asks the backend which of the remaining IDs were previously removed, and finally registers only the fresh, not-deleted sources.

**Call relations**: This is called by `ConnectedSources.register` after a connection has been judged eligible. It relies on the connector to describe its streams, on `effective_days` to combine requested and stream-specific backfill limits, on the extension backend to identify source IDs and removed rows, and on `register_source` to actually create the missing rows.

*Call graph*: calls 1 internal fn (streams); called by 1 (register); 6 external calls (__init__, model_validate, now, timedelta, member_subject, effective_days).


### `extensions/sources/ufo_ext_sources/direct.py`

`domain_logic` · `source sync authentication`

Some data sources need an API key that the system cannot get from an installed account broker, or that a deployment wants to keep under its own control. This file is the small adapter that makes that possible. Think of it like a locked key cabinet: the sync job knows which drawer to open, but the key is only taken out inside the trusted host process.

The `DirectAuthProxy` is given a `CredentialAccess` object, which is the controlled doorway into the workspace’s encrypted credential store. When a source sync asks for credentials for a provider, this proxy reads the secret stored under that provider’s name. It then wraps the secret as a `Credential` with a bearer token, which means “send this value as the authentication token when calling the provider’s HTTP API.”

The important safety rule is that the secret stays on the host side. It is used by the sync job to talk to the provider, but it is not passed into a sandbox or exposed to an agent-facing surface. The `account` value is present because the general auth-proxy interface includes it, but in this direct mode the real authority is the provider-named stored key.

#### Function details

##### `DirectAuthProxy.credential`  (lines 29–30)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Looks up the API key stored for a provider and returns it in the standard credential shape used by source sync code. Someone uses this when a source is routed through the direct, bring-your-own-key authentication path.

**Data flow**: It receives a workspace ID, a provider name, and an account handle. It uses the provider name to read the matching secret from `CredentialAccess`, waits for that read to finish, and then wraps the secret as a bearer credential. The result is a `Credential` object ready for the sync code to use when authenticating provider requests; the credential store is read but not changed.

**Call relations**: During a direct-auth source sync, the auth-proxy flow calls this method to get the provider credential. This method delegates the actual secret lookup to `CredentialAccess`, then hands the fetched value into `Credential.__init__` so the rest of the system receives credentials in the common format it expects.

*Call graph*: 1 external calls (__init__).


### Provider and resource recognition
Interprets provider-specific sync failures and identifies source items from URLs and synced provider data.

### `extensions/sources/ufo_ext_sources/providers/google.py`

`domain_logic` · `source sync error handling`

Google APIs can return the same broad HTTP refusal codes, especially 401 and 403, for very different problems. One problem is permanent for the current account grant: the user did not give the connector the needed permission, often called a scope. Retrying will not fix that. The right response is to skip that stream. The other problem is temporary: the connector has hit a usage limit or rate limit. Retrying later may work, so the run should surface the error and let the system’s backoff and retry behavior take over.

This file is a small decision helper for that fork in the road. It looks inside the error response body from Google, if one exists, and extracts the nested Google error details. It then checks whether those details name a quota problem, either through Google’s general RESOURCE_EXHAUSTED status or through specific quota-related reason strings. Finally, it combines that information with the HTTP status code to answer one question: was this refusal caused by missing permission rather than quota?

A useful analogy is a locked door: sometimes your key is wrong, and trying again will not help; sometimes the hallway is temporarily full, and waiting will. This file tells the connector which situation it is seeing.

#### Function details

##### `error_detail`  (lines 31–38)

```
def error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This function pulls out Google’s nested error information from an HTTP error response. It gives callers a safe empty dictionary when the response is not JSON or does not contain the expected Google error shape.

**Data flow**: It receives an httpx.HTTPStatusError, which includes the failed HTTP response. It tries to read the response body as JSON, then looks for the top-level error object and makes sure it is a dictionary. It returns that dictionary, or an empty one if the body cannot be read as JSON or does not have usable error details.

**Call relations**: When refused_for_scope needs to understand why Google rejected a request, it asks error_detail to extract the useful part of the response first. error_detail relies on dict_or_empty so that odd or missing response shapes do not crash the classification step.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (dict_or_empty).


##### `is_quota_refusal`  (lines 41–45)

```
def is_quota_refusal(detail: dict[str, Any]) -> bool
```

**Purpose**: This function decides whether Google’s error details point to a usage-limit problem instead of a permission problem. It recognizes both Google’s broad quota status and several specific quota reason names.

**Data flow**: It receives a dictionary of Google error details. It first checks whether the status field says RESOURCE_EXHAUSTED. If not, it looks through the nested errors list, safely treating missing or non-list data as an empty list, and checks whether any item has a known quota-related reason. It returns true for quota or rate-limit refusals, and false otherwise.

**Call relations**: After refused_for_scope has obtained the Google error details, it passes them to is_quota_refusal. This helper supplies the key distinction: if the refusal is really about quota, the caller should not treat it as a missing-permission skip.

*Call graph*: called by 1 (refused_for_scope); 1 external calls (list_or_empty).


##### `refused_for_scope`  (lines 48–53)

```
def refused_for_scope(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: This function answers the main question for the connector: did Google refuse the request because the current grant lacks permission? It returns true only for 401 or 403 responses that are not quota-related.

**Data flow**: It receives an httpx.HTTPStatusError from a failed Google API request. It checks the HTTP status code to see whether it is one of the refusal codes this file cares about. If it is, it extracts the Google error detail and asks whether that detail is a quota refusal. The result is true for settled permission refusals and false for quota problems or unrelated status codes.

**Call relations**: This is the public decision point in the file. Connector code can call it when a Google request fails: a true result tells the caller it can turn the failure into a skipped stream, while a false result lets quota and other errors continue upward so the normal retry and backoff path can handle them.

*Call graph*: calls 2 internal fn (error_detail, is_quota_refusal).


### `extensions/sources/ufo_ext_sources/resources.py`

`domain_logic` · `cross-cutting`

A trigger can be narrowed to one exact thing from a source, for example one GitHub pull request. For that to work, the system needs two abilities: first, turn a user-provided link into the one standard form it stores; second, later recognize provider records that mention or belong to that same thing.

This file is the small dispatcher for those rules. Each provider, such as GitHub, owns the details of its own URLs. This matters because providers store links in many shapes inside their JSON data. A pull request might appear as a web link, an API link, or inside another record such as a workflow run. Rather than teaching this file every provider’s data shape, it asks the provider module for two things: the canonical URL, meaning the single official stored form, and the aliases, meaning the other URL forms that may appear in synced page bodies.

If a provider has no rules registered here, the safe answer is “no match.” That prevents a narrowly targeted trigger from accidentally waking up for every page. The file also includes a helper that turns a resource URL into a short hash, useful when the raw URL cannot safely be used inside a path or object name.

#### Function details

##### `canonical_resource`  (lines 44–48)

```
def canonical_resource(provider: str, url: str) -> str | None
```

**Purpose**: This function turns a provider-specific link into the standard resource URL the system stores for narrowed triggers. If the provider is unknown, or the URL does not name a supported resource, it returns nothing.

**Data flow**: It receives a provider name and a URL. It looks up that provider’s resource rules in the local table, then passes the URL to the provider’s canonicalizing rule if one exists. The result is either a cleaned, standard resource URL or None, with no outside state changed.

**Call relations**: This is the front door for code that needs to store or compare a trigger’s target resource. It does not know GitHub URL rules itself; instead, it delegates to the provider rule registered in RESOURCE_RULES.


##### `resource_matches`  (lines 51–60)

```
def resource_matches(provider: str, resource: str, body: str) -> bool
```

**Purpose**: This function checks whether a synced page body is about a particular resource. It is used to decide whether provider JSON mentions the same item a narrowed trigger is watching.

**Data flow**: It receives a provider name, a canonical resource URL, and a page body as text. It finds the provider’s alias rules, asks for every URL form that might represent that resource, escapes each alias so special characters are treated as plain text, and searches the body without caring about letter case. It returns true as soon as one alias is found, otherwise false.

**Call relations**: This function sits in the matching step between stored trigger resources and synced page contents. It calls regular expression helpers, re.escape and re.search, so URLs are matched safely as text and so an alias stops at the end of the resource identifier, preventing a link like pull/5 from also matching pull/50.

*Call graph*: 2 external calls (escape, search).


##### `resource_digest`  (lines 63–66)

```
def resource_digest(resource: str) -> str
```

**Purpose**: This function turns a resource URL into a short, safe identifier for places where a raw URL would be awkward or invalid, such as a file path segment or object name.

**Data flow**: It receives a resource URL string, encodes it as bytes, hashes it with SHA-256, and keeps only the first eight hexadecimal characters. The output is a compact text digest; the original resource is not changed.

**Call relations**: This helper is used when other parts of the system need a stable short name for a resource but cannot use the URL directly. It relies on hashlib.sha256 from Python’s standard library to make the digest predictable for the same input.

*Call graph*: 1 external calls (sha256).


### Change notifications and checkpoints
Tracks source-change notification subscriptions and maintains sync watermarks for incremental reads.

### `extensions/sources/ufo_ext_sources/triggers.py`

`domain_logic` · `request handling and alert sweep`

A source trigger is like a standing subscription: “when this source changes, wake this conversation.” This file stores those subscriptions in two database tables. One table is for watching a whole source binding, such as an entire issue feed. The other is for watching one specific resource inside that source, such as one pull request or one issue. Keeping those separate matters because the older whole-source table only allows one row per conversation and binding, while a conversation may need to watch several individual resources under the same binding.

The main class, SourceTriggerStore, is the doorway into these tables. It always works inside the current workspace, so one workspace cannot accidentally read or delete another workspace’s triggers. It also checks that a trigger wakes a conversation owned by the currently running agent, so an agent cannot silently subscribe someone else’s conversation.

The store can create a trigger, delete one exact trigger, delete every trigger for a removed binding, report which narrowed resources a conversation already watches, find all triggers that should wake for a source update, and list triggers in a member-facing way. For member-facing lists, it also asks the wider system for conversation facts, such as who can see the conversation and what channel label should be shown. In short, this file is the subscription ledger for source alerts.

#### Function details

##### `_utc`  (lines 129–130)

```
def _utc(value: datetime) -> datetime
```

**Purpose**: This helper makes sure a date and time value has timezone information. If the database gives back a plain time with no timezone attached, it treats it as UTC, the common world time standard.

**Data flow**: It receives a datetime value. If that value already says what timezone it belongs to, it returns it unchanged. If it has no timezone, it returns a copy marked as UTC.

**Call relations**: _trigger calls this whenever it turns a database row into an in-memory SourceTrigger, so all trigger timestamps have a consistent shape before the rest of the code uses them.

*Call graph*: called by 1 (_trigger); 1 external calls (replace).


##### `_trigger`  (lines 133–152)

```
def _trigger(row: sa.RowMapping) -> SourceTrigger
```

**Purpose**: This helper turns one database row into a SourceTrigger object that the rest of the code can use. It also rejects unknown delivery modes, so bad stored data does not quietly spread further into the system.

**Data flow**: It receives a row read from either trigger table. It checks the delivery field, fills in an empty resource name when the row came from the whole-source table, normalizes the created and updated times, and returns a SourceTrigger value.

**Call relations**: The create, waking, and list_reported methods all call this after reading rows from the database. It is the shared translator between the storage format and the cleaner object that source alert code works with.

*Call graph*: calls 1 internal fn (_utc); called by 3 (create, list_reported, waking); 2 external calls (__init__, get).


##### `SourceTriggerStore.workspace_id`  (lines 162–163)

```
def workspace_id(self) -> UUID
```

**Purpose**: This property gives the store the workspace it is allowed to operate in. It is a small safety shortcut that keeps every database operation scoped to the current workspace.

**Data flow**: It reads the workspace_id from the ExtensionContext stored on the SourceTriggerStore and returns that UUID. It does not change anything.

**Call relations**: The store’s database methods use this value when creating, selecting, and deleting trigger rows. That makes the workspace boundary part of every operation rather than something callers must remember themselves.


##### `SourceTriggerStore.create`  (lines 165–228)

```
async def create(self, conversation_id: UUID, binding: str, delivery: SourceTriggerDelivery, created_by_member_id: UUID | None=None, resource: str='') -> SourceTrigger
```

**Purpose**: This creates a new source notification rule for a conversation. Use it when a conversation asks to watch either a whole source binding or one specific resource inside that binding.

**Data flow**: It receives a conversation ID, binding name, delivery mode, optional member ID, and optional resource name. It checks that the conversation belongs to the currently executing agent, builds a new row with a fresh ID and timestamps, inserts it into the right table, and returns the new SourceTrigger. If the same conversation already watches that same thing, it raises a clear ValueError instead of leaking a raw database conflict.

**Call relations**: Callers use this when setting up a watch. The method asks object_agent_id for the current agent, uses the extension context to check the conversation’s owner, writes through a transaction, and then hands the returned row to _trigger so the caller receives the normal SourceTrigger object.

*Call graph*: calls 1 internal fn (_trigger); 2 external calls (object_agent_id, uuid4).


##### `SourceTriggerStore.remove`  (lines 230–246)

```
async def remove(self, expected: SourceTrigger) -> None
```

**Purpose**: This removes one specific trigger, but only if it still matches the trigger the caller expected to remove. That protects against deleting the wrong subscription if something changed between reading and removing it.

**Data flow**: It receives the SourceTrigger the caller believes should be deleted. It checks that the trigger still belongs to the current agent, chooses the whole-source table or resource-watch table based on whether the trigger has a resource, and deletes the matching row in the current workspace. If no row was deleted, it raises a ValueError to say the trigger changed or disappeared.

**Call relations**: Code that disables a watch calls this with the trigger it previously read. The method consults object_agent_id for the current agent and then performs the database delete directly.

*Call graph*: 2 external calls (delete, object_agent_id).


##### `SourceTriggerStore.remove_binding`  (lines 248–259)

```
async def remove_binding(self, binding: str) -> None
```

**Purpose**: This deletes every trigger in the workspace for one source binding. It is used when a source binding itself is removed, so no conversation remains subscribed to a feed that no longer exists.

**Data flow**: It receives a binding name. Inside a transaction, it deletes matching rows from both the whole-source trigger table and the narrowed resource-watch table for the current workspace. It returns nothing.

**Call relations**: Source removal code calls this as cleanup. Unlike user-facing listing methods, it is intentionally workspace-wide because a source binding belongs to the workspace and may have been watched by conversations owned by different agents.

*Call graph*: 1 external calls (delete).


##### `SourceTriggerStore.watched`  (lines 261–276)

```
async def watched(self, conversation_id: UUID) -> frozenset[tuple[str, str]]
```

**Purpose**: This reports which specific resources a conversation already watches. It helps the user interface avoid offering a “watch this” action for a link the conversation is already watching.

**Data flow**: It receives a conversation ID. It reads resource-watch rows for that conversation in the current workspace, takes each row’s binding and resource, and returns them as an immutable set of pairs.

**Call relations**: Offer-building or conversation logic calls this before suggesting new watches. It reads only the narrowed resource-watch table, because it is answering which individual resources have already been subscribed to.

*Call graph*: 1 external calls (select).


##### `SourceTriggerStore.waking`  (lines 278–294)

```
async def waking(self, binding: str) -> tuple[SourceTrigger, ...]
```

**Purpose**: This finds every trigger that should be considered when a source binding has new activity. It is the alert sweep’s way to ask, “who wants to hear about this source?”

**Data flow**: It receives a binding name. It reads both whole-source triggers and narrowed resource watches for that binding in the current workspace, converts every row with _trigger, sorts the results by creation time and ID, and returns them as a tuple.

**Call relations**: The alert sweep calls this when a source reports changes. The method deliberately reads triggers across agents, because the source belongs to the workspace while each trigger row says which agent and conversation should be woken.

*Call graph*: calls 1 internal fn (_trigger); 1 external calls (select).


##### `SourceTriggerStore.list_reported`  (lines 296–336)

```
async def list_reported(self, *, conversation_id: UUID | None=None) -> tuple[ListedTrigger, ...]
```

**Purpose**: This builds the trigger list shown to members for the current agent, optionally narrowed to one conversation. It adds visibility information and display labels so the caller can decide what a member is allowed to see and how to present it.

**Data flow**: It optionally receives a conversation ID. It reads whole-source and resource-watch triggers for the current workspace and current agent, filters by conversation if requested, converts rows into SourceTrigger objects, and sorts them. Then it asks the extension context for facts about the owning conversations and returns ListedTrigger objects containing each trigger plus the conversation audience and surface label. Triggers whose conversations no longer exist are left out.

**Call relations**: Member-facing pages or commands call this when showing active source watches. It uses object_agent_id to stay within the current agent, _trigger to normalize database rows, and conversation_facts from the context to attach live conversation information such as renamed channel labels.

*Call graph*: calls 1 internal fn (_trigger); 3 external calls (__init__, select, object_agent_id).


### `extensions/sources/ufo_ext_sources/watermark.py`

`util` · `during source synchronization, after reading a batch of records`

Many data imports work like reading a long book with a bookmark. After each batch of records, the system needs to save the latest position it has seen. That saved position is often called a watermark or cursor: a value from a chosen field, such as an updated time, an ID, or a sequence number. Without this file, a source could more easily reread old data or miss where to resume.

This file provides two small checkpoint calculators. Both look at a stream description to find the cursor field, then scan the records in the current batch for values in that field. They also include the already-stored cursor, so the checkpoint never moves backward just because a batch contains older records. Only text and integer values are considered, and boolean values are deliberately ignored even though Python treats booleans as a kind of integer. That avoids accidentally treating true or false as cursor positions.

The difference between the two functions is how they compare values. One treats cursor values as plain text and chooses the greatest text value. The other treats them as whole numbers, even though it returns the result as decimal text. Together, they cover common cursor styles while keeping the rest of the source code from repeating this careful filtering and comparison logic.

#### Function details

##### `text_checkpoint`  (lines 6–19)

```
def text_checkpoint(stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This function picks the greatest cursor value when cursor values should be compared as text. It is useful for streams whose bookmark is something like an alphabetically sortable timestamp string or other text-based marker.

**Data flow**: It receives a stream description, a list of record dictionaries, and the previously saved cursor. If the stream has no cursor field, it simply returns the existing cursor. Otherwise, it gathers the old cursor plus any string or integer values found in the configured cursor field of the new records, ignores booleans, converts accepted values to text, and returns the largest text value. If there are no usable values, it returns nothing.

**Call relations**: This is a standalone helper for checkpoint calculation. When source-reading code has finished collecting records for a stream that uses text-style cursor comparison, it can call this function to produce the next saved cursor without duplicating the filtering and comparison rules.


##### `integer_checkpoint`  (lines 22–35)

```
def integer_checkpoint(stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This function picks the greatest cursor value when cursor values represent whole numbers. It returns the chosen number as text, which keeps the stored cursor format consistent with systems that save cursors as strings.

**Data flow**: It receives a stream description, a list of record dictionaries, and the previously saved cursor. If there is no cursor field configured, it returns the existing cursor unchanged. Otherwise, it collects the old cursor and any string or integer cursor-field values from the new records, skips booleans, converts the accepted values to text, compares them by their integer value, and returns the largest one. If no value is available, it returns nothing.

**Call relations**: This is a standalone helper for streams whose progress marker is numeric, such as an increasing ID. Source-reading code can call it after a batch is read to decide the next cursor, and the function keeps the numeric comparison detail in one place.

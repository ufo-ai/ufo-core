# CRM, support, marketing, and social connectors  `stage-14.6`

This stage is shared behind-the-scenes support for bringing outside customer data into the system. Each connector knows how to talk to one service’s API, meaning its web doorway for requesting data, and turns the answers into “source pages”: small batches of records the rest of the sync system can store, search, and recall.

The marketing connectors cover ActiveCampaign, Klaviyo, and Mailchimp. They pull things like contacts, audiences, campaigns, lists, events, reports, and email activity, while handling each service’s paging rules so large accounts are copied safely. Apollo, Attio, HubSpot, and Salesforce cover sales and CRM data such as companies, people, deals, tasks, notes, opportunities, conversations, analytics, and deleted-record notices. Facebook Ads and Instagram bring in social and advertising data, including campaigns, ads, posts, stories, accounts, and performance metrics. Freshdesk, Intercom, and Zendesk cover support and helpdesk work, such as tickets, conversations, users, organizations, help articles, tags, teams, and activity logs. Together, these files act like adapters for many plug shapes, making very different services feed one common sync pipeline.

## Files in this stage

### Marketing automation
Connectors that sync campaign, audience, profile, email, and event records from marketing automation platforms.

### `extensions/sources/ufo_ext_sources/providers/active_campaign.py`

`io_transport` · `data sync`

ActiveCampaign stores many kinds of records: contacts, lists, campaigns, deals, accounts, tags, webhooks, users, and more. This file is the read-only connector for those records. Without it, the wider system would not know which ActiveCampaign endpoints exist, how to authenticate, or how to collect large result sets one page at a time.

The file starts by naming every supported stream. A stream is one collection of records, like “contacts” or “campaigns.” For each stream, it records practical details such as the record’s main ID field, which date field can be used to notice updates, and whether the stream is considered a core, canonical dataset.

ActiveCampaign’s API uses a repeated pattern: each endpoint returns records inside a named envelope, and large lists are fetched with a limit and offset, like reading a book 100 lines at a time. The connector keeps a lookup table for endpoint names because some public stream names use underscores while ActiveCampaign expects camelCase names.

The connector also adapts authentication. ActiveCampaign expects an `Api-Token` header, not the more common bearer-token style. Finally, during pagination, if ActiveCampaign refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as a mysterious failure.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates one stream description for an ActiveCampaign record collection. It is a small helper that keeps the long stream list readable and consistent.

**Data flow**: It receives a stream name plus optional details such as the API object name, primary key, cursor date field, and whether the stream is canonical. It turns those pieces into a `StreamSpec`, which is the system’s standard description of a readable collection. The result is used later by the connector to know what can be synced and how to track updates.

**Call relations**: This helper is used while the file builds the ActiveCampaign stream catalog. Its main handoff is to `StreamSpec.__init__`, which creates the actual stream specification object that the connector exposes.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to ActiveCampaign, with the right kind of authentication attached. This matters because ActiveCampaign uses an `Api-Token` header instead of the usual bearer-token format.

**Data flow**: It receives a base URL and a credential. If the credential already has its own transport, it leaves that setup intact and delegates to the parent connector. Otherwise, it expects the credential’s bearer value to contain the raw ActiveCampaign API key, wraps that value into a new credential with an `Api-Token` header, and asks the parent connector to create the client. If no API key is present, it raises an error immediately.

**Call relations**: This function is part of the connector setup path, when the wider REST connector machinery needs a network client. Its notable handoff is creating a `Credential` with ActiveCampaign-specific headers before delegating client creation upward.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Translates the system’s stream name into the exact ActiveCampaign URL segment and response envelope key. This is needed because some names differ slightly, such as underscore names in this project versus camelCase names in ActiveCampaign.

**Data flow**: It receives a `StreamSpec`. It looks up the stream’s name in the mapping table. If a special mapping exists, it returns the ActiveCampaign path name and envelope key; if not, it uses the stream name for both. Nothing outside the function is changed.

**Call relations**: The pagination flow calls this before making API requests. It gives `ActiveCampaignConnector.paginate` the exact endpoint path and response key needed to fetch records from the correct ActiveCampaign collection.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records for one ActiveCampaign stream page by page. It also applies an update cursor when ActiveCampaign supports server-side filtering, so later syncs can ask mostly for changed records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It resolves the stream’s API path and envelope key, builds request parameters with a page size of 100, and adds an incremental `filters[..._after]` parameter when that stream supports it. It then yields each page of records as a list of dictionaries. If ActiveCampaign returns 401 or 403, it turns that refusal into a `StreamSkipped` error with a clear explanation; other HTTP errors are passed upward.

**Call relations**: This is the main read loop for ActiveCampaign data. It first calls `ActiveCampaignConnector._resolve_stream_segment` to find the right endpoint naming, then relies on the inherited offset-page reader to walk through results. If the API refuses access, it creates a `StreamSkipped` exception so the larger sync can understand that this particular stream could not be read because of permissions or an invalid key.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/providers/klaviyo.py`

`io_transport` · `source sync`

Klaviyo is a marketing platform, and its API returns many kinds of records in a nested JSON format. This file is the Klaviyo connector: it knows which Klaviyo objects to fetch, how to authenticate, how to ask for only new or changed records, how to follow Klaviyo's pagination links, and how to reshape each record into a simpler form.

The connector defines a catalog of streams, where each stream is one kind of Klaviyo data, such as profiles, campaigns, events, or images. For streams that can be synced incrementally, it tells the system which date field should be used as the "watermark" so future runs can resume from the last seen update instead of rereading everything.

When talking to Klaviyo, it adds the required API revision header and uses Klaviyo's private-key authorization format. For each first request, it builds the right query: page size, sort order, optional filter based on the saved cursor, and a few extra fields where useful. Later pages are reached through Klaviyo's `links.next` URL.

Klaviyo records are wrapped like envelopes: important values sit under `attributes`, with links to other records under `relationships`. The `flatten` method opens that envelope. It lifts useful fields to the top level, removes noisy counts that should not trigger changes, and exposes helpful details like a profile's email consent, an event's metric name, or a campaign's subject line. If Klaviyo refuses access to a stream, the connector marks that stream as skipped rather than failing the whole sync.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: Creates a stream description for one kind of Klaviyo data, such as profiles or campaigns. A stream description tells the broader sync system what to call the stream, where to fetch it from, what its main ID is, and which timestamp fields matter for incremental syncing.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, created time field, updated time field, and whether the stream is canonical. It fills in sensible defaults when details are missing, then returns a `StreamSpec`, which is a compact recipe the rest of the connector can follow.

**Call relations**: This helper is used while the file is loaded to build the Klaviyo stream list. It hands the completed stream recipe to `StreamSpec.__init__`, so later connector methods can use the same consistent shape for every Klaviyo resource.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Prepares the HTTP client that will talk to Klaviyo. It adds Klaviyo-specific headers so the API knows which version of the API to use and, when a direct private key is available, how to authorize the request.

**Data flow**: It receives a base URL and a resolved credential. It starts with the standard client made by the parent REST connector, adds the pinned Klaviyo revision header, and, if the credential contains a key, adds an `Authorization` header in Klaviyo's required `Klaviyo-API-Key ...` format. It returns the ready-to-use client.

**Call relations**: This method fits into the connector setup phase. The general REST connector provides the basic client, and this method customizes it for Klaviyo before any stream pagination or record fetching begins.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: Converts Klaviyo's next-page link into the path format this connector's HTTP client expects. Klaviyo gives a full web address, but the client is already bound to the Klaviyo base URL, so it only needs the path and query string.

**Data flow**: It receives a possible `links.next` value. If the value is empty or has no path, it returns `None`, meaning there is no next page to fetch. Otherwise it parses the URL, keeps the path, adds the query string if present, and returns that shorter path.

**Call relations**: During pagination, `KlaviyoConnector.paginate` calls this after each response. The returned path becomes the next request target; `None` tells the pagination loop to stop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: Chooses the correct timestamp field to use when sorting or filtering a Klaviyo stream. Different Klaviyo resources use slightly different names for their update time, so this keeps that choice in one place.

**Data flow**: It receives a stream description. If the stream is events, it returns `datetime`; if it is one of the resources that uses `updated_at`, it returns `updated_at`; otherwise it returns the default `updated` field.

**Call relations**: This helper supports the query-building step. `KlaviyoConnector._initial_query` relies on it so each stream asks Klaviyo for records in the right time order.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for the first page of a Klaviyo stream request. This is where the connector asks for a fixed page size, sets a stable sort order, and, when possible, filters out records older than the saved cursor.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It starts with the page size, then adds a Klaviyo filter like “greater than or equal to this timestamp” when both the stream and cursor support incremental syncing. It also adds stream-specific options, such as profile subscription details or event metric inclusion. It returns a dictionary of query parameters.

**Call relations**: At the start of `KlaviyoConnector.paginate`, this function creates the parameters for the first API request. After that first request, pagination follows Klaviyo's own `links.next` URLs, so these initial parameters are no longer reused.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: Safely pulls the ID of a related Klaviyo record out of a nested relationship block. It is defensive because Klaviyo may omit relationships or leave them empty.

**Data flow**: It receives a relationships object and the name of the relationship to read, such as `profile`, `metric`, or `list`. It checks each nested layer before touching it, looks for `relationships[name].data.id`, and returns that ID as text. If anything is missing or shaped unexpectedly, it returns `None` instead of crashing.

**Call relations**: `KlaviyoConnector.flatten` calls this when it wants to expose important linked-record IDs on the top-level flattened output. This keeps the flattening code simpler and avoids repeated careful checks for missing nested data.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns one Klaviyo API record from a nested JSON envelope into a flatter record that is easier for the rest of the system to store, compare, and search. It also extracts a few especially useful details that are buried inside attributes or relationships.

**Data flow**: It receives one raw Klaviyo record and the stream it came from. It starts a new flat record with the ID and resource type, copies top-level values from `attributes`, removes noisy list and segment profile counts, then adds stream-specific fields. For example, it can expose profile email consent, campaign subject and sender details, event profile and metric IDs, event message IDs, or a segment's parent list ID. It returns the flattened record and does not write it back to Klaviyo.

**Call relations**: This is the cleanup step after records have been fetched. When it needs IDs from nested relationships, it calls `KlaviyoConnector._lift_relationship_id`. Its output is what later sync machinery can treat as a straightforward record instead of a deeply nested Klaviyo response.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all available pages for one Klaviyo stream, yielding batches of raw records as it goes. It understands Klaviyo's pagination style, adds event metric names when Klaviyo includes them, and treats permission refusals as a skipped stream rather than a total sync failure.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It builds the first request path and query, fetches a page, pulls out the `data` records, optionally enriches event records with metric names from the response's `included` section, yields the records if any exist, then follows `links.next` to continue. If Klaviyo returns HTTP 401 or 403, it turns that refusal into a `StreamSkipped` signal; other HTTP errors continue upward.

**Call relations**: This is the main read loop for each Klaviyo stream. It calls `KlaviyoConnector._initial_query` to prepare the first request and `KlaviyoConnector._next_path` to move from page to page. When access is refused, it creates a `StreamSkipped` error so the surrounding sync can record that this stream was unavailable because the credential lacked the needed scope.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/providers/mailchimp.py`

`io_transport` · `during source sync, when Mailchimp streams are being read`

Mailchimp stores useful marketing data behind many web API paths, and much of it is nested. For example, members live inside lists, interests live inside interest categories inside lists, and unsubscribe records live inside reports. This file is the map and walking guide for that maze. Without it, the source system would not know which Mailchimp URLs to call, how to move through pages of results, or how to attach parent information like a list ID or campaign ID to child records.

The file defines the Mailchimp streams the system can sync, including their main identifier fields and cursor fields. A cursor is a saved “last seen time” used to ask Mailchimp for only newer or changed records when possible. The `MailchimpConnector` then decides how each stream should be paged: some streams are simple top-level lists, while others require first fetching all list IDs or report IDs and then fetching child collections under each one.

It also smooths over Mailchimp-specific quirks. It knows which JSON key contains records for each endpoint, adds missing parent IDs to nested records, creates a stable ID for individual email activity events, and treats 401 or 403 responses as a skipped stream when access is refused. The connector is read-only: it is designed only to pull data out of Mailchimp, not write anything back.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper builds a `StreamSpec`, which is the system’s small description card for one Mailchimp stream. It records facts like the stream name, the field that uniquely identifies records, and the field used for incremental syncing.

**Data flow**: It receives stream settings such as a name, optional Mailchimp object name, primary key, cursor field, and timestamp field names. It fills in sensible defaults where values are missing and returns a `StreamSpec` object that the connector later uses to know how to sync that stream.

**Call relations**: This helper is used while the file is loaded to create the `MAILCHIMP_STREAMS` list. It hands those stream descriptions to the connector class so the wider source framework can discover what Mailchimp data is available.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.record_identity`  (lines 148–155)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function decides the stable identity for a record, which is how the sync system tells whether it has seen the same record before. It has special logic for unsubscribe records because Mailchimp’s normal unsubscribe key is only unique inside a campaign.

**Data flow**: It receives one record and the stream description. For most streams, it lets the base connector use the normal primary key. For `unsubscribes`, it reads both `campaign_id` and `email_id`; if both exist, it combines them into one identity string, and if either is missing, it returns no identity.

**Call relations**: The broader sync framework calls this when it needs a record’s unique key. This function only takes over for unsubscribe records; all other streams are handed back to the base `RestConnector` behavior.


##### `MailchimpConnector.flatten`  (lines 157–163)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly normalizes Mailchimp records before the rest of the system sees them. For member streams, it creates a standard `created_at` value from Mailchimp’s signup or opt-in timestamps.

**Data flow**: It receives a record and its stream description. If the stream is `list_members` or `segment_members`, it copies the record and adds `created_at` using `timestamp_signup` first, or `timestamp_opt` if signup time is absent. Other records pass through unchanged.

**Call relations**: The sync framework calls this as part of record preparation. It does not fetch more data; it simply makes member records look more like the rest of the system expects.


##### `MailchimpConnector._data_field`  (lines 166–167)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This small helper answers the question: “Inside Mailchimp’s JSON response, which key contains the actual list of records?” Mailchimp uses different wrapper names, such as `lists`, `members`, or `emails`, depending on the endpoint.

**Data flow**: It receives a stream description. It looks up the stream name in the file’s mapping of Mailchimp response keys and returns the matching key, falling back to the stream name if no special key is listed.

**Call relations**: The pagination functions call this before fetching pages so they can ask the base REST paging helper to pull records from the right part of each Mailchimp response.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 170–177)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper turns the system’s saved cursor value into the query parameter name Mailchimp expects. That lets the connector ask Mailchimp for only records changed since a known time when the endpoint supports it.

**Data flow**: It receives a stream description and an optional cursor value. If there is no cursor or the stream has no cursor field, it returns an empty parameter set. If Mailchimp supports filtering on that field, it returns a one-item dictionary such as `since_last_changed: <cursor>`.

**Call relations**: The top-level, per-list, segment-member, and per-report pagination paths call this before requesting pages. It supplies extra URL parameters that narrow the Mailchimp response when possible.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 179–235)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Mailchimp stream. Given a stream name, it chooses the correct walking pattern: simple top-level pages, list-based child records, report-based child records, or deeper nested data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, delegates to the matching pagination helper, and yields pages of record dictionaries as they arrive. If Mailchimp refuses access with a 401 or 403 status, it turns that into a `StreamSkipped` signal with a clear explanation.

**Call relations**: The source sync framework calls this when it wants records for a Mailchimp stream. This function then hands off to helpers such as `_paginate_top_level`, `_paginate_per_list`, `_paginate_interests`, `_paginate_segment_members`, `_paginate_per_report`, or `_paginate_email_activity` depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 237–248)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Mailchimp streams that live at a single top-level API path, such as lists, campaigns, automations, and reports. These are the simplest streams because they do not require first finding a parent object.

**Data flow**: It receives an HTTP client, a stream description, an API path, and an optional cursor. It finds the right JSON record key, builds any cursor filter parameters, and asks the base REST helper to walk through offset-based pages using Mailchimp’s `count` page-size parameter. It yields each page of records.

**Call relations**: `paginate` calls this for streams listed as top-level paths. This helper relies on `_data_field` and `_cursor_params`, then delegates the actual repeated HTTP page fetching to the base connector’s offset paging helper.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 250–267)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the generic page walker for nested Mailchimp endpoints. It exists so list children, report children, interests, segment members, and email activity can all reuse the same page-by-page fetching pattern.

**Data flow**: It receives an HTTP client, a specific API path, the JSON key where records live, and optional base query parameters. It calls the base REST paging helper with Mailchimp’s page size and `count` parameter, then yields each returned page unchanged.

**Call relations**: The deeper pagination helpers call this whenever they have built a concrete child URL. It is the shared bridge between those higher-level walking routines and the base HTTP pagination machinery.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 269–277)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This helper fetches a collection and yields only the item IDs from it. It is used when later requests need parent IDs, such as list IDs or report IDs, before they can fetch child records.

**Data flow**: It receives an HTTP client, an API path, and the JSON key containing records. It pages through that endpoint, checks each returned row, and yields the row’s `id` as text when one is present.

**Call relations**: `_list_ids` and `_report_ids` call this to avoid duplicating the same ID-extraction logic. Those IDs then drive the nested list and report pagination flows.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 279–281)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function provides all Mailchimp audience list IDs. Many other Mailchimp records are stored underneath a list, so these IDs are the starting points for several nested syncs.

**Data flow**: It receives an HTTP client. It asks `_ids` to page through `/3.0/lists`, reads IDs from the `lists` response array, and yields each list ID as text.

**Call relations**: List-based walkers call this before fetching members, segments, tags, interest categories, interests, or segment members. It is the connector’s way of saying, “first find every audience, then look inside each one.”

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 283–285)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function provides all Mailchimp report IDs. Report IDs are needed before the connector can fetch report-specific child data such as unsubscribes and email activity.

**Data flow**: It receives an HTTP client. It asks `_ids` to page through `/3.0/reports`, reads IDs from the `reports` response array, and yields each report ID as text.

**Call relations**: Report-based walkers call this before fetching unsubscribe records or email activity. It supplies the parent campaign/report identifiers used to build those child URLs.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 287–308)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that sit directly under each Mailchimp list, such as members, segments, tags, or interest categories. It also adds the parent list ID to each child record when requested, so the record still shows where it came from.

**Data flow**: It receives an HTTP client, stream description, child path name, cursor, and optional parent field name. It gets all list IDs, builds a child URL for each list, fetches pages from that URL, and stamps each dictionary row with the list ID if needed. It yields the resulting pages.

**Call relations**: `paginate` calls this for direct per-list streams. It uses `_list_ids` to find parents, `_data_field` to find the response array, `_cursor_params` for incremental filters, and `_paginate_child` to fetch each child collection.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 310–332)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads interests, which are nested two levels deep: each list has interest categories, and each category has interests. It walks that hierarchy and labels each interest with both its list ID and category ID.

**Data flow**: It receives an HTTP client, stream description, and cursor value, though the cursor is not applied in this path. It fetches every list ID, fetches that list’s interest categories, then fetches interests under each category. For each interest row, it adds `list_id` and `category_id` when they are missing, then yields pages of interests.

**Call relations**: `paginate` calls this only for the `interests` stream. It depends on `_list_ids` for parent lists and `_paginate_child` for both category and interest page fetching.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 334–356)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads members who belong to Mailchimp segments. Because segment members are nested under both a list and a segment, it first finds lists, then segments, then members.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It builds cursor filter parameters, fetches every list ID, fetches each list’s segments, then fetches members for each segment. Each member row is stamped with `list_id` and `segment_id`, and pages are yielded as they are found.

**Call relations**: `paginate` calls this for the `segment_members` stream. It uses `_list_ids` to start the walk, `_cursor_params` to limit member results when possible, and `_paginate_child` for each paged Mailchimp request.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 358–378)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that live under each Mailchimp report, such as unsubscribe records. It adds the parent campaign or report ID to each row when requested, keeping child records tied back to their report.

**Data flow**: It receives an HTTP client, stream description, child path name, cursor, and optional parent field name. It gets all report IDs, builds the child URL for each report, fetches paged records, stamps each row with the parent ID if requested, and yields the pages.

**Call relations**: `paginate` calls this for report-based child streams such as `unsubscribes`. It uses `_report_ids` for parent reports, `_data_field` for the correct response key, `_cursor_params` for incremental filters, and `_paginate_child` for the repeated page requests.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 380–412)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads email activity from reports and turns Mailchimp’s nested activity lists into one record per action. That matters because the sync system needs individual, stable rows for events such as opens, clicks, or bounces.

**Data flow**: It receives an HTTP client and optional cursor. It optionally sends the cursor as Mailchimp’s `since` parameter, fetches every report ID, then fetches email activity pages for each report. For each recipient record, it copies the parent email fields, merges in each activity item, adds the campaign ID, creates a stable synthetic ID from email ID, action, and timestamp when needed, and yields only non-empty exploded pages.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to find reports and `_paginate_child` to fetch each report’s email activity, then performs the extra transformation that makes nested activity events usable as normal sync records.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### CRM and sales records
Connectors that ingest customer, company, deal, task, and sales pipeline data from CRM and sales systems.

### `extensions/sources/ufo_ext_sources/providers/apollo.py`

`io_transport` · `source sync polling`

This file is the Apollo “connector,” meaning the part of the system that knows how to talk to Apollo’s API. Apollo is a customer-relationship tool, and this connector reads two kinds of CRM data from it: contacts and accounts. Without this file, the wider system would not know which Apollo endpoints to call, how to authenticate, or how to avoid re-reading the whole Apollo database every time.

Apollo does not offer a direct “give me everything changed since this time” filter for these searches. So this connector uses a practical workaround. It asks Apollo for records newest-first, one page at a time, like reading the newest pages of a logbook first. It compares each record’s creation time with a saved cursor, also called a watermark, which means “the newest thing we had already synced last time.” As soon as a page contains an old record, the connector stops, because later pages will be older too.

The file also defines the two supported streams, contacts and accounts, including their main ID field and timestamp fields. It treats Apollo authorization refusals specially: if Apollo says 401 or 403, the stream is skipped with a clear message, because some Apollo keys cannot access all search endpoints.

#### Function details

##### `ApolloConnector._make_client`  (lines 61–69)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to call Apollo, with the authentication format Apollo expects. Apollo does not want the usual bearer-token Authorization header here; it wants the API key in an X-Api-Key header.

**Data flow**: It receives a base URL and a resolved credential. It first asks the shared REST connector to build a normal HTTP client. If the credential contains a bearer-style key, it removes the normal Authorization header and places that key into Apollo’s X-Api-Key header instead. It returns the adjusted client, ready to make Apollo requests.

**Call relations**: This is part of the setup before any Apollo pages are fetched. The broader REST connector machinery calls it when preparing a client, and the returned client is later used by the pagination flow to send Apollo search requests.


##### `ApolloConnector.paginate`  (lines 71–104)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads one Apollo stream, such as contacts or accounts, page by page. It yields only records that are newer than the saved cursor, so repeat syncs can be much smaller than a full read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It looks up which Apollo search endpoint belongs to that stream, then repeatedly sends POST requests with the page number, page size, and newest-first sorting. For each response, it pulls out the record list, filters it through ApolloConnector._above, and yields the newer records. It stops when Apollo returns no records, when it reaches a record at or below the cursor, or when Apollo says there are no more pages. If Apollo refuses access with 401 or 403, it turns that into a StreamSkipped error with a helpful explanation.

**Call relations**: This is the main reading loop for Apollo data. During each page, it uses list_or_empty to safely treat the response records as a list, calls ApolloConnector._above to keep only new records, and uses get_path to read Apollo’s nested pagination.total_pages value. If Apollo refuses the stream, it raises StreamSkipped so the larger sync can skip this stream instead of crashing as an unexplained authentication failure.

*Call graph*: calls 2 internal fn (__init__, _above); 2 external calls (get_path, list_or_empty).


##### `ApolloConnector._above`  (lines 107–116)

```
def _above(records: list[dict[str, Any]], cursor: str | None) -> list[dict[str, Any]]
```

**Purpose**: This helper keeps only records created after the saved cursor timestamp. It is the small rule that makes Apollo’s newest-first paging work like an incremental sync.

**Data flow**: It receives a list of Apollo records and either a cursor string or no cursor. If there is no cursor, it returns all records, which is what a first full sync needs. If there is a cursor, it checks each record’s created_at value and returns only records whose created_at string is later than the cursor.

**Call relations**: ApolloConnector.paginate calls this after every page of Apollo results. Its output decides both what gets yielded to the rest of the sync and whether pagination should stop: if some records on a page are not above the cursor, paginate knows it has reached already-seen data.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/attio.py`

`io_transport` · `source sync`

Attio’s API does not return every kind of data in the same shape. Company, person, and deal records come from object-specific query endpoints. Tasks and notes use their own workspace-wide endpoints. Meetings and call recordings use cursor-based pages, and call recordings need an extra request to fetch transcripts. This file hides those differences behind one connector, so the rest of the system can ask for “the Attio streams” without knowing Attio’s quirks.

The most important work here is flattening. Attio wraps record IDs inside an id object and stores attributes inside nested value cells. That is useful for Attio, but awkward for search and syncing. The connector lifts important IDs like record_id, task_id, meeting_id, and call_recording_id to the top level, and turns nested values into plain text, numbers, dates, lists, or simple references. It is like unpacking a set of labeled boxes before putting items on a shelf.

The file also knows how to page through Attio responses until there is no more data. If Attio says a standard object is disabled, or the OAuth permission grant is missing a needed scope, the connector skips that stream instead of failing the whole sync. There is no write path here; this connector only reads Attio data.

#### Function details

##### `_records_stream`  (lines 35–43)

```
def _records_stream(name: str, *, object_slug: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard stream description for Attio object records such as companies, people, and deals. A stream description tells the sync system what the stream is called, what Attio object it reads, and which field is the stable primary key.

**Data flow**: It receives a stream name, an Attio object slug, and whether the stream should be treated as canonical. It builds a StreamSpec with record_id as the primary key, no incremental cursor, and delete_missing enabled. The result is a reusable stream definition used when declaring Attio streams.

**Call relations**: This helper is used while the module is being loaded to build the ATTIO_STREAMS list. It hands its settings to StreamSpec so the broader source framework can later know how to sync each Attio object stream.

*Call graph*: 1 external calls (__init__).


##### `_nested_id`  (lines 70–71)

```
def _nested_id(value: Any, key: str) -> Any
```

**Purpose**: Safely pulls a named ID out of a nested dictionary. It avoids errors when Attio returns something that is not shaped like a dictionary.

**Data flow**: It receives any value and a key name. If the value is a dictionary, it returns value[key] when present; otherwise it returns None. Nothing else is changed.

**Call relations**: AttioConnector._value_primitive calls this when it needs a fallback ID from nested Attio objects such as select options or statuses.

*Call graph*: called by 1 (_value_primitive).


##### `AttioConnector._build_query_body`  (lines 80–81)

```
def _build_query_body(offset: int) -> dict[str, Any]
```

**Purpose**: Builds the request body used when querying Attio object records with offset paging. This keeps the page size and offset format consistent.

**Data flow**: It receives an offset number. It returns a dictionary containing the fixed page limit and that offset. The returned dictionary is sent as JSON to Attio’s records query endpoint.

**Call relations**: AttioConnector.paginate calls this each time it asks Attio for the next page of companies, people, deals, or other object records.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._value_primitive`  (lines 84–127)

```
def _value_primitive(item: dict[str, Any]) -> Any
```

**Purpose**: Turns one Attio value cell into the simplest useful value, such as text, an email address, a phone number, a selected option title, a currency number, or a reference ID. This is the core translator from Attio’s nested attribute format into ordinary data.

**Data flow**: It receives one dictionary from an Attio value cell. It checks which Attio-specific field is present, chooses the most natural plain value, and returns that value. For nested option and status IDs it uses _nested_id as a safe fallback; for record references it prefixes the ID so the reference remains recognizable.

**Call relations**: This function sits underneath the flattening helpers. When records are normalized, the connector repeatedly calls it to reduce Attio’s typed cells into values the rest of the system can index and compare.

*Call graph*: calls 1 internal fn (_nested_id).


##### `AttioConnector._flatten_cell`  (lines 130–145)

```
def _flatten_cell(cls, cell: Any) -> Any
```

**Purpose**: Flattens one Attio attribute cell into one useful value. It handles both single cells and list-shaped cells.

**Data flow**: It receives a cell that may be a list, dictionary, or already-simple value. For lists, it converts each item to a primitive and drops empty results; for multi-select option lists, it keeps the list, while most other lists become their first useful value. It returns the flattened value or None.

**Call relations**: This helper is part of the record-flattening path used before synced records are stored. It relies on AttioConnector._value_primitive to understand individual Attio value objects.


##### `AttioConnector._flatten_list_cell`  (lines 148–155)

```
def _flatten_list_cell(cls, cell: Any) -> list[Any]
```

**Purpose**: Flattens an Attio attribute that should remain a list, such as domains, categories, email addresses, or phone numbers. It preserves multiple useful values instead of choosing only the first one.

**Data flow**: It receives a cell. If the cell is not a list, it flattens it and wraps the result in a one-item list, or returns an empty list if there is no value. If the cell is a list, it converts each item to a primitive and removes empty values. The output is always a list.

**Call relations**: This helper is used by the value-flattening flow for attributes where multiple entries matter. It shares the same primitive conversion rules as the rest of Attio’s flattening logic.


##### `AttioConnector._flatten_values`  (lines 158–192)

```
def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Walks through all Attio attributes on a record and turns them into ordinary top-level fields. It also creates convenient shortcut fields like email, phone, domain, and first_name.

**Data flow**: It receives Attio’s values dictionary, where each attribute slug points to a nested cell. It flattens each cell, keeps list-style attributes as lists, extracts name parts when available, and copies the first domain, category, email, and phone into simple singular fields. It returns a new plain dictionary of attributes.

**Call relations**: This is the main attribute-cleaning step used when object records are flattened. It feeds clean fields into AttioConnector._flatten_record, which then combines them with record identity and timestamps.


##### `AttioConnector._flatten_record`  (lines 195–209)

```
def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a standard Attio object record, such as a company or person, into a flat record with a clear primary key and readable attributes. Without this, the sync system would not have a top-level record_id to identify the row.

**Data flow**: It receives the raw Attio record and the stream definition. It pulls record_id, object_id, workspace_id, created_at, and updated_at from the raw record, optionally copies a cursor field if the stream has one, then adds the flattened attributes from the values section. It returns one normalized dictionary.

**Call relations**: AttioConnector.flatten calls this for all streams that are not tasks, notes, meetings, or call recordings. It is the standard path for Attio object streams.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_task`  (lines 212–216)

```
def _flatten_task(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a raw Attio task into a task record with task_id available at the top level. This gives the sync system a stable key for task rows.

**Data flow**: It receives a raw task dictionary. It copies the whole dictionary, extracts task_id from the nested id object or uses the id directly if it is already simple, and returns the updated copy.

**Call relations**: AttioConnector.flatten calls this when the current stream is tasks. It is the task-specific version of the ID-lifting pattern used throughout this connector.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_note`  (lines 219–223)

```
def _flatten_note(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a raw Attio note into a note record with note_id available at the top level. This makes notes easy for the sync system to identify and replace.

**Data flow**: It receives a raw note dictionary. It copies the original data, extracts note_id from the nested id object or direct id value, and returns the updated note.

**Call relations**: AttioConnector.flatten calls this when the current stream is notes. It keeps the note-specific conversion small and separate from other stream types.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_meeting`  (lines 226–230)

```
def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a raw Attio meeting into a meeting record with meeting_id available at the top level. This provides the stable identifier needed for full-snapshot syncing.

**Data flow**: It receives a raw meeting dictionary. It copies the original fields, extracts meeting_id from the nested id object or direct id value, and returns the updated meeting.

**Call relations**: AttioConnector.flatten calls this when the current stream is meetings. It prepares meeting rows after pagination has retrieved them from Attio.

*Call graph*: called by 1 (flatten).


##### `AttioConnector._flatten_call_recording`  (lines 233–254)

```
def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a raw Attio call recording into a searchable record with call_recording_id, a usable recording URL, and combined transcript text. This makes recordings and their speech content recallable.

**Data flow**: It receives a raw recording dictionary. It copies the fields, lifts call_recording_id to the top level, uses web_url as recording_url if no recording_url is present, and joins transcript segments into one transcript_text string with speaker names when available. It returns the enriched recording.

**Call relations**: AttioConnector.flatten calls this for the call_recordings stream. It finishes the work started by AttioConnector._paginate_call_recordings, which may have already attached transcript segments to each recording.

*Call graph*: called by 1 (flatten).


##### `AttioConnector.flatten`  (lines 256–265)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening method for the current Attio stream. It is the public normalization entry point used after raw records have been fetched.

**Data flow**: It receives one raw record and the stream it came from. It checks the stream name and sends the record to the task, note, meeting, call recording, or standard object flattener. It returns one clean dictionary ready for the rest of the sync pipeline.

**Call relations**: The source framework calls this after pagination yields raw Attio records. This function dispatches to AttioConnector._flatten_task, _flatten_note, _flatten_meeting, _flatten_call_recording, or _flatten_record so each stream gets the shape it needs.

*Call graph*: calls 5 internal fn (_flatten_call_recording, _flatten_meeting, _flatten_note, _flatten_record, _flatten_task).


##### `AttioConnector.paginate`  (lines 267–321)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages of data for one Attio stream, using the paging style that stream requires. It is the main read path from Attio’s API.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor value. It chooses the correct endpoint and paging method, requests pages from Attio, and yields lists of raw records. If Attio reports a disabled standard object or missing OAuth permission, it raises StreamSkipped so the sync can continue with other streams.

**Call relations**: The sync framework calls this to read each Attio stream. It hands tasks and notes to AttioConnector._paginate_simple, meetings to _paginate_cursor, call recordings to _paginate_call_recordings, and standard objects to the records-query loop using _build_query_body. It uses the error-checking helpers to decide when a stream should be skipped.

*Call graph*: calls 8 internal fn (__init__, _build_query_body, _is_object_disabled, _is_scope_unauthorized, _paginate_call_recordings, _paginate_cursor, _paginate_simple, _scope_skip_reason).


##### `AttioConnector._paginate_simple`  (lines 323–330)

```
async def _paginate_simple(self, client: httpx.AsyncClient, path: str, *, page_size: int) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple offset-paged Attio endpoints, currently tasks and notes. Offset paging means asking for records starting at a numbered position, like turning pages in a notebook by page number.

**Data flow**: It receives an HTTP client, endpoint path, and page size. It asks the base REST connector to fetch offset-based pages from the data field. It yields each page of records as it arrives.

**Call relations**: AttioConnector.paginate calls this for /v2/tasks and /v2/notes. It delegates the low-level repeated GET requests to the shared REST paging helper.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._paginate_cursor`  (lines 332–350)

```
async def _paginate_cursor(self, client: httpx.AsyncClient, path: str, *, page_size: int, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads cursor-paged Attio endpoints, currently meetings and per-meeting call recordings. A cursor is a token from the server that says where to continue next.

**Data flow**: It receives an HTTP client, endpoint path, page size, and optional query parameters. It asks the base REST connector to read records from data and follow pagination.next_cursor until there is no next cursor. It yields each page of records.

**Call relations**: AttioConnector.paginate calls this for meetings. AttioConnector._paginate_call_recordings also calls it first to walk meetings and then to walk each meeting’s recordings.

*Call graph*: called by 2 (_paginate_call_recordings, paginate).


##### `AttioConnector._paginate_call_recordings`  (lines 352–388)

```
async def _paginate_call_recordings(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the call recording stream by first finding meetings, then finding recordings for each meeting, then fetching transcripts for each recording when available. This is needed because Attio exposes recordings underneath meetings rather than as one simple global list.

**Data flow**: It receives an HTTP client. It pages through meetings, extracts each meeting ID, title, start time, end time, and duration, then pages through that meeting’s recordings. For each recording it adds parent meeting context and, when possible, attaches transcript data. It yields pages of enriched recording records.

**Call relations**: AttioConnector.paginate calls this when syncing call_recordings. Inside, it uses AttioConnector._paginate_cursor for both meetings and recordings, _meeting_id and _call_recording_id to find IDs, _datetime_of and _duration_seconds to derive timing fields, and _fetch_transcript to attach speech content.

*Call graph*: calls 6 internal fn (_call_recording_id, _datetime_of, _duration_seconds, _fetch_transcript, _meeting_id, _paginate_cursor); called by 1 (paginate).


##### `AttioConnector._fetch_transcript`  (lines 390–402)

```
async def _fetch_transcript(self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str) -> dict[str, Any] | None
```

**Purpose**: Fetches the transcript for one meeting recording. If Attio says the transcript is not found or not ready yet, it quietly returns no transcript instead of failing the stream.

**Data flow**: It receives an HTTP client, a meeting ID, and a recording ID. It calls the transcript endpoint and reads the data object from the response. It returns that transcript dictionary, or None when the transcript is unavailable because Attio returned 404 or 409.

**Call relations**: AttioConnector._paginate_call_recordings calls this after it finds a recording ID. The returned transcript is attached to the recording before the recording page is yielded.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._meeting_id`  (lines 405–409)

```
def _meeting_id(meeting: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the meeting ID from an Attio meeting in either nested or simple form. This keeps the recording fan-out code from caring which shape Attio returned.

**Data flow**: It receives a meeting dictionary. If meeting['id'] is a dictionary, it returns id.meeting_id; if it is already a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this while walking meeting pages. If no meeting ID is found, that meeting is skipped because recordings cannot be requested without it.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._call_recording_id`  (lines 412–416)

```
def _call_recording_id(rec: dict[str, Any]) -> str | None
```

**Purpose**: Extracts the call recording ID from an Attio recording in either nested or simple form. The ID is needed to request that recording’s transcript.

**Data flow**: It receives a recording dictionary. If recording['id'] is a dictionary, it returns id.call_recording_id; if it is already a string, it returns that string. Otherwise it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for each recording. When it finds an ID, the connector can call AttioConnector._fetch_transcript for that recording.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._datetime_of`  (lines 419–423)

```
def _datetime_of(timeshape: Any) -> str | None
```

**Purpose**: Extracts a usable date or date-time string from Attio’s meeting time shape. Attio may represent timed meetings and all-day meetings differently.

**Data flow**: It receives a time-shaped value. If it is a dictionary, it returns the datetime field when present, otherwise the date field; if the input is not a dictionary, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this for meeting start and end values. Those extracted strings are copied onto recordings and used to estimate duration.

*Call graph*: called by 1 (_paginate_call_recordings).


##### `AttioConnector._duration_seconds`  (lines 426–436)

```
def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None
```

**Purpose**: Computes an approximate meeting duration in seconds from start and end time strings. If the times are missing or cannot be parsed, it safely gives up.

**Data flow**: It receives optional start and end strings. It parses them as ISO 8601 date-time values, treating a trailing Z as UTC, subtracts start from end, and returns the non-negative number of seconds. If parsing fails or either input is missing, it returns None.

**Call relations**: AttioConnector._paginate_call_recordings calls this after extracting meeting start and end times. When a duration is available, it is added to recordings that do not already have one.

*Call graph*: called by 1 (_paginate_call_recordings); 1 external calls (fromisoformat).


##### `AttioConnector._is_object_disabled`  (lines 439–448)

```
def _is_object_disabled(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the specific Attio error that means a standard object, such as deals, is disabled in the workspace. This lets the connector skip only that stream instead of treating it as a fatal problem.

**Data flow**: It receives an HTTP error. It checks for status code 400, tries to read the JSON body, and returns true only when the body’s code is standard_object_disabled. Otherwise it returns false.

**Call relations**: AttioConnector.paginate calls this when an object records-query request fails. If it returns true, paginate raises StreamSkipped with a clear explanation.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._is_scope_unauthorized`  (lines 451–460)

```
def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool
```

**Purpose**: Recognizes the Attio error that means the OAuth grant is missing a required permission scope. OAuth is the permission system that lets this app read Attio data without knowing the user’s password.

**Data flow**: It receives an HTTP error. It checks for status code 403, tries to read the JSON body, and returns true only when the body’s code is unauthorized. Otherwise it returns false.

**Call relations**: AttioConnector.paginate calls this around meeting and call recording reads. If the permission is missing, paginate raises StreamSkipped instead of failing the entire sync.

*Call graph*: called by 1 (paginate).


##### `AttioConnector._scope_skip_reason`  (lines 463–469)

```
def _scope_skip_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for skipping a stream because an OAuth permission is missing. It tries to include Attio’s own message when available.

**Data flow**: It receives an HTTP error. It tries to parse the response body as JSON, reads the message field if present, and returns a sentence explaining that the OAuth grant lacks a required scope. If no message is available, it uses a generic fallback.

**Call relations**: AttioConnector.paginate calls this after AttioConnector._is_scope_unauthorized confirms the missing-scope case. The returned text becomes the reason attached to StreamSkipped.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/hubspot.py`

`io_transport` · `source sync / request handling`

HubSpot is not one single tidy database. Different parts of HubSpot use different web API endpoints, different paging styles, and different field layouts. This file is the adapter that hides that mess. Without it, the rest of the system would need to know every HubSpot URL, every cursor rule, and every exception for products, custom objects, archived records, and permission-limited accounts.

The file first declares many stream definitions. A stream is one category of records to sync, such as contacts, tickets, forms, email events, or list memberships. These definitions say things like the record’s unique id field and which timestamp can be used as a sync cursor.

The `HubSpotConnector` then does the work. For normal CRM objects, it asks HubSpot which properties exist, searches records in cursor order, skips duplicate boundary records caused by HubSpot’s inclusive filtering, and finally checks archived records so deletions become tombstones. For product APIs that do not support the same search endpoint, it routes each stream to a matching paging method. It also flattens nested HubSpot records into simple top-level dictionaries, like unpacking labeled folders into one sheet of paper.

An important behavior is that some HubSpot accounts cannot access every object. When HubSpot says a stream is unavailable because of permissions or product tier, this connector marks that stream as skipped rather than failing the whole sync.

#### Function details

##### `_normalize_epoch_millis`  (lines 246–253)

```
def _normalize_epoch_millis(value: Any) -> Any
```

**Purpose**: Converts HubSpot timestamps written as milliseconds since 1970 into a standard readable date-time string. It leaves booleans and already-normal values alone so ordinary fields are not accidentally changed.

**Data flow**: It receives any value. If the value looks like a number of milliseconds, it converts that number into an ISO date-time in UTC; otherwise it returns the original value unchanged.

**Call relations**: Product API flattening and analytics view shaping call this when HubSpot returns older numeric timestamp formats. It gives later sync code a consistent date format.

*Call graph*: called by 2 (_analytics_view_rows, _flatten_product_api); 1 external calls (fromtimestamp).


##### `_stream`  (lines 256–265)

```
def _stream(name: str, *, object_type: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: Creates the standard stream description for HubSpot CRM object records. It is used for objects that follow HubSpot’s normal CRM search pattern.

**Data flow**: It receives a public stream name, a HubSpot object type, and whether the stream is canonical. It returns a `StreamSpec`, which is the project’s small recipe for how to identify and incrementally sync that stream.

**Call relations**: This helper is used at module load time to define many CRM streams such as companies, contacts, deals, tasks, tickets, and commerce objects.

*Call graph*: 1 external calls (__init__).


##### `_product_api_stream`  (lines 268–287)

```
def _product_api_stream(name: str, *, source_object: str, primary_key: str='id', cursor_field: str | None=None, created_at_field: str | None='createdAt', updated_at_field: str | None='updatedAt', pagi
```

**Purpose**: Creates a stream description for HubSpot product APIs that do not behave like normal CRM object search. These include owners, forms, campaigns, files, events, and similar surfaces.

**Data flow**: It receives stream metadata such as the source object name, primary key, cursor field, date fields, and optional pagination recipe. It returns a non-canonical `StreamSpec` for that product API stream.

**Call relations**: This helper is used while defining the file’s stream catalog. Later, `HubSpotConnector._paginate_product_api` uses these stream names to choose the correct API walking method.

*Call graph*: 1 external calls (__init__).


##### `_hubspot_get_pagination`  (lines 290–302)

```
def _hubspot_get_pagination(path: str) -> Pagination
```

**Purpose**: Builds the common pagination recipe for HubSpot GET list endpoints. Pagination means fetching many records page by page instead of all at once.

**Data flow**: It receives an API path. It returns a `Pagination` object that tells the base connector where records live in the response, where the next cursor is, and which query parameters control page size and position.

**Call relations**: It is used when defining flat product API streams whose endpoints all share HubSpot’s usual `results` plus `paging.next.after` format.

*Call graph*: 1 external calls (__init__).


##### `_junction`  (lines 305–315)

```
def _junction(name: str, *, parent_object: str) -> StreamSpec
```

**Purpose**: Creates a stream description for relationship tables, such as deal-to-contact or ticket-to-company links. These streams are synthetic because HubSpot returns the links inside parent records rather than as a separate object table.

**Data flow**: It receives a stream name and parent object type. It returns a `StreamSpec` with no cursor because HubSpot does not expose modification timestamps for these associations.

**Call relations**: The junction stream definitions created here are later recognized by `HubSpotConnector._paginate_unchecked`, which sends them to `HubSpotConnector._paginate_junction`.

*Call graph*: 1 external calls (__init__).


##### `HubSpotConnector._build_search_body`  (lines 620–650)

```
def _build_search_body(stream: StreamSpec, properties: list[str], cursor: str | None, after: str | None) -> dict[str, Any]
```

**Purpose**: Builds the JSON request body for HubSpot’s CRM search endpoint. It asks for all known properties, sorts by the cursor field, and optionally filters to records changed since the last sync.

**Data flow**: It receives the stream definition, property names, the stored cursor, and HubSpot’s page cursor. It produces a dictionary that can be sent as the body of a POST search request.

**Call relations**: Normal CRM pagination and custom object pagination call this before sending search requests. It is the shared request builder for incremental CRM-style reads.

*Call graph*: called by 2 (_paginate_custom_object_records, _paginate_unchecked).


##### `HubSpotConnector._flatten`  (lines 653–664)

```
def _flatten(record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Turns a normal HubSpot CRM record into a simpler flat record. HubSpot wraps most useful fields inside `properties`; this function lifts them to the top level.

**Data flow**: It receives one CRM record. It copies id, creation time, update time, and archive status, then adds each property as a top-level field, and returns the flattened dictionary.

**Call relations**: `HubSpotConnector.flatten` calls this for regular CRM streams after records have been fetched.

*Call graph*: called by 1 (flatten).


##### `HubSpotConnector._flatten_product_api`  (lines 667–690)

```
def _flatten_product_api(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records from HubSpot product APIs, whose shapes vary more than CRM records. It lifts common nested forms into one flat dictionary.

**Data flow**: It receives one product API record and its stream definition. It copies the record, fills in an id from `objectId` when needed, lifts `properties` and form submission `values`, normalizes a few timestamp fields, and returns the result.

**Call relations**: `HubSpotConnector.flatten` calls this for product API streams. It uses `_normalize_epoch_millis` for timestamp formats that HubSpot returns as epoch milliseconds.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 1 (flatten).


##### `HubSpotConnector.flatten`  (lines 692–699)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Chooses the right flattening rule for each HubSpot stream. It keeps special streams as-is and normalizes the streams that need it.

**Data flow**: It receives a raw record and its stream definition. Based on the stream name, it either returns the record unchanged, flattens it as a product API record, or flattens it as a normal CRM record.

**Call relations**: The base sync system calls this after pages are fetched. It hands work to `_flatten` or `_flatten_product_api` so downstream storage sees records in a consistent shape.

*Call graph*: calls 2 internal fn (_flatten, _flatten_product_api).


##### `HubSpotConnector.record_identity`  (lines 701–714)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Finds the stable unique identity for a record. It has special logic for consent states because HubSpot’s consent records are identified by a combination of contact, subscription or status, and business unit.

**Data flow**: It receives a record and stream definition. For most streams it delegates to the base connector; for consent states it builds a compound id from contact id, subscription or unsubscribe status, and business unit, or returns nothing if required pieces are missing.

**Call relations**: The sync framework uses this when deciding whether a fetched record is new, changed, or the same as an existing one. The special consent-state path prevents different consent records for the same contact from overwriting each other.


##### `HubSpotConnector._list_properties`  (lines 716–724)

```
async def _list_properties(self, client: httpx.AsyncClient, source_object: str) -> list[str]
```

**Purpose**: Asks HubSpot which fields exist for a CRM object type. This matters because HubSpot search only returns properties explicitly requested.

**Data flow**: It receives an HTTP client and a HubSpot object type. It calls the properties endpoint, filters valid property definitions, and returns their names as strings.

**Call relations**: `HubSpotConnector._paginate_unchecked` calls this before CRM search so each search request can request every available field.

*Call graph*: called by 1 (_paginate_unchecked).


##### `HubSpotConnector.paginate`  (lines 726–739)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Public page-fetching entry for this connector. It wraps the real pagination work with friendly handling for streams that the current HubSpot account cannot access.

**Data flow**: It receives an HTTP client, stream definition, and optional cursor. It yields pages from `_paginate_unchecked`; if HubSpot returns an authentication or permission-style error for a single stream, it raises `StreamSkipped` with a readable reason instead of letting the whole run fail.

**Call relations**: The source sync runner calls this for each stream. It delegates actual fetching to `_paginate_unchecked` and consults `_is_stream_unavailable` and `_stream_skip_reason` when HubSpot rejects a stream.

*Call graph*: calls 4 internal fn (__init__, _is_stream_unavailable, _paginate_unchecked, _stream_skip_reason).


##### `HubSpotConnector._paginate_unchecked`  (lines 741–791)

```
async def _paginate_unchecked(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Routes each stream to the correct fetching strategy and performs standard CRM search pagination when no special strategy is needed.

**Data flow**: It receives a stream and cursor. It may delegate to configured pagination, junction walking, custom object walking, or product API walking; otherwise it searches CRM records page by page, removes duplicate cursor-boundary records, yields pages, and then yields archived-record tombstones.

**Call relations**: `HubSpotConnector.paginate` calls this as the main worker. It fans out to helpers such as `_paginate_product_api`, `_paginate_custom_objects`, `_paginate_junction`, `_list_properties`, `_build_search_body`, and `_paginate_archived_ids`.

*Call graph*: calls 6 internal fn (_build_search_body, _list_properties, _paginate_archived_ids, _paginate_custom_objects, _paginate_junction, _paginate_product_api); called by 1 (paginate).


##### `HubSpotConnector._is_stream_unavailable`  (lines 794–817)

```
def _is_stream_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether a HubSpot HTTP error means this account simply cannot access one stream. That is different from the API being broken.

**Data flow**: It receives an HTTP status error. It checks for status 403 and looks in HubSpot’s JSON message for permission or scope wording, returning true only for those expected access-denied cases.

**Call relations**: `paginate`, archived sweeps, and custom archived sweeps call this so unavailable streams can be skipped or ignored safely instead of treated as sync failures.

*Call graph*: called by 3 (_paginate_archived_ids, _paginate_custom_object_archived_ids, paginate).


##### `HubSpotConnector._stream_skip_reason`  (lines 820–829)

```
def _stream_skip_reason(stream_name: str, exc: httpx.HTTPStatusError) -> str
```

**Purpose**: Builds a human-readable explanation for why a HubSpot stream was skipped. It includes HubSpot’s own message when available.

**Data flow**: It receives the stream name and HTTP error. It reads the response body if possible and returns a sentence explaining that the stream is unavailable for this account.

**Call relations**: `HubSpotConnector.paginate` calls this when it turns an access problem into a `StreamSkipped` signal for the runner.

*Call graph*: called by 1 (paginate).


##### `HubSpotConnector._paginate_archived_ids`  (lines 831–866)

```
async def _paginate_archived_ids(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds deleted or archived CRM records after the normal search pass. HubSpot’s search API omits archived records, so this sweep is needed to create tombstones.

**Data flow**: It receives a client and stream. It walks the archived version of the object list endpoint, collects record ids, and yields `StreamPage` objects containing deletes instead of normal rows.

**Call relations**: `_paginate_unchecked` calls this after normal CRM records are fetched. It uses `_is_archived_sweep_unsupported` and `_is_stream_unavailable` to quietly stop when HubSpot does not support the archived sweep for that object.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._paginate_product_api`  (lines 868–962)

```
async def _paginate_product_api(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Dispatches product API streams to the helper that knows that stream’s endpoint and quirks. It is the traffic director for HubSpot areas outside normal CRM search.

**Data flow**: It receives a stream and cursor. It checks the stream name, calls the matching specialized pagination method, and yields each page from that method; for simple streams, it uses a declared GET path.

**Call relations**: `_paginate_unchecked` calls this for product API streams. It hands off to many helpers, including analytics, associations, lists, conversations, forms, pipelines, email events, and generic collection pagination.

*Call graph*: calls 21 internal fn (_paginate_analytics_reports, _paginate_analytics_views, _paginate_association_labels, _paginate_associations, _paginate_campaign_assets, _paginate_consent_states, _paginate_conversation_messages, _paginate_email_events, _paginate_event_occurrences, _paginate_event_types (+11 more)); called by 1 (_paginate_unchecked).


##### `HubSpotConnector._paginate_get_collection`  (lines 964–992)

```
async def _paginate_get_collection(self, client: httpx.AsyncClient, path: str, *, limit: int=PAGE_LIMIT, extra_params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks a standard HubSpot GET collection endpoint. This is the reusable loop for endpoints that return `results` and a next-page cursor.

**Data flow**: It receives a path, optional page size, and optional extra query parameters. It repeatedly calls the endpoint, normalizes ids from `objectId` when needed, yields non-empty result pages, and stops when there is no next cursor.

**Call relations**: Many product API helpers call this to avoid rewriting the same paging loop, including owner teams, campaign assets, forms, conversations, sequences, and the fallback product API path.

*Call graph*: called by 8 (_paginate_campaign_asset_type, _paginate_campaign_assets, _paginate_conversation_messages, _paginate_form_submissions, _paginate_owner_teams, _paginate_product_api, _paginate_sequences, _sequence_user_rows).


##### `HubSpotConnector._paginate_custom_objects`  (lines 994–1029)

```
async def _paginate_custom_objects(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: Fetches records for all HubSpot custom object types in the account. Custom objects are user-defined, so the connector must first discover their schemas.

**Data flow**: It gets custom object schemas, extracts each object type id and property list, creates a temporary search stream for that object type, yields its records, and then yields tombstones for archived custom records.

**Call relations**: `_paginate_unchecked` sends the custom objects stream here. This method coordinates `_custom_object_schemas`, `_schema_object_type_id`, `_schema_property_names`, `_paginate_custom_object_records`, and `_paginate_custom_object_archived_ids`.

*Call graph*: calls 5 internal fn (_custom_object_schemas, _paginate_custom_object_archived_ids, _paginate_custom_object_records, _schema_object_type_id, _schema_property_names); called by 1 (_paginate_unchecked); 1 external calls (__init__).


##### `HubSpotConnector._custom_object_schemas`  (lines 1031–1033)

```
async def _custom_object_schemas(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads HubSpot’s definitions for custom objects. A schema describes what a custom object is called and which properties it has.

**Data flow**: It receives an HTTP client, calls the CRM schemas endpoint, keeps only dictionary-like rows, and returns them as a list.

**Call relations**: Custom object pagination uses this to know what to fetch. Association discovery also uses it so relationships involving custom objects are included.

*Call graph*: called by 2 (_association_object_types, _paginate_custom_objects).


##### `HubSpotConnector._schema_object_type_id`  (lines 1036–1041)

```
def _schema_object_type_id(schema: dict[str, Any]) -> str | None
```

**Purpose**: Finds the best HubSpot identifier for a custom object schema. HubSpot may provide this under different field names.

**Data flow**: It receives one schema. It checks several possible identifier fields and returns the first non-empty string, or nothing if none exist.

**Call relations**: Custom object fetching, custom object row building, and association object-type discovery call this whenever they need the API-facing object type id.

*Call graph*: called by 3 (_association_object_types, _custom_object_row, _paginate_custom_objects).


##### `HubSpotConnector._schema_property_names`  (lines 1044–1059)

```
def _schema_property_names(schema: dict[str, Any]) -> list[str]
```

**Purpose**: Extracts all useful property names from a custom object schema. It also includes display properties so records have readable titles.

**Data flow**: It receives a schema. It gathers property names, the primary display property, and secondary display properties without duplicates, then returns the list.

**Call relations**: `_paginate_custom_objects` calls this before searching custom object records, because HubSpot only returns properties that are requested.

*Call graph*: called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._paginate_custom_object_records`  (lines 1061–1096)

```
async def _paginate_custom_object_records(self, client: httpx.AsyncClient, stream: StreamSpec, *, schema: dict[str, Any], properties: list[str], cursor: str | None) -> AsyncIterator[list[dict[str, Any
```

**Purpose**: Searches one custom object type and turns each raw result into the connector’s shared custom-object row shape.

**Data flow**: It receives a temporary stream, schema, property list, and cursor. It pages through HubSpot search, skips duplicate cursor-boundary records, converts valid records with `_custom_object_row`, and yields pages.

**Call relations**: `_paginate_custom_objects` calls this for each discovered custom object type. It shares `_build_search_body` with normal CRM search.

*Call graph*: calls 2 internal fn (_build_search_body, _custom_object_row); called by 1 (_paginate_custom_objects).


##### `HubSpotConnector._custom_object_row`  (lines 1098–1137)

```
def _custom_object_row(self, record: dict[str, Any], *, schema: dict[str, Any]) -> dict[str, Any] | None
```

**Purpose**: Builds a readable, stable record for one custom object. It adds object metadata so all custom object types can live in one stream without id collisions.

**Data flow**: It receives a raw record and its schema. If required ids are present, it returns a row whose id combines object type and record id, includes labels, title fields, properties, timestamps, and archive status.

**Call relations**: `_paginate_custom_object_records` calls this for each raw search result. It uses `_schema_object_type_id` to make the combined id.

*Call graph*: calls 1 internal fn (_schema_object_type_id); called by 1 (_paginate_custom_object_records).


##### `HubSpotConnector._paginate_custom_object_archived_ids`  (lines 1139–1169)

```
async def _paginate_custom_object_archived_ids(self, client: httpx.AsyncClient, *, object_type_id: str) -> AsyncIterator[StreamPage]
```

**Purpose**: Finds archived records for one custom object type and emits delete markers for them. This keeps removed custom object records from lingering forever downstream.

**Data flow**: It receives an object type id. It walks that object’s archived list endpoint, prefixes each archived id with the object type id, and yields delete pages.

**Call relations**: `_paginate_custom_objects` calls this after active records for each custom object type. It uses the same unsupported-sweep and unavailable-stream checks as normal archived CRM sweeps.

*Call graph*: calls 2 internal fn (_is_archived_sweep_unsupported, _is_stream_unavailable); called by 1 (_paginate_custom_objects); 1 external calls (__init__).


##### `HubSpotConnector._paginate_owner_teams`  (lines 1171–1190)

```
async def _paginate_owner_teams(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Extracts team records from owner records. HubSpot exposes teams nested under owners, so this creates a separate team stream.

**Data flow**: It fetches all owners, inspects their `teams` lists, deduplicates teams by id, and yields one page of unique team records.

**Call relations**: `_paginate_product_api` calls this for the owner teams stream. It relies on `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_lists`  (lines 1192–1221)

```
async def _paginate_lists(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot lists through the lists search endpoint. Lists need a different offset-based paging style instead of the common `after` cursor.

**Data flow**: It posts search requests with an offset, flattens extra list properties into each row, fills in an id from `listId`, yields pages, and advances until HubSpot says there are no more.

**Call relations**: `_paginate_product_api` calls this for lists, and `_paginate_list_memberships` calls it to find which lists need membership walks.

*Call graph*: called by 2 (_paginate_list_memberships, _paginate_product_api).


##### `HubSpotConnector._paginate_site_search`  (lines 1223–1243)

```
async def _paginate_site_search(self, client: httpx.AsyncClient, *, content_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches CMS content from HubSpot’s site search endpoint for one content type, such as knowledge articles.

**Data flow**: It receives a content type. It requests pages by numeric offset, yields dictionary results, and stops when the next offset reaches the reported total.

**Call relations**: `_paginate_product_api` uses this for knowledge articles.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_assets`  (lines 1245–1269)

```
async def _paginate_campaign_assets(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Finds assets attached to marketing campaigns, such as forms, emails, landing pages, and files. HubSpot requires walking campaigns first, then asset types under each campaign.

**Data flow**: It fetches campaign pages, chooses a campaign id and name, then for every known asset type asks `_paginate_campaign_asset_type` to fetch attached assets and yields those pages.

**Call relations**: `_paginate_product_api` calls this for campaign assets. It uses `_paginate_get_collection` for campaigns and delegates each campaign/type pair to `_paginate_campaign_asset_type`.

*Call graph*: calls 2 internal fn (_paginate_campaign_asset_type, _paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_campaign_asset_type`  (lines 1271–1306)

```
async def _paginate_campaign_asset_type(self, client: httpx.AsyncClient, *, campaign_id: str, campaign_name: Any, asset_type: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one kind of asset for one campaign and gives each asset a stable combined id. Missing or forbidden asset-type endpoints are treated as absent, not fatal.

**Data flow**: It receives campaign details and an asset type. It walks that asset endpoint, builds rows containing campaign context, asset kind, asset id, and metrics, yields pages, and ignores 403 or 404 responses.

**Call relations**: `_paginate_campaign_assets` calls this for every campaign and known asset type. It uses `_paginate_get_collection` for the page loop.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_campaign_assets).


##### `HubSpotConnector._paginate_analytics_views`  (lines 1308–1314)

```
async def _paginate_analytics_views(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Yields HubSpot analytics view definitions as a stream. An analytics view is a saved filter or reporting lens.

**Data flow**: It asks `_analytics_view_rows` for all view rows and yields them if any exist.

**Call relations**: `_paginate_product_api` calls this for the analytics views stream. The heavier row shaping is done by `_analytics_view_rows`.

*Call graph*: calls 1 internal fn (_analytics_view_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_view_rows`  (lines 1316–1347)

```
async def _analytics_view_rows(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Reads and normalizes analytics view definitions. It gives each view a stable id, readable name, filter data, and normalized timestamps.

**Data flow**: It calls the analytics views endpoint, accepts either a list response or a `results` response, filters valid rows, chooses an id and name, normalizes creation time, and returns the page list.

**Call relations**: Both `_paginate_analytics_views` and `_paginate_analytics_reports` call this. Reports use the views as optional filters for report queries.

*Call graph*: calls 1 internal fn (_normalize_epoch_millis); called by 2 (_paginate_analytics_reports, _paginate_analytics_views).


##### `HubSpotConnector._paginate_analytics_reports`  (lines 1349–1379)

```
async def _paginate_analytics_reports(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Generates many analytics report rows by querying combinations of report subjects, time periods, and analytics views. This turns HubSpot’s report API into a stream of recallable records.

**Data flow**: It builds a date window, reads analytics views, creates an all-traffic view plus each saved view, then loops through report families, subjects, time periods, and filters, yielding rows from each query.

**Call relations**: `_paginate_product_api` calls this for analytics reports. It coordinates `_analytics_report_window`, `_analytics_view_rows`, and `_paginate_analytics_report_query`.

*Call graph*: calls 3 internal fn (_analytics_report_window, _analytics_view_rows, _paginate_analytics_report_query); called by 1 (_paginate_product_api).


##### `HubSpotConnector._analytics_report_window`  (lines 1382–1383)

```
def _analytics_report_window() -> tuple[str, str]
```

**Purpose**: Chooses the date range used for analytics report queries. The range starts at a fixed early date and ends today in UTC.

**Data flow**: It reads the current UTC date and returns two strings in HubSpot’s `YYYYMMDD` format: the fixed start and current end.

**Call relations**: `_paginate_analytics_reports` calls this before issuing report queries.

*Call graph*: called by 1 (_paginate_analytics_reports); 1 external calls (now).


##### `HubSpotConnector._paginate_analytics_report_query`  (lines 1385–1436)

```
async def _paginate_analytics_report_query(self, client: httpx.AsyncClient, *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date:
```

**Purpose**: Runs one analytics report query and pages through its breakdown rows. It skips report combinations HubSpot says are invalid or missing.

**Data flow**: It receives report type details, optional analytics view filter, and date window. It repeatedly calls the report endpoint, converts the response into rows with `_analytics_report_rows`, yields them, and advances by offset until complete.

**Call relations**: `_paginate_analytics_reports` calls this inside its large combination loop. It hands raw report responses to `_analytics_report_rows` for record creation.

*Call graph*: calls 1 internal fn (_analytics_report_rows); called by 1 (_paginate_analytics_reports).


##### `HubSpotConnector._analytics_report_rows`  (lines 1439–1514)

```
def _analytics_report_rows(data: dict[str, Any], *, family: str, subject: str, time_period: str, analytics_view_id: str | None, analytics_view_name: str | None, start_date: str, end_date: str, offset:
```

**Purpose**: Turns one HubSpot analytics report response into stream records. It creates a totals row and one row per breakdown item.

**Data flow**: It receives raw report data plus context such as subject, time period, view, date range, and offset. It builds stable ids, names, report metadata, metrics, filters, and formatted dates, then returns the row list.

**Call relations**: `_paginate_analytics_report_query` calls this after each API response. It uses the report id and date formatting helpers to make consistent output rows.

*Call graph*: called by 1 (_paginate_analytics_report_query).


##### `HubSpotConnector._analytics_report_id`  (lines 1517–1521)

```
def _analytics_report_id(*parts: Any) -> str
```

**Purpose**: Builds a safe unique id for an analytics report row. It cleans characters that would make the id ambiguous.

**Data flow**: It receives any number of id parts. It converts each part to text, replaces slashes and colons, substitutes `none` for missing parts, joins them, and prefixes the result with `analytics_report:`.

**Call relations**: Analytics report row creation uses this helper so totals and breakdown records have predictable ids.


##### `HubSpotConnector._analytics_report_date`  (lines 1524–1525)

```
def _analytics_report_date(value: str) -> str
```

**Purpose**: Reformats a HubSpot analytics date string into a standard date string. It changes `YYYYMMDD` into `YYYY-MM-DD`.

**Data flow**: It receives an eight-character date string and returns the same date with hyphens inserted.

**Call relations**: Analytics report row creation uses this to make report date fields easier for downstream code and people to read.


##### `HubSpotConnector._paginate_event_types`  (lines 1527–1540)

```
async def _paginate_event_types(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches definitions of custom behavioral event types. These describe kinds of tracked events in HubSpot.

**Data flow**: It calls the event-types endpoint, accepts either list or `results` responses, gives rows an id from `id` or `fullyQualifiedName` when possible, and yields one page.

**Call relations**: `_paginate_product_api` calls this for the event types stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_event_occurrences`  (lines 1542–1559)

```
async def _paginate_event_occurrences(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches actual event occurrences, optionally only those after the last cursor. An occurrence is one tracked event happening at a time.

**Data flow**: It builds query parameters from the cursor, calls the events endpoint, filters dictionary rows, yields them, and treats a 404 endpoint as no data.

**Call relations**: `_paginate_product_api` calls this for event occurrences.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_email_events`  (lines 1561–1584)

```
async def _paginate_email_events(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches marketing email event records, such as opens or clicks, using HubSpot’s older email events API.

**Data flow**: It converts the cursor to a start timestamp when possible, requests large pages with an offset token, yields event rows, and stops when HubSpot has no more pages or the offset stops advancing.

**Call relations**: `_paginate_product_api` calls this for email events. It uses `_email_event_start_timestamp` to translate cursor formats into the API’s millisecond timestamp parameter.

*Call graph*: calls 1 internal fn (_email_event_start_timestamp); called by 1 (_paginate_product_api).


##### `HubSpotConnector._email_event_start_timestamp`  (lines 1587–1596)

```
def _email_event_start_timestamp(cursor: str | None) -> int | None
```

**Purpose**: Converts a stored email-event cursor into the millisecond timestamp expected by HubSpot’s email events API.

**Data flow**: It receives a cursor string. If it is already digits, it returns it as an integer; otherwise it tries to parse an ISO date-time string and convert it to milliseconds, returning nothing if parsing fails.

**Call relations**: `_paginate_email_events` calls this before each request so incremental email-event syncs can start from the saved cursor.

*Call graph*: called by 1 (_paginate_email_events); 1 external calls (fromisoformat).


##### `HubSpotConnector._paginate_association_labels`  (lines 1598–1614)

```
async def _paginate_association_labels(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches labels that describe relationships between HubSpot object types. A label might explain that one contact is a billing contact for a company, for example.

**Data flow**: It walks all object-type pairs that have labels, converts each raw label to a normalized row, and yields pages.

**Call relations**: `_paginate_product_api` calls this for association labels. It depends on `_association_pairs_with_labels` to discover valid pairs and `_association_label_row` to shape rows.

*Call graph*: calls 2 internal fn (_association_label_row, _association_pairs_with_labels); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_associations`  (lines 1616–1631)

```
async def _paginate_associations(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches actual links between HubSpot records across object types. This is broader than the small junction streams and covers all discovered association pairs.

**Data flow**: It discovers object-type pairs with labels, pages through source record ids for each pair, sends batch association reads, and yields normalized association rows.

**Call relations**: `_paginate_product_api` calls this for the associations stream. It coordinates `_association_pairs_with_labels`, `_paginate_crm_object_id_pages`, and `_paginate_association_batch`.

*Call graph*: calls 3 internal fn (_association_pairs_with_labels, _paginate_association_batch, _paginate_crm_object_id_pages); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_association_batch`  (lines 1633–1660)

```
async def _paginate_association_batch(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str, inputs: list[dict[str, str]]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads associations for a batch of source records and follows per-record association pagination. Some records can have too many links for one response.

**Data flow**: It receives source and target object types plus input ids. It posts a batch read, yields rows from `_association_rows`, then builds follow-up inputs from `_next_association_inputs` until no per-record cursors remain.

**Call relations**: `_paginate_associations` calls this for each page of source ids. It uses `_is_optional_pair_unavailable` to skip unsupported object pairs.

*Call graph*: calls 3 internal fn (_association_rows, _is_optional_pair_unavailable, _next_association_inputs); called by 1 (_paginate_associations).


##### `HubSpotConnector._next_association_inputs`  (lines 1663–1677)

```
def _next_association_inputs(data: dict[str, Any]) -> list[dict[str, str]]
```

**Purpose**: Finds the follow-up batch inputs needed when HubSpot says a source record has more association pages. It preserves both the source id and the next cursor.

**Data flow**: It receives a batch association response. It scans each result for a source record id and a next `after` token, then returns a list of inputs for the next batch request.

**Call relations**: `_paginate_association_batch` calls this after each batch response to continue paging association-heavy records.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_pairs_with_labels`  (lines 1679–1692)

```
async def _association_pairs_with_labels(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str, list[dict[str, Any]]]]
```

**Purpose**: Discovers which object-type pairs have association labels and should therefore be walked. It checks every from/to combination among standard and custom object types.

**Data flow**: It gets the object-type list, asks HubSpot for labels for each pair, and yields only pairs where labels exist.

**Call relations**: Both `_paginate_association_labels` and `_paginate_associations` call this as their discovery step. It relies on `_association_object_types` and `_association_labels_for_pair`.

*Call graph*: calls 2 internal fn (_association_labels_for_pair, _association_object_types); called by 2 (_paginate_association_labels, _paginate_associations).


##### `HubSpotConnector._association_object_types`  (lines 1694–1707)

```
async def _association_object_types(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: Builds the list of HubSpot object types to consider for associations. It includes known standard objects and any custom objects available in the account.

**Data flow**: It starts with a built-in list of standard object types, tries to fetch custom schemas, extracts their type ids, appends new ones, and returns the combined list.

**Call relations**: `_association_pairs_with_labels` calls this before testing object-type pairs. It uses `_custom_object_schemas`, `_schema_object_type_id`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_custom_object_schemas, _is_optional_pair_unavailable, _schema_object_type_id); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_labels_for_pair`  (lines 1709–1725)

```
async def _association_labels_for_pair(self, client: httpx.AsyncClient, *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches association labels for one source object type and one target object type. If HubSpot says that pair is not supported, it returns no labels.

**Data flow**: It receives two object type names, calls the labels endpoint for that pair, filters valid rows, and returns them; optional unavailable errors become an empty list.

**Call relations**: `_association_pairs_with_labels` calls this for each candidate pair.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_association_pairs_with_labels).


##### `HubSpotConnector._association_label_row`  (lines 1728–1745)

```
def _association_label_row(label: dict[str, Any], *, from_object_type: str, to_object_type: str) -> dict[str, Any]
```

**Purpose**: Normalizes one raw association label into a stable stream record. It records both object directions and the label/category details.

**Data flow**: It receives a label plus from/to object types. It chooses type id and category fields from HubSpot’s possible names, builds a compound id, and returns the enriched label row.

**Call relations**: `_paginate_association_labels` calls this for every label returned by association discovery.

*Call graph*: called by 1 (_paginate_association_labels).


##### `HubSpotConnector._paginate_crm_object_id_pages`  (lines 1747–1759)

```
async def _paginate_crm_object_id_pages(self, client: httpx.AsyncClient, object_type: str) -> AsyncIterator[list[str]]
```

**Purpose**: Fetches pages of only CRM object ids for a given object type. This is a lightweight way to prepare batch operations such as association reads.

**Data flow**: It receives an object type, asks `_paginate_crm_object_pages` for records with only `hs_object_id`, extracts ids, and yields pages of id strings.

**Call relations**: `_paginate_associations` uses this to get source ids, and `_paginate_sequence_enrollments` uses it to get contact ids.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 2 (_paginate_associations, _paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_crm_object_pages`  (lines 1761–1791)

```
async def _paginate_crm_object_pages(self, client: httpx.AsyncClient, object_type: str, *, properties: tuple[str, ...]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks the regular CRM object list endpoint with a chosen set of properties. This is simpler than search and is useful when a full incremental query is not needed.

**Data flow**: It receives an object type and property names. It requests pages, filters dictionary rows, yields them, and follows `paging.next.after` until finished; optional unavailable object types stop quietly.

**Call relations**: `_paginate_crm_object_id_pages` and `_paginate_contact_identity_pages` call this as their shared CRM list reader.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 2 (_paginate_contact_identity_pages, _paginate_crm_object_id_pages).


##### `HubSpotConnector._association_rows`  (lines 1794–1828)

```
def _association_rows(data: dict[str, Any], *, from_object_type: str, to_object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Turns a batch association response into one flat row per relationship type between two records.

**Data flow**: It receives raw batch data plus from/to object types. It walks each source record result, each target record, and each association type, then returns normalized rows from `_association_row`.

**Call relations**: `_paginate_association_batch` calls this after each batch association API response.

*Call graph*: called by 1 (_paginate_association_batch).


##### `HubSpotConnector._association_row`  (lines 1831–1858)

```
def _association_row(association_type: dict[str, Any], *, from_object_type: str, from_record_id: str, to_object_type: str, to_record_id: str, fallback_idx: int) -> dict[str, Any]
```

**Purpose**: Builds one normalized association record linking a source record to a target record. The id includes both record ids and the association category/type so multiple relationship meanings can coexist.

**Data flow**: It receives association type details, object types, record ids, and a fallback index. It returns a dictionary with a compound id, source and target fields, label, category, type id, relationship type, and raw association type list.

**Call relations**: `_association_rows` uses this as the final row builder for each discovered link.


##### `HubSpotConnector._is_optional_pair_unavailable`  (lines 1861–1864)

```
def _is_optional_pair_unavailable(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Decides whether an error means an optional pair or endpoint simply is not available. This keeps exploratory fan-out work from failing the whole sync.

**Data flow**: It receives an HTTP error. It returns true for 400 or 404 responses, or for permission-style unavailable-stream errors detected by `_is_stream_unavailable`.

**Call relations**: Many fan-out helpers use this when probing optional associations, memberships, consent endpoints, sequences, CRM object pages, and similar account-dependent features.

*Call graph*: called by 9 (_association_labels_for_pair, _association_object_types, _consent_status_rows, _paginate_association_batch, _paginate_crm_object_pages, _paginate_memberships_for_list, _paginate_sequence_enrollments, _paginate_sequences, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_list_memberships`  (lines 1866–1880)

```
async def _paginate_list_memberships(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches which records belong to each HubSpot list. It first finds the lists, then walks memberships inside each one.

**Data flow**: It pages through lists, chooses each list id, calls `_paginate_memberships_for_list`, and yields membership pages.

**Call relations**: `_paginate_product_api` calls this for the list memberships stream. It depends on `_paginate_lists` for the parent list records.

*Call graph*: calls 2 internal fn (_paginate_lists, _paginate_memberships_for_list); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_memberships_for_list`  (lines 1882–1925)

```
async def _paginate_memberships_for_list(self, client: httpx.AsyncClient, *, list_record: dict[str, Any], list_id: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches member records for one HubSpot list and adds list context to each membership row.

**Data flow**: It receives a list record and list id. It pages through the list membership endpoint, builds rows with compound ids, list name, object type, and processing type, and yields pages.

**Call relations**: `_paginate_list_memberships` calls this for each list. It uses `_is_optional_pair_unavailable` to ignore lists whose memberships cannot be read.

*Call graph*: calls 1 internal fn (_is_optional_pair_unavailable); called by 1 (_paginate_list_memberships).


##### `HubSpotConnector._paginate_subscription_definitions`  (lines 1927–1940)

```
async def _paginate_subscription_definitions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches HubSpot communication subscription definitions. These describe categories of email or communication preferences.

**Data flow**: It calls the definitions endpoint, accepts either `results` or `subscriptionDefinitions`, gives each row an id, and yields one page.

**Call relations**: `_paginate_product_api` calls this for the subscription definitions stream.

*Call graph*: called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_consent_states`  (lines 1942–1955)

```
async def _paginate_consent_states(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches communication consent status for contacts with email addresses. It combines per-subscription consent and unsubscribe-all state.

**Data flow**: It pages through contacts with emails. For each contact, it asks for subscription statuses and unsubscribe-all statuses, combines the returned rows into a page, and yields it.

**Call relations**: `_paginate_product_api` calls this for consent states. It coordinates `_paginate_contact_identity_pages`, `_consent_status_rows`, and `_unsubscribe_all_rows`.

*Call graph*: calls 3 internal fn (_consent_status_rows, _paginate_contact_identity_pages, _unsubscribe_all_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_contact_identity_pages`  (lines 1957–1973)

```
async def _paginate_contact_identity_pages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches contacts with their email addresses for consent lookups. The consent API is keyed by email, so contact ids alone are not enough.

**Data flow**: It reads contact pages requesting the email property, pulls email from either top-level or nested properties, and yields contact rows with an `email` field.

**Call relations**: `_paginate_consent_states` calls this before asking communication-preference endpoints for each contact.

*Call graph*: calls 1 internal fn (_paginate_crm_object_pages); called by 1 (_paginate_consent_states).


##### `HubSpotConnector._consent_status_rows`  (lines 1975–1996)

```
async def _consent_status_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches normal subscription consent statuses for one contact email. These are the opt-in or opt-out states for specific subscription types.

**Data flow**: It receives a contact and email, URL-encodes the email, calls the statuses endpoint, converts each valid result with `_consent_row`, and returns the list.

**Call relations**: `_paginate_consent_states` calls this for each contact email. It uses `_is_optional_pair_unavailable` to skip unavailable responses.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._unsubscribe_all_rows`  (lines 1998–2022)

```
async def _unsubscribe_all_rows(self, client: httpx.AsyncClient, *, contact: dict[str, Any], email: str) -> list[dict[str, Any]]
```

**Purpose**: Fetches unsubscribe-all consent state for one contact email. This captures the broad preference to opt out of all email.

**Data flow**: It receives a contact and email, URL-encodes the email, calls the unsubscribe-all endpoint, converts each valid result with `_consent_row`, and returns the list.

**Call relations**: `_paginate_consent_states` calls this alongside `_consent_status_rows` for each contact email.

*Call graph*: calls 2 internal fn (_consent_row, _is_optional_pair_unavailable); called by 1 (_paginate_consent_states); 1 external calls (quote).


##### `HubSpotConnector._consent_row`  (lines 2025–2056)

```
def _consent_row(row: dict[str, Any], *, contact: dict[str, Any], email: str, status_kind: str) -> dict[str, Any]
```

**Purpose**: Normalizes one communication-preference status into a consent-state record. It adds contact and email context and builds a stable id.

**Data flow**: It receives a raw status row, contact, email, and status kind. It chooses subscription and business-unit parts, copies legal/status fields, sets purpose and timestamps, and returns the enriched row.

**Call relations**: `_consent_status_rows` and `_unsubscribe_all_rows` call this to produce rows in the same shape for both consent kinds.

*Call graph*: called by 2 (_consent_status_rows, _unsubscribe_all_rows).


##### `HubSpotConnector._paginate_sequences`  (lines 2058–2085)

```
async def _paginate_sequences(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sales sequences for each HubSpot user discovered through owners. Sequences are tied to users, so the connector must fan out by user id.

**Data flow**: It gets unique sequence users, calls the sequences endpoint with each user id, adds owner context to each row, yields pages, and skips users whose sequence data is unavailable.

**Call relations**: `_paginate_product_api` calls this for sequences. It uses `_sequence_user_rows`, `_paginate_get_collection`, and `_is_optional_pair_unavailable`.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_get_collection, _sequence_user_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_user_rows`  (lines 2087–2110)

```
async def _sequence_user_rows(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds the list of HubSpot users who may own sales sequences, using owner records as the source.

**Data flow**: It fetches owners, extracts unique `userId` values, keeps related owner id and email, and yields one page of user rows.

**Call relations**: `_paginate_sequences` calls this before fanning out to the sequences endpoint. It uses `_paginate_get_collection` to read owners.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_sequences).


##### `HubSpotConnector._paginate_sequence_enrollments`  (lines 2112–2130)

```
async def _paginate_sequence_enrollments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches sequence enrollment records for contacts. An enrollment says a contact is or was enrolled in a sales sequence.

**Data flow**: It pages through contact ids, calls the enrollment endpoint for each contact, converts responses with `_sequence_enrollment_rows`, and yields pages.

**Call relations**: `_paginate_product_api` calls this for sequence enrollments. It uses `_paginate_crm_object_id_pages` and treats optional unavailable responses as skips.

*Call graph*: calls 3 internal fn (_is_optional_pair_unavailable, _paginate_crm_object_id_pages, _sequence_enrollment_rows); called by 1 (_paginate_product_api).


##### `HubSpotConnector._sequence_enrollment_rows`  (lines 2133–2147)

```
def _sequence_enrollment_rows(data: dict[str, Any], *, contact_id: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes sequence enrollment API responses into rows with stable ids and contact context.

**Data flow**: It receives raw response data and a contact id. It accepts either a `results` list or a single response object, chooses or builds an id for each row, adds `contact_id`, and returns the rows.

**Call relations**: `_paginate_sequence_enrollments` calls this after each contact enrollment lookup.

*Call graph*: called by 1 (_paginate_sequence_enrollments).


##### `HubSpotConnector._paginate_form_submissions`  (lines 2149–2178)

```
async def _paginate_form_submissions(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches submissions for every HubSpot form. HubSpot requires finding forms first, then reading submissions per form.

**Data flow**: It pages through forms, picks each form id, walks that form’s submissions endpoint with a smaller limit, builds submission rows with form id and form name, and yields pages.

**Call relations**: `_paginate_product_api` calls this for form submissions. It uses `_paginate_get_collection` for both forms and submissions.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_conversation_messages`  (lines 2180–2195)

```
async def _paginate_conversation_messages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches messages inside HubSpot conversation threads. Threads are read first, then messages are fetched per thread.

**Data flow**: It pages through conversation threads, chooses each thread id, walks that thread’s messages endpoint, adds `thread_id` to each message, and yields pages.

**Call relations**: `_paginate_product_api` calls this for conversation messages. It uses `_paginate_get_collection` for both thread and message paging.

*Call graph*: calls 1 internal fn (_paginate_get_collection); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipelines`  (lines 2197–2204)

```
async def _paginate_pipelines(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches deal and ticket pipelines as normalized records. Pipelines describe sales or support workflow lanes.

**Data flow**: It loops over pipeline-capable object types, asks `_pipeline_rows_for_object_type` for rows, and yields non-empty pages.

**Call relations**: `_paginate_product_api` calls this for the pipelines stream.

*Call graph*: calls 1 internal fn (_pipeline_rows_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._paginate_pipeline_stages`  (lines 2206–2256)

```
async def _paginate_pipeline_stages(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches stages inside deal and ticket pipelines. Stages are the steps within a pipeline, such as open, closed, or won.

**Data flow**: It reads raw pipelines per object type, walks each pipeline’s stages, builds compound stage ids, adds pipeline context, status, probability, order, and closed-state fields, then yields pages.

**Call relations**: `_paginate_product_api` calls this for pipeline stages. It uses `_raw_pipelines_for_object_type` to get the source pipeline data.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_product_api).


##### `HubSpotConnector._pipeline_rows_for_object_type`  (lines 2258–2281)

```
async def _pipeline_rows_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Normalizes pipeline records for one object type, such as deals or tickets. It adds object kind and a stable compound id.

**Data flow**: It receives an object type, gets raw pipelines, skips rows without ids, marks each as active or archived, and returns the row list.

**Call relations**: `_paginate_pipelines` calls this for each supported pipeline object type. It depends on `_raw_pipelines_for_object_type`.

*Call graph*: calls 1 internal fn (_raw_pipelines_for_object_type); called by 1 (_paginate_pipelines).


##### `HubSpotConnector._raw_pipelines_for_object_type`  (lines 2283–2294)

```
async def _raw_pipelines_for_object_type(self, client: httpx.AsyncClient, object_type: str) -> list[dict[str, Any]]
```

**Purpose**: Reads raw pipeline data for one HubSpot object type. Forbidden or missing pipeline endpoints are treated as empty.

**Data flow**: It receives an object type, calls the pipelines endpoint, returns valid result dictionaries, and returns an empty list for 403 or 404 responses.

**Call relations**: `_pipeline_rows_for_object_type` and `_paginate_pipeline_stages` call this as their source of raw pipeline data.

*Call graph*: called by 2 (_paginate_pipeline_stages, _pipeline_rows_for_object_type).


##### `HubSpotConnector._is_archived_sweep_unsupported`  (lines 2297–2301)

```
def _is_archived_sweep_unsupported(exc: httpx.HTTPStatusError) -> bool
```

**Purpose**: Detects the specific HubSpot error that says paging through deleted objects is not supported. That lets the connector skip only this optional cleanup step.

**Data flow**: It receives an HTTP error. It checks for status 400, reads HubSpot’s message, and returns true if the message contains the known unsupported archived-paging text.

**Call relations**: Archived sweeps for normal and custom objects call this when their deleted-record list request fails.

*Call graph*: called by 2 (_paginate_archived_ids, _paginate_custom_object_archived_ids).


##### `HubSpotConnector._upstream_message`  (lines 2304–2312)

```
def _upstream_message(exc: httpx.HTTPStatusError) -> str | None
```

**Purpose**: Extracts HubSpot’s error message from an HTTP error response, if one is available. It is a small helper for readable error checks.

**Data flow**: It receives an HTTP error, tries to parse the response body as JSON, and returns the `message` field as text or nothing if it cannot.

**Call relations**: `_is_archived_sweep_unsupported` uses this to inspect HubSpot’s explanation for a failed archived sweep.


##### `HubSpotConnector._paginate_junction`  (lines 2314–2361)

```
async def _paginate_junction(self, client: httpx.AsyncClient, *, parent_object: str, target_object: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds simple relationship rows for specific parent-to-target links such as deal contacts or task companies. It reads associations embedded in parent object list responses.

**Data flow**: It receives parent and target object types. It pages through parent objects with `associations` requested, extracts each target id, builds a row with a compound id and both sides’ ids, and yields pages.

**Call relations**: `_paginate_unchecked` calls this for the small junction streams. These streams full-refresh because HubSpot does not provide association modification timestamps.

*Call graph*: called by 1 (_paginate_unchecked).


### `extensions/sources/ufo_ext_sources/providers/salesforce.py`

`io_transport` · `source sync`

This file is the Salesforce read connector. Its job is to ask a Salesforce organization what fields exist on each supported object, fetch the records in small pages, and pass them back in a common shape used by the rest of the system. Without it, this project could not sync Salesforce data into its recallable pages.

Salesforce data is organized into “SObjects,” meaning named record types like Account, Contact, or Opportunity. Rather than hard-coding every possible field, the connector first asks Salesforce to describe the object. This matters because different Salesforce organizations can add custom fields. The connector then builds a SOQL query, which is Salesforce’s SQL-like search language, to select all available fields and order the results by SystemModstamp, Salesforce’s last-changed timestamp.

The main flow is in paginate. It fetches records from Salesforce’s query endpoint, follows Salesforce’s next-page link until there are no more records, and yields each batch. On later syncs, when a cursor exists, it also asks Salesforce for records that were hard-deleted since that cursor. Those are returned as a tombstone page, so the downstream system can remove vanished records without rereading everything.

If Salesforce says access is refused, the stream is skipped with a clear message rather than crashing the whole idea of syncing other streams. The connector deliberately has no write path; it only reads.

#### Function details

##### `_stream`  (lines 29–38)

```
def _stream(name: str, *, sobject: str, canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates the standard description for one Salesforce stream, such as accounts or contacts. It records which Salesforce object to read, which field is the record ID, and which timestamp should be used to continue from the last sync.

**Data flow**: It receives a friendly stream name, the Salesforce object name, and whether the stream is considered canonical. It fills in the shared StreamSpec fields, including Id as the primary key and SystemModstamp as the change cursor. It returns a StreamSpec that the connector later uses to know what to fetch.

**Call relations**: This helper is used while building the SALESFORCE_STREAMS list at import time. It hands each completed StreamSpec to the connector’s stream list, and it relies on StreamSpec.__init__ to create the actual stream description object.

*Call graph*: 1 external calls (__init__).


##### `SalesforceConnector._build_soql`  (lines 78–82)

```
def _build_soql(stream: StreamSpec, fields: list[str], cursor: str | None) -> str
```

**Purpose**: This function builds the Salesforce query text used to fetch records for one stream. It includes all known fields, optionally filters to records changed after the last cursor, and orders results so syncing can resume predictably.

**Data flow**: It receives a stream description, a list of field names, and possibly a cursor from a previous sync. It turns the fields into a SELECT query, adds a WHERE clause if there is a cursor, adds ordering by the cursor field, and limits the page size. It returns the finished SOQL query string.

**Call relations**: paginate calls this after _describe_fields has found the available Salesforce fields. The query it returns is then sent to Salesforce’s query endpoint so paginate can start reading records.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector._describe_fields`  (lines 84–90)

```
async def _describe_fields(self, client: httpx.AsyncClient, sobject: str) -> list[str]
```

**Purpose**: This function asks Salesforce which fields exist on a given object. It avoids hard-coded schemas, which is important because Salesforce organizations often have custom fields.

**Data flow**: It receives an HTTP client and a Salesforce object name. It calls Salesforce’s describe endpoint, reads the fields section of the response, keeps valid field names, and returns them as a list of strings. It does not change local state.

**Call relations**: paginate calls this before building a query. The field list it returns is passed into _build_soql, so the later record fetch includes whatever fields the Salesforce organization exposes.

*Call graph*: called by 1 (paginate).


##### `SalesforceConnector.paginate`  (lines 92–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main reader for a Salesforce stream. It fetches records page by page, follows Salesforce’s pagination links, and, on incremental syncs, also emits delete notices for records removed since the last cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. First it asks Salesforce for the object’s fields, builds a SOQL query, and sends it to the query endpoint. For each response, it yields any records found, then follows nextRecordsUrl until Salesforce says the query is done. If a cursor was supplied, it asks _deleted_page for deleted record IDs and yields that page if one exists. If Salesforce returns 401 or 403, it turns that refusal into a StreamSkipped error with an explanatory message; other HTTP errors are allowed to rise normally.

**Call relations**: This method is called by the broader source-sync framework when it wants records for one Salesforce stream. It coordinates _describe_fields, _build_soql, and _deleted_page, and it hands record batches or StreamPage objects back to the framework. When permission is missing or credentials are invalid, it creates a StreamSkipped exception so the framework can treat that stream as unavailable.

*Call graph*: calls 4 internal fn (__init__, _build_soql, _deleted_page, _describe_fields).


##### `SalesforceConnector._deleted_page`  (lines 121–139)

```
async def _deleted_page(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str) -> StreamPage | None
```

**Purpose**: This function checks Salesforce for records that were hard-deleted during a time window. It creates a tombstone page, which is a small message saying “these IDs are gone,” so downstream storage can delete or mark them.

**Data flow**: It receives an HTTP client, a stream description, and the previous cursor. It chooses the current UTC time as the end of the deletion window, calls Salesforce’s deleted-records endpoint with the cursor and end time, extracts deleted record IDs, and chooses the next cursor from Salesforce’s latestDateCovered value when available. It returns a StreamPage containing the deleted IDs and next cursor, or None when there is nothing useful to report.

**Call relations**: paginate calls this only after normal record paging and only when a previous cursor exists. It uses datetime.datetime.now to define the deletion-check window and StreamPage.__init__ to package the tombstones for the sync framework.

*Call graph*: called by 1 (paginate); 2 external calls (__init__, now).


##### `SalesforceConnector.flatten`  (lines 141–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function cleans each Salesforce record by removing the metadata wrapper called attributes. The rest of the system usually wants the actual field values, not Salesforce’s extra envelope information.

**Data flow**: It receives one record dictionary and the stream description. If the record contains an attributes key, it returns a new dictionary with that key removed. If there is no attributes key, it returns the original record unchanged.

**Call relations**: This method fits into the connector framework’s record-normalization step. After paginate yields Salesforce records, the framework can call flatten before storing or indexing them, so downstream code sees only the business fields.


### Ads and social channels
Connectors that pull paid advertising, social account, content, and insight records through Meta graph APIs.

### `extensions/sources/ufo_ext_sources/providers/facebook_ads.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook Ads API addresses to call, how to walk through paginated results, or how to turn account-level ad data into consistent records for syncing.

The file defines the Facebook Ads streams the system can read: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is one kind of data the sync system can fetch. Most Facebook Ads data is nested under an ad account, so the connector first asks Facebook for the user's ad accounts, then loops through each account to fetch that account's campaigns, ad sets, ads, or insights. This is like first getting a list of filing cabinets, then opening each cabinet to copy the folders inside.

Facebook returns large result sets in pages. The connector follows Facebook's `paging.next` link until there are no more pages. For campaigns, ad sets, and ads, it can skip records older than a saved cursor, which is a remembered “last seen” value used for incremental syncing. For insights, it requests daily ad-level performance, either from the cursor date forward or from the last 90 days on a first run. It also creates a stable row id for each insight because Facebook's insight rows need a dependable key for storage.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Facebook API collection page after another until Facebook says there are no more pages. It hides the repetitive work of following `paging.next`, so the rest of the connector can simply receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls Facebook, reads the JSON response, takes the list under the `data` field, yields that list when it is not empty, then follows the next-page URL if Facebook provides one. After the first request, it stops re-sending the original query parameters because Facebook's next-page URL already contains what is needed.

**Call relations**: This is the low-level paging helper used by the rest of the file. `_accounts`, `_account_children`, and `_insights` all call it whenever they need to read a Facebook collection that may span multiple pages. It uses `records_at` to safely pull records out of the response's `data` field.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Gets the ad accounts available to the authenticated Facebook user. Other streams depend on this because campaigns, ads, and insights are fetched separately for each ad account.

**Data flow**: It receives an HTTP client, asks `/me/adaccounts` for account fields such as id, name, currency, status, timezone, and business, then gathers all returned pages into one list. The output is a list of account records that later steps can loop over.

**Call relations**: This function is the starting point for account-scoped reads. `paginate` calls it directly for the `ad_accounts` stream. `_account_children` and `_insights` also call it first so they know which accounts to query before fetching campaigns, ad sets, ads, or performance data.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches account-owned objects such as campaigns, ad sets, or ads. It also applies the saved cursor so repeat syncs can focus on records updated after the last successful run.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. It chooses the correct Facebook fields for the stream, loads all ad accounts, then calls the matching account endpoint for each valid account id. For each returned page, it removes older records when a cursor is present, adds helpful account context such as ad account id and name, and yields the remaining records in batches.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. Inside, it first depends on `_accounts` to discover the accounts, then uses `_paged` to read each account's collection. It passes the records through `with_context` so later storage or display can tell which ad account each item came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches daily ad performance rows, such as impressions, clicks, spend, reach, and cost metrics. It turns Facebook's report-style data into records with stable ids so the sync system can store and update them reliably.

**Data flow**: It receives an HTTP client and an optional cursor. It builds a request for ad-level daily insight rows: if there is a cursor, it asks from that cursor date through today; otherwise it asks for the last 90 days. It then loads all ad accounts, reads each account's insights pages, and rewrites each row to include a stable id made from the account, campaign, ad set, ad, and date. It yields non-empty batches of these enriched rows.

**Call relations**: `paginate` calls this when the stream is `ads_insights`. The function uses `_accounts` to find every account, `_paged` to walk through Facebook's paginated insight results, `json.dumps` to format the date range parameter, and the current UTC date to decide the end of an incremental insight window.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right reading strategy for the requested Facebook Ads stream. It is the main doorway the broader sync framework uses to ask this connector for records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is ad accounts, it returns account records. If the stream is campaigns, ad sets, or ads, it delegates to the account-child reader. If the stream is insights, it delegates to the insights reader. If the stream name is unknown, it raises a clear skip signal instead of pretending it can sync it.

**Call relations**: The surrounding source-sync machinery calls `paginate` when it needs pages of records from this connector. `paginate` then routes the work to `_accounts`, `_account_children`, or `_insights` depending on the stream. For unsupported streams, it creates a `StreamSkipped` error so the framework can treat the stream as intentionally unavailable.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes records after they are fetched, mainly to make campaign records easier and more consistent to use. For campaigns, it chooses the effective status when available and exposes the Facebook creation time under a simpler `created_at` name.

**Data flow**: It receives one record and the stream it belongs to. For campaign records, it returns a copied record with normalized `name`, `status`, and `created_at` fields. For every other stream, it returns the record unchanged.

**Call relations**: This function is used after pagination as part of the connector's record-cleanup step. It does not call other helpers in this file; it simply shapes campaign records into the form expected by the rest of the source framework while leaving other Facebook Ads records alone.


### `extensions/sources/ufo_ext_sources/providers/instagram.py`

`io_transport` · `source sync run`

Instagram business accounts are reached through Facebook Pages, so this connector starts there. It first asks Facebook for the Pages the grant can access, then looks inside each Page for a linked Instagram business account. From those accounts it can read media posts, stories, and several kinds of analytics called insights, such as reach, impressions, replies, or video views.

The file defines the available streams, which are like named lanes of data: pages, instagram_accounts, media, media_insights, stories, story_insights, and user_insights. The main method, paginate, chooses the right lane and yields records in batches. This matters because the rest of the syncing system expects every source to provide data stream by stream, without needing to know Facebook’s particular API shape.

Facebook returns many results in pages, with a `data` list and a `paging.next` link. The helper `_paged` follows those links until there is no next page. For media and stories, the connector uses a saved cursor, or watermark, to skip older records that have already been synced. If a single post or story refuses to return insights, the connector skips just that object and keeps going. But if the whole account access is refused, it marks the stream as skipped rather than treating the sync as a crash.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Follows Facebook Graph API pagination for one endpoint. It keeps asking for the next page of results until Facebook stops giving a next link.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the response body, pulls out the `data` list, yields that list when it has records, then moves to the `paging.next` URL for the next round. After the first request, it stops reusing the original query parameters because the next URL already contains them.

**Call relations**: This is the low-level page-turner used by `_pages` to read Facebook Pages and by `_account_collection` to read media or stories for each Instagram account. It relies on `records_at` to safely extract the record list from the API response.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the current grant, including any linked Instagram business account details. This is the starting point for nearly everything else in the connector.

**Data flow**: It builds a field list asking Facebook for Page IDs, names, and linked Instagram account fields. It passes that request to `_paged`, collects every batch of Page records into one list, and returns the full list.

**Call relations**: The public `paginate` method calls this when the requested stream is `pages`. `_instagram_accounts` also calls it because Instagram accounts are discovered through Pages, not by a separate top-level account list.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the Instagram business accounts linked to the accessible Facebook Pages. It also adds the Page ID and Page name so each Instagram account can be traced back to where it came from.

**Data flow**: It starts by asking `_pages` for all available Pages. For each Page, it looks for an `instagram_business_account` object with an ID. It stores those accounts in a dictionary keyed by account ID, which removes duplicates, adds Page context, and finally returns the unique accounts as a list.

**Call relations**: The public `paginate` method calls this for the `instagram_accounts` stream. `_account_collection` uses it before reading media or stories, and `_user_insights` uses it before reading account-level analytics.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a collection, such as media or stories, from every discovered Instagram business account. It can also skip records that are older than the saved cursor.

**Data flow**: It receives the collection name, the fields to request, and optional cursor information. It first gets all Instagram accounts, then for each valid account ID it calls `_paged` on that account’s collection endpoint. If a cursor is present, it keeps only records whose cursor field is newer. Before yielding a batch, it adds the Instagram account ID to each record so the record is not separated from its source account.

**Call relations**: The public `paginate` method uses this for the `media` and `stories` streams. It builds on `_instagram_accounts` for account discovery, `_paged` for Facebook pagination, and `with_context` to attach account context to each returned record.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches insight metrics for individual objects, such as a media post or a story. It turns per-object analytics into records the sync system can store.

**Data flow**: It receives an async stream of object batches, a metrics list, and the output stream name. For each object with a valid ID, it asks Facebook for that object’s insights. Each returned insight is rewritten with a stable ID made from the object ID and insight name, plus a pointer back to the parent object. If Facebook says a specific object’s insights are unavailable or forbidden with common expected errors, that object is skipped and the rest continue.

**Call relations**: The public `paginate` method calls this for `media_insights` and `story_insights`, feeding it media or story records from the connector’s own pagination flow. Inside, it uses `records_at` to pull the insight rows from Facebook’s response.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads account-level daily insight values for each Instagram business account. These are metrics about the account as a whole, such as impressions, reach, and profile views.

**Data flow**: It starts with the list of Instagram accounts. For each account ID, it asks Facebook for daily insight metrics. Facebook returns each metric with a list of dated values, so the function walks those values, skips anything at or before the saved cursor, and creates one output record per account, metric, and end time. Each record gets a stable ID and the Instagram account ID.

**Call relations**: The public `paginate` method calls this for the `user_insights` stream. It depends on `_instagram_accounts` to know which accounts to query, `records_at` to read the API’s data list, and `list_or_empty` to safely treat missing metric values as an empty list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for this connector. Given a stream name, it chooses the correct way to fetch that kind of Instagram data and yields batches of records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and then calls the matching helper: Pages, Instagram accounts, media, stories, object insights, or user insights. The chosen helper produces record batches, and `paginate` passes those batches onward. If the stream is unknown, it reports the stream as skipped.

**Call relations**: This is the method the source framework calls when it wants data from Instagram. It wires together `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, and `_user_insights`. If Facebook refuses access to a whole stream with an authentication or permission error, it raises `StreamSkipped` so the run records a controlled skip instead of a general failure.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Support and helpdesk
Connectors that sync tickets, conversations, users, organizations, knowledge content, and customer support activity.

### `extensions/sources/ufo_ext_sources/providers/freshdesk.py`

`io_transport` · `source sync`

Freshdesk exposes many kinds of information: tickets, conversations, contacts, companies, agents, help articles, forum discussions, settings, and more. This file is the connector that turns those Freshdesk API endpoints into named streams the rest of the system can sync. Without it, the system would not know where Freshdesk keeps each kind of data, how to ask for the next page, or how to deal with special cases like ticket conversations and knowledge-base trees.

The file first defines the available streams. A stream is a named lane of records, such as “tickets” or “contacts.” It then defines FreshdeskConnector, which builds an HTTP client using Freshdesk's API-key login style: the API key is used like a username, with a dummy password.

The most important method is paginate. It acts like a traffic director. For simple streams, it follows Freshdesk's “next page” links. For tickets, it uses numbered pages and can start from an updated-since cursor so syncs can be incremental. For nested data, it first fetches parent records, then visits each child endpoint. For example, conversations are fetched ticket by ticket, and solution articles are fetched by walking category → folder → article.

If Freshdesk says access is forbidden or unauthorized, the connector marks that stream as skipped instead of treating the whole sync as a mystery failure.

#### Function details

##### `_stream`  (lines 56–70)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Freshdesk data, such as tickets or contacts. This keeps the long stream list compact and consistent.

**Data flow**: It takes a stream name and optional details like the API object name, primary key, cursor field, and whether the stream is considered canonical. It fills in sensible defaults, then returns a StreamSpec object that the sync system can use later to identify and read that stream.

**Call relations**: This helper is used while the file is being loaded to build FRESHDESK_STREAMS. It hands each finished StreamSpec to the FreshdeskConnector class through its streams_list setting.

*Call graph*: 1 external calls (__init__).


##### `FreshdeskConnector.record_identity`  (lines 110–113)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Chooses the stable identity for a Freshdesk record. It gives the helpdesk settings record a fixed identity because that endpoint returns one settings object rather than a normal list of records with separate IDs.

**Data flow**: It receives one record and the stream it came from. If the stream is settings, it returns the fixed key "helpdesk". For every other stream, it lets the parent RestConnector use the usual identity rule.

**Call relations**: The broader sync machinery calls this when it needs to name or deduplicate a record. This override only steps in for the settings stream; otherwise it stays out of the way.


##### `FreshdeskConnector.record_ref`  (lines 115–119)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: Picks a human-friendly reference value for a settings record. For Freshdesk helpdesk settings, it uses the primary language when available.

**Data flow**: It receives a record and its stream. For non-settings streams, it delegates to the parent connector. For settings, it reads the primary_language field and returns it as text if it is a string or number; otherwise it returns nothing.

**Call relations**: The sync system calls this when it wants a readable label for a stored record. This method complements record_identity by making the one settings page easier to recognize.


##### `FreshdeskConnector._make_client`  (lines 121–136)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to a tenant's Freshdesk API. It also applies Freshdesk's authentication rule: API key as the username and "X" as the password.

**Data flow**: It receives a base URL and a resolved credential. It trims any trailing slash from the URL, sets JSON headers, and creates a timeout so network calls do not hang forever. If the credential already includes a custom transport, it uses that. Otherwise, if it has an API key, it creates Basic authentication with that key. If no usable authentication is present, it raises an error.

**Call relations**: The parent RestConnector calls this when a sync run is preparing to make API requests. The returned httpx.AsyncClient is then passed into paginate and the lower-level page walkers.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `FreshdeskConnector._build_tickets_params`  (lines 139–149)

```
def _build_tickets_params(cursor: str | None, page: int) -> dict[str, Any]
```

**Purpose**: Builds the query options for one page of Freshdesk tickets. It keeps ticket requests consistent, including page size, sort order, and optional incremental syncing.

**Data flow**: It receives an optional cursor and a page number. It creates a dictionary asking Freshdesk for up to 100 tickets, ordered by updated_at from oldest to newest, including useful extra fields. If a cursor is present, it adds updated_since so only newer or changed tickets are requested. The completed parameter dictionary is returned.

**Call relations**: FreshdeskConnector._paginate_tickets calls this before each ticket request. It is a small helper that keeps the ticket pagination loop easy to read.

*Call graph*: called by 1 (_paginate_tickets).


##### `FreshdeskConnector.paginate`  (lines 151–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to fetch pages for each Freshdesk stream. It is the main doorway from the generic sync system into Freshdesk-specific API behavior.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and then sends the work to the matching pagination method: numbered pages for tickets, ticket-by-ticket fetching for conversations, parent-child walking for nested objects, or link-header paging for ordinary lists. It yields lists of records as they arrive. If Freshdesk returns 401 or 403, it turns that into a StreamSkipped error with a clear explanation.

**Call relations**: The sync engine calls paginate when it wants records from a stream. paginate then calls _paginate_tickets, _paginate_conversations, _paginate_two_level, _paginate_three_level, or _paginate_link_header depending on the shape of that Freshdesk endpoint.

*Call graph*: calls 6 internal fn (__init__, _paginate_conversations, _paginate_link_header, _paginate_three_level, _paginate_tickets, _paginate_two_level).


##### `FreshdeskConnector._paginate_link_header`  (lines 232–239)

```
async def _paginate_link_header(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Freshdesk endpoints that advertise the next page through an HTTP Link header. A Link header is like a note on the response saying, “go here for the next batch.”

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the inherited link-header page reader to fetch pages of up to 100 records, then yields each page unchanged.

**Call relations**: paginate uses this for many simple Freshdesk streams. The nested walkers also use it whenever they need to fetch a parent list or a child list.

*Call graph*: called by 4 (_paginate_conversations, _paginate_three_level, _paginate_two_level, paginate).


##### `FreshdeskConnector._paginate_tickets`  (lines 241–258)

```
async def _paginate_tickets(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Freshdesk tickets using Freshdesk's special numbered-page style. It supports incremental sync by asking for tickets updated since a cursor.

**Data flow**: It starts at page 1. For each page, it builds ticket request parameters, calls the Freshdesk tickets endpoint, extracts the returned records, and yields them. It stops when Freshdesk returns no records, when the page is not full, or when it reaches Freshdesk's 300-page limit.

**Call relations**: paginate calls this for the tickets stream. _paginate_conversations also calls it first, because conversations are found by walking through tickets and then asking for each ticket's conversations.

*Call graph*: calls 1 internal fn (_build_tickets_params); called by 2 (_paginate_conversations, paginate).


##### `FreshdeskConnector._paginate_conversations`  (lines 260–276)

```
async def _paginate_conversations(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches ticket conversations by first finding tickets, then asking Freshdesk for the conversations attached to each ticket. This is needed because conversations are nested under tickets rather than exposed as one simple global list.

**Data flow**: It receives an HTTP client and optional cursor. It uses _paginate_tickets to get ticket pages allowed by that cursor. For each ticket with an ID, it fetches that ticket's conversation pages. Before yielding a conversation page, it makes sure each conversation record includes the ticket_id, so the relationship is preserved.

**Call relations**: paginate calls this for the conversations stream. Inside, it relies on _paginate_tickets to find the parent tickets and _paginate_link_header to read each ticket's conversation pages.

*Call graph*: calls 2 internal fn (_paginate_link_header, _paginate_tickets); called by 1 (paginate).


##### `FreshdeskConnector._paginate_two_level`  (lines 278–289)

```
async def _paginate_two_level(self, client: httpx.AsyncClient, *, parent_path: str, child_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks Freshdesk data that has a parent-child shape, such as categories with forums or folders with responses. It is a reusable helper for “get parents, then get each parent's children.”

**Data flow**: It receives a parent API path and a child path template containing an ID slot. It fetches parent pages, reads each parent's ID, formats the child path with that ID, then yields the child pages found there. Parents without usable IDs are skipped.

**Call relations**: paginate calls this for several nested streams, including canned responses, solution folders, discussion forums, discussion topics, and discussion comments. It uses _paginate_link_header for both parent and child lists.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


##### `FreshdeskConnector._paginate_three_level`  (lines 291–314)

```
async def _paginate_three_level(self, client: httpx.AsyncClient, *, root_path: str, mid_path_template: str, leaf_path_template: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks Freshdesk data with three layers, such as solution categories, then folders, then articles. It is the deeper version of the parent-child helper.

**Data flow**: It receives paths for the root level, middle level, and leaf level. It fetches root records, reads each root ID, fetches the middle records under that root, reads each middle ID, then fetches and yields the final leaf pages. Any record without the needed ID is skipped.

**Call relations**: paginate calls this for solution_articles. It uses _paginate_link_header at every level so it can follow Freshdesk's normal next-page links while walking the category-to-folder-to-article tree.

*Call graph*: calls 1 internal fn (_paginate_link_header); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/intercom.py`

`io_transport` · `source sync`

Intercom does not expose all of its data in one simple shape. Some resources use a search endpoint, some use a scrolling endpoint, some return a single list, and some are nested inside parent objects. This file is the adapter that hides those differences from the rest of UFO. Think of it like a travel plug: Intercom has several odd-shaped sockets, and this connector presents one standard plug to the sync engine.

The file first defines the Intercom streams UFO knows about. A stream is a named kind of data to sync, such as contacts or conversations, with details like its main identifier and its cursor field. A cursor field is the timestamp-like value used to continue later from where the last sync stopped.

The IntercomConnector then adds Intercom-specific behavior. It creates an authenticated HTTP client with the required Intercom API version header. It decides how each stream should be paged through. It also flattens some nested Intercom data into simpler top-level fields, so later SQL or indexing steps can easily read values like a conversation requester or a contact’s company.

A key detail is error behavior: if Intercom says the token is not allowed to read a stream, with HTTP 401 or 403, this connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 52–66)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=True) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is UFO’s small description of one kind of Intercom data to sync. It keeps the stream list readable by filling in common defaults like the primary key and cursor field.

**Data flow**: It receives a stream name and optional details such as the Intercom object name, primary key, cursor field, and whether the stream is canonical. It fills in missing values with sensible defaults, then returns a StreamSpec object that the connector uses later to know how to sync that stream.

**Call relations**: This helper is used while the file is loaded to build the INTERCOM_STREAMS list. It hands each stream definition to StreamSpec.__init__, which creates the formal stream description used by IntercomConnector.

*Call graph*: 1 external calls (__init__).


##### `IntercomConnector.record_identity`  (lines 101–105)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This decides the stable identity value for a record. For normal streams it uses the base connector’s rule, but Intercom data attributes need a special fallback because they may identify themselves by either id or full_name.

**Data flow**: It receives one record and the stream it belongs to. If the stream is not an Intercom attribute stream, it passes the work to the parent connector. If it is an attribute stream, it looks for id first, then full_name, converts a string or number to text, and returns that as the record’s identity.

**Call relations**: The broader sync system calls this when it needs to name or de-duplicate records. This method only steps in for company_attributes and contact_attributes; all other streams continue through the inherited RestConnector behavior.


##### `IntercomConnector.record_ref`  (lines 107–111)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a human-friendly reference label for some Intercom records. For tags, teams, and attribute records, the name field is more useful to people than a numeric or opaque id.

**Data flow**: It receives a record and its stream. For tags, teams, and attribute streams, it reads the record’s name and returns it as text if it is a string or number. For other streams, it delegates to the base connector’s normal reference logic.

**Call relations**: The sync system uses this when it wants a readable label for a synced record. This function customizes that label for a few Intercom streams and otherwise leaves the base RestConnector in charge.


##### `IntercomConnector._make_client`  (lines 113–116)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Intercom and adds the Intercom API version header. Without that header, Intercom may interpret requests using a different API version than this connector expects.

**Data flow**: It receives the API base URL and a resolved Credential, which represents the authentication details. It asks the parent connector to create the usual authenticated httpx.AsyncClient, adds the Intercom-Version header, and returns the ready-to-use client.

**Call relations**: The base RestConnector setup flow calls this when preparing network access. This method extends the parent client setup instead of replacing it, so authentication still comes from the shared connector machinery.


##### `IntercomConnector._build_search_body`  (lines 119–149)

```
def _build_search_body(stream: StreamSpec, cursor: str | None, starting_after: str | None) -> dict[str, Any]
```

**Purpose**: This builds the JSON body for Intercom search requests. It tells Intercom how many records to return, where to continue within a multi-page result, and how to filter records newer than the saved cursor.

**Data flow**: It receives a stream, the last saved cursor, and Intercom’s page cursor called starting_after. It creates a request body with a page size, ascending sort order, optional starting_after value, and a query that asks for records whose cursor field is greater than the saved cursor. It returns that body as a dictionary ready to send in a POST request.

**Call relations**: IntercomConnector._paginate_search and IntercomConnector._paginate_conversation_parts call this each time they request a search page. It supplies the exact request shape those paginators send to Intercom.

*Call graph*: called by 2 (_paginate_conversation_parts, _paginate_search).


##### `IntercomConnector._first`  (lines 152–157)

```
def _first(value: Any) -> dict[str, Any] | None
```

**Purpose**: This small helper safely picks the first dictionary from a list. It is used when Intercom wraps related objects, such as contacts or companies, inside a list and the connector only needs the first one.

**Data flow**: It receives any value. If that value is a non-empty list and its first item is a dictionary, it returns that first dictionary. Otherwise it returns None, meaning there was no usable first object.

**Call relations**: The flattening helpers use this to avoid repeating safety checks when pulling one related contact or company out of Intercom’s nested response data.


##### `IntercomConnector._flatten_conversation`  (lines 160–177)

```
def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes conversation records easier for later steps to read. It lifts important nested fields, like source type and requester id, onto the top level of the record.

**Data flow**: It receives one conversation record. It copies the record, reads nested source fields if present, and writes source__type, source__subject, and source__body onto the copy. It also looks inside the contacts wrapper and, if there is a first contact, writes that contact’s id as requester_id. It returns the enriched copy.

**Call relations**: IntercomConnector.flatten calls this for the conversations stream. It uses IntercomConnector._first to safely extract the first related contact from Intercom’s nested contact list.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_conversation_part`  (lines 180–189)

```
def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes a conversation part, such as a reply or note, easier to query later. It lifts the nested author information onto simple top-level fields.

**Data flow**: It receives one conversation part record. It copies the record, checks whether the author field is a dictionary, and if so adds author_type and author_id to the copy. It leaves conversation_id alone, because another pagination step adds that parent conversation id before flattening happens.

**Call relations**: IntercomConnector.flatten calls this for the conversation_parts stream. It prepares records that were produced by IntercomConnector._paginate_conversation_parts.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector._flatten_contact`  (lines 192–200)

```
def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This makes contact records easier to connect to companies. It pulls the first associated company id into a top-level org_id field.

**Data flow**: It receives one contact record. It copies the record, looks inside the nested companies wrapper, safely picks the first company, and writes that company’s id or company_id as org_id. It returns the modified copy.

**Call relations**: IntercomConnector.flatten calls this for the contacts stream. It uses IntercomConnector._first to handle Intercom’s nested list shape safely.

*Call graph*: called by 1 (flatten).


##### `IntercomConnector.flatten`  (lines 202–216)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This is the connector’s final cleanup step for each Intercom record. It makes selected nested fields easier to access and converts integer cursor values into strings so UFO’s watermark tracking can store them.

**Data flow**: It receives one record and its stream description. Depending on the stream name, it sends the record through the conversation, conversation part, or contact flattening helper. Then, if the stream has a cursor field and that field is an integer, it returns a copy where the cursor value is stored as decimal text. Otherwise it returns the record as-is.

**Call relations**: The shared sync flow calls this after records are fetched and before they are stored or indexed. It hands off to IntercomConnector._flatten_conversation, IntercomConnector._flatten_conversation_part, or IntercomConnector._flatten_contact when the stream needs special cleanup.

*Call graph*: calls 3 internal fn (_flatten_contact, _flatten_conversation, _flatten_conversation_part).


##### `IntercomConnector.paginate`  (lines 218–262)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Intercom streams. Given a stream name, it chooses the right pagination method and yields batches of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks which family the stream belongs to, such as search, scroll, list, attributes, nested conversation parts, company segments, or activity logs. It then yields each page produced by the matching helper. If Intercom refuses access with HTTP 401 or 403, it turns that into StreamSkipped so the stream is recorded as skipped rather than failing the whole run.

**Call relations**: The base sync machinery calls this when it needs records for a stream. This function delegates to IntercomConnector._paginate_search, _paginate_scroll, _paginate_list, _paginate_attributes, _paginate_conversation_parts, _paginate_company_segments, or _paginate_activity_logs depending on the stream.

*Call graph*: calls 8 internal fn (__init__, _paginate_activity_logs, _paginate_attributes, _paginate_company_segments, _paginate_conversation_parts, _paginate_list, _paginate_scroll, _paginate_search).


##### `IntercomConnector._paginate_search`  (lines 264–284)

```
async def _paginate_search(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that use Intercom’s search API, such as conversations, contacts, and tickets. It keeps asking for the next search page until Intercom says there are no more pages.

**Data flow**: It receives an HTTP client, a search-style stream, and the saved cursor. It builds a search request body, posts it to the correct endpoint, extracts the records from the response, and yields them as a page. It then reads Intercom’s next starting_after cursor and repeats until no next cursor exists.

**Call relations**: IntercomConnector.paginate calls this for streams listed in the search path table. Each request body comes from IntercomConnector._build_search_body, which keeps cursor filtering and page continuation consistent.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_scroll`  (lines 286–300)

```
async def _paginate_scroll(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads companies using Intercom’s scroll API. A scroll API is like being handed a bookmark for the next chunk of a long list.

**Data flow**: It starts with no scroll_param. On each loop, it sends a GET request to /companies/scroll, including the previous scroll_param if one exists. It extracts company records from data, yields them if present, then stores the returned scroll_param for the next request. It stops when there are no records or no next scroll_param.

**Call relations**: IntercomConnector.paginate calls this when syncing the companies stream. It uses the shared _get request helper inherited from RestConnector to do the actual network call.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_list`  (lines 302–314)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple Intercom streams that return a single list, such as admins, tags, teams, and segments. These do not need multi-page cursor logic in this connector.

**Data flow**: It receives the client and stream, finds the stream’s path, and sends one GET request. It then looks for a list under either the stream name or data, yields that list if it is non-empty, and returns.

**Call relations**: IntercomConnector.paginate calls this for streams listed in the simple list path table. It relies on the base connector’s _get helper for the HTTP request.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_attributes`  (lines 316–325)

```
async def _paginate_attributes(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Intercom data attribute definitions for either companies or contacts. These are custom fields that describe extra data Intercom may store on those objects.

**Data flow**: It receives the client and stream, maps the stream name to the Intercom model name, and requests /data_attributes with that model as a parameter. It keeps only items in the response data list that are dictionaries, yields them if any exist, and then finishes.

**Call relations**: IntercomConnector.paginate calls this for company_attributes and contact_attributes. The stream-to-model mapping tells it whether to ask Intercom for company attributes or contact attributes.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_conversation_parts`  (lines 327–359)

```
async def _paginate_conversation_parts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the messages, notes, or other parts inside conversations. Intercom does not provide these as a simple independent list here, so the connector first finds conversations and then fetches each conversation’s details.

**Data flow**: It receives the client and saved cursor. It searches conversations page by page, using the conversations cursor to find relevant parent conversations. For each conversation with an id, it fetches /conversations/{id}, extracts the nested conversation_parts list, stamps each part with the parent conversation_id if missing, and yields the parts. It follows the search API’s next starting_after cursor until there are no more conversation pages.

**Call relations**: IntercomConnector.paginate calls this for the conversation_parts stream. It calls IntercomConnector._build_search_body to search parent conversations in the same cursor-aware way as normal conversation syncing.

*Call graph*: calls 1 internal fn (_build_search_body); called by 1 (paginate).


##### `IntercomConnector._paginate_company_segments`  (lines 361–385)

```
async def _paginate_company_segments(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the segments attached to each company. Because the segments are reached through each company, it first scrolls through companies and then asks Intercom for every company’s segments.

**Data flow**: It starts with no scroll_param and repeatedly requests /companies/scroll. For each company returned, it reads the company id, requests /companies/{id}/segments, extracts segment records, and stamps each segment with company_id if missing. It yields segment pages when found, then continues with the next company scroll_param until there are no more companies or no next scroll marker.

**Call relations**: IntercomConnector.paginate calls this for the company_segments stream. It combines the company scroll pattern with per-company detail requests to produce records that the rest of the sync engine can treat like a normal stream.

*Call graph*: called by 1 (paginate).


##### `IntercomConnector._paginate_activity_logs`  (lines 387–409)

```
async def _paginate_activity_logs(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads admin activity logs, optionally starting after the last saved created_at cursor. It follows Intercom’s next-page links until the log stream is exhausted.

**Data flow**: It receives the client and optional cursor. If a cursor exists, it sends it as created_at_after on the first request to /admins/activity_logs. For each response, it yields any activity_logs records, reads the next page link, converts a full URL into a relative path if needed, and continues until there is no next link.

**Call relations**: IntercomConnector.paginate calls this for the activity_logs stream. It uses the inherited _get helper for each page and keeps its own next_path loop because this endpoint returns a next link rather than the same cursor shape used by other Intercom endpoints.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/zendesk.py`

`io_transport` · `during Zendesk source sync`

Zendesk stores support data behind many web API endpoints, and those endpoints do not all behave the same way. This file is the adapter that hides those differences. Without it, the system would not know which Zendesk URLs to call, how to move through pages of results, or how to turn special feeds like ticket events into useful rows such as ticket comments.

The file first defines the list of Zendesk streams the connector can read. A stream is a named kind of data, such as tickets, users, articles, or posts. Some streams use Zendesk’s incremental export API, which is like asking, “Give me everything changed since this time.” Others use ordinary page-by-page lists, where Zendesk returns a “next page” link until there is no more data.

The main class, ZendeskConnector, chooses the right reading method for each stream. Tickets, users, organizations, and ticket metric events use cursor-based incremental paging. Ticket comments are extracted from a larger ticket event feed, because Zendesk does not expose them here as a simple standalone list. User identities are read by first listing changed users, then asking Zendesk for each user’s identities.

One important detail is sideloading. For tickets, Zendesk can include related user records in the same response. This file uses those included users to copy requester, submitter, and assignee email addresses onto ticket records, making the synced ticket data easier to use.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a standard description of one Zendesk data stream, such as tickets or users. This keeps the long stream list compact and consistent, instead of repeating the same setup details for every endpoint.

**Data flow**: It receives a stream name and optional details such as the Zendesk source path, primary key, and timestamp fields. It fills in sensible defaults when details are not supplied, then returns a StreamSpec object that the connector can later use to know what to request and how to track progress.

**Call relations**: This helper is used while the module is loaded to build ZENDESK_STREAMS. It hands each finished StreamSpec to the connector’s streams_list, which later guides ZendeskConnector.paginate when a sync asks for a particular stream.

*Call graph*: 1 external calls (__init__).


##### `_apply_sideload`  (lines 142–167)

```
def _apply_sideload(records: list[dict[str, Any]], page: dict[str, Any], flatten: list[tuple[str, str, str, str]]) -> None
```

**Purpose**: Adds useful fields from related records that Zendesk included alongside the main records. In practice, it copies user email addresses onto ticket records, so downstream readers do not have to separately look up who a requester, submitter, or assignee is.

**Data flow**: It receives the main records, the full Zendesk response page, and instructions describing which ID fields point to which related records. It builds a quick lookup table from the included related items, finds matching related users for each ticket, and writes email fields directly onto the ticket records. It does not return a new list; it changes the records it was given.

**Call relations**: ZendeskConnector._paginate_incremental_cursor calls this when it is reading a stream that requested sideloaded data, especially tickets with included users. After this enrichment step, the paginator yields records that are more complete and easier for the rest of the sync system to store.

*Call graph*: called by 1 (_paginate_incremental_cursor).


##### `ZendeskConnector._data_field`  (lines 176–177)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Figures out which field in a Zendesk response contains the actual list of records for a stream. Most endpoints use the stream name, but a few use different names, such as policies or audits.

**Data flow**: It receives a StreamSpec. It checks whether that stream has a special response field name in the override table. It returns either the special name or, if there is no override, the stream’s own name.

**Call relations**: ZendeskConnector._paginate_default uses this before reading ordinary paginated endpoints. That lets the default paging logic work across endpoints whose response shapes are slightly different.

*Call graph*: called by 1 (_paginate_default).


##### `ZendeskConnector._cursor_to_unix`  (lines 180–192)

```
def _cursor_to_unix(cursor: str | None) -> int
```

**Purpose**: Converts the saved sync position into the Unix timestamp format Zendesk expects. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor value, which may be empty, already numeric, or an ISO-style date string. Empty or unparseable values become 0, meaning the sync starts from the beginning. Numeric strings become integers, and date strings are parsed into timestamps. The function returns the timestamp as an integer.

**Call relations**: The incremental paginators call this before building Zendesk URLs. ZendeskConnector._paginate_incremental_cursor, ZendeskConnector._paginate_ticket_comments, and ZendeskConnector._paginate_user_identities all rely on it so they can ask Zendesk for records changed since the last saved point.

*Call graph*: called by 3 (_paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (fromisoformat).


##### `ZendeskConnector._next_page_path`  (lines 195–204)

```
def _next_page_path(next_page: str | None) -> str | None
```

**Purpose**: Turns Zendesk’s full next-page URL into the path-and-query form this connector uses for requests. This keeps pagination safe and focused on the Zendesk API path rather than reusing an entire external URL.

**Data flow**: It receives a next-page URL, or nothing. If there is no URL or no path inside it, it returns nothing. Otherwise it extracts the URL path and keeps the query string if one exists, then returns that shorter request path.

**Call relations**: All paginator methods use this after each page is read. Zendesk returns links such as after_url or next_page, and this helper converts those links into the next request path so the paginator can keep walking through the result set.

*Call graph*: called by 4 (_paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities); 1 external calls (urlparse).


##### `ZendeskConnector.paginate`  (lines 206–230)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct paging strategy for a requested Zendesk stream and yields batches of records. It is the main doorway the rest of the source-sync system uses to read Zendesk data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the previous sync left off. It checks the stream name, delegates to the special ticket comment reader, the user identity reader, the incremental cursor reader, or the default page reader, then yields each batch those helpers produce. If Zendesk rejects the request with an authorization or permission error, it turns that into StreamSkipped so the sync can skip that stream with a clear explanation.

**Call relations**: The broader RestConnector framework calls this when it needs records for one Zendesk stream. This method acts like a traffic director: ticket_comments go to ZendeskConnector._paginate_ticket_comments, users_identities go to ZendeskConnector._paginate_user_identities, high-volume incremental streams go to ZendeskConnector._paginate_incremental_cursor, and everything else goes to ZendeskConnector._paginate_default.

*Call graph*: calls 5 internal fn (__init__, _paginate_default, _paginate_incremental_cursor, _paginate_ticket_comments, _paginate_user_identities).


##### `ZendeskConnector._paginate_incremental_cursor`  (lines 232–253)

```
async def _paginate_incremental_cursor(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads high-volume Zendesk streams using Zendesk’s cursor-based incremental export API. This is meant for large data sets where the sync should resume from a point in time instead of starting over.

**Data flow**: It receives an HTTP client, a stream description, and a cursor. It converts the cursor into a Unix timestamp, builds the initial incremental export URL, then repeatedly fetches pages. From each response it pulls the records for that stream, enriches them with sideloaded data when configured, yields non-empty batches, and follows Zendesk’s next cursor link until Zendesk says the stream has ended.

**Call relations**: ZendeskConnector.paginate sends tickets, users, organizations, and ticket_metric_events here. This method uses ZendeskConnector._cursor_to_unix to build the starting request, _apply_sideload to enrich ticket records when users are included, and ZendeskConnector._next_page_path to follow Zendesk’s after_url or next_page links.

*Call graph*: calls 3 internal fn (_cursor_to_unix, _next_page_path, _apply_sideload); called by 1 (paginate).


##### `ZendeskConnector._paginate_default`  (lines 255–265)

```
async def _paginate_default(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads ordinary Zendesk endpoints that return a page of records plus a next-page link. This is the fallback reader for streams that do not need special incremental or fan-out behavior.

**Data flow**: It receives an HTTP client and a stream description. It builds the first API path from the stream’s source object, asks Zendesk for a page, finds the correct response field containing records, yields any records it finds, and then follows the next_page link until there are no more pages.

**Call relations**: ZendeskConnector.paginate calls this for most simpler streams. It depends on ZendeskConnector._data_field to know where records live in each response and on ZendeskConnector._next_page_path to continue from one Zendesk page to the next.

*Call graph*: calls 2 internal fn (_data_field, _next_page_path); called by 1 (paginate).


##### `ZendeskConnector._paginate_ticket_comments`  (lines 267–299)

```
async def _paginate_ticket_comments(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Extracts ticket comments from Zendesk’s ticket event feed. Zendesk represents comments as child events inside larger ticket events, so this function turns those nested comment events into their own stream of comment records.

**Data flow**: It receives an HTTP client and a cursor. It builds an incremental ticket-events request using the cursor timestamp and asks Zendesk to include comment events. For each page, it looks through ticket events, keeps only child events whose type is Comment, copies each comment into a new record, adds the parent ticket_id, normalizes numeric creation times into readable UTC timestamps, yields comment batches, and follows the next cursor link until the feed ends.

**Call relations**: ZendeskConnector.paginate calls this only for the ticket_comments stream. It uses ZendeskConnector._cursor_to_unix to start at the right point in time and ZendeskConnector._next_page_path to follow Zendesk’s after_url or next_page links.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate); 1 external calls (fromtimestamp).


##### `ZendeskConnector._paginate_user_identities`  (lines 301–326)

```
async def _paginate_user_identities(self, client: httpx.AsyncClient, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads identity records for users, such as email or login identities, by first finding changed users and then asking Zendesk for each user’s identities. This is needed because identities are exposed under individual user URLs rather than as one simple global list.

**Data flow**: It receives an HTTP client and a cursor. It reads users from Zendesk’s incremental user export starting at the cursor time. For each valid user with an ID, it requests that user’s identities page by page and yields any identity records found. After finishing the users on one page, it follows the next incremental user page until Zendesk reports the end of the user stream.

**Call relations**: ZendeskConnector.paginate calls this for the users_identities stream. It relies on ZendeskConnector._cursor_to_unix to begin from the previous sync point and ZendeskConnector._next_page_path both for paging through each user’s identities and for moving through the incremental user list.

*Call graph*: calls 2 internal fn (_cursor_to_unix, _next_page_path); called by 1 (paginate).

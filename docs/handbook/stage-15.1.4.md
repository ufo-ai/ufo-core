# Marketing, ads, social, forms, and tabular-data sources  `stage-15.1.4`

This stage is part of the system’s data intake work. It is a set of connectors, meaning small adapters that know how to talk to outside services and turn their replies into the project’s standard record format. Together, they let the system bring in marketing, advertising, social, form, and table-based data so it can be stored, synced, and searched later.

The marketing connectors read customer and campaign tools: ActiveCampaign, Klaviyo, and Mailchimp. They know which objects exist, such as profiles, lists, campaigns, events, and nested collections, and how to move through paged API results. The advertising connectors read Facebook Ads and Google Ads, including accounts, campaigns, ad groups, ads, and performance numbers. Instagram reads business accounts, posts, stories, and analytics through Facebook’s API. Airtable discovers bases, tables, and rows, acting like a bridge to flexible spreadsheet-like data. Typeform reads forms, responses, workspaces, and related assets. Each file handles login, API calls, paging, and record shaping for its own service.

## Files in this stage

### Marketing automation platforms
Connectors for CRM, email, lifecycle marketing, audience, campaign, event, and catalog data sources.

### `extensions/sources/ufo_ext_sources/providers/active_campaign.py`

`io_transport` · `during source sync when reading ActiveCampaign streams`

ActiveCampaign stores many kinds of business data: contacts, lists, campaigns, deals, accounts, tags, custom fields, users, and more. This file is the connector that turns those remote API endpoints into named streams the rest of the system can sync. Without it, the platform would not know which ActiveCampaign data exists, where to fetch it, or how to deal with ActiveCampaign’s particular API shape.

The file first lists every supported stream and describes each one with a StreamSpec. A stream is one category of records, like “contacts” or “deals.” The StreamSpec says things like the stream’s name, its primary key, and which date field can be used to tell whether a record changed.

ActiveCampaign’s API returns lists in a predictable wrapper, like a box labeled “contacts” that contains the contact rows. The connector keeps a map from the system’s stream names to ActiveCampaign’s URL paths and response labels, because some names use camelCase in the API.

The ActiveCampaignConnector class then supplies the runtime behavior. It builds an HTTP client with the right Api-Token header, finds the correct endpoint for a stream, adds an incremental “changed after this time” filter when ActiveCampaign supports it, and walks through pages of 100 records at a time. If ActiveCampaign refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of pretending the sync succeeded.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the small description card for one ActiveCampaign data stream. It keeps the long stream list readable by filling in common defaults, such as using “id” as the primary key and “cdate” as the created-at field.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, and whether the stream is considered canonical. It combines those inputs with ActiveCampaign defaults, decides whether the cursor field should also count as the updated-at field, and returns a StreamSpec object for the rest of the connector to use.

**Call relations**: This helper is used while the file is loaded to build ACTIVECAMPAIGN_STREAMS. Its main handoff is to StreamSpec.__init__, which creates the actual stream description object consumed later by ActiveCampaignConnector.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to ActiveCampaign, with authentication set up in the way ActiveCampaign expects. ActiveCampaign uses an Api-Token header, not the more common bearer-token header.

**Data flow**: It receives a base URL and a credential. If the credential already has a custom transport, such as a broker or proxy path, it leaves that setup alone and delegates to the parent connector. Otherwise it expects the credential’s bearer value to contain the API key, wraps that key into an Api-Token header, and returns a configured async HTTP client. If no key is present, it raises an error so the sync fails clearly instead of making anonymous requests.

**Call relations**: The broader connector setup calls this when it needs a client for an ActiveCampaign tenant. This method either hands control back to the base RestConnector unchanged for proxy-style credentials, or creates a new Credential carrying the ActiveCampaign-specific header before asking the parent connector to build the client.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function translates an internal stream name into the URL piece and response-envelope key used by ActiveCampaign. It exists because some streams have different spelling in the system and in the API, such as snake_case internally and camelCase in ActiveCampaign.

**Data flow**: It receives a StreamSpec. It looks up the stream name in the stream-path map. If there is a special mapping, it returns the API path segment and response key from that map; otherwise it falls back to using the stream name for both.

**Call relations**: ActiveCampaignConnector.paginate calls this at the start of a fetch so it knows which endpoint to request and which field in the JSON response contains the records. It is a small lookup step before the connector starts reading pages.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one ActiveCampaign stream page by page. It is the main read loop for this connector: it builds the request, applies a “changed after” filter when possible, yields each batch of records, and turns permission failures into a clear skipped-stream signal.

**Data flow**: It receives an async HTTP client, a stream description, and an optional cursor value from a previous sync. It resolves the correct API path, starts with a limit of 100 records per request, and, if the stream supports server-side incremental filtering, adds a filter asking ActiveCampaign for records changed after the cursor. It then asks the base REST connector to walk offset-based pages and yields each page of records. If ActiveCampaign replies with 401 or 403, it raises StreamSkipped with an explanation; other HTTP errors are allowed to keep bubbling up.

**Call relations**: During a sync, the source runtime calls this to read records for a particular stream. It first calls ActiveCampaignConnector._resolve_stream_segment to translate the stream name, then relies on the inherited offset-page reader to perform the repeated requests. If access is refused, it creates a StreamSkipped error so the larger sync can treat that stream as unavailable because of missing scope or an invalid key.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/providers/klaviyo.py`

`io_transport` · `source sync`

Klaviyo sends its data in a very specific shape: each item has an id, a type, a block of attributes, optional relationships to other items, and pagination links for the next page of results. This connector is the adapter between that Klaviyo world and this project’s standard source-sync machinery. Without it, the system would not know which Klaviyo objects to read, how to authenticate, how to ask only for changed records, or how to flatten Klaviyo’s nested records into simple fields.

The file first defines the list of Klaviyo streams, such as profiles, campaigns, events, templates, catalog items, and webhooks. A stream is a description of one kind of thing to sync, including its main id field and, when available, the timestamp field used for incremental syncing. Incremental syncing means “only fetch records changed since the last successful run,” like checking only the mail that arrived after yesterday.

The KlaviyoConnector then adds Klaviyo-specific behavior. It creates an HTTP client with Klaviyo’s required headers, builds first-page query parameters, follows Klaviyo’s next-page links, and skips streams cleanly when the API says the current key lacks permission. It also flattens nested records so important fields, like a profile’s email consent, a campaign’s subject line, or an event’s metric name, are easy for the rest of the system to use.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a standard stream description for one Klaviyo resource. It keeps the large stream list readable by filling in common defaults, such as using id as the main key and updated as the usual change-tracking field.

**Data flow**: It takes a stream name plus optional details like the Klaviyo API object name, the primary key, and timestamp fields. It combines those choices into a StreamSpec object, which is the shared description the sync framework uses to know what to fetch and how to track progress.

**Call relations**: At file load time, the stream list is built by repeatedly using this helper. Each call produces a StreamSpec that later becomes part of KlaviyoConnector.streams_list, so the connector knows all the Klaviyo resources it can read.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Klaviyo and adds the headers Klaviyo requires. In plain terms, it prepares the “caller ID” and API version label that must be attached to each request.

**Data flow**: It receives a base URL and a credential. It starts with the normal client made by the parent REST connector, adds Klaviyo’s pinned revision header, and, when a private key is available directly, adds the Authorization header in Klaviyo’s required format. It returns the prepared async HTTP client.

**Call relations**: This fits into connector setup, before any data is fetched. The wider REST connector machinery asks this class for a client, and this method customizes that client so later pagination requests are accepted by Klaviyo.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Klaviyo’s full next-page URL into the path-and-query form needed by the already configured HTTP client. It is a small translation step that lets the connector follow pagination links safely.

**Data flow**: It receives a next link, which may be a full URL or may be missing. If there is no usable link, it returns None. Otherwise, it parses the URL, keeps only the path and query string, and returns that smaller request path for the next API call.

**Call relations**: During pagination, KlaviyoConnector.paginate reads links.next from each API response and hands it here. The returned path becomes the next loop target; when this helper returns None, the pagination loop ends.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This chooses the correct Klaviyo timestamp field for incremental syncing. Different Klaviyo resources use different names for “when this changed,” so this helper prevents the rest of the code from guessing.

**Data flow**: It receives a StreamSpec. If the stream is events, it returns datetime; if it is one of the resources that uses updated_at, it returns updated_at; otherwise, it returns updated. The result is used as the field name for filtering and sorting API requests.

**Call relations**: This belongs to the first-request query-building path. It supports the logic that prepares incremental sync filters, so each stream asks Klaviyo for records in the right time order using the right timestamp field.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for the first page of a Klaviyo stream. It sets the page size, adds incremental filters when a previous cursor exists, and asks for a few extra useful fields on certain streams.

**Data flow**: It receives a stream description and an optional cursor value from the previous sync. It creates a dictionary of API parameters: page size always, sort order when the stream has a cursor field, a greater-or-equal filter when a cursor is present, and special additions for profiles and events. It returns that parameter dictionary.

**Call relations**: KlaviyoConnector.paginate calls this before making the first request for a stream. Later pages do not use these parameters, because Klaviyo’s next-page link already contains the needed paging information.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This safely extracts the id of a related Klaviyo object from a nested relationships block. It is used because Klaviyo often stores links like “this event belongs to this profile” several levels deep.

**Data flow**: It receives a relationships value and the relationship name to look for. It checks each layer carefully, because Klaviyo may omit empty relationships or return unexpected shapes. If it finds relationships[key].data.id, it returns that id as a string; otherwise, it returns None.

**Call relations**: KlaviyoConnector.flatten uses this when turning nested Klaviyo records into flat records. It helps expose relationship ids such as profile_id, metric_id, or parent_list_id without making the main flattening code repeat defensive checks.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–206)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This converts one Klaviyo API record from its nested JSON:API shape into a flatter dictionary that the rest of the system can store, compare, and search more easily. It also pulls out a few especially useful details that would otherwise stay buried.

**Data flow**: It receives a raw Klaviyo record and the stream it came from. It starts with the record id and resource type, copies top-level attributes into the flat output, removes list and segment profile counts so membership changes do not falsely look like record changes, and then adds stream-specific fields. The result is a simpler record with important values available as direct keys.

**Call relations**: This is the main cleanup step after records are fetched. For profiles it delegates consent-related extraction to _flatten_profile, for campaigns it delegates message and audience extraction to _flatten_campaign, and for relationships it uses _lift_relationship_id.

*Call graph*: calls 3 internal fn (_flatten_campaign, _flatten_profile, _lift_relationship_id).


##### `KlaviyoConnector._flatten_profile`  (lines 209–223)

```
def _flatten_profile(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This pulls email marketing consent and suppression information out of a profile’s nested subscription data. Those fields are important for understanding whether a person can be contacted and why they may be blocked.

**Data flow**: It receives the flat record being built and the original attributes block. It looks inside subscriptions → email → marketing, then copies the consent value if present. It also reads suppression information, accepting either a list or a string, and writes a simple email_suppression value into the flat record when it can.

**Call relations**: KlaviyoConnector.flatten calls this only for profile records. It is a focused helper so the main flattening path can stay readable while still preserving profile-specific marketing status.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector._flatten_campaign`  (lines 226–244)

```
def _flatten_campaign(flat: dict[str, Any], attrs: Any) -> None
```

**Purpose**: This pulls useful campaign message details into easy-to-read fields, such as subject line, sender label, sender email, and primary list id. These are the details people usually need when recalling or searching campaign records.

**Data flow**: It receives the flat record being built and the original attributes block. It looks first inside the campaign audience and message settings for subject and sender information, then looks for the first included audience list. If those nested values are absent, it falls back to similarly named fields directly on the attributes block.

**Call relations**: KlaviyoConnector.flatten calls this for campaign records. It concentrates the campaign-specific extraction rules in one place, while the outer flatten method coordinates which helper applies to which stream.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.paginate`  (lines 246–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads all pages for one Klaviyo stream, yielding batches of records as they arrive. It knows how to start with the right query, follow Klaviyo’s next-page links, enrich events with metric names, and report permission failures as skipped streams instead of crashing the whole sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It begins at /api/<stream object>, builds the first query, sends requests, reads the data array from each response, optionally copies metric names from included event data into event attributes, yields non-empty record batches, and follows links.next until there are no more pages. If Klaviyo returns 401 or 403, it raises StreamSkipped with a clear explanation; other HTTP errors continue upward.

**Call relations**: This is the connector’s main read loop for Klaviyo API data. It uses _initial_query to prepare the first request and _next_path to move from one page to the next. When Klaviyo refuses a stream because the key lacks scope, it hands that situation to the sync framework through StreamSkipped so the run can record a skip.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/providers/mailchimp.py`

`io_transport` · `source sync runs`

Mailchimp stores marketing data in several places: audiences, members, campaigns, reports, segments, tags, interests, unsubscribe records, and email activity. Some of these are simple top-level lists. Others are tucked under a parent, such as members inside an audience list, or unsubscribe records inside a campaign report. This file is the map and walking route for all of that.

The connector defines the available streams, which are the named sets of records the wider system can sync. For each stream it records useful facts such as the main identifier, creation time field, and optional cursor field. A cursor is a saved “last seen” value that lets a later sync ask Mailchimp only for newer records when Mailchimp supports that filter.

The main class, `MailchimpConnector`, is read-only. Its central `paginate` method acts like a dispatcher: given a stream name, it chooses the right route through the Mailchimp API. Some routes fetch one paged endpoint. Others first fetch all list IDs or report IDs, then fetch child records for each parent. When Mailchimp omits helpful context, the connector adds it back, such as stamping a member with its `list_id`. For email activity, Mailchimp returns a recipient with an array of actions; this connector turns each action into its own row and creates a stable ID so repeated syncs recognize the same event.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper creates one stream definition for a Mailchimp resource. A stream definition tells the sync system what the resource is called, what field identifies each record, and which date fields can be used for incremental syncing.

**Data flow**: It receives a stream name and optional metadata such as source object, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then builds and returns a `StreamSpec`, which is the system’s compact description of one syncable Mailchimp collection.

**Call relations**: This helper is used while the file is being loaded to build `MAILCHIMP_STREAMS`. It hands the finished stream descriptions to the connector class through `streams_list`, so the rest of the source framework knows what Mailchimp data can be requested.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.record_identity`  (lines 148–155)

```
def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This function decides the stable identity for a record. It has a special rule for unsubscribe records because an email ID alone is not enough to distinguish the same email across different campaigns.

**Data flow**: It receives one record and the stream it came from. For most streams, it uses the standard identity logic from the base connector. For `unsubscribes`, it reads `campaign_id` and `email_id`; if both exist, it returns them joined as `campaign_id:email_id`, and if either is missing, it returns no identity.

**Call relations**: The broader sync system calls this when it needs to recognize whether a record is new, changed, or the same as one already seen. This method customizes that identity step only where Mailchimp’s unsubscribe data needs extra context.


##### `MailchimpConnector.flatten`  (lines 157–163)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly reshapes records before the rest of the system stores or compares them. For member records, it creates a common `created_at` value from Mailchimp’s signup or opt-in timestamps.

**Data flow**: It receives a record and its stream description. If the stream is `list_members` or `segment_members`, it returns a copy of the record with `created_at` set from `timestamp_signup`, or from `timestamp_opt` if signup time is missing. For all other streams, it returns the record unchanged.

**Call relations**: The source framework uses this after records are fetched. It makes member records look more like the standard shape expected by the rest of the system, without changing unrelated Mailchimp data.


##### `MailchimpConnector._data_field`  (lines 166–167)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This helper answers a common Mailchimp question: which JSON key contains the actual list of records for this stream. Mailchimp often wraps records under names like `lists`, `members`, or `emails` rather than returning a bare array.

**Data flow**: It receives a stream description. It looks up the stream name in the file’s data-field map and returns the matching wrapper key; if there is no special entry, it falls back to the stream name itself.

**Call relations**: Pagination helpers call this before asking the base REST connector to read pages. It gives `_paginate_top_level`, `_paginate_per_list`, and `_paginate_per_report` the exact field where records should be pulled from each Mailchimp response.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 170–177)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper converts the system’s saved cursor into the query parameter name Mailchimp expects. It lets later syncs ask Mailchimp for only records after a known time, when that stream supports it.

**Data flow**: It receives a stream description and an optional cursor value. If either the cursor or stream cursor field is missing, it returns an empty parameter set. If the cursor field has a known Mailchimp parameter name, it returns a one-item dictionary such as `since_last_changed: <cursor>`; otherwise it returns nothing.

**Call relations**: The pagination helpers call this when building requests for top-level, per-list, per-report, and segment-member streams. It feeds the resulting filters into the page fetches so the connector can avoid re-reading older data where Mailchimp allows it.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 179–235)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Mailchimp data. Given a stream, it chooses the correct fetching strategy: simple endpoint, per-list child data, per-report child data, or a deeper nested walk.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching private pagination method, and yields pages of records as they arrive. If Mailchimp returns 401 or 403, meaning the request is unauthorized or forbidden, it raises `StreamSkipped` so the sync can skip that stream with a clear reason; other HTTP errors are re-raised.

**Call relations**: The source framework calls this when it wants records for one Mailchimp stream. This function then hands the work to `_paginate_top_level`, `_paginate_per_list`, `_paginate_interests`, `_paginate_segment_members`, `_paginate_per_report`, or `_paginate_email_activity`, depending on where Mailchimp stores that data.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 237–248)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Mailchimp collections that live at one direct API endpoint, such as lists, campaigns, automations, and reports. It keeps requesting pages until the base pagination helper is done.

**Data flow**: It receives an HTTP client, a stream, an API path, and an optional cursor. It finds the response field that contains records, builds any cursor filter parameters, and asks the base REST connector to walk through offset-based pages using Mailchimp’s `count` parameter. It yields each page of records unchanged.

**Call relations**: `paginate` calls this for top-level streams. This function depends on `_data_field` to know where records are in the response and `_cursor_params` to add incremental filters when possible.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 250–267)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable page walker for nested Mailchimp endpoints. It is used whenever the connector already knows a specific child endpoint, such as members under one list or email activity under one report.

**Data flow**: It receives an HTTP client, a concrete API path, the response field containing records, and optional base query parameters. It asks the base REST connector to fetch offset-based pages with Mailchimp’s page size and `count` parameter, then yields each page of child records.

**Call relations**: Several higher-level pagination methods call this after they have built a parent-specific path. It is the common mechanism behind per-list, per-report, interest, segment-member, and email-activity fetching.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 269–277)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This helper reads a paged Mailchimp collection and yields only the `id` values from its records. It is used when later requests need parent IDs before they can fetch child records.

**Data flow**: It receives an HTTP client, an API path, and the response field where records live. It pages through that endpoint, checks each row, and yields the row’s `id` as text when present. Rows without an ID or non-dictionary rows are ignored.

**Call relations**: `_list_ids` and `_report_ids` call this to avoid duplicating the same ID-extraction logic. Those parent ID streams then feed the nested pagination methods that need to visit each list or report in turn.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 279–281)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function produces the IDs of all Mailchimp audience lists. Those IDs are needed because many Mailchimp resources, such as members and segments, can only be fetched inside a specific list.

**Data flow**: It receives an HTTP client. It calls `_ids` on the `/3.0/lists` endpoint and yields each list ID that `_ids` finds.

**Call relations**: `_paginate_per_list`, `_paginate_interests`, and `_paginate_segment_members` call this before fetching list-based child data. It supplies the parent list IDs that those methods plug into child API paths.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 283–285)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function produces the IDs of all Mailchimp campaign reports. Those IDs are needed before the connector can fetch report-specific details like unsubscribes and email activity.

**Data flow**: It receives an HTTP client. It calls `_ids` on the `/3.0/reports` endpoint and yields each report ID that `_ids` extracts.

**Call relations**: `_paginate_per_report` and `_paginate_email_activity` call this first. It supplies the campaign report IDs that those methods use to build report-child API paths.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 287–308)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that exist under every Mailchimp audience list, such as list members, segments, tags, and interest categories. It visits each list and fetches the chosen child records from that list.

**Data flow**: It receives an HTTP client, stream description, child path name, optional cursor, and an optional field name for stamping the parent ID. It gets the right response data field and cursor filters, loops through all list IDs, builds a safe URL for each list’s child endpoint, fetches child pages, adds the parent `list_id` when requested, and yields each page.

**Call relations**: `paginate` calls this for several list-based streams. It uses `_list_ids` to find parents, `_paginate_child` to read each child endpoint, `_data_field` to locate records in responses, `_cursor_params` for incremental filters, and URL quoting to safely place Mailchimp IDs into paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 310–332)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads interests, which are nested two levels deep in Mailchimp: first under a list, then under an interest category. It walks that hierarchy so interests can be synced as normal records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, though the cursor is not used in the current logic. It loops through list IDs, fetches each list’s interest categories, then for each category with an ID fetches its interests. Before yielding interest pages, it adds `list_id` and `category_id` to each interest record when missing.

**Call relations**: `paginate` calls this for the `interests` stream. It relies on `_list_ids` for the first level of parents and `_paginate_child` for both the category pages and the interest pages, using URL quoting when inserting IDs into API paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 334–356)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads members inside each segment inside each audience list. It is needed because segment membership is not a single global Mailchimp endpoint; it must be discovered list by list and segment by segment.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It builds cursor filter parameters, loops over all list IDs, fetches that list’s segments, then fetches members for each segment that has an ID. It adds `list_id` and `segment_id` to member records when missing and yields each page of members.

**Call relations**: `paginate` calls this for the `segment_members` stream. It uses `_list_ids` to find lists, `_paginate_child` to fetch segment and member pages, `_cursor_params` to add incremental filters for member changes, and URL quoting to build safe nested paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 358–378)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that live under every campaign report. In this file it is used for unsubscribe records, which Mailchimp exposes report by report.

**Data flow**: It receives an HTTP client, stream description, child path, optional cursor, and an optional parent stamp field. It finds the response data field and cursor filters, loops through all report IDs, builds each report-child path, fetches pages from that endpoint, adds the report ID under the requested field such as `campaign_id`, and yields each page.

**Call relations**: `paginate` calls this for per-report streams such as `unsubscribes`. It gets report IDs from `_report_ids`, reads pages through `_paginate_child`, uses `_data_field` and `_cursor_params` to shape the request, and quotes IDs before putting them into URLs.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 380–412)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads email activity from each campaign report and turns Mailchimp’s nested activity arrays into individual event records. This matters because the sync system needs one clear row per action, not one recipient row containing many actions.

**Data flow**: It receives an HTTP client and optional cursor. If a cursor exists, it sends it as Mailchimp’s `since` filter. It loops through report IDs, fetches each report’s email activity pages, then for each recipient record copies the recipient-level fields except `activity`. For every action inside `activity`, it merges recipient data and action data into one row, adds `campaign_id`, creates a stable synthetic `id` from email ID, action, and timestamp when Mailchimp does not provide one, and yields only non-empty exploded pages.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to visit each campaign report and `_paginate_child` to fetch each report’s email activity, then performs the special transformation needed before handing records back to the sync framework.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Advertising and social analytics
Connectors for paid advertising and social business platforms that expose accounts, campaigns, creative objects, posts, stories, and performance metrics.

### `extensions/sources/ufo_ext_sources/providers/facebook_ads.py`

`io_transport` · `source sync request handling`

Facebook Ads data is not available as one simple download. It lives behind Facebook's Graph API, split by ad account and then further split into campaigns, ad sets, ads, and insight reports. This file is the connector that knows how to walk that structure safely.

The file defines the available streams first: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is a named kind of data the sync system can ask for. Some streams also have a cursor field, meaning a timestamp used as a bookmark so future syncs can fetch only newer changes.

The main class, `FacebookAdsConnector`, reads from Facebook but does not write anything back. It starts by listing the user's ad accounts. For account-owned data, it loops through each account and asks Facebook for that account's campaigns, ad sets, ads, or insight rows. Because Facebook returns results in pages, it follows Facebook's `paging.next` link until there are no more pages, like following “next page” links in search results.

For campaigns, ad sets, and ads, it filters out records older than the saved cursor. For ad insights, it asks for daily per-ad metrics, starting from the saved date or from the last 90 days on a first run. It also creates a stable row id from the account, campaign, ad set, ad, and date, because insight rows do not naturally arrive with one unique id.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Facebook API collection across all of its result pages. Someone uses this when they need every record from an endpoint, not just the first page Facebook returns.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks Facebook for that page, reads the `data` list from the JSON response, yields that list if it is not empty, then looks for Facebook's `paging.next` URL and repeats. The output is a sequence of record batches, one batch per page.

**Call relations**: This is the shared page-turning helper for the connector. `_accounts`, `_account_children`, and `_insights` all call it when they need to read a Facebook collection, and it relies on `records_at` to pull the actual list of records out of the response body.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Lists the Facebook ad accounts available to the authenticated user. This matters because most other Facebook Ads data is stored under a specific ad account.

**Data flow**: It starts with a fixed list of account fields to request, then calls `_paged` on `/me/adaccounts`. As each page arrives, it adds those account records to one list. It returns the full list of ad account dictionaries.

**Call relations**: This is the connector's starting point for account-scoped data. `paginate` calls it directly for the `ad_accounts` stream, while `_account_children` and `_insights` call it first so they know which accounts to query next.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads account-owned objects such as campaigns, ad sets, and ads across every ad account. It also applies the saved cursor so repeat syncs can skip older records.

**Data flow**: It receives an HTTP client, the stream being requested, and an optional cursor timestamp. It chooses the right Facebook fields for that stream, gets all ad accounts, then queries each account's matching collection. For each page, it filters out records whose cursor field is not newer than the saved cursor, adds account context such as the ad account id and name, and yields the remaining records.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. Inside, it first asks `_accounts` for the account list, then uses `_paged` to fetch each account's child records, and uses `with_context` so downstream storage can remember which ad account each record came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads daily advertising performance rows, such as impressions, clicks, spend, and reach, at the ad level. It is used for report-like data where each row describes one ad on one day.

**Data flow**: It receives an HTTP client and an optional cursor date. It builds a Facebook insights request for daily ad-level metrics. If a cursor exists, it asks for data from that date through today; otherwise it asks for the last 90 days. It then loops through each ad account, fetches paged insight rows, adds the ad account id, creates a stable synthetic `id` from the account, campaign, ad set, ad, and date, and yields rows in batches.

**Call relations**: `paginate` calls this for the `ads_insights` stream. It depends on `_accounts` to know which accounts to report on, `_paged` to follow Facebook's pagination, `json.dumps` to format Facebook's time range parameter, and the current date to set the end of a cursor-based reporting window.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right reading path for whichever Facebook Ads stream the sync system asks for. It is the main doorway the broader connector framework uses to pull records from this source.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is ad accounts, it returns the account list. If it is campaigns, ad sets, or ads, it delegates to `_account_children`. If it is ad insights, it delegates to `_insights`. If the stream name is unknown, it raises `StreamSkipped` to clearly say this connector does not implement that stream.

**Call relations**: The source-sync framework calls `paginate` when it needs records for a stream. `paginate` then routes the request to `_accounts`, `_account_children`, or `_insights`, depending on the stream name, and passes each resulting page of records back to the framework.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Lightly normalizes records before the rest of the system stores them. For campaigns, it gives common fields predictable names and prefers Facebook's effective status over the raw status when available.

**Data flow**: It receives one record and the stream it belongs to. For campaign records, it returns a copied dictionary with `status` set from `effective_status` if present, and `created_at` copied from Facebook's `created_time`. For all other streams, it returns the record unchanged.

**Call relations**: This function is used after records have been fetched, as part of the connector's shaping step. Unlike the paging functions, it does not call other helpers; it simply prepares individual records so downstream consumers see a more consistent campaign shape.


### `extensions/sources/ufo_ext_sources/providers/googleads.py`

`io_transport` · `source sync`

Google Ads does not simply expose one easy table of data. To read it, the connector must ask Google’s API for each advertiser account the user can access, then run a Google Ads Query Language query, which is like SQL for Google Ads, against each account. This file is that translator.

The file defines several streams: customers, campaigns, ad groups, ads, campaign metrics, and customer client accounts. Each stream describes what kind of object is being read and which field uniquely identifies each record. The `GoogleAdsConnector` then builds the HTTP client, adds the required Google Ads developer token, discovers accessible customer IDs, and sends search requests for each stream.

A key detail is that Google Ads needs both OAuth access and a developer token. OAuth proves who the user is; the developer token proves the app is approved to call Google Ads. If the token is missing, or Google refuses access with a 401 or 403 status, this connector skips the stream instead of crashing the whole sync.

Finally, the connector flattens some nested Google Ads responses into simpler records. For example, campaign metrics become one record per customer, campaign, and date. Without this file, the system could not import Google Ads data in a consistent, recallable shape.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token in the environment. Google Ads requires this token on top of normal sign-in permission, so the connector cannot safely call the API without it.

**Data flow**: It reads the `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` environment variable first, then falls back to `GOOGLE_ADS_DEVELOPER_TOKEN`. If it finds a value, it returns that token. If neither exists, it raises `StreamSkipped`, meaning this Google Ads stream should be skipped rather than treated as a hard failure.

**Call relations**: When `_make_client` prepares the HTTP client, it calls `_developer_token` so every Google Ads request includes the required approval token. If the token is missing, `_developer_token` stops that setup by raising `StreamSkipped`.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Google Ads and adds the special headers Google Ads expects. A header is extra information sent with every web request, like showing a pass at a security desk.

**Data flow**: It receives a base URL and a credential object, asks the parent connector to build the basic authenticated client, then adds the developer token header. It also checks for an optional login customer ID in the environment, removes dashes from it, and adds it as another header if present. The result is an HTTP client ready to call Google Ads.

**Call relations**: This is part of the connector setup path. It calls `_developer_token` before any Google Ads request can be made, because later methods such as `_customer_ids` and `_search_stream` depend on this client having the right headers.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credentials can access. Those customer IDs are the starting points for all later data queries.

**Data flow**: It sends a request to Google Ads’ accessible-customers endpoint using the prepared HTTP client. It reads the returned resource names, keeps only strings that look like `customers/123`, strips off the `customers/` part, and returns a list of plain customer ID strings.

**Call relations**: `_query_each_customer` calls this first so it knows which accounts to query. The returned IDs are then passed one by one into `_search_stream`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one Google Ads query for one customer account and collects the result rows. It is the low-level step that actually asks for campaigns, ads, metrics, or whichever stream is being synced.

**Data flow**: It receives an HTTP client, a customer ID, and a query string. It posts that query to Google Ads’ `searchStream` endpoint for that customer. Google returns batches of results, so the function walks through those batches, pulls out each row, ignores malformed pieces, and returns a flat list of row dictionaries.

**Call relations**: `_query_each_customer` calls `_search_stream` after it gets each customer ID from `_customer_ids`. `_search_stream` gives back the raw Google Ads rows that will later be yielded by pagination and possibly simplified by `flatten`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It is the bridge between “one query” and “all accounts this user can see.”

**Data flow**: It receives an HTTP client and a query. First it asks `_customer_ids` for all accessible accounts. For each customer ID, it calls `_search_stream` with the same query. If rows come back, it adds the customer ID onto every row and yields that group of rows as a page.

**Call relations**: `paginate` uses this helper for every implemented stream. `_query_each_customer` coordinates `_customer_ids` and `_search_stream`, then hands pages of customer-stamped records back to `paginate`.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function chooses the right Google Ads query for the stream being synced and yields pages of results. In plain terms, it decides what to ask Google Ads for and sends that question across all accessible accounts.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks where a previous sync left off. Based on the stream name, it builds a specific Google Ads query. For campaign metrics, it uses the cursor date if available, otherwise it looks back 90 days. It then calls `_query_each_customer` and yields each returned page. If the stream is unknown, it raises `StreamSkipped`. If Google rejects access with 401 or 403, it turns that into `StreamSkipped` with an explanatory message; other HTTP errors are re-raised.

**Call relations**: This is the main read path for the connector. The sync framework calls `paginate` when it wants records for a stream. `paginate` delegates the repeated per-customer work to `_query_each_customer`, and the records it yields are later available for flattening and storage.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function turns some nested Google Ads response objects into simpler records with the fields the sync system expects. It is like unpacking several boxes inside a package and putting the important labels on the outside.

**Data flow**: It receives one raw record and the stream it belongs to. For customers, it pulls out a stable ID and name. For campaigns, it pulls out resource name, name, status, and start date. For campaign metrics, it combines customer ID, campaign ID, and date into a unique metric ID and exposes common metric fields such as impressions, clicks, and cost. For other streams, it returns the record unchanged. It uses `dict_or_empty` so missing nested objects do not cause errors.

**Call relations**: After `paginate` has produced raw Google Ads rows, the broader source system can call `flatten` to normalize them. `flatten` does not fetch data itself; it reshapes the records so later sync steps can identify, compare, and store them consistently.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/providers/instagram.py`

`io_transport` · `source sync run`

Instagram business data is reached through Facebook Pages, so this connector starts with the Pages a credential can access and then follows each Page to its linked Instagram business account. From there it can read media posts, stories, and different kinds of insights, which are analytics measurements such as reach, impressions, replies, and views.

The file defines the streams this source can produce, such as pages, instagram_accounts, media, stories, media_insights, story_insights, and user_insights. A stream is a named lane of records that the sync system can pull from and store separately. Some streams are incremental, meaning the connector uses a saved cursor, like a bookmark, to avoid rereading old items.

The connector also knows how Facebook Graph API pagination works. The API returns a list under data and may include a paging.next URL for the next batch. The helper for that is like following “next page” links in a photo album until there are no more.

Important behavior: if the whole account-level request is refused because the token is invalid or lacks permission, the stream is marked skipped rather than failed. But if one individual media or story object refuses insight access, that object is quietly skipped so the rest of the sync can continue.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Facebook Graph API collection that may span several pages. It follows the API’s “next” link and yields each non-empty batch of records.

**Data flow**: It starts with an API path and optional query parameters. For each request, it reads the JSON response, pulls records out of the data field, yields that list if it contains anything, then follows paging.next for the next request. When there is no next link, it stops.

**Call relations**: This is the low-level paging helper used by _pages and _account_collection. Those higher-level methods decide what kind of Instagram data to request, while _paged does the repeated fetching and unpacking of each API page.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the current grant, including any linked Instagram business account details. This is the connector’s starting point because Instagram business accounts are discovered through Pages.

**Data flow**: It asks /me/accounts for Page IDs, Page names, and embedded Instagram business account fields. It gathers all returned pages from the paged API response into one list and returns that list.

**Call relations**: This function relies on _paged to walk through all Page results. _instagram_accounts uses it to find linked Instagram accounts, and _root_pages uses it when the requested stream is the raw pages stream.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, _root_pages).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the Instagram business accounts linked to the accessible Facebook Pages. It also remembers which Facebook Page each Instagram account came from.

**Data flow**: It receives no direct account list; instead, it calls _pages. From each Page, it looks for instagram_business_account, keeps only accounts with an ID, adds page_id and page_name, and deduplicates accounts by their Instagram account ID. It returns the unique accounts as a list.

**Call relations**: This is the bridge between Facebook Pages and Instagram-specific data. _account_collection uses it before reading media or stories, _user_insights uses it before reading account analytics, and _root_pages uses it when the instagram_accounts stream is requested.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _root_pages, _user_insights).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a collection that belongs to each Instagram business account, such as media posts or stories. It can also apply a cursor so only newer records are emitted.

**Data flow**: It first gets all linked Instagram accounts. For each valid account ID, it requests a child collection such as /{account_id}/media or /{account_id}/stories with the requested fields. Each returned batch may be filtered by a cursor field, such as timestamp, so older records are dropped. Remaining records are tagged with instagram_account_id and yielded.

**Call relations**: _stream_pages calls this when the media or stories stream is requested. It depends on _instagram_accounts to know which accounts to visit, _paged to walk through each account’s collection, and with_context to attach the account ID to every returned record.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (_stream_pages); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads analytics for individual objects, such as each media post or story. If one object cannot provide insights, it skips that object instead of stopping the whole sync.

**Data flow**: It receives batches of objects, usually media or stories. For each object with an ID, it asks /{object_id}/insights for the requested metrics. Each insight returned by the API is reshaped with a stable ID, the parent object ID, and the target stream name, then collected into output batches. Permission or missing-object errors for one object are ignored; other HTTP errors are re-raised.

**Call relations**: _insight_pages calls this after creating a source stream of media or stories. It uses the incoming objects as the list of things to measure, then emits insight records for the media_insights or story_insights stream.

*Call graph*: called by 1 (_insight_pages); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads daily account-level analytics for each Instagram business account. These are measurements about the account as a whole, such as impressions, reach, and profile views.

**Data flow**: It starts by finding Instagram accounts through _instagram_accounts. For each account, it requests daily insight metrics. The API returns each metric with a list of dated values, so the function turns each value into its own record, gives it a stable ID made from account, metric name, and end time, and filters out values at or before the cursor. It yields batches only when there are new rows.

**Call relations**: _stream_pages calls this when the user_insights stream is requested. It uses records_at to pull insight objects from the API response and list_or_empty to safely handle the metric values list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (_stream_pages); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–237)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the public stream-reading entry point for the connector. The sync runner asks it for batches from a named stream, and it yields those batches in order.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It delegates to _stream_pages to choose the right stream reader, then passes each yielded batch onward. If Facebook refuses access with a 401 or 403 status, it converts that into StreamSkipped so the run records a permission skip instead of a hard failure.

**Call relations**: The wider source framework calls paginate to pull records. Inside this file, _insight_pages also calls paginate to get media or story objects before reading their insights. paginate is the protective wrapper around _stream_pages, especially for permission-related failures.

*Call graph*: calls 2 internal fn (__init__, _stream_pages); called by 1 (_insight_pages).


##### `InstagramConnector._stream_pages`  (lines 239–273)

```
def _stream_pages(self, client: httpx.AsyncClient, name: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct reader for a requested stream name. It is the connector’s internal traffic director.

**Data flow**: It receives a stream name and cursor. For pages and instagram_accounts, it returns _root_pages. For media and stories, it returns _account_collection with the right API path, fields, and cursor field. For insight streams, it returns the matching insight reader. If the stream name is unknown, it raises StreamSkipped to say this stream is not implemented.

**Call relations**: paginate calls this whenever the sync runner requests a stream. _stream_pages then routes the work to _root_pages, _account_collection, _insight_pages, or _user_insights depending on what kind of data is being requested.

*Call graph*: calls 5 internal fn (__init__, _account_collection, _insight_pages, _root_pages, _user_insights); called by 1 (paginate).


##### `InstagramConnector._root_pages`  (lines 275–282)

```
async def _root_pages(self, client: httpx.AsyncClient, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Emits the simple top-level streams: Facebook Pages or linked Instagram accounts. These streams are gathered into one batch rather than walked account by account.

**Data flow**: It receives a stream name. If the name is pages, it calls _pages; otherwise it calls _instagram_accounts. If any records are found, it yields them as one list; if none are found, it yields nothing.

**Call relations**: _stream_pages calls this for the pages and instagram_accounts streams. It provides a thin adapter so those list-returning helpers can fit the connector’s batch-yielding stream interface.

*Call graph*: calls 2 internal fn (_instagram_accounts, _pages); called by 1 (_stream_pages).


##### `InstagramConnector._insight_pages`  (lines 284–293)

```
def _insight_pages(self, client: httpx.AsyncClient, source: str, metrics: str, name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Builds a stream of per-object insights, such as analytics for each media post or each story. It first gets the source objects, then asks for metrics about each one.

**Data flow**: It receives the source stream name, the metrics to request, and the output insight stream name. It finds the matching source stream definition, calls paginate to read all source objects without a cursor, and passes those objects into _object_insights. The result is an iterator of insight record batches.

**Call relations**: _stream_pages calls this for media_insights and story_insights. It connects paginate, which supplies media or stories, with _object_insights, which turns those objects into analytics records.

*Call graph*: calls 2 internal fn (_object_insights, paginate); called by 1 (_stream_pages).


### Tabular and form sources
Connectors for flexible base/table records and survey or form response systems.

### `extensions/sources/ufo_ext_sources/providers/airtable.py`

`io_transport` · `source sync`

Airtable is organized like a set of workspaces: a base contains tables, and each table contains records. There is no single Airtable endpoint that says “give me everything,” so this connector walks the structure in order. First it asks Airtable for the available bases. Then, for each base, it asks for its tables. Finally, for each table, it reads the records in pages of up to 100 items.

The file defines three streams of data: bases, tables, and records. A stream is a named kind of data the sync system can ask for. The connector adds useful context as it goes, such as the base ID on each table and both the base ID and table ID on each record. This is important because a record ID alone is not enough to understand where the record came from.

Airtable uses a paging token called an offset to continue reading large tables. The connector follows that token until all records are read. It only reads from Airtable; there is no write or update path here. Before records leave the connector, `flatten` reshapes them slightly so downstream code gets predictable fields such as `api_url`, `created_at`, and a safe `fields` dictionary.

#### Function details

##### `AirtableConnector._bases`  (lines 39–41)

```
async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper asks Airtable for the list of bases the account can access. A base is Airtable’s container for a group of related tables, like a small database.

**Data flow**: It receives an already prepared HTTP client. It sends a request to Airtable’s base metadata endpoint, reads the `bases` list out of the response, and returns that list as plain dictionaries for the rest of the connector to use.

**Call relations**: During `AirtableConnector.paginate`, this is the first discovery step. The pagination flow calls it when syncing bases directly, and also before syncing tables or records because those later steps need to know which bases exist.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `AirtableConnector._tables_for_base`  (lines 43–50)

```
async def _tables_for_base(self, client: httpx.AsyncClient, base: dict[str, Any]) -> list[dict[str, Any]]
```

**Purpose**: This helper finds the tables inside one Airtable base. It also labels each table with the base it came from, so the table is not separated from its origin later.

**Data flow**: It receives an HTTP client and one base record. It checks that the base has a usable ID. If not, it returns an empty list. If the ID is valid, it asks Airtable for that base’s tables, extracts the `tables` list from the response, adds context such as `base_id` and `base_name` to each table, and returns the enriched table list.

**Call relations**: This is called by `AirtableConnector.paginate` after bases have been discovered. It supports both the table stream, where tables are the final output, and the record stream, where the connector must discover tables before it can read their records.

*Call graph*: called by 1 (paginate); 2 external calls (records_at, with_context).


##### `AirtableConnector._records_for_table`  (lines 52–70)

```
async def _records_for_table(self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads the actual rows, or records, from one Airtable table. It follows Airtable’s paging system so large tables can be read in chunks instead of all at once.

**Data flow**: It receives an HTTP client, a base ID, and a table description. It checks that the table has a valid ID. If it does, it repeatedly asks Airtable for pages of records from that base and table, using Airtable’s `offset` token to continue from page to page. For each non-empty page, it adds `base_id`, `table_id`, and `table_name` to every record, then yields that page onward.

**Call relations**: This is used inside `AirtableConnector.paginate` only for the records stream. After `paginate` has found a base and its tables, it hands each table to this helper, which produces record pages for the sync system to consume.

*Call graph*: called by 1 (paginate); 1 external calls (with_context).


##### `AirtableConnector.paginate`  (lines 72–101)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading path for the connector. Given a requested stream, it decides whether to return bases, tables, or records, and yields the data in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. For the `bases` stream, it reads bases and yields them once. For the `tables` stream, it reads bases, then gathers tables from each base into pages of about 100 before yielding them. For the `records` stream, it reads bases, then tables, then yields record pages for every table. If asked for an unknown stream, it raises `StreamSkipped`, which tells the sync system that this connector does not implement that stream.

**Call relations**: This function is the coordinator for the file. It calls `_bases` to start discovery, `_tables_for_base` to go one level deeper, and `_records_for_table` when record data is needed. The broader source-sync framework calls this method when it wants Airtable data.

*Call graph*: calls 4 internal fn (__init__, _bases, _records_for_table, _tables_for_base).


##### `AirtableConnector.flatten`  (lines 103–125)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Airtable items into a more predictable form before they are stored or indexed. It keeps the original data but adds or normalizes a few fields that downstream code can rely on.

**Data flow**: It receives one Airtable record and the stream it belongs to. For bases, it adds a metadata API URL. For tables, it adds an API URL pointing to that table within its base. For records, it renames Airtable’s `createdTime` into `created_at` and makes sure `fields` is a dictionary even if the source value is missing or malformed. It returns the adjusted dictionary without changing the original object in place.

**Call relations**: After `paginate` has produced raw pages, the source framework can call `flatten` on each item to prepare it for the common storage and recall format. Unlike the discovery helpers, this function does not fetch more data; it is the cleanup step before Airtable data leaves the connector.


### `extensions/sources/ufo_ext_sources/providers/typeform.py`

`io_transport` · `during source sync, when Typeform records are being fetched`

Typeform stores data behind a web API, and that API does not return everything at once. This connector is the adapter that knows Typeform’s rules: which URLs to call, how pages of results are shaped, how to continue to the next page, and how to skip streams when the user’s Typeform permission does not allow them.

The file defines the Typeform streams the system can read. Some streams are simple lists, like forms or workspaces. Others need extra steps. Responses belong to individual forms, so the connector first reads all forms, then asks Typeform for the responses for each form. Webhooks work the same way: first find the forms, then fetch each form’s webhooks.

For ordinary paged lists, Typeform uses page numbers, like turning pages in a catalog. For responses, it uses a cursor token, which is more like a bookmark saying “continue after this item.” The connector supports both styles. It also supports incremental syncing for forms and responses, meaning it can ask only for records newer than a saved cursor.

If Typeform says “unauthorized” or “forbidden,” the connector raises a skip signal instead of crashing the whole sync. This matters because one Typeform account may not have access to every kind of data.

#### Function details

##### `TypeformConnector.record_ref`  (lines 52–56)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses a human-useful reference for a Typeform record. For most streams it uses the normal reference logic, but for webhooks it uses the webhook tag because that is the meaningful identifier Typeform provides.

**Data flow**: It receives one record and the stream it came from. If the stream is not webhooks, it passes the record to the shared connector behavior. If it is webhooks, it reads the record’s tag field and returns it as text when it is a string or number; otherwise it returns nothing.

**Call relations**: The broader sync system calls this when it needs a stable label or reference for a record. This function only customizes the webhook case and lets the parent RestConnector behavior take care of every other Typeform stream.


##### `TypeformConnector.paginate`  (lines 58–85)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Typeform stream. Given a stream name, it chooses the right fetching method and yields batches of records back to the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the last synced point. It checks the stream name, calls the matching helper, and passes each returned page onward. If the stream is unknown, or if Typeform refuses access with a 401 or 403 status, it raises StreamSkipped so the run can continue without that stream.

**Call relations**: The source framework calls this when it wants records from Typeform. It hands forms to _forms, responses to _responses, simple list-style streams to _paged_items, and webhooks to _webhooks. If access is refused, it creates the skip signal used by the sync layer.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 87–105)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Typeform endpoints that return ordinary numbered pages of items. It keeps asking for the next page until Typeform says there are no more.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It adds a page number and page size, calls Typeform, pulls the list found under the items field, and yields that list when it is not empty. It stops when it reaches Typeform’s reported page count, or when a short page shows that the list has ended.

**Call relations**: paginate uses this directly for workspaces, images, and themes. _forms also uses it as its raw form reader before applying any cursor filtering. The helper relies on records_at to safely pull the item list out of Typeform’s response body.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 107–114)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Typeform forms and optionally filters them to only forms updated after a saved cursor. It is the shared starting point for any stream that depends on forms.

**Data flow**: It receives an HTTP client and an optional cursor. It asks _paged_items for pages from the /forms endpoint. If a cursor is present, it keeps only forms whose last_updated_at value is later than that cursor. It then yields each non-empty page of forms.

**Call relations**: paginate calls this when the requested stream is forms. _responses and _webhooks also call it because both responses and webhooks are attached to individual forms, so they need the form list before they can fetch their own records.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 116–138)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads submitted responses for every Typeform form. It adds form information to each response so later code knows which form the response came from.

**Data flow**: It receives an HTTP client and an optional cursor. First it reads all forms without filtering them by form update time. For each form with a valid id, it builds request parameters; if a cursor is present, it sends it as Typeform’s since value. It then walks response pages using Typeform’s next_page_token bookmark, and yields the response records after adding form_id and form_title context.

**Call relations**: paginate calls this for the responses stream. This function depends on _forms to discover form ids first, then uses the connector’s shared cursor-page reader for the response endpoint. It uses with_context so downstream storage can keep each response tied to its parent form.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 140–149)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads webhook definitions for every Typeform form. A webhook is a saved instruction telling Typeform to call another web address when something happens, such as a new response.

**Data flow**: It receives an HTTP client. It reads all forms, skips any form without a usable id, calls the form-specific webhooks endpoint, extracts the items list from the response, and yields those webhook records after adding the form id and title.

**Call relations**: paginate calls this for the webhooks stream. Like _responses, it starts with _forms because webhooks are nested under forms in the Typeform API. It uses records_at to pull webhook records from the response and with_context to attach the parent form details.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).

# Marketing, advertising, social, and form connectors  `stage-14.1.5`

This stage is a set of “connectors,” which are adapters that let the system read data from outside services during a sync run. It sits in the main data-gathering part of the system: each connector talks to a different web API, asks for records in batches, and reshapes the replies into a common stream of rows that the rest of the project can store and reuse.

The ActiveCampaign, Klaviyo, and Mailchimp files cover marketing and email tools. They fetch things like contacts, profiles, campaigns, lists, reports, tags, events, and email activity. The Facebook Ads and Google Ads files cover advertising platforms. They pull account, campaign, ad, and performance data, including daily metrics. The Instagram file reads business-facing Instagram data through Meta’s API, including pages, linked accounts, posts, stories, and insights, without posting or changing anything. The Typeform file brings in form-related data, such as forms, responses, workspaces, themes, images, and webhooks. Together, these adapters act like plug heads for different outlets, making many services feed the same sync machine.

## Files in this stage

### Marketing automation and email campaigns
Connectors that sync customer, campaign, audience, and event data from marketing automation and email campaign platforms.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `source sync, while fetching ActiveCampaign streams`

ActiveCampaign stores many kinds of business data: contacts, mailing lists, campaigns, deals, accounts, custom fields, tags, users, and more. This file is the read-only connector for that service. Without it, the larger system would not know which ActiveCampaign data sets exist, what each one is called in the API, how to authenticate, or how to walk through large result sets safely.

The file starts by defining the available “streams,” where a stream means one type of data to sync, such as contacts or campaigns. Each stream records practical details like its name, its main ID field, and which date field can be used to notice newer changes.

ActiveCampaign’s API returns lists in a repeated pattern: ask for up to 100 records, then ask again with a higher offset until there are no more full pages. The connector uses that pattern in `paginate`. For some streams, it can also send an “only records changed after this time” filter, which makes later syncs faster. For streams that do not support that server-side filter, the broader sync system can still avoid duplicates using each row’s cursor field.

Authentication has one important twist: ActiveCampaign expects an `Api-Token` header, not the more common bearer-token style. The connector converts the stored key into that header unless a proxy transport is already provided. If ActiveCampaign refuses access with a permission or key error, the stream is skipped with a clear explanation instead of crashing the whole sync unexpectedly.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the small description object for one ActiveCampaign data stream. It keeps the long stream list readable by applying common defaults, such as using `id` as the main key and `cdate` as the created-time field.

**Data flow**: It receives a stream name plus optional details like the API object name, primary key, cursor date field, and whether the stream is considered canonical. It fills in missing values with ActiveCampaign-friendly defaults, decides whether the cursor also counts as an updated-time field, and returns a `StreamSpec`, which is the system’s standard description of a syncable data set.

**Call relations**: This function is used while the module is being loaded to build `ACTIVECAMPAIGN_STREAMS`. Those stream descriptions are then attached to `ActiveCampaignConnector`, so the rest of the sync system can ask the connector what ActiveCampaign data it offers.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to ActiveCampaign. Its main job is to put the API key in the exact header name ActiveCampaign expects.

**Data flow**: It receives a base URL and a resolved credential. If the credential already contains a custom transport, it leaves that setup alone and lets the parent connector build the client. Otherwise, it reads the credential’s direct key, requires that it exists, wraps it as an `Api-Token` header, and passes that header-based credential to the shared REST client builder. The result is an asynchronous HTTP client ready to make API requests.

**Call relations**: The broader connector framework calls this when a sync run needs a network client. This method adapts the generic credential format to ActiveCampaign’s specific authentication style, then hands off to the parent REST connector for the common client setup.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function translates the system’s stream name into the exact URL path part and response field name used by ActiveCampaign. It matters because some names differ in spelling style, such as `campaign_messages` in this system versus `campaignMessages` in the API.

**Data flow**: It receives a `StreamSpec`. It looks up that stream’s name in the mapping of known ActiveCampaign paths and envelope keys. If there is no special mapping, it uses the stream name for both. It returns a pair: the URL segment to request and the JSON key where records should be found.

**Call relations**: `paginate` calls this before making requests. It is the small translation step that lets the rest of pagination stay generic, instead of filling the request loop with one-off naming rules.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one ActiveCampaign stream in pages and yields each page of records to the sync system. It is the main read loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous run. It turns the stream into an API path, starts with a page size of 100, and, when supported, adds a filter asking ActiveCampaign for records changed after the cursor. It then asks the shared REST pagination helper for offset-based pages and yields each list of records it gets back. If ActiveCampaign responds with an authorization refusal, it turns that into a clear `StreamSkipped` error; other HTTP errors are allowed to continue upward.

**Call relations**: During a sync, the framework calls this for each stream it wants to read. `paginate` first uses `_resolve_stream_segment` to match local stream names to ActiveCampaign’s API shape, then delegates the repeated page requests to the inherited offset-page helper. When permission problems happen, it signals the larger sync flow that this particular stream should be skipped rather than treated like a normal successful read.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `during Klaviyo source sync`

Klaviyo exposes its data through a JSON:API-style web service, where each record arrives wrapped in layers such as attributes, relationships, and links. This file is the adapter that knows Klaviyo’s rules: which streams exist, what URL to call, how to authenticate, how to page through results, and how to flatten nested records into simpler shapes.

Without this file, the system would not know how to pull Klaviyo data safely or incrementally. “Incremental” means it can ask only for records changed since the last run, instead of downloading everything every time. The connector chooses the right timestamp field for each kind of stream, because Klaviyo uses different names such as updated, updated_at, or datetime.

The file also handles Klaviyo’s pagination. Each API response may include a next link, like a “next page” button. The connector follows that link until there are no more pages. For events, it also reads included metric records so an event can carry a human-friendly metric name.

Before records leave this connector, flatten turns Klaviyo’s nested JSON into a simpler flat dictionary. It lifts useful fields like profile consent, campaign subject lines, event metric IDs, and related list IDs. If Klaviyo refuses access to a stream because the API key lacks permission, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a stream definition, which is the system’s recipe for one type of Klaviyo data such as profiles, campaigns, or events. It keeps the stream list compact by filling in common defaults like the primary key and timestamp fields.

**Data flow**: It receives a stream name and optional details such as the Klaviyo API object name, cursor field, and creation/update timestamp fields. It combines those inputs with sensible defaults and returns a StreamSpec object that the connector later uses to know how to sync that stream.

**Call relations**: At file load time, the Klaviyo stream list uses this helper repeatedly to build the catalog of available streams. Internally it hands the collected settings to StreamSpec.__init__, which creates the actual stream description used by the connector.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method prepares the HTTP client used to talk to Klaviyo. It adds the Klaviyo-specific headers required for successful API calls, including the pinned API revision and, when available, the private API key authentication format.

**Data flow**: It receives a base URL and a credential. First it asks the parent REST connector to create the basic web client. Then it adds Klaviyo’s revision header and, if the credential contains a key, adds an Authorization header in Klaviyo’s expected form. It returns the ready-to-use client.

**Call relations**: This fits into the connector setup step, before any Klaviyo data is requested. The base connector supplies the general client machinery, and this method layers Klaviyo’s special requirements on top so later pagination requests can succeed.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This method turns Klaviyo’s full next-page URL into the path and query string that the already-configured client can request. It is needed because Klaviyo gives an absolute URL, while the connector works relative to the base API host.

**Data flow**: It receives a next link, which may be missing or empty. If there is no usable link, it returns None. Otherwise it parses the URL, keeps only the path and query part, and returns that shortened request path.

**Call relations**: KlaviyoConnector.paginate calls this after each page to decide where to go next. It uses urllib.parse.urlparse to split the full URL into pieces, then gives paginate the next path to request or None to stop the loop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This method chooses which timestamp field should be used to sort and filter a stream. Klaviyo is not consistent across resources, so this keeps that knowledge in one place.

**Data flow**: It receives a stream definition. If the stream is events, it returns datetime. If the stream is one of the resources that use updated_at, it returns updated_at. For all other incremental streams, it returns updated.

**Call relations**: This helper supports the first-page query builder. When the connector prepares an incremental request, it uses this method’s answer to ask Klaviyo for records in the right time order.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This method builds the query parameters for the first API request in a stream. It sets page size, sorting, optional incremental filtering, and a few stream-specific extras that make records more useful.

**Data flow**: It receives a stream definition and an optional cursor, which is the last saved timestamp from an earlier sync. It starts with the page size. If the stream has a cursor, it chooses the right timestamp field and adds sorting; if a cursor value exists, it also adds a filter asking Klaviyo for records at or after that time. For profiles it asks for subscription details, and for events it asks Klaviyo to include metric data. It returns the query parameter dictionary.

**Call relations**: KlaviyoConnector.paginate calls this once, before requesting the first page. Later pages use Klaviyo’s own next links instead, so this method only shapes the starting request for the stream.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This helper safely extracts the ID of a related Klaviyo object from a nested relationship block. It avoids errors when Klaviyo leaves a relationship out or returns it in an unexpected shape.

**Data flow**: It receives a relationships value and the relationship name to look for. It checks each nested level before reading it: the relationship object, its data object, and finally the id. If an ID is present, it returns it as text; otherwise it returns None.

**Call relations**: KlaviyoConnector.flatten calls this when it wants simple fields such as profile_id, metric_id, or parent_list_id. The helper acts like a careful unpacker for Klaviyo’s nested relationship format.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts one Klaviyo record from its nested API shape into a flatter dictionary that the rest of the system can store, compare, and search more easily. It also pulls out a few especially useful details that would otherwise stay buried.

**Data flow**: It receives a raw Klaviyo record and the stream it came from. It starts a new flat record with the ID and resource type, then copies top-level attributes into that flat record. Depending on the stream, it adds useful fields: profile email consent and suppression reason, campaign subject and sender details, event profile and metric links, event message IDs, or a segment’s parent list. For lists and segments it removes profile_count so membership changes do not make the list or segment itself look changed. It returns the flattened dictionary.

**Call relations**: This is used after records are fetched and before they are handed to the broader sync system. When it needs IDs from Klaviyo relationship blocks, it calls KlaviyoConnector._lift_relationship_id so the nested lookup is done safely and consistently.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads all pages for one Klaviyo stream. It is the main loop that asks Klaviyo for data, follows next-page links, enriches event records with metric names, and yields batches of records to the rest of the sync.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It starts at the stream’s API path and builds the first request parameters with _initial_query. For each page, it performs a GET request, reads the records, optionally matches event metrics from the included data, yields the records if any exist, and then uses _next_path to find the next page. If Klaviyo returns 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped so the run records that this stream was skipped because the key lacks permission. Other HTTP errors are raised normally.

**Call relations**: This is the connector’s page-by-page reading engine. It calls KlaviyoConnector._initial_query before the first request, KlaviyoConnector._next_path after each response, and StreamSkipped.__init__ when access is refused. The broader REST source machinery calls into this method when it needs records for a Klaviyo stream.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `during source sync, while reading Mailchimp API pages`

Mailchimp stores marketing data behind a web API, and much of that data is nested. For example, members belong to lists, interests belong to interest categories, and email activity belongs to campaign reports. This file is the adapter that knows those paths and walks through them in the right order.

At the top, it defines the Mailchimp streams the system can sync and records small facts about each one, such as its primary key and the date field used for incremental syncing. Incremental syncing means “only ask for records changed since the last saved point,” like checking only new mail instead of rereading the whole mailbox.

The main class, `MailchimpConnector`, extends a shared REST connector. It does not write anything back to Mailchimp; it only reads. Its central `paginate` method decides which route to use for each stream. Simple resources, such as lists and campaigns, are fetched directly with offset pagination. Nested resources first fetch their parent IDs, then fetch each child collection. For email activity, Mailchimp returns one recipient with many actions, so this connector expands that into one row per action and invents a stable ID because Mailchimp does not provide one.

If Mailchimp refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper creates a `StreamSpec`, which is the system’s description of one kind of Mailchimp data to sync. It keeps the stream definitions short and consistent instead of repeating the same setup fields many times.

**Data flow**: It receives a stream name plus optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults when values are not supplied, then returns a configured `StreamSpec` object for the rest of the connector to use.

**Call relations**: This function is used while the module is being loaded to build `MAILCHIMP_STREAMS`. It hands each stream’s basic identity and sync rules to `StreamSpec`, which is the shared data shape understood by the broader source system.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This adjusts records after they are fetched so subscriber records have a normal `created_at` value. Mailchimp uses different signup timestamp fields, and this method turns them into the field shape expected by the rest of the system.

**Data flow**: It receives one Mailchimp record and the stream it came from. For list member and segment member streams, it copies the original record and sets `created_at` from `timestamp_signup` or, if that is missing, `timestamp_opt`; for all other streams, it returns the record unchanged.

**Call relations**: The shared connector framework can call this after records are read, before they are passed onward. It does not fetch more data itself; it simply normalizes the rows produced by the pagination methods.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This tells the connector which JSON field contains the actual list of records for a stream. Mailchimp wraps results under names like `lists`, `members`, or `emails`, so the connector needs to know where to look.

**Data flow**: It receives a stream description. It looks up the stream name in the connector’s mapping of Mailchimp response fields, and returns the matching field name; if there is no special mapping, it returns the stream name itself.

**Call relations**: Pagination helpers call this before reading pages so they can pull records out of the right place in Mailchimp’s response. It is used by top-level, per-list, and per-report pagination paths.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameter Mailchimp needs for incremental syncing. In plain terms, it turns “start from this saved timestamp” into Mailchimp’s particular `since...` filter name.

**Data flow**: It receives a stream and an optional cursor value. If there is no cursor, no cursor field, or Mailchimp has no matching filter for that field, it returns an empty parameter set; otherwise it returns a one-item dictionary such as `{since_last_changed: <timestamp>}`.

**Call relations**: Several pagination paths call this before requesting pages, so Mailchimp can do some filtering on its side. That keeps syncs smaller when the API supports filtering for the stream’s date field.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for reading a Mailchimp stream. Given the requested stream, it chooses the correct fetching strategy: direct pages, list-based child pages, report-based child pages, or deeper nested walks.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, delegates to the matching pagination helper, and yields lists of records page by page. If Mailchimp returns an access-denied status, it converts that into a `StreamSkipped` message explaining that the credentials or permissions are not enough.

**Call relations**: The broader sync engine calls this when it wants records for a stream. `paginate` then hands control to helpers such as `_paginate_top_level`, `_paginate_per_list`, `_paginate_interests`, `_paginate_segment_members`, `_paginate_per_report`, or `_paginate_email_activity`, depending on how Mailchimp organizes that data.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads simple Mailchimp collections that live at their own top-level API paths, such as lists, campaigns, automations, and reports. These are the easiest streams because they do not require first finding a parent object.

**Data flow**: It receives an HTTP client, stream description, API path, and optional cursor. It asks `_data_field` where records are in the response and `_cursor_params` whether to add a date filter, then uses the shared offset-page reader to request pages until the collection is exhausted. It yields each page of records as it arrives.

**Call relations**: `paginate` calls this for top-level streams. This helper relies on shared REST pagination from the base connector, while supplying the Mailchimp-specific field names, page size, and query parameter style.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common page reader for nested Mailchimp endpoints. It exists so per-list, per-report, and deeper nested streams do not each repeat the same offset pagination code.

**Data flow**: It receives an HTTP client, a nested API path, the response field containing records, and optional base query parameters. It requests pages with Mailchimp’s `count` and offset style and yields each page of records from the requested field.

**Call relations**: The more specific nested walkers call this after they have built the correct child path. It is the shared conveyor belt that actually reads child collections once the parent IDs are known.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This extracts object IDs from a paged Mailchimp collection. It is used when the connector must first collect parent IDs before it can fetch child data.

**Data flow**: It receives an HTTP client, an API path, and the response field that contains rows. It reads all pages from that collection, checks each row for an `id`, converts found IDs to strings, and yields them one by one.

**Call relations**: `_list_ids` and `_report_ids` call this to get audience list IDs and report IDs. Those IDs become the building blocks for nested API paths used later by the child pagination methods.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This produces the IDs of all Mailchimp audience lists. Many Mailchimp resources are stored under a specific list, so these IDs are needed before members, segments, tags, interest categories, interests, or segment members can be fetched.

**Data flow**: It receives an HTTP client. It calls `_ids` on the `/3.0/lists` endpoint, then yields each list ID it finds.

**Call relations**: Per-list pagination helpers call this at the start of their work. Once a list ID is available, those helpers build list-specific paths and fetch the related child records.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This produces the IDs of all Mailchimp campaign reports. Report-based streams, such as unsubscribes and email activity, need these IDs before their child data can be requested.

**Data flow**: It receives an HTTP client. It calls `_ids` on the `/3.0/reports` endpoint, then yields each report ID it finds.

**Call relations**: Report-focused pagination helpers call this before building report-specific API paths. The resulting IDs let `_paginate_per_report` and `_paginate_email_activity` walk through each report’s child collections.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that sit directly under each Mailchimp audience list, such as list members, segments, tags, and interest categories. It also adds the parent list ID to each row when needed, so the row still shows where it came from.

**Data flow**: It receives an HTTP client, stream description, child path name, optional cursor, and the name of a parent field to stamp onto records. It finds all list IDs, builds one child endpoint per list, reads pages from each endpoint, optionally adds `list_id` to each record, and yields the pages.

**Call relations**: `paginate` calls this for list-based streams. It uses `_list_ids` to find parents, `_data_field` to read the right response field, `_cursor_params` to add incremental filters when available, `_paginate_child` to fetch records, and URL quoting to safely place IDs in paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads interests, which are nested two levels deep: first under a list, then under an interest category. It preserves both parent IDs so each interest can be traced back to its list and category.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It loops through list IDs, fetches interest categories for each list, then fetches interests for each category. For every interest row, it adds `list_id` and `category_id` when they are not already present, and yields pages of interests.

**Call relations**: `paginate` calls this only for the `interests` stream. It depends on `_list_ids` to find lists and `_paginate_child` to read both category pages and interest pages, building safe URL paths with quoted IDs along the way.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads members inside each Mailchimp segment. Segments belong to lists, so the connector must walk list → segment → member to reach the final records.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It builds cursor parameters if possible, loops through every list, fetches that list’s segments, then fetches members for each segment. Each member row is stamped with `list_id` and `segment_id`, then the page is yielded.

**Call relations**: `paginate` calls this for the `segment_members` stream. It combines `_list_ids`, `_cursor_params`, and `_paginate_child` to move through Mailchimp’s nested structure, using URL quoting so list and segment IDs are safe inside API paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that sit under each campaign report, such as unsubscribes. It adds the parent campaign or report ID to records so the rows remain connected to the report they came from.

**Data flow**: It receives an HTTP client, stream description, child path name, optional cursor, and an optional parent field name. It finds report IDs, builds a child endpoint for each report, reads pages from that endpoint, optionally stamps the report ID into each row, and yields the pages.

**Call relations**: `paginate` calls this for report-based child streams other than email activity. It uses `_report_ids` to find parents, `_data_field` to locate records in Mailchimp responses, `_cursor_params` for date filtering, `_paginate_child` for the actual page reading, and URL quoting for safe paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads email activity from each campaign report and turns Mailchimp’s nested activity arrays into normal rows. That matters because downstream storage usually expects one event per row, not one recipient row containing many hidden actions.

**Data flow**: It receives an HTTP client and optional cursor. It builds a `since` filter when a cursor is present, loops through report IDs, fetches email activity pages for each report, then separates each recipient’s `activity` list into individual action rows. Each row gets the campaign ID and, if Mailchimp did not provide an ID, a stable synthetic ID made from email ID, action, and timestamp; only non-empty exploded pages are yielded.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to visit each report and `_paginate_child` to fetch Mailchimp’s raw email activity pages, then performs the extra reshaping before handing records back to the sync engine.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Advertising platforms
Connectors that ingest account, campaign, ad, and performance data from major paid advertising APIs.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `source sync runs`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook Ads web addresses to call, how to page through long result lists, or how to turn Meta's account-based data into streams the rest of the sync system can store and recall.

The file defines a list of streams: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is simply one kind of thing to fetch. The connector always starts by asking Facebook for the user's ad accounts. That matters because most useful Facebook Ads data lives underneath an ad account, like folders inside a filing cabinet. After it finds the accounts, it asks each account for its campaigns, ad sets, ads, or insights.

Facebook returns results in pages, not all at once. The connector follows Facebook's `paging.next` link until there are no more pages, like turning pages in a book until the last one. For campaigns, ad sets, and ads, it can skip older records by comparing their `updated_time` to the saved cursor, which is the last known sync position. For insights, it requests one row per ad per day, using either the saved cursor date or the last 90 days on a first run. It also creates a stable row id from the account, campaign, ad set, ad, and date so repeated syncs can recognize the same insight row.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Facebook Graph API collection page by page. It hides the repeated work of making a request, reading the `data` list, and following Facebook's next-page link.

**Data flow**: It receives an HTTP client, an API path or next-page URL, and optional query parameters. It asks Facebook for that page, pulls the records out of the response's `data` field, yields them as a batch when any exist, then follows the response's `paging.next` link. The output is a stream of record batches until Facebook says there are no more pages.

**Call relations**: This is the shared page-turning helper used by account lookup, account child streams, and insights fetching. Those higher-level methods decide what kind of Facebook data they want; this method does the repeated network paging work for them and uses `records_at` to safely extract the response's record list.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Gets all ad accounts available to the connected Facebook credential. Other streams depend on this because campaigns, ads, and insights are fetched separately for each account.

**Data flow**: It starts with a fixed set of account fields such as id, name, currency, time zone, and creation time. It calls `_paged` on `/me/adaccounts`, collects every page into one list, and returns that list of account dictionaries.

**Call relations**: The public `paginate` method calls this directly when syncing the `ad_accounts` stream. `_account_children` and `_insights` also call it first so they know which account-specific Facebook endpoints to visit next.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches campaigns, ad sets, or ads for every accessible ad account. It also adds account context to each record so later readers can tell which ad account the item came from.

**Data flow**: It receives the stream being synced and an optional cursor. First it chooses the right Facebook fields for campaigns, ad sets, or ads. Then it loads all ad accounts, calls the matching account endpoint for each one, optionally filters out records whose cursor field is not newer than the saved cursor, and yields non-empty batches with the account id and name attached.

**Call relations**: This method is called by `paginate` when the requested stream is `campaigns`, `ad_sets`, or `ads`. It relies on `_accounts` to find the account list, `_paged` to walk through Facebook result pages, and `with_context` to stamp each returned record with its parent ad account information.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches daily advertising performance rows, such as impressions, clicks, spend, reach, and click-through rate, for each ad. It turns Facebook's reporting rows into stable records that the sync system can recognize across runs.

**Data flow**: It builds an insights request asking for ad-level daily rows. If a cursor exists, it requests data from that cursor date through today; otherwise it asks for the last 90 days. For each ad account, it pages through the account's insights endpoint, builds a new `id` by joining the account, campaign, ad set, ad, and date fields, adds the ad account id, and yields the resulting rows.

**Call relations**: The `paginate` method calls this when syncing `ads_insights`. It uses `_accounts` to know which accounts to query, `_paged` to fetch all report pages, `json.dumps` to encode Facebook's date range parameter, and the current UTC date to end cursor-based reporting windows at today.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching path for each Facebook Ads stream. This is the main doorway the broader sync system uses to ask the connector for records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is ad accounts, it yields the account list. If the stream is campaigns, ad sets, or ads, it delegates to `_account_children`. If the stream is ad insights, it delegates to `_insights`. If the stream name is unknown, it raises a skip signal instead of pretending it can sync it.

**Call relations**: This method sits between the generic REST connector framework and the Facebook-specific helpers in this file. The framework calls it during a sync; it routes the request to `_accounts`, `_account_children`, or `_insights`, and uses `StreamSkipped` to clearly report an unsupported stream.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adjusts individual records after they are fetched, mainly to make campaign records easier and more consistent for the rest of the system to use.

**Data flow**: It receives one record and the stream it belongs to. For campaign records, it returns a copy with a normalized `status` that prefers `effective_status`, and a `created_at` field copied from Facebook's `created_time`. For all other streams, it returns the record unchanged.

**Call relations**: This function is used after pagination when records are being prepared for storage or recall. It does not fetch more data; it performs a small cleanup step so downstream code can read campaign status and creation time through more predictable fields.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `during source sync when reading Google Ads streams`

Google Ads does not provide this data as simple files. Instead, the connector must ask the Google Ads API specific questions using GAQL, the Google Ads Query Language, which is similar in spirit to SQL: it says what fields to select and which Google Ads object to read from. This file defines those readable streams and the steps needed to fetch them.

The connector first needs two kinds of permission. OAuth identifies the advertiser account, but Google Ads also requires a separate developer token on every request. This file reads that token from environment variables. If the token is missing, or Google refuses access with a 401 or 403 response, the stream is skipped rather than crashing the whole sync.

For most streams, the connector starts by asking Google which customer accounts are accessible. Then it runs the right GAQL query once for each customer account. Each returned row is stamped with the customer ID, like putting a return address on every envelope, so later records can be traced back to the account they came from.

Google Ads responses are nested, meaning campaign details may sit inside a "campaign" object and metric details inside a "metrics" object. The `flatten` method lifts the important pieces into simple top-level fields such as `id`, `name`, `resource_name`, and `date`, so the wider sync system can identify and update records consistently. This connector is read-only; it deliberately contains no code for creating or changing ads.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token required by the API. OAuth alone is not enough for Google Ads, so without this token the connector cannot safely make requests.

**Data flow**: It reads the `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` environment variable first, then `GOOGLE_ADS_DEVELOPER_TOKEN` as a fallback. If it finds a token, it returns it. If not, it raises `StreamSkipped`, which tells the sync system to skip this Google Ads stream instead of treating it as an unexpected failure.

**Call relations**: When the connector creates its HTTP client, `GoogleAdsConnector._make_client` calls this function so every Google Ads request can include the required developer token header.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Google Ads. It adds the special headers Google Ads expects in addition to the normal authentication supplied by the wider system.

**Data flow**: It receives a base URL and a credential object. It first lets the parent REST connector build the normal authenticated client, then adds the developer token header. If a login customer ID is present in the environment, it strips dashes from it and adds it as another header. The result is a ready-to-use asynchronous HTTP client.

**Call relations**: This is part of the connector setup before API calls begin. It calls `GoogleAdsConnector._developer_token` to get the required token, and the prepared client is then used by later methods that list customers and run Google Ads queries.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credentials can access. The connector needs this list because most Google Ads queries must be run for one customer account at a time.

**Data flow**: It sends a request to Google Ads' accessible-customers endpoint using the provided HTTP client. From the response, it looks for resource names shaped like `customers/1234567890`, extracts just the numeric customer ID part, and returns a list of those IDs. Unexpected or malformed entries are ignored.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this first, before running a stream query. The returned customer IDs become the targets for the per-account search requests.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one GAQL query for one Google Ads customer account and collects the result rows. It is the low-level worker that actually asks Google Ads for campaigns, ads, metrics, or other selected data.

**Data flow**: It receives an HTTP client, a customer ID, and a query string. It posts that query to the Google Ads `searchStream` endpoint for that customer. Google returns a list of batches, and each batch may contain result rows. The function walks through those batches, keeps only dictionary-shaped rows, and returns them as one flat list.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this once for each accessible customer account. This function does not decide which query to run; `GoogleAdsConnector.paginate` chooses the query for each stream and passes it down through `_query_each_customer`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It turns a single stream request into many per-customer API requests and yields pages of rows as they arrive.

**Data flow**: It receives an HTTP client and a GAQL query. It first gets the accessible customer IDs, then runs the query for each customer. If Google returns rows for a customer, it adds that `customer_id` to every row and yields the rows as a page. Empty customer results produce no page.

**Call relations**: `GoogleAdsConnector.paginate` calls this for each supported stream after choosing the right GAQL query. Inside, it relies on `GoogleAdsConnector._customer_ids` to find the accounts and `GoogleAdsConnector._search_stream` to fetch rows from each account.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main read path for Google Ads streams. Given a requested stream, it chooses the correct GAQL query and yields pages of records for the sync system to consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is a saved position from a previous sync. For each known stream name, it builds a matching GAQL query and passes it to `GoogleAdsConnector._query_each_customer`. For campaign metrics, it uses the cursor date if available; otherwise it defaults to roughly the last 90 days. It yields each page returned from the per-customer query process. If the stream is unknown, or Google refuses access with a 401 or 403 status, it raises `StreamSkipped` with an explanation.

**Call relations**: The wider sync engine calls this when it wants records for a Google Ads stream. This method coordinates the stream-specific choices, then hands the repeated customer-by-customer work to `GoogleAdsConnector._query_each_customer`. It also turns common permission failures into a clean skip signal so the rest of the sync can continue.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes nested Google Ads API rows into simpler records with stable top-level fields. The sync system needs those fields to identify records, compare updates, and store data consistently.

**Data flow**: It receives one raw record and the stream it belongs to. For customer rows, it pulls out a simple `id` and `name`. For campaign rows, it pulls out fields like `resource_name`, `name`, `status`, and `created_at`. For campaign metrics, it combines the customer ID, campaign ID, and date into a synthetic unique ID, then copies useful metric values such as impressions, clicks, and cost. For other streams, it returns the record unchanged.

**Call relations**: After `GoogleAdsConnector.paginate` yields raw rows from Google Ads, the sync framework can call this function to normalize each row before saving it. It uses `dict_or_empty` so missing nested objects behave like empty dictionaries instead of causing errors.

*Call graph*: 1 external calls (dict_or_empty).


### Social business surfaces
Connector support for reading Instagram business accounts, content, and insight metrics through Meta APIs.

### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `during source sync runs`

This connector is the bridge between the project and Instagram’s business data. Instagram business accounts are reached through Facebook Pages, so the connector starts by asking Facebook for the Pages available to the current grant, then looks inside each Page for a linked Instagram business account. From there it can read media posts, stories, and analytics-style “insights” such as reach, impressions, engagement, replies, and profile views.

The file defines several stream descriptions, which are small labels telling the sync system what kinds of records exist and which field identifies each record. The main class, InstagramConnector, knows the Facebook Graph API base address and provides one main entry point, paginate, that the rest of the sync system calls when it wants records for a specific stream.

A key detail is pagination. The Facebook API returns results in pages, like turning pages in a catalog, and may include a “next” link for more results. The connector follows those links until there is nothing left. For media, stories, and user insights, it also respects a cursor, which is a saved watermark from the last sync, so old records can be skipped. If Instagram refuses access because the grant lacks permission or the token is invalid, the connector reports the stream as skipped instead of crashing the whole run. If a single media or story object cannot return insights, it skips just that object and keeps going.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper walks through a Facebook Graph API collection that may span multiple pages of results. It is used when the API returns a list under a data field and gives a next link for the following page.

**Data flow**: It receives an HTTP client, an API path or next-page URL, and optional query parameters. It repeatedly asks the API for that page, pulls the list of records out of the response, yields that list when it is not empty, then follows the response’s next link until there are no more pages.

**Call relations**: _pages uses this to walk the /me/accounts endpoint, and _account_collection uses it to walk each Instagram account’s media or stories. It relies on records_at to safely pull the data list out of the response shape.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This reads the Facebook Pages available to the current grant, including any linked Instagram business account details embedded in each Page. It is the connector’s starting point because Instagram business data is discovered through Facebook Pages.

**Data flow**: It builds a fields request asking for Page identity and linked Instagram account details. It sends that request through _paged, gathers all returned Page records into one list, and returns that list to the caller.

**Call relations**: paginate calls this directly when syncing the pages stream. _instagram_accounts also calls it as the first step before extracting Instagram accounts from the Page records.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This extracts the actual Instagram business accounts from the Facebook Pages returned by _pages. It also attaches the Page id and Page name so each Instagram account can be traced back to the Page that exposed it.

**Data flow**: It asks _pages for all available Pages, looks at each Page’s instagram_business_account field, ignores missing or malformed accounts, and stores valid accounts by id to avoid duplicates. It returns a list of Instagram account records enriched with page_id and page_name.

**Call relations**: paginate uses this for the instagram_accounts stream. _account_collection and _user_insights also depend on it because they must know which Instagram account ids to query before they can fetch media, stories, or account-level insights.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a repeated collection, such as media or stories, from every linked Instagram business account. It is the shared machinery behind the media and stories streams.

**Data flow**: It receives the collection name to read, the fields to request, and an optional cursor field. It first gets all Instagram accounts, then for each account asks the API for that account’s collection, follows all result pages, filters out records at or before the saved cursor when a cursor is provided, adds the Instagram account id as context, and yields each non-empty batch.

**Call relations**: paginate calls this when syncing media and stories. It calls _instagram_accounts to find the accounts, _paged to walk each account’s API collection, and with_context to stamp each returned record with the account it came from.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads insights for individual objects, such as one media post or one story. It turns per-object analytics from Instagram into records the sync system can store.

**Data flow**: It receives a stream of object batches, such as media records, plus a comma-separated list of metrics to request. For each object with a valid id, it asks the API for that object’s insights, skips that object if Instagram says the insight is unavailable or forbidden, and builds insight records with a stable id, the parent object id, and the stream name. It yields batches of insight records when any were found.

**Call relations**: paginate uses this for media_insights and story_insights. In those cases paginate first creates an internal media or stories stream, then hands that stream of objects to _object_insights so it can fetch the matching analytics.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads daily account-level insights for each Instagram business account, such as impressions, reach, and profile views. These are not tied to a single post or story; they describe the account over time.

**Data flow**: It gets all Instagram accounts, then asks the API for daily insight metrics for each account. It walks through each insight’s values, ignores non-dictionary values, skips values at or before the saved cursor, and creates one record per account, metric name, and end time. It yields the rows it collected for each account when there are any new ones.

**Call relations**: paginate calls this for the user_insights stream. It depends on _instagram_accounts to know which accounts to query, records_at to read the API’s data list, and list_or_empty to safely treat the insight values as a list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main method the sync runner calls to get records for any Instagram stream. It chooses the right helper based on the requested stream name and yields batches of records back to the runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It checks the stream name, calls the matching helper for Pages, Instagram accounts, media, stories, object insights, or user insights, and yields each batch those helpers produce. If the stream name is unknown, or if Instagram refuses access with certain permission-related errors, it raises StreamSkipped so the run can record a skip instead of treating it as a broken connector.

**Call relations**: The rest of the source sync system enters this file through paginate. From there, paginate fans out to _pages, _instagram_accounts, _account_collection, _object_insights, or _user_insights depending on what is being synced, and it wraps permission failures in StreamSkipped to keep the larger run understandable and recoverable.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Form response sources
Connector support for syncing Typeform forms, responses, workspace assets, and webhook metadata.

### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `during source sync`

Typeform stores useful business data behind a web API: forms people create, responses people submit, and settings like workspaces, themes, images, and webhooks. This file is the read-only bridge from that API into UFO’s source-sync system. Without it, the system would not know which Typeform URLs to call, how to move through Typeform’s pages of results, or how to attach form information to responses and webhooks.

The main class, TypeformConnector, is a RestConnector, meaning it uses shared REST API machinery from the UFO source SDK. A REST API is a web service where the program asks for data by making HTTP requests to URLs. This connector defines the Typeform base URL and the list of supported streams. A stream is one category of data, like “forms” or “responses.”

Typeform returns many collections in pages, like a book split into chapters. The connector keeps asking for page 1, page 2, and so on until Typeform says there are no more. Responses and webhooks are special: Typeform stores them under each form, so the connector first lists all forms, then asks for responses or webhooks for each form. It also adds helpful context, such as the form id and title, to each returned response or webhook.

If Typeform refuses access with an authorization error, the connector skips that stream with a clear reason instead of crashing the whole sync.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Typeform stream. Given a requested stream, it chooses the right helper method to fetch that kind of Typeform data and turns access-denied errors into a clean “skip this stream” outcome.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value used for incremental syncing. It checks the stream name, calls the matching reader for forms, responses, workspaces, images, themes, or webhooks, and yields each page of records onward. If Typeform replies with a 401 or 403 refusal, it changes that web error into StreamSkipped so the wider sync can continue safely.

**Call relations**: The source-sync framework calls this method when it wants records for a Typeform stream. paginate then delegates the real fetching work to _forms, _responses, _paged_items, or _webhooks. If the stream is unknown, or Typeform refuses access, it raises StreamSkipped to tell the caller that this stream should not be processed.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use normal page numbers, such as page 1, page 2, and so on. It is used for simple list-style Typeform resources.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It repeatedly builds a request with a page number and page size, asks Typeform for that page, pulls the list of records from the response’s items field, and yields any records it finds. It stops when Typeform’s page_count says the last page has been reached, or when a short page suggests there is nothing more to fetch.

**Call relations**: paginate uses this directly for streams like workspaces, images, and themes. _forms also uses it as the lower-level page reader for the forms endpoint. It relies on records_at to safely extract the items list from Typeform’s response.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform forms and optionally filters them for an incremental sync. Incremental sync means only taking records newer than the last saved cursor, instead of rereading everything.

**Data flow**: It receives an HTTP client and an optional cursor string. It asks _paged_items to fetch pages from the /forms endpoint, then, if a cursor is present, keeps only forms whose last_updated_at value is later than that cursor. It yields each non-empty page of remaining forms.

**Call relations**: paginate calls this when the requested stream is forms. _responses and _webhooks also call it first because responses and webhooks are fetched per form. In those cases they ask for all forms, because they need form ids before they can reach the nested Typeform endpoints.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads submitted responses for every Typeform form. It also attaches the form id and title to each response so the response is easier to understand later.

**Data flow**: It first reads all forms through _forms. For each form with a usable id, it builds request parameters, including a since value when a cursor is available. It then uses the shared cursor-based pager to fetch that form’s responses, following Typeform’s next_page_token from one response page to the next. Each page of responses is returned with extra context fields for form_id and form_title.

**Call relations**: paginate calls this when the requested stream is responses. _responses depends on _forms to discover which form-specific response URLs to call, then uses with_context to stamp form information onto each response before handing the records back to paginate.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads webhook settings for every Typeform form. A webhook is a saved instruction telling Typeform to notify another system when something happens, such as a new response.

**Data flow**: It first reads all forms through _forms. For each form with a valid id, it asks Typeform for /forms/{form_id}/webhooks, extracts the items list from the reply, and, if any webhook records exist, adds the form id and title to them. It yields those enriched webhook records page by page.

**Call relations**: paginate calls this when the requested stream is webhooks. Like _responses, it starts with _forms because Typeform webhooks live under individual forms. It uses records_at to pull webhook records out of the API response and with_context to attach the form details before returning them.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).

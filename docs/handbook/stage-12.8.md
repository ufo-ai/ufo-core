# Marketing, advertising, social, and feedback source connectors  `stage-12.8`

This stage is a set of source connectors: small adapters that let the system fetch data from outside marketing and advertising tools during a sync run. Each connector knows how to sign in to a service, ask its web API for data, split large results into pages, and reshape the answers into steady “streams” of records that the rest of the system can store.

ActiveCampaign brings in marketing and customer-relationship data. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google customer accounts, campaigns, ad groups, ads, client accounts, and metrics. Instagram uses Meta’s API to fetch business Pages, linked Instagram accounts, posts, stories, and analytics. Klaviyo reads marketing data and flattens nested records into simpler ones. Mailchimp collects audiences, subscribers, campaigns, reports, and activity logs despite their varied formats. Typeform adds form-related data, including forms, responses, workspaces, themes, images, and webhooks. Together, these files act like translators at the system’s front door.

## Files in this stage

### Marketing CRM sync
ActiveCampaign introduces CRM and marketing-automation ingestion with authenticated, paginated object streams.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `during an ActiveCampaign sync run`

ActiveCampaign exposes many kinds of data: contacts, lists, campaigns, deals, accounts, tags, messages, custom fields, and more. This file turns those API resources into named streams the rest of the system can sync in a consistent way. Without it, the system would not know which ActiveCampaign URLs to call, where to find records inside each response, or how to pass the account API key correctly.

The file first lists all supported streams and gives each one basic facts: its name, its ID field, and which timestamp should be used to notice changes over time. Some streams can ask ActiveCampaign for “only records changed after this time”; others cannot, so they are reread and later deduplicated by the broader sync system.

The connector then builds an HTTP client. ActiveCampaign does not use the common “Bearer token” style; it expects an `Api-Token` header, so the connector rewrites the credential into that shape unless a proxy transport is already supplied.

When syncing, `paginate` chooses the right API path, adds a page size and optional incremental filter, and walks through offset-based pages. If ActiveCampaign refuses access with a 401 or 403 error, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a `StreamSpec`, which is the system’s compact description of one ActiveCampaign data stream. It saves repeated boilerplate when defining dozens of similar streams.

**Data flow**: It takes a stream name plus optional details such as the source object name, primary key, cursor field, and whether the stream is canonical. It fills in standard ActiveCampaign defaults, such as `id` for the primary key and `cdate` for the created-at field, then returns a ready-to-use `StreamSpec` object.

**Call relations**: This function is used while the file is being loaded to build `ACTIVECAMPAIGN_STREAMS`. It hands those stream descriptions to `ActiveCampaignConnector`, which later uses them to decide what to sync and how to track progress.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to ActiveCampaign. Its main job is to put the API key into the special header name that ActiveCampaign expects.

**Data flow**: It receives a base URL and a credential. If the credential already includes a custom transport, it leaves that setup alone and asks the parent connector to make the client. Otherwise, it checks for the key, wraps it as an `Api-Token` header, and passes that to the parent client builder. If there is no key, it raises an error before any network call is attempted.

**Call relations**: The broader connector framework calls this when a sync run needs a client for an ActiveCampaign tenant. This method adapts the credential format, then delegates the actual client construction to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method translates the system’s stream name into the exact ActiveCampaign URL path and response key. It matters because some ActiveCampaign names use camelCase, such as `campaignMessages`, while the local stream names use snake_case, such as `campaign_messages`.

**Data flow**: It receives a `StreamSpec`. It looks up that stream name in the mapping table. If there is a special ActiveCampaign spelling, it returns that path segment and envelope key; otherwise, it returns the stream name for both.

**Call relations**: `paginate` calls this before making requests. The result tells `paginate` which endpoint to call and which field inside the JSON response contains the list of records.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one ActiveCampaign stream page by page. It is the main read loop for this connector.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It resolves the API path, starts with a fixed page size, and adds an “updated after this cursor” filter when ActiveCampaign supports that stream-level filter. It then yields each page of records as a list of dictionaries. If ActiveCampaign responds with 401 or 403, it turns that into a clear `StreamSkipped` message; other HTTP errors are allowed to continue upward.

**Call relations**: The sync framework calls this when it wants records from a particular stream. `paginate` asks `_resolve_stream_segment` how ActiveCampaign names that stream, then hands the actual paging work to the shared offset-page reader supplied by the parent REST connector. Each yielded page flows back to the sync system for storage or further processing.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Ad and social platforms
These connectors ingest paid advertising, campaign structure, social account, media, and analytics data from Meta, Google Ads, and Instagram APIs.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `during source sync`

This connector is the bridge between the project and Facebook Ads. Its job is read-only: it asks Facebook for advertising data, follows Facebook's pagination links to collect all pages, and presents the results in the stream shape expected by the UFO source system. Without this file, the system would not know how to fetch Facebook ad accounts or the campaigns, ads, and performance numbers that belong to them.

The file starts by defining the available streams. A stream is one kind of data to sync, such as campaigns or ads_insights. Each stream says which field uniquely identifies a record and, when possible, which date field can be used as a cursor. A cursor is like a bookmark: it lets later syncs ask only for records newer than the last saved point.

The connector first finds all ad accounts for the current Facebook login. Then, for account-based data like campaigns, ad sets, and ads, it loops through each account and fetches that account's child records. For insights, it asks Facebook for daily ad-level metrics. On a fresh run it requests the last 90 days; on later runs it starts from the saved cursor date. It also builds a stable ID for each insight row because Facebook's insight rows do not naturally come with one suitable for storage.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one collection from Facebook's API page by page. Facebook may return only part of a result at a time, so this function keeps following the provided “next page” link until there are no more pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON response, pulls the list stored under the response's data field, and yields that list if it is not empty. If Facebook includes a paging.next URL, it uses that as the next request target; otherwise it stops.

**Call relations**: This is the low-level paging helper used by the rest of the connector. _accounts uses it to list ad accounts, while _account_children and _insights use it to walk through account-specific campaign, ad, and reporting pages. It relies on records_at to safely extract the data list from Facebook's response.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Gets all Facebook ad accounts available to the current credential. These accounts are the starting point for almost every other Facebook Ads stream, because campaigns, ads, and insights are fetched account by account.

**Data flow**: It starts with a fixed list of account fields to request, then calls _paged on /me/adaccounts. Each returned page is added to one output list. When all pages have been read, it returns the full list of account records.

**Call relations**: paginate calls this directly when the requested stream is ad_accounts. _account_children and _insights also call it first so they know which account IDs to query before fetching campaigns, ad sets, ads, or insight rows.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches account-owned objects such as campaigns, ad sets, and ads. It also filters out older records when a sync cursor is available, so repeated syncs do less unnecessary work.

**Data flow**: It receives the HTTP client, the stream being synced, and an optional cursor. First it chooses the correct Facebook fields for that stream. Then it gets all ad accounts, skips any account without a usable ID, and fetches that account's child objects. If a cursor exists, it keeps only records whose cursor field is newer than the saved cursor. Before yielding records, it adds account context such as the ad account ID and name.

**Call relations**: paginate sends campaigns, ad_sets, and ads streams here. This function depends on _accounts to discover accounts, _paged to fetch each account's pages, and with_context to attach account information so later parts of the system know where each record came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches daily advertising performance rows, such as impressions, clicks, spend, and reach, at the ad level. It turns these reporting rows into stable records by creating an ID from the account, campaign, ad set, ad, and date.

**Data flow**: It receives the HTTP client and an optional cursor. It builds Facebook query parameters for daily ad-level insights. If there is a cursor, it requests data from that cursor date through today; otherwise it asks for the last 90 days. It then loops through each ad account, fetches insight pages, adds an ad_account_id, builds a synthetic id for every row, and yields non-empty batches.

**Call relations**: paginate calls this when the ads_insights stream is requested. It uses _accounts to know which accounts to report on, _paged to follow Facebook's paginated responses, json.dumps to format Facebook's requested date range, and the current UTC date to set the end of cursor-based insight windows.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching path for the requested Facebook Ads stream. This is the main entry the source framework uses when it wants records from this connector.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. If the stream is ad_accounts, it returns the account list. If it is campaigns, ad_sets, or ads, it delegates to _account_children. If it is ads_insights, it delegates to _insights. If the stream name is unknown, it raises StreamSkipped to clearly say this connector does not implement that stream.

**Call relations**: The broader source-sync framework calls paginate when it is time to read a stream. paginate then routes the work to _accounts, _account_children, or _insights, depending on what kind of Facebook Ads data is being requested.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Lightly reshapes records before they are handed onward, mainly to make campaign records easier and more consistent to use. For most streams it leaves the record unchanged.

**Data flow**: It receives one record and the stream it belongs to. If the stream is campaigns, it returns a copy with a normalized status value and a created_at field copied from Facebook's created_time. For any other stream, it returns the original record as-is.

**Call relations**: This function is part of the connector's final record-shaping step after records have been fetched by paginate. It does not call other functions in this file; it simply prepares campaign records so downstream storage or display code sees friendlier field names.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `source sync`

Google Ads does not expose this data as simple web pages. Instead, it expects a special query language called GAQL, which is like SQL for Google Ads: the connector sends a “SELECT ... FROM ...” question and receives batches of matching rows. This file is the adapter between that Google-specific world and the project’s standard source-sync system.

The connector first prepares an HTTP client with the normal OAuth credential plus a required Google Ads developer token. That token is not the same as user login; it proves the app itself is approved to use the Ads API. If the token is missing, the connector skips the stream instead of crashing the whole sync.

For each stream, `paginate` chooses the right GAQL query. Before asking for data, it lists all customer accounts the authenticated user can access. It then runs the query for each customer and adds the customer id to every row, like putting a label on every box before mixing boxes from different rooms.

Google Ads returns nested objects, such as `campaign.name` inside a `campaign` object. `flatten` pulls the important fields up to predictable top-level names, so the sync system can find record ids, names, dates, and metrics consistently. The file is read-only; it deliberately does not create or edit ads.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token, which is required on every Google Ads API request. If the token is not present, it tells the sync system to skip Google Ads because OAuth alone is not enough.

**Data flow**: It reads two environment variables, first `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` and then `GOOGLE_ADS_DEVELOPER_TOKEN`. If one contains a value, that value comes out as the token. If neither is set, it raises a skip signal with a clear explanation instead of returning a usable token.

**Call relations**: When the connector is building its HTTP client, `_make_client` calls this function so the token can be placed on outgoing requests. If this function raises `StreamSkipped`, the Google Ads stream is stopped before any API calls that would be rejected.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Google Ads and adds the special headers Google Ads requires. It combines the project’s normal authenticated client with Google Ads-specific identity details.

**Data flow**: It receives a base URL and an OAuth credential. It asks the parent connector to build the basic client, then adds the developer token header from `_developer_token`. It also reads an optional login customer id from the environment, removes dashes from it, and adds it as another header when present. The result is a ready-to-use asynchronous HTTP client.

**Call relations**: This is part of setup before any Google Ads stream is read. It calls `_developer_token` because every later request made by `_customer_ids` and `_search_stream` depends on that header being present.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The connector needs this because most Google Ads queries must be run separately for each customer account.

**Data flow**: It receives an HTTP client and sends a request to Google Ads’ accessible-customers endpoint. From the response, it looks for resource names like `customers/1234567890`, keeps only valid strings in that shape, and returns just the customer id parts as a list.

**Call relations**: `_query_each_customer` calls this first, before running any GAQL query. The returned ids become the targets for later search requests, one customer at a time.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one GAQL query for one Google Ads customer account and collects the rows Google returns. It hides the batch-shaped response format so the rest of the connector can work with a simple list of records.

**Data flow**: It receives an HTTP client, a customer id, and a query string. It posts the query to that customer’s `searchStream` endpoint. Google may answer with several batches, each containing results, so the function walks through those batches, keeps dictionary-shaped rows, and returns them in one list.

**Call relations**: `_query_each_customer` calls this after it has a customer id from `_customer_ids`. The rows it returns are then tagged with the customer id before being yielded up to `paginate`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It is the bridge between “one query” and “all accounts this user can see.”

**Data flow**: It receives an HTTP client and a GAQL query. It first gets the list of customer ids, then runs `_search_stream` for each id. When a customer has rows, it adds that `customer_id` to every row and yields the rows as a page of results. Empty customer results are ignored.

**Call relations**: `paginate` uses this helper for every supported stream. Internally it depends on `_customer_ids` to find accounts and `_search_stream` to fetch data from each account.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the main reader for Google Ads streams. Given a stream such as campaigns or campaign metrics, it chooses the right GAQL query and yields pages of records for the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is a saved position from a previous sync. It matches the stream name to a GAQL query, then passes that query to `_query_each_customer` and yields each resulting page. For campaign metrics, it uses the cursor date when available; otherwise it starts from about 90 days ago. If the stream is unknown, or if Google refuses access with an authorization-style error, it raises `StreamSkipped` with an explanation.

**Call relations**: The sync framework calls `paginate` when it wants records for a specific Google Ads stream. `paginate` does not fetch rows directly; it delegates the repeated per-customer work to `_query_each_customer`. It also translates certain Google permission failures into a graceful skip so one bad or unapproved Ads setup does not look like an ordinary programming error.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Google Ads’ nested response rows into flatter records with stable keys the sync system can identify and compare. It makes fields like ids, names, dates, and metrics easier for the rest of the project to read.

**Data flow**: It receives one raw record and the stream it belongs to. For customer, campaign, and campaign-metric records, it safely pulls nested pieces out of objects such as `customer`, `campaign`, `segments`, and `metrics`, then returns a copy of the record with important top-level fields added. For other streams, it returns the record unchanged.

**Call relations**: After `paginate` has produced raw Google Ads rows, the source-sync machinery can call `flatten` before storing or indexing them. It uses `dict_or_empty` so missing or oddly shaped nested objects become empty dictionaries instead of causing simple field extraction to fail.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `during source sync, when Instagram streams are read from the Facebook Graph API`

Instagram business data is not fetched directly from Instagram here. It comes through Facebook Pages, because Instagram business accounts are linked to Pages in Facebook’s Graph API. This connector starts by asking Facebook for the user’s Pages, then finds any Instagram business account attached to each Page. From those accounts it can read media posts, stories, and several kinds of insight data, which means counts and measurements such as reach, impressions, replies, or profile views.

The file is built around streams. A stream is a named category of records the sync system can ask for, such as “media” or “user_insights.” The main `paginate` method is the traffic director: given a stream name, it chooses the right helper and yields batches of records.

The connector also deals with the Graph API’s paging style. Facebook returns a list of records plus a “next” link when more records are available. `_paged` follows those links like turning pages in a book until there are no more. For media, stories, and user insights, it supports a cursor, which is a saved timestamp used to avoid rereading older records.

Some failures are treated gently. If an individual post or story refuses insight access, the connector skips that object and keeps going. If the whole account refuses access because the token is invalid or lacks permission, the stream is marked as skipped rather than crashing the entire run.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Facebook Graph API collection from start to finish, following each “next page” link until there are no more results. It is the shared helper for API endpoints that return records in pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It asks the API for that path, pulls the `data` list out of the response, yields that list if it is not empty, then looks for a `paging.next` URL and repeats. The output is a sequence of record batches, not one giant list.

**Call relations**: `_pages` uses this to walk `/me/accounts`, and `_account_collection` uses it to walk each Instagram account’s media or stories. It relies on `records_at` to safely pull the `data` records out of the JSON response.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the current grant, including each Page’s linked Instagram business account when one exists. This is the starting point for almost every other Instagram read in this connector.

**Data flow**: It builds a field list asking for Page identity and embedded Instagram account details, then calls `_paged` on `/me/accounts`. Each returned page of Page records is added to one list, and the final result is that full list of Pages.

**Call relations**: `paginate` calls this directly when the requested stream is `pages`. `_instagram_accounts` also calls it because Instagram accounts are discovered through Pages, not from a separate top-level account list.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the Instagram business accounts linked to the fetched Facebook Pages. It also adds the Page ID and Page name beside each Instagram account so later records can be traced back to their Page.

**Data flow**: It first asks `_pages` for all Pages. For each Page, it looks for an `instagram_business_account` object with an ID. Valid accounts are stored by ID to avoid duplicates, enriched with `page_id` and `page_name`, and returned as a list.

**Call relations**: `paginate` uses this for the `instagram_accounts` stream. `_account_collection` uses it before reading account-specific media or stories, and `_user_insights` uses it before reading account-level analytics.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a collection that belongs to each Instagram account, such as media posts or stories. It can also filter out records that are older than the saved cursor timestamp.

**Data flow**: It receives an API path suffix like `media` or `stories`, a list of fields to request, and optional cursor information. It gets all Instagram accounts, asks the API for the chosen collection under each account, removes records at or before the cursor when requested, adds the account ID as context to the remaining records, and yields them in batches.

**Call relations**: `paginate` calls this when syncing the `media` and `stories` streams. It depends on `_instagram_accounts` to know which accounts to visit, `_paged` to walk each API collection, and `with_context` to stamp each record with its `instagram_account_id`.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads analytics for individual objects, such as a media post or story. It turns API insight rows into records that clearly point back to the object they describe.

**Data flow**: It receives an async stream of object batches, a metric list, and the name of the insight stream being produced. For each object with an ID, it asks `/{object_id}/insights` for the requested metrics. Each returned insight is given a stable ID made from the object ID and metric name, plus a `parent_external_id` linking it back to the original object, then the insight records are yielded in batches.

**Call relations**: `paginate` uses this for `media_insights` and `story_insights`. In those cases, `paginate` first creates a media or story stream, then hands that stream into `_object_insights` so analytics are read object by object. It uses `records_at` to pull the API’s `data` list out of each insight response. If one object’s insights are unavailable because of common permission or missing-object errors, it skips that object and continues.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads daily account-level analytics for each Instagram business account, such as impressions, reach, and profile views. These are different from post or story insights because they describe the account as a whole.

**Data flow**: It starts with all linked Instagram accounts. For each account, it asks the API for daily insight metrics. The response contains insight names, each with a list of dated values; the function turns every value into its own record, skips values at or before the cursor, gives each record a stable ID based on account, metric, and end time, and yields batches of new rows.

**Call relations**: `paginate` calls this when syncing the `user_insights` stream. It relies on `_instagram_accounts` to find accounts, `records_at` to read the top-level insight records, and `list_or_empty` to safely treat missing or invalid `values` fields as an empty list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main entry point the sync engine uses to read any Instagram stream. Given a stream definition and a saved cursor, it chooses the correct helper and yields batches of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and passes through each batch of records that helper produces. If the stream name is unknown, it reports the stream as skipped. If Facebook refuses access with an authentication or permission status, it converts that into a skip message instead of a hard failure.

**Call relations**: The wider source runner calls `paginate` whenever it wants records for one Instagram stream. `paginate` then dispatches to `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, or `_user_insights` depending on the stream. For insight streams tied to posts or stories, it composes helpers: it first asks its own media or story pagination to provide objects, then hands those objects to `_object_insights` for metric lookup.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Lifecycle marketing platforms
Klaviyo and Mailchimp connectors normalize audiences, campaigns, reports, activity, and nested marketing records into syncable streams.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `during Klaviyo source sync`

Klaviyo returns many kinds of marketing data: profiles, email lists, campaigns, events, catalog items, reviews, and more. This file is the connector that lets the wider system pull that data in a consistent way. Without it, the system would not know Klaviyo’s login style, its required API version header, its page-by-page response format, or where important fields live inside Klaviyo’s nested JSON records.

The file first declares the available streams. A stream is one type of thing to read, such as “profiles” or “campaigns.” Each stream says what field uniquely identifies a record and, when possible, what time field should be used for incremental syncing. Incremental syncing means “only ask for records changed since the last successful run,” like checking only the mail that arrived after yesterday.

The `KlaviyoConnector` then customizes the shared REST connector. It adds Klaviyo’s required headers, builds the first query for each stream, follows Klaviyo’s `links.next` pagination links, and treats permission errors as skipped streams rather than a total crash. It also flattens records: Klaviyo wraps useful data under `attributes` and `relationships`, so this connector lifts important pieces like consent status, campaign subject lines, event metric names, and related profile IDs into easy-to-read top-level fields.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a stream description for one kind of Klaviyo data, such as profiles, events, or campaigns. It keeps the long stream list readable by filling in common defaults while still allowing special cases.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, and timestamp fields. It packages those choices into a `StreamSpec`, which is the shared object the rest of the sync system uses to know how that stream should be read.

**Call relations**: The file uses this helper while building the Klaviyo stream catalog. Each call produces one stream entry that the `KlaviyoConnector` later exposes through its `streams_list`.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client so requests to Klaviyo include the headers Klaviyo expects. In plain terms, it puts the right key and API version label on every outgoing request.

**Data flow**: It receives a base URL and a resolved credential. It starts with the normal REST client, adds Klaviyo’s pinned `revision` header, and, when a direct private key is available, adds Klaviyo’s `Authorization` header. It returns the ready-to-use asynchronous HTTP client.

**Call relations**: The broader REST connector calls this when setting up communication with Klaviyo. After this point, later fetches made by pagination use the client with Klaviyo-specific authentication already attached.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Klaviyo’s full “next page” URL into the path-and-query form needed by the already configured HTTP client. It is a small adapter between Klaviyo’s pagination style and this project’s REST client style.

**Data flow**: It receives a possible `links.next` value. If there is no link, it returns nothing. If there is a link, it parses the URL, keeps only the path and query string, and returns that smaller request target.

**Call relations**: During pagination, `KlaviyoConnector.paginate` reads the next-page link from each response and asks this helper to convert it. The returned path becomes the next request; a missing result stops the loop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This chooses the correct timestamp field to use when asking Klaviyo for records in time order. Klaviyo does not use the same field name for every resource, so this avoids building the wrong filter.

**Data flow**: It receives a stream description. It checks the stream name against known special cases: events use `datetime`, campaigns/forms/images use `updated_at`, and most others use `updated`. It returns the field name to use in filters and sorting.

**Call relations**: This helper supports the first-query builder. When the connector starts reading a stream, the query needs the right timestamp name so incremental syncs resume from the correct place.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for the first request to a Klaviyo stream. It sets page size, sorting, optional incremental filters, and a few stream-specific extras.

**Data flow**: It receives a stream description and an optional saved cursor, meaning the last timestamp already synced. It creates query parameters with Klaviyo’s page size. If the stream supports incremental syncing, it sorts by the right time field and, when a cursor is present, filters to records at or after that cursor. It also asks for profile subscription details on profiles and metric details on events. It returns the parameter dictionary for the first API call.

**Call relations**: At the start of `KlaviyoConnector.paginate`, this function supplies the parameters for the first page. Later pages do not reuse these parameters because Klaviyo’s own `links.next` URL already contains what is needed.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This safely extracts the ID of a related Klaviyo object from a nested relationship block. It is defensive because Klaviyo may leave relationships empty or omit them entirely.

**Data flow**: It receives a relationships object and the relationship name to look for, such as `profile` or `metric`. It checks each nested layer before reading it. If it finds `relationships[key].data.id`, it returns that ID as text; otherwise it returns nothing.

**Call relations**: `KlaviyoConnector.flatten` calls this when turning records into flatter shapes. It is used for fields like an event’s profile ID, an event’s metric ID, or a segment’s parent list ID.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This converts Klaviyo’s nested JSON record format into a flatter record that is easier for the rest of the system to store, search, and advance cursors from. It also pulls out a few useful buried details that would otherwise be hard to recall later.

**Data flow**: It receives one raw Klaviyo record and the stream it came from. It starts a new record with the ID and resource type, copies all `attributes` fields to the top level, removes list and segment profile counts so membership count changes do not make those records look changed, then adds stream-specific fields. For profiles it extracts email consent and suppression status. For campaigns it extracts subject and sender details. For events it adds related profile and metric IDs plus message or flow references. For segments it adds a parent list ID when present. It returns the flattened record.

**Call relations**: After records are fetched from Klaviyo, the connector framework can call this before storing or indexing them. Inside this process it relies on `_lift_relationship_id` to read related-object IDs without failing on missing relationship data.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Klaviyo stream page by page until there are no more pages. It is the main loop that turns Klaviyo’s paginated API responses into batches of records for the sync system.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor. It builds the first request path and query, fetches data, yields each non-empty batch of records, then follows Klaviyo’s `links.next` value to continue. For event records, it also looks at included metric objects and copies the metric name into each event’s attributes when possible. If Klaviyo returns a permission refusal, it raises `StreamSkipped` so that stream is recorded as skipped rather than bringing down the whole run.

**Call relations**: This function is called when the sync system wants records from a particular Klaviyo stream. It asks `_initial_query` how to start, uses `_next_path` to move from one page to the next, and hands each fetched batch back to the caller through asynchronous iteration.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `request handling`

Mailchimp stores marketing data behind a web API, but the data is not all reached in the same way. Some things, like lists and campaigns, are fetched directly. Others live underneath a parent item: subscribers live under a list, interests live under a list and then a category, and unsubscribe or email activity data lives under a campaign report. This file is the map and walking guide for all of those routes.

The connector defines the Mailchimp streams the product can read, including their important identity field and optional time field used for incremental syncs. An incremental sync means “only ask Mailchimp for records changed since the last run,” when Mailchimp supports that filter.

The central method, `paginate`, acts like a dispatcher at a train station. It looks at the requested stream name and sends the sync down the right track: direct top-level paging, per-list paging, per-report paging, or deeper nested paging. Each helper then repeatedly requests pages using Mailchimp’s offset-and-count style pagination until there are no more full pages.

A few details make the output easier to use later. Child records are stamped with their parent list, segment, category, or campaign ID so their origin is not lost. Email activity is expanded from “one recipient with many actions” into “one row per action,” with a stable synthetic ID because Mailchimp does not provide one. If Mailchimp refuses access with an authorization error, the stream is skipped with a clear message rather than failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper creates a `StreamSpec`, which is the small description the sync engine uses to know what a Mailchimp stream is called, how to identify each record, and which time fields matter. It keeps the stream list readable by avoiding repeated setup code.

**Data flow**: It receives a stream name and optional details such as source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults where details are not provided, then returns a configured `StreamSpec` object for the rest of the connector to use.

**Call relations**: The file uses this helper while building the Mailchimp stream catalog. It hands each finished stream description to the connector class through `MAILCHIMP_STREAMS`, so later sync code can ask for streams by name.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function makes subscriber-like records easier for the rest of the system to understand by setting a normal `created_at` value. Mailchimp may store signup time under different field names, so this fills the common field from the best available Mailchimp timestamp.

**Data flow**: It receives one Mailchimp record and the stream description for that record. If the stream is for list members or segment members, it copies the record and adds `created_at` from `timestamp_signup` or, if that is missing, `timestamp_opt`; for all other streams it returns the record unchanged.

**Call relations**: This fits into the connector’s normalization step after records have been fetched. It does not call other helpers here; it simply prepares certain Mailchimp records so downstream storage and comparison logic can rely on a common creation-time field.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This helper answers a simple question: inside Mailchimp’s JSON response, which key contains the list of records we actually want? Mailchimp uses different wrapper names, such as `lists`, `members`, or `emails`, so the connector needs this lookup.

**Data flow**: It receives a stream description, reads the stream name, and checks the file’s mapping of stream names to Mailchimp response keys. It returns the matching response key, or the stream name itself if no special key is listed.

**Call relations**: The top-level, per-list, and per-report pagination helpers call this before requesting pages. They then pass the returned key into the generic page reader so it can pull the actual records out of Mailchimp’s response envelope.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the Mailchimp query parameter used for incremental syncing when possible. In plain terms, it says, “If we already synced up to this time, ask Mailchimp only for newer records.”

**Data flow**: It receives a stream description and an optional cursor value, usually a saved timestamp from the previous sync. If there is no cursor or the stream has no matching Mailchimp filter, it returns an empty parameter set; otherwise it returns the correct `since...` parameter with the cursor value.

**Call relations**: Pagination helpers call this before fetching streams that can be filtered by time. The returned parameters are handed to the page-fetching helper so Mailchimp can do part of the filtering before data reaches this system.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing function for reading a Mailchimp stream. Given a stream name, it chooses the correct paging strategy and yields batches of records until that stream is fully read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor timestamp. It checks the stream name, calls the matching pagination helper, and yields each page that helper produces. If Mailchimp returns an access-denied response, it turns that into a clear `StreamSkipped` error explaining that the grant or key is not allowed to read the stream.

**Call relations**: The broader sync engine calls this when it wants records for one Mailchimp stream. This function then hands work to specialized helpers for top-level resources, list children, report children, interests, segment members, or email activity, depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Mailchimp resources that are available directly, such as lists, campaigns, automations, and reports. These are the simplest streams because they do not need a parent list or report ID first.

**Data flow**: It receives an HTTP client, stream description, API path, and optional cursor. It chooses the correct response field and cursor query parameters, then asks the shared offset-page reader for pages of records. Each page it receives is yielded onward unchanged.

**Call relations**: `paginate` calls this for direct Mailchimp streams. This helper uses `_data_field` and `_cursor_params` to shape the request, then relies on the base connector’s page-reading machinery to do the actual repeated HTTP requests.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable page walker for nested Mailchimp endpoints. It is used whenever the connector already knows a specific child URL, such as members inside one list or unsubscribes inside one report.

**Data flow**: It receives an HTTP client, a path, the JSON field where records live, and optional query parameters. It repeatedly asks the base connector for offset-based pages using Mailchimp’s `count` parameter, then yields each page of child records.

**Call relations**: Several more specific helpers call this after they have built the correct nested path. It keeps those helpers focused on finding parent IDs and stamping parent information, while this helper does the common page-by-page reading.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This helper fetches a collection and yields only the item IDs from it. It is used as a small building block when the connector must first discover parent records before fetching their children.

**Data flow**: It receives an HTTP client, an API path, and the response field that contains records. It pages through that collection, looks at each row, and yields the row’s `id` as text when one exists. Rows without an ID are ignored.

**Call relations**: `_list_ids` and `_report_ids` call this to avoid duplicating ID-extraction logic. Those ID streams then feed the deeper pagination helpers that need list IDs or report IDs to build child API paths.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function provides the IDs of all Mailchimp lists, also called audiences. Many Mailchimp records are stored underneath a list, so those list IDs are the starting point for several deeper reads.

**Data flow**: It receives an HTTP client and asks `_ids` to read the Mailchimp lists endpoint. As `_ids` finds list IDs, this function yields them one by one to its caller.

**Call relations**: Per-list pagination, interest pagination, and segment-member pagination call this before fetching child records. It supplies the parent list IDs they need to form Mailchimp URLs like “members for this list.”

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function provides the IDs of Mailchimp campaign reports. Report IDs are needed before the connector can read report-specific details such as unsubscribes and email activity.

**Data flow**: It receives an HTTP client and asks `_ids` to read the reports endpoint. As report IDs are found, it yields each one to the caller.

**Call relations**: Per-report pagination and email-activity pagination call this first. It supplies the parent campaign report IDs they use to build the nested report URLs.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that live directly under each Mailchimp list, such as members, segments, tags, or interest categories. It also marks each child record with the list it came from so that relationship is not lost.

**Data flow**: It receives an HTTP client, stream description, child path name, optional cursor, and the name of the parent field to stamp. It gets all list IDs, builds a child URL for each list, fetches pages from that URL, optionally adds the list ID to every record, and yields the pages onward.

**Call relations**: `paginate` calls this for streams that are one level below a list. This helper uses `_list_ids` to find parents, `_data_field` and `_cursor_params` to shape child requests, `_paginate_child` to read pages, and URL quoting to safely place list IDs inside paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads interests, which are nested more deeply than most list data. In Mailchimp, an interest belongs to an interest category, and that category belongs to a list.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It walks through every list, fetches that list’s interest categories, then fetches the interests inside each category. Each interest record is stamped with its list ID and category ID before the page is yielded.

**Call relations**: `paginate` calls this specifically for the `interests` stream. It depends on `_list_ids` to find lists and `_paginate_child` to read both category pages and interest pages, using URL quoting so IDs are safe in web paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads the members inside each segment of each Mailchimp list. It preserves both parent links, adding the list ID and segment ID to each member record.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It gets each list ID, fetches that list’s segments, then fetches members for each segment. It adds `list_id` and `segment_id` to member records and yields each resulting page.

**Call relations**: `paginate` calls this for the `segment_members` stream. The function uses `_cursor_params` for incremental filtering, `_list_ids` for parent lists, `_paginate_child` for both segment and member pages, and URL quoting when building nested Mailchimp paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that live under campaign reports, such as unsubscribe records. It adds the campaign report ID to each child record so the record can be traced back to its report.

**Data flow**: It receives an HTTP client, stream description, child path, optional cursor, and an optional parent field name. It gets all report IDs, builds a child endpoint for each report, fetches pages from that endpoint, stamps each row with the campaign ID when requested, and yields the pages.

**Call relations**: `paginate` calls this for report child streams. It uses `_report_ids` to find parent reports, `_data_field` and `_cursor_params` to shape requests, `_paginate_child` to read the pages, and URL quoting to safely include report IDs in paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads per-recipient email activity from campaign reports and turns it into one row per action, such as an open or click. That reshaping matters because Mailchimp groups many actions under one recipient, while the sync system needs stable individual records.

**Data flow**: It receives an HTTP client and optional cursor. It builds a `since` filter when a cursor is present, gets every report ID, fetches email activity pages for each report, and then expands each recipient’s `activity` list into separate rows. Each output row keeps the recipient fields, gains the action fields, gets the campaign ID, and receives a synthetic ID made from email ID, action, and timestamp when Mailchimp did not provide one.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to find reports and `_paginate_child` to fetch each report’s activity, then performs the special expansion step before yielding pages to the sync engine.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Form response collection
Typeform closes the stage by ingesting forms, responses, workspace metadata, themes, media, and webhook configuration.

### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync runs`

Typeform stores survey and form data behind a web API, and that API does not return everything at once. This connector is the piece that knows how to ask Typeform for each kind of data, follow Typeform's paging rules, and give the rest of UFO clean batches of records. Without it, UFO would not know where Typeform's endpoints are, how to continue from one page to the next, or how to connect responses and webhooks back to the form they belong to.

The file defines the available Typeform streams, including forms, responses, workspaces, images, themes, and webhooks. A stream is a named kind of data the sync runner can request. The main class, TypeformConnector, is read-only: it fetches data but does not write anything back to Typeform.

Most streams use normal page numbers: ask for page 1, then page 2, and so on until Typeform says there are no more. Responses are different. They live under each form, so the connector first lists all forms, then asks for responses for each form. It also supports incremental syncing, meaning it can ask only for records newer than a saved cursor value. When Typeform refuses access with a permission or authentication error, the connector marks that stream as skipped instead of treating the whole sync as a mysterious failure.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for Typeform streams. Given a requested stream, it chooses the right fetching method and yields batches of records for the sync runner.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that represents the last synced point. It checks the stream name, sends the work to the matching helper, and passes each returned batch onward. If Typeform refuses access with a 401 or 403 response, it turns that into a clear “stream skipped” message; if the stream is unknown, it also skips it.

**Call relations**: The sync framework calls this when it wants records from Typeform. It delegates forms to TypeformConnector._forms, responses to TypeformConnector._responses, simple paged collections to TypeformConnector._paged_items, and webhooks to TypeformConnector._webhooks. When a stream cannot be read because of missing permission or an invalid key, it raises StreamSkipped so the larger sync can continue safely.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use ordinary numbered pages. It keeps asking for the next page until Typeform says the collection is finished.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It adds a page number and page size, sends a GET request through the connector's shared request method, pulls the list of records from the response's “items” field, and yields that list when it is not empty. It stops when Typeform's page count has been reached, or when a short page suggests there are no more records.

**Call relations**: TypeformConnector.paginate uses this directly for collections like workspaces, images, and themes. TypeformConnector._forms also uses it as the basic way to list forms before applying its own cursor filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and, when asked, filters them to only those updated after a saved cursor. It is the foundation for other form-based streams.

**Data flow**: It receives an HTTP client and an optional cursor string. It gets pages of forms from TypeformConnector._paged_items, compares each form's last_updated_at value to the cursor when one is present, and yields only the remaining forms. It does not change the forms except for possibly removing older ones from the batch.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is forms. TypeformConnector._responses and TypeformConnector._webhooks also call it first, because both responses and webhooks must be fetched one form at a time.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted answers for every Typeform form. It also labels each response with the form's id and title so the response is not separated from its context.

**Data flow**: It receives an HTTP client and an optional cursor. First it loads all forms without filtering them by form update time, because a form can receive new responses even if the form itself was not recently edited. For each valid form id, it requests response pages from that form's response endpoint. If a cursor is present, it sends it as a “since” filter so Typeform returns newer submissions. Each response batch is then enriched with form_id and form_title before being yielded.

**Call relations**: TypeformConnector.paginate calls this for the responses stream. Inside, it relies on TypeformConnector._forms to discover which forms exist, then uses the base connector's cursor-page fetching behavior to walk through response pages. It uses with_context to attach form details before handing records back to the caller.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches webhook settings attached to each Typeform form. A webhook is a saved instruction telling Typeform to notify another system when something happens, such as a new response.

**Data flow**: It receives an HTTP client. It first lists forms, then skips any form without a usable id. For each valid form, it asks Typeform for that form's webhooks, pulls the records from the response's “items” field, adds the form id and title to each record, and yields non-empty batches.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is webhooks. It depends on TypeformConnector._forms to find the forms to inspect, records_at to extract webhook records from Typeform's response shape, and with_context to keep each webhook tied to its parent form.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).

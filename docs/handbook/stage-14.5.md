# Marketing, advertising, and lifecycle source connectors  `stage-14.5`

This stage is a set of source connectors: small adapters that let the system pull data from outside marketing tools. It is part of the main syncing work, not startup or shutdown. Each connector knows how to sign in to one service, ask for the right kinds of data, handle pages of results, and turn the replies into standard records the rest of the system can store, search, and reuse.

ActiveCampaign brings in marketing and customer-relationship objects, while safely skipping API areas that refuse access. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google advertiser accounts, including campaigns, ad groups, ads, and metrics. Instagram uses Meta’s API to collect business pages, linked accounts, posts, stories, and analytics. Klaviyo covers email and ecommerce marketing data such as profiles, lists, campaigns, events, forms, catalog items, and webhooks. Mailchimp reads audiences, subscribers, campaigns, reports, tags, and email activity. Typeform brings in forms, responses, workspaces, themes, images, and webhook settings. Together, they act like translators for different marketing systems.

## Files in this stage

### Marketing CRM source
Defines the ActiveCampaign connector for syncing marketing and CRM objects with authentication, pagination, and stream-skipping behavior.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `during source sync, when connecting to ActiveCampaign and reading paged API data`

ActiveCampaign exposes many different kinds of data: contacts, lists, campaigns, deals, accounts, custom fields, webhooks, users, and more. This file turns those API resources into named “streams,” which are repeatable feeds of records the wider sync system can pull in. Without this file, the system would not know what ActiveCampaign data exists, which date field to use for incremental syncing, or how to call ActiveCampaign’s version 3 API.

The file first records the common rules of the API. ActiveCampaign returns list results inside an envelope, such as `{contacts: [...], meta: ...}`, and uses `limit` plus `offset` paging, like reading a long book 100 lines at a time. It also has slightly different URL names for some compound resources, such as `campaignMessages`, so the file keeps a lookup table that maps the system’s stream names to ActiveCampaign’s path and response names.

The `_stream` helper builds the stream descriptions used by the connector. `ACTIVECAMPAIGN_STREAMS` is the catalog of everything this connector can read. `ActiveCampaignConnector` then supplies the runtime behavior: it converts the stored API key into ActiveCampaign’s required `Api-Token` header, resolves each stream’s API path, walks through pages of results, adds an incremental “changed after this time” filter when ActiveCampaign supports one, and turns permission or invalid-key errors into a clean skipped-stream signal.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one ActiveCampaign stream, such as contacts or campaigns. It keeps the long stream catalog readable by filling in common defaults like the primary key and date fields.

**Data flow**: It receives a stream name plus optional details such as the ActiveCampaign source object name, the primary key field, the cursor field used for incremental syncing, and whether the stream is canonical. It uses those values to build and return a `StreamSpec`, which is the system’s compact description of how that stream should be treated.

**Call relations**: The file calls this helper repeatedly while building `ACTIVECAMPAIGN_STREAMS`. Each returned `StreamSpec` is later used by `ActiveCampaignConnector.paginate` to know which ActiveCampaign resource to request and which cursor rules apply.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to ActiveCampaign, with authentication in the form ActiveCampaign expects. ActiveCampaign uses an `Api-Token` header rather than the more common bearer-token header.

**Data flow**: It receives a base URL and a credential. If the credential already has a special transport, such as a brokered or proxied connection, it leaves that setup alone and lets the parent connector build the client. Otherwise it reads the credential’s API key, places it into an `Api-Token` header, and returns an asynchronous HTTP client ready to make requests. If no API key is present, it raises an error instead of making unauthenticated calls.

**Call relations**: The wider connector framework calls this when a sync run needs a network client for ActiveCampaign. This method adapts the project’s generic credential shape into ActiveCampaign’s particular authentication style, then hands the final client creation back to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method translates the system’s stream name into the exact ActiveCampaign API path segment and response envelope key. It exists because some streams use snake_case internally but camelCase in ActiveCampaign’s API.

**Data flow**: It receives a `StreamSpec`. It looks up the stream name in the `_STREAM_PATHS` table. If there is a match, it returns the configured API path name and response key; if not, it falls back to using the stream name for both.

**Call relations**: It is called by `ActiveCampaignConnector.paginate` just before making API requests. Pagination needs this translation so it can request the right URL and pull records from the right field in the JSON response.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads one ActiveCampaign stream page by page. It applies incremental filters when the API supports them, yields batches of records, and reports permission problems as skipped streams rather than crashing the whole sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It resolves the stream’s API path, builds request parameters with a page size of 100, and, when possible, adds a filter asking ActiveCampaign for records changed after the cursor. It then asks the shared REST paging helper for offset-based pages and yields each page of records. If ActiveCampaign returns 401 or 403, meaning unauthorized or forbidden, it raises `StreamSkipped`; other HTTP errors are re-raised.

**Call relations**: During a sync, the connector framework calls this method for each selected stream. It calls `_resolve_stream_segment` to understand ActiveCampaign’s naming, delegates the repeated offset requests to the inherited `_get_offset_pages` helper, and hands each resulting page back to the sync pipeline. When ActiveCampaign refuses a stream, it uses `StreamSkipped` so the larger run can treat that stream as unavailable rather than as ordinary data.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Advertising and social analytics
Covers paid advertising and social-platform connectors that fetch account, campaign, creative, post, story, and performance data.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `source sync runs`

This connector is the system's read-only doorway into Facebook Ads. Without it, the platform would not know which Facebook API addresses to call, how to move through Facebook's paged responses, or how to turn account-level ad data into consistent records for syncing.

The file defines the available Facebook Ads streams first: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is one kind of data the sync system can ask for. Each stream says what object to read, which field uniquely identifies a record, and which date field can be used as a cursor. A cursor is like a bookmark: it helps the next sync start from the last known point instead of rereading everything.

The `FacebookAdsConnector` then supplies the actual reading behavior. It uses the Facebook Graph API, follows Facebook's `paging.next` links until there are no more pages, and always starts by listing the user's ad accounts. Campaigns, ad sets, ads, and insights are then fetched separately for each account, because Facebook stores those objects under account-specific API paths.

For changing objects like campaigns, it filters out records older than the saved cursor. For daily insight rows, it requests either the last 90 days on a fresh sync or the date range from the cursor to today. The connector does not write anything back to Facebook; it is intentionally a source-only reader.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Facebook API collection that may span many pages. It follows Facebook's own “next page” link until the collection is finished, yielding each batch of records as it arrives.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the raw GET request method, reads the JSON response, extracts the list stored under `data`, and yields that list when it is not empty. If Facebook includes a `paging.next` URL, it uses that as the next request target; once there is no next link, it stops.

**Call relations**: This is the shared paging engine for the connector. Account listing, account child collections, and insights all call it when they need to read a Facebook endpoint that may return more than one page. It relies on `records_at` to safely pull the `data` list out of the response.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function gets the Facebook ad accounts available to the current credential. Other reads depend on it because campaigns, ads, and insights are all fetched one ad account at a time.

**Data flow**: It starts with a fixed list of useful account fields, such as name, currency, time zone, and creation time. It asks `_paged` to read `/me/adaccounts`, collects every returned page into one list, and returns that full list of account records.

**Call relations**: This is the connector's first step for most streams. `paginate` calls it directly for the `ad_accounts` stream, while `_account_children` and `_insights` call it first so they know which account-specific Facebook API paths to visit.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads account-owned objects: campaigns, ad sets, or ads. It also applies the sync cursor so old records can be skipped when possible.

**Data flow**: It receives the HTTP client, the requested stream, and an optional cursor. It chooses the correct Facebook fields for that stream, fetches all accessible ad accounts, and then reads the matching child endpoint under each account. For each page, it removes records whose cursor field is not newer than the saved cursor, then adds account context such as the ad account id and name before yielding the page.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. It depends on `_accounts` to find the accounts to scan, `_paged` to walk through Facebook's paged API responses, and `with_context` to attach account information so downstream records still show where they came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads daily ad performance statistics, such as impressions, clicks, spend, reach, and click-through rate. It builds stable row ids because Facebook insight rows are report rows, not ordinary objects with their own permanent id.

**Data flow**: It receives the HTTP client and an optional cursor. It builds a Facebook insights request at ad level, with one row per day. If there is a cursor, it asks for data from that cursor date through today; otherwise it asks for the last 90 days. For each ad account, it reads insight pages, creates an `id` by joining the account, campaign, ad set, ad, and date values, adds the ad account id, and yields the resulting rows.

**Call relations**: `paginate` calls this for the `ads_insights` stream. It uses `_accounts` to know which accounts to query and `_paged` to follow Facebook's paginated responses. It also uses JSON encoding to send Facebook the date range in the format its API expects, and the current UTC date to close an incremental sync window.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector's main dispatcher for reading a stream. Given a stream name, it chooses the correct reading path and yields pages of records back to the sync system.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. For ad accounts, it returns the account list directly. For campaigns, ad sets, and ads, it delegates to `_account_children`. For ad insights, it delegates to `_insights`. If the stream name is unknown, it raises a skip signal instead of pretending it can read it.

**Call relations**: The broader source framework calls this when it wants records for one Facebook Ads stream. This method is the traffic director: it sends each supported stream to the specialized helper that knows how to read it, and it uses `StreamSkipped` to clearly report unsupported streams.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly normalizes records after they are fetched. At present, it gives campaign records a simpler status and creation field shape while leaving other streams unchanged.

**Data flow**: It receives one record and the stream it belongs to. If the stream is `campaigns`, it returns a copy of the record with `status` set to the effective status when available and `created_at` copied from Facebook's `created_time`. For every other stream, it returns the original record as-is.

**Call relations**: The source framework can call this after pagination to prepare records for storage or comparison. It does not fetch more data; it is a final cleanup step, especially for campaign records whose most useful status may live in Facebook's `effective_status` field.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `source sync`

Google Ads does not expose this data as simple pages of JSON. Instead, the connector must send GAQL queries, which are SQL-like questions such as “select these campaign fields from campaigns,” to each accessible advertiser account. This file is the bridge between that Google-specific world and the project’s normal source-sync system.

The connector first prepares an HTTP client with the special headers Google Ads requires. OAuth proves who the user is, but Google Ads also requires a developer token, which is like an approved app badge. If that token is missing, the connector skips the stream instead of crashing the whole sync.

For each stream, such as campaigns or campaign metrics, `paginate` chooses the right GAQL query. It asks Google which customer accounts are accessible, runs the query for each customer, and stamps the customer id onto every returned row so later code knows where the data came from. Metrics are treated specially: if there is no saved cursor, the connector only asks for the last 90 days.

Finally, `flatten` reshapes Google’s nested response objects into simpler top-level fields, such as `resource_name`, `name`, `date`, and metric counts. Without this file, the system would not know how to talk to Google Ads or how to turn its API answers into usable source records.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token needed for every Google Ads API request. Google requires this token in addition to the user’s OAuth permission, so the connector cannot safely read data without it.

**Data flow**: It reads environment variables looking first for `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` and then for `GOOGLE_ADS_DEVELOPER_TOKEN`. If it finds a token, it returns that text. If it finds nothing, it raises a skip signal so this Google Ads stream is skipped with a clear explanation instead of failing mysteriously.

**Call relations**: When the connector is building its HTTP client, `_make_client` calls this function to get the required developer token. If the token is missing, the skip signal travels back through setup and prevents requests that Google would reject anyway.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to call Google Ads and adds the headers Google expects. It makes sure every request carries the developer token and, when configured, the manager account id used for login context.

**Data flow**: It receives a base URL and a credential object from the wider source runner. It starts with the normal REST client from the parent connector, adds the developer token header, optionally reads a login customer id from the environment, removes dashes from that id, and stores it as another header. It returns the prepared asynchronous HTTP client.

**Call relations**: This is part of connector setup before any stream is read. It calls `_developer_token` because Google Ads refuses requests without that app-level token, then hands the configured client to later pagination and query functions.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The connector needs this list because most Google Ads data must be queried separately for each customer account.

**Data flow**: It receives an HTTP client and calls Google’s accessible-customers endpoint. From the response, it looks for resource names shaped like `customers/1234567890`, extracts just the id part, and returns a list of customer id strings. If the response is not in the expected shape, it quietly returns an empty list.

**Call relations**: `_query_each_customer` calls this first, before running any actual stream query. The returned customer ids become the route map for asking the same GAQL question across all accounts the user can see.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function sends one GAQL query to one Google Ads customer account and collects the result rows. It hides the Google Ads detail that search results come back as batches inside a top-level array.

**Data flow**: It receives an HTTP client, a customer id, and a GAQL query string. It posts the query to that customer’s `searchStream` endpoint, reads the JSON response, walks through each batch, and collects dictionary-shaped result rows. It returns a plain list of those rows.

**Call relations**: `_query_each_customer` calls this once per customer id. It is the low-level step that turns one selected stream query into raw Google Ads records before the connector adds customer context and later flattens the data.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It is the connector’s “visit each account” loop.

**Data flow**: It receives an HTTP client and a GAQL query. First it asks `_customer_ids` for all accessible customer ids. For each id, it calls `_search_stream` to get rows for that account. When rows exist, it adds a `customer_id` field to each row and yields that group as one page of records.

**Call relations**: `paginate` uses this function after choosing the right query for a stream. This function ties together account discovery and per-account searching, then hands pages of customer-stamped rows back to the sync process.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function decides what to ask Google Ads for each supported stream and yields the results in pages. It is the main read path for customers, campaigns, ad groups, ads, metrics, and customer-client relationships.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where a previous sync left off. Based on the stream name, it builds the matching GAQL query, then delegates to `_query_each_customer` to run it across accounts and yield pages. For campaign metrics, it uses the cursor date if available; otherwise it starts from 90 days ago. If the stream is unknown, or Google refuses access with an authorization-style error, it raises a skip signal with a useful message.

**Call relations**: The source sync system calls this when it wants records for a particular Google Ads stream. `paginate` does the stream-specific decision-making, hands the actual account-by-account work to `_query_each_customer`, and reports permission or developer-token refusals as skipped streams rather than ordinary crashes.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Google Ads records into simpler records the rest of the system can key, store, and display. Google’s API nests fields under objects like `campaign`, `segments`, and `metrics`; this pulls important values up to the top level.

**Data flow**: It receives one raw record and the stream description. For customers, it extracts an id and readable name. For campaigns, it extracts the resource name, name, status, and start date. For campaign metrics, it builds a unique id from customer, campaign, and date, and pulls out metric fields such as impressions, clicks, and cost. For streams without special shaping, it returns the record unchanged.

**Call relations**: After `paginate` has yielded raw Google Ads rows, the broader source framework calls `flatten` so downstream storage sees consistent top-level fields. It uses `dict_or_empty` to safely treat missing nested objects as empty dictionaries instead of breaking on incomplete records.

*Call graph*: 1 external calls (dict_or_empty).


### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `source sync`

Instagram business data is not fetched directly from Instagram here. It comes through Facebook Pages, because business Instagram accounts are linked to Pages in the Facebook Graph API. This connector starts at the user's Pages, finds each linked Instagram business account, then fans out to that account's media, stories, and insight numbers such as reach or impressions.

The file defines several streams, which are named lanes of data the sync system can ask for: pages, Instagram accounts, media, stories, and different kinds of insights. Think of the connector like a librarian with a route map: first find the right shelf, then collect each set of books from that shelf.

Most API results arrive in pages, meaning the API gives a batch of records plus a “next” link for the following batch. The connector follows those links until there is nothing left. For media, stories, and user insights, it uses a cursor, which is a saved last-seen timestamp, so future syncs skip older records.

A few errors are treated carefully. If an individual media or story object refuses to give insights, the connector skips that object and keeps going. If the whole account walk is refused because permissions are missing or the token is invalid, it raises a skip signal rather than crashing the entire run. This file only reads data; it does not publish or modify Instagram content.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper walks through a Facebook Graph API collection that may be split across many pages. It keeps asking for the next batch until the API says there are no more records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It requests the first page, reads the list under the response's data field, yields that list if it has records, then follows the response's next link. After the first request, it stops reusing the original query parameters because the next link already contains the needed details.

**Call relations**: The page and account collection readers call this when they need to consume a Facebook Graph API list. It relies on records_at to safely pull the data list out of the API response, then hands each batch back to its caller.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This asks Facebook for the Pages available to the current grant, including any linked Instagram business account information. It is the starting point for almost every other Instagram stream in this connector.

**Data flow**: It sends a request for the user's Pages with fields such as page id, page name, and linked Instagram account details. It collects all batches returned by _paged into one list. The result is a list of Page records, each possibly containing an Instagram business account nested inside it.

**Call relations**: paginate calls this directly when the requested stream is pages. _instagram_accounts also calls it because Instagram business accounts are discovered through Pages.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This extracts the Instagram business accounts linked to the user's Facebook Pages. It also adds Page context so each Instagram account can be traced back to the Page it came from.

**Data flow**: It starts with the Page list from _pages. For each Page, it looks for an instagram_business_account object with an id. It builds a de-duplicated dictionary keyed by Instagram account id, adds the Page id and Page name, and returns the accounts as a list.

**Call relations**: paginate uses this when syncing the instagram_accounts stream. The media, stories, and user insight paths also use it first, because they need account ids before they can ask Instagram for account-specific data.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a collection that belongs to each Instagram business account, such as media posts or stories. It can also skip records older than a saved cursor so repeated syncs do not reprocess old items.

**Data flow**: It receives the collection name, the fields to request, and optional cursor information. It first gets all Instagram accounts, then for each valid account id it asks the API for that account's collection. If a cursor is present, it filters out records whose cursor field is not newer. Before yielding each batch, it adds the Instagram account id to every record so the data keeps its parent context.

**Call relations**: paginate calls this for the media and stories streams. It gets account ids from _instagram_accounts, reads paged API responses through _paged, and uses with_context to attach the account id before handing records back to paginate.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches analytics for individual media or story objects. It turns per-object API insight responses into records that can be stored as their own insight stream.

**Data flow**: It receives batches of media or story objects, plus a list of insight metrics to request. For each object with a usable id, it asks the API for that object's insights. It skips objects that return expected permission or availability errors, but re-raises unexpected errors. For each returned insight, it creates a stable id made from the object id and metric name, records the parent object id, and yields batches of insight records.

**Call relations**: paginate calls this for media_insights and story_insights. Instead of finding objects itself, it is fed by paginate's media or stories stream, then it uses records_at to read the insight rows returned by the API.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches daily account-level analytics for each Instagram business account, such as impressions, reach, and profile views. These are different from media or story insights because they describe the account as a whole.

**Data flow**: It receives an HTTP client and an optional saved cursor. It gets all Instagram accounts, asks the API for daily insight metrics for each account, then expands each metric's values list into individual rows. Rows at or before the cursor are skipped. Each output row gets a stable id, the metric name, and the Instagram account id.

**Call relations**: paginate calls this when the requested stream is user_insights. It depends on _instagram_accounts to know which accounts to query, records_at to read the API's insight list, and list_or_empty to safely treat missing values as an empty list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for the connector. The sync system asks it for one named stream, and it routes that request to the correct helper that knows how to fetch that kind of Instagram data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it yields pages of records from the matching helper: Pages, Instagram accounts, media, stories, media insights, story insights, or user insights. If the stream is unknown, it reports that the stream is skipped. If Facebook refuses access with an authorization-style error, it converts that into a stream skip with a clear permission message; other errors still bubble up as real failures.

**Call relations**: The broader source sync runner calls paginate whenever it wants records for a stream. paginate then calls the specialized helpers in this file. For insight streams, it chains helpers together: it first obtains media or stories, then passes those objects into _object_insights so their analytics can be fetched.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Lifecycle email platforms
Defines lifecycle and email-marketing connectors that sync profiles, audiences, campaigns, reports, events, catalog data, and engagement activity.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `during Klaviyo source sync`

Klaviyo exposes its data through a web API, but that API has its own rules: special authentication headers, a required API version header, pages of results linked by `links.next`, and records wrapped in nested `attributes` and `relationships` blocks. This file is the adapter that hides those details from the rest of the system.

At the top, it defines the Klaviyo streams the system knows how to read. A stream is one kind of Klaviyo data, like profiles or campaigns. Some streams can be read incrementally, meaning the connector asks only for records updated after the last saved cursor, rather than downloading everything again.

`KlaviyoConnector` then supplies the Klaviyo-specific behavior. It builds an HTTP client with Klaviyo’s required headers, creates the first query for each stream, follows Klaviyo’s next-page links, and skips streams cleanly when the API says the current key does not have permission.

The file also reshapes each Klaviyo record. Klaviyo sends useful fields nested inside `attributes` and links to other records inside `relationships`. The `flatten` method lifts the important values into a simpler flat dictionary, like unpacking a nested filing cabinet into one readable form. This matters because later sync logic needs fields such as update time, consent status, campaign subject, metric name, and related profile IDs to be easy to find.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: Creates a small description of one Klaviyo data stream, such as profiles, campaigns, or events. The rest of the connector uses this description to know the API object name, main ID field, time fields, and whether the stream is a main searchable stream.

**Data flow**: It receives a stream name and optional details like the Klaviyo API object name, primary key, cursor field, and timestamp field names. It fills in sensible defaults when details are not provided, then returns a `StreamSpec`, which is the system’s standard recipe for syncing that stream.

**Call relations**: This helper is used while the file is loaded to build the `KLAVIYO_STREAMS` list. Each returned stream recipe is later used by `KlaviyoConnector` when it builds API requests, flattens records, and paginates through results.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Klaviyo and adds the headers Klaviyo expects. In plain terms, it puts the right badge on every request before the connector enters Klaviyo’s API.

**Data flow**: It receives a base API address and a credential. It asks the parent REST connector to make the basic client, then adds Klaviyo’s pinned API revision header. If the credential contains a private key value, it also adds Klaviyo’s `Authorization` header using Klaviyo’s private-key format, and returns the prepared client.

**Call relations**: This method customizes the general REST connector’s client for Klaviyo. The base connector creates the shared HTTP machinery, and this method layers on Klaviyo-specific authentication and versioning before any stream requests are made.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: Converts Klaviyo’s full next-page URL into the path-and-query form this connector’s HTTP client can request. It is used so the connector can keep reading page after page without rebuilding the URL by hand.

**Data flow**: It receives a `links.next` value from Klaviyo, which may be a full URL or may be missing. If there is no usable link, it returns `None`. If there is a link, it parses the URL, keeps only the path and query string, and returns that smaller request path.

**Call relations**: `paginate` calls this after each page of results. Klaviyo tells the connector where the next page is; `_next_path` trims that address into the form needed for the already base-bound HTTP client.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: Chooses which timestamp field should be used for incremental syncing for a given stream. This matters because Klaviyo does not use the same field name for every object type.

**Data flow**: It receives a stream description. If the stream is events, it returns `datetime`; if it is one of the streams that use `updated_at`, it returns `updated_at`; otherwise it returns `updated`.

**Call relations**: `_initial_query` relies on this choice when it builds the first API request for a stream. The result decides both the filter field and the sort field used for incremental reads.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for the first page of a Klaviyo stream request. It sets page size, optional incremental filters, sorting, and a few stream-specific extras.

**Data flow**: It receives a stream description and an optional cursor value from the previous sync. It starts with the configured page size. If the stream supports cursor-based syncing, it chooses the correct time field, adds a filter when a cursor exists, and sorts by that same field. For profiles it asks Klaviyo to include subscription details, and for events it asks Klaviyo to include metric records. It returns the completed parameter dictionary.

**Call relations**: `paginate` calls this before requesting the first page. After the first page, Klaviyo’s own `links.next` URL carries the paging information, so these initial parameters are not reused.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: Safely reads the ID of a related Klaviyo object from a record’s nested `relationships` block. It avoids crashes when Klaviyo leaves a relationship out or sends it in an unexpected shape.

**Data flow**: It receives a relationships value and the relationship name to look up, such as `profile`, `metric`, or `list`. It checks each nested level before reading it. If it finds a related ID, it returns that ID as a string; otherwise it returns `None`.

**Call relations**: `flatten` calls this when it wants to expose important relationship IDs as simple top-level fields. This keeps the repeated defensive lookup logic in one place.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns one nested Klaviyo API record into a simpler flat record that the sync system can store and use more easily. It also pulls out a few high-value details, such as profile consent, campaign sender information, event metric links, and parent list IDs.

**Data flow**: It receives a raw Klaviyo record and the stream it came from. It starts a new dictionary with the record ID and resource type, then copies fields from `attributes` to the top level. For lists and segments it removes `profile_count`, because changing membership counts should not make the list or segment itself look changed. Depending on the stream, it then adds extra readable fields from nested subscription, audience, event property, or relationship data. The output is the flattened record.

**Call relations**: This method is part of the connector’s handoff from raw API data to the rest of the source sync system. It calls `_lift_relationship_id` when related object IDs need to be pulled out safely, especially for events and segments.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads all pages for one Klaviyo stream and yields batches of raw records. It knows how to start the request, follow Klaviyo’s `links.next` paging style, enrich event records with metric names when available, and turn permission failures into a clean skipped-stream signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It starts at `/api/<stream object>` with the query from `_initial_query`. For each page, it performs a GET request, reads the `data` records, optionally uses included metric records to add metric names to event attributes, yields the records if any exist, then uses `_next_path` to find the next page. If Klaviyo returns 401 or 403, it raises `StreamSkipped` so the overall sync can record that this stream was not allowed instead of treating it like a broken connector.

**Call relations**: This is the main read loop for Klaviyo streams. It depends on `_initial_query` to form the first request and `_next_path` to continue through later pages. When permission is missing, it hands control back to the wider sync system through `StreamSkipped`, which tells the run to skip that stream rather than fail everything.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `during source sync, whenever Mailchimp streams are read`

Mailchimp stores marketing data behind a web API, and much of that data is split across pages or nested under parent objects. For example, subscribers live inside audiences, interests live inside interest categories, and email activity lives inside campaign reports. This file is the map and walking plan for collecting all of that without missing pieces.

At the top, it defines the Mailchimp streams the product knows about, including each stream’s name, main identifier, and optional “cursor” field. A cursor is a timestamp or similar marker used to ask, “only give me records changed since this point.” The connector then decides how to fetch each stream. Some streams are simple top-level lists, like campaigns. Others require a fan-out: first fetch every audience, then fetch members for each audience. Reports work the same way for unsubscribes and email activity.

The main class, MailchimpConnector, inherits common REST API behavior from the base connector. It supplies Mailchimp-specific paths, query parameters, and small fixes to records. One important behavior is that access refusal, such as a bad key or missing permission, becomes StreamSkipped instead of a generic crash. Another important detail is email activity: Mailchimp gives several actions inside one recipient record, so this connector splits them into one row per action and creates a stable id for each action.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: Creates a StreamSpec, which is the system’s description of one readable Mailchimp stream. It is a small helper that keeps the stream definitions compact and consistent.

**Data flow**: It receives a stream name and optional details such as the Mailchimp object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults where details are not supplied, then returns a StreamSpec object that the connector later uses to know how to sync that stream.

**Call relations**: This helper is used while building the MAILCHIMP_STREAMS list at import time. It hands each completed StreamSpec to the connector class through streams_list, so later sync code can ask the connector to read those streams.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Lightly reshapes individual Mailchimp records before they leave the connector. For subscriber-like records, it creates a common created_at value from Mailchimp’s signup or opt-in timestamps.

**Data flow**: It receives one record and the stream definition for that record. If the stream is list_members or segment_members, it copies the record and adds created_at from timestamp_signup, falling back to timestamp_opt; for all other streams it returns the record unchanged.

**Call relations**: This is part of the connector’s record-cleanup path inherited from the base REST connector. It does not call other functions in this file; it simply makes member records easier for the rest of the system to interpret consistently.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field where Mailchimp places the actual list of records for a stream. This matters because Mailchimp wraps records under names like lists, members, or emails instead of returning a bare array.

**Data flow**: It receives a stream definition. It looks up the stream name in a local mapping and returns the matching wrapper field; if there is no special mapping, it returns the stream name itself.

**Call relations**: Pagination helpers call this before reading pages so they know where the records are inside each API response. It is used by the top-level, per-list, and per-report pagination paths.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the Mailchimp query parameter used for incremental sync, when Mailchimp supports filtering by the stream’s cursor field. Incremental sync means asking only for records since the last saved point instead of reading everything again.

**Data flow**: It receives a stream definition and an optional cursor value. If either is missing, or if Mailchimp has no matching since-style parameter for that cursor field, it returns an empty dictionary. Otherwise it returns a one-item dictionary such as {"since_last_changed": cursor}.

**Call relations**: Pagination helpers call this before making API requests. It supplies the filtering parameters used by top-level streams, per-list streams, segment members, and per-report streams.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct fetching strategy for a requested Mailchimp stream and yields pages of records. It is the main dispatcher for reading Mailchimp data.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It checks the stream name, routes it to the right pagination helper, and yields each page produced by that helper. If Mailchimp returns 401 or 403, meaning unauthorized or forbidden, it turns that into StreamSkipped with a clear message; other HTTP errors continue upward.

**Call relations**: The base sync system calls this when it wants records for a stream. Depending on the stream, this function hands off to top-level pagination, list fan-out pagination, interest traversal, segment-member traversal, report fan-out pagination, or email activity expansion.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Mailchimp collections that live at one top-level API path, such as lists, campaigns, automations, and reports. These do not require first finding a parent object.

**Data flow**: It receives the HTTP client, stream definition, API path, and optional cursor. It determines the response field to read and any cursor query parameter, then asks the base REST connector to walk through offset-based pages using Mailchimp’s count parameter. It yields each page of records it receives.

**Call relations**: MailchimpConnector.paginate calls this for top-level streams. This helper relies on _data_field and _cursor_params to prepare the request, then uses the inherited paging machinery to do the actual web requests.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a nested Mailchimp collection at a specific API path. It is the reusable page-walker for child resources such as members inside a list or unsubscribes inside a report.

**Data flow**: It receives an HTTP client, a full API path, the JSON field containing records, and optional base query parameters. It asks the base REST connector to fetch offset-based pages from that path and yields each page unchanged.

**Call relations**: Higher-level helpers call this after they have built a child path. It is used by per-list, interests, segment members, per-report, and email activity flows so they do not each repeat the same page-fetching code.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: Fetches only the id values from a paged Mailchimp collection. It is used when later work needs to visit every parent object, like every list or every report.

**Data flow**: It receives an HTTP client, an API path, and the response field containing records. It pages through that collection, checks each row, and yields the row’s id as a string when one exists.

**Call relations**: _list_ids and _report_ids call this as their shared helper. Those id streams then feed the fan-out functions that need to build child URLs for each list or report.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the ids of all Mailchimp audiences, which Mailchimp calls lists. Many other records are reached only by first knowing which list they belong to.

**Data flow**: It receives an HTTP client. It asks _ids to page through /3.0/lists and yields each list id it finds.

**Call relations**: The per-list, interests, and segment-members pagination flows call this before fetching their child collections. It acts like getting all folder names before opening each folder.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the ids of all Mailchimp reports. Report ids are needed to fetch report-specific data such as unsubscribes and email activity.

**Data flow**: It receives an HTTP client. It asks _ids to page through /3.0/reports and yields each report id it finds.

**Call relations**: The per-report and email-activity pagination flows call this first. Each yielded report id becomes part of the API path for the next set of requests.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections that live under each Mailchimp list, such as members, segments, tags, or interest categories. It also marks each returned record with the list id when requested, so the record keeps its parent context.

**Data flow**: It receives an HTTP client, stream definition, child path name, optional cursor, and the name of the parent-id field to add. It gets cursor parameters and the correct response field, loops through every list id, builds a safe URL for that list’s child endpoint, fetches child pages, adds the list id to each record when needed, and yields the pages.

**Call relations**: MailchimpConnector.paginate calls this for list-based streams. It depends on _list_ids to discover parents, _paginate_child to fetch each child collection, and the small helpers that choose data fields and cursor parameters.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Mailchimp interests, which are nested two levels deep: lists contain interest categories, and categories contain interests. It preserves both the list id and category id on each interest record.

**Data flow**: It receives an HTTP client, stream definition, and optional cursor, though the cursor is not used here. It loops through list ids, fetches interest categories for each list, then for each category fetches its interests. Before yielding interest pages, it adds list_id and category_id to each interest record when possible.

**Call relations**: MailchimpConnector.paginate calls this when the stream is interests. This helper uses _list_ids to find lists and _paginate_child twice: once for categories and once for interests.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the members inside every segment of every Mailchimp list. This is a deeper walk because segment members are reached through list → segment → members.

**Data flow**: It receives an HTTP client, stream definition, and optional cursor. It builds cursor parameters, loops through all list ids, fetches each list’s segments, then fetches members for each segment. It adds list_id and segment_id to each member record before yielding the page.

**Call relations**: MailchimpConnector.paginate calls this for the segment_members stream. It uses _list_ids to discover lists, _paginate_child to read segments and members, and _cursor_params to request only recently changed members when possible.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections under each campaign report, such as unsubscribes. It adds the report or campaign id to each returned record so the record can be traced back to its report.

**Data flow**: It receives an HTTP client, stream definition, child path name, optional cursor, and the parent-id field to stamp. It gets the response field and cursor parameters, loops through report ids, builds a safe child URL for each report, fetches pages, adds the parent id to each record when requested, and yields the pages.

**Call relations**: MailchimpConnector.paginate calls this for report-based child streams. It relies on _report_ids for parent discovery, _paginate_child for page fetching, and the small helper methods for response-field and cursor setup.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email activity from each Mailchimp report and turns nested activity lists into separate event records. This is needed because Mailchimp groups several actions under one recipient, while the sync system needs stable individual rows.

**Data flow**: It receives an HTTP client and an optional cursor. It builds a since parameter if a cursor exists, loops through all report ids, fetches each report’s email-activity pages, and then splits each recipient’s activity array into one row per action. Each row keeps the recipient fields, gets the campaign_id, and receives a synthesized id made from email_id, action, and timestamp; only non-empty exploded pages are yielded.

**Call relations**: MailchimpConnector.paginate calls this for the email_activity stream. It uses _report_ids to find reports and _paginate_child to fetch the raw grouped activity before doing the Mailchimp-specific split into event rows.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Form response collection
Defines the Typeform connector for syncing forms, responses, workspace assets, themes, images, and webhook records.

### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync runs`

Typeform is an online form service, and its data lives behind a web API. This connector is the bridge between that API and UFO's source-sync system. Without it, the system would not know which Typeform endpoints to call, how to page through long lists, or how to connect responses and webhooks back to the form they belong to.

The file first declares the Typeform streams the system can read. A stream is a named kind of data, like "forms" or "responses", with hints about its unique ID and time fields. The TypeformConnector then decides how each stream should be fetched.

Most Typeform lists are read page by page, like turning pages in a catalog. Forms can also be filtered during incremental syncs, so older unchanged forms are skipped. Responses are special: they are not fetched as one global list. The connector first gets all forms, then asks Typeform for the responses for each form, adding the form's ID and title to every response so the record keeps its context. Webhooks work similarly, one form at a time.

If Typeform refuses access with a 401 or 403 status, the connector does not crash the whole sync. It raises StreamSkipped, meaning this particular stream cannot be read, usually because the credential is invalid or lacks permission.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway the sync runner uses to ask for one Typeform stream. It chooses the right fetching method for forms, responses, workspaces, images, themes, or webhooks, and reports unsupported or forbidden streams as skipped.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is a saved timestamp used to continue from a previous sync. It checks the stream name, delegates to the matching helper, and yields each batch of records it gets back. If Typeform returns an access-denied response, it turns that into a StreamSkipped message; other errors continue upward unchanged.

**Call relations**: The wider RestConnector flow calls this when it is time to read a Typeform stream. Depending on the stream, it hands the work to _forms, _responses, _paged_items, or _webhooks, then passes their yielded pages back to the sync runner.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use ordinary page numbers. It keeps asking for page 1, page 2, and so on until Typeform says there are no more pages.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. For each request, it adds the current page number and a default page size, reads the response, pulls the list under the "items" field using records_at, and yields that list if it is not empty. It stops when the reported page count is reached, or when a short final page shows there is nothing more.

**Call relations**: paginate uses this directly for simple streams such as workspaces, images, and themes. _forms also uses it as its base reader before applying form-specific filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform forms and optionally filters them for an incremental sync. It is the shared starting point for anything that needs to know which forms exist.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It gets batches of forms from _paged_items, and if a cursor is present, keeps only forms whose last_updated_at value is newer than that cursor. It yields only non-empty batches.

**Call relations**: paginate calls this when the requested stream is forms. _responses and _webhooks also call it first, because they must walk through forms before they can fetch each form's responses or webhooks.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads submitted responses for every Typeform form. It adds form information to each response so a later reader can tell which form produced it.

**Data flow**: It receives an HTTP client and an optional cursor. First it asks _forms for all forms, ignoring the cursor there because it needs the full form list. For each valid form ID, it asks Typeform for that form's responses, passing the cursor as a "since" filter when available. Each batch of response records is enriched with form_id and form_title through with_context, then yielded.

**Call relations**: paginate calls this for the responses stream. Inside, it depends on _forms to discover form IDs and uses with_context before handing response batches back, so downstream storage receives responses with their form context attached.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads webhook definitions for each Typeform form. A webhook is a callback URL Typeform can notify when something happens on a form.

**Data flow**: It receives an HTTP client. It first gets all forms through _forms, then skips any form without a usable string ID. For each valid form, it requests that form's webhooks, extracts the "items" list with records_at, adds form_id and form_title with with_context, and yields the resulting records when any exist.

**Call relations**: paginate calls this for the webhooks stream. Like _responses, it starts with _forms because webhooks are attached to individual forms, then returns context-rich webhook batches to the main sync flow.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).

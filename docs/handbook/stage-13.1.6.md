# Marketing, advertising, social, and forms connectors  `stage-13.1.6`

This stage is part of the system’s data intake work. It does not run the main product by itself. Instead, it acts like a set of adapters that let the sync engine talk to outside marketing and social platforms. Each adapter knows that service’s API, meaning its web doorway for requesting data, and reshapes the results into regular “streams” of records the rest of the system can store, search, or process.

ActiveCampaign brings in marketing data such as contacts, campaigns, deals, lists, tags, users, and custom fields. Facebook Ads reads ad accounts, campaigns, ad sets, ads, and daily performance numbers. Google Ads does the same kind of job for Google advertiser accounts, campaigns, ad groups, ads, and metrics. Instagram uses Facebook’s Graph API to collect business pages, linked Instagram accounts, posts, stories, and engagement statistics. Klaviyo reads marketing objects and supports paging and resuming from the last synced point. Mailchimp normalizes audiences, subscribers, campaigns, reports, and email activity. Typeform fetches forms, responses, and related assets, but only reads data and never changes it.

## Files in this stage

### Marketing automation CRM
ActiveCampaign supplies broad marketing automation streams for contacts, campaigns, deals, lists, tags, users, and custom fields.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `request handling`

ActiveCampaign is a marketing and customer relationship tool, and its API exposes many separate collections of records. This file is the read-only connector for those collections. Without it, the larger system would not know which ActiveCampaign endpoints exist, how to authenticate to them, how to page through large result sets, or how to resume from a previous sync.

The file first defines a catalog of streams. A stream is one type of thing to sync, like contacts, campaigns, deals, or account custom field data. Each stream records practical details such as its name, its main ID field, and which date field can be used as a cursor. A cursor is a bookmark, usually a timestamp, that lets a later run ask for only records changed after the last run.

ActiveCampaign’s API has a mostly regular shape: each list endpoint returns records inside a named envelope and supports paging with a limit and offset. The connector uses a lookup table to translate internal stream names, such as `campaign_messages`, into ActiveCampaign’s URL and response names, such as `campaignMessages`.

Authentication is slightly special. ActiveCampaign expects the API key in an `Api-Token` header, not the more common bearer-token style. The connector adapts credentials into that shape. During pagination, permission failures such as 401 or 403 are turned into a skipped stream instead of crashing the whole sync, which is useful when an API key lacks access to only some data.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream definition for a specific ActiveCampaign resource. It keeps the long stream catalog readable by filling in common defaults, such as using `id` as the primary key and `cdate` as the created date.

**Data flow**: It receives a stream name and optional details like the source object name, primary key, cursor field, and whether the stream is considered canonical. It combines those inputs with ActiveCampaign defaults, decides whether the cursor field should also count as the updated-at field, and returns a `StreamSpec`, which is the system’s description of a syncable collection.

**Call relations**: The file calls this helper repeatedly while building the ActiveCampaign stream list. Each call produces a stream specification that the `ActiveCampaignConnector` later exposes through its `streams_list`.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to ActiveCampaign, with authentication set up in the way ActiveCampaign expects. Its main job is to turn a stored API key into the required `Api-Token` request header.

**Data flow**: It receives a base URL and a credential. If the credential already contains a custom transport, it leaves that special setup alone and delegates to the parent connector. Otherwise, it expects the credential’s bearer value to contain the ActiveCampaign API key, wraps that key into an `Api-Token` header, and returns an asynchronous HTTP client configured by the parent connector. If there is no key, it raises an error immediately.

**Call relations**: This method is used when the connector is preparing to make API requests. It relies on the shared REST connector behavior for the actual client construction, but adjusts the credential first so ActiveCampaign receives the right kind of authentication.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This translates the system’s stream name into the exact URL segment and response-envelope key ActiveCampaign uses. It matters because some names differ only by style, such as snake_case inside this project versus camelCase in ActiveCampaign.

**Data flow**: It receives a stream specification. It looks up that stream’s name in the file’s mapping table and returns the matching pair of strings: one for the API path and one for where records appear in the JSON response. If the stream is not in the table, it safely uses the stream name for both.

**Call relations**: The pagination method calls this before making requests. In the larger flow, this is the small translation step that lets one generic pagination routine work across many differently named ActiveCampaign resources.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one ActiveCampaign stream page by page and yields batches of records to the sync system. It also applies an incremental filter when ActiveCampaign supports one, so later syncs can avoid re-reading everything.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It translates the stream into an API path and response key, builds request parameters with a page size of 100, and, when possible, adds a filter such as “records updated after this timestamp.” It then asks the shared REST paging helper for offset-based pages and yields each page of records. If ActiveCampaign refuses access with a 401 or 403 response, it converts that into a `StreamSkipped` error with a clear explanation; other HTTP errors continue upward unchanged.

**Call relations**: This is the connector’s main read loop for a stream. It calls `_resolve_stream_segment` to find the right endpoint details, then hands the actual offset paging work to the base REST connector machinery. When access is denied for a stream, it signals the sync layer with `StreamSkipped` so the run can treat that stream as unavailable rather than mistaking the response format or credentials silently.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Advertising platforms
Facebook Ads and Google Ads connectors read account, campaign, ad structure, and performance metrics from major paid advertising networks.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `source sync`

This connector is the bridge between UFO and Facebook Ads. Facebook Ads data is not a single table you can download at once. It is spread across ad accounts, and each account has its own campaigns, ad sets, ads, and insight reports. This file knows how to walk that structure in the right order.

The connector first asks Facebook for the user’s ad accounts. For account-based data, it then visits each account and requests the matching collection, such as campaigns or ads. Facebook returns results in pages, a bit like a book with a “next page” link at the bottom. The helper method follows that next link until there are no more pages.

For campaigns, ad sets, and ads, the connector can use a cursor, which is a saved “last seen” timestamp, to avoid sending old records again. For ad insights, it asks for daily ad-level performance. On a first run it looks back 90 days; on later runs it starts from the cursor date. It also creates a stable row id from the account, campaign, ad set, ad, and date, because Facebook insight rows do not naturally arrive with one simple unique id.

This file only reads from Facebook. It does not create or change ads.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s page-turner for Facebook API responses. It requests one page, extracts the list of records from the response, yields that list, then follows Facebook’s next-page link until the collection is exhausted.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request through the connector’s raw GET method, reads the JSON response, pulls records from the response’s data field using records_at, and then checks the response paging information for a next URL. It outputs one list of records at a time and does not permanently change connector state.

**Call relations**: The account, child-object, and insights readers all rely on this method whenever they need to walk through Facebook’s paginated results. It is the shared low-level loop underneath _accounts, _account_children, and _insights.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches all Facebook ad accounts available to the authenticated user. The rest of the connector needs this because most Facebook Ads data must be requested account by account.

**Data flow**: It starts with a fixed list of ad account fields, such as id, name, currency, timezone, and created time. It asks _paged to read /me/adaccounts and collects every returned page into one list. It returns that full list of account records.

**Call relations**: paginate calls this directly when the requested stream is ad_accounts. _account_children and _insights also call it first so they know which ad accounts to visit before requesting campaigns, ads, or performance rows.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads account-owned objects: campaigns, ad sets, or ads. It also attaches ad account context so each returned record can be traced back to the account it came from.

**Data flow**: It receives the HTTP client, a stream description, and an optional cursor timestamp. It chooses the correct Facebook fields for the requested stream, fetches all ad accounts, then queries each account’s matching endpoint. If a cursor is present, it keeps only records whose update time is newer than that cursor. Before yielding records, it adds account id and account name context with with_context.

**Call relations**: paginate hands campaign, ad set, and ad streams to this method. This method in turn uses _accounts to find the accounts and _paged to read each account’s Facebook pages, then returns batches upward to paginate.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads daily ad performance statistics, such as impressions, clicks, spend, reach, and click-through rate. It shapes those report rows into stable records the sync system can identify later.

**Data flow**: It receives an HTTP client and an optional cursor date. It builds Facebook insights parameters for ad-level, daily results. If there is a cursor, it asks Facebook for data from that date through today; otherwise it asks for the last 90 days. For each ad account, it reads insight pages, adds the account id, and creates a synthetic id by joining the account, campaign, ad set, ad, and date pieces. It yields lists of these enriched rows.

**Call relations**: paginate calls this when the requested stream is ads_insights. This method uses _accounts to decide which accounts to report on, _paged to move through Facebook result pages, json.dumps to format Facebook’s date range parameter, and the current UTC date to set the end of cursor-based windows.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a stream from Facebook Ads. Given a stream name, it chooses the right reading path and yields batches of records for the sync engine.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor. For ad_accounts, it fetches accounts and yields them once. For campaigns, ad_sets, and ads, it delegates to _account_children. For ads_insights, it delegates to _insights. If the stream name is unknown, it raises StreamSkipped to tell the sync system that this stream is not implemented here.

**Call relations**: The broader source sync system calls paginate when it wants records for one Facebook Ads stream. paginate is the traffic director: it sends each known stream to _accounts, _account_children, or _insights, and stops unsupported streams with StreamSkipped.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly normalizes records after they are fetched. At present it adjusts campaign records so common fields are easier for the rest of the system to use.

**Data flow**: It receives one record and the stream description. If the record is from the campaigns stream, it returns a copy with a simplified status field that prefers effective_status over status, and a created_at field copied from Facebook’s created_time. For all other streams, it returns the record unchanged.

**Call relations**: After paginate has supplied raw records, the connector framework can call flatten to make each record fit the system’s expected shape. This method does not call other project functions; it is a small final cleanup step for campaign data.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `during Google Ads source sync`

Google Ads does not expose its data as simple downloadable tables. Instead, this connector asks Google Ads questions using GAQL, the Google Ads Query Language, which is similar in spirit to SQL: “select these fields from this advertising object.” The connector first finds which customer accounts the current OAuth grant can access, then runs the right query against each account.

A key detail is that Google Ads needs two kinds of permission. OAuth proves which advertiser account is being used, but Google also requires a developer token, like an approved API badge. This file reads that token from the environment. If it is missing, or if Google refuses access with a 401 or 403 response, the stream is skipped instead of crashing the whole sync.

The main class, GoogleAdsConnector, defines the available streams and knows how to fetch each one. Its pagination step chooses a GAQL query for the requested stream, runs it for every accessible customer, and adds the customer id to each returned row. Its flatten step then pulls useful fields out of Google’s nested response shape. For example, campaign data arrives inside a campaign object, but the rest of the system wants simple fields like resource_name, name, status, and created_at.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token required for API access. It protects the sync from making doomed requests when the token is missing by marking the stream as skipped.

**Data flow**: It reads the environment variables UFO_GOOGLE_ADS_DEVELOPER_TOKEN and GOOGLE_ADS_DEVELOPER_TOKEN. If either contains a value, that value comes out as the token. If neither is set, it raises StreamSkipped with a clear explanation, so the caller knows this source cannot run with OAuth alone.

**Call relations**: GoogleAdsConnector._make_client calls this when preparing the HTTP client. That means every Google Ads request created by this connector gets the required developer-token header, or the stream is skipped before requests are sent.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Google Ads. It adds the special headers Google Ads expects in addition to the normal OAuth-based credential setup.

**Data flow**: It receives a base URL and a credential. It first creates the normal REST client, then adds the developer token returned by GoogleAdsConnector._developer_token. It also reads an optional login customer id from the environment, removes dashes from it, and adds it as a header when present. The result is an HTTP client ready to make Google Ads API calls.

**Call relations**: This is part of the connector setup before data fetching begins. Its most important handoff is to GoogleAdsConnector._developer_token, because without that token Google Ads will reject otherwise valid OAuth requests.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The connector needs this list because most Google Ads queries must be run for a specific customer account.

**Data flow**: It uses the HTTP client to call the accessible-customers endpoint. From the response, it looks for resource names shaped like customers/1234567890, extracts just the id part, and returns a list of customer id strings. Invalid or unexpected entries are ignored.

**Call relations**: GoogleAdsConnector._query_each_customer calls this first. The returned ids become the loop of accounts that later search queries are run against.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one GAQL query against one Google Ads customer account. It collects the rows from Google’s streamed batch response into a plain list.

**Data flow**: It receives an HTTP client, a customer id, and a query string. It posts the query to that customer’s searchStream endpoint. Google may return a top-level list of batches, so the function walks through each batch, takes its results rows, keeps only dictionary-shaped rows, and returns them as a list.

**Call relations**: GoogleAdsConnector._query_each_customer calls this once for each accessible customer id. It is the piece that turns a chosen stream query into actual rows from Google Ads.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same query across every accessible Google Ads customer account. It is useful because one OAuth grant may cover many advertiser accounts.

**Data flow**: It receives an HTTP client and a GAQL query. It first asks GoogleAdsConnector._customer_ids for the account ids. For each id, it calls GoogleAdsConnector._search_stream. When rows are found, it adds customer_id to every row and yields that group of rows as a page.

**Call relations**: GoogleAdsConnector.paginate calls this after choosing the right query for a stream. This helper does the repeated account-by-account work and hands pages of enriched rows back to paginate.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the main reader for Google Ads streams. Given a stream name, it chooses the correct GAQL query, runs it across accessible customers, and yields pages of records for the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It matches the stream name to a GAQL query for customers, campaigns, ad groups, ads, campaign metrics, or customer clients. For campaign metrics, it uses the cursor date when available, otherwise it starts from about 90 days ago. It sends the chosen query through GoogleAdsConnector._query_each_customer and yields each returned page. If the stream is unknown, it raises StreamSkipped. If Google refuses access with 401 or 403, it also raises StreamSkipped with a permission-focused message; other HTTP errors are allowed to bubble up.

**Call relations**: This is the function the source sync flow relies on to fetch data. It delegates the repeated customer-account work to GoogleAdsConnector._query_each_customer, and it uses StreamSkipped to tell the wider system when a stream should be ignored rather than treated as a fatal failure.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Google Ads records into simpler records with stable keys and commonly used fields at the top level. It makes nested API responses easier for the rest of UFO to store and compare.

**Data flow**: It receives one raw record and its stream description. For customer records, it extracts a simple id and name. For campaign records, it extracts resource_name, name, status, and created_at. For campaign metrics, it combines customer id, campaign id, and date into a unique id, and pulls out metric values like impressions, clicks, and cost_micros. For other streams, it returns the record unchanged. It uses dict_or_empty so missing nested objects behave like empty dictionaries instead of causing errors.

**Call relations**: This runs after pages have been fetched by GoogleAdsConnector.paginate. It is the cleanup stage: paginate gets the rows from Google Ads, and flatten turns each row into the shape expected by the downstream sync and storage code.

*Call graph*: 1 external calls (dict_or_empty).


### Social business analytics
Instagram business data is synced through Facebook Graph API, including pages, accounts, posts, stories, and performance statistics.

### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `source sync run`

Instagram business accounts are reached through Facebook Pages, so this connector starts from the user's Facebook Pages and follows the link from each Page to its Instagram business account. From there it reads media posts, stories, and insight numbers such as reach, impressions, engagement, profile views, and story taps. Think of it like starting with a company directory, finding each branch office, then collecting the branch's public reports.

The file defines several streams, which are named categories of data the sync system can request: pages, Instagram accounts, media, media insights, stories, story insights, and user insights. The main class, `InstagramConnector`, knows the Facebook Graph API base address and provides the `paginate` method that the wider sync runner calls when it wants one of those streams.

The connector is read-only. It does not publish media or change Instagram data. It also does not keep an access token itself; the surrounding runner supplies an authenticated HTTP client. It understands Facebook's paging style, where each response contains a `data` list and may include a `paging.next` link for the next page. For incremental streams, it skips records older than the saved cursor, which is a timestamp-like bookmark. If Instagram refuses access to a whole stream because the permission is missing or the token is bad, the stream is marked as skipped rather than crashing the whole run. If a single media item or story refuses its insights, that one object is skipped and the connector keeps going.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a paginated Facebook Graph API collection and yields each non-empty page of records. It hides the repeated work of following `paging.next`, so other code can treat a long API collection like a stream of record batches.

**Data flow**: It receives an authenticated HTTP client, an API path, and optional query parameters. It requests the first page, pulls the list under the response's `data` field, yields that list if it has records, then follows the response's `paging.next` link until there are no more pages. Its output is an asynchronous sequence of lists of dictionaries, where each dictionary is one API record.

**Call relations**: This is the shared page-turning helper. `_pages` uses it to walk `/me/accounts`, and `_account_collection` uses it to walk each Instagram account's media or stories. It relies on `records_at` to safely extract the `data` array from the JSON response.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the current grant, including each Page's linked Instagram business account summary when present. This is the starting point for almost everything else because Instagram business accounts are discovered through Pages.

**Data flow**: It receives an authenticated HTTP client. It asks `/me/accounts` for Page fields such as id and name, plus the embedded `instagram_business_account` fields. It gathers all paged results into one list and returns that list.

**Call relations**: The top-level `paginate` method calls this directly when the requested stream is `pages`. `_instagram_accounts` also calls it as the first step in finding which Instagram accounts can be synced. It delegates the actual Facebook paging work to `_paged`.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Builds a clean list of Instagram business accounts linked to the user's Facebook Pages. It also attaches the Page id and Page name, so each Instagram account can be traced back to where it came from.

**Data flow**: It receives an authenticated HTTP client and first reads all Pages through `_pages`. For each Page, it looks for an `instagram_business_account` object with an id. It stores accounts by id to avoid duplicates, adds `page_id` and `page_name`, and returns the unique accounts as a list.

**Call relations**: This is the connector's account-discovery step. `paginate` uses it when syncing the `instagram_accounts` stream. `_account_collection` uses it before reading each account's media or stories, and `_user_insights` uses it before reading account-level statistics.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a collection that belongs to each Instagram account, such as media posts or stories. It can also apply a cursor bookmark so older records are not emitted again during incremental syncs.

**Data flow**: It receives an HTTP client, a collection name such as `media` or `stories`, the fields to request, and optional cursor information. It first finds Instagram accounts, then calls the account-specific API path for each one. For every returned page, it drops records whose cursor field is not newer than the saved cursor, adds the `instagram_account_id` context to the remaining records, and yields them in batches.

**Call relations**: `paginate` calls this when the requested stream is `media` or `stories`. It gets the account list from `_instagram_accounts`, uses `_paged` to walk each account's API collection, and uses `with_context` to add the account id so downstream storage knows which account each post or story belongs to.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads insight metrics for individual objects, such as a media post or story. It turns per-object metric responses into records with stable ids that can be stored as their own stream.

**Data flow**: It receives an HTTP client, an asynchronous stream of object batches, a comma-separated metric list, and the output stream name. For each object with an id, it requests `/{object_id}/insights`. It skips individual objects if Facebook says that object's insights are unavailable or forbidden with certain expected error codes. For each returned insight, it creates a record containing the insight data, a generated id made from the object id and metric name, the parent object's id, and the stream name, then yields non-empty batches.

**Call relations**: `paginate` calls this for `media_insights` and `story_insights`. In those cases, `paginate` first creates a fresh media or story stream and passes it in as the list of parent objects. This helper then fans out from each parent object to its insight endpoint and uses `records_at` to read the returned `data` list.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads account-level daily insight values for each Instagram business account, such as impressions, reach, and profile views. It supports incremental syncing by ignoring values that are not newer than the saved `end_time` cursor.

**Data flow**: It receives an authenticated HTTP client and an optional cursor. It finds all Instagram accounts, asks each account's `/insights` endpoint for daily metrics, then walks each metric's `values` list. For each daily value newer than the cursor, it creates a record with a stable id made from account id, metric name, and end time, and includes the account id. It yields rows in batches when there is anything new.

**Call relations**: `paginate` calls this when the requested stream is `user_insights`. It depends on `_instagram_accounts` to know which accounts to query, `records_at` to extract the metric list, and `list_or_empty` to safely treat the metric's `values` field as a list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry method the sync framework uses to ask the Instagram connector for records from a specific stream. It chooses the right helper for pages, accounts, media, stories, object insights, or user insights, and turns permission refusals into clean stream skips.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor bookmark. Based on the stream name, it calls the matching helper and yields batches of records. For media, stories, and user insights, it passes the cursor so old records can be filtered out. If the stream name is unknown, it raises a skip signal. If Facebook returns an authorization-style HTTP error for the whole stream, it converts that into `StreamSkipped`; other errors are re-raised.

**Call relations**: This method is the connector's dispatcher. The wider source runner calls it for each stream it wants to sync. It hands work to `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, or `_user_insights` depending on the requested stream. For media and story insights, it also calls its own media or stories pagination path to supply the parent objects whose insights should be fetched.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Email marketing platforms
Klaviyo and Mailchimp connectors normalize audiences, subscribers, campaigns, reports, activity, and related marketing records into syncable streams.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `during source sync runs when reading Klaviyo streams`

Klaviyo returns marketing data such as profiles, lists, campaigns, events, forms, catalog items, and webhooks through a JSON API. This file is the adapter that makes those many Klaviyo endpoints look like normal source streams to the wider UFO sync system.

It starts by describing each stream: its name, where it lives in Klaviyo’s API, its main ID field, and which timestamp should be used to continue an incremental sync. Incremental sync means “only fetch things newer than the last thing we already saw,” like bookmarking your place in a long book.

The connector also prepares the HTTP client with Klaviyo-specific requirements: a fixed API revision header and Klaviyo’s private-key authorization format. When it asks Klaviyo for data, it requests one page at a time and follows Klaviyo’s `links.next` URL until there are no more pages.

Klaviyo records are deeply nested: useful fields sit under `attributes`, and links to related objects sit under `relationships`. The `flatten` method lifts important values into a simpler top-level shape. This matters because later sync code can then find IDs, timestamps, campaign subjects, consent information, event metric names, and related profile or list IDs without needing to understand Klaviyo’s full response layout.

If Klaviyo refuses access to a stream because the API key lacks permission, the connector marks that stream as skipped instead of crashing the whole run.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates one stream description for a Klaviyo resource. It keeps the long list of Klaviyo streams readable by filling in common defaults, such as using `id` as the main key and `updated` as the usual change timestamp.

**Data flow**: It takes a stream name and optional details such as the API object name, primary key, cursor field, and timestamp fields. It combines those choices into a `StreamSpec`, which is the project’s standard description of a readable source stream. The result is stored in the Klaviyo stream list and later used by the connector when syncing.

**Call relations**: This helper is used while the file is being loaded to build `KLAVIYO_STREAMS`. It hands each completed stream description to the rest of the connector, which later uses those descriptions to decide which URL to call, how to sort results, and how to track progress.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method prepares the web client used to talk to Klaviyo. It adds the special headers Klaviyo expects, including the pinned API revision and, when available, the Klaviyo private API key authorization format.

**Data flow**: It receives a base URL and a resolved credential. First it asks the parent connector to build the normal HTTP client. Then it adds Klaviyo’s `revision` header, and if the credential contains a bearer-style secret, it rewrites that into Klaviyo’s `Klaviyo-API-Key ...` authorization header. It returns the configured client that future requests will use.

**Call relations**: The wider connector framework calls this when setting up communication with Klaviyo. After this method finishes, pagination and data-fetching code can use the client without repeating Klaviyo’s authentication and version-header rules on every request.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper turns Klaviyo’s full next-page URL into the relative path that this connector’s already-configured client can request. It is needed because Klaviyo gives pagination links as absolute URLs, while the client is already tied to Klaviyo’s base address.

**Data flow**: It receives a possible `next` link from Klaviyo. If the link is missing or has no path, it returns nothing. Otherwise it parses the URL, keeps the path and query string, and returns that shorter path for the next request.

**Call relations**: The `paginate` method calls this after each page of results. `paginate` uses the returned path as the next loop target, so this helper is the small bridge between Klaviyo’s pagination style and the connector’s request style.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This helper chooses the correct timestamp field for incremental syncing of a stream. Klaviyo is not fully consistent: most resources use `updated`, some use `updated_at`, and events use `datetime`.

**Data flow**: It receives a stream description and checks the stream name against the connector’s known exceptions. It returns the exact field name that should be used in Klaviyo’s filter and sort query. Nothing else is changed.

**Call relations**: This method is used by the initial-query builder when preparing the first API request for a stream. Its choice affects which records Klaviyo returns first and how the connector resumes from a previous sync position.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This method builds the query parameters for the first page of a Klaviyo stream. It sets page size, sorting, optional incremental filtering, and a few stream-specific extras needed to make records more useful.

**Data flow**: It receives a stream description and an optional saved cursor value. It starts with the page size. If the stream supports a cursor, it adds a sort field; if a cursor value exists, it also adds a Klaviyo filter meaning “only return records at or after this timestamp.” For profiles it asks Klaviyo to include subscription details. For events it asks Klaviyo to include metric information. It returns the dictionary of query parameters for the first API call.

**Call relations**: The `paginate` method calls this before fetching the first page. Later pages do not use it because Klaviyo’s `links.next` URL already contains the needed pagination information.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This helper safely extracts the ID of a related Klaviyo object from a nested relationship block. It avoids errors when Klaviyo leaves out a relationship or returns it in an unexpected shape.

**Data flow**: It receives a relationships object and the relationship name to look for, such as `profile`, `metric`, or `list`. It checks each nested layer before reading `data.id`. If an ID is present, it returns it as text; otherwise it returns nothing.

**Call relations**: The `flatten` method calls this when it needs to surface related IDs on events and segments. This keeps the careful defensive checks in one place instead of repeating them for every relationship.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method converts one Klaviyo record from its nested API shape into a flatter record that the rest of the system can index, compare, and recall more easily. It also pulls out a few high-value details, such as profile email consent, campaign subject lines, event metric IDs, and parent list IDs.

**Data flow**: It receives one raw Klaviyo record and the stream it came from. It starts a new flat dictionary with the record ID and resource type, then copies fields from `attributes` onto the top level. For lists and segments it removes `profile_count` so changing membership counts do not make the list or segment look changed. Depending on the stream, it then adds extra fields from nested subscription, audience, event, or relationship data. It returns the flattened record and does not modify external state.

**Call relations**: This method is used by the broader source framework after records have been fetched. It calls `_lift_relationship_id` when it needs related object IDs, turning Klaviyo’s nested relationship links into simple fields that downstream sync and recall code can use.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method fetches all pages for one Klaviyo stream. It starts at the stream’s API path, asks for records page by page, follows Klaviyo’s next-page links, and yields batches of raw records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the first request query, requests data from Klaviyo, optionally enriches event records with metric names from the included response data, yields any records found, then follows the next-page link until there is no next page. If Klaviyo returns an authorization or permission error, it turns that into a stream-skip signal; other HTTP errors are allowed to bubble up.

**Call relations**: The connector framework calls this during a sync whenever it needs records from a Klaviyo stream. Inside the loop it uses `_initial_query` to prepare the first request and `_next_path` to move from one page to the next. If access is refused, it raises `StreamSkipped` so the run can record that this particular stream was unavailable instead of treating the whole connector as broken.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `during Mailchimp source sync`

Mailchimp stores related data in layers: an account has lists, lists have members and segments, segments have members, reports have unsubscribes and email activity. This file is the map and walking plan for that maze. Without it, the system would not know which Mailchimp web addresses to call, how to page through long result sets, or how to attach parent information like a list ID or campaign ID to child records.

The file defines the available Mailchimp streams, including their identifying key and optional cursor field. A cursor is a saved timestamp used to ask Mailchimp for only records changed since the last sync. The main connector, `MailchimpConnector`, is read-only. It relies on the shared REST connector for HTTP calls and authentication, while this file supplies Mailchimp-specific paths, paging rules, and record cleanup.

Most streams use offset pagination, like reading a long book 500 lines at a time. Some streams require fan-out: first fetch all lists, then fetch members for each list; or first fetch all reports, then fetch email activity for each report. Email activity has one extra twist: Mailchimp groups many actions under one recipient, so this connector splits those actions into separate rows and creates a stable ID for each one. If Mailchimp rejects access with an authorization error, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: Builds a `StreamSpec`, which is the small description object the sync system uses to understand one Mailchimp stream. It records things like the stream name, the primary key, and which timestamp can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as source object, primary key, cursor field, and timestamp fields. It fills in sensible defaults where details are missing, then returns a `StreamSpec` object ready to be placed in the connector’s stream list.

**Call relations**: This helper is used while the module is loaded to build `MAILCHIMP_STREAMS`. It hands its settings to `StreamSpec.__init__`, so the rest of the connector can later ask each stream how it should be identified and filtered.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adjusts Mailchimp records into the shape expected by the rest of the system. In particular, subscriber records get a `created_at` value derived from Mailchimp’s signup or opt-in timestamps.

**Data flow**: It receives one record and the stream description for that record. For list member streams, it copies the record and adds `created_at` from `timestamp_signup` or `timestamp_opt`; for all other streams, it returns the record unchanged.

**Call relations**: This is part of the connector’s normal record cleanup step after records are fetched. It does not call other functions in this file; it simply prepares records so downstream storage can rely on a common created-time field where possible.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field where Mailchimp places the actual records for a stream. Mailchimp wraps responses under names like `lists`, `members`, or `emails`, and this function chooses the right wrapper name.

**Data flow**: It receives a stream description, looks up the stream name in the file’s data-field map, and returns the matching response field. If there is no special entry, it uses the stream name itself.

**Call relations**: Pagination helpers call this before asking the shared REST paging code to read a Mailchimp response. It is used by top-level, per-list, and per-report pagination so those paths can all find records in Mailchimp’s wrapped JSON.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Turns a saved cursor value into the query parameter Mailchimp understands for that stream. This lets a sync ask for only newer or changed records when Mailchimp supports that filter.

**Data flow**: It receives a stream description and an optional cursor timestamp. If either is missing, or if Mailchimp has no known parameter for that cursor field, it returns an empty set of parameters. Otherwise it returns a small dictionary such as `{since_last_changed: cursor}`.

**Call relations**: The pagination helpers use this when they build API requests for streams that can be filtered by time. It keeps Mailchimp’s different `since_*` parameter names in one place instead of spreading them across every endpoint walker.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the traffic director for reading a Mailchimp stream. Given a stream name, it chooses the correct paging strategy and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks which family the stream belongs to: top-level resources, children of lists, deeper list children, children of reports, or email activity. It then delegates to the matching helper and yields each page it gets back. If Mailchimp returns a permission or authentication refusal, it turns that into a `StreamSkipped` message.

**Call relations**: The wider sync engine calls this when it wants records for a Mailchimp stream. This function then hands work to helpers such as `_paginate_top_level`, `_paginate_per_list`, `_paginate_segment_members`, `_paginate_per_report`, or `_paginate_email_activity`, depending on the shape of the Mailchimp endpoint.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Mailchimp resources that live directly at one API path, such as lists, campaigns, automations, and reports. These are the easiest streams because they do not require first looking up a parent object.

**Data flow**: It receives the HTTP client, stream description, API path, and optional cursor. It chooses the right response field and cursor parameters, then asks the base REST connector to fetch offset-based pages of up to 500 records. It yields each page as it arrives.

**Call relations**: `paginate` calls this for streams listed as top-level resources. This helper uses `_data_field` and `_cursor_params` to translate stream metadata into the exact request shape needed by Mailchimp’s API.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the common paging routine for nested Mailchimp endpoints. It is used whenever the connector already knows a specific parent path, such as one list’s members or one report’s unsubscribes.

**Data flow**: It receives an HTTP client, a full API path, the JSON field containing records, and optional query parameters. It asks the base REST connector to walk through the endpoint using Mailchimp’s `count` and offset style, then yields each page of records.

**Call relations**: Several higher-level walkers call this after they have built the correct nested path. It is the reusable “read this child collection page by page” tool used by per-list, interest, segment-member, per-report, and email-activity flows.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: Reads an endpoint and yields only the `id` values from its records. This is used when the connector needs parent IDs before it can fetch child data.

**Data flow**: It receives an HTTP client, an API path, and the response field containing records. It pages through that endpoint and, for each dictionary record with an `id`, yields that ID as text.

**Call relations**: `_list_ids` and `_report_ids` call this to avoid repeating the same ID-extraction logic. Those IDs then drive the fan-out flows that fetch children for every list or report.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of all Mailchimp audience lists. Many Mailchimp records are stored under a specific list, so these IDs are the starting points for list-based fan-out.

**Data flow**: It receives an HTTP client, calls `_ids` on the `/3.0/lists` endpoint, and yields each list ID it finds.

**Call relations**: List-based paginators call this before fetching members, segments, tags, interest categories, interests, or segment members. It supplies the parent list ID that those helpers insert into each child request path.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of Mailchimp campaign reports. Report IDs are needed before the connector can fetch report-specific details like unsubscribes or email activity.

**Data flow**: It receives an HTTP client, calls `_ids` on the `/3.0/reports` endpoint, and yields each report ID it finds.

**Call relations**: Report-based paginators call this before fetching unsubscribes or email activity. It supplies the campaign report ID used to build each nested report path.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child collections that belong to each Mailchimp list, such as members, segments, tags, or interest categories. It also marks each returned child row with its parent list ID when requested.

**Data flow**: It receives the HTTP client, stream description, child path name, cursor, and the parent-field name to stamp onto records. It gets all list IDs, builds a safe URL for each list’s child endpoint, pages through that child collection, adds the list ID to each dictionary record when appropriate, and yields the page.

**Call relations**: `paginate` calls this for streams that are direct children of lists. It uses `_list_ids` to find parents, `_data_field` to know where records sit in the response, `_cursor_params` for incremental filters, `_paginate_child` for the actual paging, and URL quoting so list IDs are safe inside paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches interests, which are nested two levels deep under lists and interest categories. It walks the hierarchy in order: list, then category, then interests.

**Data flow**: It receives the HTTP client, stream description, and optional cursor, though this flow does not apply the cursor. It gets every list ID, fetches that list’s interest categories, then for each category with an ID fetches its interests. Each interest record is stamped with both `list_id` and `category_id`, then pages are yielded.

**Call relations**: `paginate` calls this for the `interests` stream. The helper relies on `_list_ids` to start the walk and `_paginate_child` for both category pages and interest pages, using URL quoting when inserting IDs into Mailchimp paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches members inside each segment of each Mailchimp list. This is a deeper fan-out because the connector must discover lists first, then segments, then the members of each segment.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It builds any cursor filter, gets all list IDs, fetches each list’s segments, then fetches the members for every segment with an ID. Each member row is stamped with its `list_id` and `segment_id`, and each page is yielded.

**Call relations**: `paginate` calls this for the `segment_members` stream. It uses `_list_ids` for parent lists, `_paginate_child` for segment and member pages, `_cursor_params` for time-based filtering of members, and URL quoting to safely build nested paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child collections that belong to each campaign report, such as unsubscribe records. It adds the report or campaign ID to each child row so the row does not lose its context.

**Data flow**: It receives the HTTP client, stream description, child path, cursor, and optional parent-field name. It gets all report IDs, builds each nested report path, pages through the child records, stamps the parent ID onto each dictionary record when requested, and yields the page.

**Call relations**: `paginate` calls this for report-based child streams other than email activity. It uses `_report_ids` to find reports, `_data_field` to read the right response wrapper, `_cursor_params` for incremental filters, `_paginate_child` for paging, and URL quoting for safe paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches email activity from each report and turns Mailchimp’s grouped activity lists into one row per action. This matters because downstream storage needs stable individual records, not a bundle of actions hidden inside one recipient object.

**Data flow**: It receives the HTTP client and optional cursor. It builds a `since` filter if a cursor exists, gets every report ID, fetches that report’s email activity pages, and then splits each recipient’s `activity` array into separate records. Each new record includes the recipient-level fields, the action fields, the campaign ID, and a synthesized ID made from email ID, action, and timestamp. It yields only pages that contain exploded activity rows.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to walk every report and `_paginate_child` to fetch each report’s email activity, then performs the Mailchimp-specific reshaping before handing records back to the sync engine.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Form response collection
Typeform provides read-only syncing for forms, responses, workspaces, themes, images, and webhooks.

### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync runs`

This connector is the bridge between Typeform’s web API and UFO’s source-sync system. Typeform stores data in several separate areas: forms, the answers people submit to those forms, workspaces, visual themes, uploaded images, and webhooks. Without this file, the system would not know which Typeform API endpoints to call, how to move through Typeform’s pages of results, or how to attach useful form context to responses and webhooks.

The file defines the available Typeform streams, including their record identifiers and time fields used for incremental syncing. Incremental syncing means “only fetch records newer than the last saved point,” like checking only today’s mail instead of rereading the whole mailbox.

The main class, `TypeformConnector`, chooses the right fetching path for each stream. Some Typeform endpoints use simple numbered pages. Form responses use a cursor, which is a token Typeform gives back to say “continue from here next time.” Responses and webhooks are also special because they belong to individual forms, so the connector first lists forms, then asks Typeform for each form’s related records. It adds the form ID and title to those child records so they are easier to understand later. If Typeform refuses access with an authorization error, the stream is skipped cleanly instead of crashing the whole sync.

#### Function details

##### `TypeformConnector.paginate`  (lines 52–79)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Typeform stream. Given a stream name, it chooses the correct fetching method and yields batches of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. It checks which Typeform stream is being requested, calls the matching helper, and passes each returned page onward. If the stream is unknown, or if Typeform rejects access with a 401 or 403 status, it turns that situation into a `StreamSkipped` signal so the rest of the sync can continue.

**Call relations**: The sync framework calls this when it wants records for a Typeform stream. This function then hands off to `_forms`, `_responses`, `_paged_items`, or `_webhooks` depending on the stream. It is the front door for all the more specific Typeform fetching logic in this file.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 81–99)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use ordinary numbered pages. It keeps asking for page 1, page 2, and so on until Typeform says there are no more pages.

**Data flow**: It receives an HTTP client, an API path such as `/forms`, and optional query parameters. For each request, it adds the page number and page size, downloads the response, extracts the list under `items`, and yields that list if it is not empty. It stops when Typeform’s reported page count is reached, or when a short page suggests the end has been reached.

**Call relations**: `paginate` uses this directly for simple streams such as workspaces, images, and themes. `_forms` also uses it as the basic way to list forms before applying any form-specific filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 101–108)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and, when requested, filters them to only forms updated after a saved cursor. It is the connector’s reusable way to get the form list.

**Data flow**: It receives an HTTP client and an optional cursor value. It asks `_paged_items` for all `/forms` pages, then compares each form’s `last_updated_at` value with the cursor if one was supplied. It yields only non-empty batches of forms that should be included in the current sync.

**Call relations**: `paginate` calls this when the requested stream is forms. `_responses` and `_webhooks` also call it because both of those streams must first know which forms exist before they can fetch each form’s child data.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 110–132)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted answers for every Typeform form. It also adds the parent form’s ID and title to each response so a response can be understood outside its original Typeform context.

**Data flow**: It starts by reading all forms with `_forms`, ignoring the incremental cursor at that stage so it can check every form for new responses. For each valid form ID, it builds request parameters; if a cursor exists, it sends it as Typeform’s `since` filter. It then follows Typeform’s response cursor pages, takes each batch of response items, adds `form_id` and `form_title`, and yields the enriched records.

**Call relations**: `paginate` calls this when the responses stream is requested. Internally it depends on `_forms` to discover forms first, then uses the shared cursor-page reader from the base REST connector to walk through each form’s response pages.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 134–143)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches webhook definitions for every Typeform form. A webhook is a saved callback URL Typeform can notify when something happens, such as a new form submission.

**Data flow**: It first gets all forms through `_forms`. For each form with a usable string ID, it requests that form’s `/webhooks` endpoint, extracts the records from the `items` field, adds the form ID and title to each record, and yields the enriched batch if any records exist.

**Call relations**: `paginate` calls this when the webhooks stream is requested. Like `_responses`, it depends on `_forms` because Typeform webhooks are fetched per form rather than from one global endpoint.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).

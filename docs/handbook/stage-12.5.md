# Marketing, ads, social, and forms source connectors  `stage-12.5`

This stage is part of the system’s data intake work. It provides “source connectors,” which are small adapters that know how to talk to outside services, sign in, request data in pages, and reshape the answers into records the rest of the system can store.

Each file is one adapter for a marketing or audience tool. ActiveCampaign reads CRM and marketing collections while handling login and safe paging. Facebook Ads and Google Ads fetch advertising structures such as accounts, campaigns, ad groups or ad sets, ads, and performance metrics. Instagram reads Pages, linked business accounts, media, stories, and analytics through Meta’s API. Klaviyo covers a broad marketing storehouse, including profiles, lists, campaigns, events, catalog items, forms, and webhooks. Mailchimp reads audiences, subscribers, campaigns, reports, tags, segments, and email activity. Typeform brings in forms, responses, workspaces, themes, images, and webhooks.

Together, these files act like plug adapters for different outlets: each service has its own shape, but the system receives steady, named streams of records.

## Files in this stage

### Marketing CRM
ActiveCampaign provides the stage's CRM-oriented marketing source with authentication, collection definitions, and safe API pagination.

### `extensions/sources/ufo_ext_sources/activecampaign.py`

`io_transport` · `during an ActiveCampaign sync run`

ActiveCampaign stores many kinds of business data: contacts, lists, campaigns, deals, accounts, tags, custom fields, users, webhooks, and more. This file is the read-only connector for that data. Without it, the larger system would not know which ActiveCampaign endpoints exist, what each returned list is called, or how to fetch records page by page.

The file first lists all supported streams. A stream is one kind of data to sync, like “contacts” or “deals.” For each stream it records basic facts such as its name, its main ID field, and, when possible, the date field used to pick up only newer changes. Think of this list as a menu of ActiveCampaign tables the system can ask for.

ActiveCampaign’s API returns data in pages: up to 100 records at a time, with an offset saying where to start. The connector builds the right URL path, adds paging settings, and then keeps asking for pages until the API runs out of data. For some streams it also adds a server-side “changed after this time” filter so syncs can be faster.

Authentication is slightly special. ActiveCampaign expects an `Api-Token` header, not the common “Bearer token” style. This file converts the system’s stored credential into the header ActiveCampaign wants. If the API rejects access with a 401 or 403 response, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 97–111)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one ActiveCampaign collection. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main record key.

**Data flow**: It receives a stream name plus optional details like the ActiveCampaign object name, the primary key, the cursor date field, and whether the stream is considered canonical. It uses those values to build a `StreamSpec`, which is the system’s compact description of how that stream should be synced. The result is returned and later stored in the connector’s stream list.

**Call relations**: This helper is used while the file is being loaded to build `ACTIVECAMPAIGN_STREAMS`, the connector’s catalog of available ActiveCampaign data. Each call produces one stream specification that the connector later exposes to the sync framework.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 173–178)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to ActiveCampaign, with the right authentication style. It matters because ActiveCampaign wants an `Api-Token` header, while many services use a different token format.

**Data flow**: It receives the ActiveCampaign base URL and a credential object. If the credential already has a custom transport, such as a broker or proxy layer, it leaves that path alone and asks the parent connector to build the client. Otherwise it checks for a stored API key, rewrites that key into an `Api-Token` header, and returns an asynchronous HTTP client ready to make requests. If there is no API key, it raises an error immediately.

**Call relations**: The sync framework calls this when it is preparing to contact ActiveCampaign. This function adapts the project’s general credential shape into the exact form ActiveCampaign expects, then hands the final client creation back to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 181–182)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function translates the system’s stream name into the exact URL piece and response field name used by ActiveCampaign. It is needed because some stream names use snake_case in this code, while ActiveCampaign uses camelCase in its API.

**Data flow**: It receives a stream specification. It looks up that stream’s name in the mapping of known ActiveCampaign paths. If it finds a match, it returns the API path segment and the response envelope key. If not, it falls back to using the stream name for both. The output tells the paging code where to request data and where to find the records inside the JSON response.

**Call relations**: The pagination function calls this at the start of each stream fetch. It supplies the small but important translation step that lets the rest of the pagination code stay generic instead of having special cases for every oddly named ActiveCampaign endpoint.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 184–208)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches one ActiveCampaign stream in pages and yields each page of records to the sync system. It also adds incremental filters when ActiveCampaign supports them, so repeat syncs can avoid rereading as much old data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value, which is usually the last synced timestamp. It turns the stream into an API path, starts with a page size of 100, and, when the stream supports it, adds a “changed after this cursor” filter. It then asks the shared REST paging helper to request offset-based pages and yields each list of records it receives. If ActiveCampaign returns 401 or 403, it changes that low-level HTTP failure into a clear `StreamSkipped` message; other HTTP errors are passed upward unchanged.

**Call relations**: During a sync, the connector framework calls this to read records for a particular ActiveCampaign stream. It first uses `_resolve_stream_segment` to find the right endpoint details, then delegates the repeated page fetching to the base REST connector. When access is refused, it reports that this stream cannot be read, instead of pretending the stream is empty or crashing without context.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Paid Advertising
Facebook Ads and Google Ads connectors fetch account, campaign, ad structure, and performance records from major ad platforms.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `source sync`

This connector is the read-only bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook API paths to call, how to walk through paged results, or how to turn account-specific advertising data into consistent records.

The file defines a set of streams, which are the named kinds of data the sync can ask for: ad accounts, campaigns, ad sets, ads, and ad insights. Most Facebook Ads data lives under a specific ad account, so the connector first asks Facebook for the user’s ad accounts. It then visits each account and asks for that account’s campaigns, ad sets, ads, or insights. This is like first getting a list of filing cabinets, then opening each cabinet to collect the folders inside.

Facebook returns results in pages, not all at once. The connector follows Facebook’s provided “next page” link until there are no more pages. For campaigns, ad sets, and ads, it can skip older records by comparing their update time with a saved cursor, which is a bookmark from the last sync. For insights, it asks for daily ad-level statistics, either since the cursor date or for the last 90 days on a first run. It also creates a stable row id for each insight because Facebook insight rows do not arrive with one ready-made.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 58–71)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Facebook API collection page by page. It hides the repetitive work of making a request, extracting the list of records, and following Facebook’s next-page link.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It requests that path, reads the JSON response, pulls out the records under the response’s data field, and yields each non-empty page. If Facebook includes a next-page URL, it uses that for the next request; once there is no next page, it stops.

**Call relations**: The account, child-object, and insights readers all rely on this as their shared paging engine. It calls the external records_at helper to safely pick the data list out of Facebook’s response before handing pages back to its callers.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 73–78)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function gets the Facebook ad accounts available to the authenticated user. Other streams need this list because campaigns, ads, and insights are fetched separately for each account.

**Data flow**: It starts with a fixed list of ad account fields to request. It asks _paged to read /me/adaccounts, collects every returned page into one list, and returns that full list of account records.

**Call relations**: paginate uses it directly when the requested stream is ad_accounts. _account_children and _insights also call it first so they know which account-specific API paths to visit next.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 80–105)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads account-owned objects such as campaigns, ad sets, and ads. It also adds account context so each returned record still shows which ad account it came from.

**Data flow**: It receives the stream being synced and an optional cursor bookmark. It chooses the right Facebook fields for that stream, fetches all ad accounts, then requests the matching child collection under each account. If a cursor is present, it keeps only records whose update time is newer than that cursor. Before yielding a page, it adds the account id and account name to each record.

**Call relations**: paginate calls this when it is syncing campaigns, ad_sets, or ads. This function depends on _accounts to find the accounts, _paged to read each account’s Facebook pages, and with_context to attach account information to the returned records.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 107–150)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads daily advertising performance statistics, such as impressions, clicks, spend, and reach, at the ad level. It turns these rows into stable records by creating an id from the account, campaign, ad set, ad, and date.

**Data flow**: It builds the insight query fields and asks Facebook for one day at a time. If a cursor exists, it requests data from that cursor date through today; otherwise it requests the last 90 days. For every ad account, it pages through the account’s insights, adds an ad_account_id, builds a repeatable id for each row, and yields non-empty batches.

**Call relations**: paginate calls this when the ads_insights stream is requested. It first calls _accounts to know which account insight endpoints to visit, then uses _paged to walk through Facebook’s paginated insight responses. It uses json.dumps to send the date range in Facebook’s expected format and datetime.now to set the current end date.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 152–168)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a requested Facebook Ads stream. Given a stream name, it chooses the right reader and yields pages of records back to the sync framework.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For ad_accounts it returns the account list. For campaigns, ad_sets, and ads it delegates to _account_children. For ads_insights it delegates to _insights. If the stream name is unknown, it raises StreamSkipped so the system knows this connector does not implement that stream.

**Call relations**: The wider source framework calls paginate when it wants records for a stream. paginate then routes the work to _accounts, _account_children, or _insights, depending on the stream, and passes the yielded pages back upward.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 170–178)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly reshapes records before they are stored. At the moment, it gives campaign records a simpler status and created_at field while leaving other streams unchanged.

**Data flow**: It receives one record and the stream it belongs to. If the stream is campaigns, it copies the record and adds normalized fields: name, status chosen from effective_status when available, and created_at copied from created_time. For all other streams, it returns the original record as-is.

**Call relations**: This fits after records have been fetched and before they are written into the system’s common shape. It does not call other functions in this file; it is a small cleanup step used by the connector framework when preparing individual records.


### `extensions/sources/ufo_ext_sources/google_ads.py`

`io_transport` · `source sync`

Google Ads data is not fetched with simple fixed web addresses. Instead, the connector sends Google Ads Query Language queries, which are SQL-like requests such as “select these campaign fields from campaign.” This file defines the streams the system can read and the steps needed to ask Google Ads for each one.

The connector first builds an HTTP client for Google Ads. OAuth proves which advertiser account the user has granted access to, but Google Ads also requires a separate developer token. This file reads that token from environment variables. If the token is missing, the connector skips the stream instead of crashing the whole sync.

To fetch data, the connector asks Google Ads which customer accounts are accessible. It then runs the right query once for each customer account, like a mail carrier visiting every building on a route. Each returned row is stamped with the customer id so records can later be traced back to the account they came from.

Google Ads responses are nested, with fields grouped under objects like campaign, customer, segments, and metrics. The flattening step pulls out the important values into simple top-level fields, such as campaign name, customer id, metric date, clicks, and impressions. This is what makes the data usable by the wider source-sync system.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 70–79)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token required by Google’s API. OAuth access alone is not enough for Google Ads, so without this token the connector cannot safely make requests.

**Data flow**: It reads the process environment, first looking for UFO_GOOGLE_ADS_DEVELOPER_TOKEN and then GOOGLE_ADS_DEVELOPER_TOKEN. If it finds a value, it returns that token. If it finds nothing, it raises a skip signal so this source stream is left out rather than treated as a broken sync.

**Call relations**: When the connector is building its HTTP client, _make_client calls this function to get the token that must be attached to every Google Ads request. If the token is missing, the skip signal travels upward and prevents unauthorized API calls.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 81–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client used to talk to Google Ads. It adds the Google Ads-specific headers that the regular shared REST connector would not know about.

**Data flow**: It receives a base URL and an OAuth credential. It first asks the parent REST connector to create the basic authenticated client. Then it adds the developer token header, and, if present in the environment, adds a login customer id with dashes removed. It returns the ready-to-use client.

**Call relations**: This is part of setup before any stream is read. It calls _developer_token because every later request made by paginate, _customer_ids, and _search_stream depends on the client already carrying the required Google Ads headers.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 91–98)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. Those customer ids are the starting points for all later data queries.

**Data flow**: It sends a request to Google Ads’ accessible-customers endpoint and receives a response containing resource names such as customers/1234567890. It checks that the response has the expected shape, extracts the final id part from each valid name, and returns a list of plain customer id strings.

**Call relations**: _query_each_customer calls this before running a stream query. In the larger flow, it decides which advertiser accounts will be visited for customers, campaigns, ads, and metrics.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 100–119)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one Google Ads query for one customer account and collects the returned rows. It is the low-level step that actually asks Google Ads for campaign, ad, or metric data.

**Data flow**: It receives an HTTP client, a customer id, and a query string. It posts that query to the customer’s Google Ads searchStream endpoint. Google returns batches of results, so the function walks through each batch, keeps only dictionary-like result rows, and returns one flat list of rows.

**Call relations**: _query_each_customer calls this after choosing a customer id. It does not decide what query to run; paginate chooses the query for the stream, and this function performs the actual request for each customer.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 121–129)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It makes multi-account syncing feel like one stream to the rest of the system.

**Data flow**: It receives an HTTP client and a query. It first gets the accessible customer ids, then runs the query separately for each one. For every non-empty result set, it adds the customer id to each row and yields that group of rows as a page.

**Call relations**: paginate calls this once it has built the right query for a stream. This function sits between the stream-level logic and the per-customer API call: it gets ids from _customer_ids, fetches rows through _search_stream, and hands pages back to paginate.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 131–199)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function decides what Google Ads query to run for each supported stream and yields the results in pages. It is the main read path for customers, campaigns, ad groups, ads, campaign metrics, and customer-client relationships.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is a remembered position from a previous sync. Based on the stream name, it builds the matching Google Ads query. For campaign metrics, it uses the cursor date if available, otherwise it starts from about 90 days ago. It then yields pages produced by _query_each_customer. If the stream is unknown, or Google Ads refuses access with a permission-related status, it raises a skip signal.

**Call relations**: The source-sync framework calls paginate when it needs records for a stream. paginate chooses the query, delegates the account-by-account work to _query_each_customer, and passes pages onward to the rest of the sync pipeline. It also translates Google Ads permission refusals into stream skips so one inaccessible stream does not look like an unexpected programming error.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 201–232)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function turns nested Google Ads rows into simpler records with the key fields at the top level. This makes records easier for the sync system to identify, store, and compare.

**Data flow**: It receives one raw record and the stream it belongs to. For customers, campaigns, and campaign metrics, it safely reads nested objects such as customer, campaign, segments, and metrics, then copies important values into top-level fields like id, name, resource_name, date, campaign_id, impressions, clicks, and cost_micros. For streams that do not need special reshaping, it returns the record unchanged.

**Call relations**: After paginate has produced raw Google Ads rows, the wider source framework can call flatten before writing records. It relies on dict_or_empty so missing or oddly shaped nested objects become harmless empty dictionaries instead of causing failures.

*Call graph*: 1 external calls (dict_or_empty).


### Social Analytics
The Instagram connector reads business account content and analytics through the Facebook Graph API.

### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `during source sync`

Instagram business data is reached through Facebook Pages, not by going straight to an Instagram username. This file is the map for that route. It first asks Facebook for the Pages the connected account can access, then looks inside each Page for a linked Instagram business account. From those accounts it reads posts, stories, and analytics numbers called insights.

The connector is built around streams, which are named feeds of records such as “pages”, “media”, or “user_insights”. The main `paginate` method chooses the right path for each stream. For Facebook Graph API collections, `_paged` follows the API’s “next page” link, like turning pages in a catalog until there are no more. Media and stories can use a cursor, which is a saved timestamp watermark; records at or before that watermark are skipped so repeat syncs do not reread old items.

The file is careful about permissions. If a whole account-level request is refused because the token is missing access, it marks the stream as skipped instead of crashing the whole run. If a single media item or story cannot return insights, it quietly skips that object and keeps going. The connector does not publish or change Instagram content; it only reads data.

#### Function details

##### `InstagramConnector._paged`  (lines 86–103)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Facebook Graph API collection from start to finish. It follows the API’s paging links and yields each non-empty batch of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a request, reads the JSON response, pulls the list under the `data` field, yields that list when it has records, then follows `paging.next` if Facebook provides another page. After the first request, it stops sending the original query parameters because the `next` URL already contains them.

**Call relations**: `_pages` uses this to walk `/me/accounts`, and `_account_collection` uses it to walk each Instagram account’s media or stories. It relies on `records_at` to safely extract the response’s `data` list.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 105–110)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches all Facebook Pages available to the current grant, including each Page’s linked Instagram business account when present. This is the starting point for nearly every other Instagram stream.

**Data flow**: It builds a Facebook fields request asking for Page identity plus nested Instagram business account details. It passes that request to `_paged`, collects all returned page batches into one list, and returns that list.

**Call relations**: `paginate` calls this directly for the `pages` stream. `_instagram_accounts` also calls it because Instagram business accounts are discovered from Pages rather than from a separate top-level account list.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 112–122)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the Instagram business accounts linked to the accessible Facebook Pages. It also attaches the owning Page’s id and name so later records can be traced back to the Page that exposed them.

**Data flow**: It starts with the full Page list from `_pages`. For each Page, it looks for an `instagram_business_account` object with an id. It stores accounts by id to avoid duplicates, adds `page_id` and `page_name`, and returns the unique accounts as a list.

**Call relations**: `paginate` calls this for the `instagram_accounts` stream. `_account_collection` and `_user_insights` call it first because both need to know which Instagram account ids they are allowed to query.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 124–145)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a repeated kind of object, such as media or stories, from every discovered Instagram business account. It can also skip records that are not newer than the saved cursor timestamp.

**Data flow**: It receives a path suffix such as `media` or `stories`, the fields to request, and optional cursor information. It gets all Instagram accounts, queries each account’s collection through `_paged`, filters each batch by the cursor field when a cursor is present, adds the Instagram account id as context to every kept record, and yields non-empty batches.

**Call relations**: `paginate` uses this for the `media` and `stories` streams. It depends on `_instagram_accounts` to find account ids, `_paged` to follow Facebook pagination, and `with_context` to add the parent Instagram account id before records move onward.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 147–181)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches analytics for individual objects, such as media posts or stories. It turns each object’s insight rows into records with stable ids that include the parent object id.

**Data flow**: It receives an async stream of object batches, a comma-separated metric list, and the name of the insight stream being produced. For each object with an id, it requests `/{object_id}/insights`. If Facebook says that one object cannot provide insights with status 400, 403, or 404, it skips just that object. Otherwise it converts each returned insight into a record containing the insight data, a generated id, the parent object id, and the stream name, then yields batches when there is anything to return.

**Call relations**: `paginate` calls this for `media_insights` and `story_insights`. In those cases, `paginate` first creates an object stream by calling itself for media or stories, then hands that stream to `_object_insights` so analytics can be read object by object. It uses `records_at` to pull insight rows from the API response.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 183–213)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads account-level daily analytics for each Instagram business account, such as impressions, reach, and profile views. It produces one record per metric value per day.

**Data flow**: It receives an HTTP client and an optional cursor. It gets all Instagram accounts, requests daily insights for each account, then walks each insight’s `values` list. For each value, it checks the `end_time`; if the value is not newer than the cursor, it is skipped. New values are turned into records with a generated id, metric name, date information, and Instagram account id, then yielded in batches.

**Call relations**: `paginate` calls this for the `user_insights` stream. It first depends on `_instagram_accounts` to know which accounts to query, then uses `records_at` and `list_or_empty` to safely read the nested insight data returned by Facebook.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 215–293)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the connector’s traffic director. Given a requested stream name, it chooses the correct reading path and yields batches of records for that stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It compares the stream name against the supported Instagram streams, calls the helper that knows how to fetch that kind of data, and yields each resulting batch. If the stream is unknown, it raises `StreamSkipped` so the runner records that the stream was not implemented. If Facebook refuses access with status 401 or 403, it also raises `StreamSkipped` with a permission-focused message instead of treating the whole sync as a hard failure.

**Call relations**: The sync runner calls `paginate` when it wants records for a stream. `paginate` then delegates to `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, or `_user_insights` depending on the stream. For media and story insights, it chains the flow: first produce media or story objects, then pass those objects into `_object_insights` to fetch their analytics.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Email Marketing Automation
Klaviyo and Mailchimp connectors expose audiences, profiles, campaigns, events, reports, and related email marketing records as syncable streams.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `sync runs and page-by-page API reading`

Klaviyo exposes its data through a web API, but the data is wrapped in a fairly nested format and split across many pages. This file is the bridge between that Klaviyo-shaped world and the project’s simpler stream-based sync system. Without it, the system would not know which Klaviyo resources can be synced, how to authenticate, how to ask for only newer records, or how to follow Klaviyo’s pagination links.

The file first defines a list of streams. A stream is one kind of Klaviyo data, such as profiles, campaigns, or events. Each stream records the API object name, the main ID field, and whether there is a time field that can be used as a bookmark for incremental syncing. Incremental syncing means “start from where we left off” instead of downloading everything every time.

The `KlaviyoConnector` then supplies the Klaviyo-specific rules. It adds the required API headers, builds the first request for each stream, follows `links.next` to get later pages, and skips streams when Klaviyo says the current key lacks permission. It also flattens Klaviyo records. Klaviyo sends records like a folder with `attributes` and `relationships` inside; this connector lifts important values out to the top level so downstream search and storage can use them easily. For events, it also looks at included metric records so an event can carry a human-readable metric name.

#### Function details

##### `_stream`  (lines 35–49)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream description for a Klaviyo resource. It keeps the long list of Klaviyo streams readable by filling in common defaults, such as using the stream name as the API object name unless told otherwise.

**Data flow**: It receives a friendly stream name and optional details like the API object name, primary key, cursor field, and whether the stream is considered canonical. It packages those choices into a `StreamSpec`, which is the project’s small description object for a syncable data source. The result is used later in the connector’s stream list.

**Call relations**: This function is used while the file is being loaded to build `KLAVIYO_STREAMS`. It hands each completed stream description to the connector class through `streams_list`, so later sync code knows what Klaviyo resources are available.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 86–94)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Klaviyo and adds the headers Klaviyo requires. In plain terms, it prepares the “messenger” with the right ID badge before sending requests.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent REST connector to create the normal HTTP client. Then it adds Klaviyo’s required `revision` header, and if the credential contains a private key, it adds Klaviyo’s `Authorization` header using Klaviyo’s private-key format. It returns the ready-to-use client.

**Call relations**: The wider REST connector setup calls this when a sync needs to contact Klaviyo. This method builds on the parent connector’s client creation instead of replacing it, then adds the Klaviyo-only authentication details before any API pages are requested.


##### `KlaviyoConnector._next_path`  (lines 97–108)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Klaviyo’s full next-page URL into the path and query string needed by the already configured client. It is used so pagination can continue cleanly from one page to the next.

**Data flow**: It receives the `links.next` value from a Klaviyo response, which may be missing or may be a full URL. If there is no usable URL path, it returns `None`, meaning there is no next page. Otherwise, it strips the URL down to just the path plus any query text and returns that smaller string.

**Call relations**: During pagination, `KlaviyoConnector.paginate` reads the next-page link from each response and calls this helper. The returned path becomes the next request target, or `None` tells the loop to stop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 111–116)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This chooses the correct time field to use as the sync bookmark for a stream. Different Klaviyo resources use slightly different field names for “when this changed,” so this keeps that rule in one place.

**Data flow**: It receives a stream description. If the stream is events, it returns `datetime`; if it is one of the resources that use `updated_at`, it returns `updated_at`; otherwise it returns the usual `updated`. The output is a field name used in sorting and filtering requests.

**Call relations**: This helper supports first-page query construction. `KlaviyoConnector._initial_query` uses it when it needs to ask Klaviyo for records in time order or for records newer than the saved cursor.


##### `KlaviyoConnector._initial_query`  (lines 119–137)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for the first API request for a stream. It decides page size, time-based filtering, sorting, and any extra fields Klaviyo should include.

**Data flow**: It receives a stream description and an optional saved cursor value. It starts with the requested page size. If the stream supports a cursor, it adds sorting by the right time field, and if a cursor value exists, it also adds a Klaviyo filter meaning “give me records at or after this time.” For certain streams, it asks Klaviyo for helpful extras, such as profile subscriptions, list counts, or event metric data. It returns the complete parameter dictionary for the first request.

**Call relations**: At the beginning of `KlaviyoConnector.paginate`, this function prepares the first request. Later pages do not call it, because Klaviyo’s own `links.next` URL already contains the correct continuation information.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 140–151)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This safely extracts the ID of a related Klaviyo object from a nested relationship block. It avoids crashes when Klaviyo leaves a relationship empty or shaped differently than expected.

**Data flow**: It receives a relationships object and the name of the relationship to read, such as `profile` or `metric`. It checks each nested layer before touching it: relationship name, `data`, and `id`. If it finds an ID, it returns it as text; if anything is missing or not shaped like a dictionary, it returns `None`.

**Call relations**: The `flatten` method calls this when turning records into simpler flat records. It is especially useful for events and segments, where IDs of related objects are important for recall but are buried inside Klaviyo’s relationship structure.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 153–217)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This converts one Klaviyo record from Klaviyo’s nested API format into a flatter record that is easier for the rest of the system to store, search, and use as an incremental sync bookmark.

**Data flow**: It receives a raw Klaviyo record and the stream it belongs to. It starts a new flat record with the record ID and resource type, then copies fields from `attributes` up to the top level. For certain stream types, it pulls out extra useful details: profile email consent, campaign subject and sender fields, event profile and metric IDs, event message IDs, and a segment’s parent list ID. It returns the flattened dictionary and does not modify external state, though it reads nested pieces of the input record.

**Call relations**: This is the connector’s cleanup step after records have been fetched. It uses `_lift_relationship_id` for relationship IDs, and its output is what downstream sync and storage code can treat as a normal, flat record rather than a Klaviyo-specific nested envelope.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 219–272)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads all pages for one Klaviyo stream, yielding batches of raw records as they arrive. It knows how to start the request, follow Klaviyo’s next-page links, enrich event records with metric names, and turn permission failures into a clean skipped-stream result.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It builds the first path and query, requests a page, reads the `data` records, and yields them as a batch when present. For event streams, it also looks at the response’s included metric objects and copies each metric’s name into the matching event attributes. After each page, it reads `links.next`, converts it into the next request path, and repeats until there is no next page. If Klaviyo returns 401 or 403, it raises `StreamSkipped` so the run records that this stream could not be read because the key lacks permission.

**Call relations**: This is the main page-reading loop used by the connector during a sync. It calls `_initial_query` once to prepare the first request, calls `_next_path` after each response to continue pagination, and relies on the base REST connector’s `_get` method to actually fetch each page. When access is refused, it hands control back to the sync framework by raising `StreamSkipped` instead of treating the whole run as an unexpected failure.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `during source sync, while fetching Mailchimp streams`

Mailchimp does not expose all of its data as one simple list. Some data is top-level, like campaigns or reports. Other data sits inside a parent object, like members inside an audience list, or email activity inside a campaign report. This file is the map and walking guide for that maze.

It defines the Mailchimp streams the system knows about, including their names, primary keys, and optional cursor fields. A cursor is a saved timestamp or marker that lets a later sync ask Mailchimp for only newer records where Mailchimp supports that. The connector then decides, stream by stream, which route to take through the Mailchimp API.

For simple resources, it walks through pages using Mailchimp’s `count` and `offset` query parameters. For nested resources, it first fetches parent IDs, such as list IDs or report IDs, then fetches the child records under each parent. It also adds the parent ID back onto each child row so the row still makes sense after it leaves Mailchimp.

One special case is email activity. Mailchimp groups many actions under one recipient, but this system wants one row per action. The connector splits those actions apart and creates a stable ID for each one. If Mailchimp rejects access with a 401 or 403 response, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Mailchimp stream, such as `lists` or `campaigns`. This keeps the stream list compact and consistent by filling in common defaults like the source object name and primary key.

**Data flow**: It takes a stream name plus optional details such as the Mailchimp object name, primary key, cursor field, and whether the stream is canonical. It fills in missing defaults, then returns a `StreamSpec`, which is the system’s standard description of a stream.

**Call relations**: This helper is used when the file builds `MAILCHIMP_STREAMS`. It hands each completed stream description to `StreamSpec`, so the connector later knows what streams exist and how each one should be read.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector._data_field`  (lines 101–102)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field where Mailchimp places the records for a given stream. For example, the `list_members` stream is stored under Mailchimp’s `members` field.

**Data flow**: It receives a stream description, looks up the stream name in the file’s record-field map, and returns the matching Mailchimp response field. If there is no special mapping, it returns the stream name itself.

**Call relations**: Pagination helpers call this before reading pages, so they know which part of Mailchimp’s response contains the actual rows. It supports top-level streams, per-list streams, and per-report streams.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 105–112)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameter that asks Mailchimp for only records newer than a saved cursor, when Mailchimp supports that filter. This helps incremental syncs avoid rereading everything.

**Data flow**: It receives a stream description and an optional cursor value. If either is missing, or if the stream’s cursor field has no Mailchimp query parameter, it returns an empty dictionary. Otherwise it returns a one-item dictionary such as `{"since_last_changed": cursor}`.

**Call relations**: Several pagination paths call this before making API requests. It gives `_paginate_top_level`, `_paginate_per_list`, `_paginate_segment_members`, and `_paginate_per_report` the extra query parameters they should pass down into page fetching.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 114–170)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching strategy for each Mailchimp stream and yields pages of rows. It is the main doorway the sync system uses to read Mailchimp data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, sends the request to the appropriate helper, and yields each page that helper produces. If Mailchimp says access is unauthorized or forbidden, it converts that into a `StreamSkipped` error with a human-readable reason.

**Call relations**: The broader source sync calls this when it wants records for a Mailchimp stream. This function then delegates to helpers for top-level resources, list children, report children, segment members, interests, or email activity, depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 172–183)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads simple Mailchimp collections that live at their own API path, such as lists, campaigns, automations, and reports. These are the easiest streams because they do not need a parent object first.

**Data flow**: It receives an HTTP client, a stream description, an API path, and an optional cursor. It finds the right response field, builds any cursor query parameter, and asks the base REST connector to walk through offset-based pages. It yields each page of records as it arrives.

**Call relations**: `paginate` calls this for top-level streams. This helper relies on `_data_field` and `_cursor_params` to prepare the request details before handing the actual page walking to the inherited REST pagination method.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 185–202)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a nested Mailchimp collection at a specific API path using the same offset paging style. It is the shared worker for child endpoints like list members, segments, interests, and report activity.

**Data flow**: It receives an HTTP client, a path, the response field that contains records, and optional base query parameters. It asks the base REST connector to request pages with Mailchimp’s `count` limit parameter and yields each page of rows.

**Call relations**: The more specialized helpers call this after they have built the correct nested path. It keeps the repeated page-fetching behavior in one place so per-list, per-report, interest, segment-member, and email-activity flows can share it.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 204–212)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: Fetches IDs from a paged Mailchimp collection. It is used when later requests need parent IDs before they can fetch child records.

**Data flow**: It receives an API path and the response field that contains rows. It walks through the pages, checks each row for an `id`, converts that ID to text, and yields IDs one by one. Rows without usable IDs are ignored.

**Call relations**: `_list_ids` and `_report_ids` call this to get the parent identifiers they need. Those IDs then drive the deeper list-based and report-based pagination paths.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 214–216)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of Mailchimp audience lists. Many Mailchimp resources, such as members, tags, segments, interests, and segment members, can only be fetched after knowing which list they belong to.

**Data flow**: It asks `_ids` to read the `/3.0/lists` endpoint and pull IDs from the `lists` response field. It then yields each list ID to the caller.

**Call relations**: List-based pagination helpers call this first. Once they receive a list ID, they build child paths under that list and continue fetching more detailed records.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 218–220)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of Mailchimp campaign reports. Report-related child streams, such as unsubscribes and email activity, need these IDs before their endpoints can be queried.

**Data flow**: It asks `_ids` to read the `/3.0/reports` endpoint and pull IDs from the `reports` response field. It yields each report ID as text.

**Call relations**: Report-based pagination helpers call this before fetching children under each report. The returned IDs become part of the API paths used for unsubscribes and email activity.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 222–243)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records that live under every Mailchimp audience list, such as members, segments, tags, or interest categories. It also labels each returned row with the list it came from.

**Data flow**: It receives a stream, a child path name, an optional cursor, and the name of the parent field to add. It gets all list IDs, builds a safe URL for each list’s child endpoint, pages through the child records, adds the list ID to each row when requested, and yields the pages.

**Call relations**: `paginate` calls this for streams that are direct children of lists. It uses `_list_ids` to find parents, `_data_field` and `_cursor_params` to prepare reading, and `_paginate_child` to fetch the actual pages.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 245–267)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches Mailchimp interests, which are nested two levels deep: first under a list, then under an interest category. It adds both the list ID and category ID to each interest row.

**Data flow**: It gets each list ID, fetches that list’s interest categories, then uses each category ID to fetch the interests inside it. For every interest row, it adds `list_id` and `category_id` if they are not already present, then yields the page.

**Call relations**: `paginate` calls this for the `interests` stream. This helper uses `_list_ids` to start the walk and `_paginate_child` for both category pages and interest pages, building safe URL pieces along the way.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 269–291)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the members inside each Mailchimp segment. Because segment members are nested under both a list and a segment, it preserves both IDs on each returned row.

**Data flow**: It receives a stream and optional cursor. It builds cursor parameters when possible, gets every list ID, fetches that list’s segments, then fetches members for each segment. It adds `list_id` and `segment_id` to each member row and yields the resulting pages.

**Call relations**: `paginate` calls this for the `segment_members` stream. It uses `_list_ids` to find lists, `_paginate_child` to fetch segments and members, and `_cursor_params` to filter member records when Mailchimp supports it.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 293–313)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child records that live under every Mailchimp report, such as unsubscribes. It labels each child row with the report or campaign ID it came from.

**Data flow**: It receives a stream, a report child path, an optional cursor, and a parent field name to stamp onto rows. It gets report IDs, builds each report child URL, pages through the records, adds the parent campaign/report ID when requested, and yields the pages.

**Call relations**: `paginate` calls this for report-child streams. It uses `_report_ids` to find parent reports, `_data_field` and `_cursor_params` to prepare request details, and `_paginate_child` to do the repeated page fetching.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 315–347)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches email activity from each Mailchimp report and turns Mailchimp’s nested activity lists into one row per email action. This makes opens, clicks, bounces, and similar actions easier to store and compare across syncs.

**Data flow**: It receives an HTTP client and optional cursor. It adds a `since` filter if there is a cursor, gets each report ID, fetches that report’s email activity pages, then copies each recipient’s shared fields onto every action in its `activity` list. For each action row, it adds the campaign ID and creates a stable ID from email ID, action, and timestamp when Mailchimp did not provide one. It yields only non-empty exploded pages.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to walk reports and `_paginate_child` to fetch Mailchimp’s grouped email-activity pages, then reshapes those grouped records before handing them back to the sync system.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


### Forms and Responses
Typeform supplies form, response, workspace, theme, image, and webhook records for the broader source connector stage.

### `extensions/sources/ufo_ext_sources/typeform.py`

`io_transport` · `source sync run`

Typeform is an online form service, and its API does not give all data in one big answer. It sends results in pages, and some data, like responses and webhooks, must be fetched separately for each form. This file is the adapter that knows those Typeform-specific rules.

The main class, TypeformConnector, is a read-only connector. It does not keep the user’s access token itself; the wider runner supplies an authorized HTTP client. Its job is to decide which Typeform endpoint to call for each stream, ask for data page by page, and yield batches of plain records.

For simple lists such as forms, workspaces, images, and themes, it follows Typeform’s numbered pages. For responses, it first lists all forms, then asks Typeform for the responses belonging to each form. It also adds helpful context, such as the form ID and title, to each response so the record is understandable later. Webhooks work similarly: list forms first, then fetch each form’s webhooks.

If Typeform refuses access with a 401 or 403 status, the connector treats that stream as skipped instead of crashing the whole run. This matters because a user’s Typeform grant may not include every possible permission.

#### Function details

##### `TypeformConnector.paginate`  (lines 46–73)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for Typeform streams. Given a requested stream, it chooses the right Typeform fetching method and yields batches of records for the sync system to consume.

**Data flow**: It receives an authorized HTTP client, a stream description, and an optional cursor value that marks where the last sync left off. It checks the stream name, sends the work to the matching helper, and passes each returned page onward. If Typeform says the request is unauthorized or forbidden, it changes that into a clear “stream skipped” signal; unknown streams are skipped the same way.

**Call relations**: The sync framework calls this when it wants records from a Typeform stream. It hands form requests to TypeformConnector._forms, response requests to TypeformConnector._responses, simple paged lists to TypeformConnector._paged_items, and webhook requests to TypeformConnector._webhooks. If a stream cannot be read, it raises StreamSkipped so the larger sync can continue safely.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 75–93)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads Typeform endpoints that use ordinary numbered pages, like page 1, page 2, and so on. It keeps asking for pages until Typeform says there are no more.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It adds a page number and page size, fetches that page, pulls the list under the “items” field, and yields the records if any exist. It then uses Typeform’s page_count value, or the size of the returned page, to decide whether to stop or request the next page.

**Call relations**: TypeformConnector.paginate uses this directly for streams such as workspaces, images, and themes. TypeformConnector._forms also uses it as its basic source of form pages before applying form-specific filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 95–102)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches Typeform forms and, when asked, filters them to only forms updated after a saved cursor. That lets repeat syncs avoid re-reading older form records.

**Data flow**: It receives an HTTP client and an optional cursor, which is usually a timestamp from a previous sync. It reads form pages through TypeformConnector._paged_items, compares each form’s last_updated_at value to the cursor when one is present, and yields only the forms that still matter.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is forms. TypeformConnector._responses and TypeformConnector._webhooks also call it first because Typeform responses and webhooks must be fetched form by form.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 104–126)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches submitted responses for every Typeform form. It is needed because Typeform stores responses underneath each form rather than as one global response list.

**Data flow**: It receives an HTTP client and an optional cursor. First it fetches all forms without filtering them by update time, then it skips any form without a usable ID. For each valid form, it requests that form’s responses, optionally passing the cursor as a “since” value so Typeform returns newer submissions. It follows Typeform’s response cursor, adds the form ID and form title to each response batch, and yields those enriched records.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is responses. Inside, it depends on TypeformConnector._forms to discover the forms to visit, and it uses with_context to attach form information before handing response records back to the main sync flow.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 128–137)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper fetches webhook settings for every Typeform form. Webhooks are callbacks Typeform can send to another service when something happens, so they belong to individual forms.

**Data flow**: It receives an HTTP client. It first fetches all forms, skips forms without a valid ID, then calls the webhooks endpoint for each remaining form. From each API response it extracts the “items” list, adds the form ID and form title to those webhook records, and yields the enriched batch if any records were found.

**Call relations**: TypeformConnector.paginate calls this when the requested stream is webhooks. It uses TypeformConnector._forms to know which forms to inspect, records_at to pull records out of the Typeform response shape, and with_context to preserve which form each webhook came from.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).

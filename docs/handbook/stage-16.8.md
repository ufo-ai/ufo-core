# Marketing, Ads, and Audience Providers  `stage-16.8`

This stage is a set of behind-the-scenes “readers” used when the system syncs marketing and advertising data. Each reader knows how to talk to one outside service, sign in with the right credentials, ask for data in small pages, and turn the replies into regular records the rest of the system can store, search, and recall.

ActiveCampaign reads customer and marketing collections from that platform. Facebook Ads uses Meta’s Graph API, an online doorway for Meta data, to collect ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google advertiser accounts, campaigns, ad groups, ads, and performance numbers. Instagram also uses Meta’s API, but focuses on business Pages, connected Instagram accounts, posts, stories, and insight metrics. Klaviyo reads email and ecommerce marketing data such as profiles, campaigns, events, lists, and catalog items. Mailchimp reads audiences, members, campaigns, reports, unsubscribes, and email activity. Together, these files act like adapters that make many different marketing tools look consistent to the sync engine.

## Files in this stage

### CRM marketing
ActiveCampaign establishes customer relationship and marketing automation reader patterns for authenticated, paged collection syncs.

### `extensions/sources/ufo_ext_sources/providers/active_campaign.py`

`io_transport` · `during source sync when ActiveCampaign streams are read`

ActiveCampaign exposes many kinds of data: contacts, lists, campaigns, deals, accounts, tags, custom fields, users, and more. This file turns those API resources into named “streams,” meaning repeatable feeds of records the rest of the system can ask for. Without it, the system would not know which ActiveCampaign endpoints exist, what field identifies each record, or how to fetch records page by page.

The file starts by listing the ActiveCampaign API’s naming quirks. Most stream names match the API path, but some use camelCase names such as `campaignMessages` or `accountContacts`. The `_STREAM_PATHS` table is the translation guide, like a bilingual street map between this project’s stream names and ActiveCampaign’s endpoint names.

It also marks which streams support server-side incremental filters. “Incremental” means asking only for records changed after the last saved cursor, instead of rereading everything. Streams that do not support this still work, but they are fetched more broadly and later deduplicated by record-level cursor logic elsewhere.

`ActiveCampaignConnector` is the main connector class. It builds an HTTP client with ActiveCampaign’s required `Api-Token` header, resolves stream names into API paths, and walks through paginated API responses using `limit` and `offset`. If ActiveCampaign rejects access with a 401 or 403 response, the connector skips that stream with a clear message instead of treating the whole sync as a mysterious failure.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a `StreamSpec`, which is the project’s compact description of one readable ActiveCampaign collection. It records things like the stream name, the record ID field, and which timestamp field can be used to track changes over time.

**Data flow**: It receives a stream name plus optional details such as the API object name, primary key, cursor field, and whether the stream is considered canonical. It fills in sensible defaults, decides whether the cursor field also counts as an updated-at field, and returns a ready-to-use `StreamSpec` object.

**Call relations**: This helper is used while the file is being loaded to build the `ACTIVECAMPAIGN_STREAMS` list. It hands each stream definition to `StreamSpec.__init__`, so the rest of the connector can work from consistent stream descriptions instead of many scattered settings.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to ActiveCampaign with the right authentication style. ActiveCampaign expects an `Api-Token` header, not the more common bearer-token header.

**Data flow**: It receives a base URL and a resolved credential. If the credential already has a custom transport, it leaves that path alone. Otherwise, it checks for a direct API key, wraps that key into a new `Credential` whose headers contain `Api-Token`, and returns the client built by the parent connector logic. If no key is present, it raises an error before any network request is attempted.

**Call relations**: This method fits into the connector setup phase, before pages are fetched. Its key handoff is creating a `Credential` in the shape ActiveCampaign requires, so the shared REST connector machinery can send authenticated requests normally.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Translates this project’s stream name into the ActiveCampaign API path segment and response envelope key. This is needed because some resources use different spelling or capitalization in the API than in the local stream name.

**Data flow**: It receives a `StreamSpec`. It looks up the stream name in the path mapping table. If a special mapping exists, it returns that API path and response key; otherwise, it returns the stream name for both.

**Call relations**: This is called by `ActiveCampaignConnector.paginate` right before building the request path. It gives `paginate` the exact URL piece to request and the exact key to read from the JSON response.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one ActiveCampaign stream in pages and yields batches of records. It also adds an incremental “changed after this time” filter when the stream and saved cursor support it.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It resolves the stream into an API path and response key, prepares request parameters such as `limit=100`, adds a `filters[..._after]` parameter when possible, then requests offset-based pages. Each page of records is yielded outward. If ActiveCampaign returns 401 or 403, it turns that into a `StreamSkipped` message explaining that the key or permissions are not good enough; other HTTP errors are allowed to bubble up.

**Call relations**: This is the active read loop for the connector. It calls `_resolve_stream_segment` to understand which endpoint to use, relies on the inherited REST paging behavior to walk through pages, and creates `StreamSkipped` when ActiveCampaign refuses a specific stream so the wider sync can report the problem clearly.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Paid advertising
Facebook Ads and Google Ads readers sync advertiser accounts, campaign structures, creatives, and performance metrics from major ad platforms.

### `extensions/sources/ufo_ext_sources/providers/facebook_ads.py`

`io_transport` · `during source sync`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook API addresses to call, how to walk through paged results, or how to turn Facebook’s account-based ad data into normal streams the rest of the sync system can understand.

The file defines the Facebook Ads streams first: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is a named kind of record the system can sync. Some streams use a cursor, which is a saved “last seen” value, so later runs only fetch newer or changed data.

The main class, `FacebookAdsConnector`, reads from Meta’s Graph API. It first asks Facebook for the user’s ad accounts. Then, for account-owned data such as campaigns or ads, it loops through each ad account and asks for that account’s records. This is like checking every folder in a filing cabinet instead of only opening the cabinet door.

Facebook sends large results in pages, so `_paged` follows Facebook’s `paging.next` link until there are no more pages. For ad insights, the connector requests daily per-ad performance metrics. On a first run it looks back 90 days; on later runs it starts from the saved cursor date. The connector only reads data. It does not create or update anything in Facebook Ads.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Facebook API collection page by page. Someone uses it when they want all results from an endpoint, not just the first batch Facebook returns.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It calls the raw GET request method, reads the JSON response, pulls the list under the `data` field, and yields that list when it has records. If Facebook includes a `paging.next` URL, it follows that next URL and repeats; when there is no next page, it stops.

**Call relations**: This is the shared paging engine for the connector. `_accounts`, `_account_children`, and `_insights` all call it so they do not each have to reimplement Facebook’s page-following behavior. It uses `records_at` to safely pick the records out of the response.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches the Facebook ad accounts available to the current credential. The rest of the connector depends on this because campaigns, ads, and insights are requested separately for each ad account.

**Data flow**: It starts with a fixed list of account fields to request, such as account ID, name, currency, time zone, and creation time. It asks `_paged` for `/me/adaccounts`, gathers every returned page into one list, and returns that full list of account dictionaries.

**Call relations**: This is the connector’s starting point for account-scoped data. `paginate` calls it directly for the `ad_accounts` stream. `_account_children` and `_insights` also call it first so they can loop over each account before asking Facebook for campaigns, ads, or daily metrics.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches records that live under each ad account, namely campaigns, ad sets, and ads. It also filters out older records when a saved cursor is available, so repeat syncs can avoid sending unchanged data.

**Data flow**: It receives the stream being synced and an optional cursor value. Based on the stream name, it chooses the right Facebook fields to request. It fetches all ad accounts, then for each valid account ID it calls `_paged` on that account’s campaigns, ad sets, or ads endpoint. If a cursor is present, it keeps only records whose cursor field is newer than that value. Before yielding records, it adds account context such as the ad account ID and name.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. This function relies on `_accounts` to know which accounts to visit and `_paged` to walk through each Facebook result set. It uses `with_context` so downstream code can still tell which ad account each child record came from.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches daily ad performance data, such as impressions, clicks, spend, reach, and click-through rate. It turns Facebook’s metric rows into stable records by creating an ID for each account/ad/day combination.

**Data flow**: It receives an HTTP client and an optional cursor date. It builds a Facebook insights request for ad-level daily data. If a cursor exists, it asks from that date through today; otherwise it requests the last 90 days. It then fetches each ad account, calls `_paged` for that account’s insights endpoint, and rewrites each returned row by adding a generated `id` and the `ad_account_id`. It yields each non-empty page of rewritten rows.

**Call relations**: `paginate` calls this for the `ads_insights` stream. Like the child-record flow, it first uses `_accounts` to find every account and `_paged` to follow Facebook’s pages. It uses `json.dumps` to format the date range in the shape Facebook expects, and `datetime.now` to make the end of a cursor-based range today.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher the sync framework calls when it wants records for a particular Facebook Ads stream. It decides which specialized reader should do the work.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is `ad_accounts`, it fetches accounts and yields them. If the stream is campaigns, ad sets, or ads, it delegates to `_account_children`. If the stream is ad insights, it delegates to `_insights`. If the stream name is unknown, it raises `StreamSkipped` to clearly say this connector does not implement that stream.

**Call relations**: This function is the public paging entry used by the broader `RestConnector` sync machinery. It sits above `_accounts`, `_account_children`, and `_insights`, routing each stream to the right lower-level reader and passing along the cursor when needed.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly normalizes records before the rest of the system stores them. For campaigns, it makes a few common fields easier and more consistent to read.

**Data flow**: It receives one record and its stream description. If the record is from the `campaigns` stream, it returns a copy that keeps the original fields but also sets a normalized `status` from `effective_status` when available, and a `created_at` field from Facebook’s `created_time`. For all other streams, it returns the record unchanged.

**Call relations**: The wider source framework calls this after records have been fetched. Unlike the pagination functions, it does not call other helpers; it is a final cleanup step, mainly for campaign records, before downstream storage or indexing sees the data.


### `extensions/sources/ufo_ext_sources/providers/googleads.py`

`io_transport` · `during source sync, when Google Ads streams are read`

Google Ads does not offer these records as simple downloadable files. Instead, the connector must ask Google’s API with a special query language called GAQL, which is like SQL for Google Ads data. This file builds those queries, sends them to each accessible advertising customer account, and returns the results in the shape the sync system expects.

The connector has two important requirements. First, normal OAuth access proves who the user is, but Google Ads also requires a developer token, which is a Google-approved key for API access. If that token is missing, the connector skips the stream instead of crashing the whole sync. Second, an advertiser login customer ID can be added when needed, so agencies or manager accounts can reach the right customer tree.

The flow is like visiting every mailbox in an office building. The connector first asks Google which customer accounts are accessible. Then, for each account, it runs the right GAQL query, collects the batches Google returns, and stamps each row with the customer ID so later code knows where it came from. Finally, `flatten` turns Google’s nested response objects into simpler fields such as `id`, `resource_name`, `name`, `date`, and metric values. This makes the records easier to identify, update, and store consistently.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token needed for every API request. If the token is not set, it stops this Google Ads stream cleanly by marking it as skipped, because OAuth alone is not enough for Google Ads.

**Data flow**: It reads the environment variables `UFO_GOOGLE_ADS_DEVELOPER_TOKEN` and `GOOGLE_ADS_DEVELOPER_TOKEN`. If one is present, it returns that token as text. If neither exists, it raises a skip signal with a clear message explaining what is missing.

**Call relations**: When the connector is building its HTTP client, `GoogleAdsConnector._make_client` calls this function before any Google Ads request is sent. The returned token is then placed into the request headers so Google will accept the API call.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to Google Ads and adds the Google Ads-specific headers that normal OAuth setup does not provide. It prepares the client so later requests include the developer token and, when configured, the manager or login customer ID.

**Data flow**: It receives a base URL and an OAuth credential from the wider sync system. It starts with the standard REST client, adds the developer token from `GoogleAdsConnector._developer_token`, optionally reads a login customer ID from the environment, removes dashes from that ID, and stores it in the client headers. It returns the ready-to-use asynchronous HTTP client.

**Call relations**: This is part of the connector setup path inherited from the REST connector framework. It calls `GoogleAdsConnector._developer_token` because every later API call depends on that header being present.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can access. The connector needs this list because most Google Ads data must be queried one customer account at a time.

**Data flow**: It uses the prepared HTTP client to call Google’s accessible-customers endpoint. From the response, it looks for resource names such as `customers/1234567890`, extracts the numeric customer IDs, ignores anything malformed, and returns a list of clean customer ID strings.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this first so it knows which accounts to visit. The returned IDs become the inputs for the per-customer search requests made by `GoogleAdsConnector._search_stream`.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one GAQL query against one Google Ads customer account and collects the rows Google returns. It hides the detail that Google sends search results as batches inside a top-level list.

**Data flow**: It receives an HTTP client, a customer ID, and a GAQL query string. It posts the query to that customer’s `searchStream` endpoint, reads the JSON response if there is content, walks through each returned batch, pulls out valid result rows, and returns them as a list of dictionaries.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this once for each accessible customer account. The rows it returns are then tagged with the customer ID before being yielded to the higher-level pagination flow.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function is the connector’s account-by-account runner. Given one query, it applies that query to every accessible customer account and yields pages of results as they are found.

**Data flow**: It receives an HTTP client and a GAQL query. It first gets the customer IDs from `GoogleAdsConnector._customer_ids`, then calls `GoogleAdsConnector._search_stream` for each ID. When rows come back, it adds `customer_id` to every row and yields that group of rows as a page.

**Call relations**: `GoogleAdsConnector.paginate` uses this helper after it has chosen the right query for a stream, such as campaigns or metrics. This helper hands off the actual API request work to `_search_stream` and supplies the stream pages that `paginate` passes onward.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function decides what Google Ads query to run for each supported stream and yields the resulting pages of records. It is the main read path for customers, campaigns, ad groups, ads, campaign metrics, and customer-client relationships.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that says where a previous sync left off. Based on the stream name, it builds the matching GAQL `SELECT` query. For campaign metrics, it uses the cursor date if available, otherwise it asks for roughly the last 90 days. It then sends the query through `GoogleAdsConnector._query_each_customer` and yields each returned page. If the stream is unknown, or if Google refuses access with an authorization-style error, it raises a skip signal instead of producing records.

**Call relations**: The sync framework calls this when it wants records for a Google Ads stream. `paginate` is the coordinator: it chooses the query, delegates repeated per-customer work to `GoogleAdsConnector._query_each_customer`, and turns refusal errors into a controlled `StreamSkipped` outcome.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function reshapes Google Ads records into simpler records with the key fields the sync system needs. Google’s responses are nested, so this step pulls important values up to the top level.

**Data flow**: It receives one raw record and the stream it belongs to. For customers, it extracts a stable customer `id` and display `name`. For campaigns, it extracts fields such as `resource_name`, `name`, `status`, and `created_at`. For campaign metrics, it builds a unique metric row ID from customer, campaign, and date, then pulls out date and performance numbers such as impressions, clicks, and cost. Streams without special shaping are returned unchanged.

**Call relations**: After `GoogleAdsConnector.paginate` has supplied raw records, the wider source framework can call `flatten` before storing or comparing them. It uses `dict_or_empty` to safely treat missing nested objects as empty dictionaries, which keeps partial Google responses from causing avoidable errors.

*Call graph*: 1 external calls (dict_or_empty).


### Social business insights
Instagram business readers collect connected social accounts, published media, stories, and engagement insight metrics through Meta APIs.

### `extensions/sources/ufo_ext_sources/providers/instagram.py`

`io_transport` · `source sync run`

Instagram business accounts are reached through Facebook Pages, so this connector starts at the user’s Pages and fans out from there. Without this file, the system would not know the path from a Facebook grant to the Instagram accounts behind it, or how to collect posts, stories, and analytics from those accounts.

The file defines several streams, which are named feeds of records such as pages, media, stories, and insights. The main class, `InstagramConnector`, is a read-only connector. It does not publish media or change Instagram data. It only asks the Facebook Graph API for data and yields batches of records back to the sync runner.

A key helper, `_paged`, follows Facebook’s pagination style: each response contains a `data` list and may include a `paging.next` link for the next page. This is like reading a book where the bottom of each page tells you where to find the next one.

The connector first gathers Pages, then extracts linked Instagram business accounts. From each account it can read media, stories, and daily user insights. For per-media and per-story insight reads, some objects may reject the request because the account lacks permission or the object cannot provide that metric. Those individual failures are skipped so one bad post does not stop the whole sync. If the broader account-level access is refused, the stream is marked as skipped rather than treated as a system crash.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Facebook Graph API collection that may span many pages of results. It hides the repeated work of following `paging.next` links and gives callers simple batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It requests that path, reads the response body, pulls the list found under `data`, and yields that list when it is not empty. If the response includes a next-page URL, it keeps going with that URL until there are no more pages.

**Call relations**: This is the low-level page-walker used by `_pages` to read `/me/accounts` and by `_account_collection` to read media or stories for each Instagram account. It relies on `records_at` to safely extract the `data` list from the API response.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the grant, including any linked Instagram business account information. This is the starting point because Instagram business data is discovered through Pages.

**Data flow**: It builds a Graph API field list asking for Page identity and linked Instagram account details. It sends that request through `_paged`, collects all returned Page records into one list, and returns that list to the caller.

**Call relations**: Both `_instagram_accounts` and `paginate` call this when they need the Page stream or need to discover Instagram accounts. It delegates the repeated pagination work to `_paged` so it can focus on which fields to ask Facebook for.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the Instagram business accounts linked to the user’s Facebook Pages. It also remembers which Page each Instagram account came from, giving later records useful context.

**Data flow**: It calls `_pages` to get Page records. For each Page, it looks for an `instagram_business_account` object with an id. It builds a deduplicated dictionary keyed by Instagram account id, adds the Page id and Page name to each account, and returns the accounts as a list.

**Call relations**: This function sits between Page discovery and account-based reads. `_account_collection` uses it before reading media or stories, `_user_insights` uses it before reading account metrics, and `paginate` uses it directly for the `instagram_accounts` stream.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a collection, such as media or stories, from every discovered Instagram business account. It can also filter out records that are not newer than the saved cursor, which supports incremental syncing.

**Data flow**: It receives the collection name, the fields to request, and optional cursor information. It first gets Instagram accounts from `_instagram_accounts`. For each valid account id, it calls `_paged` on that account’s collection endpoint. If a cursor is present, it keeps only records whose cursor field is newer. Before yielding each batch, it adds the Instagram account id to every record.

**Call relations**: `paginate` calls this for the `media` and `stories` streams. It combines account discovery from `_instagram_accounts`, page-by-page API reading from `_paged`, and context stamping from `with_context` so downstream storage knows which account each record belongs to.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads insight metrics for individual objects, such as media posts or stories. It is careful to skip objects whose metrics cannot be read, so one unavailable insight does not ruin the whole stream.

**Data flow**: It receives an async stream of object batches, a comma-separated metric list, and the insight stream name. For each object with an id, it requests that object’s `/insights` endpoint. It turns each returned insight into a record with a stable id made from the object id and metric name, plus a link back to the parent object. If Facebook returns certain expected errors for one object, it skips that object and continues.

**Call relations**: `paginate` uses this for `media_insights` and `story_insights`. In those cases, `paginate` first creates an object stream by calling itself for media or stories, then hands that stream to `_object_insights` to enrich each object with its metrics. It uses `records_at` to pull insight rows from the API response.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads daily account-level insight values, such as impressions, reach, and profile views, for each Instagram business account. These are not tied to one post or story; they describe the account over time.

**Data flow**: It starts by getting Instagram accounts from `_instagram_accounts`. For each account id, it requests daily insight metrics. Each metric contains a list of dated values, so the function walks those values, ignores malformed entries, and skips anything at or before the cursor. It creates one output row per account, metric, and end time, then yields non-empty batches.

**Call relations**: `paginate` calls this for the `user_insights` stream. It depends on `_instagram_accounts` for the list of accounts, `records_at` to find returned metric records, and `list_or_empty` to safely treat missing or invalid value lists as empty.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the main dispatcher for all Instagram streams. Given a stream name, it chooses the right reading strategy and yields batches of records to the sync runner.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It checks the stream name and routes the request: Pages go through `_pages`, accounts through `_instagram_accounts`, media and stories through `_account_collection`, object insights through `_object_insights`, and account insights through `_user_insights`. It yields each produced batch. If the stream is unknown, or if Facebook refuses access with certain permission-related errors, it raises `StreamSkipped` so the run records a skip instead of a hard failure.

**Call relations**: This is the connector method the wider source-sync framework calls when it wants records for a specific stream. It coordinates all helper functions in this file and also contains the top-level error policy: individual object insight refusals are handled inside `_object_insights`, while broader 401 or 403 refusals are converted into `StreamSkipped` here.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Email audiences
Klaviyo and Mailchimp readers turn email marketing audiences, profiles, campaigns, reports, events, and activity into consistent syncable records.

### `extensions/sources/ufo_ext_sources/providers/klaviyo.py`

`io_transport` · `sync run`

Klaviyo is a marketing platform, and its API returns data in a nested JSON:API shape, where each item has an id, attributes, relationships, and paging links. This file is the connector that knows Klaviyo’s rules: how to authenticate, which resources can be synced, how to ask only for newer records, how to follow Klaviyo’s next-page links, and how to reshape each record into a simpler form.

The connector defines a list of streams, which are the separate kinds of Klaviyo data the system can read, like profiles, events, forms, and campaigns. Some streams are incremental, meaning the sync can resume from a saved time marker instead of rereading everything. Different Klaviyo resources use different timestamp field names, so the connector chooses the right one.

When a sync runs, it builds an HTTP client with Klaviyo’s required headers, asks for the first page, then keeps following links.next until there are no more pages. For events, it also reads included metric records so an event can carry the human-readable metric name. If Klaviyo refuses access because the API key lacks permission, the connector marks that stream as skipped instead of crashing the whole run. Finally, flatten turns nested Klaviyo records into straightforward key-value records, like unpacking a box so the contents are easy to label and find.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a StreamSpec, which is the system’s description of one Klaviyo data stream. It keeps the stream list compact by filling in common defaults such as the primary key and timestamp fields.

**Data flow**: It receives a stream name plus optional details like the Klaviyo API object name, cursor field, and whether the stream is canonical. It combines those values with safe defaults and returns a StreamSpec object that the connector later uses to know what to request and how to track progress.

**Call relations**: This function is used while the file is loaded to build the KLAVIYO_STREAMS list. It hands its settings to StreamSpec.__init__, and the resulting stream definitions are later used by the connector during pagination and flattening.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method prepares the HTTP client so its requests match Klaviyo’s authentication rules. Klaviyo needs a fixed API revision header and, for direct private keys, a special Authorization header.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent RestConnector to create the normal async HTTP client, then adds Klaviyo’s revision header. If the credential contains a bearer-style secret, it rewrites it into Klaviyo’s private-key format and returns the ready-to-use client.

**Call relations**: The broader connector setup calls this when it needs a client for Klaviyo. It builds on the base connector’s client creation, then adds the Klaviyo-specific headers before any stream requests are made.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper converts Klaviyo’s full next-page URL into the path and query string that the already configured client can request. It is needed because Klaviyo gives an absolute URL, while the client is already bound to the base Klaviyo address.

**Data flow**: It receives a possible next-page link. If the link is missing or has no path, it returns nothing. Otherwise, it parses the URL, keeps only the path and any query parameters, and returns that shorter request target.

**Call relations**: paginate calls this after each page to decide where to go next. Internally it uses urllib.parse.urlparse to split the URL into pieces, then hands the cleaned path back to paginate for the next request.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This helper chooses the correct timestamp field for incremental syncing of a stream. Klaviyo does not use the same field name everywhere, so this keeps that difference in one place.

**Data flow**: It receives a StreamSpec. It checks the stream name and returns datetime for events, updated_at for streams such as campaigns, forms, and images, and updated for the default case.

**Call relations**: This is used by _initial_query when building filters and sort order for the first API request. It helps paginate ask Klaviyo for records in the right time order.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This method builds the query parameters for the first page of a Klaviyo stream request. It sets page size, optional incremental filters, sorting, and a few stream-specific extras.

**Data flow**: It receives a stream definition and an optional saved cursor value. It starts with the page size, then, if the stream supports a cursor, adds sorting and possibly a greater-or-equal filter so only records at or after the cursor are returned. For profiles it asks for subscription details, and for events it asks Klaviyo to include metric information. It returns the finished parameter dictionary.

**Call relations**: paginate calls this before making the first request for a stream. Later pages do not use it, because Klaviyo’s links.next already contains the correct paging information.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This helper safely extracts the id of a related Klaviyo object, such as the profile or metric attached to an event. It avoids errors when Klaviyo omits a relationship or leaves it empty.

**Data flow**: It receives a relationships value and the relationship name to look for. It checks each expected layer, relationships to the named relationship to data to id, only continuing when the shape is a dictionary. If it finds an id, it returns it as text; otherwise it returns nothing.

**Call relations**: flatten calls this when it wants useful relationship ids on the top-level output record. This lets flattened events and segments include simple fields like profile_id, metric_id, or parent_list_id.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method turns one nested Klaviyo API record into a simpler flat record for storage and recall. It also adds a few important fields that are buried in relationships or sub-objects.

**Data flow**: It receives the original Klaviyo record and the stream it belongs to. It starts a new dictionary with the record id and resource type, copies attributes to the top level, removes list and segment profile counts so membership count changes do not make those records look changed, and then adds stream-specific helpful fields. For example, it lifts email consent from profiles, sender and subject details from campaigns, profile and metric ids from events, and parent list ids from segments. It returns the flat dictionary.

**Call relations**: The sync framework calls this after records are fetched so they can be compared, stored, and recalled consistently. When it needs relationship ids, it delegates the careful nested lookup to KlaviyoConnector._lift_relationship_id.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads all pages for one Klaviyo stream. It is the main loop that requests data, follows next-page links, enriches event records with metric names, and reports permission problems as skipped streams.

**Data flow**: It receives an async HTTP client, a stream definition, and an optional cursor. It builds the first API path and query, sends a request, reads the returned records, optionally looks at included metric records for events, yields each non-empty batch of records, then follows the links.next value to the next page. If Klaviyo returns 401 or 403, it raises StreamSkipped so the run records that this stream was refused because of missing permission; other HTTP errors continue upward.

**Call relations**: The sync process calls paginate when it is time to fetch records for a stream. paginate uses _initial_query to prepare the first request, the base connector’s _get method to talk to Klaviyo, and _next_path to turn Klaviyo’s next links into follow-up requests. When access is refused, it hands off to StreamSkipped so the larger run can continue cleanly.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/providers/mailchimp.py`

`io_transport` · `during Mailchimp source sync`

Mailchimp stores related data in several places. Some things, like campaigns or lists, can be fetched directly. Other things are hidden under a parent, such as members inside each list, interests inside each list category, or unsubscribe data inside each report. This file is the map and guide for walking through all of those places safely.

It defines the available Mailchimp streams, including each stream’s name, main identifier, and date field used for incremental syncs. An incremental sync means “only ask for records newer than the last saved point,” so the connector can avoid re-reading everything every time.

The main class, `MailchimpConnector`, extends the project’s shared REST connector. A REST connector is code that talks to a web API using normal HTTP requests. Its central job is pagination: Mailchimp returns records in batches, so this connector keeps requesting pages until there are no more. For nested data, it first gathers parent IDs, then walks into each child endpoint. For email activity, it also reshapes Mailchimp’s nested activity list into one row per action, because downstream systems usually expect one event per record.

If Mailchimp rejects access with a permission or authentication error, the connector marks that stream as skipped instead of pretending the data is empty.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: Creates a `StreamSpec`, which is a small description of one Mailchimp stream: its name, source object, primary key, and date fields. This keeps the long stream list readable and consistent.

**Data flow**: It receives stream settings such as the stream name, primary key, and cursor field. It fills in sensible defaults when optional values are missing, then returns a `StreamSpec` object that the connector later uses to know how to read that stream.

**Call relations**: This helper is used while the file is loaded to build the `MAILCHIMP_STREAMS` list. It hands each finished stream description to `StreamSpec.__init__`, which stores the metadata for the rest of the connector.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adjusts individual Mailchimp records before they leave the connector. For member records, it creates a more standard `created_at` value from Mailchimp’s signup or opt-in timestamp.

**Data flow**: It receives one record and the stream it came from. If the stream is `list_members` or `segment_members`, it copies the record and adds `created_at`; otherwise it returns the record unchanged.

**Call relations**: This method is part of the connector interface used by the broader sync system when records are being normalized. It does not call other local helpers; it is a final cleanup step after pagination has produced records.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field where Mailchimp places the actual records for a stream. For example, the `list_members` stream is wrapped under a `members` field.

**Data flow**: It receives a stream description. It looks up the stream name in the file’s data-field map and returns the matching wrapper field, falling back to the stream name if there is no special mapping.

**Call relations**: `_paginate_top_level`, `_paginate_per_list`, and `_paginate_per_report` call this before fetching pages, so they know which part of Mailchimp’s response contains the useful rows.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the date-filter query parameter for incremental syncs. This is how the connector asks Mailchimp for only records changed or created since a saved timestamp.

**Data flow**: It receives a stream description and an optional cursor value, which is the last saved sync position. If both the cursor and a supported cursor field exist, it returns a small dictionary like `{since_last_changed: cursor}`; otherwise it returns an empty dictionary.

**Call relations**: The pagination helpers for top-level streams, per-list streams, per-report streams, and segment members call this when preparing API requests. It lets each of those flows reuse the same incremental-sync rule.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct reading strategy for each Mailchimp stream. It is the traffic director that decides whether to fetch a simple top-level collection, walk through lists, walk through reports, or perform deeper nested reads.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. Based on the stream name, it delegates to the matching pagination helper and yields pages of records as they arrive. If Mailchimp refuses access with a 401 or 403 response, it turns that into a `StreamSkipped` error with a clear explanation.

**Call relations**: The wider sync engine calls this method when it wants records for a stream. `paginate` then calls `_paginate_top_level`, `_paginate_per_list`, `_paginate_interests`, `_paginate_segment_members`, `_paginate_per_report`, or `_paginate_email_activity` depending on what shape that Mailchimp data has.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads streams that live directly at a Mailchimp API endpoint, such as lists, campaigns, automations, and reports. These are the simplest streams because no parent record must be found first.

**Data flow**: It receives the HTTP client, stream description, endpoint path, and optional cursor. It asks `_cursor_params` for any date filter and `_data_field` for the response wrapper, then repeatedly requests offset-based pages and yields each page of records.

**Call relations**: `paginate` calls this for streams listed in the top-level path map. This helper relies on shared pagination behavior from the base REST connector, while local helpers provide the Mailchimp-specific field names and cursor parameters.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a nested Mailchimp collection from a known endpoint path. It is a reusable helper for child resources such as members under a list or unsubscribes under a report.

**Data flow**: It receives the HTTP client, endpoint path, the response field that contains records, and optional base query parameters. It walks through Mailchimp’s offset pages and yields each batch of records.

**Call relations**: This is the common page-walking tool used by `_paginate_per_list`, `_paginate_interests`, `_paginate_segment_members`, `_paginate_per_report`, and `_paginate_email_activity`. Those higher-level methods figure out the right paths; this helper performs the repeated page fetching.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: Extracts IDs from a paged Mailchimp collection. It is used when the connector needs parent IDs before it can fetch child data.

**Data flow**: It receives an endpoint path and the JSON field containing rows. It reads each page, checks each row for an `id`, converts that ID to text, and yields it one at a time.

**Call relations**: `_list_ids` and `_report_ids` call this with their specific paths. It acts like a shared ID scanner for parent collections.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of all Mailchimp audience lists. These IDs are needed before the connector can read list-specific data like members, segments, tags, categories, and interests.

**Data flow**: It receives the HTTP client. It calls `_ids` on the `/3.0/lists` endpoint and yields each list ID that comes back.

**Call relations**: `_paginate_per_list`, `_paginate_interests`, and `_paginate_segment_members` call this at the start of their work. It supplies the parent list IDs that those methods use to build child endpoint paths.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of all Mailchimp campaign reports. Report IDs are needed before the connector can read report-specific data like unsubscribes and email activity.

**Data flow**: It receives the HTTP client. It calls `_ids` on the `/3.0/reports` endpoint and yields each report ID it finds.

**Call relations**: `_paginate_per_report` and `_paginate_email_activity` call this before walking report child endpoints. It supplies the parent report IDs used to build those API paths.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections that exist under each Mailchimp list, such as list members, segments, tags, and interest categories. It also adds the parent list ID to each child row when requested, so the relationship is not lost.

**Data flow**: It receives the HTTP client, stream description, child path name, optional cursor, and the name of the parent field to stamp. It gets the response field and cursor parameters, loops through every list ID, fetches each child collection, adds `list_id` or another parent field to rows when needed, and yields the resulting pages.

**Call relations**: `paginate` calls this for per-list streams. Inside the flow it calls `_data_field`, `_cursor_params`, `_list_ids`, and `_paginate_child`, and it quotes list IDs before placing them into URLs so special characters cannot break the path.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Mailchimp interests, which are nested two levels deep: each list has interest categories, and each category has interests. This method walks that full tree.

**Data flow**: It receives the HTTP client, stream description, and optional cursor, although this specific walk does not apply the cursor. It loops through list IDs, fetches each list’s interest categories, then fetches interests under each category. It stamps each interest row with its `list_id` and `category_id` before yielding the page.

**Call relations**: `paginate` calls this when the requested stream is `interests`. It depends on `_list_ids` to find parent lists and `_paginate_child` to fetch both category pages and interest pages, quoting IDs as it builds nested paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the members inside each segment of each Mailchimp list. This is a deeper walk because the connector must find lists first, then segments, then segment members.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It builds cursor parameters, loops through list IDs, fetches segments for each list, then fetches members for each segment. Each member row is stamped with both `list_id` and `segment_id` before being yielded.

**Call relations**: `paginate` calls this for the `segment_members` stream. It calls `_cursor_params`, `_list_ids`, and `_paginate_child`, and it quotes list and segment IDs before using them in Mailchimp URLs.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections that live under each Mailchimp report, such as unsubscribed recipients. It keeps the connection to the parent campaign/report by stamping the parent ID onto each row when requested.

**Data flow**: It receives the HTTP client, stream description, child path, optional cursor, and parent field name. It finds the right response wrapper and cursor parameters, loops through report IDs, fetches each child collection, adds the campaign/report ID to rows when needed, and yields each page.

**Call relations**: `paginate` calls this for per-report streams such as unsubscribes. It calls `_data_field`, `_cursor_params`, `_report_ids`, and `_paginate_child`, using quoted report IDs to safely build endpoint paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads email activity from each report and converts Mailchimp’s nested activity arrays into separate event rows. This matters because one recipient can have many actions, such as opens or clicks, and each action needs its own stable record.

**Data flow**: It receives the HTTP client and optional cursor. It builds a `since` filter if a cursor exists, loops through report IDs, fetches email-activity pages, copies each recipient’s base fields, then combines those base fields with each activity item. For each action it creates a stable synthetic ID from email ID, action, and timestamp, and yields only non-empty exploded pages.

**Call relations**: `paginate` calls this for the `email_activity` stream. It calls `_report_ids` to find reports and `_paginate_child` to read their email-activity endpoints, quoting report IDs before placing them into URLs.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).

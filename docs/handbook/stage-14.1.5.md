# Marketing, Ads, and Social Source Connectors  `stage-14.1.5`

This stage is a set of behind-the-scenes connectors for marketing and advertising tools. A connector is like a plug adapter: it knows how to talk to one outside service, ask for the right data, and reshape the reply so the rest of the system can use it in the same way.

ActiveCampaign brings in marketing and customer relationship records, carefully moving through pages of results from its API, which is the service’s data doorway. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance numbers. Google Ads does the same for Google customers, campaigns, ad groups, ads, and metrics, flattening complex replies into simple records. Instagram uses Meta’s Graph API to collect business Pages, linked Instagram accounts, posts, stories, and insight statistics.

Klaviyo focuses on lifecycle marketing data. It can continue from the last synced point and turn nested records into searchable rows. Mailchimp reads audiences, subscribers, campaigns, reports, tags, and email activity. Together, these files feed campaign, audience, and engagement data into the wider sync system.

## Files in this stage

### Lifecycle CRM connector
ActiveCampaign provides the stage's CRM and lifecycle marketing source coverage, including record selection and safe API pagination.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `source sync`

ActiveCampaign exposes many kinds of business data: contacts, lists, campaigns, deals, accounts, tags, users, webhooks, and more. This file turns those API resources into named streams that the rest of the system can ask for in a consistent way. Without it, the system would not know which ActiveCampaign endpoints exist, what each response is called, or how to authenticate requests.

The file starts by defining the known streams and their important bookkeeping fields, such as the record ID and the field that shows when a record was last changed. That lets later sync runs avoid re-reading everything when ActiveCampaign supports a server-side date filter. For resources that do not support that filter, the broader sync system can still use row-level cursors to avoid duplicate work.

The main class, ActiveCampaignConnector, is the actual reader. It builds an HTTP client with ActiveCampaign's required Api-Token header, maps each internal stream name to the API path and response envelope key, then walks through pages using limit and offset values. Think of it like reading a long address book 100 entries at a time. If ActiveCampaign refuses a stream because the API key is invalid or lacks permission, the connector reports that stream as skipped instead of treating every refusal as an unexplained crash.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is a small description of one ActiveCampaign data stream. It keeps the long stream list readable by filling in common defaults such as the primary key and date fields.

**Data flow**: It receives a stream name and optional details like the ActiveCampaign object name, primary key, cursor field, and whether the stream is canonical. It combines those inputs with shared defaults, decides whether the cursor field should also count as the updated-at field, and returns a StreamSpec object for the rest of the connector to use.

**Call relations**: This function is used while the file is loaded to build the ACTIVECAMPAIGN_STREAMS list. It hands each completed stream description to StreamSpec so the wider source framework can later ask the connector to read that stream.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to ActiveCampaign. Its special job is to put the API key in the header name ActiveCampaign expects, Api-Token, instead of using a normal bearer-token style header.

**Data flow**: It receives a base URL and a credential. If the credential already has a custom transport, it leaves that path alone and lets the parent connector build the client. Otherwise, it reads the credential's bearer value as the raw ActiveCampaign API key, wraps it into an Api-Token header, and returns an async HTTP client. If no key is present, it raises an error immediately so the sync does not fail later in a confusing way.

**Call relations**: The source framework calls this when setting up a run for an ActiveCampaign account. It relies on the parent RestConnector for the common client-building work, but first reshapes the credential into the form ActiveCampaign requires.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method translates the system's stream name into the exact ActiveCampaign API path segment and response key. It matters because some ActiveCampaign names use camelCase, while the system's stream names use snake_case.

**Data flow**: It receives a StreamSpec. It looks up the stream name in the file's mapping table and returns the matching URL segment and response envelope key. If the stream is not listed, it falls back to using the stream name for both values.

**Call relations**: paginate calls this at the start of each stream read. The returned names tell paginate which endpoint to request and where inside the JSON response to find the records.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one ActiveCampaign stream page by page. It applies an incremental date filter when ActiveCampaign supports one, so later syncs can ask mainly for records changed after the saved cursor.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous run. It converts the stream into an API path, builds request parameters with a page size of 100, adds a date-after filter when available, and then yields each page of records as a list of dictionaries. If ActiveCampaign returns 401 or 403, meaning unauthorized or forbidden, it turns that into a StreamSkipped message; other HTTP errors continue upward unchanged.

**Call relations**: The sync engine calls this when it needs records for a particular stream. paginate first asks _resolve_stream_segment how to address the stream, then delegates the repeated offset-page fetching to the shared REST connector machinery. When access is refused, it hands back a clear StreamSkipped signal so the larger run can understand that this stream could not be read because of credentials or permissions.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Paid advertising connectors
Facebook Ads and Google Ads connectors stream advertising account structures, campaign entities, ads, and performance metrics from major paid media platforms.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `during Facebook Ads source sync`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know which Facebook Ads web addresses to call, how to page through long result lists, or how to turn Facebook’s responses into steady records that can be saved and revisited later.

The file defines the available Facebook Ads streams: ad accounts, campaigns, ad sets, ads, and ad insights. A stream is one kind of data the sync can read. Most of the data is account-scoped, so the connector first asks Facebook for the user’s ad accounts, then visits each account to collect its campaigns, ad sets, ads, or insights. This is like first getting a list of store branches, then going branch by branch to collect inventory.

Facebook returns large lists in pages, with a link to the next page. The connector follows those links until there are no more. For campaigns, ad sets, and ads, it can skip older records using a saved cursor, which is a remembered “last seen” point. For ad insights, it asks for daily ad-level performance, either since the cursor date or for the last 90 days on a fresh sync. Insight rows get a stable made-up ID built from account, campaign, ad set, ad, and date, because Facebook’s insight rows do not naturally provide one in the shape this system needs.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Facebook API collection from start to finish, even when Facebook splits the answer across many pages. It hides the repetitive work of calling the current URL, taking the records out of the response, and following Facebook’s next-page link.

**Data flow**: It starts with an API path and optional query details, such as requested fields or page size. It sends a request, reads the JSON response, pulls out the list under the response’s data field, and yields that list if it is not empty. If Facebook includes a paging.next link, it uses that as the next request target; once there is no next link, it stops.

**Call relations**: The account reader, account-child reader, and insights reader all call this when they need to walk through Facebook results page by page. It relies on the shared records_at helper to safely find the records inside Facebook’s response before handing each page back to its caller.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function gets all ad accounts available to the connected Facebook credential. Other reads depend on it because campaigns, ads, and insights are requested separately for each ad account.

**Data flow**: It asks Facebook for the current user’s ad accounts with a specific set of useful account fields, such as name, currency, timezone, and creation time. It collects every page returned by _paged into one list. The result is a complete list of account dictionaries for the rest of the connector to use.

**Call relations**: paginate calls it directly when the requested stream is ad_accounts. The account-child and insights flows also call it first so they know which account IDs to visit before asking Facebook for campaigns, ad sets, ads, or insight rows.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads account-level objects such as campaigns, ad sets, and ads across all available ad accounts. It also adds account context so each returned record can be traced back to the ad account it came from.

**Data flow**: It receives a stream description and an optional cursor, then chooses the correct Facebook fields for that stream. It gets all ad accounts, skips any account without a usable ID, and requests the matching collection for each account. If a cursor is present, it keeps only records whose update time is newer than that cursor. Before yielding a page, it adds the ad account ID and name to each record.

**Call relations**: paginate sends campaign, ad set, and ad requests here. This function first depends on _accounts to discover where to look, then uses _paged to fetch each account’s pages, and uses with_context to attach the account information that downstream sync code will need.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads daily ad performance rows, such as impressions, clicks, spend, reach, and click-through rate. It turns Facebook’s report-style rows into records with stable IDs so the sync system can recognize the same day’s result again later.

**Data flow**: It builds a Facebook insights request for ad-level daily data. If a cursor exists, it asks from that cursor date through today; otherwise it asks for the last 90 days. It then gets all ad accounts, requests insights for each valid account, and rewrites each row by adding a generated ID and the ad account ID. It yields only non-empty pages of these enriched rows.

**Call relations**: paginate calls this when the requested stream is ads_insights. The function uses _accounts to know which accounts to report on, _paged to follow Facebook’s paginated insight results, json.dumps to format the date range for Facebook, and the current UTC date to set the end of a cursor-based reporting window.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Facebook Ads stream. Given the stream the sync engine wants, it chooses the right internal reader and yields pages of records back to the engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For ad_accounts, it returns the account list. For campaigns, ad sets, and ads, it delegates to the account-child reader. For ads_insights, it delegates to the insights reader. If the stream name is unknown, it raises a StreamSkipped signal to say this connector does not implement that stream.

**Call relations**: The broader RestConnector machinery calls paginate when it is time to fetch records. paginate then directs the work to _accounts, _account_children, or _insights depending on the stream, acting like a switchboard that sends each sync request to the right specialized path.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly reshapes records before they leave the connector. Its special case makes campaign records easier and more consistent for the rest of the system to read.

**Data flow**: It receives one record and its stream description. If the record is from the campaigns stream, it returns a copy that keeps the original data but normalizes a few fields: it sets status from effective_status when available, and sets created_at from Facebook’s created_time. For all other streams, it returns the record unchanged.

**Call relations**: This fits into the connector’s output-cleanup step after records have been fetched. It does not call other project functions here; instead, it gives downstream sync code a tidier campaign shape while leaving other Facebook Ads records as Facebook provided them.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `during Google Ads source sync`

Google Ads data is not fetched as simple web pages. The Google Ads API expects a special query language called GAQL, which is like SQL for Google Ads, and each query must be sent to the right advertiser customer account. This file is the bridge between the project and that API.

The connector first prepares an HTTP client with the usual OAuth credential supplied by the wider system. Google Ads also requires a separate developer token, like an extra badge that proves the app is approved to use the Ads API. If that badge is missing, this connector skips the stream instead of crashing the whole sync.

For each stream, such as campaigns or ads, the file builds the matching GAQL query. Before it can run those queries, it asks Google which customer accounts are accessible. It then runs the query once per customer and adds the customer id to every returned row, so later code knows where each record came from.

Google Ads returns nested objects, such as a campaign object inside a row. The `flatten` step lifts the important fields into simple top-level keys, like `resource_name`, `name`, or a generated metrics id. This makes the records easier for the rest of the system to key, compare, and store. This file only reads from Google Ads; it deliberately does not create or change campaigns.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This finds the Google Ads developer token, which is required in addition to OAuth. If the token is not available, it marks the Google Ads stream as skipped so a missing setup step does not look like a normal data failure.

**Data flow**: It reads environment variables that may contain the developer token. If it finds one, it returns that token as text. If it finds none, it raises a skip signal explaining that Google Ads OAuth alone is not enough.

**Call relations**: When the connector is building its HTTP client, `GoogleAdsConnector._make_client` calls this function to get the extra Google Ads approval token. The returned token is then attached to every API request.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Google Ads. It adds Google Ads-specific request headers that the base connector would not know about.

**Data flow**: It receives a base URL and an OAuth credential from the sync runner. It asks the parent connector to create a normal authenticated client, adds the developer token header, and optionally adds a login customer id from the environment after removing dashes. It returns the configured async HTTP client.

**Call relations**: This is part of the connector setup before any Google Ads stream is read. It calls `GoogleAdsConnector._developer_token` because every Google Ads request needs that token, then hands the prepared client to the later paging and querying steps.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This asks Google Ads which customer accounts the current credential can access. The rest of the connector needs this list because data queries must be run against a specific customer id.

**Data flow**: It sends a request to the Google Ads endpoint that lists accessible customers. From the response, it looks for resource names shaped like `customers/123`, extracts just the numeric id, and returns a list of those ids. Invalid or unexpected entries are ignored.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this before running a GAQL query. It supplies the customer id list that drives the per-account loop.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This runs one GAQL query against one Google Ads customer account and collects the rows Google returns. It hides the API’s batch-style response format from the rest of the connector.

**Data flow**: It receives an HTTP client, a customer id, and a GAQL query string. It posts the query to that customer’s search-stream endpoint, reads the JSON response, walks through each returned batch, and gathers valid result rows into one list. The output is a list of raw Google Ads row dictionaries.

**Call relations**: `GoogleAdsConnector._query_each_customer` calls this once for each accessible customer id. This function does the actual API query, then gives the raw rows back for customer stamping and paging.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This runs the same Google Ads query across every accessible customer account. It is the connector’s way of turning “read campaigns” or “read ads” into repeated per-account API calls.

**Data flow**: It receives an HTTP client and a GAQL query. It first gets the accessible customer ids, then runs the query for each id. When rows come back, it adds `customer_id` to each row and yields that group of rows as a page for the sync system.

**Call relations**: `GoogleAdsConnector.paginate` uses this helper for every supported stream. It calls `GoogleAdsConnector._customer_ids` to know where to query and `GoogleAdsConnector._search_stream` to fetch the rows from each account.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This chooses the right Google Ads query for each stream and yields the results in pages. It is the main read path for customers, campaigns, ad groups, ads, campaign metrics, and customer-client links.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that says where a previous sync left off. Based on the stream name, it builds a GAQL `SELECT` query, then asks `GoogleAdsConnector._query_each_customer` to run it across all accessible customers. For campaign metrics, it uses the cursor date if present, otherwise it defaults to roughly the last 90 days. It yields pages of raw records, or raises a skip signal when the stream is unsupported or Google refuses access with an authorization-style error.

**Call relations**: The sync engine calls this when it needs records for a Google Ads stream. This function is the dispatcher: it decides what to ask Google for, delegates the repeated per-customer work to `GoogleAdsConnector._query_each_customer`, and turns certain permission failures into a clean stream skip.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This turns nested Google Ads rows into simpler records with the key fields at the top level. The rest of the sync system can then identify and store records consistently.

**Data flow**: It receives one raw Google Ads record and the stream it belongs to. For customer, campaign, and campaign-metric records, it safely pulls nested pieces out of objects like `customer`, `campaign`, `segments`, and `metrics`, then adds plain fields such as `id`, `name`, `resource_name`, `date`, `campaign_id`, `impressions`, and `clicks`. Streams without special shaping are returned unchanged.

**Call relations**: After `GoogleAdsConnector.paginate` has produced raw API rows, the source framework uses this function to normalize each row. It relies on `dict_or_empty` so missing or oddly shaped nested objects do not cause simple flattening to fail.

*Call graph*: 1 external calls (dict_or_empty).


### Social business connector
The Instagram connector reads social business accounts, content, and engagement insights through Meta's Graph API.

### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `during Instagram source syncs`

Instagram business data is reached through Facebook Pages, not by asking Instagram directly. This connector starts with the user’s Facebook Pages, finds any linked Instagram business account on each Page, and then walks outward from those accounts to collect media, stories, and analytics insights. Without this file, the system would have no Instagram source: it could not discover accounts, fetch posts or stories, or record metrics such as reach and impressions.

The connector is read-only. It does not publish media or change anything on Instagram. It also does not keep an access token itself; the wider runner supplies authenticated HTTP access through the normal source framework.

Most Facebook Graph API results arrive in pages, like a long shopping receipt split across several screens. The helper `_paged` follows the API’s “next” link until there are no more results. Higher-level helpers reuse that paging behavior: `_pages` gets Facebook Pages, `_instagram_accounts` extracts linked Instagram accounts, and `_account_collection` gets per-account collections such as media or stories.

Some streams are incremental, meaning they skip records older than a saved checkpoint. Media and stories compare timestamps, while user insights compare their end time. If Facebook refuses access to a whole stream because of missing permission or an invalid token, the connector records the stream as skipped instead of crashing the whole sync. If a single media or story object cannot return insights, that object is ignored and the rest continue.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared page-turning helper for Facebook Graph API collections. It asks for one API page, yields the records inside its `data` list, then follows the API’s `paging.next` link until the collection is finished.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It sends a raw GET request, reads the JSON response, extracts the list under `data`, and yields that list when it is not empty. If the response includes a next-page URL, it uses that for the next request; once there is no next link, it stops.

**Call relations**: The connector’s Page and account-collection readers rely on this as their common way to walk Facebook’s paginated results. It uses the source framework’s `records_at` helper to safely pull out the `data` records before handing each batch back to `_pages` or `_account_collection`.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Facebook Pages available to the current grant, including any linked Instagram business account information. It is the connector’s starting point because Instagram business access is discovered through Pages.

**Data flow**: It builds a field list asking Facebook for Page IDs, Page names, and linked Instagram account details. It then asks `_paged` to walk `/me/accounts`, gathers every returned Page into one list, and returns that list.

**Call relations**: This function is called directly when the requested stream is `pages`. It is also the foundation for `_instagram_accounts`, which uses the returned Pages to discover the Instagram accounts that later media, stories, and insights are fetched from.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This extracts the Instagram business accounts linked to the fetched Facebook Pages. It also remembers which Page each Instagram account came from, so later records can keep that context.

**Data flow**: It first gets Pages from `_pages`. For each Page, it looks for an `instagram_business_account` object with an ID. It stores accounts by ID to avoid duplicates, adds the Page ID and Page name beside the Instagram account details, and returns the unique accounts as a list.

**Call relations**: This function is used when syncing the `instagram_accounts` stream. It also feeds `_account_collection` and `_user_insights`, because those later steps need Instagram account IDs before they can ask Facebook for media, stories, or account-level analytics.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a collection that belongs to each Instagram account, such as that account’s media or stories. It can also apply an incremental checkpoint so old records are skipped.

**Data flow**: It receives the collection name, the fields to request, and an optional cursor value. It gets the available Instagram accounts, then for each valid account ID it pages through the requested account endpoint. If a cursor and cursor field are supplied, it keeps only records newer than that cursor. Before yielding a batch, it adds the Instagram account ID to each record so the record can be traced back to its account.

**Call relations**: The main `paginate` method calls this for the `media` and `stories` streams. Internally it depends on `_instagram_accounts` for account discovery, `_paged` for Facebook pagination, and `with_context` from the source framework to attach account context to each returned record.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches analytics insights for individual objects, such as a post or a story. It turns per-object metrics into records that can be stored separately from the original media or story.

**Data flow**: It receives an async stream of object batches, a metric list, and the name of the insight stream being produced. For each object with an ID, it asks Facebook for that object’s `/insights`. Each returned insight is copied into a new record with a generated ID, the parent object ID, and the stream name. If Facebook says a particular object’s insights cannot be read with common non-fatal errors, that object is skipped; other errors are raised.

**Call relations**: The main `paginate` method uses this to produce `media_insights` from the media stream and `story_insights` from the stories stream. It consumes object batches supplied by `paginate` itself and uses the shared `records_at` helper to read the `data` section of each insight response.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads account-level daily analytics, such as impressions, reach, and profile views, for each Instagram business account. These are different from post or story insights because they describe the account as a whole over time.

**Data flow**: It receives an HTTP client and an optional saved cursor. It gets Instagram accounts, asks Facebook for daily insight metrics for each account, then walks through each insight’s `values` list. For every value newer than the cursor, it creates a record containing the metric value, a stable generated ID, the metric name, and the Instagram account ID. It yields batches only when there are new rows.

**Call relations**: The main `paginate` method calls this for the `user_insights` stream. It depends on `_instagram_accounts` to know which accounts to query, and uses source helpers to safely read `data` lists and treat missing or non-list `values` as an empty list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the connector’s dispatcher: given a stream name, it chooses the right reading path and yields batches of records for that stream. It is the main method the source framework calls during a sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor checkpoint. It checks the stream name and routes to the matching helper: Pages, Instagram accounts, media, stories, media insights, story insights, or user insights. It yields each resulting batch. If the stream is unknown, it marks it skipped. If Facebook refuses access with an authorization-style error, it converts that into a skipped stream message rather than a hard sync failure.

**Call relations**: This sits above all the other helpers in the file. The framework calls it whenever it wants records for one Instagram stream, and it then delegates to `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, or `_user_insights` as needed. It also wraps permission failures in `StreamSkipped` so the larger run can continue cleanly.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Email marketing connectors
Klaviyo and Mailchimp connectors handle audience, campaign, subscriber, report, and activity data from email and marketing automation platforms.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `source sync`

Klaviyo stores many kinds of marketing data: profiles, lists, campaigns, flows, events, catalog items, forms, webhooks, and more. This file is the read-only connector that pulls those resources into the larger system. Without it, the system would not know which Klaviyo endpoints exist, how to authenticate, how to ask for the next page of results, or how to interpret Klaviyo’s nested JSON records.

The file starts by declaring a set of streams. A stream is one kind of Klaviyo object to sync, such as “profiles” or “campaigns.” Each stream records practical facts: the API name, its main ID field, and which timestamp can be used as a bookmark for incremental syncing. Incremental syncing means “only fetch records changed since the last run,” like continuing a book from the bookmark instead of starting over.

The `KlaviyoConnector` then adds Klaviyo-specific rules on top of a shared REST connector. It builds an HTTP client with Klaviyo’s required headers, creates the first query for each stream, follows Klaviyo’s `links.next` paging links, and turns permission failures into a clean “stream skipped” result.

Klaviyo records arrive wrapped in a JSON:API shape with `attributes` and `relationships`. The connector flattens the useful parts so later search and recall do not need to dig through nested objects. It also deliberately removes list and segment profile counts, because membership changes should not make those list or segment records look changed.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: Creates a `StreamSpec`, which is the small description the sync system uses for one Klaviyo resource. It keeps the stream declarations short and consistent.

**Data flow**: It receives a stream name and optional details such as the API object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults where details are not supplied, then returns a `StreamSpec` object that the connector can later use during syncing.

**Call relations**: This helper is used while the file is loaded to build `KLAVIYO_STREAMS`, the connector’s catalog of Klaviyo resources. It hands the finished settings into `StreamSpec.__init__`, which stores them in the shared format expected by the rest of the source framework.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Klaviyo and adds the headers Klaviyo requires. In particular, it sets Klaviyo’s pinned API revision and, when available, the private API key authorization header.

**Data flow**: It receives a base URL and a resolved credential. It first asks the parent REST connector to create the normal client, then adds a `revision` header and, if the credential contains a key, an `Authorization` header in Klaviyo’s required format. It returns the prepared asynchronous HTTP client.

**Call relations**: The shared connector machinery calls this when setting up a Klaviyo sync. This method does not fetch data itself; it prepares the transport so later requests made by pagination and other REST helper code are accepted by Klaviyo.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: Converts Klaviyo’s full “next page” URL into the path and query string needed by the already-configured client. This lets pagination continue without rebuilding the whole request from scratch.

**Data flow**: It receives a possible next-page link. If the link is missing or has no path, it returns `None`, meaning there is no next page to fetch. Otherwise it parses the URL, keeps only the path and query portion, and returns that shorter string.

**Call relations**: `paginate` calls this after each page is fetched. Klaviyo gives pagination links as absolute URLs, while the connector’s HTTP client is already bound to the Klaviyo base URL, so this helper acts like trimming an address down from “country, city, street” to just “street and house number” once you are already in the right city.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: Chooses the correct timestamp field to use as the incremental-sync bookmark for a stream. Different Klaviyo resources use different field names for “when this changed.”

**Data flow**: It receives a stream description. If the stream is events, it returns `datetime`; if it is one of the streams that uses `updated_at`, it returns `updated_at`; otherwise it returns `updated`. The result is just the field name to use in API filters and sorting.

**Call relations**: This is part of building the first request for a stream. `_initial_query` relies on this choice so it can ask Klaviyo for records in the right time order and, when a saved cursor exists, only records at or after that cursor.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for the first API request of a stream. It sets the page size, adds sorting, adds an incremental filter when there is a saved cursor, and requests a few extra fields for streams that need them.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It creates a parameter dictionary with Klaviyo’s page size. If the stream supports a cursor, it picks the right timestamp field, sorts by it, and optionally filters for records greater than or equal to the cursor. For profiles it asks for subscription details; for events it asks Klaviyo to include metric data. It returns the completed parameter dictionary.

**Call relations**: `paginate` calls this before fetching the first page. After that first page, Klaviyo’s own `links.next` URL carries the paging details, so these parameters are only needed at the start of the stream.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: Safely extracts the ID from one of Klaviyo’s nested relationship blocks. It is used when a record points to another record, such as an event pointing to its profile or metric.

**Data flow**: It receives a relationships object and the relationship name to look for. It checks each nested level carefully, because Klaviyo may omit empty relationships or return unexpected shapes. If it finds `relationships[key].data.id`, it returns that ID as a string; otherwise it returns `None`.

**Call relations**: `flatten` calls this when it wants to preserve useful links between records without keeping the whole nested relationship object. The helper keeps the flattening code from failing when a relationship is absent.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns a Klaviyo record from its nested API shape into a simpler flat dictionary. This makes important fields easier for the rest of the system to index, compare, and recall.

**Data flow**: It receives one raw Klaviyo record and its stream description. It starts a new dictionary with the record ID and resource type, then copies top-level `attributes` into it. For some streams it adds special useful fields: profile email consent and suppression reason, campaign subject and sender details, event profile and metric IDs, event message reference, and segment parent list ID. For lists and segments it removes `profile_count` so ordinary membership changes do not churn those records. It returns the flattened record.

**Call relations**: This method is part of the connector’s record-shaping step after raw pages have been fetched. When it needs an ID from a nested relationship, it calls `_lift_relationship_id` rather than digging through the nested structure directly.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Klaviyo stream, yielding batches of raw records as it goes. It also enriches event records with metric names when Klaviyo includes those metric objects in the response.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It starts at `/api/<stream object>`, builds the first query with `_initial_query`, then repeatedly requests the current page. From each response it reads the `data` records, optionally matches included metric objects back onto event records, yields non-empty batches, and moves to the next page by converting `links.next` with `_next_path`. If Klaviyo returns a 401 or 403 permission error, it raises `StreamSkipped` so the run records that this stream was unavailable rather than treating the whole sync as a crash.

**Call relations**: This is the main read loop for the connector. The broader REST sync framework calls it when syncing a Klaviyo stream. Inside the loop it delegates first-page parameter construction to `_initial_query`, page-link cleanup to `_next_path`, and permission refusals to the shared `StreamSkipped` mechanism.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `sync run data fetching`

Mailchimp stores marketing data behind a web API, and that API is not shaped like one simple table. Some things are top-level, like lists and campaigns. Other things live underneath a parent, like members inside each list, interests inside each list category, or unsubscribes inside each report. This file is the map and walking plan for all of those cases.

The file defines the Mailchimp streams the product can sync, including each stream’s name, main ID field, and optional cursor field. A cursor is a saved “last seen” time used to ask Mailchimp for only newer records on the next run. The MailchimpConnector then decides which route to take for each stream. For simple resources it walks through pages using Mailchimp’s count and offset query parameters. For nested resources it first fetches parent IDs, then visits each child endpoint in turn, adding the parent ID to each row so the row still makes sense later.

One important special case is email activity. Mailchimp returns a recipient with a list of actions, but this connector turns each action into its own row and invents a stable ID from email, action, and timestamp. If Mailchimp rejects access with a 401 or 403 response, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: This helper creates a StreamSpec, which is the system’s small description card for one Mailchimp data stream. It keeps the stream list readable by filling in common defaults, such as using "id" as the primary key unless told otherwise.

**Data flow**: It receives a stream name plus optional details like the Mailchimp object name, primary key, cursor field, and timestamp fields. It combines those values with defaults and returns a StreamSpec object that the connector later uses to know how to sync that stream.

**Call relations**: This helper is used while the file is loaded to build MAILCHIMP_STREAMS. It hands its settings to StreamSpec.__init__, which creates the actual stream description used by MailchimpConnector.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly reshapes records after they are fetched. For Mailchimp member records, it creates a standard created_at value from Mailchimp’s signup or opt-in timestamps so downstream code has a consistent field to read.

**Data flow**: It receives one Mailchimp record and the stream it came from. If the stream is list_members or segment_members, it copies the record and adds created_at from timestamp_signup, falling back to timestamp_opt; otherwise it returns the record unchanged.

**Call relations**: This method is part of the connector contract inherited from RestConnector. After pagination has produced raw rows, the broader sync system can call this method to normalize rows before storing or processing them.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: This helper answers a simple question: inside Mailchimp’s JSON response, which key contains the list of records for this stream? Mailchimp often wraps rows under names like "members" or "campaigns" instead of returning the rows directly.

**Data flow**: It receives a StreamSpec. It looks up the stream name in the file’s mapping of Mailchimp wrapper keys and returns the matching key, or the stream name itself if no special mapping is needed.

**Call relations**: Top-level, per-list, and per-report paginators call this before asking the shared page reader to fetch rows. It gives those paginators the correct JSON field name to extract.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper turns the system’s saved cursor into the query parameter name Mailchimp expects. That lets a sync ask Mailchimp for records changed since a particular time, when Mailchimp supports that filter.

**Data flow**: It receives a stream and an optional cursor value. If either is missing, or if this stream’s cursor field has no Mailchimp filter name, it returns an empty dictionary; otherwise it returns a one-item dictionary such as {"since_last_changed": cursor}.

**Call relations**: The top-level, per-list, per-report, and segment-member paginators call this when they build API requests. It keeps Mailchimp’s many different "since" parameter names in one place.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Mailchimp streams. Given a stream name, it chooses the right paging strategy: simple top-level pages, children under each list, children under each report, or one of the deeper special walks.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching private paginator, and yields pages of records as they arrive. If Mailchimp refuses access with a 401 or 403 status, it changes that into a StreamSkipped error with a human-readable reason; other errors keep bubbling up.

**Call relations**: The wider source framework calls paginate when it wants rows for a Mailchimp stream. paginate then delegates to _paginate_top_level, _paginate_per_list, _paginate_interests, _paginate_segment_members, _paginate_per_report, or _paginate_email_activity depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Mailchimp resources that live at one direct endpoint, such as lists, campaigns, automations, and reports. It repeatedly requests pages until there are no more full pages to fetch.

**Data flow**: It receives an HTTP client, a stream, an API path, and an optional cursor. It finds the correct JSON record field and cursor query parameters, then asks the base connector’s offset-page reader for batches of up to 500 records, yielding each batch onward.

**Call relations**: paginate calls this for top-level streams. Before it hands work to the base page reader, it uses _data_field and _cursor_params to translate this stream into Mailchimp-specific request details.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared page reader for nested Mailchimp endpoints, such as members inside a list or unsubscribes inside a report. It avoids repeating the same offset-pagination code in every nested paginator.

**Data flow**: It receives an HTTP client, a specific API path, the JSON field that contains rows, and optional base query parameters. It asks the base connector to request pages with Mailchimp’s count and offset style, then yields each page of records.

**Call relations**: The per-list, interest, segment-member, per-report, and email-activity paginators all call this after they have built the right child endpoint path. It is their common doorway into the lower-level HTTP paging behavior.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: This helper fetches only the IDs from a paged Mailchimp collection. It is used when the connector needs parent IDs before it can visit child endpoints.

**Data flow**: It receives an HTTP client, an API path, and the JSON field containing records. It walks through all pages at that path, looks at each row, and yields the row’s id as a string when one is present.

**Call relations**: _list_ids and _report_ids call this to avoid duplicating ID-gathering logic. Those parent-ID streams then feed the nested paginators that need to visit one endpoint per list or per report.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This yields the IDs of all Mailchimp audiences, which Mailchimp calls lists. Those IDs are needed because many useful resources are stored underneath a specific list.

**Data flow**: It receives an HTTP client. It calls _ids on the /3.0/lists endpoint and yields each list ID it finds.

**Call relations**: The per-list, interests, and segment-members paginators call this first. Once they have a list ID, they can build child paths like /lists/{list_id}/members or /lists/{list_id}/segments.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This yields the IDs of Mailchimp campaign reports. Those report IDs are needed to fetch report-specific details such as unsubscribes and email activity.

**Data flow**: It receives an HTTP client. It calls _ids on the /3.0/reports endpoint and yields each report ID as text.

**Call relations**: The per-report and email-activity paginators call this before fetching report children. It supplies the parent IDs they plug into report-specific API paths.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads resources that exist separately under every Mailchimp list, such as members, segments, tags, and interest categories. It also stamps each returned row with the list ID so the row can be traced back to its audience.

**Data flow**: It receives an HTTP client, stream details, the child endpoint name, an optional cursor, and the name of the parent field to add. It gets all list IDs, builds a safe URL path for each list, fetches child pages, adds the parent list_id when requested, and yields those pages.

**Call relations**: paginate calls this for list-based streams. It relies on _list_ids to find parents, _data_field to know where records are in the response, _cursor_params for incremental filters, _paginate_child for page fetching, and quote to safely place IDs in URLs.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Mailchimp interests, which are nested two levels deep: each list has interest categories, and each category has interests. It walks that hierarchy and labels each interest with both its list and category.

**Data flow**: It receives an HTTP client, stream details, and an optional cursor, though this routine does not use the cursor in its requests. It gets each list ID, fetches that list’s interest categories, then fetches interests for each category and adds list_id and category_id to each interest row before yielding the page.

**Call relations**: paginate calls this only for the interests stream. It uses _list_ids to begin the parent walk, _paginate_child to fetch categories and interests, and quote to safely build nested URL paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the members inside each segment of each Mailchimp list. Because segment membership is nested under both a list and a segment, the function adds both IDs to every member row.

**Data flow**: It receives an HTTP client, stream details, and an optional cursor. It turns the cursor into Mailchimp query parameters, gets each list, fetches that list’s segments, then fetches members for each segment; each member row is given list_id and segment_id before the page is yielded.

**Call relations**: paginate calls this for the segment_members stream. It depends on _list_ids for list parents, _paginate_child for both segment and member pages, _cursor_params for incremental member filtering, and quote for safe URL construction.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads resources that live under each Mailchimp report, such as unsubscribed recipients. It stamps each returned row with the campaign/report ID so the child record is not separated from its parent campaign.

**Data flow**: It receives an HTTP client, stream details, a child endpoint path, an optional cursor, and an optional parent field name. It gets report IDs, builds a child path for each report, fetches paged records, adds the campaign_id when requested, and yields each page.

**Call relations**: paginate calls this for report-based child streams. It uses _report_ids to find parent reports, _data_field for the response wrapper key, _cursor_params for time filtering, _paginate_child for HTTP paging, and quote to safely include report IDs in URLs.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads detailed email activity from each Mailchimp report and turns Mailchimp’s nested activity lists into one row per action. That makes opens, clicks, bounces, and similar events easier to store and compare across syncs.

**Data flow**: It receives an HTTP client and an optional cursor. It builds a since filter when a cursor is present, fetches email-activity pages for each report, copies each recipient’s shared fields, then merges in each individual activity item. For each activity row it adds campaign_id and, if needed, creates a stable id from email_id, action, and timestamp; it yields only pages that contain exploded activity rows.

**Call relations**: paginate calls this for the email_activity stream. It uses _report_ids to find reports, _paginate_child to fetch each report’s email activity, and quote to build safe report paths before doing its special row-exploding step.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).

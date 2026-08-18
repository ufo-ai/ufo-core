# Marketing, advertising, and social source connectors  `stage-12.1.7`

This stage is part of the system’s data-gathering work. Its job is to connect to outside marketing and advertising services, ask them for business data through their APIs, and reshape the replies into standard records the rest of the project can store, search, and reuse. An API is simply a service’s official doorway for software to request data.

Each file is like an adapter for a different platform. ActiveCampaign reads customer and marketing automation objects. Facebook Ads reads Meta ad accounts, campaigns, ad sets, ads, and daily performance results. Google Ads does the same for Google customers, campaigns, ad groups, ads, and performance numbers. Instagram uses Meta’s Graph API to collect business pages, connected Instagram accounts, posts, stories, and analytics. Klaviyo pulls many marketing resources, including profiles, campaigns, events, lists, segments, flows, and catalog items. Mailchimp reads audiences, subscribers, campaigns, reports, and email activity. Together, these connectors hide each platform’s different paging and response style behind one consistent stream format.

## Files in this stage

### Marketing CRM connector
ActiveCampaign provides the first marketing automation source, defining CRM and campaign objects and their API pagination.

### `extensions/sources/ufo_ext_sources/active_campaign.py`

`io_transport` · `during an ActiveCampaign sync, when streams are discovered and API pages are fetched`

ActiveCampaign exposes many kinds of records: contacts, lists, campaigns, deals, accounts, tags, webhooks, and more. This file is the connector that turns all of those remote records into streams the rest of the system can sync. Without it, the platform would not know which ActiveCampaign endpoints exist, how to authenticate to them, or how to walk through their paged responses.

The file first defines a shared page size and a map from the system’s stream names to ActiveCampaign’s actual URL names and response keys. This matters because some names differ slightly, such as `campaign_messages` in this system becoming `campaignMessages` in ActiveCampaign. It also records which streams support a server-side “only give me records changed after this time” filter.

The helper `_stream` builds each stream description, including its main ID field and the date field used to notice updates. `ACTIVECAMPAIGN_STREAMS` is the full menu of available data.

`ActiveCampaignConnector` then provides the live behavior. It creates an HTTP client using ActiveCampaign’s `Api-Token` header, resolves each stream to the correct API path, and fetches pages by offset until the API runs out of records. If ActiveCampaign refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 98–114)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='udate', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard stream description for one kind of ActiveCampaign data, such as contacts or campaigns. It keeps the long stream list readable and makes sure each stream is described in a consistent way.

**Data flow**: It receives a stream name plus optional details like the ActiveCampaign object name, the primary key, and the date field used for updates. It fills in sensible defaults, decides whether the update field should also count as the official updated-at field, and returns a `StreamSpec`, which is the system’s description of one syncable collection.

**Call relations**: This is used while the file is loaded to build `ACTIVECAMPAIGN_STREAMS`, the connector’s catalog of available ActiveCampaign data. Its main handoff is to `StreamSpec`, which stores the stream description for the broader sync system.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._make_client`  (lines 176–181)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to ActiveCampaign. Its special job is to put the API key into the header name ActiveCampaign expects: `Api-Token`, not the more common bearer-token format.

**Data flow**: It receives the tenant-specific base URL and a credential. If the credential already contains a custom transport, it leaves that path alone and asks the parent connector to build the client. Otherwise, it checks for a direct API key, wraps that key in a new credential with an `Api-Token` header, and returns an HTTP client ready to make authenticated requests. If no key is present, it raises an error so the sync fails clearly.

**Call relations**: The broader REST connector flow calls this when it needs a network client for a sync run. This method adapts the generic credential shape into ActiveCampaign’s particular authentication style, then hands client creation back to the shared REST connector machinery.

*Call graph*: 1 external calls (__init__).


##### `ActiveCampaignConnector._resolve_stream_segment`  (lines 184–185)

```
def _resolve_stream_segment(stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This translates the system’s stream name into the exact ActiveCampaign URL segment and response-envelope key. It is needed because some stream names use underscores locally but camelCase in the ActiveCampaign API.

**Data flow**: It receives a `StreamSpec`, looks up the stream’s name in the predefined mapping, and returns the matching API path part and JSON key. If the stream is not in the mapping, it falls back to using the stream name for both.

**Call relations**: The pagination method calls this before making API requests. It gives `paginate` the two names it needs: where to request the data and where to find the list of records inside ActiveCampaign’s JSON response.

*Call graph*: called by 1 (paginate).


##### `ActiveCampaignConnector.paginate`  (lines 187–211)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches one ActiveCampaign stream page by page. It adds optional “changed after this cursor” filtering when ActiveCampaign supports it, and turns permission failures into a clean skipped-stream signal.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It resolves the API path, builds query parameters with a fixed page limit, and, when possible, adds an incremental filter so ActiveCampaign sends only newer records. It then asks the shared offset-pagination helper to fetch each page and yields each page of records onward. If ActiveCampaign returns 401 or 403, it raises `StreamSkipped`; other HTTP errors are re-raised.

**Call relations**: This is the main read path used by the sync engine when it wants records from a particular ActiveCampaign stream. It first calls `_resolve_stream_segment` to translate stream names, then relies on the inherited REST pagination helper to do the repeated HTTP requests, and finally yields the pages back to the caller for normal syncing.

*Call graph*: calls 2 internal fn (__init__, _resolve_stream_segment).


### Paid advertising connectors
Facebook Ads and Google Ads expose paid media account structures, campaign entities, and performance reporting streams.

### `extensions/sources/ufo_ext_sources/facebook_ads.py`

`io_transport` · `during source sync`

This connector is the bridge between UFO and Facebook Ads. Without it, the system would not know where to ask Facebook for advertising data, how to follow Facebook's paginated responses, or how to split the data into useful streams such as campaigns and ad insights.

The file defines the available Facebook Ads streams first. A stream is a named kind of data the sync system can read, like “campaigns” or “ads_insights,” along with basic facts such as its unique ID field and which date field can be used as a cursor. A cursor is a saved “last seen” marker that lets later syncs fetch only newer data.

The `FacebookAdsConnector` then does the actual reading. It talks to the Graph API, starts by finding the Facebook ad accounts available to the authenticated user, and then asks each account for its campaigns, ad sets, ads, or daily insights. Facebook returns results in pages, like a long report split across many screens, so the connector keeps following Facebook's `paging.next` link until there are no more pages.

For campaigns, ad sets, and ads, it filters out records older than the saved cursor. For insights, it asks Facebook for one row per ad per day, either since the cursor date or for the last 90 days on a first run. It also creates a stable ID for each insight row so the system can recognize the same daily metric row later.

#### Function details

##### `FacebookAdsConnector._paged`  (lines 74–87)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Facebook API collection page by page. It hides the repeated work of making a request, pulling the list of records out of the response, and following Facebook's “next page” link.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It requests that path, reads the JSON response, extracts the records under the `data` field, yields them as a page, then moves to the `paging.next` URL if Facebook provides one. The first request uses the given parameters; later requests use Facebook's full next-page URL instead.

**Call relations**: The account, child-object, and insights readers all rely on this helper whenever they need to walk through Facebook's paginated results. It uses the shared `records_at` helper to safely pull the list of records from the response before handing each page back to its caller.

*Call graph*: called by 3 (_account_children, _accounts, _insights); 1 external calls (records_at).


##### `FacebookAdsConnector._accounts`  (lines 89–94)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook ad accounts available to the current credential. Other streams need this because most Facebook Ads data is stored under a specific ad account.

**Data flow**: It starts with no account list, asks `/me/adaccounts` for selected account fields such as ID, name, currency, time zone, and business, and collects every page returned by `_paged`. It returns one combined list of account records.

**Call relations**: This is the first step for most reads. `paginate` calls it directly for the `ad_accounts` stream, while `_account_children` and `_insights` call it first so they can loop through each account and fetch account-specific data.

*Call graph*: calls 1 internal fn (_paged); called by 3 (_account_children, _insights, paginate).


##### `FacebookAdsConnector._account_children`  (lines 96–121)

```
async def _account_children(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches account-owned objects such as campaigns, ad sets, and ads across every accessible ad account. It also adds account context so each returned record says which ad account it came from.

**Data flow**: It receives the HTTP client, the requested stream, and an optional cursor. It chooses the correct Facebook fields for that stream, fetches all ad accounts, then requests the matching collection under each account. If a cursor is present, it keeps only records whose update time is newer than that cursor. Before yielding a page, it attaches the ad account ID and name to each record.

**Call relations**: `paginate` calls this when the requested stream is `campaigns`, `ad_sets`, or `ads`. This function depends on `_accounts` to know which accounts to visit, `_paged` to read each account collection, and `with_context` to add account information to the records before sending them back to the sync pipeline.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `FacebookAdsConnector._insights`  (lines 123–166)

```
async def _insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches daily advertising performance metrics, such as impressions, clicks, and spend, broken down by ad. It turns Facebook's metric rows into stable records the sync system can store and revisit.

**Data flow**: It receives the HTTP client and an optional cursor. It builds a Facebook insights request for ad-level daily rows. If there is a cursor, it asks for data from that cursor date through today; otherwise it asks for the last 90 days. For each ad account, it reads insight pages, creates a stable ID from the account, campaign, ad set, ad, and date, adds the account ID, and yields the finished rows.

**Call relations**: `paginate` calls this for the `ads_insights` stream. This function first uses `_accounts` to find every account, then uses `_paged` to read each account's `/insights` endpoint. It uses `json.dumps` to format the Facebook time range and the current UTC date to set the end of cursor-based requests.

*Call graph*: calls 2 internal fn (_accounts, _paged); called by 1 (paginate); 2 external calls (now, dumps).


##### `FacebookAdsConnector.paginate`  (lines 168–184)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right reader for the requested Facebook Ads stream. It is the connector's main entry point for producing pages of records during a sync.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is ad accounts, it fetches and yields the account list. If the stream is campaigns, ad sets, or ads, it delegates to `_account_children`. If the stream is ad insights, it delegates to `_insights`. If the stream name is unknown, it raises a skip signal instead of pretending it can read it.

**Call relations**: The broader source sync system calls this when it needs records from one stream. This function acts like a traffic director: it sends each known stream to the helper built for that kind of Facebook data, and it stops unsupported streams with `StreamSkipped`.

*Call graph*: calls 4 internal fn (__init__, _account_children, _accounts, _insights).


##### `FacebookAdsConnector.flatten`  (lines 186–194)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes individual records after they are fetched, mainly to make campaign records easier for the rest of the system to use. For most streams, it leaves records unchanged.

**Data flow**: It receives one record and the stream it belongs to. For campaign records, it copies the record while setting a simpler `status` field from Facebook's effective status when available and a `created_at` field from `created_time`. For any other stream, it returns the original record as-is.

**Call relations**: This is used after pages have been read, when records are being shaped into the system's expected format. It does not call other project helpers; it simply applies a small stream-specific cleanup step for campaigns.


### `extensions/sources/ufo_ext_sources/googleads.py`

`io_transport` · `source sync`

Google Ads is not a simple “give me everything” service. To read it, this connector first needs an OAuth credential from UFO’s auth system, plus a separate Google Ads developer token from the environment. Without that developer token, Google Ads will refuse the request, so this file skips the stream instead of crashing the whole sync.

The connector works like a tour guide for Google Ads accounts. First it asks Google which customer accounts the credential can access. Then, for each customer account, it sends a Google Ads Query Language query, which is similar to SQL: a “SELECT these fields FROM this resource” request. Google returns batches of nested data, and the connector tags each row with the customer id it came from.

Different streams use different queries: customers, campaigns, ad groups, ads, customer-client relationships, and campaign metrics. Metrics are special because they are time-based. If the sync already has a cursor, it reads from that date; otherwise it starts from the last 90 days.

Finally, the connector flattens some nested Google Ads objects into simpler fields, such as ids, names, dates, and metric counts. This matters because the wider sync system needs stable keys and plain fields, not deeply nested API-shaped objects.

#### Function details

##### `GoogleAdsConnector._developer_token`  (lines 71–80)

```
def _developer_token(self) -> str
```

**Purpose**: This function finds the Google Ads developer token needed for every Google Ads API request. OAuth proves who the advertiser is, but the developer token proves the calling application is approved to use Google Ads.

**Data flow**: It reads two possible environment variables, preferring the UFO-specific one. If it finds a token, it returns that token as text. If neither variable is set, it turns the current stream into a skipped stream with a clear message, so the sync can continue without pretending Google Ads is available.

**Call relations**: When the connector builds its HTTP client, it calls this function before making Google Ads requests. If the token is missing, this function raises the skip signal that the source framework understands.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_make_client); 1 external calls (getenv).


##### `GoogleAdsConnector._make_client`  (lines 82–90)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function prepares the HTTP client that will talk to Google Ads. It adds the special headers Google Ads requires on top of the normal authenticated client.

**Data flow**: It receives a base URL and an OAuth credential. It first asks the parent connector to create the usual authenticated web client, then adds the developer token header. If a login customer id is configured in the environment, it also adds that header after removing dashes. The result is a ready-to-use asynchronous HTTP client.

**Call relations**: This is part of the connector setup before any stream is read. It relies on _developer_token to supply the required Google Ads token, then hands the prepared client to later paging and query functions.

*Call graph*: calls 1 internal fn (_developer_token); 1 external calls (getenv).


##### `GoogleAdsConnector._customer_ids`  (lines 92–99)

```
async def _customer_ids(self, client: httpx.AsyncClient) -> list[str]
```

**Purpose**: This function asks Google Ads which customer accounts the current credential can see. The rest of the connector needs this list because most Google Ads queries must be run separately for each customer account.

**Data flow**: It uses the HTTP client to call Google’s accessible-customers endpoint. From the response, it looks for resource names like “customers/1234567890”, keeps only valid customer entries, strips off the prefix, and returns a plain list of customer id strings.

**Call relations**: The per-customer query loop calls this first. Its output decides which accounts _search_stream will query one by one.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._search_stream`  (lines 101–120)

```
async def _search_stream(self, client: httpx.AsyncClient, customer_id: str, query: str) -> list[dict[str, Any]]
```

**Purpose**: This function runs one Google Ads query against one customer account and collects the rows from Google’s streamed response format. It hides the awkward batch layout so later code can work with a simple list of rows.

**Data flow**: It receives an HTTP client, a customer id, and a query string. It posts the query to that customer’s Google Ads search stream endpoint. Google may return an array of batches, so the function walks through those batches, pulls out each result row that is shaped like a dictionary, and returns all rows as one list.

**Call relations**: The per-customer loop calls this after it knows a customer id. It gives the raw rows back to _query_each_customer, which adds the customer id before yielding them onward.

*Call graph*: called by 1 (_query_each_customer).


##### `GoogleAdsConnector._query_each_customer`  (lines 122–130)

```
async def _query_each_customer(self, client: httpx.AsyncClient, query: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function runs the same Google Ads query across every accessible customer account. It is the bridge between “one query” and “all accounts this advertiser can access.”

**Data flow**: It receives an HTTP client and a query. It first gets the accessible customer ids, then runs the query for each customer. For every non-empty set of rows, it adds the customer id to each row and yields that group as a page of records.

**Call relations**: The main pagination function uses this helper for each stream-specific query. This function coordinates _customer_ids and _search_stream so paginate does not have to repeat that loop for every stream.

*Call graph*: calls 2 internal fn (_customer_ids, _search_stream); called by 1 (paginate).


##### `GoogleAdsConnector.paginate`  (lines 132–200)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function chooses the right Google Ads query for the requested stream and yields pages of records. It is where stream names such as “campaigns” or “campaign_metrics” become actual Google Ads queries.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It matches the stream name to a query, then asks _query_each_customer to run that query across all accessible customer accounts. For campaign metrics, it uses the cursor date if present, otherwise it starts 90 days before the current date. It yields pages of raw records, or skips unsupported/refused streams with a clear reason.

**Call relations**: The source sync framework calls this when it wants records for a stream. It hands the chosen query to _query_each_customer, and it catches Google refusal errors so a missing permission or unapproved developer token becomes a controlled StreamSkipped result instead of an unexpected crash.

*Call graph*: calls 2 internal fn (__init__, _query_each_customer); 2 external calls (now, timedelta).


##### `GoogleAdsConnector.flatten`  (lines 202–233)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function turns selected nested Google Ads records into simpler, flatter records with stable key fields. The sync system needs those keys to identify and update the same customer, campaign, or metric row over time.

**Data flow**: It receives one record and the stream it belongs to. For customers, it pulls out the customer id and name. For campaigns, it pulls out the resource name, name, status, and start date. For campaign metrics, it builds a unique id from customer id, campaign id, and date, then copies common metric values like impressions, clicks, and cost. For streams that do not need special shaping, it returns the record unchanged.

**Call relations**: After paginate has produced raw Google Ads rows, the sync system can call this to normalize them. It uses dict_or_empty so missing nested sections behave like empty dictionaries instead of causing errors.

*Call graph*: 1 external calls (dict_or_empty).


### Social business analytics
Instagram reads business pages, connected accounts, content, stories, and analytics through Meta's Graph API.

### `extensions/sources/ufo_ext_sources/instagram.py`

`io_transport` · `sync run / stream pagination`

Instagram business data is reached through Facebook Pages, so this connector starts there. It asks Facebook for the Pages the current grant can access, finds any linked Instagram business account on each Page, and then uses those account IDs to read media, stories, and insight numbers such as reach or impressions. Think of the Pages as the front desk: the connector must check there first before it can enter the Instagram rooms behind it.

The file defines several stream descriptions, one for each kind of record the sync can produce. A stream is just a named lane of data, such as "media" or "user_insights", with rules like which field is the record ID and which date field can be used as a bookmark for incremental syncing.

Most of the work is done by `InstagramConnector`. It follows Facebook Graph API pagination, where each response contains a `data` list and sometimes a `paging.next` link to the next page. For media, stories, and daily user insights, it uses the saved cursor to skip older records. For insights attached to individual media or stories, it tolerates common "not available" errors and keeps going. If the whole account refuses access because the grant lacks permission or the token is invalid, it marks the stream as skipped rather than crashing the entire run.

#### Function details

##### `InstagramConnector._paged`  (lines 92–109)

```
async def _paged(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Facebook Graph API collection that may span many pages. It hides the repeated work of following the API's "next" links so callers can receive batches of records.

**Data flow**: It receives an HTTP client, an API path, and optional query parameters. It requests the first page, extracts the list stored under `data`, yields that list when it is not empty, then follows the `paging.next` URL until there are no more pages. It does not combine all results into one large list; it streams them out page by page.

**Call relations**: `_pages` uses this to walk `/me/accounts`, and `_account_collection` uses it to walk each Instagram account's media or stories. It also relies on `records_at` to safely pull the `data` array out of the API response.

*Call graph*: called by 2 (_account_collection, _pages); 1 external calls (records_at).


##### `InstagramConnector._pages`  (lines 111–116)

```
async def _pages(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Facebook Pages available to the current credential, including any linked Instagram business account information. This is the starting point for nearly every other Instagram read.

**Data flow**: It builds a field list asking for Page ID, Page name, and linked Instagram account details. It sends that request through `_paged`, collects all returned Page batches into one list, and returns that list to the caller.

**Call relations**: `paginate` calls this directly when the requested stream is `pages`. `_instagram_accounts` also calls it because Instagram accounts are discovered through Pages rather than listed independently here.

*Call graph*: calls 1 internal fn (_paged); called by 2 (_instagram_accounts, paginate).


##### `InstagramConnector._instagram_accounts`  (lines 118–128)

```
async def _instagram_accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Extracts the usable Instagram business accounts from the Pages the grant can access. It also adds the Page ID and Page name beside each Instagram account so later records can be traced back to their Page.

**Data flow**: It asks `_pages` for all accessible Pages. For each Page, it looks for an `instagram_business_account` object with an ID, copies that account's fields, adds `page_id` and `page_name`, and stores it by account ID to avoid duplicates. It returns the deduplicated accounts as a list.

**Call relations**: `paginate` calls this when syncing the `instagram_accounts` stream. `_account_collection` and `_user_insights` call it first because they need account IDs before they can request media, stories, or account-level insight data.

*Call graph*: calls 1 internal fn (_pages); called by 3 (_account_collection, _user_insights, paginate).


##### `InstagramConnector._account_collection`  (lines 130–151)

```
async def _account_collection(self, client: httpx.AsyncClient, path_suffix: str, *, fields: str, cursor: str | None, cursor_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a repeated collection, such as media or stories, from every Instagram business account. It is the shared path for account-owned objects that support paging and optional incremental filtering.

**Data flow**: It first gets all Instagram accounts from `_instagram_accounts`. For each valid account ID, it calls `_paged` on a path like `/{account_id}/media` or `/{account_id}/stories` with the requested fields. If a cursor and cursor field are provided, it keeps only records newer than that cursor. Before yielding each batch, it adds the Instagram account ID to every record so the destination knows where the record came from.

**Call relations**: `paginate` uses this helper for the `media` and `stories` streams. It hands each batch through `with_context`, which attaches the account ID as extra context before the records move onward in the sync.

*Call graph*: calls 2 internal fn (_instagram_accounts, _paged); called by 1 (paginate); 1 external calls (with_context).


##### `InstagramConnector._object_insights`  (lines 153–187)

```
async def _object_insights(self, client: httpx.AsyncClient, objects: AsyncIterator[list[dict[str, Any]]], *, metrics: str, stream_name: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches analytics for individual objects, such as each media item or story. It turns per-object insight responses into records that can be stored as their own stream.

**Data flow**: It receives an async stream of object batches, plus a list of metric names and the output stream name. For every object with an ID, it requests `/{object_id}/insights`. If Facebook says the insights are unavailable or forbidden for that object with common non-fatal statuses, it skips that object. Otherwise, it converts each returned insight into a record with a stable ID, the parent object ID, and the stream name, then yields batches of insight records.

**Call relations**: `paginate` uses this for `media_insights` and `story_insights`. In those cases, `paginate` first creates the underlying media or story stream, then passes it into `_object_insights` so insights are read after the objects they belong to are found.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `InstagramConnector._user_insights`  (lines 189–219)

```
async def _user_insights(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads daily account-level analytics for each Instagram business account. These are metrics about the account as a whole, not about a specific post or story.

**Data flow**: It starts by getting Instagram accounts from `_instagram_accounts`. For each account ID, it requests daily metrics such as impressions, reach, and profile views. It walks through each insight's `values` list, ignores malformed entries, skips values at or before the cursor, and creates records with an ID made from the account, metric name, and end time. It yields a batch when there are new rows for an account.

**Call relations**: `paginate` calls this when the selected stream is `user_insights`. It uses `records_at` to read the returned insight list and `list_or_empty` to safely treat missing or non-list `values` as an empty list.

*Call graph*: calls 1 internal fn (_instagram_accounts); called by 1 (paginate); 2 external calls (list_or_empty, records_at).


##### `InstagramConnector.paginate`  (lines 221–299)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the correct reading path for the requested Instagram stream. This is the main method the sync runner calls when it wants batches of records for one stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name and then delegates to the matching helper: Pages, Instagram accounts, media, stories, object insights, or user insights. Each helper yields batches of dictionaries, and `paginate` passes those batches back to the sync runner. If the stream is unknown, it marks it as skipped.

**Call relations**: This is the connector's dispatcher. It calls `_pages`, `_instagram_accounts`, `_account_collection`, `_object_insights`, and `_user_insights` depending on the stream being synced. It also catches permission-style HTTP failures from the Facebook API and converts them into `StreamSkipped`, so a missing permission is recorded as a skipped stream instead of a full sync failure.

*Call graph*: calls 6 internal fn (__init__, _account_collection, _instagram_accounts, _object_insights, _pages, _user_insights).


### Lifecycle email marketing
Klaviyo and Mailchimp cover richer email, audience, campaign, event, report, and customer engagement data streams.

### `extensions/sources/ufo_ext_sources/klaviyo.py`

`io_transport` · `source sync`

Klaviyo exposes its data through a web API, but that API has its own rules: special authentication headers, fixed API revision headers, cursor filters for incremental syncing, paged responses, and records wrapped in nested JSON. This file is the adapter that hides those details from the rest of the project.

At startup, it defines the Klaviyo streams the connector can read. A stream is one kind of thing to sync, such as profiles or campaigns. Some streams can be synced incrementally, meaning the system asks only for records changed since the last saved time, instead of downloading everything again.

The KlaviyoConnector builds an HTTP client with Klaviyo’s required headers, creates the right first request for each stream, follows Klaviyo’s “next page” links, and yields batches of records. If Klaviyo refuses access because the API key lacks permission, it marks that stream as skipped instead of crashing the whole run.

Klaviyo records arrive wrapped as JSON objects with fields like attributes and relationships. The flatten method turns those nested records into simpler dictionaries. For example, it pulls profile subscription consent, campaign sender details, event metric references, and segment parent list IDs into top-level fields. This matters because the rest of the system expects easy-to-read records and needs cursor fields at the top level to know where the next sync should continue.

#### Function details

##### `_stream`  (lines 36–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated', created_at_field: str='created', updated_at_field: str | None='updated', canonical:
```

**Purpose**: This helper creates a StreamSpec, which is the system’s description of one Klaviyo data stream. It saves repeated setup code and gives each stream its API name, primary key, time fields, and whether it is a main, canonical stream.

**Data flow**: It receives a friendly stream name and optional details such as the Klaviyo API object name, cursor field, created time field, and updated time field. It fills in sensible defaults when details are not provided, then returns a StreamSpec object that the connector later uses to know how to request and sync that stream.

**Call relations**: This function is used while the file is being loaded to build the KLAVIYO_STREAMS list. It hands the completed stream description to StreamSpec, which stores the metadata the connector uses during pagination and flattening.

*Call graph*: 1 external calls (__init__).


##### `KlaviyoConnector._make_client`  (lines 113–121)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to Klaviyo and adds the headers Klaviyo requires. Without these headers, Klaviyo may reject requests or interpret them using the wrong API version.

**Data flow**: It receives a base URL and a credential. It first asks the parent RestConnector to build the normal client, then adds Klaviyo’s pinned revision header. If the credential contains a direct API key, it also writes the Klaviyo-specific Authorization header. It returns the ready-to-use HTTP client.

**Call relations**: The broader connector setup calls this when preparing to sync Klaviyo. It builds on the base connector’s client creation rather than replacing it, then layers Klaviyo’s special authentication and version rules on top.


##### `KlaviyoConnector._next_path`  (lines 124–135)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This helper converts Klaviyo’s full next-page URL into the path and query string needed by the already-configured HTTP client. It lets pagination continue without changing the client’s base address.

**Data flow**: It receives a next-page link, which may be missing or empty. If there is no usable link, it returns None. If there is a link, it parses the URL, keeps only the path and query part, and returns that shorter request path.

**Call relations**: KlaviyoConnector.paginate calls this after each API response. The pagination loop uses the returned path as the next request target, or stops when this helper returns None.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `KlaviyoConnector._cursor_field_for`  (lines 138–143)

```
def _cursor_field_for(stream: StreamSpec) -> str
```

**Purpose**: This helper chooses the Klaviyo timestamp field to use for incremental syncing for a given stream. Different Klaviyo resources use different field names for the same idea: “when was this record updated or created?”

**Data flow**: It receives a stream description. If the stream is events, it returns datetime. If the stream is one of the resources that use updated_at, it returns updated_at. Otherwise, it returns updated.

**Call relations**: This is part of building the first API request for a stream. KlaviyoConnector._initial_query uses it to create the correct filter and sort parameters before pagination begins.


##### `KlaviyoConnector._initial_query`  (lines 146–160)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This method builds the query parameters for the first Klaviyo API request for a stream. It decides page size, sorting, incremental filters, and extra fields needed for certain streams.

**Data flow**: It receives a stream description and an optional saved cursor value from a previous sync. It starts with the standard page size. If the stream supports a cursor, it sorts by the right time field, and if a cursor value exists, it adds a filter asking Klaviyo for records at or after that time. It also asks for subscription details on profiles and metric details on events. It returns the finished parameter dictionary.

**Call relations**: KlaviyoConnector.paginate calls this once before requesting the first page. Later pages do not reuse these parameters because Klaviyo’s next-page links already contain the information needed to continue.

*Call graph*: called by 1 (paginate).


##### `KlaviyoConnector._lift_relationship_id`  (lines 163–174)

```
def _lift_relationship_id(rels: Any, key: str) -> str | None
```

**Purpose**: This small helper safely extracts the ID of a related Klaviyo object from a nested relationships block. It avoids errors when Klaviyo leaves a relationship empty or shaped differently than expected.

**Data flow**: It receives a relationships value and the name of the relationship to read, such as profile, metric, or list. It checks each nested layer before reading it. If it finds an ID, it returns it as text. If anything is missing or not in the expected shape, it returns None.

**Call relations**: KlaviyoConnector.flatten calls this when turning nested Klaviyo records into simpler records. It is used especially for events and segments, where related profile, metric, or list IDs are important context.

*Call graph*: called by 1 (flatten).


##### `KlaviyoConnector.flatten`  (lines 176–242)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method turns Klaviyo’s nested JSON API records into plain dictionaries that are easier for the rest of the system to store, search, and use for incremental syncing. It also pulls out a few especially useful nested details that would otherwise be buried.

**Data flow**: It receives one raw Klaviyo record and the stream it belongs to. It starts a new flat record with the record ID and type, then copies fields from attributes to the top level. For lists and segments, it removes profile_count so changing membership counts do not make the records look changed. For specific streams, it adds helpful fields such as profile email consent, campaign subject and sender, event profile and metric IDs, event message ID, and segment parent list ID. It returns the flattened dictionary.

**Call relations**: This method is used by the connector framework after records are fetched. When it needs related object IDs, it calls KlaviyoConnector._lift_relationship_id so the nested relationship parsing stays safe and consistent.

*Call graph*: calls 1 internal fn (_lift_relationship_id).


##### `KlaviyoConnector.paginate`  (lines 244–297)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads one Klaviyo stream page by page. It is the main loop that sends API requests, follows next-page links, adds event metric names when Klaviyo includes them, and yields batches of records to the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It builds the first path and query, requests a page, reads the data array, enriches event records with metric names when available, yields records when the page has any, then follows the response’s next link. This repeats until there is no next page. If Klaviyo returns 401 or 403, it turns that refusal into a StreamSkipped result so the stream is recorded as skipped because of missing permission.

**Call relations**: The connector framework calls this when it is time to fetch data for a Klaviyo stream. It calls KlaviyoConnector._initial_query to prepare the first request and KlaviyoConnector._next_path to move through later pages. When access is refused, it creates a StreamSkipped error instead of letting the whole connector run fail.

*Call graph*: calls 3 internal fn (__init__, _initial_query, _next_path).


### `extensions/sources/ufo_ext_sources/mailchimp.py`

`io_transport` · `during source sync`

Mailchimp stores useful marketing data in many places, and some records are nested under other records. For example, subscribers belong to a list, interests belong to an interest category inside a list, and email activity belongs to a campaign report. This file is the map and walking route for all of those shapes.

It defines the Mailchimp streams the system can sync, including their names, main ID fields, and time fields used for incremental syncs. An incremental sync means “only ask for records changed since the last successful run,” which saves time and API calls.

The main class, MailchimpConnector, is a read-only connector built on the shared REST connector. It knows which Mailchimp URL to call for each stream, how Mailchimp wraps records inside response fields like `lists` or `members`, and how to keep requesting pages until all records are fetched. Some streams are simple top-level lists, like campaigns. Others fan out: the connector first finds all list IDs, then asks Mailchimp for each list’s members or segments. For email activity, Mailchimp gives one recipient with an `activity` array, so this file splits that into one row per action and creates a stable ID for each action. If Mailchimp refuses access with an authorization error, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 62–80)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None=None, created_at_field: str='created_at', updated_at_field: str | None='updated_at', canonical
```

**Purpose**: Builds a StreamSpec, which is the system’s small description card for one Mailchimp stream. It records things like the stream name, where the data comes from, which field is the record ID, and which time field can be used for incremental syncing.

**Data flow**: It receives stream settings such as name, source object, primary key, cursor field, and timestamp fields. It fills in sensible defaults when some settings are not provided, then returns a StreamSpec object that the connector later uses to decide how to sync that stream.

**Call relations**: This helper is used while defining the file’s Mailchimp stream list. It hands those stream descriptions to StreamSpec so the broader connector framework can treat Mailchimp streams in the same standard way as other sources.

*Call graph*: 1 external calls (__init__).


##### `MailchimpConnector.flatten`  (lines 148–154)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes individual Mailchimp records before the rest of the system stores or compares them. For member records, it creates a common `created_at` value from Mailchimp’s signup or opt-in timestamps.

**Data flow**: It receives one record and the stream it came from. If the stream is list members or segment members, it copies the record and adds `created_at` from `timestamp_signup` or, if that is missing, `timestamp_opt`; otherwise it returns the record unchanged.

**Call relations**: This method is part of the connector’s record-cleanup step. It does not call other local helpers, but it prepares Mailchimp’s slightly different member timestamp fields for the shared sync machinery.


##### `MailchimpConnector._data_field`  (lines 157–158)

```
def _data_field(stream: StreamSpec) -> str
```

**Purpose**: Finds the JSON field where Mailchimp puts the actual records for a stream. This matters because Mailchimp responses are wrapped, such as `{ "lists": [...] }` or `{ "members": [...] }`, instead of returning the list directly.

**Data flow**: It receives a stream description. It looks up the stream name in the file’s field map and returns the matching response field name, or falls back to the stream name if no special mapping exists.

**Call relations**: The pagination helpers call this when they are about to read a Mailchimp response. It gives `_paginate_top_level`, `_paginate_per_list`, and `_paginate_per_report` the exact wrapper field to extract records from.

*Call graph*: called by 3 (_paginate_per_list, _paginate_per_report, _paginate_top_level).


##### `MailchimpConnector._cursor_params`  (lines 161–168)

```
def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Turns the system’s saved cursor value into the query parameter Mailchimp expects for incremental syncing. A cursor is the last-seen time marker used to avoid rereading old records.

**Data flow**: It receives a stream description and an optional cursor string. If either the cursor or the stream’s cursor field is missing, it returns no parameters. If Mailchimp supports that cursor field, it returns a small dictionary such as `{ "since_last_changed": cursor }`.

**Call relations**: Pagination helpers call this before making requests so Mailchimp can filter results server-side. It feeds `_paginate_top_level`, `_paginate_per_list`, `_paginate_segment_members`, and `_paginate_per_report` with the right query parameters.

*Call graph*: called by 4 (_paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector.paginate`  (lines 170–226)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Acts as the traffic director for Mailchimp syncing. Given a stream, it chooses the right fetching strategy: simple top-level paging, list-based fan-out, report-based fan-out, or special email activity expansion.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching pagination helper, and yields pages of records as they arrive. If Mailchimp returns a 401 or 403 authorization refusal, it changes that into a StreamSkipped error with a clear message.

**Call relations**: The shared sync engine calls this when it wants records for a Mailchimp stream. This method then hands the work to helpers such as `_paginate_top_level`, `_paginate_per_list`, `_paginate_interests`, `_paginate_segment_members`, `_paginate_per_report`, or `_paginate_email_activity` depending on the stream.

*Call graph*: calls 7 internal fn (__init__, _paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members, _paginate_top_level).


##### `MailchimpConnector._paginate_top_level`  (lines 228–239)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches streams that live directly at a Mailchimp endpoint, such as lists, campaigns, automations, or reports. These are the simple cases where the connector can page through one URL.

**Data flow**: It receives an HTTP client, stream description, endpoint path, and optional cursor. It builds the right record field name and cursor query parameters, then asks the base REST connector to walk through offset-based pages. It yields each page of records.

**Call relations**: `paginate` calls this for top-level streams. This helper uses `_data_field` to know where records are in the response and `_cursor_params` to add incremental-sync filters.

*Call graph*: calls 2 internal fn (_cursor_params, _data_field); called by 1 (paginate).


##### `MailchimpConnector._paginate_child`  (lines 241–258)

```
async def _paginate_child(self, client: httpx.AsyncClient, path: str, *, data_field: str, params_base: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the common paging routine for nested Mailchimp endpoints, such as members under a list or unsubscribes under a report. It is the reusable “turn pages until done” helper for child collections.

**Data flow**: It receives an HTTP client, endpoint path, response field name, and optional base query parameters. It calls the shared offset-page reader with Mailchimp’s page size and `count` limit parameter, then yields each list of records it gets back.

**Call relations**: Several deeper pagination methods rely on this instead of repeating the same paging code. It is used by list fan-outs, report fan-outs, interest traversal, segment member traversal, and email activity fetching.

*Call graph*: called by 5 (_paginate_email_activity, _paginate_interests, _paginate_per_list, _paginate_per_report, _paginate_segment_members).


##### `MailchimpConnector._ids`  (lines 260–268)

```
async def _ids(self, client: httpx.AsyncClient, path: str, data_field: str) -> AsyncIterator[str]
```

**Purpose**: Reads a paged Mailchimp collection and yields only the record IDs. This is useful when later calls need parent IDs before they can fetch child records.

**Data flow**: It receives an endpoint path and the response field that contains records. It pages through that endpoint, looks at each row, and yields the row’s `id` as a string when present. Rows without usable IDs are ignored.

**Call relations**: `_list_ids` and `_report_ids` call this as their shared ID-reading helper. Those IDs then drive the fan-out methods that fetch records under each list or report.

*Call graph*: called by 2 (_list_ids, _report_ids).


##### `MailchimpConnector._list_ids`  (lines 270–272)

```
async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of all Mailchimp audiences, which Mailchimp calls lists. Many other records cannot be fetched until the connector knows which list they belong to.

**Data flow**: It asks `_ids` to read the `/3.0/lists` endpoint and extract IDs from the `lists` response field. It yields one list ID at a time.

**Call relations**: List-based pagination methods call this before fetching child data. `_paginate_per_list`, `_paginate_interests`, and `_paginate_segment_members` use each yielded list ID to build the next Mailchimp endpoint.

*Call graph*: calls 1 internal fn (_ids); called by 3 (_paginate_interests, _paginate_per_list, _paginate_segment_members).


##### `MailchimpConnector._report_ids`  (lines 274–276)

```
async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Produces the IDs of Mailchimp campaign reports. Report IDs are needed before the connector can fetch report-specific records like unsubscribes or email activity.

**Data flow**: It asks `_ids` to read the `/3.0/reports` endpoint and extract IDs from the `reports` response field. It yields one report ID at a time.

**Call relations**: Report-based pagination methods call this as their first step. `_paginate_per_report` and `_paginate_email_activity` use the IDs to visit each report’s child endpoints.

*Call graph*: calls 1 internal fn (_ids); called by 2 (_paginate_email_activity, _paginate_per_report).


##### `MailchimpConnector._paginate_per_list`  (lines 278–299)

```
async def _paginate_per_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child collections that sit directly under each Mailchimp list, such as members, segments, tags, or interest categories. It also marks each returned row with the list it came from when requested.

**Data flow**: It receives the child endpoint name, stream description, cursor, and optional parent-stamp field. It finds all list IDs, builds a child URL for each list, pages through that child endpoint, and adds the parent `list_id` to each record when needed. It yields pages of child records.

**Call relations**: `paginate` calls this for list-based streams. It uses `_list_ids` to discover parents, `_data_field` to read the right response wrapper, `_cursor_params` for incremental filters, `_paginate_child` for the actual page walking, and URL quoting to safely place IDs in paths.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_interests`  (lines 301–323)

```
async def _paginate_interests(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches interests, which are nested two levels deep in Mailchimp: each list has interest categories, and each category has interests. This method walks that hierarchy in order.

**Data flow**: It receives an HTTP client, stream description, and cursor argument, though this specific path does not apply cursor parameters. It gets every list ID, fetches that list’s interest categories, then fetches interests for each category. Each interest record is stamped with its `list_id` and `category_id` before pages are yielded.

**Call relations**: `paginate` calls this for the `interests` stream. It depends on `_list_ids` to find lists and `_paginate_child` to fetch both category pages and interest pages, using URL quoting whenever IDs are placed into endpoint paths.

*Call graph*: calls 2 internal fn (_list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_segment_members`  (lines 325–347)

```
async def _paginate_segment_members(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the members inside each segment of each list. This is another two-step fan-out: find lists, find their segments, then fetch members for each segment.

**Data flow**: It receives an HTTP client, stream description, and optional cursor. It builds cursor query parameters, loops through list IDs, fetches segments for each list, then fetches members for each segment. It adds both `list_id` and `segment_id` to each member row before yielding pages.

**Call relations**: `paginate` calls this for the `segment_members` stream. It uses `_cursor_params` for incremental member filtering, `_list_ids` for parent lists, `_paginate_child` for segment and member page fetching, and URL quoting for safe endpoint paths.

*Call graph*: calls 3 internal fn (_cursor_params, _list_ids, _paginate_child); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_per_report`  (lines 349–369)

```
async def _paginate_per_report(self, client: httpx.AsyncClient, stream: StreamSpec, *, child_path: str, cursor: str | None, stamp_parent_field: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child collections that live under each campaign report, such as unsubscribes. It marks each returned row with the report or campaign ID it came from when requested.

**Data flow**: It receives the child endpoint name, stream description, cursor, and optional parent-stamp field. It gets all report IDs, builds a child endpoint for each report, pages through the records, and adds the parent field such as `campaign_id` to each row when needed. It yields pages of records.

**Call relations**: `paginate` calls this for report-based child streams. It uses `_report_ids` to find parent reports, `_data_field` to locate records inside responses, `_cursor_params` for incremental filters, `_paginate_child` for paging, and URL quoting for safe path construction.

*Call graph*: calls 4 internal fn (_cursor_params, _data_field, _paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).


##### `MailchimpConnector._paginate_email_activity`  (lines 371–403)

```
async def _paginate_email_activity(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches email activity for each campaign report and reshapes it into one row per recipient action. This is needed because Mailchimp groups many actions inside one recipient record, while the sync system needs stable individual rows.

**Data flow**: It receives an HTTP client and optional cursor. If a cursor is present, it sends it as Mailchimp’s `since` filter. For each report ID, it fetches email activity pages. For each recipient, it copies the recipient-level fields, then merges in each item from the recipient’s `activity` list. It creates a stable ID from email ID, action, and timestamp when Mailchimp does not provide one, and yields only pages that contain expanded activity rows.

**Call relations**: `paginate` calls this for the `email_activity` stream. It uses `_report_ids` to visit every report, `_paginate_child` to fetch the grouped activity records, and URL quoting to build safe report-specific endpoint paths.

*Call graph*: calls 2 internal fn (_paginate_child, _report_ids); called by 1 (paginate); 1 external calls (quote).

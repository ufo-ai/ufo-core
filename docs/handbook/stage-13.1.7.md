# Finance, billing, accounting, and commerce connectors  `stage-13.1.7`

This stage is shared behind-the-scenes support for bringing money-related business data into the system. It is not the place where invoices are paid or accounts are changed. Instead, it acts like a set of adapters that read from outside finance tools and turn their data into steady “streams” of records, meaning ordered batches the rest of the system can store, sync, and look up later.

Each file is one adapter for a different service. Brex reads spend-management data such as transactions, expenses, users, vendors, budgets, and departments. Chargebee, Recurly, and Stripe read subscription and billing data, including customers, subscriptions, invoices, payments, and related detail records. QuickBooks and Xero read accounting records such as accounts, bills, contacts, invoices, and payments, while hiding each service’s API quirks from the rest of the code. Square reads commerce data like customers, locations, orders, catalog items, payments, and inventory counts. Together, these connectors make many different finance systems look consistent to the main sync machinery.

## Files in this stage

### Spend management
Connectors that ingest company spend, expenses, vendors, budgets, departments, and related operational finance records.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `during Brex source sync`

Brex exposes business spending data through a web API. This file is the read-only connector for that API. Without it, the system would not know which Brex endpoints to call, how to move through Brex’s paged results, or how to label each kind of Brex object as a stream of records.

The file first defines the Brex streams the system can sync. A stream is one category of data, like “transactions” or “vendors.” Each stream has a name, a primary key, and sometimes a cursor field. A cursor field is a date-like value the system can remember as a progress marker. For Brex, transactions and expenses have these markers, but Brex does not use them to filter results on the server side, so the connector still reads full pages from the API.

The central class, `BrexConnector`, says where Brex lives on the internet, which streams exist, and how to fetch one stream page by page. Brex uses a common paging pattern: each response contains an `items` list and a `next_cursor` token. Think of the cursor like a bookmark in a long catalog. The connector asks for 100 records at a time, yields any records it finds, then follows the bookmark until Brex says there are no more pages.

This connector only reads data. It does not create, update, or delete anything in Brex.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard description of one Brex data stream, such as expenses or users. It keeps the stream setup short and consistent instead of repeating the same fields for every stream.

**Data flow**: It receives a stream name plus optional details like the primary key, cursor field, and whether the stream is considered canonical. It uses those values to create a `StreamSpec`, which is the system’s small description card for a stream: what it is called, how records are identified, and which field can act as a progress marker. The result is returned and later collected into the Brex stream list.

**Call relations**: This helper is used while the file is being loaded to build `BREX_STREAMS`. Internally, it hands the stream details to `StreamSpec`, the shared source framework type that the rest of the connector system understands.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches all pages for one Brex stream from the Brex API. It is used when the sync process needs actual records, not just the description of what records exist.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor argument. It looks up the correct Brex API path for that stream, then repeatedly requests up to 100 records. From each API response, it reads the `items` list, safely turns missing or invalid item data into an empty list, yields any records found, and then follows `next_cursor` to the next page. When Brex stops returning a next cursor, the method ends. If the stream has no known Brex endpoint, it raises an error instead of guessing.

**Call relations**: The broader source framework calls this method when syncing a Brex stream. During each page fetch, it relies on the parent REST connector’s request helper to make the web request, and it uses `ufo.sdk.sources.list_or_empty` to make sure the returned `items` value is safe to iterate over before passing records back to the sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### Subscription billing
Connectors that sync recurring-revenue platforms, including customers, subscriptions, invoices, transactions, and nested billing records.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `during source sync when reading Chargebee streams`

Chargebee is an online billing system, and its API returns data in a very regular but slightly wrapped shape. Each page contains a list of records, and each record is usually inside an envelope named after the resource, such as `{customer: {...}}`. This connector knows those Chargebee habits so the wider system does not have to.

The file defines the list of Chargebee streams the product can read, including common top-level objects like customers and invoices, plus child objects like contacts or quote line groups. A stream is a named kind of data, with details such as its main ID field and the timestamp used for incremental syncing. Incremental syncing means “only ask for records newer than the last successful run,” which avoids rereading everything every time.

The connector builds an HTTP client using Chargebee’s expected authentication: the API key is sent as the username for HTTP Basic authentication, with an empty password. It then chooses the right paging strategy for each stream. Most streams use Chargebee’s normal `list` plus `next_offset` paging. Some child streams first read parent records, such as customers, then ask Chargebee for the children under each parent, stamping the parent ID onto each child so the relationship is not lost.

If Chargebee rejects access with a permission or authentication error, the stream is skipped with a clear message instead of failing silently.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one kind of Chargebee data. It keeps the long stream list readable by filling in common defaults, such as using `id` as the main record key and `updated_at` as the usual change-tracking timestamp.

**Data flow**: It receives a stream name and optional details like the source object name, primary key, and cursor timestamp field. It packages those details into a `StreamSpec`, which is the system’s standard description of a readable data stream. The result is later collected into the Chargebee stream list.

**Call relations**: This helper is used while the module is loaded to build `CHARGEBEE_STREAMS`. Its output is handed to the connector as the catalog of Chargebee data types the sync engine can request.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Chargebee. It sets the Chargebee base address, request timeouts, required headers, and authentication so later code can simply make API calls.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares JSON/form headers, and sets connection and read time limits. If the credential already provides a special transport, it uses that; otherwise it uses the bearer value as a Chargebee API key in HTTP Basic authentication. It returns an `httpx.AsyncClient`, or raises an error if no usable authentication exists.

**Call relations**: The wider REST connector framework calls this when it needs a network client for a Chargebee sync. The returned client is then passed into pagination methods such as `ChargebeeConnector.paginate` and its helper methods.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This unwraps Chargebee records from their resource envelope so downstream code sees a normal flat record. For example, it turns `{customer: {id: ...}}` into a record where the customer fields are at the top level.

**Data flow**: It receives one raw record and the stream description. It looks for a nested dictionary under the stream’s source object name. If found, it copies that inner record and preserves any extra top-level fields, such as a parent ID added by a substream. It returns the flattened record; if there is no expected envelope, it returns the original record unchanged.

**Call relations**: After pagination yields raw Chargebee records, the broader connector flow can call this to normalize each record. It is especially important for child streams, because `_paginate_substream` stamps parent IDs at the top level and `flatten` keeps those IDs when unwrapping the child record.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading pages from a Chargebee stream. It decides whether a stream can be read directly from a list endpoint or needs a special parent-child walk.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It checks the stream name and delegates to the matching paging method. As each helper produces a page of records, this method yields that page outward. If Chargebee responds with 401 or 403, meaning unauthorized or forbidden, it turns that into a `StreamSkipped` error with a human-readable reason.

**Call relations**: The sync engine calls this when it wants records for a particular Chargebee stream. It hands normal streams to `_paginate_list`, child streams to helpers like `_paginate_contacts`, and scheduled subscription detail reads to `_paginate_subscription_scheduled`.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, adds the “only records after this cursor” filter used for incremental syncing.

**Data flow**: It receives the stream description and an optional cursor value. It always starts with a `limit` parameter set to the connector’s page size. If a cursor exists and the stream has a cursor field, it adds a Chargebee-style parameter such as `updated_at[after]`. It returns the finished parameter dictionary.

**Call relations**: `_paginate_list` calls this before requesting pages from Chargebee. This keeps the paging code focused on walking pages, while this helper focuses on building the correct Chargebee query.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a standard top-level Chargebee list endpoint, one page at a time. It is the common path for streams such as customers, invoices, items, and transactions.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the API path for that stream, builds the request parameters, and then asks the base connector’s cursor-page helper to follow Chargebee’s `next_offset` tokens. It yields each page of raw records as it arrives.

**Call relations**: `paginate` calls this directly for ordinary streams. The child-stream methods also call it first to discover parent records, such as items before attached items or customers before contacts.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads attached items, which Chargebee exposes underneath each item rather than as one global list. It first finds items, then asks for attached items belonging to each item.

**Data flow**: It receives an HTTP client and an optional cursor. It uses `_paginate_list` to read item pages, extracts each item ID, skips parents without an ID, and calls `_paginate_substream` for `/items/{item_id}/attached_items`. The yielded child records include the parent `item_id` so the relationship remains clear.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This helper relies on `_paginate_list` for parent item discovery and `_paginate_substream` for the repeated child-page walking.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads customer contacts, which Chargebee stores under individual customers. It walks through customers first, then fetches each customer’s contact pages.

**Data flow**: It receives an HTTP client and an optional cursor. It reads customer pages through `_paginate_list`, extracts each customer ID, and ignores records where no ID can be found. For each valid customer, it calls `_paginate_substream` on `/customers/{customer_id}/contacts`, yielding contact pages marked with `customer_id`.

**Call relations**: `paginate` calls this for the `contact` stream. It connects the top-level customer list to the generic substream reader so contacts can be synced even though they are not available from one simple global endpoint.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads quote line groups, which are child records under individual quotes. It preserves which quote each line group came from.

**Data flow**: It receives an HTTP client and an optional cursor. It reads quote pages using `_paginate_list`, extracts each quote ID, and skips records without one. For each quote, it calls `_paginate_substream` for `/quotes/{quote_id}/quote_line_groups`, yielding pages of child records with the parent `quote_id` added.

**Call relations**: `paginate` calls this when syncing `quote_line_group`. Like the other child-stream readers, it uses `_paginate_list` to find parents and `_paginate_substream` to walk each parent’s child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the special “subscription with scheduled changes” view for each subscription. Unlike normal list endpoints, Chargebee exposes this as a single detail request per subscription.

**Data flow**: It receives an HTTP client and an optional cursor. It reads subscription pages through `_paginate_list`, extracts each subscription ID, and skips records without one. For each subscription, it requests `/subscriptions/{id}/retrieve_with_scheduled_changes`; if the response contains a subscription record, it yields a one-record page that includes both the subscription envelope and `subscription_id`.

**Call relations**: `paginate` calls this for `subscription_with_scheduled_changes`. It uses `_paginate_list` only to find the parent subscriptions, then performs its own per-subscription detail request because this Chargebee endpoint does not follow the normal list pattern.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared reader for child endpoints that use Chargebee’s normal paged list format. It also adds the parent ID to every child record, like putting a return address on each item so it can be traced back later.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent ID field to add, and the parent ID value. It asks the base cursor-page helper to follow `next_offset` pages with the standard page limit. For each dictionary record returned, it copies the record, adds the parent ID field, and yields non-empty pages of these enriched records.

**Call relations**: `_paginate_attached_items`, `_paginate_contacts`, and `_paginate_quote_line_groups` call this after they have found a parent ID. It provides the common child-page walking behavior so each parent-specific helper only needs to know how to find its parents and endpoint path.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during Recurly source sync`

This connector is the system's adapter for Recurly. Without it, the platform would not know how to fetch Recurly accounts, subscriptions, invoices, plans, coupons, and related records in a reliable way.

Recurly returns list results in pages, like a book that says “turn to this next page” until there are no more pages. The connector follows those page links and yields batches of records. For incremental syncs, where the system only wants records changed since a previous time, it adds Recurly's `begin_time` filter and sorts results by the stream's time field.

Some Recurly data is nested under another object. For example, account notes live under each account, and unique coupon codes live under each bulk coupon. This file first fetches the parent IDs, then asks Recurly for each parent's child records, and stamps the parent ID onto each child row so the relationship is not lost.

Authentication is also Recurly-specific. Recurly uses HTTP Basic authentication, where the API key is used as the username, rather than a bearer token. The connector also pins the Recurly API version through an HTTP header. If Recurly refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole sync unnecessarily.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream definition, which is the system's description of one kind of Recurly data to read. It keeps the stream list concise by filling in common defaults such as the primary key and update-time field.

**Data flow**: It receives a stream name plus optional details like the Recurly API object name, primary key, cursor field, and whether the stream is canonical. It fills in defaults when details are not provided, then returns a `StreamSpec`, which is the connector's recipe for reading that stream.

**Call relations**: It is used while this module is loaded to build the list of Recurly streams. Each resulting stream definition is later used by the connector when `paginate` decides which Recurly endpoint to call and how to request incremental data.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Recurly. It applies the correct Recurly API version, timeout settings, and authentication style before any data is requested.

**Data flow**: It receives a base URL and a credential. It trims the base URL, prepares headers, sets connect and read time limits, then chooses how to authenticate: either through a provided proxy transport or by using the API key as HTTP Basic authentication. It returns an asynchronous HTTP client ready to send requests, or raises an error if no usable credential is present.

**Call relations**: The broader connector framework calls this when it needs a network client for Recurly. The client it returns is then passed into pagination functions, which use it to fetch pages from Recurly's API.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This normalizes Recurly's “next page” link into the path format the HTTP client expects. It allows the connector to follow pagination links whether Recurly returns a full URL or just a path.

**Data flow**: It receives a possible next-page link. If the link is empty, it returns nothing. If it is a full URL, it strips it down to the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: Pagination helpers call this after reading each page from Recurly. It turns the `next` value in Recurly's response into the next request target for top-level streams, parent ID scans, coupon ID scans, and per-parent child streams.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the first set of query parameters for a Recurly list request. It sets page size, sort order, and, when available, the starting time for an incremental sync.

**Data flow**: It receives a stream definition and an optional cursor value from the previous sync. It creates parameters asking Recurly for up to 200 records in ascending order, sorted by the stream's cursor field or by creation time. If the stream supports a cursor and a cursor value was supplied, it adds `begin_time` so Recurly only returns newer records.

**Call relations**: The top-level and per-parent pagination routines call this before their first request. After the first request, they stop sending these initial parameters because Recurly's own `next` link already carries the cursor needed for later pages.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for a Recurly stream. Given a stream and an optional sync cursor, it yields pages of Recurly records in the right way for that stream.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. It checks whether the stream is a normal top-level resource, a parent-filtered view, or a special coupon-parent stream. It then delegates to the right pagination helper and yields each non-empty page of records. If Recurly rejects access with 401 or 403, it turns that into a skipped stream message.

**Call relations**: The connector framework calls this when it wants records for one Recurly stream. This function is the dispatcher: it sends ordinary streams to `_paginate_top_level`, nested streams to `_paginate_per_parent`, and filters the special `unique_coupons_parent` stream to only bulk coupons.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly collection endpoint, such as accounts or invoices, one page at a time. It follows Recurly's pagination links until there are no more pages.

**Data flow**: It receives an HTTP client, a stream definition, a starting API path, and an optional cursor. It builds the first query, sends a request, yields the `data` records if any exist, then follows the response's `next` link while `has_more` is true. It stops when Recurly says there are no more pages.

**Call relations**: `paginate` calls this for standard streams and for the coupon-parent stream. Inside the loop, it uses `_initial_query` for the first request and `_next_path` to prepare each follow-up request.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields their IDs. It is used when the connector must fetch child records that live underneath each account.

**Data flow**: It starts at the `/accounts` endpoint with a simple page-size and sort query. For each page returned by Recurly, it looks through the account rows and yields each valid account ID as text. It follows `next` links until the account list is finished.

**Call relations**: `_paginate_per_parent` calls this for account-based child streams such as account notes, billing infos, shipping addresses, and account coupon redemptions. The IDs it yields become part of the child endpoint paths.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through Recurly coupons and yields only the IDs of bulk coupons. It is used because unique coupon codes are available under bulk coupon parents.

**Data flow**: It starts at the `/coupons` endpoint and reads pages in creation order. For each coupon row, it checks that the row has an ID and that its `coupon_type` is `bulk`. Only those matching IDs are yielded. It follows Recurly's next-page links until finished.

**Call relations**: `_paginate_per_parent` calls this when the parent resource is coupons. The returned coupon IDs are used to request each coupon's unique coupon codes.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Recurly streams where records are nested under a parent object. It first finds each parent, then fetches that parent's child records and adds the parent ID to each child row.

**Data flow**: It receives the HTTP client, stream definition, parent path, child path, field name for the parent ID, and optional cursor. It chooses whether to list account IDs or bulk coupon IDs. For each parent ID, it builds a child URL, sends paged requests with the stream's initial query, stamps the parent ID onto each child record if missing, and yields each page of child records.

**Call relations**: `paginate` calls this for nested Recurly streams such as account notes and unique coupon codes. This function relies on `_account_ids` or `_coupon_ids` to discover parents, `_initial_query` to start each child listing correctly, and `_next_path` to continue through child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `source sync`

Stripe exposes many kinds of business data, but it does not send everything in one simple download. Most lists come back a page at a time, with a marker saying whether more pages exist. Some records also live underneath other records, like payment methods under customers or line items under invoices. This file is the Stripe-specific map and driver for walking all of those shapes safely.

At the top, it defines the available Stripe streams and the extra rules some streams need. For example, subscriptions ask for all statuses, external accounts are split into bank accounts and cards, and child streams remember which parent record they came from. The StripeConnector class then uses those rules to fetch records from Stripe’s API.

The main flow starts with paginate. It decides whether a stream is a normal Stripe list, a child list reached through a parent URL, a child list filtered by a query parameter, or an external account list. The shared page loop asks Stripe for up to 100 records at a time, follows Stripe’s starting_after marker, and optionally sends a created-after filter for incremental syncing. Each record is lightly normalized so Unix timestamps become readable ISO date strings. If Stripe refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole connector.

#### Function details

##### `_stream`  (lines 86–104)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates one stream definition for a Stripe object. It keeps the long list of Stripe streams readable by filling in common defaults, such as using id as the main identifier and created as the usual time cursor.

**Data flow**: It receives a stream name and optional details like the Stripe API object name, primary key, cursor field, and whether the stream is considered canonical. It combines those choices with sensible defaults, then returns a StreamSpec object that the connector later uses as instructions for syncing that stream.

**Call relations**: The file uses this helper while building the STRIPE_STREAMS list. It hands the finished stream description to StreamSpec, which is the shared source-system type that tells the rest of the sync machinery what each stream is called and how to track it.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 179–182)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the web client used to talk to Stripe. Its Stripe-specific job is to pin the Stripe API version, so responses keep the expected shape even if Stripe changes its default API behavior later.

**Data flow**: It receives the base URL and a credential object supplied by the surrounding authentication system. It asks the base REST connector to create the HTTP client, adds the Stripe-Version header, and returns the ready-to-use client.

**Call relations**: This runs when the connector is being set up for network calls. The rest of this file’s pagination methods use the resulting client indirectly when they request Stripe pages.


##### `StripeConnector._list_path`  (lines 185–186)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This turns a stream definition into the standard Stripe list URL path. It is the small rule that says, for example, a stream whose source object is customers should be read from /v1/customers.

**Data flow**: It receives a StreamSpec, reads its source_object value, and formats that into a Stripe API path string. Nothing external is changed.

**Call relations**: The main paginate method uses it for ordinary streams. The child-stream methods also use it to find parent lists before they fetch the child records below each parent.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 189–201)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved sync cursor into the Unix timestamp format Stripe expects for created-after filters. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor as text or nothing. If there is no cursor, it returns nothing. If the cursor is already all digits, it returns it as a number. Otherwise it tries to read it as an ISO date string, assumes UTC time if no time zone is present, and returns the matching Unix timestamp. If the text cannot be understood as a date, it returns nothing.

**Call relations**: The shared page loop calls this before making requests. Its result decides whether _page_loop can ask Stripe to only return records created at or after the saved point.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 203–229)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Stripe stream. It chooses the right fetching strategy for the stream and yields pages of records to the broader sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name against the special cases: external accounts, child streams with parent paths, child streams selected by query parameters, or a normal list. It then delegates to the matching helper and yields each page it gets back. If Stripe rejects the request with an authorization-style error, it turns that into a StreamSkipped signal with a human-readable reason.

**Call relations**: The surrounding source runner calls this when it wants records from Stripe. paginate then hands the work to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, or the shared _page_loop, depending on the shape of the Stripe endpoint.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 231–262)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common engine for walking through Stripe’s paged list responses. It keeps asking for the next page until Stripe says there are no more records.

**Data flow**: It receives the client, URL path, stream description, optional cursor, and optional extra request parameters. It builds request parameters with a page size of 100, adds Stripe’s starting_after marker when moving past the first page, adds a created-after filter when possible, and merges any stream-specific extras. It fetches a page, normalizes each record through _browse_record, yields non-empty record batches, and stops when Stripe says has_more is false or there is no usable last record id.

**Call relations**: paginate uses this for ordinary streams, and all special pagination helpers reuse it for parent and child lists. It relies on _cursor_to_unix before requests and _browse_record after responses, making it the central conveyor belt for Stripe records.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._browse_record`  (lines 265–285)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly cleans up one Stripe record so time fields are easier for the rest of the system to read. It does not reshape the whole record; it mainly adds or converts standard created_at and updated_at values.

**Data flow**: It receives one Stripe record and the stream description. It copies the record, converts numeric created_at or updated_at fields into ISO date strings, fills created_at from Stripe’s created timestamp when needed, and fills updated_at from the stream cursor when that cursor is numeric. It returns the normalized copy and does not change the original input record.

**Call relations**: _page_loop calls this for every record received from Stripe before yielding a page. This means all normal, child, query-child, and external-account streams benefit from the same timestamp cleanup.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 287–310)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that live under a parent record’s URL, such as invoice line items under each invoice. It also stamps each child with the parent id, so the relationship is not lost after syncing.

**Data flow**: It receives the client and the child stream definition. It looks up which parent stream to walk, builds the parent list path, and pages through all parent records. For each parent with an id, it builds the child URL, pages through that child collection, adds parent-identifying fields and selected parent timestamps where configured, and yields the enriched child records.

**Call relations**: paginate calls this when the requested stream is one of the configured path-based substreams. This helper uses _stream_spec to find the parent definition, _list_path to form the parent URL, and _page_loop for both parent and child paging.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_substream_query`  (lines 312–326)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that Stripe exposes through a shared endpoint filtered by a parent id, instead of through a parent-specific URL. An example is asking for subscription items by passing a subscription id as a query parameter.

**Data flow**: It receives the client and child stream definition. It looks up the parent stream name, the query parameter name, and the child endpoint. It pages through parent records, skips parents without ids, then calls the child endpoint with the parent id as an extra parameter. Each returned child row is copied with an added parent-id field before being yielded.

**Call relations**: paginate calls this for streams listed as query-based child streams. Like the path-based version, it uses _stream_spec, _list_path, and _page_loop so parent walking and child paging stay consistent with the rest of the connector.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 328–341)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe connected accounts. These are special because Stripe uses the same external_accounts endpoint and an object filter decides whether the rows are bank accounts or cards.

**Data flow**: It receives the client and the requested external-account stream. It finds the accounts stream, pages through all Stripe accounts, and for each account with an id it requests that account’s external_accounts path. The shared page loop applies the configured object filter for the stream. Each child row is copied with the account_id added, then yielded.

**Call relations**: paginate calls this for the two external account streams. It uses _stream_spec to get the accounts stream, _list_path to build the accounts list URL, and _page_loop to fetch both account pages and external-account pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 343–344)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the stream definition with a given name. It is used when a child stream needs to know how to read its parent stream.

**Data flow**: It receives a stream name, searches the file’s STRIPE_STREAMS list, and returns the first matching StreamSpec. It does not modify anything.

**Call relations**: The three special pagination helpers call this before walking parent records. It connects child-stream rules, which store parent names as text, back to the full stream definitions needed by _list_path and _page_loop.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Accounting ledgers
Connectors that read accounting-system entities such as accounts, invoices, bills, contacts, customers, and payments.

### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `source sync`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each kind of data. Instead, every read is done through one query endpoint, using a SQL-like sentence such as “select all invoices, starting at row 101, return up to 100.” This file hides that awkward shape behind a connector so the rest of the system can treat QuickBooks like a set of normal data streams.

The file first defines the list of QuickBooks streams the system knows about: accounts, customers, vendors, invoices, bills, payments, journal entries, and many more. Each stream says which QuickBooks object it reads, what field uniquely identifies a record, and which timestamp can be used to continue from the last sync.

The main class, `QuickBooksConnector`, builds the QuickBooks query, sends it to the `/query` endpoint, and walks through pages of results 100 records at a time. For most streams, it can do an incremental sync, meaning it asks only for records updated after the last saved timestamp. A few reference streams do not use that cursor and are read in full each time.

One important detail is that QuickBooks stores the update timestamp inside a nested object. The `flatten` method copies that nested value onto a flat key, so the syncing system can easily track progress. If QuickBooks refuses access with a 401 or 403 response, the connector skips that stream with a clear message rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one QuickBooks stream. It saves repeated setup work by filling in shared facts, such as using `Id` as the primary key and QuickBooks metadata timestamps for create and update times.

**Data flow**: It receives the friendly stream name, the matching QuickBooks object name, an optional cursor field, and whether the stream is considered canonical. It packages those details into a `StreamSpec`, which is the system’s description of how to read that kind of record.

**Call relations**: This helper is used while the file is being loaded to build `QUICKBOOKS_STREAMS`, the full menu of QuickBooks objects this connector can read. It hands each completed stream description to the connector class through `streams_list`.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This method builds the SQL-like query string that QuickBooks expects at its shared `/query` endpoint. It is what turns the system’s idea of “read this stream from this point onward” into a QuickBooks request.

**Data flow**: It receives a stream description, an optional saved cursor timestamp, and the page starting position. It creates a `SELECT * FROM ...` query, adds a `WHERE` and `ORDER BY` clause when incremental syncing is possible, escapes single quotes in the cursor value, and ends with the page size limit. The output is a single query string ready to send to QuickBooks.

**Call relations**: During pagination, `QuickBooksConnector.paginate` calls this method before each request. The query it returns is then sent to QuickBooks so the connector can fetch the next page of records.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads one QuickBooks stream page by page. It keeps asking QuickBooks for up to 100 records at a time until there are no more records to fetch.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced update time. Starting at position 1, it builds a query, sends it to QuickBooks, pulls the records out of `QueryResponse`, and yields each non-empty page to the caller. If a page has fewer than 100 records, it knows it has reached the end. If QuickBooks replies with 401 or 403, it turns that refusal into a `StreamSkipped` error with an explanation; other HTTP errors are allowed to continue upward.

**Call relations**: This is the main read loop used by the connector framework when syncing a QuickBooks stream. It relies on `_build_query` to create each request, uses the inherited `_get` behavior from the REST connector to talk to QuickBooks, and reports permission problems through `StreamSkipped` so the broader sync can skip only the blocked stream.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method makes nested QuickBooks cursor data easier for the sync system to track. In particular, it copies `MetaData.LastUpdatedTime` onto a flat field with that same dotted name.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream uses a dotted cursor field, it reads the nested value from the record and returns a new record that includes that value under the flat cursor key. If there is no dotted cursor field, it returns the record unchanged.

**Call relations**: After records are fetched, the connector framework can call this method before saving or comparing cursor values. It uses `get_path` to safely read the nested timestamp, allowing the rest of the sync machinery to advance its watermark without needing to understand QuickBooks’ nested metadata shape.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `source sync`

Xero is an accounting service, and its API does not return data in one simple universal shape. Each kind of data comes wrapped in a named envelope, such as `Accounts` or `Invoices`. Some collections are split into pages, while others always come back in one response. Xero also uses a special request header, `If-Modified-Since`, to ask for only records changed after a previous sync time.

This file is the adapter between those Xero-specific rules and the project’s general source-sync machinery. It first defines the list of Xero streams the system knows how to read. Each stream says what Xero object to request, what field should act as the record’s main identity, and whether the stream can be synced incrementally by update time.

The `XeroConnector` then opens an HTTP client for Xero, optionally adding the required `xero-tenant-id` header, which tells Xero which organization to read from. During syncing, it requests each stream, follows page numbers where needed, and yields batches of records. If Xero refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole source. Finally, it normalizes record IDs: Xero calls them things like `InvoiceID` or `ContactID`, but the wider system expects a plain `id` field.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a stream description for one kind of Xero data, such as invoices or contacts. It keeps the stream list compact and consistent by filling in the common settings in one place.

**Data flow**: It receives a stream name, the matching Xero API object name, and optional details about update tracking and whether the stream is a main, commonly used one. It turns those inputs into a `StreamSpec`, which is the project’s standard description of a readable data stream, with `id` as the primary key and `UpdatedDateUTC` as the usual update timestamp.

**Call relations**: This helper is used while the file is being loaded to build `XERO_STREAMS`. Those stream descriptions are then attached to `XeroConnector`, so the broader sync runner knows which Xero resources this connector can read.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This converts a saved sync cursor into the date format Xero expects in the `If-Modified-Since` request header. In plain terms, it translates “where we left off last time” into Xero’s preferred timestamp style.

**Data flow**: It receives a cursor value, which may be missing, blank, a numeric Unix timestamp, an ISO-style date string, or already some other date-like text. If it can understand the value, it converts it to a GMT date string such as `Tue, 05 Mar 2024 12:30:00 GMT`; if the value is empty or unusable, it returns nothing, and if an unknown text date cannot be parsed, it passes that text through unchanged.

**Call relations**: When `XeroConnector.paginate` is about to request changed records only, it calls this function to prepare the `If-Modified-Since` header. That lets pagination stay focused on making requests while this helper owns the date-format translation.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This stores the optional Xero tenant ID, which identifies the specific Xero organization to read from. Xero grants can cover more than one organization, so this value can be necessary to point requests at the right one.

**Data flow**: It receives an optional tenant ID when the connector is created. It saves that value on the connector instance so later HTTP client setup can add it to outgoing requests.

**Call relations**: This runs when code creates a `XeroConnector`. Later, `XeroConnector._make_client` reads the saved tenant ID and turns it into the request header Xero requires.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Xero and adds Xero’s tenant header when a tenant ID was provided. The HTTP client is the object that actually sends web requests to the Xero API.

**Data flow**: It receives the base API address and a credential object supplied by the project’s authentication layer. It asks the parent REST connector to build the normal authenticated client, then, if a tenant ID was saved earlier, adds `xero-tenant-id` to the client’s default headers. It returns the prepared client.

**Call relations**: The sync framework calls this when it needs a client for Xero. It builds on the shared REST connector behavior for authentication and base setup, then adds the one Xero-specific header before `XeroConnector.paginate` starts using the client for requests.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Xero stream from the API in batches. It knows which Xero collections use page numbers, which ones do not, and how to ask Xero for only records changed since the last sync.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero path from the stream’s source object, optionally converts the cursor into an `If-Modified-Since` header, then sends GET requests. For non-paged streams, it makes one request and yields the records found inside Xero’s envelope. For paged streams, it requests page 1, page 2, and so on, yielding each batch until Xero returns no records or a short final page. If Xero refuses access with 401 or 403, it turns that into a `StreamSkipped` signal; other HTTP errors are allowed to continue upward.

**Call relations**: The wider sync runner calls this when it is time to fetch records for a particular Xero stream. Inside the function, `_cursor_to_rfc1123` prepares the incremental-sync header, and the HTTP client performs the actual Xero requests. The batches yielded here are then passed onward to the rest of the source pipeline, where records can be flattened, keyed, and stored.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Xero records so the rest of the system can find each record’s identity under the standard field name `id`. Xero uses different ID field names for different resources, such as `InvoiceID`, `ContactID`, or `AccountID`.

**Data flow**: It receives one Xero record and the stream it came from. If the record already has an `id`, it leaves it unchanged. Otherwise, it looks up the correct Xero ID field for that stream, reads that value, and returns a copy of the record with an added string `id`. If no known ID field exists or the value is missing, it returns the original record.

**Call relations**: After `XeroConnector.paginate` yields raw Xero records, the sync machinery can call this to put records into the shape expected by the common stream system. It does not make network calls; it simply translates Xero’s per-resource ID names into the shared convention used downstream.


### Commerce operations
Connectors that pull point-of-sale and commerce data including customers, locations, payments, catalog items, orders, and inventory.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `sync request handling`

Square exposes different kinds of data through different API patterns. This file hides those differences behind one connector, so the rest of the system can simply ask for a named stream like "customers" or "orders" and receive batches of records. Without this file, the project would not know which Square endpoints to call, how to page through long result sets, or how to treat missing permissions gracefully.

The file defines the Square streams the system supports, including each stream’s record name, primary key, and time field used for incremental syncing. Incremental syncing means asking only for records newer than a saved cursor, like resuming a book from the last bookmark instead of starting at page one.

The main class, `SquareConnector`, builds an HTTP client for Square and adds the pinned Square API version header. Its central method, `paginate`, chooses the right reading strategy for each stream. Some streams are simple list endpoints with cursors. Catalog streams use Square’s search endpoint. Orders are searched across all account locations, so the connector first fetches locations and then asks for orders for those location IDs. Inventory counts and locations are fetched as single-shot collections.

If Square replies that access is forbidden or unauthorized, the connector raises `StreamSkipped` instead of crashing the whole sync. That matters because one Square account may not grant every permission, and the system should still sync whatever it can.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Square and adds the required Square API version header. The version header tells Square which version of its API rules this connector expects.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the normal authenticated client, then adds `Square-Version: 2026-04-16` to that client’s headers. It returns the ready-to-use client.

**Call relations**: This is part of connector setup before any stream is read. Later requests made by `paginate` and its helper methods use this client, so every Square API call carries the expected version information.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Square streams. Given a stream name, it chooses the correct Square API pattern and yields records in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the last sync stopped. It checks the stream name, calls the helper suited to that stream, and yields lists of record dictionaries. If Square says the token is invalid or lacks permission, it turns that into `StreamSkipped`; if the stream is unknown, it also skips it with a clear message.

**Call relations**: The wider sync system calls this method when it wants data from a Square stream. `paginate` then hands off to `_locations` for locations, `_cursor_get` for customers/payments/refunds, `_catalog` for catalog items and categories, `_orders` for orders, or directly extracts inventory counts with `records_at` after a POST request.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use a standard cursor-based list endpoint, such as customers, payments, and refunds. A cursor is a continuation marker or saved time value that helps continue reading from the right place.

**Data flow**: It receives the client, the stream description, and an optional cursor. For payments and refunds, it sends the cursor as Square’s `begin_time` filter. It then walks Square’s paged responses through the shared `_get_cursor_pages` helper. For streams that cannot use `begin_time`, it filters records locally so only records newer than the cursor are yielded.

**Call relations**: `paginate` calls this when it sees the customers, payments, or refunds streams. This helper relies on the shared REST paging machinery from the parent connector, then returns only non-empty pages back to `paginate`.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either items or categories and yields them page by page. It uses Square’s catalog search API because catalog data is not read through the same simple list shape as customers or payments.

**Data flow**: It receives the client, the stream description, and an optional saved cursor. It converts the stream name into a Square catalog object type, posts a search request with a page limit, extracts records from the `objects` field, filters by the stream’s time field if needed, yields any records found, then repeats while Square returns another cursor token.

**Call relations**: `paginate` calls this for `catalog_items` and `catalog_categories`. Inside the loop it uses `records_at` to pull the actual list of catalog objects out of Square’s response before handing each page back to the caller.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations matter on their own as a stream, and they are also needed to search for orders because Square order searches must be tied to location IDs.

**Data flow**: It receives the HTTP client, sends a GET request to Square’s `/locations` endpoint, and extracts the list stored under `locations`. It returns that list as ordinary record dictionaries.

**Call relations**: `paginate` calls this directly when syncing the `locations` stream. `_orders` also calls it first so it can build the list of location IDs required for the order search request.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches for Square orders across all available account locations. It first finds the locations, then asks Square for orders belonging to those locations.

**Data flow**: It receives the client and an optional cursor. It calls `_locations`, keeps only valid string location IDs, and stops early if there are none. It then posts to `/orders/search` with those IDs, a page limit, and, when provided, a created-at start time based on the cursor. It yields each non-empty page of orders and follows Square’s returned cursor token until there are no more pages.

**Call relations**: `paginate` calls this when syncing the `orders` stream. `_orders` depends on `_locations` to know where to search, uses `records_at` to extract the `orders` list from each response, and hands each page back to the main sync flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).

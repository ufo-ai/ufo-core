# Finance, billing, accounting, and commerce source connectors  `stage-12.1.9`

This stage is the set of “adapters” that let the sync system read money-related data from outside services. It is used during the main sync work, after the system knows which source to contact. Each file speaks the language of one finance tool, calls that tool’s API, and turns the answers into a steady, common stream of records the rest of the system can store.

Brex reads spend-management information like card transactions, expenses, vendors, budgets, users, and departments. Chargebee and Recurly read subscription billing data such as customers, subscriptions, invoices, items, and payments. QuickBooks and Xero read accounting records, including accounts, contacts, invoices, and payments. Square reads commerce data like customers, locations, catalog items, orders, payments, and inventory. Stripe reads a wide range of payment and billing records, including customers, invoices, subscriptions, checkout sessions, and connected-account data.

Most of these services return results in pages, like search results. These connectors handle that paging and package each page into the project’s standard format.

## Files in this stage

### Spend management ingestion
Brex defines the spend-management endpoints and pagination needed to sync transactions, expenses, users, vendors, budgets, and departments.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `during Brex source sync`

This connector is a read-only bridge between Brex and the rest of the system. Brex is a spend-management service, and its API returns business records like card transactions, expenses, vendors, and budgets. Without this file, the system would not know which Brex URLs to call, how to authenticate through the shared REST connector base, or how to keep asking Brex for the next page of results.

The file starts by listing the Brex streams the system can sync. A stream is one kind of record, like “transactions” or “users.” Each stream is described with a primary key, which is the field used to identify one record, and sometimes a cursor field, which is a date-like field the wider sync system can use as a progress marker. The helper `_stream` keeps those stream descriptions consistent.

The `BrexConnector` class then supplies the Brex base URL and the list of streams. Its main behavior is pagination. Brex returns list results in pages, like a stack of envelopes: each response contains some items and, if more data exists, a `next_cursor` token saying where to continue. `paginate` repeatedly calls the right endpoint with a limit of 100, yields each non-empty batch of records, and stops when Brex no longer provides a cursor. This connector intentionally only reads data; it does not create or update anything in Brex.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard description of one Brex data stream. It is used so each stream, such as expenses or transactions, can be declared in a compact and consistent way.

**Data flow**: It receives a stream name plus optional details like the primary key, cursor field, and whether the stream is considered canonical. It packages those choices into a `StreamSpec`, which is the shared object the sync system uses to understand what records exist and how to identify them.

**Call relations**: This function is used while the file defines `BREX_STREAMS`, the catalog of Brex record types available for syncing. It hands its settings to `StreamSpec.__init__`, which creates the stream description consumed later by `BrexConnector`.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads all pages for one Brex stream from the Brex API. Someone would use it when syncing a stream and needing every available record, not just the first page.

**Data flow**: It receives an HTTP client, a stream description, and a cursor argument. It looks up the correct Brex endpoint for that stream, requests up to 100 records at a time, converts the response’s `items` field into a safe list with `list_or_empty`, yields each non-empty batch, then follows Brex’s `next_cursor` until there are no more pages. If the stream has no known endpoint, it raises an error instead of guessing.

**Call relations**: The wider REST source framework calls this method when it needs records from Brex. Inside the loop, this method relies on the inherited `_get` request helper to fetch data from Brex, then uses `ufo.sdk.sources.list_or_empty` to avoid problems if the response has no usable `items` list. It hands batches of raw Brex records back to the sync framework as they arrive.

*Call graph*: 1 external calls (list_or_empty).


### Billing and accounting platforms
These connectors read recurring-billing and accounting records from paged finance APIs and normalize them into syncable record streams.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `sync run`

Chargebee is a subscription billing service, and its API exposes many different kinds of records. This file is the read-only connector for that API. Without it, the system would not know which Chargebee endpoints exist, how to authenticate, how to walk through pages of results, or how to reshape Chargebee’s nested response format into normal records.

The file first defines the streams the connector can read. A stream is one kind of data, such as `customer` or `invoice`. Most streams map directly to one Chargebee list endpoint. A few are substreams: they only exist underneath a parent record. For example, contacts are fetched by first reading customers, then asking Chargebee for each customer’s contacts.

Chargebee returns list responses in a repeated shape: a `list` of wrapped records plus a `next_offset` token for the next page. This connector follows that token until there are no more pages. For incremental syncs, it can add a filter like “updated after this cursor,” so later runs do not have to reread everything.

Authentication uses HTTP Basic authentication with the API key as the username. The connector also respects a provided custom transport, which lets an auth broker or proxy take over the actual network path. If Chargebee refuses access with a 401 or 403 status, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a `StreamSpec`, which is the small description the sync system uses to know what a Chargebee stream is called, what field identifies each record, and what timestamp can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a `StreamSpec` object that the connector later uses when fetching and normalizing records.

**Call relations**: This helper is used while the module is being loaded to build the connector’s full list of Chargebee streams. It hands the finished stream description to `StreamSpec.__init__`, which creates the object used throughout the rest of the connector.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to Chargebee. It sets the base address, timeouts, headers, and authentication so later code can simply request Chargebee paths.

**Data flow**: It takes a base URL and a resolved credential. It trims the URL, prepares JSON/form headers, and sets connection and read timeouts. If the credential supplies a custom transport, it builds a client around that transport. If the credential supplies a direct API key, it uses that key as the username in HTTP Basic authentication. If neither is present, it raises an error because it cannot safely contact Chargebee.

**Call relations**: The broader connector framework calls this when a sync needs a network client. Internally it relies on `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient` to create the actual asynchronous HTTP client.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function removes Chargebee’s extra wrapper around each record so the rest of the system sees a plain record. For example, it turns a response shaped like `{customer: {...}}` into just the customer fields.

**Data flow**: It receives one raw record and the stream description. It looks for a nested object whose key matches the stream’s source object. If that nested object is a dictionary, it copies its fields outward and preserves any useful top-level fields, such as a parent ID added by a substream. If there is no expected wrapper, it returns the record unchanged.

**Call relations**: After pages are fetched, the connector framework can call this before storing or passing records onward. It is especially important for substreams, because those records may have a parent ID stamped on the outside that must not be lost when the Chargebee envelope is removed.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading records from a Chargebee stream. It decides whether a stream can be read directly from a list endpoint or needs special parent-by-parent fetching.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. Based on the stream name, it delegates to the right paginator and yields pages of raw records. If the stream is unknown, it raises an implementation error. If Chargebee replies with 401 or 403, it converts that into `StreamSkipped` with a clear explanation that the key or permission scope is not enough.

**Call relations**: The sync framework calls this when it wants pages for a particular stream. This function then calls `_paginate_list` for ordinary streams, or one of `_paginate_attached_items`, `_paginate_contacts`, `_paginate_quote_line_groups`, and `_paginate_subscription_scheduled` for substreams that need a parent loop.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, adds the “only records after this cursor” filter for incremental syncing.

**Data flow**: It receives a stream description and an optional cursor value. It always starts with a `limit` of 100 records. If a cursor exists and the stream has a cursor field, it adds a Chargebee-style filter such as `updated_at[after]=...`. It returns the completed parameter dictionary.

**Call relations**: `_paginate_list` calls this right before it asks Chargebee for list pages. This keeps cursor and page-size rules in one place instead of repeating them for every stream.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a standard Chargebee list endpoint, page by page. It is the common path for streams like customers, invoices, subscriptions, and transactions.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for that stream, asks `_build_list_params` for the request parameters, then follows Chargebee’s `next_offset` cursor through all pages. Each page of records is yielded to the caller.

**Call relations**: `paginate` calls this for ordinary streams. The substream paginators also call it first to find their parent records, such as items before attached items or customers before contacts.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads attached items, which Chargebee exposes underneath individual items rather than as one global list. It first finds items, then fetches each item’s attached items.

**Data flow**: It receives an HTTP client and an optional cursor. It uses `_paginate_list` to read item pages, extracts each item ID, skips parents without an ID, and then asks `_paginate_substream` to fetch `/items/{item_id}/attached_items`. The child records come out in pages with the parent `item_id` added.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This function depends on `_paginate_list` for the parent item walk and `_paginate_substream` for the repeated child-page logic.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads customer contacts, which are stored under each customer in Chargebee. It works like going through an address book one customer at a time and copying out that customer’s contacts.

**Data flow**: It receives an HTTP client and an optional cursor. It reads customer pages through `_paginate_list`, extracts each customer ID, skips any customer that cannot be identified, and then fetches `/customers/{customer_id}/contacts` through `_paginate_substream`. The resulting contact records are yielded with `customer_id` stamped onto them.

**Call relations**: `paginate` calls this for the `contact` stream. It uses `_paginate_list` to find parent customers and `_paginate_substream` to fetch and label each customer’s child contact records.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads quote line groups, which Chargebee exposes underneath individual quotes. It first gathers quotes, then asks for the line groups belonging to each one.

**Data flow**: It receives an HTTP client and an optional cursor. It uses `_paginate_list` to read quote pages, extracts each quote ID, skips parents without an ID, and calls `_paginate_substream` for `/quotes/{quote_id}/quote_line_groups`. It yields pages of child records with the parent `quote_id` included.

**Call relations**: `paginate` calls this when reading the `quote_line_group` stream. Like the other substream readers, it combines `_paginate_list` for parent discovery with `_paginate_substream` for fetching labeled child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads the special “subscription with scheduled changes” view for each subscription. Chargebee provides this as a single detail endpoint per subscription, not as a normal list.

**Data flow**: It receives an HTTP client and an optional cursor. It reads subscription pages through `_paginate_list`, extracts each subscription ID, skips missing IDs, then requests `/subscriptions/{id}/retrieve_with_scheduled_changes` for each subscription. If the response contains a subscription object, it yields a one-record page containing that object plus the original `subscription_id`.

**Call relations**: `paginate` calls this for the `subscription_with_scheduled_changes` stream. It uses `_paginate_list` to discover subscriptions, then performs one detail fetch per parent subscription before handing the result back as stream pages.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This shared helper reads a child endpoint that uses the same Chargebee paging shape as normal lists. It also adds the parent ID to every child record so the relationship is not lost.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent-ID field to add, and the actual parent ID value. It fetches pages using Chargebee’s `list` and `next_offset` pattern, copies each dictionary record, adds the parent ID field, and yields only non-empty stamped pages.

**Call relations**: `_paginate_attached_items`, `_paginate_contacts`, and `_paginate_quote_line_groups` call this after they have found a parent record. This keeps the repeated child-pagination behavior in one place, while the parent-specific functions only decide which parent IDs and paths to use.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `source sync / request handling`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each kind of data. Instead, every read is sent as a SQL-like query to one shared `/query` endpoint. This file hides that awkwardness behind a connector, so the rest of the system can treat QuickBooks like a set of normal streams such as accounts, customers, invoices, bills, and payments.

The file first defines a helper, `_stream`, which describes one type of QuickBooks data: its public stream name, the QuickBooks object name, its primary key, and the field used to tell what changed since the last sync. Most streams use `MetaData.LastUpdatedTime` as the “cursor,” meaning the bookmark used to resume later without rereading everything. A few reference lists, such as payment methods and tax agencies, do not use that cursor and are reread fully.

`QuickBooksConnector` then provides the actual reading behavior. For each stream, it builds a QuickBooks query, asks for up to 100 records, yields those records, and moves to the next page until QuickBooks returns fewer than 100. If QuickBooks refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole connector. Finally, `flatten` copies the nested cursor value onto a flat key, so the common sync machinery can easily compare and store the latest update time.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the standard description for one QuickBooks stream, such as invoices or vendors. It keeps the long list of supported QuickBooks objects consistent, so each stream uses the same primary key and timestamp fields unless it explicitly opts out.

**Data flow**: It receives a friendly stream name, the matching QuickBooks object name, an optional cursor field, and a flag saying whether the stream is considered canonical. It puts those details into a `StreamSpec`, which is the system’s small recipe for how to sync that kind of record, and returns that recipe for the connector’s stream list.

**Call relations**: This function is used while the file is loaded to build `QUICKBOOKS_STREAMS`. Its main handoff is to `StreamSpec.__init__`, which receives the stream settings and turns them into the structured stream definition used later by the connector.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This function writes the SQL-like query string that QuickBooks Online expects. It is used whenever the connector needs to request one page of records, optionally only asking for records updated after the last saved cursor.

**Data flow**: It takes a stream description, a cursor value if one exists, and the page’s starting position. It starts with `SELECT * FROM <QuickBooks object>`, adds a `WHERE` clause and ordering when incremental syncing is possible, escapes apostrophes in the cursor so the query stays valid, then appends the page size and starting offset. The output is a single query string ready to send to QuickBooks.

**Call relations**: `QuickBooksConnector.paginate` calls this every time it needs the next page. `_build_query` does not contact QuickBooks itself; it only prepares the request text that `paginate` sends through the connector’s HTTP helper.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous generator reads one QuickBooks stream page by page. It turns QuickBooks’ query endpoint into a simple stream of record batches for the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. Starting at position 1, it builds a query, sends it to `/query`, looks inside `QueryResponse` for the records belonging to the requested QuickBooks entity, and yields each non-empty batch. If the batch is smaller than 100 records, it knows there are no more pages and stops. If QuickBooks returns 401 or 403, it converts that refusal into `StreamSkipped`; other HTTP errors are allowed to keep failing normally.

**Call relations**: During a sync, the connector framework calls this function to retrieve records for a stream. Inside the loop it relies on `QuickBooksConnector._build_query` to create the QuickBooks query. When access is refused, it hands a clear reason to `StreamSkipped.__init__`, which tells the larger sync process that this stream should be skipped rather than treated as a successful read.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function makes QuickBooks records easier for the shared sync code to track. In particular, it lifts the nested update timestamp `MetaData.LastUpdatedTime` onto a flat top-level key with the same name.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream’s cursor field is a dotted path, meaning the value lives inside nested data, it reads that nested value with `get_path` and returns a copy of the record with an added flat cursor key. If there is no nested cursor to lift, it returns the record unchanged.

**Call relations**: The connector framework uses this after records are fetched so the common cursor-tracking code can find the update time without understanding QuickBooks’ nested shape. It delegates the nested lookup to `ufo.sdk.sources.get_path`, which walks through the record using the dotted field path.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during source sync`

Recurly returns list results in pages, like a long paper report split across many envelopes. Each envelope contains some records, a note saying whether more pages exist, and a pointer to the next page. This file is the Recurly source connector: it opens the right HTTP client, asks Recurly for each supported stream, follows those next-page pointers, and yields batches of records to the rest of the sync system.

It defines the available streams, such as accounts, subscriptions, invoices, plans, coupons, and related child resources. Some streams are simple top-level lists. Others live under a parent record, such as notes under each account or unique coupon codes under each bulk coupon. For those, the connector first lists the parents, then visits each parent’s child endpoint and adds the parent id to every child row so the relationship is not lost.

The connector also supports incremental syncing. When a stream has a cursor field, meaning a timestamp used to resume from a previous point, the first request includes that time so Recurly only returns newer or changed data. Authentication is Recurly-specific: the API key is sent as the username for HTTP Basic authentication, unless the platform provides a proxy transport for the secret. If Recurly refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Recurly resource. A stream description tells the sync system the stream’s name, where to fetch it from, what field identifies each row, and what timestamp field can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as the source endpoint name, primary key, cursor field, and whether the stream is considered canonical. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses when deciding what to request from Recurly.

**Call relations**: This is used while the module is being loaded to build the RECURLY_STREAMS list. It hands the normalized stream settings to StreamSpec so the RecurlyConnector can advertise all supported streams to the wider source framework.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It sets Recurly’s required API version header, request timeouts, and the correct authentication method.

**Data flow**: It takes a base URL and a credential. It trims the base URL, prepares headers and time limits, then either uses a provided proxy transport or turns the API key into HTTP Basic authentication. It returns an asynchronous HTTP client ready to make Recurly API calls, or raises an error if no usable credential is present.

**Call relations**: The connector framework calls this when it needs a network client for Recurly. Inside, it relies on httpx objects for the client, timeout, and Basic authentication, so the rest of the connector can simply make requests without rebuilding those settings each time.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s next-page link into a path the client can request. It accepts both full URLs and plain paths, because APIs sometimes return either form.

**Data flow**: It receives a next link, which may be missing, a full URL, or just a path. If the link is empty, it returns nothing. If it is a full URL, it strips off the scheme and host but keeps the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: All pagination loops use this after reading Recurly’s next value. Top-level pagination, account listing, coupon listing, and child-resource pagination call it so they can keep following pages using a safe relative path.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the first set of query parameters for a Recurly list request. It controls page size, sort order, and the optional starting timestamp for incremental syncs.

**Data flow**: It receives a stream description and an optional cursor value from a previous sync. It builds a parameter dictionary with the maximum page size used by this connector, ascending order, and a sort field. If the stream has a cursor field and a cursor was provided, it also adds begin_time so Recurly starts from that point.

**Call relations**: Top-level and per-parent pagination both call this before their first request. After the first page, Recurly’s own next link carries the paging information, so the pagination functions stop reusing these initial parameters.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing function for reading a Recurly stream. It decides whether a stream should be fetched directly, fetched as child records under parent records, or filtered in a special way.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It chooses the right pagination path, then yields pages of records as they arrive. If Recurly returns an authorization refusal, it converts that into a StreamSkipped error with a useful message; other HTTP errors are allowed to continue upward.

**Call relations**: The broader sync framework calls this when it wants records for one Recurly stream. This function then hands off to _paginate_top_level for ordinary lists or _paginate_per_parent for child resources. For the unique_coupons_parent stream, it calls top-level coupon pagination and only yields bulk coupons, because only those have unique coupon codes beneath them.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This walks through a normal Recurly list endpoint, one page at a time. It is used for resources such as accounts, subscriptions, invoices, and coupons.

**Data flow**: It receives a client, stream description, endpoint path, and optional cursor. It sends the first request with initial query parameters, yields any records returned, then follows Recurly’s next link until there are no more pages. Each output is a list of record dictionaries from one page.

**Call relations**: The public paginate method calls this for top-level streams and for the special coupon-parent filtering path. It uses _initial_query to form the first request and _next_path to turn Recurly’s next-page link into the next request path.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This lists account ids from Recurly. It exists so child streams under accounts, such as account notes or billing info, can be fetched account by account.

**Data flow**: It starts at the /accounts endpoint with a simple ordered query. For each page, it reads the account rows, extracts each non-empty id, and yields ids one at a time as strings. It follows next-page links until Recurly says there are no more accounts.

**Call relations**: _paginate_per_parent calls this when it needs parent ids for account-based child streams. This function uses _next_path to keep moving through the account pages before handing each id back to the child pagination loop.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This lists ids for bulk coupons only. Bulk coupons are the coupon type that can have unique coupon codes beneath them, so other coupon types are ignored.

**Data flow**: It starts at the /coupons endpoint and reads coupon pages in order. For each row, it checks that the row is a dictionary, has an id, and has coupon_type set to bulk. Only then does it yield the coupon id as a string. It keeps following next-page links until the coupon list is finished.

**Call relations**: _paginate_per_parent calls this when the child stream lives under coupons rather than accounts. It uses _next_path for Recurly pagination and supplies only the parent coupon ids that can actually have child unique coupon code records.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This fetches streams that are nested under another Recurly object. For example, it can fetch notes for every account or unique coupon codes for every bulk coupon.

**Data flow**: It receives the client, stream description, parent endpoint, child endpoint name, the field used to store the parent id, and an optional cursor. It first gets parent ids from either _account_ids or _coupon_ids. For each parent id, it requests that parent’s child endpoint, follows all child pages, stamps the parent id onto each child row if it is a dictionary, and yields each non-empty page of child records.

**Call relations**: The main paginate method calls this for streams listed as per-parent streams. This function coordinates several helpers: it asks _account_ids or _coupon_ids for the parent list, uses _initial_query for the first child request under each parent, and uses _next_path to continue through child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### Commerce and payment platforms
Square and Stripe cover commerce, payment, subscription, invoice, and marketplace-style records with connector-specific pagination handling.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `during source sync when reading Square streams`

Square exposes different kinds of data in different ways. Some lists are fetched with a simple web request and a cursor, some require a search request, and orders must first be tied to Square locations. This file hides those differences behind one connector, so the rest of the project can ask for a stream like “payments” or “orders” without knowing Square’s exact web API rules.

The main class, SquareConnector, is a read-only connector. It creates an HTTP client for Square, adds Square’s required API version header, and defines the streams the system can recall later. A stream is a named collection of records, like customers or catalog_items, with hints about IDs and time fields used for incremental syncing.

The central method is paginate. It acts like a dispatcher: it looks at the requested stream name and chooses the right fetching method. Simple streams use cursor-based GET requests. Catalog streams use Square’s catalog search endpoint. Orders are searched across all known locations. Locations and inventory counts are fetched as single batches.

If Square rejects access with a 401 or 403 response, the connector does not crash the whole sync. It raises StreamSkipped, meaning “this stream cannot be read with the current credentials or permissions.” This matters because one missing Square permission should not necessarily stop every other available stream from syncing.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the web client used to talk to Square and adds the required Square API version header. Square expects that header so it knows which version of its API rules to apply.

**Data flow**: It receives a base URL and a credential. It first asks the parent RestConnector to create the normal authenticated HTTP client, then adds the Square-Version header with the pinned version value. It returns the prepared client, ready to make Square API calls.

**Call relations**: This is part of the connector setup before any stream is read. Later fetching methods use the client it prepares, so every request sent by paginate and its helper methods carries the correct Square API version.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Square stream. Given a stream name, it chooses the correct Square API pattern and yields records in pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks the last synced point. It checks the stream name, calls the matching helper, and yields each non-empty page of records. If the stream is unsupported or Square refuses access with an authorization error, it turns that into StreamSkipped; other HTTP errors are allowed to continue upward.

**Call relations**: The sync runner calls this when it wants records for one Square stream. paginate then hands work to _locations, _cursor_get, _catalog, or _orders depending on the stream. For inventory counts it makes the batch request itself and uses records_at to pull the list of counts out of Square’s response.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use a normal cursor-style list endpoint, such as customers, payments, and refunds. A cursor is like a bookmark that tells the next request where to continue.

**Data flow**: It receives the HTTP client, the stream description, and an optional previous cursor. For payments and refunds, it sends the cursor as Square’s begin_time filter. For other streams, it fetches pages and then locally keeps only records newer than the cursor field. It yields each page that still has records after filtering.

**Call relations**: paginate calls this for customers, payments, and refunds. It relies on the shared RestConnector page-walking helper to follow Square’s next-page cursor, then gives cleaned pages back to paginate to pass along to the sync system.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for one kind of object, either items or categories, and returns them page by page. It exists because catalog data is fetched through a search POST request rather than a simple list endpoint.

**Data flow**: It receives the HTTP client, the catalog stream description, and an optional cursor from the last sync. It translates the stream name into Square’s object type, sends a search request with a page limit, reads the objects from the response, filters out older records when a cursor is present, and yields the remaining records. It repeats while Square provides another cursor token.

**Call relations**: paginate calls this for catalog_items and catalog_categories. Inside the loop it uses records_at to extract the objects list from Square’s response, then returns pages to paginate until Square says there are no more pages.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations are useful both as their own stream and as the starting point for order searches, because Square orders are tied to locations.

**Data flow**: It receives the HTTP client, sends a GET request to Square’s locations endpoint, and extracts the locations list from the response. It returns that list as plain record dictionaries.

**Call relations**: paginate calls this directly when syncing the locations stream. _orders also calls it first so it can gather location IDs before searching for orders across those locations.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders by searching across all Square locations on the account. It exists because Square’s order search needs location IDs before it can return matching orders.

**Data flow**: It receives the HTTP client and an optional cursor. It first fetches locations, keeps only valid string location IDs, and stops if there are none. It then sends repeated order search requests with those location IDs, an optional created-at start time based on the cursor, and Square’s continuation cursor when present. Each response is unpacked into order records and yielded page by page until no next cursor remains.

**Call relations**: paginate calls this when the requested stream is orders. _orders depends on _locations to discover where to search, uses records_at to pull the orders list from each response, and hands each page back to paginate for the larger sync flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `during Stripe source sync`

Stripe exposes many separate lists of business data, but they mostly follow the same pattern: ask for up to 100 records, then ask for the next page using the last record’s id. This file captures that pattern once and reuses it across dozens of Stripe streams. Without it, the project would not know which Stripe endpoints exist, how to walk through all their pages, or how to fetch nested lists like an invoice’s line items or a customer’s payment methods.

The file starts by declaring the Stripe streams the connector supports. A stream is a named collection of records, with hints such as its main id field and the date field used for incremental syncing. The connector then adds Stripe-specific behavior: it pins the Stripe API version in a request header, builds endpoint paths, converts saved cursor values into Unix timestamps when Stripe expects them, and normalizes timestamp fields into readable date strings.

The main flow is like a delivery route. For simple streams, it drives straight down one list endpoint. For child streams, it first visits each parent record, then asks Stripe for that parent’s children and stamps the parent id onto each child row so the relationship is not lost. If Stripe refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 89–107)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates a stream description for one Stripe collection. It saves repeated boilerplate, so each supported Stripe object can be declared in a short, readable way.

**Data flow**: It receives a stream name and optional details such as the Stripe endpoint name, primary key, cursor field, and date fields. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses to know what to request and how to label the records.

**Call relations**: This function is used while the file is being loaded to build the STRIPE_STREAMS list. It hands its settings to StreamSpec, which is the shared stream description type used by the source framework.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 182–185)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Stripe and adds the Stripe API version header. Pinning the version keeps Stripe responses stable even if Stripe changes its default behavior later.

**Data flow**: It receives a base URL and a credential object. It asks the parent RestConnector to make the normal authenticated client, adds the Stripe-Version header, and returns the ready-to-use client.

**Call relations**: The broader source framework calls this when it needs a client for this connector. After this point, all Stripe requests made by pagination methods use a client that carries the correct API version.


##### `StripeConnector._list_path`  (lines 188–189)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This builds the standard Stripe list endpoint path for a stream. For example, a stream whose source object is customers becomes the path /v1/customers.

**Data flow**: It receives a StreamSpec, reads its source_object value, and returns the matching Stripe API path as text. It does not make any network request itself.

**Call relations**: The main paginate flow uses this for ordinary streams. The child-stream helpers also use it when they need to list parent records before fetching the children.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 192–204)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved sync cursor into the Unix timestamp format Stripe expects for created-date filters. A cursor is the bookmark that says, “resume from here next time.”

**Data flow**: It receives a cursor value as text or None. If there is no cursor, it returns None. If the value is already digits, it returns it as an integer. If it looks like an ISO date string, it parses it and returns the matching Unix timestamp. If it cannot understand the value, it returns None.

**Call relations**: The shared page loop calls this before making requests. Its result decides whether _page_loop can add a created[gte] filter so Stripe only returns records created after the saved point.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 206–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Stripe stream. It chooses the right fetching strategy depending on whether the stream is a simple list, a child list, a query-based child list, or an external-account list.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It routes to the correct pagination helper, yields pages of records as they arrive, and translates Stripe 401 or 403 permission failures into a StreamSkipped signal with a clear explanation.

**Call relations**: The source framework calls this when it wants records for a Stripe stream. paginate then hands the work to _page_loop for normal streams, to _paginate_substream for path-based child streams, to _paginate_substream_query for query-based child streams, or to _paginate_external_accounts for connected-account external accounts.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 234–265)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable engine for walking through Stripe’s paginated list responses. It keeps asking for the next page until Stripe says there are no more records.

**Data flow**: It receives a client, an endpoint path, a stream description, an optional cursor, and optional extra query parameters. It builds request parameters such as limit, starting_after, date filters, and stream-specific filters, requests a page from Stripe, normalizes each record, yields non-empty pages, and updates the next-page marker from the last record id.

**Call relations**: Most other methods rely on this as their common page reader. paginate uses it directly for simple streams, while the substream helpers call it for both parent lists and child lists.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 268–288)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly cleans up one Stripe record before the rest of the system sees it. Its main job is to turn numeric Stripe timestamps into readable UTC date strings in created_at and updated_at fields.

**Data flow**: It receives one record and the stream description. It copies the record, checks timestamp-like fields, fills created_at from created when needed, fills updated_at from the stream cursor when suitable, and returns the normalized copy without changing the original record.

**Call relations**: _page_loop calls this for every record it receives from Stripe. That means all pagination paths benefit from the same timestamp cleanup before records are yielded onward.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 290–312)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that live under a parent record’s URL, such as line items under an invoice or payment methods under a customer. It preserves the link back to the parent by adding the parent id to each child row.

**Data flow**: It receives a client and a child stream description. It looks up the parent stream and child path template, reads parent pages, loops through each parent id, fetches that parent’s child pages, adds parent id fields and any configured parent timestamp fields to each child record, and yields the enriched child pages.

**Call relations**: paginate calls this for streams listed as path-based substreams. It uses _stream_spec to find the parent stream, _parent_pages to read parents, and _page_loop to read each child collection.

*Call graph*: calls 3 internal fn (_page_loop, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 314–319)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This supplies pages of parent records for child-stream fetching. It hides whether the parent itself is a normal stream or another query-based substream.

**Data flow**: It receives a client and a parent stream description. If that parent is query-based, it returns the query-substream paginator; otherwise it returns the normal page loop for the parent’s list endpoint.

**Call relations**: _paginate_substream calls this when it needs parent records before fetching children. This is especially important for deeper chains, where a stream such as usage records depends on subscription items, which themselves are fetched through subscriptions.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 321–335)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that Stripe fetches by passing a parent id as a query parameter instead of placing the parent id in the URL path. An example is asking for subscription items with subscription=<id>.

**Data flow**: It receives a client and a query-based child stream. It finds the parent stream, lists parent records, takes each parent id, requests child pages with that id in the query parameters, adds a parent-id field to each child record, and yields those pages.

**Call relations**: paginate calls this directly for query-based child streams. _parent_pages can also call it when another child stream needs a parent collection that is itself query-based.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 337–350)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe connected accounts. These need a special route because Stripe asks for external accounts under each account and filters the object type separately.

**Data flow**: It receives a client and an external-account stream description. It lists Stripe accounts, takes each account id, requests that account’s external_accounts endpoint, lets _page_loop apply the stream’s object filter, adds account_id to every returned row, and yields the resulting pages.

**Call relations**: paginate calls this for external_account_bank_accounts and external_account_cards. It uses _stream_spec to find the accounts stream, _list_path to build the account list endpoint, and _page_loop for both listing accounts and reading each account’s external accounts.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 352–353)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the stream description with a given name. It is a small lookup helper used when one stream needs to know about another stream, such as a child stream finding its parent.

**Data flow**: It receives a stream name, searches the STRIPE_STREAMS list, and returns the matching StreamSpec. If no stream matches, the normal Python lookup behavior would raise an error.

**Call relations**: The substream and external-account paginators call this when they need the definition of a parent stream. That lets those methods reuse the same stream catalog instead of duplicating endpoint details.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Accounting ledger ingestion
Xero completes the finance connector set by reading accounting entities such as accounts, invoices, contacts, and payments into standard stream records.

### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `sync request handling`

Xero is an accounting service, and its API has a few habits that are different from many other services. This file is the adapter that knows those habits. It lists the Xero resources the system can read, gives each one a standard name, and explains which field should be treated as the record’s ID.

The main class, XeroConnector, is read-only. It does not create or update anything in Xero. Its job is to ask Xero for pages of records and pass them back in a shape the rest of the platform understands.

Several details matter here. Xero wraps each list of records inside a named envelope, such as returning accounts under an "Accounts" key. Most larger resources use page numbers, with 100 records per page, while some smaller resources return everything at once. For incremental syncs, Xero does not use a query parameter like "updated_since". Instead, it expects an HTTP header called "If-Modified-Since", so this file converts saved cursor times into the date format Xero expects.

Xero also identifies records with different field names, such as "InvoiceID" or "AccountID". The flatten step copies that typed ID into a common "id" field. Without this connector, the rest of the system would not know how to page through Xero, ask only for changed records, or recognize records consistently.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Xero data stream, such as invoices or contacts. It saves repeated setup work by filling in common choices like the primary key field and the updated-time field.

**Data flow**: It receives a stream name, the Xero response envelope name, an optional cursor field, and whether the stream is considered canonical. It packages those choices into a StreamSpec object, which is the sync system’s recipe for reading that stream.

**Call relations**: This helper is used while the file is loaded to build the XERO_STREAMS list. Each call produces one stream recipe, and those recipes are later exposed through XeroConnector.streams_list so the broader sync system knows what Xero resources are available.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved sync cursor into the date format Xero expects for "If-Modified-Since" requests. That lets the connector ask Xero for records changed after a previous sync point.

**Data flow**: It starts with a cursor value, which may be missing, blank, a Unix timestamp number, an ISO-style date string, or already some other text. If it can understand the value as a time, it converts it to UTC and formats it like "Tue, 15 Nov 1994 08:12:31 GMT". If the value is empty or impossible to convert safely, it returns nothing; if it is an unrecognized date string, it passes the original text through.

**Call relations**: XeroConnector.paginate calls this when it is doing an incremental sync for a stream that has a cursor field. The converted value is placed into the request header that Xero uses to filter by modification time.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This sets up a Xero connector instance with an optional tenant ID. In Xero, a tenant ID means the particular organization or company file being accessed under an authorization grant.

**Data flow**: It receives an optional tenant ID string and stores it on the connector. It does not contact Xero or validate the value at this point; it simply keeps the information for later HTTP requests.

**Call relations**: This runs when code creates a XeroConnector. Later, XeroConnector._make_client reads the stored tenant ID and, if present, adds it to the outgoing request headers so Xero knows which organization to use.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Xero and adds Xero’s required tenant header when a tenant ID was provided. The HTTP client is the object that actually sends web requests.

**Data flow**: It receives the API base URL and a credential object supplied by the platform’s authentication proxy. It asks the parent RestConnector to build the normal authenticated client, then adds a "xero-tenant-id" header if this connector has one stored. It returns the prepared client.

**Call relations**: The broader RestConnector flow calls this when it needs a client for Xero API calls. This method relies on the parent connector for the common authentication setup, then adds the Xero-specific tenant choice before paginate uses the client to fetch data.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for Xero streams. It fetches records from Xero, one page or collection at a time, and yields batches of records for the sync system to process.

**Data flow**: It receives an HTTP client, a stream recipe, and an optional cursor from a previous sync. It builds the Xero path from the stream’s source object, optionally converts the cursor into an "If-Modified-Since" header, then sends GET requests. For non-paged streams it requests the collection once. For paged streams it starts at page 1 and keeps requesting the next page until Xero returns no records or fewer than 100 records. It yields each non-empty batch of records. If Xero returns 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped so the system can skip that stream rather than treat it like an ordinary crash.

**Call relations**: The sync engine calls this when it wants records for a particular Xero stream. Inside the loop it calls _cursor_to_rfc1123 to prepare incremental-sync headers and uses httpx.AsyncClient.get to make each web request. If Xero refuses access to a stream, it creates a StreamSkipped error with a clear message for the surrounding sync flow.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Xero records so they have a common "id" field. That matters because the rest of the sync system expects every record to have the same primary key name, while Xero uses names like "ContactID" and "InvoiceID".

**Data flow**: It receives one record and the stream it came from. If the record already has "id", it returns it unchanged. Otherwise it looks up the Xero-specific ID field for that stream, reads the value, and returns a copy of the record with "id" added as a string. If there is no known ID field or the value is missing, it leaves the record unchanged.

**Call relations**: After paginate has produced raw Xero records, the connector framework can call flatten to put each record into the platform’s expected shape. It uses the stream name to choose the right Xero ID field from this file’s override table.

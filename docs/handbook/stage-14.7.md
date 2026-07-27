# Finance, billing, and commerce source connectors  `stage-14.7`

This stage is the system’s set of “cash register and ledger” connectors. It is used during syncing, when the system reaches out to outside services and brings back financial or commercial records in a standard shape. Each file is a translator for one service’s web API, meaning the online doorway the service provides for software to request data.

The Brex connector reads spend data like transactions, expenses, vendors, budgets, and departments. Chargebee and Recurly focus on subscription billing, turning customers, subscriptions, invoices, and payments into record streams. QuickBooks and Xero cover accounting data, such as accounts, contacts, invoices, and payments. Square reads commerce data like customers, orders, catalog items, locations, payments, and inventory. Stripe handles a wide range of payment and billing objects, including customers, charges, invoices, subscriptions, and connected accounts.

Together, these connectors do the same basic job: authenticate, ask the right endpoint for each kind of object, follow paged results, and feed clean batches of records into the rest of the sync system.

## Files in this stage

### Brex spend management
Defines the Brex source connector for syncing spend-management records such as transactions, expenses, users, vendors, budgets, and departments.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `during source sync when fetching Brex records`

Brex is an external service, so the rest of the system needs a small adapter that knows Brex’s rules. This file is that adapter. It says: “these are the Brex data streams we can read, these are their API paths, and this is how to keep asking for the next page until there is no more data.”

The file defines six streams that match common Brex objects: budgets, departments, expenses, transactions, users, and vendors. A stream is a named kind of record the sync system can fetch. Some streams also name a date field, such as `purchased_at` for expenses, so the wider system can treat that field as a progress marker. Most Brex endpoints do not let this connector ask only for records changed since a date, so it reads full lists and lets the rest of the system decide what is new or important.

The `BrexConnector` class provides the Brex base web address and a `paginate` method. Brex returns lists in pages, like a book with a “next page” bookmark called `next_cursor`. The connector requests 100 records at a time, yields each non-empty batch, then follows `next_cursor` until Brex says there are no more pages. It only reads from Brex; there is no code here to create or update Brex data.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds the description of one Brex data stream, such as transactions or vendors. It keeps the stream definitions short and consistent, so each stream can say what its record ID is and whether it has a date field used as a progress marker.

**Data flow**: It receives a stream name plus optional details like the primary key, cursor date field, and whether the stream is canonical. It packages those details into a `StreamSpec`, which is the system’s standard description of a readable source stream, and returns that object.

**Call relations**: This helper is used while the module is being loaded to build the `BREX_STREAMS` list. It hands each completed stream description to the connector class through that list, so later sync code knows which Brex streams exist and how to identify records in them.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Brex stream from the Brex API, one page at a time. Someone uses it when they want all records for a stream without having to know Brex’s cursor-based paging format.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor argument. It looks up the Brex API path for that stream, then repeatedly sends requests with a page size of 100 and, after the first page, the `next_cursor` returned by Brex. Each response’s `items` field is turned into a safe list with `list_or_empty`; non-empty lists are yielded to the caller. The method stops when Brex no longer returns a `next_cursor`, and it raises an error if the stream has no known endpoint path.

**Call relations**: The broader source-sync machinery calls this method when it needs records from Brex. `paginate` relies on the base REST connector’s `_get` helper to perform the actual web request, then uses `list_or_empty` to normalize the response before handing each batch of records back to the sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### Chargebee subscription billing
Defines the Chargebee source connector for streaming subscription-billing entities and related child records from paged API responses.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `during a Chargebee sync run, while fetching pages from the API`

Chargebee is an online billing system, and its API returns data in a very regular but slightly wrapped shape: each page has a list of records, and each record is tucked inside an envelope named after the resource. This file is the adapter that knows those Chargebee rules. Without it, the system would not know which Chargebee endpoints to call, how to authenticate, how to move from page to page, or how to unwrap records into a usable shape.

The file first defines the available streams, which are the kinds of Chargebee data that can be synced. Some are ordinary lists, like customers or invoices. Others are child lists, like contacts under a customer or attached items under an item. For those child streams, the connector first reads the parent records, then asks Chargebee for each parent’s children, and stamps the parent id onto each child so the relationship is not lost.

The connector uses HTTP Basic authentication, with the Chargebee API key as the username and an empty password. It reads pages using Chargebee’s `next_offset` token, which is like a “next page ticket.” For incremental streams, it can also ask Chargebee for only records after the last saved cursor value. If Chargebee refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a stream description for one kind of Chargebee data. A stream description tells the sync system the stream’s name, main id field, time cursor field, and whether it is one of the main canonical streams.

**Data flow**: It takes simple settings such as a stream name, source object name, primary key, and timestamp fields. It fills in sensible defaults when values are not provided, then returns a `StreamSpec`, which is the system’s small recipe for reading that stream.

**Call relations**: This helper is used while building the file’s list of Chargebee streams. It hands each finished stream recipe to `StreamSpec.__init__`, so the connector later has a consistent catalogue of what Chargebee data it can read.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Chargebee. It sets time limits, standard headers, and the correct authentication method for the provided credential.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares JSON/form headers and timeouts, then either uses a provided custom transport or creates Basic authentication from the API key. It returns an `httpx.AsyncClient`, which is the reusable web client for API calls. If no usable authentication is present, it raises an error.

**Call relations**: The base connector calls this when it needs a client for a sync. This function delegates the low-level client pieces to `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient`, then gives the completed client back to the rest of the connector.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Unwraps Chargebee records from their resource envelope so downstream code can read fields directly. This matters because Chargebee returns records like `{customer: {...}}`, while the sync system expects a flatter record shape.

**Data flow**: It receives one raw record and the stream description. It looks for the envelope whose name matches the stream’s source object. If that envelope is a dictionary, it copies the inside fields to the top level and preserves any extra top-level fields, such as a parent id added by a child-stream paginator. If no proper envelope exists, it returns the record unchanged.

**Call relations**: This is used after pages are fetched, as part of making Chargebee’s response shape match the system’s normal record shape. It does not call other project functions; it is a small translation step between API output and sync-ready data.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right page-reading strategy for a requested Chargebee stream. It is the main traffic director for fetching records from ordinary list endpoints and from child endpoints.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, then yields pages from the matching helper: normal list pagination for most streams, or a parent-child loop for special substreams. If no strategy exists, it raises an implementation error. If Chargebee responds with 401 or 403, it turns that refusal into a `StreamSkipped` message.

**Call relations**: The sync framework calls this when it wants records for a stream. This method then calls `_paginate_attached_items`, `_paginate_contacts`, `_paginate_quote_line_groups`, `_paginate_subscription_scheduled`, or `_paginate_list` depending on the stream. When access is refused, it creates a `StreamSkipped` error so the wider run can treat the stream as unavailable rather than mysteriously failing.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, adds the incremental “only after this cursor” filter.

**Data flow**: It receives a stream description and an optional cursor value from a previous run. It always starts with the configured page limit. If both a cursor and a cursor field exist, it adds a Chargebee-style parameter such as `updated_at[after]`. It returns the finished parameter dictionary for the API request.

**Call relations**: `_paginate_list` calls this just before asking Chargebee for pages. This function keeps the small but important parameter-building rule in one place, so every ordinary list stream uses the same paging and incremental-sync behavior.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads pages from one of Chargebee’s ordinary list endpoints, such as customers, invoices, or transactions. It follows Chargebee’s `next_offset` token until there are no more pages.

**Data flow**: It receives the HTTP client, stream description, and optional cursor. It finds the endpoint path for the stream, builds request parameters with `_build_list_params`, then repeatedly yields each page of records from the inherited cursor-page helper. The output is an asynchronous sequence of record lists.

**Call relations**: `paginate` calls this for normal streams. The child-stream helpers also call it first to find parent records before fetching their children. It is the shared “read a normal Chargebee list” building block used throughout this connector.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads attached items, which are child records that live under individual Chargebee items. It first finds items, then asks Chargebee for the attached items for each one.

**Data flow**: It receives an HTTP client and optional cursor. It uses `_paginate_list` to read item pages, extracts each item id, skips parents without an id, then calls `_paginate_substream` for `/items/{item_id}/attached_items`. It yields child pages where each child record is marked with the parent `item_id`.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This function relies on `_paginate_list` to supply parent items and `_paginate_substream` to do the repeated child-page fetching and parent-id stamping.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads customer contacts, which are child records stored under individual Chargebee customers. It keeps each contact tied back to the customer it came from.

**Data flow**: It receives an HTTP client and optional cursor. It reads customer pages through `_paginate_list`, extracts each customer id, skips any customer without one, then asks `_paginate_substream` to read `/customers/{customer_id}/contacts`. The yielded contact records include a `customer_id` field.

**Call relations**: `paginate` calls this for the `contact` stream. It uses `_paginate_list` for the parent customer loop and `_paginate_substream` for the child contact pages, so the rest of the connector can treat contacts like any other stream of pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads quote line groups, which are child records under Chargebee quotes. It preserves the link between each line group and its quote.

**Data flow**: It receives an HTTP client and optional cursor. It reads quote pages using `_paginate_list`, extracts each quote id, skips parents without an id, then calls `_paginate_substream` for `/quotes/{quote_id}/quote_line_groups`. It yields pages of child records stamped with `quote_id`.

**Call relations**: `paginate` calls this when syncing the `quote_line_group` stream. This function follows the same parent-then-children pattern as the other substream helpers, using `_paginate_list` for parents and `_paginate_substream` for paged children.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads the scheduled-change view for each subscription. Unlike ordinary child lists, this endpoint returns one detailed subscription record per parent subscription.

**Data flow**: It receives an HTTP client and optional cursor. It reads subscription pages through `_paginate_list`, extracts each subscription id, skips records without an id, then makes a detail request for `/subscriptions/{id}/retrieve_with_scheduled_changes`. If the response contains a subscription envelope, it yields a one-record page containing that subscription plus the original `subscription_id`.

**Call relations**: `paginate` calls this for the `subscription_with_scheduled_changes` stream. It uses `_paginate_list` to find subscriptions, then performs a direct detail fetch for each one rather than using `_paginate_substream`, because this Chargebee endpoint returns a single record instead of a paged child list.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the shared paging loop for child streams such as contacts, attached items, and quote line groups. It also adds the parent id to each child record so the relationship is not lost.

**Data flow**: It receives the HTTP client, child endpoint path, the name of the parent-id field to add, and the actual parent id value. It reads pages from the child endpoint using Chargebee’s `list` and `next_offset` response shape. For every dictionary record, it copies the record, adds the parent id field, and yields non-empty stamped pages.

**Call relations**: `_paginate_attached_items`, `_paginate_contacts`, and `_paginate_quote_line_groups` call this after they have found a parent id. It is the reusable child-page worker that lets those helpers avoid repeating the same pagination and parent-stamping code.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### QuickBooks accounting
Defines the QuickBooks Online source connector for querying and paging through accounting objects safely.

### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `during QuickBooks source sync`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each kind of record. Instead, every read is done by sending a SQL-like query to one shared `/query` endpoint. This file wraps that awkward API shape into a normal source connector that the rest of the system can use.

The file first lists the QuickBooks record types the system can recall later, such as accounts, customers, invoices, bills, payments, journal entries, and vendors. Each stream says what QuickBooks object it reads, that `Id` is the unique identifier, and, when possible, that `MetaData.LastUpdatedTime` is the field used to fetch only records changed since the last sync. A few reference lists, such as payment methods and tax agencies, do not use that incremental cursor and are refreshed fully.

`QuickBooksConnector` then turns those stream definitions into actual HTTP reads. For each page, it builds a QuickBooks query, sends it to `/query`, pulls the records out of QuickBooks’ response wrapper, and keeps asking for the next page until QuickBooks returns fewer than 100 records. If QuickBooks refuses access with a permission or authentication error, the stream is skipped with a clear explanation instead of crashing the whole sync. Finally, because the cursor field is nested inside `MetaData`, `flatten` copies it to a flat key so the syncing machinery can track progress correctly.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates one reusable description of a QuickBooks stream, such as invoices or vendors. The rest of the connector uses these descriptions to know what object to query, what field uniquely identifies records, and whether the stream can be synced incrementally.

**Data flow**: It receives a friendly stream name, the QuickBooks object name, an optional cursor field, and a flag saying whether the stream is considered canonical. It packages those details into a `StreamSpec`, which is the system’s standard recipe for reading one kind of source data. The result is added to the list of QuickBooks streams.

**Call relations**: This helper is used while the file is loaded to build `QUICKBOOKS_STREAMS`. It hands each completed stream recipe to `StreamSpec`, so `QuickBooksConnector` can later expose the full set of QuickBooks objects to the sync system.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: Builds the SQL-like text query that QuickBooks expects. It is what turns a stream recipe and an optional saved cursor into a concrete request for one page of data.

**Data flow**: It receives a stream, an optional cursor value, and the starting row number for the page. It creates a `SELECT * FROM ...` query for the QuickBooks object. If a cursor is available and the stream supports one, it adds a `WHERE` clause to ask only for records updated after that cursor, escapes single quotes in the cursor value, and orders results by the cursor field. It then adds the page size and returns the finished query string.

**Call relations**: `paginate` calls this each time it needs the next page from QuickBooks. `_build_query` does not send anything itself; it only prepares the request text that `paginate` passes to the QuickBooks `/query` endpoint.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads records from QuickBooks one page at a time. It hides QuickBooks’ paging rules so the rest of the system can simply receive batches of records until the stream is done.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. Starting at row 1, it builds a query, sends it to QuickBooks, opens the `QueryResponse` wrapper, and extracts the list for the requested entity. If records are found, it yields that batch to the caller. If the batch has fewer than 100 records, it stops because there are no more pages; otherwise it advances the starting position and repeats. If QuickBooks returns 401 or 403, it changes that into a `StreamSkipped` message explaining that access was refused.

**Call relations**: This is the main read loop used by the connector during syncing. It relies on `_build_query` to produce the right QuickBooks query for each page. When access is refused, it raises `StreamSkipped` so the larger sync process can skip that particular stream rather than treating every refusal as an unexpected failure.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Makes the nested QuickBooks update timestamp easy for the sync system to read. This matters because the cursor lives at `MetaData.LastUpdatedTime`, while the generic syncing code expects a simple field name it can compare and store.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream has a dotted cursor field, it looks inside the nested record using that path and returns a copy of the record with an extra flat key named exactly like the cursor field. If there is no nested cursor to lift, it returns the record unchanged.

**Call relations**: The connector uses this after records are fetched so the shared source-sync machinery can advance its saved watermark. It calls `get_path` to safely read the nested value from the QuickBooks record.

*Call graph*: 1 external calls (get_path).


### Recurly subscription billing
Defines the Recurly source connector for authenticated, paged syncing of subscription-billing objects.

### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during source sync`

Recurly stores useful billing data such as accounts, subscriptions, invoices, plans, coupons, and notes. This connector is the bridge between Recurly’s web API and the rest of the UFO source-sync system. Without it, the system would not know how to ask Recurly for data, how to move from one page of results to the next, or how to deal with child records such as account notes that live under a specific account.

The file first defines the list of streams, where a stream means one kind of data that can be copied, such as “accounts” or “invoices.” Each stream records basics like its name, its unique key, and the date field used for incremental syncing. Incremental syncing means starting from a saved time instead of rereading everything.

The main class, RecurlyConnector, builds an HTTP client with Recurly’s required headers and Basic authentication. It then fetches records page by page. Recurly returns list responses in an envelope containing the records, a flag saying whether there is more, and a link to the next page. The connector follows that link until no more pages remain.

Some streams are nested under parent records. For example, account notes require first listing accounts, then asking for notes for each account. The connector does that fan-out work and stamps the parent ID onto each child row so the relationship is not lost.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Recurly data type. It gives the rest of the sync system a compact recipe for what to read, what field identifies each record, and what timestamp field can be used for incremental syncing.

**Data flow**: It takes a stream name plus optional details such as the Recurly object path, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults when values are not supplied, then returns a StreamSpec object that the connector can later use while fetching data.

**Call relations**: This function is used while the file is loaded to build the RECURLY_STREAMS list. Those stream definitions are then attached to RecurlyConnector so the wider source framework knows what Recurly datasets are available.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Recurly. It sets Recurly’s required API version header and chooses the right authentication style for the credential supplied by the system.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares request timeouts and headers, then either uses a provided proxy transport or turns the API key into HTTP Basic authentication, where the key is the username and the password is blank. It returns an asynchronous HTTP client ready to make Recurly requests, or raises an error if no usable credential is present.

**Call relations**: The source framework calls this when setting up the connector’s network access. The client it returns is later passed into pagination methods so they can make actual API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This normalizes Recurly’s “next page” link into a path the client can request. It accepts both full URLs and already-relative paths.

**Data flow**: It receives a next-page link, which may be missing, a full URL, or a path. If the link is empty, it returns nothing. If it is a full URL, it strips it down to just the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: All pagination loops use this after reading a page from Recurly. Top-level streams, account ID listing, coupon ID listing, and per-parent child streams all call it so they can safely follow Recurly’s cursor link without caring what shape the link arrived in.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the first set of query parameters for a Recurly list request. It controls page size, sorting direction, and where an incremental sync should begin.

**Data flow**: It receives a stream definition and an optional saved cursor value, usually a timestamp. It creates parameters asking for up to 200 records sorted oldest to newest by the stream’s cursor field, or by created_at when there is no cursor field. If both a cursor field and cursor value exist, it adds begin_time so Recurly starts at that point. It returns the parameter dictionary.

**Call relations**: Top-level pagination and per-parent pagination both call this before their first request. After the first request, they follow Recurly’s returned next links instead of rebuilding these starting parameters.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Recurly stream. It decides whether the stream is a normal top-level list, a special parent-filtered coupon list, or a child list that must be fetched under each parent.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. Based on the stream name, it routes the work to the correct pagination helper. It yields pages of records as lists of dictionaries. If Recurly responds with 401 or 403, meaning unauthorized or forbidden, it turns that into StreamSkipped so the sync can skip that stream with a clear reason instead of crashing as an unexplained HTTP failure.

**Call relations**: The broader source-sync engine calls this when it wants records for a stream. This method then hands off to _paginate_top_level for ordinary collections or _paginate_per_parent for child resources such as account notes and billing infos.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly collection such as accounts, invoices, plans, or coupons. It keeps requesting pages until Recurly says there are no more.

**Data flow**: It receives a client, a stream definition, a starting API path, and an optional cursor. It builds the first query, sends a request, yields any records found in the response’s data field, then follows the next link when has_more is true. Once there are no more pages, it stops.

**Call relations**: RecurlyConnector.paginate calls this for ordinary streams and for the special unique_coupons_parent stream. It relies on _initial_query to prepare the first request and _next_path to clean up each following page link.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This lists Recurly account IDs so child account resources can be fetched one account at a time. It is like first collecting folder names before opening each folder to read the files inside.

**Data flow**: It starts at the /accounts endpoint with a simple created_at sort. For each page returned by Recurly, it looks through the data rows and yields the id from each valid account record. It follows next links until there are no more account pages.

**Call relations**: _paginate_per_parent calls this when it needs to fetch child streams that live under accounts, such as account notes, billing infos, shipping addresses, or account coupon redemptions. It uses _next_path to continue across pages of accounts.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This lists coupon IDs, but only for bulk coupons. Those IDs are needed before fetching unique coupon codes that belong under each bulk coupon.

**Data flow**: It starts at the /coupons endpoint and reads coupon pages in created_at order. For each row, it checks that the row is a dictionary, has an id, and has coupon_type set to bulk. It yields only those qualifying IDs, then follows Recurly’s next links until all coupon pages have been checked.

**Call relations**: _paginate_per_parent calls this when the parent path is /coupons, which is the case for unique coupon code syncing. It uses _next_path for the same page-following behavior used elsewhere in the connector.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child resources that are stored under a parent resource in Recurly. For example, it can list every account first, then fetch notes or billing information for each account.

**Data flow**: It receives the parent path, child path, field name used to store the parent ID, and optional cursor. It chooses whether to list account IDs or bulk coupon IDs, then for each parent ID builds a child URL. It requests child pages, stamps the parent ID onto each child record when possible, yields non-empty pages, and follows next links until that parent’s children are exhausted before moving to the next parent.

**Call relations**: RecurlyConnector.paginate calls this for streams listed in the per-parent stream map. It depends on _account_ids or _coupon_ids to find parents, _initial_query to prepare each child stream’s first request, and _next_path to continue through each child collection’s pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### Square commerce payments
Defines the Square source connector for normalizing customers, payments, catalog, orders, locations, and inventory APIs into syncable pages.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `during source sync, whenever Square records are fetched`

Square exposes its data through several slightly different web API patterns. This file hides those differences behind one connector, `SquareConnector`, so the rest of the project can simply ask for a stream like “customers” or “orders” and receive batches of records.

The file first defines which Square streams exist and what field identifies each record. Some streams also have a time field used as a bookmark, so later syncs can ask only for newer records. Think of this like leaving a bookmark in a long ledger and resuming from that point next time.

When the connector creates its HTTP client, it adds Square’s pinned API version header, which tells Square which version of its API rules to use. The main `paginate` method then chooses the right reading strategy for each stream. Simple list endpoints, such as customers or payments, use cursor-style paging. Catalog objects use a search endpoint. Orders are more complicated because Square orders must be searched across known location IDs, so the connector first asks for locations, then searches orders for those locations.

If Square rejects access with a 401 or 403 response, the connector does not crash the whole sync. It raises `StreamSkipped`, meaning this stream is politely skipped because the credential is invalid or missing the needed permission.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Square and adds the required Square API version header. The version header matters because Square can change API behavior over time, and this pins the connector to the version it was written for.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the normal authenticated HTTP client, then adds `Square-Version: 2026-04-16` to the outgoing headers. It returns that prepared client for later API calls.

**Call relations**: This is part of the setup path inherited from the common REST connector. Before any Square stream is read, this method prepares the client that the later paging methods use for their requests.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the central dispatcher for reading Square streams page by page. Given a requested stream, it chooses the right Square API pattern and yields batches of records to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream name, calls the matching helper, and yields each non-empty batch of records. If the stream is unsupported, or if Square refuses access with a 401 or 403 response, it raises `StreamSkipped` so the system can skip that stream cleanly.

**Call relations**: The sync system calls this when it needs records for a Square stream. It hands simple cursor-based streams to `SquareConnector._cursor_get`, catalog streams to `SquareConnector._catalog`, locations to `SquareConnector._locations`, and orders to `SquareConnector._orders`. For inventory counts it makes a one-off request and uses `records_at` to pull the list out of Square’s response.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use ordinary cursor-based list endpoints, such as customers, payments, and refunds. A cursor is a continuation marker or saved point that lets the connector continue through many pages or resume from a previous sync.

**Data flow**: It receives the HTTP client, the stream description, and an optional saved cursor. For payments and refunds, it sends the cursor as Square’s `begin_time` filter. For other cursor-based streams, it fetches pages and then filters out records whose cursor field is not newer than the saved cursor. It yields only pages that still contain records after filtering.

**Call relations**: `SquareConnector.paginate` calls this for customers, payments, and refunds. This helper relies on the shared REST connector paging machinery to walk through Square’s cursor pages, then gives the cleaned record batches back to `paginate`.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either items or categories and returns them in pages. Square uses a search request for catalog data rather than a simple list URL, so this helper builds the correct request body.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor bookmark. It translates the stream name into Square’s catalog object type, posts a search request with a limit, and follows Square’s returned continuation cursor until there are no more pages. If a saved cursor is present, it keeps only records whose update time is newer. It yields each non-empty batch of catalog objects.

**Call relations**: `SquareConnector.paginate` calls this when the requested stream is `catalog_items` or `catalog_categories`. Inside each loop it posts to Square’s catalog search endpoint and uses `records_at` to pull the `objects` list out of the response before yielding records back to the main paging flow.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations in one request. Locations are useful both as their own stream and as the required starting point for searching orders.

**Data flow**: It receives the HTTP client, sends a request to Square’s `/locations` endpoint, and extracts the `locations` list from the response. It returns that list as plain record dictionaries.

**Call relations**: `SquareConnector.paginate` calls this directly when syncing the `locations` stream. `SquareConnector._orders` also calls it first, because Square order searches need location IDs before they can ask for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders by first finding the account’s locations, then searching for orders across those locations. This extra step is needed because Square’s order search API is location-based.

**Data flow**: It receives the HTTP client and an optional cursor bookmark. It first fetches locations, keeps only valid location IDs, and stops if there are none. Then it repeatedly posts an order search request with those location IDs, an optional start time based on the cursor, and any continuation cursor returned by Square. It extracts the `orders` list from each response and yields non-empty pages until Square stops returning a continuation cursor.

**Call relations**: `SquareConnector.paginate` calls this for the `orders` stream. This helper calls `SquareConnector._locations` to get the location IDs it needs, then uses `records_at` to extract orders from each Square search response before passing pages back to `paginate`.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### Stripe payment platform
Defines the Stripe source connector for streaming customers, invoices, subscriptions, charges, and connected-account records from paged APIs.

### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `source sync`

Stripe exposes its data through many web endpoints, but they all need the same careful routine: ask for a page of records, follow Stripe’s “has more” signal, and remember the last record so the next request continues in the right place. This file packages that routine into a Stripe connector.

At the top, it lists all supported Stripe streams and describes each one in a small StreamSpec: its public name, where it lives in Stripe’s API, its main ID field, and which time field can be used for incremental syncing. Incremental syncing means “only fetch records newer than the last successful run,” which keeps later runs from rereading everything.

The StripeConnector then builds HTTP requests against Stripe’s API. It adds the pinned Stripe API version header so responses stay predictable even if Stripe changes defaults later. For ordinary streams, it walks Stripe’s standard list format. For nested data, such as a customer’s payment methods or an invoice’s line items, it first fetches parent records and then fetches each parent’s child records, stamping the parent ID onto every child row so the relationship is not lost.

If Stripe refuses access with a permission or authentication error, the connector marks that stream as skipped instead of crashing the whole sync. This matters because one Stripe key may be allowed to read some resources but not others.

#### Function details

##### `_stream`  (lines 86–104)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates a StreamSpec, which is the small description the sync system needs for one Stripe stream. It keeps the long stream list readable by filling in common defaults like using “id” as the main key and “created” as the usual time cursor.

**Data flow**: It receives a stream name plus optional details such as the Stripe API object name, primary key, cursor field, and timestamp fields. It combines those inputs with sensible defaults and returns a StreamSpec object that the connector later uses to know what to fetch and how to track it.

**Call relations**: This helper is used while the file is loaded to build STRIPE_STREAMS. It hands each completed stream description to StreamSpec.__init__, and those descriptions become the map that StripeConnector follows during sync.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 179–182)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Stripe and adds the exact Stripe API version the connector expects. Pinning the version is like asking Stripe to speak a known dialect, so the connector is less likely to be surprised by response changes.

**Data flow**: It takes a base URL and a credential object supplied by the wider system. It asks the parent RestConnector to build the basic client, adds the Stripe-Version header, and returns the prepared client ready for requests.

**Call relations**: The broader REST connector setup calls this when a sync needs a Stripe client. This method customizes the generic client just enough for Stripe before the pagination methods use it.


##### `StripeConnector._list_path`  (lines 185–186)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This turns a stream description into the normal Stripe list endpoint path. For example, a stream whose source object is customers becomes the path for listing customers.

**Data flow**: It receives a StreamSpec, reads its source_object field, prefixes it with Stripe’s /v1 API path, and returns that path as text.

**Call relations**: The main paginate method uses this for ordinary streams. The substream methods also use it to find parent collections before they fetch each parent’s child records.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 189–201)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved sync cursor into the timestamp format Stripe expects for time filtering. Stripe’s created[gte] filter wants a Unix timestamp, meaning seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date string. It returns an integer timestamp when it can understand the value, or None when there is no usable cursor.

**Call relations**: The page loop calls this before making requests. Its result decides whether the connector can ask Stripe for only records created after the previous sync point.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 203–229)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry for reading one Stripe stream page by page. It decides which fetching strategy fits the stream: a plain list, a nested child collection, a child collection filtered by query parameter, or external accounts under connected accounts.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It routes the request to the right pagination helper and yields batches of record dictionaries. If Stripe replies with a permission or authentication refusal, it turns that into a StreamSkipped signal so the sync can continue with other streams.

**Call relations**: The source sync framework calls this when it wants records for a stream. This method then hands off to _page_loop for simple streams, or to one of the specialized substream paginators when records must be fetched through parent objects.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 231–262)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the reusable loop that walks through Stripe’s paginated list responses. It keeps asking for the next page until Stripe says there are no more records.

**Data flow**: It receives a client, an API path, a stream description, an optional cursor, and optional extra query parameters. It builds request parameters such as limit, starting_after, and created[gte], asks Stripe for data, normalizes each record, yields non-empty pages, and updates starting_after from the last record ID so the next request continues after it.

**Call relations**: Most other fetching methods rely on this as their engine. paginate uses it for ordinary streams, while the substream helpers use it first for parent pages and then again for each child collection.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._browse_record`  (lines 265–285)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly normalizes one Stripe record before it leaves the connector. Its main job is to add or convert created_at and updated_at values into readable timestamp strings when Stripe provides numeric times.

**Data flow**: It receives a raw record dictionary and the stream description. It copies the record, converts numeric created_at or updated_at values when present, fills created_at from Stripe’s created field when needed, fills updated_at from the stream cursor when possible, and returns the normalized copy.

**Call relations**: _page_loop calls this for every record it receives from Stripe. The normalized records are then yielded upward to whichever pagination path requested them.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 287–310)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that live under a parent record’s URL, such as invoice line items under one invoice. It preserves the parent-child relationship by adding the parent ID, and sometimes parent timestamps, to each child row.

**Data flow**: It looks up which parent stream and child path template belong to the requested child stream. It fetches parent pages, takes each parent ID, builds the child URL, fetches child pages, adds fields such as customer_id or invoice_id where configured, and yields the enriched child records.

**Call relations**: paginate calls this when the stream is listed in the child-path mapping. This helper uses _stream_spec to find the parent StreamSpec, _list_path to build the parent endpoint, and _page_loop to fetch both parent and child pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_substream_query`  (lines 312–326)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that are fetched by adding a parent ID as a query parameter instead of putting it in the URL path. For example, it can fetch subscription items by asking for items where subscription equals a particular subscription ID.

**Data flow**: It finds the parent stream name, query parameter name, and child endpoint for the requested stream. It fetches parent records, takes each parent ID, requests child pages with that ID in the query string, stamps the parent ID onto each child row, and yields the result.

**Call relations**: paginate calls this for streams configured in the query-parent mapping. It depends on _stream_spec, _list_path, and _page_loop in the same parent-then-child pattern as the path-based substream helper.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 328–341)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe connected accounts. These records are nested under each account, so the connector must first walk the accounts list and then fetch external accounts for each one.

**Data flow**: It gets the accounts stream description, fetches account pages, skips accounts without an ID, builds the external_accounts path for each account, fetches the child pages, adds account_id to every child record, and yields those records.

**Call relations**: paginate calls this for the external account streams. It uses _stream_spec to find the accounts stream, _list_path to create the accounts endpoint, and _page_loop to fetch both accounts and their external-account children.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 343–344)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the StreamSpec for a stream name from the connector’s Stripe stream list. The substream code uses it when it needs to fetch a parent stream before fetching child records.

**Data flow**: It receives a stream name, searches STRIPE_STREAMS for the matching StreamSpec, and returns that description. If no matching stream exists, the search naturally fails rather than inventing a stream.

**Call relations**: The substream pagination helpers call this to look up parent streams such as accounts, customers, invoices, subscriptions, or payouts. The returned StreamSpec is then passed into _list_path and _page_loop so the parent records can be fetched correctly.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Xero accounting
Defines the Xero source connector for turning accounting API responses such as accounts, contacts, invoices, and payments into consistent records.

### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `sync run`

Xero is an accounting service, and its API has a few habits this file hides from the rest of the project. Xero wraps lists inside named envelopes, such as returning accounts under an "Accounts" key. It also uses different ID field names for different record types, like "InvoiceID" for invoices and "ContactID" for contacts. This connector smooths those differences into a common shape, especially by adding a standard "id" field when possible.

The file defines the list of Xero data streams the system can read. A stream is one kind of resource, like invoices or payments. Some streams can be read page by page, 100 records at a time. Others are small and must be read all at once. For incremental syncing, where the system asks only for records changed since a previous run, Xero expects an "If-Modified-Since" request header in a specific internet date format. This file converts stored cursor values into that format.

The main class, XeroConnector, builds an HTTP client for Xero, optionally adding the required tenant header that identifies which Xero organization to read from. During sync, it fetches pages, yields batches of records, and skips streams cleanly if Xero says the current credentials do not have permission. It deliberately only reads data; it does not write anything back to Xero.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Xero resource type, such as accounts or invoices. It keeps the stream definitions short and consistent.

**Data flow**: It receives a local stream name, the Xero API object name, and a few options such as which field marks updates. It fills in shared defaults, including using "id" as the primary key, and returns a StreamSpec object that the sync system can use to know what to fetch and how to identify records.

**Call relations**: At file load time, the XERO_STREAMS list calls this helper repeatedly to build the catalog of Xero streams. Inside, it hands the collected details to StreamSpec so the broader source framework can treat each Xero resource in the same structured way.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved sync cursor into the date format Xero expects for "changed since" requests. That matters because Xero uses an HTTP header for incremental sync instead of a normal query parameter.

**Data flow**: It receives a cursor value that may be empty, a Unix timestamp, an ISO-style date string, or already some other date text. Empty or unusable values become None. Numeric timestamps are turned into a GMT date string. ISO date strings are parsed, treated as UTC if they have no timezone, and formatted as RFC 1123 text like an email-style date. If parsing fails, it returns the original text so the caller can still send it.

**Call relations**: XeroConnector.paginate calls this when a stream supports incremental sync and has a previous cursor. The returned date string is placed into the "If-Modified-Since" header before the HTTP request goes to Xero.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This creates a Xero connector instance and remembers which Xero tenant, or organization, it should read from. Xero needs this tenant ID when one login grant can access more than one organization.

**Data flow**: It receives an optional tenant ID. It stores that value on the connector instance for later use when building the HTTP client. It does not contact Xero or validate the tenant immediately.

**Call relations**: This runs when the source connector is constructed. Later, XeroConnector._make_client reads the stored tenant ID and adds it to outgoing requests if it was provided.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Xero. If the connector was given a tenant ID, it adds the Xero-specific header required to select the organization.

**Data flow**: It receives the API base URL and a credential object supplied by the surrounding auth system. It asks the parent REST connector to create the basic authenticated async HTTP client. Then, if a tenant ID is stored, it adds a "xero-tenant-id" header. The finished client is returned for making API requests.

**Call relations**: The wider REST source framework calls this when it is ready to sync. XeroConnector.paginate later uses the client produced here to make GET requests. This method relies on the parent connector for normal authentication setup and only adds the Xero-specific tenant detail.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads records for one Xero stream and yields them in batches. It knows which Xero resources use page numbers and which ones must be fetched in a single request.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero path from the stream’s source object. If a cursor is present for an update-aware stream, it converts the cursor into Xero’s required date header. For non-paged streams, it makes one GET request and yields the records found inside Xero’s envelope. For paged streams, it requests page 1, page 2, and so on, yielding each batch until Xero returns no records or fewer than 100 records. If Xero returns a permission or authentication refusal, it raises StreamSkipped so the sync can skip that stream with a clear explanation; other HTTP errors are passed upward.

**Call relations**: The sync engine calls this when it needs data from a particular Xero stream. It calls _cursor_to_rfc1123 to prepare incremental-sync headers and uses httpx.AsyncClient.get to fetch data from Xero. When Xero refuses access with a 401 or 403 status, it creates a StreamSkipped error so the rest of the sync can understand that this stream was unavailable rather than silently broken.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes one Xero record by adding a common "id" field when Xero used a resource-specific ID name instead. This lets the rest of the system identify records without needing to know every Xero naming rule.

**Data flow**: It receives one record and the stream it came from. If the record already has an "id", it returns it unchanged. Otherwise it looks up the correct Xero ID field for that stream, reads the value, and returns a copy of the record with "id" added as a string. If there is no known ID field or the value is missing, it leaves the record unchanged.

**Call relations**: The broader source framework uses this after records have been fetched so they can be stored and keyed consistently. It depends on the file’s ID-field mapping, which captures Xero’s different names such as "AccountID", "InvoiceID", and "TaxType".

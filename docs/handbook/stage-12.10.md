# Finance, billing, accounting, and commerce source connectors  `stage-12.10`

This stage is part of the system’s behind-the-scenes data intake work. It is a set of connectors, which are small adapters that know how to talk to outside services and translate their answers into a common shape the rest of the system can store, search, and recall.

Each file covers a different finance or commerce tool. Brex brings in spend-management records like expenses, vendors, budgets, and departments. Chargebee and Recurly read subscription-billing data such as customers, subscriptions, invoices, coupons, and transactions. Stripe handles a wide range of payment and billing records, including customers, invoices, subscriptions, payments, and connected accounts. QuickBooks Online and Xero read accounting records like ledgers, accounts, contacts, invoices, and payments. Square reads commerce data such as locations, catalog items, orders, inventory, customers, and payments.

Together, these connectors act like translators at a loading dock: each service speaks differently, but the system receives steady streams of usable records.

## Files in this stage

### Spend management
Brex connector support introduces spend, expense, user, vendor, budget, and department records.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `during source sync when fetching Brex records`

Brex exposes business spending data through a web API, but the rest of this project needs a consistent way to pull that data in. This file is the adapter between the two worlds. It says which Brex objects are available, where to fetch each one, and how to walk through Brex’s paged responses.

The file defines six streams: budgets, departments, expenses, transactions, users, and vendors. A stream is one kind of record the system can sync. Some streams mark a date field, such as a transaction’s posted date, as a cursor field. That gives the wider sync system a useful timeline marker, even though Brex itself is not being asked to filter by that date here.

The main class, `BrexConnector`, inherits from a shared REST connector. REST means the system talks to Brex over ordinary web requests. This connector is read-only: it fetches records but does not create or update anything in Brex.

The important behavior is pagination. Brex returns records in batches, like giving someone one stack of papers at a time with a note saying where to ask for the next stack. `BrexConnector.paginate` keeps requesting pages with a cursor token until Brex says there are no more. Each non-empty page is yielded back to the shared sync machinery.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one Brex data stream, such as `transactions` or `vendors`. This keeps the stream setup short and consistent instead of repeating the same fields for every Brex object type.

**Data flow**: It receives a stream name and optional details like the primary key, cursor date field, and whether the stream is considered canonical. It puts those values into a `StreamSpec`, which is the project’s common description of a syncable record type, and returns that description for later use.

**Call relations**: This helper is used while the file is loaded to build the `BREX_STREAMS` list. Inside, it hands the collected stream settings to `StreamSpec.__init__`, so the rest of the connector framework receives stream definitions in the format it already understands.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches all pages for one Brex stream from the Brex API. Someone uses it when they want every available record for a stream, not just the first batch Brex returns.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from the wider framework. It looks up the Brex API path for that stream, then repeatedly sends a request with a page size of 100 and, when available, the next-page cursor Brex provided. From each response, it reads the `items` list, safely treats missing or invalid lists as empty through `list_or_empty`, yields any records it found, and stops when Brex no longer returns a `next_cursor`. If the stream has no known Brex endpoint, it raises an error instead of guessing.

**Call relations**: The shared REST connector machinery is expected to call this method when syncing a Brex stream. This method does the Brex-specific page walking, relies on the inherited `_get` request helper to make the actual web call, and uses `ufo.sdk.sources.list_or_empty` to normalize the response before passing record batches back to the sync flow.

*Call graph*: 1 external calls (list_or_empty).


### Subscription billing
Chargebee connector support covers subscription-billing entities such as customers, subscriptions, invoices, items, and transactions.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `during source sync reads`

Chargebee is an online billing service, and its API returns data in a very regular but slightly wrapped shape. This connector is the translator between Chargebee and UFO’s generic source-sync machinery. Without it, the system would not know which Chargebee endpoints exist, how to authenticate, how to move through pages of results, or how to unwrap records into a useful form.

The file first defines the Chargebee streams the system can read. A stream is one kind of thing to sync, such as a customer or invoice. Most streams map directly to a Chargebee list endpoint. Some are substreams: for example, contacts belong to customers, and attached items belong to items. Those are fetched by first reading the parent records, then asking Chargebee for each parent’s children. Think of it like checking every folder in a filing cabinet, then reading the papers inside each folder.

The connector uses HTTP Basic authentication, where the Chargebee API key is sent as the username and the password is blank. It also respects a provided proxy transport when credentials come through a broker. Pagination follows Chargebee’s `next_offset` token until there is no next page. If Chargebee refuses access with a 401 or 403 response, the stream is marked as skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one Chargebee resource. It keeps the long stream list readable by filling in common defaults such as the primary key and update-time cursor.

**Data flow**: It receives the stream name and optional details like the Chargebee object name, key field, and time fields. It combines those values with sensible defaults and returns a `StreamSpec`, which is the system’s small record describing what can be synced and how to identify changes.

**Call relations**: This function is used while the file is loaded to build `CHARGEBEE_STREAMS`. It hands each completed stream description to the connector class so the wider sync system knows which Chargebee data types are available.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Chargebee. It sets timeouts, standard request headers, the tenant-specific base URL, and the right authentication method.

**Data flow**: It receives a base URL and a resolved credential. It trims the URL, prepares JSON/form headers, and builds an asynchronous HTTP client. If the credential includes a proxy transport, that transport is used unchanged; otherwise, if it includes a direct API key, the function sends it using Basic authentication with an empty password. If neither is present, it raises an error because the connector cannot safely contact Chargebee.

**Call relations**: The parent `RestConnector` infrastructure calls this when it needs a client for a sync run. This function delegates the low-level HTTP setup to `httpx` objects, then returns the ready client to the rest of the connector’s pagination code.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This removes Chargebee’s extra wrapper around each record so downstream code can read fields like `id` and `updated_at` directly. It also preserves any parent ID that a substream added.

**Data flow**: It receives one raw record and the stream description. If the record contains an inner dictionary under the stream’s source object name, it copies that inner record and then adds any top-level extra fields that are not already present. If the expected wrapper is missing, it returns the record unchanged.

**Call relations**: The generic source-sync flow calls this after pages are fetched. It sits between the Chargebee API shape and the system’s normal record shape, making the output easier for later storage or processing steps to use.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for reading a Chargebee stream. Given a requested stream, it chooses the correct paging strategy and yields pages of raw records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous run. It checks the stream name, sends substreams to their special parent-child loops, sends ordinary streams to the shared list paginator, and yields each page it gets back. If Chargebee returns 401 or 403, it converts that refusal into a `StreamSkipped` signal with a clear message.

**Call relations**: The broader sync system calls `paginate` when it wants records for one stream. `paginate` then hands off to `_paginate_list`, `_paginate_attached_items`, `_paginate_contacts`, `_paginate_quote_line_groups`, or `_paginate_subscription_scheduled` depending on the stream. It is the main doorway from the generic connector framework into the Chargebee-specific fetch logic.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, asks Chargebee only for records newer than the saved cursor.

**Data flow**: It receives a stream description and an optional cursor. It always starts with a `limit` value. If there is both a cursor and a cursor field for that stream, it adds a Chargebee filter like `updated_at[after]=...`, meaning only records strictly after that point should be returned. It returns the finished parameter dictionary.

**Call relations**: `_paginate_list` calls this just before it starts walking through pages. This keeps the paging loop simple while centralizing the small but important rule for incremental syncs.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads any standard Chargebee list endpoint, such as customers, invoices, or transactions. It follows Chargebee’s `next_offset` token until all pages are read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for that stream, builds request parameters with `_build_list_params`, and asks the shared cursor-page helper to read records from the response’s `list` field while following `next_offset`. It yields each page of records as it arrives.

**Call relations**: `paginate` calls this for ordinary streams. The substream paginators also call it first to read their parent records before fetching child records. It provides the common list-reading behavior that most of the connector depends on.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads attached items, which are not available as one simple global list. It first reads items, then fetches the attached items for each item.

**Data flow**: It receives an HTTP client and optional cursor. It uses the normal item list paginator to get parent items, extracts each item ID, skips parents without an ID, and then asks `_paginate_substream` to read `/items/{item_id}/attached_items`. Each child record is stamped with the parent `item_id` so its origin is not lost.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This function relies on `_paginate_list` for parent items and `_paginate_substream` for the repeated child-endpoint paging.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads contacts by walking through customers first, because contacts live under individual customers in Chargebee. It keeps the customer ID attached to each contact.

**Data flow**: It receives an HTTP client and optional cursor. It fetches customer pages through `_paginate_list`, pulls out each customer ID, skips customers without an ID, and then reads `/customers/{customer_id}/contacts` through `_paginate_substream`. The child records are enriched with `customer_id` before being yielded.

**Call relations**: `paginate` calls this for the `contact` stream. It acts as the customer-to-contacts bridge, using `_paginate_list` for the parent customer search and `_paginate_substream` for each customer’s contacts.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads quote line groups by first reading quotes, then fetching the line groups belonging to each quote. It preserves which quote each group came from.

**Data flow**: It receives an HTTP client and optional cursor. It paginates quotes with `_paginate_list`, extracts each quote ID, skips records without one, and then asks `_paginate_substream` to read `/quotes/{quote_id}/quote_line_groups`. Each returned line group gets a `quote_id` added.

**Call relations**: `paginate` calls this when syncing `quote_line_group`. Like the other fan-out streams, it combines the shared parent-list reader with the shared substream reader.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads subscription records that include scheduled changes, which Chargebee exposes through a special per-subscription detail endpoint rather than a normal list. It produces one enriched record per subscription that has usable detail.

**Data flow**: It receives an HTTP client and optional cursor. It first paginates normal subscriptions, extracts each subscription ID, and skips missing IDs. For each subscription, it requests `/subscriptions/{id}/retrieve_with_scheduled_changes`, looks for the returned `subscription` object, and yields it wrapped with a `subscription_id` field so the record can be identified.

**Call relations**: `paginate` calls this for `subscription_with_scheduled_changes`. It uses `_paginate_list` to find the parent subscriptions, then makes one direct detail request per parent through the connector’s inherited HTTP helper.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared loop for child resources that belong to a parent, such as contacts under a customer. It reads a paged child endpoint and adds the parent’s ID to every child record.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent-ID field to add, and the parent ID value. It pages through the endpoint using Chargebee’s `list` and `next_offset` pattern, copies each dictionary record, adds the parent ID, and yields only non-empty stamped pages.

**Call relations**: `_paginate_attached_items`, `_paginate_contacts`, and `_paginate_quote_line_groups` call this after they have found a parent ID. It gives those three flows one common way to read child pages and preserve the parent-child link.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### Accounting ledgers
QuickBooks connector support handles accounting objects and incremental change tracking for ledger-oriented syncs.

### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `sync request handling`

QuickBooks Online exposes many accounting objects, such as customers, invoices, bills, payments, and journal entries. This file turns those objects into named streams that the rest of the system can sync and remember. Think of each stream like a folder in a filing cabinet: one folder for invoices, one for vendors, one for accounts, and so on.

QuickBooks does not provide a simple “give me all invoices” endpoint for each object. Instead, the connector must send SQL-like text queries to a shared `/query` endpoint. This file builds those queries, adds paging instructions so results come back in chunks of 100, and keeps asking for the next chunk until QuickBooks returns a short page.

For most streams, the connector also supports incremental sync. That means it asks only for records updated after a saved time, instead of rereading everything every time. A few reference lists, such as payment methods and tax agencies, do not use that update cursor and are read as full refreshes.

The file also smooths over a shape mismatch: QuickBooks stores update times inside a nested `MetaData` object, but the syncing layer expects an easy-to-read field. The `flatten` method copies that nested value onto a flat key so the watermark can advance correctly. If QuickBooks refuses access with a 401 or 403 error, the stream is skipped with a clear explanation rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard stream description for one QuickBooks object. It saves repetition by filling in the common QuickBooks rules, such as using `Id` as the unique record key and `MetaData.LastUpdatedTime` as the usual update cursor.

**Data flow**: It receives a friendly stream name, the QuickBooks object name to query, and optional choices such as whether the stream is canonical or whether it has a cursor. It packages those details into a `StreamSpec`, which is the system’s description of what can be synced and how records should be identified and ordered.

**Call relations**: This function is used while the file is being loaded to build the `QUICKBOOKS_STREAMS` list. It hands those stream descriptions to `QuickBooksConnector`, which later uses them when building queries, paging through results, and flattening cursor fields.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This method builds the SQL-like query string that QuickBooks Online expects. It chooses the right QuickBooks object, optionally limits results to records changed after a cursor time, and adds paging instructions.

**Data flow**: It takes a stream description, an optional cursor value, and a starting row number. It turns those into a query like “select all rows from this object, after this update time, starting at this position, with at most 100 results.” If the cursor contains a single quote, it escapes it so the query stays valid.

**Call relations**: `QuickBooksConnector.paginate` calls this every time it needs another page of records. The query it returns is then sent to QuickBooks through the shared `/query` endpoint.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads records from QuickBooks one page at a time. It is the main read loop for a stream, yielding batches of records until there are no more to fetch.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. Starting at position 1, it builds a QuickBooks query, sends it to `/query`, pulls the relevant records out of `QueryResponse`, and yields each non-empty batch. If a full page of 100 records comes back, it moves to the next page; if fewer than 100 come back, it stops. If QuickBooks answers with 401 or 403, it turns that refusal into a `StreamSkipped` message explaining that access is missing or invalid.

**Call relations**: The broader sync engine calls this when it wants data for a QuickBooks stream. Inside the loop it relies on `_build_query` to create each request. When access is refused, it creates `StreamSkipped` so the sync system can skip that stream cleanly instead of treating the whole connector as broken.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method makes nested QuickBooks cursor values easier for the sync system to read. In particular, it copies `MetaData.LastUpdatedTime` from inside the record into a flat field with that same dotted name.

**Data flow**: It receives one QuickBooks record and the stream it belongs to. If the stream’s cursor field is a dotted path, it reads that nested value from the record and returns a new record with an extra top-level cursor key. If there is no dotted cursor field, it returns the record unchanged.

**Call relations**: The sync layer uses this after records are fetched so it can compare and store update times consistently. It relies on `get_path` to safely read the nested value from the original QuickBooks record.

*Call graph*: 1 external calls (get_path).


### Billing and commerce transactions
Recurly, Square, and Stripe connectors stream recurring-billing, commerce, catalog, payment, and marketplace records.

### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during source sync, while reading Recurly streams`

Recurly exposes many business records through its web API, but the data does not arrive all at once. It comes in pages, with a “next” link that points to the next page. This file is the Recurly source connector: it knows which Recurly objects can be synced, how to authenticate, how to ask for records in a stable order, and how to keep following pages until there are no more.

The main class, RecurlyConnector, is read-only. It does not create or change Recurly data. It builds an HTTP client using Recurly’s required Basic Authentication style, where the API key is used like a username, and it pins the Recurly API version through request headers so responses stay predictable.

Most streams are simple top-level lists, such as accounts or invoices. Some streams are nested under a parent record, like account notes under each account or unique coupon codes under each bulk coupon. For those, the connector first walks the parent list, then asks Recurly for the child records under each parent, adding the parent id onto each child row so the relationship is not lost. If Recurly refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as broken.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Recurly object. A stream description tells the sync system what the stream is called, what API object it maps to, what field identifies each record, and what time field can be used for incremental syncing.

**Data flow**: It receives a stream name plus optional settings such as the source object name, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses when deciding what to request from Recurly.

**Call relations**: This function is used while the module is being loaded to build the RECURLY_STREAMS list. It hands the finished settings to StreamSpec.__init__, so the rest of the connector can work from consistent stream metadata instead of repeating those details in every method.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It applies the correct base URL, timeout limits, Recurly API version header, and authentication method.

**Data flow**: It receives a base URL and a credential object. If the credential already carries a special transport, it uses that transport, which lets another service proxy the secret. Otherwise, if the credential has an API key-like bearer value, it uses that value as the username in HTTP Basic Authentication, with an empty password. It returns an httpx AsyncClient ready to make Recurly requests; if no usable credential is present, it raises an error.

**Call relations**: The broader RestConnector framework calls this when it needs a client for the Recurly sync. Inside, it creates httpx timeout, authentication, and client objects so later pagination methods can focus on fetching pages rather than setting up network details.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s next-page link into the path format the connector can request next. It accepts both full URLs and already-relative paths.

**Data flow**: It receives a next link from a Recurly response. If the link is empty, it returns nothing. If it is a full URL, it strips it down to just the path and query string, such as `/accounts?cursor=...`. If it is already a path, it returns it unchanged.

**Call relations**: The page-walking methods call this whenever Recurly says there is another page. _paginate_top_level, _paginate_per_parent, _account_ids, and _coupon_ids all rely on it so they can keep moving through Recurly’s cursor links without caring whether Recurly returned an absolute URL or a relative path.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the first set of query parameters for a Recurly list request. It asks Recurly for a fixed page size and a stable ascending order, and it can start from a saved time cursor for incremental syncs.

**Data flow**: It receives the stream description and an optional cursor value from a previous sync. It builds a dictionary containing the page limit, sort field, and ascending order. If the stream has a cursor field and a cursor value was provided, it adds `begin_time` so Recurly starts at that point in time. The returned dictionary is used only on the first request in a paginated sequence.

**Call relations**: _paginate_top_level and _paginate_per_parent call this at the start of each listing flow. After the first request, Recurly’s own next links carry the cursor information, so the pagination methods stop sending these initial parameters.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing method for reading one Recurly stream. It decides whether the stream is a normal top-level list, a nested child list under each parent, or the special bulk-coupon parent stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. For parent-child streams, it delegates to _paginate_per_parent. For `unique_coupons_parent`, it reads coupons and keeps only bulk coupons. For normal streams, it builds the API path from the stream’s source object and delegates to _paginate_top_level. It yields lists of records as pages. If Recurly rejects the request with 401 or 403, it converts that into StreamSkipped so the sync can skip that stream cleanly.

**Call relations**: The sync framework calls this when it wants records for a particular Recurly stream. This method acts like a traffic director: it sends simple streams to _paginate_top_level, nested streams to _paginate_per_parent, and turns permission failures into StreamSkipped for the higher-level sync flow.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly collection, such as accounts, invoices, or plans, one page at a time. It follows Recurly’s `next` link until the API says there are no more pages.

**Data flow**: It receives the HTTP client, stream description, starting API path, and optional cursor. It builds the initial query parameters, requests the current page, yields the records if any exist, then checks `has_more`. If more pages exist, it uses _next_path to prepare the next request path. The output is an async sequence of record batches.

**Call relations**: paginate calls this for ordinary streams and for the coupon stream used by `unique_coupons_parent`. It uses _initial_query for the first request and _next_path after that, so the rest of the connector can consume pages without duplicating the page-following loop.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields just their account ids. Those ids are needed before the connector can fetch account-specific child records, such as notes or billing information.

**Data flow**: It starts at `/accounts` with a simple created-time ordering. For each page, it looks through the returned rows, keeps rows that are dictionaries with an `id`, and yields that id as text. If Recurly has more pages, it uses _next_path to move to the next page; otherwise it stops.

**Call relations**: _paginate_per_parent calls this when it needs to fetch children under accounts. In that flow, each yielded account id becomes part of a child URL like `/accounts/{id}/notes` or `/accounts/{id}/shipping_addresses`.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through coupons and yields only the ids of bulk coupons. Bulk coupon ids are needed because unique coupon codes live underneath those parent coupons.

**Data flow**: It starts at `/coupons` and reads pages in created-time order. For each coupon row, it requires a valid `id` and checks that `coupon_type` is `bulk`. Only those coupon ids are yielded. It follows further pages using _next_path until Recurly reports there are no more.

**Call relations**: _paginate_per_parent calls this when the parent path is `/coupons`. The ids it yields are used to build requests for each bulk coupon’s `unique_coupon_codes` child collection.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that are stored under another Recurly object. For example, it can fetch notes under every account or unique coupon codes under every bulk coupon.

**Data flow**: It receives the client, stream description, parent path, child path, field name for the parent id, and optional cursor. First it chooses the right parent id source: account ids for account-based children, or bulk coupon ids for coupon-based children. For each parent id, it builds the child URL, requests child pages with initial query parameters, yields any child records found, and adds the parent id into each child record if that field is missing. It follows Recurly next links until each parent’s child pages are exhausted.

**Call relations**: paginate calls this for the streams listed as per-parent streams. This method brings together _account_ids or _coupon_ids, _initial_query, and _next_path: first find every parent, then page through that parent’s children, then stamp the parent relationship onto the records before yielding them back to the sync framework.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `during source sync when Square streams are read`

Square exposes different kinds of data in different ways. Some records are fetched with simple list requests, some require search requests, and orders must be searched across all store locations. This file hides those differences behind one connector, so the rest of the system can ask for a stream by name and receive pages of records in a predictable form.

The main class, SquareConnector, is a read-only connector. It uses Square's web API, adds the required Square API version header, and then decides how to fetch each stream. For example, locations are fetched once, customers and payments use cursor-style paging, catalog items and categories use Square's catalog search endpoint, and orders first look up locations and then search orders for those locations.

A cursor is a marker that says, "continue from here" or "only return things newer than this." This connector uses cursors where Square supports them, and sometimes filters records itself when Square does not provide the exact filter shape needed. If Square refuses access with a 401 or 403 response, the connector raises StreamSkipped, meaning this stream should be skipped rather than crashing the whole sync. In everyday terms, this file is the translator at the front desk: it knows which Square counter to visit for each kind of record and repackages the answers into the format the system expects.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Square and adds the Square API version header that Square expects. The version header tells Square which behavior and response format this connector was written for.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the normal authenticated client, then adds the Square-Version header, and returns the ready-to-use client.

**Call relations**: This is used when the connector is being prepared to make Square API calls. After it returns the client, the other methods use that client to fetch locations, customers, orders, and the other streams.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Square stream. Given a stream name, it chooses the right Square API pattern and yields records in pages for the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, calls the matching helper, and yields each non-empty page of records. If the stream is unsupported or Square refuses access with a permission or authentication error, it raises StreamSkipped so the sync can move on safely.

**Call relations**: This method sits at the center of the connector. It calls _locations for locations, _cursor_get for customers, payments, and refunds, _catalog for catalog items and categories, _orders for orders, and records_at directly for inventory counts. It is the method the broader source framework relies on when it wants the next pages from a Square stream.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use ordinary GET requests with cursor-based paging, such as customers, payments, and refunds. A cursor is like a bookmark showing where the previous sync stopped.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor. For payments and refunds, it sends the cursor as Square's begin_time filter. For other streams, it fetches pages and then keeps only records whose cursor field is newer than the provided cursor. It yields each page that still contains records.

**Call relations**: paginate calls this when the requested stream is customers, payments, or refunds. It relies on the shared REST paging helper from the parent connector to walk through Square's pages, then performs any extra filtering needed before handing records back to paginate.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square's catalog for either item records or category records. Square puts both behind the same search endpoint, so this method chooses the right object type before asking.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor. It builds a search body with the catalog object type and page size, sends it to Square, extracts the returned objects, filters out older records if a cursor was supplied, yields non-empty pages, and follows Square's returned cursor until there are no more pages.

**Call relations**: paginate calls this for catalog_items and catalog_categories. This helper calls records_at to pull the objects list out of Square's response before passing each page back up to paginate.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account's locations, such as store or business locations. Other Square data, especially orders, depends on knowing these location IDs first.

**Data flow**: It receives the HTTP client, sends a GET request to Square's /locations endpoint, extracts the locations list from the response, and returns that list.

**Call relations**: paginate calls this directly when syncing the locations stream. _orders also calls it first, because Square order search needs location IDs before it can ask for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders by first finding the account's locations and then searching for orders across those locations. This extra step is needed because Square's order search API is location-based.

**Data flow**: It receives the HTTP client and an optional cursor. It fetches locations, keeps the valid location IDs, and stops if there are none. It then sends repeated order search requests with those IDs, an optional created-at start filter from the cursor, and any continuation token returned by Square. Each response is turned into a page of order records and yielded until Square gives no next cursor.

**Call relations**: paginate calls this when the requested stream is orders. _orders calls _locations to get the required location IDs, uses records_at to extract orders from each response, and then hands the pages back to paginate.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `request handling`

Stripe exposes many different billing and payment objects through web API endpoints. This file is the Stripe “source connector”: it knows which Stripe collections exist, how to ask Stripe for each page of results, and how to follow child lists such as invoice line items or customer payment methods. Without it, the system would not know how to pull Stripe data in a consistent way.

The file starts by defining the Stripe streams the system can read. A stream is one kind of record, like customers or charges. Some streams map directly to one Stripe list endpoint. Others are substreams: they only make sense under a parent record. For example, invoice line items are fetched by first listing invoices, then asking Stripe for the lines for each invoice.

`StripeConnector` is the main class. It creates an HTTP client for Stripe, adds Stripe’s required API-version header, chooses the right paging strategy for each stream, and walks through Stripe’s `data` plus `has_more` page format. It also converts Stripe’s Unix timestamp numbers into readable ISO date strings for common `created_at` and `updated_at` fields.

A key behavior is that permission failures, such as HTTP 401 or 403, do not crash the whole sync as an ordinary error. They become `StreamSkipped`, meaning this particular stream is unavailable because the Stripe key lacks access or is invalid.

#### Function details

##### `_stream`  (lines 86–104)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper builds a `StreamSpec`, which is the small description the rest of the source system uses to know what a Stripe stream is called, where it comes from, and which fields identify or order its records. It keeps the long stream list compact and consistent.

**Data flow**: It receives a stream name plus optional details such as the Stripe object path, primary key, cursor field, and date fields. It fills in sensible defaults, then returns a `StreamSpec` object that describes that stream for later syncing.

**Call relations**: This function is used while the module is being loaded to create the `STRIPE_STREAMS` list. Each call hands its settings into `StreamSpec`, which becomes the connector’s catalog of Stripe record types.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 179–182)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Stripe and adds the pinned Stripe API version header. Pinning the version matters because Stripe can change API behavior over time, and the connector expects a specific response shape.

**Data flow**: It receives a base URL and a credential reference. It asks the parent REST connector to create the normal authenticated client, adds the `Stripe-Version` header, and returns that prepared client.

**Call relations**: This method customizes the client setup inherited from `RestConnector`. Later, all paging and fetch methods use this client so every Stripe request carries the expected API version.


##### `StripeConnector._list_path`  (lines 185–186)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This turns a stream description into the normal Stripe list endpoint path. For example, a stream whose source object is `customers` becomes `/v1/customers`.

**Data flow**: It receives a `StreamSpec`, reads its `source_object`, prefixes it with `/v1/`, and returns the path string used in HTTP requests.

**Call relations**: The main `paginate` method uses this for ordinary streams. The substream methods also use it to find parent collections before fetching children.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 189–201)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved cursor value into the Unix timestamp format Stripe expects for date filtering. A cursor is the remembered “last seen” position used to avoid rereading old records.

**Data flow**: It receives a cursor as text or nothing. If the cursor is empty, it returns nothing. If it is already a number-like string, it returns that number. Otherwise it tries to parse an ISO date string and returns its Unix timestamp; if parsing fails, it returns nothing.

**Call relations**: `_page_loop` calls this before requesting pages. The result is used only when the stream’s cursor is Stripe’s `created` field, so requests can include `created[gte]` and start near the remembered point.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 203–229)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Stripe stream. It decides whether the stream is a simple list, a child list under parent records, a query-based child list, or an external-account list, then yields pages of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It chooses the right paging helper, yields each page it receives, and catches Stripe permission errors. If Stripe returns 401 or 403, it raises `StreamSkipped` with a clear explanation instead of treating it like an unexpected failure.

**Call relations**: The broader source-sync framework calls `paginate` when it wants records for a Stripe stream. `paginate` then hands work to `_paginate_external_accounts`, `_paginate_substream`, `_paginate_substream_query`, or `_page_loop`, depending on the stream’s shape.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 231–262)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the common loop for walking through Stripe’s paginated list responses. Stripe returns up to a limited number of records at a time, so this function keeps asking for the next page until Stripe says there are no more.

**Data flow**: It receives a client, an API path, a stream description, an optional cursor, and optional extra query parameters. It builds request parameters such as `limit`, `starting_after`, cursor filters, and stream-specific filters. It fetches data, normalizes each record with `_browse_record`, yields non-empty pages, and stops when `has_more` is false or when it cannot find a last record ID to continue from.

**Call relations**: This is the shared worker used by normal pagination and all substream pagination methods. It calls `_cursor_to_unix` to prepare date filtering and `_browse_record` to make timestamps easier for the rest of the system to use.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._browse_record`  (lines 265–285)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly cleans up a Stripe record before the system stores or indexes it. Its main job is to add readable timestamp fields when Stripe provides timestamps as Unix numbers.

**Data flow**: It receives one Stripe record and its stream description. It copies the record, converts numeric `created_at` and `updated_at` values to ISO date strings, fills `created_at` from Stripe’s `created` field when possible, and fills `updated_at` from the stream cursor value when possible. It returns the normalized copy and does not change the original record.

**Call relations**: `_page_loop` calls this for every record it receives from Stripe. The normalized records are then yielded to whichever higher-level paging method requested them.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 287–310)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that are reached through a parent-specific URL. For example, it can list customers first, then fetch each customer’s payment methods from a path containing that customer’s ID.

**Data flow**: It receives a client and a child stream. It finds the parent stream, lists all parent records, skips parents without IDs, builds each child URL using the parent ID, and pages through the child records. It may add the parent ID and selected parent timestamp fields onto each child row so the child record keeps its context.

**Call relations**: `paginate` calls this when the stream is listed in the child-path mapping. This method relies on `_stream_spec` to find the parent stream, `_list_path` to build the parent list path, and `_page_loop` to fetch both parent and child pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_substream_query`  (lines 312–326)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that Stripe exposes through a shared endpoint filtered by a parent ID in the query string. For example, it can fetch subscription items by asking for `/v1/subscription_items?subscription=<id>` for each subscription.

**Data flow**: It receives a client and a child stream. It looks up the parent stream name, the query parameter to use, and the child endpoint. It pages through parents, then for each parent ID pages through child records with that ID as an extra filter. It yields child rows stamped with a `<query_field>_id` value so their parent is clear.

**Call relations**: `paginate` calls this for streams listed in the query-parent mapping. It uses `_stream_spec` and `_list_path` to get the parent list, then delegates the actual HTTP paging to `_page_loop`.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 328–341)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe connected accounts. These records live under each account, so the connector must first list accounts and then visit each account’s external-accounts endpoint.

**Data flow**: It receives a client and an external-account stream. It lists Stripe accounts, skips accounts without IDs, builds `/v1/accounts/{account_id}/external_accounts` for each one, and pages through the matching child records. Each returned row is stamped with `account_id` so it remains tied to its account.

**Call relations**: `paginate` calls this for the two external-account streams. The method uses `_stream_spec` to get the accounts stream, `_list_path` for the accounts endpoint, and `_page_loop` for both parent and child page fetching.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 343–344)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the stream description with a given name from the connector’s Stripe stream catalog. It is a small lookup helper used when one stream needs to fetch another stream as its parent.

**Data flow**: It receives a stream name, searches `STRIPE_STREAMS`, and returns the matching `StreamSpec`. If no matching stream exists, the normal Python lookup behavior raises an error because the connector’s internal mapping would be inconsistent.

**Call relations**: The substream pagination methods call this when they need a parent stream such as `customers`, `accounts`, or `subscriptions`. The returned stream description is then passed to `_list_path` and `_page_loop` to fetch parent records.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Accounting exports
Xero connector support rounds out accounting sync with accounts, contacts, invoices, payments, and related financial records.

### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `during source sync when reading Xero API streams`

Xero is an accounting service, and its API has a few habits that the rest of the system should not need to know about. This file acts like a translator between Xero and the project’s generic “REST connector” machinery. It defines the list of Xero data streams to read, such as invoices or contacts, and tells the system which field marks each record as updated.

When a sync runs, the connector asks Xero for one stream at a time. Some Xero resources come back in pages of up to 100 records, so the connector keeps asking for page 1, page 2, and so on until Xero returns a short or empty page. Other small resources do not really page, so the connector reads them once. For incremental syncs, where the system only wants records changed since the last run, Xero expects the date in an HTTP header called `If-Modified-Since`; this file converts stored cursor values into that format.

Xero also wraps records inside a named envelope, such as `{"Invoices": [...]}`, and uses different ID field names for different resources, such as `InvoiceID` or `AccountID`. The connector unwraps the records and adds a standard `id` field so downstream code can treat all streams the same. If Xero refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one kind of Xero data, such as accounts or invoices. This keeps the long list of Xero streams compact and consistent.

**Data flow**: It receives a stream name, the matching Xero API object name, an optional field used to track updates, and whether the stream is considered canonical. It packages those choices into a `StreamSpec`, which is the system’s small instruction card for how to sync that stream.

**Call relations**: This helper is used while building the `XERO_STREAMS` list at import time. It hands each stream definition to `StreamSpec.__init__`, so the connector later has a ready-made catalog of Xero resources to read.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: Converts the system’s saved “last seen update time” into the date format Xero expects in the `If-Modified-Since` request header. This matters because Xero does not use a normal query parameter for incremental reads.

**Data flow**: It takes a cursor value, which may be empty, a Unix timestamp number, an ISO-style date string, or another date-like string. Empty or unusable values become `None`; valid timestamps or parsed dates become a GMT string like `Tue, 15 Nov 1994 08:12:31 GMT`; unparseable non-empty text is passed through unchanged.

**Call relations**: When `XeroConnector.paginate` is about to request updated records only, it calls this function to prepare the header value. Internally, this helper uses Python date parsing and timestamp conversion to normalize the time before the HTTP request is sent.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: Stores the optional Xero tenant ID, which identifies the specific Xero organization to read from. Xero may grant access to more than one organization, so this value tells requests which one to use.

**Data flow**: It receives an optional tenant ID string when the connector is created. It saves that value on the connector instance so later HTTP client setup can include it in request headers.

**Call relations**: This runs when someone constructs a `XeroConnector`. The stored tenant value is later read by `XeroConnector._make_client` when preparing the HTTP client used for Xero API calls.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Xero and, when available, adds the required `xero-tenant-id` header. The header is like writing the organization name on every envelope sent to Xero.

**Data flow**: It receives the base API URL and a credential object supplied by the authentication proxy. It first lets the parent REST connector create the normal authorized HTTP client, then adds the tenant header if this connector was given one, and returns the prepared client.

**Call relations**: This method fits into the generic REST connector setup path. The broader connector framework asks for a client before syncing; this method customizes that client for Xero-specific requirements before `paginate` uses it to make GET requests.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from one Xero stream, page by page when needed, and yields batches of records to the sync system. It also applies Xero’s incremental-sync rule by sending `If-Modified-Since` as a header.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero path and response envelope name, converts the cursor into a Xero-friendly date header if needed, then sends GET requests. For non-paged streams it reads once; for paged streams it keeps increasing the page number until there are no records or fewer than 100. It yields each non-empty batch of raw records. If Xero returns 401 or 403, it turns that refusal into `StreamSkipped`; other HTTP errors are allowed to continue upward.

**Call relations**: This is the main read loop for the connector. During a sync, the framework calls it for each `StreamSpec`; it calls `_cursor_to_rfc1123` when an incremental cursor is present, uses `httpx.AsyncClient.get` to contact Xero, and raises `StreamSkipped` when access to a stream is refused.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adds a standard `id` field to Xero records when Xero used a resource-specific ID name instead. This lets the rest of the system identify records without knowing every Xero naming convention.

**Data flow**: It receives one record and the stream it came from. If the record already has `id`, it returns it unchanged. Otherwise it looks up the stream’s Xero-specific ID field, reads that value from the record, and returns a shallow copy with `id` set to that value as text. If no matching ID field or value exists, it leaves the record unchanged.

**Call relations**: This runs after records have been fetched by the connector’s read path. It is the cleanup step that makes output from different Xero streams look consistent before downstream sync code stores or compares the records.

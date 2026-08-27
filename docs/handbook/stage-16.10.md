# Finance, Billing, and Commerce Providers  `stage-16.10`

This stage is a set of behind-the-scenes “connectors” for finance and commerce services. It is used during source syncing, when the system reaches out to outside products, reads their data, and turns it into a common stream of records that the rest of the codebase can store, search, or index. Each file is like an adapter plug for a different service.

Brex reads spend-management data such as expenses, transactions, users, vendors, budgets, and departments. Chargebee and Recurly read subscription billing data, including customer and billing objects, while handling each service’s login and page-by-page result format. QuickBooks and Xero read accounting records such as accounts, contacts, invoices, and payments, asking their APIs for large result sets safely. Square reads commerce data such as customers, payments, locations, catalog items, orders, and inventory counts. Stripe reads a wide range of payment and subscription records, including connected-account data.

Together, these providers hide the differences between many financial APIs and present one steady record stream to the sync system.

## Files in this stage

### Spend Management
Brex provides the stage's spend-management reader for transactions, expenses, users, vendors, budgets, and departments.

### `extensions/sources/ufo_ext_sources/providers/brex.py`

`io_transport` · `source sync / API fetching`

Brex exposes business spending data through a web API. This file is the adapter that knows which Brex web addresses to call, what kinds of records are available, and how to walk through Brex’s paged responses. Without it, the system would not know how to fetch Brex records or how to divide them into meaningful streams like “transactions” or “vendors.”

The file first defines the Brex streams. A stream is one kind of object to sync, like a table in a spreadsheet. Each stream has a name, a main identifying field, and sometimes a date-like cursor field. A cursor field is a value the sync can use as a progress marker, like a bookmark in a long book. Brex does not support server-side filtering for most of these endpoints, so the connector generally reads full lists and lets the wider system track progress where possible.

The `BrexConnector` class supplies the shared details for talking to Brex: its public name, its base API address, and the stream list. Its main job is pagination. Brex returns results in chunks, each with `items` and a `next_cursor`. The connector asks for 100 records at a time, yields each non-empty batch, then follows `next_cursor` until Brex says there are no more pages. This connector is read-only; it fetches data but does not write anything back to Brex.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a `StreamSpec`, which is the system’s small description card for one Brex data stream. It saves repeated setup code when defining streams like transactions, expenses, or users.

**Data flow**: It receives a stream name and optional details such as the primary key field, cursor field, and whether the stream is considered canonical. It packages those details into a `StreamSpec` object, setting the source object name to match the stream name and using the cursor field as the created-at field when present. The result is a ready-to-use stream description that the connector can advertise to the sync system.

**Call relations**: This helper is used while the file builds the fixed Brex stream catalog. It hands the completed stream descriptions to the module-level stream list, which `BrexConnector` later exposes through its `streams_list` setting.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function fetches all pages for one Brex stream. Someone uses it when they want the records for a stream, but Brex may return those records in several chunks instead of all at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor argument. It looks up the Brex API path for that stream, requests up to 100 records, pulls the `items` list out of Brex’s response, and yields that list if it contains records. Then it reads Brex’s `next_cursor` value and repeats the request with that cursor until there is no next page. If the stream is unknown, it stops with an error instead of guessing the wrong endpoint.

**Call relations**: The broader source framework calls this method when it is syncing a Brex stream. Inside the loop, it relies on the inherited `_get` method to make the authenticated web request, then uses `list_or_empty` to safely treat a missing or non-list `items` field as an empty list. Each yielded batch is handed back to the framework so the records can be processed downstream.

*Call graph*: 1 external calls (list_or_empty).


### Subscription Billing
Chargebee and Recurly cover recurring-revenue systems by defining billable resources, authentication, pagination, and response unwrapping.

### `extensions/sources/ufo_ext_sources/providers/chargebee.py`

`io_transport` · `during source sync, whenever Chargebee records are fetched`

Chargebee stores billing data such as customers, subscriptions, invoices, items, and transactions behind many HTTP API endpoints. This connector is the project’s read-only bridge to those endpoints. Without it, the system would not know where to ask Chargebee for each kind of object, how to prove it is allowed to read the data, or how to keep fetching later pages until all records are collected.

The file starts by defining the available Chargebee streams. A stream is one kind of data the sync can read, such as “customer” or “invoice.” Each stream records useful facts like its main ID field and the timestamp field used for incremental syncing, which means “only fetch records newer than the last run.”

Chargebee’s API returns records in a wrapped shape, like a package inside a box: each item in the list contains a nested object named after the resource. The connector’s `flatten` method opens that box so downstream code sees the real record fields at the top level.

Most streams use the same list-and-next-token pattern: ask for up to 100 records, then follow Chargebee’s `next_offset` token until there is no next page. A few streams are substreams, meaning they must first list a parent object and then fetch children under each parent, such as contacts under customers. If Chargebee rejects access with a permission or authentication error, the connector skips that stream with a clear explanation instead of hiding the problem.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a stream description for one Chargebee object type. This is used to say what the object is called, what field uniquely identifies it, and which timestamp field can be used to resume syncing later.

**Data flow**: It receives a stream name plus optional details such as the source object name, primary key, and cursor fields. It fills in sensible defaults when details are not provided, then returns a `StreamSpec`, which is the system’s compact description of a readable data stream.

**Call relations**: This helper is used while the file is being loaded to build the `CHARGEBEE_STREAMS` list. It hands each completed stream description to the connector class so the rest of the sync system can discover what Chargebee data is available.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Chargebee. It applies the right base URL, timeout settings, request headers, and authentication method.

**Data flow**: It receives a Chargebee base URL and a resolved credential. It trims the URL, prepares JSON/form headers, sets connection and read time limits, then either reuses a broker-provided transport or sends the Chargebee API key as HTTP Basic authentication with an empty password. It returns an asynchronous HTTP client ready to make requests.

**Call relations**: The broader connector framework calls this when a sync run needs a network client. After this function creates the client, pagination methods use that client to call Chargebee endpoints.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Turns Chargebee’s wrapped API records into flatter records that are easier for the rest of the system to store and compare. It also preserves extra fields added by this connector, such as a parent ID on substream records.

**Data flow**: It receives one raw record and the stream description that says which wrapper key to look for. If the record contains a nested object under that key, it copies the nested object and adds any outer fields that are not already present. If the expected wrapper is missing, it returns the record unchanged.

**Call relations**: The sync framework uses this after records are fetched from `paginate`. It prepares records from both normal list streams and parent-child substreams so downstream code sees a consistent shape.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right page-fetching strategy for a requested Chargebee stream. It is the main routing point between ordinary list endpoints and special parent-child endpoints.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous run. It checks the stream name, delegates to the matching pagination helper, and yields pages of raw records. If Chargebee returns an authorization refusal, it turns that into a `StreamSkipped` error with a human-readable reason; other HTTP errors are allowed to surface normally.

**Call relations**: The source sync system calls this when it wants records for a stream. For simple streams it hands off to `_paginate_list`; for special streams it hands off to one of the substream-specific methods. Those helpers yield pages back through this method to the caller.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, adds the “only records after this cursor” filter for incremental syncing.

**Data flow**: It receives a stream description and an optional cursor value. It always starts with a limit of 100 records. If there is a cursor and the stream has a cursor field, it adds a Chargebee-style filter such as `updated_at[after] = cursor`. It returns the parameter dictionary for the HTTP request.

**Call relations**: `_paginate_list` calls this just before asking Chargebee for pages. It keeps the pagination code simple by centralizing the rules for ordinary list request parameters.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches pages from one of Chargebee’s standard list endpoints. This is the common path for most streams, such as customers, invoices, and transactions.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for the stream, builds request parameters, then repeatedly follows Chargebee’s `next_offset` token. Each step yields one page of records from the response’s `list` field.

**Call relations**: `paginate` calls this directly for ordinary streams. The substream methods also call it first to list parent records, such as items, customers, quotes, or subscriptions, before fetching child data.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches attached-item records by first finding items and then asking Chargebee for the attached items under each one. This is needed because attached items are not exposed as one simple global list.

**Data flow**: It receives an HTTP client and an optional cursor. It lists item records, extracts each item ID, skips parents without an ID, and then fetches `/items/{item_id}/attached_items`. Each child record is stamped with the parent `item_id` before being yielded in pages.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This method relies on `_paginate_list` to get parent items and on `_paginate_substream` to walk each child endpoint.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches customer contacts by walking through customers first, then fetching contacts for each customer. This mirrors Chargebee’s API shape, where contacts live under a specific customer.

**Data flow**: It receives an HTTP client and an optional cursor. It lists customer records, extracts each customer ID, ignores any record without a usable ID, and then fetches `/customers/{customer_id}/contacts`. The returned contact records are enriched with the matching `customer_id`.

**Call relations**: `paginate` calls this for the `contact` stream. It uses `_paginate_list` for parent customers and `_paginate_substream` for the repeated contact-list calls below each customer.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches quote line groups by first listing quotes and then asking for the line groups belonging to each quote. This is another parent-child API pattern in Chargebee.

**Data flow**: It receives an HTTP client and an optional cursor. It lists quote records, extracts each quote ID, skips records without an ID, and fetches `/quotes/{quote_id}/quote_line_groups`. Each child record is marked with the parent `quote_id` before being yielded.

**Call relations**: `paginate` calls this when syncing `quote_line_group`. It gets parent quotes through `_paginate_list` and delegates the repeated child pagination to `_paginate_substream`.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches the scheduled-change version of each subscription. Unlike normal substreams, this calls a single-record detail endpoint for each subscription rather than a list of children.

**Data flow**: It receives an HTTP client and an optional cursor. It lists subscriptions, extracts each subscription ID, skips unusable records, then calls `/subscriptions/{id}/retrieve_with_scheduled_changes`. If the response contains a subscription object, it yields it wrapped together with the original `subscription_id`.

**Call relations**: `paginate` calls this for `subscription_with_scheduled_changes`. It depends on `_paginate_list` to find the parent subscriptions, then performs its own detail request for each parent because `_paginate_substream` is designed for list-shaped child endpoints.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the shared page-walking logic for child endpoints that sit under a parent record. It also labels each child with the parent ID so the relationship is not lost.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent-ID field to add, and the parent ID value. It asks Chargebee for pages using the same `list` and `next_offset` pattern as normal endpoints. For each dictionary record, it copies the record, adds the parent ID, and yields only non-empty stamped pages.

**Call relations**: The attached-item, contact, and quote-line-group methods call this after they have found a parent record. It gives those methods one reusable way to fetch child pages and preserve the link back to the parent.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/providers/recurly.py`

`io_transport` · `during source sync, while reading Recurly API streams`

Recurly returns lists of things, such as accounts, invoices, plans, and coupons, in small batches called pages. This file is the connector that repeatedly asks Recurly for those pages until there is no more data, then gives each batch back to the wider sync system. Without it, this project would not know Recurly’s URL format, authentication style, page format, or special cases for nested data.

The file first defines the available streams, meaning the named collections of data that can be copied from Recurly. Some are simple top-level lists, like accounts or subscriptions. Others live underneath a parent record, like notes under an account or unique coupon codes under a coupon. For those child streams, the connector first walks through the parent list, then asks for each parent’s child records. It also adds the parent id onto each child row, like writing the folder name on every paper taken from that folder.

Authentication is also Recurly-specific. Recurly uses HTTP Basic authentication, where the API key is sent as the username, not as a bearer token. The connector also pins a specific Recurly API version in the request headers. If Recurly replies that access is forbidden or unauthorized, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream definition for one Recurly collection. A stream definition tells the sync system the collection’s name, where it comes from in Recurly, how to identify each row, and what field can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults when details are missing, then returns a StreamSpec object that the connector uses later.

**Call relations**: This is used while the file is being loaded to build the RECURLY_STREAMS list. It hands its settings to StreamSpec, which is the shared source framework’s way of describing a readable data collection.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It sets Recurly’s required headers, request time limits, and the correct authentication method.

**Data flow**: It receives a base URL and a credential. It trims the base URL, prepares headers for the pinned Recurly API version, and sets connection and read timeouts. If the credential has a proxy transport, it uses that; otherwise, if it has an API key, it sends that key as the username in HTTP Basic authentication. If neither form of access is present, it raises an error.

**Call relations**: The broader RestConnector machinery calls this when it needs a ready-to-use client for a sync. It relies on httpx objects to perform the actual network requests and returns the configured AsyncClient to the rest of the connector.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s “next page” link into a path the client can request next. It protects the connector from mixing full URLs and relative paths incorrectly.

**Data flow**: It receives a next-link value from Recurly, which may be empty, a relative path, or a full URL. If there is no link, it returns nothing. If it is a full URL, it keeps only the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: Pagination helpers call this after each page when Recurly says more data exists. It is used by top-level pagination, parent-child pagination, account id discovery, and coupon id discovery so they all follow Recurly’s cursor links in the same safe way.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the first set of query parameters for a Recurly list request. It tells Recurly how many records to return, how to sort them, and where to begin for incremental syncing.

**Data flow**: It receives a stream definition and an optional cursor value from a previous sync. It creates parameters with the fixed page size, ascending order, and a sort field. If the stream has a cursor field and a cursor was supplied, it adds begin_time so Recurly starts at that point in time.

**Call relations**: Both top-level pagination and per-parent pagination call this before their first request. After that first request, Recurly’s own next link carries the continuing cursor, so the pagination functions stop sending these initial parameters.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Recurly stream. It decides whether a stream is a normal list, a special parent-child list, or a filtered coupon parent list, then yields pages of records.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. For parent-child streams, it delegates to the per-parent paginator. For the special unique_coupons_parent stream, it reads coupons and keeps only bulk coupons. For normal streams, it reads the stream’s top-level API path. It yields each non-empty page of records. If Recurly refuses access with a 401 or 403 response, it turns that into a StreamSkipped error with a helpful explanation.

**Call relations**: The source sync framework calls this when it wants records for a stream. This function chooses the right lower-level pagination helper and passes record pages back upward to the framework.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a simple Recurly collection that lives at one API path, such as accounts, invoices, or plans. It keeps following Recurly’s next-page links until the collection is exhausted.

**Data flow**: It receives a client, stream definition, API path, and optional cursor. It builds the first query, requests the current page, yields the records if any are present, and then checks whether Recurly says there are more pages. If so, it converts the next link into a usable path and repeats.

**Call relations**: RecurlyConnector.paginate calls this for ordinary streams and for the coupon parent stream before filtering. It uses _initial_query to start correctly and _next_path to follow Recurly’s paging links.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields just their ids. Those ids are needed before the connector can fetch account-specific child records, such as notes or billing information.

**Data flow**: It starts at the /accounts path with page size and sorting parameters. For each page returned by Recurly, it looks at each row and yields the id if one exists. It then follows the next-page link until there are no more accounts.

**Call relations**: The per-parent paginator calls this when it needs to fetch child data under accounts. This helper supplies the parent ids, and _paginate_per_parent uses those ids to build each child URL.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through Recurly coupons and yields ids only for bulk coupons. Bulk coupons are the ones that can have unique coupon codes underneath them.

**Data flow**: It starts at the /coupons path with page size and sorting parameters. For each returned coupon row, it checks that the row is a dictionary, has an id, and has coupon_type set to bulk. Matching coupon ids are yielded. It follows Recurly’s next-page links until all coupons have been checked.

**Call relations**: The per-parent paginator calls this when reading unique coupon codes under coupons. It filters parent coupons first so the connector does not ask for unique-code children on coupon types that should not have them.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that are stored under another Recurly object, such as account notes under each account. It first finds the parent ids, then reads each parent’s child records page by page.

**Data flow**: It receives the client, stream definition, parent path, child path, field name for the parent id, and optional cursor. It chooses either account ids or bulk coupon ids as parents. For each parent id, it builds the child URL, sends the first request with the stream’s initial query, then follows next-page links. Before yielding child records, it writes the parent id into each child row when that field is missing.

**Call relations**: RecurlyConnector.paginate calls this for streams listed as per-parent streams. This function relies on _account_ids or _coupon_ids to discover parents, _initial_query to begin each child listing, and _next_path to keep moving through Recurly’s child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### Accounting Platforms
QuickBooks and Xero provide accounting-system readers for ledgers, contacts, invoices, payments, and related financial records.

### `extensions/sources/ufo_ext_sources/providers/quickbooks.py`

`io_transport` · `during source sync pagination and record preparation`

QuickBooks Online does not offer a simple “give me all invoices” web address for each kind of record. Instead, this connector must send SQL-like queries to one shared `/query` endpoint, a bit like asking a librarian for “all books from this shelf, starting at item 101, give me 100.” This file builds those queries for accounts, customers, invoices, bills, payments, journal entries, and many other QuickBooks objects.

The file lists all supported streams, where a stream means one kind of data the sync can read. Most streams use QuickBooks’ `MetaData.LastUpdatedTime` field as a cursor, meaning the sync can ask only for records changed since the last successful run. A few reference lists, such as payment methods and tax agencies, do not use that cursor and are read in full.

`QuickBooksConnector` does the live work. During a sync, it builds a query, sends it through the shared REST connector, reads records from `QueryResponse`, yields one page at a time, and keeps moving the start position forward until QuickBooks returns fewer than 100 records. If QuickBooks refuses access with a 401 or 403 error, the connector reports that this stream should be skipped instead of crashing the whole sync. It also flattens the nested cursor field so the rest of the system can track progress with a simple key.

#### Function details

##### `_stream`  (lines 32–47)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description of one QuickBooks data stream, such as invoices or customers. It keeps the stream definitions short and consistent by filling in the common QuickBooks rules, including the primary key and timestamp fields.

**Data flow**: It receives a friendly stream name, the matching QuickBooks object name, an optional cursor field, and whether the stream is considered canonical. It puts those values into a `StreamSpec`, which is the system’s small instruction card for how to sync that record type. The result is returned and later collected into the connector’s stream list.

**Call relations**: This function is used while the file is loaded to build `QUICKBOOKS_STREAMS`. Its only handoff is to `StreamSpec`, which stores the sync instructions that `QuickBooksConnector` later uses when building queries and reading pages.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 88–95)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This builds the SQL-like QuickBooks query string for one page of one stream. It is what turns the system’s idea of “sync invoices after this timestamp” into the exact text QuickBooks expects.

**Data flow**: It receives a stream description, an optional cursor value from the previous sync, and the starting row number for the page. It starts with `SELECT * FROM <QuickBooks object>`, adds a `WHERE` and `ORDER BY` clause when incremental syncing is possible, escapes single quotes in the cursor so the query stays valid, and adds the page size instructions. It returns the finished query string.

**Call relations**: `paginate` calls this each time it needs the next page from QuickBooks. The query it returns is sent to the QuickBooks `/query` endpoint, so this function is the translation step between the connector’s paging loop and QuickBooks’ query language.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 97–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads records from QuickBooks one page at a time. It exists so large streams can be synced steadily instead of trying to fetch everything in one huge request.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It starts at row 1, builds a query, sends it to `/query`, pulls the relevant records out of `QueryResponse`, and yields each non-empty page to the caller. If a full page of 100 records comes back, it asks for the next page; if fewer come back, it stops. If QuickBooks returns 401 or 403, it changes that failure into a `StreamSkipped` signal; other HTTP errors are passed upward unchanged.

**Call relations**: During a sync, the broader source framework calls this method to get pages for a particular QuickBooks stream. Inside the loop it relies on `_build_query` to make each request. When QuickBooks refuses access, it hands back a `StreamSkipped` exception so the sync runner can treat that stream as unavailable rather than as an ordinary page of data.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 121–124)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This prepares each QuickBooks record so the sync engine can easily find the update cursor. QuickBooks stores the cursor inside nested data, and this function copies it to a flat key.

**Data flow**: It receives one record and the stream description. If the stream’s cursor field contains a dot, such as `MetaData.LastUpdatedTime`, it reads that nested value using `get_path` and returns a new record that also contains the full cursor name as a top-level key. If no flattening is needed, it returns the original record unchanged.

**Call relations**: After pages are fetched, the source framework can call this before advancing its saved watermark, which is the remembered “last seen update time.” It delegates the nested lookup to `get_path`, then hands back a record shaped in the simple format the rest of the sync system expects.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/xero.py`

`io_transport` · `source sync / API fetching`

Xero is an online accounting service, and its API has a few habits that are different from other services. This file is the adapter that hides those differences from the rest of the project. It lists the Xero resources that can be synced, says which field should be treated as each record’s unique ID, and knows how to ask Xero for pages of results.

The main idea is simple: the wider system asks for a stream, such as invoices, and this connector fetches that stream from Xero. Xero wraps lists inside named envelopes, for example an invoice response contains an "Invoices" list. The connector opens that envelope and yields plain batches of records. For most streams it walks through pages using page numbers until Xero returns a short page. Some small streams do not really use paging, so the connector fetches them once.

For incremental sync, meaning “only give me things changed since last time,” Xero expects a special request header called If-Modified-Since rather than a normal query parameter. This file converts saved cursor values into the date format Xero expects. It also adds Xero’s tenant header when a tenant ID is available, because one Xero login can grant access to more than one organization. If Xero refuses access, the connector marks that stream as skipped instead of crashing the whole source.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one Xero data stream, such as accounts or invoices. This keeps the stream list concise and ensures each stream uses the same default settings unless it needs a special case.

**Data flow**: It receives a stream name, the matching Xero API object name, optional cursor information, and whether the stream is considered canonical. It packages those details into a StreamSpec with a shared primary key named "id" and returns that specification for the sync system to use later.

**Call relations**: This helper is used while the file builds the XERO_STREAMS list. It hands its collected settings to StreamSpec, which is the common stream description type understood by the rest of the source framework.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: Converts a saved “last updated” cursor into the date text format Xero expects in the If-Modified-Since request header. This matters because Xero will not understand every date style the rest of the system might store.

**Data flow**: It receives a cursor value as text or nothing. If the cursor is empty, it returns nothing. If the cursor looks like a Unix timestamp, it turns that number into a UTC date string. If it looks like an ISO date, it parses it and formats it as GMT. If parsing fails, it returns the original text so the caller can still send it through.

**Call relations**: XeroConnector.paginate calls this when it is about to make an incremental request. The helper relies on Python date parsing functions to normalize different cursor shapes before the request goes to Xero.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: Stores the optional Xero tenant ID for later API requests. A tenant ID identifies which Xero organization should be read when the same credential can access more than one.

**Data flow**: It receives an optional tenant ID when the connector is created. It saves that value on the connector instance so later client setup can add it to request headers. It does not return a separate value.

**Call relations**: This runs when something creates a XeroConnector. Later, XeroConnector._make_client reads the saved tenant ID and adds it to the HTTP client if it exists.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Xero and adds Xero’s tenant header when needed. Without this header, Xero may not know which organization’s accounting data to return.

**Data flow**: It receives the base API URL and a credential object supplied by the authentication proxy. It first asks the parent RestConnector to create the normal authenticated client. Then, if this connector was given a tenant ID, it adds that ID to the client’s default headers. It returns the ready-to-use async HTTP client.

**Call relations**: The source framework calls this when it needs a client for API calls. It extends the parent connector’s setup rather than replacing it, then XeroConnector.paginate uses the resulting client to fetch stream data.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records for one Xero stream, one batch at a time. It knows Xero’s paging rules, incremental-sync header, response envelope names, and access-refusal behavior.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero path from the stream’s source object. If there is a cursor, it converts it into an If-Modified-Since header. For non-paged streams it makes one GET request, opens the response envelope, and yields the records if any exist. For paged streams it requests page 1, page 2, and so on, yielding each batch until there are no records or fewer than Xero’s page size. If Xero returns 401 or 403, it raises StreamSkipped to say this stream cannot be read with the current access grant.

**Call relations**: This is the main read loop used by the sync framework for Xero streams. It calls _cursor_to_rfc1123 before incremental requests, uses httpx.AsyncClient.get to contact Xero, and raises StreamSkipped when Xero refuses access so the larger sync can continue in a controlled way.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adds a standard "id" field to Xero records when Xero uses a resource-specific ID name instead. This lets the rest of the sync system key records the same way across all streams.

**Data flow**: It receives one record and the stream it came from. If the record already has "id", it returns it unchanged. Otherwise it looks up the stream’s Xero-specific ID field, such as InvoiceID or ContactID. If that value exists, it returns a copy of the record with an added string "id" field. If no suitable ID is found, it returns the original record.

**Call relations**: The broader connector framework calls this after records are fetched and before they are stored or emitted in the common format. It complements XeroConnector.paginate: paginate gets the raw records from Xero, and flatten makes each record easier for the rest of the system to identify.


### Commerce and Payments
Square and Stripe read commerce catalogs, orders, customers, subscriptions, payments, invoices, and connected-account records.

### `extensions/sources/ufo_ext_sources/providers/square.py`

`io_transport` · `during source sync, when Square streams are being read`

Square exposes different kinds of data in different ways. Some lists are fetched with simple GET requests, some require POST search requests, and some use location IDs before they can be searched. This file hides those differences behind one connector, so the rest of the project can ask for a stream like “payments” or “orders” without knowing the Square API details.

The main class, `SquareConnector`, is a read-only connector. It creates an HTTP client for Square and adds the required Square API version header, which is like telling Square which edition of its rulebook this connector expects. It also defines the streams this connector can read and how each stream should be paged through.

The central method is `paginate`. It looks at the requested stream name and sends the work to the right helper. Locations and inventory counts are fetched once. Customers, payments, and refunds use cursor-based GET pages. Catalog records use Square’s catalog search endpoint. Orders first fetch locations, then search orders across those locations.

If Square rejects access with a 401 or 403 status, this connector turns that into `StreamSkipped`, meaning the sync can skip that stream rather than crash the whole run. That usually means the credential is wrong or does not have the needed permission.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Square and adds the required Square API version header. Someone would use it indirectly when the connector starts making Square requests.

**Data flow**: It receives a base URL and a credential. It first lets the shared REST connector create the normal authorized client, then adds `Square-Version` to the request headers. It returns a ready-to-use asynchronous HTTP client that will identify the Square API version on every request.

**Call relations**: This is part of the connector setup before any stream is read. It relies on the parent REST connector for the basic client and authentication, then adds Square-specific setup so later methods like `paginate` can make valid API calls.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the traffic director for reading Square streams. It decides which Square API pattern to use for the requested stream and yields records in pages, so the rest of the sync system gets a consistent flow of data.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks where a previous sync left off. It checks the stream name, calls the matching helper, and yields each non-empty page of records. If the stream is unknown, or if Square refuses access with an authorization error, it raises `StreamSkipped` so that stream can be skipped cleanly.

**Call relations**: The broader sync system calls this when it wants records for a Square stream. `paginate` then hands off to `_locations`, `_cursor_get`, `_catalog`, or `_orders` depending on the stream, and uses `records_at` directly for inventory counts. It also wraps Square permission failures in a clearer skip signal for the rest of the runner.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use ordinary list endpoints with cursor-based paging, such as customers, payments, and refunds. A cursor is a marker that says “continue from around here” rather than starting from the beginning.

**Data flow**: It receives a client, stream details, and an optional saved cursor. For payments and refunds, it sends the cursor as Square’s `begin_time` filter. For other cursor-aware streams, it fetches pages and then filters out records whose cursor field is not newer than the saved cursor. It yields only pages that still contain records after filtering.

**Call relations**: `paginate` calls this for the GET-style Square streams. This helper delegates the repetitive page-walking work to the shared REST paging method from the parent connector, then applies Square-specific filtering before handing records back to `paginate`.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either item records or category records. It exists because Square catalog data is not read with a simple list URL; it must be requested through a search endpoint with an object type.

**Data flow**: It receives a client, stream details, and an optional saved cursor. It translates the stream name into the Square catalog object type, sends search requests with a page limit, and follows Square’s returned continuation cursor until there are no more pages. For incremental reads, it filters out catalog records whose update time is not newer than the saved cursor, then yields the remaining records.

**Call relations**: `paginate` calls this when the requested stream is a catalog stream. Inside the loop, this function posts to Square’s catalog search endpoint, uses `records_at` to pull the list of catalog objects out of the response, and then yields usable pages back to `paginate`.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations are useful on their own as a stream, and they are also needed before searching for orders.

**Data flow**: It receives an HTTP client. It sends a GET request to Square’s locations endpoint, extracts the `locations` list from the response, and returns that list as records.

**Call relations**: `paginate` calls this directly when syncing the locations stream. `_orders` also calls it first because Square order searches need location IDs before they can ask for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all available account locations. It does the extra setup Square requires: first find the locations, then search orders for those location IDs.

**Data flow**: It receives a client and an optional saved cursor. It fetches locations, keeps only valid string location IDs, and stops if there are none. It then repeatedly posts order search requests with those location IDs, an optional start time based on the cursor, and Square’s continuation cursor for later pages. Each response is reduced to its `orders` list, and non-empty pages are yielded.

**Call relations**: `paginate` calls this for the orders stream. `_orders` first depends on `_locations` to discover where to search, then uses `records_at` to extract orders from each Square response before returning those pages to the sync flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### `extensions/sources/ufo_ext_sources/providers/stripe.py`

`io_transport` · `source sync`

Stripe exposes data through many API endpoints, but most of them follow the same pattern: ask for up to 100 records, check whether there are more, then ask again starting after the last record seen. This file wraps that pattern so the rest of the system does not need to know Stripe’s paging rules or endpoint names.

The file first defines a catalog of Stripe streams. A stream is a named collection of records, such as customers or invoices, along with hints like its ID field and time field. The `StripeConnector` then uses that catalog to make HTTP requests to Stripe. It adds Stripe’s required API-version header, builds endpoint paths, converts saved cursors into Unix timestamps, and reads page after page.

Some Stripe data is nested under a parent object. For example, a customer has payment methods, and an invoice has line items. For these, the connector first walks through the parent records, then asks Stripe for each parent’s children, stamping the parent ID onto each child record so the relationship is not lost. This is like copying the folder label onto every paper inside the folder before filing the papers separately.

If Stripe rejects a stream because the key lacks permission, the connector skips that stream with a clear message instead of crashing the whole sync. This file is read-only; it never writes back to Stripe.

#### Function details

##### `_stream`  (lines 89–107)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: Creates a `StreamSpec`, which is the small description object the sync system uses to know what a Stripe collection is called and how to track it over time. It keeps the long stream catalog readable by filling in common defaults.

**Data flow**: It receives a stream name plus optional details such as the Stripe object path, primary key, cursor field, and whether the stream is considered canonical. It passes those details into `StreamSpec` and returns the finished stream description.

**Call relations**: This helper is used while building the `STRIPE_STREAMS` list at import time. The connector later reads those stream descriptions when deciding which Stripe endpoint to call and how to interpret each record.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 182–185)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Stripe and adds the pinned Stripe API version header. Pinning the version helps keep Stripe responses stable even if Stripe changes its default API behavior later.

**Data flow**: It receives a base URL and a credential object. It asks the parent `RestConnector` to create the basic HTTP client, adds the `Stripe-Version` header, and returns the prepared client.

**Call relations**: This runs when the connector is being prepared to make Stripe requests. After it returns, all later pagination and fetch helpers use the client with the correct Stripe version already attached.


##### `StripeConnector._list_path`  (lines 188–189)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: Builds the normal Stripe list endpoint path for a stream. For example, a stream whose source object is `customers` becomes `/v1/customers`.

**Data flow**: It receives a stream description, reads its `source_object`, prefixes it with `/v1/`, and returns that URL path as text.

**Call relations**: The main pagination method and several child-stream helpers call this whenever they need the ordinary list endpoint for a stream or parent stream before handing that path to `_page_loop`.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 192–204)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: Converts a saved cursor value into the Unix timestamp format Stripe expects for created-time filters. A Unix timestamp is a count of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date string. It returns an integer timestamp when it can understand the value, or `None` when there is no usable cursor.

**Call relations**: `_page_loop` calls this before making requests. If conversion succeeds and the stream uses Stripe’s `created` field, `_page_loop` asks Stripe only for records created at or after that time.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 206–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to read pages for a given Stripe stream. It is the main doorway the rest of the sync system uses when it wants records from Stripe.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks whether the stream is a special nested stream, an external-account stream, a query-based child stream, or a normal top-level stream, then yields lists of records page by page. If Stripe responds with a permission or authentication refusal, it turns that into a `StreamSkipped` signal.

**Call relations**: The sync runner calls this to fetch data. Depending on the stream name, it delegates to `_paginate_external_accounts`, `_paginate_substream`, `_paginate_substream_query`, or directly to `_page_loop`; `_list_path` is used for the normal endpoint case.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 234–265)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one Stripe list endpoint from start to finish, following Stripe’s `has_more` and `starting_after` paging rules. This is the shared engine behind almost every stream.

**Data flow**: It receives an HTTP client, an endpoint path, a stream description, an optional cursor, and optional extra query parameters. It builds request parameters, fetches a page, normalizes each record with `_browse_record`, yields non-empty pages, and keeps going until Stripe says there are no more records or there is no usable last ID.

**Call relations**: This helper is called by the main `paginate` method and by all the nested-stream readers. Before requesting data it uses `_cursor_to_unix`; after receiving data it sends each record through `_browse_record` so downstream code sees more consistent timestamp fields.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 268–288)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Lightly normalizes one Stripe record so time fields are easier for the rest of the system to use. It preserves the original data while adding or converting standard timestamp fields where possible.

**Data flow**: It receives a record dictionary and the stream description. It copies the record, converts numeric `created_at` and `updated_at` values into readable UTC date-time strings, and fills missing `created_at` or `updated_at` from Stripe’s `created` or cursor field when those are numeric. It returns the normalized copy.

**Call relations**: `_page_loop` calls this for every record returned by Stripe. The cleaned record then becomes part of the page yielded back to whichever pagination path requested it.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 290–312)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections that live under a parent object path, such as invoice line items under invoices or payment methods under customers. It keeps the parent-child link by adding the parent ID to each child row.

**Data flow**: It receives an HTTP client and a child stream description. It finds the parent stream, pages through parent records, builds the child endpoint for each parent ID, fetches the child pages, adds parent ID fields and selected parent timestamp fields, then yields the enriched child records.

**Call relations**: `paginate` calls this for streams listed as path-based substreams. It uses `_stream_spec` to find the parent description, `_parent_pages` to read parents, and `_page_loop` to read each parent’s children.

*Call graph*: calls 3 internal fn (_page_loop, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 314–319)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides pages of parent records for a child-stream sync. It hides whether the parent is a normal top-level Stripe stream or is itself produced through a query-based fan-out.

**Data flow**: It receives an HTTP client and a parent stream description. If that parent stream is query-based, it returns the query substream paginator; otherwise it returns the normal page loop for the parent’s list endpoint.

**Call relations**: `_paginate_substream` calls this before fetching child records. It may hand off to `_paginate_substream_query` for multi-level cases, or to `_page_loop` after building the parent path with `_list_path`.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 321–335)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections that Stripe exposes through a shared endpoint plus a parent ID query parameter. For example, subscription items are fetched by asking for `/v1/subscription_items` with a specific subscription ID.

**Data flow**: It receives an HTTP client and the child stream description. It looks up the parent stream, pages through all parents, and for each parent ID requests the child endpoint with the right query parameter. Each child row is returned with an added `<query_field>_id` value so the parent relationship is visible.

**Call relations**: `paginate` calls this directly for query-based child streams. `_parent_pages` can also call it when a deeper substream needs parents that are themselves query-derived, such as usage records under subscription items.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 337–350)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads external bank accounts or cards attached to Stripe connected accounts. These records need special treatment because they are reached through each account’s own external-accounts endpoint.

**Data flow**: It receives an HTTP client and the external-account stream description. It first pages through Stripe accounts, then for each account ID fetches `/v1/accounts/{account_id}/external_accounts`, and yields each child record with the account ID stamped onto it.

**Call relations**: `paginate` calls this for the external bank account and external card streams. It uses `_stream_spec` to get the accounts stream, `_list_path` and `_page_loop` to read accounts, and `_page_loop` again to read each account’s external accounts.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 352–353)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream description with a given name from the connector’s Stripe stream catalog. Other helpers use it when they need to move from a child stream to its parent stream.

**Data flow**: It receives a stream name, searches the `STRIPE_STREAMS` list, and returns the matching `StreamSpec`. If no stream matches, the normal Python lookup behavior raises an error.

**Call relations**: The nested pagination helpers call this when they need details about parent streams such as accounts, customers, subscriptions, or invoices. The returned description is then used to build paths and run page loops.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).

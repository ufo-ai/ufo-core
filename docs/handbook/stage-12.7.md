# Finance, billing, accounting, and commerce source connectors  `stage-12.7`

This stage is the system’s set of “adapters” for money-related services. It is used during source sync, when the system is pulling data in from outside products. Each connector knows how to talk to one service’s web API, which is the service’s online doorway for requesting data, and reshapes the answers into the common stream format the rest of the system understands.

Brex reads spend-management records such as transactions, expenses, users, vendors, budgets, and departments. Chargebee and Recurly bring in subscription-billing data like customers, subscriptions, invoices, items, and transactions. QuickBooks Online and Xero cover accounting records, including accounts, contacts, invoices, payments, and other bookkeeping objects. Square reads commerce data such as customers, payments, locations, catalog items, orders, refunds, and inventory counts. Stripe handles many payment and billing records, including customers, invoices, subscriptions, payments, and connected-account data.

Together, these files act like plug adapters: each one fits a different outside system, but all deliver records in the same shape for syncing.

## Files in this stage

### Spend management
Brex provides the spend-management connector for transactions, expenses, users, vendors, budgets, and departments.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `during source sync`

Brex exposes business spending data through a web API. This file is the adapter that tells the broader source-sync system where to find each kind of Brex data and how to walk through Brex’s paged responses. A paged response is like a long report split across many sheets: each API call returns one sheet of items plus a token that says where the next sheet starts.

The file first defines the Brex streams the system knows about, such as transactions, expenses, users, departments, vendors, and budgets. Each stream has a name, a primary key that identifies records, and sometimes a cursor field. A cursor field is a date-like marker the system can remember as a rough “how far have we seen?” point, even though most Brex endpoints do not let the request ask only for newer records.

The `BrexConnector` class supplies the Brex base web address and the list of streams. Its main job here is pagination: for a requested stream, it looks up the right Brex endpoint, asks for up to 100 records, yields any returned records to the sync system, then follows Brex’s `next_cursor` token until there are no more pages. If the stream is unknown, it fails clearly instead of silently doing the wrong thing.

#### Function details

##### `_stream`  (lines 33–42)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a stream description for one kind of Brex object. It keeps the stream list easy to read by filling in common defaults, such as using `id` as the usual record identifier.

**Data flow**: It receives a stream name and optional details like the primary key, cursor field, and whether the stream is considered canonical. It passes those values into `StreamSpec`, which creates the structured stream description. The result is a `StreamSpec` object that the connector later advertises as something it can sync.

**Call relations**: At file load time, this helper is used to build the `BREX_STREAMS` list. Inside, it hands the actual construction work to `StreamSpec`, so the rest of the connector can work with a standard stream description rather than loose strings and settings.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 61–78)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches every page of records for one Brex stream. It is the part that knows Brex’s paging style: ask for a page, read the returned items, then use Brex’s next-page token until there is no token left.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. It looks up the Brex API path for that stream, requests pages with a limit of 100 records, and adds Brex’s `cursor` parameter when continuing to the next page. Each response’s `items` value is cleaned into a safe list using `list_or_empty`; non-empty batches are yielded to the caller. The function stops when Brex returns no `next_cursor`, and it raises an error if the stream has no known endpoint.

**Call relations**: This method is the Brex-specific paging implementation used by the shared REST connector flow when it needs records from Brex. During each page, it relies on `list_or_empty` to turn a missing or non-list `items` field into an empty list, so bad or empty responses do not confuse the batch-yielding loop.

*Call graph*: 1 external calls (list_or_empty).


### Subscription billing
Chargebee and Recurly stream subscription-billing objects such as customers, subscriptions, invoices, items, and transactions.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `during source sync, while reading Chargebee streams`

Chargebee is an online billing service, and this file is the project’s read-only connector for it. Without this file, the system would not know which Chargebee endpoints exist, how to authenticate, how to walk through paginated results, or how to turn Chargebee’s nested response shape into records that other parts of the sync pipeline can use.

The file starts by listing the Chargebee streams the system can read. A stream is one kind of data, like customers or invoices. Most streams map directly to one Chargebee list endpoint. A few are substreams: they can only be fetched by first reading a parent record, then asking Chargebee for the child records attached to that parent. For example, contacts are fetched customer by customer.

The `ChargebeeConnector` class supplies the actual behavior. It creates an HTTP client using Chargebee’s required Basic authentication, where the API key is used as the username and the password is empty. It requests records in pages, follows Chargebee’s `next_offset` token until there are no more pages, and uses an optional cursor so later syncs can ask only for records changed after a previous point in time.

Chargebee wraps each returned record inside a resource-named envelope, such as `{customer: {...}}`. The connector’s `flatten` step unwraps that envelope so the record is easier to work with. If Chargebee refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 57–71)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a `StreamSpec`, which is the project’s description of one readable Chargebee data type. It keeps the stream list compact and consistent, so each stream can declare its name, key field, cursor field, and whether it is considered a core stream.

**Data flow**: It receives a stream name and optional details such as the Chargebee object name, primary key, cursor field, and canonical flag. It fills in sensible defaults when details are not provided, then returns a `StreamSpec` object that the connector later uses to know how to sync that stream.

**Call relations**: It is used while building the `CHARGEBEE_STREAMS` list at import time. Each returned `StreamSpec` becomes part of the connector’s menu of Chargebee streams.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 112–130)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the web client used to talk to Chargebee. It sets the base address, timeouts, headers, and authentication so later code can make API calls without repeating those details.

**Data flow**: It receives a Chargebee base URL and a resolved credential. It trims the URL, prepares JSON/form headers, and sets connection and read time limits. If the credential supplies a custom transport, it uses that unchanged; otherwise, if it has a direct API key, it uses that key as HTTP Basic authentication with an empty password. It returns an asynchronous HTTP client ready to send requests, or raises an error if no usable authentication is present.

**Call relations**: The broader connector framework calls this when it needs a client for a sync run. The returned client is then passed into pagination methods such as `ChargebeeConnector.paginate` and the lower-level page readers.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 132–144)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This turns Chargebee’s wrapped records into simpler records. Chargebee often returns something like `{customer: {...}}`; this function lifts the inner customer data to the top level so downstream code can read fields like `id` and `updated_at` directly.

**Data flow**: It receives one raw record and the stream description that says which envelope name to look for. If the expected envelope contains a dictionary, it copies that inner dictionary and preserves any extra top-level fields, such as a parent ID added for a substream. It returns the flattened record. If the expected envelope is missing or not a dictionary, it returns the original record unchanged.

**Call relations**: This is part of the connector’s normal record-cleaning step after pages are fetched. It complements the pagination methods, which retrieve Chargebee’s raw page entries in the API’s native wrapped format.


##### `ChargebeeConnector.paginate`  (lines 146–177)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Chargebee stream. Given a stream name, it chooses the right paging strategy and yields batches of records until that stream is exhausted.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. It checks whether the stream is one of the special child streams, a normal list stream, or an unknown stream. For known streams, it calls the matching pagination helper and yields each page it receives. If Chargebee responds with 401 or 403, it turns that refusal into a `StreamSkipped` error with a clear explanation; other HTTP errors are allowed to continue upward.

**Call relations**: The sync framework calls this when it wants records for a particular Chargebee stream. This function then hands work to `ChargebeeConnector._paginate_list` for ordinary streams or to one of the substream methods for parent-child data.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 180–186)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, adds the incremental-sync filter that asks Chargebee for records after the saved cursor.

**Data flow**: It receives a stream description and an optional cursor value. It always starts with `limit` set to the connector’s page size. If both a cursor value and a cursor field are available, it adds a Chargebee-style `field[after]` parameter. It returns the completed parameter dictionary.

**Call relations**: `ChargebeeConnector._paginate_list` calls this just before requesting pages from a Chargebee list endpoint. It is the small helper that keeps incremental filtering consistent across ordinary streams.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 188–200)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a standard Chargebee list endpoint, such as customers or invoices, page by page. It follows Chargebee’s `next_offset` token until there are no more pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the API path for the stream, builds request parameters, and asks the base REST connector to fetch cursor-based pages from the response’s `list` field while following `next_offset`. It yields each page of raw records as it arrives.

**Call relations**: `ChargebeeConnector.paginate` uses this for ordinary streams. The substream methods also use it first to read parent records, such as items, customers, quotes, or subscriptions, before fetching children for each parent.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 202–219)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads attached items, which are child records under each Chargebee item. Because Chargebee does not expose them as one global list, the connector first reads items and then asks for attached items item by item.

**Data flow**: It receives an HTTP client and an optional cursor. It reads pages of parent `item` records, extracts each item ID, skips parents without an ID, then calls the generic substream paginator for `/items/{item_id}/attached_items`. Each child record is stamped with the parent `item_id` before being yielded in pages.

**Call relations**: `ChargebeeConnector.paginate` calls this when the requested stream is `attached_item`. This method relies on `ChargebeeConnector._paginate_list` to find parent items and on `ChargebeeConnector._paginate_substream` to read each item’s child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 221–240)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads customer contacts, which are fetched separately for each customer. It is needed because contacts are reached through a customer-specific Chargebee endpoint rather than a single shared contacts endpoint.

**Data flow**: It receives an HTTP client and an optional cursor. It reads customer pages, extracts each customer ID, skips any customer without an ID, then requests `/customers/{customer_id}/contacts` through the generic substream paginator. The returned contact records are enriched with the matching `customer_id` and yielded page by page.

**Call relations**: `ChargebeeConnector.paginate` calls this for the `contact` stream. It uses `ChargebeeConnector._paginate_list` to get customers first, then hands each customer-specific path to `ChargebeeConnector._paginate_substream`.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 242–259)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads quote line groups, which are child records belonging to individual quotes. It walks through quotes first, then gathers the line groups for each quote.

**Data flow**: It receives an HTTP client and an optional cursor. It reads pages of quote records, extracts each quote ID, skips records without a usable ID, then fetches `/quotes/{quote_id}/quote_line_groups`. Each returned child record is stamped with `quote_id` so its parent quote is not lost, and pages are yielded as they are fetched.

**Call relations**: `ChargebeeConnector.paginate` calls this when syncing `quote_line_group`. It depends on `ChargebeeConnector._paginate_list` for the parent quote list and `ChargebeeConnector._paginate_substream` for the quote-specific child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 261–283)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads the scheduled-change version of each subscription. For every subscription, it calls a detail endpoint that may return the subscription together with planned future changes.

**Data flow**: It receives an HTTP client and an optional cursor. It reads parent subscription pages, extracts each subscription ID, and skips any record without one. For each valid ID, it makes a single detail request to `/subscriptions/{id}/retrieve_with_scheduled_changes`. If the response contains a subscription object, it yields a one-record page containing that subscription plus the parent `subscription_id`.

**Call relations**: `ChargebeeConnector.paginate` calls this for `subscription_with_scheduled_changes`. Unlike the other child-stream methods, it does not use the generic substream paginator because the Chargebee endpoint returns one detail record at a time rather than a normal paginated child list.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 285–311)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for child streams that look like normal Chargebee paginated lists once a parent-specific path is known. It also stamps each child record with the parent ID so the relationship is preserved.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent ID field to add, and the parent ID value. It requests pages from that endpoint using the usual Chargebee `list` and `next_offset` shape. For each dictionary record it copies the record, adds the parent ID field, collects the enriched records, and yields only non-empty pages.

**Call relations**: The attached-item, contact, and quote-line-group paginators call this after they have found a parent item, customer, or quote. It is the reusable child-page loop that prevents those methods from duplicating the same paging and parent-stamping behavior.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during Recurly source sync`

Recurly returns lists of records in pages, much like a long search result split across several screens. This connector is the part of the system that keeps clicking “next” until all needed records have been read. It defines the Recurly streams the system can sync, such as accounts, subscriptions, invoices, coupons, and related child records like account notes or shipping addresses.

The file also builds the HTTP client used to talk to Recurly. Recurly expects HTTP Basic authentication, where the API key is used like a username, and it requires a specific API version in the request headers. If the system is using an auth proxy, the connector uses that instead of putting the key directly into the client.

Most streams are simple top-level API paths, like `/accounts` or `/invoices`. A few are “per parent” streams: for example, to get account notes, the connector first lists accounts, then asks Recurly for notes under each account. When it reads those child records, it adds the parent account or coupon id onto each row so the relationship is not lost.

If Recurly says access is forbidden or unauthorized, the connector skips that stream with a clear explanation instead of crashing the whole sync. Without this file, the system would not know how to safely and completely pull Recurly data.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of Recurly data, such as accounts or invoices. A stream description tells the wider sync system what the stream is called, what field identifies each record, and what time field can be used for incremental syncing.

**Data flow**: It receives a stream name plus optional details like the source API object, primary key, cursor field, and whether it is a main canonical stream. It fills in sensible defaults when details are not supplied, then returns a `StreamSpec`, which is the system’s compact description of that stream.

**Call relations**: This function is used while the file is being loaded to build the `RECURLY_STREAMS` list. It hands each finished stream description to `StreamSpec.__init__`, so the connector later has a ready-made menu of Recurly data types it can sync.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to send requests to Recurly. It also sets the required Recurly API version and authentication style, so every request is shaped the way Recurly expects.

**Data flow**: It takes a base URL and a credential. It trims the base URL, prepares timeout limits and headers, then either uses a provided proxy transport or turns the bearer value into HTTP Basic authentication. The result is an `httpx.AsyncClient`, an asynchronous web client that can make Recurly API calls; if no usable credential is present, it raises an error.

**Call relations**: The broader connector framework calls this when it needs a client for Recurly. Inside, it relies on `httpx.Timeout`, `httpx.BasicAuth`, and `httpx.AsyncClient` to build the actual network client before pagination functions start making requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s “next page” link into a path the client can request. Recurly may return either a full web address or just a path, and this helper normalizes both forms.

**Data flow**: It receives a next-page link, or nothing. If there is no link, it returns nothing. If the link is a full URL, it keeps only the path and query string, such as `/accounts?cursor=...`; if it is already a path, it returns it unchanged.

**Call relations**: The pagination loops call this whenever Recurly says more pages exist. `_paginate_top_level`, `_account_ids`, `_coupon_ids`, and `_paginate_per_parent` all use it to move from the current page to the next one without caring whether Recurly returned a full URL or a relative path.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the first set of query parameters for a Recurly list request. It sets the page size, sort field, sort direction, and, when possible, the starting time for incremental syncing.

**Data flow**: It receives a stream description and an optional cursor value, which is usually the last synced time. It creates parameters asking Recurly for up to 200 records in ascending order. If the stream has a cursor field and a cursor was provided, it adds `begin_time` so Recurly starts from that point. The output is a dictionary of request parameters.

**Call relations**: `_paginate_top_level` and `_paginate_per_parent` call this before their first request for a stream. After the first page, Recurly’s own next-page link carries the cursor forward, so these parameters are not reused.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main paging entry point for reading one Recurly stream. It decides whether the stream is a normal top-level list, a special filtered coupon list, or a child list that must be fetched under each parent record.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It chooses the right pagination strategy, then yields pages of records as lists of dictionaries. If Recurly returns a 401 or 403 refusal, it turns that into `StreamSkipped`, meaning this stream is unavailable because of missing permissions or a bad key; other HTTP errors continue upward.

**Call relations**: The sync framework calls this when it wants records from a Recurly stream. This function then delegates the actual page walking to `_paginate_top_level` or `_paginate_per_parent`. For `unique_coupons_parent`, it uses `_paginate_top_level` over coupons and only passes along bulk coupons.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly collection, such as accounts or invoices, one page at a time. It keeps following Recurly’s next-page pointer until there are no more pages.

**Data flow**: It receives a client, stream description, API path, and optional cursor. It builds the first query with `_initial_query`, requests the page, yields the records if any are present, then uses `_next_path` to find the following page. It stops when Recurly says `has_more` is false.

**Call relations**: `paginate` calls this for ordinary streams and for the special coupon-parent stream. This function is the simple paging worker: it does not decide what kind of stream is being read, but once given a path it repeatedly fetches and yields data.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields their ids. It exists so child streams, such as account notes or billing information, can be fetched account by account.

**Data flow**: It starts at `/accounts` with a basic created-time sort and page limit. For each returned account row, it checks that the row is a dictionary and has an id, then yields that id as text. It follows Recurly’s next-page link until no more account pages remain.

**Call relations**: `_paginate_per_parent` calls this when it needs to visit account-based child resources. `_account_ids` uses `_next_path` to continue through the account list, then hands each parent id back to `_paginate_per_parent` so child URLs can be built.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through Recurly coupons and yields only the ids of bulk coupons. It is used because unique coupon codes live under bulk coupon parents, not under every coupon type.

**Data flow**: It starts at `/coupons` with a page limit and created-time sort. For each coupon row, it ignores anything that is not a dictionary, has no id, or is not marked as `coupon_type` equal to `bulk`. Matching ids are yielded as text. It follows next-page links until the coupon list is exhausted.

**Call relations**: `_paginate_per_parent` calls this when the parent path is `/coupons`. This helper supplies the eligible coupon ids, and `_paginate_per_parent` uses those ids to request each coupon’s unique coupon codes.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that are stored underneath parent records in Recurly’s API. For example, it can read notes under each account or unique coupon codes under each bulk coupon.

**Data flow**: It receives the parent path, child path, field name to stamp onto child rows, and optional cursor. It first chooses the right parent id source: account ids or bulk coupon ids. For each parent id, it builds the child URL, fetches pages using the initial query and next-page links, adds the parent id into each child row if the row is a dictionary, and yields each non-empty page.

**Call relations**: `paginate` calls this for streams listed in `_PER_PARENT_STREAMS`. This function coordinates `_account_ids` or `_coupon_ids` for the parent walk, `_initial_query` for the first child request, and `_next_path` for continuing through child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### Accounting platforms
QuickBooks and Xero provide accounting-system connectors for accounts, contacts, invoices, payments, and related business records.

### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `during source sync reads`

QuickBooks Online exposes accounting records like customers, invoices, bills, payments, and journal entries through a query endpoint rather than through simple “list all invoices” URLs. This file wraps that awkward shape in a connector the rest of the system can use in a standard way.

At startup, it builds a catalog of QuickBooks streams. A stream is one kind of record to sync, such as “customers” or “invoices.” Most streams use QuickBooks’ nested “last updated” timestamp as a cursor, meaning the sync can later ask only for records changed after the last run. A few reference lists, such as payment methods and tax agencies, do not use that cursor and are read in full.

The main connector, `QuickBooksConnector`, sends SQL-like queries to QuickBooks’ `/query` endpoint. It requests up to 100 records at a time, then moves the starting position forward until QuickBooks returns fewer than 100 records, which means there are no more pages. If QuickBooks refuses access with an authorization error, the connector marks that stream as skipped instead of crashing the whole sync.

One important detail is that the cursor field is nested inside each record. The `flatten` method copies that nested value to a flat key so the shared sync engine can track progress consistently.

#### Function details

##### `_stream`  (lines 28–41)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates one stream description for a QuickBooks record type. It gives the rest of the sync system the stream’s friendly name, the QuickBooks object to query, its primary key, and whether it supports incremental syncing.

**Data flow**: It receives a stream name, the QuickBooks source object name, an optional cursor field, and a flag saying whether the stream is part of the main canonical set. It packages those details into a `StreamSpec`, which is the shared description object used by the connector framework. The result is added to the file’s list of QuickBooks streams.

**Call relations**: This function is used while the module is being loaded to build `QUICKBOOKS_STREAMS`. Each returned `StreamSpec` later tells `QuickBooksConnector.paginate` what QuickBooks entity to query and which cursor field, if any, should limit the query to newly changed records.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 82–89)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This method builds the SQL-like text query that QuickBooks expects. It turns the system’s stream and cursor information into a QuickBooks request such as “select invoices changed after this time, starting at this page.”

**Data flow**: It receives a stream description, an optional cursor value from the previous sync, and the page starting position. It starts with `SELECT * FROM ...`, adds a `WHERE` and `ORDER BY` clause when the stream supports incremental syncing, escapes single quotes in the cursor so the query stays valid, and appends the page size. It returns the finished query string.

**Call relations**: `QuickBooksConnector.paginate` calls this method before every request to QuickBooks. The query it returns is then sent to the `/query` endpoint so QuickBooks knows which entity, date range, and page of results to return.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 91–113)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous method reads one QuickBooks stream page by page. It is the part that actually asks QuickBooks for records and yields them back to the sync engine in batches.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last successful sync. It starts at QuickBooks position 1, builds a query, sends it to `/query`, unwraps the records from `QueryResponse.<Entity>`, and yields each non-empty batch. If the batch is smaller than the fixed page size, it stops because that means the final page has been reached. If QuickBooks returns a 401 or 403 refusal, it turns that into `StreamSkipped`; other HTTP errors are passed upward unchanged.

**Call relations**: The connector framework calls this method when it needs to read a QuickBooks stream. For each page, it relies on `QuickBooksConnector._build_query` to create the request text. When access is refused, it hands back a `StreamSkipped` signal so the larger sync can skip that stream cleanly instead of treating it like an unexpected failure.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 115–118)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method makes QuickBooks records easier for the shared sync engine to track. In particular, it exposes the nested last-updated timestamp as a simple top-level field.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream’s cursor field is written as a dotted path, such as `Metadata.LastUpdatedTime`, it reads that nested value using `get_path` and returns a copy of the record with an extra flat key holding the same value. If there is no nested cursor field, it returns the record unchanged.

**Call relations**: After records have been fetched by `QuickBooksConnector.paginate`, the broader source framework can call this method before storing or checkpointing them. It depends on `get_path` to safely find the nested cursor value, and the flattened result lets the shared adapter update its watermark without needing QuickBooks-specific knowledge.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `request handling during source sync`

Xero is an accounting service, and its API has a few special rules. This file captures those rules so the rest of the project does not need to know them. It defines the list of Xero “streams,” meaning the kinds of records that can be synced, such as invoices or bank transactions. For each stream, it records the Xero API object name, the field used to detect updates, and whether it is one of the main commonly used streams.

When syncing, the connector asks Xero for one stream at a time. Most Xero lists are paged, like a book split into 100-row chapters, so the connector keeps requesting page 1, page 2, and so on until a page is short or empty. Some small lists do not support paging, so they are fetched once. For incremental sync, where the system only wants records changed since last time, Xero expects a special HTTP header called `If-Modified-Since`, not a normal URL query setting. This file converts saved cursor times into the date format Xero expects.

Xero also uses different ID field names for different record types, such as `InvoiceID` for invoices. The connector normalizes those into a plain `id` field so downstream code can treat all streams consistently. If Xero refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 64–77)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream definition for one kind of Xero record, such as invoices or contacts. A stream definition tells the sync system what to call the stream, which Xero response envelope to read, which field marks updates, and whether it is a main canonical stream.

**Data flow**: It receives a friendly stream name, the matching Xero object name, an optional update-time field, and a canonical flag. It packages those into a `StreamSpec`, which is the shared description object the rest of the source system understands.

**Call relations**: This helper is used while building the module-level list of Xero streams. It hands its information to `StreamSpec`, so later the connector can paginate, flatten, and sync each stream using the same common format.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 105–122)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: Converts a saved sync cursor into the date text format Xero expects for incremental fetching. RFC 1123 is a standard HTTP date format, like `Tue, 15 Nov 1994 08:12:31 GMT`.

**Data flow**: It receives a cursor value, which may be missing, blank, a Unix timestamp number, an ISO-style date string, or already some other date text. If possible, it turns that value into a UTC HTTP date string. If the value is empty or unusable, it returns nothing; if it cannot parse a non-numeric date, it leaves the text as-is.

**Call relations**: XeroConnector.paginate calls this before making requests for streams that support incremental updates. The converted value is placed in the `If-Modified-Since` request header so Xero can return only records changed after that time.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 130–131)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: Stores the optional Xero tenant ID for this connector instance. In Xero, a tenant ID identifies which organization, or company account, should be read under the granted access.

**Data flow**: It receives an optional tenant ID when the connector is created. It saves that value on the connector so later HTTP requests can include it if present. It does not return a separate result.

**Call relations**: This runs when code constructs a `XeroConnector`. The saved tenant ID is later used by `XeroConnector._make_client` when preparing the HTTP client that talks to Xero.


##### `XeroConnector._make_client`  (lines 133–137)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Xero and adds Xero’s required tenant header when a tenant ID is available. This makes sure requests are aimed at the right Xero organization.

**Data flow**: It receives the base API URL and a credential object supplied by the surrounding authentication system. It first asks the parent `RestConnector` to create the standard authenticated HTTP client. Then, if this connector has a tenant ID, it adds `xero-tenant-id` to the client’s default headers. It returns the ready-to-use client.

**Call relations**: The broader REST source machinery calls this when it needs a client for syncing. This method builds on the parent connector’s client setup and adds the Xero-specific organization header before `paginate` starts making API calls.


##### `XeroConnector.paginate`  (lines 139–174)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from one Xero stream, page by page when needed. It also applies incremental-sync filtering and turns access refusals into a controlled “skip this stream” outcome.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It builds the Xero API path from the stream’s source object. If the stream has a cursor field and a cursor value was provided, it converts the cursor into Xero’s `If-Modified-Since` header. For non-paged streams, it makes one request and yields the records inside Xero’s response envelope. For paged streams, it requests `page=1`, then `page=2`, and continues yielding batches until Xero returns no records or fewer than 100 records. If Xero replies with 401 or 403, it raises `StreamSkipped`; other HTTP errors are re-raised.

**Call relations**: The sync runner calls this when it needs the raw records for a particular Xero stream. It relies on `_cursor_to_rfc1123` to prepare incremental-sync headers and on `httpx.AsyncClient.get` to perform the actual network requests. It yields batches of records onward to the rest of the source pipeline, where they can be flattened, keyed, and stored.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 176–185)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Normalizes each Xero record so it has a common `id` field. This matters because Xero uses different ID names for different record types, while the rest of the sync system expects a consistent primary key.

**Data flow**: It receives one record and the stream it came from. If the record already has `id`, it returns it unchanged. Otherwise, it looks up the Xero-specific ID field for that stream, reads that value from the record, and returns a copy of the record with `id` added as text. If no matching ID field or value exists, it leaves the record unchanged.

**Call relations**: After `paginate` yields Xero records, the source pipeline can call this to make each record easier to identify consistently. It uses the file’s stream-to-ID mapping, so downstream code does not need to know whether a record originally used `InvoiceID`, `ContactID`, `TaxType`, or another Xero-specific key.


### Commerce and payments
Square and Stripe cover commerce and payment APIs, normalizing customers, payments, orders, refunds, subscriptions, invoices, and connected-account records into syncable streams.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `source sync / request handling`

Square exposes different kinds of data in different ways. Some lists are fetched with normal GET requests and a cursor, some require POST search requests, and orders must be searched across all Square locations in the account. This file hides those differences behind one connector, `SquareConnector`, so the rest of the system can simply ask for a stream and receive pages of records.

The connector defines which Square streams exist, what each record’s main identifier is, and which time field can be used as a bookmark for incremental syncs. A bookmark is like saying, “only bring me records newer than this date.” For payments and refunds, Square supports this directly with a `begin_time` request parameter. For some other streams, the connector fetches pages and then filters out older records itself.

It also pins the Square API version by adding a `Square-Version` header to every request. That matters because API behavior can change over time, and this keeps responses predictable.

If Square refuses access with a 401 or 403 error, the connector raises `StreamSkipped` instead of crashing the whole run. In plain terms, if the key is invalid or lacks permission for one stream, the system can skip that stream cleanly.

#### Function details

##### `SquareConnector._make_client`  (lines 77–80)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Square and adds the required Square API version header. This keeps all requests tied to the expected Square API behavior.

**Data flow**: It receives a base URL and a credential, asks the parent REST connector to build the normal authenticated HTTP client, then adds `Square-Version: 2026-04-16` to that client’s headers. It returns the prepared client, which will be used for Square API calls.

**Call relations**: This is part of the setup path for the connector. The broader REST connector machinery calls it when preparing to contact Square, and later methods such as `SquareConnector.paginate` use the resulting client to make requests.


##### `SquareConnector.paginate`  (lines 82–116)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right Square fetching strategy for the requested stream and yields records in pages. It is the main doorway the rest of the sync system uses to read from Square.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream name, then either fetches locations once, walks cursor-based GET pages, searches catalog objects, searches orders, or retrieves inventory counts. It yields non-empty lists of records. If the stream is unknown, or Square refuses access with a 401 or 403 response, it raises `StreamSkipped` so the run can skip that stream cleanly.

**Call relations**: The sync runner calls this when it wants records for a Square stream. `paginate` then delegates to helper methods: `_locations` for locations, `_cursor_get` for customers/payments/refunds, `_catalog` for catalog items and categories, and `_orders` for orders. For inventory counts it calls the shared POST helper directly and uses `records_at` to pull the `counts` list out of the response.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 118–135)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads Square streams that use a standard cursor-based list API, such as customers, payments, and refunds. A cursor is a continuation marker that tells the next request where to resume.

**Data flow**: It receives the client, the stream description, and an optional cursor bookmark. For payments and refunds, it sends the bookmark to Square as `begin_time`, letting Square filter older records. For other streams, it fetches pages from Square and then locally keeps only records whose cursor field is newer than the bookmark. It yields each non-empty filtered page.

**Call relations**: `SquareConnector.paginate` calls this for customers, payments, and refunds. This helper relies on the shared REST connector’s `_get_cursor_pages` behavior to do the repetitive work of following Square’s `cursor` field from page to page.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 137–154)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Searches Square’s catalog for either items or categories and yields the results page by page. It adapts Square’s catalog search endpoint into the same page-stream shape used by the rest of the connector.

**Data flow**: It receives the client, a catalog stream description, and an optional cursor bookmark. It translates the stream name into Square’s object type, sends POST requests to `/catalog/search`, follows Square’s returned continuation cursor, extracts the `objects` list, and optionally filters out records older than the bookmark. It yields each non-empty page until Square stops returning a next cursor.

**Call relations**: `SquareConnector.paginate` calls this when the requested stream is `catalog_items` or `catalog_categories`. Inside the loop it uses `records_at` to safely pull the list of catalog objects from each Square response before handing those records back to `paginate`.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 156–158)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: Fetches the Square account’s locations in one request. Locations matter on their own as a stream, and they are also needed before searching orders.

**Data flow**: It receives the HTTP client, sends a GET request to `/locations`, extracts the `locations` list from the response, and returns that list. It does not yield multiple pages because this stream is treated as a single-shot collection here.

**Call relations**: `SquareConnector.paginate` calls this directly when syncing the `locations` stream. `SquareConnector._orders` also calls it first because Square order searches need a list of location IDs to search within.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 160–184)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Searches Square orders across all available account locations and yields them in pages. This extra step is needed because Square’s order search requires location IDs instead of searching the whole account automatically.

**Data flow**: It receives the client and an optional cursor bookmark. First it calls `_locations` and extracts valid location IDs. If there are no locations, it stops without producing records. Otherwise it sends POST requests to `/orders/search`, including the location IDs, a page limit, an optional start date filter based on the cursor, and any continuation cursor returned by Square. It extracts the `orders` list from each response, yields non-empty pages, and continues until Square stops returning a next cursor.

**Call relations**: `SquareConnector.paginate` calls this for the `orders` stream. `_orders` depends on `_locations` to discover where to search, and uses `records_at` to pull the orders list out of each Square response before returning pages to the main pagination flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `source sync / Stripe API pagination`

Stripe exposes its data through web API lists: each request returns a batch of records, plus a flag saying whether more records exist. This file wraps that pattern in a StripeConnector so the rest of the project does not need to know Stripe’s URL shapes, page controls, or special cases.

The file first defines the Stripe streams the system can read. A stream is one named collection, like customers or invoices. Some streams are simple top-level lists. Others are children of another record: for example, invoice line items live under a specific invoice, and transfer reversals live under a specific transfer. For those, the connector first walks the parent list, then asks Stripe for each parent’s child list, and adds the parent id to each child row so the relationship is not lost.

The connector also pins a Stripe API version in the request header, which helps keep Stripe responses stable over time. For incremental syncs, it can turn a saved cursor into a Unix timestamp and send it as Stripe’s created[gte] filter when the stream supports that. If Stripe refuses access with 401 or 403, the stream is skipped with a clear reason instead of crashing the whole sync. There is no write path here; this connector only reads from Stripe.

#### Function details

##### `_stream`  (lines 77–91)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Stripe data stream, such as its name, Stripe object path, primary key, and cursor field. It keeps the long stream list readable by avoiding repeated StreamSpec setup code.

**Data flow**: It receives a stream name plus optional details like the Stripe source object, primary key, cursor field, and whether it is canonical. It fills in sensible defaults, then builds and returns a StreamSpec object that the connector later uses to know what to request and how to track progress.

**Call relations**: This helper feeds the module-level STRIPE_STREAMS list. Each call produces one stream definition, and those definitions are later searched by _stream_spec and used by paginate and the paging helpers.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 159–162)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Stripe and adds the Stripe API version header. This matters because pinning the API version keeps Stripe responses predictable even if Stripe changes its defaults later.

**Data flow**: It receives the base Stripe URL and a credential object supplied by the wider system. It asks the parent REST connector to create the basic asynchronous HTTP client, adds the Stripe-Version header, and returns the ready-to-use client.

**Call relations**: This fits into the connector startup path, when the REST framework prepares a client for API calls. After this method returns, all later Stripe requests made through that client carry the pinned API version automatically.


##### `StripeConnector._list_path`  (lines 165–166)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: Turns a stream definition into the normal Stripe list URL path. For example, a stream whose source object is customers becomes /v1/customers.

**Data flow**: It receives a StreamSpec and reads its source_object value. It prefixes that value with /v1/ and returns the resulting path string.

**Call relations**: paginate uses this for ordinary top-level streams. The substream helpers also use it to find parent collections before walking child records.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 169–181)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: Converts a saved cursor value into the Unix timestamp format Stripe expects for created[gte] filters. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be empty, already numeric, or an ISO date-time string such as 2024-01-01T00:00:00Z. Empty or unparseable values become None; numeric text becomes an integer; date-time text is parsed, given UTC if it has no timezone, and returned as a timestamp integer.

**Call relations**: _page_loop calls this before sending requests. Its result decides whether the request can include Stripe’s created[gte] filter for incremental reading.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 183–209)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a Stripe stream and yields batches of records. It is the main read doorway for Stripe data.

**Data flow**: It receives an HTTP client, a stream definition, and an optional saved cursor. It checks whether the stream is an external-account stream, a child stream reached by URL path, a child stream reached by query parameter, or a normal top-level stream. It then delegates to the matching helper and yields each page it receives. If Stripe replies with 401 or 403, it converts that refusal into StreamSkipped with a clear explanation.

**Call relations**: The wider source-sync framework calls this when it wants records for one Stripe stream. paginate then hands off to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, or _page_loop, using _list_path for ordinary stream URLs.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 211–242)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through Stripe’s standard paginated list format. It repeatedly asks for up to 100 records, yields each non-empty batch, and follows Stripe’s “has_more” signal until the list is finished.

**Data flow**: It receives an HTTP client, a URL path, a stream definition, a cursor, and optional extra query parameters. It converts the cursor to a timestamp when useful, builds request parameters such as limit, starting_after, created[gte], and stream-specific filters, then performs GET requests. It yields each returned data list and updates starting_after from the last record id before fetching the next page.

**Call relations**: paginate uses this directly for simple streams. The substream helpers also rely on it twice: once to walk parent records and again to walk each parent’s children.

*Call graph*: calls 1 internal fn (_cursor_to_unix); called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._paginate_substream`  (lines 244–261)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections that are reached by putting a parent id in the URL path. For example, it can read line items under each invoice or payment methods under each customer.

**Data flow**: It receives an HTTP client and a child stream definition. It looks up the parent stream, lists parent records, skips parents without an id, builds each child URL using that id, and pages through the children. When configured, it adds the parent id onto each child row before yielding the page, so later users can tell which parent each child came from.

**Call relations**: paginate calls this when the stream name is in the child-path mapping. This helper uses _stream_spec to find the parent StreamSpec, _list_path to form the parent list URL, and _page_loop to fetch both parent and child pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_substream_query`  (lines 263–277)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child collections where Stripe expects the parent id as a query parameter instead of inside the URL path. Subscription items, payout balance transactions, and setup attempts use this shape.

**Data flow**: It receives an HTTP client and a child stream definition. It looks up which parent stream and query field belong to that child stream, pages through the parents, and for each parent id requests the child path with an extra query parameter. It yields child rows with an added parent-id field such as subscription_id or payout_id.

**Call relations**: paginate calls this for streams listed in the query-parent mapping. It depends on _stream_spec and _list_path to find and address the parent list, then uses _page_loop for both parent and filtered child requests.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 279–292)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads bank accounts or cards attached to Stripe connected accounts. These are special because Stripe uses one external_accounts endpoint and an object filter to separate bank accounts from cards.

**Data flow**: It receives an HTTP client and the external-account stream definition. It first pages through Stripe accounts, skips any account without an id, then requests /v1/accounts/{account_id}/external_accounts for each account. It yields each child page after adding account_id to every row.

**Call relations**: paginate calls this for the external_account_bank_accounts and external_account_cards streams. It finds the accounts stream with _stream_spec, builds the accounts list path with _list_path, and uses _page_loop to walk both accounts and their external accounts.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 294–295)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream definition with a given name. Substream readers use it when they know a parent stream by name and need its full StreamSpec details.

**Data flow**: It receives a stream name, scans the STRIPE_STREAMS list, and returns the first StreamSpec whose name matches. If no matching stream exists, Python’s next call would raise an error, which signals a broken internal mapping.

**Call relations**: _paginate_substream, _paginate_substream_query, and _paginate_external_accounts call this before paging parent records. It acts like a small lookup table for the connector’s stream catalog.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).

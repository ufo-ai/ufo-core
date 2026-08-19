# Finance, billing, spend, and accounting connectors  `stage-14.1.6`

This stage is a set of behind-the-scenes connectors for financial systems. A connector is the adapter that knows how to talk to one outside service, ask for records, and reshape the answers into the project’s standard “streams,” meaning repeatable lists of records the sync engine can process.

Each file covers a different money-related service. Brex reads spend-management data such as expenses, vendors, budgets, users, and departments. Chargebee and Recurly read subscription billing records like customers, subscriptions, invoices, and transactions. Stripe does similar billing and payment work, including checkout sessions and connected-account data. Square focuses on point-of-sale records such as payments, orders, catalog items, locations, inventory, and customers. QuickBooks pulls accounting records like bills, invoices, vendors, payments, and journal entries. Xero also reads accounting data, while handling Xero-specific details such as tenant headers, update dates, and record IDs.

Together, these files are like plug adapters for different financial tools. Each one hides the service’s paging and authentication rules so the rest of the system can sync financial records in one common way.

## Files in this stage

### Spend management
Brex provides spend-management streams for transactions, expenses, organizational entities, vendors, budgets, and departments.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `source sync`

Brex exposes business spending data through a web API, but the rest of this system needs that data in a predictable shape. This file is the adapter between the two. It names the Brex data streams the system knows about, records which Brex web address each stream comes from, and describes simple facts about each stream, such as its main ID field and whether it has a date field that can act like a progress marker.

The main class, BrexConnector, is a read-only connector. It uses OAuth bearer authentication supplied elsewhere by the resolved credential, then calls Brex list endpoints. Brex returns records in pages, like a book split into chapters: each response contains an `items` list plus a `next_cursor`, which is a token saying where to continue. The connector keeps asking for the next page until Brex stops sending a cursor.

Most Brex endpoints do not let the connector ask only for recently changed records, so this is mostly a full refresh source. For transactions and expenses, the file still notes useful date fields so the wider sync system can track how far it has seen, even though Brex is not filtering by those dates here. There is no write path; this file only reads Brex data.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Brex data stream, such as transactions or vendors. It avoids repeating the same setup details for every stream in the catalog.

**Data flow**: It receives a stream name and optional details like the primary key field, cursor date field, and whether the stream is considered canonical. It packages those choices into a StreamSpec object, which is the system’s common description of a syncable stream. The result is used as one entry in the Brex stream list.

**Call relations**: This helper hands its inputs to StreamSpec.__init__, which builds the actual stream description object. The file uses those stream descriptions to form BREX_STREAMS, and BrexConnector later exposes that list so the wider source framework knows which Brex objects can be read.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads all pages for one Brex stream from the Brex API. Someone would use it when the sync engine needs batches of records for a stream like expenses, budgets, or users.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor argument. It looks up the correct Brex endpoint for the stream, asks Brex for up to 100 records at a time, safely turns the response’s `items` value into a list, yields any records it found, then follows Brex’s `next_cursor` token to request the next page. When there is no next cursor, it stops. If the stream has no known endpoint, it raises an error instead of guessing.

**Call relations**: During a sync, the source framework calls this method through the RestConnector pattern to fetch records page by page. Inside the loop it uses the connector’s HTTP GET helper to contact Brex, then calls ufo.sdk.sources.list_or_empty so a missing or null `items` field becomes an ordinary empty list rather than breaking the sync. Each yielded batch is handed back to the wider sync pipeline for storage or indexing.

*Call graph*: 1 external calls (list_or_empty).


### Subscription billing
Chargebee, Recurly, and Stripe expose subscription, customer, invoice, transaction, and related billing records through paged API streams.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `during source sync when Chargebee streams are read`

Chargebee is a subscription billing service, and its API returns data in a very regular but slightly wrapped shape. Each page contains a list of records, and each record is tucked inside a small envelope named after the resource, such as `customer` or `invoice`. This file defines a Chargebee connector that knows which Chargebee endpoints exist, how to authenticate, how to walk through pages, and how to flatten those envelopes into easier-to-use records.

The connector is read-only. Its job is like a careful librarian: it visits each shelf in Chargebee, asks for one page at a time, follows the “next page” token until there are no more pages, and hands back clean batches of records. For streams that can be updated over time, it can start after a saved cursor value, so later syncs do not need to reread everything.

Some Chargebee data is not available as a simple top-level list. For example, contacts live under customers, and attached items live under items. For these, the connector first reads the parent records, then visits each parent’s child endpoint and stamps the parent ID onto each child record. If Chargebee refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole run as a mysterious failure.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream definition for one kind of Chargebee data, such as customers or invoices. It keeps the long stream list readable by filling in common defaults like the primary key and update cursor.

**Data flow**: It takes a stream name and optional details such as the source object name, primary key, and date fields. It packages those choices into a `StreamSpec`, which is the system’s small description of what a stream is and how it should be tracked.

**Call relations**: This is used while the file is being loaded to build the Chargebee stream catalog. It hands each completed stream description to the connector class through the `CHARGEBEE_STREAMS` list.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Chargebee. It also applies the right authentication and timeout settings so requests are accepted and do not hang forever.

**Data flow**: It receives a base URL and a resolved credential. It trims the base URL, prepares JSON/form headers, sets connection and read time limits, and then returns an `httpx.AsyncClient`. If the credential already provides a transport, it uses that transport unchanged; otherwise it uses the credential’s API key as the username in HTTP Basic authentication, with an empty password. If no usable authentication is present, it raises an error.

**Call relations**: The broader REST connector setup calls this when it is ready to contact Chargebee. The client it returns is then passed into pagination methods so they can make API requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This removes Chargebee’s extra wrapper around each record. It makes records easier for the rest of the system to read by moving the actual resource fields to the top level.

**Data flow**: It receives one raw record and the stream description that says which envelope name to expect. If the record contains a matching nested dictionary, it copies the inner fields into a new flat record and preserves any extra top-level fields, such as a stamped parent ID. If there is no expected envelope, it returns the record as-is.

**Call relations**: After pages are fetched, the connector framework can call this before records are stored or emitted. It is especially important for child streams, because their parent ID is added outside the Chargebee envelope and must not be lost.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main router for reading a Chargebee stream. Given a stream name, it chooses the correct paging strategy and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For special child streams, it sends the work to the matching child-stream paginator. For normal list streams, it sends the work to the generic list paginator. It yields each page it receives. If Chargebee responds with 401 or 403, it turns that refusal into a `StreamSkipped` message explaining that the key or permissions are not sufficient.

**Call relations**: The sync engine calls this when it wants records for one Chargebee stream. This function then delegates to `_paginate_attached_items`, `_paginate_contacts`, `_paginate_quote_line_groups`, `_paginate_subscription_scheduled`, or `_paginate_list`, depending on the stream.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters used for a normal Chargebee list request. It sets the page size and, when possible, adds the incremental “only records after this cursor” filter.

**Data flow**: It receives a stream description and an optional cursor value from a previous run. It starts with a `limit` parameter. If both a cursor and a cursor field exist, it adds a Chargebee-style parameter like `updated_at[after]` or `created_at[after]`. It returns the finished parameter dictionary.

**Call relations**: `_paginate_list` calls this before requesting pages. Its output tells Chargebee how many records to return per page and where to begin for incremental syncs.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a regular Chargebee list endpoint, such as `/customers` or `/invoices`. It follows Chargebee’s `next_offset` token until all pages are read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for that stream, builds request parameters, asks the lower-level REST paging helper for pages from the response’s `list` field, and yields each page of raw records.

**Call relations**: `paginate` uses this for ordinary streams. The child-stream paginators also call it first to read their parent records before asking for children.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads attached items, which Chargebee exposes under each item rather than as one global list. It first finds items, then reads the attached items for each one.

**Data flow**: It receives an HTTP client and optional cursor. It paginates the parent `item` stream, extracts each item ID, skips parents without an ID, then requests `/items/{item_id}/attached_items`. Each child record is yielded with the parent `item_id` stamped onto it.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. It relies on `_paginate_list` to get parent items and `_paginate_substream` to walk each item’s child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads customer contacts, which live underneath individual customers in Chargebee. It connects every contact back to the customer it came from.

**Data flow**: It receives an HTTP client and optional cursor. It paginates customers, extracts each customer ID, skips any customer without an ID, then reads `/customers/{customer_id}/contacts`. It yields contact pages with `customer_id` added to each record.

**Call relations**: `paginate` calls this for the `contact` stream. It uses `_paginate_list` to discover customers and `_paginate_substream` to fetch and label their contacts.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads quote line groups, which are stored under individual quotes. It makes those child records usable as their own stream by adding the quote ID to each one.

**Data flow**: It receives an HTTP client and optional cursor. It paginates quotes, extracts each quote ID, skips quotes without an ID, and then reads `/quotes/{quote_id}/quote_line_groups`. It yields pages of child records with `quote_id` attached.

**Call relations**: `paginate` calls this for the `quote_line_group` stream. It gets parent quotes through `_paginate_list` and reads each quote’s child pages through `_paginate_substream`.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads subscription details that include scheduled changes. Chargebee provides this through a special one-subscription-at-a-time endpoint, not a normal list.

**Data flow**: It receives an HTTP client and optional cursor. It paginates subscriptions, extracts each subscription ID, skips missing IDs, then requests `/subscriptions/{id}/retrieve_with_scheduled_changes` for each one. If the response contains a subscription envelope, it yields a one-record page containing that subscription plus `subscription_id`.

**Call relations**: `paginate` calls this for the `subscription_with_scheduled_changes` stream. It uses `_paginate_list` to find subscriptions, then performs the special detail lookup for each parent subscription.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared child-stream pager for Chargebee endpoints that live under a parent record. It reads the child pages and labels each child with the parent ID.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent ID field to add, and the parent ID value. It requests pages using Chargebee’s usual `list` and `next_offset` format. For every dictionary record returned, it copies the record, adds the parent ID, and yields non-empty stamped pages.

**Call relations**: The attached-item, contact, and quote-line-group paginators call this after they have found a parent ID. It gives them one common way to fetch child pages and preserve the parent-child relationship.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during source sync, while fetching Recurly records`

Recurly returns lists of accounts, invoices, subscriptions, coupons, and similar billing records in pages, like a book where each page points to the next one. This connector turns those pages into streams the rest of the system can recall and sync.

The file first defines the available Recurly streams. A stream is a named kind of record, such as accounts or invoices, along with hints like its primary key and the time field used for incremental syncing. Incremental syncing means “start from the last known time instead of rereading everything.”

The RecurlyConnector class then provides the Recurly-specific rules. It builds an HTTP client with Recurly’s required API version header and uses HTTP Basic authentication, where the API key is used as the username. It also understands Recurly’s list response shape: a data list, a has_more flag, and a next link.

Most streams are simple top-level lists. A few are child lists under a parent, such as account notes under each account. For those, the connector first reads all parent IDs, then asks Recurly for each parent’s child records and stamps the parent ID onto each child row. If Recurly refuses access with a 401 or 403 response, the stream is skipped instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream definition for one kind of Recurly record. It keeps the stream list readable by filling in common defaults, such as using id as the main identifier and updated_at as the usual sync cursor.

**Data flow**: It receives a stream name and optional details such as the Recurly API object name, primary key, cursor field, and whether it is a main canonical stream. It combines those choices with defaults and returns a StreamSpec object that the connector can later use to fetch that kind of data.

**Call relations**: This is used while the module is loaded to build the RECURLY_STREAMS list. Those stream definitions are then exposed by RecurlyConnector so the wider sync system knows what Recurly data can be requested.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It sets the required Recurly API version, timeouts, and authentication so later requests are made in the exact form Recurly expects.

**Data flow**: It receives a base URL and a credential. If the credential contains a special transport, it uses that transport, which lets an auth proxy send the secret safely. Otherwise, if there is an API key, it puts that key into HTTP Basic authentication as the username. It returns an asynchronous HTTP client ready to make Recurly requests, or raises an error if no usable credential is present.

**Call relations**: The broader RestConnector machinery calls this when it needs a network client for a Recurly sync. The client it returns is then passed into pagination methods that fetch the actual pages of records.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This normalizes Recurly’s next-page link into a path that the HTTP client can request. It protects the rest of the code from caring whether Recurly returned a full URL or just a relative path.

**Data flow**: It receives a next link, which may be missing, relative, or an absolute URL. If it is empty, it returns nothing. If it is a full URL, it strips it down to just the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: The pagination helpers call this after each page whenever Recurly says there are more records. It hands them the next request path so they can keep walking forward through the result set.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for the first request in a stream. It sets the page size, sort order, and optional starting time for incremental syncing.

**Data flow**: It receives a stream definition and an optional saved cursor value. It creates a dictionary with the Recurly page limit, ascending order, and the field to sort by. If the stream has a cursor field and a previous cursor is supplied, it adds begin_time so Recurly starts at that point. The result is the parameter set for the first API call.

**Call relations**: Both top-level pagination and per-parent pagination use this at the start of a listing. After the first request, Recurly’s own next link carries the cursor details, so the pagination code stops sending these initial parameters.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading a Recurly stream. It decides whether a stream is a normal list, a filtered coupon-parent list, or a child list under each account or coupon.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. For ordinary streams, it asks the top-level paginator to read from the stream’s endpoint. For child streams, it asks the per-parent paginator to first find parents and then read children. For unique coupon parents, it reads coupons but only yields bulk coupons. It yields lists of records as pages. If Recurly rejects access with an authorization error, it turns that into a StreamSkipped signal.

**Call relations**: The sync framework calls this when it wants records for a particular Recurly stream. This function then hands the work to _paginate_top_level or _paginate_per_parent, depending on the stream, and passes their yielded pages back to the caller.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly list endpoint from first page to last page. It is used for streams like accounts, subscriptions, invoices, and other records that live directly under a single API path.

**Data flow**: It receives an HTTP client, a stream definition, an API path, and an optional cursor. It builds the first request’s query parameters, requests a page, yields the records if any are present, then follows Recurly’s next link while has_more is true. It stops when Recurly says there are no more pages.

**Call relations**: paginate calls this for ordinary streams and for the coupon list used by unique_coupons_parent. During the loop it uses _initial_query for the first request and _next_path to continue from one Recurly page to the next.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This reads all account IDs from Recurly so child account resources can be fetched. It is like first collecting every folder name before opening each folder to read the notes inside.

**Data flow**: It starts at the accounts endpoint with a fixed page size and ascending creation-time order. For each page, it looks through the returned rows and yields the id of each valid account. If there are more pages, it follows Recurly’s next link; otherwise it stops.

**Call relations**: _paginate_per_parent calls this when it needs parent IDs for account-based child streams, such as account notes, billing infos, shipping addresses, and account coupon redemptions. The IDs it yields become part of the child endpoint URL.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This reads coupon IDs that are eligible to have unique coupon codes underneath them. It only yields bulk coupons, because those are the coupon records that can have many unique child codes.

**Data flow**: It starts at the coupons endpoint and walks through all coupon pages. For each coupon row, it checks that the row is a dictionary, has an id, and has coupon_type set to bulk. Matching coupon IDs are yielded as strings. The function follows next links until Recurly reports no more pages.

**Call relations**: _paginate_per_parent calls this when the child stream is based under coupons rather than accounts. The coupon IDs it yields are used to request each coupon’s unique_coupon_codes child endpoint.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that do not have one single list endpoint, because their records live under parent records. It first finds each parent, then reads the child records for that parent.

**Data flow**: It receives the parent path, child path, the field name where the parent ID should be stored, and an optional cursor. It chooses either account IDs or bulk coupon IDs as parents. For each parent ID, it builds the child URL, requests pages of child records, stamps the parent ID onto each child row if that field is missing, and yields each non-empty page. It follows next links for each parent until that parent’s child pages are exhausted.

**Call relations**: paginate calls this for Recurly streams such as account notes, billing infos, shipping addresses, account coupon redemptions, and unique coupon codes. It relies on _account_ids or _coupon_ids to fan out across parents, uses _initial_query for the first child request, and uses _next_path to keep following child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `source sync`

Stripe exposes its data through many web endpoints, but they mostly follow the same pattern: ask for a page of up to 100 records, look at whether more records exist, then ask again after the last record seen. This file wraps that pattern so the rest of the project does not need to know Stripe’s URL shapes, page rules, or special cases.

The file starts by defining a catalog of Stripe streams. A stream is a named kind of data, such as customers or invoices, with hints like its main ID field and the time field used for incremental syncing. Incremental syncing means “only ask for records newer than the last one we already saw.”

The main class, `StripeConnector`, is a read-only connector. It creates an HTTP client for Stripe, adds Stripe’s pinned API version header, and then decides how to fetch each stream. Simple streams use one list endpoint. Substreams, like invoice line items, first walk parent records, like invoices, then fetch the child records for each parent. Query-based substreams do the same thing, but pass the parent ID as a query parameter instead of putting it in the path. External account streams are another special case: they walk Stripe accounts and fetch either bank accounts or cards beneath each one.

If Stripe refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as broken. This matters because one Stripe key may not have permission for every possible Stripe surface.

#### Function details

##### `_stream`  (lines 89–107)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates one stream definition for the Stripe catalog. It keeps the long list of Stripe streams readable by filling in common defaults, such as using `id` as the main key and `created` as the usual time cursor.

**Data flow**: It receives a stream name and optional details like the Stripe object path, primary key, cursor field, and whether the stream is canonical. It combines those values with sensible defaults and returns a `StreamSpec`, which is the system’s compact description of one syncable data type.

**Call relations**: This function is used while the module is loaded to build `STRIPE_STREAMS`. Those stream definitions are later used by `StripeConnector` to know which Stripe endpoints exist and how each one should be read.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 182–185)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the web client used to talk to Stripe and adds the Stripe API version header. Pinning the API version helps keep Stripe responses stable even if Stripe changes its default behavior later.

**Data flow**: It receives the base Stripe URL and a credential object supplied by the wider authentication system. It asks the parent connector class to make the basic HTTP client, adds the `Stripe-Version` header, and returns the prepared client.

**Call relations**: The broader connector framework calls this when it needs a client for a Stripe sync. After this client is created, pagination functions use it for every Stripe request.


##### `StripeConnector._list_path`  (lines 188–189)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This small helper turns a stream definition into the normal Stripe list URL path. For example, a stream whose source object is `customers` becomes `/v1/customers`.

**Data flow**: It receives a `StreamSpec` and reads its `source_object` value. It prefixes that value with `/v1/` and returns the resulting path string.

**Call relations**: The main pagination flow calls this for ordinary streams. Substream helpers also use it when they first need to list parent records, such as accounts before external accounts or subscriptions before subscription items.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 192–204)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This function converts a saved sync cursor into the Unix timestamp format Stripe expects for date filtering. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date string such as `2024-01-01T00:00:00Z`. It returns an integer timestamp when it can understand the value, or `None` when there is no usable cursor.

**Call relations**: `_page_loop` calls this before making requests. If a stream uses Stripe’s `created` field as its cursor, the converted value is sent to Stripe as `created[gte]` so Stripe returns only records created at or after that time.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 206–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Stripe stream page by page. It chooses the right fetching strategy for ordinary streams, child streams, query-based child streams, and external account streams.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor from the previous sync. It checks which kind of stream is being requested, delegates to the matching paginator, and yields lists of normalized records. If Stripe replies with 401 or 403, it turns that refusal into a `StreamSkipped` signal.

**Call relations**: The sync framework calls `paginate` when it wants records from a Stripe stream. `paginate` then hands off to `_page_loop` for simple listing, `_paginate_substream` for child endpoints, `_paginate_substream_query` for parent-ID query endpoints, or `_paginate_external_accounts` for connected-account bank account and card streams.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 234–265)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function performs Stripe’s standard page-by-page list walk. It is the reusable engine behind most Stripe reads in this file.

**Data flow**: It receives a client, URL path, stream definition, optional cursor, and optional extra query parameters. It builds request parameters such as `limit`, `starting_after`, and sometimes `created[gte]`, sends the request, normalizes each returned record, yields the page, and repeats while Stripe says `has_more` is true.

**Call relations**: `paginate` uses `_page_loop` directly for ordinary streams. The substream helpers also call it to list parents and children, so this function is the shared “turn Stripe pages into record batches” machinery.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 268–288)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function lightly normalizes a Stripe record before the rest of the system sees it. Its main job is to turn numeric timestamp fields into readable ISO date strings where the system expects `created_at` or `updated_at` values.

**Data flow**: It receives one raw Stripe record and the stream definition that explains the cursor field. It copies the record, converts numeric `created_at` and `updated_at` values if present, fills `created_at` from `created` when possible, fills `updated_at` from the stream cursor when possible, and returns the copied record.

**Call relations**: `_page_loop` calls this for every record returned by Stripe. The normalized records are then yielded upward to `paginate` or to whichever substream paginator requested the page.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 290–312)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections that live under parent Stripe objects. For example, it can read line items under each invoice or payment methods under each customer.

**Data flow**: It receives a client and a child stream definition. It finds the parent stream, walks parent pages, extracts each parent ID, builds the child URL path, reads child pages, and adds parent information such as `customer_id` or `invoice_id` onto each child record before yielding it.

**Call relations**: `paginate` calls this when the requested stream is listed in the child-path map. It relies on `_stream_spec` to find the parent definition, `_parent_pages` to walk the parents, and `_page_loop` to fetch each child collection.

*Call graph*: calls 3 internal fn (_page_loop, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 314–319)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper returns pages of parent records for a substream. It hides whether the parent itself is a normal stream or another query-based substream.

**Data flow**: It receives a client and a parent stream definition. If that parent is query-based, it returns the query-substream paginator; otherwise it returns the normal page loop for the parent’s list endpoint.

**Call relations**: `_paginate_substream` calls this before it can fetch children. When the parent is simple, `_parent_pages` uses `_list_path` and `_page_loop`; when the parent is itself query-based, it hands off to `_paginate_substream_query`.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 321–335)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads child collections where Stripe expects the parent ID as a query parameter rather than inside the URL path. Subscription items are an example: it lists subscriptions, then asks for subscription items with `subscription=<id>`.

**Data flow**: It receives a client and a stream definition. It looks up the parent stream name, the query parameter name, and the child path; walks all parent records; sends child requests with the parent ID as an extra parameter; and stamps that parent ID onto each child row before yielding it.

**Call relations**: `paginate` calls this directly for query-based substreams. `_parent_pages` can also call it when a deeper substream needs a parent that is itself query-based, such as usage records beneath subscription items.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 337–350)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads external bank accounts or cards attached to Stripe accounts. These are special because the connector must first list accounts, then fetch each account’s external accounts endpoint.

**Data flow**: It receives a client and an external-account stream definition. It lists Stripe accounts, extracts each account ID, calls `/v1/accounts/{account_id}/external_accounts`, and adds `account_id` to every returned bank account or card record before yielding the page.

**Call relations**: `paginate` calls this for the two external account streams. It uses `_stream_spec` to find the accounts stream, `_list_path` to build the accounts endpoint, and `_page_loop` to read both the account list and each account’s external-account list.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 352–353)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This helper finds the stream definition with a given name. It lets the pagination code move from a child stream to the parent stream it depends on.

**Data flow**: It receives a stream name, searches the `STRIPE_STREAMS` catalog, and returns the matching `StreamSpec`. If no matching stream exists, the search would fail rather than silently guessing.

**Call relations**: Substream pagination functions call this whenever they need a related stream definition, such as finding `customers` before reading customer payment methods or finding `accounts` before reading external accounts.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Point-of-sale commerce
Square supplies commerce-oriented streams for customers, payments, locations, catalog data, orders, and inventory counts.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `during source sync pagination`

Square exposes different kinds of data through different web API patterns. Some resources are fetched with normal GET requests, some require POST search requests, some use page cursors, and orders must first look up the account's locations. This file hides those differences behind one connector called SquareConnector.

Think of it like a translator at a service desk. The rest of the system asks, “Give me the next page of payments” or “Give me catalog items after this point,” and this connector knows which Square endpoint to call, which request shape Square expects, and where the records live in the response.

The file defines the available Square streams and their key fields, such as each record's primary ID and optional time field used for incremental syncing. Incremental syncing means asking only for records newer than the last saved cursor, so the system does not reread everything every time.

It also sets the required Square API version header on every request. If Square rejects access with a 401 or 403 status, the connector turns that into StreamSkipped, meaning this stream should be skipped because the credential is invalid or missing the needed permission. This matters because one missing Square permission should not necessarily crash the entire sync.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Square and adds the Square API version header. Square requires this header so both sides agree on which version of the API rules to use.

**Data flow**: It receives a base URL and a credential, asks the parent RestConnector to build the authenticated web client, then adds the Square-Version header. It returns the ready-to-use client, with authentication and the pinned Square API version attached.

**Call relations**: This is part of the connector setup before any stream is read. The broader RestConnector machinery calls it when creating the client, and later pagination methods use that client for all Square requests.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading Square streams page by page. Given a stream name, it chooses the right Square-specific reading method and yields lists of records for the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor marking where the previous sync left off. It checks the stream name, calls the matching helper, and yields each non-empty page of records. If the stream is unknown or Square refuses access with 401 or 403, it raises StreamSkipped so the sync can skip that stream with a clear reason.

**Call relations**: The syncing framework calls this when it needs data from a Square stream. It hands customers, payments, and refunds to SquareConnector._cursor_get; catalog streams to SquareConnector._catalog; orders to SquareConnector._orders; locations to SquareConnector._locations; and inventory counts to a direct POST request, using records_at to pull the record list out of Square's response.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square endpoints that use a simple cursor-based list pattern, such as customers, payments, and refunds. A cursor is a marker that tells the API where to continue, like a bookmark in a long list.

**Data flow**: It receives the client, the stream description, and an optional saved cursor. For payments and refunds, it sends the cursor as Square's begin_time filter. For other streams, it fetches pages and then filters out records whose cursor field is not newer than the saved cursor. It yields only pages that still contain records after filtering.

**Call relations**: SquareConnector.paginate calls this for customers, payments, and refunds. This helper relies on the shared RestConnector page-walking behavior to make repeated GET requests, then applies Square-specific cursor rules before handing pages back to paginate.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square's catalog for either item records or category records. Square uses one search endpoint for both, so this function chooses the correct object type and walks through all result pages.

**Data flow**: It receives the client, the catalog stream description, and an optional saved cursor. It builds a POST body with the desired catalog object type and page limit, includes Square's continuation cursor when there is another page, and extracts records from the objects field of the response. If a saved cursor exists, it keeps only records newer than that cursor, then yields non-empty pages until Square stops returning a next cursor.

**Call relations**: SquareConnector.paginate calls this for catalog_items and catalog_categories. It uses records_at to safely pull the objects list out of Square's response, then returns those pages to paginate for the main sync flow.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account's locations. Locations are useful as their own stream and also needed before searching orders, because Square order searches must be scoped to location IDs.

**Data flow**: It receives the HTTP client, sends a GET request to Square's /locations endpoint, and extracts the locations list from the response. It returns that list as plain record dictionaries.

**Call relations**: SquareConnector.paginate calls this directly when syncing the locations stream. SquareConnector._orders also calls it first so it can collect location IDs before asking Square for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all available account locations. It first discovers the locations, then searches for orders tied to those location IDs.

**Data flow**: It receives the client and an optional cursor. It fetches locations, keeps only valid string location IDs, and stops if there are none. It then repeatedly POSTs to /orders/search with those location IDs, a page limit, an optional created_at start filter based on the cursor, and Square's continuation cursor when needed. It extracts the orders list from each response and yields pages until there is no next cursor.

**Call relations**: SquareConnector.paginate calls this when the requested stream is orders. This function calls SquareConnector._locations because orders depend on location IDs, then uses records_at to pull order records out of each Square search response before passing pages back to paginate.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### Accounting systems
QuickBooks and Xero provide read-only accounting connectors for invoices, bills, vendors, payments, journal entries, tenant-aware records, and other ledger data.

### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `source sync / request handling`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each record type. Instead, every read is done by sending a SQL-like query to one shared `/query` endpoint. This file hides that awkwardness behind a connector that the rest of the system can use in a standard way.

The file first describes all the QuickBooks streams the system knows how to read. A stream is one kind of record, such as customers, accounts, bills, or tax rates. Most streams can be read incrementally, meaning the connector asks only for records changed after the last saved timestamp. A few reference lists, such as payment methods and tax agencies, do not use that timestamp and are read as full refreshes.

`QuickBooksConnector` then performs the actual reading. For each stream, it builds a QuickBooks query, asks for up to 100 records at a time, and keeps moving the starting position forward until QuickBooks returns a short page. That short page is the sign that there is no more data. If QuickBooks refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole sync. Finally, because QuickBooks stores update times inside a nested `MetaData` object, the connector copies that nested value to a flat field so the sync engine can easily track progress.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description for one QuickBooks record type that can be synced. It saves repeated setup, such as saying that QuickBooks uses `Id` as the main identifier and `MetaData.LastUpdatedTime` as the usual change timestamp.

**Data flow**: It receives a friendly stream name, the exact QuickBooks entity name to query, and optional choices such as whether the stream is canonical or whether it has a cursor timestamp. It packages those details into a `StreamSpec`, which is the standard stream description the rest of the source framework understands.

**Call relations**: This function is used while the file is loaded to build `QUICKBOOKS_STREAMS`, the connector’s menu of available QuickBooks record types. It hands each completed stream description to `StreamSpec`, which stores the metadata used later by `QuickBooksConnector.paginate` and `QuickBooksConnector.flatten`.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This function writes the QuickBooks query string for one page of one stream. It turns the system’s stream settings into the SQL-like language QuickBooks expects at its `/query` endpoint.

**Data flow**: It takes a stream description, an optional saved cursor value, and the page’s starting position. It creates a `SELECT * FROM ...` query, adds a `WHERE` and `ORDER BY` clause when incremental syncing is possible, escapes single quotes in the cursor value, and appends the page size limit. The result is a plain query string ready to send to QuickBooks.

**Call relations**: `QuickBooksConnector.paginate` calls this each time it needs the next page of records. `_build_query` does not contact QuickBooks itself; it only prepares the question that `paginate` will send.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for QuickBooks records. It asks QuickBooks for records one page at a time and yields each non-empty page back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced update time. Starting at position 1, it builds a query, sends it to QuickBooks, extracts the records from `QueryResponse`, and yields the list of records if any were returned. If the page has fewer than 100 records, it stops; otherwise it advances the starting position and repeats. If QuickBooks returns 401 or 403, it converts that refusal into a `StreamSkipped` error with a helpful explanation.

**Call relations**: The source framework calls this when it wants records from a QuickBooks stream. During each loop it relies on `QuickBooksConnector._build_query` to create the query text, then uses the inherited REST request helper to call `/query`. If QuickBooks refuses access, it raises `StreamSkipped` so the wider sync can treat that stream as unavailable rather than as a mysterious failure.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function makes QuickBooks records easier for the sync engine to track. In particular, it copies a nested cursor value such as `MetaData.LastUpdatedTime` onto the top level of the record.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream’s cursor field is written as a dotted path, it reads that nested value from the record and returns a new record with the same data plus a flat key using the dotted name. If there is no nested cursor path to flatten, it returns the record unchanged.

**Call relations**: The source framework uses this after records are read, before progress is measured and records are stored or passed onward. It calls `get_path` to safely read the nested value, and its output lets the sync engine advance the saved watermark without needing to understand QuickBooks’ nested `MetaData` shape.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `source sync`

Xero is an accounting service, but its API does not return data in the exact shape this project expects. This file is the adapter between the two. It lists the Xero resources the system can sync, such as accounts, invoices, contacts, payments, and tax rates, and describes how each should be named and keyed once it enters the system.

The connector only reads from Xero. It does not write or update accounting data. When a sync runs, it asks Xero for one stream at a time. Some Xero resources arrive in pages of up to 100 records, like turning pages in a catalog. Others are small enough that Xero sends them all at once. The connector knows which behavior to use.

For incremental syncs, where the system only wants records changed since the last run, Xero expects the date in an HTTP header called `If-Modified-Since`, not in the URL. This file converts stored cursor values into the date format Xero expects. It also adds the required `xero-tenant-id` header when the connector was created for a specific Xero organization.

Finally, Xero uses different ID field names for different resources, such as `InvoiceID` or `AccountID`. The rest of the system expects a plain `id`, so this connector copies the right Xero field into `id` before records move on.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Xero resource stream. It keeps the stream definitions short and consistent, so each resource can be registered with the same key fields and update-time fields.

**Data flow**: It receives a stream name, the matching Xero response object name, an optional cursor field, and whether the stream is considered canonical. It packages those choices into a `StreamSpec`, which is the system’s small instruction card for how to sync that stream.

**Call relations**: This function is used while the file is being loaded to build the `XERO_STREAMS` list. It hands the finished stream description to the rest of the connector, and later `XeroConnector.paginate` and `XeroConnector.flatten` rely on those descriptions to know which Xero endpoint and fields to use.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function turns the system’s saved “last seen update time” into the exact date text Xero wants in the `If-Modified-Since` request header. That matters because Xero will only do incremental reads correctly when the date is in this older web-standard format.

**Data flow**: It receives a cursor value, which may be missing, empty, a Unix timestamp number, an ISO-style date string, or already-formatted text. It cleans and interprets the value when it can, converts it to UTC time, and returns a string like `Tue, 12 Mar 2024 15:30:00 GMT`; if the value is unusable or absent, it returns nothing.

**Call relations**: During a sync, `XeroConnector.paginate` calls this before making requests for streams that support update cursors. The result is placed into the HTTP headers sent to Xero, so Xero can return only records modified since that time.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This sets up a Xero connector instance, optionally tied to one Xero tenant. In Xero, a tenant is one organization or company account inside an authorized grant.

**Data flow**: It receives an optional tenant ID and stores it on the connector. Nothing is returned, but later requests can use that saved tenant ID to tell Xero which organization’s data to read.

**Call relations**: This runs when code creates a `XeroConnector`. The saved tenant ID is later used by `XeroConnector._make_client` when preparing the HTTP client that will talk to Xero.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to call Xero’s API. It adds Xero’s required tenant header when a tenant ID was supplied, while leaving authentication to the shared credential system.

**Data flow**: It receives the API base URL and a credential object from the wider system. It asks the parent REST connector to create the basic async HTTP client, then, if a tenant ID is available, adds `xero-tenant-id` to the client’s default headers. It returns the ready-to-use client.

**Call relations**: The broader REST connector flow calls this when it needs a client for a sync. After this function returns, `XeroConnector.paginate` uses that client to make the actual GET requests to Xero.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Xero streams. It requests records from Xero, follows Xero’s page-by-page rules, applies incremental-sync headers when possible, and yields batches of records back to the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It builds the Xero path from the stream, optionally converts the cursor into an `If-Modified-Since` header, then sends GET requests. For small non-paged streams it makes one request; for paged streams it keeps requesting `page=1`, `page=2`, and so on until Xero returns no records or fewer than 100 records. It yields each non-empty batch of raw records. If Xero replies with 401 or 403, meaning unauthorized or forbidden, it turns that into `StreamSkipped` so the sync can skip that stream instead of treating every case as a crash.

**Call relations**: This function is called by the connector framework when it is time to read a stream. It calls `_cursor_to_rfc1123` to prepare incremental-read dates, uses the HTTP client to fetch data from Xero, and may raise `StreamSkipped` to explain that the current credential or permission grant cannot access that stream.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes each Xero record so the rest of the system can identify it with a simple `id` field. Xero uses different ID names for different resources, so this function hides that inconsistency.

**Data flow**: It receives one record and the stream it came from. If the record already has `id`, it leaves it alone. Otherwise it looks up the Xero-specific ID field for that stream, copies that value into a new string `id`, and returns the updated record. If no matching ID field or value exists, it returns the record unchanged.

**Call relations**: After `XeroConnector.paginate` has yielded raw Xero records, the connector framework can call this as part of preparing records for storage or recall. It uses the stream name from the same `StreamSpec` definitions created by `_stream` to choose the right Xero ID field.

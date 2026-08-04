# Finance, Billing, Spend, and Commerce Source Connectors  `stage-14.1.6`

This stage is a set of read-only “source connectors.” A connector is an adapter that knows how to talk to one outside service and reshape its data into the common form used by the sync system. These files are used during the main syncing work, after the system has been configured and is ready to fetch records.

Each connector handles one finance or commerce tool. Brex brings in spend data such as expenses, vendors, budgets, users, and departments. Chargebee and Recurly bring in subscription-billing records, including customers, subscriptions, invoices, and transactions. Stripe reads payment and billing data such as charges, checkout sessions, invoices, customers, and related child records. QuickBooks and Xero read accounting ledgers, including invoices, bills, contacts, accounts, payments, and journal entries. Square reads commerce records such as customers, payments, locations, catalog items, orders, and inventory counts.

Together, they act like different plug shapes for the same socket: each speaks its service’s API, follows paged results, and produces reusable streams of records for storage or later processing.

## Files in this stage

### Spend Management
Brex connector support covers spend-management records such as transactions, expenses, users, vendors, budgets, and departments.

### `extensions/sources/ufo_ext_sources/brex.py`

`io_transport` · `source sync`

Brex exposes business spending data through a web API. This file is the connector that knows which Brex API address to call for each kind of data, how Brex splits long result lists into pages, and how to describe those data streams to the rest of the system.

The file first defines the Brex streams the system can sync. A stream is one category of records, such as “transactions” or “vendors.” Each stream has a name, a field that uniquely identifies each record, and sometimes a date field that can be used as a progress marker. For example, expenses use “purchased_at,” while transactions use “posted_at_date.”

The `BrexConnector` class then supplies the Brex base URL and the page-reading logic. Brex returns list results in a standard envelope: an `items` list plus a `next_cursor`, which is like a bookmark for the next page. The connector asks for up to 100 records at a time, yields any records it receives, then follows the bookmark until Brex says there are no more pages.

An important detail is that most Brex endpoints do not support asking only for recently changed records. So this connector mostly performs a full read of each stream, while still recording cursor-like date fields where available.

#### Function details

##### `_stream`  (lines 33–44)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a stream description for one Brex object type. It saves repeated setup by filling in the standard fields the rest of the sync system expects.

**Data flow**: It receives a stream name, a unique-record field, an optional date field used as a progress marker, and a flag saying whether this stream is a main/canonical one. It puts those values into a `StreamSpec`, which is the system’s small description object for a source stream, and returns that object.

**Call relations**: This helper is used while the file is loaded to build the `BREX_STREAMS` list. It hands its settings to `StreamSpec`, so the wider source framework can later know what Brex streams exist and how to identify records in them.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 63–80)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads all pages for one Brex stream from the Brex API. Someone would use it when syncing a stream, so the system can receive records in manageable batches instead of trying to fetch everything at once.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value. It looks up the correct Brex API path for that stream, asks Brex for a page of up to 100 records, turns the returned `items` value into a safe list, yields that list if it has records, then follows Brex’s `next_cursor` bookmark to request the next page. When there is no next cursor, it stops. If the stream has no known Brex endpoint, it raises an error instead of guessing.

**Call relations**: During a sync, the connector framework calls this method to fetch records for each Brex stream. Inside the loop it relies on the inherited `_get` request helper to talk to Brex, then uses `ufo.sdk.sources.list_or_empty` to make sure missing or empty `items` values do not break the batch flow.

*Call graph*: 1 external calls (list_or_empty).


### Subscription Billing
Subscription-billing connectors expose customer, subscription, invoice, transaction, and related recurring-revenue records as syncable streams.

### `extensions/sources/ufo_ext_sources/chargebee.py`

`io_transport` · `during source sync, while fetching Chargebee records`

Chargebee is an online billing system, and its API returns data in a very regular but slightly wrapped shape: each page contains a list, each list entry hides the real record inside a resource-named envelope, and more pages are fetched with a next_offset token. This file is the connector that knows those rules.

It defines which Chargebee objects can be synced, which field identifies each record, and which timestamp can be used as a cursor. A cursor is like a bookmark: on the next run, the connector can ask Chargebee for only records newer than the last one it saw.

The main class, ChargebeeConnector, builds an HTTP client with Chargebee’s required authentication, chooses the right paging strategy for each stream, and flattens Chargebee’s wrapped records into a simpler shape. Most streams are ordinary list endpoints, such as /customers or /invoices. A few are substreams, meaning they must first read parent records and then fetch child records under each parent, such as contacts for each customer.

If Chargebee refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating it like an unknown crash. There is no write path here; this connector only reads from Chargebee.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a StreamSpec, which is the project’s small description of one syncable Chargebee stream. It saves repeated boilerplate when declaring many similar streams.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults when details are not supplied, then returns a StreamSpec object that the connector later uses to know how to sync that stream.

**Call relations**: This helper is used while the module is being loaded to build the CHARGEBEE_STREAMS list. It hands each completed stream description to the connector class through that list, so later pagination and flattening can use the same shared stream metadata.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to Chargebee. It applies Chargebee’s authentication style and sets timeouts so a stalled network call does not wait forever.

**Data flow**: It takes a base URL and a resolved Credential. It trims the URL, prepares JSON/form headers, and then either uses a provided custom transport or creates Basic authentication with the API key as the username and an empty password. It returns an httpx.AsyncClient ready to make asynchronous web requests. If there is no usable authentication information, it raises an error instead of silently making bad requests.

**Call relations**: The broader REST connector framework calls this when it needs a client for a Chargebee sync. This function delegates the low-level networking setup to httpx objects, while keeping the Chargebee-specific authentication rule in one place.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function unwraps Chargebee records so the rest of the system can read them like ordinary flat dictionaries. Without it, useful fields such as id or updated_at would remain hidden inside a nested Chargebee envelope.

**Data flow**: It receives one raw record and the stream description for that record. If the record contains a nested dictionary under the stream’s source object name, it copies the inner record out to the top level. Any extra fields already on the outer record, such as a stamped parent id for a substream, are preserved unless the inner record already has that key. It returns the flattened record. If the expected envelope is not present, it returns the record unchanged.

**Call relations**: After pagination produces raw Chargebee-shaped records, the connector framework can call this before storing or passing records onward. It does not call other helpers; it is the small cleanup step between Chargebee’s response format and the project’s common record format.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading records from Chargebee. Given a stream, it chooses the correct way to fetch all of that stream’s pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor bookmark. It checks the stream name. For normal streams, it sends the work to the shared list paginator. For special child streams, it sends the work to the matching parent-and-child paginator. It yields pages of raw records as they arrive. If Chargebee responds with 401 or 403, it turns that into StreamSkipped, meaning this stream cannot be read with the current access but the situation is understood.

**Call relations**: The sync framework calls this when it wants records for one Chargebee stream. This function then hands off to _paginate_list, _paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, or _paginate_subscription_scheduled depending on the stream. It is the connector’s central branching point for all read paths.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters used for ordinary Chargebee list requests. It adds both the page size and, when possible, the incremental bookmark.

**Data flow**: It receives a stream description and an optional cursor value. It always starts with a limit of 100 records per page. If a cursor is present and the stream has a cursor field, it adds Chargebee’s after filter for that field, meaning 'only send records strictly newer than this value.' It returns the completed parameter dictionary.

**Call relations**: _paginate_list calls this right before asking Chargebee for a normal list endpoint. By keeping parameter construction here, every ordinary list stream uses the same page size and cursor rule.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal Chargebee list endpoint, such as customers, invoices, or transactions. It follows Chargebee’s next_offset tokens until there are no more pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the correct endpoint path for the stream, builds request parameters with _build_list_params, then asks the shared cursor-page helper to fetch each page from Chargebee’s list field and follow next_offset. It yields each page of raw records.

**Call relations**: paginate calls this directly for standard streams. The substream paginators also call it first to read their parent records, such as items before attached items or customers before contacts. This makes it the common base reader for most Chargebee data.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads attached items, which Chargebee exposes underneath each item rather than as one simple global list. It first finds items, then asks for attached items for each one.

**Data flow**: It receives an HTTP client and an optional cursor. It uses the normal item stream to read item pages, extracts each item id, skips parents without an id, and then requests /items/{item_id}/attached_items for each parent. Each child record is stamped with the item_id so it remains clear which item it came from. It yields pages of attached-item records.

**Call relations**: paginate calls this when the requested stream is attached_item. This function relies on _paginate_list to get parent items and then hands each parent-specific endpoint to _paginate_substream to do the repeated child paging work.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads customer contacts, which are stored under individual customers in Chargebee. It walks through customers first, then fetches contacts for each customer.

**Data flow**: It receives an HTTP client and an optional cursor. It reads customer pages through the normal list paginator, pulls out each customer id, and skips any customer record without one. For every valid customer id, it fetches /customers/{customer_id}/contacts and adds customer_id to each contact record. It yields pages of contact records.

**Call relations**: paginate calls this for the contact stream. It uses _paginate_list to get the parent customers, then uses _paginate_substream so the child paging pattern stays shared with other parent-child streams.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads quote line groups, which Chargebee exposes under each quote. It connects each child group back to the quote it came from.

**Data flow**: It receives an HTTP client and an optional cursor. It reads quote pages, extracts each quote id, and skips records without an id. For each quote id, it requests /quotes/{quote_id}/quote_line_groups and stamps quote_id onto each child record. It yields pages of quote-line-group records.

**Call relations**: paginate calls this for the quote_line_group stream. Like the other child-stream readers, it gets parents through _paginate_list and delegates the repeated child-page loop to _paginate_substream.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads subscription details that include scheduled changes. Chargebee exposes this as a one-subscription-at-a-time detail endpoint rather than a normal list.

**Data flow**: It receives an HTTP client and an optional cursor. It first reads subscription pages, extracts each subscription id, and skips records without an id. For each subscription, it requests /subscriptions/{id}/retrieve_with_scheduled_changes. If the response contains a subscription envelope, it yields a one-record page containing that subscription plus subscription_id so the record keeps its parent identity.

**Call relations**: paginate calls this when the stream is subscription_with_scheduled_changes. It uses _paginate_list to discover the parent subscriptions, then performs the per-subscription detail request itself because this endpoint returns a single record rather than a paged list of children.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads a paged child endpoint and labels every child record with its parent id. It avoids repeating the same child-pagination code for attached items, contacts, and quote line groups.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent id field to add, and the parent id value. It requests pages using Chargebee’s list and next_offset format with a limit of 100. For each dictionary-shaped record, it copies the record, adds the parent id field, and collects it into a stamped page. It yields only non-empty stamped pages.

**Call relations**: _paginate_attached_items, _paginate_contacts, and _paginate_quote_line_groups call this after they have found a valid parent id. It is the shared worker that turns a parent-specific Chargebee endpoint into usable child pages with parent context attached.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/recurly.py`

`io_transport` · `during source sync when reading Recurly streams`

Recurly’s API does not return all records at once. It sends data in pages, with a “next” link when more pages are available. This file is the adapter that turns those pages into streams the rest of the system can consume, like a clerk repeatedly asking for the next folder until the filing cabinet is empty.

The file defines the available Recurly streams, such as accounts, subscriptions, invoices, plans, coupons, and related child records. Some records live directly at a top-level endpoint, like `/accounts`. Others live underneath a parent record, such as account notes under a specific account. For those child streams, the connector first lists parent IDs, then asks Recurly for each parent’s child records, and adds the parent ID onto each returned row so the relationship is not lost.

Authentication is special here: Recurly uses HTTP Basic authentication, with the API key as the username, rather than a bearer token. The connector also pins a specific Recurly API version through request headers so returned data stays predictable. If Recurly refuses access with an authorization error, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a stream description for one kind of Recurly data, such as accounts or invoices. This keeps the stream list compact while still recording details like the stream name, primary key, and time field used for incremental syncing.

**Data flow**: It receives a stream name plus optional settings. It fills in sensible defaults, such as using the name as the Recurly object path and `id` as the primary key, then returns a `StreamSpec`, which is the system’s standard description of a readable stream.

**Call relations**: This helper is used while the file is loaded to build the `RECURLY_STREAMS` list. It hands those stream descriptions to the connector class so the wider sync system knows what Recurly data can be requested.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Recurly. It sets timeouts, API version headers, and the correct authentication style before any data is requested.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares headers, and then either uses a provided auth-proxy transport or creates HTTP Basic authentication from the API key. It returns an asynchronous HTTP client ready to send requests. If no usable credential is present, it raises an error.

**Call relations**: The base connector infrastructure calls this when a Recurly sync needs a network client. The client it returns is later passed into pagination methods that fetch pages from Recurly endpoints.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: Converts Recurly’s “next page” link into a path the client can safely request. Recurly may return either a full URL or just a path, and this normalizes both forms.

**Data flow**: It receives a possible next-page link. If the link is empty, it returns nothing. If the link is a full URL, it keeps only the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: The pagination helpers call this after each page when Recurly says more data is available. It supplies the next request path for top-level streams, parent ID listing, coupon ID listing, and per-parent child streams.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for the first request to a Recurly stream. These parameters control page size, sort order, and where an incremental sync should begin.

**Data flow**: It receives a stream description and an optional cursor value, which is usually a timestamp from the last sync. It creates a parameter dictionary with the fixed page size, ascending order, and the stream’s cursor field. If both a cursor field and cursor value exist, it adds `begin_time` so Recurly returns only newer records.

**Call relations**: Top-level and per-parent pagination both call this before their first request. After the first request, Recurly’s own next-page links carry the paging information, so these initial parameters are not reused.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a requested Recurly stream and yields batches of records. This is the main read path used by the rest of the connector framework.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks whether the stream is a child stream, a special coupon-parent stream, or a normal top-level stream. It then delegates to the correct pagination helper and yields each non-empty page of records. If Recurly responds with 401 or 403, it turns that refusal into a `StreamSkipped` message.

**Call relations**: The sync engine calls this when it wants records for one stream. This function decides whether to hand off to `_paginate_per_parent` or `_paginate_top_level`, and it protects the wider sync from authorization failures on individual streams.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a normal Recurly endpoint that lists records directly, such as accounts, plans, or invoices. It keeps following Recurly’s next-page links until there are no more records.

**Data flow**: It receives a client, stream description, endpoint path, and optional cursor. It creates the first query, requests a page, yields the records if any are present, and then uses Recurly’s `has_more` and `next` fields to decide whether to continue. It stops when Recurly says there are no more pages.

**Call relations**: `paginate` calls this for ordinary streams and for the special coupon-parent case. This function relies on `_initial_query` for the first request and `_next_path` to prepare each follow-up request.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists account IDs from Recurly so child account resources can be fetched later. It is a preparatory step for streams that live under accounts, such as account notes or billing information.

**Data flow**: It starts at the `/accounts` endpoint with a fixed page size and sort order. For each returned account row, it checks that the row is a dictionary and has an ID, then yields that ID as text. It follows next-page links until the account list is complete.

**Call relations**: `_paginate_per_parent` calls this when it needs to visit every account before fetching a child resource. `_account_ids` uses `_next_path` to continue through Recurly’s account pages.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Lists only the coupon IDs that can have unique coupon codes beneath them. Recurly only exposes those child codes for bulk coupons, so this function filters out other coupon types.

**Data flow**: It starts at the `/coupons` endpoint and reads coupon pages. For each row, it confirms the row has an ID and that its `coupon_type` is `bulk`. It yields the qualifying coupon ID and follows next-page links until finished.

**Call relations**: `_paginate_per_parent` calls this for coupon-based child streams. It uses `_next_path` to move from one coupon page to the next before child records are requested.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child records that are stored under each parent record, such as notes under accounts or unique coupon codes under coupons. It preserves the parent-child relationship by adding the parent ID to each child row when needed.

**Data flow**: It receives the client, stream description, parent endpoint, child endpoint name, the field used to store the parent ID, and an optional cursor. It first chooses whether to list account IDs or coupon IDs. For each parent ID, it requests that parent’s child endpoint, yields any child records found, and stamps each dictionary row with the parent ID if the row does not already include it. It follows child next-page links before moving to the next parent.

**Call relations**: `paginate` calls this for streams listed as per-parent streams. This function depends on `_account_ids` or `_coupon_ids` to find parents, `_initial_query` to start each child listing, and `_next_path` to follow Recurly’s child pagination.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/stripe.py`

`io_transport` · `source sync run`

Stripe exposes many kinds of business data through a web API, but each kind has slightly different addresses and some records live underneath other records. This file is the Stripe “source connector”: it knows which Stripe streams exist, where to fetch them, how to walk through pages, and how to deal with linked child data.

The main idea is simple: Stripe returns lists in pages, like a book that says “there are more pages” and gives the last item’s id so the next request can continue after it. The connector repeatedly asks for up to 100 records, remembers the last id, and keeps going until Stripe says there is no more data. When possible, it also uses a cursor, usually a creation time, so a later sync can ask only for newer records.

Some streams are not top-level lists. For example, line items belong to checkout sessions, and payment methods belong to customers. For those, the connector first lists the parent records, then fetches each parent’s children, and adds the parent id onto each child row so the relationship is not lost.

The connector also pins a Stripe API version in the request header, normalizes timestamp fields into readable ISO date strings, and skips streams cleanly when Stripe replies that the current key is not allowed to read them.

#### Function details

##### `_stream`  (lines 86–104)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates a description of one Stripe stream, such as its name, Stripe object path, main id field, and cursor field. It keeps the long stream list readable and consistent.

**Data flow**: It receives the stream’s name and optional details like the source object name or cursor field. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses as a recipe for fetching that stream.

**Call relations**: It is used while building the module-level list of Stripe streams. Each call hands its settings to StreamSpec so the rest of the connector can treat all streams in a uniform way.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 179–182)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Stripe and adds the required Stripe API version header. That header tells Stripe which version of its API behavior this connector expects.

**Data flow**: It receives a base URL and a credential object. It asks the parent REST connector to create the basic authenticated web client, adds the Stripe-Version header, and returns the prepared client.

**Call relations**: This fits into connector setup before requests are made. The inherited REST connector does the general client creation, and this method adds the Stripe-specific version pin before pagination starts.


##### `StripeConnector._list_path`  (lines 185–186)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This builds the Stripe API path for a normal top-level list stream. For example, a stream whose source object is customers becomes /v1/customers.

**Data flow**: It receives a StreamSpec and reads its source_object field. It formats that value into a Stripe API path string and returns the path.

**Call relations**: The main paginate method and the substream helpers call this when they need the list address for a stream or a parent stream. It supplies the path that _page_loop will actually request.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 189–201)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved cursor into the numeric time format Stripe expects for created[gte] filters. It accepts either an already numeric timestamp or an ISO-style date string.

**Data flow**: It receives a cursor value or nothing. If the cursor is empty, it returns nothing; if it is digits, it returns that number; if it is a date string, it parses it and returns Unix time, meaning seconds since 1970-01-01 UTC. If parsing fails, it returns nothing.

**Call relations**: _page_loop calls this before requesting pages. Its result lets _page_loop ask Stripe for records created at or after the last remembered sync point.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 203–229)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry for reading one Stripe stream. It chooses the right fetching strategy: a simple top-level list, a child stream under parent records, a child stream filtered by query parameter, or external accounts under connected accounts.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name, delegates to the matching pagination helper, and yields pages of records. If Stripe returns 401 or 403, meaning unauthorized or forbidden, it turns that into StreamSkipped so one unreadable stream does not crash the whole sync.

**Call relations**: The broader source runner calls this when it wants records for a Stripe stream. This method then hands off to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, or _page_loop, depending on the stream shape.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 231–262)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This performs the repeated “get the next page” work for Stripe list endpoints. It is the shared engine behind normal streams and most child-stream fetching.

**Data flow**: It receives a client, an API path, a stream description, a cursor, and optional extra request parameters. It builds request parameters such as limit, starting_after, created[gte], and stream-specific filters, sends the request, normalizes each returned record, yields non-empty pages, and continues until Stripe says there are no more pages or it cannot safely continue.

**Call relations**: paginate uses it for simple streams, and the substream helpers use it for both parent and child lists. It calls _cursor_to_unix to prepare incremental time filtering and _browse_record to clean up each record before yielding it.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 4 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, paginate).


##### `StripeConnector._browse_record`  (lines 265–285)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly normalizes one Stripe record so time fields are easier for the rest of the system to read. In particular, it turns numeric timestamps into ISO date strings where appropriate.

**Data flow**: It receives one Stripe record and the stream description. It copies the record, converts created_at and updated_at if they are numeric timestamps, fills created_at from created when possible, fills updated_at from the stream cursor when possible, and returns the cleaned copy.

**Call relations**: _page_loop calls this for every record it receives from Stripe. The normalized records are then yielded up through paginate or through the substream helpers.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 287–310)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that are reached through a parent-specific URL, such as a customer’s payment methods or an invoice’s line items. It preserves the parent-child relationship by adding the parent id to each child row.

**Data flow**: It receives a client and a child stream description. It looks up the parent stream and child path template, pages through all parents, builds each child URL from the parent id, pages through the children, adds parent id fields and selected parent timestamp fields when configured, and yields the child pages.

**Call relations**: paginate calls this for streams listed in the child-path mapping. This helper uses _stream_spec to find the parent stream, _list_path to build the parent list URL, and _page_loop to fetch both parent and child pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_substream_query`  (lines 312–326)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child records that Stripe exposes through a shared endpoint filtered by a parent id in the query string. For example, it can fetch subscription items by asking for items with a particular subscription id.

**Data flow**: It receives a client and stream description. It finds the parent stream, pages through parents, skips parents without ids, then requests the child endpoint with an extra query parameter pointing to the current parent. It adds a matching parent id field to each child row and yields those rows in pages.

**Call relations**: paginate calls this for streams listed in the query-parent mapping. It relies on _stream_spec for the parent recipe, _list_path for the parent endpoint, and _page_loop for the actual repeated Stripe requests.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 328–341)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe accounts. These records are nested under each account, so the connector must visit accounts first and then fetch their external accounts.

**Data flow**: It receives a client and an external-account stream description. It pages through Stripe accounts, reads each account id, requests that account’s external_accounts endpoint, adds account_id to each returned child record, and yields the resulting pages.

**Call relations**: paginate calls this for the external_account_bank_accounts and external_account_cards streams. It uses _stream_spec to find the accounts stream, _list_path to fetch accounts, and _page_loop for both account and external-account pagination.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 343–344)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the StreamSpec recipe for a named Stripe stream. The substream code uses it when it needs to fetch a parent stream before fetching child records.

**Data flow**: It receives a stream name, searches the STRIPE_STREAMS list, and returns the first stream description with that name. If no matching stream exists, the search naturally fails.

**Call relations**: The substream helpers call this to translate a parent stream name, such as customers or accounts, into the full stream recipe needed by _list_path and _page_loop.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Accounting Ledgers
Accounting connectors read ledger-oriented business records such as invoices, bills, contacts, payments, accounts, customers, and journal entries.

### `extensions/sources/ufo_ext_sources/quickbooks.py`

`io_transport` · `during source sync when reading QuickBooks pages`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each kind of data. Instead, every read is sent as a SQL-like query to one shared `/query` endpoint. This file hides that awkwardness behind a `QuickBooksConnector`, so the rest of the project can treat QuickBooks like a normal paged data source.

The file first defines the QuickBooks streams the system knows about: accounts, customers, vendors, invoices, bills, payments, tax records, and more. Each stream says what QuickBooks object it reads, what field uniquely identifies a record, and which timestamp can be used as a cursor. A cursor is like a bookmark: it lets the next sync ask only for records changed after the last successful run. Some reference data, such as payment methods and tax agencies, does not use that bookmark and is read in full.

During sync, the connector builds a QuickBooks query, asks for up to 100 records, yields those records, then asks for the next page until QuickBooks returns fewer than 100. If QuickBooks refuses access with an authorization error, the stream is skipped with a clear message instead of crashing the whole connector. One small but important cleanup step flattens the nested update timestamp, so the shared sync machinery can track progress consistently.

#### Function details

##### `_stream`  (lines 28–43)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the description of one QuickBooks stream. It saves repeated setup by filling in the common QuickBooks rules, such as using `Id` as the unique record key and QuickBooks metadata timestamps for creation and update times.

**Data flow**: It takes a friendly stream name, the QuickBooks object name to query, and optional choices such as whether the stream has a cursor. It combines those details with the shared defaults for QuickBooks records and returns a `StreamSpec`, which is the system’s small recipe for syncing that kind of record.

**Call relations**: The file uses this helper while building the list of QuickBooks streams. Each call produces one stream recipe that `QuickBooksConnector` later exposes to the wider source-sync framework.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 84–91)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This builds the text query that QuickBooks expects for one page of one stream. It is the bridge between the system’s generic idea of “read this stream after this cursor” and QuickBooks’ SQL-like query language.

**Data flow**: It receives a stream description, an optional cursor value, and the starting row number for the page. It creates a `SELECT * FROM ...` query, adds a `WHERE` clause when incremental syncing is possible, escapes apostrophes in the cursor so the query stays valid, adds ordering by the cursor field, and finishes with the page size. The result is a query string ready to send to QuickBooks.

**Call relations**: `paginate` calls this each time it needs another page. After this function builds the query text, `paginate` sends it to QuickBooks through the shared REST request helper.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 93–115)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one QuickBooks stream page by page. It keeps asking QuickBooks for the next group of records until there are no more pages, and it turns authorization failures into a controlled “skip this stream” outcome.

**Data flow**: It starts with an HTTP client, a stream recipe, and an optional cursor bookmark. It builds a query for the first page, sends it to QuickBooks, opens the `QueryResponse` wrapper, and pulls out the records for the requested entity. Non-empty record lists are yielded to the caller. If a page is shorter than the maximum page size, it stops because that means the stream has ended. If QuickBooks responds with a refusal such as missing permission or bad credentials, it raises `StreamSkipped`; other HTTP errors are passed upward unchanged.

**Call relations**: The broader sync framework calls this when it is time to read a QuickBooks stream. Inside its loop it relies on `_build_query` to create each QuickBooks query, then hands each page of records back to the framework for storage and cursor tracking.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 117–120)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This makes nested QuickBooks timestamp data easier for the generic sync system to read. In particular, it copies a value such as `MetaData.LastUpdatedTime` onto the top level of the record under that same dotted name.

**Data flow**: It receives one QuickBooks record and the stream recipe for that record. If the stream’s cursor field is written as a dotted path, it looks inside the nested record to find that value and returns a copy of the record with an extra flat key. If there is no dotted cursor field, it returns the record unchanged.

**Call relations**: The source-sync machinery uses this after records have been fetched so it can update its cursor bookmark. This function delegates the nested lookup to `get_path`, then returns the record shape the rest of the sync code expects.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/xero.py`

`io_transport` · `sync run, while reading records from Xero`

Xero is an accounting service, and its API has a few habits that this file hides from the rest of the project. Xero wraps lists inside named envelopes, such as returning accounts under an "Accounts" key. It also uses different ID field names for different record types, such as "InvoiceID" or "ContactID", while the sync system expects a common "id" field. This connector translates those differences.

The file first defines the Xero streams: the kinds of things the system can read from Xero, like accounts, contacts, invoices, payments, tax rates, and users. A stream is simply one category of records to sync. Some streams are marked as especially important, or "canonical", meaning they are core resource types for this source.

When syncing, the connector asks Xero for pages of data. Most Xero endpoints return 100 records at a time, so the connector keeps asking for page 1, page 2, and so on until it sees a short or empty page. A few small endpoints do not really page, so it reads those once. For incremental sync, it sends Xero an "If-Modified-Since" request header, which means "only give me records changed after this time." If Xero refuses access, the stream is skipped with a clear explanation instead of crashing the whole idea of syncing other streams.

#### Function details

##### `_stream`  (lines 64–78)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Xero data stream, such as invoices or contacts. It keeps the stream definitions compact and consistent so each one has the right name, source object, primary key, and update-time field.

**Data flow**: It receives a local stream name, the matching Xero response object name, and optional settings about change tracking and whether the stream is canonical. It packages those choices into a StreamSpec object. The result is a reusable stream description used later by the connector when it decides what endpoint to call and how to interpret records.

**Call relations**: This helper is used while the file is loaded to build the XERO_STREAMS list. It hands its settings into StreamSpec.__init__, which creates the actual stream specification objects that XeroConnector exposes through its streams_list.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 106–123)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved sync cursor into the date format Xero expects in the "If-Modified-Since" header. In plain terms, it turns "last time we synced" into the particular timestamp wording Xero understands.

**Data flow**: It receives a cursor value, which may be empty, a Unix timestamp number, an ISO-style date string, or already some other date text. Empty values become nothing. Numeric values are treated as seconds since 1970 and formatted as a GMT date. ISO-style dates are parsed, treated as UTC if they have no timezone, and then formatted for Xero. If parsing fails, the original text is passed through.

**Call relations**: XeroConnector.paginate calls this when it has a cursor for a stream. The returned date string is then placed in the HTTP request header so Xero can filter results by modification time. Internally, it relies on datetime parsing helpers to understand timestamp and ISO date inputs.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 131–132)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This stores the optional Xero tenant ID for the connector. A tenant ID identifies which Xero organisation to read from when one account grant can cover more than one organisation.

**Data flow**: It receives an optional tenant ID from whoever creates the connector. It saves that value on the connector instance. Nothing is returned, but later client creation can use the saved tenant ID to add the required Xero request header.

**Call relations**: This runs when a XeroConnector object is created. Its saved tenant ID is later read by XeroConnector._make_client, which adds it to outgoing Xero API requests when present.


##### `XeroConnector._make_client`  (lines 134–138)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Xero and adds Xero’s tenant header when needed. The tenant header is like telling Xero which company file or organisation the request is for.

**Data flow**: It receives a base URL and a credential object. It first asks the parent RestConnector to build the normal authenticated HTTP client. If this connector was given a tenant ID, it adds that ID to the client’s default headers under "xero-tenant-id". It returns the ready-to-use async HTTP client.

**Call relations**: The broader REST connector machinery calls this when it needs a client for Xero. This method builds on the parent connector’s client setup, then adds Xero-specific tenant information before XeroConnector.paginate uses that client to make GET requests.


##### `XeroConnector.paginate`  (lines 140–175)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads records for one Xero stream, one batch at a time. It knows which Xero endpoints are paged, which are one-shot, how to ask for only recently changed records, and how to turn access refusals into a controlled stream skip.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero path from the stream’s source object. If the stream supports change tracking and a cursor was supplied, it converts that cursor into Xero’s required date header. For non-paged streams, it makes one GET request and yields the records inside the response envelope. For paged streams, it repeatedly requests page 1, page 2, and so on, yielding each list of records until there are no records or fewer than 100. If Xero replies with 401 or 403, it raises StreamSkipped with a helpful message; other HTTP errors are allowed to continue upward.

**Call relations**: This is the main read loop used by the sync system for Xero streams. It calls _cursor_to_rfc1123 to prepare incremental-sync headers, uses httpx.AsyncClient.get to fetch data from Xero, and raises StreamSkipped when Xero refuses a stream because of missing permission or invalid credentials. The records it yields are later available for flattening and storage by the surrounding source framework.

*Call graph*: calls 2 internal fn (__init__, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 177–186)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes a single Xero record so the rest of the system can identify it using a common "id" field. Xero uses different ID names for different resource types, and this function smooths that out.

**Data flow**: It receives one record and the stream it came from. If the record already has an "id", it leaves it alone. Otherwise, it looks up the Xero-specific ID field for that stream, such as "InvoiceID" for invoices. If that field exists in the record, it returns a copy of the record with an added string "id" value. If no matching ID can be found, it returns the record unchanged.

**Call relations**: This fits after XeroConnector.paginate has yielded raw records from the API. The surrounding connector framework can call it before storing or indexing records, so every stream can use the same primary key name even though Xero’s API uses many different names.


### Commerce Transactions
The Square connector brings point-of-sale and commerce records such as customers, payments, locations, catalog items, orders, and inventory counts into the sync system.

### `extensions/sources/ufo_ext_sources/square.py`

`io_transport` · `during source sync pagination`

Square exposes different kinds of business data through different API shapes. Some data is read with simple list requests, some requires search requests, and some must be fetched one account location at a time. This file hides those differences behind one connector, so the rest of the project can simply ask for a named stream like “payments” or “orders” and receive batches of records.

The main class, SquareConnector, describes the available Square streams and knows the Square API base address. It also pins the Square API version by adding a Square-Version header to every request. That matters because Square can change behavior between versions; this keeps syncs predictable.

The central method is paginate. It acts like a traffic director. For each stream name, it chooses the right fetching strategy: locations are fetched once, customers/payments/refunds use cursor-based GET pages, catalog data uses Square’s catalog search endpoint, orders are searched across known locations, and inventory counts are retrieved with a batch endpoint. A cursor is a bookmark that says “continue from here” during incremental syncs.

If Square refuses access with a 401 or 403 status, the connector raises StreamSkipped instead of crashing the whole sync. In plain terms, if the account token lacks permission for one stream, the system can skip that stream and explain why.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Square. It adds the required Square API version header so every request uses the expected version of Square’s API.

**Data flow**: It receives a base URL and a credential, asks the parent RestConnector to build the basic authenticated client, then adds the Square-Version header. It returns that ready-to-use client.

**Call relations**: This is part of the connector setup before any stream is read. Later methods, such as paginate and its helper methods, use this client to make Square API requests.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading a Square stream. Given a stream name and an optional cursor bookmark, it chooses the correct Square API path and yields records in pages.

**Data flow**: It receives an authenticated HTTP client, a StreamSpec describing which Square data to read, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and yields each non-empty batch of records. If Square denies access with a 401 or 403 response, it turns that into StreamSkipped so the stream can be skipped with a clear reason.

**Call relations**: The sync system calls paginate when it wants records for a Square stream. paginate then delegates to _locations for locations, _cursor_get for customers, payments, and refunds, _catalog for catalog items and categories, _orders for orders, or directly extracts inventory counts with records_at. If the stream is unknown or unavailable, it raises StreamSkipped.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square endpoints that return ordinary cursor-based pages, such as customers, payments, and refunds. It also applies incremental filtering so the sync can avoid sending old records again.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor bookmark. For payments and refunds, it sends the cursor to Square as a begin_time filter. For other streams, it fetches pages and locally keeps only records whose cursor field is newer than the provided cursor. It yields each remaining non-empty page.

**Call relations**: paginate calls this when the stream is customers, payments, or refunds. This helper relies on the shared REST pagination behavior from the parent connector, then hands filtered record pages back to paginate.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square catalog objects, specifically items or categories, through Square’s catalog search endpoint. It follows Square’s continuation cursor until there are no more catalog pages.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor bookmark. It builds a search body for either ITEM or CATEGORY objects, posts it to Square, extracts the objects list, filters out older records when a cursor is present, yields non-empty pages, then repeats with Square’s returned cursor until Square stops giving one.

**Call relations**: paginate calls this for catalog_items and catalog_categories. Inside the loop, it uses records_at to pull the useful object list out of Square’s response before returning those records to paginate.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations matter both as their own stream and because order searches must be tied to one or more location IDs.

**Data flow**: It receives the HTTP client, sends a GET request to Square’s /locations endpoint, and extracts the locations list from the response. It returns that list of location records.

**Call relations**: paginate calls this directly when syncing the locations stream. _orders also calls it first so it knows which location IDs to include when searching for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders by first finding the account’s locations, then searching for orders across those locations. It supports incremental sync by asking Square for orders created after the provided cursor.

**Data flow**: It receives the HTTP client and an optional cursor bookmark. It fetches locations, keeps only valid string location IDs, and stops if there are none. Then it repeatedly posts an order search request containing those location IDs, an optional created_at start time, and any continuation cursor returned by Square. It extracts orders from each response, yields non-empty pages, and continues until Square provides no next cursor.

**Call relations**: paginate calls this for the orders stream. _orders calls _locations first because Square order searches need location IDs, then uses records_at to pull the order records from each search response before yielding them back to paginate.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).

# Finance, Billing, Banking, and Commerce Connectors  `stage-12.1.6`

This stage is part of the system’s behind-the-scenes syncing work. Its job is to connect to finance and commerce services, fetch business records from their web APIs, and reshape them into common “streams,” meaning steady lists of records the rest of the product can store, search, and revisit.

Each file is a connector for one outside service. Brex and Ramp read spend-management data such as card transactions, expenses, users, vendors, budgets, bills, and receipts. Chargebee, Recurly, and Stripe cover subscription and payment systems, pulling records like customers, invoices, subscriptions, payouts, and checkout sessions. Mercury reads banking data, especially accounts and transactions. QuickBooks and Xero read accounting records such as accounts, bills, payments, customers, vendors, and invoices, while handling each service’s own paging and update rules. Square reads commerce data, including customers, payments, orders, catalog items, locations, and inventory.

Together, these connectors act like adapters for different plug shapes: each provider has its own API style, but this stage turns them all into the same kind of syncable record flow.

## Files in this stage

### Spend Management
Readers for corporate card, expense, vendor, budget, transfer, and receipt data from spend-management platforms.

### `extensions/sources/ufo_ext_sources/providers/brex.py`

`io_transport` · `during source sync polling`

Brex exposes business spending data through web API endpoints, but those endpoints do not return everything at once. They return one page of results, plus a cursor, which is like a bookmark saying where to continue. This file defines a Brex connector that knows which Brex endpoint belongs to each kind of data and how to keep asking for the next page until there is no bookmark left.

The file also describes each Brex data stream with a StreamSpec, which is the system’s small instruction card for a stream: what it is called, what field identifies a record, and whether there is a date field that can act as a progress marker. Most Brex endpoints are treated as full reads because Brex does not provide a server-side “only changed since this time” filter for them. For transactions and expenses, the connector still records useful date fields as cursors, but it cannot ask Brex to pre-filter by those dates.

The main class, BrexConnector, plugs into the shared REST connector base. During a sync, the wider framework supplies an authenticated HTTP client, and this connector uses it to call Brex list endpoints. Each page’s items are yielded onward as plain record dictionaries. If a stream has no known Brex endpoint, the connector fails loudly instead of silently returning wrong or incomplete data.

#### Function details

##### `_stream`  (lines 39–50)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds the small description object used by the sync system for one Brex stream. It keeps stream setup consistent, so each Brex object type can be declared in a short, readable way.

**Data flow**: It receives a stream name and optional details such as the primary key field, cursor date field, and whether the stream is canonical. It passes those choices into a StreamSpec object, filling in standard Brex defaults along the way, and returns that StreamSpec for use in the connector’s stream list.

**Call relations**: At file load time, the Brex stream catalog is assembled by calling this helper for budgets, departments, expenses, transactions, transfers, users, and vendors. The helper hands the finished settings to StreamSpec.__init__, which creates the object the shared source framework later reads when deciding what streams exist and how records should be identified.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 71–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Brex stream from the Brex API, page by page. Someone would use it when the sync system needs all available records for a stream without having to know Brex’s cursor format itself.

**Data flow**: It starts with the requested stream and looks up the matching Brex list endpoint. It then repeatedly sends a GET request with a page size and, after the first page, the cursor returned by Brex. From each response it takes the items list, safely turns missing or invalid item data into an empty list through list_or_empty, yields any records it found, and stops when Brex no longer returns a next cursor. If the stream is unknown, it raises an error instead of guessing.

**Call relations**: The shared REST source framework calls this method as part of a sync for a specific stream. Inside the loop, this method delegates the actual HTTP request to the base connector’s GET helper and delegates response item cleanup to ufo.sdk.sources.list_or_empty. It then hands batches of Brex records back to the framework, which can continue the broader import process.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/ramp.py`

`io_transport` · `source sync runs`

Ramp is a company spending platform, and its API exposes many separate lists: transactions, transfers, cards, users, departments, vendors, receipts, and more. This file is the connector for those lists. Without it, the system would not know which Ramp web addresses to call, how to move through Ramp’s pages of results, or how to treat permission problems from a partly approved Ramp account.

The file first defines the Ramp streams the system can read. A stream is one kind of object, like “transactions” or “users,” with enough extra information to identify records and, when possible, sync only newer data. Ramp only supports time-based incremental syncing for transactions, so transactions use a cursor field called `user_transaction_time`. The other streams are read again each run, and the wider sync system skips duplicates.

The `RampConnector` then does the practical work. It starts at the right Ramp list endpoint, asks for up to 100 records at a time, yields each non-empty batch, and follows Ramp’s `page.next` link until there is no next page. Ramp returns that next link as a full web address, so the connector trims it down to the path and query that its base API client expects.

One important behavior is graceful skipping: if Ramp replies with 401 or 403, meaning “not allowed,” the connector reports that this stream was skipped instead of failing the whole run. Finally, transactions get a human-friendly title from `merchant_name`, because that is the name a person is most likely to recognize.

#### Function details

##### `_stream`  (lines 50–66)

```
def _stream(name: str, *, cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a `StreamSpec`, which is the small description the sync system needs for one Ramp object type. It avoids repeating the same setup for every Ramp list, while still allowing special fields like a cursor for transactions.

**Data flow**: It receives a stream name and optional field names, such as which field marks creation time or update time. It packages those choices with Ramp’s common primary key, `id`, into a `StreamSpec`. The result is a reusable stream definition that later tells the connector what to fetch and how to interpret each record.

**Call relations**: This helper is used while the file is loaded to build `RAMP_STREAMS`, the catalog of Ramp streams exposed by `RampConnector`. Its direct handoff is to `StreamSpec.__init__`, which creates the actual stream description object.

*Call graph*: 1 external calls (__init__).


##### `RampConnector._next_path`  (lines 97–104)

```
def _next_path(next_link: Any) -> str | None
```

**Purpose**: This function converts Ramp’s next-page link into the form this connector’s HTTP client can use. Ramp gives a full URL, but the client is already tied to Ramp’s base address, so it only needs the path and optional query string.

**Data flow**: It receives a possible next-page value from Ramp. If the value is not a useful string, or if it has no path, it returns nothing. Otherwise it parses the URL and returns just the path, plus the query text if there is one, such as `/developer/v1/transactions?page=...`.

**Call relations**: During pagination, `RampConnector.paginate` asks this function to translate `page.next` after each Ramp response. This function uses `urllib.parse.urlparse` to safely split the full URL into its parts, then gives `paginate` the next path to request.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RampConnector.paginate`  (lines 106–134)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function reads one Ramp stream page by page and yields batches of records. It is the main bridge between Ramp’s API format and the system’s internal sync loop.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from a previous sync. It chooses the correct Ramp endpoint, adds a page size, and for transactions adds date ordering and a `from_date` filter when a cursor exists. It repeatedly requests a page, pulls the `data` list out safely, yields any records found, reads the next-page link, and continues until there is no next page. If Ramp refuses access with 401 or 403, it turns that into a `StreamSkipped` signal; other HTTP errors still bubble up as real failures.

**Call relations**: The broader source sync system calls this when it is time to fetch records for a Ramp stream. Inside the loop it uses `list_or_empty` so missing or non-list data does not confuse the batching logic, and it calls `RampConnector._next_path` to follow Ramp’s pagination links. When a permission refusal happens, it creates `StreamSkipped` so the run can record that one stream was unavailable instead of treating the entire Ramp sync as broken.

*Call graph*: calls 2 internal fn (__init__, _next_path); 1 external calls (list_or_empty).


##### `RampConnector.render`  (lines 136–146)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function decides how a Ramp record should be shown when stored as recallable text. It gives transactions a clearer title by using the merchant name, which is usually what a person remembers.

**Data flow**: It receives one Ramp record and the stream it came from. If the stream is not transactions, or if there is no usable `merchant_name`, it falls back to the standard rendering supplied by the parent connector. For a transaction with a merchant name, it returns that merchant as the title and a text body containing a heading plus the full record encoded as sorted JSON.

**Call relations**: The sync system uses rendering after records are fetched, when it needs a readable title and body for storage or recall. This method only customizes transaction records; everything else is handed back to the parent `RestConnector` behavior. It uses `json.dumps` to turn the transaction dictionary into stable text.

*Call graph*: 1 external calls (dumps).


### Subscription Billing
Readers that page through subscription, invoice, customer, and recurring-revenue records from billing platforms.

### `extensions/sources/ufo_ext_sources/providers/chargebee.py`

`io_transport` · `active during source sync reads`

Chargebee is a billing service, and its API exposes many related things: customers, subscriptions, invoices, transactions, coupons, quotes, and more. This file is the read-only connector for that API. Without it, the system would not know which Chargebee endpoints exist, how to authenticate, how to move through pages of results, or how to pull child records that live under a parent record.

The file first defines the list of supported streams. A stream is one kind of data the sync can read, such as `customer` or `invoice`. Each stream also records useful bookkeeping fields, like its primary key and the time field used for incremental syncs. An incremental sync means “only ask for records newer than the last one we saw.”

The `ChargebeeConnector` then provides the actual API behavior. It creates an HTTP client using Chargebee’s Basic authentication style, where the API key is sent as the username. It knows Chargebee’s standard paging pattern: ask for up to 100 records, read the `next_offset` token, and keep asking until there is no next token. It also flattens Chargebee’s response shape, because Chargebee wraps each record inside a named envelope like `{customer: {...}}`.

A few streams are special child streams. For example, contacts are found by first listing customers, then asking for each customer’s contacts. The connector stamps the parent ID onto each child record so the relationship is not lost.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one kind of Chargebee data. It keeps the long stream list readable by filling in common defaults, such as using `id` as the primary key and `updated_at` as the usual cursor field.

**Data flow**: It receives a stream name plus optional details like the source object name, key field, and time fields. It combines those values with sensible defaults and returns a `StreamSpec`, which is the system’s compact description of how that stream should be treated.

**Call relations**: This is used while the file is loaded to build `CHARGEBEE_STREAMS`. It hands those stream descriptions to the connector class, which later uses them when deciding what to read and how to track progress.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Chargebee. It applies timeouts, standard headers, and authentication so later code can focus on asking for data rather than setting up every request.

**Data flow**: It receives a base URL and a resolved credential. It trims the base URL, prepares JSON/form headers, sets connection and read time limits, then either uses a supplied transport or builds Basic authentication from the API key. It returns an `httpx.AsyncClient`, which is an asynchronous web client that can make API calls without blocking the whole program.

**Call relations**: The broader connector framework calls this when a Chargebee sync starts. The client it returns is then passed into pagination methods such as `ChargebeeConnector.paginate`, which use it to fetch pages from Chargebee.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This makes Chargebee records easier for the rest of the system to read. Chargebee wraps each record inside a resource-named envelope, and this function lifts the actual record contents to the top level.

**Data flow**: It receives one raw record and the stream description. If the record contains a dictionary under the stream’s source object name, it copies that inner dictionary and adds any outer fields that are not already present. If the expected envelope is not there, it returns the record unchanged.

**Call relations**: The source framework uses this after records are fetched. It is especially important for child streams, because pagination may stamp a parent ID on the outside of the envelope, and `flatten` preserves that ID while exposing the child record itself.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Chargebee stream. Given a stream name, it chooses the right paging strategy and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value from a previous run. It checks whether the stream is one of the special child streams or a normal list endpoint, then delegates to the matching pagination method. It outputs pages of raw records. If Chargebee responds with 401 or 403, meaning unauthorized or forbidden, it turns that into a `StreamSkipped` message instead of crashing the whole sync.

**Call relations**: The sync runtime calls this when it wants records for a particular stream. `paginate` then hands off to `ChargebeeConnector._paginate_list` for ordinary streams, or to one of the child-stream paginators for attached items, contacts, quote line groups, and scheduled subscription changes.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, adds the incremental filter that asks Chargebee for records after the last saved cursor.

**Data flow**: It receives a stream description and an optional cursor value. It always starts with `limit: 100`. If there is both a cursor value and a cursor field for the stream, it adds a parameter like `updated_at[after]`, using Chargebee’s “strictly after this value” filter. It returns the completed parameter dictionary.

**Call relations**: `ChargebeeConnector._paginate_list` calls this just before starting its page loop. The parameters it returns control how much data Chargebee sends back and whether the read starts from the beginning or from the previous checkpoint.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one ordinary Chargebee list endpoint, such as customers or invoices. It follows Chargebee’s `next_offset` paging token until all available pages have been read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for the stream, builds request parameters, then asks the shared REST paging helper to repeatedly fetch the `list` records and follow `next_offset`. It yields each page of records as it arrives.

**Call relations**: `ChargebeeConnector.paginate` uses this for normal streams. The special child-stream methods also use it first to list parent records, such as items, customers, quotes, or subscriptions, before fetching children underneath each parent.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads attached items, which are child records found under individual Chargebee items. It first finds the parent items, then visits each item’s attached-items endpoint.

**Data flow**: It receives an HTTP client and optional cursor. It lists item pages, extracts each item ID, skips any parent without an ID, then requests `/items/{item_id}/attached_items`. For every child page it yields, the child records are marked with the parent `item_id` so the link back to the item is preserved.

**Call relations**: `ChargebeeConnector.paginate` calls this when the requested stream is `attached_item`. This function relies on `ChargebeeConnector._paginate_list` to find parent items and on `ChargebeeConnector._paginate_substream` to read the paged child records.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads customer contacts, which Chargebee exposes underneath each customer rather than as one flat global list. It walks through customers and then fetches the contacts for each one.

**Data flow**: It receives an HTTP client and optional cursor. It lists customers, pulls out each customer ID, skips records where no ID can be found, and then asks Chargebee for `/customers/{customer_id}/contacts`. The returned contact records are stamped with `customer_id` before being yielded.

**Call relations**: `ChargebeeConnector.paginate` chooses this function for the `contact` stream. It uses `ChargebeeConnector._paginate_list` for the parent customer scan and `ChargebeeConnector._paginate_substream` for the contact pages below each customer.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads quote line groups, which are child records attached to individual quotes. It keeps the quote-to-line-group relationship by adding the quote ID to each child record.

**Data flow**: It receives an HTTP client and optional cursor. It lists quotes, extracts each quote ID, skips parents without an ID, then reads `/quotes/{quote_id}/quote_line_groups`. Each returned child record is enriched with `quote_id` and yielded in pages.

**Call relations**: `ChargebeeConnector.paginate` calls this for the `quote_line_group` stream. It gets parent quotes through `ChargebeeConnector._paginate_list` and uses `ChargebeeConnector._paginate_substream` to do the repeated child-page fetching.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads subscription details that include scheduled future changes. Chargebee exposes this as a separate one-subscription-at-a-time endpoint, so the connector has to visit it for each subscription.

**Data flow**: It receives an HTTP client and optional cursor. It lists subscriptions, extracts each subscription ID, skips parents without an ID, then fetches `/subscriptions/{id}/retrieve_with_scheduled_changes`. If the response contains a subscription object, it yields a one-record page containing that subscription plus the `subscription_id` stamp.

**Call relations**: `ChargebeeConnector.paginate` calls this for the `subscription_with_scheduled_changes` stream. It uses `ChargebeeConnector._paginate_list` to discover the subscriptions, then directly fetches each detailed scheduled-change record.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared child-stream page reader. It is used when Chargebee returns child records under a parent endpoint and those child records need to remember which parent they came from.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent ID field to add, and the parent ID value. It fetches pages using Chargebee’s usual `list` and `next_offset` pattern. For each dictionary record, it copies the record, adds the parent ID field, and yields non-empty pages of these enriched records.

**Call relations**: The attached-item, contact, and quote-line-group paginators call this after they have found a parent ID. It centralizes the repeated child-page behavior so each parent-specific method only has to decide which endpoint to visit and what parent ID name to stamp.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/providers/recurly.py`

`io_transport` · `during source sync`

Recurly returns lists of records in pages, like a long receipt split across several sheets. This file knows how to keep asking for the next sheet until there are no more. It defines the Recurly streams the product can read, such as accounts, subscriptions, invoices, plans, coupons, notes, and billing information.

The main class, RecurlyConnector, is a read-only connector. It builds an HTTP client with Recurly’s required API version header and uses HTTP Basic authentication, where the API key is used as the username. It can also use a broker-provided transport when secrets are proxied instead of held directly.

Most streams are simple top-level Recurly endpoints, such as /accounts or /invoices. Some are child resources that only exist under a parent, such as account notes under a specific account. For those, the connector first walks through the parent list, then asks for each parent’s child records, and adds the parent id to each child row so the relationship is not lost.

For incremental syncing, it can start from a saved cursor time by sending Recurly a begin_time parameter. If Recurly refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of failing the whole connector unexpectedly.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a small description of one Recurly stream, such as its name, its Recurly API object, its primary key, and the field used for incremental syncing. This keeps the stream list readable and consistent.

**Data flow**: It receives a stream name and optional details like the source object name, primary key, cursor field, and whether it is canonical. It fills in sensible defaults when details are missing, then returns a StreamSpec object that the connector later uses as instructions for syncing that stream.

**Call relations**: This helper is used while the file is being loaded to build RECURLY_STREAMS. It hands its information into StreamSpec.__init__, which creates the stream definition used later by RecurlyConnector.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Recurly. It makes sure every request has the right timeout, API version header, content type, and authentication style.

**Data flow**: It receives a base URL and a credential. It trims the base URL, prepares headers Recurly expects, and chooses how to authenticate: either through a provided proxy transport or by using the API key as HTTP Basic authentication. It returns an httpx.AsyncClient ready to make Recurly requests, or raises an error if no usable credential is present.

**Call relations**: The wider RestConnector flow calls this when it needs a network client for Recurly. Inside, it relies on httpx.Timeout, httpx.BasicAuth, and httpx.AsyncClient to assemble the actual client object.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: Turns Recurly’s “next page” link into a path the client can request next. This matters because Recurly may return either a full URL or just a path.

**Data flow**: It receives a next-page link, or nothing. If there is no link, it returns nothing. If the link is a full URL, it strips it down to just the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: Pagination helpers call this after each page to decide where to go next. It uses urllib.parse.urlparse to understand whether the link is a full URL, then passes the cleaned path back to _paginate_top_level, _paginate_per_parent, _account_ids, or _coupon_ids.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for the first request of a stream. These parameters tell Recurly how many records to return, how to sort them, and where to start for an incremental sync.

**Data flow**: It receives a StreamSpec and an optional saved cursor value. It creates a parameter dictionary with the page size, ascending order, and the stream’s cursor field if one exists. If both a cursor field and cursor value are present, it adds begin_time so Recurly starts from that point in time. It returns the parameter dictionary.

**Call relations**: _paginate_top_level and _paginate_per_parent call this before their first request. After that first request, Recurly’s own next link carries the paging position, so the helpers stop re-sending these initial parameters.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a requested Recurly stream and yields pages of records. It is the main read path for this connector.

**Data flow**: It receives an HTTP client, a stream definition, and an optional cursor. If the stream is a child resource, it delegates to the per-parent paginator. If the stream is the special coupon parent stream, it reads coupons and only yields bulk coupons. Otherwise, it reads a normal top-level endpoint. It yields lists of record dictionaries. If Recurly rejects access with 401 or 403, it turns that into a StreamSkipped error with a helpful explanation.

**Call relations**: The source syncing framework calls paginate when it wants records for a stream. paginate then hands the work to _paginate_per_parent or _paginate_top_level depending on the stream shape, and wraps permission failures in StreamSkipped so the larger sync can understand that this stream was not available.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads a normal Recurly list endpoint one page at a time. This is used for resources that live directly at an endpoint, such as accounts, invoices, or plans.

**Data flow**: It receives a client, a stream definition, an endpoint path, and an optional cursor. It builds the first query, requests the current page, yields any records found under Recurly’s data field, and follows Recurly’s next link while has_more is true. It stops when Recurly says there are no more pages.

**Call relations**: paginate calls this for ordinary streams and for the coupon parent stream. It calls _initial_query to prepare the first request and _next_path to clean Recurly’s next-page link before the next loop.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Walks through all Recurly accounts and yields only their ids. These ids are needed before the connector can fetch account-specific child records, such as notes or billing information.

**Data flow**: It starts at the /accounts endpoint with page size and sort parameters. For each page, it looks at the returned rows, keeps rows that are dictionaries with an id, and yields each id as text. It follows next links until Recurly reports there are no more account pages.

**Call relations**: _paginate_per_parent calls this when it needs parent account ids. As it moves through account pages, it calls _next_path to convert Recurly’s next link into the next request path.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: Walks through Recurly coupons and yields ids only for bulk coupons. This is needed because unique coupon codes are fetched under their bulk coupon parent.

**Data flow**: It starts at the /coupons endpoint with page size and sort parameters. For each returned row, it checks that the row is a dictionary, has an id, and has coupon_type equal to bulk. It yields those coupon ids as text and follows Recurly’s next links until the coupon list is finished.

**Call relations**: _paginate_per_parent calls this when the parent path is /coupons. Like the account id reader, it uses _next_path to follow Recurly’s paging links.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child resources that sit underneath each parent record. For example, it can fetch notes for every account, or unique coupon codes for every bulk coupon.

**Data flow**: It receives a client, stream definition, parent endpoint, child endpoint name, the field used to store the parent id, and an optional cursor. It first chooses the right parent id source: account ids or bulk coupon ids. For each parent id, it requests the child endpoint, yields each page of child records, and adds the parent id onto each child row when possible. It follows child next links until that parent’s children are finished, then moves to the next parent.

**Call relations**: paginate calls this for streams listed as per-parent streams. This helper calls _account_ids or _coupon_ids to discover parents, calls _initial_query to prepare each child listing, and calls _next_path to keep following Recurly’s child-page links.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/stripe.py`

`io_transport` · `during source sync when Stripe streams are read`

Stripe exposes business data through many web API endpoints, and each endpoint has its own path and sometimes its own relationship to other data. This file is the Stripe source connector: it knows which Stripe resources are available, how to ask Stripe for them, how to walk through pages of results, and how to follow parent-child links such as customers to their payment methods or subscriptions to their subscription items.

The file first defines a list of streams. A stream is a named category of records, like "customers" or "charges". Some streams are simple lists. Others are substreams, meaning Stripe only provides them by first looking up a parent record. For example, invoice line items are fetched by listing invoices first, then asking for the lines for each invoice. The connector also adds helpful parent IDs to child rows, like stamping a payment method with the customer it came from.

Stripe returns data in pages, like a book that must be read one page at a time. The connector repeatedly requests pages until Stripe says there are no more. If a cursor is available, it can ask Stripe only for records created after a certain time. If Stripe refuses access because the key is missing permission, the stream is skipped rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 89–107)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This helper creates a stream description for one Stripe resource. It keeps the long stream list readable by filling in common defaults, such as using "id" as the main identifier and "created" as the usual time cursor.

**Data flow**: It receives a stream name and optional details such as the Stripe API object name, primary key, cursor field, and whether the stream is considered canonical. It combines those details with sensible defaults and returns a StreamSpec, which is the system’s compact description of how that stream should be treated.

**Call relations**: The file uses this helper while building STRIPE_STREAMS. Each call produces one entry that the StripeConnector later consults when it decides which endpoint to call and how to interpret the records.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 182–185)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the web client used to talk to Stripe. Its important Stripe-specific job is to pin the Stripe API version so responses stay predictable over time.

**Data flow**: It receives the base Stripe URL and a credential object. It asks the parent RestConnector to build the normal authenticated HTTP client, then adds the Stripe-Version header before returning that client.

**Call relations**: This fits into the connector setup before any stream is read. The inherited connector machinery creates the client, and this method adds Stripe’s required version setting so later paging methods all make requests in the same expected API shape.


##### `StripeConnector._list_path`  (lines 188–189)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This converts a stream description into the standard Stripe list endpoint path. For a stream whose source object is "customers", it produces the path for listing customers.

**Data flow**: It receives a StreamSpec and reads its source_object field. It then formats that value into a Stripe API path under /v1 and returns the path string.

**Call relations**: The main pagination code and the special parent-child pagination helpers call this whenever they need the basic list endpoint for a stream. It is the small shared rule that keeps endpoint construction consistent.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 192–204)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This turns a saved cursor value into the Unix timestamp format Stripe expects for created-time filters. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be empty, already numeric, or written as an ISO date-time string. It returns an integer timestamp when it can understand the value, or None when there is no usable cursor.

**Call relations**: The page-reading loop calls this before making requests. If it gets a usable timestamp and the stream uses Stripe’s created field as its cursor, the loop asks Stripe for only newer records.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 206–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading records from one Stripe stream. It chooses the right reading strategy: a plain list, a child list under each parent, a query-based child list, or external accounts under connected accounts.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks what kind of Stripe stream is being requested, delegates to the matching helper, and yields pages of records. If Stripe answers with 401 or 403, meaning unauthorized or forbidden, it raises StreamSkipped with a clear explanation instead of treating it as an unexpected failure.

**Call relations**: The wider source sync process calls this when it wants pages for a particular Stripe stream. This method then routes the work to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, or the general _page_loop, depending on the stream’s shape.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 234–265)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Stripe list endpoint from beginning to end, one page at a time. It is the reusable engine behind nearly every stream in this connector.

**Data flow**: It receives a client, endpoint path, stream description, optional cursor, and optional extra query parameters. It builds request parameters such as the page size, the next-page marker, created-time filter, and stream-specific filters. It calls Stripe, normalizes each returned record, yields non-empty pages, and continues until Stripe says there are no more pages or the next page cannot be safely identified.

**Call relations**: paginate uses this for normal streams, and the substream helpers use it for both parent and child collections. Before requesting data it uses _cursor_to_unix to prepare incremental filters, and after receiving data it uses _browse_record to make timestamps easier for the rest of the system to consume.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 268–288)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This lightly normalizes one Stripe record so time fields are easier to use. Stripe often returns times as raw numbers, and this function adds or converts readable ISO date-time strings where appropriate.

**Data flow**: It receives one record dictionary and the stream description. It copies the record, converts numeric created_at or updated_at values to UTC ISO strings, fills created_at from Stripe’s created field when needed, and fills updated_at from the stream cursor when that cursor is numeric. It returns the normalized copy without changing the original input record.

**Call relations**: _page_loop calls this for every record it receives from Stripe. That means all stream readers benefit from the same timestamp cleanup before pages are yielded to the rest of the sync system.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 290–312)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Stripe child resources that live under a parent resource path. For example, it can list customers first and then fetch each customer’s payment methods.

**Data flow**: It receives a client and a child stream description. It looks up the parent stream, walks through parent pages, pulls each parent ID, builds the child endpoint path, reads child pages, and adds parent information such as customer_id or invoice_id to each child row when configured. It yields child pages enriched with that parent context.

**Call relations**: paginate calls this for streams listed as child-path substreams. It relies on _stream_spec to find the parent stream, _parent_pages to enumerate parents, and _page_loop to read each child collection.

*Call graph*: calls 3 internal fn (_page_loop, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 314–319)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This provides pages of parent records for a substream. It hides whether the parent itself is a simple Stripe list or another query-based substream.

**Data flow**: It receives a client and a parent stream description. If the parent is query-based, it returns that specialized pagination flow; otherwise it returns the general page loop for the parent’s list endpoint.

**Call relations**: _paginate_substream calls this when it needs parent records before fetching children. This is especially important for nested cases, where a child stream may depend on a parent stream that is not a simple top-level list.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 321–335)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child resources that Stripe exposes through a query parameter rather than through a nested URL path. For example, it can fetch subscription items by asking for items where subscription equals a specific subscription ID.

**Data flow**: It receives a client and a stream description. It looks up the parent stream and child endpoint, reads each parent page, takes each parent ID, calls the child endpoint with that ID as a query filter, and stamps the parent ID onto each child row. It yields the resulting child pages.

**Call relations**: paginate calls this directly for query-based substreams. _parent_pages can also call it when a deeper nested stream needs this query-based stream as its parent, such as walking subscriptions to subscription items before fetching usage summaries.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 337–350)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts and cards attached to Stripe accounts. These are special because Stripe groups both kinds under an account’s external_accounts endpoint and filters them by object type.

**Data flow**: It receives a client and the requested external-account stream. It lists Stripe accounts, takes each account ID, reads that account’s external accounts endpoint, and adds account_id to every returned child row. The stream’s configured extra parameters decide whether Stripe returns bank accounts or cards.

**Call relations**: paginate calls this for the two external account streams. It uses _stream_spec to get the accounts stream, _list_path to list accounts, and _page_loop to read both the account list and each account’s external account pages.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 352–353)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the stream description with a given name. It is a small lookup helper used when one stream needs to refer to another stream, such as a child stream needing its parent.

**Data flow**: It receives a stream name, searches the STRIPE_STREAMS list, and returns the matching StreamSpec. If no matching stream exists, the normal Python next lookup would fail.

**Call relations**: The substream pagination methods call this when they need to move from a child stream name to its parent stream description. That returned description then guides endpoint creation and page reading.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Banking and Accounting
Readers for bank accounts, transactions, accounting ledgers, tenants, invoices, bills, customers, vendors, and payments.

### `extensions/sources/ufo_ext_sources/providers/mercury.py`

`io_transport` · `source sync polling`

This connector is the bridge between Mercury, the banking service, and the source-sync system. Without it, the system would not know which Mercury API endpoints to call, how to walk through paged transaction results, or how to resume a later sync without rereading everything unnecessarily.

It defines two streams of data: accounts and transactions. Accounts are simple: one API call returns the list. Transactions are more like reading a long receipt roll account by account. The connector first asks Mercury for all accounts, then for each account it requests transactions in pages of 100 until Mercury returns a shorter page, which means there is no more to read.

For incremental syncing, transactions use Mercury's posted date as a watermark. Mercury's API accepts only a date, not an exact position, so the connector rereads the whole day where the last sync stopped. Any duplicates are expected to be removed later by the wider system. Pending transactions do not move the watermark because they do not yet have a posted date.

If Mercury rejects the request because the API key is missing or lacks access, the connector marks the stream as skipped instead of crashing the whole sync. For display, transaction pages are titled by their counterparty name, since that is the most human-friendly label Mercury provides.

#### Function details

##### `MercuryConnector.paginate`  (lines 62–82)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Mercury streams. It decides whether the system is asking for accounts or transactions, fetches the right data, and yields it in batches for the sync engine to consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor from a previous sync. For accounts, it fetches the account list and yields it once if anything came back. For transactions, it fetches accounts first, extracts usable account IDs, then reads each account's transaction pages and adds the account ID as extra context to each transaction. If Mercury returns a 401 or 403 refusal, it turns that into a clear skipped-stream message; other HTTP errors continue upward.

**Call relations**: During a sync, the source framework calls this method to get batches of Mercury records. It calls MercuryConnector._accounts to discover accounts, MercuryConnector._account_ids to keep only valid account IDs, MercuryConnector._transactions to page through each account's transactions, and with_context to attach the account ID before handing records back to the wider sync flow. If access is refused, it creates a StreamSkipped error so the framework can treat that stream as unavailable rather than silently wrong.

*Call graph*: calls 4 internal fn (__init__, _account_ids, _accounts, _transactions); 1 external calls (with_context).


##### `MercuryConnector._accounts`  (lines 84–86)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches the Mercury account list. It keeps the account-fetching detail in one place so the main pagination method can stay focused on the overall sync flow.

**Data flow**: It receives an HTTP client, sends a GET request to Mercury's accounts endpoint, reads the returned JSON-like data, and pulls out the accounts field. It passes that field through list_or_empty, so missing or non-list data becomes a safe empty list instead of surprising the caller.

**Call relations**: MercuryConnector.paginate calls this when syncing the accounts stream and also before syncing transactions, because transactions must be requested account by account. After this helper returns accounts, paginate either yields them directly or sends them to MercuryConnector._account_ids to choose which accounts to use for transaction requests.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector._account_ids`  (lines 89–94)

```
def _account_ids(accounts: list[dict[str, Any]]) -> list[str]
```

**Purpose**: This helper extracts valid account IDs from account records. It protects the transaction sync from trying to call Mercury with missing, empty, or non-text IDs.

**Data flow**: It receives a list of account dictionaries. It looks at each account's id field and keeps only IDs that are real non-empty strings. It returns a clean list of account ID strings that can safely be placed into transaction API URLs.

**Call relations**: MercuryConnector.paginate uses this after fetching accounts and before reading transactions. Its output becomes the set of account IDs passed one by one into MercuryConnector._transactions, so it acts like a small filter between account discovery and transaction paging.

*Call graph*: called by 1 (paginate).


##### `MercuryConnector._transactions`  (lines 96–115)

```
async def _transactions(self, client: httpx.AsyncClient, account_id: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads all transaction pages for one Mercury account. It also applies the saved cursor date so later syncs can start near where the previous sync stopped.

**Data flow**: It receives an HTTP client, one account ID, and an optional cursor. It repeatedly asks Mercury for up to 100 transactions, increasing the offset each time to move to the next page. If a cursor exists, it sends the first 10 characters as the start date, because Mercury filters by day. It yields each non-empty batch of transactions and stops when the returned batch is smaller than 100, which signals the end.

**Call relations**: MercuryConnector.paginate calls this once for each valid account ID while syncing the transactions stream. This helper calls list_or_empty to normalize Mercury's transactions field before yielding pages back to paginate, which then adds the account ID context and passes the records onward.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector.render`  (lines 117–126)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method chooses the human-readable title and body for a synced Mercury record. For transactions, it makes the counterparty name the title, which is more useful than a raw ID or generic label.

**Data flow**: It receives one record and its stream description. If the record is a transaction and has a usable counterpartyName, it returns that name as the title and a text body containing sorted JSON for the full record. Otherwise, it falls back to the standard rendering behavior inherited from the base REST connector.

**Call relations**: The wider source system uses this when turning fetched records into recallable pages. For Mercury transactions, this method calls json.dumps to format the complete record as stable JSON text, while still highlighting the counterparty as the page title. For accounts and transactions without a counterparty name, it hands the work back to the parent connector's default renderer.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/quickbooks.py`

`io_transport` · `request handling`

QuickBooks Online does not offer a simple “list all invoices” or “list all customers” endpoint for each type of record. Instead, every read is done by sending a SQL-like query to one shared `/query` endpoint. This file hides that awkwardness from the rest of the project. It defines the QuickBooks streams the system knows about, gives each one its QuickBooks entity name, primary key, and update-time field, and then builds the right query for each page of data.

The main class, `QuickBooksConnector`, is a read-only connector. It asks QuickBooks for records in pages of 100. If the sync already has a saved cursor, meaning “the last update time we successfully saw,” it adds a filter so QuickBooks only returns newer records. This is like asking a filing clerk, “Give me invoices updated after Tuesday, 100 at a time.”

QuickBooks wraps returned rows inside `QueryResponse`, so the connector unwraps that envelope before yielding records. Some QuickBooks reference lists do not support the update cursor, so they are always read by full refresh. If QuickBooks refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole run as an unknown failure. Finally, because QuickBooks stores update time inside nested data, `flatten` copies that nested value to a flat key so the shared sync machinery can track progress.

#### Function details

##### `_stream`  (lines 32–47)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one QuickBooks stream, such as invoices or vendors. It saves repeated setup by filling in shared QuickBooks rules, like using `Id` as the primary key and `MetaData.LastUpdatedTime` as the usual update marker.

**Data flow**: It receives a friendly stream name, the matching QuickBooks object name, and optional choices such as whether the stream is canonical or whether it has a cursor. It packages those details into a `StreamSpec`, which is the project’s small description object for “what to read and how to identify changes.” The result is used in the file’s stream list.

**Call relations**: This helper is used while the module is being loaded to build `QUICKBOOKS_STREAMS`. Each call hands its settings to `StreamSpec.__init__`, so the connector later has a complete menu of QuickBooks entities it can read.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 88–95)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This function writes the QuickBooks query string for one page of one stream. It is responsible for asking for the right entity, optionally filtering to only records newer than the saved cursor, and choosing the page position.

**Data flow**: It receives a stream description, an optional cursor value, and the page’s starting position. It builds text beginning with `SELECT * FROM <entity>`. If a cursor is available and the stream supports one, it safely escapes single quotes in that cursor, adds a `WHERE` clause for newer records, and orders by the same update field. It then adds QuickBooks paging instructions and returns the final query string.

**Call relations**: `paginate` calls this each time it needs the next page from QuickBooks. `_build_query` does not send the request itself; it only prepares the exact question that `paginate` will pass to the QuickBooks `/query` endpoint.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 97–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This asynchronous function reads one QuickBooks stream page by page. It turns QuickBooks’ paged query responses into batches of records that the wider sync system can consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. Starting at position 1, it builds a query, sends it to `/query`, unwraps the returned `QueryResponse`, and pulls out the records for the requested QuickBooks entity. Non-empty pages are yielded to the caller. If a page contains fewer than 100 records, it knows there are no more pages and stops. If QuickBooks replies with 401 or 403, it raises `StreamSkipped` with a clear explanation; other HTTP errors are passed upward unchanged.

**Call relations**: The shared source-sync runner calls `paginate` when it wants records for a QuickBooks stream. Inside the loop, `paginate` relies on `_build_query` to form each QuickBooks query. When access is refused, it creates a `StreamSkipped` error so the larger sync can treat that stream as unavailable because of permissions or credentials rather than as a mysterious crash.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 121–124)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function makes QuickBooks records easier for the shared sync code to track. In particular, it copies a nested cursor value such as `MetaData.LastUpdatedTime` onto the top level of the record.

**Data flow**: It receives one record and the stream description. If the stream’s cursor field uses a dotted path, meaning the value lives inside nested data, it reads that nested value with `get_path` and returns a new record that includes the same dotted cursor name as a flat key. If no such nested cursor is needed, it returns the original record unchanged.

**Call relations**: After records are fetched, the connector’s normal processing path can call `flatten` before the shared sync machinery updates its saved cursor. `flatten` delegates the nested lookup to `get_path`, which knows how to follow a path like `MetaData.LastUpdatedTime` through the record.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/xero.py`

`io_transport` · `active during source sync runs, when fetching Xero records`

Xero is an accounting service, and its API has a few rules that this connector must obey. Every accounting request needs a tenant header, which tells Xero which organization’s books to read. If the connector was not given a tenant up front, it asks Xero’s connections endpoint which organizations the current grant can access. If there is exactly one, it uses that. If there are none or several, it stops with a clear fault, because silently choosing the wrong company would copy the wrong books into the workspace.

The file also defines the list of Xero resources that can be synced, such as accounts, contacts, invoices, payments, journals, and tax rates. Xero returns each resource inside a named wrapper, like `{"Invoices": [...]}`, so the connector knows which wrapper to open for each stream. Some Xero resources support pages of 100 records; others come back all at once. The connector uses the right style for each one.

For incremental sync, Xero does not use a normal query parameter. Instead, it expects an `If-Modified-Since` request header in a web date format. This file converts stored cursor values into that format. Finally, because Xero records use different ID field names, such as `InvoiceID` or `AccountID`, the connector copies the right one into a common `id` field so downstream code can treat all streams consistently.

#### Function details

##### `_stream`  (lines 75–89)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Xero data stream, such as invoices or contacts. It keeps the stream list compact and consistent by filling in shared defaults like the primary key and update timestamp field.

**Data flow**: It receives a friendly stream name, the Xero response wrapper name, and optional choices such as whether the stream has a cursor or is considered canonical. It packages those details into a `StreamSpec`, which is the platform’s small instruction card for how to sync that resource.

**Call relations**: This helper is used while building the module-level `XERO_STREAMS` list. It hands each completed stream description to the connector class through `streams_list`, so later sync code knows what Xero resources are available.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 117–134)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved sync cursor into the date format Xero expects for incremental reads. In plain terms, it turns “last time we synced” into the exact kind of timestamp Xero wants in an HTTP header.

**Data flow**: It receives a cursor value as text or nothing. Empty input becomes no date. A number is treated like a Unix timestamp, meaning seconds since 1970. An ISO-style date string is parsed and converted to UTC. If parsing fails, the original text is passed through. The output is either a web-style date such as `Tue, 05 Mar 2024 12:00:00 GMT` or `None` if no safe date can be made.

**Call relations**: The `XeroConnector.paginate` method calls this before making Xero requests for streams that support incremental syncing. Its output becomes the `If-Modified-Since` header, which tells Xero to return records changed after that time.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 142–143)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This stores an optional Xero tenant ID for the connector run. A tenant ID identifies the specific Xero organization whose accounting data should be read.

**Data flow**: It receives either a tenant ID string or `None`. It saves that value on the connector instance. Nothing is returned, but later client setup and tenant checks use the saved value.

**Call relations**: This runs when a `XeroConnector` is created. If a tenant was supplied, `XeroConnector._make_client` will place it directly into request headers; otherwise `XeroConnector._ensure_tenant` will discover the tenant during pagination.


##### `XeroConnector._make_client`  (lines 145–149)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Xero and, when possible, preloads it with the required tenant header. The HTTP client is the object that actually sends web requests.

**Data flow**: It receives a base URL and a credential object supplied by the platform’s authentication layer. It asks the parent REST connector to build the client with the normal authentication setup. If this connector already has a tenant ID, it adds `xero-tenant-id` to the client headers. It returns the prepared client.

**Call relations**: This fits into the setup phase for a sync run. It builds on the parent connector’s client creation and prepares the client that `XeroConnector.paginate` later uses to fetch pages from Xero.


##### `XeroConnector._ensure_tenant`  (lines 151–179)

```
async def _ensure_tenant(self, client: httpx.AsyncClient) -> None
```

**Purpose**: This makes sure every Xero Accounting API request knows which organization to read. It protects against a dangerous mistake: syncing one company’s books when the grant actually covers multiple companies.

**Data flow**: It receives the HTTP client for the current run. If the client already has a `xero-tenant-id` header, it does nothing. Otherwise it calls Xero’s connections endpoint, reads the returned connection list, keeps only organization tenants, and sorts their tenant IDs. If there is exactly one tenant, it writes that ID into the client headers. If there are none or more than one, it raises a `StreamFault`, meaning the sync cannot safely continue.

**Call relations**: Before fetching any stream, `XeroConnector.paginate` calls this function. It may use `list_or_empty` to safely interpret Xero’s response list, and it raises `StreamFault` when the grant is unusable or ambiguous. Once it succeeds, the same client carries the tenant header for the rest of that run.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `XeroConnector.paginate`  (lines 181–217)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Xero stream data. It asks Xero for records, page by page when needed, and yields batches of records for the rest of the sync pipeline to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the last sync. It builds the Xero path from the stream’s source object, adds an `If-Modified-Since` header when incremental sync is possible, and first ensures the client has the right tenant. For non-paged streams it makes one request and yields the returned records if any exist. For paged streams it requests `page=1`, then `page=2`, and so on, yielding each batch until Xero returns no records or a short page under 100 records. If Xero returns 401 or 403, it turns that into `StreamSkipped`, meaning this stream is unavailable because of missing permission or invalid access.

**Call relations**: This method is called by the broader source-sync framework when it needs records for a Xero stream. It relies on `_ensure_tenant` before network access, uses `_cursor_to_rfc1123` to format incremental-sync dates, and then sends GET requests through `httpx.AsyncClient.get`. It hands record batches back to the framework as an asynchronous iterator, so the system can process data as it arrives instead of waiting for everything at once.

*Call graph*: calls 3 internal fn (__init__, _ensure_tenant, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 219–228)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes Xero records so every synced item has a common `id` field. That matters because Xero names IDs differently for different resources, such as `AccountID`, `InvoiceID`, or `TaxType`.

**Data flow**: It receives one record and the stream it came from. If the record already has `id`, it returns the record unchanged. Otherwise it looks up the correct Xero ID field for that stream. If the field exists and has a value, it returns a copy of the record with `id` added as a string. If no matching ID field or value exists, it leaves the record unchanged.

**Call relations**: This is used after records are fetched to put them into the platform’s expected shape. It complements `XeroConnector.paginate`: pagination gets the raw records from Xero, while `flatten` makes each record easier for downstream sync code to key, store, and compare.


### Commerce Operations
Readers for point-of-sale and commerce records such as customers, payments, catalog items, orders, locations, and inventory.

### `extensions/sources/ufo_ext_sources/providers/square.py`

`io_transport` · `source sync request handling`

Square exposes different kinds of data through different web API patterns. Some lists are fetched with a simple GET request and a cursor, some require POST search requests, and orders must first be tied to the seller’s locations. This file hides those differences behind one connector class, `SquareConnector`, so the rest of the project can ask for a stream like “payments” or “orders” without knowing Square’s details.

The file defines the available Square streams and how each one should be identified, such as its primary key and the field used for incremental syncing. Incremental syncing means asking only for records newer than a saved point in time, instead of downloading everything again.

When the connector opens an HTTP client, it adds Square’s pinned API version header. That is like telling Square, “speak the version of your language this connector understands.” The main `paginate` method then chooses the right fetching strategy for each stream. It yields batches of records, not one giant result, so large accounts can be synced piece by piece.

If Square refuses access with an authorization error, the file raises `StreamSkipped` instead of crashing the whole run. That matters because one missing permission should not necessarily stop unrelated streams from syncing.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Square and adds the required Square API version header. Someone would use it when starting a Square sync so every request uses the expected version of Square’s API.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the basic authenticated client, then adds `Square-Version: 2026-04-16` to the client’s headers. It returns that prepared client for later API calls.

**Call relations**: This is part of the connector setup inherited from the shared REST source framework. Later methods such as `paginate`, `_locations`, `_catalog`, and `_orders` use the prepared client to make actual Square requests.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a Square stream. It looks at which stream was requested and sends the work to the helper that knows how that particular Square endpoint behaves.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor value. Based on the stream name, it fetches records from Square as one or more pages, then yields each non-empty page to the caller. If Square refuses access with a 401 or 403 response, it turns that into a `StreamSkipped` message so the sync runner can move on safely.

**Call relations**: The sync framework calls this when it needs data for a Square stream. `paginate` calls `_locations` for locations, `_cursor_get` for cursor-based GET lists, `_catalog` for catalog searches, `_orders` for order searches, and uses `records_at` directly for inventory counts. If the stream is unknown or access is refused, it raises `StreamSkipped` to tell the larger sync flow that this stream should be skipped.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use a normal GET request with cursor-based pagination, such as customers, payments, and refunds. It also applies incremental filtering when needed, so old records are not sent again.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor from a previous sync. For payments and refunds, it sends the cursor to Square as `begin_time`; for other cursor-based streams, it fetches pages and locally keeps only records whose cursor field is newer than the saved cursor. It yields each page that still contains records after filtering.

**Call relations**: `paginate` calls this for the customers, payments, and refunds streams. This method relies on the shared REST connector’s cursor-page helper to do the repeated HTTP requests, then hands cleaned pages back up to `paginate` for delivery to the sync runner.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either items or categories and returns them page by page. It exists because catalog data uses Square’s POST search endpoint rather than a simple list endpoint.

**Data flow**: It receives the HTTP client, the requested catalog stream, and an optional saved cursor. It converts the stream name into the Square object type, sends a `/catalog/search` request, extracts the returned `objects`, filters out records older than the saved cursor when needed, and yields non-empty pages. It repeats this while Square returns a continuation cursor.

**Call relations**: `paginate` calls this when the requested stream is `catalog_items` or `catalog_categories`. Inside the loop, it uses `records_at` to pull the list of catalog objects out of Square’s response, then returns pages upward to `paginate`.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations are useful as their own stream, and they are also needed before searching for orders.

**Data flow**: It receives the HTTP client, makes a GET request to Square’s `/locations` endpoint, and extracts the `locations` list from the response. It returns that list as ordinary record dictionaries.

**Call relations**: `paginate` calls this directly when syncing the locations stream. `_orders` also calls it first, because Square order searches require location IDs before orders can be requested.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all available locations for the account. It first discovers the locations because Square requires order searches to say which locations to search.

**Data flow**: It receives the HTTP client and an optional saved cursor. It asks `_locations` for the account’s locations, keeps only valid string location IDs, and stops if there are none. It then posts search requests to `/orders/search`, optionally adding a created-at start time from the cursor, yields returned order pages, and follows Square’s continuation cursor until there are no more pages.

**Call relations**: `paginate` calls this for the orders stream. `_orders` depends on `_locations` to get the location IDs required by Square, uses `records_at` to extract the `orders` list from each response, and hands each page back to `paginate` for the main sync flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).

# Finance, billing, accounting, and spend sources  `stage-15.1.5`

This stage is shared behind-the-scenes support for the system’s data syncing work. Its job is to connect to finance services, ask them for records through their web APIs, and turn the answers into a common stream of records the rest of the system can store, search, and reuse. An API is a service’s “front desk” for software requests.

Each file is an adapter for one outside service. Brex, Ramp, and Mercury bring in company spending and banking data, such as cards, expenses, transfers, bank accounts, and transactions. Chargebee, Recurly, and Stripe bring in subscription billing data, including customers, invoices, subscriptions, coupons, and payments. Square brings in commerce data such as orders, refunds, inventory, locations, and catalog items. QuickBooks and Xero bring in accounting records like accounts, contacts, invoices, and payments.

Together, these adapters work like different plug shapes for one power strip: each understands its own service’s login, paging, and record format, then outputs data in the system’s standard shape.

## Files in this stage

### Spend and banking feeds
Connectors that collect corporate spend, card activity, bank accounts, transactions, and related operational finance records.

### `extensions/sources/ufo_ext_sources/providers/brex.py`

`io_transport` · `source sync`

Brex exposes company spending data through web API endpoints, but the rest of this project needs that data in a standard shape called streams. This file is the adapter between those two worlds. It says, for example, that the project’s “transactions” stream should be read from Brex’s `/v2/transactions/card/primary` endpoint, while “vendors” should come from `/v1/vendors`.

The file first defines a small helper, `_stream`, which creates stream descriptions. A stream description tells the larger source system the stream’s name, its main identifier field, and whether it has a date field that can act like a progress marker. Most Brex endpoints do not let the caller ask for “only records changed since this date,” so the connector mostly performs full reads. Still, transactions and expenses include date fields that the wider system can remember as watermarks.

The main class, `BrexConnector`, plugs into the shared `RestConnector` framework. Its important job here is pagination: Brex returns records in pages, like reading a long report 100 rows at a time. `paginate` asks for one page, yields the records if any are present, then follows Brex’s `next_cursor` token to request the next page. When Brex stops sending a next cursor, the read is complete.

#### Function details

##### `_stream`  (lines 39–50)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard description of one Brex data stream. It keeps the stream definitions short and consistent, so each stream can say only what is special about it, such as its primary key or date cursor field.

**Data flow**: It receives a stream name and optional details such as the primary key field, cursor field, and whether the stream is considered canonical. It puts those values into a `StreamSpec`, which is the project’s shared object for describing a source stream, and returns that object for use in the connector’s stream list.

**Call relations**: This function is used while the file is being loaded to assemble `BREX_STREAMS`, the catalog of Brex streams the connector offers. It hands the completed stream descriptions to the `BrexConnector` class through its `streams_list`, so the shared source framework knows what can be synced.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 71–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Brex stream from the Brex API, one page at a time. It is used when the source framework wants records for a stream and needs the connector to follow Brex’s cursor-based pagination until all available records have been read.

**Data flow**: It receives an HTTP client, a stream description, and a cursor argument. It looks up the Brex endpoint path for that stream, then repeatedly requests up to 100 records from Brex. Each response is expected to contain an `items` list and maybe a `next_cursor`. The method turns the `items` value into a safe list, yields non-empty batches of records to the caller, and keeps following `next_cursor` until there is no next page. If the stream has no known Brex endpoint, it stops with a clear error.

**Call relations**: The shared `RestConnector` machinery calls this method during a sync when it needs to fetch records for a Brex stream. Inside the loop, this method relies on the connector’s HTTP GET helper to talk to Brex and uses `ufo.sdk.sources.list_or_empty` to normalize the returned `items` field before handing record batches back to the wider sync pipeline.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/mercury.py`

`io_transport` · `sync polling`

This connector is a read-only bridge between Mercury and the project’s source-sync framework. Without it, the system would not know which Mercury API endpoints to call, how to page through transaction history, or how to recover cleanly when an API key is missing permission.

It defines two streams: accounts and transactions. Accounts are simple: the connector asks Mercury for all accounts in one request. Transactions are more like checking every folder in a filing cabinet. First it reads the account list, then for each account it asks for transactions in pages of 100 until Mercury returns a smaller page, which means there is nothing more to fetch.

For incremental syncs, the connector uses Mercury’s date-based `start` filter. A cursor, or saved “last seen” point, is cut down to its date. That means the last day is read again on the next run, and duplicate records are expected to be removed later by the shared sync machinery. This avoids missing transactions that share the same posting day. Pending transactions do not move the cursor because they have no posted date yet.

The file also customizes how transaction records are shown to users: transactions are titled by their `counterpartyName`, so a recalled record looks like it is about the person or business on the other side of the payment.

#### Function details

##### `MercuryConnector.paginate`  (lines 62–82)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Mercury streams. It decides whether the sync is asking for accounts or transactions, fetches the right data, and yields records page by page so the wider sync system can process them.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. For the accounts stream, it fetches the account list and yields it if there is anything to send. For the transactions stream, it first fetches accounts, extracts their IDs, then walks through each account’s transaction pages and adds the account ID as extra context to every transaction record. If Mercury rejects the request with a 401 or 403 status, it changes that low-level HTTP failure into a `StreamSkipped` message explaining that the API key is invalid or lacks read access.

**Call relations**: The sync framework calls this when it wants records from Mercury. Inside, it relies on `MercuryConnector._accounts` to get the account records, `MercuryConnector._account_ids` to decide which accounts need transaction reads, `MercuryConnector._transactions` to fetch each account’s transaction pages, and `with_context` to attach the account ID before handing records back to the framework.

*Call graph*: calls 4 internal fn (__init__, _account_ids, _accounts, _transactions); 1 external calls (with_context).


##### `MercuryConnector._accounts`  (lines 84–86)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches the Mercury account list. It gives the rest of the connector a clean list of account records instead of making callers know the exact shape of Mercury’s JSON response.

**Data flow**: It uses the HTTP client to request `/api/v1/accounts`. Mercury’s response is expected to contain an `accounts` field; the helper reads that field and passes it through `list_or_empty`, which means missing or unusable data becomes an empty list rather than surprising later code.

**Call relations**: `MercuryConnector.paginate` calls this both when syncing the accounts stream directly and when preparing to sync transactions. It is the connector’s single doorway for reading account data from Mercury.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector._account_ids`  (lines 89–94)

```
def _account_ids(accounts: list[dict[str, Any]]) -> list[str]
```

**Purpose**: This helper pulls usable account IDs out of account records. It protects the transaction sync from trying to call Mercury with missing or invalid account identifiers.

**Data flow**: It receives a list of account dictionaries. It looks at each record’s `id` field and keeps only values that are non-empty strings. The output is a simple list of account ID strings ready to be used in transaction API paths.

**Call relations**: After `MercuryConnector.paginate` has fetched accounts, it calls this helper before fetching transactions. The returned IDs become the input for `MercuryConnector._transactions`, one account at a time.

*Call graph*: called by 1 (paginate).


##### `MercuryConnector._transactions`  (lines 96–115)

```
async def _transactions(self, client: httpx.AsyncClient, account_id: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one account’s transaction history from Mercury, page by page. It is what lets the connector handle accounts with more transactions than Mercury returns in a single response.

**Data flow**: It receives an HTTP client, one Mercury account ID, and an optional cursor. It starts at offset zero and requests up to 100 transactions at a time. If a cursor exists, it sends Mercury a `start` date based on the cursor’s first 10 characters, so the sync begins at that posting day. Each response’s `transactions` field is cleaned into a list, yielded if non-empty, and then the offset moves forward. When Mercury returns fewer than 100 records, the helper stops because it has reached the end for that account.

**Call relations**: `MercuryConnector.paginate` calls this for each account ID it found. This helper does the repeated API calls, while `paginate` adds account context and passes the pages onward to the sync framework.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector.render`  (lines 117–126)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function decides how a Mercury record should be titled and displayed when it becomes a recallable page. For transactions, it uses the counterparty name so the record is easier for a person to recognize later.

**Data flow**: It receives one record and the stream it came from. If the stream is `transactions` and the record has a non-empty string `counterpartyName`, it returns that name as the title and a Markdown-like body containing the full JSON record. For accounts, or transactions without a usable counterparty name, it falls back to the standard rendering behavior from the base connector.

**Call relations**: The broader source framework calls this when turning raw synced records into human-readable pages. This function only customizes Mercury transactions; everything else is handed back to the parent `RestConnector` rendering logic.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/ramp.py`

`io_transport` · `source sync / request handling`

Ramp is a finance platform, and its API exposes many kinds of spend records. This file is the Ramp connector: the small adapter that knows Ramp’s URLs, pagination style, permission failures, and a few naming quirks. Without it, the wider source system would not know how to fetch Ramp data or how to move from one Ramp page of results to the next.

The file first defines the Ramp streams the system can sync. A stream is one kind of object, such as transactions or vendors. Most streams are re-read from the beginning on each run, and the larger system avoids storing duplicates. Transactions are different: Ramp can filter them by time, so this connector asks only for transactions after the last saved cursor.

Ramp returns paged responses with a `data` list and a `page.next` link. The connector follows that next link until there is no more. If Ramp answers with 401 or 403, meaning “not allowed” or “not signed in,” the connector marks that stream as skipped instead of failing the whole sync. This matters because some Ramp grants may include transactions and users but not bills or reimbursements.

Finally, the connector customizes how transaction records are shown. Ramp stores the human-friendly transaction title in `merchant_name`, so the connector uses that as the title; other records use the normal default rendering.

#### Function details

##### `_stream`  (lines 50–66)

```
def _stream(name: str, *, cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard description of one Ramp stream, such as transactions or cards. It keeps the stream definitions short and consistent, so each stream declares the same basic facts: its name, primary key, date fields, and whether it is a main supported stream.

**Data flow**: It receives a stream name and optional settings such as which field acts as the sync cursor. It puts those details into a `StreamSpec`, which is the shared object the source framework uses to understand how to sync that stream. The result is returned and later collected into the Ramp stream list.

**Call relations**: This function is used while the file is loaded to build `RAMP_STREAMS`. Each call creates one stream specification, and the `RampConnector` then exposes that list to the rest of the source-sync system.

*Call graph*: 1 external calls (__init__).


##### `RampConnector._next_path`  (lines 97–104)

```
def _next_path(next_link: Any) -> str | None
```

**Purpose**: This helper converts Ramp’s next-page link into the form the HTTP client in this connector needs. Ramp gives a full web address, but the connector’s client is already tied to Ramp’s base address, so it only needs the path and query part.

**Data flow**: It receives a possible next-page value from Ramp’s response. If the value is not a useful string, it returns nothing. If it is a valid URL with a path, it extracts the path and keeps the query string if one exists, then returns that shorter next request path.

**Call relations**: During pagination, `RampConnector.paginate` calls this after each response. `_next_path` tells pagination whether there is another page and, if so, exactly what path to request next.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RampConnector.paginate`  (lines 106–134)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Ramp list endpoints. It asks Ramp for one page of records at a time, yields each non-empty batch to the sync system, and follows Ramp’s next-page links until the stream is complete.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. It chooses the correct Ramp API path for that stream, adds a page size, and for transactions also adds ascending date order and a `from_date` filter when a cursor exists. For each API response, it takes the `data` list, yields it if there are records, reads `page.next`, and continues with the next path until there is no next page. If Ramp refuses access with 401 or 403, it turns that into a skipped stream message; other HTTP errors are passed upward.

**Call relations**: The source framework calls this when it is time to fetch a Ramp stream. Inside the loop it uses `list_or_empty` to safely treat missing or invalid `data` as an empty list, and it asks `_next_path` to translate Ramp’s next-page URL into the next request. When permissions are missing, it raises `StreamSkipped` so the overall sync can continue with other streams.

*Call graph*: calls 2 internal fn (__init__, _next_path); 1 external calls (list_or_empty).


##### `RampConnector.render`  (lines 136–146)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function decides how a Ramp record should look when it is turned into recallable text. It gives transactions a useful human title based on the merchant name, because Ramp does not expose that value under a generic title field.

**Data flow**: It receives one record and the stream it came from. If the stream is not transactions, or the record has no usable `merchant_name`, it falls back to the standard rendering from the parent connector. If it is a transaction with a merchant name, it returns that merchant as the title and a text body containing the full record as sorted JSON.

**Call relations**: The source framework calls this after records are fetched, when it needs a title and text body for storage or recall. This method only customizes transaction display; all other Ramp streams are handed back to the shared default renderer.

*Call graph*: 1 external calls (dumps).


### Subscription billing platforms
Connectors that normalize recurring billing, subscription, invoice, customer, and payment records from subscription revenue systems.

### `extensions/sources/ufo_ext_sources/providers/chargebee.py`

`io_transport` · `during source sync and API pagination`

Chargebee is a billing platform, and its API returns many kinds of business records: customers, subscriptions, invoices, transactions, items, quotes, and more. This file is the read-only connector for that API. Without it, the system would not know how to pull Chargebee data into a sync run.

The file first defines the list of supported streams. A stream is one category of records, like “customer” or “invoice.” Most streams use a simple Chargebee pattern: ask an endpoint for up to 100 records, read the returned “next_offset” token, then ask for the next page until no token remains. For incremental syncs, it can also send a cursor such as “updated_at after this value,” meaning “only give me records newer than what I already saw.”

Chargebee wraps each record inside a named envelope, such as `{customer: {...}}`. The connector flattens that wrapper so downstream code sees the useful fields at the top level. A few streams are substreams: for example, contacts must be fetched customer by customer. The connector first lists the parent records, then visits each child endpoint and stamps the parent ID onto each child record, like putting a return address on every envelope.

Authentication uses HTTP Basic auth with the API key as the username, unless a proxy transport has already been supplied. If Chargebee refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a compact description of one Chargebee data stream, such as which field uniquely identifies records and which timestamp should be used for incremental syncing. It keeps the stream list readable and consistent.

**Data flow**: It receives a stream name plus optional details like primary key, cursor field, and whether the stream is canonical. It fills in sensible defaults when details are not supplied, then returns a StreamSpec object that the connector later uses as its instruction card for that stream.

**Call relations**: This helper is used while the file is loaded to build the CHARGEBEE_STREAMS list. It hands its settings to StreamSpec.__init__, which turns those plain choices into the stream objects used by ChargebeeConnector.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Chargebee. It sets timeouts, standard request headers, and the right authentication method for the resolved credential.

**Data flow**: It receives a base URL and a credential. It trims the base URL, prepares JSON/form headers and timeout limits, then either uses a supplied transport, uses the credential’s API key as Basic auth, or raises an error if no usable authentication is present. The output is an httpx.AsyncClient ready to make Chargebee API calls.

**Call relations**: The broader RestConnector machinery calls this when it needs a network client for a sync. This function delegates client construction to httpx.AsyncClient, httpx.Timeout, and, when using a direct API key, httpx.BasicAuth.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Removes Chargebee’s extra wrapper around each record so the rest of the system can read fields directly. It also preserves parent IDs that substream pagination added beside the wrapped record.

**Data flow**: It receives one raw record and the stream description. If the record contains a dictionary under the stream’s source object name, it copies the inner record and merges in any outer fields that are not already present. If there is no expected wrapper, it returns the record unchanged.

**Call relations**: This is used after pages are fetched, when records are being normalized for downstream processing. It does not call other functions; it is the cleanup step that turns Chargebee-shaped records into system-shaped records.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right paging strategy for a requested Chargebee stream. It is the main traffic director for reading pages of records from Chargebee.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. Based on the stream name, it sends the work to the normal list paginator or to one of the special parent-child paginators. It yields pages of raw records as they arrive. If Chargebee answers with 401 or 403, it turns that refusal into a StreamSkipped error with a clear explanation.

**Call relations**: The sync engine calls this when it wants records for a stream. This method then calls _paginate_list for ordinary streams, or _paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, or _paginate_subscription_scheduled for substreams. When access is refused, it creates a StreamSkipped exception so the larger sync can treat the stream as unavailable rather than crashing without context.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Builds the query parameters for a normal Chargebee list request. These parameters control page size and, when possible, ask only for records newer than the saved cursor.

**Data flow**: It receives a stream description and an optional cursor value. It always starts with a page limit of 100. If a cursor is present and the stream has a cursor field, it adds a Chargebee “after” filter for that field. It returns the parameter dictionary used in the API request.

**Call relations**: _paginate_list calls this just before starting a list walk. It is the small helper that translates the system’s idea of incremental sync into Chargebee’s query parameter format.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads an ordinary Chargebee list endpoint page by page. This is the common path for streams like customers, invoices, transactions, and most other top-level resources.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the correct endpoint path, builds request parameters, then asks the inherited cursor-page helper to repeatedly fetch pages from Chargebee’s `list` field while following the `next_offset` token. It yields each page of records as it is returned.

**Call relations**: paginate calls this for normal streams. The substream paginators also call it first to find their parent records, such as items, customers, quotes, or subscriptions. Before handing off to the inherited page walker, it calls _build_list_params to prepare the Chargebee request parameters.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches attached items, which Chargebee exposes underneath each parent item rather than as one global list. It walks through items first, then asks Chargebee for the attached items belonging to each one.

**Data flow**: It receives an HTTP client and an optional cursor. It lists item records, extracts each item ID, skips parents without an ID, then fetches `/items/{item_id}/attached_items` for each valid parent. Each child record page is yielded with the parent item ID stamped onto it.

**Call relations**: paginate calls this when the requested stream is `attached_item`. This function uses _paginate_list to discover parent items and _paginate_substream to read each child endpoint and attach the parent ID.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches customer contacts, which must be requested one customer at a time. It makes contact records usable on their own by adding the customer ID they came from.

**Data flow**: It receives an HTTP client and an optional cursor. It lists customers, extracts each customer ID, skips any parent without an ID, then reads `/customers/{customer_id}/contacts`. It yields pages of contact records enriched with the matching customer ID.

**Call relations**: paginate calls this for the `contact` stream. It relies on _paginate_list to get customer parents and _paginate_substream to fetch and label each customer’s contacts.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches quote line groups, which are nested under individual quotes in the Chargebee API. It connects every line group back to the quote it belongs to.

**Data flow**: It receives an HTTP client and an optional cursor. It lists quotes, pulls out each quote ID, ignores quote records without an ID, then reads `/quotes/{quote_id}/quote_line_groups`. It yields child pages with the parent quote ID added to each record.

**Call relations**: paginate calls this for the `quote_line_group` stream. This function uses _paginate_list to find quotes and _paginate_substream to walk each quote’s child pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches subscription records together with their scheduled changes, using a special one-record Chargebee endpoint for each subscription. This covers data that is not available through the normal subscription list alone.

**Data flow**: It receives an HTTP client and an optional cursor. It lists subscriptions, extracts each subscription ID, skips missing IDs, then calls `/subscriptions/{id}/retrieve_with_scheduled_changes` for each one. If the response contains a subscription object, it yields a one-record page containing that subscription plus a `subscription_id` field.

**Call relations**: paginate calls this for the `subscription_with_scheduled_changes` stream. It uses _paginate_list to discover parent subscriptions, then uses the inherited `_get` request helper to fetch each detailed scheduled-change record.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the shared paging loop for child resources that live under a parent record. It also labels each child with the parent ID, so the relationship is not lost after extraction.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent ID field to add, and the parent ID value. It fetches pages using Chargebee’s usual `list` and `next_offset` pattern, copies each dictionary record, adds the parent ID, and yields only non-empty stamped pages.

**Call relations**: _paginate_attached_items, _paginate_contacts, and _paginate_quote_line_groups call this after they have found a parent ID. It centralizes the repeated child-page behavior so those parent-specific functions only need to decide which endpoint and parent field to use.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/providers/recurly.py`

`io_transport` · `source sync / data extraction`

Recurly returns lists of data in pages, like a long report split across many envelopes. Each envelope says what records it contains, whether there are more, and where to ask for the next page. This file knows how to follow those envelopes until all available records have been read.

The file defines the Recurly streams the system can sync, including core objects such as accounts, plans, invoices, subscriptions, and transactions. It also covers child data that lives underneath a parent object, such as account notes under an account or unique coupon codes under a coupon. For those child streams, it first walks through the parent list, then asks Recurly for each parent’s children, and adds the parent id onto each child row so the relationship is not lost.

Authentication is also specific here. Recurly uses HTTP Basic authentication, which means the API key is sent like a username rather than as a bearer token. The connector also pins the Recurly API version through an HTTP header so responses stay predictable. If Recurly refuses access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole sync. Without this file, the system would not know Recurly’s paging style, authentication style, stream list, or parent-child API layout.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one Recurly object. A stream description tells the sync system the stream’s name, where to read it from, what field uniquely identifies rows, and what time field can be used for incremental syncing.

**Data flow**: It receives a stream name and optional details such as a different Recurly object name, primary key, cursor field, and whether the stream is a main canonical stream. It fills in sensible defaults, then returns a StreamSpec object that the connector can advertise to the rest of the system.

**Call relations**: This helper is used while the file is loaded to build the RECURLY_STREAMS list. It hands its settings to StreamSpec so the rest of the connector can later paginate each stream consistently.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It makes sure requests use Recurly’s required API version, timeouts, and authentication style.

**Data flow**: It receives a base URL and a credential. It trims the base URL, sets Recurly-specific headers, builds a timeout, and then chooses how to authenticate: either by using a supplied proxy transport or by sending the API key as HTTP Basic authentication. It returns an asynchronous HTTP client ready to make Recurly requests, or raises an error if no usable credential exists.

**Call relations**: This is the connector’s client-building hook. It calls httpx’s client, timeout, and BasicAuth helpers so later pagination methods can focus on fetching records rather than recreating connection details.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s “next page” link into a path the HTTP client can safely request. Recurly may return either a full URL or just a path, and this function normalizes that difference.

**Data flow**: It receives a next-link value from a Recurly response. If the value is empty, it returns nothing. If the value is a full URL, it keeps only the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: All pagination loops use this after Recurly says there are more pages. Top-level streams, account id discovery, coupon id discovery, and per-parent child streams call it before making the next request.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the first set of query parameters for a Recurly list request. It controls page size, sort order, and optional incremental syncing from a saved cursor time.

**Data flow**: It receives a stream description and an optional cursor value. It creates parameters asking Recurly for up to 200 records in ascending order, sorted by the stream’s cursor field or by created_at when there is no cursor field. If both a cursor field and cursor value exist, it adds begin_time so Recurly starts from that point.

**Call relations**: Top-level pagination and per-parent pagination call this before their first request. After that first request, Recurly’s own next-page link carries the cursor information forward.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading entry for a Recurly stream. Given a stream and an optional saved cursor, it yields batches of records until that stream has no more data to read.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It decides which reading strategy fits the stream: ordinary top-level paging, parent-child paging, or the special coupon-parent stream that keeps only bulk coupons. It yields lists of record dictionaries. If Recurly refuses access with 401 or 403, it turns that into a StreamSkipped message so the sync can continue appropriately.

**Call relations**: The broader source sync flow calls this when it wants records from a Recurly stream. This method dispatches to _paginate_top_level or _paginate_per_parent, and it wraps permission failures in StreamSkipped to give the caller a clear, stream-specific reason.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly collection such as accounts, plans, invoices, or transactions. It follows Recurly’s page links until the collection is exhausted.

**Data flow**: It receives an HTTP client, stream description, API path, and optional cursor. It builds the first query, requests the current page, yields the records if any exist, then uses Recurly’s next link to move to the following page. It stops when Recurly says there are no more pages.

**Call relations**: paginate calls this for most streams and for the coupon-parent special case. It relies on _initial_query for the first request and _next_path for every follow-up page link.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields their ids. Those ids are needed before the connector can read child data that belongs to each account.

**Data flow**: It starts at the accounts endpoint with a simple page query. For each returned account row, it checks that the row is a dictionary and has an id, then yields that id as text. It follows next-page links until Recurly reports no more account pages.

**Call relations**: _paginate_per_parent calls this when a child stream lives under accounts, such as account notes or billing infos. This function uses _next_path to continue through account pages.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through Recurly coupons and yields the ids of bulk coupons only. Bulk coupon ids are needed to fetch their unique coupon codes.

**Data flow**: It starts at the coupons endpoint and reads pages in creation order. For each row, it ignores invalid rows, rows without ids, and coupons that are not marked as bulk. It yields each qualifying coupon id as text and follows next-page links until done.

**Call relations**: _paginate_per_parent calls this when the parent collection is coupons. This function narrows the parent list before child paging begins, and uses _next_path to continue through coupon pages.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that are nested under a parent object, such as notes under each account or unique coupon codes under each bulk coupon. It preserves the parent-child link by adding the parent id to each child record when needed.

**Data flow**: It receives the HTTP client, stream description, parent path, child path, the field name used to store the parent id, and an optional cursor. It first chooses the right parent id source: accounts or bulk coupons. For each parent id, it requests the child endpoint, yields each page of child records, and stamps the parent id onto dictionary rows that do not already have it. It follows child next-page links until that parent is finished, then moves to the next parent.

**Call relations**: paginate calls this for streams listed as per-parent streams. It calls _account_ids or _coupon_ids to discover parents, _initial_query to start each child request, and _next_path to walk through each child collection’s pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/stripe.py`

`io_transport` · `during source data sync`

Stripe exposes its data through many web API endpoints, and most of them use the same pattern: ask for up to 100 records, then ask for the next page after the last record’s id. This file wraps that pattern in a StripeConnector so the rest of the project does not need to know the details of Stripe’s URLs, page format, date filters, or special nested resources.

The file first lists all supported Stripe “streams,” meaning named collections of records such as customers, invoices, charges, and checkout session line items. Some streams are simple top-level lists. Others are child lists that only make sense under a parent, like invoice line items under an invoice. The connector walks those parent-child relationships for the caller, like opening every folder in a filing cabinet and copying the papers inside while writing the folder label on each paper.

When syncing, it builds HTTP requests with Stripe’s pinned API version, optional date filtering, and any special Stripe parameters. It normalizes timestamp fields into readable date strings. If Stripe refuses access with a 401 or 403 response, the connector marks that stream as skipped instead of crashing the whole sync. It only reads data; there is no write or update path here.

#### Function details

##### `_stream`  (lines 89–107)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: Creates a StreamSpec, which is the small description the sync system uses to know what a Stripe collection is called, what field identifies each record, and what field can be used for incremental syncing.

**Data flow**: It receives a friendly stream name plus optional details such as the Stripe API object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a StreamSpec object that becomes part of the connector’s list of readable Stripe streams.

**Call relations**: This helper is used while the file is being loaded to build STRIPE_STREAMS. Those stream descriptions are later used by StripeConnector when deciding which Stripe endpoint to call and how to interpret records from it.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 182–185)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to Stripe and adds the exact Stripe API version this connector expects. Pinning the version helps keep Stripe responses stable even if Stripe changes its defaults later.

**Data flow**: It receives a base URL and a credential object supplied by the wider system. It asks the parent RestConnector to create the normal authenticated client, adds the Stripe-Version header, and returns that ready-to-use client.

**Call relations**: This is part of the connector setup inherited from RestConnector. Once the client is prepared, later pagination methods use it to make Stripe API requests with the correct authentication and API version.


##### `StripeConnector._list_path`  (lines 188–189)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: Builds the standard Stripe API path for a top-level stream. For example, a stream whose source object is customers becomes /v1/customers.

**Data flow**: It receives a StreamSpec and reads its source_object value. It returns the matching Stripe URL path string under /v1.

**Call relations**: The main paginate flow and the nested pagination helpers call this when they need the ordinary list endpoint for a stream or parent stream before fetching records.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 192–204)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: Converts a saved sync position into the Unix timestamp format Stripe expects for created-date filtering. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor value, which may be empty, already numeric, or an ISO-style date string. Empty or unrecognized values become None; numeric values become integers; valid date strings become UTC Unix seconds.

**Call relations**: The page loop calls this before making requests. If the stream uses Stripe’s created field as its cursor, the converted value is sent to Stripe so the sync can ask only for records created after that point.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 206–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to read a Stripe stream and yields batches of records. It is the main doorway the source-sync runner uses when it wants records from Stripe.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks whether the stream is a special external-account stream, a path-based child stream, a query-based child stream, or a normal top-level stream, then delegates to the matching pagination routine. It yields lists of record dictionaries. If Stripe refuses access with 401 or 403, it turns that into a StreamSkipped signal.

**Call relations**: The wider RestConnector framework calls this during sync. paginate then hands work to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, or _page_loop depending on the stream shape, so callers get one uniform stream of pages regardless of Stripe’s endpoint layout.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 234–265)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one Stripe list endpoint page by page until there are no more records. This is the reusable engine behind both simple streams and many nested streams.

**Data flow**: It receives an HTTP client, a URL path, a stream description, an optional cursor, and optional extra query parameters. It builds Stripe query parameters such as limit, starting_after, created[gte], and stream-specific filters, sends each request, normalizes each returned record, yields non-empty pages, and stops when Stripe says there are no more pages or there is no usable last id.

**Call relations**: paginate calls this directly for ordinary streams. The substream helpers also call it for parent lists and child lists. It uses _cursor_to_unix to prepare date filters and _browse_record to clean up each record before handing pages back upward.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 268–288)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Makes a single Stripe record easier for the rest of the system to use by adding or converting readable timestamp fields. It does not deeply reshape the record; it mostly passes Stripe’s data through.

**Data flow**: It receives one record dictionary and the stream description. It copies the record, converts numeric created_at or updated_at fields to ISO date strings, fills created_at from Stripe’s created field when possible, and fills updated_at from the stream cursor when possible. It returns the normalized copy.

**Call relations**: _page_loop calls this for every record it receives from Stripe. The cleaned records are then yielded to whichever higher-level pagination path requested them.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 290–312)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child records that live under each parent record’s URL path, such as line items under an invoice or payment methods under a customer. It also stamps the parent id onto each child so the relationship is not lost.

**Data flow**: It receives an HTTP client and a child stream description. It finds the parent stream, walks through all parent pages, skips parents without ids, builds each child URL from the parent id, reads the child pages, adds parent fields such as customer_id or invoice_id when configured, and yields the enriched child records.

**Call relations**: paginate calls this for streams listed as path-based substreams. It relies on _stream_spec to find the parent stream, _parent_pages to enumerate parents, and _page_loop to fetch each child collection.

*Call graph*: calls 3 internal fn (_page_loop, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 314–319)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides pages of parent records for a child-stream sync. It hides whether the parent itself is a normal top-level stream or another query-based child stream.

**Data flow**: It receives an HTTP client and a parent stream description. If that parent stream must be fetched through a query-based fan-out, it returns that special pagination flow; otherwise it returns the ordinary page loop for the parent’s list endpoint.

**Call relations**: _paginate_substream calls this when it needs parent records before fetching children. It may hand off to _paginate_substream_query for more complex parents, or to _page_loop for normal parents.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 321–335)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads child records that Stripe exposes by passing the parent id as a query parameter instead of putting it in the URL path. For example, subscription items are requested with a subscription id parameter.

**Data flow**: It receives an HTTP client and a stream description. It looks up the parent stream name, the query parameter name, and the child endpoint path; then it pages through parents, skips parents without ids, requests child pages using the parent id as a query parameter, stamps that parent id onto each child row, and yields the results.

**Call relations**: paginate calls this for query-based child streams. _parent_pages can also call it when a path-based child stream depends on a parent that is itself query-based, allowing multi-level walks such as subscriptions to subscription items to usage records.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 337–350)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads external bank accounts or cards attached to Stripe Connect accounts. These need a special account-by-account walk and an object-type filter.

**Data flow**: It receives an HTTP client and the external-account stream description. It pages through Stripe accounts, skips accounts without ids, requests each account’s external_accounts endpoint, and yields child records with account_id added so each bank account or card can be traced back to its Stripe account.

**Call relations**: paginate calls this only for the external_account_bank_accounts and external_account_cards streams. It uses _stream_spec to find the accounts stream, _list_path and _page_loop to read accounts, and _page_loop again to read each account’s external accounts.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 352–353)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: Finds the StreamSpec for a named Stripe stream. This lets helper methods move from a child stream name to the parent stream description they need.

**Data flow**: It receives a stream name, searches the file’s STRIPE_STREAMS list, and returns the first matching StreamSpec. If the name is not present, the normal Python search behavior raises an error.

**Call relations**: The substream helpers call this when they need details about a parent stream such as accounts, customers, invoices, or subscriptions before they can request child data from Stripe.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Commerce payments
The commerce connector that reads point-of-sale and payment platform records such as customers, orders, catalog entries, refunds, and inventory.

### `extensions/sources/ufo_ext_sources/providers/square.py`

`io_transport` · `source sync`

Square exposes its data through several different API patterns. Some records are fetched with simple GET requests and a cursor, some require POST search requests, and some are one-time lists. This file hides those differences behind one connector, so the rest of the project can simply ask for a named Square stream and receive batches of records.

The central class, SquareConnector, is a read-only connector. It sets Square’s required API version header, chooses the right fetching strategy for each stream, and yields records page by page. A “page” here means a batch of records, like a few sheets from a large filing cabinet rather than the whole cabinet at once.

For customers, payments, and refunds, it follows Square’s cursor-based list endpoints. For catalog items and categories, it calls Square’s catalog search endpoint and keeps sending back Square’s continuation cursor until there is no more data. For orders, it first fetches locations, then searches orders across those location IDs. Locations and inventory counts are fetched as single collections.

The file also protects the larger sync run from expected access problems. If Square returns 401 or 403, meaning the token is invalid or lacks permission, it raises StreamSkipped instead of crashing the whole connector run. Unsupported streams are skipped the same way.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Square. In addition to the normal client setup from the shared REST connector, it adds the Square API version header that Square requires for stable behavior.

**Data flow**: It receives a base URL and a credential, asks the parent REST connector to build an authenticated HTTP client, then adds the Square-Version header. It returns that ready-to-use client, now carrying both authentication and the pinned Square API version.

**Call relations**: This is part of the connector setup before any Square stream is read. Later requests made by paginate and its helper methods use this client, so every call to Square carries the expected API version.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading a Square stream. Given a stream name, it chooses the correct Square API pattern and yields batches of records in the format the syncing system expects.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where a previous sync left off. It checks the stream name, calls the matching helper, and yields each non-empty page of records. If Square refuses access with 401 or 403, or if the stream is unknown, it turns that into StreamSkipped so the system can skip that stream cleanly.

**Call relations**: The broader source syncing machinery calls paginate when it wants records for a Square stream. paginate then hands off to _locations for locations, _cursor_get for customers/payments/refunds, _catalog for catalog streams, _orders for orders, or performs the inventory-count request itself and uses records_at to pull the list out of Square’s response.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that behave like normal cursor-based lists, such as customers, payments, and refunds. It keeps requesting pages until Square says there are no more.

**Data flow**: It receives the HTTP client, the stream description, and an optional sync cursor. For payments and refunds, it can send that cursor to Square as a begin_time filter. For other cursor-based streams, it fetches pages and filters out records whose cursor field is not newer than the saved cursor. It yields only non-empty batches.

**Call relations**: paginate calls this helper when the requested stream is customers, payments, or refunds. This helper relies on the shared REST connector’s cursor-page reader to do the repeated HTTP requests, then applies Square-specific date filtering before handing pages back to paginate.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either items or categories and returns them page by page. It exists because catalog data uses Square’s POST-based search endpoint rather than a simple list endpoint.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor from a previous sync. It translates the stream name into Square’s catalog object type, sends a search request with a page limit, extracts catalog objects from the response, filters by updated time if needed, yields any remaining records, then repeats with Square’s continuation cursor until there is no next cursor.

**Call relations**: paginate calls this helper for catalog_items and catalog_categories. _catalog uses records_at to pull the objects list out of each Square response, then gives each cleaned page back to paginate for the main sync flow.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations are useful both as their own stream and as the starting point for finding orders, because Square order search needs location IDs.

**Data flow**: It receives the HTTP client, sends a GET request to Square’s locations endpoint, and extracts the locations list from the response. It returns that list of location records.

**Call relations**: paginate calls this directly when syncing the locations stream. _orders also calls it first, because it must know which Square locations to include when searching for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all available locations. It first discovers the account’s location IDs, then searches orders for those locations in pages.

**Data flow**: It receives the HTTP client and an optional cursor. It calls _locations, keeps only valid string location IDs, and stops early if there are none. It then builds an orders search request with those IDs, adds a created-at start time if a cursor was supplied, follows Square’s continuation cursor between pages, extracts orders from each response, and yields each non-empty batch.

**Call relations**: paginate calls this when the requested stream is orders. _orders depends on _locations to find where to search, uses records_at to pull orders out of each Square response, and then returns pages back into the main pagination flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### Accounting ledgers
Connectors that read accounting-system entities such as accounts, contacts, invoices, payments, and other financial records.

### `extensions/sources/ufo_ext_sources/providers/quickbooks.py`

`io_transport` · `source sync`

QuickBooks Online stores each company’s data behind a company-specific web address, and it does not offer simple “give me all invoices” list endpoints for every record type. Instead, this connector must send SQL-like queries such as “select all invoices, starting at row 101.” This file turns that awkward API shape into normal streams of records that the rest of the system can recall and sync.

The file first defines a helper, `_stream`, which describes each kind of QuickBooks data the system can read: accounts, customers, invoices, bills, payments, journal entries, and many others. Each stream uses QuickBooks’ `Id` as its unique key. Most streams also use `MetaData.LastUpdatedTime` as a cursor, meaning a bookmark that lets later syncs fetch only records changed since the last run. A few reference lists, such as payment methods and tax agencies, do not use that cursor and are read in full.

`QuickBooksConnector` is the working connector. For each stream, it builds a QuickBooks query, sends it to the `/query` endpoint, reads records from the `QueryResponse` wrapper, and keeps asking for later pages until QuickBooks returns fewer than 100 records. If QuickBooks refuses access with a 401 or 403 response, the stream is skipped with a clear explanation instead of crashing the whole sync. Finally, `flatten` copies the nested update timestamp onto a flat key so the shared sync machinery can advance its bookmark.

#### Function details

##### `_stream`  (lines 32–47)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one QuickBooks record type that can be synced. This keeps the long list of QuickBooks streams short, consistent, and less error-prone.

**Data flow**: It takes a friendly stream name, the matching QuickBooks entity name, optional cursor information, and whether the stream is considered canonical. It fills in the shared details, such as `Id` as the primary key and QuickBooks metadata fields for creation and update times, then returns a `StreamSpec` object that the connector can later use.

**Call relations**: This helper is used while the file is being loaded to build `QUICKBOOKS_STREAMS`. Each returned `StreamSpec` becomes an instruction card for `QuickBooksConnector`, telling it what entity to query and how to track changes over time.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 88–95)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: Builds the SQL-like text query that QuickBooks expects for one page of one stream. It also adds the incremental-sync filter when there is a saved cursor, so the system asks only for records updated after that point.

**Data flow**: It receives a stream description, an optional cursor value, and the starting row number for the page. It creates a query beginning with `SELECT * FROM <entity>`, optionally adds a `WHERE` and `ORDER BY` clause using the stream’s update timestamp, then adds QuickBooks pagination controls. The output is a single query string ready to send to QuickBooks.

**Call relations**: `paginate` calls this each time it needs another page of results. `_build_query` does not contact QuickBooks itself; it simply prepares the exact question that `paginate` will send.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 97–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from QuickBooks one page at a time for a given stream. It hides QuickBooks’ start-position paging style and yields simple batches of records to the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. Starting at position 1, it builds a query, sends it to QuickBooks’ `/query` endpoint, pulls the actual records out of `QueryResponse.<Entity>`, and yields each non-empty batch. If a batch has fewer than 100 records, it stops because that means there are no more pages. If QuickBooks returns 401 or 403, it turns that refusal into a `StreamSkipped` error with a helpful message; other HTTP errors are passed onward unchanged.

**Call relations**: During a sync, the shared source runner calls this method to read a stream. `paginate` relies on `_build_query` to form each QuickBooks query, then hands each returned batch back to the caller. When access is denied for a stream, it raises `StreamSkipped` so the larger sync can treat that stream as unavailable rather than silently producing bad data.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 121–124)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Makes QuickBooks’ nested update timestamp easier for the shared sync code to read. This matters because the cursor field is written as `MetaData.LastUpdatedTime`, but the value is stored inside a nested `MetaData` object in the raw record.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream’s cursor field is a dotted path, it reads that nested value from the record and returns a copy of the record with an extra flat key named exactly like the cursor field. If there is no dotted cursor path, it returns the record unchanged.

**Call relations**: After records are fetched, the source framework can call this before saving or comparing cursor values. `flatten` uses `get_path` to pull the nested timestamp out of the raw QuickBooks data, so the framework can advance its sync bookmark without needing special QuickBooks-specific knowledge.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/xero.py`

`io_transport` · `during source sync runs, when fetching Xero records`

Xero is an accounting service, and its API has a few special rules. This file is the adapter that knows those rules. Without it, the sync runner would not know which Xero resources exist, how to page through them, how to ask only for recently changed records, or how to identify each record consistently.

The file first defines the Xero “streams,” which are the named collections the system can read, such as invoices or contacts. Each stream says what Xero calls that collection, what field marks changes over time, and that the project should use a common `id` field as the record key.

The `XeroConnector` then does the live API work. Before reading accounting data, it makes sure every request has a Xero tenant header. A tenant is the specific organisation inside a Xero login. If a login is connected to several organisations, the connector refuses to guess, because picking the wrong one would import the wrong company’s books.

When reading data, it sends Xero’s incremental-sync date in an `If-Modified-Since` header, because Xero expects that instead of a normal URL query value. It reads paged collections page by page, but reads small non-paged collections in one request. Finally, it reshapes records by copying Xero’s typed IDs, like `InvoiceID`, into the common `id` field used by the rest of the system.

#### Function details

##### `_stream`  (lines 75–89)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard stream description for one Xero collection. It saves repetition when listing many Xero resources that all follow the same basic pattern.

**Data flow**: It takes a friendly stream name, the exact object name Xero uses in its response, an optional field used to track changes, and whether the stream is considered canonical. It packages those details into a `StreamSpec`, which is the project’s shared description of a readable collection.

**Call relations**: At file load time, the Xero stream list is built by repeatedly calling this helper. The helper hands the collected details to `StreamSpec`, so later the connector can use those stream descriptions when making API requests and naming returned records.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 117–134)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This converts the system’s saved sync position into the date format Xero expects for incremental reads. In plain terms, it turns “start from this last-seen time” into a web-date string Xero understands.

**Data flow**: It receives a cursor value, which may be missing, blank, a numeric timestamp, an ISO-style date string, or already some other date text. Empty or unusable values become `None`; numeric timestamps and parseable dates become UTC strings like an HTTP date; unparseable text is passed through unchanged.

**Call relations**: `XeroConnector.paginate` calls this when a stream has a change-tracking field and a previous cursor. The converted value is then placed into the `If-Modified-Since` request header before Xero is asked for records.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 142–143)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This creates a Xero connector instance and optionally pins it to one known Xero organisation. That is useful when the caller already knows which tenant, or organisation, should be synced.

**Data flow**: It receives an optional tenant ID. It stores that value on the connector so future HTTP clients can include it in Xero requests; it does not contact Xero or read any records.

**Call relations**: This is the setup step for the connector object. Later, when a client is made or a sync starts, the stored tenant ID can be used by `_make_client` or avoided by `_ensure_tenant` if the header is already present.


##### `XeroConnector._make_client`  (lines 145–149)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Xero and adds the required organisation header when the connector was created with a tenant ID. The HTTP client is the object that actually sends web requests.

**Data flow**: It receives a base URL and a credential, asks the parent REST connector to make the normal authenticated client, then adds the `xero-tenant-id` header if a tenant ID was provided earlier. It returns the prepared client.

**Call relations**: This fits into the broader REST connector flow when a sync run needs a client for Xero. It relies on the parent connector for the usual authenticated setup, then adds Xero’s organisation-specific requirement before requests are made.


##### `XeroConnector._ensure_tenant`  (lines 151–179)

```
async def _ensure_tenant(self, client: httpx.AsyncClient) -> None
```

**Purpose**: This makes sure the HTTP client has the Xero organisation header required for accounting API calls. If the connector was not given a tenant up front, it looks at the Xero grant and safely chooses the single organisation only when there is exactly one.

**Data flow**: It receives an HTTP client. If the client already has a tenant header, it leaves it alone. Otherwise it calls Xero’s connections endpoint, reads the returned connection list, filters it to organisation tenants, and either writes the one tenant ID into the client header or raises a clear sync fault if there are none or more than one.

**Call relations**: `XeroConnector.paginate` calls this before fetching stream data, because every accounting request needs the tenant header. It uses `list_or_empty` to safely treat the connections response as a list, and it raises `StreamFault` when continuing would mean either syncing nothing or guessing the wrong organisation.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `XeroConnector.paginate`  (lines 181–217)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one Xero stream from the API, yielding batches of records as it goes. It knows which Xero collections use pages and which return everything in one response.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero path, optionally converts the cursor into an `If-Modified-Since` header, ensures the tenant header is present, then sends GET requests. For non-paged streams it yields one batch if records exist. For paged streams it keeps requesting page 1, page 2, and so on until Xero returns no records or fewer than the normal page size. If Xero refuses with 401 or 403, it turns that into a skipped stream instead of a crash for all streams.

**Call relations**: This is the main read loop used during syncing. Before making API calls it asks `_ensure_tenant` to prepare the organisation header and `_cursor_to_rfc1123` to prepare the incremental-sync date. It then hands batches of raw Xero records back to the surrounding sync machinery, while converting permission failures into `StreamSkipped` so the runner can report that this stream could not be read.

*Call graph*: calls 3 internal fn (__init__, _ensure_tenant, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 219–228)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This gives each Xero record the common `id` field expected by the rest of the sync system. Xero uses different ID field names for different resources, so this function normalizes them.

**Data flow**: It receives one record and its stream description. If the record already has `id`, it returns it unchanged. Otherwise it looks up the Xero-specific ID field for that stream, reads the value if present, and returns a copy of the record with `id` added as text. If no suitable ID exists, it leaves the record unchanged.

**Call relations**: After `paginate` yields raw records from Xero, the general connector flow can call this to make records easier to key and store. It relies on the file’s ID-field map, such as `InvoiceID` for invoices or `AccountID` for accounts, so downstream code can use one consistent primary key name.

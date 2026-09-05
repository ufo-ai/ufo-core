# Finance, billing, spend, and document-commerce connectors  `stage-14.1.8`

This stage is a set of read-only “connectors” used during the system’s data-sync work. A connector is an adapter: it knows how to talk to one outside service, ask for records page by page, and reshape them into the standard streams the rest of the product can store, search, and recall.

The finance side covers Brex and Ramp for spend data like cards, expenses, vendors, receipts, budgets, and transfers; Mercury for bank accounts and transactions; QuickBooks and Xero for accounting records; and Stripe, Square, Chargebee, and Recurly for payments and subscription billing, including customers, invoices, subscriptions, payouts, orders, and related child records. The document-commerce side covers DocuSign and PandaDoc, pulling envelopes, templates, contacts, and documents.

Together, these files act like a row of translators at the edge of the system. Each understands one vendor’s API, including its paging rules and record IDs, but all produce a common flow of syncable records for the shared source-sync machinery.

## Files in this stage

### Spend and banking feeds
Corporate-card, spend-management, and banking connectors turn expenses, accounts, transactions, vendors, cards, bills, receipts, and transfers into read-only sync streams.

### `extensions/sources/ufo_ext_sources/providers/brex.py`

`io_transport` · `source sync / API fetching`

Brex exposes business spending data through web API endpoints, but the rest of this project wants a standard shape: named streams of records that can be fetched page by page. This file is the adapter between those two worlds. It defines which Brex objects are available, which API path to call for each one, what field identifies each record, and whether a stream has a date-like field that can be used as a progress marker.

The connector follows Brex’s paging style. Each API call asks for up to 100 records. Brex replies with an `items` list and, if there is more to read, a `next_cursor` token. The connector keeps calling the same endpoint with that token until Brex says there are no more pages. This is like reading a long book where each page tells you where to find the next page.

Most Brex endpoints do not let the connector ask only for recently changed records, so many syncs are effectively full reads. For transactions and expenses, the records include date fields that the wider system can use as a watermark, meaning a remembered “how far we got” marker.

If Brex rejects a stream because the OAuth credential lacks permission, the connector skips that stream instead of failing the whole source. Other HTTP errors are still allowed to bubble up as real failures.

#### Function details

##### `_stream`  (lines 41–52)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard stream description for one kind of Brex object. It keeps the stream definitions short and consistent, so the file can list Brex streams without repeating the same setup each time.

**Data flow**: It receives a stream name and optional details such as the primary key field, cursor field, and whether the stream is considered canonical. It packages those details into a `StreamSpec`, which is the project’s common description of a readable data stream. The result is a stream specification used later by the connector when syncing Brex data.

**Call relations**: The file uses this helper while building the Brex stream catalog. The helper hands off to `StreamSpec` to create the actual stream object that `BrexConnector` exposes through its `streams_list`.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 73–98)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches all pages for one Brex stream from the Brex API. It is used when the sync engine asks the connector to read records for a particular stream, such as expenses or vendors.

**Data flow**: It receives an HTTP client, a stream description, and a cursor value from the wider sync interface. It looks up the Brex API path for that stream, then repeatedly requests pages with a limit of 100 records. From each response, it takes the `items` list, safely treats missing or invalid item lists as empty, yields non-empty batches of records, and follows Brex’s `next_cursor` token until there are no more pages. If Brex returns a permission error, it turns that into a stream skip; otherwise, unexpected HTTP errors remain errors.

**Call relations**: The core source-sync flow calls this method when it needs records from Brex. During each page read, it relies on the connector’s HTTP get helper and on `list_or_empty` to normalize the returned `items`. If Brex refuses access with a 401 or 403 status, it creates a `StreamSkipped` error so the larger sync can move past that unavailable stream instead of treating it as a total connector failure.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/mercury.py`

`io_transport` · `source sync runs`

This file is the Mercury connector: the part of the system that knows how to talk to Mercury's banking API. Without it, the project could not pull Mercury accounts or transaction history into its common source-sync pipeline.

It defines two streams of data. The first is accounts, fetched in one request. The second is transactions, which are fetched account by account and page by page, because Mercury returns transactions in chunks. Think of it like checking every folder in a filing cabinet: first it lists the folders, then it opens each folder and reads every page inside.

For incremental syncs, it uses Mercury's `postedAt` date as a watermark, meaning “start from the last posting day we saw.” Mercury only accepts a date, not an exact position, so the connector re-reads that whole day. Duplicate records are expected to be removed later by the wider system.

The connector also handles authorization failures clearly. If Mercury returns 401 or 403, meaning the API key is invalid or lacks access, it raises `StreamSkipped` instead of pretending the stream is empty. Finally, transactions are rendered with their `counterpartyName` as the title, so recalled records are easier for people to recognize.

#### Function details

##### `MercuryConnector.paginate`  (lines 62–82)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Mercury streams. It decides whether to fetch accounts or transactions, then yields batches of records for the rest of the sync system to store or process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. For the accounts stream, it asks Mercury for all accounts and yields them once. For the transactions stream, it first fetches accounts, extracts their IDs, then fetches transaction pages for each account and adds the account ID as extra context to every transaction. If Mercury refuses access with 401 or 403, it turns that into a clear skipped-stream error; other HTTP errors keep bubbling up.

**Call relations**: The broader source-sync engine calls this when it wants records from Mercury. Inside, it relies on `_accounts` to get the account list, `_account_ids` to find usable account IDs, `_transactions` to walk through each account's transaction pages, and `with_context` to attach the account ID so downstream code knows where each transaction came from.

*Call graph*: calls 4 internal fn (__init__, _account_ids, _accounts, _transactions); 1 external calls (with_context).


##### `MercuryConnector._accounts`  (lines 84–86)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This small helper fetches the user's Mercury bank accounts. It hides the exact API path and returns a simple list for the rest of the connector to use.

**Data flow**: It takes an HTTP client, sends a GET request to Mercury's accounts endpoint, reads the `accounts` field from the response, and converts missing or non-list data into an empty list through `list_or_empty`. The result is a list of account dictionaries.

**Call relations**: `paginate` calls this whenever it needs account data. For the accounts stream, the returned list is the output. For the transactions stream, the returned accounts are passed through `_account_ids` so the connector can fetch transactions account by account.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector._account_ids`  (lines 89–94)

```
def _account_ids(accounts: list[dict[str, Any]]) -> list[str]
```

**Purpose**: This helper picks out valid account IDs from account records. It protects the transaction fetch from bad or missing IDs.

**Data flow**: It receives a list of account dictionaries. It looks at each account's `id`, keeps only IDs that are non-empty strings, and returns a clean list of those IDs. It does not change the original account records.

**Call relations**: `paginate` uses this after fetching accounts and before fetching transactions. The IDs it returns become the inputs to `_transactions`, one account at a time.

*Call graph*: called by 1 (paginate).


##### `MercuryConnector._transactions`  (lines 96–115)

```
async def _transactions(self, client: httpx.AsyncClient, account_id: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads all transaction pages for one Mercury account. It supports incremental syncing by asking Mercury to start from the date stored in the cursor.

**Data flow**: It receives an HTTP client, one account ID, and an optional cursor. It requests transactions with a fixed page size and an offset, adding a `start` date when a cursor exists. Each response's `transactions` field is normalized into a list and yielded if it has records. If a page has fewer records than the page size, it knows there are no more pages and stops; otherwise it increases the offset and asks for the next page.

**Call relations**: `paginate` calls this for every valid account ID when syncing the transactions stream. It hands each page back to `paginate`, which then adds account context before yielding the records onward to the source-sync system.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector.render`  (lines 117–126)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function chooses how a Mercury record should appear when shown or recalled later. For transactions, it uses the counterparty name as the title because that is the most recognizable label for a payment.

**Data flow**: It receives one record and the stream it came from. If the record is a transaction and has a usable `counterpartyName`, it returns that name as the title plus a Markdown-style body containing the full JSON record. For all other cases, it falls back to the standard rendering behavior from the base connector.

**Call relations**: The wider source system calls this when it needs a readable representation of a synced record. This function only customizes transaction display; everything else is handed back to the parent `RestConnector` rendering logic.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/ramp.py`

`io_transport` · `source sync polling`

Ramp is a finance platform, and its API exposes many kinds of company spend records. This file is the Ramp connector: the adapter that knows which Ramp web addresses to call, how Ramp paginates long lists, and how to describe each kind of Ramp object to the shared source-sync machinery.

The file defines a list of streams, where a stream means “one type of thing to sync,” such as transactions or departments. Most Ramp streams are fully re-read each run. Transactions are different: Ramp can filter them by time, so this connector asks only for transactions after the last saved cursor when possible. That makes transaction syncing incremental, like continuing from a bookmark instead of rereading the whole book.

Ramp returns list results in pages. Each response contains a data list and, sometimes, a link to the next page. The connector follows that next link until there are no more pages. If Ramp refuses access with a 401 or 403 status, the connector treats that stream as skipped rather than crashing the whole sync. This matters because some Ramp grants may not include every data type, such as bills or reimbursements.

The file also customizes how transactions are shown: their useful title is the merchant name, so a coffee shop charge can be recalled by the shop name rather than by raw JSON alone.

#### Function details

##### `_stream`  (lines 50–66)

```
def _stream(name: str, *, cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates the standard description for one Ramp stream. It saves the repeated setup needed to tell the sync system a stream’s name, unique ID field, time fields, and whether it is considered a main canonical source.

**Data flow**: It receives a stream name and optional details such as which field acts as the time cursor. It packages those choices into a StreamSpec object. The result is a small blueprint that the connector later uses to know how to sync that kind of Ramp record.

**Call relations**: This helper is used while the file is being loaded to build the Ramp stream list. It hands each completed StreamSpec to the connector class through RAMP_STREAMS, so later sync work can loop over the known Ramp data types.

*Call graph*: 1 external calls (__init__).


##### `RampConnector._next_path`  (lines 97–104)

```
def _next_path(next_link: Any) -> str | None
```

**Purpose**: This helper converts Ramp’s next-page link into the form needed by the HTTP client used here. Ramp gives a full web address, but the connector’s client is already tied to Ramp’s base address, so it only needs the path and query part.

**Data flow**: It receives a possible next-page value from Ramp’s response. If the value is not a useful string, it returns nothing. If it is a real URL, it parses it and returns just the path, plus the query string when present, so the next request can be made correctly.

**Call relations**: RampConnector.paginate calls this after each page is fetched. It acts like reading the “continued on page…” note at the bottom of a page and turning it into the exact next request to make.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RampConnector.paginate`  (lines 106–134)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading loop for Ramp list endpoints. It asks Ramp for one page of records at a time, yields each non-empty batch to the sync system, and follows Ramp’s next-page link until the stream is finished.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It chooses the right Ramp endpoint for that stream, adds page-size settings, and for transactions may add a from-date so Ramp only returns newer records. It repeatedly fetches JSON from Ramp, extracts the data list safely, yields records in batches, and then moves to the next page if Ramp provides one. If Ramp refuses access with a permission-related status, it raises StreamSkipped so the run records that this stream was unavailable instead of treating it as a system failure.

**Call relations**: The shared RestConnector machinery calls this when it is time to fetch a Ramp stream. Inside the loop it uses RampConnector._next_path to follow pagination and list_or_empty to make sure the response’s data field is treated as a list. When access is denied, it hands control back by raising StreamSkipped with a clear explanation.

*Call graph*: calls 2 internal fn (__init__, _next_path); 1 external calls (list_or_empty).


##### `RampConnector.render`  (lines 136–146)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function decides how a Ramp record should appear when it is turned into recallable text. It gives transactions a human-friendly title based on the merchant name, because that is usually what a person remembers about a charge.

**Data flow**: It receives one Ramp record and its stream description. If the record is a transaction and has a non-empty merchant_name, it returns that merchant name as the title and a JSON body containing the full record. For all other cases, it falls back to the general rendering behavior from the parent connector.

**Call relations**: The broader source-sync system calls this after records are fetched and need to be represented as searchable or recallable content. This function only special-cases Ramp transactions; everything else is handed off to the base RestConnector rendering logic.

*Call graph*: 1 external calls (dumps).


### Subscription billing feeds
Subscription-billing connectors page through customer, subscription, invoice, transaction, and related billing records for downstream storage and recall.

### `extensions/sources/ufo_ext_sources/providers/chargebee.py`

`io_transport` · `during source sync / stream reading`

Chargebee is an online billing system, and its API exposes many kinds of data through list endpoints. This file is the read-only connector for that API. Without it, the project would not know which Chargebee endpoints exist, how to authenticate, how to walk through pages of results, or how to reshape Chargebee’s wrapped records into a simpler form.

The file first defines the available streams, which are the named sets of records that can be synced, such as "customer", "invoice", or "subscription". Most streams map directly to one Chargebee list endpoint. A few are substreams: they only exist under a parent record, like contacts under a customer or attached items under an item. For those, the connector first reads the parent records, then asks Chargebee for each parent’s children, like checking every folder in a filing cabinet for its smaller documents.

Chargebee uses cursor-style pagination, where each response may include a `next_offset` token telling the client how to fetch the next page. The connector follows that token until there is no next page. For incremental syncs, it can also ask Chargebee for records after a stored cursor value, usually a timestamp.

Authentication is done with HTTP Basic authentication, using the API key as the username and an empty password. If Chargebee rejects access with a permission or authentication error, the stream is skipped with a clear reason instead of crashing unclearly.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a `StreamSpec`, which is the project’s small description of one syncable Chargebee record type. It avoids repeating the same setup details for every stream, while still allowing special cases like a different cursor field or primary key.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, cursor field, and timestamp fields. It fills in sensible defaults when those details are not provided, then returns a `StreamSpec` object that the connector later uses to know how to sync that stream.

**Call relations**: This function is used while the file is loaded to build the `CHARGEBEE_STREAMS` list. It hands each completed stream description to the connector class through `streams_list`, so later sync code can ask the connector what Chargebee data is available.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Chargebee. It makes sure requests have the right base address, time limits, headers, and authentication.

**Data flow**: It receives a base URL and a resolved credential. It trims extra slashes from the base URL, sets JSON/form-related headers, sets connection and read timeouts, and then chooses how to authenticate. If the credential provides a custom transport, it preserves that. If it provides a direct API key, it uses that key as HTTP Basic authentication. It returns an asynchronous HTTP client ready to make Chargebee API calls, or raises an error if no usable authentication is present.

**Call relations**: The broader `RestConnector` flow calls this when it needs a network client for a sync run. This function hands back the prepared `httpx.AsyncClient`, which the pagination methods then use to fetch Chargebee pages.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function removes Chargebee’s extra wrapper around each record so downstream code can read the actual fields directly. For example, it turns a record shaped like `{customer: {...}}` into just the customer fields.

**Data flow**: It receives one raw record and the stream description that says which wrapper key to expect. If the expected wrapper contains a dictionary, it copies the inner record and also preserves any extra top-level fields, such as a parent ID added by a substream. It returns the flattened record. If the wrapper is not present, it returns the record unchanged.

**Call relations**: After pagination has fetched raw Chargebee records, the connector framework can call this before storing or emitting them. It does not call other project functions; it is the cleanup step that makes Chargebee’s envelope format look like normal records to the rest of the system.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main routing point for reading records from a Chargebee stream. It decides whether a stream can be fetched from a normal list endpoint or needs a special parent-child walk.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It checks the stream name, calls the matching pagination method, and yields pages of raw records as they arrive. If Chargebee returns a 401 or 403 response, meaning unauthorized or forbidden, it converts that into a clear `StreamSkipped` error. If the stream name has no known strategy, it raises an implementation error.

**Call relations**: The source sync engine calls this when it wants pages for one stream. This function then hands work to `_paginate_list` for ordinary streams, or to `_paginate_attached_items`, `_paginate_contacts`, `_paginate_quote_line_groups`, or `_paginate_subscription_scheduled` for substreams. It is the traffic controller for all Chargebee read paths in this file.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for a Chargebee list request. It sets the page size and, when possible, adds the incremental-sync filter that asks for only records after the saved cursor.

**Data flow**: It receives the stream description and an optional cursor value. It always starts with a `limit` value of 100. If a cursor is present and the stream has a cursor field, it adds a Chargebee-style filter such as `updated_at[after]=...`. It returns the completed parameter dictionary.

**Call relations**: `_paginate_list` calls this just before requesting pages from a normal Chargebee list endpoint. The result is passed into the shared cursor-page fetching helper supplied by the base connector.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal Chargebee list endpoint, page by page. It is used for streams that map directly to endpoints like `/customers`, `/invoices`, or `/transactions`.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for the stream, builds the request parameters, then asks the base connector to follow Chargebee’s `next_offset` pagination. Each time a page of records is returned, it yields that page unchanged for later flattening and processing.

**Call relations**: `paginate` calls this for ordinary streams. The substream methods also call it first to get parent records, such as customers before contacts or items before attached items. It relies on `_build_list_params` for the initial request parameters and on the base connector’s cursor-page helper to do the repeated HTTP fetching.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads attached items, which are not available as one global list. It first reads items, then fetches the attached items under each individual item.

**Data flow**: It receives an HTTP client and an optional cursor. It uses the normal item stream to fetch item pages. For each item with an ID, it requests `/items/{item_id}/attached_items`. Each child record is stamped with the parent `item_id`, then pages of attached-item records are yielded.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This function uses `_paginate_list` to find the parent items and `_paginate_substream` to walk each item’s child endpoint while adding the parent ID.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads customer contacts, which live underneath individual customers in Chargebee. It walks through customers first, then fetches the contacts for each customer.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches customer pages using the normal customer list. For each customer that has an ID, it requests `/customers/{customer_id}/contacts`. It adds the `customer_id` to each contact record and yields the resulting child pages.

**Call relations**: `paginate` calls this for the `contact` stream. It depends on `_paginate_list` for the parent customer records and `_paginate_substream` for the repeated child-page fetching.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads quote line groups, which are child records attached to individual quotes. It finds quotes first, then asks Chargebee for the line groups under each quote.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches quote pages, extracts each quote ID, and skips any parent that does not have one. For each valid quote, it reads `/quotes/{quote_id}/quote_line_groups`, adds the `quote_id` to each child record, and yields the child pages.

**Call relations**: `paginate` calls this when syncing `quote_line_group`. It uses `_paginate_list` to get parent quotes and `_paginate_substream` to read each quote’s child endpoint in the same paged style.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads the scheduled-change version of each subscription. Unlike normal list streams, this is a one-at-a-time detail endpoint under each subscription.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches subscription pages, extracts each subscription ID, and then calls Chargebee’s `/subscriptions/{id}/retrieve_with_scheduled_changes` endpoint for that subscription. If the response contains a subscription object, it yields a one-record page containing that subscription plus the parent `subscription_id`.

**Call relations**: `paginate` calls this for `subscription_with_scheduled_changes`. It uses `_paginate_list` to find parent subscriptions, then directly calls the base connector’s single-request helper to fetch each detailed scheduled-change record.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the shared helper for child endpoints that use Chargebee’s normal `list` plus `next_offset` page shape. It also adds the parent record’s ID to every child record so the relationship is not lost.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent-ID field to add, and the parent ID value. It requests pages from that child endpoint with the standard page size. For every dictionary-shaped record in each page, it copies the record, adds the parent ID field, and yields the stamped page if it contains records.

**Call relations**: The attached-item, contact, and quote-line-group paginators call this after they have found a parent record. It centralizes the repeated child-pagination pattern so each substream method only has to decide which parent stream and endpoint path to use.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/providers/recurly.py`

`io_transport` · `source sync run`

Recurly returns lists of records in pages, much like a search result page that says “next” when there are more results. This file wraps that behavior so the rest of the system can ask for streams such as accounts, subscriptions, invoices, coupons, and account notes without knowing Recurly’s API details.

The file first defines the available streams and their basic rules: what the stream is called, which field identifies a record, and which time field can be used to resume from a previous sync. The RecurlyConnector then builds an HTTP client with Recurly’s required headers and authentication. Recurly uses HTTP Basic authentication, where the API key is sent as the username, rather than the more common bearer token style.

The main work is pagination. For normal streams, the connector requests the first page, yields the records, follows Recurly’s next link, and repeats until Recurly says there are no more pages. Some streams are “per parent”: for example, account notes live under each account. For those, the connector first walks through all accounts or bulk coupons, then asks Recurly for each parent’s child records, adding the parent ID onto each child row so the relationship is not lost.

If Recurly rejects access with a 401 or 403 response, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This small helper creates a StreamSpec, which is the system’s description of one Recurly data stream. It keeps the stream list readable by filling in common defaults such as using id as the primary key and updated_at as the resume field.

**Data flow**: It receives a stream name plus optional settings such as the Recurly object path, primary key, cursor field, and whether the stream is canonical. It combines those choices with defaults and returns a StreamSpec object that the connector later uses to decide what to request and how to resume.

**Call relations**: This helper is used while the file is being loaded to build RECURLY_STREAMS. It hands the completed stream descriptions to the RecurlyConnector class through streams_list, so later sync code can ask the connector to read each stream.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Recurly. It matters because Recurly needs a specific API version header, sensible time limits, and authentication in the format Recurly expects.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares headers that pin the Recurly API version, and sets connection and read timeouts. If the credential includes a proxy transport, it builds a client using that transport. Otherwise, if it has an API key-like bearer value, it sends that value as the username in HTTP Basic authentication. If no usable credential is present, it raises an error instead of making unauthenticated requests.

**Call relations**: The broader RestConnector machinery calls this when it needs a network client for a sync. This function hands back an httpx AsyncClient, which the pagination functions then use for all Recurly requests.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This function turns Recurly’s next-page link into a path the client can request. It protects the connector from caring whether Recurly returns a full URL or just a relative path.

**Data flow**: It receives a next link, which may be empty, a full URL, or a path. If there is no link, it returns nothing. If the link is a full URL, it keeps only the path and query string, because the client already knows the base Recurly host. If the link is already a path, it returns it unchanged.

**Call relations**: All pagination loops call this after reading a page that says more data exists. It gives _paginate_top_level, _account_ids, _coupon_ids, and _paginate_per_parent the next address to request.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This function builds the query parameters for the first request of a stream. These parameters tell Recurly how many records to return, how to sort them, and, for incremental syncs, where to start.

**Data flow**: It receives a StreamSpec and an optional cursor value from a previous sync. It creates a parameter dictionary with the page size, ascending order, and the stream’s cursor field, or created_at if the stream has no cursor field. If both a cursor field and cursor value exist, it adds begin_time so Recurly only returns records from that point onward.

**Call relations**: The top-level and per-parent pagination functions call this before their first request. After that first request, they stop sending these initial parameters and follow Recurly’s own next links instead.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Recurly stream page by page. It chooses the right paging strategy for ordinary streams, child streams under accounts or coupons, and the special bulk-coupon parent stream.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. If the stream is one of the child streams, it delegates to the per-parent paginator and yields each child page. If the stream is unique_coupons_parent, it reads coupons and only yields bulk coupons. Otherwise, it builds a normal top-level Recurly path and yields pages from the top-level paginator. If Recurly responds with 401 or 403, it converts that into StreamSkipped with a readable explanation; other HTTP errors are allowed to continue upward.

**Call relations**: The sync framework calls this when it wants records for a stream. This function is the traffic director: it hands normal streams to _paginate_top_level, parent-child streams to _paginate_per_parent, and turns permission failures into a controlled skip.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal Recurly collection, such as accounts or invoices, one page at a time. It is the basic “keep following next until done” loop.

**Data flow**: It receives a client, a stream description, a starting path, and an optional cursor. It builds the first query with _initial_query, requests the page, yields the list of records if any are present, then checks Recurly’s has_more flag. If more pages exist, it uses _next_path to find the next request path and repeats. When there are no more pages, it stops.

**Call relations**: RecurlyConnector.paginate calls this for most streams and also for the coupon parent stream. It relies on _initial_query for the first request and _next_path for every follow-up page.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function walks through all Recurly accounts and yields only their IDs. It exists because several child streams, like account notes and billing info, must be requested separately for each account.

**Data flow**: It starts at the /accounts endpoint with a fixed page size and sorting order. For each returned account row, if the row is a dictionary-like record and has an id, it yields that id as text. It follows Recurly’s next links until there are no more account pages.

**Call relations**: _paginate_per_parent calls this when it needs to visit account-based child resources. This function supplies the parent IDs that become part of the child request URLs.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This function walks through Recurly coupons and yields the IDs of bulk coupons only. It is used because unique coupon codes are children of bulk coupon records, not of every coupon.

**Data flow**: It starts at the /coupons endpoint with a fixed page size and sorting order. For each coupon row, it skips anything that is not a record, has no id, or is not marked as coupon_type bulk. For matching rows, it yields the coupon id as text. It follows next links until Recurly says there are no more coupon pages.

**Call relations**: _paginate_per_parent calls this for coupon-based child streams. It supplies only the coupon IDs that can have unique coupon code children, avoiding unnecessary child requests for non-bulk coupons.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads streams that live underneath another Recurly object, such as notes under each account or unique coupon codes under each bulk coupon. It preserves the parent-child relationship by adding the parent ID to each child record when needed.

**Data flow**: It receives the client, stream description, parent path, child path, field name used to store the parent ID, and optional cursor. It first chooses the right parent ID source: account IDs for account children, or bulk coupon IDs for coupon children. For each parent ID, it builds the child URL, sends the initial query, yields each page of child records, and stamps the parent ID onto dictionary records if that field is missing. It follows Recurly’s next links for that parent before moving to the next parent.

**Call relations**: RecurlyConnector.paginate calls this for the streams listed as per-parent streams. This function depends on _account_ids or _coupon_ids to find parents, uses _initial_query for the first child request, and uses _next_path to continue through child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### Business document feeds
Document-commerce connectors import envelopes, templates, documents, and contacts so signed or generated business documents can be searched later.

### `extensions/sources/ufo_ext_sources/providers/docusign.py`

`io_transport` · `source sync runs`

DocuSign stores customer data behind different regional API addresses, so this connector cannot use one fixed data host. It first asks DocuSign’s shared identity service which account the current authorization belongs to, then builds the correct account-specific API address from that answer. This is like checking a hotel directory before going to the right room: the front desk is shared, but the actual room depends on the guest.

The file defines two streams: envelopes and templates. Envelopes are the sent signing packets, and templates are reusable signing setups. When syncing envelopes, it asks DocuSign for records changed since the last saved cursor. If there is no cursor yet, it starts from a very old date so the first sync gets the full available history instead of only recent items. It also asks DocuSign to include recipients and custom fields, so each envelope record contains useful context about who signed and what extra fields were attached.

The connector reads results in pages of 100 until DocuSign returns fewer than 100, which means there is no next page. If DocuSign refuses access with an authorization error, the stream is skipped with a clear explanation instead of crashing the whole sync. The file deliberately has no write behavior; it only reads from DocuSign.

#### Function details

##### `DocuSignConnector._account_base`  (lines 79–109)

```
async def _account_base(self, client: httpx.AsyncClient) -> str
```

**Purpose**: Finds the correct DocuSign account API address for the current authorization. This matters because DocuSign accounts live on different regional hosts, and using the wrong host would make later data requests fail or read the wrong place.

**Data flow**: It receives an HTTP client that already knows how to authenticate. It asks DocuSign’s identity endpoint for user information, reads the accounts listed there, keeps only accounts with both an account ID and a base URL, then chooses the only usable account or the one marked as the default. It returns a full base URL for later envelope and template requests; if there is no usable account, or several accounts with no single default, it raises a clear stream fault instead of guessing.

**Call relations**: During pagination, DocuSignConnector.paginate calls this first so it knows where to send the real data requests. It relies on list_or_empty to safely treat missing or malformed account lists as empty lists, and it raises StreamFault when the grant cannot be tied to exactly one account.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `DocuSignConnector.paginate`  (lines 111–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one DocuSign stream, such as envelopes or templates, page by page. It is the main reader that turns DocuSign’s paged API responses into batches of records for the sync system.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor showing where the last sync left off. It looks up the right DocuSign list endpoint, asks _account_base for the correct account URL, then repeatedly requests up to 100 records at a time. For envelopes, it adds the cursor date, recipient and custom-field inclusion, and ascending order. It yields each non-empty batch of records and stops when a short page shows there are no more results. If DocuSign replies with an access refusal, it changes that into a StreamSkipped message explaining that the grant lacks permission.

**Call relations**: This is the function the source-sync machinery depends on when it wants records from DocuSign. It first hands off account discovery to DocuSignConnector._account_base, uses list_or_empty to normalize each API response into a safe record list, and reports authorization refusals through StreamSkipped so the larger sync can continue cleanly.

*Call graph*: calls 2 internal fn (__init__, _account_base); 1 external calls (list_or_empty).


##### `DocuSignConnector.render`  (lines 145–154)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Turns a raw DocuSign record into a readable page title and body. It gives envelopes a human-friendly title based on the email subject that recipients saw.

**Data flow**: It receives one DocuSign record and the stream it came from. If the record is an envelope with a non-empty emailSubject, it uses that subject as the page title and writes the full record as sorted JSON in the page body. For templates or envelopes without a usable subject, it falls back to the standard rendering behavior from the parent connector.

**Call relations**: After records have been fetched, the broader source system can call this to decide how each record should appear as a recallable page. It uses json.dumps to include the complete original record in a stable text form, while only customizing envelope titles where DocuSign’s useful title field has a provider-specific name.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/pandadoc.py`

`io_transport` · `sync polling`

PandaDoc is an online document service. This connector is the bridge between PandaDoc and the rest of the system. Without it, the system would not know which PandaDoc web addresses to call, how to authenticate, how to page through long result lists, or how to fetch the richer details for each document.

The file defines three streams: documents, templates, and contacts. A stream is a named feed of records that the sync engine can ask for. Documents are treated specially because PandaDoc supports incremental syncing for them: the connector can ask only for documents changed after a saved timestamp, instead of rereading everything every time.

When syncing, the connector requests PandaDoc list pages in batches of 100. For documents, the list response is not enough; it mostly contains status and dates. So each document is then “hydrated,” meaning the connector fetches its detail page to add useful information such as fields, tokens, pricing, and recipients. If one listed document cannot be opened because access is denied or it is missing, the connector keeps the basic list record and continues. This prevents one bad record from breaking the whole sync.

Authentication also has a PandaDoc-specific detail: member-provided keys use PandaDoc’s `API-Key` authorization scheme, not the more common bearer-token scheme.

#### Function details

##### `_stream`  (lines 37–51)

```
def _stream(name: str, *, cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of PandaDoc record, such as documents or contacts. The description tells the sync system the stream name, its unique ID field, and which date fields represent creation and updates.

**Data flow**: It takes a stream name plus optional settings for the update cursor and whether the stream is canonical. It packages those choices into a `StreamSpec`, which is a small description object the rest of the source system can understand. The result is returned and later collected into the PandaDoc stream list.

**Call relations**: At import time, this helper is used to build the connector’s stream definitions. It hands the actual stream description creation to `StreamSpec.__init__`, so the rest of the file can declare streams in a compact, consistent way.

*Call graph*: 1 external calls (__init__).


##### `PandaDocConnector._make_client`  (lines 66–72)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client that will talk to PandaDoc. Its main PandaDoc-specific job is to put a member-supplied API key into the exact authorization header format PandaDoc expects.

**Data flow**: It receives a base API address and a resolved credential. First it asks the parent connector to create the normal asynchronous HTTP client. If the credential contains a bearer value, this method rewrites that into an `Authorization` header using PandaDoc’s `API-Key` scheme. It returns the ready-to-use client, with broker-provided authentication left alone when the broker transport is already responsible for it.

**Call relations**: This method fits into the connector setup phase. The broader `RestConnector` flow asks for a client before making API calls, and this override adds the PandaDoc-specific authentication detail before pagination or detail fetching begins.


##### `PandaDocConnector.paginate`  (lines 74–103)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for PandaDoc list endpoints. It walks through pages of documents, templates, or contacts and yields batches of records for the sync engine.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor timestamp. It chooses the correct PandaDoc endpoint for the stream, then repeatedly asks for pages of up to 100 records. For documents, it also adds sorting and an optional `modified_from` filter so only changed documents are read, then expands each document by calling `_details`. Each non-empty batch is yielded outward. When a short page arrives, it means there are no more records, so the function stops. If PandaDoc refuses access with a 401 or 403 status, it turns that into `StreamSkipped`, which tells the sync system to skip this stream instead of crashing the whole run.

**Call relations**: The sync engine calls this during a PandaDoc sync whenever it needs records for a stream. Inside the loop, it uses `list_or_empty` to safely treat the API’s `results` field as a list. For document records, it hands each row to `PandaDocConnector._details` so the batch contains fuller, more useful records before being yielded back to the sync flow.

*Call graph*: calls 2 internal fn (__init__, _details); 1 external calls (list_or_empty).


##### `PandaDocConnector._details`  (lines 105–117)

```
async def _details(self, client: httpx.AsyncClient, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This fetches the full detail record for a single PandaDoc document. It fills in information that the document list endpoint does not include, while tolerating documents that the account can list but not open.

**Data flow**: It receives the HTTP client and one document list record. It looks for a valid string `id`; if there is no usable ID, it simply returns the original record. If there is an ID, it requests that document’s details from PandaDoc. A successful response is merged over the original list record, producing one richer record. If PandaDoc says the detail page is forbidden or missing, the original list record is returned unchanged. Other HTTP errors are allowed to rise because they may indicate a real sync problem.

**Call relations**: This function is called by `PandaDocConnector.paginate` while reading the documents stream. It acts like a second pass: pagination finds the document rows, then `_details` enriches each one before pagination yields the final batch to the rest of the system.

*Call graph*: called by 1 (paginate).


### Accounting ledger feeds
Accounting connectors read query-based or tenant-scoped ledger data from online accounting systems as standardized syncable streams.

### `extensions/sources/ufo_ext_sources/providers/quickbooks.py`

`io_transport` · `source sync`

QuickBooks Online does not offer a simple “give me all invoices” style endpoint for each record type. Instead, every read is a SQL-like query sent to one shared `/query` endpoint. This file wraps that awkward shape in a connector the rest of the system can use like a normal data source.

It first defines the QuickBooks streams: accounts, customers, invoices, bills, payments, journal entries, and many others. A stream is a named kind of data the sync system can fetch. Most streams use `Id` as the unique key and `MetaData.LastUpdatedTime` as the update cursor, meaning the system can ask, “only send records changed after this time.” A few reference lists, such as payment methods and tax agencies, do not use that cursor and are read as full refreshes.

The `QuickBooksConnector` builds QuickBooks queries, sends them page by page, and stops when QuickBooks returns fewer than the page size. If QuickBooks refuses access with a 401 or 403 status, the connector marks that stream as skipped instead of treating the whole sync as a mystery failure. It also flattens the nested update timestamp into a simple field name, so the wider sync engine can track progress consistently.

#### Function details

##### `_stream`  (lines 32–47)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one QuickBooks data stream, such as invoices or vendors. It keeps the repeated stream settings in one place so each stream can be declared with only the details that differ.

**Data flow**: It receives a friendly stream name, the QuickBooks object name, and optional cursor and canonical flags. It fills in shared choices such as the primary key `Id`, the created-time field, and the updated-time field, then returns a `StreamSpec`, which is the sync system’s recipe for reading that kind of record.

**Call relations**: When the module builds the list of QuickBooks streams, it calls `_stream` repeatedly. `_stream` hands each completed recipe to `StreamSpec.__init__`, which creates the stream description used later by the connector.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 88–95)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This builds the SQL-like text query that QuickBooks expects. It is used to ask QuickBooks for one page of one record type, optionally limited to records updated after a saved cursor.

**Data flow**: It takes a stream description, an optional cursor value, and a starting row number. It turns those into a query such as selecting all rows from a QuickBooks object, adding a `WHERE` and `ORDER BY` clause when incremental syncing is possible, and adding the page size and starting position. The output is a single query string ready to send to QuickBooks.

**Call relations**: `QuickBooksConnector.paginate` calls this each time it needs the next page of records. `_build_query` does not contact QuickBooks itself; it only prepares the question that `paginate` will send.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 97–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one QuickBooks stream page by page. It hides QuickBooks’ paging rules from the rest of the sync system and yields batches of records until there are no more.

**Data flow**: It starts with a QuickBooks HTTP client, a stream description, and an optional cursor from the previous sync. For each page, it builds a query, sends it to `/query`, opens the `QueryResponse` wrapper in the response, and extracts the records for the requested entity. It yields each non-empty batch, advances the starting position by 100, and stops when a page has fewer than 100 records. If QuickBooks replies with 401 or 403, it changes that failure into a `StreamSkipped` error with a clear explanation.

**Call relations**: During a sync, this is the connector’s main reading loop. It relies on `QuickBooksConnector._build_query` to create each QuickBooks query. If QuickBooks refuses a stream, it calls `StreamSkipped.__init__` so the larger source runtime can understand that this stream was deliberately skipped because access was denied.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 121–124)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This adjusts each QuickBooks record so the sync engine can easily find the update timestamp. QuickBooks stores that timestamp inside nested `MetaData`, but the sync engine tracks cursors more easily with a flat field name.

**Data flow**: It receives one record and the stream description. If the stream’s cursor field is a dotted path like `MetaData.LastUpdatedTime`, it reads that nested value from the record and returns a copy of the record with an extra flat key using the same dotted name. If no dotted cursor is needed, it returns the record unchanged.

**Call relations**: This function is used after records are fetched, before cursor progress is tracked. It delegates the nested lookup to `get_path`, which knows how to follow a dotted path through a dictionary-like record.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/xero.py`

`io_transport` · `during source sync, while fetching and normalizing Xero records`

Xero is an accounting service, but its API does not look exactly like the generic source system expects. This file is the adapter between the two. It lists the Xero resources that can be read, such as accounts, contacts, invoices, and payments, and describes how each should appear to the rest of the system.

The main job is to safely fetch pages of records from Xero. Some Xero endpoints return 100 rows at a time and need repeated requests with page numbers. Others return everything in one response. For incremental sync, meaning “only fetch things changed since last time,” Xero expects a special HTTP header called If-Modified-Since rather than a URL parameter, so this file converts saved cursor values into the date format Xero accepts.

Xero also requires a tenant ID, which identifies the specific organisation inside a user’s Xero grant. If the connector was not given one directly, it asks Xero which organisations the grant covers. If there is none, or more than one, it stops with a clear fault instead of silently syncing the wrong company’s books.

Finally, Xero records use names like AccountID or InvoiceID instead of a common id field. The flatten step copies the right Xero-specific ID into id so the rest of the sync system can treat all streams consistently.

#### Function details

##### `_stream`  (lines 75–89)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard stream description for one Xero resource. A stream description tells the sync system what the resource is called, what field identifies each record, and whether it can be synced incrementally.

**Data flow**: It receives a friendly stream name, the Xero API object name, an optional cursor field, and a flag saying whether this is a main/common stream. It packages those choices into a StreamSpec object. The result is later used as the recipe for reading that Xero resource.

**Call relations**: This helper is used when the file builds the XERO_STREAMS list at import time. It hands its settings to StreamSpec.__init__, which creates the formal stream object the connector exposes.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 117–134)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: Converts a saved sync cursor into the date format Xero expects for “only send me records changed since this time.” RFC 1123 is a standard HTTP date style, like the timestamp format used in many web headers.

**Data flow**: It takes a cursor value that may be empty, a Unix timestamp number, an ISO-style date string, or already-formatted text. Empty or unusable values become None. Valid numeric or ISO date values are converted to UTC and returned as a GMT HTTP-date string; text it cannot parse is returned unchanged.

**Call relations**: XeroConnector.paginate calls this before making Xero requests for streams that support incremental sync. Internally it uses datetime.fromtimestamp for numeric cursors and datetime.fromisoformat for ISO date strings.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 142–143)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: Creates a Xero connector instance and optionally remembers the tenant ID for the Xero organisation to read. The tenant ID matters because one Xero login can grant access to more than one organisation.

**Data flow**: It receives an optional tenant_id. It stores that value on the connector. Nothing is fetched yet; this only records whether later API clients should be preloaded with the Xero organisation header.

**Call relations**: This runs when the connector object is constructed. Later, _make_client and _ensure_tenant use the stored tenant choice to decide whether the API client already knows which Xero organisation to read.


##### `XeroConnector._make_client`  (lines 145–149)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Xero and adds the tenant header if the tenant was already known. An HTTP client is the object that sends web requests and keeps common settings like headers.

**Data flow**: It receives the base URL and credential, asks the parent RestConnector to create the normal authenticated client, then adds the xero-tenant-id header when this connector was initialized with a tenant. It returns the prepared client.

**Call relations**: This overrides the standard client creation step from RestConnector. It prepares the client that paginate later uses for actual Xero API calls, avoiding an extra tenant lookup when the tenant was supplied up front.


##### `XeroConnector._ensure_tenant`  (lines 151–179)

```
async def _ensure_tenant(self, client: httpx.AsyncClient) -> None
```

**Purpose**: Makes sure the HTTP client has the Xero organisation header required for accounting API calls. If the organisation was not already chosen, it asks Xero which organisations the credential can access and accepts only a single clear match.

**Data flow**: It receives an HTTP client. If the client already has xero-tenant-id, it leaves it alone. Otherwise it requests Xero’s connections endpoint, reads the returned connection list, filters it to organisation tenants with tenant IDs, and sorts the result. If there are no organisations, it raises a StreamFault. If there are several, it also raises a StreamFault to avoid choosing the wrong company. If there is exactly one, it writes that tenant ID into the client headers.

**Call relations**: XeroConnector.paginate calls this before reading any Xero stream. It uses list_or_empty to safely treat the response as a list, and raises StreamFault when the grant cannot identify exactly one organisation.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `XeroConnector.paginate`  (lines 181–217)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches records from one Xero stream, one batch at a time. It knows which streams are paged, which come back all at once, how to add incremental-sync headers, and how to turn authorization refusals into a skipped stream instead of a crash.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero endpoint path from the stream’s source object. If the stream has a cursor and a cursor value was provided, it converts that cursor into an If-Modified-Since header. It ensures the client has a tenant ID, then sends GET requests to Xero. For non-paged streams it makes one request and yields the records if any exist. For paged streams it starts at page 1, yields each non-empty page, and stops when Xero returns no records or fewer than the page size. If Xero returns 401 or 403, it raises StreamSkipped with a useful message.

**Call relations**: This is the connector’s main read loop for Xero data. It calls _ensure_tenant before requests, _cursor_to_rfc1123 when incremental sync is possible, and httpx.AsyncClient.get to make the web calls. When Xero refuses access, it creates a StreamSkipped error so the broader sync can treat that stream as unavailable rather than pretending the data was read.

*Call graph*: calls 3 internal fn (__init__, _ensure_tenant, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 219–228)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Adds a common id field to Xero records when they only have Xero-specific ID names. This lets the rest of the sync system identify records consistently across all Xero streams.

**Data flow**: It receives one record and its stream description. If the record already has id, it returns the record unchanged. Otherwise it looks up the correct Xero ID field for that stream, such as InvoiceID for invoices. If that field exists in the record, it returns a copy of the record with id set to that value as text. If no suitable ID is found, it returns the original record.

**Call relations**: This fits after records have been fetched by paginate and before they are stored or compared by the generic sync machinery. It uses the file’s stream-to-ID mapping so downstream code can rely on the standard primary key named id.


### Payment commerce feeds
Payment and point-of-sale connectors normalize customers, invoices, payments, orders, payouts, checkout details, catalog records, and inventory into recallable source streams.

### `extensions/sources/ufo_ext_sources/providers/square.py`

`io_transport` · `source sync`

Square exposes its data through several different web API patterns. Some lists are fetched with simple GET requests, some require POST search requests, and some need special steps such as looking up locations before searching orders. This file hides those differences behind one connector, so the rest of the system can ask for a stream like “payments” or “orders” without knowing Square’s details.

The file defines the Square streams the system knows about, including each stream’s name, source object, main identifier, and time field used for incremental syncing. Incremental syncing means “only fetch things newer than the last saved point,” like resuming a book from a bookmark instead of starting again at page one.

`SquareConnector` adds Square-specific behavior to a shared REST connector. It pins the Square API version in a request header, chooses the right fetching method for each stream, follows Square cursors for multi-page results, and filters records by time when needed. If Square refuses access because the token is invalid or missing permissions, the connector raises `StreamSkipped` so the sync can skip that stream cleanly instead of crashing the whole run. This file is read-only by design; it does not create or update Square data.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Square and adds the required Square API version header. Someone would use it when starting a Square sync so every request speaks the expected Square API version.

**Data flow**: It receives a base web address and a credential. It asks the shared REST connector to build the basic authenticated client, then adds the `Square-Version` header. It returns the prepared client, ready to make Square API calls.

**Call relations**: This fits into the setup step before any records are fetched. The wider source framework relies on it to prepare the client, and the rest of the connector’s methods then use that client for GET and POST requests.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main dispatcher for reading Square streams page by page. It looks at the requested stream name and chooses the correct Square-specific fetching path.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor that marks where a previous sync left off. It routes the request to the right helper: locations, cursor-based GET lists, catalog search, order search, or inventory counts. It yields lists of records as pages; if a stream is unknown or Square denies access, it turns that into a clear skip signal.

**Call relations**: The sync runtime calls this when it wants records for a Square stream. `paginate` then calls `_locations`, `_cursor_get`, `_catalog`, or `_orders` as needed, or extracts inventory count records directly with `records_at`. If Square returns a 401 or 403 refusal, it raises `StreamSkipped` so the broader sync can continue without treating that stream as a fatal failure.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Square streams that use a normal list endpoint with a continuation cursor, such as customers, payments, and refunds. It also applies time-based resume behavior so repeated syncs do not have to keep all old records.

**Data flow**: It receives the client, a stream description, and an optional saved cursor. For payments and refunds, it sends the cursor to Square as a `begin_time` filter. For other cursor-based streams, it fetches pages and then removes records whose stream time field is not newer than the saved cursor. It yields only non-empty pages of records.

**Call relations**: `paginate` calls this for the streams that follow Square’s GET-list style. This helper leans on the shared REST connector’s cursor-page reader to walk through pages, then hands cleaned pages back to `paginate` for the sync system to consume.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function searches Square’s catalog for either items or categories and returns them page by page. It exists because Square catalog data is fetched through a POST search request rather than a simple list request.

**Data flow**: It receives the client, the catalog stream description, and an optional saved cursor. It builds a search body with the right catalog object type, sends it to Square, pulls records from the `objects` part of the response, filters out older records when a cursor is present, and follows Square’s returned cursor until there are no more pages. It yields each non-empty page.

**Call relations**: `paginate` calls this when the requested stream is `catalog_items` or `catalog_categories`. Inside the loop, it uses `records_at` to pull the useful list out of Square’s response, then returns those pages to the main pagination flow.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This function fetches the Square account’s locations. Locations are useful both as their own stream and as required input for searching orders.

**Data flow**: It receives the HTTP client, sends a request to Square’s locations endpoint, and extracts the list stored under `locations` in the response. It returns that list of location records.

**Call relations**: `paginate` calls this directly when syncing the `locations` stream. `_orders` also calls it first, because Square order searches need location IDs before they can ask for orders.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads Square orders across all available locations. It first discovers the account’s location IDs because Square requires orders to be searched by location.

**Data flow**: It receives the client and an optional saved cursor. It calls `_locations`, keeps only valid string location IDs, and stops early if there are none. It then repeatedly sends order search requests, adding a created-at time filter when a cursor is present and adding Square’s continuation cursor when more pages exist. It yields pages of order records until Square has no next cursor.

**Call relations**: `paginate` calls this for the `orders` stream. `_orders` depends on `_locations` to gather the location IDs needed for the search, uses `records_at` to extract orders from each response, and hands each page back into the main sync flow.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### `extensions/sources/ufo_ext_sources/providers/stripe.py`

`io_transport` · `source sync`

Stripe exposes billing and payment data through a web API, but not every Stripe record is reached the same way. Some lists are simple, like customers. Others are nested under a parent, like invoice line items under an invoice, or external bank accounts under a connected account. This file is the map and walking guide for all of those Stripe shapes.

The file defines the available Stripe streams, including their names, Stripe API paths, primary keys, and time fields used for incremental syncing. The main class, StripeConnector, builds an HTTP client, pins the Stripe API version, and then chooses the right paging strategy for each stream.

For a simple stream, it repeatedly asks Stripe for up to 100 records, using Stripe’s “starting_after” marker to move through pages. If a cursor is available, it can ask only for records created after a certain time. For nested streams, it first reads parent records, then asks Stripe for each parent’s child records, stamping the parent id onto each child so the relationship is not lost. This is like checking every folder in a filing cabinet, then labeling each document with the folder it came from.

The file also makes Stripe errors safer and clearer. Permission failures skip a stream instead of crashing the whole sync. Missing parent records during fan-out are ignored, because Stripe data can disappear between the parent and child request. Other Stripe-named errors are turned into useful failure messages without storing sensitive response bodies.

#### Function details

##### `_stripe_error`  (lines 95–101)

```
def _stripe_error(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This helper pulls Stripe’s structured error details out of a failed HTTP response. It exists because Stripe puts useful information, such as an error code or message, inside an "error" object in the response body.

**Data flow**: It receives an HTTP status error from a failed Stripe request. It tries to read the response body as JSON, looks for a dictionary named "error", and returns that dictionary if it is present. If the body is not JSON or does not match Stripe’s expected shape, it returns an empty dictionary.

**Call relations**: When the connector needs to understand why Stripe rejected a request, _refusal_reason calls this helper first. _child_pages also uses it to tell the difference between a harmless missing parent record and a real child-request failure.

*Call graph*: called by 2 (_child_pages, _refusal_reason).


##### `_refusal_reason`  (lines 104–120)

```
def _refusal_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: This helper turns a Stripe client-side error into a short human-readable reason. It is used so failure records say what Stripe complained about, not just that a URL returned an error.

**Data flow**: It receives an HTTP status error. If the response is not a client error, it returns an empty string. Otherwise it reads Stripe’s error detail, extracts the message and optional code, and returns text like "message [code]". If Stripe gave no usable message, it returns an empty string.

**Call relations**: paginate calls this after a Stripe request fails. _refusal_reason relies on _stripe_error to read Stripe’s error object, then hands paginate either a useful reason for a StreamFault or an empty result that tells paginate to re-raise the original error.

*Call graph*: calls 1 internal fn (_stripe_error); called by 1 (paginate).


##### `_stream`  (lines 123–141)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This small factory creates a StreamSpec, which is the system’s description of one readable Stripe collection. It keeps the long stream list compact and consistent.

**Data flow**: It receives a stream name and optional details such as the Stripe object path, primary key, cursor field, and timestamp fields. It fills in defaults where needed and returns a StreamSpec object that the connector later uses to decide how to read that stream.

**Call relations**: The module uses this helper while building STRIPE_STREAMS, the master list of Stripe streams exposed by StripeConnector. Each call becomes one stream definition the rest of the connector can look up later.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 217–220)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to Stripe and adds the pinned Stripe API version header. Pinning the version helps keep Stripe responses stable even if Stripe changes defaults later.

**Data flow**: It receives a base URL and a credential object supplied by the wider authentication system. It asks the parent RestConnector to build the basic client, adds the "Stripe-Version" header, and returns the configured async HTTP client.

**Call relations**: The source framework calls this when it needs a client for Stripe requests. After this setup, the paging methods use the client to make all later API calls with the expected Stripe version.


##### `StripeConnector._list_path`  (lines 223–224)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This method turns a stream description into the normal Stripe list endpoint path. For example, a stream whose source object is "customers" becomes "/v1/customers".

**Data flow**: It receives a StreamSpec. It reads the stream’s source_object field and returns a Stripe API path string with "/v1/" in front.

**Call relations**: paginate uses this for ordinary top-level streams. The parent-walking methods also use it when they need to list parent records before fetching children.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 227–239)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This method converts a saved cursor into the Unix timestamp format Stripe expects for created-time filters. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date-time string. It returns an integer timestamp when it can understand the value, or None when there is no usable cursor.

**Call relations**: _page_loop calls this before asking Stripe for pages. If it gets a timestamp and the stream uses Stripe’s "created" field as its cursor, _page_loop includes that timestamp in the request so old records can be skipped.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 241–273)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main entry point for reading one Stripe stream. It chooses the correct paging plan: simple list, nested child list, query-based child list, or external account fan-out.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks the stream name, delegates to the matching paginator, and yields pages of cleaned record dictionaries. If Stripe refuses access, it raises StreamSkipped; if Stripe returns a specific client-side reason, it raises StreamFault with a clearer message.

**Call relations**: The source-sync framework calls paginate when it wants records for a Stripe stream. paginate then hands work to _page_loop for simple streams, _paginate_substream for path-based child streams, _paginate_substream_query for query-based child streams, or _paginate_external_accounts for connected-account external accounts. When errors bubble up, it asks _refusal_reason whether Stripe supplied a meaningful explanation.

*Call graph*: calls 8 internal fn (__init__, __init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query, _refusal_reason).


##### `StripeConnector._page_loop`  (lines 275–307)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method walks through Stripe’s standard paged list format. It is the reusable engine for requests that return `{data: [...], has_more: bool}`.

**Data flow**: It receives a client, an API path, a stream description, an optional cursor, and optional extra query parameters. It builds request parameters, asks Stripe for one page, cleans each row with _browse_record, yields non-empty pages, and keeps going with Stripe’s "starting_after" marker until Stripe says there are no more pages.

**Call relations**: paginate uses _page_loop for ordinary streams. The parent and child pagination methods also use it whenever they need to list records from Stripe. Before requesting pages it calls _cursor_to_unix, and after receiving rows it calls _browse_record to normalize each record.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_child_pages, _paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 310–338)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This method cleans and normalizes one Stripe record before the rest of the system sees it. It removes fields that change every run and fills in common timestamp fields in a consistent readable format.

**Data flow**: It receives a raw Stripe record and the stream description. It copies the record while dropping volatile URL fields, creates a stable key for usage records, removes Stripe’s unstable usage summary id, and converts numeric timestamp fields into ISO date-time strings where appropriate. It returns the normalized record dictionary.

**Call relations**: _page_loop calls this for every row returned by Stripe. Its output is what paginate and the nested pagination methods yield to the wider source-sync pipeline.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 340–362)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads child collections that live under a parent record’s URL path, such as invoice lines under an invoice or persons under an account. It preserves the parent-child link by adding the parent id to each child row.

**Data flow**: It receives a client and a child stream description. It looks up the parent stream and child path template, reads parent pages, then for each parent id fetches child pages. It adds parent id fields, and sometimes selected parent timestamp fields, to each child record before yielding the child page.

**Call relations**: paginate calls this for streams listed as path-based substreams. _paginate_substream asks _stream_spec which parent stream to use, _parent_pages to read parents, and _child_pages to safely fetch each parent’s children.

*Call graph*: calls 3 internal fn (_child_pages, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 364–369)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method provides pages of parent records for a nested stream. It hides whether the parent itself is a normal top-level list or another query-based substream.

**Data flow**: It receives a client and a parent stream description. If that parent stream must itself be built through a query fan-out, it returns that specialized paginator. Otherwise it builds the normal list path and returns the standard page loop for the parent stream.

**Call relations**: _paginate_substream calls this before fetching child records. _parent_pages either hands off to _paginate_substream_query for more complex parents or to _page_loop for ordinary parent lists.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 371–385)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads child collections that Stripe exposes through a query parameter instead of a nested URL path. For example, it can list subscription items by asking for subscription_items with a particular subscription id.

**Data flow**: It receives a client and a stream description. It looks up the parent stream, the query parameter name, and the child endpoint. It reads parent records, then for each parent id requests child pages with that id as a query parameter. It adds a parent-id field to each child row and yields the results.

**Call relations**: paginate calls this directly for query-based substreams. _parent_pages may also call it when a path-based substream depends on a parent stream that is itself query-based, such as the two-step path needed for usage records. It uses _stream_spec, _list_path, _page_loop, and _child_pages to complete that walk.

*Call graph*: calls 4 internal fn (_child_pages, _list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 387–400)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads external bank accounts or cards attached to Stripe connected accounts. Stripe requires first listing accounts, then asking each account for its external accounts.

**Data flow**: It receives a client and an external-account stream description. It lists Stripe accounts, skips any account without an id, fetches that account’s external accounts, adds the owning account_id to each child record, and yields the stamped child pages.

**Call relations**: paginate calls this for the two external-account streams. It looks up the accounts stream with _stream_spec, gets the accounts path with _list_path, reads accounts through _page_loop, and fetches each account’s children through _child_pages.

*Call graph*: calls 4 internal fn (_child_pages, _list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._child_pages`  (lines 402–431)

```
async def _child_pages(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method fetches one parent’s child collection while treating a missing parent as a normal race condition. That matters because a Stripe customer, subscription, or account can disappear after it was listed but before its children are requested.

**Data flow**: It receives a client, child path, stream description, and optional query parameters. It delegates the actual paging to _page_loop and yields each page. If Stripe says the named parent resource is missing, it quietly yields nothing for that parent; for any other HTTP error, it re-raises the failure.

**Call relations**: The substream paginators call _child_pages whenever they move from a parent record to that parent’s children. _child_pages uses _stripe_error to confirm that a 400 or 404 really means Stripe’s "resource_missing" case before deciding to skip it.

*Call graph*: calls 2 internal fn (_page_loop, _stripe_error); called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._stream_spec`  (lines 433–434)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This method finds the StreamSpec for a stream name. It is a simple lookup used when one stream needs to know how to read another related stream.

**Data flow**: It receives a stream name. It searches the Stripe stream list and returns the matching StreamSpec. If no match exists, the normal Python lookup failure would surface.

**Call relations**: The nested pagination methods call _stream_spec when they need the definition of a parent stream, such as accounts for external accounts or subscriptions for subscription items. The returned StreamSpec is then passed into path-building and page-reading methods.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).

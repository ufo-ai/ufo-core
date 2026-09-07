# Finance, billing, commerce, and contract source providers  `stage-14.2.8`

This stage is shared behind-the-scenes support for bringing outside money and contract records into the system. It does not run the business logic itself. Instead, each file is a connector, like a plug adapter, that knows how to talk to one external service’s API, meaning its web doorway for data.

The finance connectors cover business spending, banking, and accounting. Brex and Ramp read company spend data such as cards, vendors, receipts, bills, and reimbursements. Mercury reads bank accounts and transactions. QuickBooks and Xero read accounting records such as invoices, bills, contacts, payments, and journal entries.

The billing and commerce connectors read customer and sales activity. Chargebee and Recurly handle subscription billing records. Stripe reads payments, customers, invoices, subscriptions, and related details. Square reads customers, locations, orders, refunds, catalog items, payments, and inventory.

The document connectors, DocuSign and PandaDoc, read envelopes, templates, documents, and contacts. Together these providers turn many different outside API formats into the system’s common stream of searchable records, while tracking paging and sync progress.

## Files in this stage

### Spend and banking feeds
Connectors that stream corporate spend, card, reimbursement, account, and transaction records from spend-management and banking platforms.

### `extensions/sources/ufo_ext_sources/providers/brex.py`

`io_transport` · `source sync polling`

Brex is an external spend-management service, and its API returns company data such as card transactions, expenses, transfers, users, vendors, budgets, and departments. This file is the project’s Brex connector: it is the adapter that turns Brex’s web API into a set of named streams the rest of the system can sync.

The file first defines the Brex streams and their important fields. A stream is one kind of object to copy, like “transactions” or “vendors.” Some streams have a date field that can be used as a checkpoint, which is like a bookmark showing how far the sync has seen. Brex does not let most endpoints filter by update time, so this connector generally reads full lists and computes any bookmark locally.

The main class, `BrexConnector`, supplies the Brex base URL, the stream list, and the pagination logic. Brex uses cursor-based pagination: each response contains a batch of `items` and possibly a `next_cursor` token for the next batch. The connector keeps asking for pages until there is no next cursor.

One important behavior is permission handling. If Brex replies with 401 or 403, meaning the credential is missing access or the right scope, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 42–53)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a `StreamSpec`, which is the project’s small description of one Brex data stream. It keeps the stream declarations short and consistent, so each stream can say its name, primary key, optional date bookmark field, and whether it is considered canonical.

**Data flow**: It receives a stream name and a few optional settings, such as the field that uniquely identifies records and the field used as a date checkpoint. It packages those choices into a `StreamSpec` object. The result is later placed into the Brex stream list so the sync system knows what Brex objects exist and how to identify their records.

**Call relations**: This function is used while the file is being loaded to build `BREX_STREAMS`. It hands each finished stream description to the connector class indirectly through that list, and the wider source-sync system later reads those descriptions when deciding which Brex streams to fetch.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 75–100)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Brex stream page by page from the correct API endpoint. It hides Brex’s cursor-based paging from the rest of the system, so callers can simply receive batches of records until the stream is done.

**Data flow**: It receives an HTTP client, a stream description, and a cursor argument from the sync framework. It looks up the Brex API path for that stream, asks Brex for up to 100 records, pulls the `items` list out of the response, and yields each non-empty batch. If Brex gives back a `next_cursor`, it uses that token to request the next page; when no token remains, it stops. If Brex says access is refused with 401 or 403, it turns that into a `StreamSkipped` error explaining that the grant lacks the needed scope.

**Call relations**: The core source-sync flow calls this method when it is time to fetch a Brex stream. Inside the loop it relies on the base connector’s HTTP GET behavior to contact Brex, uses `list_or_empty` to safely turn the response’s `items` field into a list, and hands batches of records back to the caller. If permissions are missing, it hands control back by raising `StreamSkipped`, allowing the larger sync to skip that stream cleanly.

*Call graph*: calls 1 internal fn (__init__); 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/mercury.py`

`io_transport` · `source sync polling`

This connector is the bridge between UFO and Mercury, a banking service. Without it, the system would not know which Mercury API endpoints to call, how to page through transaction history, or how to label transactions in a useful way for later recall.

It defines two streams of data: accounts and transactions. Accounts are simple: the connector asks Mercury for the account list once. Transactions are more work: it first gets every account, then asks Mercury for that account's transactions in pages of 100 records until there are no more full pages. This is like checking every folder in a filing cabinet, one stack at a time.

The file also deals with incremental syncing. A cursor, or saved stopping point, is based on Mercury's transaction posting date. Because Mercury accepts a date rather than an exact transaction position, the connector rereads the whole day where it last stopped. That may produce repeats, but downstream deduplication can remove them, and it avoids missing late or same-day records.

If Mercury rejects the API key with a 401 or 403 response, the connector marks the stream as skipped instead of crashing the whole source run. For display, transactions are titled by their counterparty name, because that is usually the most human-readable way to recognize a bank transaction.

#### Function details

##### `MercuryConnector.paginate`  (lines 64–84)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Mercury streams. It decides whether the sync is asking for accounts or transactions, fetches the right data, and yields it in batches for the rest of the source system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. For the accounts stream, it fetches the account list and yields it once. For the transactions stream, it fetches accounts, extracts their account IDs, then reads each account's transaction pages and adds the account ID as extra context to every transaction. If Mercury refuses access with a 401 or 403 response, it turns that into a clear StreamSkipped result; other HTTP errors are allowed to continue upward.

**Call relations**: During a sync, the core source framework calls this method to pull records. It calls _accounts to learn what Mercury accounts exist, _account_ids to choose the usable account IDs, _transactions to walk each account's transaction history, and with_context to attach the account ID before handing each page back to the framework.

*Call graph*: calls 4 internal fn (__init__, _account_ids, _accounts, _transactions); 1 external calls (with_context).


##### `MercuryConnector._accounts`  (lines 86–88)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches the Mercury account list. It exists so both the accounts stream and the transactions stream can use the same, clean way to read accounts.

**Data flow**: It takes an HTTP client, sends a GET request to Mercury's accounts endpoint, reads the returned JSON object, and pulls out the accounts field. If that field is missing or not a usable list, list_or_empty turns it into an empty list so the caller can continue safely.

**Call relations**: paginate calls this when it needs either to return account records directly or to find the accounts whose transactions should be fetched. After this function returns the raw account records, paginate either yields them or passes them to _account_ids.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector._account_ids`  (lines 91–96)

```
def _account_ids(accounts: list[dict[str, Any]]) -> list[str]
```

**Purpose**: This helper filters a list of account records down to valid Mercury account IDs. It keeps transaction syncing from trying to call Mercury with missing, empty, or non-text IDs.

**Data flow**: It receives account dictionaries. It checks each one for an id value that is a non-empty string, collects those strings, and returns the resulting list. It does not contact Mercury or change the account records.

**Call relations**: paginate uses this after _accounts returns account data. The IDs it returns become the inputs to _transactions, so each valid account can be read separately.

*Call graph*: called by 1 (paginate).


##### `MercuryConnector._transactions`  (lines 98–117)

```
async def _transactions(self, client: httpx.AsyncClient, account_id: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Mercury account's transactions, page by page. It supports incremental sync by asking Mercury for transactions from the saved posting date onward.

**Data flow**: It receives an HTTP client, one account ID, and an optional cursor. It repeatedly calls Mercury's account transactions endpoint with a limit of 100 and an increasing offset. If a cursor is present, it sends only the date part as Mercury's start filter. Each response is cleaned into a list of transaction records and yielded if non-empty. When Mercury returns fewer than 100 records, the function knows it has reached the end and stops.

**Call relations**: paginate calls this once for each valid account ID found by _account_ids. Each page it yields is handed back to paginate, which adds the account ID context and then passes the records on to the source framework.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector.render`  (lines 119–128)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function chooses the human-facing title and body text for a Mercury record. For transactions, it makes the counterparty name the title so a person can recognize the transaction quickly.

**Data flow**: It receives one record and its stream description. If the record is a transaction and has a non-empty counterpartyName, it returns that counterparty as the title plus a Markdown-style body containing the full JSON record. For accounts or transactions without a usable counterparty name, it falls back to the standard rendering from the parent connector.

**Call relations**: The broader source system uses this when turning synced records into recallable pages. This method only customizes transaction display; otherwise it hands off to the base RestConnector rendering behavior. It uses json.dumps to include a stable, sorted JSON view of the original record in the page body.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/ramp.py`

`io_transport` · `source sync polling`

Ramp is a business spending platform, and its API exposes many kinds of company finance objects. This connector is the adapter between Ramp’s REST API and UFO’s source-sync system. Without it, the system would not know which Ramp endpoints to call, how to follow Ramp’s pagination links, or how to deal with missing permissions.

The file first defines the Ramp streams: each stream is a kind of object, such as transactions or departments. Most streams are fully reread each sync. Transactions are special because Ramp can filter them by time, so this connector can ask only for transactions after the last saved checkpoint.

When syncing, `RampConnector.paginate` calls the right Ramp list endpoint, requests up to 100 records at a time, yields records to the caller, and follows Ramp’s `page.next` link until there are no more pages. Ramp gives the next page as a full web address, so `_next_path` trims it down to the path and query that the already-configured HTTP client can use.

If Ramp replies with 401 or 403, meaning the connected account does not have permission for that stream, the connector marks that stream as skipped instead of failing the whole sync. Finally, `render` gives transactions a human-friendly title based on the merchant name, because that is what a person would recognize first.

#### Function details

##### `_stream`  (lines 51–67)

```
def _stream(name: str, *, cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Ramp stream, such as transactions or users. It keeps the stream setup consistent so the rest of the file can list many Ramp objects without repeating the same fields each time.

**Data flow**: It receives a stream name and optional details such as which field acts as the time cursor, which fields mean created or updated time, and whether the stream is considered canonical. It packages those choices into a `StreamSpec`, which is the system’s small instruction card for how to identify and sync that kind of record.

**Call relations**: This function is used while the module is loaded to build `RAMP_STREAMS`, the list of Ramp streams exposed by `RampConnector`. It hands those stream descriptions to the connector class so later sync code knows what Ramp objects are available.

*Call graph*: 1 external calls (__init__).


##### `RampConnector._next_path`  (lines 99–106)

```
def _next_path(next_link: Any) -> str | None
```

**Purpose**: This helper converts Ramp’s next-page link into a form the connector’s HTTP client can reuse. Ramp returns a full URL, but the client is already tied to Ramp’s base address, so it only needs the path and query part.

**Data flow**: It takes whatever Ramp put in `page.next`. If that value is not a useful string or has no path, it returns nothing. If it is a valid URL, it extracts the path and keeps the query string when present, producing something like `/developer/v1/transactions?page=...`.

**Call relations**: During pagination, `RampConnector.paginate` asks this helper to interpret each `page.next` value. The result becomes the next request path, allowing the sync to keep walking through Ramp’s pages until the helper says there is no next page.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RampConnector.paginate`  (lines 108–136)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Ramp list endpoints. It fetches one stream page by page, yields batches of records to the sync engine, and treats permission problems as a skipped stream rather than a broken run.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It chooses the Ramp endpoint for that stream, adds a page size, and for transactions also adds ascending date order and a `from_date` filter when a cursor exists. It repeatedly requests data, turns the `data` field into a safe list, yields non-empty batches, then follows the next-page link. If Ramp returns 401 or 403, it raises `StreamSkipped`; other HTTP errors continue upward as real failures.

**Call relations**: The wider source-sync system calls this method when it is time to poll Ramp for a particular stream. Inside the loop it relies on `_next_path` to move from one page to the next and on `list_or_empty` to safely normalize Ramp’s response. Its yielded batches are what the rest of the source system stores, indexes, or compares against previous runs.

*Call graph*: calls 2 internal fn (__init__, _next_path); 1 external calls (list_or_empty).


##### `RampConnector.render`  (lines 138–148)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function gives synced Ramp records a readable title and body for recall. It especially fixes transactions, whose most useful human label is the merchant name stored in `merchant_name` rather than in a generic title field.

**Data flow**: It receives one Ramp record and the stream it came from. If the stream is a transaction and the record has a non-empty merchant name, it returns that merchant as the title and a text body containing the full record as sorted JSON. For all other records, it falls back to the standard rendering behavior from the parent connector.

**Call relations**: The source system calls this after records have been fetched and need to be turned into recallable text. For transactions, this method provides a clearer human-facing label; for every other stream, it hands the work back to the shared connector rendering logic.

*Call graph*: 1 external calls (dumps).


### Subscription billing feeds
Connectors that page through subscription billing platforms and expose their customers, invoices, subscriptions, and related billing objects as syncable records.

### `extensions/sources/ufo_ext_sources/providers/chargebee.py`

`io_transport` · `source sync and API pagination`

Chargebee is a billing service, and this connector is the read-only doorway into it. Without this file, the project would not know how to fetch Chargebee customers, subscriptions, invoices, transactions, items, and related records in a reliable, repeatable way.

The file first defines the Chargebee streams the system can sync. A stream is one kind of record, such as “customer” or “invoice.” Most streams map directly to a Chargebee list endpoint. A few are substreams: they can only be found by first reading a parent record, like reading each customer before asking Chargebee for that customer’s contacts.

Chargebee returns records inside wrappers, for example an entry may look like `{customer: {...}}`. The connector flattens that shape so downstream code sees the actual customer fields at the top level. It also follows Chargebee’s `next_offset` pagination token, which is like a “continue from here” ticket for the next page of results.

For incremental syncs, the connector can send a cursor such as `updated_at[after]=...`, meaning “only give me records changed after this point.” Authentication uses HTTP Basic auth with the API key as the username. If Chargebee rejects access with a permission or authentication error, the stream is skipped with a clear explanation instead of failing mysteriously.

#### Function details

##### `_stream`  (lines 58–76)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: Creates a small description of one Chargebee data stream, such as customers or invoices. This keeps the stream list readable and makes sure each stream has the fields the sync system needs, like its primary key and cursor field.

**Data flow**: It receives a stream name and optional details such as the source object name, primary key, and timestamp fields. It fills in sensible defaults where details are missing, then produces a `StreamSpec`, which is the system’s standard description of a stream.

**Call relations**: This helper is used while building the file’s Chargebee stream list. It hands each finished stream description to `StreamSpec.__init__`, so the wider source framework can later ask the connector what streams are available.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 129–147)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Chargebee. It applies the right base URL, timeouts, headers, and authentication so later API calls do not need to repeat that setup.

**Data flow**: It receives a base URL and a resolved credential. It trims the base URL, prepares JSON/form headers, sets connection and read time limits, then either uses a provided proxy transport or creates Basic authentication from the API key. It returns an `httpx.AsyncClient`; if there is no usable authentication information, it raises an error.

**Call relations**: The source framework calls this when it is ready to connect to Chargebee. This function creates the `httpx` timeout, optional Basic auth, and async client that all pagination functions use afterward.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 149–161)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Removes Chargebee’s extra wrapper around each record. This makes a record easier for the rest of the system to read, because fields like `id` and timestamps become top-level fields.

**Data flow**: It receives one raw Chargebee record and the stream description that says which wrapper key to expect. If the record contains a dictionary under that wrapper key, it copies the inner fields into a new flat record and preserves extra top-level fields such as a parent id. If no matching wrapper exists, it returns the record unchanged.

**Call relations**: This is used after pages have been fetched, when the connector is preparing raw API records for the common source pipeline. It complements the pagination methods, which fetch Chargebee’s original wrapped records.


##### `ChargebeeConnector.paginate`  (lines 163–194)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right way to fetch pages for a requested Chargebee stream. It is the main traffic director for reading records from Chargebee.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, sends ordinary streams to the normal list paginator, and sends special child streams to their parent-and-child paginators. It yields pages of raw records. If Chargebee responds with 401 or 403, it turns that into a clear `StreamSkipped` message.

**Call relations**: The source framework calls this when it needs records for a stream. Depending on the stream, it calls `_paginate_list`, `_paginate_attached_items`, `_paginate_contacts`, `_paginate_quote_line_groups`, or `_paginate_subscription_scheduled`, then passes their pages back upward.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 197–203)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: Creates the query parameters for a normal Chargebee list request. Its main job is to ask for a fixed-size page and, when possible, only records newer than the saved cursor.

**Data flow**: It receives a stream description and an optional cursor value. It starts with the page limit. If both a cursor and a cursor field exist, it adds Chargebee’s `field[after]` filter. It returns the parameter dictionary used in the API request.

**Call relations**: `_paginate_list` calls this before starting a normal list pagination loop. This keeps cursor filtering in one place so every ordinary stream uses the same rule.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 205–217)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches a standard Chargebee list endpoint page by page. This is used for most top-level resources such as customers, subscriptions, invoices, and transactions.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for that stream, builds request parameters, then follows Chargebee’s `next_offset` token until there are no more pages. It yields each page of records as it arrives.

**Call relations**: `paginate` calls this for ordinary streams. The substream paginators also call it first to fetch parent records before asking for each parent’s children.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 219–236)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches attached items, which are child records under individual Chargebee items. It first finds the parent items, then asks Chargebee for each item’s attached items.

**Data flow**: It receives an HTTP client and an optional cursor. It reads item pages through `_paginate_list`, extracts each item id, skips parents without an id, then requests `/items/{item_id}/attached_items`. Each child record is stamped with the parent `item_id` before being yielded.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This function depends on `_paginate_list` to find parent items and `_paginate_substream` to walk the child pages for each parent.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 238–257)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches customer contacts, which are child records under individual Chargebee customers. It is needed because contacts are not read from one simple global list in this connector.

**Data flow**: It receives an HTTP client and an optional cursor. It reads customer pages through `_paginate_list`, extracts each customer id, skips customers without an id, then requests `/customers/{customer_id}/contacts`. Each returned contact is marked with the `customer_id` it came from.

**Call relations**: `paginate` calls this for the `contact` stream. It uses `_paginate_list` to get customers first, then `_paginate_substream` to fetch and stamp each customer’s contacts.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 259–276)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches quote line groups, which belong to individual quotes. It connects each child record back to the quote it came from.

**Data flow**: It receives an HTTP client and an optional cursor. It reads quote pages through `_paginate_list`, extracts each quote id, skips quotes without an id, then requests `/quotes/{quote_id}/quote_line_groups`. It yields child pages after adding the parent `quote_id` to each record.

**Call relations**: `paginate` calls this for the `quote_line_group` stream. Like the other child-stream readers, it combines `_paginate_list` for parents with `_paginate_substream` for paged children.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 278–300)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches each subscription together with any scheduled changes. This uses a per-subscription detail endpoint instead of a normal list of child records.

**Data flow**: It receives an HTTP client and an optional cursor. It reads subscription pages through `_paginate_list`, extracts each subscription id, then calls Chargebee’s `retrieve_with_scheduled_changes` endpoint for that subscription. If the response contains a subscription object, it yields a one-record page marked with the original `subscription_id`.

**Call relations**: `paginate` calls this for `subscription_with_scheduled_changes`. It uses `_paginate_list` only to discover which subscriptions to inspect, then performs one detail request per subscription.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 302–328)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides the shared paging loop for child streams that live under a parent record. It also labels every child with the id of its parent so the relationship is not lost.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent id field to add, and the parent id value. It fetches the child endpoint page by page using Chargebee’s `next_offset` token, copies each dictionary record, adds the parent id, and yields only non-empty stamped pages.

**Call relations**: `_paginate_attached_items`, `_paginate_contacts`, and `_paginate_quote_line_groups` call this after they have found a parent id. It gives those functions one reusable way to page through child records and attach the parent link.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/providers/recurly.py`

`io_transport` · `source sync run`

Recurly returns lists of things, such as accounts, invoices, subscriptions, and coupons, in pages rather than all at once. This file is the connector that turns those pages into streams the rest of the system can consume. Without it, the system would not know Recurly’s URL, required API headers, authentication style, paging rules, or the special paths for data that lives underneath a parent record.

At the top, the file defines the available Recurly streams. A stream is a named collection of records to sync, with details such as its primary key and the time field used for incremental syncing. Incremental syncing means “start from the last saved point instead of reading everything again.”

The main class, RecurlyConnector, builds an HTTP client with Recurly’s required API version and Basic authentication. It then provides pagination helpers. Most streams are simple top-level lists, like `/accounts` or `/invoices`. Some streams are nested under another object, like notes under each account. For those, the connector first lists the parent IDs, then asks Recurly for each parent’s child records, stamping the parent ID onto each child row so the relationship is not lost.

A notable behavior is that Recurly’s `next` link may be a full URL or just a path. The connector normalizes it before making the next request. If Recurly returns 401 or 403, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `_stream`  (lines 40–54)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the system’s description of one Recurly collection to sync. It keeps the stream list readable by filling in common defaults, such as using `id` as the main identifier and `updated_at` as the usual change-tracking field.

**Data flow**: It receives a stream name plus optional details like the source API object, primary key, cursor field, and whether it is a main canonical stream. It combines those choices with sensible defaults and returns a StreamSpec object that the connector later uses to decide what URL to call and how to track progress.

**Call relations**: This helper is used while the module is being loaded to build the RECURLY_STREAMS list. Its only handoff is to StreamSpec.__init__, which creates the actual stream description object used later by RecurlyConnector.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 85–100)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It applies Recurly’s required API version header, timeout settings, and the correct authentication method.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares headers, and sets connection and read time limits. If the credential includes a proxy transport, it builds a client that sends requests through that transport. If the credential includes a key, it uses that key as the username in HTTP Basic authentication, with an empty password. If neither is present, it raises an error because it cannot safely contact Recurly.

**Call relations**: This method is part of the connector setup path used when the broader RestConnector machinery needs a ready-to-use HTTP client. It hands off to httpx.Timeout, httpx.BasicAuth, and httpx.AsyncClient to create the actual network client.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 103–114)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s “next page” link into a path the HTTP client can request. Recurly may return either a full URL or a relative path, so this helper makes both forms usable.

**Data flow**: It receives a next-link value, which may be missing, a full URL, or a path. If it is missing, it returns nothing. If it is a full URL, it extracts only the path and query string. If it is already a path, it returns it unchanged.

**Call relations**: The pagination functions call this whenever Recurly says there are more pages. RecurlyConnector._paginate_top_level, RecurlyConnector._account_ids, RecurlyConnector._coupon_ids, and RecurlyConnector._paginate_per_parent all rely on it before making the next request.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 117–125)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the first set of query parameters for a Recurly list request. It tells Recurly how many records to return, how to sort them, and where to begin for incremental syncs.

**Data flow**: It receives a stream description and an optional saved cursor value. It creates parameters with the page size, ascending order, and the stream’s cursor field as the sort field, or `created_at` if the stream has no cursor field. If both a cursor field and cursor value exist, it adds `begin_time` so Recurly starts from that point.

**Call relations**: This is called at the start of top-level and per-parent pagination. RecurlyConnector._paginate_top_level and RecurlyConnector._paginate_per_parent use its output only on the first request, then follow Recurly’s own next-page links after that.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 127–160)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main paging entry for a stream. Given a Recurly stream, it decides whether to read a normal top-level collection, a special parent-child collection, or a filtered coupon collection.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It checks the stream name. For nested streams, it delegates to the per-parent pager. For `unique_coupons_parent`, it reads coupons and keeps only bulk coupons. For ordinary streams, it builds a path from the stream’s source object and reads pages from that endpoint. It yields lists of records as they arrive. If Recurly refuses access with 401 or 403, it turns that into a StreamSkipped error with a useful explanation.

**Call relations**: The larger source-sync system calls this when it wants records for a specific Recurly stream. This method then routes the work to RecurlyConnector._paginate_per_parent or RecurlyConnector._paginate_top_level. If authorization fails, it creates a StreamSkipped exception so the sync can treat that stream as unavailable rather than as a generic failure.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 162–175)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly list endpoint page by page. It is used for streams where records live directly under paths such as `/accounts`, `/plans`, or `/transactions`.

**Data flow**: It receives an HTTP client, stream description, API path, and optional cursor. It builds the first query with RecurlyConnector._initial_query, requests the current page, yields the `data` records if any exist, and then follows Recurly’s `next` link while `has_more` is true. After the first request, it stops sending the original parameters because Recurly’s next link already contains the cursor details.

**Call relations**: RecurlyConnector.paginate calls this for ordinary streams and for the coupon parent stream used by unique coupons. During the loop, it asks RecurlyConnector._next_path to clean up each next-page link before the next HTTP request.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 177–188)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields their IDs. It exists because several child streams, such as account notes and billing info, must be requested one account at a time.

**Data flow**: It starts at `/accounts` with a page size and created-time sorting. For each returned account row, it checks that the row is a dictionary and has an `id`, then yields that ID as text. If Recurly reports more pages, it follows the normalized next path until there are no more pages.

**Call relations**: RecurlyConnector._paginate_per_parent calls this when the child records live under accounts. This function uses RecurlyConnector._next_path to continue through all account pages before the child requests are made.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 190–204)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through Recurly coupons and yields only the IDs for bulk coupons. It is used because unique coupon codes are only meaningful under those bulk coupon parents.

**Data flow**: It starts at `/coupons` with page size and created-time sorting. For each coupon row, it ignores anything that is not a dictionary, has no `id`, or is not marked as `coupon_type` equal to `bulk`. For the remaining rows, it yields the coupon ID as text. It follows Recurly’s next-page link until all coupon pages have been checked.

**Call relations**: RecurlyConnector._paginate_per_parent calls this when it needs parent IDs from coupons rather than accounts. Like the account ID reader, it depends on RecurlyConnector._next_path to keep paging safely.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 206–234)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams where records are nested under a parent object, like notes under each account or unique coupon codes under each bulk coupon. It is like checking every folder in a filing cabinet: first find each folder, then read the papers inside it.

**Data flow**: It receives the HTTP client, stream description, parent path, child path, field name used to store the parent ID, and optional cursor. It chooses the right parent ID source: coupon IDs for coupon parents, account IDs otherwise. For each parent ID, it builds the child URL, prepares the first query, requests child pages, and yields any child records. Before yielding, it adds the parent ID into each dictionary record if that field is not already present.

**Call relations**: RecurlyConnector.paginate calls this for the streams listed as per-parent streams. This method calls RecurlyConnector._account_ids or RecurlyConnector._coupon_ids to discover parents, RecurlyConnector._initial_query to start each child listing, and RecurlyConnector._next_path to follow additional child pages.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### Contract document feeds
Read-only connectors that turn contract and document-system envelopes, templates, documents, and contacts into searchable synced pages.

### `extensions/sources/ufo_ext_sources/providers/docusign.py`

`io_transport` · `during scheduled source sync polling`

DocuSign is not reached through one fixed data server. First, the connector asks DocuSign’s shared identity service which account the logged-in grant belongs to and what regional API address that account uses. This is like asking a company receptionist which branch office holds the files before going to fetch them.

Once it knows the correct account and base address, the connector can poll two kinds of DocuSign objects: envelopes and templates. Envelopes are the sent signing packets people act on. Templates are reusable signing layouts. The connector reads these in pages of 100 items at a time, stopping when DocuSign returns fewer than a full page.

Envelope syncing is incremental. That means later runs can start from the last known status-change time instead of reading everything again. On a first run, it deliberately starts far back in time so it does not accidentally miss older envelope history. For envelopes, it also asks DocuSign to include recipients and custom fields, so the stored page contains useful signing context.

If DocuSign says the credential is not allowed to read the data, the stream is skipped with a clear reason. If the grant contains multiple possible accounts and no single default account, the connector fails instead of guessing and possibly syncing the wrong company’s documents.

#### Function details

##### `DocuSignConnector._account_base`  (lines 81–111)

```
async def _account_base(self, client: httpx.AsyncClient) -> str
```

**Purpose**: Finds the exact DocuSign account API address to use for data reads. This matters because DocuSign accounts can live on different regional hosts, so the connector cannot safely use one hard-coded data URL.

**Data flow**: It takes an HTTP client that can talk to DocuSign. It reads the user information response, looks at the listed accounts, keeps only accounts that have both an account ID and a base API address, then chooses the only usable account or the one marked as default. If there is no usable account, or several accounts with no single default, it raises a clear fault instead of guessing. If selection succeeds, it returns a full account-specific REST API base URL.

**Call relations**: This is called by DocuSignConnector.paginate before any envelopes or templates are fetched. It uses list_or_empty to safely treat missing or malformed account lists as empty lists, and it raises StreamFault when the grant cannot point to one safe account.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `DocuSignConnector.paginate`  (lines 113–145)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one DocuSign stream, such as envelopes or templates, in batches. It is the main fetching loop that turns DocuSign’s paged API responses into chunks of records the rest of the sync system can process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value that marks where a previous envelope sync left off. It first finds the proper account API base, then repeatedly asks DocuSign for up to 100 records starting at the current position. For envelopes, it adds the starting date, recipient and custom-field details, and ascending order. Each non-empty batch is yielded outward. When a returned batch is smaller than 100 records, it knows there are no more pages and stops. If DocuSign refuses access with an authorization-style status, it converts that into a skipped stream message; other HTTP errors are allowed to keep rising.

**Call relations**: The sync framework calls this when it wants records for a DocuSign stream. Before fetching records, it calls DocuSignConnector._account_base so it knows the right regional account endpoint. For each API response, it uses list_or_empty to safely pull out the expected record list, and it raises StreamSkipped when DocuSign says the grant is not allowed to read the stream.

*Call graph*: calls 2 internal fn (__init__, _account_base); 1 external calls (list_or_empty).


##### `DocuSignConnector.render`  (lines 147–156)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: Builds the human-facing page title and body for a DocuSign record. For envelopes, it uses the email subject as the title because that is the name recipients actually saw.

**Data flow**: It receives one DocuSign record and the stream it came from. If the record is an envelope with a non-empty emailSubject, it returns that subject as the page title and a text body containing a heading plus the full record encoded as sorted JSON. If the record is not such an envelope, it falls back to the standard rendering behavior from the parent connector.

**Call relations**: This is used after records have been fetched, when the source system needs to turn raw DocuSign data into recallable text. It uses json.dumps to include the full envelope data in a stable text form, while relying on the parent renderer for templates and envelopes that do not have a usable subject.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/pandadoc.py`

`io_transport` · `source sync`

PandaDoc is an online document service, and this connector is the bridge between PandaDoc’s API and this project’s source-sync system. Without this file, the system would not know which PandaDoc endpoints to call, how to authenticate, how to page through long result lists, or how to recover when one document cannot be opened.

The file defines three streams: documents, templates, and contacts. A stream is a named kind of data the sync system can pull. Documents are special because they support incremental syncing: instead of rereading everything every time, the connector can ask PandaDoc for documents changed after a saved date. This saved position is called a checkpoint, like a bookmark in a book.

The main class, PandaDocConnector, builds an HTTP client for PandaDoc, adding PandaDoc’s required `API-Key` authorization format when the user supplied a key. It then pages through PandaDoc list endpoints 100 records at a time. For documents, each list result is only a summary, so the connector makes a second request for each document’s details, adding richer information like fields, pricing, tokens, and recipients.

There is careful failure behavior. If PandaDoc refuses access to a whole stream with a 401 or 403 response, the stream is skipped with a clear message. If one document appears in the list but its details cannot be opened, the connector keeps the summary row instead of failing the entire sync.

#### Function details

##### `_stream`  (lines 38–52)

```
def _stream(name: str, *, cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream description for one kind of PandaDoc data. A stream description tells the sync system the stream’s name, its unique ID field, and which date fields can be used to track creation and updates.

**Data flow**: It receives a stream name and optional settings, such as which field should act as the update cursor and whether the stream is considered canonical. It packages those choices into a StreamSpec object. The result is a reusable definition that the connector advertises to the rest of the system.

**Call relations**: This helper is used while the file is being loaded to build the PandaDoc stream list. It hands its settings into StreamSpec so the wider source-sync machinery knows how to treat documents, templates, and contacts.

*Call graph*: 1 external calls (__init__).


##### `PandaDocConnector._make_client`  (lines 68–74)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This method creates the HTTP client used to talk to PandaDoc and applies PandaDoc’s special API key format when needed. PandaDoc expects keys to be sent as `API-Key ...`, not the more common `Bearer ...` format.

**Data flow**: It receives the PandaDoc base URL and a resolved credential. First it asks the parent RestConnector to create the basic HTTP client. If the credential contains a user-supplied key, it adds an Authorization header in PandaDoc’s expected format. It returns the ready-to-use client, possibly with that header added.

**Call relations**: The broader connector framework calls this when a PandaDoc sync needs a network client. This method customizes the generic REST client just enough for PandaDoc authentication, while leaving broker-provided proxy authentication untouched.


##### `PandaDocConnector.paginate`  (lines 76–105)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one PandaDoc stream page by page and yields batches of records to the sync engine. It knows how to request only changed documents when a previous checkpoint exists.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from the previous sync. It looks up the correct PandaDoc list endpoint, then requests pages of up to 100 records. For document pages, it also enriches each summary record by calling `_details`. Each non-empty batch is yielded to the caller. If PandaDoc returns fewer than 100 records, the method knows it has reached the end. If PandaDoc refuses access to the stream, it turns that into a StreamSkipped error so the sync can continue sensibly.

**Call relations**: The source-sync framework calls this when it is time to fetch PandaDoc data. During document syncs it calls `_details` for each listed document, and it uses `list_or_empty` to safely treat missing or non-list result data as an empty list. If the whole stream is not readable, it raises StreamSkipped with a clear explanation for the framework to report.

*Call graph*: calls 2 internal fn (__init__, _details); 1 external calls (list_or_empty).


##### `PandaDocConnector._details`  (lines 107–119)

```
async def _details(self, client: httpx.AsyncClient, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: This method fetches the full details for one PandaDoc document and combines them with the basic list row. It gives the sync richer document data without letting one inaccessible document stop the whole run.

**Data flow**: It receives an HTTP client and one document record from the list endpoint. It reads the record’s `id`; if there is no usable ID, it simply returns the original record. Otherwise it requests the document details endpoint. If the details request succeeds, it merges the original summary and the detailed response into one record. If PandaDoc says the document is forbidden or missing, it returns the original summary instead.

**Call relations**: This method is called by `PandaDocConnector.paginate` only for the documents stream. It acts as the second step after listing documents: first get the summaries, then widen each readable document with its full details before handing the batch back to the sync system.

*Call graph*: called by 1 (paginate).


### Accounting ledger feeds
Connectors that stream accounting objects such as invoices, vendors, bills, journal entries, contacts, accounts, and payments from ledger systems.

### `extensions/sources/ufo_ext_sources/providers/quickbooks.py`

`io_transport` · `source sync / request handling`

QuickBooks Online does not offer a simple “give me the next page of invoices” endpoint for each kind of data. Instead, every read is sent as a SQL-like query to one shared `/query` endpoint. This file hides that awkwardness behind a connector, so the rest of the system can treat QuickBooks like a normal paged data source.

The file first defines the list of QuickBooks record types the system can read, such as accounts, customers, invoices, payments, tax rates, and time activities. Each stream says which QuickBooks object it maps to, that `Id` is the unique identifier, and where to find the update time used for incremental syncing. Incremental syncing means “only fetch records changed since last time,” like checking only the mail that arrived after your last visit. A few reference lists do not use that cursor and are fully refreshed instead.

`QuickBooksConnector` builds the query text QuickBooks expects, pages through results 100 records at a time, and yields each page to the sync runner. If QuickBooks refuses access with a 401 or 403 error, the connector skips that stream with a clear message instead of crashing the whole sync. It also flattens the nested update timestamp into a top-level key so the shared watermark logic can remember progress reliably.

#### Function details

##### `_stream`  (lines 33–48)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a stream definition for one QuickBooks object. It keeps the long stream list readable by filling in the common QuickBooks rules, such as using `Id` as the primary key and the metadata timestamp as the update field.

**Data flow**: It takes a friendly stream name, the QuickBooks object name, and a few optional choices such as whether the stream has a cursor. It packages those details into a `StreamSpec`, which is the system’s description of how one kind of record should be synced. The result is used later by the connector to know what to query and how to track progress.

**Call relations**: This function is used while the module is being loaded to build `QUICKBOOKS_STREAMS`. It hands each completed stream definition to the connector class through `streams_list`, so the runtime can discover all QuickBooks data types this source supports.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 90–97)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: This function writes the SQL-like query string that QuickBooks expects for a page of records. It is what turns the system’s stream-and-cursor information into an actual QuickBooks request.

**Data flow**: It receives a stream definition, an optional cursor value from the previous sync, and the starting row number for the page. It creates a `SELECT * FROM ...` query, adds a “last updated after this cursor” filter when possible, orders by that same update field, and appends the page size and start position. It returns the finished query string ready to send to QuickBooks.

**Call relations**: The pagination loop calls this each time it needs another page. `_build_query` does not contact QuickBooks itself; it only prepares the text that `paginate` sends through the connector’s HTTP request method.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 99–121)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches QuickBooks records one page at a time. It is the main read loop for every QuickBooks stream, allowing the sync runner to process large result sets without loading everything at once.

**Data flow**: It starts at QuickBooks row position 1, builds a query for the requested stream and cursor, sends it to the `/query` endpoint, and looks inside `QueryResponse` for records of the expected entity type. When records are found, it yields that list as one page. If the page has fewer than 100 records, it knows there are no more pages and stops. If QuickBooks returns an access refusal, it turns that into a `StreamSkipped` error with a human-readable explanation; other HTTP errors are passed upward unchanged.

**Call relations**: The source runtime calls this when syncing a particular QuickBooks stream. Inside the loop it relies on `_build_query` to create each QuickBooks query, then hands each page of records back to the caller. If QuickBooks says the app lacks permission for a stream, it raises `StreamSkipped` so the broader sync can skip that stream cleanly.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 123–126)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function makes nested QuickBooks timestamps easier for the shared sync machinery to read. In particular, it lifts `MetaData.LastUpdatedTime` into a top-level field when that nested cursor is used.

**Data flow**: It receives one raw QuickBooks record and its stream definition. If the stream’s cursor field contains a dotted path, it reads that nested value from the record and returns a copy of the record with an extra top-level key using the same dotted name. If no nested cursor is needed, it returns the record unchanged.

**Call relations**: After pages are fetched, the source framework can call this before computing the next checkpoint, or watermark, for the stream. It uses `get_path` to safely read the nested value, then returns a record shape that the common checkpoint logic can understand.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/xero.py`

`io_transport` · `during source sync`

Xero is an accounting service, and its API has a few rules that this connector hides from the rest of the project. Each kind of accounting object is described as a stream: for example, accounts come from Xero’s “Accounts” endpoint, invoices from “Invoices”, and so on. The connector knows which Xero field is the real record identifier, then copies that value into a plain “id” field so every stream looks consistent to the wider system.

Before it can read accounting data, Xero requires a tenant header, which means “which organisation inside this Xero login should we read?” If the connector was not given a tenant ahead of time, it asks Xero’s connections endpoint. If there is exactly one organisation, it uses that. If there are none, or more than one, it raises a clear fault instead of silently syncing the wrong company’s books.

For reading records, the connector supports both full syncs and incremental syncs. Incremental sync means “only ask for records changed since the last checkpoint.” Xero expects that date in an HTTP header called “If-Modified-Since”, so the file converts saved cursor values into the date format Xero wants. It also understands Xero’s paging style: most resources are fetched 100 records at a time until a shorter page appears, while a few small resources are fetched all at once. If Xero refuses access with a permission or authentication error, the stream is skipped with an explanation.

#### Function details

##### `_stream`  (lines 76–90)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a stream description for one kind of Xero data, such as invoices or contacts. It keeps the long list of Xero streams readable and consistent.

**Data flow**: It receives a friendly stream name, the Xero API object name, and optional details about update tracking. It packages those into a StreamSpec, which tells the sync system where to read the data, what field to use as the record key, and which date field marks changes.

**Call relations**: This is used while the file defines XERO_STREAMS, the catalog of Xero resources the connector can read. It hands each stream’s shape to StreamSpec so the generic source-sync machinery can later ask XeroConnector to fetch those streams.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 118–135)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved checkpoint date into the exact date text Xero expects for incremental reads. It helps the connector ask Xero for “only records modified since this time.”

**Data flow**: It takes a cursor value, which may be empty, a Unix timestamp, an ISO-style date string, or already-formatted text. Empty or unusable values become None. Numeric timestamps and parseable dates are converted to UTC text like an HTTP date; unparseable non-empty text is passed through unchanged.

**Call relations**: XeroConnector.paginate calls this when a stream has a cursor and the sync is trying to resume from a previous point. The resulting text is placed into the If-Modified-Since request header before the HTTP request is sent to Xero.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 144–145)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This creates a Xero connector instance and optionally remembers which Xero organisation, or tenant, it should read from. A tenant is Xero’s way of identifying one company inside an account grant.

**Data flow**: It receives an optional tenant id. It stores that value on the connector so later HTTP clients can include it in requests; it does not contact Xero or fetch data at this point.

**Call relations**: This runs when the connector is constructed for a sync setup. If a tenant id is provided here, XeroConnector._make_client can put it directly into the request headers; otherwise XeroConnector._ensure_tenant resolves the tenant during pagination.


##### `XeroConnector._make_client`  (lines 147–151)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Xero. If the connector already knows the organisation tenant, it adds the required Xero tenant header immediately.

**Data flow**: It receives the API base URL and a credential object supplied by the auth proxy. It asks the parent RestConnector to build the normal authenticated HTTP client, then adds the xero-tenant-id header when a tenant id was configured. It returns the ready-to-use client.

**Call relations**: The source runtime uses this when starting a Xero sync connection. The client it returns is later used by XeroConnector.paginate, and may already contain the tenant header that XeroConnector._ensure_tenant would otherwise have to discover.


##### `XeroConnector._ensure_tenant`  (lines 153–181)

```
async def _ensure_tenant(self, client: httpx.AsyncClient) -> None
```

**Purpose**: This makes sure the HTTP client has the Xero organisation header required for accounting API calls. It protects users from accidentally syncing the wrong organisation when one Xero grant contains more than one company.

**Data flow**: It looks at the client’s headers. If the tenant header is already present, it does nothing. Otherwise it requests Xero’s connections list, extracts organisation tenant ids, and checks how many valid organisations are available. With one organisation, it writes that tenant id into the client headers. With none or several, it raises a StreamFault explaining why the sync cannot safely continue.

**Call relations**: XeroConnector.paginate calls this before fetching stream data. It depends on the raw Xero connections response and uses list_or_empty to safely treat missing or odd response data as an empty list. If it raises StreamFault, the sync stops for this connector rather than making ambiguous accounting requests.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `XeroConnector.paginate`  (lines 183–219)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Xero stream data. It sends the HTTP requests, follows Xero’s paging rules, and yields batches of records for the sync system to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero endpoint path, adds an If-Modified-Since header when possible, ensures a tenant is selected, then requests records from Xero. For non-paged streams it makes one request and yields the returned records. For paged streams it requests page 1, page 2, and so on, yielding each non-empty batch until Xero returns no records or fewer than 100 records. If Xero returns a 401 or 403 refusal, it turns that into a StreamSkipped message; other HTTP errors are allowed to rise normally.

**Call relations**: The generic source-sync runner calls this whenever it needs records for one Xero stream. It calls XeroConnector._ensure_tenant first so every accounting request names an organisation, and it calls _cursor_to_rfc1123 when doing an incremental sync. The batches it yields are later normalized by XeroConnector.flatten and checkpointed by the broader sync framework.

*Call graph*: calls 3 internal fn (__init__, _ensure_tenant, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 221–230)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This normalizes a single Xero record so the rest of the system can identify it using a standard “id” field. Xero uses different id field names for different resources, so this function smooths over that difference.

**Data flow**: It receives one record dictionary and the stream it came from. If the record already has an “id”, it leaves it alone. Otherwise it looks up the correct Xero-specific id field for that stream, reads the value, and returns a copy of the record with “id” added as text. If no known id field or id value is present, it returns the record unchanged.

**Call relations**: The sync framework uses this after records have been fetched by XeroConnector.paginate. It relies on the stream name to choose the right Xero id field, so downstream storage and recall code can treat all Xero records as having the same primary key shape.


### Payments and commerce feeds
Connectors that normalize payments, commerce, customer, order, refund, catalog, inventory, and related financial records into standard sync streams.

### `extensions/sources/ufo_ext_sources/providers/square.py`

`io_transport` · `source sync`

Square exposes its data through several different web API patterns. Some endpoints are simple lists, some use a cursor token to fetch the next page, and some require a search request sent with a POST body. This file hides those differences behind one connector, so the rest of the project can ask for “the next page of payments” or “the next page of orders” without knowing Square’s special rules.

The main class, SquareConnector, is a read-only connector. It sets the Square API base address, declares which streams exist, and pins the Square API version by adding a Square-Version header to every request. A stream is a named kind of data, like customers or orders, with details such as its primary key and timestamp field.

The central method is paginate. It acts like a traffic director. Depending on the requested stream, it calls the right helper: simple location fetches, cursor-based GET requests, catalog searches, order searches across all locations, or inventory count retrieval. Each helper yields lists of records, like handing over one tray of results at a time.

The file also deals with incremental syncing. If the caller provides a cursor, meaning “only give me records after this point,” the connector either sends that time to Square or filters records locally. If Square rejects access with a 401 or 403, the stream is skipped with a clear message instead of crashing the whole sync.

#### Function details

##### `SquareConnector._make_client`  (lines 88–91)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Square and adds the required Square API version header. Someone would use it so every request is sent with the version of Square’s API that this connector expects.

**Data flow**: It receives a base URL and a credential. It first asks the parent RestConnector to build the normal authenticated web client, then adds the Square-Version header, and returns that prepared client for later API calls.

**Call relations**: This is part of the connector setup before pages are fetched. Later methods such as SquareConnector.paginate use the resulting client to make requests, and Square sees the pinned API version on each request.


##### `SquareConnector.paginate`  (lines 93–127)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main doorway for reading a Square stream page by page. It decides which Square API pattern is needed for the requested stream and yields lists of records in a shared format.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It checks the stream name, calls the matching helper, and yields each non-empty page of records. For inventory counts it makes a single POST request and extracts the counts. If the stream is unknown or Square refuses access with a 401 or 403 response, it raises StreamSkipped so the sync can move on cleanly.

**Call relations**: The sync runner calls this when it wants records for a Square stream. Inside, it hands work to SquareConnector._locations for locations, SquareConnector._cursor_get for customers, payments, and refunds, SquareConnector._catalog for catalog items and categories, and SquareConnector._orders for orders. It also uses records_at to pull record lists out of Square’s JSON responses, and StreamSkipped to report streams that cannot be read.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 129–146)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that Square exposes as cursor-based GET lists, such as customers, payments, and refunds. It supports continuing from a previous checkpoint so repeated syncs do not have to reread everything.

**Data flow**: It receives the HTTP client, the stream description, and an optional cursor. For payments and refunds, it sends the cursor to Square as begin_time. For other cursor-based streams, it asks Square for pages and then filters out records whose timestamp is not newer than the cursor. It yields only pages that still contain records after filtering.

**Call relations**: SquareConnector.paginate calls this when the requested stream is customers, payments, or refunds. This helper relies on the inherited _get_cursor_pages behavior from RestConnector to walk through Square’s next-page cursor tokens, then passes each cleaned page back up to paginate.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 148–165)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either item objects or category objects and returns them page by page. It exists because catalog data is not read through a simple list endpoint; Square requires a search request body.

**Data flow**: It receives the HTTP client, the catalog stream description, and an optional cursor. It translates the stream name into Square’s catalog object type, sends a /catalog/search POST request with a page limit, and follows Square’s returned cursor token until no more pages remain. If a sync cursor was provided, it keeps only records whose timestamp is newer, then yields non-empty pages.

**Call relations**: SquareConnector.paginate calls this for catalog_items and catalog_categories. This function uses records_at to extract the objects list from Square’s response, then gives each page back to paginate for delivery to the rest of the sync pipeline.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 167–169)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations in one request. Locations matter both as their own stream and as the starting point for reading orders, because Square order searches need location IDs.

**Data flow**: It receives the HTTP client, sends a GET request to /locations, extracts the locations list from the response, and returns that list. It does not page through results because this endpoint is treated as a single-shot collection here.

**Call relations**: SquareConnector.paginate calls this directly when syncing the locations stream. SquareConnector._orders also calls it first so it can learn which location IDs to include in the order search request. In both cases, records_at is used to pull the location records out of the JSON response.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 171–195)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all known account locations. It exists because Square requires order searches to name the locations being searched, so orders cannot be fetched until locations are known.

**Data flow**: It receives the HTTP client and an optional cursor. It first fetches locations, keeps only valid string location IDs, and stops if there are none. Then it repeatedly sends /orders/search POST requests with those location IDs, a page limit, an optional created-at start time from the cursor, and Square’s continuation cursor when present. It extracts orders from each response and yields each non-empty page until Square stops returning a next-page cursor.

**Call relations**: SquareConnector.paginate calls this when syncing the orders stream. This helper first calls SquareConnector._locations to gather the required location IDs, then uses records_at to extract orders from each search response before handing pages back to paginate.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### `extensions/sources/ufo_ext_sources/providers/stripe.py`

`io_transport` · `source sync and page fetching`

Stripe exposes many lists through its web API, and most of them use the same pattern: ask for up to 100 records, then keep asking with the last record’s id until there are no more. This file wraps that pattern in a StripeConnector so the rest of the project does not need to know every Stripe URL or pagination rule.

The file also protects against a common billing-data problem: Stripe records can change after they are first created. For streams that use Stripe’s created time as a cursor, the connector periodically rereads a recent window instead of only reading brand-new records. This is like checking the last few pages of a ledger again, because someone may have corrected an entry after it was written.

Some Stripe data lives under another object, such as invoice line items under invoices or payment methods under customers. The connector first walks the parent list, then asks Stripe for each parent’s child list, adding the parent id to each child record so it remains understandable later.

It also smooths over Stripe-specific errors. Lack of permission skips a stream cleanly. A vanished parent record only skips that one child request. Records are lightly normalized by removing unstable URL fields and adding readable timestamp fields. The connector is read-only; it fetches data but never writes to Stripe.

#### Function details

##### `_stripe_error`  (lines 130–136)

```
def _stripe_error(error: httpx.HTTPStatusError) -> dict[str, Any]
```

**Purpose**: This helper pulls Stripe’s structured error details out of an HTTP error response. It is used when the connector needs to understand whether Stripe named a specific reason, such as a missing resource.

**Data flow**: It receives an HTTP status error from a failed Stripe request. It tries to read the response body as JSON, looks for Stripe’s single error object, and returns that object as a plain dictionary. If the body is not JSON or does not match Stripe’s expected shape, it returns an empty dictionary.

**Call relations**: When a request fails, _refusal_reason uses this helper to build a clearer human-readable failure message. _child_pages also uses it to decide whether a failed child request is safe to skip because the parent object disappeared.

*Call graph*: called by 2 (_child_pages, _refusal_reason).


##### `_refusal_reason`  (lines 139–155)

```
def _refusal_reason(error: httpx.HTTPStatusError) -> str
```

**Purpose**: This helper turns a Stripe client-side error into a short explanation, usually Stripe’s message plus its error code. It avoids storing the full error body, because that body can echo sensitive request details.

**Data flow**: It receives an HTTP status error. If the response is not a client error, it returns an empty string. Otherwise it reads Stripe’s error detail with _stripe_error, extracts the message and optional code, and returns text such as “No such customer [resource_missing]”.

**Call relations**: StripeConnector.paginate calls this after a Stripe request fails. If it gets a useful reason back, paginate raises a StreamFault with that reason so the sync failure record says what Stripe object or parameter was refused.

*Call graph*: calls 1 internal fn (_stripe_error); called by 1 (paginate).


##### `_stream`  (lines 158–176)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: This is a small factory for declaring one Stripe stream, such as customers or invoices. It keeps the long stream list readable by filling in common defaults like the id field and created-time cursor.

**Data flow**: It receives a stream name and optional details such as the Stripe API object name, primary key, cursor field, and timestamp fields. It uses those values to create and return a StreamSpec, which is the system’s description of one readable source of records.

**Call relations**: The module uses this helper while building STRIPE_STREAMS. Those stream definitions are then exposed through StripeConnector.streams_list so the sync runner knows what Stripe collections this connector can read.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector.checkpoint`  (lines 253–264)

```
def checkpoint(self, stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None) -> str | None
```

**Purpose**: This decides what cursor, or saved place in the stream, should be stored after records have been read. It preserves Stripe’s special sweep behavior for created-time streams and delegates other integer-like cursors to the shared watermark helper.

**Data flow**: It receives the stream being synced, the records just read, and the previous cursor. If the stream has no cursor or uses Stripe’s special created cursor, it returns the cursor unchanged. For other cursor fields, it converts the cursor to Unix seconds, feeds the records and normalized cursor to integer_checkpoint, and returns the updated saved position.

**Call relations**: The broader source runner calls this when it needs to save progress. It relies on _cursor_to_unix to understand old cursor formats and on integer_checkpoint to advance ordinary integer timestamp cursors safely.

*Call graph*: calls 1 internal fn (_cursor_to_unix); 1 external calls (integer_checkpoint).


##### `StripeConnector._make_client`  (lines 266–269)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used to talk to Stripe and pins the Stripe API version. Pinning the version keeps Stripe’s response shapes predictable even if Stripe changes its default API behavior later.

**Data flow**: It receives the base URL and a credential object supplied by the authentication layer. It asks the parent RestConnector to create the actual async HTTP client, adds the Stripe-Version header, and returns the prepared client.

**Call relations**: The source framework calls this when setting up network access for the connector. After this, all pagination methods use the returned client for Stripe requests with the expected API version attached.


##### `StripeConnector._list_path`  (lines 272–273)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: This builds the standard Stripe list URL path for a stream. It keeps URL construction consistent for top-level Stripe collections.

**Data flow**: It receives a StreamSpec, reads its source_object value, and returns a path like /v1/customers or /v1/issuing/cards.

**Call relations**: paginate, _created_walk, _parent_pages, _paginate_substream_query, and _paginate_external_accounts call this whenever they need the normal list endpoint for a stream before handing that path to the page-reading logic.

*Call graph*: called by 5 (_created_walk, _paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 276–288)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: This converts a saved cursor into Unix seconds, which means the number of seconds since 1970-01-01 UTC. It lets the connector understand both plain numeric cursors and older ISO timestamp strings.

**Data flow**: It receives a cursor string or nothing. Empty input becomes None. A string of digits becomes an integer. Otherwise it tries to parse the string as a date-time, assumes UTC if no timezone is present, and returns the equivalent Unix timestamp; if parsing fails, it returns None.

**Call relations**: checkpoint, _created_walk, and _page_loop call this before using a saved cursor in Stripe’s created[gte] filter. _created_walk treats an unreadable stored cursor as expired so the driver can clear it instead of failing forever.

*Call graph*: called by 3 (_created_walk, _page_loop, checkpoint); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 290–326)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This is the main dispatcher that chooses how to read a requested Stripe stream. Different Stripe streams need different routes: top-level lists, created-time sweeps, parent-child fan-out, query-based substreams, or external account lists.

**Data flow**: It receives an HTTP client, a stream description, and the saved cursor. It checks the stream name and cursor style, then yields pages from the matching helper. If Stripe refuses access, it turns that into a skipped stream or a clear stream fault instead of leaking a raw HTTP error when possible.

**Call relations**: The source runner calls paginate to get records. paginate hands work to _paginate_external_accounts, _paginate_substream, _paginate_substream_query, _created_walk, or _page_loop, and uses _refusal_reason to turn Stripe’s error format into useful failure text.

*Call graph*: calls 9 internal fn (__init__, __init__, _created_walk, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query, _refusal_reason).


##### `StripeConnector._created_walk`  (lines 328–364)

```
async def _created_walk(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This reads streams whose natural cursor is Stripe’s created timestamp while rereading a recent window from time to time. That prevents updates to recently created records from being missed.

**Data flow**: It receives a client, a stream, and the stored cursor. It converts the cursor to seconds, decides whether a new hourly sweep is due, and builds Stripe requests with limit, starting_after, extra parameters, and sometimes created[gte]. It yields normalized record pages, follows Stripe pagination until the end, then yields a StreamPage containing the next cursor to save.

**Call relations**: paginate calls this for streams whose cursor field is created. It uses _cursor_to_unix to read saved progress, _list_path to find the endpoint, _browse_record to normalize each Stripe record, and StreamPage to tell the runner the new checkpoint only after the walk completes.

*Call graph*: calls 3 internal fn (_browse_record, _cursor_to_unix, _list_path); called by 1 (paginate); 3 external calls (__init__, __init__, now).


##### `StripeConnector._page_loop`  (lines 366–398)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the general Stripe pagination loop. It repeatedly asks Stripe for one page, yields cleaned records, and moves forward using Stripe’s starting_after token.

**Data flow**: It receives a client, URL path, stream, optional cursor, and optional extra query parameters. It builds request parameters, optionally adds a created[gte] filter, fetches a page, normalizes the records, yields them if any exist, and continues until Stripe says there are no more pages or no usable last id is available.

**Call relations**: paginate uses this for simple streams. _parent_pages, _paginate_substream_query, _paginate_external_accounts, and _child_pages use it as their basic page reader before layering parent-child behavior on top.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_child_pages, _paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 401–429)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This makes a raw Stripe record more stable and easier for the rest of the system to compare over time. It removes fields that change for reasons unrelated to the business record and adds readable timestamp fields.

**Data flow**: It receives one Stripe record and the stream definition. It copies the record while dropping volatile URL fields, creates a stable synthetic key for usage record summaries, removes Stripe’s unstable usage-summary id, and converts numeric created or updated times into ISO date-time strings when needed. It returns the normalized record dictionary.

**Call relations**: _created_walk and _page_loop call this before yielding records. Because all major reading paths eventually pass through those two methods, this normalization is applied consistently to top-level records and child records.

*Call graph*: called by 2 (_created_walk, _page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 431–453)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that live under a parent object path, such as a customer’s payment methods or an invoice’s line items. It adds the parent id, and sometimes parent timestamp fields, so each child row keeps its context.

**Data flow**: It receives a client and the child stream definition. It finds the parent stream, walks parent pages, skips parents without ids, builds each child URL by inserting the parent id, reads the child pages, and yields child records enriched with parent information.

**Call relations**: paginate calls this for streams listed in the child-path mapping. It uses _stream_spec to find the parent definition, _parent_pages to enumerate parents, and _child_pages to read each parent’s child collection while tolerating vanished parents.

*Call graph*: calls 3 internal fn (_child_pages, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 455–460)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This supplies pages of parent records for a child stream. It hides whether the parent itself is a normal top-level Stripe list or another query-based substream.

**Data flow**: It receives a client and a parent stream. If that parent is query-based, it returns the query-substream paginator; otherwise it returns the normal page loop for the parent’s list path with no cursor.

**Call relations**: _paginate_substream calls this before fetching children. For nested cases such as usage records, it can hand off to _paginate_substream_query so the connector can walk multiple levels of Stripe relationships.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 462–476)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads child collections that Stripe exposes through a query parameter rather than a nested URL path. For example, it can list subscription items by asking for subscription_items with a particular subscription id.

**Data flow**: It receives a client and the query-child stream definition. It looks up the parent stream name, query field, and child path, walks all parent pages, skips parents without ids, requests the child collection with the parent id as a query parameter, and yields each child record stamped with that parent id.

**Call relations**: paginate calls this for query-based substreams, and _parent_pages calls it when such a substream is itself the parent of another stream. It uses _stream_spec and _list_path to find the parent list, _page_loop to read parents, and _child_pages to read children.

*Call graph*: calls 4 internal fn (_child_pages, _list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 478–491)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads external bank accounts or cards attached to Stripe Connect accounts. It first lists accounts, then asks Stripe for each account’s external accounts and stamps the account id onto each result.

**Data flow**: It receives a client and either the external bank account or external card stream. It gets the accounts stream definition, pages through accounts, skips accounts without ids, builds /v1/accounts/{id}/external_accounts for each account, reads child pages, and yields records with account_id added.

**Call relations**: paginate calls this for the external account streams. It uses _stream_spec and _list_path to walk accounts, _page_loop for the account list, and _child_pages for the per-account external account requests.

*Call graph*: calls 4 internal fn (_child_pages, _list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._child_pages`  (lines 493–532)

```
async def _child_pages(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads one parent object’s child collection and quietly skips cases where that one parent is gone or cannot provide legacy usage summaries. This prevents one stale parent reference from stopping the whole stream.

**Data flow**: It receives a client, child path, stream definition, and optional query parameters. It delegates normal paging to _page_loop and yields each child page. If Stripe returns a known missing-resource error, or the specific message saying an item uses Stripe’s newer meter system instead of legacy usage summaries, it yields nothing for that parent; other errors are raised.

**Call relations**: _paginate_substream, _paginate_substream_query, and _paginate_external_accounts call this after choosing a parent. It uses _stripe_error to inspect Stripe’s error code and decide whether skipping is safe or whether the failure should bubble up.

*Call graph*: calls 2 internal fn (_page_loop, _stripe_error); called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._stream_spec`  (lines 534–535)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: This finds the stream definition with a given name. It is a small lookup helper used when one stream needs to read another stream as its parent.

**Data flow**: It receives a stream name, searches the module’s STRIPE_STREAMS list, and returns the matching StreamSpec. If no match exists, the underlying next call would raise an error.

**Call relations**: _paginate_substream, _paginate_substream_query, and _paginate_external_accounts call this when they need the StreamSpec for a parent collection such as customers, accounts, subscriptions, or setup intents.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).

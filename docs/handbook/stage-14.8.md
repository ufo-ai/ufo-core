# Finance, billing, commerce, and document connectors  `stage-14.8`

This stage is shared behind-the-scenes support for bringing outside business data into the system. Each file is a connector: a small translator that knows how to talk to one company’s web API, meaning its online doorway for requesting data. The connectors ask for records, follow pagination when results come back in batches, and reshape the answers into the system’s common stream of syncable records.

Brex, Ramp, and Mercury bring in spend, card, vendor, transfer, account, and banking transaction data. QuickBooks and Xero bring in accounting records such as invoices, accounts, contacts, and payments. Chargebee, Recurly, and Stripe bring in billing and subscription data, including customers, invoices, subscriptions, coupons, and payments. Square brings in commerce data such as orders, catalog items, locations, inventory, customers, and payments. DocuSign and PandaDoc bring in document, template, envelope, and contact information for later search and recall. Typeform brings in forms, responses, workspaces, themes, images, and webhook settings. Together, these connectors act like plug adapters, making many different services fit the same sync machinery.

## Files in this stage

### Spend and banking connectors
Connectors for corporate spend, card activity, bank accounts, transactions, and related operational finance records.

### `extensions/sources/ufo_ext_sources/providers/brex.py`

`io_transport` · `sync run`

Brex exposes company spending data through web API endpoints, but the rest of this project needs a standard way to ask for records without knowing Brex’s exact URLs or paging rules. This file is that adapter. It defines which Brex objects can be synced, where each object lives in Brex’s API, and how to walk through Brex’s pages of results.

The main idea is like reading a long report one page at a time. Brex returns up to 100 records, plus a “next cursor,” which is a token telling you where the next page starts. The connector keeps asking for the next page until Brex stops giving a cursor.

The file also describes each available stream with a `StreamSpec`, which is a small recipe for a type of data: its name, its unique ID field, and, where available, a date field that can act as a progress marker. Most Brex endpoints do not let the system ask only for recently changed records, so the connector reads full lists and lets the broader sync machinery decide what to do with them.

There is no write behavior here. This connector only reads from Brex using the OAuth access token supplied elsewhere by the source framework.

#### Function details

##### `_stream`  (lines 39–50)

```
def _stream(name: str, *, primary_key: str='id', cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds the standard description for one Brex data stream, such as transactions or vendors. It saves repetition by filling in the common fields Brex streams share.

**Data flow**: It receives a stream name and optional details such as the primary key, cursor date field, and whether the stream is considered canonical. It puts those values into a `StreamSpec`, which is the project’s plain recipe for how to identify and track records in that stream. The result is returned and later collected into the list of all Brex streams.

**Call relations**: This function is used while the file is being loaded to create `BREX_STREAMS`. It hands each completed `StreamSpec` to the connector class through `streams_list`, so the wider source framework knows which Brex record types this connector can fetch.

*Call graph*: 1 external calls (__init__).


##### `BrexConnector.paginate`  (lines 71–88)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This method reads one Brex stream from its API endpoint, one page at a time. It is what lets the sync system fetch all available records without caring about Brex’s cursor-based paging format.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor value from the wider framework. It looks up the correct Brex URL path for that stream, requests up to 100 records, turns the returned `items` value into a safe list, and yields that batch if it contains records. Then it reads Brex’s `next_cursor` token and repeats until there is no next page. If the stream has no known Brex endpoint, it raises an error instead of guessing.

**Call relations**: The source framework calls this method when it wants records for a Brex stream during a sync. Inside the loop, it relies on the inherited `_get` request helper to make the HTTP call, and on `list_or_empty` to normalize Brex’s `items` field before handing batches of records back to the framework.

*Call graph*: 1 external calls (list_or_empty).


### `extensions/sources/ufo_ext_sources/providers/mercury.py`

`io_transport` · `source sync polling`

This connector is the bridge between the app and Mercury, a banking service. Without it, the system would not know where Mercury keeps account and transaction data, how to page through long transaction lists, or how to label those records in a useful way.

The file defines two streams of data: accounts and transactions. Accounts are simple: the connector asks Mercury for the account list once. Transactions are more involved. Mercury returns transactions account by account, and only in pages of up to 100 records. So the connector first fetches all account IDs, then visits each account and keeps asking for the next page of transactions until Mercury returns a short page, which means there is no more data.

For incremental syncing, it uses Mercury's posted date filter. A cursor, meaning the last known position in a previous sync, is sent as a start date. Because Mercury accepts a date rather than an exact transaction position, the connector rereads the whole day where it left off. That can create repeats, but the downstream sync can remove duplicates. Pending transactions do not advance the cursor because they do not have a posted date yet.

If Mercury rejects the API key with a 401 or 403 status, this file turns that into a skipped stream instead of treating it like ordinary data. For display, transactions are titled by their counterparty name, making bank activity easier to recognize than a raw JSON blob.

#### Function details

##### `MercuryConnector.paginate`  (lines 62–82)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Mercury streams. It decides whether the system is asking for accounts or transactions, fetches the right data from Mercury, and yields it in batches for the sync engine to consume.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from the previous sync. For the accounts stream, it fetches Mercury's account list and outputs it as one batch. For the transactions stream, it fetches accounts first, extracts their IDs, then asks Mercury for each account's transaction pages and adds the account ID as extra context to each transaction batch. If Mercury refuses access with an authorization error, it turns that into a StreamSkipped signal; other HTTP errors continue upward unchanged.

**Call relations**: The wider sync process calls this method when it wants records from Mercury. Inside, it relies on _accounts to get account data, _account_ids to find usable account IDs, _transactions to walk through each account's transaction pages, and with_context to attach the account ID so later stages know which account each transaction came from.

*Call graph*: calls 4 internal fn (__init__, _account_ids, _accounts, _transactions); 1 external calls (with_context).


##### `MercuryConnector._accounts`  (lines 84–86)

```
async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This helper fetches the list of Mercury bank accounts. It exists so both the accounts stream and the transactions stream can start from the same reliable account lookup.

**Data flow**: It receives an HTTP client. It sends a request to Mercury's accounts endpoint, reads the 'accounts' field from the response, and returns it as a list. If that field is missing or not in the expected list shape, list_or_empty safely turns it into an empty list instead of letting bad data spread.

**Call relations**: paginate calls this when it is syncing accounts directly and also when it needs account IDs before syncing transactions. The result may be handed to _account_ids so the connector can visit each account's transaction endpoint.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector._account_ids`  (lines 89–94)

```
def _account_ids(accounts: list[dict[str, Any]]) -> list[str]
```

**Purpose**: This helper pulls valid account IDs out of Mercury account records. It filters out missing, empty, or non-text IDs so later API calls are made only with usable account identifiers.

**Data flow**: It receives a list of account dictionaries. It looks at each account's 'id' value, keeps only IDs that are non-empty strings, and returns a clean list of those ID strings. It does not call Mercury or change the original account records.

**Call relations**: paginate uses this after _accounts returns account records. The cleaned IDs become the inputs to _transactions, which needs one account ID at a time to fetch that account's transaction history.

*Call graph*: called by 1 (paginate).


##### `MercuryConnector._transactions`  (lines 96–115)

```
async def _transactions(self, client: httpx.AsyncClient, account_id: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This helper reads one Mercury account's transactions, page by page. It knows how to use Mercury's limit and offset paging, and how to apply the previous sync cursor as a starting date.

**Data flow**: It receives an HTTP client, one account ID, and an optional cursor. It builds request parameters with a page size of 100 and an offset starting at zero. If a cursor is present, it sends only the date part as Mercury's 'start' filter. For each response, it reads the 'transactions' field as a list and yields that page if it contains records. When Mercury returns fewer than 100 records, it stops because that means the account has no further pages.

**Call relations**: paginate calls this once for each account ID found by _account_ids. Each page it yields goes back to paginate, which adds account context before handing the records to the rest of the sync pipeline.

*Call graph*: called by 1 (paginate); 1 external calls (list_or_empty).


##### `MercuryConnector.render`  (lines 117–126)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function chooses a human-friendly title and body for a Mercury record when the system turns it into a recallable page. For transactions, it uses the counterparty name so the record is recognizable by who money moved to or from.

**Data flow**: It receives a Mercury record and the stream it came from. If the record is a transaction and has a non-empty text 'counterpartyName', it returns that name as the title and a JSON-formatted body containing the full record. For accounts or transactions without a usable counterparty name, it falls back to the standard rendering behavior from the base connector.

**Call relations**: The source framework calls this when it needs to present or store a synced Mercury record as text. It uses json.dumps to include the complete transaction details in a stable, sorted JSON form, while leaving non-transaction streams to the parent connector's default rendering.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/ramp.py`

`io_transport` · `source sync polling`

Ramp is an external spend-management service, and its API returns lists in pages, like a long receipt split across many sheets. This file defines the Ramp connector: the piece that knows which Ramp endpoints exist, how to ask for each page, how to follow the “next page” link, and how to label records once they arrive.

Most Ramp collections are fully re-read each sync run. That is safe because the wider driver can skip duplicates it has already seen. Transactions are different: Ramp can filter them by time, so this connector asks only for transactions after the saved cursor and walks them from oldest to newest. That makes transaction syncing incremental, meaning later runs can pick up where earlier runs stopped.

The file also deals with permission gaps. If Ramp replies with 401 or 403, meaning the token is not allowed to read that stream, the connector marks that stream as skipped instead of failing the whole sync. This matters because a Ramp grant may include transactions and users but omit bills or reimbursements.

Finally, it customizes how transactions are displayed. Ramp stores the useful human title as `merchant_name`, so transaction records are titled with the merchant instead of falling back to a generic JSON label.

#### Function details

##### `_stream`  (lines 50–66)

```
def _stream(name: str, *, cursor_field: str | None=None, created_at_field: str | None='created_at', updated_at_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper builds a standard description of one Ramp stream, such as transactions or users. It saves repeated setup code by filling in common details like the primary key and timestamp fields.

**Data flow**: It receives a stream name and optional choices for cursor, creation time, update time, and whether the stream is considered canonical. It uses those values to create a `StreamSpec`, which is the system’s small instruction card for how to treat that stream. The result is returned and later collected into the list of Ramp streams.

**Call relations**: This helper is used while the file is loaded to build `RAMP_STREAMS`. Each call hands its settings to `StreamSpec.__init__`, so the rest of the connector can work from a uniform list of stream descriptions instead of hard-coded one-off rules.

*Call graph*: 1 external calls (__init__).


##### `RampConnector._next_path`  (lines 97–104)

```
def _next_path(next_link: Any) -> str | None
```

**Purpose**: This turns Ramp’s absolute “next page” URL into the path-and-query form needed by the HTTP client already bound to Ramp’s base address. It is a small adapter between Ramp’s pagination style and this connector’s request style.

**Data flow**: It receives whatever Ramp provided as the next-page link. If that value is not a non-empty string, or if it has no URL path, it returns nothing. Otherwise it parses the URL and returns only the path plus query string, such as `/developer/v1/transactions?page=2`, so the next request can use the existing base URL.

**Call relations**: During pagination, `RampConnector.paginate` calls this after each page to decide whether another request is needed. `_next_path` relies on `urllib.parse.urlparse` to split the URL safely, then gives `paginate` either the next request path or a signal to stop.

*Call graph*: called by 1 (paginate); 1 external calls (urlparse).


##### `RampConnector.paginate`  (lines 106–134)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Ramp list endpoints. It requests records page by page, yields batches of records to the sync engine, and knows how to continue until Ramp says there are no more pages.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It looks up the correct Ramp endpoint for that stream, builds query parameters, and for transactions adds ordering plus a `from_date` filter when a cursor exists. It repeatedly fetches a page, pulls the `data` array into a safe list, yields records when present, then follows Ramp’s `page.next` link. If Ramp refuses access with 401 or 403, it changes that failure into a `StreamSkipped` result so the rest of the sync can continue.

**Call relations**: The source-sync framework calls this when it wants records for one Ramp stream. Inside the loop it uses the connector’s HTTP GET helper from the base class, normalizes the returned `data` with `list_or_empty`, and asks `RampConnector._next_path` where to go next. When Ramp reports a permission refusal, it creates `StreamSkipped` to tell the larger sync process that this stream should be recorded as unavailable rather than treated as a broken connector.

*Call graph*: calls 2 internal fn (__init__, _next_path); 1 external calls (list_or_empty).


##### `RampConnector.render`  (lines 136–146)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This decides how a Ramp record should appear when shown or recalled by a person. It gives transactions a useful title based on the merchant name, because Ramp does not store that title under a generic title-like field.

**Data flow**: It receives one Ramp record and the stream it came from. If the stream is not transactions, or if the transaction has no usable `merchant_name`, it falls back to the normal rendering behavior. If there is a merchant name, it returns that merchant as the title and a text body containing a short Ramp heading plus the full record as sorted JSON.

**Call relations**: The wider source system calls this after records have been fetched and need to be turned into recallable text. For transactions, this function uses `json.dumps` to include the complete raw record under a human-friendly merchant title. For all other streams, it hands the job back to the shared rendering behavior from the base connector.

*Call graph*: 1 external calls (dumps).


### Accounting connectors
Connectors for syncing accounting ledgers, invoices, contacts, payments, and other bookkeeping records.

### `extensions/sources/ufo_ext_sources/providers/quickbooks.py`

`io_transport` · `during source sync, while reading pages from QuickBooks`

QuickBooks Online does not offer simple “give me all invoices” style endpoints for each record type. Instead, this connector must send SQL-like search queries to a shared `/query` endpoint, asking for one kind of record at a time. This file is the adapter that turns the system’s normal syncing process into the special shape QuickBooks expects.

It first lists the QuickBooks streams the system can read, such as accounts, customers, invoices, bills, payments, journal entries, tax codes, and more. Each stream says what QuickBooks entity it maps to, what field uniquely identifies a record, and which timestamp can be used to continue a later sync from where the last one stopped.

The `QuickBooksConnector` then builds queries for those streams. If the stream supports incremental syncing, it adds a filter for records updated after the saved cursor. It requests records in pages of 100, like reading a long book 100 lines at a time, and keeps moving forward until QuickBooks returns a shorter page.

One important detail is permissions. If QuickBooks refuses a stream with a 401 or 403 response, the connector marks that stream as skipped instead of treating the whole sync as broken. It also flattens the nested QuickBooks update timestamp so the wider sync system can track progress consistently.

#### Function details

##### `_stream`  (lines 32–47)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None=CURSOR_FIELD, canonical: bool=False) -> StreamSpec
```

**Purpose**: Creates a standard description of one QuickBooks stream, such as invoices or customers. This keeps the long stream list compact and consistent, so every entity is described in the same way.

**Data flow**: It receives a friendly stream name, the exact QuickBooks entity name to query, and optional settings such as whether the stream has an update cursor. It fills in shared defaults like the primary key `Id`, created time, and updated time, then returns a `StreamSpec`, which is the system’s recipe for syncing that stream.

**Call relations**: The file uses this helper while building the QuickBooks stream list. Internally it hands the prepared values to `StreamSpec`, which is the shared source-sync object that the rest of the system understands.

*Call graph*: 1 external calls (__init__).


##### `QuickBooksConnector._build_query`  (lines 88–95)

```
def _build_query(stream: StreamSpec, *, cursor: str | None, start_position: int) -> str
```

**Purpose**: Builds the actual QuickBooks query string for one page of one stream. This is how the connector turns a normal sync request into the SQL-like language QuickBooks expects.

**Data flow**: It receives a stream description, an optional cursor value from a previous sync, and the starting row number for the page. It creates a `SELECT * FROM ...` query, adds a `WHERE` and `ORDER BY` clause when incremental syncing is possible, escapes single quotes in the cursor for safety, then appends the page size instructions. The output is a text query ready to send to QuickBooks.

**Call relations**: `QuickBooksConnector.paginate` calls this each time it needs the next page. The query it returns is then sent to QuickBooks through the connector’s HTTP read path.

*Call graph*: called by 1 (paginate).


##### `QuickBooksConnector.paginate`  (lines 97–119)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Reads one QuickBooks stream page by page. It is the main loop that keeps asking QuickBooks for more records until there are no more to fetch.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. Starting at row 1, it builds a query, sends it to QuickBooks, opens the `QueryResponse` wrapper in the reply, and pulls out the records for the requested entity. Each non-empty batch is yielded to the sync system. If the batch has fewer than 100 records, it stops because that means the stream is exhausted. If QuickBooks refuses access with 401 or 403, it turns that into a `StreamSkipped` signal so the sync can continue with other streams.

**Call relations**: The broader source-sync runner calls this when it wants records for a QuickBooks stream. Inside the loop it relies on `_build_query` to form each request. If QuickBooks reports a permission or authorization refusal, it raises `StreamSkipped`, which tells the caller that this stream should be skipped rather than retried as a general failure.

*Call graph*: calls 2 internal fn (__init__, _build_query).


##### `QuickBooksConnector.flatten`  (lines 121–124)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Makes QuickBooks records easier for the sync system to track by copying a nested cursor value to a flat field name. This matters because the update timestamp lives inside `MetaData`, but the general sync machinery expects to compare a direct field value.

**Data flow**: It receives one QuickBooks record and the stream description. If the stream’s cursor field contains a dot, meaning it points inside nested data such as `MetaData.LastUpdatedTime`, it reads that nested value and returns a copy of the record with an added flat key of the same cursor name. If there is no nested cursor to extract, it returns the record unchanged.

**Call relations**: The sync process uses this after records are fetched so it can advance its saved cursor correctly. To read the nested value, it delegates to `get_path`, a helper that follows dotted paths through dictionaries.

*Call graph*: 1 external calls (get_path).


### `extensions/sources/ufo_ext_sources/providers/xero.py`

`io_transport` · `during source sync, when reading Xero streams`

Xero is an accounting service, but its API has a few special rules. This file is the adapter that knows those rules so the rest of the system does not have to. It defines which Xero resources can be synced, what each resource is called in Xero’s responses, and which field should be treated as the record’s stable ID.

The main class, XeroConnector, is a read-only connector. It opens an HTTP client, adds the required Xero organisation header when needed, asks Xero for pages of data, and yields records back in batches. Xero requires a tenant ID, which means “which organisation inside this Xero login should we read from.” If the connector was not given one, it asks Xero’s connections endpoint to find it. If the grant covers more than one organisation, it refuses to guess, because guessing could sync the wrong company’s books.

The file also handles incremental sync. Instead of asking “give me records after this time” in the URL, Xero expects an If-Modified-Since request header. This file converts saved cursor values into the date format Xero expects. Finally, it normalizes record IDs: Xero uses names like AccountID or InvoiceID, while the rest of the system expects a common id field.

#### Function details

##### `_stream`  (lines 75–89)

```
def _stream(name: str, *, source_object: str, cursor_field: str | None='UpdatedDateUTC', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a standard description of one Xero data stream, such as invoices or contacts. It keeps the stream list concise and consistent by filling in common details like the primary key and update-time field.

**Data flow**: It receives a friendly stream name, the matching Xero response object name, an optional cursor field, and whether the stream is considered canonical. It packages those choices into a StreamSpec, which is the system’s recipe for how to sync that stream.

**Call relations**: This helper is used while building the XERO_STREAMS list at import time. It hands its settings to StreamSpec so the connector later knows what endpoints to call and how to interpret each stream.

*Call graph*: 1 external calls (__init__).


##### `_cursor_to_rfc1123`  (lines 117–134)

```
def _cursor_to_rfc1123(cursor: str | None) -> str | None
```

**Purpose**: This function converts a saved sync cursor into the date text format Xero expects in an If-Modified-Since header. In plain terms, it translates “where we left off last time” into Xero’s preferred clock format.

**Data flow**: It receives a cursor value, which may be empty, a Unix timestamp, an ISO-style date string, or already-formatted text. Empty or unusable values become None. Numeric timestamps and parseable date strings are converted to GMT text like an HTTP date; unparseable text is passed through unchanged.

**Call relations**: XeroConnector.paginate calls this before requesting incrementally updated streams. The converted value is then placed into the request header so Xero can return only records modified since that time.

*Call graph*: called by 1 (paginate); 2 external calls (fromisoformat, fromtimestamp).


##### `XeroConnector.__init__`  (lines 142–143)

```
def __init__(self, tenant_id: str | None=None) -> None
```

**Purpose**: This initializer remembers an optional Xero tenant ID, meaning the specific organisation to read from. Supplying it avoids having to discover the organisation later from the Xero grant.

**Data flow**: It receives an optional tenant ID string. It stores that value on the connector instance so later HTTP clients can include it in their request headers.

**Call relations**: This runs when a XeroConnector is created. Later, XeroConnector._make_client uses the stored tenant ID to prepare the HTTP client, and XeroConnector._ensure_tenant fills it in dynamically if it was not provided.


##### `XeroConnector._make_client`  (lines 145–149)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function creates the HTTP client used to talk to Xero and adds the tenant header if the connector already knows which organisation to read. The tenant header is required by Xero’s Accounting API.

**Data flow**: It receives a base URL and a credential object. It asks the parent RestConnector to make the basic authenticated client, then, if a tenant ID was stored on this connector, adds xero-tenant-id to the client’s default headers. It returns the prepared client.

**Call relations**: The sync framework calls this when setting up a Xero run. It builds on RestConnector’s client creation, then prepares the client for Xero-specific organisation selection before paginate starts making requests.


##### `XeroConnector._ensure_tenant`  (lines 151–179)

```
async def _ensure_tenant(self, client: httpx.AsyncClient) -> None
```

**Purpose**: This function makes sure the HTTP client has the Xero organisation header required for accounting calls. If the tenant was not set ahead of time, it asks Xero which organisations the current grant can access and accepts exactly one.

**Data flow**: It receives an HTTP client. If the client already has a xero-tenant-id header, it does nothing. Otherwise it calls Xero’s connections endpoint, reads the returned list, keeps only organisation tenants, and checks the result. No organisation becomes a StreamFault; multiple organisations also become a StreamFault; exactly one organisation is written into the client headers.

**Call relations**: XeroConnector.paginate calls this before reading any stream. It relies on the shared raw GET behavior from the parent connector and uses list_or_empty to safely treat the response as a list. If the tenant situation is unsafe, it stops the stream by raising StreamFault rather than letting the system sync ambiguous data.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `XeroConnector.paginate`  (lines 181–217)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reader for Xero streams. It requests records from Xero, page by page when needed, and yields batches of records for the sync engine to process.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous sync. It builds the Xero endpoint path, optionally converts the cursor into an If-Modified-Since header, ensures the tenant header is present, then sends GET requests. Small non-paged streams are fetched once. Paged streams are fetched with page numbers until Xero returns no records or fewer than the page size. Each non-empty batch is yielded. If Xero responds with 401 or 403, it turns that refusal into StreamSkipped, meaning this stream is skipped because access is missing or invalid.

**Call relations**: The sync framework calls this whenever it needs records from a Xero stream. It calls _cursor_to_rfc1123 to prepare incremental sync headers, _ensure_tenant to select the organisation, and httpx.AsyncClient.get to make the actual network requests. It hands batches back to the broader RestConnector syncing flow.

*Call graph*: calls 3 internal fn (__init__, _ensure_tenant, _cursor_to_rfc1123); 1 external calls (get).


##### `XeroConnector.flatten`  (lines 219–228)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function normalizes Xero records so each one has the common id field expected by the rest of the system. Xero uses different ID field names for different resources, so this creates a shared shape.

**Data flow**: It receives one record and the stream it came from. If the record already has id, it returns it unchanged. Otherwise it looks up the Xero-specific ID field for that stream, reads the value, and, when present, returns a copy of the record with id added as a string. If no suitable ID is found, it returns the record unchanged.

**Call relations**: The connector framework uses this after records are fetched, before they are keyed and stored. It depends on the file’s ID field override table, which maps stream names like invoices to Xero fields like InvoiceID.


### Billing and subscription connectors
Connectors for subscription platforms and payment systems that expose customers, invoices, subscriptions, coupons, and related billing records.

### `extensions/sources/ufo_ext_sources/providers/chargebee.py`

`io_transport` · `source sync runs`

Chargebee is a billing service, and this connector is the read-only bridge from Chargebee into this project’s source-sync system. Without it, the system would not know how to fetch customers, subscriptions, invoices, transactions, and related records from a tenant’s Chargebee site.

The file starts by naming the supported streams, which are the kinds of records that can be synced. A stream is like a labeled shelf: “customers” go on one shelf, “invoices” on another, and so on. Most streams map directly to a Chargebee list endpoint, such as `/customers` or `/invoices`.

Chargebee returns data in a repeated shape: a response has a `list` of wrapped records and sometimes a `next_offset` token for the next page. This connector follows that token until there are no more pages. For incremental syncing, it can also ask Chargebee for only records newer than the last saved cursor, such as an `updated_at` timestamp.

A few record types are substreams. They do not have their own top-level list. Instead, the connector first reads parent records, such as items, customers, or quotes, then asks Chargebee for each parent’s child records. It stamps the parent ID onto each child so the relationship is not lost.

The connector uses HTTP Basic authentication with the API key as the username. If Chargebee refuses access with a 401 or 403 response, the stream is skipped with a clear explanation rather than failing mysteriously.

#### Function details

##### `_stream`  (lines 57–75)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', created_at_field: str | None='created_at', updated_at_field: str | None='updated
```

**Purpose**: This helper creates a stream description for one Chargebee record type. It keeps the long stream list readable by filling in common defaults, such as using `id` as the primary key and `updated_at` as the usual change-tracking field.

**Data flow**: It receives a stream name and optional details, such as which field identifies a record or which timestamp should be used for incremental syncing. It combines those choices with sensible defaults and returns a `StreamSpec`, which is the project’s compact description of one syncable record type.

**Call relations**: The file uses this helper while building the `CHARGEBEE_STREAMS` list. Each returned stream description is later used by `ChargebeeConnector` to decide which endpoint to call, how to track progress, and how to label records.

*Call graph*: 1 external calls (__init__).


##### `ChargebeeConnector._make_client`  (lines 127–145)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This function builds the HTTP client used to talk to Chargebee. It applies the right base URL, timeout settings, request headers, and authentication method.

**Data flow**: It receives a Chargebee base URL and a resolved credential. It trims the URL, sets JSON/form-friendly headers, and creates an asynchronous HTTP client. If the credential already contains a custom transport, it preserves that path; otherwise, it uses the credential’s API key as the Basic Auth username. If no usable authentication is present, it raises an error.

**Call relations**: The broader source-sync framework calls this when it is ready to open a connection for the connector. The client it returns is then passed into pagination functions, which use it to fetch Chargebee pages.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `ChargebeeConnector.flatten`  (lines 147–159)

```
def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: This function removes Chargebee’s extra wrapper around each record. It turns a shape like `{ "customer": { ... } }` into the customer object itself, while keeping useful added fields such as a parent ID.

**Data flow**: It receives one raw record and the stream description that says which wrapper key to expect. If the expected wrapped object is present, it copies the inner object and merges in any top-level fields that were added by this connector. If the record is not wrapped in that way, it returns the record unchanged.

**Call relations**: After pages are fetched, the source framework can use this function to normalize records before saving or passing them onward. It is especially important for substreams, because their parent IDs are added outside Chargebee’s usual envelope and must not be lost.


##### `ChargebeeConnector.paginate`  (lines 161–192)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading a stream from Chargebee. Given a stream name, it chooses the correct paging strategy and yields batches of records.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor from a previous run. It checks whether the stream is a special substream or a normal list stream, then delegates to the matching helper. It yields each page of raw records as it arrives. If Chargebee answers with 401 or 403, it turns that into a clear `StreamSkipped` message.

**Call relations**: The sync framework calls this when it wants records for a specific stream. This function then hands normal streams to `_paginate_list`, attached items to `_paginate_attached_items`, contacts to `_paginate_contacts`, quote line groups to `_paginate_quote_line_groups`, and scheduled subscription changes to `_paginate_subscription_scheduled`.

*Call graph*: calls 6 internal fn (__init__, _paginate_attached_items, _paginate_contacts, _paginate_list, _paginate_quote_line_groups, _paginate_subscription_scheduled).


##### `ChargebeeConnector._build_list_params`  (lines 195–201)

```
def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This helper builds the query parameters for a normal Chargebee list request. It sets the page size and, when possible, asks only for records after the saved cursor.

**Data flow**: It receives a stream description and an optional cursor value. It starts with a `limit` parameter. If there is both a cursor value and a cursor field for the stream, it adds Chargebee’s `field[after]` filter. The result is a small dictionary of request parameters.

**Call relations**: `_paginate_list` calls this before asking for pages. This keeps the details of Chargebee’s incremental filter syntax in one place instead of spreading it through every endpoint reader.

*Call graph*: called by 1 (_paginate_list).


##### `ChargebeeConnector._paginate_list`  (lines 203–215)

```
async def _paginate_list(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads a normal top-level Chargebee list endpoint, such as customers, invoices, or transactions. It follows Chargebee’s `next_offset` token until the endpoint has no more pages.

**Data flow**: It receives the HTTP client, a stream description, and an optional cursor. It looks up the endpoint path for the stream, builds request parameters, and asks the shared REST pagination helper to fetch pages from the response’s `list` field. Each page of records is yielded to the caller.

**Call relations**: `paginate` uses this for ordinary streams. The substream readers also use it first to fetch parent records, because they need parent IDs before they can ask Chargebee for child records.

*Call graph*: calls 1 internal fn (_build_list_params); called by 5 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups, _paginate_subscription_scheduled, paginate).


##### `ChargebeeConnector._paginate_attached_items`  (lines 217–234)

```
async def _paginate_attached_items(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads attached items, which Chargebee exposes underneath each item rather than as one global list. It walks through items first, then fetches the attached items for each one.

**Data flow**: It receives the HTTP client and an optional cursor. It reads item pages through `_paginate_list`, extracts each item’s ID, and skips any parent without an ID. For every valid item ID, it calls the substream paginator for `/items/{item_id}/attached_items`. The child records it yields are stamped with the parent `item_id`.

**Call relations**: `paginate` calls this when the requested stream is `attached_item`. This function depends on `_paginate_list` to find parent items and `_paginate_substream` to do the repeated child-page fetching.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_contacts`  (lines 236–255)

```
async def _paginate_contacts(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads customer contacts, which are stored under individual customers in Chargebee. It first finds customers, then fetches the contacts for each customer.

**Data flow**: It receives the HTTP client and an optional cursor. It reads customer pages, extracts each customer ID, and ignores any malformed parent record without an ID. For each customer ID, it requests `/customers/{customer_id}/contacts` and yields child pages that include the `customer_id`.

**Call relations**: `paginate` calls this for the `contact` stream. It uses `_paginate_list` for the parent customer loop and `_paginate_substream` for the child contact pages.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_quote_line_groups`  (lines 257–274)

```
async def _paginate_quote_line_groups(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads quote line groups, which are nested under each quote. It preserves the connection back to the quote by adding the quote ID to every child record.

**Data flow**: It receives the HTTP client and an optional cursor. It reads quote pages, extracts each quote ID, and skips parents without a usable ID. For each quote, it reads `/quotes/{quote_id}/quote_line_groups` and yields pages of child records marked with `quote_id`.

**Call relations**: `paginate` calls this when syncing `quote_line_group`. It follows the same parent-then-children pattern as the other substream functions, using `_paginate_list` for quotes and `_paginate_substream` for the nested endpoint.

*Call graph*: calls 2 internal fn (_paginate_list, _paginate_substream); called by 1 (paginate).


##### `ChargebeeConnector._paginate_subscription_scheduled`  (lines 276–298)

```
async def _paginate_subscription_scheduled(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function reads subscriptions together with their scheduled changes. Chargebee exposes this as a one-at-a-time detail request for each subscription, not as a normal child list.

**Data flow**: It receives the HTTP client and an optional cursor. It reads subscription pages, extracts each subscription ID, and skips any parent without an ID. For each subscription, it requests `/subscriptions/{id}/retrieve_with_scheduled_changes`. If the response contains a subscription object, it yields a one-record page containing that object plus the original `subscription_id`.

**Call relations**: `paginate` calls this for `subscription_with_scheduled_changes`. It uses `_paginate_list` to find the parent subscriptions, then makes an individual detail request for each subscription because this Chargebee endpoint does not use the same list-style substream shape.

*Call graph*: calls 1 internal fn (_paginate_list); called by 1 (paginate).


##### `ChargebeeConnector._paginate_substream`  (lines 300–326)

```
async def _paginate_substream(self, client: httpx.AsyncClient, path: str, *, parent_id_field: str, parent_id_value: str) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reusable helper reads a nested Chargebee endpoint that still uses the standard `list` and `next_offset` page format. It also adds the parent ID to each child record so the child can be traced back later.

**Data flow**: It receives an HTTP client, a child endpoint path, the name of the parent-ID field to add, and the parent-ID value. It pages through the endpoint with a fixed page size. For every dictionary-shaped child record, it copies the record, adds the parent ID, and yields the cleaned page if there is anything to return.

**Call relations**: The attached-item, contact, and quote-line-group readers all call this after they have found a parent ID. It centralizes the repeated child-pagination pattern so those functions only need to decide which parent stream and endpoint path to use.

*Call graph*: called by 3 (_paginate_attached_items, _paginate_contacts, _paginate_quote_line_groups).


### `extensions/sources/ufo_ext_sources/providers/recurly.py`

`io_transport` · `source sync / API pagination`

Recurly exposes its data through a web API, but that API has rules the rest of the system should not need to know. This file hides those details behind a RecurlyConnector. Without it, the system would not know how to authenticate to Recurly, which Recurly objects are available to copy, how to follow Recurly’s “next page” links, or how to read child records such as account notes that live under a parent account.

The file first defines the list of Recurly streams, such as accounts, subscriptions, plans, invoices, coupons, and line items. A stream is a named feed of records, with details like its main ID field and the time field used for incremental syncing. Incremental syncing means “only ask for records changed since the last saved point,” instead of downloading everything every time.

The connector builds an HTTP client with Recurly’s required headers and Basic authentication. Recurly uses the API key as the username, which is a little unusual compared with bearer-token APIs.

When asked to paginate a stream, the connector chooses the right route. Most streams are simple top-level lists. Some are “per-parent” streams: it first lists accounts or coupons, then asks for each parent’s child records and stamps the parent ID onto each child row. If Recurly refuses access with 401 or 403, the connector reports the stream as skipped rather than treating the whole sync as a generic crash.

#### Function details

##### `_stream`  (lines 39–53)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='updated_at', canonical: bool=False) -> StreamSpec
```

**Purpose**: This helper creates a StreamSpec, which is the system’s small description of one Recurly data feed. It saves repeated boilerplate when declaring many similar streams.

**Data flow**: It receives a stream name and optional details such as the Recurly object path, primary key, cursor time field, and whether the stream is a main canonical stream. It fills in sensible defaults, then returns a StreamSpec object that the connector later uses to know what to request and how to track progress.

**Call relations**: This function is used while the file is loaded to build the RECURLY_STREAMS list. It hands the normalized stream description to StreamSpec.__init__, and the RecurlyConnector exposes that list to the wider source-sync system.

*Call graph*: 1 external calls (__init__).


##### `RecurlyConnector._make_client`  (lines 83–98)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This builds the HTTP client used to talk to Recurly. It applies Recurly’s required API version header, time limits, and authentication style.

**Data flow**: It receives a base URL and a credential. It trims the URL, prepares request headers, sets connect and read timeouts, then chooses how to authenticate: either by using a provided proxy transport or by sending the API key as a Basic-auth username. It returns an httpx.AsyncClient ready to make Recurly API requests, or raises an error if no usable credential is present.

**Call relations**: The broader RestConnector flow calls this when it needs a network client for the Recurly sync. Inside, it relies on httpx.Timeout, httpx.BasicAuth, and httpx.AsyncClient to create the actual asynchronous web client that later pagination functions use.

*Call graph*: 3 external calls (AsyncClient, BasicAuth, Timeout).


##### `RecurlyConnector._next_path`  (lines 101–112)

```
def _next_path(next_link: str | None) -> str | None
```

**Purpose**: This turns Recurly’s next-page link into a path the connector can request next. It accepts both full URLs and already-relative paths.

**Data flow**: It receives a next link, which may be empty, a full URL, or a path. If there is no link, it returns nothing. If the link is a full URL, it keeps only the path and query string, because the HTTP client already knows the base Recurly address. If the link is already a path, it returns it unchanged.

**Call relations**: Pagination helpers call this after reading each Recurly response. _paginate_top_level, _paginate_per_parent, _account_ids, and _coupon_ids all use it to follow Recurly’s cursor-style page links without accidentally duplicating the base URL.

*Call graph*: called by 4 (_account_ids, _coupon_ids, _paginate_per_parent, _paginate_top_level); 1 external calls (urlparse).


##### `RecurlyConnector._initial_query`  (lines 115–123)

```
def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]
```

**Purpose**: This prepares the query parameters for the first request in a stream. It sets page size, sort order, and, when possible, the starting time for incremental syncing.

**Data flow**: It receives a stream description and an optional saved cursor value. It creates parameters asking Recurly for up to 200 records, sorted oldest-to-newest by the stream’s cursor field or by created_at as a fallback. If the stream has a cursor field and a cursor value was supplied, it adds begin_time so Recurly starts at that point. It returns the parameter dictionary.

**Call relations**: _paginate_top_level and _paginate_per_parent call this before making the first request for a list. After the first request, they stop sending these initial parameters and instead follow Recurly’s own next links.

*Call graph*: called by 2 (_paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector.paginate`  (lines 125–158)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main reading doorway for Recurly streams. Given a stream, it decides how to fetch its pages and yields batches of records to the sync system.

**Data flow**: It receives an HTTP client, a StreamSpec, and an optional cursor. It checks what kind of stream is being read. For child streams under accounts or coupons, it delegates to per-parent pagination. For the special unique_coupons_parent stream, it reads coupons and keeps only bulk coupons. For ordinary streams, it reads the top-level Recurly endpoint. It yields lists of record dictionaries. If Recurly responds with 401 or 403, it turns that into StreamSkipped so the caller understands the stream is unavailable because of permissions or credentials.

**Call relations**: The source-sync framework calls this when it wants records for a Recurly stream. paginate then hands the work to _paginate_top_level or _paginate_per_parent as appropriate, and raises StreamSkipped for permission refusals so the larger sync can make a clear decision.

*Call graph*: calls 3 internal fn (__init__, _paginate_per_parent, _paginate_top_level).


##### `RecurlyConnector._paginate_top_level`  (lines 160–173)

```
async def _paginate_top_level(self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads a normal Recurly list endpoint, one page at a time. It is used for streams like accounts, subscriptions, invoices, and coupons.

**Data flow**: It receives an HTTP client, a stream description, an API path, and an optional cursor. It builds the first query with _initial_query, requests the current page, yields the records if any are present, then checks whether Recurly says more pages exist. If so, it uses _next_path to move to the next page and repeats until there are no more pages.

**Call relations**: paginate calls this for ordinary top-level streams and for the coupon list used by unique_coupons_parent. This helper supplies the simple page-by-page behavior, while paginate decides which endpoint should be read.

*Call graph*: calls 2 internal fn (_initial_query, _next_path); called by 1 (paginate).


##### `RecurlyConnector._account_ids`  (lines 175–186)

```
async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through all Recurly accounts and yields their IDs. It exists so child streams, such as account notes or billing information, can be fetched account by account.

**Data flow**: It starts at the /accounts endpoint with a simple oldest-first query. For each page, it reads the data list and yields the id from each valid account row. If Recurly says there are more pages, it uses _next_path to continue. The output is a sequence of account ID strings.

**Call relations**: _paginate_per_parent calls this when it needs parent IDs for account-based child streams. It provides the parent list, and _paginate_per_parent uses each ID to build the child endpoint URL.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._coupon_ids`  (lines 188–202)

```
async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]
```

**Purpose**: This walks through Recurly coupons and yields only the IDs for bulk coupons. It is used because unique coupon codes live under bulk coupon parents.

**Data flow**: It starts at the /coupons endpoint, reads pages of coupon records, ignores rows without IDs, and skips coupons whose coupon_type is not bulk. For each matching coupon, it yields the coupon ID as a string. It follows more pages using _next_path until Recurly says the list is finished.

**Call relations**: _paginate_per_parent calls this when the child records belong under coupons instead of accounts. It filters parent coupons before child pagination begins, so the connector only asks for unique coupon codes where they can exist.

*Call graph*: calls 1 internal fn (_next_path); called by 1 (_paginate_per_parent).


##### `RecurlyConnector._paginate_per_parent`  (lines 204–232)

```
async def _paginate_per_parent(self, client: httpx.AsyncClient, stream: StreamSpec, *, parent_path: str, child_path: str, stamp_field: str, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads streams that are nested under another Recurly object. For example, it can list accounts first, then fetch each account’s notes and attach the account ID to those note records.

**Data flow**: It receives the HTTP client, stream description, parent path, child path, name of the parent-ID field to add, and optional cursor. It chooses either account IDs or bulk coupon IDs as parents. For each parent ID, it builds the child URL, prepares the first query with _initial_query, fetches pages of child records, stamps the parent ID into each dictionary record when missing, and yields each non-empty batch. It follows Recurly’s next links with _next_path until each parent’s child pages are complete.

**Call relations**: paginate calls this for the special per-parent streams listed in the file’s mapping. This helper depends on _account_ids or _coupon_ids to find parents, and on _initial_query and _next_path to read each child collection page by page.

*Call graph*: calls 4 internal fn (_account_ids, _coupon_ids, _initial_query, _next_path); called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/stripe.py`

`io_transport` · `active during Stripe source sync`

Stripe exposes its data through many web API endpoints, but they all have slightly different names and some records live underneath other records. This file is the Stripe “source connector”: it knows which Stripe collections exist, where to fetch them, how to page through long lists, and how to attach helpful parent IDs when a record comes from inside another record.

The file starts by defining the Stripe streams the system can read. A stream is a named feed of records, like “customers” or “invoice_line_items.” Most streams can be fetched directly from Stripe. Others are substreams, meaning the connector must first fetch parent records and then ask Stripe for each parent’s children. For example, invoice line items are fetched by walking invoices first, then fetching each invoice’s lines. This is like checking every folder in a filing cabinet and copying the papers inside, while writing the folder name on each paper.

The main class, `StripeConnector`, builds an HTTP client for Stripe, adds the pinned Stripe API version header, and provides pagination. Pagination means repeatedly asking Stripe for the next page of up to 100 records until there are no more. It can also start from a saved time cursor when Stripe supports that. If Stripe refuses access with an authorization error, the connector marks that stream as skipped rather than crashing the whole sync. The connector only reads data; it deliberately has no write path.

#### Function details

##### `_stream`  (lines 89–107)

```
def _stream(name: str, *, source_object: str | None=None, primary_key: str='id', cursor_field: str | None='created', created_at_field: str | None='created', updated_at_field: str | None=None, canonica
```

**Purpose**: Creates a `StreamSpec`, which is the system’s short description of one Stripe record feed. It lets the file declare many Stripe streams in a compact, consistent way.

**Data flow**: It receives a stream name and optional details such as the Stripe endpoint name, primary key, cursor field, and timestamp fields. It fills in sensible defaults, then returns a `StreamSpec` object that the connector later uses to know what to fetch and how to interpret it.

**Call relations**: This helper is used while building the module-level `STRIPE_STREAMS` list. Those stream descriptions are then attached to `StripeConnector` so the broader sync runner knows which Stripe feeds are available.

*Call graph*: 1 external calls (__init__).


##### `StripeConnector._make_client`  (lines 182–185)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used to talk to Stripe and adds the exact Stripe API version this connector expects. Pinning the API version helps keep Stripe responses stable even if Stripe changes defaults later.

**Data flow**: It receives a base URL and a credential object supplied by the surrounding authentication system. It asks the parent REST connector to create the normal client, adds the `Stripe-Version` header, and returns the prepared client.

**Call relations**: The base connector calls this when it needs a network client for a sync. After this point, all Stripe requests made through the client carry the pinned API version.


##### `StripeConnector._list_path`  (lines 188–189)

```
def _list_path(stream: StreamSpec) -> str
```

**Purpose**: Turns a stream description into the standard Stripe list endpoint path. It is used for ordinary top-level Stripe collections.

**Data flow**: It receives a `StreamSpec` with a `source_object` such as `customers` or `checkout/sessions`. It prefixes that value with `/v1/` and returns the path to request from Stripe.

**Call relations**: Pagination code calls this whenever it needs the normal list URL for a stream. Specialized flows for parent records, query-based substreams, external accounts, and ordinary streams all use it before handing the path to `_page_loop`.

*Call graph*: called by 4 (_paginate_external_accounts, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._cursor_to_unix`  (lines 192–204)

```
def _cursor_to_unix(cursor: str | None) -> int | None
```

**Purpose**: Converts a saved cursor value into the Unix timestamp format Stripe expects for time filtering. A Unix timestamp is a number of seconds since January 1, 1970.

**Data flow**: It receives a cursor that may be missing, already numeric, or written as an ISO date string. It returns an integer timestamp when it can understand the input, or `None` when there is no usable time value.

**Call relations**: `_page_loop` calls this before making requests. If a stream uses Stripe’s `created` field as its cursor, the returned timestamp becomes Stripe’s `created[gte]` filter so the sync can avoid rereading older records.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromisoformat).


##### `StripeConnector.paginate`  (lines 206–232)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Chooses the right fetching strategy for a Stripe stream and yields pages of records. This is the main doorway the rest of the sync system uses to read Stripe data.

**Data flow**: It receives an HTTP client, a stream description, and an optional saved cursor. It checks whether the stream is an external-account stream, a path-based substream, a query-based substream, or a normal top-level stream. It then delegates to the matching paginator and yields each page it gets back. If Stripe returns a permission refusal, it converts that into a skipped stream message.

**Call relations**: The sync runner calls `paginate` when it wants records for one Stripe stream. `paginate` then hands work to `_paginate_external_accounts`, `_paginate_substream`, `_paginate_substream_query`, or `_page_loop`, depending on the stream shape.

*Call graph*: calls 6 internal fn (__init__, _list_path, _page_loop, _paginate_external_accounts, _paginate_substream, _paginate_substream_query).


##### `StripeConnector._page_loop`  (lines 234–265)

```
async def _page_loop(self, client: httpx.AsyncClient, path: str, stream: StreamSpec, *, cursor: str | None, extra_params: dict[str, str] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Walks through Stripe’s standard paged list responses until all available records are read. It is the reusable engine behind most Stripe fetching in this file.

**Data flow**: It receives a client, an API path, a stream description, a cursor, and optional extra query parameters. It builds request parameters such as page size, cursor time filter, Stripe-specific extras, and `starting_after` for the next page. For each Stripe response, it normalizes each record with `_browse_record`, yields a non-empty page, and continues while Stripe says there are more records.

**Call relations**: Most higher-level pagination methods eventually call `_page_loop`. It calls `_cursor_to_unix` to prepare time filtering and `_browse_record` to make records easier for the rest of the system to consume.

*Call graph*: calls 2 internal fn (_browse_record, _cursor_to_unix); called by 5 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query, _parent_pages, paginate).


##### `StripeConnector._browse_record`  (lines 268–288)

```
def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]
```

**Purpose**: Makes a Stripe record’s timestamp fields easier and more consistent for downstream use. Stripe often returns times as raw numbers, while other parts of the system prefer readable timestamp strings.

**Data flow**: It receives one Stripe record and the stream description. It copies the record, converts numeric `created_at` and `updated_at` values into ISO timestamp strings, and fills missing `created_at` or `updated_at` from Stripe’s `created` field or the stream cursor when possible. It returns the normalized copy without changing the original record.

**Call relations**: `_page_loop` calls this for every record fetched from Stripe. The normalized records are then yielded upward to whichever pagination path requested them.

*Call graph*: called by 1 (_page_loop); 1 external calls (fromtimestamp).


##### `StripeConnector._paginate_substream`  (lines 290–312)

```
async def _paginate_substream(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child records that live under parent records in Stripe URLs. For example, it can fetch each customer’s payment methods or each invoice’s line items.

**Data flow**: It receives a client and a child stream description. It looks up the parent stream and the child URL pattern, reads parent pages, then for each parent with an ID fetches that parent’s child pages. It stamps the parent ID, and sometimes selected parent timestamp fields, onto each child record before yielding the child page.

**Call relations**: `paginate` calls this for streams listed as path-based substreams. It uses `_stream_spec` to find the parent stream, `_parent_pages` to read parents, and `_page_loop` to read each child collection.

*Call graph*: calls 3 internal fn (_page_loop, _parent_pages, _stream_spec); called by 1 (paginate).


##### `StripeConnector._parent_pages`  (lines 314–319)

```
def _parent_pages(self, client: httpx.AsyncClient, parent_stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Provides pages of parent records for substream fetching. It hides whether the parent itself is a normal stream or another query-based substream.

**Data flow**: It receives a client and a parent stream description. If the parent stream must itself be fetched through a query fan-out, it returns that specialized paginator. Otherwise, it builds the normal list path and returns the standard page loop for the parent stream.

**Call relations**: `_paginate_substream` calls this before fetching child records. It may hand off to `_paginate_substream_query` for nested cases, or to `_page_loop` for ordinary parent streams.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _paginate_substream_query); called by 1 (_paginate_substream).


##### `StripeConnector._paginate_substream_query`  (lines 321–335)

```
async def _paginate_substream_query(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches child records that Stripe exposes by passing the parent ID as a query parameter rather than embedding it in the URL path. For example, subscription items are fetched by asking for items with a particular subscription ID.

**Data flow**: It receives a client and a stream description. It looks up the parent stream name, query parameter name, and child path. It fetches parent pages, then for each parent ID requests child pages with that ID in the query string. It adds a `<query_field>_id` field to each child record before yielding it.

**Call relations**: `paginate` calls this directly for query-based substreams. `_parent_pages` can also call it when a path-based child has a parent that is itself query-based, allowing nested fan-outs such as usage records.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 2 (_parent_pages, paginate).


##### `StripeConnector._paginate_external_accounts`  (lines 337–350)

```
async def _paginate_external_accounts(self, client: httpx.AsyncClient, stream: StreamSpec) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches external bank accounts or cards attached to Stripe connected accounts. These records require first walking accounts, then asking each account for its external accounts.

**Data flow**: It receives a client and the external-account stream description. It fetches all account pages, skips accounts without IDs, then calls each account’s external accounts endpoint. Each returned child record is copied with the parent `account_id` added before the page is yielded.

**Call relations**: `paginate` calls this for the two external-account streams. It uses `_stream_spec` to find the accounts stream, `_list_path` to build the accounts endpoint, and `_page_loop` to read both accounts and their external-account children.

*Call graph*: calls 3 internal fn (_list_path, _page_loop, _stream_spec); called by 1 (paginate).


##### `StripeConnector._stream_spec`  (lines 352–353)

```
def _stream_spec(self, name: str) -> StreamSpec
```

**Purpose**: Finds the stream description with a given name. It is a small lookup helper used when one stream needs to know about another stream.

**Data flow**: It receives a stream name as text. It searches the connector’s Stripe stream list and returns the matching `StreamSpec`; if no match exists, the normal Python lookup behavior raises an error.

**Call relations**: Substream paginators call this when they need the parent stream definition. That returned definition is then used to build parent paths and feed parent records into the child-fetching flow.

*Call graph*: called by 3 (_paginate_external_accounts, _paginate_substream, _paginate_substream_query).


### Commerce connectors
Connectors for commerce data such as customers, payments, locations, catalog items, orders, and inventory.

### `extensions/sources/ufo_ext_sources/providers/square.py`

`io_transport` · `during source sync runs`

Square exposes different kinds of data in different ways. Some records are fetched with simple list requests, some require search requests, and orders must be searched separately across all known locations. This file hides those differences behind one connector, so the wider system can simply ask for a named stream and receive batches of records.

The file defines which Square streams exist and what field identifies or orders each record. The SquareConnector then builds an HTTP client for Square’s API and adds the required Square API version header, which is like telling Square, “Please speak this exact version of your language.”

The main entry point is paginate. It looks at the requested stream name and sends the work to the right helper. Locations are fetched once. Customers, payments, and refunds use cursor-based list endpoints. Catalog items and categories use Square’s catalog search endpoint. Orders first fetch locations, then search orders for those locations. Inventory counts are fetched with a single batch request.

A key behavior is that permission failures, such as missing Square scopes or an invalid key, do not crash the whole sync as ordinary errors. They become StreamSkipped, meaning this particular stream is politely skipped. The connector is read-only; it only pulls data from Square and does not write anything back.

#### Function details

##### `SquareConnector._make_client`  (lines 86–89)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: This prepares the HTTP client used to talk to Square. It adds Square’s required API version header so every request is interpreted using the pinned Square API version.

**Data flow**: It receives a base URL and a credential. It asks the parent REST connector to build the normal authenticated client, then adds the Square-Version header to that client. The result is an HTTP client ready to make Square API calls.

**Call relations**: This is part of the connector setup before any stream is read. The broader REST connector machinery calls it when creating a client, and the returned client is later passed into paginate and the helper methods that make actual Square requests.


##### `SquareConnector.paginate`  (lines 91–125)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the dispatcher that turns a requested Square stream into pages of records. Someone uses it when they want to sync one Square data type without caring which Square endpoint or paging style that type uses.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor, which is the last-seen point from a previous sync. It checks the stream name, calls the appropriate helper, and yields lists of records as pages. If Square says access is forbidden or unauthorized, it changes that failure into a StreamSkipped result so the stream can be skipped cleanly.

**Call relations**: The sync framework calls paginate when it is time to read a stream. paginate then hands off to _locations, _cursor_get, _catalog, or _orders depending on the stream. For inventory counts it makes the request directly and uses records_at to pull the counts list out of Square’s response. If no matching implementation exists, it raises StreamSkipped to tell the caller that this stream cannot be read here.

*Call graph*: calls 5 internal fn (__init__, _catalog, _cursor_get, _locations, _orders); 1 external calls (records_at).


##### `SquareConnector._cursor_get`  (lines 127–144)

```
async def _cursor_get(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square streams that use a simple cursor-based list pattern, such as customers, payments, and refunds. A cursor is a marker that lets the next request continue where the previous page left off.

**Data flow**: It receives an HTTP client, a stream description, and an optional last-seen cursor. For payments and refunds, it sends the cursor to Square as a begin_time filter. For other streams, it asks Square for pages and then locally keeps only records newer than the cursor. It yields each non-empty page of records.

**Call relations**: paginate calls this helper for customers, payments, and refunds. This helper relies on the shared REST connector’s page-walking method to do the repeated HTTP requests, then applies Square-specific filtering rules before handing pages back up to paginate.

*Call graph*: called by 1 (paginate).


##### `SquareConnector._catalog`  (lines 146–163)

```
async def _catalog(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This searches Square’s catalog for either items or categories and returns matching records page by page. It exists because catalog data uses a POST search endpoint rather than a simple list endpoint.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor. It converts the stream name into Square’s object type, sends a catalog search request with a page limit, extracts returned objects, filters out records that are not newer than the cursor when needed, and yields non-empty pages. If Square returns another cursor token, it repeats; otherwise it stops.

**Call relations**: paginate calls this helper for catalog_items and catalog_categories. Inside the loop, _catalog uses records_at to pull the objects list out of Square’s response, then returns those pages to paginate for the sync framework to consume.

*Call graph*: called by 1 (paginate); 1 external calls (records_at).


##### `SquareConnector._locations`  (lines 165–167)

```
async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]
```

**Purpose**: This fetches the Square account’s locations. Locations matter both as their own stream and as the starting point for order searches, because Square orders are tied to locations.

**Data flow**: It receives an HTTP client, sends a GET request to Square’s locations endpoint, and extracts the locations list from the response. It returns that list of location records.

**Call relations**: paginate calls _locations directly when syncing the locations stream. _orders also calls it first so it can discover which location IDs to include in the order search request.

*Call graph*: called by 2 (_orders, paginate); 1 external calls (records_at).


##### `SquareConnector._orders`  (lines 169–193)

```
async def _orders(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Square orders across all accessible locations. It first discovers the locations because Square’s order search needs location IDs before it can return orders.

**Data flow**: It receives an HTTP client and an optional cursor. It fetches locations, keeps only valid location IDs, and stops if there are none. It then repeatedly posts to Square’s order search endpoint with those location IDs, an optional created-at start filter based on the cursor, and any continuation cursor returned by Square. Each response’s orders are extracted and yielded as pages until there is no next cursor.

**Call relations**: paginate calls _orders when the requested stream is orders. _orders calls _locations to get the required location IDs, uses records_at to extract orders from each response, and passes each page back to paginate for the rest of the sync pipeline.

*Call graph*: calls 1 internal fn (_locations); called by 1 (paginate); 1 external calls (records_at).


### Document and form connectors
Connectors for e-signature, document-template, contact, form, and response data that becomes searchable synced content.

### `extensions/sources/ufo_ext_sources/providers/docusign.py`

`io_transport` · `scheduled source sync`

DocuSign does not have one universal data server for every customer. A user signs in through a shared identity service, but their actual envelope and template data lives on a regional account URL. This connector first asks DocuSign who the signed-in user is, then chooses the right account and regional base address before fetching anything else.

The connector exposes two streams: envelopes and templates. Envelopes are synced incrementally, meaning later runs can ask only for items changed since the last saved cursor. On the first run, it asks from a fixed old date so it does not accidentally miss historical envelopes. Templates are listed without a cursor.

Both streams are fetched in pages of 100 records. This is like reading a long report 100 rows at a time until the final page is shorter than 100 rows, which means there is no more to read. For envelopes, the connector also asks DocuSign to include recipients and custom fields so the synced record contains useful signing context.

If DocuSign refuses access with an authorization error, the stream is skipped with a clear explanation instead of crashing the whole sync. When rendering envelopes into readable pages, the connector uses the envelope email subject as the title, because that is the human-facing name recipients saw.

#### Function details

##### `DocuSignConnector._account_base`  (lines 79–109)

```
async def _account_base(self, client: httpx.AsyncClient) -> str
```

**Purpose**: This function finds the exact DocuSign account and regional API address that should be used for data calls. It prevents the system from guessing when one login has access to several DocuSign accounts.

**Data flow**: It receives an HTTP client and asks DocuSign's shared identity endpoint for user information. From the returned accounts, it keeps only accounts that have both an account ID and a base URI. If there is one usable account, it chooses it; if there are several, it chooses the single default account; if there is none or the choice is ambiguous, it raises a clear sync fault. The output is a full REST API base URL for the chosen account.

**Call relations**: This is called by DocuSignConnector.paginate before any envelopes or templates are fetched. It uses list_or_empty to safely treat missing account lists as empty, and it raises StreamFault when continuing would mean syncing the wrong account or no account at all.

*Call graph*: calls 1 internal fn (__init__); called by 1 (paginate); 1 external calls (list_or_empty).


##### `DocuSignConnector.paginate`  (lines 111–143)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This function fetches DocuSign envelopes or templates a page at a time. It is the main reading loop that turns DocuSign API responses into batches of records for the rest of the sync system.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing where the last sync left off. It chooses the correct DocuSign list endpoint, asks _account_base for the account-specific API URL, then repeatedly requests 100 records starting at the current position. For envelopes, it adds the from-date cursor, recipient and custom-field includes, and ascending order. Each non-empty page is yielded outward, and the loop stops when DocuSign returns fewer than 100 records. If DocuSign returns a 401 or 403 refusal, it turns that into a StreamSkipped message explaining the likely permission problem.

**Call relations**: The sync framework calls this when it needs records for a DocuSign stream. Before paging, it calls DocuSignConnector._account_base so it talks to the right regional account URL. It uses list_or_empty to safely extract the record list from each response, and it raises StreamSkipped for permission refusals so the larger sync can continue cleanly where appropriate.

*Call graph*: calls 2 internal fn (__init__, _account_base); 1 external calls (list_or_empty).


##### `DocuSignConnector.render`  (lines 145–154)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This function turns a raw DocuSign record into a title and page body for recall. For envelopes, it gives the page the same subject line that recipients saw in email.

**Data flow**: It receives one DocuSign record and the stream it came from. If the stream is envelopes and the record has a usable emailSubject, it returns that subject as the title and a simple Markdown body containing the JSON record. Otherwise, it falls back to the standard rendering behavior from the parent connector.

**Call relations**: The sync system uses this after records have been fetched to make them readable and searchable. This function only customizes envelope naming; it uses json.dumps to include the complete record in a stable text form, while non-envelope records are handed back to the shared rendering path.

*Call graph*: 1 external calls (dumps).


### `extensions/sources/ufo_ext_sources/providers/pandadoc.py`

`io_transport` · `during source sync polling`

This connector is the bridge between UFO and PandaDoc. PandaDoc stores business documents, templates, and contacts, but the rest of the system needs a standard way to fetch them. This file defines that standard shape: which PandaDoc objects exist, how to authenticate, how to page through lists, and how to enrich document rows with fuller detail.

The connector reads only. It does not create or edit PandaDoc data. Think of it like a careful librarian who can copy index cards from PandaDoc, but cannot rewrite the books.

Most PandaDoc list endpoints return results in pages of up to 100 items. The connector keeps asking for page 1, page 2, and so on until PandaDoc returns a short page, which means there is nothing more to fetch. Documents get special treatment: their list response is thin, so each document is opened again through a details endpoint to collect fields, tokens, pricing, and recipients. If one document cannot be opened, the sync keeps going and saves the basic list row instead.

Authentication also has one PandaDoc-specific wrinkle. If the credential contains a user-provided key, this file sends it as PandaDoc’s `API-Key` authorization style, not as the more common bearer token style. If PandaDoc refuses access to an entire stream, the stream is skipped rather than crashing the whole sync.

#### Function details

##### `_stream`  (lines 37–51)

```
def _stream(name: str, *, cursor_field: str | None=None, canonical: bool=False) -> StreamSpec
```

**Purpose**: Builds the small description object that tells the sync system what a PandaDoc stream looks like. A stream is a named collection of records, such as documents or contacts, with hints about IDs and timestamps.

**Data flow**: It receives a stream name and optional settings, such as which field should be used as the change cursor and whether this is a main, standard stream. It fills in the common PandaDoc field names, like `id`, `date_created`, and `date_modified`, and returns a `StreamSpec` object that the rest of the sync system can understand.

**Call relations**: This helper is used while the file defines `PANDADOC_STREAMS`. It hands its choices to `StreamSpec.__init__`, which packages them into the shared stream description format used by the source framework.

*Call graph*: 1 external calls (__init__).


##### `PandaDocConnector._make_client`  (lines 66–72)

```
def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to PandaDoc and applies PandaDoc’s special authentication format when needed. This matters because PandaDoc expects user API keys to be sent as `API-Key`, not as a normal bearer token.

**Data flow**: It receives the PandaDoc base URL and a resolved credential. First it asks the parent connector to build the normal HTTP client. Then, if the credential contains a bearer value, it writes an `Authorization` header in PandaDoc’s required `API-Key ...` form. It returns the ready-to-use client.

**Call relations**: This method plugs into the shared REST connector setup. The common code creates the base client, while this override adds the PandaDoc-specific header only when the credential needs it; broker-provided transports can still inject their own authentication without being disturbed.


##### `PandaDocConnector.paginate`  (lines 74–103)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: Fetches one PandaDoc stream page by page and yields batches of records to the sync system. For documents, it also expands each list item with the richer details that users are likely to search for later.

**Data flow**: It receives an HTTP client, a stream description, and an optional cursor showing the last synced change point. It chooses the right PandaDoc endpoint, requests pages of 100 records, turns the returned `results` value into a safe list, and yields each non-empty batch. For documents, it adds sorting by modification date and, when a cursor exists, asks PandaDoc only for documents modified since that cursor. It stops when a page has fewer than 100 records. If PandaDoc refuses access with 401 or 403, it raises `StreamSkipped` so this stream can be skipped cleanly.

**Call relations**: When the source framework needs records for a PandaDoc stream, this is the method that supplies them. It uses `list_or_empty` to normalize PandaDoc’s response, calls `PandaDocConnector._details` for each document row that needs enrichment, and raises `StreamSkipped` when the API says the current credential is not allowed to read that stream.

*Call graph*: calls 2 internal fn (__init__, _details); 1 external calls (list_or_empty).


##### `PandaDocConnector._details`  (lines 105–117)

```
async def _details(self, client: httpx.AsyncClient, record: dict[str, Any]) -> dict[str, Any]
```

**Purpose**: Adds full document information to a basic document row. This is needed because PandaDoc’s document list only includes summary data, while the details endpoint includes the richer contents people may want to recall.

**Data flow**: It receives an HTTP client and one document record from the list endpoint. If the record has no usable string `id`, it returns the record unchanged. Otherwise it asks PandaDoc for `/details` for that document. If the detail request succeeds, it merges the original list row and the detail response into one fuller record. If PandaDoc says this individual document is forbidden or missing, it returns the original row so one bad document does not stop the whole sync.

**Call relations**: This function is called by `PandaDocConnector.paginate` only for document records. It hands enriched records back to `paginate`, which then yields them as part of the current batch.

*Call graph*: called by 1 (paginate).


### `extensions/sources/ufo_ext_sources/providers/typeform.py`

`io_transport` · `source sync`

Typeform stores useful customer data behind a web API, but that API does not give everything in one simple request. Some lists come back in numbered pages, responses must be fetched separately for each form, and webhooks are also tied to individual forms. This file is the adapter that knows those rules.

The main class, TypeformConnector, is a read-only connector. It does not store an API token itself; the wider runner supplies authenticated HTTP access. The connector declares which Typeform streams exist, what field identifies each record, and which time field can be used for incremental syncing. Incremental syncing means “only ask for things newer than the last successful run,” like checking mail since yesterday instead of rereading the whole mailbox.

When the sync runner asks for a stream, paginate chooses the right route. Simple streams like workspaces, images, and themes use normal numbered pages. Forms are also paged, but can be filtered by last update time. Responses are more involved: the connector first reads all forms, then asks Typeform for responses for each form, adding the form id and title to every response so the record keeps its context. Webhooks follow the same “first forms, then per-form detail” pattern.

If Typeform refuses access with a 401 or 403 error, the connector marks that stream as skipped instead of crashing the whole sync.

#### Function details

##### `TypeformConnector.record_ref`  (lines 52–56)

```
def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None
```

**Purpose**: This chooses the human-readable reference used for a synced Typeform record. For most streams it uses the standard behavior, but for webhooks it uses the webhook tag because that is the useful label Typeform provides.

**Data flow**: It receives one Typeform record and the stream it came from. If the stream is not webhooks, it passes the decision to the parent connector. If it is webhooks, it reads the record's tag field and returns it as text when it is a string or number; otherwise it returns nothing.

**Call relations**: The broader connector framework calls this when it needs a stable display reference for a record. This method only customizes the webhook case and otherwise hands the work back to the shared RestConnector behavior.


##### `TypeformConnector.paginate`  (lines 58–85)

```
async def paginate(self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This is the main traffic director for reading Typeform streams. Given a stream name, it decides which Typeform API-walking method should be used and yields batches of records to the sync runner.

**Data flow**: It receives an authenticated HTTP client, a stream description, and an optional cursor value from a previous sync. It checks the stream name, calls the matching helper, and passes each batch of records onward. If the stream is unknown, or if Typeform refuses access with a 401 or 403 status, it raises StreamSkipped so the run can continue without that stream.

**Call relations**: The sync framework calls paginate when it wants records from Typeform. paginate then delegates to _forms, _responses, _paged_items, or _webhooks depending on the stream. It also translates access-denied HTTP errors into a clean skipped-stream signal.

*Call graph*: calls 5 internal fn (__init__, _forms, _paged_items, _responses, _webhooks).


##### `TypeformConnector._paged_items`  (lines 87–105)

```
async def _paged_items(self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None=None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Typeform endpoints that return a simple list of items split across numbered pages. It keeps asking for the next page until Typeform says there are no more.

**Data flow**: It receives an HTTP client, an API path such as /forms, and optional query parameters. It adds page and page_size values, requests the page, pulls the items list out of the response, and yields that list when it is not empty. It stops when it reaches Typeform's reported page count, or when a short page suggests the end of the list.

**Call relations**: paginate uses this directly for simple streams such as workspaces, images, and themes. _forms also uses it as the basic way to read the forms list before applying any extra filtering.

*Call graph*: called by 2 (_forms, paginate); 1 external calls (records_at).


##### `TypeformConnector._forms`  (lines 107–114)

```
async def _forms(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads Typeform forms and optionally filters them to only those updated after a saved cursor. It is the common starting point for form-based streams.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. It gets pages of forms through _paged_items, and if a cursor is present it keeps only forms whose last_updated_at value is later than that cursor. It yields each remaining non-empty batch.

**Call relations**: paginate calls _forms when syncing the forms stream. _responses and _webhooks also call it because both need to know which forms exist before they can fetch per-form responses or webhooks.

*Call graph*: calls 1 internal fn (_paged_items); called by 3 (_responses, _webhooks, paginate).


##### `TypeformConnector._responses`  (lines 116–138)

```
async def _responses(self, client: httpx.AsyncClient, *, cursor: str | None) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads submitted answers from Typeform, form by form. It adds the form id and title to each response so a response is not separated from the form it belongs to.

**Data flow**: It receives an HTTP client and an optional cursor timestamp. First it reads all forms without filtering by form update time. For each valid form id, it asks the form's responses endpoint for response pages. If a cursor exists, it sends it as a since parameter so Typeform returns newer responses. Each response batch is then enriched with form_id and form_title before being yielded.

**Call relations**: paginate calls _responses for the responses stream. _responses depends on _forms to discover the forms, then uses the shared cursor-page helper from the parent connector for Typeform's response pagination, and uses with_context to attach form details to each response.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 1 external calls (with_context).


##### `TypeformConnector._webhooks`  (lines 140–149)

```
async def _webhooks(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]
```

**Purpose**: This reads configured webhooks for every Typeform form. Since webhooks are attached to individual forms, it must visit each form and ask for that form's webhook list.

**Data flow**: It receives an HTTP client. It reads all forms, skips any form without a usable id, fetches /forms/{form_id}/webhooks for each remaining form, extracts the items list, and yields those webhook records after adding the form id and title as context.

**Call relations**: paginate calls _webhooks for the webhooks stream. _webhooks uses _forms to find the forms first, records_at to pull webhook records from Typeform's response shape, and with_context to preserve which form each webhook came from.

*Call graph*: calls 1 internal fn (_forms); called by 1 (paginate); 2 external calls (records_at, with_context).
